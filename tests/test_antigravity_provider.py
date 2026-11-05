"""Tests for the Google Antigravity CLI (`agy`) LLM client and provider integration."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

import cli.prompts as prompts
from cli.stats_handler import StatsCallbackHandler
from tradingagents import default_config
from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.structured import bind_structured, invoke_structured_or_freetext
from tradingagents.llm_clients import antigravity_client, factory
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.llm_clients.validators import validate_model


@tool
def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Retrieve OHLCV stock price data for a ticker symbol."""
    return f"Date,Close\n{end_date},140.50"


@pytest.fixture
def fake_agy_binary(monkeypatch, tmp_path):
    binary = tmp_path / "agy"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("ANTIGRAVITY_CLI_PATH", str(binary))
    return str(binary)


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["antigravity", "agy", "Antigravity"])
def test_factory_routes_antigravity_provider(provider):
    client = factory.create_llm_client(provider, "gemini-3.8-flash")
    assert isinstance(client, antigravity_client.AntigravityClient)
    llm = client.get_llm()
    assert isinstance(llm, antigravity_client.ChatAntigravityCLI)
    assert llm.model == "gemini-3.8-flash"


@pytest.mark.unit
def test_no_api_key_required_and_models_validate():
    assert get_api_key_env("antigravity") is None
    assert get_api_key_env("agy") is None
    assert validate_model("antigravity", "gemini-4-argon") is True
    assert validate_model("antigravity", "any-custom-model-id") is True
    assert ("Gemini 3.8 Flash - Fast, low latency", "gemini-3.8-flash") in get_model_options("antigravity", "quick")
    assert ("Gemini 4 Argon - Frontier coding and reasoning", "gemini-4-argon") in get_model_options("antigravity", "deep")


@pytest.mark.unit
def test_tier_client_forwards_thinking_level_as_effort(fake_agy_binary):
    config = {
        **default_config.DEFAULT_CONFIG,
        "llm_provider": "antigravity",
        "quick_think_llm": "gemini-3.8-flash",
        "deep_think_llm": "gemini-4-argon",
        "google_thinking_level": "minimal",
    }
    quick_llm = factory.create_tier_client(config, "quick").get_llm()
    cmd = quick_llm._build_command("Hello")
    assert cmd[:4] == [fake_agy_binary, "-p", "Hello", "--output-format"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "gemini-3.8-flash"
    # "minimal" maps to "low" for `agy --effort`
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "low"


@pytest.mark.unit
def test_plain_invoke_parses_json_envelope_and_records_token_stats(fake_agy_binary, monkeypatch):
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        envelope = {
            "conversation_id": "conv-123",
            "status": "SUCCESS",
            "response": "Bullish momentum continues above 50 SMA.",
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 350,
                "thinking_tokens": 100,
                "total_tokens": 1550,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(antigravity_client.subprocess, "run", fake_run)

    stats = StatsCallbackHandler()
    llm = factory.create_llm_client(
        "antigravity", "gemini-3.8-flash-high", thinking_level="high", callbacks=[stats]
    ).get_llm()

    reply = llm.invoke([SystemMessage("You are a bull analyst."), HumanMessage("Analyze NVDA.")])
    assert reply.content == "Bullish momentum continues above 50 SMA."
    assert stats.get_stats() == {
        "llm_calls": 1,
        "tool_calls": 0,
        "tokens_in": 1200,
        "tokens_out": 350,
    }
    sent_cmd = captured_cmds[0]
    assert "--model" in sent_cmd and sent_cmd[sent_cmd.index("--model") + 1] == "gemini-3.8-flash-high"
    assert "--effort" in sent_cmd and sent_cmd[sent_cmd.index("--effort") + 1] == "high"
    prompt_arg = sent_cmd[sent_cmd.index("-p") + 1]
    assert "[System Instructions]\nYou are a bull analyst." in prompt_arg
    assert "[User]\nAnalyze NVDA." in prompt_arg


@pytest.mark.unit
def test_analyst_bind_tools_roundtrip_with_take_turn(fake_agy_binary, monkeypatch):
    responses = [
        # Turn 1: Analyst requests get_stock_data tool call
        {
            "status": "SUCCESS",
            "response": "",
            "structured_output": {
                "tool_calls": [
                    {
                        "name": "get_stock_data",
                        "arguments": {
                            "symbol": "NVDA",
                            "start_date": "2026-10-01",
                            "end_date": "2026-11-02",
                        },
                    }
                ],
                "final_answer": "",
            },
            "usage": {"input_tokens": 500, "output_tokens": 80, "total_tokens": 580},
        },
        # Turn 2: Analyst receives ToolMessage and writes final report
        {
            "status": "SUCCESS",
            "response": "",
            "structured_output": {
                "tool_calls": [],
                "final_answer": "## NVDA Market Analysis\nClose at 140.50.",
            },
            "usage": {"input_tokens": 700, "output_tokens": 200, "total_tokens": 900},
        },
    ]
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        payload = responses.pop(0)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(antigravity_client.subprocess, "run", fake_run)

    llm = factory.create_llm_client("antigravity", "gemini-3.8-flash").get_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Tools available: {tool_names}"),
            MessagesPlaceholder(variable_name="messages"),
        ]
    ).partial(tool_names="get_stock_data")

    # Turn 1: model emits tool call
    history = [HumanMessage("Analyze NVDA on 2026-11-02")]
    ai_turn1, report1 = take_turn(prompt, llm, (get_stock_data,), history)
    assert report1 == ""
    assert len(ai_turn1.tool_calls) == 1
    assert ai_turn1.tool_calls[0]["name"] == "get_stock_data"
    assert ai_turn1.tool_calls[0]["args"] == {
        "symbol": "NVDA",
        "start_date": "2026-10-01",
        "end_date": "2026-11-02",
    }
    assert "--json-schema" in captured_cmds[0]

    # Turn 2: after ToolMessage, model emits final report
    history.extend(
        [
            ai_turn1,
            ToolMessage(
                content="Date,Close\n2026-11-02,140.50",
                name="get_stock_data",
                tool_call_id=ai_turn1.tool_calls[0]["id"],
            ),
        ]
    )
    ai_turn2, report2 = take_turn(prompt, llm, (get_stock_data,), history)
    assert ai_turn2.tool_calls == []
    assert report2 == "## NVDA Market Analysis\nClose at 140.50."
    prompt_turn2 = captured_cmds[1][captured_cmds[1].index("-p") + 1]
    assert "[Tool Output (get_stock_data)]" in prompt_turn2


@pytest.mark.unit
def test_structured_output_validates_pydantic_schema_and_falls_back_gracefully(fake_agy_binary, monkeypatch):
    valid_decision = {
        "rating": "Buy",
        "executive_summary": "Enter long position on NVDA.",
        "investment_thesis": "Strong data-center demand and technical breakout.",
    }
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        envelope = {
            "status": "SUCCESS",
            "response": json.dumps(valid_decision),
            "structured_output": valid_decision,
            "usage": {"input_tokens": 900, "output_tokens": 150, "total_tokens": 1050},
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(antigravity_client.subprocess, "run", fake_run)

    llm = factory.create_llm_client("antigravity", "gemini-4-argon").get_llm()
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")
    assert structured_llm is not None

    rendered = invoke_structured_or_freetext(
        structured_llm, llm, "Deliver final decision", render_pm_decision, "Portfolio Manager"
    )
    assert "Buy" in rendered
    assert "Enter long position on NVDA." in rendered
    assert "--json-schema" in calls[0]


@pytest.mark.unit
def test_missing_agy_binary_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("ANTIGRAVITY_CLI_PATH", raising=False)
    monkeypatch.setattr(antigravity_client.shutil, "which", lambda _: None)
    monkeypatch.setattr(antigravity_client, "_CANDIDATE_PATHS", ())

    llm = factory.create_llm_client("antigravity", "gemini-3.8-flash").get_llm()
    with pytest.raises(RuntimeError, match="brew install --cask antigravity-cli"):
        llm.invoke("Hello")


@pytest.mark.unit
def test_provider_table_includes_antigravity():
    entries = {key: display for display, key, _ in prompts._llm_provider_table()}
    assert "antigravity" in entries
    assert "Antigravity CLI" in entries["antigravity"]
