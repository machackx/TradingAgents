import { ANALYST_LABELS, NODE_LABELS } from "./labels";
import { debateTurns, displayStatus, structuredOf, type DebateTurn, type NodeStatus, type RunState } from "./runState";

export type StanceBand =
  | "Bullish"
  | "Mildly Bullish"
  | "Neutral"
  | "Mixed"
  | "Mildly Bearish"
  | "Bearish";

export type ConfidenceLevel = "high" | "medium" | "low";

export interface AnalystViewpoint {
  key: string;
  nodeId: string;
  label: string;
  shortLabel: string;
  status: NodeStatus | "skipped";
  hasReport: boolean;
  stance: StanceBand | null;
  score: number | null; // 0 to 10
  confidence: ConfidenceLevel | null;
  executiveSummary: string;
  keyTakeaways: string[];
  bodyMarkdown: string;
}

export interface ResearcherTurnViewpoint {
  turn: number;
  nodeId: string;
  side: "bull" | "bear" | "other";
  stance: StanceBand | null;
  conviction: number | null; // 0 to 10
  executiveSummary: string;
  keyArguments: string[];
  bodyMarkdown: string;
  ts: number;
}

export interface AnalystConsensus {
  completedCount: number;
  totalSelected: number;
  averageScore: number | null;
  consensusBand: StanceBand | null;
  agreementLabel: string | null;
  bullCount: number;
  neutralCount: number;
  bearCount: number;
}

export const ANALYST_DEFS: { key: string; nodeId: string; section: string; short: string }[] = [
  { key: "market", nodeId: "Market Analyst", section: "market_report", short: "技术" },
  { key: "social", nodeId: "Sentiment Analyst", section: "sentiment_report", short: "情绪" },
  { key: "news", nodeId: "News Analyst", section: "news_report", short: "新闻" },
  { key: "fundamentals", nodeId: "Fundamentals Analyst", section: "fundamentals_report", short: "基本面" },
];

const STANCE_ALIASES: Record<string, StanceBand> = {
  bullish: "Bullish",
  "mildly bullish": "Mildly Bullish",
  neutral: "Neutral",
  mixed: "Mixed",
  "mildly bearish": "Mildly Bearish",
  bearish: "Bearish",
  看多: "Bullish",
  偏多: "Mildly Bullish",
  温和看多: "Mildly Bullish",
  中性: "Neutral",
  分歧: "Mixed",
  震荡: "Mixed",
  偏空: "Mildly Bearish",
  温和看空: "Mildly Bearish",
  看空: "Bearish",
};

const DEFAULT_SCORE_BY_STANCE: Record<StanceBand, number> = {
  Bullish: 7.8,
  "Mildly Bullish": 6.2,
  Neutral: 5.0,
  Mixed: 5.0,
  "Mildly Bearish": 3.8,
  Bearish: 2.2,
};

const CONFIDENCE_ALIASES: Record<string, ConfidenceLevel> = {
  high: "high",
  medium: "medium",
  med: "medium",
  low: "low",
  高: "high",
  中: "medium",
  中等: "medium",
  低: "low",
};

const SPEAKER_PREFIX_RE = /^\s*(?:Bull|Bear|Aggressive|Conservative|Neutral)\s+Analyst:\s*/i;
const STANCE_LINE_RE =
  /^\s*(?:\*\*|##?\s*)?(?:Overall\s+(?:Stance|Sentiment)|Stance|Rating|立场|整体情绪|综合立场)\s*(?:\*\*)?\s*[:：]\s*(.+)$/i;
const SCORE_IN_LINE_RE = /(?:Score|Conviction|评分|得分|信心|强度)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:\/\s*10)?/i;
const CONFIDENCE_LINE_RE = /^\s*(?:\*\*|##?\s*)?(?:Confidence|置信度|把握度)\s*(?:\*\*)?\s*[:：]\s*(.+)$/i;
const EXEC_SUMMARY_LINE_RE =
  /^\s*(?:\*\*|##?\s*)?(?:Executive\s+Summary|Summary|核心观点|简述|执行摘要|核心结论)\s*(?:\*\*)?\s*[:：]\s*(.*)$/i;
const TAKEAWAYS_HEADER_RE =
  /^\s*(?:\*\*|##?\s*)?(?:Key\s+(?:Takeaways|Arguments|Points)|核心(?:要点|论据|论点)|关键要点)\s*(?:\*\*)?\s*[:：]?\s*$/i;
const BULLET_LINE_RE = /^\s*(?:[-*•]|\d+[.)])\s+(.+)$/;

function normalizeStance(raw: string): { stance: StanceBand | null; score: number | null } {
  let score: number | null = null;
  const scoreMatch = raw.match(SCORE_IN_LINE_RE);
  if (scoreMatch) {
    const val = Number(scoreMatch[1]);
    if (Number.isFinite(val) && val >= 0 && val <= 10) {
      score = Math.round(val * 10) / 10;
    }
  }
  const cleaned = raw
    .replace(/\(.*?\)|（.*?）/g, "")
    .replace(/[*`]/g, "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
  let stance: StanceBand | null = STANCE_ALIASES[cleaned] ?? null;
  if (!stance) {
    const entries = Object.entries(STANCE_ALIASES).sort((a, b) => b[0].length - a[0].length);
    for (const [alias, canonical] of entries) {
      if (cleaned.includes(alias)) {
        stance = canonical;
        break;
      }
    }
  }
  if (score === null && stance) {
    score = DEFAULT_SCORE_BY_STANCE[stance];
  }
  return { stance, score };
}

function normalizeConfidence(raw: string): ConfidenceLevel | null {
  const cleaned = raw.replace(/\*/g, "").trim().toLowerCase();
  for (const token of cleaned.split(/[\s,;/()（）]+/)) {
    if (token in CONFIDENCE_ALIASES) {
      return CONFIDENCE_ALIASES[token];
    }
  }
  return null;
}

export function bandFromScore(score: number): StanceBand {
  if (score >= 6.5) return "Bullish";
  if (score >= 5.5) return "Mildly Bullish";
  if (score > 4.5) return "Neutral";
  if (score >= 3.5) return "Mildly Bearish";
  return "Bearish";
}

export function parseReportViewpoint(
  text: string,
  structured?: Record<string, unknown>,
): {
  stance: StanceBand | null;
  score: number | null;
  confidence: ConfidenceLevel | null;
  executiveSummary: string;
  keyTakeaways: string[];
  bodyMarkdown: string;
} {
  let stance: StanceBand | null = null;
  let score: number | null = null;
  let confidence: ConfidenceLevel | null = null;
  let executiveSummary = "";
  let keyTakeaways: string[] = [];

  if (structured) {
    if (typeof structured.overall_band === "string") {
      stance = STANCE_ALIASES[structured.overall_band.toLowerCase()] ?? (structured.overall_band as StanceBand);
    }
    if (structured.overall_score != null && Number.isFinite(Number(structured.overall_score))) {
      score = Math.round(Number(structured.overall_score) * 10) / 10;
    }
    if (typeof structured.confidence === "string") {
      confidence = normalizeConfidence(structured.confidence);
    }
    if (typeof structured.executive_summary === "string" && structured.executive_summary.trim()) {
      executiveSummary = structured.executive_summary.trim();
    }
    if (Array.isArray(structured.key_takeaways)) {
      keyTakeaways = structured.key_takeaways
        .map((x) => String(x ?? "").trim().replace(/^[-*•]\s*/, ""))
        .filter(Boolean);
    }
  }

  const rawText = (text ?? "").trim();
  if (!rawText) {
    return {
      stance,
      score,
      confidence,
      executiveSummary,
      keyTakeaways,
      bodyMarkdown: "",
    };
  }

  const lines = rawText.split(/\r?\n/);
  let idx = 0;
  let inTakeaways = false;
  let inSummary = false;
  const summaryParts: string[] = [];

  while (idx < lines.length && idx < 38) {
    const stripped = lines[idx].trim();

    if (!stripped) {
      if (inSummary && summaryParts.length > 0) {
        inSummary = false;
      }
      idx += 1;
      continue;
    }

    if (stripped === "---" || stripped === "***" || stripped === "___") {
      idx += 1;
      break;
    }

    const mStance = stripped.match(STANCE_LINE_RE);
    if (mStance) {
      const parsed = normalizeStance(mStance[1]);
      if (stance === null && parsed.stance) stance = parsed.stance;
      if (score === null && parsed.score !== null) score = parsed.score;
      inTakeaways = false;
      inSummary = false;
      idx += 1;
      continue;
    }

    const mConf = stripped.match(CONFIDENCE_LINE_RE);
    if (mConf) {
      if (confidence === null) {
        confidence = normalizeConfidence(mConf[1]);
      }
      inTakeaways = false;
      inSummary = false;
      idx += 1;
      continue;
    }

    const mExec = stripped.match(EXEC_SUMMARY_LINE_RE);
    if (mExec) {
      const rest = mExec[1].trim();
      if (rest) summaryParts.push(rest);
      inSummary = true;
      inTakeaways = false;
      idx += 1;
      continue;
    }

    if (TAKEAWAYS_HEADER_RE.test(stripped)) {
      inTakeaways = true;
      inSummary = false;
      idx += 1;
      continue;
    }

    const mBullet = stripped.match(BULLET_LINE_RE);
    if (inTakeaways && mBullet) {
      const pt = mBullet[1].trim();
      if (pt && keyTakeaways.length < 5) {
        keyTakeaways.push(pt);
      }
      idx += 1;
      continue;
    }

    if (inSummary && !/^(?:#|\*\*|[-*•]|\|)/.test(stripped)) {
      summaryParts.push(stripped);
      idx += 1;
      continue;
    }

    break;
  }

  if (!executiveSummary && summaryParts.length > 0) {
    executiveSummary = summaryParts.join(" ").trim();
  }

  let bodyMarkdown = idx > 0 ? lines.slice(idx).join("\n").trim() : rawText;
  if (!bodyMarkdown) {
    bodyMarkdown = rawText;
  }

  // Fallback for legacy runs without an explicit Executive Summary header
  if (!executiveSummary) {
    const blocks = bodyMarkdown.split(/\n\s*\n/);
    for (const block of blocks) {
      const p = block.trim();
      if (!p || /^(?:#|\||[-*•]|\*\*Overall|\*\*Confidence)/.test(p)) continue;
      const clean = p.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\s+/g, " ").trim();
      if (clean.length >= 16) {
        executiveSummary = clean.length > 220 ? `${clean.slice(0, 220)}…` : clean;
        break;
      }
    }
  }

  if (keyTakeaways.length === 0) {
    for (const line of bodyMarkdown.split(/\r?\n/)) {
      const m = line.trim().match(BULLET_LINE_RE);
      if (m) {
        const pt = m[1].trim().replace(/\*\*([^*]+)\*\*/g, "$1");
        if (pt.length >= 10 && pt.length <= 160) {
          keyTakeaways.push(pt);
        }
        if (keyTakeaways.length >= 3) break;
      }
    }
  }

  if (stance && score === null) {
    score = DEFAULT_SCORE_BY_STANCE[stance];
  } else if (!stance && score !== null) {
    stance = bandFromScore(score);
  }

  return {
    stance,
    score,
    confidence,
    executiveSummary,
    keyTakeaways,
    bodyMarkdown,
  };
}

export function extractAnalystViewpoints(run: RunState): AnalystViewpoint[] {
  const selectedKeys = run.analysts.length > 0 ? run.analysts : ANALYST_DEFS.map((d) => d.key);
  return ANALYST_DEFS.filter((d) => selectedKeys.includes(d.key) || run.nodes[d.nodeId]).map((def) => {
    const node = run.nodes[def.nodeId];
    const status = node ? displayStatus(run, node) : "pending";
    const reportText = node?.reports[0]?.content ?? "";
    const structured =
      def.key === "social" ? structuredOf(run, def.nodeId, "sentiment_report") : node?.structured[0]?.data;
    const parsed = parseReportViewpoint(reportText, structured);

    return {
      key: def.key,
      nodeId: def.nodeId,
      label: NODE_LABELS[def.nodeId] ?? def.nodeId,
      shortLabel: ANALYST_LABELS[def.key] ?? def.short,
      status,
      hasReport: Boolean(reportText.trim() || structured),
      stance: parsed.stance,
      score: parsed.score,
      confidence: parsed.confidence,
      executiveSummary: parsed.executiveSummary,
      keyTakeaways: parsed.keyTakeaways,
      bodyMarkdown: parsed.bodyMarkdown,
    };
  });
}

export function computeAnalystConsensus(views: AnalystViewpoint[]): AnalystConsensus {
  const completed = views.filter((v) => v.hasReport);
  const scored = completed.filter((v) => v.score != null);
  let bullCount = 0;
  let neutralCount = 0;
  let bearCount = 0;

  for (const v of completed) {
    if (v.stance === "Bullish" || v.stance === "Mildly Bullish" || (v.stance == null && (v.score ?? 5) >= 5.5)) {
      bullCount += 1;
    } else if (
      v.stance === "Bearish" ||
      v.stance === "Mildly Bearish" ||
      (v.stance == null && (v.score ?? 5) <= 4.5)
    ) {
      bearCount += 1;
    } else {
      neutralCount += 1;
    }
  }

  if (scored.length === 0) {
    return {
      completedCount: completed.length,
      totalSelected: views.length,
      averageScore: null,
      consensusBand: null,
      agreementLabel: null,
      bullCount,
      neutralCount,
      bearCount,
    };
  }

  const sum = scored.reduce((acc, v) => acc + (v.score ?? 5), 0);
  const avg = Math.round((sum / scored.length) * 10) / 10;
  const variance = scored.reduce((acc, v) => acc + Math.pow((v.score ?? 5) - avg, 2), 0) / scored.length;
  const stdDev = Math.sqrt(variance);

  let consensusBand: StanceBand = bandFromScore(avg);
  if (bullCount > 0 && bearCount > 0 && stdDev >= 1.8) {
    consensusBand = "Mixed";
  }

  let agreementLabel = "温和共识";
  if (scored.length >= 2) {
    if (stdDev <= 1.0 && (bullCount === scored.length || bearCount === scored.length || neutralCount === scored.length)) {
      agreementLabel = "高度一致";
    } else if (stdDev >= 1.8 || (bullCount > 0 && bearCount > 0)) {
      agreementLabel = "观点分歧";
    }
  } else {
    agreementLabel = "单维信号";
  }

  return {
    completedCount: completed.length,
    totalSelected: views.length,
    averageScore: avg,
    consensusBand,
    agreementLabel,
    bullCount,
    neutralCount,
    bearCount,
  };
}

export function extractResearcherTurnViewpoint(turn: DebateTurn): ResearcherTurnViewpoint {
  const cleaned = turn.content.replace(SPEAKER_PREFIX_RE, "").trim();
  const parsed = parseReportViewpoint(cleaned);
  const side: "bull" | "bear" | "other" =
    turn.node === "Bull Researcher" ? "bull" : turn.node === "Bear Researcher" ? "bear" : "other";
  let stance = parsed.stance;
  if (!stance) {
    if (side === "bull") stance = "Bullish";
    else if (side === "bear") stance = "Bearish";
  }
  let conviction = parsed.score;
  // If a bear researcher wrote Score: 2.0/10 (bearish on price) instead of Conviction: 8.0/10,
  // convert to conviction strength when score < 4.5 on a Bearish stance unless explicitly labelled Conviction
  if (
    side === "bear" &&
    conviction !== null &&
    conviction < 4.5 &&
    !/conviction|信心|强度/i.test(cleaned.slice(0, 120))
  ) {
    conviction = Math.round((10 - conviction) * 10) / 10;
  }
  return {
    turn: turn.turn,
    nodeId: turn.node,
    side,
    stance,
    conviction,
    executiveSummary: parsed.executiveSummary,
    keyArguments: parsed.keyTakeaways,
    bodyMarkdown: parsed.bodyMarkdown,
    ts: turn.ts,
  };
}

export function extractInvestmentDebateRounds(run: RunState): {
  bull?: ResearcherTurnViewpoint;
  bear?: ResearcherTurnViewpoint;
}[] {
  const turns = debateTurns(run, "investment").map(extractResearcherTurnViewpoint);
  const rounds: { bull?: ResearcherTurnViewpoint; bear?: ResearcherTurnViewpoint }[] = [];
  for (const t of turns) {
    const last = rounds[rounds.length - 1];
    if (!last || (t.side === "bull" && last.bull) || (t.side === "bear" && last.bear)) {
      rounds.push(t.side === "bull" ? { bull: t } : { bear: t });
    } else if (t.side === "bull") {
      last.bull = t;
    } else if (t.side === "bear") {
      last.bear = t;
    }
  }
  return rounds;
}
