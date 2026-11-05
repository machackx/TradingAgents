"""Runs started from the page: one at a time, on a worker thread of their own.

A run's events are kept in memory for every page watching it (tokens included)
and written to its events file for replay (tokens left out). One run at a time:
each run spends model quota and calls the data vendors, and the page has one
run to show.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
import uuid
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cli.prompts import detect_asset_type
from tradingagents.dataflows.date_window import get_current_date
from tradingagents.dataflows.symbols import normalize_symbol, safe_ticker_component
from tradingagents.graph.events import SCHEMA_VERSION, JsonlEventSink, RunEvents, events_path
from tradingagents.llm_clients.factory import normalize_role_key
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

from .history import report_dir

logger = logging.getLogger(__name__)

ANALYSTS = ("market", "social", "news", "fundamentals")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._:/@+-]+$")
# Runs that ended are kept in memory this long (by count) for pages still
# replaying their tokens; older ones are read back from disk.
_KEEP_ENDED = 5


class RunRequest(BaseModel):
    """What the page may choose for a run; the rest comes from the server's config."""

    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=32)
    trade_date: str = Field(description="YYYY-MM-DD, no later than today")
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: list(ANALYSTS), min_length=1)
    max_debate_rounds: int | None = Field(None, ge=1, le=5)
    max_risk_discuss_rounds: int | None = Field(None, ge=1, le=5)
    output_language: str | None = Field(None, min_length=1, max_length=40)
    quick_think_provider: str | None = Field(None, min_length=1, max_length=64)
    deep_think_provider: str | None = Field(None, min_length=1, max_length=64)
    quick_think_llm: str | None = Field(None, min_length=1, max_length=100)
    deep_think_llm: str | None = Field(None, min_length=1, max_length=100)
    agent_models: dict[str, dict[str, str]] | None = Field(
        None, description="Per-role provider and model overrides"
    )
    openai_reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    google_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    anthropic_effort: Literal["low", "medium", "high", "max"] | None = None
    tokens: bool = Field(True, description="Stream the models' text as it is generated")

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        return safe_ticker_component(normalize_symbol(value.strip()))

    @field_validator("trade_date")
    @classmethod
    def _trade_date(cls, value: str) -> str:
        try:
            canonical = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
        except ValueError:
            canonical = False
        if not canonical:
            raise ValueError("trade_date must be a date in YYYY-MM-DD format")
        if value > get_current_date():
            raise ValueError("trade_date cannot be in the future")
        return value

    @field_validator("quick_think_llm", "deep_think_llm")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        if value is not None and not _MODEL_ID.match(value):
            raise ValueError("model id has characters a model id does not use")
        return value

    @field_validator("quick_think_provider", "deep_think_provider")
    @classmethod
    def _provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned not in MODEL_OPTIONS:
            raise ValueError(f"unsupported provider: {value!r}")
        return cleaned

    @field_validator("agent_models")
    @classmethod
    def _validate_agent_models(cls, value: dict[str, dict[str, str]] | None) -> dict[str, dict[str, str]] | None:
        if value is None:
            return None
        normalized: dict[str, dict[str, str]] = {}
        for raw_role, spec in value.items():
            canonical = normalize_role_key(raw_role)
            if not isinstance(spec, dict):
                raise ValueError(f"agent_models[{raw_role!r}] must be an object")
            unknown_keys = set(spec.keys()) - {"provider", "model"}
            if unknown_keys:
                raise ValueError(f"unexpected keys in agent_models[{raw_role!r}]: {sorted(unknown_keys)}")
            entry: dict[str, str] = {}
            prov = (spec.get("provider") or "").strip().lower()
            mod = (spec.get("model") or "").strip()
            if prov:
                if prov not in MODEL_OPTIONS:
                    raise ValueError(f"unsupported provider {prov!r} for role {canonical!r}")
                entry["provider"] = prov
            if mod:
                if not _MODEL_ID.match(mod):
                    raise ValueError(f"invalid model id {mod!r} for role {canonical!r}")
                entry["model"] = mod
            if entry:
                normalized[canonical] = entry
        return normalized

    @field_validator("output_language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        if value is not None and not value.replace(" ", "").replace("-", "").isalpha():
            raise ValueError("output_language must be the name of a language")
        return value

    @model_validator(mode="after")
    def _analysts(self) -> RunRequest:
        chosen = set(self.analysts)
        if self.asset_type == "crypto":
            chosen.discard("fundamentals")    # as the CLI: no statements to read
        if not chosen:
            raise ValueError("no analyst left for this asset: fundamentals does not cover crypto")
        self.analysts = [a for a in ANALYSTS if a in chosen]
        return self

    @property
    def asset_type(self) -> str:
        return detect_asset_type(self.ticker).value


def build_config(base: dict, request: RunRequest) -> dict:
    """The server's config with the page's choices applied."""
    config = copy.deepcopy(base)
    for key in (
        "max_debate_rounds",
        "max_risk_discuss_rounds",
        "output_language",
        "quick_think_provider",
        "deep_think_provider",
        "quick_think_llm",
        "deep_think_llm",
        "openai_reasoning_effort",
        "google_thinking_level",
        "anthropic_effort",
    ):
        value = getattr(request, key)
        if value is not None:
            config[key] = value
    if request.agent_models is not None:
        merged = dict(config.get("agent_models") or {})
        merged.update(request.agent_models)
        config["agent_models"] = merged
    return config


class WebSettingsRequest(BaseModel):
    """Persistent default settings configurable directly from the Web UI."""

    model_config = ConfigDict(extra="forbid")

    llm_provider: str | None = Field(None, min_length=1, max_length=64)
    quick_think_provider: str | None = Field(None, min_length=1, max_length=64)
    deep_think_provider: str | None = Field(None, min_length=1, max_length=64)
    quick_think_llm: str | None = Field(None, min_length=1, max_length=100)
    deep_think_llm: str | None = Field(None, min_length=1, max_length=100)
    agent_models: dict[str, dict[str, str]] | None = None
    max_debate_rounds: int | None = Field(None, ge=1, le=5)
    max_risk_discuss_rounds: int | None = Field(None, ge=1, le=5)
    output_language: str | None = Field(None, min_length=1, max_length=40)
    openai_reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    google_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    anthropic_effort: Literal["low", "medium", "high", "max"] | None = None

    @field_validator("llm_provider", "quick_think_provider", "deep_think_provider")
    @classmethod
    def _provider(cls, value: str | None) -> str | None:
        return RunRequest._provider(value)

    @field_validator("quick_think_llm", "deep_think_llm")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        return RunRequest._model(value)

    @field_validator("agent_models")
    @classmethod
    def _validate_agent_models(cls, value: dict[str, dict[str, str]] | None) -> dict[str, dict[str, str]] | None:
        return RunRequest._validate_agent_models(value)

    @field_validator("output_language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return RunRequest._language(value)


class RunBusy(RuntimeError):
    def __init__(self, run_id: str):
        super().__init__(f"run {run_id} is still in progress")
        self.run_id = run_id


class LiveRun:
    """A run this process started, with every event it has emitted so far."""

    def __init__(self, run_id: str, request: RunRequest, config: dict):
        self.run_id = run_id
        self.request = request
        self.config = config
        self.events: list[dict] = []
        self.done = False
        self._seqs: list[int] = []
        self._cond = threading.Condition()
        self._run: RunEvents | None = None
        self._cancel_requested = False

    @property
    def report_dir(self):
        return report_dir(self.config["results_dir"], self.request.ticker, self.request.trade_date, self.run_id)

    def publish(self, event: dict) -> None:
        with self._cond:
            self.events.append(event)
            self._seqs.append(event["seq"])
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def since(self, after: int) -> list[dict]:
        with self._cond:
            return self.events[bisect_right(self._seqs, after):]

    def wait(self, after: int, timeout: float) -> tuple[list[dict], bool]:
        """Events after ``after``, waiting up to ``timeout`` for one; and whether the run is done."""
        with self._cond:
            self._cond.wait_for(lambda: self.done or (self._seqs and self._seqs[-1] > after), timeout)
            return self.events[bisect_right(self._seqs, after):], self.done

    def attach(self, run: RunEvents) -> None:
        with self._cond:
            self._run = run
            if self._cancel_requested:
                run.cancel()

    def cancel(self) -> None:
        with self._cond:
            self._cancel_requested = True
            if self._run is not None:
                self._run.cancel()


def _failure(run_id: str, exc: BaseException, started: float) -> dict:
    """The one event of a run whose graph could not be built (a sign-in or key missing, say)."""
    return {"v": SCHEMA_VERSION, "seq": 1, "ts": round(time.time(), 3), "run_id": run_id,
            "type": "run_failed", "error_type": type(exc).__name__, "message": str(exc),
            "duration_s": round(time.monotonic() - started, 3),
            "stats": {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}}


def _default_graph_factory(analysts: list[str], config: dict):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    return TradingAgentsGraph(analysts, config=config)


class RunManager:
    """Starts runs one at a time and keeps the recent ones' events in memory."""

    def __init__(self, config: dict, graph_factory: Callable[[list[str], dict], object] | None = None):
        self.config = config
        self.graph_factory = graph_factory or _default_graph_factory
        self._lock = threading.Lock()
        self._runs: OrderedDict[str, LiveRun] = OrderedDict()
        self._active: LiveRun | None = None

    @property
    def active(self) -> LiveRun | None:
        with self._lock:
            return self._active if self._active is not None and not self._active.done else None

    def get(self, run_id: str) -> LiveRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def recent(self) -> list[LiveRun]:
        """The runs still held in memory, the one in progress among them."""
        with self._lock:
            return list(self._runs.values())

    def start(self, request: RunRequest) -> LiveRun:
        with self._lock:
            if self._active is not None and not self._active.done:
                raise RunBusy(self._active.run_id)
            live = LiveRun(uuid.uuid4().hex, request, build_config(self.config, request))
            self._active = live
            self._runs[live.run_id] = live
            while len(self._runs) > _KEEP_ENDED + 1:
                oldest = next(iter(self._runs))
                if self._runs[oldest] is live:
                    break
                del self._runs[oldest]
        threading.Thread(target=self._work, args=(live,), name=f"run-{live.run_id[:8]}", daemon=True).start()
        return live

    def _work(self, live: LiveRun) -> None:
        request, config = live.request, live.config
        started = time.monotonic()
        try:
            with JsonlEventSink(events_path(config, request.ticker, request.trade_date)) as sink:
                def publish(event: dict) -> None:
                    live.publish(event)     # the page first: a full disk must not blank it
                    sink(event)

                try:
                    graph = self.graph_factory(request.analysts, config)
                except Exception as exc:
                    logger.warning("Run %s could not start: %s", live.run_id, exc)
                    publish(_failure(live.run_id, exc, started))
                    return
                run = RunEvents(graph, request.ticker, request.trade_date, request.asset_type,
                                sink=publish, tokens=request.tokens, run_id=live.run_id)
                live.attach(run)
                try:
                    for _ in run:
                        pass
                except Exception as exc:    # its run_failed event says what happened
                    logger.warning("Run %s failed: %s", live.run_id, exc)
                if run.final_state is not None:
                    try:
                        graph.save_reports(run.final_state, request.ticker, live.report_dir)
                    except Exception as exc:
                        logger.warning("Run %s: the report could not be saved: %s", live.run_id, exc)
        except Exception as exc:     # the events file could not be opened, say
            logger.exception("Run %s stopped unexpectedly", live.run_id)
            if not live.events:
                live.publish(_failure(live.run_id, exc, started))
        finally:
            live.finish()
