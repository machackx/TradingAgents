"""Every price consumer uses one dataset, including concurrent and resumed runs."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.agents.tools import get_indicators, get_stock_data, get_verified_market_snapshot
from tradingagents.dataflows.pinned_market import pinned_market_data
from tradingagents.dataflows.vendors.yahoo import ohlcv
from tradingagents.dataflows.vendors.yahoo.snapshot import prepare_market_snapshot

pytestmark = pytest.mark.unit


def _prices(close=123.45):
    return pd.DataFrame({
        "Date": pd.bdate_range(end="2026-11-04", periods=240),
        "Open": close - 1, "High": close + 1, "Low": close - 2,
        "Close": close, "Volume": 100000,
    })


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(ohlcv, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    calls = []

    def history(**kwargs):
        calls.append(1)
        return _prices().set_index("Date")

    monkeypatch.setattr(ohlcv.yf, "Ticker", lambda *a: SimpleNamespace(history=history))
    snapshot = prepare_market_snapshot("AAPL", "2026-11-04")

    def changed_vendor(**kwargs):
        pytest.fail("a pinned dataset must not re-read the vendor")

    monkeypatch.setattr(ohlcv.yf, "Ticker", lambda *a: SimpleNamespace(history=changed_vendor))
    return snapshot, calls


def test_prices_indicators_and_snapshot_share_one_frame(frozen):
    snapshot, calls = frozen
    prices = get_stock_data.func("AAPL", "2026-10-30", "2026-11-04", "2026-11-04", snapshot)
    indicators = get_indicators.func("AAPL", "close_10_ema", "2026-11-04", 2, "2026-11-04", snapshot)
    verified = get_verified_market_snapshot.func("AAPL", "2026-11-04", 2, "2026-11-04", snapshot)
    for text in (prices, indicators, verified):
        assert snapshot["snapshot_id"] in text
        assert "123.45" in text or "123.450" in text
    assert len(calls) == 1
    assert "| Close | 123.45 |" in verified


def test_snapshot_survives_json_checkpoint_roundtrip(frozen):
    snapshot, _ = frozen
    restored = json.loads(json.dumps(snapshot, allow_nan=False))
    text = get_verified_market_snapshot.func("AAPL", "2026-11-04", 2, "2026-11-04", restored)
    assert restored["snapshot_id"] in text and "| Close | 123.45 |" in text


def test_concurrent_frames_are_isolated(frozen):
    first, _ = frozen
    other = {**first, "snapshot_id": "other", "records": _prices(99).assign(Date=lambda df: df.Date.astype(str)).to_dict("records")}

    def read(snapshot):
        with pinned_market_data(snapshot):
            return float(ohlcv.load_ohlcv("AAPL", "2026-11-04", fill_gaps=False)["Close"].iloc[-1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(read, [first, other])) == [123.45, 99]


def test_current_day_is_provisional_and_future_rows_are_excluded(monkeypatch):
    from datetime import UTC, datetime

    from tradingagents.dataflows.vendors.yahoo import snapshot as module

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    data = _prices()
    data["Date"] = pd.date_range(end=today, periods=len(data))
    future = data.tail(1).assign(Date=pd.Timestamp(today) + pd.Timedelta(days=1), Close=999)
    monkeypatch.setattr(module, "load_ohlcv", lambda *a, **k: pd.concat([data, future]))
    snapshot = module.prepare_market_snapshot("BTC-USD", today)
    assert "provisional" in snapshot["bar_status"]
    assert snapshot["latest_bar_date"] == today
    assert 999 not in [row["Close"] for row in snapshot["records"]]
