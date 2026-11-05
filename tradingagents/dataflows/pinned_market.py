"""A serializable market dataset, carried in graph state and injected into tools.

Context is bound only for one tool call. No process-wide frame cache: independent
runs and resumed checkpoints each retain their own dataset.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_snapshot: ContextVar[dict | None] = ContextVar("pinned_market_snapshot", default=None)


@contextmanager
def pinned_market_data(snapshot: dict | None):
    token = _snapshot.set(snapshot)
    try:
        yield
    finally:
        _snapshot.reset(token)


def current_snapshot() -> dict | None:
    return _snapshot.get()


def snapshot_note(snapshot: dict | None) -> str:
    if not snapshot or snapshot.get("status") != "available":
        return ""
    return (
        f"# Run snapshot ID: {snapshot['snapshot_id']}\n"
        f"# Dataset captured at (UTC): {snapshot['captured_at_utc']}\n"
        f"# Source download at (UTC): {snapshot.get('downloaded_at_utc') or 'not supplied'}\n"
        f"# Price basis: Yahoo Finance, auto_adjust=True\n"
        f"# Latest bar status: {snapshot['bar_status']}\n"
        "# A current-session bar is provisional; do not describe it as a confirmed closing price.\n\n"
    )
