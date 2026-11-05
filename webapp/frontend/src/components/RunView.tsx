import { Suspense, lazy, useEffect, useMemo, useState } from "react";

import { api, reportUrl } from "../api";
import { fmtCount, fmtDuration } from "../format";
import { ANALYST_LABELS, RATING_TONE, ratingLabel } from "../labels";
import type { RunState } from "../runState";
import type { RunSummary } from "../types";
import { useNow, useRunStream, type Connection } from "../useRunStream";
import { RatingBadge, RunStatusPill } from "./Badges";
import { DebateView } from "./DebateView";
import { DecisionCard, traderProposal } from "./DecisionCard";
import { EventLog } from "./EventLog";
import { NodeDetail } from "./NodeDetail";
import { Pipeline } from "./Pipeline";
import type { PriceLevel } from "./PriceChart";
import { ReportView } from "./ReportView";
import { ViewpointDashboard } from "./ViewpointDashboard";
import { WaterfallView } from "./WaterfallView";

interface Props {
  runId: string;
  onEnded: (summary: RunSummary) => void;
}

// The chart library is most of the page's weight; it loads when a chart is first shown.
const PriceChart = lazy(() => import("./PriceChart").then((m) => ({ default: m.PriceChart })));

type Tab = "flow" | "timeline" | "debate" | "report";
const TABS: { key: Tab; label: string }[] = [
  { key: "flow", label: "流程" },
  { key: "timeline", label: "时间轴" },
  { key: "debate", label: "辩论" },
  { key: "report", label: "报告" },
];

const CONNECTION_LABELS: Record<Connection, string> = {
  connecting: "连接中…",
  live: "实时",
  reconnecting: "连接断开，重连中…",
  closed: "",
  error: "无法读取这次运行",
};

/** The node to show when the user has not picked one: the one at work, else the decision. */
function followed(run: RunState): string | null {
  const nodes = run.order.map((id) => run.nodes[id]).filter(Boolean);
  const running = nodes.filter((n) => n.status === "running").sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0));
  if (running.length) return running[0].id;
  if (run.nodes["Portfolio Manager"]?.status === "done") return "Portfolio Manager";
  const done = nodes.filter((n) => n.finishedAt).sort((a, b) => (b.finishedAt ?? 0) - (a.finishedAt ?? 0));
  return done[0]?.id ?? nodes[0]?.id ?? null;
}

export function RunView({ runId, onEnded }: Props) {
  const { run, connection } = useRunStream(runId, onEnded);
  const running = run.status === "running" && !run.ended;
  const now = useNow(running);
  const [picked, setPicked] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("flow");

  useEffect(() => {
    setPicked(null);
    setTab("flow");
    setCancelling(false);
    setCancelError(null);
  }, [runId]);

  const selected = picked && run.nodes[picked] ? picked : followed(run);
  const elapsed =
    run.durationS ?? (run.startedAt ? (running ? now : run.endedAt ?? run.startedAt) - run.startedAt : null);
  const quality = (run.nodes["Evidence check"]?.update as Record<string, any> | undefined)?.data_quality;
  const settings = run.settings as Record<string, any> | undefined;
  const market = (run.nodes["Prepare market data"]?.update as Record<string, any> | undefined)?.market_data;
  const levels = useMemo(() => tradeLevels(run), [run]);
  const marker = run.rating
    ? { text: `决策：${ratingLabel(run.rating)}`, tone: (RATING_TONE[run.rating] === "up" ? "rise" : RATING_TONE[run.rating] === "down" ? "fall" : RATING_TONE[run.rating] ?? "level") as "rise" | "fall" | "level" | "warn" }
    : undefined;

  async function cancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await api.cancel(runId);
    } catch (e) {
      setCancelError(String((e as Error).message ?? e));
      setCancelling(false);
    }
  }

  if (!run.startedAt && !run.log.length) {
    // Nothing has arrived yet: whether the run is going or long over is not known.
    return (
      <section className="panel">
        {connection === "error" ? (
          <p className="error">{CONNECTION_LABELS.error}。</p>
        ) : (
          <p className="muted">正在读取这次分析…</p>
        )}
      </section>
    );
  }

  return (
    <div className="run-view">
      <header className="panel run-head">
        <div className="run-title">
          <h1>
            {run.ticker ?? "…"} <span className="muted">{run.tradeDate}</span>
          </h1>
          <div className="run-badges">
            <RunStatusPill status={running ? "running" : run.status} />
            {run.rating && <RatingBadge rating={run.rating} large />}
            {run.resumed && <span className="pill">断点续跑</span>}
            {CONNECTION_LABELS[connection] && running && <span className="muted small">{CONNECTION_LABELS[connection]}</span>}
          </div>
        </div>
        <div className="run-actions">
          {running && (
            <button onClick={cancel} disabled={cancelling}>
              {cancelling ? "正在取消（等当前一步结束）…" : "取消分析"}
            </button>
          )}
          {run.summary?.has_report && (
            <a className="button" href={reportUrl(runId)} target="_blank" rel="noopener noreferrer">
              打开完整报告
            </a>
          )}
        </div>
        {cancelError && <p className="error">{cancelError}</p>}

        <dl className="stats">
          <div>
            <dt>耗时</dt>
            <dd>{fmtDuration(elapsed)}</dd>
          </div>
          <div>
            <dt>模型调用</dt>
            <dd>{run.stats.llm_calls}</dd>
          </div>
          <div>
            <dt>工具调用</dt>
            <dd>{run.stats.tool_calls}</dd>
          </div>
          <div>
            <dt>输入 tokens</dt>
            <dd>{fmtCount(run.stats.tokens_in)}</dd>
          </div>
          <div>
            <dt>输出 tokens</dt>
            <dd>{fmtCount(run.stats.tokens_out)}</dd>
          </div>
        </dl>

        {settings && (
          <details className="small">
            <summary>运行设置</summary>
            <p className="muted">
              分析师：{run.analysts.map((a) => ANALYST_LABELS[a] ?? a).join("、")} · 模型：
              {settings.quick_think_provider ?? settings.llm_provider} {settings.quick_think_llm}（快速）/{" "}
              {settings.deep_think_provider ?? settings.llm_provider} {settings.deep_think_llm}（深度） · 辩论{" "}
              {settings.max_debate_rounds} 轮 · 风险讨论 {settings.max_risk_discuss_rounds} 轮 · 语言{" "}
              {settings.output_language} · 版本 {settings.version}
              {settings.agent_models && Object.keys(settings.agent_models).length > 0 && (
                <>
                  {" "}
                  · 角色独立模型：
                  {Object.entries(settings.agent_models as Record<string, { provider?: string; model?: string }>)
                    .map(([role, spec]) => `${role}=${spec.provider ?? ""}:${spec.model ?? ""}`)
                    .join("，")}
                </>
              )}
            </p>
          </details>
        )}

        {run.instrument && (
          <details className="small">
            <summary>标的身份与市场上下文（注入所有智能体）</summary>
            <pre className="code">{run.instrument}</pre>
          </details>
        )}
      </header>

      {run.error && <p className="panel error">运行失败：{run.error}</p>}
      {quality?.status === "insufficient_data" && (
        <p className="panel warn">
          证据检查判定资料不足（{(quality.issues ?? []).join("；")}），本次跳过辩论和交易，结果为“待复核”。
        </p>
      )}
      {run.status === "finished" && <DecisionCard run={run} />}

      <ViewpointDashboard
        run={run}
        selectedNode={selected}
        onSelectNode={(nodeId) => {
          setPicked(nodeId);
          if (tab !== "flow" && tab !== "timeline") {
            setTab("flow");
          }
        }}
        onOpenDebateTab={() => setTab("debate")}
      />

      {market?.status === "available" && (
        <section className="panel" aria-label="行情">
          <h2 className="panel-title">
            行情 <span className="muted small">智能体分析时看到的同一份数据：{market.source}，截至 {market.latest_bar_date}，快照 {market.snapshot_id}</span>
          </h2>
          <Suspense fallback={<div className="chart-box chart-loading">正在加载图表…</div>}>
            <PriceChart records={market.records ?? []} currency={market.quote_currency} levels={levels} marker={marker} />
          </Suspense>
        </section>
      )}

      <div className="tabs" role="tablist" aria-label="查看方式">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={tab === t.key ? "on" : ""}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "flow" && (
        <>
          <section className="panel">
            <Pipeline run={run} now={now} selected={selected} onSelect={setPicked} />
            {picked && running && (
              <button className="link" onClick={() => setPicked(null)}>
                跟随当前进度
              </button>
            )}
          </section>
          {selected && <NodeDetail run={run} nodeId={selected} now={now} />}
          <EventLog events={run.log} />
        </>
      )}
      {tab === "timeline" && (
        <>
          <WaterfallView run={run} now={now} selected={selected} onSelect={setPicked} />
          {selected && <NodeDetail run={run} nodeId={selected} now={now} />}
          <EventLog events={run.log} />
        </>
      )}
      {tab === "debate" && <DebateView run={run} />}
      {tab === "report" && <ReportView run={run} />}
    </div>
  );
}


/** The trade's price levels for the chart: the Trader's entry and stop, the Portfolio Manager's target. */
function tradeLevels(run: RunState): PriceLevel[] {
  const levels: PriceLevel[] = [];
  const proposal = traderProposal(run);
  const decision = run.nodes["Portfolio Manager"]?.structured.find((x) => x.schema === "portfolio_decision")?.data;
  const add = (value: unknown, label: string, kind: PriceLevel["kind"]) => {
    const price = Number(value);
    if (value != null && value !== "" && Number.isFinite(price) && price > 0) levels.push({ price, label, kind });
  };
  add(proposal?.entry_price, "入场", "entry");
  add(proposal?.stop_loss, "止损", "stop");
  add(decision?.price_target, "目标", "target");
  return levels;
}
