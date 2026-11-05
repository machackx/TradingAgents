"""Futu's public content API, documented in its official MIT-licensed skills.

Reference: https://www.futunn.com/skillhub/news_search and the API reference in
https://www.futunn.com/skills/futu.zip (news_search and stock_feed).
No OpenD, brokerage credentials or trading endpoints are used.
"""

from __future__ import annotations

import re

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

BASE_URL = "https://ai-news-search.futunn.com"


def _get(path: str, params: dict) -> list[dict]:
    try:
        response = requests.get(
            f"{BASE_URL}/{path}", params=params,
            headers={"User-Agent": "futunn-news-search/0.0.2 (Skill)" if path == "news_search"
                     else "futu-comment-sentiment/0.0.2 (Skill)"},
            timeout=get_config()["chinese_source_timeout"],
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise VendorUnavailableError(f"Futu {path} request failed ({type(exc).__name__})") from exc
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0"):
        raise VendorUnavailableError(f"Futu {path} returned an unsuccessful response")
    data = payload.get("data")
    if data is None:
        data = []
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise VendorUnavailableError(f"Futu {path} returned an invalid data schema")
    return data


def fetch_news(keyword: str, limit: int = 20, news_type: int = 1, strict: bool = True) -> NewsBatch:
    items = _get("news_search", {
        "keyword": keyword, "size": max(1, min(50, limit)), "news_type": news_type,
        "lang": "zh-CN", "sort_type": 2,
    })
    kind = {1: "新闻", 2: "公告检索", 3: "研报观点"}[news_type]
    return NewsBatch(f"富途牛牛 · {kind}", [NewsItem(
        title=clean_text(item.get("title"), 300),
        published=published_at(item.get("publish_time")),
        url=safe_url(item.get("url")),
        # The public API often omits the original publisher. Do not attribute
        # a broker's report or a syndicated item to Futu's own newsroom.
        publisher=clean_text(item.get("source") or item.get("publisher"), 100)
        or "富途聚合（接口未提供原始发布方，请核对原文）",
        kind=kind,
    ) for item in items if not strict or matches_keyword(clean_text(item.get("title")), keyword)], note=(
        "Announcement search results are aggregated by Futu; verify the issuer and original disclosure."
        if news_type == 2 else ""
    ))


def get_news_futu(ticker: str, start_date: str, end_date: str) -> str:
    limit = get_config()["news_article_limit"]
    batch = fetch_news(search_keyword(ticker, "futu"), limit)
    return render_batches([batch], start_date, end_date, limit)


def fetch_community(ticker: str, limit: int = 30) -> NewsBatch:
    items = _get("stock_feed", {"keyword": search_keyword(ticker, "futu"), "size": max(1, min(50, limit))})
    posts, seen = [], set()
    for item in items:
        title = clean_text(item.get("title"), 200)
        body = clean_text(item.get("desc"), 500)
        text = body if title in body else f"{title} {body}".strip()
        # Strip the cashtag before measuring substance: '$腾讯(00700.HK)$'
        # alone is not an investment view. Do not invent likes or vote counts.
        substance = re.sub(r"\$[^$]+\$", "", text).strip()
        if len(substance) < 12 or text in seen:
            continue
        seen.add(text)
        posts.append(NewsItem(
            title=text[:500], published=published_at(item.get("publish_time")),
            url=safe_url(item.get("url")), publisher="牛牛社区用户（个人观点，非权威事实）",
            kind="社区观点", identity=clean_text(str(item.get("id") or ""), 80),
        ))
    return NewsBatch("富途牛牛社区", posts, note=(
        "Recent discussion sample after basic text/deduplication filtering; no user-labeled sentiment, "
        "engagement counts or population-wide bullish/bearish ratio is supplied. "
        "Cross-listed shares may appear together. This is opinion, not verified news."
    ))
