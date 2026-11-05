"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call. Its rating is the run's
``final_rating``, and the decision is rendered to markdown as
``final_trade_decision`` for the memory log, CLI display and saved reports.
When a provider does not expose structured output, the agent falls back to
free-text generation and the rating is read from that text.
"""

from __future__ import annotations

import math

from tradingagents.agents.context import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
)
from tradingagents.agents.evidence import review_report
from tradingagents.agents.proposal_validation import labelled_fields
from tradingagents.agents.rating import parse_rating
from tradingagents.agents.rating_policy import (
    analyst_evidence,
    evaluate_assessment,
    has_reports,
    policy_footer,
    policy_instruction,
)
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS, bind_structured
from tradingagents.dataflows.config import get_config


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        portfolio_context = get_portfolio_context_from_state(state)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        issues = []
        # Complete graph runs always carry the deterministic evidence check.
        # A missing/empty result in such a run is a failed check, not permission
        # to let the model fill the gap. Older direct node callers are still
        # supported; explicit failed checks can never be bypassed.
        quality = state.get("data_quality")
        if quality is not None and quality.get("status") != "adequate":
            issues.extend(quality.get("issues") or ["Evidence coverage has not passed the required checks."])
        research_validation = state.get("research_validation")
        if research_validation and research_validation.get("status") != "validated":
            issues.extend(research_validation.get("issues") or ["Research assessment has not passed validation."])
        validation = state.get("trader_validation")
        # An empty trader check is expected when an upstream check skipped it.
        # Without an upstream blocker, that same empty check still fails closed.
        if (validation is not None and validation.get("status") != "validated"
                and (validation or not issues)):
            issues.extend(validation.get("issues") or ["Trader proposal has not passed programmatic validation."])

        contract = policy_instruction(state)
        evidence = analyst_evidence(state)
        independent = ""
        if not issues and get_config().get("rating_independent_review", True) and has_reports(state):
            independent_prompt = f"""Form an independent portfolio research rating from the original
analyst evidence, before seeing any research manager, trader or risk-debate recommendation.
{instrument_context}
{contract}
{evidence}
Return a PortfolioDecision including confidence, expected_alpha_pct (or null), evidence_status,
executive_summary and investment_thesis. Give a research view, not an assumed account order.
{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""
            independent, independent_issues = evaluate_assessment(
                llm, structured_llm, PortfolioDecision, render_pm_decision,
                independent_prompt, state, "Portfolio Manager independent",
            )
            if independent_issues:
                independent = review_report(independent_issues)

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

{portfolio_context}

---

{contract}

**Original analyst evidence:**
{evidence}

**Independent assessment before seeing recommendations:**
{independent or '(not requested or analyst reports unavailable)'}

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Ground conclusions in original evidence. Audit prior recommendations, including your independent
assessment, rather than treating them as facts or votes. Explain a changed view with a specific
fact or corrected inference. Repeated claims and speaking order carry no extra weight.
Keep the relative research rating separate from position limits and account actions: an
unknown or concentrated portfolio does not itself imply the stock will underperform.

Any supplied numeric trader proposal has already been checked. Do not introduce different order
quantities, entry/stop levels or sizing arithmetic in your narrative. Missing cash, NAV and currency
conversion remain unknown; a research rating alone does not certify an affordable executable order.

## Output

Write these sections, in this order, starting with the rating on its own line:

- **Rating**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Confidence**: high / medium / low, independent from direction
- **Expected Alpha Pct**: a supported number, or not provided
- **Evidence Status**: usable / limited / insufficient
- **Executive Summary**: the call and how to act on it
- **Investment Thesis**: the evidence that decided it, and what would change it

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""

        # Typed decisions render their own explicit opening rating; the common
        # assessment check rejects contradictory alpha instead of relabelling it.
        raw_assessment = ""
        if issues:
            final_trade_decision = review_report(issues)
            final_rating = "REVIEW"
        else:
            final_trade_decision, assessment_errors = evaluate_assessment(
                llm, structured_llm, PortfolioDecision, render_pm_decision,
                prompt, state, "Portfolio Manager",
            )
            raw_assessment = final_trade_decision
            issues.extend(assessment_errors)
            final_rating = parse_rating(final_trade_decision)
            if not assessment_errors:
                try:
                    target = labelled_fields(final_trade_decision, unique_fields={"price target"}).get("price target")
                    if target and target.casefold() not in {"not provided", "none", "n/a"}:
                        price = float(target.replace(",", "").lstrip("$€£¥"))
                        if not math.isfinite(price) or price <= 0:
                            raise ValueError("invalid price")
                except ValueError:
                    issues.append("Portfolio Manager target price must be unambiguous, finite and positive.")
            if issues:
                final_trade_decision, final_rating = review_report(issues), "REVIEW"
            elif has_reports(state):
                final_trade_decision += policy_footer(state)

        new_risk_debate_state = {
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "final_rating": final_rating,
            "independent_portfolio_decision": independent,
            "portfolio_validation": {
                "status": "invalid" if issues else "validated", "issues": issues,
                **({"raw_assessment": raw_assessment} if issues and raw_assessment else {}),
            },
        }

    return portfolio_manager_node
