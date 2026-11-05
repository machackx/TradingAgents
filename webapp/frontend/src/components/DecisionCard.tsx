import { RATING_LABELS, RATING_TONE, nodeLabel } from "../labels";
import { structuredOf, type RunState } from "../runState";
import { RatingBadge } from "./Badges";
import { Markdown } from "./Markdown";

const SCALE = ["Sell", "Underweight", "Hold", "Overweight", "Buy"];

type Data = Record<string, any>;

export function lastClose(run: RunState): { close: number; date: string } | null {
  const md = (run.nodes["Prepare market data"]?.update as Data | undefined)?.market_data;
  const last = md?.records?.[md.records.length - 1];
  return last && Number.isFinite(Number(last.Close)) ? { close: Number(last.Close), date: String(last.Date) } : null;
}

/** The Trader's proposal as the model gave it, else as the validator read it from the text. */
export function traderProposal(run: RunState): Data | undefined {
  return (
    structuredOf(run, "Trader", "trader_proposal") ??
    ((run.nodes.Trader?.update as Data | undefined)?.trader_validation?.proposal as Data | undefined)
  );
}

function pct(value: number, base: number): string {
  const p = (value / base - 1) * 100;
  return `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
}

const num = (v: unknown) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

export function DecisionCard({ run }: { run: RunState }) {
  const rating = run.rating ?? null;
  const decision = structuredOf(run, "Portfolio Manager", "portfolio_decision") as Data | undefined;
  const plan = structuredOf(run, "Research Manager", "research_plan") as Data | undefined;
  const proposal = traderProposal(run);
  const validation = (run.nodes.Trader?.update as Data | undefined)?.trader_validation as Data | undefined;
  const quality = (run.nodes["Evidence check"]?.update as Data | undefined)?.data_quality as Data | undefined;
  const close = lastClose(run);
  const review = rating === "REVIEW";
  const tone = rating ? RATING_TONE[rating] : "level";

  const entry = num(proposal?.entry_price);
  const stop = num(proposal?.stop_loss);
  const target = num(decision?.price_target);
  const reasons = [
    ...(quality?.status === "insufficient_data" ? quality.issues ?? [] : []),
    ...(validation?.status === "invalid" ? validation.issues ?? [] : []),
  ] as string[];

  const chain = [
    { node: "Research Manager", label: "建议", value: plan?.recommendation },
    { node: "Trader", label: "方向", value: proposal?.action },
    { node: "Portfolio Manager", label: "评级", value: rating },
  ];

  return (
    <section className="panel decision-card" aria-label="最终决策">
      <div className="decision-top">
        <div>
          <span className="muted small">最终评级</span>
          <div className="decision-rating">
            <RatingBadge rating={rating} large />
            {decision?.time_horizon && <span className="muted small">周期：{String(decision.time_horizon)}</span>}
          </div>
        </div>
        <ol className="scale" aria-label="五档评级">
          {SCALE.map((step) => (
            <li
              key={step}
              className={`scale-step${step === rating ? ` on tone-${tone}` : ""}`}
              aria-current={step === rating ? "true" : undefined}
            >
              {RATING_LABELS[step]}
            </li>
          ))}
        </ol>
      </div>

      {review && (
        <div className="panel warn review">
          <strong>这次没有给出可执行的评级。</strong>
          {reasons.length ? (
            <ul>
              {reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : (
            <span> 最终决策里读不出评级，请阅读下面的决策原文自行判断。</span>
          )}
        </div>
      )}

      <ol className="chain" aria-label="决策链">
        {chain.map((c) => (
          <li key={c.node}>
            <span className="muted small">
              {nodeLabel(c.node)}
              {c.label}
            </span>
            {c.value ? <RatingBadge rating={String(c.value)} /> : <span className="muted">—</span>}
          </li>
        ))}
      </ol>

      <dl className="fields levels">
        {close && (
          <div>
            <dt>最新收盘（{close.date}）</dt>
            <dd>{close.close.toFixed(2)}</dd>
          </div>
        )}
        <div>
          <dt>入场价</dt>
          <dd>{entry != null ? entry.toFixed(2) : <span className="muted">未给出</span>}</dd>
        </div>
        <div>
          <dt>止损价</dt>
          <dd>
            {stop != null ? (
              <>
                {stop.toFixed(2)}
                {(entry ?? close?.close) && (
                  <span className="muted small"> 距{entry != null ? "入场" : "收盘"} {pct(stop, entry ?? close!.close)}</span>
                )}
              </>
            ) : (
              <span className="muted">未给出</span>
            )}
          </dd>
        </div>
        <div>
          <dt>目标价</dt>
          <dd>
            {target != null ? (
              <>
                {target.toFixed(2)}
                {close && <span className="muted small"> 距收盘 {pct(target, close.close)}</span>}
              </>
            ) : (
              <span className="muted">未给出</span>
            )}
          </dd>
        </div>
        {num(proposal?.position_size_pct) != null && (
          <div>
            <dt>目标仓位</dt>
            <dd>{num(proposal?.position_size_pct)}% 组合净值</dd>
          </div>
        )}
        {num(proposal?.order_quantity) != null && (
          <div>
            <dt>数量</dt>
            <dd>{num(proposal?.order_quantity)}</dd>
          </div>
        )}
      </dl>
      {proposal?.position_sizing && <p className="small">仓位：{String(proposal.position_sizing)}</p>}

      {validation && (
        <p className={`small ${validation.status === "validated" ? "muted" : "warn"}`}>
          {validation.status === "validated" ? "交易提议通过了程序校验" : "交易提议未通过程序校验"}
          {(validation.notes ?? []).length > 0 && `：${(validation.notes as string[]).join("；")}`}
        </p>
      )}

      {decision?.executive_summary && <p className="summary">{String(decision.executive_summary)}</p>}
      {decision?.investment_thesis && (
        <details>
          <summary>投资逻辑</summary>
          <Markdown text={String(decision.investment_thesis)} />
        </details>
      )}
      {!decision && run.nodes["Portfolio Manager"]?.reports[0] && (
        <details open={review}>
          <summary>决策原文</summary>
          <Markdown text={run.nodes["Portfolio Manager"].reports[0].content} />
        </details>
      )}
    </section>
  );
}
