import { useState } from "react";

import { fmtClock } from "../format";
import { skipReason } from "../checks";
import { nodeLabel } from "../labels";
import { debateTurns, structuredOf, type DebateTurn, type RunState } from "../runState";
import { RatingBadge } from "./Badges";
import { Markdown } from "./Markdown";
import { StancePill } from "./ViewpointDashboard";
import { extractResearcherTurnViewpoint } from "../viewpoint";

const DEBATES = [
  {
    key: "investment",
    title: "多空辩论",
    speakers: ["Bull Researcher", "Bear Researcher"],
    judge: "Research Manager",
    note: "多头和空头研究员轮流反驳对方，研究经理权衡后给出投资计划。",
  },
  {
    key: "risk",
    title: "风险讨论",
    speakers: ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"],
    judge: "Portfolio Manager",
    note: "三位风控从激进、保守、中性的角度审视交易员的提议，投资组合经理最后拍板。",
  },
];

const SPEAKER_PREFIX = /^(Bull|Bear|Aggressive|Conservative|Neutral) Analyst:\s*/;
const STANCE = /^\s*\*\*Rating\*\*:\s*([^\n]+)\n*/;
const STANCE_LABELS: Record<string, { label: string; tone: string }> = {
  bullish: { label: "看多", tone: "rise" },
  bearish: { label: "看空", tone: "fall" },
  neutral: { label: "中性", tone: "level" },
};

/** A turn's text without the speaker's name, and the stance it opens with, if any. */
function parseTurn(content: string): { stance: string | null; text: string } {
  const text = content.replace(SPEAKER_PREFIX, "");
  const match = text.match(STANCE);
  return match ? { stance: match[1].replace(/\*/g, "").trim(), text: text.slice(match[0].length) } : { stance: null, text };
}

function Stance({ stance }: { stance: string }) {
  const known = STANCE_LABELS[stance.toLowerCase()];
  return <span className={`stance tone-${known?.tone ?? "level"}`}>{known?.label ?? stance}</span>;
}

/** Turns grouped into rounds: a round ends when a speaker comes round again. */
function rounds(turns: DebateTurn[]): DebateTurn[][] {
  const grouped: DebateTurn[][] = [];
  for (const turn of turns) {
    const current = grouped[grouped.length - 1];
    if (!current || current.some((t) => t.node === turn.node)) grouped.push([turn]);
    else current.push(turn);
  }
  return grouped;
}

function Turn({ turn }: { turn: DebateTurn }) {
  const [open, setOpen] = useState(false);
  const vp = turn.debate === "investment" ? extractResearcherTurnViewpoint(turn) : null;
  const { stance, text: rawText } = parseTurn(turn.content);
  const text = vp?.bodyMarkdown || rawText;
  const long = text.length > 700;
  return (
    <div className="turn-card">
      <div className="turn-head small">
        {vp?.stance ? <StancePill stance={vp.stance} /> : stance && <Stance stance={stance} />}
        {vp?.conviction != null && <span className="pill">信心 {vp.conviction.toFixed(1)}/10</span>}
        <span className="muted">
          第 {turn.turn} 次发言 · {fmtClock(turn.ts)}
        </span>
      </div>
      {vp && (vp.executiveSummary || vp.keyArguments.length > 0) && (
        <div className="vp-turn-summary">
          {vp.executiveSummary && (
            <div className="vp-exec-box">
              <div className="vp-box-label">核心观点简述 (Executive Summary)</div>
              <p className="vp-exec-text">{vp.executiveSummary}</p>
            </div>
          )}
          {vp.keyArguments.length > 0 && (
            <div className="vp-takeaways">
              <div className="vp-box-label">本轮论据要点</div>
              <ul>
                {vp.keyArguments.map((arg, i) => (
                  <li key={i}>{arg}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <div className={long && !open ? "clamped" : undefined}>
        <Markdown text={text} />
      </div>
      {long && (
        <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "收起详细辩论" : "展开详细辩论全文"}
        </button>
      )}
    </div>
  );
}

export function DebateView({ run }: { run: RunState }) {
  const over = run.status !== "running";
  return (
    <div className="debates">
      {DEBATES.map((d) => {
        const turns = debateTurns(run, d.key);
        const live = d.speakers.find((s) => run.nodes[s]?.status === "running");
        const judge = run.nodes[d.judge];
        const grouped = rounds(turns);
        if (!turns.length && !live && over) {
          return (
            <section key={d.key} className="panel">
              <h2>{d.title}</h2>
              <p className="muted">这次没有进行{d.title}。{skipReason(run, d.speakers[0])}</p>
            </section>
          );
        }
        return (
          <section key={d.key} className="panel debate">
            <h2>{d.title}</h2>
            <p className="muted small">{d.note}</p>
            <div className="debate-grid" style={{ gridTemplateColumns: `repeat(${d.speakers.length}, minmax(0, 1fr))` }}>
              {d.speakers.map((s) => (
                <h3 key={s} className={`speaker speaker-${s.split(" ")[0].toLowerCase()}`}>
                  {nodeLabel(s)}
                </h3>
              ))}
              {grouped.map((round, i) => (
                <div key={i} className="debate-round" style={{ gridColumn: `1 / span ${d.speakers.length}` }}>
                  <div className="round-label small muted">第 {i + 1} 轮</div>
                  <div className="round-cells" style={{ gridTemplateColumns: `repeat(${d.speakers.length}, minmax(0, 1fr))` }}>
                    {d.speakers.map((s) => {
                      const turn = round.find((t) => t.node === s);
                      const speaking = !turn && live === s && i === grouped.length - 1;
                      return (
                        <div key={s} className={`round-cell speaker-${s.split(" ")[0].toLowerCase()}`}>
                          {turn ? (
                            <Turn turn={turn} />
                          ) : speaking ? (
                            <LiveTurn text={run.nodes[s].liveText} />
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
              {live && grouped.every((round) => round.some((t) => t.node === live)) && (
                <div className="debate-round" style={{ gridColumn: `1 / span ${d.speakers.length}` }}>
                  <div className="round-label small muted">第 {grouped.length + 1} 轮</div>
                  <div className="round-cells" style={{ gridTemplateColumns: `repeat(${d.speakers.length}, minmax(0, 1fr))` }}>
                    {d.speakers.map((s) => (
                      <div key={s} className={`round-cell speaker-${s.split(" ")[0].toLowerCase()}`}>
                        {s === live && <LiveTurn text={run.nodes[s].liveText} />}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <Verdict run={run} judge={d.judge} kind={d.key} status={judge?.status} />
          </section>
        );
      })}
    </div>
  );
}

function LiveTurn({ text }: { text: string }) {
  return (
    <div className="turn-card live">
      <div className="turn-head small">
        正在发言 <span className="caret" aria-hidden />
      </div>
      {text ? <Markdown text={text.replace(SPEAKER_PREFIX, "")} /> : <p className="muted small">思考中…</p>}
    </div>
  );
}

function Verdict({ run, judge, kind, status }: { run: RunState; judge: string; kind: string; status?: string }) {
  const data =
    kind === "investment"
      ? structuredOf(run, judge, "research_plan")
      : structuredOf(run, judge, "portfolio_decision");
  const report = run.nodes[judge]?.reports[0]?.content;
  if (!data && !report) {
    return status === "running" ? <p className="muted verdict">{nodeLabel(judge)}正在权衡…</p> : null;
  }
  const rating = data ? String(kind === "investment" ? data.recommendation : data.rating) : null;
  const execSummary =
    data && kind === "investment" && typeof data.executive_summary === "string" && data.executive_summary.trim()
      ? data.executive_summary.trim()
      : null;
  const reasoning = data ? String((kind === "investment" ? data.rationale : data.executive_summary) ?? "") : null;
  return (
    <div className="verdict">
      <div className="verdict-head">
        <strong>{nodeLabel(judge)}的结论</strong>
        {rating && <RatingBadge rating={kind === "risk" && run.rating ? run.rating : rating} />}
      </div>
      {execSummary && (
        <div className="vp-exec-box" style={{ marginTop: 8 }}>
          <div className="vp-box-label">裁决核心简述 (Executive Summary)</div>
          <p className="vp-exec-text">{execSummary}</p>
        </div>
      )}
      {reasoning ? <Markdown text={reasoning} /> : report && <Markdown text={report} />}
      {data && kind === "investment" && data.strategic_actions != null && (
        <details>
          <summary>执行要点</summary>
          <Markdown text={String(data.strategic_actions)} />
        </details>
      )}
    </div>
  );
}
