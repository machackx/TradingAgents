import { reportUrl } from "../api";
import { SECTION_LABELS, nodeLabel } from "../labels";
import type { RunState } from "../runState";
import { Markdown } from "./Markdown";

const ORDER = [
  "market_report",
  "sentiment_report",
  "news_report",
  "fundamentals_report",
  "investment_plan",
  "trader_investment_plan",
  "final_trade_decision",
];

/** Every report in the order the run wrote them up, to read through like one document. */
export function ReportView({ run }: { run: RunState }) {
  const reports = Object.values(run.nodes)
    .flatMap((n) => n.reports.map((r) => ({ ...r, node: n.id })))
    .sort((a, b) => ORDER.indexOf(a.section) - ORDER.indexOf(b.section));

  if (!reports.length) {
    return (
      <section className="panel">
        <p className="muted">还没有报告。分析师完成后，报告会出现在这里。</p>
      </section>
    );
  }
  return (
    <section className="panel reports">
      <nav className="report-toc small" aria-label="报告目录">
        {reports.map((r) => (
          <a key={r.section} href={`#section-${r.section}`} onClick={(e) => {
            e.preventDefault();
            document.getElementById(`section-${r.section}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}>
            {SECTION_LABELS[r.section] ?? r.section}
          </a>
        ))}
        {run.summary?.has_report && (
          <a className="toc-page" href={reportUrl(run.runId)} target="_blank" rel="noopener noreferrer">
            打开可打印的完整报告 ↗
          </a>
        )}
      </nav>
      {reports.map((r) => (
        <article key={r.section} id={`section-${r.section}`} className="report-section">
          <h2>
            {SECTION_LABELS[r.section] ?? r.section}
            <span className="muted small"> · {nodeLabel(r.node)}</span>
          </h2>
          <Markdown text={r.content} />
        </article>
      ))}
    </section>
  );
}
