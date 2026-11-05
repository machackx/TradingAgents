import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "../api";
import { ANALYST_LABELS } from "../labels";
import type { Options, RunRequest } from "../types";

interface Props {
  options: Options | null;
  onStarted: (runId: string) => void;
}

const LANGUAGES = ["Chinese", "English", "Japanese", "Korean"];

export function NewRunForm({ options, onStarted }: Props) {
  const [ticker, setTicker] = useState("");
  const [tradeDate, setTradeDate] = useState("");
  const [analysts, setAnalysts] = useState<string[]>([]);
  const [debateRounds, setDebateRounds] = useState(1);
  const [riskRounds, setRiskRounds] = useState(1);
  const [language, setLanguage] = useState("");
  const [quickModel, setQuickModel] = useState("");
  const [deepModel, setDeepModel] = useState("");
  const [tokens, setTokens] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; activeRun?: string } | null>(null);

  useEffect(() => {
    if (!options) return;
    const d = options.defaults;
    setTradeDate(d.trade_date);
    setAnalysts(d.analysts);
    setDebateRounds(d.max_debate_rounds ?? 1);
    setRiskRounds(d.max_risk_discuss_rounds ?? 1);
    setLanguage(d.output_language ?? "");
    setQuickModel(d.quick_think_llm ?? "");
    setDeepModel(d.deep_think_llm ?? "");
  }, [options]);

  const toggle = (key: string) =>
    setAnalysts((current) => (current.includes(key) ? current.filter((k) => k !== key) : [...current, key]));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    const d = options?.defaults;
    const body: RunRequest = { ticker: ticker.trim(), trade_date: tradeDate, analysts, tokens };
    // Only what differs from the server's config is sent.
    if (debateRounds !== d?.max_debate_rounds) body.max_debate_rounds = debateRounds;
    if (riskRounds !== d?.max_risk_discuss_rounds) body.max_risk_discuss_rounds = riskRounds;
    if (language && language !== d?.output_language) body.output_language = language;
    if (quickModel && quickModel !== d?.quick_think_llm) body.quick_think_llm = quickModel;
    if (deepModel && deepModel !== d?.deep_think_llm) body.deep_think_llm = deepModel;
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
  const modelChoices = (list: { label: string; value: string }[] | undefined) =>
    (list ?? []).filter((m) => m.value !== "custom");

  return (
    <form className="panel new-run" onSubmit={submit}>
      <h1>新建分析</h1>
      <p className="muted">
        每次分析都会调用 {options?.provider || "已配置的"} 模型，消耗额度；模型服务商和接口地址以本机 .env 为准。
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
          <span>快速模型（分析师、辩论、交易员）</span>
          <input list="quick-models" value={quickModel} onChange={(e) => setQuickModel(e.target.value)} />
          <datalist id="quick-models">
            {modelChoices(options?.models.quick).map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </datalist>
        </label>
        <label>
          <span>深度模型（研究经理、组合经理）</span>
          <input list="deep-models" value={deepModel} onChange={(e) => setDeepModel(e.target.value)} />
          <datalist id="deep-models">
            {modelChoices(options?.models.deep).map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </datalist>
        </label>
        <label>
          <span>报告语言</span>
          <input list="languages" value={language} onChange={(e) => setLanguage(e.target.value)} />
          <datalist id="languages">
            {LANGUAGES.map((l) => (
              <option key={l} value={l} />
            ))}
          </datalist>
        </label>
        <label className="check">
          <input type="checkbox" checked={tokens} onChange={(e) => setTokens(e.target.checked)} />
          <span>逐字显示模型输出</span>
        </label>
      </div>

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

      <button className="primary" type="submit" disabled={busy || !options || analysts.length === 0}>
        {busy ? "正在启动…" : "开始分析"}
      </button>
    </form>
  );
}
