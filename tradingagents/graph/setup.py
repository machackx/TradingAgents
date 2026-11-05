import logging
from collections import Counter
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.analysts.turn import WRAP_UP
from tradingagents.agents.evidence import assess_evidence, tool_evidence
from tradingagents.agents.state import AgentState
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.router import get_vendor
from tradingagents.dataflows.vendors.yahoo.snapshot import prepare_market_snapshot

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)

# Every target a shared conditional router can return. Each edge driven by the
# router maps all of them, so a fall-through return (e.g. under prompt/i18n/
# refactor drift in the speaker labels) can never hit a missing path_map entry
# and crash LangGraph mid-run (#1088).
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",
    "Conservative Analyst": "Conservative Analyst",
    "Neutral Analyst": "Neutral Analyst",
    "Portfolio Manager": "Portfolio Manager",
}


def _tools_or_done(state) -> str:
    """Route an analyst's turn: run its tool calls, or finish with its report."""
    return "tools" if state["messages"][-1].tool_calls else END


def _analyst_graph(spec, agent, max_tool_rounds: int):
    """One analyst as a graph of its own: the model and its tools, on a private message history.

    It returns only its report, so analysts running side by side never write the
    same key, and its tool calls never reach the other analysts' messages. After
    ``max_tool_rounds`` rounds of tool calls it is told to write its report, and
    that turn ends it whatever it answers, so a model that keeps calling tools
    cannot run the graph into its recursion limit (#1420).
    """
    output = TypedDict(f"{spec.key.capitalize()}Report", {spec.report_key: str, "analyst_evidence": dict})
    graph = StateGraph(AgentState, output_schema=output)
    graph.add_node("agent", agent)
    def finish(state):
        evidence = (state.get("analyst_evidence", {}).get(spec.key, {}) if not spec.tools
                    else tool_evidence(state["messages"]))
        return {"analyst_evidence": {spec.key: evidence}}

    graph.add_node("finish", finish)
    graph.add_edge("finish", END)
    graph.add_edge(START, "agent")
    if not spec.tools:
        graph.add_edge("agent", "finish")
        return graph.compile()

    def calls(messages):
        return [call["name"] for m in messages for call in (getattr(m, "tool_calls", None) or [])]

    def rounds(messages) -> int:
        return sum(1 for m in messages if getattr(m, "tool_calls", None))

    def more_or_wrap_up(state) -> str:
        return "wrap_up" if rounds(state["messages"]) >= max_tool_rounds else "agent"

    def wrap_up(state):
        repeated = ", ".join(f"{name} x{n}" for name, n in Counter(calls(state["messages"])).most_common())
        logger.warning("%s used its %d tool rounds (%s); asking for its report",
                       spec.agent_node, max_tool_rounds, repeated)
        return agent({**state, "messages": [*state["messages"], HumanMessage(WRAP_UP)]})

    graph.add_node("tools", ToolNode(list(spec.tools)))
    graph.add_node("wrap_up", wrap_up)
    graph.add_conditional_edges("agent", _tools_or_done, {"tools": "tools", END: "finish"})
    graph.add_conditional_edges("tools", more_or_wrap_up, ["agent", "wrap_up"])
    graph.add_edge("wrap_up", "finish")
    return graph.compile()


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        conditional_logic: ConditionalLogic,
        max_tool_rounds: int,
        agent_llms: dict[str, Any] | None = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.conditional_logic = conditional_logic
        self.max_tool_rounds = max_tool_rounds
        self.agent_llms = dict(agent_llms or {})

    def _llm_for_role(self, role_key: str, default_llm: Any) -> Any:
        """Return the role-specific LLM if configured, else the tier default."""
        return self.agent_llms.get(role_key, default_llm)

    def setup_graph(
        self, selected_analysts=("market", "social", "news", "fundamentals"), memory_node=None
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Sentiment analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
            memory_node: Node that settles past decisions and returns the run's
                ``past_context``. It runs alongside the analysts.
        """
        plan = build_analyst_execution_plan(selected_analysts)
        debate_order = get_config().get("research_debate_order", "bull_first")
        if debate_order not in {"bull_first", "bear_first"}:
            raise ValueError("research_debate_order must be bull_first or bear_first")
        debate_start = "Bull Researcher" if debate_order == "bull_first" else "Bear Researcher"

        analyst_factories = {
            "market": lambda: create_market_analyst(self._llm_for_role("market_analyst", self.quick_thinking_llm)),
            "social": lambda: create_sentiment_analyst(self._llm_for_role("social_analyst", self.quick_thinking_llm)),
            "news": lambda: create_news_analyst(self._llm_for_role("news_analyst", self.quick_thinking_llm)),
            "fundamentals": lambda: create_fundamentals_analyst(
                self._llm_for_role("fundamentals_analyst", self.quick_thinking_llm)
            ),
        }

        bull_researcher_node = create_bull_researcher(self._llm_for_role("bull_researcher", self.quick_thinking_llm))
        bear_researcher_node = create_bear_researcher(self._llm_for_role("bear_researcher", self.quick_thinking_llm))
        research_manager_node = create_research_manager(self._llm_for_role("research_manager", self.deep_thinking_llm))
        trader_node = create_trader(self._llm_for_role("trader", self.quick_thinking_llm))

        aggressive_analyst = create_aggressive_debator(
            self._llm_for_role("aggressive_debator", self.quick_thinking_llm)
        )
        neutral_analyst = create_neutral_debator(self._llm_for_role("neutral_debator", self.quick_thinking_llm))
        conservative_analyst = create_conservative_debator(
            self._llm_for_role("conservative_debator", self.quick_thinking_llm)
        )
        portfolio_manager_node = create_portfolio_manager(
            self._llm_for_role("portfolio_manager", self.deep_thinking_llm)
        )

        workflow = StateGraph(AgentState)

        def prepare(state):
            if "market" not in [spec.key for spec in plan.specs]:
                return {"market_data": {}}
            try:
                for category, method in (("core_stock_apis", "get_stock_data"), ("technical_indicators", "get_indicators")):
                    primary = get_vendor(category, method).split(",")[0].strip()
                    if primary != "yfinance":
                        return {"market_data": {"status": "unavailable", "reason": (
                            "A shared run snapshot currently requires yfinance as the primary price and indicator vendor; "
                            f"{method} is configured with {primary}. Mixed-source prices are not certified."
                        )}}
                snapshot = prepare_market_snapshot(state["company_of_interest"], state["trade_date"])
            except Exception as exc:
                logger.warning("Run market snapshot unavailable: %s", exc)
                snapshot = {"status": "unavailable", "reason": type(exc).__name__,
                            "symbol": state["company_of_interest"], "as_of_date": state["trade_date"]}
            return {"market_data": snapshot}

        workflow.add_node("Prepare market data", prepare)
        workflow.add_edge(START, "Prepare market data")
        workflow.add_node("Evidence check", lambda state: {
            "data_quality": assess_evidence(state, [spec.key for spec in plan.specs]),
        })

        for spec in plan.specs:
            workflow.add_node(spec.agent_node,
                              _analyst_graph(spec, analyst_factories[spec.key](), self.max_tool_rounds))

        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # The analysts work at the same time; the research debate starts once
        # every one of them has filed its report. The memory log settles past
        # decisions alongside them: only the Portfolio Manager reads its lessons.
        first_steps = [spec.agent_node for spec in plan.specs]
        if memory_node is not None:
            workflow.add_node("Memory Log", memory_node)
            first_steps.append("Memory Log")
        for node in first_steps:
            workflow.add_edge("Prepare market data", node)
        workflow.add_edge(first_steps, "Evidence check")
        workflow.add_conditional_edges(
            "Evidence check",
            lambda state: debate_start if state["data_quality"]["status"] == "adequate" else "Portfolio Manager",
            {"Bull Researcher": "Bull Researcher", "Bear Researcher": "Bear Researcher", "Portfolio Manager": "Portfolio Manager"},
        )

        # Both research-debate edges share the complete DEBATE_PATH_MAP (#1088).
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )
        workflow.add_conditional_edges(
            "Research Manager",
            lambda state: "Trader" if state.get("research_validation", {}).get("status") == "validated" else "Portfolio Manager",
            {"Trader": "Trader", "Portfolio Manager": "Portfolio Manager"},
        )
        workflow.add_conditional_edges(
            "Trader",
            lambda state: "Aggressive Analyst" if state.get("trader_validation", {}).get("status") == "validated" else "Portfolio Manager",
            {"Aggressive Analyst": "Aggressive Analyst", "Portfolio Manager": "Portfolio Manager"},
        )
        # All three risk edges share the complete RISK_ANALYSIS_PATH_MAP (#1088).
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
