import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { NewRunForm } from "./components/NewRunForm";
import { RunList } from "./components/RunList";
import { RunView } from "./components/RunView";
import { SubscriptionAccounts } from "./components/SubscriptionAccounts";
import type { Options, RunSummary, SavedReport, SubscriptionAccount } from "./types";
import { UpDownContext, useUpDownSetting } from "./updown";

type Route = { page: "new" } | { page: "run"; runId: string };

function parseRoute(hash: string): Route {
  const match = hash.match(/^#\/runs\/([0-9a-f]{32})$/);
  return match ? { page: "run", runId: match[1] } : { page: "new" };
}

const REFRESH_MS = 5000;

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [options, setOptions] = useState<Options | null>(null);
  const [accounts, setAccounts] = useState<SubscriptionAccount[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [offline, setOffline] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [updown, toggleUpDown] = useUpDownSetting();

  const formOptions = useMemo(() => options ? {
      ...options,
      providers: options.providers?.map((provider) => {
        const account = accounts.find((item) => item.provider === provider.key);
        return account ? {
          ...provider,
          available: account.authenticated && !account.error,
          detail: account.error || (account.authenticated
            ? account.email || `已连接（${account.auth_mode ?? "订阅"}）`
            : account.installed ? "未登录" : "未安装 CLI"),
        } : provider;
      }),
    } : null, [options, accounts]);

  const refresh = useCallback(async () => {
    try {
      setRuns(await api.runs());
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    api.options().then(setOptions).catch(() => setOffline(true));
    api.reports().then(setReports).catch(() => undefined);
    refresh();
    const id = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const open = (runId: string) => {
    window.location.hash = `#/runs/${runId}`;
  };

  return (
    <UpDownContext.Provider value={updown}>
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          TradingAgents <span className="muted">分析台</span>
        </a>
        {offline && <span className="error small">连不上本机服务（tradingagents-web 是否在运行？）</span>}
        <button
          className="updown"
          onClick={toggleUpDown}
          title="切换涨跌颜色"
          aria-label={`涨跌颜色：${updown === "red" ? "红涨绿跌" : "绿涨红跌"}，点击切换`}
        >
          <span style={{ color: "var(--rise)" }}>涨</span>
          <span style={{ color: "var(--fall)" }}>跌</span>
          <span className="muted small">{updown === "red" ? "红涨绿跌" : "绿涨红跌"}</span>
        </button>
        <a className="button primary" href="#/">
          新建分析
        </a>
      </header>
      <div className="layout">
        <RunList
          runs={runs}
          reports={reports}
          selected={route.page === "run" ? route.runId : undefined}
          onSelect={open}
        />
        <main className="main">
          <SubscriptionAccounts runActive={runs.some((run) => run.status === "running")}
            onAuthBusy={setAuthBusy} onAccountsUpdated={setAccounts} />
          {route.page === "run" ? (
            <RunView key={route.runId} runId={route.runId} onEnded={refresh} />
          ) : (
            <NewRunForm
              options={formOptions}
              authBusy={authBusy}
              onOptionsUpdated={setOptions}
              onStarted={(runId) => {
                refresh();
                open(runId);
              }}
            />
          )}
          <p className="disclaimer muted small">
            仅供研究使用，不构成投资建议。模型结论可能出错，请结合自己的判断。
          </p>
        </main>
      </div>
    </div>
    </UpDownContext.Provider>
  );
}
