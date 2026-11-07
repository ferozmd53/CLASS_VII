"""Position book: entry/exit, target, stop-loss and trailing stop.

repo 1 did this with ~700 lines of nested ``if trade_info[15] == ...`` blocks
inside ``start_Trade_Terminal``, driven by the string in column ``Ab``. This is
the same behaviour expressed as a small state machine with explicit states:

    IDLE -> ENTRY_PENDING -> ACTIVE -> EXIT_PENDING -> CLOSED

Nothing here touches Excel or the broker, so the rules can be unit-tested.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from . import settings as S

logger = logging.getLogger("class_vii.position")


class State(str, Enum):
    IDLE = "Idle"
    ENTRY_PENDING = "Entry_Pending"
    ACTIVE = "Active"
    EXIT_PENDING = "Exit_Pending"
    CLOSED = "Closed"


@dataclass
class TrailingStop:
    """Trail the stop in ``step`` increments once price runs in our favour."""

    step_pct: float = 0.2
    enabled: bool = True

    def next_stop(self, side: str, entry: float, stop: float, ltp: float) -> float:
        if not self.enabled or entry <= 0 or ltp <= 0:
            return stop
        step = entry * self.step_pct / 100.0
        if step <= 0:
            return stop
        if side.upper() == "BUY":
            moved = (ltp - entry) / step
            if moved >= 1:
                candidate = entry + (int(moved) - 1) * step
                return max(stop, round(candidate, 2))
        else:
            moved = (entry - ltp) / step
            if moved >= 1:
                candidate = entry - (int(moved) - 1) * step
                return min(stop, round(candidate, 2))
        return stop


@dataclass
class Position:
    symbol: str
    exchange: str = "NSE"
    qty: int = 1
    state: State = State.IDLE
    side: str = "BUY"
    entry_signal: bool = False
    exit_signal: bool = False
    entry_price: float = 0.0
    exit_price: float = 0.0
    entry_order_id: str = ""
    exit_order_id: str = ""
    target: float = 0.0
    stop_loss: float = 0.0
    latest_stop: float = 0.0
    remarks: str = ""
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    trail: TrailingStop = field(default_factory=TrailingStop)

    # -- derived --------------------------------------------------------- #
    @property
    def is_open(self) -> bool:
        return self.state in (State.ENTRY_PENDING, State.ACTIVE, State.EXIT_PENDING)

    @property
    def pnl(self) -> float:
        if self.state is not State.CLOSED or not self.entry_price or not self.exit_price:
            return 0.0
        sign = 1 if self.side.upper() == "BUY" else -1
        return round((self.exit_price - self.entry_price) * self.qty * sign, 2)

    def row(self) -> List:
        return [
            f"{self.exchange}:{self.symbol}", self.side, self.qty,
            round(self.entry_price, 2), round(self.exit_price, 2),
            round(self.target, 2), round(self.latest_stop, 2),
            self.state.value, self.entry_order_id, self.exit_order_id,
            round(self.pnl, 2), self.remarks,
        ]

    @staticmethod
    def headers() -> List[str]:
        return [
            "Symbol", "Side", "Qty", "Entry", "Exit", "Target", "Latest SL",
            "Status", "Entry Order", "Exit Order", "PnL", "Remarks",
        ]


class PositionBook:
    """Tracks every symbol and decides what to do on each tick."""

    def __init__(
        self,
        gateway,
        trading: S.TradingConfig,
        alerts=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.gateway = gateway
        self.config = trading
        self.alerts = alerts
        self.log = logger or logging.getLogger("class_vii.position")
        self.positions: Dict[str, Position] = {}
        self.closed: List[Position] = []

    # ------------------------------------------------------------------ #
    def upsert(
        self,
        symbol: str,
        exchange: str = "NSE",
        qty: Optional[int] = None,
        side: str = "BUY",
        entry_signal: bool = False,
        exit_signal: bool = False,
        target: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Position:
        key = f"{exchange}:{symbol}"
        position = self.positions.get(key)
        if position is None:
            position = Position(symbol=symbol, exchange=exchange, qty=qty or self.config.quantity)
            position.trail = TrailingStop(self.config.trail_step_pct, self.config.trail_enabled)
            self.positions[key] = position
        if qty:
            position.qty = int(qty)
        position.side = "BUY" if str(side).upper().startswith("B") else "SELL"
        position.entry_signal = bool(entry_signal)
        position.exit_signal = bool(exit_signal)
        if target:
            position.target = float(target)
        if stop_loss:
            position.stop_loss = float(stop_loss)
        return position

    # ------------------------------------------------------------------ #
    def on_tick(self, key: str, ltp: float) -> Optional[str]:
        """Advance one position. Returns the action taken, for logging/tests."""
        if key not in self.positions or ltp <= 0:
            return None
        position = self.positions[key]

        if position.state is State.IDLE and position.entry_signal:
            return self._enter(position, ltp)

        if position.state is State.ACTIVE:
            return self._manage(position, ltp)

        if position.state is State.ACTIVE or position.state is State.ENTRY_PENDING:
            if position.exit_signal:
                return self._exit(position, ltp)
        return None

    def _enter(self, position: Position, ltp: float) -> str:
        position.state = State.ENTRY_PENDING
        result = self.gateway.place(
            position.exchange, position.symbol, position.qty, position.side, ltp=ltp
        )
        if not result.ok:
            position.state = State.IDLE
            position.remarks = result.message[:120]
            self._notify(f"Entry failed for {position.symbol}: {result.message}")
            return "entry_failed"

        position.entry_order_id = result.order_id
        position.entry_price = result.price or ltp
        position.state = State.ACTIVE
        position.opened_at = datetime.now()
        self._apply_targets(position, position.entry_price)
        self._notify(
            f"ENTRY {position.side} {position.symbol} x{position.qty} @ "
            f"{position.entry_price:.2f} (order {result.order_id})"
        )
        return "entered"

    @staticmethod
    def _apply_targets(position: Position, price: float) -> None:
        if not position.target:
            position.target = round(price * 1.01, 2)
        if not position.stop_loss:
            position.stop_loss = round(price * 0.995, 2)
        if not position.latest_stop:
            position.latest_stop = position.stop_loss

    def _manage(self, position: Position, ltp: float) -> Optional[str]:
        position.latest_stop = position.trail.next_stop(
            position.side, position.entry_price, position.latest_stop, ltp
        )

        if position.exit_signal:
            return self._exit(position, ltp)

        if position.side == "BUY":
            if position.latest_stop and ltp <= position.latest_stop:
                return self._exit(position, ltp, reason="stop-loss")
            if position.target and ltp >= position.target:
                return self._exit(position, ltp, reason="target")
        else:
            if position.latest_stop and ltp >= position.latest_stop:
                return self._exit(position, ltp, reason="stop-loss")
            if position.target and ltp <= position.target:
                return self._exit(position, ltp, reason="target")
        return None

    def _exit(self, position: Position, ltp: float, reason: str = "signal") -> str:
        position.state = State.EXIT_PENDING
        side = "S" if position.side == "BUY" else "B"
        result = self.gateway.place(
            position.exchange, position.symbol, position.qty, side, ltp=ltp,
            remarks=f"class_vii_exit_{reason}",
        )
        if not result.ok:
            position.state = State.ACTIVE
            self._notify(f"Exit failed for {position.symbol}: {result.message}")
            return "exit_failed"

        position.exit_order_id = result.order_id
        position.exit_price = result.price or ltp
        position.state = State.CLOSED
        position.closed_at = datetime.now()
        position.remarks = reason
        self.closed.append(position)
        self._notify(
            f"EXIT {position.symbol} x{position.qty} @ {position.exit_price:.2f} "
            f"[{reason}] PnL {position.pnl:+.2f}"
        )
        return "exited"

    # ------------------------------------------------------------------ #
    def close_all(self) -> int:
        count = 0
        for position in list(self.positions.values()):
            if position.is_open and position.entry_price:
                if self._exit(position, position.entry_price, reason="kill-switch") == "exited":
                    count += 1
        return count

    def open_positions(self) -> List[Position]:
        return [p for p in self.positions.values() if p.is_open]

    def to_frame(self):
        import pandas as pd

        rows = [p.row() for p in self.positions.values()]
        return pd.DataFrame(rows, columns=Position.headers())

    def _notify(self, message: str) -> None:
        self.log.info(message)
        if self.alerts:
            self.alerts.notify(message)
