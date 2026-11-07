"""TradingView-compatible RSI, Stochastic RSI and Bollinger Bands.

Ported from repo 2 (``Extreme_Reversal_Signal.py``) and kept numerically
identical: Wilder smoothing via ``ewm(alpha=1/length, adjust=False)`` for RSI,
rolling min/max for StochRSI, population std (``ddof=0``) for the bands.
That matters - a sample std shifts every band edge and silently changes signals.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index, TradingView ``ta.rsi`` equivalent."""
    close = pd.Series(close, dtype="float64")
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    alpha = 1.0 / float(length)
    avg_gain = gain.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - (100.0 / (1.0 + rs))
    # A flat series (avg_loss == 0) is fully overbought, not NaN.
    out = out.where(avg_loss != 0, 100.0)
    return out.fillna(50.0)


def stoch_rsi(rsi_series: pd.Series, length: int = 14) -> pd.Series:
    """Stochastic RSI over the RSI series, scaled 0-100."""
    rsi_series = pd.Series(rsi_series, dtype="float64")
    lowest = rsi_series.rolling(length).min()
    highest = rsi_series.rolling(length).max()
    span = (highest - lowest).replace(0.0, np.nan)
    stoch = 100.0 * (rsi_series - lowest) / span
    return stoch.fillna(50.0)


def bollinger(
    close: pd.Series, length: int = 20, std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Return (middle, upper, lower) Bollinger Bands."""
    close = pd.Series(close, dtype="float64")
    middle = close.rolling(length).mean()
    deviation = close.rolling(length).std(ddof=0)
    upper = middle + std * deviation
    lower = middle - std * deviation
    return middle, upper, lower


def add_indicators(
    frame: pd.DataFrame,
    close_col: str = "close",
    rsi_length: int = 14,
    stoch_length: int = 14,
    sma_stoch_length: int = 3,
    bb_length: int = 20,
    bb_std: float = 2.0,
) -> pd.DataFrame:
    """Attach RSI / StochRSI / SMA(StochRSI) / BB columns to *frame* in place."""
    if close_col not in frame.columns:
        raise KeyError(f"column {close_col!r} not in frame")

    frame["RSI"] = rsi(frame[close_col], rsi_length)
    frame["STOCH_RSI"] = stoch_rsi(frame["RSI"], stoch_length)
    frame["SMA_STOCH"] = frame["STOCH_RSI"].rolling(sma_stoch_length).mean()
    middle, upper, lower = bollinger(frame[close_col], bb_length, bb_std)
    frame["BB_MIDDLE"] = middle
    frame["BB_UPPER"] = upper
    frame["BB_LOWER"] = lower
    return frame


def add_signals(
    frame: pd.DataFrame,
    stoch_lower: float = 30.0,
    stoch_upper: float = 70.0,
) -> pd.DataFrame:
    """Mean-reversion signal exactly as defined in repo 2.

    BUY  : previous close closed *below* the previous lower band,
           today's close is back above today's lower band,
           previous SMA(StochRSI) < lower threshold.
    SELL : mirror image above the upper band.
    """
    close = frame["close"]
    prev_close = close.shift(1)
    prev_lower = frame["BB_LOWER"].shift(1)
    prev_upper = frame["BB_UPPER"].shift(1)
    prev_stoch = frame["SMA_STOCH"].shift(1)

    frame["BUY_SIGNAL"] = (
        (prev_close < prev_lower)
        & (close > frame["BB_LOWER"])
        & (prev_stoch < stoch_lower)
    )
    frame["SELL_SIGNAL"] = (
        (prev_close > prev_upper)
        & (close < frame["BB_UPPER"])
        & (prev_stoch > stoch_upper)
    )
    frame["SIGNAL"] = np.where(
        frame["BUY_SIGNAL"], "BUY", np.where(frame["SELL_SIGNAL"], "SELL", "")
    )
    return frame


def snapshot(
    frame: pd.DataFrame,
    fallback: Optional[dict] = None,
) -> dict:
    """Latest + previous bar of every indicator, with NaN-safe defaults."""
    defaults = {
        "close": 0.0,
        "rsi": 50.0,
        "stoch_rsi": 50.0,
        "sma_stoch": 50.0,
        "bb_upper": 0.0,
        "bb_middle": 0.0,
        "bb_lower": 0.0,
        "prev_close": 0.0,
        "prev_bb_upper": 0.0,
        "prev_bb_lower": 0.0,
        "prev_sma_stoch": 50.0,
    }
    if fallback:
        defaults.update(fallback)
    if frame is None or frame.empty:
        return defaults

    def last(column: str, default: float) -> float:
        series = frame.get(column)
        if series is None or series.empty:
            return default
        value = series.iloc[-1]
        return float(value) if pd.notna(value) else default

    def previous(column: str, default: float) -> float:
        series = frame.get(column)
        if series is None or len(series) < 2:
            return default
        value = series.iloc[-2]
        return float(value) if pd.notna(value) else default

    return {
        "close": last("close", defaults["close"]),
        "rsi": last("RSI", defaults["rsi"]),
        "stoch_rsi": last("STOCH_RSI", defaults["stoch_rsi"]),
        "sma_stoch": last("SMA_STOCH", defaults["sma_stoch"]),
        "bb_upper": last("BB_UPPER", defaults["bb_upper"]),
        "bb_middle": last("BB_MIDDLE", defaults["bb_middle"]),
        "bb_lower": last("BB_LOWER", defaults["bb_lower"]),
        "prev_close": previous("close", defaults["prev_close"]),
        "prev_bb_upper": previous("BB_UPPER", defaults["prev_bb_upper"]),
        "prev_bb_lower": previous("BB_LOWER", defaults["prev_bb_lower"]),
        "prev_sma_stoch": previous("SMA_STOCH", defaults["prev_sma_stoch"]),
    }
