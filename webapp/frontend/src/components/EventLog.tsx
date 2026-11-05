import { fmtClock, fmtCount, fmtDuration, shortJson } from "../format";
import { DEBATE_LABELS, SCHEMA_LABELS, SECTION_LABELS, STEP_LABELS, nodeLabel, ratingLabel } from "../labels";
import type { RunEvent } from "../types";

const SHOWN = 400;

function describe(e: RunEvent): string {
  const where = nodeLabel(e.node) + (e.step ? ` · ${STEP_LABELS[e.step] ?? e.step}` : "");
  switch (e.type) {
    case "run_started":
      return `开始分析 ${e.ticker}（${e.trade_date}）${e.resumed ? "，从断点继续" : ""}`;
    case "node_started":
      return `${where} 开始`;
    case "node_finished":
      return e.error ? `${where} 出错：${e.error}` : `${where} 完成，${fmtDuration(e.duration_s)}`;
    case "tool_call":
      return `${where} 调用 ${e.name} ${shortJson(e.args, 60)}`;
    case "tool_result":
      return `${where} ${e.name} 返回 ${fmtCount(e.chars)} 字，${e.status === "error" ? "出错" : e.usable ? "可用" : "无可用数据"}`;
    case "llm_call":
      return `${where} 模型 ${e.model ?? ""} 输入 ${fmtCount(e.tokens_in)} / 输出 ${fmtCount(e.tokens_out)}，${fmtDuration(e.duration_s)}${e.error ? `，出错：${e.error}` : ""}`;
    case "message":
      return `${where} 说明：${String(e.content).slice(0, 80)}`;
    case "report":
      return `${where} 提交${SECTION_LABELS[e.section] ?? e.section}`;
    case "debate_turn":
      return `${where} 在${DEBATE_LABELS[e.debate] ?? e.debate}中第 ${e.turn} 次发言`;
    case "structured_output":
      return `${where} 给出${SCHEMA_LABELS[e.schema] ?? e.schema}`;
    case "run_finished":
      return `分析完成，评级 ${ratingLabel(e.rating)}，共 ${fmtDuration(e.duration_s)}`;
    case "run_failed":
      return `分析失败：${e.error_type}: ${e.message}`;
    case "run_cancelled":
      return "分析已取消";
    default:
      return `${e.type} ${where}`;
  }
}

export function EventLog({ events }: { events: RunEvent[] }) {
  const shown = events.slice(-SHOWN);
  return (
    <details className="panel log">
      <summary>
        事件日志（{events.length}）{events.length > SHOWN && <span className="muted small">，显示最近 {SHOWN} 条</span>}
      </summary>
      <ol className="log-lines">
        {shown.map((e) => (
          <li key={e.seq} className={`log-${e.type}`}>
            <span className="muted">{fmtClock(e.ts)}</span>
            <span>{describe(e)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
