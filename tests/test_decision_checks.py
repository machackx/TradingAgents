"""Programmatic constraints apply to real values and the free-text fallback alike."""

import math
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from tradingagents.agents.evidence import assess_evidence, tool_evidence, usable_result
from tradingagents.agents.proposal_validation import check_trader_text, validate_proposal
from tradingagents.agents.schemas import PortfolioDecision, TraderProposal
from tradingagents.dataflows.config import run_config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("field,value", [
    ("entry_price", -10), ("entry_price", 0), ("entry_price", math.nan),
    ("entry_price", math.inf), ("stop_loss", -1), ("stop_loss", math.inf),
    ("position_size_pct", 101), ("position_size_pct", -1), ("order_quantity", 0),
    ("order_quantity", math.nan), ("position_sizing", "200% of portfolio"),
])
def test_invalid_trade_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        TraderProposal(action="Buy", reasoning="test", **{field: value})


@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, "nan"])
def test_invalid_pm_targets_are_rejected(value):
    with pytest.raises(ValidationError):
        PortfolioDecision(rating="Buy", executive_summary="x", investment_thesis="x", price_target=value)


def test_buy_stop_must_be_below_entry():
    for stop in (100, 120):
        with pytest.raises(ValidationError):
            TraderProposal(action="Buy", reasoning="test", entry_price=100, stop_loss=stop)
    assert TraderProposal(action="Buy", reasoning="test", entry_price=100, stop_loss=90).stop_loss == 90


@pytest.mark.parametrize("field,value", [
    ("Entry Price", "-10"), ("Stop Loss", "nan"), ("Entry Price", "15%"),
    ("Order Quantity", "-1"), ("Position Sizing", "200% of portfolio"),
])
def test_free_text_cannot_bypass_numeric_checks(field, value):
    result = check_trader_text(f"**Action**: Buy\n**{field}**: {value}", {})
    assert result["status"] == "invalid"


@pytest.mark.parametrize("field,first,last", [
    ("Entry Price", "-10", "100"), ("Action", "Sell", "Buy"),
    ("Position Sizing", "200%", "5%"),
])
def test_conflicting_free_text_fields_cannot_hide_invalid_values(field, first, last):
    text = f"**Action**: Buy\n**{field}**: {first}\n**{field}**: {last}"
    assert check_trader_text(text, {})["status"] == "invalid"


def _state(cash=500, currency="USD", **extra):
    return {"company_of_interest": "AAPL", "market_data": {"quote_currency": "USD"},
            "portfolio_data": {"cash": cash, "currency": currency, **extra}}


def test_buy_budget_and_currencies_are_checked():
    proposal = TraderProposal(action="Buy", reasoning="test", entry_price=100, order_quantity=6)
    assert validate_proposal(proposal, _state())["status"] == "invalid"
    assert validate_proposal(proposal, _state(cash=1000))["status"] == "validated"
    assert validate_proposal(proposal, _state(cash=1000, currency="HKD"))["status"] == "invalid"
    assert validate_proposal(proposal, {})["status"] == "invalid"


def test_nav_cap_includes_existing_position():
    proposal = TraderProposal(action="Buy", reasoning="test", entry_price=100, order_quantity=5)
    state = _state(cash=1000, net_asset_value=10000, positions=[{"ticker": "AAPL", "quantity": 10}])
    with run_config({"max_position_pct": 10.0}):
        assert validate_proposal(proposal, state)["status"] == "invalid"
        assert validate_proposal(TraderProposal(action="Buy", reasoning="x", position_sizing="15%"), state)["status"] == "invalid"


def test_sell_does_not_assume_short_permission_or_unknown_holdings():
    proposal = TraderProposal(action="Sell", reasoning="test", order_quantity=5)
    assert validate_proposal(proposal, _state(positions=[{"ticker": "AAPL", "quantity": 3}]))["status"] == "invalid"
    assert validate_proposal(proposal, _state(positions=[{"ticker": "AAPL", "quantity": 8}]))["status"] == "validated"


def test_no_order_does_not_claim_affordability_was_checked():
    result = validate_proposal(TraderProposal(action="Buy", reasoning="test"), {})
    assert result["status"] == "validated"
    assert "unverified" in " ".join(result["notes"])


def test_review_is_not_reflected_and_a_successful_retry_replaces_it(tmp_path, monkeypatch):
    from tradingagents.memory import TradingMemoryLog, settlement

    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "memory.md")})
    log.store_decision("AAPL", "2026-01-05", "**Rating**: REVIEW\n\nMissing evidence.")
    fetch = MagicMock(side_effect=AssertionError("REVIEW must not be scored"))
    monkeypatch.setattr(settlement, "fetch_returns", fetch)
    reflector = MagicMock()
    done = settlement.settle_pending("AAPL", log, reflector, {})
    assert not done.settled and not done.failed
    fetch.assert_not_called()
    reflector.reflect_on_final_decision.assert_not_called()
    log.store_decision("AAPL", "2026-01-05", "**Rating**: Buy\n\nEvidence supplied.")
    entries = log.load_entries()
    assert len(entries) == 1 and entries[0]["rating"] == "Buy"
    assert "Evidence supplied" in entries[0]["decision"]
    # A later ordinary decision still cannot rewrite an already recorded call.
    log.store_decision("AAPL", "2026-01-05", "**Rating**: Sell\n\nAnother run.")
    assert log.load_entries()[0]["rating"] == "Buy"


@pytest.mark.parametrize("text", [
    "DATA_UNAVAILABLE: failed", "NO_DATA_AVAILABLE: no rows", "",
    "<no StockTwits messages found for $AAPL>", "<no Reddit posts found>",
    "<Reddit unavailable: fetch failed>", "Profile fundamentals are withheld for this date.",
    "Cashflow data is withheld for this date.", "No news found for AAPL",
])
def test_absence_and_failure_notices_do_not_count_as_evidence(text):
    assert usable_result(text) is False


def test_a_successful_chinese_supplement_survives_primary_outage():
    text = "DATA_UNAVAILABLE: Yahoo failed\n\n- [新闻] 公司公告\n  Published: 2026-11-04 18:00:00 +0800\n  Link: https://example.com"
    assert usable_result(text)
    assert usable_result("### Company withheld its earnings guidance\nLink: https://example.com")


def test_model_prose_and_failed_tool_messages_cannot_claim_evidence():
    messages = [AIMessage("I verified the prices"), ToolMessage(
        "error", name="get_stock_data", tool_call_id="1", status="error",
    )]
    evidence = tool_evidence(messages)
    result = assess_evidence({"analyst_evidence": {"market": evidence}}, ["market"])
    assert result["status"] == "insufficient_data"


def test_only_selected_analysts_are_required_and_profiles_do_not_replace_statements():
    state = {"analyst_evidence": {"news": {"get_news": {"usable": 1}},
                                  "fundamentals": {"get_fundamentals": {"usable": 1}}}}
    assert assess_evidence(state, ["news"])["status"] == "adequate"
    result = assess_evidence(state, ["news", "fundamentals"])
    assert result["status"] == "insufficient_data"
    assert len(result["issues"]) == 3
    assert assess_evidence({**state, "asset_type": "crypto"}, ["fundamentals"])["status"] == "adequate"


def test_pm_skips_model_and_returns_review_when_checks_fail():
    from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager

    llm = MagicMock()
    state = {
        "company_of_interest": "AAPL", "risk_debate_state": {"history": "Buy!"},
        "investment_plan": "Buy", "trader_investment_plan": "Buy",
        "data_quality": {"status": "insufficient_data", "issues": ["news unavailable"]},
        "trader_validation": {"status": "validated"},
    }
    # Retain the risk fields used by the PM's public state update.
    state["risk_debate_state"].update(
        aggressive_history="", conservative_history="", neutral_history="",
        current_aggressive_response="", current_conservative_response="",
        current_neutral_response="", count=3,
    )
    result = create_portfolio_manager(llm)(state)
    assert result["final_rating"] == "REVIEW"
    assert "news unavailable" in result["final_trade_decision"]
    llm.invoke.assert_not_called()
    llm.with_structured_output.return_value.invoke.assert_not_called()
