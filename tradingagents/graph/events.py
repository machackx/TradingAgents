"""A run as a stream of events, for a page that shows the analysis as it happens.

The CLI infers progress from which state fields have filled in. A page that draws
the graph needs more: when each node starts and finishes, which analyst a tool
call belongs to, the managers' decisions as data rather than markdown, and what
each model call cost. ``RunEvents`` runs the graph the way the CLI does (the same
initial state, checkpoint handling and decision log) and yields one plain,
JSON-ready dict per event, so a server can forward each as it is and
``JsonlEventSink`` can keep them for replay.

Every event has ``v`` (the schema version), ``seq`` (its place in the run),
``ts`` (epoch seconds), ``run_id`` and ``type``. ``node`` is the top-level graph
node the event belongs to, so an analyst's tool calls carry the analyst's name;
``step`` is the step inside it ("agent", "tools", "wrap_up", "finish"), or None
for the node itself.

=================  =====================================================
type               fields
=================  =====================================================
run_started        ticker, trade_date, asset_type, analysts, resumed,
                   instrument_context, settings, graph, started_at
node_started       node, step, task_id
node_finished      node, step, task_id, duration_s, error, update
tool_call          node, step, call_id, name, args
tool_result        node, step, call_id, name, status, usable, chars,
                   content, truncated
message            node, step, content
report             node, section, content
debate_turn        node, debate, turn, content
structured_output  node, step, schema, data
llm_call           node, step, model, tokens_in, tokens_out, duration_s,
                   error
token              node, step, text  (only with ``tokens=True``)
run_finished       rating, duration_s, stats
run_failed         error_type, message, duration_s, stats
run_cancelled      duration_s, stats
=================  =====================================================

``update`` is what the node wrote to the run's state, less its messages, reports
and debate transcripts (those have events of their own): the pinned market data,
evidence coverage, trade validation, rating and so on.

``structured_output`` is the parsed object a structured agent's model returned
(``research_plan``, ``trader_proposal``, ``portfolio_decision``,
``sentiment_report``), caught as it is returned, so the agents need no change.
It is what the model proposed: when a check rejects a proposal, the node's
``report`` and the run's rating say so, and they are what count. An agent that
fell back to free text has none.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel

from tradingagents.agents import schemas
from tradingagents.agents.evidence import usable_result
from tradingagents.agents.rating import run_rating
from tradingagents.dataflows.config import run_config_context
from tradingagents.dataflows.symbols import safe_ticker_component

from .analyst_execution import ANALYST_NODE_SPECS

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# The columns a page draws the graph in, left to right.
PHASES = ("data", "analysts", "evidence", "research", "trading", "risk", "portfolio")
_NODE_PHASES = {
    "Prepare market data": "data",
    **{spec.agent_node: "analysts" for spec in ANALYST_NODE_SPECS.values()},
    "Memory Log": "analysts",          # runs alongside the analysts
    "Evidence check": "evidence",
    "Bull Researcher": "research",
    "Bear Researcher": "research",
    "Research Manager": "research",
    "Trader": "trading",
    "Aggressive Analyst": "risk",
    "Conservative Analyst": "risk",
    "Neutral Analyst": "risk",
    "Portfolio Manager": "portfolio",
}

# Each debate speaker: which debate, the state holding it, and its latest argument.
_DEBATES = {
    "Bull Researcher": ("investment", "investment_debate_state", "current_response"),
    "Bear Researcher": ("investment", "investment_debate_state", "current_response"),
    "Aggressive Analyst": ("risk", "risk_debate_state", "current_aggressive_response"),
    "Conservative Analyst": ("risk", "risk_debate_state", "current_conservative_response"),
    "Neutral Analyst": ("risk", "risk_debate_state", "current_neutral_response"),
}

REPORT_SECTIONS = (
    *(spec.report_key for spec in ANALYST_NODE_SPECS.values()),
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)
# State a node writes that reaches the page as events of its own, not in ``update``.
_CONTENT_KEYS = frozenset({"messages", *REPORT_SECTIONS, "investment_debate_state", "risk_debate_state"})
_GRAPH_ENDS = frozenset({"__start__", "__end__"})


def events_path(config: dict, ticker: str, trade_date: str) -> Path:
    """Where a run's events go: beside the CLI's message log for the same ticker and date."""
    return Path(config["results_dir"]) / safe_ticker_component(ticker) / str(trade_date) / "events.jsonl"


def _jsonable(value: Any) -> Any:
    """``value`` as plain JSON types, anything else as its string."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _text(content: Any) -> str:
    """A message's text, whether its content is a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type", "text") == "text":
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def _reasoning(message: Any) -> str:
    """A message's reasoning/thinking text from additional_kwargs or thinking blocks."""
    if message is None:
        return ""
    parts: list[str] = []
    additional = getattr(message, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "thinking", "reasoning"):
        val = additional.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("thinking", "reasoning"):
                txt = part.get("thinking") or part.get("reasoning") or part.get("text") or ""
                if txt:
                    parts.append(str(txt))
    return "".join(parts)


def _format_prompts(messages_or_prompts: Any, max_chars: int = 6000) -> list[dict]:
    """Turn a chat model's input message batch or prompt strings into JSON-ready dicts."""
    if not messages_or_prompts:
        return []
    batch = messages_or_prompts
    if isinstance(batch, list) and batch and isinstance(batch[0], list):
        batch = batch[0]
    if not isinstance(batch, list):
        batch = [batch]
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    formatted: list[dict] = []
    for item in batch:
        if isinstance(item, str):
            role, text = "user", item
        elif isinstance(item, dict):
            role = str(item.get("role") or item.get("type") or "user")
            text = _text(item.get("content", ""))
        else:
            raw_type = getattr(item, "type", "user")
            role = role_map.get(raw_type, str(raw_type))
            text = _text(getattr(item, "content", ""))
            tool_calls = getattr(item, "tool_calls", None)
            if tool_calls:
                calls = ", ".join(
                    f"{c.get('name')}({json.dumps(c.get('args', {}), ensure_ascii=False, default=str)})"
                    for c in tool_calls
                )
                text = f"{text}\n[Tool calls: {calls}]".strip()
        entry: dict[str, Any] = {"role": role, "content": text[:max_chars]}
        if len(text) > max_chars:
            entry["truncated"] = True
            entry["chars"] = len(text)
        formatted.append(entry)
    return formatted


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _locate(metadata: dict | None) -> tuple[str | None, str | None]:
    """The ``(node, step)`` a callback ran in, from LangGraph's metadata.

    The namespace's first segment is ``"<top-level node>:<task id>"`` whether the
    call ran in that node or in a step of its own graph.
    """
    metadata = metadata or {}
    inner = metadata.get("langgraph_node")
    node = (metadata.get("checkpoint_ns") or "").split("|")[0].partition(":")[0] or inner
    return node, (inner if inner and inner != node else None)


class _RunCallbacks(BaseCallbackHandler):
    """What only callbacks see: parsed structured outputs, and each model call's usage.

    Callbacks fire on the graph's worker threads (the analysts run side by side),
    so they queue their events for the run to emit between steps, in its own order.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._chains: dict = {}       # run id -> metadata, while the runnable runs
        self._models: dict = {}       # run id -> (metadata, started)
        self._reported: list = []     # a parser and the sequence around it end with one object

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs) -> None:
        with self._lock:
            self._chains[run_id] = metadata or {}

    def on_chain_end(self, outputs, *, run_id, **kwargs) -> None:
        with self._lock:
            metadata = self._chains.pop(run_id, None)
            if not (isinstance(outputs, BaseModel) and type(outputs).__module__ == schemas.__name__):
                return
            if any(seen is outputs for seen in self._reported):
                return
            self._reported.append(outputs)
        node, step = _locate(metadata)
        self._pending.put(("structured_output", {
            "node": node, "step": step, "schema": _snake(type(outputs).__name__),
            "data": outputs.model_dump(mode="json"),
        }))

    def on_chain_error(self, error, *, run_id, **kwargs) -> None:
        with self._lock:
            self._chains.pop(run_id, None)

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs) -> None:
        prompts = _format_prompts(messages)
        with self._lock:
            self._models[run_id] = (metadata or {}, time.monotonic(), prompts)

    def on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs) -> None:
        formatted = _format_prompts(prompts)
        with self._lock:
            self._models[run_id] = (metadata or {}, time.monotonic(), formatted)

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        usage = {}
        reasoning = ""
        with contextlib.suppress(IndexError, AttributeError, TypeError):
            msg = response.generations[0][0].message
            usage = getattr(msg, "usage_metadata", None) or {}
            reasoning = _reasoning(msg).strip()
        self._model_done(
            run_id,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            None,
            reasoning=reasoning or None,
        )

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        self._model_done(run_id, 0, 0, f"{type(error).__name__}: {error}")

    def _model_done(
        self,
        run_id,
        tokens_in: int,
        tokens_out: int,
        error: str | None,
        *,
        reasoning: str | None = None,
    ) -> None:
        with self._lock:
            metadata, started, prompts = self._models.pop(run_id, ({}, None, []))
        node, step = _locate(metadata)
        payload: dict[str, Any] = {
            "node": node, "step": step, "model": metadata.get("ls_model_name"),
            "tokens_in": tokens_in or 0, "tokens_out": tokens_out or 0,
            "duration_s": round(time.monotonic() - started, 3) if started is not None else None,
            "error": error,
        }
        if prompts:
            payload["prompts"] = prompts
        if reasoning:
            payload["reasoning"] = reasoning
        self._pending.put(("llm_call", payload))

    def drain(self) -> Iterator[tuple[str, dict]]:
        while True:
            try:
                yield self._pending.get_nowait()
            except queue.Empty:
                return


class RunEvents:
    """One analysis run, iterated as events; see the module docstring for the events.

    Iterate it once. It ends with ``run_finished``, ``run_cancelled`` or
    ``run_failed``; after a failure the exception is raised to the caller, and
    a checkpointed run keeps its checkpoint for a later run to resume. After
    ``run_finished``, ``final_state`` and ``rating`` hold the result and the
    decision is in the memory log, as after ``propagate()``.

    ``sink`` is called with every event as it is emitted, such as a
    ``JsonlEventSink``; the caller closes it. ``tokens=True`` adds the models'
    text as it is generated: models are then called in streaming mode, which
    some providers handle differently, so it is off by default. A structured
    agent may stream no text at all, its answer arriving as tool-call
    arguments; its ``structured_output`` and ``report`` come when it is done.

    ``cancel()`` may be called from any thread. The run stops when its current
    step returns (a model call in progress finishes first), records no
    decision, and keeps its checkpoint.
    """

    def __init__(
        self,
        graph,
        ticker: str,
        trade_date: str,
        asset_type: str = "stock",
        portfolio=None,
        *,
        sink: Callable[[dict], None] | None = None,
        tokens: bool = False,
        max_tool_chars: int = 20_000,
        run_id: str | None = None,
    ):
        self.graph = graph
        self.ticker = ticker
        self.trade_date = trade_date
        self.asset_type = asset_type
        self.portfolio = portfolio
        self.sink = sink
        self.tokens = tokens
        self.max_tool_chars = max_tool_chars
        self.run_id = run_id or uuid.uuid4().hex
        self.final_state: dict | None = None
        self.rating: str | None = None
        self._cancelled = threading.Event()
        self._iterated = False
        self._seq = 0
        self._task_started: dict[str, float] = {}
        self._stats = {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}
        self._sink_failed = False

    def cancel(self) -> None:
        self._cancelled.set()

    def __iter__(self) -> Iterator[dict]:
        if self._iterated:
            raise RuntimeError("a RunEvents runs once; make a new one for another run")
        self._iterated = True
        return self._run()

    # -- the run ----------------------------------------------------------

    def _run(self) -> Iterator[dict]:
        graph = self.graph
        # Every call into the graph reads the graph's config, and the caller
        # keeps its own between events (see TradingAgentsGraph.stream_run).
        context = run_config_context(graph.config)
        started = time.monotonic()
        callbacks = _RunCallbacks()
        scope = (self.ticker, self.trade_date, self.asset_type, self.portfolio)
        try:
            init_state = context.run(graph.create_run_state, *scope)
            args = graph.propagator.get_graph_args(callbacks=[callbacks])
            args["stream_mode"] = ["tasks", "values", *(["messages"] if self.tokens else [])]
            thread_id = context.run(graph.begin_checkpoint, *scope)
            if thread_id is not None:
                args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = thread_id
            graph_input = graph.checkpoint_input(init_state)

            yield self._event(
                "run_started",
                ticker=self.ticker,
                trade_date=init_state["trade_date"],
                asset_type=self.asset_type,
                analysts=list(graph.selected_analysts),
                resumed=graph_input is None,
                instrument_context=init_state.get("instrument_context", ""),
                settings=_jsonable(graph.run_settings()),
                graph=self._topology(),
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )

            final_state: dict = {}
            stream = context.run(graph.graph.stream, graph_input, subgraphs=True, **args)
            try:
                while (step := context.run(next, stream, None)) is not None:
                    # A step's callbacks ran before the step itself was reported.
                    yield from self._drain(callbacks)
                    namespace, mode, chunk = step
                    if mode == "values":
                        if not namespace:
                            final_state = chunk
                    elif mode == "tasks":
                        yield from self._task(namespace, chunk)
                    elif mode == "messages":
                        yield from self._token(chunk)
                    if self._cancelled.is_set():
                        break
                yield from self._drain(callbacks)
            finally:
                context.run(stream.close)

            if self._cancelled.is_set():
                yield self._event("run_cancelled", duration_s=self._since(started), stats=dict(self._stats))
                return

            context.run(graph.record_decision, self.ticker, init_state["trade_date"], final_state)
            context.run(graph.clear_checkpoint_on_success, *scope)
            self.final_state = final_state
            self.rating = run_rating(final_state)
            yield self._event("run_finished", rating=self.rating, duration_s=self._since(started),
                              stats=dict(self._stats))
        except Exception as exc:
            yield self._event("run_failed", error_type=type(exc).__name__, message=str(exc),
                              duration_s=self._since(started), stats=dict(self._stats))
            raise
        finally:
            context.run(graph.end_checkpoint)

    def _task(self, namespace: tuple, chunk: dict) -> Iterator[dict]:
        name, task_id = chunk.get("name"), chunk.get("id")
        if namespace:      # a step inside an analyst's own graph
            node, step = namespace[0].partition(":")[0], name
        else:
            node, step = name, None

        if "result" not in chunk and "error" not in chunk:
            self._task_started[task_id] = time.monotonic()
            yield self._event("node_started", node=node, step=step, task_id=task_id)
            return

        result = chunk.get("result") or {}
        if not isinstance(result, dict):     # older LangGraph: a list of (channel, value) writes
            result = dict(result)
        if step is None:
            yield from self._node_content(node, result)
        else:
            yield from self._step_messages(node, step, result.get("messages") or [])
        error = chunk.get("error")
        started = self._task_started.pop(task_id, None)
        yield self._event(
            "node_finished", node=node, step=step, task_id=task_id,
            duration_s=self._since(started) if started is not None else None,
            error=f"{type(error).__name__}: {error}" if error else None,
            update=_jsonable({k: v for k, v in result.items() if k not in _CONTENT_KEYS}),
        )

    def _node_content(self, node: str, result: dict) -> Iterator[dict]:
        """A top-level node's reports and debate turn."""
        for section in REPORT_SECTIONS:
            text = result.get(section)
            if isinstance(text, str) and text.strip():
                yield self._event("report", node=node, section=section, content=text)
        if node in _DEBATES:
            debate, key, field = _DEBATES[node]
            state = result.get(key) or {}
            if state.get(field):
                yield self._event("debate_turn", node=node, debate=debate, turn=state.get("count"),
                                  content=state[field])

    def _step_messages(self, node: str, step: str, messages: list) -> Iterator[dict]:
        """An analyst step's tool calls and their results.

        An analyst's last answer is its report, which comes with the analyst's
        own node; text alongside tool calls is the model thinking aloud.
        """
        for message in messages:
            if isinstance(message, ToolMessage):
                content = _text(message.content)
                status = getattr(message, "status", "success") or "success"
                yield self._event(
                    "tool_result", node=node, step=step, call_id=message.tool_call_id,
                    name=message.name, status=status,
                    usable=status != "error" and usable_result(content),
                    chars=len(content), content=content[: self.max_tool_chars],
                    truncated=len(content) > self.max_tool_chars,
                )
            elif isinstance(message, AIMessage) and message.tool_calls:
                text = _text(message.content).strip()
                reasoning = _reasoning(message).strip()
                if text or reasoning:
                    msg_fields: dict[str, Any] = {"node": node, "step": step, "content": text}
                    if reasoning:
                        msg_fields["reasoning"] = reasoning
                    yield self._event("message", **msg_fields)
                for call in message.tool_calls:
                    self._stats["tool_calls"] += 1
                    yield self._event("tool_call", node=node, step=step, call_id=call.get("id"),
                                      name=call.get("name"), args=_jsonable(call.get("args") or {}))

    def _token(self, chunk) -> Iterator[dict]:
        message, metadata = chunk
        # Messages mode also repeats whole messages from node outputs; only
        # chunks are text as it is generated.
        if not isinstance(message, AIMessageChunk):
            return
        text = _text(message.content)
        thinking = _reasoning(message)
        if text or thinking:
            node, step = _locate(metadata)
            fields: dict[str, Any] = {"node": node, "step": step, "text": text}
            if thinking:
                fields["thinking"] = thinking
            yield self._event("token", **fields)

    def _drain(self, callbacks: _RunCallbacks) -> Iterator[dict]:
        for kind, payload in callbacks.drain():
            if kind == "llm_call":
                self._stats["llm_calls"] += 1
                self._stats["tokens_in"] += payload["tokens_in"]
                self._stats["tokens_out"] += payload["tokens_out"]
            yield self._event(kind, **payload)

    def _topology(self) -> dict:
        drawn = self.graph.graph.get_graph()
        return {
            "phases": list(PHASES),
            "nodes": [{"id": node, "phase": _NODE_PHASES.get(node, "other")}
                      for node in drawn.nodes if node not in _GRAPH_ENDS],
            "edges": [{"source": e.source, "target": e.target, "conditional": bool(e.conditional)}
                      for e in drawn.edges if e.source not in _GRAPH_ENDS and e.target not in _GRAPH_ENDS],
        }

    def _event(self, type_: str, **fields) -> dict:
        self._seq += 1
        event = {"v": SCHEMA_VERSION, "seq": self._seq, "ts": round(time.time(), 3),
                 "run_id": self.run_id, "type": type_, **fields}
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception as exc:     # losing the replay must not end a paid run
                if not self._sink_failed:
                    logger.warning("Event sink failed (%s); the run goes on without it", exc)
                    self._sink_failed = True
        return event

    @staticmethod
    def _since(started: float) -> float:
        return round(time.monotonic() - started, 3)


class JsonlEventSink:
    """Appends each event to a JSON Lines file, one flushed line per event.

    A page can tail the file while the run goes on, or replay it later. A
    resumed run appends under a new ``run_id``. Token events are left out
    unless ``include_tokens``: there are many and the reports hold their text.
    """

    def __init__(self, path, include_tokens: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.include_tokens = include_tokens
        self._file = open(self.path, "a", encoding="utf-8")   # noqa: SIM115 - closed by close()

    def __call__(self, event: dict) -> None:
        if event["type"] == "token" and not self.include_tokens:
            return
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> JsonlEventSink:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_events(path, run_id: str | None = None) -> list[dict]:
    """The events saved in ``path``, optionally only one run's.

    A line that does not parse, such as the last line of a run that crashed
    mid-write, is skipped.
    """
    events = []
    with open(path, encoding="utf-8") as file:
        for number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping unreadable event on line %d of %s", number, path)
                continue
            if run_id is None or event.get("run_id") == run_id:
                events.append(event)
    return events
