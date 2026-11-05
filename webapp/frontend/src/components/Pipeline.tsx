import { fmtCount, fmtDuration } from "../format";
import { researchRejected } from "../checks";
import { NODE_STATUS_LABELS, PHASE_LABELS, STEP_LABELS, nodeLabel, ratingLabel } from "../labels";
import { displayStatus, nodeElapsed, type NodeState, type RunState } from "../runState";
import { NodeStatusDot } from "./Badges";

interface Props {
  run: RunState;
  now: number;
  selected: string | null;
  onSelect: (nodeId: string) => void;
}

const DEBATERS = new Set([
  "Bull Researcher",
  "Bear Researcher",
  "Aggressive Analyst",
  "Conservative Analyst",
  "Neutral Analyst",
]);

// What a finished node's card says about its result, in a few words.
function outcome(n: NodeState): string | null {
  const u = n.update as Record<string, any>;
  if (n.id === "Prepare market data") {
    const md = u.market_data;
    if (!md) return null;
    return md.status === "available" ? `${md.records?.length ?? 0} 根日线 · 截至 ${md.latest_bar_date ?? "?"}` : "行情不可用";
  }
  if (n.id === "Memory Log") {
    const settledCount = u.memory_settlement?.settled?.length ?? 0;
    if (u.memory_note) return "部分未能结算";
    if (settledCount > 0) return `本次结算 ${settledCount} 条 · 已载入历史经验`;
    return u.past_context ? "已载入历史经验" : "暂无历史经验";
  }
  if (n.id === "Evidence check") {
    const dq = u.data_quality;
    return dq ? (dq.status === "adequate" ? "证据充足 → 进入辩论" : "证据不足 → 跳过辩论") : null;
  }
  if (DEBATERS.has(n.id)) return n.turns.length ? `发言 ${n.turns.length} 次` : null;
  if (n.id === "Research Manager" && researchRejected(n)) return "未通过研究校验 → 待复核";
  const plan = n.structured.find((s) => s.schema === "research_plan")?.data;
  if (plan) return `建议 ${ratingLabel(String(plan.recommendation))}`;
  const proposal = n.structured.find((s) => s.schema === "trader_proposal")?.data;
  if (n.id === "Trader") {
    const validation = u.trader_validation?.status;
    const action = proposal ? ratingLabel(String(proposal.action)) : null;
    if (validation === "invalid") return "未通过校验 → 跳过风控";
    return action ? `方向 ${action} · 校验通过` : null;
  }
  if (n.id === "Portfolio Manager") return u.final_rating ? `评级 ${ratingLabel(u.final_rating)}` : null;
  if (n.tools.length) {
    const usable = n.tools.filter((t) => t.result?.usable).length;
    const wrapped = n.stepRuns.some((sr) => sr.step === "wrap_up");
    return `工具 ${n.tools.length} 次 · ${usable} 次可用${wrapped ? " · 触发收尾" : ""}`;
  }
  const sentiment = n.structured.find((s) => s.schema === "sentiment_report")?.data;
  if (sentiment) return `情绪 ${sentiment.overall_band} · ${sentiment.overall_score}/10`;
  return null;
}

function activity(n: NodeState): string {
  if (n.step) {
    const tool = n.step === "tools" ? n.tools[n.tools.length - 1]?.name : null;
    return (STEP_LABELS[n.step] ?? n.step) + (tool ? ` · ${tool}` : "");
  }
  if (n.liveThinking) return "正在深度思考…";
  return n.liveText ? "正在输出…" : "进行中";
}

export function Pipeline({ run, now, selected, onSelect }: Props) {
  const phases = run.phases.filter((phase) => run.order.some((id) => run.nodes[id]?.phase === phase));
  const others = run.order.filter((id) => !run.phases.includes(run.nodes[id]?.phase));
  if (others.length) phases.push("other");

  return (
    <div className="pipeline" role="list">
      {phases.map((phase, i) => (
        <section key={phase} className="phase" role="listitem" aria-label={PHASE_LABELS[phase] ?? phase}>
          <h3 className="phase-title">
            <span className="phase-index">{i + 1}</span>
            {PHASE_LABELS[phase] ?? phase}
          </h3>
          <div className="phase-nodes">
            {run.order
              .filter((id) => (run.nodes[id]?.phase ?? "other") === phase || (phase === "other" && others.includes(id)))
              .map((id) => {
                const n = run.nodes[id];
                const status = displayStatus(run, n);
                const elapsed = nodeElapsed(n, now);
                const tokensOut = n.llm.reduce((sum, c) => sum + c.tokensOut, 0);
                const detail = status === "running" ? activity(n) : status === "done" ? outcome(n) : NODE_STATUS_LABELS[status];
                return (
                  <button
                    key={id}
                    className={`node node-${status}${selected === id ? " selected" : ""}`}
                    onClick={() => onSelect(id)}
                    aria-pressed={selected === id}
                  >
                    <span className="node-head">
                      <NodeStatusDot status={status} />
                      <span className="node-name">{nodeLabel(id)}</span>
                    </span>
                    {detail && <span className="node-detail">{detail}</span>}
                    <span className="node-meta">
                      {elapsed != null && <span>{fmtDuration(elapsed)}</span>}
                      {tokensOut > 0 && <span>{fmtCount(tokensOut)} tokens</span>}
                    </span>
                  </button>
                );
              })}
          </div>
        </section>
      ))}
    </div>
  );
}
