"""Excel trade terminal: the workbook-driven side of repo 1, rebuilt.

Instead of 700 lines of nested ``if`` blocks reading fixed cells, the terminal
reads a table of ``Symbol | Qty | BUY/SELL | Target | SL | Trail`` rows from the
``Trade_Terminal`` sheet, pushes them into :class:`PositionBook`, and writes the
status/PnL back on a timer. Sheet names are the same as the original workbook so
an existing ``Finvasia_Trade_Terminal_v3.xlsm`` can be reused.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import pandas as pd

from . import settings as S
from .excel_bridge import ExcelBridge
from .instruments import parse_symbol
from .positions import PositionBook

TERMINAL_SHEET = "Trade_Terminal"
ORDERBOOK_SHEET = "OrderBook"
POSITION_SHEET = "OpenPosition"

# 1-based offsets from column A on the Trade_Terminal sheet.
COLUMNS = {
    "symbol": 1,
    "qty": 13,
    "action": 14,
    "entry_signal": 15,
    "exit_signal": 20,
    "target": 24,
    "stop_loss": 25,
    "trail": 26,
    "latest_stop": 27,
    "status": 28,
    "pnl": 29,
}

HEADER_ROW = 3
FIRST_ROW = 4
LAST_ROW = 1000


class ExcelTerminal:
    """Bridges the workbook and :class:`PositionBook`."""

    def __init__(
        self,
        book: ExcelBridge,
        positions: PositionBook,
        trading: S.TradingConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.book = book
        self.positions = positions
        self.config = trading
        self.log = logger or logging.getLogger("class_vii.terminal")
        self.sheet = None

    # ------------------------------------------------------------------ #
    def attach(self) -> "ExcelTerminal":
        self.sheet = self.book.sheet(TERMINAL_SHEET)
        return self

    # ------------------------------------------------------------------ #
    def read_rows(self) -> List[dict]:
        if self.sheet is None:
            return []
        wanted_rows = LAST_ROW - FIRST_ROW + 1
        rows: List[dict] = []
        for offset in range(wanted_rows):
            row = FIRST_ROW + offset
            symbol = self.book.read_range(self.sheet, f"A{row}")
            if not symbol:
                continue
            try:
                exchange, tradingsymbol = parse_symbol(str(symbol))
            except ValueError:
                continue
            row_data = {"row": row, "exchange": exchange, "symbol": tradingsymbol}
            for name, column in COLUMNS.items():
                if name == "symbol":
                    continue
                row_data[name] = self.book.read_range(
                    self.sheet, f"{_letter(column)}{row}"
                )
            rows.append(row_data)
        return rows

    def sync(self) -> int:
        """Read the sheet into the position book. Returns rows processed."""
        rows = self.read_rows()
        for row in rows:
            qty = _as_int(row.get("qty"), self.config.quantity)
            entry_signal = _as_signal(row.get("entry_signal")) or _as_signal(row.get("action")) == "BUY"
            exit_signal = _as_signal(row.get("exit_signal")) == "SELL"
            position = self.positions.upsert(
                symbol=row["symbol"],
                exchange=row["exchange"],
                qty=qty,
                side="BUY",
                entry_signal=entry_signal,
                exit_signal=exit_signal,
                target=_as_float(row.get("target")),
                stop_loss=_as_float(row.get("stop_loss")),
            )
            if position.trail is not None and row.get("trail") is not None:
                position.trail.enabled = str(row.get("trail")).strip().upper() in {
                    "TRUE", "YES", "1", "Y"
                }
        return len(rows)

    # ------------------------------------------------------------------ #
    def publish(self) -> int:
        """Write status/PnL back to the sheet, one bulk write per block."""
        if self.sheet is None:
            return 0
        updates = []
        for key, position in self.positions.positions.items():
            updates.append(
                {
                    "row": None,
                    "status": position.state.value,
                    "latest_stop": round(position.latest_stop, 2),
                    "pnl": round(position.pnl, 2),
                    "symbol": f"{position.exchange}:{position.symbol}",
                }
            )
        if not updates:
            return 0

        rows = self.read_rows()
        index = {row["symbol"]: row["row"] for row in rows}
        for update in updates:
            exchange, symbol = update["symbol"].split(":", 1)
            row = index.get(symbol)
            if not row:
                continue
            self.book.write(self.sheet, f"{_letter(COLUMNS['status'])}{row}", update["status"])
            self.book.write(self.sheet, f"{_letter(COLUMNS['latest_stop'])}{row}", update["latest_stop"])
            self.book.write(self.sheet, f"{_letter(COLUMNS['pnl'])}{row}", update["pnl"])
        return len(rows)

    # ------------------------------------------------------------------ #
    def publish_orderbook(self, gateway) -> int:
        sheet = self.book.sheet(ORDERBOOK_SHEET, header=[
            "Order No", "Symbol", "Qty", "Type", "Status", "Price",
        ])
        orders = gateway.order_book()
        written = 0
        for offset, order in enumerate(orders[:200]):
            written += self.book.write_block(
                sheet,
                2 + offset,
                "A",
                [[
                    order.get("norenordno", order.get("order_id", "")),
                    order.get("tsym", order.get("tradingsymbol", "")),
                    order.get("qty", order.get("quantity", "")),
                    order.get("trantype", order.get("side", "")),
                    order.get("status", order.get("ordstatus", "")),
                    order.get("prc", order.get("price", "")),
                ]],
            )
        return written

    def publish_positions(self) -> int:
        sheet = self.book.sheet(POSITION_SHEET)
        frame = self.positions.to_frame()
        self.book.write_row(sheet, 1, list(frame.columns))
        return self.book.write_block(sheet, 2, "A", frame.itertuples(index=False, name=None))

    # ------------------------------------------------------------------ #
    def loop(self, gateway, interval: Optional[float] = None) -> None:
        interval = interval or self.config.poll_interval
        while True:
            try:
                self.sync()
                self.publish()
                self.publish_orderbook(gateway)
                self.publish_positions()
                self.book.save()
            except KeyboardInterrupt:
                self.log.info("terminal stopped by user")
                return
            except Exception as exc:  # noqa: BLE001 - workbook calls are fragile
                self.log.exception("terminal cycle failed: %s", exc)
            time.sleep(interval)


# --------------------------------------------------------------------------- #
def _letter(column: int) -> str:
    from .excel_bridge import column_letter

    return column_letter(column)


def _as_float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number else None


def _as_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_signal(value) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "B", "1", "TRUE"}:
        return "BUY"
    if text in {"SELL", "S", "EXIT"}:
        return "SELL"
    return ""
