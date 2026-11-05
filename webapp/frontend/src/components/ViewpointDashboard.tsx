import { useState } from "react";

import { CONFIDENCE_LABELS, NODE_STATUS_LABELS, STANCE_BAND_LABELS } from "../labels";
import { structuredOf, type RunState } from "../runState";
import {
  computeAnalystConsensus,
  extractAnalystViewpoints,
  extractInvestmentDebateRounds,
  parseReportViewpoint,
  type AnalystViewpoint,
  type ConfidenceLevel,
  type ResearcherTurnViewpoint,
  type StanceBand,
} from "../viewpoint";
import { NodeStatusDot, RatingBadge } from "./Badges";
import { Markdown } from "./Markdown";

export function StancePill({ stance }: { stance: StanceBand | string | null | undefined }) {
  if (!stance) return <span className="pill">待提炼</span>;
  const info = STANCE_BAND_LABELS[stance];
  const tone = info?.tone ?? "level";
  return <span className={`rating rating-${tone}`}>{info?.label ?? stance}</span>;
}

export function ConfidencePill({ confidence }: { confidence: ConfidenceLevel | null | undefined }) {
  if (!confidence) return null;
  return <span className="pill">{CONFIDENCE_LABELS[confidence] ?? confidence}</span>;
}

/** 0–10 bidirectional score bar centered at 5.0 (left < 5 bearish, right > 5 bullish). */
export function DivergingScoreBar({
  score,
  stance,
  label = "观点评分",
}: {
  score: number | null;
  stance?: StanceBand | null;
  label?: string;
}) {
  if (score == null) {
    return (
      <div className="vp-score-row muted small">
        <span>{label}</span>
        <span>暂无量化分</span>
      </div>
    );
  }
  const clamped = Math.max(0, Math.min(10, score));
  const tone =
    stance && STANCE_BAND_LABELS[stance]
      ? STANCE_BAND_LABELS[stance].tone
      : clamped >= 5.5
        ? "up"
        : clamped <= 4.5
          ? "down"
          : "level";

  // Center is 50%. Score > 5 fills from 50% rightward; score < 5 fills from left toward 50%.
  const isRight = clamped >= 5;
  const widthPct = Math.max(3, Math.abs(clamped - 5) * 10);
  const leftPct = isRight ? 50 : 50 - widthPct;

  return (
    <div className="vp-score-block">
      <div className="vp-score-row small">
        <span className="muted">{label}</span>
        <strong className={`vp-score-num tone-text-${tone}`}>{clamped.toFixed(1)} / 10</strong>
      </div>
      <div className="vp-diverging-track" role="meter" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={10}>
        <div className="vp-center-tick" aria-hidden />
        <div
          className={`vp-diverging-fill fill-${tone}`}
          style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
        />
        <div className={`vp-score-thumb thumb-${tone}`} style={{ left: `${clamped * 10}%` }} />
      </div>
      <div className="vp-scale-ends muted">
        <span>0 看空</span>
        <span>5 中性</span>
        <span>10 看多</span>
      </div>
    </div>
  );
}

interface Props {
  run: RunState;
  selectedNode: string | null;
  onSelectNode: (nodeId: string) => void;
  onOpenDebateTab: () => void;
}

export function ViewpointDashboard({ run, selectedNode, onSelectNode, onOpenDebateTab }: Props) {
  const analystViews = extractAnalystViewpoints(run);
  const consensus = computeAnalystConsensus(analystViews);
  const debateRounds = extractInvestmentDebateRounds(run);
  const [roundIdx, setRoundIdx] = useState<number>(-1); // -1 means latest round

  const rmStructured = structuredOf(run, "Research Manager", "research_plan");
  const rmReport = run.nodes["Research Manager"]?.reports[0]?.content ?? "";
  const rmParsed = parseReportViewpoint(rmReport);

  const anyAnalystActive = analystViews.some((v) => v.hasReport || v.status === "running");
  const anyDebateActive =
    debateRounds.length > 0 ||
    run.nodes["Bull Researcher"]?.status === "running" ||
    run.nodes["Bear Researcher"]?.status === "running" ||
    Boolean(rmStructured || rmReport);

  if (!anyAnalystActive && !anyDebateActive) {
    return null;
  }

  const effectiveRoundIdx =
    roundIdx >= 0 && roundIdx < debateRounds.length ? roundIdx : Math.max(0, debateRounds.length - 1);
  const activeRound = debateRounds[effectiveRoundIdx];

  return (
    <section className="panel vp-dashboard" aria-label="核心观点与数据看板">
      <header className="vp-head">
        <div>
          <h2 className="panel-title">
            核心观点看板 <span className="muted small">Executive Summary · 分析师四维信号与多空研究员交锋提炼</span>
          </h2>
        </div>
        {consensus.completedCount > 0 && (
          <div className="vp-consensus-badges">
            <span className="muted small">
              已完成 {consensus.completedCount}/{consensus.totalSelected} 位分析师
            </span>
            {consensus.agreementLabel && <span className="pill">{consensus.agreementLabel}</span>}
            {consensus.consensusBand && <StancePill stance={consensus.consensusBand} />}
            {consensus.averageScore != null && (
              <strong className="vp-avg-score">综合均值 {consensus.averageScore.toFixed(1)}/10</strong>
            )}
          </div>
        )}
      </header>

      {/* Analyst Consensus Spectrum Ruler */}
      {consensus.completedCount > 0 && consensus.averageScore != null && (
        <div className="vp-consensus-bar">
          <div className="vp-consensus-meta small">
            <div className="vp-stance-counts">
              <span className="vp-count-item rise-text">看多/偏多 {consensus.bullCount}</span>
              <span className="muted">·</span>
              <span className="vp-count-item muted">中性/分歧 {consensus.neutralCount}</span>
              <span className="muted">·</span>
              <span className="vp-count-item fall-text">看空/偏空 {consensus.bearCount}</span>
            </div>
            <span className="muted">圆点代表各维度分析师在 0–10 多空光谱上的落位</span>
          </div>
          <div className="vp-spectrum-track">
            <div className="vp-center-tick" />
            {analystViews
              .filter((v) => v.score != null)
              .map((v) => {
                const tone = v.stance ? STANCE_BAND_LABELS[v.stance]?.tone ?? "level" : "level";
                return (
                  <button
                    key={v.key}
                    type="button"
                    className={`vp-spectrum-pin pin-${tone}`}
                    style={{ left: `${Math.max(2, Math.min(98, (v.score ?? 5) * 10))}%` }}
                    title={`${v.label}：${v.score?.toFixed(1)}/10`}
                    onClick={() => onSelectNode(v.nodeId)}
                  >
                    <span>{v.shortLabel}</span>
                  </button>
                );
              })}
            <div
              className="vp-spectrum-avg"
              style={{ left: `${Math.max(1, Math.min(99, consensus.averageScore * 10))}%` }}
              title={`综合均值 ${consensus.averageScore.toFixed(1)}/10`}
            />
          </div>
        </div>
      )}

      {/* 4-Analyst Viewpoint Cards Grid */}
      <div className="vp-analyst-grid">
        {analystViews.map((v) => (
          <AnalystViewpointCard
            key={v.key}
            view={v}
            selected={selectedNode === v.nodeId}
            onSelect={() => onSelectNode(v.nodeId)}
          />
        ))}
      </div>

      {/* Bull vs Bear Researcher Debate & Research Manager Verdict */}
      {anyDebateActive && (
        <div className="vp-research-section">
          <div className="vp-research-head">
            <div>
              <h3>
                多空研究员交锋提炼
                <span className="muted small"> · Bull vs. Bear Executive Summary</span>
              </h3>
            </div>
            <div className="vp-research-controls">
              {debateRounds.length > 1 && (
                <div className="segmented small" role="tablist" aria-label="选择辩论轮次">
                  {debateRounds.map((_, idx) => (
                    <button
                      key={idx}
                      type="button"
                      className={effectiveRoundIdx === idx ? "on" : ""}
                      onClick={() => setRoundIdx(idx)}
                    >
                      第 {idx + 1} 轮
                    </button>
                  ))}
                </div>
              )}
              <button type="button" className="link small" onClick={onOpenDebateTab}>
                打开完整辩论视图 →
              </button>
            </div>
          </div>

          {/* Conviction Tug-of-War Bar */}
          {activeRound && <ConvictionTugOfWar bull={activeRound.bull} bear={activeRound.bear} />}

          {/* Side-by-Side Bull & Bear Executive Summary Cards */}
          <div className="vp-debate-cards">
            <ResearcherSummaryCard
              side="bull"
              title="多头研究员 (Bull)"
              turn={activeRound?.bull}
              running={run.nodes["Bull Researcher"]?.status === "running"}
              onSelect={() => onSelectNode("Bull Researcher")}
            />
            <ResearcherSummaryCard
              side="bear"
              title="空头研究员 (Bear)"
              turn={activeRound?.bear}
              running={run.nodes["Bear Researcher"]?.status === "running"}
              onSelect={() => onSelectNode("Bear Researcher")}
            />
          </div>

          {/* Research Manager Verdict Bar */}
          {(rmStructured || rmReport || run.nodes["Research Manager"]?.status === "running") && (
            <ResearchManagerVerdictCard
              structured={rmStructured}
              fallbackSummary={rmParsed.executiveSummary}
              running={run.nodes["Research Manager"]?.status === "running"}
              onSelect={() => onSelectNode("Research Manager")}
            />
          )}
        </div>
      )}
    </section>
  );
}

function AnalystViewpointCard({
  view,
  selected,
  onSelect,
}: {
  view: AnalystViewpoint;
  selected: boolean;
  onSelect: () => void;
}) {
  const tone = view.stance ? STANCE_BAND_LABELS[view.stance]?.tone ?? "level" : "level";
  return (
    <article className={`vp-card vp-card-${tone}${selected ? " vp-card-selected" : ""}`}>
      <div className="vp-card-top">
        <div className="vp-card-title">
          <NodeStatusDot status={view.status} />
          <strong>{view.label}</strong>
        </div>
        <div className="vp-card-badges">
          {view.hasReport ? (
            <>
              <StancePill stance={view.stance} />
              <ConfidencePill confidence={view.confidence} />
            </>
          ) : (
            <span className="muted small">{NODE_STATUS_LABELS[view.status] ?? view.status}</span>
          )}
        </div>
      </div>

      {view.hasReport ? (
        <>
          <DivergingScoreBar score={view.score} stance={view.stance} label="维度信号强度" />
          {view.executiveSummary && (
            <div className="vp-exec-box">
              <div className="vp-box-label">核心简述 (Executive Summary)</div>
              <p className="vp-exec-text">{view.executiveSummary}</p>
            </div>
          )}
          {view.keyTakeaways.length > 0 && (
            <div className="vp-takeaways">
              <div className="vp-box-label">核心要点</div>
              <ul>
                {view.keyTakeaways.slice(0, 4).map((pt, i) => (
                  <li key={i}>{pt}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : view.status === "running" ? (
        <p className="muted small vp-placeholder">正在采集数据并提炼核心观点…</p>
      ) : (
        <p className="muted small vp-placeholder">等待执行…</p>
      )}

      <div className="vp-card-foot">
        <button type="button" className="link small" onClick={onSelect}>
          {selected ? "当前已选中详情" : "查看完整报告与工具证据 →"}
        </button>
      </div>
    </article>
  );
}

function ConvictionTugOfWar({
  bull,
  bear,
}: {
  bull?: ResearcherTurnViewpoint;
  bear?: ResearcherTurnViewpoint;
}) {
  const bullScore = bull?.conviction ?? (bull ? 7.5 : null);
  const bearScore = bear?.conviction ?? (bear ? 7.5 : null);
  if (bullScore == null && bearScore == null) return null;

  const bVal = bullScore ?? 5;
  const rVal = bearScore ?? 5;
  const total = bVal + rVal || 10;
  const bullPct = Math.round((bVal / total) * 100);
  const bearPct = 100 - bullPct;

  return (
    <div className="vp-tug">
      <div className="vp-tug-labels small">
        <span className="rise-text">
          <strong>多头论据强度</strong> {bullScore != null ? `${bullScore.toFixed(1)}/10` : "—"}
        </span>
        <span className="muted">多空交锋力量对比 ({bullPct}% vs {bearPct}%)</span>
        <span className="fall-text">
          <strong>空头论据强度</strong> {bearScore != null ? `${bearScore.toFixed(1)}/10` : "—"}
        </span>
      </div>
      <div className="vp-tug-track">
        <div className="vp-tug-bull" style={{ width: `${bullPct}%` }} />
        <div className="vp-tug-bear" style={{ width: `${bearPct}%` }} />
      </div>
    </div>
  );
}

function ResearcherSummaryCard({
  side,
  title,
  turn,
  running,
  onSelect,
}: {
  side: "bull" | "bear";
  title: string;
  turn?: ResearcherTurnViewpoint;
  running: boolean;
  onSelect: () => void;
}) {
  return (
    <article className={`vp-researcher-card vp-researcher-${side}`}>
      <div className="vp-card-top">
        <strong>{title}</strong>
        <div className="vp-card-badges">
          {turn && <StancePill stance={side === "bull" ? "Bullish" : "Bearish"} />}
          {turn?.conviction != null && <span className="pill">信心 {turn.conviction.toFixed(1)}/10</span>}
          {running && <span className="pill pill-running">发言中…</span>}
        </div>
      </div>

      {turn ? (
        <>
          {turn.executiveSummary && (
            <div className="vp-exec-box">
              <div className="vp-box-label">核心论点简述 (Executive Summary)</div>
              <p className="vp-exec-text">{turn.executiveSummary}</p>
            </div>
          )}
          {turn.keyArguments.length > 0 && (
            <div className="vp-takeaways">
              <div className="vp-box-label">{side === "bull" ? "多头核心论据" : "空头核心风险与反驳"}</div>
              <ul>
                {turn.keyArguments.slice(0, 4).map((pt, i) => (
                  <li key={i}>{pt}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : running ? (
        <p className="muted small vp-placeholder">正在组织论据并撰写辩论观点…</p>
      ) : (
        <p className="muted small vp-placeholder">尚未发言。</p>
      )}

      <div className="vp-card-foot">
        <button type="button" className="link small" onClick={onSelect}>
          查看研究员节点详情 →
        </button>
      </div>
    </article>
  );
}

function ResearchManagerVerdictCard({
  structured,
  fallbackSummary,
  running,
  onSelect,
}: {
  structured?: Record<string, unknown>;
  fallbackSummary: string;
  running: boolean;
  onSelect: () => void;
}) {
  const rec = structured?.recommendation ? String(structured.recommendation) : null;
  const execSummary =
    (typeof structured?.executive_summary === "string" && structured.executive_summary.trim()) ||
    fallbackSummary ||
    (typeof structured?.rationale === "string" ? structured.rationale : "");
  const bullPts = Array.isArray(structured?.bull_key_points)
    ? structured.bull_key_points.map((x) => String(x)).filter(Boolean)
    : [];
  const bearPts = Array.isArray(structured?.bear_key_points)
    ? structured.bear_key_points.map((x) => String(x)).filter(Boolean)
    : [];

  return (
    <div className="vp-rm-verdict">
      <div className="vp-rm-head">
        <div className="vp-rm-title">
          <strong>研究经理裁决 (Research Manager)</strong>
          {rec && <RatingBadge rating={rec} />}
          {running && <span className="pill pill-running">正在权衡多空论据…</span>}
        </div>
        <button type="button" className="link small" onClick={onSelect}>
          查看投资计划详情 →
        </button>
      </div>
      {execSummary && (
        <div className="vp-rm-summary">
          <Markdown text={execSummary} />
        </div>
      )}
      {(bullPts.length > 0 || bearPts.length > 0) && (
        <div className="vp-rm-points small">
          {bullPts.length > 0 && (
            <div>
              <strong className="rise-text">采纳多头论据：</strong>
              <ul>
                {bullPts.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {bearPts.length > 0 && (
            <div>
              <strong className="fall-text">关注空头风险：</strong>
              <ul>
                {bearPts.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
