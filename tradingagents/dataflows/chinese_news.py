"""Optional Chinese-market enrichment, separate from the primary vendor chain.

The router's ordered fallback contract remains intact. These explicitly
configured supplements are all read even when the primary vendor succeeds.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta

from tradingagents.dataflows.chinese_sources import (
    NewsBatch,
    announcement_keyword,
    render_batches,
    search_keyword,
    uses_chinese_sources,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors import eastmoney, futu

logger = logging.getLogger(__name__)


def _fetch_batches(keyword_for, *, ticker: str = "") -> list[NewsBatch]:
    config = get_config()
    jobs = []
    vendors = dict.fromkeys(v.strip() for v in config["chinese_news_sources"].split(",") if v.strip())
    for vendor in vendors:
        fetch = {"futu": futu.fetch_news, "eastmoney": eastmoney.fetch_news}.get(vendor)
        if fetch is None:
            raise ValueError(f"Unknown Chinese news source {vendor!r}; choose futu or eastmoney")
        args = ((keyword_for(vendor), config["chinese_news_fetch_limit"], 1, bool(ticker)) if vendor == "futu"
                else (keyword_for(vendor), config["chinese_news_fetch_limit"], bool(ticker)))
        jobs.append((vendor, fetch, args))
        if vendor == "futu" and ticker and config["futu_announcements_enabled"]:
            jobs.append(("富途公告检索", futu.fetch_news, (announcement_keyword(ticker), 10, 2)))
    if not jobs:
        return []
    batches = []
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        # Each worker needs its own context, so concurrent graph runs retain
        # their own source settings/timeouts instead of reading global config.
        pending = [(label, pool.submit(copy_context().run, fetch, *args)) for label, fetch, args in jobs]
        for label, future in pending:
            try:
                batches.append(future.result())
            except VendorUnavailableError as exc:
                logger.warning("Chinese source %s unavailable: %s", label, exc)
                batches.append(NewsBatch(label, note=f"<unavailable: {exc}>"))
    return batches


def supplement_news(primary: str, ticker: str, start_date: str, end_date: str) -> str:
    if not uses_chinese_sources(ticker) or not get_config()["chinese_news_sources"].strip():
        return primary
    batches = _fetch_batches(lambda vendor: search_keyword(ticker, vendor), ticker=ticker)
    return primary + "\n\n" + render_batches(
        batches, start_date, end_date, get_config()["chinese_news_article_limit"],
    )


def supplement_global_news(
    primary: str, ticker: str, as_of_date: str, look_back_days: int | None, limit: int | None,
) -> str:
    if not uses_chinese_sources(ticker) or not get_config()["chinese_news_sources"].strip():
        return primary
    config = get_config()
    days = config["global_news_lookback_days"] if look_back_days is None else look_back_days
    start = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    batches = []
    for query in config["chinese_macro_queries"]:
        batches.extend(_fetch_batches(lambda vendor, query=query: query))
    return primary + "\n\n" + render_batches(
        batches, start, as_of_date,
        config["global_news_article_limit"] if limit is None else limit,
    )


def fetch_futu_sentiment(ticker: str, start_date: str, end_date: str) -> str:
    if not uses_chinese_sources(ticker) or not get_config()["futu_community_enabled"]:
        return ""
    try:
        batch = futu.fetch_community(ticker, 30)
    except VendorUnavailableError as exc:
        return f"<富途牛牛社区 unavailable: {exc}; this is not an absence of discussion>"
    return render_batches([batch], start_date, end_date, 20)
