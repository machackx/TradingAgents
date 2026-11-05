import { fmtClock, fmtCount, fmtDuration, shortJson } from "../format";
import {
  ANALYST_LABELS,
  DEBATE_LABELS,
  FIELD_LABELS,
  NODE_STATUS_LABELS,
  SCHEMA_LABELS,
  SECTION_LABELS,
  STEP_LABELS,
  nodeLabel,
} from "../labels";
import { debateTurns, displayStatus, type NodeState, type RunState, type ToolCall } from "../runState";
import { NodeStatusDot, RatingBadge } from "./Badges";
import { Markdown } from "./Markdown";

const DEBATE_OF: Record<string, string> = {
  "Bull Researcher": "investment",
  "Bear Researcher": "investment",
  "Aggressive Analyst": "risk",
  "Conservative Analyst": "risk",
  "Neutral Analyst": "risk",
};
const JUDGE_OF: Record<string, string> = { "Research Manager": "investment", "Portfolio Manager": "risk" };
const SPEAKER_PREFIX = /^(Bull|Bear|Aggressive|Conservative|Neutral) Analyst:\s*/;
const RATING_FIELDS = new Set(["recommendation", "action", "rating"]);

interface Props {
  run: RunState;
  nodeId: string;
  now: number;
}

export function NodeDetail({ run, nodeId, now }: Props) {
  const n = run.nodes[nodeId];
  if (!n) return null;
  const status = displayStatus(run, n);
  const elapsed = status === "running" && n.startedAt ? now - n.startedAt : n.duration;
  const tokensIn = n.llm.reduce((s, c) => s + c.tokensIn, 0);
  const tokensOut = n.llm.reduce((s, c) => s + c.tokensOut, 0);
  const models = [...new Set(n.llm.map((c) => c.model).filter(Boolean))].join("、");
  const debate = DEBATE_OF[nodeId];
  const judged = JUDGE_OF[nodeId];
  const empty =
    !n.liveText && !n.reports.length && !n.turns.length && !n.structured.length && !n.tools.length &&
    !Object.keys(n.update).length;

  return (
    <section className="panel detail" aria-label={`${nodeLabel(nodeId)} 详情`}>
      <header className="detail-head">
        <h2>
          <NodeStatusDot status={status} /> {nodeLabel(nodeId)}
          <span className="muted small"> {nodeId}</span>
        </h2>
        <div className="detail-meta small muted">
          <span>{NODE_STATUS_LABELS[status]}</span>
          {status === "running" && n.step && <span>{STEP_LABELS[n.step] ?? n.step}</span>}
          {elapsed != null && <span>耗时 {fmtDuration(elapsed)}</span>}
          {n.llm.length > 0 && (
            <span>
              模型调用 {n.llm.length} 次 · 输入 {fmtCount(tokensIn)} / 输出 {fmtCount(tokensOut)} tokens
              {models && ` · ${models}`}
            </span>
          )}
        </div>
      </header>

      {n.error && <p className="error">节点出错：{n.error}</p>}
      {status === "pending" && <p className="muted">尚未开始。</p>}
      {status === "skipped" && (
        <p className="muted">
          这一步没有执行。证据检查判定资料不足时，流程会跳过辩论和交易，直接由投资组合经理给出“待复核”。
        </p>
      )}
      {status === "stopped" && <p className="muted">运行在这一步进行中时结束了（取消或出错）。</p>}

      {n.structured.map((s) => (
        <StructuredCard key={s.schema} schema={s.schema} data={s.data} />
      ))}

      {debate && <Conversation run={run} debate={debate} highlight={nodeId} />}

      {n.liveText && (
        <article className="live">
          <h3>
            正在输出 <span className="caret" aria-hidden />
          </h3>
          <Markdown text={n.liveText} />
        </article>
      )}

      {!debate &&
        n.reports.map((r) =>
          // A structured agent's report repeats its card above; keep it at hand, folded.
          n.structured.length ? (
            <details key={r.section} className="block">
              <summary>{SECTION_LABELS[r.section] ?? r.section}原文</summary>
              <Markdown text={r.content} />
            </details>
          ) : (
            <article key={r.section} className="report">
              <h3>{SECTION_LABELS[r.section] ?? r.section}</h3>
              <Markdown text={r.content} />
            </article>
          ),
        )}

      {judged && debateTurns(run, judged).length > 0 && (
        <details className="block">
          <summary>{DEBATE_LABELS[judged]}全文（{debateTurns(run, judged).length} 次发言）</summary>
          <Conversation run={run} debate={judged} />
        </details>
      )}

      {n.messages.length > 0 && (
        <details className="block">
          <summary>调用工具时的说明（{n.messages.length}）</summary>
          {n.messages.map((m, i) => (
            <p key={i} className="small">
              {m}
            </p>
          ))}
        </details>
      )}

      {n.tools.length > 0 && <ToolList tools={n.tools} />}
      <UpdateView node={n} />
      {n.llm.length > 0 && <LlmTable node={n} />}
      {empty && status === "running" && <p className="muted">正在等待第一步的结果…</p>}
    </section>
  );
}

function StructuredCard({ schema, data }: { schema: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== "");
  const short = entries.filter(([, v]) => typeof v !== "string" || v.length <= 60);
  const long = entries.filter(([, v]) => typeof v === "string" && v.length > 60);
  return (
    <article className="structured">
      <h3>{SCHEMA_LABELS[schema] ?? schema}</h3>
      <dl className="fields">
        {short.map(([key, value]) => (
          <div key={key}>
            <dt>{FIELD_LABELS[key] ?? key}</dt>
            <dd>{RATING_FIELDS.has(key) ? <RatingBadge rating={String(value)} /> : String(value)}</dd>
          </div>
        ))}
      </dl>
      {long.map(([key, value]) => (
        <div key={key} className="field-long">
          <h4>{FIELD_LABELS[key] ?? key}</h4>
          <Markdown text={String(value)} />
        </div>
      ))}
    </article>
  );
}

function Conversation({ run, debate, highlight }: { run: RunState; debate: string; highlight?: string }) {
  const turns = debateTurns(run, debate);
  if (!turns.length) return null;
  return (
    <article className="conversation">
      <h3>{DEBATE_LABELS[debate] ?? debate}</h3>
      {turns.map((t) => (
        <div
          key={t.seq}
          className={`turn turn-${t.node.split(" ")[0].toLowerCase()}${t.node === highlight ? " mine" : ""}`}
        >
          <div className="turn-head small">
            <strong>{nodeLabel(t.node)}</strong>
            <span className="muted">
              第 {t.turn} 次发言 · {fmtClock(t.ts)}
            </span>
          </div>
          <Markdown text={t.content.replace(SPEAKER_PREFIX, "")} />
        </div>
      ))}
    </article>
  );
}

function ToolList({ tools }: { tools: ToolCall[] }) {
  return (
    <article className="block">
      <h3>工具调用（{tools.length}）</h3>
      <ul className="tools">
        {tools.map((t, i) => {
          const state = !t.result ? "pending" : t.result.status === "error" ? "error" : t.result.usable ? "ok" : "unusable";
          const label = { pending: "等待返回", error: "出错", ok: "可用", unusable: "无可用数据" }[state];
          return (
            <li key={`${t.callId}-${i}`}>
              <details>
                <summary>
                  <code>{t.name}</code>
                  <span className="muted small args">{shortJson(t.args, 70)}</span>
                  <span className={`tag tag-${state}`}>{label}</span>
                  {t.result && <span className="muted small">{fmtCount(t.result.chars)} 字</span>}
                </summary>
                <pre className="code">{JSON.stringify(t.args, null, 2)}</pre>
                {t.result && (
                  <>
                    <pre className="code result">{t.result.content}</pre>
                    {t.result.truncated && <p className="muted small">内容过长，只保留了前面部分。</p>}
                  </>
                )}
              </details>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

function UpdateView({ node }: { node: NodeState }) {
  const u = node.update as Record<string, any>;
  const keys = Object.keys(u).filter((k) => k !== "final_rating");
  if (!keys.length) return null;
  return (
    <article className="block">
      <h3>写入运行状态</h3>
      {keys.map((key) => {
        const value = u[key];
        if (key === "market_data") return <MarketData key={key} data={value} />;
        if (key === "data_quality") return <DataQuality key={key} data={value} />;
        if (key === "analyst_evidence") return <Evidence key={key} data={value} />;
        if (key === "trader_validation") return <Validation key={key} data={value} />;
        if (key === "memory_note")
          return value ? (
            <p key={key} className="warn">
              {value}
            </p>
          ) : null;
        if (key === "past_context")
          return (
            <details key={key}>
              <summary>历史经验（供投资组合经理参考）</summary>
              {value ? <pre className="code">{value}</pre> : <p className="muted">这只股票还没有已结算的历史决策。</p>}
            </details>
          );
        return (
          <details key={key}>
            <summary>
              <code>{key}</code>
            </summary>
            <pre className="code">{JSON.stringify(value, null, 2)}</pre>
          </details>
        );
      })}
    </article>
  );
}

function MarketData({ data }: { data: Record<string, any> }) {
  if (!data || data.status !== "available") {
    return <p className="warn">行情快照不可用{data?.reason ? `（${data.reason}）` : ""}。</p>;
  }
  const records: Record<string, any>[] = data.records ?? [];
  const last = records[records.length - 1];
  return (
    <dl className="fields">
      <div>
        <dt>代码</dt>
        <dd>{data.symbol}</dd>
      </div>
      <div>
        <dt>最新日线</dt>
        <dd>
          {data.latest_bar_date}
          {last?.Close != null && ` · 收盘 ${Number(last.Close).toFixed(2)} ${data.quote_currency ?? ""}`}
        </dd>
      </div>
      <div>
        <dt>日线条数</dt>
        <dd>{records.length}</dd>
      </div>
      <div>
        <dt>数据来源</dt>
        <dd>{data.source}</dd>
      </div>
      <div>
        <dt>快照 ID</dt>
        <dd>
          <code>{data.snapshot_id}</code>
        </dd>
      </div>
      <div>
        <dt>K 线状态</dt>
        <dd>{data.bar_status}</dd>
      </div>
    </dl>
  );
}

const COVERAGE: Record<string, string> = { available: "充足", missing: "缺失", not_applicable: "不适用" };

function DataQuality({ data }: { data: Record<string, any> }) {
  if (!data) return null;
  return (
    <div>
      <p className={data.status === "adequate" ? "ok" : "warn"}>
        {data.status === "adequate" ? "每位分析师都拿到了可用的数据。" : "资料不足，本次不会给出可执行的评级。"}
      </p>
      <dl className="fields">
        {Object.entries(data.coverage ?? {}).map(([key, value]) => (
          <div key={key}>
            <dt>{ANALYST_LABELS[key] ?? key}</dt>
            <dd className={value === "missing" ? "bad" : ""}>{COVERAGE[String(value)] ?? String(value)}</dd>
          </div>
        ))}
      </dl>
      {(data.issues ?? []).length > 0 && (
        <ul className="small">
          {data.issues.map((issue: string) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Evidence({ data }: { data: Record<string, Record<string, { attempted: number; usable: number }>> }) {
  const rows = Object.entries(data ?? {}).flatMap(([analyst, tools]) =>
    Object.entries(tools).map(([tool, counts]) => ({ analyst, tool, ...counts })),
  );
  if (!rows.length) return null;
  return (
    <table className="table small">
      <thead>
        <tr>
          <th>数据来源</th>
          <th>尝试</th>
          <th>可用</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.analyst}-${r.tool}`} className={r.usable ? "" : "bad"}>
            <td>
              <code>{r.tool}</code>
            </td>
            <td>{r.attempted}</td>
            <td>{r.usable}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Validation({ data }: { data: Record<string, any> }) {
  if (!data) return null;
  const label = { validated: "通过程序校验", invalid: "未通过程序校验", unparsed: "无法解析" }[data.status as string] ?? data.status;
  return (
    <div>
      <p className={data.status === "validated" ? "ok" : "warn"}>交易提议{label}。</p>
      {(data.issues ?? []).length > 0 && (
        <ul className="small bad">
          {data.issues.map((x: string) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      {(data.notes ?? []).length > 0 && (
        <ul className="small muted">
          {data.notes.map((x: string) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LlmTable({ node }: { node: NodeState }) {
  return (
    <details className="block">
      <summary>模型调用明细（{node.llm.length}）</summary>
      <table className="table small">
        <thead>
          <tr>
            <th>结束时间</th>
            <th>步骤</th>
            <th>模型</th>
            <th>输入</th>
            <th>输出</th>
            <th>耗时</th>
          </tr>
        </thead>
        <tbody>
          {node.llm.map((c, i) => (
            <tr key={i} className={c.error ? "bad" : ""}>
              <td>{fmtClock(c.ts)}</td>
              <td>{c.step ? STEP_LABELS[c.step] ?? c.step : "—"}</td>
              <td>{c.model ?? "—"}</td>
              <td>{fmtCount(c.tokensIn)}</td>
              <td>{fmtCount(c.tokensOut)}</td>
              <td>{c.error ? c.error : fmtDuration(c.duration)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

