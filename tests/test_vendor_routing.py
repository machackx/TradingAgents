"""Vendor router must respect the configured chain and never silently hide a
broken primary.

Regressions for #988 (explicit single-vendor config still fell back to others),
#289 (fallback ran for unchosen vendors), and #989 (serious primary failures
were swallowed without a trace).
"""
import copy
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import router
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError


def _reset_config():
    # Hard reset: set_config() merges, so empty DEFAULT dicts (e.g. tool_vendors)
    # don't clear keys leaked by other tests. Replace the global outright.
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


def _no_data(symbol, *a, **k):
    raise NoMarketDataError(symbol, symbol, "no rows")


def _returns(value):
    def impl(symbol, *a, **k):
        return value
    return impl


def _raises(exc):
    def impl(symbol, *a, **k):
        raise exc
    return impl


@pytest.mark.unit
class VendorRoutingTests(unittest.TestCase):
    def setUp(self):
        _reset_config()

    def tearDown(self):
        _reset_config()

    def _route(self, vendors_for_get_stock_data):
        return mock.patch.dict(
            router.VENDOR_METHODS,
            {"get_stock_data": vendors_for_get_stock_data},
            clear=False,
        )

    def test_explicit_single_vendor_does_not_fall_back(self):
        # #988: with yfinance pinned, a healthy alpha_vantage must NOT be used.
        set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
        av = mock.Mock(side_effect=_returns("AV_DATA"))
        with self._route({"yfinance": _no_data, "alpha_vantage": av}):
            result = router.route_to_vendor("get_stock_data", "FAKE", "2026-01-01", "2026-01-10")
        self.assertIn("NO_DATA_AVAILABLE", result)
        av.assert_not_called()  # the unchosen vendor was never tried

    def test_explicit_multi_vendor_falls_back_within_chain(self):
        # Listing both vendors opts in to ordered fallback.
        set_config({"data_vendors": {"core_stock_apis": "yfinance,alpha_vantage"}})
        with self._route({"yfinance": _no_data, "alpha_vantage": _returns("AV_DATA")}):
            result = router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertEqual(result, "AV_DATA")

    def test_primary_error_is_logged_not_masked(self):
        # #989: a broken primary is not hidden behind a fallback's verdict. It
        # never said whether it has the symbol, so the answer is unavailable,
        # and the failure is in the logs.
        set_config({"data_vendors": {"core_stock_apis": "yfinance,alpha_vantage"}})
        with self._route({"yfinance": _raises(ValueError("boom")), "alpha_vantage": _no_data}), \
                self.assertLogs("tradingagents.dataflows.router", level="WARNING") as cm:
            result = router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertTrue(result.startswith("DATA_UNAVAILABLE"), result)
        joined = "\n".join(cm.output)
        self.assertIn("boom", joined)            # the real error surfaced in logs
        self.assertIn("yfinance", joined)

    def test_unknown_configured_vendor_raises(self):
        set_config({"data_vendors": {"core_stock_apis": "bogus_vendor"}})
        with self.assertRaises(ValueError) as ctx:
            router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertIn("bogus_vendor", str(ctx.exception))

    def test_default_sentinel_uses_all_vendors(self):
        # No explicit choice ("default") keeps the resilient full-chain behavior.
        set_config({"data_vendors": {"core_stock_apis": "default"}})
        with self._route({"yfinance": _no_data, "alpha_vantage": _returns("AV_DATA")}):
            result = router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")
        self.assertEqual(result, "AV_DATA")

    def _route_method(self, method, vendors):
        return mock.patch.dict(router.VENDOR_METHODS, {method: vendors}, clear=False)

    def test_optional_category_degrades_instead_of_raising(self):
        # An optional enrichment vendor (FRED macro) that raises must NOT abort
        # the run — the router returns a sentinel so the analysis proceeds.
        set_config({"data_vendors": {"macro_data": "fred"}})
        with self._route_method(
            "get_macro_indicators", {"fred": _raises(ValueError("FRED 400: bad series"))}
        ):
            result = router.route_to_vendor("get_macro_indicators", "cpi", "2026-01-01")
        self.assertIn("DATA_UNAVAILABLE", result)
        self.assertIn("macro_data", result)

    def test_core_category_still_raises_on_error(self):
        # A core category (single configured vendor) propagates the error so a
        # broken primary is loud, not silently degraded.
        set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
        with self._route({"yfinance": _raises(ValueError("boom"))}), \
                self.assertRaises(ValueError):
            router.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")


if __name__ == "__main__":
    unittest.main()


@pytest.mark.unit
@pytest.mark.parametrize("method", ["get_balance_sheet", "get_income_statement", "get_cashflow"])
def test_stale_sec_quarters_gain_a_separate_configured_live_supplement(method, monkeypatch):
    _reset_config()
    set_config({"data_vendors": {"fundamental_data": "sec_edgar,yfinance"}})
    monkeypatch.setattr(router, "get_current_date", lambda: "2026-11-05")
    sec = "# Latest tagged period: 2022-09-30\nSEC historical rows"
    yahoo = "# Latest tagged period: 2026-06-30\nYahoo recent rows"
    live = mock.Mock(return_value=yahoo)
    monkeypatch.setitem(router.VENDOR_METHODS, method, {"sec_edgar": mock.Mock(return_value=sec), "yfinance": live})
    result = router.route_to_vendor(method, "FUTU", "quarterly", "2026-11-05")
    assert result.startswith(sec) and yahoo in result
    assert "share basis separate" in result and "filing vintage" in result
    live.assert_called_once_with("FUTU", "quarterly", "2026-11-05")


@pytest.mark.unit
@pytest.mark.parametrize("freq,as_of,latest,vendors", [
    ("quarterly", "2026-11-04", "2022-09-30", "sec_edgar,yfinance"),
    ("quarterly", "2026-11-05", "2022-09-30", "sec_edgar"),
    ("quarterly", "2026-11-05", "2026-06-30", "sec_edgar,yfinance"),
    ("annual", "2026-11-05", "2022-12-31", "sec_edgar,yfinance"),
])
def test_sec_supplement_respects_vintage_vendor_selection_and_frequency(freq, as_of, latest, vendors, monkeypatch):
    _reset_config()
    set_config({"data_vendors": {"fundamental_data": vendors}})
    monkeypatch.setattr(router, "get_current_date", lambda: "2026-11-05")
    sec = f"# Latest tagged period: {latest}\nSEC rows"
    live = mock.Mock(side_effect=AssertionError("live data must not be requested"))
    monkeypatch.setitem(router.VENDOR_METHODS, "get_income_statement", {"sec_edgar": mock.Mock(return_value=sec), "yfinance": live})
    assert router.route_to_vendor("get_income_statement", "FUTU", freq, as_of) == sec
    live.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("supplement", [
    "DATA_UNAVAILABLE: no connection",
    "# Latest tagged period: 2021-09-30\nOlder rows",
    "# Latest tagged period: 2026-12-31\nFuture rows",
    RuntimeError("Yahoo unreachable"),
])
def test_failed_or_older_supplements_keep_usable_sec_history(supplement, monkeypatch):
    from tradingagents.agents.evidence import usable_result

    _reset_config()
    set_config({"data_vendors": {"fundamental_data": "sec_edgar,yfinance"}})
    monkeypatch.setattr(router, "get_current_date", lambda: "2026-11-05")
    sec = "# Latest tagged period: 2022-09-30\nSEC historical rows"
    live = mock.Mock(side_effect=supplement) if isinstance(supplement, Exception) else mock.Mock(return_value=supplement)
    monkeypatch.setitem(router.VENDOR_METHODS, "get_cashflow", {"sec_edgar": mock.Mock(return_value=sec), "yfinance": live})
    result = router.route_to_vendor("get_cashflow", ticker="FUTU", freq="quarterly", as_of_date="2026-11-05")
    assert result.startswith(sec) and "SEC history retained" in result and usable_result(result)
    assert "Future rows" not in result and "Older rows" not in result
