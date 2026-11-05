import { fmtCount, fmtDuration } from "../format";
import { NODE_STATUS_LABELS, PHASE_LABELS, STEP_LABELS, nodeLabel } from "../labels";
import { displayStatus, nodeElapsed, type NodeState, type NodeStepRun, type RunState } from "../runState";
import { NodeStatusDot } from "./Badges";

interface Props {
  run: RunState;
  now: number;
  selected: string | null;
  onSelect: (nodeId: string) => void;
}

interface Segment {
  key: string;
  leftPct: number;
  widthPct: number;
  kind: "agent" | "tools" | "wrap_up" | "finish" | "node" | "error";
  label: string;
  running: boolean;
}

function stepKind(sr: NodeStepRun): Segment["kind"] {
  if (sr.error) return "error";
  if (sr.step === "agent") return "agent";
  if (sr.step === "tools") return "tools";
  if (sr.step === "wrap_up") return "wrap_up";
  if (sr.step === "finish") return "finish";
  return "node";
}

function buildSegments(n: NodeState, runStart: number, totalSpan: number, now: number, runRunning: boolean): Segment[] {
  const subSteps = n.stepRuns.filter((sr) => sr.step != null);
  const topSteps = n.stepRuns.filter((sr) => sr.step == null);
  const source = subSteps.length > 0 ? subSteps : topSteps;

  if (!source.length) {
    if (n.startedAt == null) return [];
    const end = n.finishedAt ?? (n.status === "running" && runRunning ? now : n.startedAt);
    const leftPct = Math.max(0, Math.min(100, ((n.startedAt - runStart) / totalSpan) * 100));
    const widthPct = Math.max(0.8, Math.min(100 - leftPct, ((Math.max(end, n.startedAt) - n.startedAt) / totalSpan) * 100));
    return [
      {
        key: `${n.id}-0`,
        leftPct,
        widthPct,
        kind: n.error ? "error" : "node",
        label: `${nodeLabel(n.id)} · ${fmtDuration(end - n.startedAt)}`,
        running: n.status === "running" && runRunning,
      },
    ];
  }

  return source.map((sr, i) => {
    const active = sr.finishedAt == null && n.status === "running" && runRunning;
    const end = sr.finishedAt ?? (active ? now : sr.startedAt + (sr.duration ?? 0));
    const dur = sr.duration ?? Math.max(0, end - sr.startedAt);
    const leftPct = Math.max(0, Math.min(100, ((sr.startedAt - runStart) / totalSpan) * 100));
    const widthPct = Math.max(0.8, Math.min(100 - leftPct, ((Math.max(end, sr.startedAt) - sr.startedAt) / totalSpan) * 100));
    const stepName = sr.step ? STEP_LABELS[sr.step] ?? sr.step : `第 ${i + 1} 轮`;
    const offsetS = Math.max(0, sr.startedAt - runStart).toFixed(1);
    return {
      key: `${sr.taskId}-${i}`,
      leftPct,
      widthPct,
      kind: stepKind(sr),
      label: `${stepName} · +${offsetS}s 开始 · 耗时 ${fmtDuration(dur)}`,
      running: active,
    };
  });
}

export function WaterfallView({ run, now, selected, onSelect }: Props) {
  const runStart = run.startedAt ?? now;
  const runRunning = run.status === "running" && !run.ended;
  const runEnd = runRunning ? now : (run.endedAt ?? (run.durationS ? runStart + run.durationS : now));
  const totalSpan = Math.max(0.2, runEnd - runStart);

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((p) => ({
    pct: p * 100,
    label: `+${fmtDuration(totalSpan * p)}`,
  }));

  return (
    <section className="panel waterfall-panel" aria-label="执行时间轴">
      <div className="waterfall-header">
        <div>
          <h2 className="panel-title">
            并行执行时间轴（Waterfall）
            <span className="muted small">点击任意节点查看该节点的完整执行明细</span>
          </h2>
        </div>
        <ul className="waterfall-legend small muted" aria-label="图例">
          <li>
            <span className="wf-swatch wf-agent" /> 模型思考 (agent)
          </li>
          <li>
            <span className="wf-swatch wf-tools" /> 工具调用 (tools)
          </li>
          <li>
            <span className="wf-swatch wf-wrap_up" /> 强制收尾 (wrap_up)
          </li>
          <li>
            <span className="wf-swatch wf-node" /> 节点执行 / 辩论发言
          </li>
        </ul>
      </div>

      <div className="waterfall-ruler small muted" aria-hidden>
        <div className="wf-col-label">节点 / 阶段</div>
        <div className="wf-col-track">
          {ticks.map((t) => (
            <span key={t.pct} className="wf-tick" style={{ left: `${t.pct}%` }}>
              {t.label}
            </span>
          ))}
        </div>
        <div className="wf-col-meta">耗时 / 调用</div>
      </div>

      <div className="waterfall-rows" role="list">
        {run.order.map((id) => {
          const n = run.nodes[id];
          if (!n) return null;
          const status = displayStatus(run, n);
          const elapsed = nodeElapsed(n, now);
          const segments = buildSegments(n, runStart, totalSpan, now, runRunning);
          const tokensOut = n.llm.reduce((s, c) => s + c.tokensOut, 0);

          return (
            <button
              key={id}
              type="button"
              role="listitem"
              className={`wf-row wf-status-${status}${selected === id ? " selected" : ""}`}
              onClick={() => onSelect(id)}
              aria-pressed={selected === id}
            >
              <span className="wf-col-label">
                <NodeStatusDot status={status} />
                <span className="wf-node-name">{nodeLabel(id)}</span>
                <span className="muted small wf-phase-tag">{PHASE_LABELS[n.phase] ?? n.phase}</span>
              </span>

              <span className="wf-col-track">
                {segments.length > 0 ? (
                  segments.map((seg) => (
                    <span
                      key={seg.key}
                      className={`wf-bar wf-${seg.kind}${seg.running ? " wf-running" : ""}`}
                      style={{ left: `${seg.leftPct}%`, width: `${seg.widthPct}%` }}
                      title={seg.label}
                    />
                  ))
                ) : (
                  <span className="wf-empty small muted">{NODE_STATUS_LABELS[status]}</span>
                )}
              </span>

              <span className="wf-col-meta small muted">
                <span>{elapsed != null ? fmtDuration(elapsed) : NODE_STATUS_LABELS[status]}</span>
                {n.tools.length > 0 && <span>🔧 {n.tools.length}</span>}
                {tokensOut > 0 && <span>{fmtCount(tokensOut)} tok</span>}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
