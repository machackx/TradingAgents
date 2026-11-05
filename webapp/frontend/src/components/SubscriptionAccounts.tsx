import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { AccountState, SubscriptionAccount, SubscriptionProvider } from "../types";

const LABELS: Record<SubscriptionProvider, string> = {
  chatgpt: "ChatGPT", codex: "Codex", "claude-cli": "Claude", antigravity: "Antigravity",
};

interface Props {
  runActive: boolean;
  onAuthBusy: (busy: boolean) => void;
  onAccountsUpdated: (accounts: SubscriptionAccount[]) => void;
}

function identity(account: SubscriptionAccount): string {
  if (!account.installed) return `未安装 ${LABELS[account.provider]} CLI`;
  if (account.error) return "账号状态读取失败";
  if (!account.authenticated) return "未登录";
  if (["apiKey", "api_key", "api_key_helper", "third_party"].includes(account.auth_mode ?? "")) {
    return "API／外部凭据模式（未使用订阅账号）";
  }
  return account.email ?? "已登录（账号未提供邮箱）";
}

export function SubscriptionAccounts({ runActive, onAuthBusy, onAccountsUpdated }: Props) {
  const [state, setState] = useState<AccountState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const sequence = useRef(0);
  const mounted = useRef(true);
  const pending = state?.login?.status === "starting" || state?.login?.status === "pending";
  const blocked = busy || pending || runActive || !!state?.active_run;

  const load = useCallback(async (refresh = false) => {
    const requestId = ++sequence.current;
    try {
      const next = await api.accounts(refresh);
      if (mounted.current && requestId === sequence.current) {
        setState(next);
        onAccountsUpdated(next.accounts);
        setError(null);
      }
    } catch (reason) {
      if (mounted.current && requestId === sequence.current) {
        setError(reason instanceof Error ? reason.message : "无法读取账号，请重试。");
      }
    }
  }, [onAccountsUpdated]);

  useEffect(() => {
    mounted.current = true;
    load();
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      mounted.current = false;
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => load(), pending ? 1500 : 15000);
    return () => window.clearInterval(timer);
  }, [load, pending]);

  useEffect(() => onAuthBusy(busy || pending), [onAuthBusy, busy, pending]);
  useEffect(() => setCode(""), [state?.login?.id, state?.login?.status]);

  const act = async (action: "login" | "logout" | "cancel", provider?: SubscriptionProvider) => {
    setBusy(true);
    setNotice(null);
    setError(null);
    setCode("");
    // An older poll must not overwrite a newly started or cancelled login.
    sequence.current += 1;
    try {
      if (action === "login" && provider) {
        const result = await api.login(provider);
        setState((current) => current ? { ...current, login: result.login } : current);
      } else if (action === "logout" && provider) {
        const result = await api.logout(provider);
        setNotice(result.message);
      } else if (action === "cancel" && state?.login) {
        const result = await api.cancelLogin(state.login.id);
        setState((current) => current ? { ...current, login: result.login } : current);
        setNotice("已取消登录。");
      }
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账号操作失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const login = state?.login;
  const submitCode = async () => {
    if (!login || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.submitLoginCode(login.id, code);
      setCode("");
      setNotice("授权码已提交，正在确认登录结果…");
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "授权码提交失败，请重试。");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel subscription-accounts" aria-labelledby="subscription-title">
      <div className="account-heading">
        <h1 id="subscription-title">订阅账号</h1>
        <button type="button" disabled={busy} onClick={() => load(true)}>刷新账号</button>
      </div>
      <p className="muted small account-intro">统一管理 ChatGPT、Codex、Claude 和 Antigravity 的本机账号，在各服务商官方页面完成授权。切换账号请先退出，再使用目标账号登录。</p>
      {!state && !error && <p className="muted" role="status">正在读取账号…</p>}
      <div className="account-grid">
        {state?.accounts.map((account) => (
          <article className="account-card" key={account.provider} aria-label={account.label}>
            <div className="account-heading">
              <strong>{account.label}</strong>
              <span className={`pill ${account.authenticated && !account.error ? "pill-finished" : "pill-cancelled"}`}>
                {account.error ? "状态未知" : account.authenticated ? "已连接" : "未连接"}
              </span>
            </div>
            <p className="account-identity">{identity(account)}</p>
            {account.plan && <p className="muted small">订阅方案：{account.plan}</p>}
            {account.error && <p className="error small">{account.error}</p>}
            {!account.installed && <p className="muted small">安装 {LABELS[account.provider]} CLI 后，刷新即可登录。</p>}
            <div className="account-actions">
              <button type="button" className="primary" disabled={blocked || !account.installed}
                onClick={() => act("login", account.provider)}>
                {account.provider === "chatgpt" ? "继续使用 ChatGPT" : `登录 ${LABELS[account.provider]}`}
              </button>
              <button type="button" disabled={blocked || !account.installed || (!account.authenticated && !account.error)}
                onClick={() => act("logout", account.provider)} aria-label={`退出 ${account.label}`}>
                退出登录
              </button>
            </div>
          </article>
        ))}
      </div>
      {pending && login && (
        <div className="account-login" role="status">
          <strong>{LABELS[login.provider]}：{login.auth_url ? "等待浏览器授权" : "正在准备登录…"}</strong>
          <p className="muted small">请在 10 分钟内完成授权，返回后会自动更新当前账号。</p>
          <div className="account-actions">
            {login.auth_url && <a className="button primary" href={login.auth_url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">打开 {LABELS[login.provider]} 官方登录页</a>}
            <button type="button" disabled={busy} onClick={() => act("cancel")}>取消登录</button>
          </div>
          {login.input_required && (
            <div className="account-code">
              <label htmlFor="subscription-auth-code">如果官方页面显示授权码，请粘贴到此完成登录</label>
              <div className="account-actions">
                <input id="subscription-auth-code" type="password" autoComplete="off" value={code}
                  onChange={(event) => setCode(event.target.value)} maxLength={4096}
                  onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); submitCode(); } }} />
                <button type="button" disabled={busy || !code.trim()} onClick={submitCode}>提交授权码</button>
              </div>
            </div>
          )}
        </div>
      )}
      <div aria-live="polite">
        {login?.status === "succeeded" && <p className="save-notice">{LABELS[login.provider]} 登录成功，当前账号已更新。</p>}
        {login?.status === "failed" && <p className="error small" role="alert">{login.error}</p>}
        {notice && <p className="save-notice">{notice}</p>}
        {error && <p className="error small" role="alert">{error}</p>}
      </div>
      {(runActive || state?.active_run) && <p className="muted small">分析正在运行，结束或取消后可切换账号。</p>}
      <p className="muted small account-footnote">四家账号分别管理。CLI 登录状态与使用同一凭据的本机工具共享，退出也会影响这些工具。Claude 订阅通道使用本机保存的账号，API 通道仍使用独立密钥。</p>
    </section>
  );
}
