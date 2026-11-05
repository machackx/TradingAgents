"""Structured viewpoint & executive-summary instructions and parsers.

Every analyst (Market, Sentiment, News, Fundamentals) and researcher (Bull, Bear)
surfaces a concise Executive Summary, a directional signal/conviction score on a
0–10 scale, and 3–4 bullet takeaways at the top of its output so dashboards and
readers can grasp the core thesis in seconds before diving into the full prose.
"""

from __future__ import annotations

import re
from typing import Any

ANALYST_SUMMARY_INSTRUCTION = """

## Executive Summary Header (Required at the very top of your final report)

Begin your final report with the following structured header block (keep the English field labels and stance/confidence enum values exactly as shown so the dashboard can parse them; write the summary and bullet points in the requested output language):

**Overall Stance**: <Bullish | Mildly Bullish | Neutral | Mixed | Mildly Bearish | Bearish> (Score: <0.0-10.0>/10)
**Confidence**: <High | Medium | Low>
**Executive Summary**: <2–3 sentences distilling your core findings and directional signal>
**Key Takeaways**:
- <Key takeaway 1 with concrete evidence/numbers>
- <Key takeaway 2 with concrete evidence/numbers>
- <Key takeaway 3 with concrete evidence/numbers>

---
(Follow immediately with your detailed analysis report and the closing Markdown summary table.)"""


RESEARCHER_SUMMARY_INSTRUCTION = """

## Executive Summary Header (Required at the very top of your response)

Begin your response with the following structured header block (keep the English field labels and Bullish/Bearish stance value as shown; write the summary and bullet points in the requested output language):

**Stance**: <Bullish | Bearish> (Conviction: <0.0-10.0>/10)
**Executive Summary**: <1–2 sentences summarizing your core thesis and primary rebuttal in this round>
**Key Arguments**:
- <Core evidence-backed argument or rebuttal 1>
- <Core evidence-backed argument or rebuttal 2>
- <Core evidence-backed argument or rebuttal 3>

---
(Follow immediately with your full conversational debate argument.)"""


_STANCE_ALIASES: dict[str, str] = {
    "bullish": "Bullish",
    "mildly bullish": "Mildly Bullish",
    "neutral": "Neutral",
    "mixed": "Mixed",
    "mildly bearish": "Mildly Bearish",
    "bearish": "Bearish",
    "看多": "Bullish",
    "偏多": "Mildly Bullish",
    "温和看多": "Mildly Bullish",
    "中性": "Neutral",
    "分歧": "Mixed",
    "震荡": "Mixed",
    "偏空": "Mildly Bearish",
    "温和看空": "Mildly Bearish",
    "看空": "Bearish",
}

_DEFAULT_SCORE_BY_STANCE: dict[str, float] = {
    "Bullish": 7.8,
    "Mildly Bullish": 6.2,
    "Neutral": 5.0,
    "Mixed": 5.0,
    "Mildly Bearish": 3.8,
    "Bearish": 2.2,
}

_CONFIDENCE_ALIASES: dict[str, str] = {
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
    "高": "high",
    "中": "medium",
    "中等": "medium",
    "低": "low",
}

_SPEAKER_PREFIX_RE = re.compile(r"^\s*(?:Bull|Bear|Aggressive|Conservative|Neutral)\s+Analyst:\s*", re.IGNORECASE)
_STANCE_LINE_RE = re.compile(
    r"^\s*(?:\*\*|##?\s*)?(?:Overall\s+(?:Stance|Sentiment)|Stance|立场|整体情绪|综合立场)\s*(?:\*\*)?\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
_SCORE_IN_LINE_RE = re.compile(
    r"(?:Score|Conviction|评分|得分|信心|强度)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:/\s*10)?",
    re.IGNORECASE,
)
_CONFIDENCE_LINE_RE = re.compile(
    r"^\s*(?:\*\*|##?\s*)?(?:Confidence|置信度|把握度)\s*(?:\*\*)?\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
_EXEC_SUMMARY_LINE_RE = re.compile(
    r"^\s*(?:\*\*|##?\s*)?(?:Executive\s+Summary|Summary|核心观点|简述|执行摘要|核心结论)\s*(?:\*\*)?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
_TAKEAWAYS_HEADER_RE = re.compile(
    r"^\s*(?:\*\*|##?\s*)?(?:Key\s+(?:Takeaways|Arguments|Points)|核心(?:要点|论据|论点)|关键要点)\s*(?:\*\*)?\s*[:：]?\s*$",
    re.IGNORECASE,
)
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")


def _normalize_stance(raw: str) -> tuple[str | None, float | None]:
    """Extract normalized stance band and optional 0–10 score from a stance line."""
    score: float | None = None
    score_match = _SCORE_IN_LINE_RE.search(raw)
    if score_match:
        try:
            val = float(score_match.group(1))
            if 0.0 <= val <= 10.0:
                score = round(val, 1)
        except ValueError:
            pass

    cleaned = re.sub(r"\(.*?\)|（.*?）", "", raw)
    cleaned = cleaned.replace("*", "").replace("`", "").strip()
    key = " ".join(cleaned.lower().split())
    stance = _STANCE_ALIASES.get(key)
    if not stance:
        for alias, canonical in sorted(_STANCE_ALIASES.items(), key=lambda x: -len(x[0])):
            if alias in key:
                stance = canonical
                break
    if score is None and stance in _DEFAULT_SCORE_BY_STANCE:
        score = _DEFAULT_SCORE_BY_STANCE[stance]
    return stance, score


def _normalize_confidence(raw: str) -> str | None:
    cleaned = raw.replace("*", "").strip().lower()
    for token in re.split(r"[\s,;/()（）]+", cleaned):
        if token in _CONFIDENCE_ALIASES:
            return _CONFIDENCE_ALIASES[token]
    return None


def parse_analyst_viewpoint(text: str, structured: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse an analyst report (and optional structured output) into a viewpoint dict."""
    stance: str | None = None
    score: float | None = None
    confidence: str | None = None
    executive_summary = ""
    key_takeaways: list[str] = []

    if structured:
        if structured.get("overall_band"):
            stance = str(structured["overall_band"])
        if structured.get("overall_score") is not None:
            try:
                score = round(float(structured["overall_score"]), 1)
            except (TypeError, ValueError):
                pass
        if structured.get("confidence"):
            confidence = _normalize_confidence(str(structured["confidence"]))
        if structured.get("executive_summary"):
            executive_summary = str(structured["executive_summary"]).strip()
        if isinstance(structured.get("key_takeaways"), list):
            key_takeaways = [str(x).strip() for x in structured["key_takeaways"] if str(x).strip()]

    raw_text = (text or "").strip()
    if not raw_text:
        return {
            "stance": stance,
            "score": score,
            "confidence": confidence,
            "executive_summary": executive_summary,
            "key_takeaways": key_takeaways,
            "body": "",
        }

    lines = raw_text.splitlines()
    idx = 0
    in_takeaways = False
    in_summary = False
    summary_parts: list[str] = []

    while idx < len(lines) and idx < 35:
        line = lines[idx]
        stripped = line.strip()

        if not stripped:
            if in_summary and summary_parts:
                in_summary = False
            idx += 1
            continue

        if stripped in ("---", "***", "___"):
            idx += 1
            break

        m_stance = _STANCE_LINE_RE.match(stripped)
        if m_stance and stance is None:
            stance, parsed_score = _normalize_stance(m_stance.group(1))
            if score is None:
                score = parsed_score
            in_takeaways = False
            in_summary = False
            idx += 1
            continue

        if m_stance and score is None:
            _, score = _normalize_stance(m_stance.group(1))
            in_takeaways = False
            in_summary = False
            idx += 1
            continue

        m_conf = _CONFIDENCE_LINE_RE.match(stripped)
        if m_conf:
            if confidence is None:
                confidence = _normalize_confidence(m_conf.group(1))
            in_takeaways = False
            in_summary = False
            idx += 1
            continue

        m_exec = _EXEC_SUMMARY_LINE_RE.match(stripped)
        if m_exec:
            rest = m_exec.group(1).strip()
            if rest:
                summary_parts.append(rest)
            in_summary = True
            in_takeaways = False
            idx += 1
            continue

        if _TAKEAWAYS_HEADER_RE.match(stripped):
            in_takeaways = True
            in_summary = False
            idx += 1
            continue

        m_bullet = _BULLET_LINE_RE.match(stripped)
        if in_takeaways and m_bullet:
            pt = m_bullet.group(1).strip()
            if pt and len(key_takeaways) < 5:
                key_takeaways.append(pt)
            idx += 1
            continue

        if in_summary and not stripped.startswith(("#", "**", "-", "*", "|")):
            summary_parts.append(stripped)
            idx += 1
            continue

        break

    if not executive_summary and summary_parts:
        executive_summary = " ".join(summary_parts).strip()

    body = "\n".join(lines[idx:]).strip() if idx > 0 else raw_text
    if not body:
        body = raw_text

    # Fallback extraction for legacy/unstructured reports
    if not executive_summary:
        for para in re.split(r"\n\s*\n", body):
            p = para.strip()
            if not p or p.startswith(("#", "|", "-", "*", "**Overall", "**Confidence")):
                continue
            cleaned_p = re.sub(r"\*\*([^*]+)\*\*", r"\1", p)
            executive_summary = cleaned_p[:240] + ("…" if len(cleaned_p) > 240 else "")
            break

    if not key_takeaways:
        for line in body.splitlines():
            m_b = _BULLET_LINE_RE.match(line.strip())
            if m_b:
                pt = m_b.group(1).strip()
                if 10 <= len(pt) <= 180:
                    key_takeaways.append(pt)
                if len(key_takeaways) >= 3:
                    break

    return {
        "stance": stance,
        "score": score,
        "confidence": confidence,
        "executive_summary": executive_summary,
        "key_takeaways": key_takeaways,
        "body": body,
    }


def parse_researcher_viewpoint(text: str, speaker: str = "") -> dict[str, Any]:
    """Parse a Bull or Bear researcher debate turn into structured viewpoint fields."""
    cleaned = _SPEAKER_PREFIX_RE.sub("", (text or "").strip())
    parsed = parse_analyst_viewpoint(cleaned)
    side = "bull" if "bull" in speaker.lower() else "bear" if "bear" in speaker.lower() else None
    stance = parsed["stance"]
    if not stance and side:
        stance = "Bullish" if side == "bull" else "Bearish"
    conviction = parsed["score"]
    if conviction is not None and side == "bear" and parsed["stance"] == "Bearish" and conviction < 5.0:
        # If the model wrote Score: 2.5/10 instead of Conviction: 7.5/10 for Bearish,
        # normalize conviction to represent argument strength (0–10).
        pass
    return {
        "side": side,
        "stance": stance,
        "conviction": conviction,
        "executive_summary": parsed["executive_summary"],
        "key_arguments": parsed["key_takeaways"],
        "body": parsed["body"],
    }
