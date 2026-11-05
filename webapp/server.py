"""The HTTP API the page talks to.

=====  =============================  ==========================================
GET    /api/health                    version, and the run in progress if any
GET    /api/options                   analysts, models and defaults for the form
POST   /api/runs                      start a run (202), or 409 while one runs
GET    /api/runs                      every run, newest first
GET    /api/runs/{id}                 one run's summary
GET    /api/runs/{id}/events          its events as JSON (``?after=``, ``?tokens=``)
GET    /api/runs/{id}/stream          its events as Server-Sent Events, live
POST   /api/runs/{id}/cancel          stop it between steps
GET    /api/runs/{id}/report          its saved report page
GET    /api/reports                   reports the CLI saved
GET    /api/reports/{name}            one of those pages
=====  =============================  ==========================================

The stream sends each event as an unnamed message whose ``id`` is the event's
``seq``, so a reconnecting ``EventSource`` resumes where it left off (or pass
``?after=<seq>``), then one ``end`` event once the run is over and its report
saved. A finished run's stream replays its saved events and ends.

The server has no sign-in: it answers only on this machine (``Host`` must be a
loopback name) and refuses a state-changing request sent from another site's
page, which could otherwise start runs on the user's model quota.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

import tradingagents
from tradingagents.dataflows.date_window import get_current_date
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS
from tradingagents.llm_clients.model_catalog import get_model_options

from . import LOOPBACK_HOSTS
from .history import (
    RunHistory,
    RunSummary,
    report_dir,
    saved_report_page,
    saved_reports,
    summarize,
    valid_run_id,
)
from .runs import ANALYSTS, LiveRun, RunBusy, RunManager, RunRequest

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
_KEEPALIVE_S = 10.0
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _same_machine(origin: str) -> bool:
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS


def _frame(event: dict) -> str:
    return f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(config: dict | None = None, *, graph_factory=None,
               allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS) -> FastAPI:
    """The app over ``config`` (the defaults and ``.env`` when None).

    ``graph_factory(analysts, config)`` builds each run's graph; tests pass one
    with scripted models.
    """
    config = copy.deepcopy(config or DEFAULT_CONFIG)
    manager = RunManager(config, graph_factory)
    history = RunHistory(config["results_dir"])

    app = FastAPI(title="TradingAgents", version=tradingagents.__version__,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.manager = manager
    app.state.history = history
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.middleware("http")
    async def refuse_other_sites(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in _SAFE_METHODS and origin and not _same_machine(origin):
            return JSONResponse({"detail": "requests from other sites are refused"}, status_code=403)
        return await call_next(request)

    def live_summary(live: LiveRun) -> RunSummary:
        request = live.request
        events = live.since(0)
        if events:
            summary = summarize(events, request.ticker, request.trade_date)
        else:
            summary = RunSummary(run_id=live.run_id, ticker=request.ticker, trade_date=request.trade_date,
                                 status="running", asset_type=request.asset_type, analysts=request.analysts)
        if not live.done:
            summary.status = "running"
        summary.has_report = (live.report_dir / "complete_report.html").is_file()
        return summary

    def summary_of(run_id: str) -> RunSummary:
        live = manager.get(run_id) if valid_run_id(run_id) else None
        summary = live_summary(live) if live is not None else history.find(run_id)
        if summary is None:
            raise HTTPException(404, "no such run")
        return summary

    @app.get("/api/health")
    def health():
        active = manager.active
        return {"ok": True, "version": tradingagents.__version__,
                "active_run": active.run_id if active else None}

    @app.get("/api/options")
    def options():
        provider = config.get("llm_provider") or ""

        def models(mode: str) -> list[dict]:
            try:
                return [{"label": label, "value": value} for label, value in get_model_options(provider, mode)]
            except (KeyError, AttributeError):
                return []

        return {
            "analysts": [{"key": key, "name": ANALYST_NODE_SPECS[key].agent_node} for key in ANALYSTS],
            "provider": provider,
            "models": {"quick": models("quick"), "deep": models("deep")},
            "defaults": {
                "analysts": list(ANALYSTS),
                "trade_date": get_current_date(),
                "quick_think_llm": config.get("quick_think_llm"),
                "deep_think_llm": config.get("deep_think_llm"),
                "output_language": config.get("output_language"),
                "max_debate_rounds": config.get("max_debate_rounds"),
                "max_risk_discuss_rounds": config.get("max_risk_discuss_rounds"),
            },
            "limits": {"rounds": {"min": 1, "max": 5}},
        }

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest):
        try:
            live = manager.start(request)
        except RunBusy as busy:
            raise HTTPException(409, {"message": "a run is in progress", "active_run": busy.run_id}) from None
        return {"run": live_summary(live).to_dict()}

    @app.get("/api/runs")
    def list_runs():
        runs = {s.run_id: s for s in history.runs()}
        for live in manager.recent():
            runs[live.run_id] = live_summary(live)
        ordered = sorted(runs.values(), key=lambda s: (s.status == "running", s.started_at or ""), reverse=True)
        return {"runs": [s.to_dict() for s in ordered]}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return {"run": summary_of(run_id).to_dict()}

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0, tokens: bool = False):
        summary_of(run_id)
        live = manager.get(run_id)
        events = live.since(after) if live is not None else [
            e for e in history.events(run_id) or [] if e["seq"] > after]
        if not tokens:
            events = [e for e in events if e["type"] != "token"]
        return {"events": events}

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, after: int | None = None,
                     last_event_id: str | None = Header(None)):
        await asyncio.to_thread(summary_of, run_id)
        if after is None:
            after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        live = manager.get(run_id)

        async def frames() -> AsyncIterator[str]:
            position = after
            if live is None:
                for event in await asyncio.to_thread(history.events, run_id) or []:
                    if event["seq"] > position:
                        yield _frame(event)
            else:
                while True:
                    events, done = await asyncio.to_thread(live.wait, position, _KEEPALIVE_S)
                    for event in events:
                        yield _frame(event)
                    if events:
                        position = events[-1]["seq"]
                    if done:
                        break
                    if not events:
                        yield ": keep-alive\n\n"
            ended = await asyncio.to_thread(summary_of, run_id)
            yield f"event: end\ndata: {json.dumps(ended.to_dict(), ensure_ascii=False)}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        summary_of(run_id)
        live = manager.get(run_id)
        if live is None or live.done:
            raise HTTPException(409, "the run is not in progress")
        live.cancel()
        return {"run_id": run_id, "cancelling": True}

    @app.get("/api/runs/{run_id}/report")
    def run_report(run_id: str):
        summary = summary_of(run_id)
        page = report_dir(config["results_dir"], summary.ticker, summary.trade_date, run_id) / "complete_report.html"
        if not page.is_file():
            raise HTTPException(404, "this run has no saved report")
        return FileResponse(page, media_type="text/html")

    @app.get("/api/reports")
    def list_reports():
        return {"reports": saved_reports(config["results_dir"])}

    @app.get("/api/reports/{name}")
    def report_page(name: str):
        page = saved_report_page(config["results_dir"], name)
        if page is None:
            raise HTTPException(404, "no such report")
        return FileResponse(page, media_type="text/html")

    # Anything else under /api is a JSON 404, whether or not the page is built
    # (its static files would otherwise answer it).
    @app.api_route("/api/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    def no_such_endpoint(rest: str):
        raise HTTPException(404, "no such endpoint")

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        def index():
            return {"message": "TradingAgents API; the page is not built yet", "docs": "/api/docs"}

    return app
