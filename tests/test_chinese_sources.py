"""Chinese source integration: identity, publication dates, failures and provenance."""

import json
from datetime import UTC, datetime

import pytest
import requests

from tradingagents.dataflows import chinese_news as news
from tradingagents.dataflows.chinese_sources import (
    NewsBatch,
    NewsItem,
    clean_text,
    published_at,
    render_batches,
    search_keyword,
    uses_chinese_sources,
)
from tradingagents.dataflows.config import get_config, run_config, set_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.vendors import eastmoney, futu

pytestmark = pytest.mark.unit


def _response(payload=None, *, text=None):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    response = Response()
    response.text = text if text is not None else json.dumps(payload)
    return response


def _item(title, url="https://example.com/news", date="2026-11-04T10:00:00+08:00", **kwargs):
    return NewsItem(title, published_at(date), url, "证券时报", **kwargs)


@pytest.mark.parametrize("ticker,keyword", [
    ("0700.HK", "HK.00700"), ("700.HK", "HK.00700"), ("HK.00700", "HK.00700"),
    ("600519.SS", "SH.600519"), ("600519.SH", "SH.600519"),
    ("000001.SZ", "SZ.000001"), ("BABA", "阿里巴巴"), ("US.PDD", "拼多多"),
])
def test_futu_company_identity(ticker, keyword):
    assert search_keyword(ticker, "futu") == keyword
    assert uses_chinese_sources(ticker)


def test_opt_out_and_custom_adr_identity():
    assert not uses_chinese_sources("NVDA")
    assert not uses_chinese_sources("BTC-USD")
    set_config({"chinese_news_keywords": {"NEWADR": "示例公司"}})
    assert uses_chinese_sources("NEWADR")
    assert search_keyword("NEWADR", "futu") == "示例公司"
    set_config({"chinese_sources_enabled": False})
    assert not uses_chinese_sources("0700.HK")


def test_chinese_timestamps_and_html():
    expected = datetime(2026, 11, 4, 2, tzinfo=UTC)
    assert published_at("2026-11-04 10:00:00").astimezone(UTC) == expected
    assert published_at(str(int(expected.timestamp()))) == expected
    assert published_at(str(int(expected.timestamp() * 1000))) == expected
    assert published_at("not a date") is None
    assert published_at(10 ** 50) is None
    assert clean_text("<p>贵州<em>茅台</em>&amp;公告</p><p>详情</p>") == "贵州茅台&公告 详情"


def test_filter_before_limit_and_use_chinese_calendar_day():
    batch = NewsBatch("富途", [
        _item("future", date="2026-11-05T00:00:00+08:00"),
        _item("undated", date=None),
        _item("old", date="2026-11-03T23:59:59+08:00"),
        _item("included", date="2026-11-04T00:00:00+08:00"),
    ])
    out = render_batches([batch], "2026-11-04", "2026-11-04", 1)
    assert "included" in out
    for title in ("future", "undated", "old"):
        assert f"] {title}" not in out
    assert "2026-11-04 00:00:00 +0800" in out


def test_reposts_deduplicate_and_sources_share_budget():
    batches = [NewsBatch("富途", [
        _item("共同标题", "https://example.com/one?src=39"),
        _item("另一条", "https://example.com/two"),
    ]), NewsBatch("东方财富", [
        _item("共同标题", "https://other.example/copy"),
        _item("独立报道", "https://example.com/three"),
    ])]
    out = render_batches(batches, "2026-11-04", "2026-11-04", 2)
    assert out.count("] 共同标题") == 1
    assert "] 独立报道" in out
    assert "] 另一条" not in out
    assert "证券时报" in out


def test_futu_contract_and_fuzzy_company_filter(monkeypatch):
    captured = {}

    def get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _response({"code": 0, "data": [
            {"title": "<em>腾讯</em>(00700.HK)回购", "publish_time": "1793757600",
             "url": "https://news.futunn.com/post/1"},
            {"title": "香港其他公司的新闻", "publish_time": "1793757600",
             "url": "https://news.futunn.com/post/2"},
        ]})

    monkeypatch.setattr(futu.requests, "get", get)
    batch = futu.fetch_news("HK.00700", 99)
    assert len(batch.items) == 1
    assert batch.items[0].title == "腾讯(00700.HK)回购"
    assert "未提供原始发布方" in batch.items[0].publisher
    assert captured["params"] == {
        "keyword": "HK.00700", "size": 50, "news_type": 1, "lang": "zh-CN", "sort_type": 2,
    }


@pytest.mark.parametrize("payload", [
    {"code": 1}, [], {"code": 0, "data": "broken"}, {"code": 0, "data": [None]},
])
def test_futu_errors_are_outages(monkeypatch, payload):
    monkeypatch.setattr(futu.requests, "get", lambda *a, **k: _response(payload))
    with pytest.raises(VendorUnavailableError):
        futu.fetch_news("腾讯")


def test_futu_success_without_data_is_an_unavailable_sample(monkeypatch):
    monkeypatch.setattr(futu.requests, "get", lambda *a, **k: _response({"code": 0}))
    out = futu.get_news_futu("0700.HK", "2026-11-01", "2026-11-04")
    assert "unavailable" in out
    assert "does not establish an absence" in out


def test_eastmoney_jsonp_retains_original_media(monkeypatch):
    payload = {"code": 0, "result": {"cmsArticleWebOld": [{
        "title": "<em>贵州茅台</em>公告", "content": "<p>公司业绩说明</p>",
        "mediaName": "财联社", "date": "2026-11-04 10:00:00",
        "url": "https://finance.eastmoney.com/a/123.html",
    }]}}
    monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **k: _response(text=f"tradingagents({json.dumps(payload)});"))
    batch = eastmoney.fetch_news("贵州茅台")
    assert batch.items[0].publisher == "财联社"
    assert batch.items[0].summary == "公司业绩说明"
    assert batch.items[0].published.hour == 10


@pytest.mark.parametrize("body", ["", "<html>access denied</html>", 'tradingagents({"code":1})',
                                        'tradingagents({"code":0,"result":{}})'])
def test_eastmoney_block_or_schema_drift_is_not_silence(monkeypatch, body):
    monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **k: _response(text=body))
    with pytest.raises(VendorUnavailableError):
        eastmoney.fetch_news("贵州茅台")


def test_all_supplements_read_after_successful_primary_and_outage_is_visible(monkeypatch):
    calls = []

    def fetch(keyword, limit, news_type=1, strict=True):
        calls.append((keyword, news_type))
        return NewsBatch(f"futu {news_type}", [_item(f"report {news_type}", f"https://example.com/{news_type}")])

    def unavailable(*a):
        raise VendorUnavailableError("rate limited")

    monkeypatch.setattr(futu, "fetch_news", fetch)
    monkeypatch.setattr(eastmoney, "fetch_news", unavailable)
    out = news.supplement_news("Yahoo primary", "0700.HK", "2026-11-01", "2026-11-04")
    assert out.startswith("Yahoo primary")
    assert set(calls) == {("HK.00700", 1), ("腾讯", 2)}
    assert "] report 1" in out and "] report 2" in out
    assert "rate limited" in out


def test_disabled_supplements_make_no_requests(monkeypatch):
    monkeypatch.setattr(futu, "fetch_news", lambda *a: pytest.fail("unexpected request"))
    set_config({"chinese_sources_enabled": False})
    assert news.supplement_news("primary", "0700.HK", "2026-11-01", "2026-11-04") == "primary"
    assert news.fetch_futu_sentiment("0700.HK", "2026-11-01", "2026-11-04") == ""


def test_worker_fetches_read_their_runs_config(monkeypatch):
    seen = []

    def fetch(*a):
        seen.append(get_config()["chinese_source_timeout"])
        return NewsBatch("futu")

    monkeypatch.setattr(futu, "fetch_news", fetch)
    with run_config({"chinese_news_sources": "futu", "chinese_source_timeout": 3}):
        news.supplement_news("primary", "0700.HK", "2026-11-01", "2026-11-04")
    assert seen == [3, 3]


def test_macro_queries_and_overrides(monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(args)
        return NewsBatch("macro", [_item(args[0], f"https://example.com/{len(calls)}")])

    monkeypatch.setattr(futu, "fetch_news", fetch)
    set_config({"chinese_news_sources": "futu", "chinese_macro_queries": ["中国央行", "港股"]})
    out = news.supplement_global_news("primary", "BABA", "2026-11-04", 2, 1)
    assert {args[0] for args in calls} == {"中国央行", "港股"}
    assert all(args[-1] is False for args in calls)
    assert "2026-11-02 至 2026-11-04" in out
    assert out.count("Published:") == 1


def test_community_posts_without_urls_keep_distinct_ids_and_filter_filler(monkeypatch):
    posts = [
        {"id": "1", "title": "<p>$腾讯 (00700.HK)$</p>", "desc": "买", "publish_time": "1793757600"},
        {"id": "2", "title": "盈利改善值得观察，需关注后续资本开支和现金流变化", "publish_time": "1793757600"},
        {"id": "3", "title": "估值仍有压力，等待财报披露后再评估未来业绩预期", "publish_time": "1793757600"},
    ]
    monkeypatch.setattr(futu, "_get", lambda *a: posts)
    out = news.fetch_futu_sentiment("0700.HK", "2026-11-04", "2026-11-04")
    assert out.count("[社区观点]") == 2
    assert "Source item ID: 2" in out and "Source item ID: 3" in out
    assert "not supplied by source" in out
    assert "个人观点" in out


def test_community_timeout_is_reported(monkeypatch):
    def timeout(*a, **k):
        raise requests.Timeout()

    monkeypatch.setattr(futu.requests, "get", timeout)
    assert "unavailable" in news.fetch_futu_sentiment("BABA", "2026-11-01", "2026-11-04")


def test_tool_supplements_obey_trade_date_clamp(monkeypatch):
    import tradingagents.agents.tools as tools

    captured = []
    monkeypatch.setattr(tools, "route_to_vendor", lambda *a: "primary")
    monkeypatch.setattr(tools, "supplement_news", lambda *a: captured.append(a) or "enriched")
    assert tools.get_news.func("0700.HK", "2026-10-28", "2026-11-05", "2026-11-04") == "enriched"
    assert captured[0] == ("primary", "0700.HK", "2026-10-28", "2026-11-04")
    monkeypatch.setattr(tools, "supplement_global_news", lambda *a: captured.append(a) or "enriched")
    assert tools.get_global_news.func("2026-11-05", 7, 10, "2026-11-04", "BABA") == "enriched"
    assert captured[-1] == ("primary", "BABA", "2026-11-04", 7, 10)
    schema = tools.get_global_news.tool_call_schema.model_json_schema()["properties"]
    assert set(schema) == {"curr_date", "look_back_days", "limit"}


def test_new_environment_switches(monkeypatch):
    from tradingagents.default_config import build_default_config

    monkeypatch.setenv("TRADINGAGENTS_CHINESE_NEWS_SOURCES", "futu")
    monkeypatch.setenv("TRADINGAGENTS_FUTU_COMMUNITY_ENABLED", "false")
    config = build_default_config()
    assert config["chinese_news_sources"] == "futu"
    assert config["futu_community_enabled"] is False


@pytest.mark.parametrize("error", [VendorNotConfiguredError("key missing"), VendorUnavailableError("network failed"), RuntimeError("upstream failed")])
def test_primary_not_configured_still_reads_chinese_news(monkeypatch, error):
    import tradingagents.agents.tools as tools

    def fail(*a):
        raise error

    monkeypatch.setattr(tools, "route_to_vendor", fail)
    monkeypatch.setattr(tools, "supplement_news", lambda primary, *a: primary + "\nFutu results")
    monkeypatch.setattr(tools, "supplement_global_news", lambda primary, *a: primary + "\nChinese macro results")
    assert "Futu results" in tools.get_news.func("1810.HK", "2026-10-28", "2026-11-04")
    assert "Chinese macro results" in tools.get_global_news.func("2026-11-04", ticker="1810.HK")
