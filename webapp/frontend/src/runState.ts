// A run's events folded into what the page draws: one state per graph node.

import type { RunEvent, RunStatus, RunSummary, Stats } from "./types";

export type NodeStatus = "pending" | "running" | "done" | "failed" | "stopped";

export interface ToolCall {
  callId: string;
  name: string;
  args: Record<string, unknown>;
  step: string | null;
  ts: number;
  duration?: number | null;
  result?: {
    status: string;
    usable: boolean;
    chars: number;
    content: string;
    truncated: boolean;
    ts: number;
  };
}

export interface PromptMessage {
  role: string;
  content: string;
  truncated?: boolean;
  chars?: number;
}

export interface LlmCall {
  model: string | null;
  step: string | null;
  tokensIn: number;
  tokensOut: number;
  duration: number | null;
  error: string | null;
  prompts?: PromptMessage[];
  reasoning?: string | null;
  ts: number;
}

export interface AgentMessage {
  content: string;
  reasoning?: string | null;
  step: string | null;
  ts: number;
}

export interface NodeStepRun {
  taskId: string;
  step: string | null; // null = top-level node turn, else subgraph step ("agent", "tools", "wrap_up", "finish")
  startedAt: number;
  finishedAt?: number;
  duration?: number | null;
  error?: string | null;
}

export interface Report {
  section: string;
  content: string;
  ts: number;
}

export interface DebateTurn {
  node: string;
  debate: string;
  turn: number;
  content: string;
  seq: number;
  ts: number;
}

export interface Structured {
  schema: string;
  data: Record<string, unknown>;
  ts: number;
}

export interface NodeState {
  id: string;
  phase: string;
  status: NodeStatus;
  startedAt?: number;
  lastStartedAt?: number;
  finishedAt?: number;
  duration?: number | null;
  step: string | null; // the step inside it now running, for an analyst
  stepRuns: NodeStepRun[];
  tools: ToolCall[];
  llm: LlmCall[];
  liveText: string; // the model's text as it streams, until its report lands
  liveThinking: string; // the model's reasoning/thinking stream while in progress
  messages: string[];
  agentMessages: AgentMessage[];
  reports: Report[];
  turns: DebateTurn[];
  structured: Structured[];
  update: Record<string, unknown>;
  error?: string | null;
}

export interface RunState {
  runId: string;
  ticker?: string;
  tradeDate?: string;
  analysts: string[];
  resumed: boolean;
  instrument?: string;
  settings?: Record<string, unknown>;
  startedAt?: number;
  endedAt?: number;
  durationS?: number; // as the run measured it, once over
  status: RunStatus;
  rating?: string | null;
  error?: string;
  phases: string[];
  order: string[];
  nodes: Record<string, NodeState>;
  stats: Stats;
  log: RunEvent[];
  lastSeq: number;
  ended: boolean; // the stream said it is over (and the report is saved)
  summary?: RunSummary;
}

const LOG_LIMIT = 3000;

export function initialRun(runId: string): RunState {
  return {
    runId,
    analysts: [],
    resumed: false,
    status: "running",
    phases: [],
    order: [],
    nodes: {},
    stats: { llm_calls: 0, tool_calls: 0, tokens_in: 0, tokens_out: 0 },
    log: [],
    lastSeq: 0,
    ended: false,
  };
}

function blankNode(id: string, phase = "other"): NodeState {
  return {
    id,
    phase,
    status: "pending",
    step: null,
    stepRuns: [],
    tools: [],
    llm: [],
    liveText: "",
    liveThinking: "",
    messages: [],
    agentMessages: [],
    reports: [],
    turns: [],
    structured: [],
    update: {},
  };
}

const ENDINGS: Record<string, RunStatus> = {
  run_finished: "finished",
  run_failed: "failed",
  run_cancelled: "cancelled",
};

/** ``events`` (oldest first) applied to ``state``; events already seen are skipped. */
export function applyEvents(state: RunState, events: RunEvent[]): RunState {
  const fresh = events.filter((e) => e.seq > state.lastSeq);
  if (!fresh.length) return state;

  const next: RunState = { ...state, nodes: { ...state.nodes }, stats: { ...state.stats } };
  const touched = new Set<string>();
  const logged: RunEvent[] = [];

  // A node is copied the first time this batch changes it.
  const node = (id: string): NodeState => {
    if (!touched.has(id)) {
      const old = next.nodes[id] ?? blankNode(id);
      next.nodes[id] = {
        ...old,
        stepRuns: [...old.stepRuns],
        tools: [...old.tools],
        llm: [...old.llm],
        messages: [...old.messages],
        agentMessages: [...old.agentMessages],
        reports: [...old.reports],
        turns: [...old.turns],
        structured: [...old.structured],
        update: { ...old.update },
      };
      if (!next.order.includes(id)) next.order = [...next.order, id];
      touched.add(id);
    }
    return next.nodes[id];
  };

  for (const e of fresh) {
    next.lastSeq = e.seq;
    if (e.type !== "token") logged.push(e);
    const id = e.node ?? "";

    switch (e.type) {
      case "run_started": {
        next.ticker = e.ticker;
        next.tradeDate = e.trade_date;
        next.analysts = e.analysts ?? [];
        next.resumed = !!e.resumed;
        next.instrument = e.instrument_context;
        next.settings = e.settings;
        next.startedAt = e.ts;
        next.status = "running";
        next.phases = e.graph?.phases ?? [];
        for (const n of e.graph?.nodes ?? []) {
          if (!next.nodes[n.id]) next.nodes[n.id] = blankNode(n.id, n.phase);
        }
        next.order = (e.graph?.nodes ?? []).map((n: { id: string }) => n.id);
        break;
      }
      case "node_started": {
        const n = node(id);
        n.stepRuns.push({
          taskId: e.task_id ?? `${id}-${e.seq}`,
          step: e.step ?? null,
          startedAt: e.ts,
        });
        if (e.step == null) {
          n.status = "running";
          n.startedAt = n.startedAt ?? e.ts;
          n.lastStartedAt = e.ts;
          n.error = null;
        } else {
          if (n.status === "pending") {
            n.status = "running";
            n.startedAt = n.startedAt ?? e.ts;
            n.lastStartedAt = n.lastStartedAt ?? e.ts;
          }
          n.step = e.step;
        }
        break;
      }
      case "node_finished": {
        const n = node(id);
        const stepIdx = n.stepRuns.findIndex((sr) => sr.taskId === e.task_id && sr.finishedAt == null);
        if (stepIdx >= 0) {
          n.stepRuns[stepIdx] = {
            ...n.stepRuns[stepIdx],
            finishedAt: e.ts,
            duration: e.duration_s,
            error: e.error,
          };
        }
        if (e.step == null) {
          n.status = e.error ? "failed" : "done";
          n.finishedAt = e.ts;
          if (e.duration_s != null) {
            n.duration = (n.duration ?? 0) + e.duration_s;
          }
          n.lastStartedAt = undefined;
          n.error = e.error;
          n.step = null;
          n.liveThinking = "";
          Object.assign(n.update, e.update ?? {});
        } else if (n.step === e.step) {
          n.step = null;
        }
        break;
      }
      case "tool_call": {
        node(id).tools.push({
          callId: e.call_id,
          name: e.name,
          args: e.args ?? {},
          step: e.step ?? null,
          ts: e.ts,
        });
        next.stats.tool_calls += 1;
        break;
      }
      case "tool_result": {
        const n = node(id);
        const index = n.tools.findIndex((t) => t.callId === e.call_id && !t.result);
        const result = {
          status: e.status,
          usable: !!e.usable,
          chars: e.chars,
          content: e.content ?? "",
          truncated: !!e.truncated,
          ts: e.ts,
        };
        if (index >= 0) {
          const prev = n.tools[index];
          const duration = prev.ts ? Math.max(0, e.ts - prev.ts) : null;
          n.tools[index] = { ...prev, duration, result };
        } else {
          n.tools.push({ callId: e.call_id, name: e.name, args: {}, step: e.step ?? null, ts: e.ts, result });
        }
        break;
      }
      case "llm_call": {
        const n = node(id);
        n.llm.push({
          model: e.model,
          step: e.step ?? null,
          tokensIn: e.tokens_in ?? 0,
          tokensOut: e.tokens_out ?? 0,
          duration: e.duration_s,
          error: e.error,
          prompts: e.prompts,
          reasoning: e.reasoning ?? null,
          ts: e.ts,
        });
        n.liveThinking = "";
        next.stats.llm_calls += 1;
        next.stats.tokens_in += e.tokens_in ?? 0;
        next.stats.tokens_out += e.tokens_out ?? 0;
        break;
      }
      case "token": {
        const n = node(id);
        if (e.text) n.liveText += e.text;
        if (e.thinking) n.liveThinking += e.thinking;
        break;
      }
      case "message": {
        const n = node(id);
        if (e.content) n.messages.push(e.content);
        n.agentMessages.push({
          content: e.content ?? "",
          reasoning: e.reasoning ?? null,
          step: e.step ?? null,
          ts: e.ts,
        });
        break;
      }
      case "report": {
        const n = node(id);
        n.reports.push({ section: e.section, content: e.content, ts: e.ts });
        n.liveText = "";
        n.liveThinking = "";
        break;
      }
      case "debate_turn": {
        const n = node(id);
        n.turns.push({ node: id, debate: e.debate, turn: e.turn, content: e.content, seq: e.seq, ts: e.ts });
        n.liveText = "";
        n.liveThinking = "";
        break;
      }
      case "structured_output": {
        node(id).structured.push({ schema: e.schema, data: e.data ?? {}, ts: e.ts });
        break;
      }
      default: {
        if (e.type in ENDINGS) {
          next.status = ENDINGS[e.type];
          next.endedAt = e.ts;
          next.durationS = e.duration_s;
          if (e.stats) next.stats = { ...e.stats };
          if (e.type === "run_finished") next.rating = e.rating;
          if (e.type === "run_failed") next.error = `${e.error_type}: ${e.message}`;
          for (const nid of Object.keys(next.nodes)) {
            if (next.nodes[nid].status === "running") {
              const n = node(nid);
              n.status = "stopped";
              n.step = null;
              n.lastStartedAt = undefined;
              n.liveThinking = "";
            }
          }
        }
      }
    }
  }

  if (logged.length) {
    const log = [...state.log, ...logged];
    next.log = log.length > LOG_LIMIT ? log.slice(log.length - LOG_LIMIT) : log;
  }
  return next;
}

/** Total elapsed seconds for a node, including accumulated rounds and any active turn. */
export function nodeElapsed(n: NodeState, now: number): number | undefined {
  if (n.status === "running") {
    const activeStart = n.lastStartedAt ?? n.startedAt;
    const current = activeStart != null ? Math.max(0, now - activeStart) : 0;
    return (n.duration ?? 0) + current;
  }
  return n.duration ?? undefined;
}

/** A node that never started in a run that is over was skipped (the evidence check can end a run early). */
export function displayStatus(run: RunState, n: NodeState): NodeStatus | "skipped" {
  if (n.status === "pending" && run.status !== "running") return "skipped";
  return n.status;
}

/** Every turn of ``debate`` across its speakers, in the order they spoke. */
export function debateTurns(run: RunState, debate: string): DebateTurn[] {
  return Object.values(run.nodes)
    .flatMap((n) => n.turns)
    .filter((t) => t.debate === debate)
    .sort((a, b) => a.seq - b.seq);
}

export function structuredOf(run: RunState, nodeId: string, schema: string): Record<string, unknown> | undefined {
  return run.nodes[nodeId]?.structured.find((s) => s.schema === schema)?.data;
}
