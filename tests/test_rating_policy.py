"""Policy boundaries, independent assessments and unresolved-data gates."""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.rating_policy import policy_instruction, rating_for_alpha, rating_policy
from tradingagents.agents.schemas import PortfolioDecision, ResearchPlan
from tradingagents.dataflows.config import run_config
from tradingagents.graph.propagation import Propagator
from tradingagents.rating_audit import audit_case, reorder_debate

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("alpha,rating", [
    (-6, "Sell"), (-5, "Sell"), (-4.99, "Underweight"), (-2.01, "Underweight"),
    (-2, "Hold"), (0, "Hold"), (2, "Hold"), (2.01, "Overweight"), (4.99, "Overweight"),
    (5, "Buy"), (6, "Buy"),
])
def test_alpha_boundaries_are_symmetric(alpha, rating):
    assert rating_for_alpha(alpha, rating_policy("AAPL")) == rating


@pytest.mark.parametrize("config", [
    {"holding_period_days": 0}, {"holding_period_days": 2.5},
    {"rating_neutral_alpha_pct": float("nan")},
    {"rating_neutral_alpha_pct": 5, "rating_strong_alpha_pct": 2},
    {"rating_standard_allocation_pct": 0}, {"rating_standard_allocation_pct": 101},
])
def test_invalid_contract_is_rejected(config):
    with pytest.raises(ValueError):
        rating_policy("AAPL", config)


def state():
    s = Propagator().create_initial_state("9988.HK", "2026-11-05")
    s.update({"market_report": "RAW_PRICES", "fundamentals_report": "RAW_FINANCIALS",
              "data_quality": {"status": "adequate"}, "trader_validation": {"status": "validated"},
              "investment_plan": "RESEARCH_ANCHOR", "trader_investment_plan": "TRADER_ANCHOR"})
    s["investment_debate_state"]["history"] = "Bull Analyst: BULL_ANCHOR\nBear Analyst: BEAR_ANCHOR"
    s["risk_debate_state"]["history"] = "RISK_ANCHOR"
    return s


def test_horizon_benchmark_and_missing_allocation_are_explicit():
    with run_config({"holding_period_days": 21, "benchmark_ticker": "QQQ"}):
        text = policy_instruction(state())
    assert "Benchmark: QQQ" in text and "21 instrument trading sessions" in text
    assert "first closing price strictly after" in text
    assert "Standard allocation: not supplied" in text
    assert "not empirically calibrated" in text


@pytest.mark.parametrize("schema,factory,output_key,independent_key", [
    (ResearchPlan, create_research_manager, "investment_plan", "independent_research_plan"),
    (PortfolioDecision, create_portfolio_manager, "final_trade_decision", "independent_portfolio_decision"),
])
def test_first_manager_call_is_blind_to_other_recommendations(schema, factory, output_key, independent_key):
    s = state()
    base = ({"recommendation": "Buy", "rationale": "Evidence supports upside", "strategic_actions": "Conditional"}
            if schema is ResearchPlan else
            {"rating": "Buy", "executive_summary": "Upside", "investment_thesis": "Evidence"})
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = schema(
        **base, confidence="low", expected_alpha_pct=6, evidence_status="limited",
    )
    out = factory(llm)(s)
    prompts = [c.args[0] for c in llm.with_structured_output.return_value.invoke.call_args_list]
    assert len(prompts) == 2
    assert "RAW_FINANCIALS" in prompts[0] and "RAW_PRICES" in prompts[0]
    for marker in ("BULL_ANCHOR", "BEAR_ANCHOR", "RESEARCH_ANCHOR", "TRADER_ANCHOR", "RISK_ANCHOR"):
        assert marker not in prompts[0]
    assert out[independent_key]
    assert "**Confidence**: low" in out[output_key]  # low confidence does not invert a positive edge
    assert "**Evaluation Benchmark**: ^HSI" in out[output_key]


@pytest.mark.parametrize("structured", [False, True])
def test_insufficient_evidence_is_review_on_both_paths(structured):
    s = state()
    llm = MagicMock()
    if structured:
        llm.with_structured_output.return_value.invoke.return_value = PortfolioDecision(
            rating="Underweight", executive_summary="Conflicting units", investment_thesis="Cannot assess",
            evidence_status="insufficient", confidence="low",
        )
    else:
        llm.with_structured_output.side_effect = NotImplementedError
        llm.invoke.return_value = AIMessage(content="**Rating**: Underweight\n**Evidence Status**: insufficient")
    assert create_portfolio_manager(llm)(s)["final_rating"] == "REVIEW"


def test_inconsistent_alpha_is_rejected_rather_than_silently_relabelled():
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = ResearchPlan(
        recommendation="Underweight", expected_alpha_pct=6, rationale="Inconsistent", strategic_actions="Wait",
    )
    out = create_research_manager(llm)(state())
    assert out["research_validation"]["status"] == "invalid"
    assert "**Recommendation**: REVIEW" in out["investment_plan"]


def test_audit_keeps_reports_frozen_and_swaps_each_pair(tmp_path):
    import json

    s = state()
    s["investment_debate_state"]["history"] += "\nBull Analyst: BULL_SECOND\nBear Analyst: BEAR_SECOND"
    path = tmp_path / "state.json"
    path.write_text(json.dumps(s))
    original = json.loads(path.read_text())
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = ResearchPlan(
        recommendation="Hold", rationale="Balanced", strategic_actions="No forced change",
    )
    result = audit_case(path, model_factory=lambda cfg: llm, execute=True)
    assert len(result["variants"]) == 4
    assert {v["recommendation"] for v in result["variants"]} == {"Hold"}
    assert json.loads(path.read_text()) == original
    swapped = reorder_debate(s["investment_debate_state"]["history"], True)
    assert swapped.index("BEAR_ANCHOR") < swapped.index("BULL_ANCHOR") < swapped.index("BEAR_SECOND") < swapped.index("BULL_SECOND")


def test_settlement_uses_saved_contract_after_configuration_changes(tmp_path, monkeypatch):
    from tradingagents.memory import TradingMemoryLog, settlement

    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "memory.md")})
    policy = rating_policy("AAPL", {"holding_period_days": 21, "benchmark_ticker": "QQQ"})
    log.store_decision("AAPL", "2026-01-05", "**Rating**: Buy", evaluation_policy=policy)
    fetch = MagicMock(return_value=(0.1, 0.03, 21, "2026-02-05"))
    monkeypatch.setattr(settlement, "fetch_returns", fetch)
    reflector = MagicMock()
    reflector.reflect_on_final_decision.return_value = "A lesson"
    outcome = settlement.settle_pending("AAPL", log, reflector, {
        "holding_period_days": 5, "benchmark_ticker": "SPY",
    })
    fetch.assert_called_once_with("AAPL", "2026-01-05", 21, benchmark="QQQ")
    assert outcome.outcomes[0]["benchmark"] == "QQQ"
    entry = log.load_entries()[0]
    assert entry["evaluation_policy"] == policy and entry["holding"] == "21d"
    assert "QQQ" in log.get_past_context("AAPL")


def test_invalid_saved_contract_is_not_silently_settled_using_new_settings(tmp_path, monkeypatch):
    from tradingagents.memory import TradingMemoryLog, settlement

    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "memory.md")})
    log.store_decision("AAPL", "2026-01-05", "**Rating**: Buy", evaluation_policy={})
    fetch = MagicMock()
    monkeypatch.setattr(settlement, "fetch_returns", fetch)
    outcome = settlement.settle_pending("AAPL", log, MagicMock(), {"benchmark_ticker": "SPY"})
    assert outcome.failed and log.load_entries()[0]["pending"]
    fetch.assert_not_called()


def test_audit_output_cannot_overwrite_input(tmp_path, monkeypatch):
    from tradingagents.rating_audit import main

    source = tmp_path / "source.json"
    source.write_text("original")
    monkeypatch.setattr("sys.argv", ["rating_audit", "--output", str(source), str(source)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and source.read_text() == "original"
