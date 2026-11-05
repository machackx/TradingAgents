import type { AccountState, LoginAttempt, Options, RunRequest, RunSummary, SavedReport, SubscriptionProvider, WebSettingsRequest } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly activeRun?: string,
  ) {
    super(message);
  }
}

// FastAPI's ``detail`` is a string, an object we chose, or a list of validation errors.
function describe(detail: unknown): { message: string; activeRun?: string } {
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      const where = Array.isArray(item?.loc) ? item.loc.filter((p: unknown) => p !== "body").join(".") : "";
      const text = String(item?.msg ?? "").replace(/^Value error, /, "");
      return where ? `${where}: ${text}` : text;
    });
    return { message: parts.join("；") };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; active_run?: string };
    return { message: d.message ?? JSON.stringify(detail), activeRun: d.active_run };
  }
  return { message: "请求失败" };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* not JSON */
    }
    const { message, activeRun } = describe(detail);
    throw new ApiError(message, response.status, activeRun);
  }
  return (await response.json()) as T;
}

export const api = {
  accounts: (refresh = false) => request<AccountState>(`/api/auth${refresh ? "?refresh=true" : ""}`, { cache: "no-store" }),
  login: (provider: SubscriptionProvider) =>
    request<{ login: LoginAttempt }>(`/api/auth/${provider}/login`, {
      method: "POST", headers: { "X-TradingAgents-Auth": "1" },
    }),
  logout: (provider: SubscriptionProvider) =>
    request<{ message: string }>(`/api/auth/${provider}/logout`, {
      method: "POST", headers: { "X-TradingAgents-Auth": "1" },
    }),
  cancelLogin: (loginId: string) =>
    request<{ login: LoginAttempt }>(`/api/auth/logins/${loginId}/cancel`, {
      method: "POST", headers: { "X-TradingAgents-Auth": "1" },
    }),
  options: () => request<Options>("/api/options"),
  saveSettings: (body: WebSettingsRequest) =>
    request<Options>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  runs: () => request<{ runs: RunSummary[] }>("/api/runs").then((r) => r.runs),
  start: (body: RunRequest) =>
    request<{ run: RunSummary }>("/api/runs", { method: "POST", body: JSON.stringify(body) }).then((r) => r.run),
  cancel: (runId: string) => request<unknown>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  reports: () => request<{ reports: SavedReport[] }>("/api/reports").then((r) => r.reports),
};

export const streamUrl = (runId: string) => `/api/runs/${runId}/stream`;
export const reportUrl = (runId: string) => `/api/runs/${runId}/report`;
export const savedReportUrl = (name: string) => `/api/reports/${encodeURIComponent(name)}`;
