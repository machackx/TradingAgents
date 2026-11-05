"""Validate typed and free-text proposals before any downstream rating is usable.

Unstructured arithmetic is never executed or silently trusted. Numeric order
quantities require a matching currency and sufficient supplied cash/holdings.
"""

from __future__ import annotations

import math
import re

from pydantic import ValidationError

from tradingagents.agents.schemas import TraderAction, TraderProposal, sizing_percentages
from tradingagents.dataflows.config import get_config
from tradingagents.portfolio import PortfolioContext


def labelled_fields(text: str, unique_fields=()) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        match = re.match(r"\s*(?:[-*]\s+)?\*{0,2}([A-Za-z][A-Za-z -]+?)\*{0,2}\s*[:：]\s*(.*?)\s*$", line)
        if match:
            name, value = match.groups()
            name, value = name.strip().lower().replace("-", " "), value.strip("* ")
            if name in unique_fields and name in values and values[name] != value:
                raise ValueError(f"conflicting repeated {name} fields")
            values[name] = value
    return values


def parse_proposal(text: str) -> TraderProposal:
    if not isinstance(text, str):
        raise ValueError("free-text proposal did not contain a text response")
    fields = labelled_fields(text, unique_fields={
        "action", "entry price", "stop loss", "position sizing", "position size pct", "order quantity",
    })
    action = fields.get("action")
    if action not in {item.value for item in TraderAction}:
        raise ValueError("free-text proposal has no unambiguous Action: Buy / Hold / Sell line")
    values = {"action": action, "reasoning": fields.get("reasoning", "Free-text proposal")}
    aliases = {
        "entry price": "entry_price", "stop loss": "stop_loss", "position sizing": "position_sizing",
        "position size pct": "position_size_pct", "order quantity": "order_quantity",
    }
    for name, key in aliases.items():
        if name in fields and fields[name].casefold() not in {"not provided", "none", "n/a", "", "null"}:
            if key in {"entry_price", "stop_loss", "order_quantity", "position_size_pct"}:
                value = fields[name].replace(",", "").lstrip("$€£¥").strip()
                # Do not let a malformed provided number be coerced to None by
                # the backwards-compatible schema's optional-field cleanup.
                try:
                    float(value)
                except ValueError as exc:
                    raise ValueError(f"{name} must be a single absolute number") from exc
                values[key] = float(value)
            else:
                values[key] = fields[name]
    return TraderProposal.model_validate(values)


def validate_proposal(proposal: TraderProposal, state: dict) -> dict:
    # Revalidate even providers that return model_construct() or dict results.
    proposal = TraderProposal.model_validate(proposal.model_dump())
    issues, notes = [], []
    cap = float(get_config()["max_position_pct"])
    if not math.isfinite(cap) or not 0 < cap <= 100:
        raise ValueError("max_position_pct must be finite and between 0 (exclusive) and 100")
    percentages = ([proposal.position_size_pct] if proposal.position_size_pct is not None else [])
    percentages += sizing_percentages(proposal.position_sizing or "")
    if any(value > cap for value in percentages):
        issues.append(f"position sizing exceeds the configured {cap:g}% cap")
    portfolio = PortfolioContext.model_validate(state.get("portfolio_data") or {})
    currency = (state.get("market_data") or {}).get("quote_currency")
    if proposal.order_quantity is not None:
        if proposal.action == TraderAction.HOLD:
            issues.append("a Hold proposal cannot specify an order quantity")
        elif proposal.action == TraderAction.BUY:
            if proposal.entry_price is None or portfolio.cash is None:
                issues.append("buy quantity requires an entry price and supplied cash balance")
            elif not currency or (portfolio.currency or "").upper() != currency:
                issues.append("cash and quote currencies cannot be verified as matching; no FX conversion is assumed")
            else:
                notional = proposal.entry_price * proposal.order_quantity
                if notional > portfolio.cash:
                    issues.append("buy notional exceeds supplied cash before fees")
                if portfolio.net_asset_value is not None:
                    held = sum(max(0, p.quantity) for p in portfolio.positions_in(state["company_of_interest"]))
                    weight = (held + proposal.order_quantity) * proposal.entry_price / portfolio.net_asset_value * 100
                    if weight > cap:
                        issues.append(f"resulting position exceeds the configured {cap:g}% NAV cap")
                elif cap < 100:
                    issues.append("a NAV-based position cap cannot be verified without supplied net_asset_value")
        elif proposal.action == TraderAction.SELL:
            held = sum(p.quantity for p in portfolio.positions_in(state["company_of_interest"]))
            if proposal.order_quantity > max(0, held):
                issues.append("sell quantity exceeds supplied long holdings; short-selling is not inferred")
    else:
        notes.append("No numeric order quantity supplied; affordability and resulting portfolio weight are unverified.")
    if any(percentages) and portfolio.net_asset_value is None:
        notes.append("No portfolio NAV supplied; percentage sizing has not been converted into an affordable order.")
    if proposal.action == TraderAction.SELL and proposal.stop_loss is not None:
        notes.append("Sell is treated as reducing a long position; a short-position stop is not inferred.")
    return {"status": "invalid" if issues else "validated", "issues": issues, "notes": notes,
            "proposal": proposal.model_dump(mode="json") if not issues else None}


def check_trader_text(text: str, state: dict) -> dict:
    try:
        return validate_proposal(parse_proposal(text), state)
    except (ValueError, ValidationError) as exc:
        return {"status": "invalid", "issues": [str(exc).split("\n")[0]], "notes": []}
