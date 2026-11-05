"""Regression coverage for Codex's strict output-schema wire format."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from tradingagents.agents.analysts.fundamentals_analyst import TOOLS
from tradingagents.agents.schemas import PortfolioDecision
from tradingagents.llm_clients.codex_cli_client import ChatCodexCLI

pytestmark = pytest.mark.unit


@pytest.fixture
def codex_binary(monkeypatch, tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("CODEX_CLI_PATH", str(binary))


def _assert_strict_objects(schema):
    if isinstance(schema, list):
        for item in schema:
            _assert_strict_objects(item)
    elif isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert set(schema.get("required", [])) == set(schema.get("properties", {}))
        assert "default" not in schema
        for key, value in schema.items():
            if key in ("properties", "$defs", "definitions"):
                for child in value.values():
                    _assert_strict_objects(child)
            elif key in ("items", "anyOf", "allOf"):
                _assert_strict_objects(value)


def test_fundamentals_tools_have_closed_per_tool_argument_schemas(codex_binary, monkeypatch):
    expected_calls = [
        {"name": "get_balance_sheet", "arguments": {"freq": "quarterly", "curr_date": "2026-11-05"}},
        {"name": "get_insider_transactions", "arguments": {}},
    ]
    wire_schemas = []

    def fake_run(command, **kwargs):
        schema_path = Path(command[command.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text())
        wire_schemas.append((schema_path, schema))
        _assert_strict_objects(schema)
        variants = schema["properties"]["tool_calls"]["items"]["anyOf"]
        by_name = {v["properties"]["name"]["enum"][0]: v["properties"]["arguments"] for v in variants}
        assert set(by_name) == {tool.name for tool in TOOLS}
        assert set(by_name["get_balance_sheet"]["properties"]) == {"freq", "curr_date"}
        assert by_name["get_balance_sheet"]["properties"]["freq"]["enum"] == ["annual", "quarterly"]
        assert by_name["get_insider_transactions"]["properties"] == {}
        assert kwargs["stdin"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0, stdout=json.dumps({"tool_calls": expected_calls, "final_answer": ""}), stderr="")

    monkeypatch.setattr("tradingagents.llm_clients.codex_cli_client.subprocess.run", fake_run)
    bound = ChatCodexCLI(model="gpt-6.1-terra", max_retries=0).bind_tools(TOOLS)
    original_tools = copy.deepcopy(bound.bound_tools)
    result = bound.invoke([HumanMessage("Request the balance sheet and insider transactions for AAPL.")])
    assert [{"name": call["name"], "arguments": call["args"]} for call in result.tool_calls] == expected_calls
    assert bound.bound_tools == original_tools
    assert not wire_schemas[0][0].exists()


def test_pydantic_decision_schema_is_strict_and_still_parses(codex_binary, monkeypatch):
    schema_before = PortfolioDecision.model_json_schema()
    decision = {
        "rating": "Hold", "executive_summary": "Insufficient evidence.",
        "investment_thesis": "Wait for data.", "price_target": None, "time_horizon": None,
    }

    def fake_run(command, **kwargs):
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        _assert_strict_objects(schema)
        assert set(schema["required"]) == set(PortfolioDecision.model_fields)
        assert {"type": "null"} in schema["properties"]["price_target"]["anyOf"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(decision), stderr="")

    monkeypatch.setattr("tradingagents.llm_clients.codex_cli_client.subprocess.run", fake_run)
    result = ChatCodexCLI(model="gpt-6.1-sol", max_retries=0).with_structured_output(PortfolioDecision).invoke("Return a Hold decision.")
    assert isinstance(result, PortfolioDecision)
    assert result.price_target is None
    assert result.rating.value == "Hold"
    assert PortfolioDecision.model_json_schema() == schema_before


def test_nested_referenced_raw_schema_is_normalized_without_mutation(codex_binary, monkeypatch):
    original = {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": {"$ref": "#/$defs/Row"}}},
        "$defs": {"Row": {"type": "object", "properties": {"value": {"type": "integer", "default": 0}}}},
    }
    before = copy.deepcopy(original)

    def fake_run(command, **kwargs):
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        _assert_strict_objects(schema)
        assert schema["properties"]["rows"]["items"]["$ref"] == "#/$defs/Row"
        return SimpleNamespace(returncode=0, stdout='{"rows":[{"value":1}]}', stderr="")

    monkeypatch.setattr("tradingagents.llm_clients.codex_cli_client.subprocess.run", fake_run)
    result = ChatCodexCLI(max_retries=0).with_structured_output(original).invoke("Return one row.")
    assert result == {"rows": [{"value": 1}]}
    assert original == before
