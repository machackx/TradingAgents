import { savedReportUrl } from "../api";
import { fmtDuration, fmtStarted } from "../format";
import type { RunSummary, SavedReport } from "../types";
import { RatingBadge, RunStatusPill } from "./Badges";

interface Props {
  runs: RunSummary[];
  reports: SavedReport[];
  selected?: string;
  onSelect: (runId: string) => void;
}

export function RunList({ runs, reports, selected, onSelect }: Props) {
  return (
    <nav className="sidebar" aria-label="分析记录">
      <h2 className="sidebar-title">分析记录</h2>
      {runs.length === 0 && <p className="muted small">还没有从网页发起的分析。</p>}
      <ul className="run-list">
        {runs.map((run) => (
          <li key={run.run_id}>
            <button
              className={`run-item${run.run_id === selected ? " selected" : ""}`}
              onClick={() => onSelect(run.run_id)}
            >
              <span className="run-item-top">
                <strong>{run.ticker}</strong>
                <span className="muted small">{run.trade_date}</span>
                <RatingBadge rating={run.rating} />
              </span>
              <span className="run-item-bottom small muted">
                <RunStatusPill status={run.status} />
                <span>{fmtStarted(run.started_at)}</span>
                {run.duration_s != null && <span>· {fmtDuration(run.duration_s)}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {reports.length > 0 && (
        <details className="saved-reports">
          <summary className="sidebar-title">CLI 保存的报告（{reports.length}）</summary>
          <ul>
            {reports.map((report) => (
              <li key={report.name}>
                {report.html ? (
                  <a href={savedReportUrl(report.name)} target="_blank" rel="noopener noreferrer">
                    {report.ticker}
                    <span className="muted small"> {new Date(report.saved_at * 1000).toLocaleString("zh-CN", { hour12: false })}</span>
                  </a>
                ) : (
                  <span className="muted">{report.name}（仅 Markdown）</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </nav>
  );
}
