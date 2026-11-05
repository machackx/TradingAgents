"""Tests for per-role cross-provider model assignment and Claude/Codex CLI providers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from tests.test_graph_end_to_end import TRADE_DATE
from tests.test_webapp import _client
from tradingagents import default_config
from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.structured import bind_structured, invoke_structured_or_freetext
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients import (
    AGENT_ROLE_SPECS,
    claude_cli_client,
    codex_cli_client,
    configured_agent_models,
    factory,
    resolve_agent_spec,
)
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.llm_clients.validators import validate_model
from tradingagents.report_html import render_report
from tradingagents.reporting import _header


@tool
def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Retrieve OHLCV stock price data for a ticker symbol."""
    return f"Date,Close\n{end_date},140.50"


@pytest.fixture
def fake_claude_binary(monkeypatch, tmp_path):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(binary))
    return str(binary)


@pytest.fixture
def fake_codex_binary(monkeypatch, tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("CODEX_CLI_PATH", str(binary))
    return str(binary)


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["claude-cli", "claude_cli", "claude-code"])
def test_factory_routes_claude_cli_provider(provider):
    client = factory.create_llm_client(provider, "claude-sonnet-5")
    assert isinstance(client, claude_cli_client.ClaudeCLIClient)
    llm = client.get_llm()
    assert isinstance(llm, claude_cli_client.ChatClaudeCLI)
    assert llm.model == "claude-sonnet-5"
    assert get_api_key_env(provider) is None
    assert validate_model(provider, "claude-sonnet-5") is True


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["codex", "codex-cli", "codex_cli"])
def test_factory_routes_codex_cli_provider(provider):
    client = factory.create_llm_client(provider, "gpt-6-luna")
    assert isinstance(client, codex_cli_client.CodexCLIClient)
    llm = client.get_llm()
    assert isinstance(llm, codex_cli_client.ChatCodexCLI)
    assert llm.model == "gpt-6-luna"
    assert get_api_key_env(provider) is None
    assert validate_model(provider, "gpt-6-luna") is True


@pytest.mark.unit
def test_claude_cli_invoke_tools_and_structured_output(fake_claude_binary, monkeypatch):
    responses = [
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": {
                "tool_calls": [
                    {
                        "name": "get_stock_data",
                        "arguments": {"symbol": "NVDA", "start_date": "2026-10-01", "end_date": "2026-11-02"},
                    }
                ],
                "final_answer": "",
            },
            "usage": {"input_tokens": 400, "output_tokens": 60},
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": {
                "tool_calls": [],
                "final_answer": "## Claude Market Report\nStrong momentum.",
            },
            "usage": {"input_tokens": 600, "output_tokens": 120},
        },
    ]
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps(responses.pop(0)), stderr="")

    monkeypatch.setattr(claude_cli_client.subprocess, "run", fake_run)

    llm = factory.create_llm_client("claude-cli", "claude-sonnet-5", effort="high").get_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", "Tools: {tool_names}"), MessagesPlaceholder(variable_name="messages")]
    ).partial(tool_names="get_stock_data")

    history = [HumanMessage("Analyze NVDA")]
    ai1, report1 = take_turn(prompt, llm, (get_stock_data,), history)
    assert report1 == ""
    assert ai1.tool_calls[0]["name"] == "get_stock_data"
    assert "--effort" in captured_cmds[0] and captured_cmds[0][captured_cmds[0].index("--effort") + 1] == "high"

    history.extend([ai1, ToolMessage(content="140.50", name="get_stock_data", tool_call_id=ai1.tool_calls[0]["id"])])
    ai2, report2 = take_turn(prompt, llm, (get_stock_data,), history)
    assert ai2.tool_calls == []
    assert "Claude Market Report" in report2


@pytest.mark.unit
def test_codex_cli_invoke_and_structured_output(fake_codex_binary, monkeypatch):
    valid_decision = {
        "rating": "Overweight",
        "executive_summary": "Add NVDA on pullback.",
        "investment_thesis": "AI capex cycle intact.",
    }
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps(valid_decision), stderr="")

    monkeypatch.setattr(codex_cli_client.subprocess, "run", fake_run)

    llm = factory.create_llm_client("codex", "gpt-6-sol", reasoning_effort="high").get_llm()
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")
    rendered = invoke_structured_or_freetext(
        structured_llm, llm, "Deliver decision", render_pm_decision, "Portfolio Manager"
    )
    assert "Overweight" in rendered
    assert "--output-schema" in captured_cmds[0]
    assert 'model_reasoning_effort="high"' in captured_cmds[0]


@pytest.mark.unit
def test_per_role_override_resolution_and_env_parsing(monkeypatch):
    monkeypatch.setenv(
        "TRADINGAGENTS_AGENT_MODELS",
        "market_analyst=antigravity:gemini-3.8-flash,bull_researcher=claude-cli:claude-sonnet-5",
    )
    monkeypatch.setenv("TRADINGAGENTS_PORTFOLIO_MANAGER_PROVIDER", "codex")
    monkeypatch.setenv("TRADINGAGENTS_PORTFOLIO_MANAGER_LLM", "gpt-6-astra")

    cfg = default_config.build_default_config()

    market_spec = resolve_agent_spec(cfg, "market_analyst")
    assert market_spec["overridden"] is True
    assert (market_spec["provider"], market_spec["model"]) == ("antigravity", "gemini-3.8-flash")

    bull_spec = resolve_agent_spec(cfg, "bull")
    assert bull_spec["overridden"] is True
    assert (bull_spec["provider"], bull_spec["model"]) == ("claude-cli", "claude-sonnet-5")

    pm_spec = resolve_agent_spec(cfg, "portfolio_manager")
    assert pm_spec["overridden"] is True
    assert (pm_spec["provider"], pm_spec["model"]) == ("codex", "gpt-6-astra")

    # Non-overridden role inherits from its tier
    news_spec = resolve_agent_spec(cfg, "news_analyst")
    assert news_spec["overridden"] is False
    assert (news_spec["provider"], news_spec["model"]) == (cfg["llm_provider"], cfg["quick_think_llm"])

    active = configured_agent_models(cfg)
    assert set(active.keys()) == {"market_analyst", "bull_researcher", "portfolio_manager"}


@pytest.mark.unit
def test_graph_builds_distinct_llms_per_overridden_role_and_caches_identical_specs(monkeypatch, tmp_path):
    created: list[tuple[str, str]] = []

    class DummyClient:
        def __init__(self, provider: str, model: str):
            self.provider = provider
            self.model = model

        def get_llm(self):
            return SimpleNamespace(provider=self.provider, model=self.model, bind_tools=lambda t: self)

    def fake_create_llm_client(provider, model, base_url=None, **kwargs):
        created.append((provider, model))
        return DummyClient(provider, model)

    monkeypatch.setattr(factory, "create_llm_client", fake_create_llm_client)

    cfg = {
        **default_config.DEFAULT_CONFIG,
        "results_dir": str(tmp_path / "results"),
        "data_cache_dir": str(tmp_path / "cache"),
        "memory_log_path": str(tmp_path / "memory.md"),
        "llm_provider": "chatgpt",
        "quick_think_llm": "gpt-6-luna",
        "deep_think_llm": "gpt-6-sol",
        "agent_models": {
            "market_analyst": {"provider": "antigravity", "model": "gemini-3.8-flash"},
            "news_analyst": {"provider": "antigravity", "model": "gemini-3.8-flash"},
            "bull_researcher": {"provider": "claude-cli", "model": "claude-sonnet-5"},
            "portfolio_manager": {"provider": "codex", "model": "gpt-6-astra"},
        },
    }

    graph = TradingAgentsGraph(selected_analysts=("market", "news"), config=cfg)

    # market_analyst and news_analyst share the same (antigravity, gemini-3.8-flash) LLM instance
    assert graph.agent_llms["market_analyst"] is graph.agent_llms["news_analyst"]
    assert graph.agent_llms["market_analyst"].provider == "antigravity"
    assert graph.agent_llms["bull_researcher"].provider == "claude-cli"
    assert graph.agent_llms["portfolio_manager"].provider == "codex"
    assert "bear_researcher" not in graph.agent_llms
    assert graph.graph_setup._llm_for_role("bear_researcher", graph.quick_thinking_llm) is graph.quick_thinking_llm

    settings = graph.run_settings()
    assert settings["agent_models"]["market_analyst"] == {"provider": "antigravity", "model": "gemini-3.8-flash"}
    assert settings["agent_models"]["bull_researcher"] == {"provider": "claude-cli", "model": "claude-sonnet-5"}
    md_header = _header("NVDA", {"trade_date": "2026-11-03"}, settings)
    assert "market_analyst antigravity gemini-3.8-flash" in md_header
    html_doc = render_report("NVDA", {"trade_date": "2026-11-03"}, settings, [])
    assert "market_analyst" in html_doc and "gemini-3.8-flash" in html_doc


@pytest.mark.unit
def test_webapp_exposes_providers_and_roles_and_accepts_per_role_models(tmp_path):
    built = []

    def graph_factory(analysts, config):
        built.append((analysts, config))
        raise RuntimeError("stop after capturing config")

    client = _client(tmp_path, graph_factory=graph_factory)
    options = client.get("/api/options").json()

    provider_keys = [p["key"] for p in options["providers"]]
    assert "chatgpt" in provider_keys
    assert "antigravity" in provider_keys
    assert "claude-cli" in provider_keys
    assert "codex" in provider_keys
    assert [r["key"] for r in options["roles"]] == [s["key"] for s in AGENT_ROLE_SPECS]
    assert len(get_model_options("chatgpt", "quick")) > 1

    res = client.post(
        "/api/runs",
        json={
            "ticker": "NVDA",
            "trade_date": TRADE_DATE,
            "analysts": ["market", "news"],
            "quick_think_provider": "antigravity",
            "quick_think_llm": "gemini-3.8-flash",
            "deep_think_provider": "chatgpt",
            "deep_think_llm": "gpt-6-sol",
            "agent_models": {
                "market_analyst": {"provider": "antigravity", "model": "gemini-3.8-flash-high"},
                "bull_researcher": {"provider": "claude-cli", "model": "claude-sonnet-5"},
                "portfolio_manager": {"provider": "codex", "model": "gpt-6-astra"},
            },
        },
    )
    assert res.status_code == 202
    _, cfg = built[0]
    assert cfg["quick_think_provider"] == "antigravity"
    assert cfg["deep_think_provider"] == "chatgpt"
    assert cfg["agent_models"]["market_analyst"] == {"provider": "antigravity", "model": "gemini-3.8-flash-high"}
    assert cfg["agent_models"]["bull_researcher"] == {"provider": "claude-cli", "model": "claude-sonnet-5"}
    assert cfg["agent_models"]["portfolio_manager"] == {"provider": "codex", "model": "gpt-6-astra"}

    # Invalid role or provider is rejected with 422 before starting a run
    bad_role = client.post(
        "/api/runs",
        json={
            "ticker": "NVDA",
            "trade_date": TRADE_DATE,
            "agent_models": {"nonexistent_role": {"provider": "antigravity", "model": "gemini-3.8-flash"}},
        },
    )
    assert bad_role.status_code == 422

    bad_provider = client.post(
        "/api/runs",
        json={
            "ticker": "NVDA",
            "trade_date": TRADE_DATE,
            "agent_models": {"market_analyst": {"provider": "unknown_vendor", "model": "m"}},
        },
    )
    assert bad_provider.status_code == 422


@pytest.mark.unit
def test_webapp_settings_persistence(tmp_path):
    settings_file = tmp_path / "web_settings.json"
    client = _client(tmp_path, settings_path=settings_file)
    res = client.put(
        "/api/settings",
        json={
            "llm_provider": "antigravity",
            "quick_think_provider": "antigravity",
            "quick_think_llm": "gemini-3.8-flash",
            "deep_think_provider": "claude-cli",
            "deep_think_llm": "claude-opus-5",
            "agent_models": {
                "bull_researcher": {"provider": "claude-cli", "model": "claude-sonnet-5"},
                "portfolio_manager": {"provider": "codex", "model": "gpt-6-sol"},
            },
            "openai_reasoning_effort": "high",
            "google_thinking_level": "high",
            "anthropic_effort": "max",
        },
    )
    assert res.status_code == 200
    updated_opts = res.json()
    assert updated_opts["defaults"]["quick_think_provider"] == "antigravity"
    assert updated_opts["defaults"]["deep_think_provider"] == "claude-cli"
    assert updated_opts["defaults"]["agent_models"]["portfolio_manager"] == {
        "provider": "codex",
        "model": "gpt-6-sol",
    }
    assert settings_file.is_file()

    # A newly created app loading from the same settings_path restores all saved defaults
    reloaded = _client(tmp_path, settings_path=settings_file).get("/api/options").json()
    assert reloaded["defaults"]["quick_think_provider"] == "antigravity"
    assert reloaded["defaults"]["deep_think_provider"] == "claude-cli"
    assert reloaded["defaults"]["agent_models"]["bull_researcher"] == {
        "provider": "claude-cli",
        "model": "claude-sonnet-5",
    }
    assert reloaded["defaults"]["anthropic_effort"] == "max"
