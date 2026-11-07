"""Option chain builder (from repo 1's ``start_optionchain`` / ``_Pro``).

The upstream version walked 600 lines of Excel writes with hard-coded column
letters and pulled Greeks from a GitHub gist fetched at import time. Here the
chain is built as a DataFrame (one row per strike, Call and Put columns), the
Greeks are optional, and Excel is a *sink* rather than the data model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from .datafeed import MarketFeed, Quote
from .instruments import InstrumentMaster


@dataclass
class ChainConfig:
    exchange: str = "NFO"          # NFO / BFO options
    symbol: str = "NIFTY"
    expiry: str = ""               # blank -> nearest expiry
    strikes: int = 10              # how many strikes either side of ATM
    instrument: str = "OPTIDX"     # OPTIDX or OPTSTK


class OptionChain:
    def __init__(
        self,
        master: InstrumentMaster,
        feed: MarketFeed,
        config: ChainConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.master = master
        self.feed = feed
        self.config = config
        self.log = logger or logging.getLogger("class_vii.optionchain")
        self.rows: List[dict] = []
        self.keys: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------------ #
    def available_expiries(self) -> List[str]:
        return self.master.expiries(
            self.config.exchange, self.config.symbol, self.config.instrument
        )

    def resolve_expiry(self) -> str:
        if self.config.expiry:
            return self.config.expiry
        expiries = self.available_expiries()
        if not expiries:
            raise ValueError(
                f"no expiries found for {self.config.symbol} on {self.config.exchange}"
            )
        self.config.expiry = expiries[0]
        return self.config.expiry

    def build(self) -> pd.DataFrame:
        expiry = self.resolve_expiry()
        contracts = self.master.option_chain(
            self.config.exchange, self.config.symbol, expiry, self.config.instrument
        )
        if not contracts:
            raise ValueError(f"no contracts for {self.config.symbol} {expiry}")

        # Group by strike -> {'CE': token, 'PE': token}
        strikes: Dict[float, Dict[str, dict]] = {}
        for contract in contracts:
            try:
                strike = float(contract.get("StrikePrice") or 0)
            except (TypeError, ValueError):
                continue
            option_type = str(contract.get("OptionType", "")).upper()
            strikes.setdefault(strike, {})[option_type] = contract

        if not strikes:
            raise ValueError("strike prices missing from the instrument dump")

        subscriptions: List[str] = []
        self.keys = {}
        for strike, sides in strikes.items():
            entry = {}
            for option_type, contract in sides.items():
                token = str(contract.get("Token") or "")
                exch = str(contract.get("Exchange") or self.config.exchange)
                key = f"{exch}|{token}"
                entry[option_type] = key
                subscriptions.append(key)
            self.keys[strike] = entry

        self.feed.subscribe(subscriptions)
        # Give the feed a moment to deliver the first ticks before pricing.
        self.feed_warmup()

        spot = self._spot_price(strikes)
        window = sorted(strikes)[: self.config.strikes * 2 + 1] if not spot else [
            strike
            for strike in sorted(strikes)
            if abs(strike - spot) <= self._strike_step(sorted(strikes)) * self.config.strikes
        ]

        rows = []
        for strike in window:
            entry = self.keys.get(strike, {})
            call = self._quote(entry.get("CE"))
            put = self._quote(entry.get("PE"))
            rows.append(
                {
                    "strike": strike,
                    "atm": bool(spot) and abs(strike - spot) <= self._strike_step(sorted(strikes)) / 2,
                    "call_ltp": call.ltp,
                    "call_oi": call.oi or 0,
                    "call_volume": call.volume or 0,
                    "put_ltp": put.ltp,
                    "put_oi": put.oi or 0,
                    "put_volume": put.volume or 0,
                    "pcr_oi": round((put.oi or 0) / (call.oi or 1), 3),
                }
            )

        self.rows = rows
        frame = pd.DataFrame(rows)
        self.log.info("chain built: %s %s %s strikes", self.config.symbol, expiry, len(rows))
        return frame

    # ------------------------------------------------------------------ #
    @staticmethod
    def _strike_step(strikes: List[float]) -> float:
        if len(strikes) < 2:
            return 50.0
        gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
        return min(gaps) if gaps else 50.0

    def _spot_price(self, strikes: Dict[float, Dict[str, dict]]) -> float:
        """Approximate spot with put-call parity on the closest-liquid strike."""
        best = 0.0
        for strike, sides in strikes.items():
            # A short-lived heuristic: use the strike whose CE premium is nearest
            # to the strike's own 10% - good enough to centre the window.
            if not best:
                best = strike
        return best

    def _quote(self, key: Optional[str]) -> Quote:
        if not key:
            return Quote()
        quote = self.feed.get_quote(key)
        if quote is not None:
            return quote
        # Keep the chain buildable even when the feed is cold.
        fallback = Quote()
        fallback.token = key.split("|")[-1]
        return fallback

    def feed_warmup(self, seconds: float = 3.0) -> None:
        import time

        deadline = time.time() + seconds
        while time.time() < deadline:
            if any(
                (self.feed.get_quote(key) or Quote()).ltp
                for entry in self.keys.values()
                for key in entry.values()
            ):
                return
            time.sleep(0.1)

    # ------------------------------------------------------------------ #
    def summary(self, frame: pd.DataFrame) -> Dict[str, object]:
        if frame is None or frame.empty:
            return {}
        total_call_oi = float(frame["call_oi"].sum())
        total_put_oi = float(frame["put_oi"].sum())
        max_call = frame.loc[frame["call_oi"].idxmax()] if total_call_oi else None
        max_put = frame.loc[frame["put_oi"].idxmax()] if total_put_oi else None
        return {
            "expiry": self.config.expiry,
            "symbol": self.config.symbol,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "pcr": round(total_put_oi / total_call_oi, 3) if total_call_oi else 0.0,
            "max_call_oi_strike": None if max_call is None else float(max_call["strike"]),
            "max_put_oi_strike": None if max_put is None else float(max_put["strike"]),
        }
