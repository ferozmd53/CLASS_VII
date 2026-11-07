"""Multi-symbol scanner: live quotes + daily indicators + reversal signals."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from . import indicators
from . import settings as S
from .datafeed import HistoricalData, MarketFeed
from .instruments import InstrumentMaster, parse_symbol


class SymbolScanner:
    """Tracks a basket of symbols and computes live indicator snapshots."""

    def __init__(
        self,
        feed: MarketFeed,
        history: HistoricalData,
        master: InstrumentMaster,
        config: S.ScannerConfig,
        alerts=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.feed = feed
        self.history = history
        self.master = master
        self.config = config
        self.alerts = alerts
        self.log = logger or logging.getLogger("class_vii.scanner")
        self.keys: Dict[str, str] = {}          # symbol -> "EXCH|TOKEN"
        self.snapshots: Dict[str, dict] = {}    # symbol -> latest indicator snapshot
        self.ticks: int = 0
        self._signalled: Dict[str, str] = {}    # symbol -> last signal emitted

    # ------------------------------------------------------------------ #
    def add_symbols(self, symbols: List[str]) -> List[str]:
        added: List[str] = []
        subscriptions: List[str] = []
        for raw in symbols:
            exchange, tradingsymbol = parse_symbol(raw)
            exchange, tradingsymbol = exchange.upper(), tradingsymbol.upper()
            if not tradingsymbol.endswith("-EQ") and exchange in {"NSE", "BSE"}:
                if self.master.token_for(exchange, tradingsymbol) is None:
                    tradingsymbol = f"{tradingsymbol}-EQ"
            token = self.master.token_for(exchange, tradingsymbol)
            if not token:
                self.log.warning("unknown symbol: %s:%s", exchange, tradingsymbol)
                continue
            key = f"{exchange}|{token}"
            self.keys[tradingsymbol] = key
            subscriptions.append(key)
            added.append(tradingsymbol)

        if subscriptions:
            self.feed.subscribe(subscriptions)

        # Warm indicators from daily history so the first tick is meaningful.
        for symbol in added:
            payload = self.history.load(symbol, self.config.exchange)
            if payload:
                self.snapshots[symbol] = dict(payload["snapshot"])
        self.log.info("scanner tracking %s symbols", len(self.keys))
        return added

    # ------------------------------------------------------------------ #
    def on_tick(self, key: str, ltp: float) -> Optional[str]:
        """Fold the tick into the indicator series; return 'BUY'/'SELL'/None."""
        if ltp <= 0:
            return None
        symbol = self._symbol_for_key(key)
        if symbol is None:
            return None

        self.ticks += 1
        payload = self.history.cache.get(symbol)
        if not payload:
            return None

        closes = payload["closes"] + [ltp]
        frame = pd.DataFrame({"close": closes})
        frame = indicators.add_indicators(
            frame,
            rsi_length=self.config.rsi_length,
            stoch_length=self.config.stoch_length,
            sma_stoch_length=self.config.sma_stoch_length,
            bb_length=self.config.bb_length,
            bb_std=self.config.bb_std,
        )
        frame = indicators.add_signals(frame, self.config.stoch_lower, self.config.stoch_upper)

        snapshot = indicators.snapshot(frame)
        snapshot["ltp"] = ltp
        snapshot["updated_at"] = datetime.now()
        self.snapshots[symbol] = snapshot

        signal = ""
        if bool(frame["BUY_SIGNAL"].iloc[-1]):
            signal = "BUY"
        elif bool(frame["SELL_SIGNAL"].iloc[-1]):
            signal = "SELL"

        # Emit once per symbol per side-change, not on every tick.
        if signal and self._signalled.get(symbol) != signal:
            self._signalled[symbol] = signal
            message = (
                f"{signal} {symbol} @ {ltp:.2f} | RSI {snapshot['rsi']:.2f} "
                f"StochRSI {snapshot['sma_stoch']:.2f} "
                f"BB[{snapshot['bb_lower']:.2f}-{snapshot['bb_upper']:.2f}]"
            )
            self.log.info(message)
            if self.alerts:
                self.alerts.notify(message)
        elif not signal:
            self._signalled.pop(symbol, None)

        return signal or None

    def _symbol_for_key(self, key: str) -> Optional[str]:
        for symbol, symbol_key in self.keys.items():
            if symbol_key == key:
                return symbol
        return None

    # ------------------------------------------------------------------ #
    def frame(self) -> pd.DataFrame:
        """Snapshot table, ready for Excel or CSV."""
        rows = []
        for symbol, key in self.keys.items():
            data = self.snapshots.get(symbol, {})
            rows.append(
                [
                    f"{self.config.exchange}:{symbol}",
                    round(float(data.get("ltp", 0.0) or 0.0), 2),
                    round(float(data.get("open", 0.0) or 0.0), 2),
                    round(float(data.get("rsi", 50.0) or 50.0), 2),
                    round(float(data.get("stoch_rsi", 50.0) or 50.0), 2),
                    round(float(data.get("sma_stoch", 50.0) or 50.0), 2),
                    round(float(data.get("bb_upper", 0.0) or 0.0), 2),
                    round(float(data.get("bb_middle", 0.0) or 0.0), 2),
                    round(float(data.get("bb_lower", 0.0) or 0.0), 2),
                    self._signalled.get(symbol, ""),
                    str(data.get("updated_at", "")),
                ]
            )
        return pd.DataFrame(rows, columns=self.headers())

    @staticmethod
    def headers() -> List[str]:
        return [
            "Symbol", "LTP", "Open", "RSI", "StochRSI", "SMA Stoch",
            "BB Upper", "BB Middle", "BB Lower", "Signal", "Last Update",
        ]

    # ------------------------------------------------------------------ #
    def run(self, interval: float = 0.5, on_cycle=None) -> None:
        """Blocking loop: refresh snapshots, optionally publish them."""
        while True:
            try:
                if on_cycle:
                    on_cycle(self)
                time.sleep(interval)
            except KeyboardInterrupt:
                self.log.info("scanner stopped by user")
                return
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                self.log.exception("scanner cycle failed: %s", exc)
                time.sleep(1.0)
