import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api, ApiError } from "../api";
import { ANALYST_LABELS } from "../labels";
import type { AgentModelOverride, AgentRoleSpec, ModelOption, Options, ProviderOption, RunRequest } from "../types";

interface Props {
  options: Options | null;
  authBusy?: boolean;
  onOptionsUpdated?: (options: Options) => void;
  onStarted: (runId: string) => void;
}

const LANGUAGES = [
  { label: "中文 (Chinese)", value: "Chinese" },
  { label: "英文 (English)", value: "English" },
  { label: "日文 (Japanese)", value: "Japanese" },
  { label: "韩文 (Korean)", value: "Korean" },
];

const ROLE_GROUP_LABELS: Record<string, string> = {
  analysts: "一、调研分析师团队（工具调用与数据搜集）",
  research: "二、多空研究与研究经理（逻辑辩论与研究定调）",
  trading: "三、交易执行（仓位与入场止损方案）",
  risk: "四、风控辩论团队（激进 / 保守 / 中性三方评估）",
  portfolio: "五、投资组合经理（最终评级与拍板）",
};

const FALLBACK_ROLES: AgentRoleSpec[] = [
  { key: "market_analyst", node: "Market Analyst", tier: "quick", group: "analysts", label: "Market Analyst", zh_label: "市场分析师" },
  { key: "social_analyst", node: "Sentiment Analyst", tier: "quick", group: "analysts", label: "Sentiment Analyst", zh_label: "情绪分析师" },
  { key: "news_analyst", node: "News Analyst", tier: "quick", group: "analysts", label: "News Analyst", zh_label: "新闻分析师" },
  { key: "fundamentals_analyst", node: "Fundamentals Analyst", tier: "quick", group: "analysts", label: "Fundamentals Analyst", zh_label: "基本面分析师" },
  { key: "bull_researcher", node: "Bull Researcher", tier: "quick", group: "research", label: "Bull Researcher", zh_label: "多头研究员" },
  { key: "bear_researcher", node: "Bear Researcher", tier: "quick", group: "research", label: "Bear Researcher", zh_label: "空头研究员" },
  { key: "research_manager", node: "Research Manager", tier: "deep", group: "research", label: "Research Manager", zh_label: "研究经理" },
  { key: "trader", node: "Trader", tier: "quick", group: "trading", label: "Trader", zh_label: "交易员" },
  { key: "aggressive_debator", node: "Aggressive Analyst", tier: "quick", group: "risk", label: "Aggressive Analyst", zh_label: "激进风控" },
  { key: "conservative_debator", node: "Conservative Analyst", tier: "quick", group: "risk", label: "Conservative Analyst", zh_label: "保守风控" },
  { key: "neutral_debator", node: "Neutral Analyst", tier: "quick", group: "risk", label: "Neutral Analyst", zh_label: "中性风控" },
  { key: "portfolio_manager", node: "Portfolio Manager", tier: "deep", group: "portfolio", label: "Portfolio Manager", zh_label: "投资组合经理" },
];

/** Merge quick + deep models for a provider, keeping the preferred tier first and deduplicating. */
function modelsForProvider(
  providersByKey: Record<string, ProviderOption>,
  fallbackModels: { quick: ModelOption[]; deep: ModelOption[] } | undefined,
  providerKey: string,
  tier: "quick" | "deep",
  currentValue?: string,
): ModelOption[] {
  const p = providersByKey[providerKey];
  const primary = p ? p.models[tier] : fallbackModels?.[tier] ?? [];
  const secondary = p ? p.models[tier === "quick" ? "deep" : "quick"] : [];
  const seen = new Set<string>();
  const combined: ModelOption[] = [];
  for (const item of [...primary, ...secondary]) {
    if (item.value === "custom" || seen.has(item.value)) continue;
    seen.add(item.value);
    combined.push(item);
  }
  if (currentValue && !seen.has(currentValue)) {
    combined.unshift({ label: `${currentValue} (当前配置)`, value: currentValue });
  }
  return combined;
}

export function NewRunForm({ options, authBusy = false, onOptionsUpdated, onStarted }: Props) {
  const [ticker, setTicker] = useState("");
  const [tradeDate, setTradeDate] = useState("");
  const [analysts, setAnalysts] = useState<string[]>([]);
  const [debateRounds, setDebateRounds] = useState(1);
  const [riskRounds, setRiskRounds] = useState(1);
  const [language, setLanguage] = useState("");
  const [quickProvider, setQuickProvider] = useState("");
  const [deepProvider, setDeepProvider] = useState("");
  const [quickModel, setQuickModel] = useState("");
  const [deepModel, setDeepModel] = useState("");
  const [agentModels, setAgentModels] = useState<Record<string, AgentModelOverride>>({});
  const [openaiEffort, setOpenaiEffort] = useState<string>("");
  const [googleThinking, setGoogleThinking] = useState<string>("");
  const [anthropicEffort, setAnthropicEffort] = useState<string>("");
  const [tokens, setTokens] = useState(true);
  const [busy, setBusy] = useState(false);
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; activeRun?: string } | null>(null);

  const providers = useMemo<ProviderOption[]>(() => {
    if (options?.providers && options.providers.length > 0) return options.providers;
    if (!options?.provider) return [];
    return [
      {
        key: options.provider,
        label: options.provider,
        models: options.models,
      },
    ];
  }, [options]);

  const providersByKey = useMemo<Record<string, ProviderOption>>(() => {
    const map: Record<string, ProviderOption> = {};
    for (const p of providers) map[p.key] = p;
    return map;
  }, [providers]);

  const roles = useMemo<AgentRoleSpec[]>(() => options?.roles ?? FALLBACK_ROLES, [options]);

  useEffect(() => {
    if (!options) return;
    const d = options.defaults;
    setTradeDate(d.trade_date);
    setAnalysts(d.analysts);
    setDebateRounds(d.max_debate_rounds ?? 1);
    setRiskRounds(d.max_risk_discuss_rounds ?? 1);
    setLanguage(d.output_language ?? "Chinese");
    setQuickProvider(d.quick_think_provider || options.provider || "");
    setDeepProvider(d.deep_think_provider || options.provider || "");
    setQuickModel(d.quick_think_llm ?? "");
    setDeepModel(d.deep_think_llm ?? "");
    setAgentModels(d.agent_models ? { ...d.agent_models } : {});
    setOpenaiEffort(d.openai_reasoning_effort ?? "");
    setGoogleThinking(d.google_thinking_level ?? "");
    setAnthropicEffort(d.anthropic_effort ?? "");
  }, [options?.defaults, options?.provider]);

  const toggle = (key: string) =>
    setAnalysts((current) => (current.includes(key) ? current.filter((k) => k !== key) : [...current, key]));

  const handleQuickProviderChange = (nextProvider: string) => {
    setQuickProvider(nextProvider);
    const list = modelsForProvider(providersByKey, options?.models, nextProvider, "quick");
    if (list.length > 0) {
      setQuickModel(list[0].value);
    }
  };

  const handleDeepProviderChange = (nextProvider: string) => {
    setDeepProvider(nextProvider);
    const list = modelsForProvider(providersByKey, options?.models, nextProvider, "deep");
    if (list.length > 0) {
      setDeepModel(list[0].value);
    }
  };

  const handleRoleProviderChange = (role: AgentRoleSpec, nextProvider: string) => {
    setAgentModels((prev) => {
      const next = { ...prev };
      if (!nextProvider) {
        delete next[role.key];
        return next;
      }
      const list = modelsForProvider(providersByKey, options?.models, nextProvider, role.tier);
      const defaultModel =
        list[0]?.value ?? (role.tier === "deep" ? deepModel : quickModel) ?? "";
      next[role.key] = { provider: nextProvider, model: defaultModel };
      return next;
    });
  };

  const handleRoleModelChange = (role: AgentRoleSpec, nextModel: string) => {
    setAgentModels((prev) => {
      const next = { ...prev };
      const existingProv = next[role.key]?.provider;
      if (!nextModel && !existingProv) {
        delete next[role.key];
        return next;
      }
      const effectiveProv = existingProv || (role.tier === "deep" ? deepProvider : quickProvider);
      next[role.key] = { provider: effectiveProv, model: nextModel };
      return next;
    });
  };

  const resetRoleModels = () => setAgentModels({});

  const applyRecommendedSubscriptionPreset = () => {
    setQuickProvider("antigravity");
    setQuickModel("gemini-3.8-flash");
    setDeepProvider("claude-cli");
    setDeepModel("claude-opus-5");
    setAgentModels({
      market_analyst: { provider: "antigravity", model: "gemini-3.8-flash" },
      social_analyst: { provider: "antigravity", model: "gemini-3.8-flash" },
      news_analyst: { provider: "antigravity", model: "gemini-3.8-flash" },
      fundamentals_analyst: { provider: "codex", model: "gpt-6-luna" },
      bull_researcher: { provider: "claude-cli", model: "claude-sonnet-5" },
      bear_researcher: { provider: "codex", model: "gpt-6-luna" },
      research_manager: { provider: "claude-cli", model: "claude-opus-5" },
      trader: { provider: "codex", model: "gpt-6-luna" },
      aggressive_debator: { provider: "antigravity", model: "gemini-3.8-pro" },
      conservative_debator: { provider: "claude-cli", model: "claude-sonnet-5" },
      neutral_debator: { provider: "codex", model: "gpt-6-luna" },
      portfolio_manager: { provider: "codex", model: "gpt-6-sol" },
    });
  };

  async function saveAsDefaults() {
    setError(null);
    setSaveMessage(null);
    setSavingDefaults(true);
    const cleanedAgentModels: Record<string, AgentModelOverride> = {};
    for (const [roleKey, spec] of Object.entries(agentModels)) {
      if (spec && (spec.provider || spec.model)) {
        cleanedAgentModels[roleKey] = spec;
      }
    }
    try {
      const updated = await api.saveSettings({
        llm_provider: quickProvider || options?.provider || "chatgpt",
        quick_think_provider: quickProvider || undefined,
        deep_think_provider: deepProvider || undefined,
        quick_think_llm: quickModel || undefined,
        deep_think_llm: deepModel || undefined,
        agent_models: cleanedAgentModels,
        max_debate_rounds: debateRounds,
        max_risk_discuss_rounds: riskRounds,
        output_language: language || undefined,
        openai_reasoning_effort: (openaiEffort || null) as "low" | "medium" | "high" | "xhigh" | null,
        google_thinking_level: (googleThinking || null) as "minimal" | "low" | "medium" | "high" | null,
        anthropic_effort: (anthropicEffort || null) as "low" | "medium" | "high" | "max" | null,
      });
      onOptionsUpdated?.(updated);
      setSaveMessage("✓ 已保存为本机默认配置（重启后自动生效）");
    } catch (e) {
      setError(e instanceof ApiError ? { message: e.message, activeRun: e.activeRun } : { message: String(e) });
    } finally {
      setSavingDefaults(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (authBusy) return;
    setError(null);
    setSaveMessage(null);
    setBusy(true);
    const d = options?.defaults;
    const defaultQuickProv = d?.quick_think_provider || options?.provider || "";
    const defaultDeepProv = d?.deep_think_provider || options?.provider || "";
    const body: RunRequest = { ticker: ticker.trim(), trade_date: tradeDate, analysts, tokens };
    // Only what differs from the server's config is sent.
    if (debateRounds !== d?.max_debate_rounds) body.max_debate_rounds = debateRounds;
    if (riskRounds !== d?.max_risk_discuss_rounds) body.max_risk_discuss_rounds = riskRounds;
    if (language && language !== d?.output_language) body.output_language = language;
    if (quickProvider && quickProvider !== defaultQuickProv) body.quick_think_provider = quickProvider;
    if (deepProvider && deepProvider !== defaultDeepProv) body.deep_think_provider = deepProvider;
    if (quickModel && quickModel !== d?.quick_think_llm) body.quick_think_llm = quickModel;
    if (deepModel && deepModel !== d?.deep_think_llm) body.deep_think_llm = deepModel;
    if (openaiEffort && openaiEffort !== (d?.openai_reasoning_effort ?? "")) {
      body.openai_reasoning_effort = openaiEffort as RunRequest["openai_reasoning_effort"];
    }
    if (googleThinking && googleThinking !== (d?.google_thinking_level ?? "")) {
      body.google_thinking_level = googleThinking as RunRequest["google_thinking_level"];
    }
    if (anthropicEffort && anthropicEffort !== (d?.anthropic_effort ?? "")) {
      body.anthropic_effort = anthropicEffort as RunRequest["anthropic_effort"];
    }

    const cleanedAgentModels: Record<string, AgentModelOverride> = {};
    for (const [roleKey, spec] of Object.entries(agentModels)) {
      if (spec && (spec.provider || spec.model)) {
        cleanedAgentModels[roleKey] = spec;
      }
    }
    if (Object.keys(cleanedAgentModels).length > 0) {
      body.agent_models = cleanedAgentModels;
    }

    try {
      const run = await api.start(body);
      onStarted(run.run_id);
    } catch (e) {
      setError(e instanceof ApiError ? { message: e.message, activeRun: e.activeRun } : { message: String(e) });
    } finally {
      setBusy(false);
    }
  }

  const { min, max } = options?.limits.rounds ?? { min: 1, max: 5 };
  const rounds = Array.from({ length: max - min + 1 }, (_, i) => min + i);
  const quickModelOptions = modelsForProvider(providersByKey, options?.models, quickProvider, "quick", quickModel);
  const deepModelOptions = modelsForProvider(providersByKey, options?.models, deepProvider, "deep", deepModel);

  const groupedRoles = useMemo(() => {
    const groups: { group: string; title: string; items: AgentRoleSpec[] }[] = [];
    const byGroup: Record<string, AgentRoleSpec[]> = {};
    for (const r of roles) {
      (byGroup[r.group] ??= []).push(r);
    }
    for (const [groupKey, title] of Object.entries(ROLE_GROUP_LABELS)) {
      if (byGroup[groupKey]?.length) {
        groups.push({ group: groupKey, title, items: byGroup[groupKey] });
      }
    }
    return groups;
  }, [roles]);

  const activeOverrideCount = Object.keys(agentModels).length;

  return (
    <form className="panel new-run" onSubmit={submit}>
      <h1>新建分析</h1>
      <p className="muted">
        支持跨厂商组合本地订阅（ChatGPT/Codex OAuth、Antigravity CLI、Claude CLI、Codex CLI）及 API 模型；可按默认层级统一设置，也可为每个调研/决策角色单独指定厂商与模型。
      </p>

      <div className="form-grid">
        <label>
          <span>股票代码</span>
          <input
            required
            autoFocus
            value={ticker}
            maxLength={32}
            placeholder="NVDA、0700.HK、600519.SS、BTC-USD"
            onChange={(e) => setTicker(e.target.value)}
          />
        </label>
        <label>
          <span>分析日期</span>
          <input
            required
            type="date"
            value={tradeDate}
            max={options?.defaults.trade_date}
            onChange={(e) => setTradeDate(e.target.value)}
          />
        </label>
      </div>

      <fieldset>
        <legend>分析师</legend>
        <div className="chips">
          {(options?.analysts ?? []).map((a) => (
            <label key={a.key} className={`chip${analysts.includes(a.key) ? " on" : ""}`}>
              <input type="checkbox" checked={analysts.includes(a.key)} onChange={() => toggle(a.key)} />
              {ANALYST_LABELS[a.key] ?? a.name}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="provider-status-bar">
        <span className="muted small">本机订阅与厂商状态：</span>
        {providers
          .filter((p) => ["chatgpt", "antigravity", "claude-cli", "codex"].includes(p.key) || p.available)
          .map((p) => (
            <span
              key={p.key}
              className={`pill ${p.available === false ? "tone-warn" : "pill-finished"}`}
              title={p.detail ?? p.label}
            >
              {p.available === false ? "○ " : "✓ "}
              {p.label}
              {p.detail ? ` · ${p.detail}` : ""}
            </span>
          ))}
      </div>

      <div className="form-grid">
        <label>
          <span>快速层默认厂商（分析师、辩论、交易员）</span>
          <select value={quickProvider} onChange={(e) => handleQuickProviderChange(e.target.value)}>
            {providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.available === false ? "○ " : "✓ "}
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>快速层默认模型</span>
          <select value={quickModel} onChange={(e) => setQuickModel(e.target.value)}>
            {quickModelOptions.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label} ({m.value})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>深度层默认厂商（研究经理、组合经理）</span>
          <select value={deepProvider} onChange={(e) => handleDeepProviderChange(e.target.value)}>
            {providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.available === false ? "○ " : "✓ "}
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>深度层默认模型</span>
          <select value={deepModel} onChange={(e) => setDeepModel(e.target.value)}>
            {deepModelOptions.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label} ({m.value})
              </option>
            ))}
          </select>
        </label>
      </div>

      <details className="role-models-panel" open>
        <summary className="role-models-summary">
          <span>
            按角色独立选择厂商与模型（12 个角色跨厂商混用）
            {activeOverrideCount > 0 ? (
              <span className="pill pill-running" style={{ marginLeft: 8 }}>
                已单独指定 {activeOverrideCount} 个角色
              </span>
            ) : (
              <span className="muted small" style={{ marginLeft: 8 }}>
                当前全部跟随默认快速/深度层
              </span>
            )}
          </span>
          <span className="role-summary-actions">
            <button
              type="button"
              className="button small"
              onClick={(e) => {
                e.preventDefault();
                applyRecommendedSubscriptionPreset();
              }}
              title="使用 Antigravity + Claude + Codex 跨厂商协作预设"
            >
              一键填入三大本地订阅协作预设
            </button>
            {activeOverrideCount > 0 && (
              <button
                type="button"
                className="button small"
                onClick={(e) => {
                  e.preventDefault();
                  resetRoleModels();
                }}
              >
                重置为全部跟随默认
              </button>
            )}
          </span>
        </summary>

        <div className="role-groups">
          {groupedRoles.map(({ group, title, items }) => (
            <div key={group} className="role-group">
              <h3 className="role-group-title">{title}</h3>
              <div className="role-grid">
                {items.map((role) => {
                  const analystShortKey = role.key.replace(/_analyst$/, "");
                  const isDisabledAnalyst = role.group === "analysts" && !analysts.includes(analystShortKey);
                  const override = agentModels[role.key];
                  const tierDefaultProv = role.tier === "deep" ? deepProvider : quickProvider;
                  const tierDefaultModel = role.tier === "deep" ? deepModel : quickModel;
                  const effectiveProvider = override?.provider || tierDefaultProv;
                  const roleModelOptions = modelsForProvider(
                    providersByKey,
                    options?.models,
                    effectiveProvider,
                    role.tier,
                    override?.model || tierDefaultModel,
                  );
                  const tierLabel = role.tier === "deep" ? "深度层" : "快速层";
                  const defaultProvLabel = providersByKey[tierDefaultProv]?.label ?? tierDefaultProv;

                  return (
                    <div
                      key={role.key}
                      className={`role-card${override ? " role-card-custom" : ""}${isDisabledAnalyst ? " role-card-disabled" : ""}`}
                    >
                      <div className="role-card-head">
                        <strong>{role.zh_label}</strong>
                        <span className="muted small">{role.label}</span>
                        {isDisabledAnalyst && <span className="pill">未启用该分析师</span>}
                        {!isDisabledAnalyst && override && <span className="pill pill-finished">独立配置</span>}
                      </div>
                      <div className="role-card-controls">
                        <label>
                          <span>厂商</span>
                          <select
                            aria-label={`${role.zh_label}厂商`}
                            disabled={isDisabledAnalyst}
                            value={override?.provider ?? ""}
                            onChange={(e) => handleRoleProviderChange(role, e.target.value)}
                          >
                            <option value="">
                              跟随{tierLabel}默认 ({defaultProvLabel})
                            </option>
                            {providers.map((p) => (
                              <option key={p.key} value={p.key}>
                                {p.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>模型</span>
                          <select
                            aria-label={`${role.zh_label}模型`}
                            disabled={isDisabledAnalyst}
                            value={override?.model ?? ""}
                            onChange={(e) => handleRoleModelChange(role, e.target.value)}
                          >
                            {!override?.provider && (
                              <option value="">
                                跟随{tierLabel}默认 ({tierDefaultModel})
                              </option>
                            )}
                            {roleModelOptions.map((m) => (
                              <option key={m.value} value={m.value}>
                                {m.label} ({m.value})
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </details>

      <div className="form-grid">
        <label>
          <span>多空辩论轮数</span>
          <select value={debateRounds} onChange={(e) => setDebateRounds(Number(e.target.value))}>
            {rounds.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>风险讨论轮数</span>
          <select value={riskRounds} onChange={(e) => setRiskRounds(Number(e.target.value))}>
            {rounds.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>报告语言</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={tokens} onChange={(e) => setTokens(e.target.checked)} />
          <span>逐字显示模型输出</span>
        </label>
      </div>

      <details className="role-models-panel">
        <summary className="role-models-summary">
          <span>推理强度与思考深度设置（可选）</span>
          <span className="muted small">针对 Codex/ChatGPT、Antigravity/Google、Claude 的推理档位</span>
        </summary>
        <div className="form-grid" style={{ marginTop: 8 }}>
          <label>
            <span>OpenAI / ChatGPT / Codex 推理强度</span>
            <select value={openaiEffort} onChange={(e) => setOpenaiEffort(e.target.value)}>
              <option value="">默认 (Medium)</option>
              <option value="low">Low（更快）</option>
              <option value="medium">Medium（均衡）</option>
              <option value="high">High（深度推理）</option>
              <option value="xhigh">XHigh（最高推理）</option>
            </select>
          </label>
          <label>
            <span>Google / Antigravity 思考级别</span>
            <select value={googleThinking} onChange={(e) => setGoogleThinking(e.target.value)}>
              <option value="">默认 (High)</option>
              <option value="minimal">Minimal（最快）</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High（深度思考）</option>
            </select>
          </label>
          <label>
            <span>Claude / Anthropic Effort</span>
            <select value={anthropicEffort} onChange={(e) => setAnthropicEffort(e.target.value)}>
              <option value="">默认 (High)</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High（推荐）</option>
              <option value="max">Max（深度分析）</option>
            </select>
          </label>
        </div>
      </details>

      {error && (
        <p className="error" role="alert">
          {error.message}
          {error.activeRun && (
            <>
              {" "}
              <a href={`#/runs/${error.activeRun}`}>查看正在运行的分析</a>
            </>
          )}
        </p>
      )}

      {saveMessage && (
        <p className="save-notice" role="status">
          {saveMessage}
        </p>
      )}

      <div className="form-actions">
        <button className="primary" type="submit" disabled={busy || authBusy || !options || analysts.length === 0}>
          {busy ? "正在启动…" : authBusy ? "请先完成或取消登录" : "开始分析"}
        </button>
        <button
          type="button"
          className="button"
          disabled={savingDefaults || !options}
          onClick={saveAsDefaults}
        >
          {savingDefaults ? "正在保存…" : "保存当前配置为默认"}
        </button>
      </div>
    </form>
  );
}
