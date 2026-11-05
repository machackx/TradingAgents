"""The local web server: runs started, streamed, listed and replayed over HTTP.

Runs use the end-to-end suite's scripted models, so nothing leaves the machine.
"""

from __future__ import annotations

import copy
import json
import threading

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import Field  # noqa: E402

from tests.test_graph_end_to_end import TRADE_DATE, ScriptedModel, _Client, offline  # noqa: E402, F401
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph import trading_graph  # noqa: E402
from webapp.server import create_app  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("offline")]


def _config(tmp_path) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(results_dir=str(tmp_path / "results"), data_cache_dir=str(tmp_path / "cache"),
                  memory_log_path=str(tmp_path / "log.md"), checkpoint_enabled=False,
                  max_debate_rounds=1, max_risk_discuss_rounds=1)
    return config


@pytest.fixture
def scripted(monkeypatch):
    """Every graph the server builds runs on ``model``."""
    def use(model):
        monkeypatch.setattr(trading_graph, "create_tier_client", lambda config, tier, **k: _Client(model))
        return model
    return use


def _client(tmp_path, **kwargs) -> TestClient:
    return TestClient(create_app(_config(tmp_path), **kwargs), base_url="http://localhost")


def _sse(client, url, headers=None):
    """A stream read to its end: its events, and its ``end`` event's data."""
    events, end, kind, data = [], None, None, []
    with client.stream("GET", url, headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event:"):
                kind = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].strip())
            elif line == "":
                if data:
                    payload = json.loads("\n".join(data))
                    if kind == "end":
                        end = payload
                    else:
                        events.append(payload)
                kind, data = None, []
    return events, end


class HeldModel(ScriptedModel):
    """ScriptedModel whose every call waits until ``gate`` opens."""

    gate: threading.Event = Field(default_factory=threading.Event)   # shared across bound copies

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        assert self.gate.wait(10), "the test never opened the gate"
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_a_run_started_from_the_page_streams_to_its_end_and_stays_replayable(tmp_path, scripted):
    scripted(ScriptedModel(structured=True))
    client = _client(tmp_path)

    started = client.post("/api/runs", json={"ticker": "nvda", "trade_date": TRADE_DATE,
                                             "analysts": ["news", "market"]})

    assert started.status_code == 202
    run = started.json()["run"]
    run_id = run["run_id"]
    assert run["ticker"] == "NVDA" and run["trade_date"] == TRADE_DATE
    events, end = _sse(client, f"/api/runs/{run_id}/stream")
    assert events[0]["type"] == "run_started" and events[0]["analysts"] == ["market", "news"]
    assert events[-1]["type"] == "run_finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert end["status"] == "finished" and end["rating"] == "Overweight" and end["has_report"]

    # A page that reconnects picks up after the last event it saw.
    assert _sse(client, f"/api/runs/{run_id}/stream", {"Last-Event-ID": "5"})[0] == events[5:]
    assert _sse(client, f"/api/runs/{run_id}/stream?after=10")[0] == events[10:]
    assert client.get(f"/api/runs/{run_id}/events?after=3").json()["events"] == events[3:]

    assert [r["run_id"] for r in client.get("/api/runs").json()["runs"]] == [run_id]
    page = client.get(f"/api/runs/{run_id}/report")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Overweight" in page.text

    # A restarted server reads the run back from disk.
    restarted = _client(tmp_path)
    listed = restarted.get("/api/runs").json()["runs"]
    assert [(r["run_id"], r["status"], r["rating"], r["has_report"]) for r in listed] == [
        (run_id, "finished", "Overweight", True)]
    replayed, end = _sse(restarted, f"/api/runs/{run_id}/stream")
    assert replayed == events and end["status"] == "finished"
    assert restarted.get(f"/api/runs/{run_id}/report").status_code == 200


def test_one_run_at_a_time_and_a_run_can_be_cancelled(tmp_path, scripted):
    model = scripted(HeldModel(structured=True))
    client = _client(tmp_path)
    run_id = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": TRADE_DATE}).json()["run"]["run_id"]

    busy = client.post("/api/runs", json={"ticker": "AAPL", "trade_date": TRADE_DATE})
    assert busy.status_code == 409 and busy.json()["detail"]["active_run"] == run_id
    assert client.get("/api/health").json()["active_run"] == run_id
    assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "running"

    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
    model.gate.set()
    events, end = _sse(client, f"/api/runs/{run_id}/stream")

    assert events[-1]["type"] == "run_cancelled" and end["status"] == "cancelled"
    assert not end["has_report"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409
    assert client.get("/api/health").json()["active_run"] is None
    assert client.post("/api/runs", json={"ticker": "AAPL", "trade_date": TRADE_DATE}).status_code == 202


@pytest.mark.parametrize("body", [
    {"ticker": "NVDA", "trade_date": "2999-01-01"},
    {"ticker": "NVDA", "trade_date": "09/01/2026"},
    {"ticker": "../etc", "trade_date": TRADE_DATE},
    {"ticker": "NVDA", "trade_date": TRADE_DATE, "analysts": ["quant"]},
    {"ticker": "NVDA", "trade_date": TRADE_DATE, "analysts": []},
    {"ticker": "BTC-USD", "trade_date": TRADE_DATE, "analysts": ["fundamentals"]},
    {"ticker": "NVDA", "trade_date": TRADE_DATE, "quick_think_llm": "gpt 6; rm"},
    {"ticker": "NVDA", "trade_date": TRADE_DATE, "max_debate_rounds": 50},
    {"ticker": "NVDA", "trade_date": TRADE_DATE, "llm_provider": "openai"},   # not the page's to choose
], ids=["future", "format", "path", "analyst", "none", "crypto-fundamentals", "model", "rounds", "provider"])
def test_a_request_the_run_could_not_honour_is_refused_before_anything_starts(tmp_path, body):
    built = []
    client = _client(tmp_path, graph_factory=lambda *a: built.append(a))

    assert client.post("/api/runs", json=body).status_code == 422
    assert built == [] and client.get("/api/runs").json()["runs"] == []


def test_the_page_chooses_rounds_models_and_language_for_its_run(tmp_path):
    built = []

    def factory(analysts, config):
        built.append((analysts, config))
        raise RuntimeError("stop here")

    client = _client(tmp_path, graph_factory=factory)
    run_id = client.post("/api/runs", json={
        "ticker": "BTC-USD", "trade_date": TRADE_DATE, "analysts": ["fundamentals", "market"],
        "max_debate_rounds": 2, "deep_think_llm": "gpt-6-sol", "output_language": "Chinese",
    }).json()["run"]["run_id"]
    _sse(client, f"/api/runs/{run_id}/stream")

    analysts, config = built[0]
    assert analysts == ["market"]          # fundamentals does not cover crypto
    assert config["max_debate_rounds"] == 2 and config["max_risk_discuss_rounds"] == 1
    assert config["deep_think_llm"] == "gpt-6-sol" and config["output_language"] == "Chinese"


def test_a_run_whose_graph_cannot_be_built_says_why(tmp_path):
    def not_signed_in(analysts, config):
        raise RuntimeError("Not signed in to ChatGPT")

    client = _client(tmp_path, graph_factory=not_signed_in)
    run_id = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": TRADE_DATE}).json()["run"]["run_id"]

    events, end = _sse(client, f"/api/runs/{run_id}/stream")

    assert [e["type"] for e in events] == ["run_failed"]
    assert end["status"] == "failed" and "Not signed in" in end["error"]
    restarted = _client(tmp_path).get(f"/api/runs/{run_id}").json()["run"]
    assert restarted["status"] == "failed" and restarted["ticker"] == "NVDA"


def test_requests_from_other_sites_and_other_hosts_are_refused(tmp_path):
    client = _client(tmp_path, graph_factory=lambda *a: pytest.fail("no run may start"))
    body = {"ticker": "NVDA", "trade_date": TRADE_DATE}

    assert client.post("/api/runs", json=body, headers={"Origin": "https://example.com"}).status_code == 403
    # The dev server's page is on this machine: it passes (to validation, here).
    assert client.post("/api/runs", json={}, headers={"Origin": "http://localhost:5173"}).status_code == 422
    rebound = TestClient(create_app(_config(tmp_path)), base_url="http://attacker.example")
    assert rebound.get("/api/health").status_code == 400


def test_unknown_runs_are_not_found(tmp_path):
    client = _client(tmp_path)
    for run_id in ("0" * 32, "not-a-run", "..%2F..%2Fetc"):
        assert client.get(f"/api/runs/{run_id}").status_code == 404
        assert client.get(f"/api/runs/{run_id}/events").status_code == 404
        assert client.get(f"/api/runs/{run_id}/stream").status_code == 404
        assert client.get(f"/api/runs/{run_id}/report").status_code == 404
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 404


def test_reports_the_cli_saved_are_listed_and_served(tmp_path):
    saved = tmp_path / "results" / "reports" / "NVDA_20261103_200430"
    saved.mkdir(parents=True)
    (saved / "complete_report.html").write_text("<html>NVDA report</html>", encoding="utf-8")
    (tmp_path / "results" / "reports" / "empty_dir").mkdir()
    client = _client(tmp_path)

    reports = client.get("/api/reports").json()["reports"]

    assert [(r["name"], r["ticker"], r["html"]) for r in reports] == [("NVDA_20261103_200430", "NVDA", True)]
    assert client.get("/api/reports/NVDA_20261103_200430").text == "<html>NVDA report</html>"
    for name in ("empty_dir", "..", "nope"):
        assert client.get(f"/api/reports/{name}").status_code == 404


def test_the_form_gets_its_choices_and_defaults(tmp_path):
    client = _client(tmp_path)

    options = client.get("/api/options").json()
    health = client.get("/api/health").json()

    assert [a["key"] for a in options["analysts"]] == ["market", "social", "news", "fundamentals"]
    assert options["defaults"]["max_debate_rounds"] == 1
    assert {"quick", "deep"} == set(options["models"])
    assert health["ok"] and health["active_run"] is None
