"""Order gateway: one place that talks to ``place_order`` and friends.

Merges repo 1's ``place_trade`` (SL-M padding, product mapping, logging) with
repo 2's ``place_order`` (algo_id, live-price offset) and adds a paper-trading
mode so the merged code is safe to run before going live.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from . import settings as S

PRODUCT_MAP = {"MIS": "I", "INTRADAY": "I", "CNC": "C", "DELIVERY": "C", "NRML": "M"}
EXCHANGE_DELIVERY = {"NSE", "BSE"}


def round_to_tick(price: float, tick: float = 0.05) -> float:
    if tick <= 0:
        return round(price, 2)
    return round(round(price / tick) * tick, 4)


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    status: str = "PLACED"
    paper: bool = False
    message: str = ""
    placed_at: datetime = field(default_factory=datetime.now)

    @property
    def ok(self) -> bool:
        return bool(self.order_id)


class OrderGateway:
    def __init__(
        self,
        api,
        trading: S.TradingConfig,
        logger: Optional[logging.Logger] = None,
        alerts=None,
        algo_id: str = "",
    ) -> None:
        self.api = api
        self.config = trading
        self.log = logger or logging.getLogger("class_vii.orders")
        self.alerts = alerts
        self.algo_id = algo_id
        self.paper_orders: Dict[str, OrderResult] = {}
        self._counter = 0

    # ------------------------------------------------------------------ #
    @property
    def paper(self) -> bool:
        return self.config.paper_trade

    def product_for(self, exchange: str) -> str:
        wanted = (self.config.product_type or "").upper()
        if wanted in PRODUCT_MAP:
            return PRODUCT_MAP[wanted]
        return "C" if exchange.upper() in EXCHANGE_DELIVERY else "M"

    def entry_price(self, ltp: float, side: str) -> float:
        """Marketable limit price: cross the spread by a small buffer."""
        if ltp <= 0:
            raise ValueError("cannot price an order without a live LTP")
        buffer_pct = abs(float(self.config.price_buffer_pct or 0.0))
        factor = 1 + buffer_pct / 100.0 if side.upper().startswith("B") else 1 - buffer_pct / 100.0
        return round_to_tick(ltp * factor, self.config.tick_size)

    # ------------------------------------------------------------------ #
    def place(
        self,
        exchange: str,
        symbol: str,
        qty: int,
        side: str,
        price: Optional[float] = None,
        order_type: Optional[str] = None,
        ltp: float = 0.0,
        remarks: str = "class_vii",
    ) -> OrderResult:
        side = "B" if str(side).upper().startswith("B") else "S"
        order_type = (order_type or self.config.order_type or "LIMIT").upper()
        exchange = exchange.upper()

        try:
            if price is None or price <= 0:
                price = self.entry_price(ltp, side) if ltp > 0 else 0.0
        except ValueError as exc:
            return OrderResult("", symbol, side, qty, 0.0, "REJECTED", self.paper, str(exc))

        price_type, trigger_price, send_price = self._price_type(order_type, price, side, exchange)

        if self.paper:
            self._counter += 1
            order_id = f"PAPER{self._counter:06d}"
            result = OrderResult(order_id, symbol, side, qty, send_price, "FILLED", True,
                                 "paper fill")
            self.paper_orders[order_id] = result
            self.log.info("[PAPER] %s %s x%s @ %s (%s)", side, symbol, qty, send_price, order_id)
            self._notify(f"[PAPER] {'BUY' if side == 'B' else 'SELL'} {symbol} "
                         f"qty {qty} @ {send_price}{' SL' if trigger_price else ''}")
            return result

        payload = {
            "buy_or_sell": side,
            "product_type": self.product_for(exchange),
            "exchange": exchange,
            "tradingsymbol": symbol,
            "quantity": int(qty),
            "discloseqty": 0,
            "price_type": price_type,
            "price": send_price,
            "trigger_price": trigger_price,
            "retention": "DAY",
            "remarks": remarks,
        }
        if self.algo_id:
            payload["algo_id"] = self.algo_id

        self.log.info("placing order: %s", payload)
        try:
            response = self.api.place_order(**payload) or {}
        except Exception as exc:  # noqa: BLE001
            self.log.error("place_order raised: %s", exc)
            return OrderResult("", symbol, side, qty, send_price, "ERROR", False, str(exc))

        order_id = str(response.get("norenordno") or "")
        if not order_id:
            message = str(response.get("emsg") or response)
            self.log.error("order rejected: %s", message)
            return OrderResult("", symbol, side, qty, send_price, "REJECTED", False, message)

        self.log.info("order accepted: %s", order_id)
        self._notify(f"{'BUY' if side == 'B' else 'SELL'} {symbol} qty {qty} "
                     f"@ {send_price} order {order_id}")
        return OrderResult(order_id, symbol, side, qty, send_price, "PLACED", False)

    def _price_type(self, order_type: str, price: float, side: str, exchange: str):
        """Translate MARKET / LIMIT / SL-M into Shoonya price_type + trigger."""
        if order_type == "MARKET":
            return "MKT", None, 0.0
        if order_type == "SL-M":
            buffer_pct = abs(float(self.config.price_buffer_pct or 0.0))
            factor = 1 + buffer_pct / 100.0 if side == "B" else 1 - buffer_pct / 100.0
            pad = round_to_tick(price * factor, self.config.tick_size)
            return "SL-LMT", round_to_tick(price, self.config.tick_size), pad
        return "LMT", None, round_to_tick(price, self.config.tick_size)

    # ------------------------------------------------------------------ #
    def modify(self, order_id: str, price: Optional[float] = None,
               trigger_price: Optional[float] = None, qty: Optional[int] = None,
               price_type: str = "LMT") -> bool:
        if self.paper:
            self.log.info("[PAPER] modify %s -> price=%s trigger=%s", order_id, price, trigger_price)
            return True
        payload = {"orderno": order_id, "price_type": price_type}
        if price is not None:
            payload["price"] = price
        if trigger_price is not None:
            payload["trigger_price"] = trigger_price
        if qty is not None:
            payload["quantity"] = qty
        try:
            response = self.api.modify_order(**payload) or {}
        except Exception as exc:  # noqa: BLE001
            self.log.error("modify failed: %s", exc)
            return False
        return str(response.get("stat", "")).lower() == "ok"

    def cancel(self, order_id: str) -> bool:
        if self.paper:
            self.log.info("[PAPER] cancel %s", order_id)
            return True
        try:
            response = self.api.cancel_order(order_id=order_id) or {}
        except Exception as exc:  # noqa: BLE001
            self.log.error("cancel failed: %s", exc)
            return False
        return str(response.get("stat", "")).lower() == "ok"

    def exit_all(self) -> int:
        """Square off every open position - repo 1's KillSwitch, behind a flag."""
        closed = 0
        for position in self.positions():
            net_qty = int(float(position.get("netqty") or 0))
            if net_qty == 0:
                continue
            side = "S" if net_qty > 0 else "B"
            exchange = str(position.get("exch") or "NSE")
            symbol = str(position.get("tsym") or "")
            result = self.place(exchange, symbol, abs(net_qty), side,
                                order_type="MARKET", remarks="class_vii_exit_all")
            if result.ok:
                closed += 1
            time.sleep(0.35)  # broker order-rate limit
        if closed:
            self._notify(f"Kill switch: squared off {closed} position(s)")
        return closed

    # ------------------------------------------------------------------ #
    def order_book(self) -> List[dict]:
        if self.paper:
            return [vars(order) for order in self.paper_orders.values()]
        try:
            return self.api.get_order_book() or []
        except Exception as exc:  # noqa: BLE001
            self.log.warning("order book failed: %s", exc)
            return []

    def positions(self) -> List[dict]:
        if self.paper:
            return []
        try:
            return self.api.get_positions() or []
        except Exception as exc:  # noqa: BLE001
            self.log.warning("positions failed: %s", exc)
            return []

    def holdings(self) -> List[dict]:
        if self.paper:
            return []
        try:
            return self.api.get_holdings() or []
        except Exception as exc:  # noqa: BLE001
            self.log.warning("holdings failed: %s", exc)
            return []

    def limits(self) -> dict:
        if self.paper:
            return {}
        try:
            return self.api.get_limits() or {}
        except Exception as exc:  # noqa: BLE001
            self.log.warning("limits failed: %s", exc)
            return {}

    # ------------------------------------------------------------------ #
    def _notify(self, message: str) -> None:
        if self.alerts:
            self.alerts.notify(message)
        else:
            self.log.info(message)
