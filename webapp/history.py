"""Past runs, read back from their events files, and the reports saved beside them.

A run's events are at ``<results_dir>/<ticker>/<date>/events.jsonl`` (one file
holds every run of that ticker and date, a resumed run under a new run id) and
a run started from the page saves its report to ``web_reports/<run_id>`` in the
same directory. Reports the CLI saved stay under ``<results_dir>/reports``.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.graph.events import read_events

logger = logging.getLogger(__name__)

_ENDINGS = {"run_finished": "finished", "run_failed": "failed", "run_cancelled": "cancelled"}
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_REPORT_NAME = re.compile(r"^[A-Za-z0-9._\-\^=+]+$")


def report_dir(results_dir, ticker: str, trade_date: str, run_id: str) -> Path:
    """Where a run started from the page saves its report."""
    return Path(results_dir) / safe_ticker_component(ticker) / trade_date / "web_reports" / run_id


def valid_run_id(run_id: str) -> bool:
    return bool(_RUN_ID.match(run_id))


@dataclass
class RunSummary:
    """One run as a list of runs shows it."""

    run_id: str
    ticker: str
    trade_date: str
    status: str                  # running, finished, failed, cancelled, interrupted
    asset_type: str | None = None
    analysts: list[str] = field(default_factory=list)
    resumed: bool = False
    started_at: str | None = None
    rating: str | None = None
    duration_s: float | None = None
    stats: dict | None = None
    error: str | None = None
    has_report: bool = False
    events: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def summarize(events: list[dict], ticker: str, trade_date: str) -> RunSummary:
    """A run's summary from its events, oldest first; a run with no ending was interrupted."""
    first = events[0]
    summary = RunSummary(run_id=first["run_id"], ticker=ticker, trade_date=trade_date,
                         status="interrupted", events=len(events))
    for event in events:
        kind = event.get("type")
        if kind == "run_started":
            summary.ticker = event.get("ticker", ticker)
            summary.trade_date = event.get("trade_date", trade_date)
            summary.asset_type = event.get("asset_type")
            summary.analysts = list(event.get("analysts") or [])
            summary.resumed = bool(event.get("resumed"))
            summary.started_at = event.get("started_at")
        elif kind in _ENDINGS:
            summary.status = _ENDINGS[kind]
            summary.rating = event.get("rating")
            summary.duration_s = event.get("duration_s")
            summary.stats = event.get("stats")
            if kind == "run_failed":
                summary.error = f"{event.get('error_type')}: {event.get('message')}"
    return summary


class RunHistory:
    """The runs saved under a results directory, re-read only when a file changes."""

    def __init__(self, results_dir):
        self.results_dir = Path(results_dir)
        self._lock = threading.Lock()
        self._files: dict[Path, tuple[tuple[int, int], list[RunSummary]]] = {}
        self._where: dict[str, Path] = {}

    def runs(self) -> list[RunSummary]:
        """Every saved run, newest first."""
        found = []
        seen = set()
        for path in self.results_dir.glob("*/*/events.jsonl"):
            seen.add(path)
            found.extend(self._summaries(path))
        with self._lock:
            for gone in set(self._files) - seen:
                del self._files[gone]
        return sorted(found, key=lambda s: s.started_at or "", reverse=True)

    def find(self, run_id: str) -> RunSummary | None:
        if not valid_run_id(run_id):
            return None
        path = self._where.get(run_id)
        if path is None or not path.exists():
            self.runs()
            path = self._where.get(run_id)
        if path is None:
            return None
        return next((s for s in self._summaries(path) if s.run_id == run_id), None)

    def events(self, run_id: str) -> list[dict] | None:
        if self.find(run_id) is None:
            return None
        return read_events(self._where[run_id], run_id=run_id)

    def _summaries(self, path: Path) -> list[RunSummary]:
        try:
            stat = path.stat()
        except OSError:
            return []
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._files.get(path)
            if cached is not None and cached[0] == key:
                return cached[1]
        by_run: dict[str, list[dict]] = {}
        for event in read_events(path):
            if isinstance(event, dict) and isinstance(event.get("run_id"), str):
                by_run.setdefault(event["run_id"], []).append(event)
        ticker, trade_date = path.parent.parent.name, path.parent.name
        summaries = []
        for run_id, events in by_run.items():
            summary = summarize(events, ticker, trade_date)
            summary.has_report = (path.parent / "web_reports" / run_id / "complete_report.html").is_file()
            summaries.append(summary)
        with self._lock:
            self._files[path] = (key, summaries)
            for summary in summaries:
                self._where[summary.run_id] = path
        return summaries


def saved_reports(results_dir) -> list[dict]:
    """The reports the CLI saved under ``<results_dir>/reports``, newest first."""
    root = Path(results_dir) / "reports"
    if not root.is_dir():
        return []
    found = []
    for directory in root.iterdir():
        page = directory / "complete_report.html"
        markdown = directory / "complete_report.md"
        if not directory.is_dir() or not _REPORT_NAME.match(directory.name):
            continue
        if not (page.is_file() or markdown.is_file()):
            continue
        ticker = re.sub(r"_\d{8}_\d{6}$", "", directory.name)
        found.append({"name": directory.name, "ticker": ticker, "html": page.is_file(),
                      "saved_at": (page if page.is_file() else markdown).stat().st_mtime})
    return sorted(found, key=lambda r: r["saved_at"], reverse=True)


def saved_report_page(results_dir, name: str) -> Path | None:
    """The HTML page of a CLI-saved report, only if ``name`` is one of those listed."""
    if not _REPORT_NAME.match(name) or name in {".", ".."}:
        return None
    root = (Path(results_dir) / "reports").resolve()
    page = (root / name / "complete_report.html").resolve()
    return page if page.is_file() and page.parent.parent == root else None
