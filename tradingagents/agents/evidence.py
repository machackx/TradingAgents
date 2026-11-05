"""Evidence checks use actual tool results, never an analyst's self-assessment."""

import re

from langchain_core.messages import ToolMessage


def merge_evidence(left: dict, right: dict) -> dict:
    return {**left, **right}


def usable_result(text: str) -> bool:
    """Recognize typed/vendor absence notices, allowing successful supplements.

    This checks retrieval, not the truth of an article. A mixed result with a
    failed primary and dated Chinese articles still contains useful evidence.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    if re.search(r"- \[(?:新闻|公告检索|研报观点|社区观点)\].+\n\s*Published:", text):
        return True
    notice = (r"DATA_UNAVAILABLE|NO_DATA_AVAILABLE|(?:fundamentals|statements|data) (?:are|is) withheld"
              r"|<[^>]*(?:unavailable|no (?:StockTwits|Reddit)|none of the)"
              r"|^No (?:news|global news|posts|messages|data)\b")
    if re.search(notice, text, re.IGNORECASE | re.MULTILINE):
        return False
    numeric_cell = r"[:|]\s*[-+$€£¥]?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:[%|]|$)"
    return not ("N/A" in text and not re.search(numeric_cell, text, re.MULTILINE))


def tool_evidence(messages) -> dict:
    results = {}
    for message in messages:
        if not isinstance(message, ToolMessage) or not message.name:
            continue
        ok = getattr(message, "status", "success") != "error" and usable_result(message.content)
        entry = results.setdefault(message.name, {"attempted": 0, "usable": 0})
        entry["attempted"] += 1
        entry["usable"] += int(ok)
    return results


def prefetched_evidence(**sources: str) -> dict:
    return {name: {"attempted": 1, "usable": int(usable_result(text))} for name, text in sources.items()}


def assess_evidence(state: dict, selected: list[str]) -> dict:
    evidence = state.get("analyst_evidence", {})
    required = {
        "market": [("get_stock_data",), ("get_verified_market_snapshot",)],
        "news": [("get_news",)],
        "fundamentals": [("get_balance_sheet",), ("get_income_statement",), ("get_cashflow",)],
        "social": [("news", "stocktwits", "reddit", "futu")],
    }
    missing = []
    coverage = {}
    for key in selected:
        if key == "fundamentals" and state.get("asset_type") == "crypto":
            coverage[key] = "not_applicable"
            continue
        observed = evidence.get(key, {})
        absent = [" / ".join(group) for group in required[key]
                  if not any(observed.get(name, {}).get("usable", 0) > 0 for name in group)]
        coverage[key] = "missing" if absent else "available"
        missing.extend(f"{key}: no usable result from {group}" for group in absent)
    if "market" in selected:
        snapshot = state.get("market_data") or {}
        if snapshot.get("status") != "available":
            missing.append("market: no fixed market dataset was available for this run")
            if snapshot.get("reason"):
                missing.append(f"market snapshot: {snapshot['reason']}")
    return {"status": "insufficient_data" if missing else "adequate", "issues": missing,
            "coverage": coverage, "selected_analysts": selected}


def review_report(reasons: list[str]) -> str:
    from tradingagents.dataflows.config import get_config

    chinese = "chinese" in get_config().get("output_language", "").lower() or "中文" in get_config().get("output_language", "")
    explanation = "资料或建议未通过程序校验，本次不生成可用交易评级。" if chinese else "Evidence or proposal checks failed; no actionable rating is issued."
    return "**Rating**: REVIEW\n\n**Decision status**: REVIEW\n\n" + explanation + "\n\n" + "\n".join(f"- {reason}" for reason in reasons)
