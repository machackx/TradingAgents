"""Tests for structured viewpoint & executive summary instructions, schemas, and parsers."""

from __future__ import annotations

import inspect

import pytest

from tradingagents.agents.analysts import (
    fundamentals_analyst,
    market_analyst,
    news_analyst,
    sentiment_analyst,
)
from tradingagents.agents.researchers import bear_researcher, bull_researcher
from tradingagents.agents.schemas import (
    PortfolioRating,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
    render_research_plan,
    render_sentiment_report,
)
from tradingagents.agents.viewpoint import (
    ANALYST_SUMMARY_INSTRUCTION,
    RESEARCHER_SUMMARY_INSTRUCTION,
    parse_analyst_viewpoint,
    parse_researcher_viewpoint,
)


@pytest.mark.unit
def test_analyst_prompts_include_executive_summary_instruction():
    for mod in (market_analyst, news_analyst, fundamentals_analyst):
        src = inspect.getsource(mod)
        assert "ANALYST_SUMMARY_INSTRUCTION" in src
    assert "Executive Summary" in ANALYST_SUMMARY_INSTRUCTION
    assert "Overall Stance" in ANALYST_SUMMARY_INSTRUCTION


@pytest.mark.unit
def test_researcher_prompts_include_executive_summary_instruction():
    for mod in (bull_researcher, bear_researcher):
        src = inspect.getsource(mod)
        assert "RESEARCHER_SUMMARY_INSTRUCTION" in src
    assert "Executive Summary" in RESEARCHER_SUMMARY_INSTRUCTION
    assert "Conviction" in RESEARCHER_SUMMARY_INSTRUCTION


@pytest.mark.unit
def test_sentiment_report_renders_executive_summary_and_takeaways():
    report = SentimentReport(
        overall_band=SentimentBand.BULLISH,
        overall_score=7.8,
        confidence="high",
        executive_summary="Retail and institutional news flows align on strong AI demand.",
        key_takeaways=["StockTwits 78% bullish", "Subreddit tone constructive"],
        narrative="Detailed narrative body.",
    )
    rendered = render_sentiment_report(report)
    assert "**Overall Sentiment:** **Bullish** (Score: 7.8/10)" in rendered
    assert "**Executive Summary:** Retail and institutional news flows align" in rendered
    assert "**Key Takeaways:**" in rendered
    assert "- StockTwits 78% bullish" in rendered
    assert "Detailed narrative body." in rendered


@pytest.mark.unit
def test_research_plan_renders_executive_summary_and_key_points():
    plan = ResearchPlan(
        recommendation=PortfolioRating.BUY,
        executive_summary="Bull thesis decisively outweighs near-term valuation concerns.",
        bull_key_points=["Data center revenue +120% YoY", "Gross margin above 75%"],
        bear_key_points=["Customer concentration risk"],
        rationale="Full debate weighing.",
        strategic_actions="Scale in over two tranches.",
    )
    rendered = render_research_plan(plan)
    assert "**Recommendation**: Buy" in rendered
    assert "**Executive Summary**: Bull thesis decisively outweighs" in rendered
    assert "**Bull Key Points**:" in rendered
    assert "- Data center revenue +120% YoY" in rendered
    assert "**Bear Key Points**:" in rendered
    assert "- Customer concentration risk" in rendered


@pytest.mark.unit
def test_parse_analyst_viewpoint_structured_header():
    md = """**Overall Stance**: Bullish (Score: 8.2/10)
**Confidence**: High
**Executive Summary**: Price broke above the 50-day SMA on 1.8x average volume with RSI at 62.
**Key Takeaways**:
- Close at $189.50 above 50 SMA ($181.20)
- MACD bullish crossover confirmed
- ATR at 4.2 suggests manageable volatility

---
## Technical Breakdown
Detailed analysis here.
"""
    parsed = parse_analyst_viewpoint(md)
    assert parsed["stance"] == "Bullish"
    assert parsed["score"] == 8.2
    assert parsed["confidence"] == "high"
    assert "Price broke above the 50-day SMA" in parsed["executive_summary"]
    assert len(parsed["key_takeaways"]) == 3
    assert "## Technical Breakdown" in parsed["body"]
    assert "**Overall Stance**" not in parsed["body"]


@pytest.mark.unit
def test_parse_researcher_viewpoint_with_speaker_prefix():
    turn = """Bull Analyst: **Stance**: Bullish (Conviction: 8.5/10)
**Executive Summary**: Earnings revisions and supply-chain checks refute the bear's margin compression claim.
**Key Arguments**:
- Next-gen rack shipments on schedule
- Operating leverage expanding

---
The bear analyst overlooks the backlog visibility..."""
    parsed = parse_researcher_viewpoint(turn, speaker="Bull Researcher")
    assert parsed["side"] == "bull"
    assert parsed["stance"] == "Bullish"
    assert parsed["conviction"] == 8.5
    assert "Earnings revisions" in parsed["executive_summary"]
    assert len(parsed["key_arguments"]) == 2
    assert parsed["body"].startswith("The bear analyst overlooks")
