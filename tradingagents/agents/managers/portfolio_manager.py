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
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS, bind_structured, invoke_structured


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
        validation = state.get("trader_validation")
        if validation is not None and validation.get("status") != "validated":
            issues.extend(validation.get("issues") or ["Trader proposal has not passed programmatic validation."])

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

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Ground every conclusion in specific evidence from the analysts. The risk debate always contains conflicting stances; deciding which is stronger is the job, so conflict alone is not a reason to Hold. Commit to the stronger case, sized by how decisively it wins. Choose Hold only when the evidence is still balanced after that weighing, or too thin to support a call; do not force a direction to appear decisive. Weigh the analysts on their merits, independent of speaking order.

Any supplied numeric trader proposal has already been checked. Do not introduce different order
quantities, entry/stop levels or sizing arithmetic in your narrative. Missing cash, NAV and currency
conversion remain unknown; a research rating alone does not certify an affordable executable order.

## Output

Write these sections, in this order, starting with the rating on its own line:

- **Rating**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Executive Summary**: the call and how to act on it
- **Investment Thesis**: the evidence that decided it, and what would change it

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""

        # The typed rating is the decision; the rendered text only carries it.
        # Read back from text, a rating the thesis quotes could replace it.
        if issues:
            final_trade_decision = review_report(issues)
            final_rating = "REVIEW"
        else:
            decision = invoke_structured(structured_llm, prompt, "Portfolio Manager")
            if decision is not None:
                try:
                    decision = PortfolioDecision.model_validate(decision.model_dump())
                    final_trade_decision = render_pm_decision(decision)
                    final_rating = decision.rating.value
                except ValueError:
                    issues.append("Portfolio Manager returned an invalid structured decision.")
            else:
                final_trade_decision = llm.invoke(prompt).content
                final_rating = parse_rating(final_trade_decision)
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
        }

    return portfolio_manager_node
