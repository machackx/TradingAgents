"""Indicator calculations with full windows for simple moving averages."""

import re

import pandas as pd
from stockstats import wrap


def indicator_series(data: pd.DataFrame, indicator: str) -> pd.Series:
    """Stockstats allows partial SMA windows; a named N-session SMA must not."""
    match = re.fullmatch(r"close_([1-9]\d*)_sma", indicator)
    if match:
        close = pd.to_numeric(data["Close"], errors="coerce").replace(
            [float("inf"), float("-inf")], float("nan"),
        )
        window = int(match[1])
        return close.rolling(window, min_periods=window).mean()
    return wrap(data.copy())[indicator]


def unavailable_indicator(indicator: str) -> str:
    match = re.fullmatch(r"close_([1-9]\d*)_sma", indicator)
    return f"N/A: requires a full {match[1]}-session close window" if match else "N/A"
