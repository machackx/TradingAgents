// What the server sends: see tradingagents/graph/events.py and webapp/server.py.

export type RunStatus = "running" | "finished" | "failed" | "cancelled" | "interrupted";

export interface Stats {
  llm_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
}

export interface RunSummary {
  run_id: string;
  ticker: string;
  trade_date: string;
  status: RunStatus;
  asset_type: string | null;
  analysts: string[];
  resumed: boolean;
  started_at: string | null;
  rating: string | null;
  duration_s: number | null;
  stats: Stats | null;
  error: string | null;
  has_report: boolean;
  events: number;
}

export interface GraphTopology {
  phases: string[];
  nodes: { id: string; phase: string }[];
  edges: { source: string; target: string; conditional: boolean }[];
}

// Every event has these; the rest depends on ``type``.
export interface RunEvent {
  v: number;
  seq: number;
  ts: number;
  run_id: string;
  type: string;
  node?: string | null;
  step?: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [field: string]: any;
}

export interface ModelOption {
  label: string;
  value: string;
}

export interface ProviderOption {
  key: string;
  label: string;
  available?: boolean;
  detail?: string;
  models: { quick: ModelOption[]; deep: ModelOption[] };
}

export type SubscriptionProvider = "chatgpt" | "codex" | "claude-cli" | "antigravity";

export interface SubscriptionAccount {
  provider: SubscriptionProvider;
  label: string;
  installed: boolean;
  authenticated: boolean;
  email: string | null;
  auth_mode: string | null;
  plan: string | null;
  error: string | null;
}

export interface LoginAttempt {
  id: string;
  provider: SubscriptionProvider;
  status: "starting" | "pending" | "succeeded" | "failed" | "cancelled";
  auth_url: string | null;
  input_required?: boolean;
  expires_at: number;
  error: string | null;
}

export interface AccountState {
  accounts: SubscriptionAccount[];
  login: LoginAttempt | null;
  active_run: string | null;
}

export interface AgentRoleSpec {
  key: string;
  node: string;
  tier: "quick" | "deep";
  group: string;
  label: string;
  zh_label: string;
}

export interface AgentModelOverride {
  provider?: string;
  model?: string;
}

export interface Options {
  analysts: { key: string; name: string }[];
  provider: string;
  providers?: ProviderOption[];
  roles?: AgentRoleSpec[];
  models: { quick: ModelOption[]; deep: ModelOption[] };
  defaults: {
    analysts: string[];
    trade_date: string;
    quick_think_provider?: string | null;
    deep_think_provider?: string | null;
    quick_think_llm: string | null;
    deep_think_llm: string | null;
    agent_models?: Record<string, AgentModelOverride>;
    output_language: string | null;
    max_debate_rounds: number | null;
    max_risk_discuss_rounds: number | null;
    openai_reasoning_effort?: "low" | "medium" | "high" | "xhigh" | null;
    google_thinking_level?: "minimal" | "low" | "medium" | "high" | null;
    anthropic_effort?: "low" | "medium" | "high" | "max" | null;
  };
  limits: { rounds: { min: number; max: number } };
}

export interface SavedReport {
  name: string;
  ticker: string;
  html: boolean;
  saved_at: number;
}

export interface RunRequest {
  ticker: string;
  trade_date: string;
  analysts: string[];
  max_debate_rounds?: number;
  max_risk_discuss_rounds?: number;
  output_language?: string;
  quick_think_provider?: string;
  deep_think_provider?: string;
  quick_think_llm?: string;
  deep_think_llm?: string;
  agent_models?: Record<string, AgentModelOverride>;
  openai_reasoning_effort?: "low" | "medium" | "high" | "xhigh";
  google_thinking_level?: "minimal" | "low" | "medium" | "high";
  anthropic_effort?: "low" | "medium" | "high" | "max";
  tokens: boolean;
}

export interface WebSettingsRequest {
  llm_provider?: string;
  quick_think_provider?: string;
  deep_think_provider?: string;
  quick_think_llm?: string;
  deep_think_llm?: string;
  agent_models?: Record<string, AgentModelOverride>;
  max_debate_rounds?: number;
  max_risk_discuss_rounds?: number;
  output_language?: string;
  openai_reasoning_effort?: "low" | "medium" | "high" | "xhigh" | null;
  google_thinking_level?: "minimal" | "low" | "medium" | "high" | null;
  anthropic_effort?: "low" | "medium" | "high" | "max" | null;
}
