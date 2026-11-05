"""Shared, explicit evaluation contract for research and portfolio decisions."""

import math

from tradingagents.agents.context import report_or_absent
from tradingagents.dataflows.config import get_config
from tradingagents.memory.settlement import resolve_benchmark

POLICY_VERSION = "relative-alpha-v1"
REPORTS = ("market_report", "sentiment_report", "news_report", "fundamentals_report")


def rating_policy(ticker: str, config: dict | None = None) -> dict:
    cfg = get_config() if config is None else config
    days = cfg.get("holding_period_days", 5)
    neutral = float(cfg.get("rating_neutral_alpha_pct", 2.0))
    strong = float(cfg.get("rating_strong_alpha_pct", 5.0))
    allocation = cfg.get("rating_standard_allocation_pct")
    allocation = float(allocation) if allocation is not None else None
    if isinstance(days, bool) or int(days) != float(days) or int(days) <= 0:
        raise ValueError("holding_period_days must be a positive integer")
    if not (math.isfinite(neutral) and math.isfinite(strong) and 0 <= neutral < strong):
        raise ValueError("rating thresholds must be finite and satisfy 0 <= neutral < strong")
    if allocation is not None and not (math.isfinite(allocation) and 0 < allocation <= 100):
        raise ValueError("rating_standard_allocation_pct must be in (0, 100]")
    return {
        "version": POLICY_VERSION, "benchmark": resolve_benchmark(ticker, cfg),
        "holding_period_days": int(days), "neutral_alpha_pct": neutral,
        "strong_alpha_pct": strong, "standard_allocation_pct": allocation,
    }


def rating_for_alpha(alpha_pct: float, policy: dict) -> str:
    """Alpha is in percentage points, not a decimal fraction or a probability."""
    if not math.isfinite(alpha_pct):
        raise ValueError("expected alpha must be finite")
    neutral, strong = policy["neutral_alpha_pct"], policy["strong_alpha_pct"]
    if abs(alpha_pct) <= neutral:
        return "Hold"
    if alpha_pct >= strong:
        return "Buy"
    if alpha_pct <= -strong:
        return "Sell"
    return "Overweight" if alpha_pct > 0 else "Underweight"


def policy_instruction(state: dict) -> str:
    p = rating_policy(state["company_of_interest"])
    allocation = (
        f"Caller-specified standard allocation: {p['standard_allocation_pct']:g}% of NAV."
        if p["standard_allocation_pct"] is not None else
        "Standard allocation: not supplied. Do not invent a baseline weight or a fraction of it."
    )
    return f"""## Evaluation contract ({p['version']})
Analysis date: {state.get('trade_date', 'not supplied')}.
Benchmark: {p['benchmark']}. Horizon: {p['holding_period_days']} instrument trading sessions
AFTER entry at the first closing price strictly after the analysis date, matching outcome settlement.
Expected alpha = instrument return minus benchmark return over those same entry/exit dates,
in percentage points. A longer-term thesis must be labelled separately, not replace this horizon.
The thresholds are configurable policy settings, not empirically calibrated return forecasts.

**Rating Scale**:
- **Buy**: expected alpha >= +{p['strong_alpha_pct']:g} percentage points.
- **Overweight**: +{p['neutral_alpha_pct']:g} < expected alpha < +{p['strong_alpha_pct']:g}.
- **Hold**: expected alpha between -{p['neutral_alpha_pct']:g} and +{p['neutral_alpha_pct']:g}, inclusive;
  also use Hold for a usable but balanced body of evidence with no supported directional edge.
- **Underweight**: -{p['strong_alpha_pct']:g} < expected alpha < -{p['neutral_alpha_pct']:g}.
- **Sell**: expected alpha <= -{p['strong_alpha_pct']:g} percentage points.

Report Confidence (high/medium/low) independently from direction. Missing data, lack of
confirmation, volatility or an unselected analyst do not by themselves establish negative alpha.
Apply the same burden of proof to upside and downside scenarios. Do not fabricate a numerical
forecast to fill expected_alpha_pct: leave it null when unsupported. Without a numerical forecast,
explain the evidence for the sign and strength of the relative-return edge; uncertainty alone
supports lower confidence, not Underweight. If material currency, per-share, reporting-period or
cash-flow contradictions prevent a defensible assessment, set evidence_status=insufficient and
identify the unresolved fields. Otherwise distinguish limited coverage from adverse business facts.

{allocation}
The rating is a relative research view, separate from an account order. It does not imply
that a caller holds the stock, must buy/sell, or has exceeded a concentration limit.
Do not target an even distribution of ratings or force a directional winner.
"""


def analyst_evidence(state: dict) -> str:
    return "\n\n".join(
        f"### {key.removesuffix('_report').title()} analyst\n{report_or_absent(state.get(key, ''), key)}"
        for key in REPORTS
    )


def has_reports(state: dict) -> bool:
    return any(state.get(key, "").strip() for key in REPORTS)


def assessment_issues(decision, ticker: str) -> list[str]:
    if decision.evidence_status == "insufficient":
        return ["Material evidence contradictions or gaps prevent a defensible research rating."]
    if decision.expected_alpha_pct is not None:
        expected = rating_for_alpha(decision.expected_alpha_pct, rating_policy(ticker))
        actual = getattr(decision, "recommendation", None) or getattr(decision, "rating", None)
        if actual != expected:
            return [f"Rating {actual} conflicts with expected alpha and the evaluation contract ({expected})."]
    return []


def text_assessment_issues(text: str, ticker: str) -> list[str]:
    """Honor explicit insufficiency on the free-text path as on the typed path."""
    from tradingagents.agents.proposal_validation import labelled_fields

    try:
        fields = labelled_fields(text, unique_fields={
            "evidence status", "expected alpha pct", "recommendation", "rating",
        })
        status = fields.get("evidence status", "").casefold()
        if status == "insufficient":
            return ["Material evidence contradictions or gaps prevent a defensible research rating."]
        alpha = fields.get("expected alpha pct", "").strip()
        if alpha.casefold() not in {"", "none", "null", "not provided", "n/a"}:
            expected = rating_for_alpha(float(alpha), rating_policy(ticker))
            if fields.get("recommendation", fields.get("rating")) != expected:
                return ["Free-text rating conflicts with expected alpha and the evaluation contract."]
    except ValueError as exc:
        return [f"Invalid research assessment: {exc}"]
    return []


def evaluate_assessment(llm, structured_llm, schema, render, prompt, state, role):
    from tradingagents.agents.structured import invoke_structured

    decision = invoke_structured(structured_llm, prompt, role)
    if decision is not None:
        try:
            values = decision.model_dump() if hasattr(decision, "model_dump") else decision
            decision = schema.model_validate(values)
            return render(decision), assessment_issues(decision, state["company_of_interest"])
        except ValueError as exc:
            return "", [f"Invalid structured research assessment: {str(exc).splitlines()[0]}"]
    text = llm.invoke(prompt).content
    return text, text_assessment_issues(text, state["company_of_interest"])


def policy_footer(state: dict) -> str:
    p = rating_policy(state["company_of_interest"])
    return (f"\n\n**Evaluation Benchmark**: {p['benchmark']}"
            f"\n\n**Evaluation Horizon**: {p['holding_period_days']} trading sessions after entry"
            f"\n\n**Rating Policy**: {p['version']}")


def risk_instruction(state: dict) -> str:
    return policy_instruction(state) + """
Independently challenge the trader's direction as well as sizing. You may reject Buy, Hold
or Sell when the original evidence supports a different view. The trader's action and other
analysts' recommendations are proposals, not facts or votes. Separate a portfolio risk limit
from an expectation of negative alpha; preserve unknown holdings, cash and NAV as unknown.
Identify both the opportunity cost and the downside of following or rejecting the proposal.
"""
