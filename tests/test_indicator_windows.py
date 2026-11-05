"""Named SMA periods require complete history in every price consumer."""

import pandas as pd
import pytest

from tradingagents.dataflows.vendors.yahoo import market, snapshot

pytestmark = pytest.mark.unit


def prices(n):
    close = pd.Series(range(1, n + 1), dtype=float)
    return pd.DataFrame({
        "Date": pd.bdate_range("2025-01-01", periods=n), "Close": close,
        "Open": close, "High": close + 1, "Low": close - 1, "Volume": 1000,
    })


@pytest.mark.parametrize("n,window", [(49, 50), (50, 50), (83, 200), (199, 200), (200, 200), (201, 200)])
def test_bulk_single_and_verified_snapshot_agree(monkeypatch, n, window):
    data = prices(n)
    date = str(data.Date.iloc[-1].date())
    for module in (market, snapshot):
        monkeypatch.setattr(module, "load_ohlcv", lambda *a, **k: data.copy())
    indicator = f"close_{window}_sma"
    bulk = market._get_stock_stats_bulk("NEW", indicator, date)
    single = market.get_stock_stats("NEW", indicator, date)
    verified = snapshot.build_verified_market_snapshot("NEW", date, indicators=[indicator])
    if n < window:
        assert str(single).startswith("N/A")
        assert bulk[date].startswith("N/A")
        assert f"| {indicator} | N/A: requires a full {window}-session close window |" in verified
    else:
        expected = data.Close.tail(window).mean()
        assert single == pytest.approx(expected)
        assert float(bulk[date]) == pytest.approx(expected)
        assert f"| {indicator} | {expected:.2f} |" in verified
        # Availability is per date, not the total size of the downloaded frame.
        assert bulk[str(data.Date.iloc[window - 2].date())].startswith("N/A")
        assert float(bulk[str(data.Date.iloc[window - 1].date())]) == pytest.approx((window + 1) / 2)


def test_nonfinite_close_invalidates_only_windows_that_contain_it(monkeypatch):
    data = prices(251)
    data.loc[0, "Close"] = float("inf")
    data.loc[1, "Close"] = float("nan")
    monkeypatch.setattr(market, "load_ohlcv", lambda *a, **k: data.copy())
    values = market._get_stock_stats_bulk("NEW", "close_200_sma", str(data.Date.iloc[-1].date()))
    assert values[str(data.Date.iloc[200].date())].startswith("N/A")
    assert float(values[str(data.Date.iloc[201].date())]) == pytest.approx(data.Close.iloc[2:202].mean())


def test_existing_full_history_non_sma_indicator_is_preserved(monkeypatch):
    data = prices(240)
    monkeypatch.setattr(market, "load_ohlcv", lambda *a, **k: data.copy())
    assert market.get_stock_stats("OLD", "rsi", str(data.Date.iloc[-1].date())) == pytest.approx(100)
