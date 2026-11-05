import { useMemo, useState } from "react";

import { fmtClock, fmtCount, fmtDuration, shortJson } from "../format";
import { DEBATE_LABELS, SCHEMA_LABELS, SECTION_LABELS, STEP_LABELS, nodeLabel, ratingLabel } from "../labels";
import type { RunEvent } from "../types";

const SHOWN = 400;

type Category = "all" | "lifecycle" | "llm" | "tool" | "output" | "error";

const CATEGORIES: { key: Category; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "lifecycle", label: "节点启停" },
  { key: "llm", label: "模型与思考" },
  { key: "tool", label: "工具调用" },
  { key: "output", label: "报告与辩论" },
  { key: "error", label: "异常/无数据" },
];

function matchCategory(e: RunEvent, cat: Category): boolean {
  if (cat === "all") return true;
  if (cat === "lifecycle") {
    return (
      e.type === "run_started" ||
      e.type === "node_started" ||
      e.type === "node_finished" ||
      e.type === "run_finished" ||
      e.type === "run_failed" ||
      e.type === "run_cancelled"
    );
  }
  if (cat === "llm") {
    return e.type === "llm_call" || e.type === "message" || e.type === "structured_output";
  }
  if (cat === "tool") {
    return e.type === "tool_call" || e.type === "tool_result";
  }
  if (cat === "output") {
    return e.type === "report" || e.type === "debate_turn";
  }
  if (cat === "error") {
    return (
      !!e.error ||
      e.type === "run_failed" ||
      e.type === "run_cancelled" ||
      e.status === "error" ||
      (e.type === "tool_result" && !e.usable)
    );
  }
  return true;
}

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
      return `${where} 模型 ${e.model ?? ""} 输入 ${fmtCount(e.tokens_in)} / 输出 ${fmtCount(e.tokens_out)}，${fmtDuration(e.duration_s)}${e.reasoning ? "（含思考链）" : ""}${e.error ? `，出错：${e.error}` : ""}`;
    case "message":
      return `${where} 说明：${String(e.content || e.reasoning || "").slice(0, 80)}`;
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
  const [category, setCategory] = useState<Category>("all");
  const [nodeFilter, setNodeFilter] = useState<string>("all");
  const [query, setQuery] = useState<string>("");

  const nodes = useMemo(() => {
    const seen = new Set<string>();
    for (const e of events) {
      if (e.node) seen.add(e.node);
    }
    return Array.from(seen);
  }, [events]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((e) => {
      if (!matchCategory(e, category)) return false;
      if (nodeFilter !== "all" && e.node !== nodeFilter) return false;
      if (q) {
        const text = `${describe(e)} ${e.name ?? ""} ${e.model ?? ""} ${e.type}`.toLowerCase();
        if (!text.includes(q)) return false;
      }
      return true;
    });
  }, [events, category, nodeFilter, query]);

  const shown = filtered.slice(-SHOWN);

  return (
    <details className="panel log">
      <summary>
        事件日志（{filtered.length === events.length ? events.length : `${filtered.length} / ${events.length}`}）
        {filtered.length > SHOWN && <span className="muted small">，显示最近 {SHOWN} 条</span>}
      </summary>

      <div className="log-toolbar small">
        <div className="segmented" role="group" aria-label="事件类型过滤">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              type="button"
              className={category === c.key ? "on" : ""}
              onClick={() => setCategory(c.key)}
            >
              {c.label}
            </button>
          ))}
        </div>

        <select
          className="log-node-select"
          aria-label="按节点筛选"
          value={nodeFilter}
          onChange={(e) => setNodeFilter(e.target.value)}
        >
          <option value="all">全部节点</option>
          {nodes.map((n) => (
            <option key={n} value={n}>
              {nodeLabel(n)} ({n})
            </option>
          ))}
        </select>

        <input
          type="search"
          className="log-search"
          placeholder="搜索工具名、模型、内容…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {shown.length === 0 ? (
        <p className="muted small">没有符合筛选条件的事件。</p>
      ) : (
        <ol className="log-lines">
          {shown.map((e) => (
            <li key={e.seq} className={`log-${e.type}${e.error || e.status === "error" ? " log-err" : ""}`}>
              <details className="log-detail">
                <summary>
                  <span className="muted">#{e.seq} · {fmtClock(e.ts)}</span>
                  <span>{describe(e)}</span>
                </summary>
                <pre className="code">{JSON.stringify(e, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}
