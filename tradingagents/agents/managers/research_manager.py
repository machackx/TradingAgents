"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.evidence import review_report
from tradingagents.agents.rating_policy import (
    analyst_evidence,
    evaluate_assessment,
    has_reports,
    policy_footer,
    policy_instruction,
)
from tradingagents.agents.schemas import ResearchPlan, render_research_plan
from tradingagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
)
from tradingagents.dataflows.config import get_config


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]
        contract = policy_instruction(state)
        evidence = analyst_evidence(state)
        independent = ""
        if get_config().get("rating_independent_review", True) and has_reports(state):
            independent_prompt = f"""Form an independent research assessment using only the analyst
evidence below, before seeing any research debate or other agent recommendation.
{instrument_context}
{contract}
{evidence}
Return a ResearchPlan with recommendation, confidence, expected_alpha_pct (or null),
evidence_status, rationale and strategic_actions. Distinguish unresolved data conflicts
from actual adverse business evidence. Do not propose account transactions.
{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""
            independent, independent_issues = evaluate_assessment(
                llm, structured_llm, ResearchPlan, render_research_plan,
                independent_prompt, state, "Research Manager independent",
            )
            if independent_issues:
                independent = review_report(independent_issues)

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

{contract}

**Original analyst evidence:**
{evidence}

**Independent assessment before seeing the debate:**
{independent or '(not requested or analyst reports unavailable)'}

Audit both sides against the original evidence. Neither the last speaker nor the number
of repeated assertions gives an argument extra weight. Explain any change from the
independent assessment using a specific supported fact or correction; agreement with an
earlier recommendation is not evidence. A balanced view may legitimately be Hold.

---

**Debate History:**
{history}

## Output

Write these sections, in this order, starting with the recommendation on its own line:

- **Recommendation**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Confidence**: high / medium / low, independent from direction
- **Expected Alpha Pct**: a supported number, or not provided
- **Evidence Status**: usable / limited / insufficient
- **Executive Summary**: 1–2 sentences explaining the view and uncertainty
- **Bull Key Points**: 2–3 bullet points of the strongest bull arguments
- **Bear Key Points**: 2–3 bullet points of the strongest bear risks/counterarguments
- **Rationale**: which arguments decided it
- **Strategic Actions**: conditional guidance; use a standard allocation only when supplied

{NO_EXTERNAL_TOOLS}""" + get_language_instruction()

        investment_plan, issues = evaluate_assessment(
            llm, structured_llm, ResearchPlan, render_research_plan,
            prompt, state, "Research Manager",
        )
        validation = {"status": "invalid" if issues else "validated", "issues": issues}
        if issues:
            validation["raw_assessment"] = investment_plan
            investment_plan = review_report(issues).replace("**Rating**:", "**Recommendation**:", 1)
        elif has_reports(state):
            investment_plan += policy_footer(state)

        new_investment_debate_state = {
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
            "independent_research_plan": independent,
            "research_validation": validation,
        }

    return research_manager_node
