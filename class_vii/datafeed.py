"""Shoonya websocket market data and daily history.

NorenApi.subscribe() and unsubscribe() expect instrument keys such as
"NSE|2885" (strings). Passing dictionaries causes the websocket client's
'expected str instance, dict found' error and prevents all live ticks.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from . import indicators
from . import settings as S
from .instruments import InstrumentMaster, parse_symbol

TICK_FIELDS = {
    "e": "exchange",
    "tk": "token",
    "ts": "tradingsymbol",
    "lp": "ltp",
    "pc": "pct_change",
    "c": "close",
    "o": "open",
    "h": "high",
    "l": "low",
    "v": "volume",
    "ltq": "last_qty",
    "bp1": "bid",
    "sp1": "ask",
    "ap": "avg_price",
    "oi": "oi",
    "poi": "prev_oi",
    "toi": "total_oi",
}


class Quote:
    """Normalised quote for one instrument."""

    __slots__ = tuple(TICK_FIELDS.values()) + ("updated_at",)

    def __init__(self) -> None:
        for field in TICK_FIELDS.values():
            setattr(self, field, None)
        # Numeric fields start at 0.0 so callers never do arithmetic on None.
        for numeric in ("ltp", "open", "high", "low", "close", "volume", "oi"):
            setattr(self, numeric, 0.0)
        self.updated_at: Optional[datetime] = None

    def update(self, message: dict) -> "Quote":
        for tick_key, attribute in TICK_FIELDS.items():
            if tick_key in message:
                value = message[tick_key]
                if attribute in {"ltp", "open", "high", "low", "close", "oi"}:
                    value = _to_float(value)
                setattr(self, attribute, value)
        self.updated_at = datetime.now()
        return self

    def as_dict(self) -> Dict[str, object]:
        return {name: getattr(self, name, None) for name in self.__slots__}


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class MarketFeed:
    """Owns the websocket and a thread-safe quote cache."""

    def __init__(
        self,
        api,
        logger: Optional[logging.Logger] = None,
        on_tick: Optional[Callable[[Quote, dict], None]] = None,
        on_order: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.api = api
        self.log = logger or logging.getLogger("class_vii.feed")
        self.on_tick = on_tick
        self.on_order = on_order
        self.quotes: Dict[str, Quote] = {}
        self._lock = threading.RLock()
        self._subscribed: List[Tuple[str, str]] = []
        self._is_open = False
        self._stop = threading.Event()

    # -- lifecycle ------------------------------------------------------- #
    @property
    def is_open(self) -> bool:
        return self._is_open

    def start(self, timeout: float = 20.0) -> "MarketFeed":
        self._stop.clear()
        self.api.start_websocket(
            subscribe_callback=self._handle_tick,
            order_update_callback=self._handle_order,
            socket_open_callback=self._handle_open,
            socket_close_callback=self._handle_close,
        )
        deadline = time.time() + timeout
        while not self._is_open and time.time() < deadline:
            time.sleep(0.1)
        if not self._is_open:
            raise TimeoutError(f"websocket did not open within {timeout}s")
        self.log.info("websocket open")
        return self

    def stop(self) -> None:
        self._stop.set()
        close = getattr(self.api, "close_websocket", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("close failed: %s", exc)
        self._is_open = False

    def wait(self) -> None:
        """Block until :meth:`stop` is called - keeps ``main`` alive."""
        self._stop.wait()

    # -- subscriptions --------------------------------------------------- #
    def subscribe(self, symbols: Iterable[str], exchange: Optional[str] = None,
                  master: Optional[InstrumentMaster] = None) -> List[str]:
        """Subscribe valid instruments; skip bad symbols and isolate failed batches.

        Shoonya NorenApi expects strings like ["NSE|2885"]. For compatibility
        with older callers, dictionary items containing exchange/token are
        normalised to that string format before they reach the API.
        """
        instruments: List[str] = []
        seen = set()
        total_input = 0

        for raw in symbols:
            total_input += 1
            try:
                if isinstance(raw, dict):
                    exch = raw.get("exchange") or raw.get("e") or exchange
                    rest = (raw.get("token") or raw.get("tk") or
                            raw.get("tradingsymbol") or raw.get("symbol") or "")
                    if not exch:
                        self.log.warning("skipping symbol without exchange: %s", raw)
                        continue
                else:
                    exch, rest = parse_symbol(raw) if exchange is None else (exchange, raw)

                exch = str(exch).strip().upper()
                rest = str(rest).strip()
                if not exch or not rest:
                    self.log.warning("skipping empty instrument: %s", raw)
                    continue

                token = rest if rest.isdigit() else None
                if token is None and master is not None:
                    token = master.token_for(exch, rest)
                if not token:
                    self.log.warning("skipping unresolved symbol %s:%s", exch, rest)
                    continue

                key = f"{exch}|{token}"
                if key in seen:
                    continue
                seen.add(key)
                instruments.append(key)
                with self._lock:
                    self.quotes.setdefault(key, Quote())
            except Exception as exc:  # noqa: BLE001 - skip malformed row, keep processing
                self.log.warning("skipping invalid instrument %r: %s", raw, exc)

        subscribed: List[str] = []
        failed: List[str] = []
        for chunk in _chunks(instruments, 10):
            try:
                # Never pass dictionaries to NorenApi.subscribe().
                self.api.subscribe(list(chunk))
                subscribed.extend(chunk)
                self._subscribed.extend(
                    tuple(item.split("|", 1)) for item in chunk
                    if tuple(item.split("|", 1)) not in self._subscribed
                )
            except Exception as exc:  # noqa: BLE001 - continue with later batches
                failed.extend(chunk)
                self.log.error("subscription batch skipped (%s instruments): %s", len(chunk), exc)
            time.sleep(0.1)

        self.log.info(
            "websocket subscriptions: %s succeeded, %s failed, %s unresolved/skipped",
            len(subscribed), len(failed), total_input - len(instruments)
        )
        return subscribed

    def unsubscribe_all(self) -> None:
        pairs = list(self._subscribed)
        self._subscribed.clear()
        for chunk in _chunks(pairs, 10):
            payload = [f"{exch}|{token}" for exch, token in chunk]
            try:
                self.api.unsubscribe(payload)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("unsubscribe failed: %s", exc)

    def get_quote(self, key: str) -> Optional[Quote]:
        with self._lock:
            return self.quotes.get(key)

    def ltp(self, key: str) -> float:
        quote = self.get_quote(key)
        return quote.ltp if quote else 0.0

    # -- callbacks ------------------------------------------------------- #
    def _handle_open(self) -> None:
        self._is_open = True
        # Shoonya drops subscriptions on reconnect - replay them.
        if self._subscribed:
            self.log.info("re-subscribing %s instruments", len(self._subscribed))
            payload = [f"{e}|{t}" for e, t in self._subscribed]
            for chunk in _chunks(payload, 10):
                try:
                    self.api.subscribe(chunk)
                except Exception as exc:  # noqa: BLE001
                    self.log.error("re-subscribe failed: %s", exc)

    def _handle_close(self) -> None:
        self._is_open = False
        self.log.warning("websocket closed")

    def _handle_order(self, order: dict) -> None:
        self.log.info("order update: %s", order)
        if self.on_order:
            self.on_order(order)

    def _handle_tick(self, message) -> None:
        try:
            if isinstance(message, str):
                message = json.loads(message)
            exchange = str(message.get("e", ""))
            token = str(message.get("tk", ""))
            if not exchange or not token:
                return
            key = f"{exchange}|{token}"
            with self._lock:
                quote = self.quotes.get(key) or Quote()
                quote.update(message)
                self.quotes[key] = quote
            if self.on_tick:
                self.on_tick(quote, message)
        except Exception as exc:  # noqa: BLE001 - one bad tick must not kill the feed
            self.log.debug("tick handling failed: %s", exc)


def _chunks(items: List, size: int) -> List[List]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class HistoricalData:
    """Daily OHLCV + indicators, cached per symbol (repo 2's fetcher)."""

    def __init__(
        self,
        api,
        scanner_config: S.ScannerConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.api = api
        self.config = scanner_config
        self.log = logger or logging.getLogger("class_vii.history")
        self.cache: Dict[str, dict] = {}

    def load(self, symbol: str, exchange: Optional[str] = None) -> Optional[dict]:
        exchange = (exchange or self.config.exchange).upper()
        try:
            frame = self.fetch_frame(symbol, exchange)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("history failed for %s: %s", symbol, exc)
            return None
        if frame is None or len(frame) < self.config.bb_length:
            self.log.warning("not enough history for %s", symbol)
            return None

        frame = indicators.add_indicators(
            frame,
            rsi_length=self.config.rsi_length,
            stoch_length=self.config.stoch_length,
            sma_stoch_length=self.config.sma_stoch_length,
            bb_length=self.config.bb_length,
            bb_std=self.config.bb_std,
        )
        frame = indicators.add_signals(
            frame, self.config.stoch_lower, self.config.stoch_upper
        )
        payload = {
            "frame": frame,
            "closes": frame["close"].tolist(),
            "snapshot": indicators.snapshot(frame),
            "last_date": frame["datetime"].iloc[-1],
        }
        self.cache[symbol] = payload
        return payload

    def fetch_frame(self, symbol: str, exchange: str = "NSE") -> Optional[pd.DataFrame]:
        end = datetime.now()
        start = end - timedelta(days=self.config.load_days)
        response = self.api.get_daily_price_series(
            exchange=exchange,
            tradingsymbol=symbol,
            startdate=str(int(start.timestamp())),
            enddate=str(int(end.timestamp())),
        )
        if not response:
            return None

        rows = []
        for item in response:
            rows.append(json.loads(item) if isinstance(item, str) else item)
        if not rows:
            return None

        frame = pd.DataFrame(rows).rename(
            columns={
                "time": "datetime",
                "into": "open",
                "inth": "high",
                "intl": "low",
                "intc": "close",
                "intv": "volume",
            }
        )
        frame["datetime"] = pd.to_datetime(
            frame["datetime"], format="%d-%b-%Y", errors="coerce"
        )
        for column in ("open", "high", "low", "close", "volume"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = (
            frame.dropna(subset=["datetime", "close"])
            .sort_values("datetime")
            .reset_index(drop=True)
        )
        return frame
