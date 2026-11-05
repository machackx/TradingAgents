"""Eastmoney public web news search (the same search used by so.eastmoney.com).

This is a website endpoint rather than a versioned/stability-guaranteed API.
Keep original publishers and URLs; reject errors and unexpected response shapes.
"""

from __future__ import annotations

import json

import requests

from tradingagents.dataflows.chinese_sources import (
    NewsBatch,
    NewsItem,
    clean_text,
    matches_keyword,
    published_at,
    render_batches,
    safe_url,
    search_keyword,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorUnavailableError


def fetch_news(keyword: str, limit: int = 20, strict: bool = True) -> NewsBatch:
    params = {
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default", "pageIndex": 1,
            "pageSize": max(1, min(50, limit)),
        }},
    }
    try:
        response = requests.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "tradingagents", "param": json.dumps(params, ensure_ascii=False)},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://so.eastmoney.com/"},
            timeout=get_config()["chinese_source_timeout"],
        )
        response.raise_for_status()
        text = response.text.strip().removesuffix(";")
        # Parse JSONP as data only. Never eval JavaScript from a remote service.
        if text.startswith("tradingagents(") and text.endswith(")"):
            text = text[len("tradingagents("):-1]
        payload = json.loads(text)
    except (requests.RequestException, ValueError) as exc:
        raise VendorUnavailableError(f"Eastmoney search failed ({type(exc).__name__})") from exc
    if (not isinstance(payload, dict) or payload.get("code") not in (0, "0")
            or not isinstance(payload.get("result"), dict)
            or not isinstance(payload["result"].get("cmsArticleWebOld"), list)):
        raise VendorUnavailableError("Eastmoney search returned an unsuccessful or invalid response")
    items = payload["result"]["cmsArticleWebOld"]
    if any(not isinstance(item, dict) for item in items):
        raise VendorUnavailableError("Eastmoney search returned invalid article data")
    return NewsBatch("东方财富 · 财经媒体检索", [NewsItem(
        title=clean_text(item.get("title"), 300), published=published_at(item.get("date")),
        url=safe_url(item.get("url")),
        publisher=clean_text(item.get("mediaName"), 100) or "东方财富聚合（原始发布方未提供）",
        summary=clean_text(item.get("content"), 350),
    ) for item in items if not strict or matches_keyword(
        clean_text(item.get("title")) + " " + clean_text(item.get("content")), keyword
    )], note=(
        "Keyword search, not exchange-tagged company coverage; check relevance. "
        "The original media publisher is retained when supplied."
    ))


def get_news_eastmoney(ticker: str, start_date: str, end_date: str) -> str:
    limit = get_config()["news_article_limit"]
    return render_batches([fetch_news(search_keyword(ticker, "eastmoney"), limit)], start_date, end_date, limit)
