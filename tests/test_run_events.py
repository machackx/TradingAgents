"""A run as events, for a page that draws the analysis as it happens.

The whole graph runs offline on the scripted models of the end-to-end suite;
these tests pin what a page can rely on: every node's start and finish, each
tool call under the analyst that made it, the reports, the debate turns, the
structured decisions as data, and an ending that says how the run went.
"""

from __future__ import annotations

import json
import re

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from tests.test_graph_end_to_end import TRADE_DATE, ScriptedModel, _graph, offline  # noqa: F401
from tradingagents.dataflows import config as dataflows_config, router
from tradingagents.graph.events import (
    REPORT_SECTIONS,
    JsonlEventSink,
    RunEvents,
    events_path,
    read_events,
)

ANALYSTS = ("Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst")
ROUNDS = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 1}


def _of(events, type_):
    return [e for e in events if e["type"] == type_]


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_a_run_reports_every_node_from_start_to_decision(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, ScriptedModel(structured=True), **ROUNDS)
    run = RunEvents(graph, "NVDA", TRADE_DATE)

    events = list(run)

    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert {e["run_id"] for e in events} == {run.run_id}
    for event in events:
        json.dumps(event)   # ready for a server to send as it is

    started, finished = events[0], events[-1]
    assert started["type"] == "run_started" and started["resumed"] is False
    assert started["analysts"] == ["market", "social", "news", "fundamentals"]
    drawn = {n["id"]: n["phase"] for n in started["graph"]["nodes"]}
    assert set(ANALYSTS) <= set(drawn) and "other" not in drawn.values()
    assert {"source": "Research Manager", "target": "Trader", "conditional": False} in started["graph"]["edges"]
    assert finished["type"] == "run_finished" and finished["rating"] == run.rating == "Overweight"
    assert run.final_state["final_rating"] == "Overweight"

    # Every top-level node starts, then finishes; so does every step inside one.
    begun = {e["task_id"]: e for e in _of(events, "node_started")}
    ended = {e["task_id"]: e for e in _of(events, "node_finished")}
    assert begun.keys() == ended.keys()
    assert all(begun[t]["seq"] < ended[t]["seq"] and not ended[t]["error"] for t in ended)
    assert {e["node"] for e in ended.values() if e["step"] is None} == set(drawn)
    assert {e["step"] for e in ended.values() if e["node"] == "Market Analyst"} == {None, "agent", "tools", "finish"}

    # Each tool call carries the analyst that made it, and has its result.
    calls = {(e["node"], e["call_id"]): e for e in _of(events, "tool_call")}
    results = {(e["node"], e["call_id"]): e for e in _of(events, "tool_result")}
    assert calls.keys() == results.keys()
    owner = {e["name"]: e["node"] for e in calls.values()}
    assert owner["get_stock_data"] == "Market Analyst"
    assert owner["get_balance_sheet"] == "Fundamentals Analyst"
    assert owner["get_news"] == "News Analyst"
    assert all(r["usable"] and r["status"] == "success" for r in results.values())

    reports = {e["section"]: e["node"] for e in _of(events, "report")}
    assert reports == {
        "market_report": "Market Analyst", "sentiment_report": "Sentiment Analyst",
        "news_report": "News Analyst", "fundamentals_report": "Fundamentals Analyst",
        "investment_plan": "Research Manager", "trader_investment_plan": "Trader",
        "final_trade_decision": "Portfolio Manager",
    }
    assert set(reports) == set(REPORT_SECTIONS)

    turns = [(e["debate"], e["node"], e["turn"]) for e in _of(events, "debate_turn")]
    assert turns == [
        ("investment", "Bull Researcher", 1), ("investment", "Bear Researcher", 2),
        ("risk", "Aggressive Analyst", 1), ("risk", "Conservative Analyst", 2), ("risk", "Neutral Analyst", 3),
    ]

    structured = {e["schema"]: e for e in _of(events, "structured_output")}
    assert {k: e["node"] for k, e in structured.items()} == {
        "sentiment_report": "Sentiment Analyst", "research_plan": "Research Manager",
        "trader_proposal": "Trader", "portfolio_decision": "Portfolio Manager",
    }
    assert structured["trader_proposal"]["data"]["action"] == "Buy"
    assert structured["portfolio_decision"]["data"]["rating"] == "Overweight"
    # A decision's data comes before the node that made it is done.
    trader_done = next(e for e in ended.values() if e["node"] == "Trader")
    assert structured["trader_proposal"]["seq"] < trader_done["seq"]

    updates = {e["node"]: e["update"] for e in ended.values() if e["step"] is None}
    assert updates["Prepare market data"]["market_data"]["status"] == "available"
    assert updates["Evidence check"]["data_quality"]["status"] == "adequate"
    assert updates["Trader"]["trader_validation"]["status"] == "validated"
    assert updates["Portfolio Manager"] == {"final_rating": "Overweight"}
    assert "market_report" not in updates["Market Analyst"]   # reports come as their own events

    models = _of(events, "llm_call")
    assert {e["node"] for e in models} >= set(ANALYSTS) - {"Sentiment Analyst"} | {"Bull Researcher"}
    assert finished["stats"]["llm_calls"] == len(models)
    assert finished["stats"]["tool_calls"] == len(calls)

    # The run ends like propagate(): its decision is in the memory log.
    assert [e["rating"] for e in graph.memory_log.load_entries()] == ["Overweight"]


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_a_free_text_run_has_the_same_reports_and_no_structured_data(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, ScriptedModel(structured=False), **ROUNDS)

    events = list(RunEvents(graph, "NVDA", TRADE_DATE, max_tool_chars=10))

    assert {e["section"] for e in _of(events, "report")} == set(REPORT_SECTIONS)
    assert _of(events, "structured_output") == []
    assert events[-1]["rating"] == "Overweight"
    snapshot = next(e for e in _of(events, "tool_result") if e["name"] == "get_verified_market_snapshot")
    assert snapshot["truncated"] and len(snapshot["content"]) == 10 < snapshot["chars"]


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_the_events_are_saved_for_replay(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, ScriptedModel(structured=True), **ROUNDS)
    path = events_path(graph.config, "NVDA", TRADE_DATE)

    with JsonlEventSink(path) as sink:
        events = list(RunEvents(graph, "NVDA", TRADE_DATE, sink=sink))

    assert path == tmp_path / "results" / "NVDA" / TRADE_DATE / "events.jsonl"
    assert read_events(path) == events
    with open(path, "a", encoding="utf-8") as file:
        file.write('{"type": "node_sta')   # a crash mid-write
    assert read_events(path) == events


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_a_failed_run_says_so_and_a_later_run_resumes_it(tmp_path, monkeypatch):
    model = ScriptedModel(fail_at=12)            # past the analysts, before the decision
    graph = _graph(tmp_path, monkeypatch, model, checkpoint_enabled=True, **ROUNDS)
    path = events_path(graph.config, "NVDA", TRADE_DATE)
    first = RunEvents(graph, "NVDA", TRADE_DATE)

    with JsonlEventSink(path) as sink:
        first.sink = sink
        failed = []
        with pytest.raises(RuntimeError, match="provider unavailable"):
            for event in first:
                failed.append(event)
        resumed = list(RunEvents(graph, "NVDA", TRADE_DATE, sink=sink))

    assert failed[-1]["type"] == "run_failed" and failed[-1]["error_type"] == "RuntimeError"
    assert first.final_state is None
    assert resumed[0]["resumed"] is True and resumed[-1]["rating"] == "Overweight"
    # The analysts' work was kept: the resumed run does not redo it.
    assert not {e["node"] for e in _of(resumed, "node_started")} & set(ANALYSTS)
    assert read_events(path, run_id=first.run_id) == failed
    assert [e["rating"] for e in graph.memory_log.load_entries()] == ["Overweight"]


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_a_cancelled_run_stops_between_steps_and_can_resume(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, ScriptedModel(structured=True), checkpoint_enabled=True, **ROUNDS)
    run = RunEvents(graph, "NVDA", TRADE_DATE)

    events = []
    for event in run:
        events.append(event)
        if event["type"] == "node_finished" and event["node"] == "Evidence check":
            run.cancel()

    assert events[-1]["type"] == "run_cancelled"
    assert "Bull Researcher" not in {e.get("node") for e in events}
    assert graph.memory_log.load_entries() == []

    resumed = list(RunEvents(graph, "NVDA", TRADE_DATE))
    assert resumed[0]["resumed"] is True and resumed[-1]["rating"] == "Overweight"
    assert not {e["node"] for e in _of(resumed, "node_started")} & set(ANALYSTS)


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_a_run_that_cannot_start_says_why(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, ScriptedModel(), **ROUNDS)
    events = []

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        for event in RunEvents(graph, "NVDA", "09/01/2026"):
            events.append(event)

    assert [e["type"] for e in events] == ["run_failed"]
    assert events[0]["error_type"] == "ValueError"


class StreamingModel(ScriptedModel):
    """ScriptedModel that streams its text a word at a time."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages, stop, run_manager, **kwargs).generations[0].message
        if message.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ]))
            return
        for word in re.split(r"(\s+)", message.content):
            if word:
                yield ChatGenerationChunk(message=AIMessageChunk(content=word))


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_tokens_stream_only_when_asked_and_are_not_saved(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, StreamingModel(structured=True), **ROUNDS)
    assert _of(list(RunEvents(graph, "NVDA", TRADE_DATE)), "token") == []

    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink:
        events = list(RunEvents(graph, "NVDA", "2026-01-08", tokens=True, sink=sink))

    tokens = _of(events, "token")
    bull = "".join(e["text"] for e in tokens if e["node"] == "Bull Researcher")
    turn = next(e for e in _of(events, "debate_turn") if e["node"] == "Bull Researcher")
    assert bull and turn["content"].endswith(bull)
    assert {e["node"] for e in tokens} >= {"Market Analyst", "Bull Researcher"}
    assert not [e for e in read_events(path) if e["type"] == "token"]
    assert events[-1]["rating"] == "Overweight"


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_the_run_reads_its_own_config_and_the_caller_keeps_its_own(tmp_path, monkeypatch):
    """A server iterates many runs in one process; each run's tools read that
    run's config, and the server sees its own between events."""
    graph = _graph(tmp_path, monkeypatch, ScriptedModel(), output_language="French", **ROUNDS)
    monkeypatch.setattr(dataflows_config, "_config", None)   # restored after the test
    dataflows_config.set_config({"output_language": "German"})
    seen = []
    monkeypatch.setitem(router.VENDOR_METHODS["get_stock_data"], "yfinance",
                        lambda *a, **k: seen.append(dataflows_config.get_config()["output_language"]) or "prices")

    between = [dataflows_config.get_config()["output_language"] for _ in RunEvents(graph, "NVDA", TRADE_DATE)]

    assert seen and set(seen) == {"French"}
    assert set(between) == {"German"}


class ReasoningStreamingModel(StreamingModel):
    """ScriptedModel that attaches reasoning_content to its replies and streams thinking."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        res = super()._generate(messages, stop, run_manager, **kwargs)
        res.generations[0].message.additional_kwargs["reasoning_content"] = "Step 1: analyze trend."
        return res

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        yield ChatGenerationChunk(
            message=AIMessageChunk(content="", additional_kwargs={"reasoning_content": "Step 1: analyze trend."})
        )
        yield from super()._stream(messages, stop, run_manager, **kwargs)


@pytest.mark.unit
@pytest.mark.usefixtures("offline")
def test_events_include_prompts_reasoning_prefetched_corpus_and_settlement_details(tmp_path, monkeypatch):
    graph = _graph(tmp_path, monkeypatch, ReasoningStreamingModel(structured=True), **ROUNDS)
    events = list(RunEvents(graph, "NVDA", TRADE_DATE, tokens=True))

    # LLM calls carry input prompts and reasoning_content
    llm_calls = _of(events, "llm_call")
    assert any(c.get("prompts") for c in llm_calls)
    assert any(c.get("reasoning") == "Step 1: analyze trend." for c in llm_calls)

    # Tool-calling turns emit message events when reasoning is present
    msgs = _of(events, "message")
    assert any(m.get("reasoning") == "Step 1: analyze trend." for m in msgs)

    # Token stream includes thinking chunks
    tokens = _of(events, "token")
    assert any(t.get("thinking") == "Step 1: analyze trend." for t in tokens)

    # Sentiment Analyst's analyst_evidence includes pre-fetched corpus content
    ended = {e["node"]: e["update"] for e in _of(events, "node_finished") if e["step"] is None}
    social_ev = ended["Sentiment Analyst"]["analyst_evidence"]["social"]
    assert "content" in social_ev["news"] and social_ev["news"]["chars"] > 0
    assert "content" in social_ev["stocktwits"]

    # Memory Log node reports memory_settlement structure
    assert "memory_settlement" in ended["Memory Log"]
    assert "settled" in ended["Memory Log"]["memory_settlement"]
    assert "failed" in ended["Memory Log"]["memory_settlement"]
