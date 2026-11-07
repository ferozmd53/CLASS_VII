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
        self.live_sheet = None

    # ------------------------------------------------------------------ #
    def attach(self) -> "ExcelTerminal":
        """Attach to the user's workbook without replacing or rearranging it.

        Trade_Terminal, OrderBook, OpenPosition and Holdings are existing
        operational sheets and must already be present. Output-only sheets
        are added only when absent. An existing legacy "Live" sheet is reused
        for live indicators rather than creating a duplicate tab.
        """
        names = [s.name for s in self.book._book.sheets]
        required = (TERMINAL_SHEET, ORDERBOOK_SHEET, POSITION_SHEET, "Holdings")
        missing = [name for name in required if name not in names]
        if missing:
            raise KeyError(
                "Workbook is missing required trading sheets: " + ", ".join(missing)
                + ". Existing sheets were not changed."
            )

        # Create only missing output tabs; never clear, rename or replace
        # any existing worksheet, formulas, macros or credential cells.
        for name in ("BrokerPositions", "Funds", "Alerts"):
            if name not in names:
                self.book._book.sheets.add(name, after=self.book._book.sheets[-1])
                names.append(name)
                self.log.info("created missing output worksheet: %s", name)

        if "LiveIndicators" in names:
            self.live_sheet = self.book.sheet("LiveIndicators")
        elif "Live" in names:
            self.live_sheet = self.book.sheet("Live")
            self.log.info("using existing Live worksheet for live indicator output")
        else:
            self.live_sheet = self.book._book.sheets.add(
                "LiveIndicators", after=self.book._book.sheets[-1]
            )
            self.log.info("created missing output worksheet: LiveIndicators")

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
        """Broker-only order view; paper orders are deliberately excluded."""
        sheet = self.book.sheet(ORDERBOOK_SHEET)
        headers = ["Order No", "Exchange", "Symbol", "Side", "Product", "Qty",
                   "Filled Qty", "Pending Qty", "Order Type", "Price", "Trigger Price",
                   "Average Price", "Status", "Remarks", "Order Time"]
        self.book.write_row(sheet, 1, headers)
        sheet.range("A2:O1001").clear_contents()
        rows = []
        for order in gateway.order_book()[:1000]:
            rows.append([
                order.get("norenordno", order.get("order_id", "")),
                order.get("exch", order.get("exchange", "")),
                order.get("tsym", order.get("tradingsymbol", "")),
                order.get("trantype", order.get("side", "")),
                order.get("prd", order.get("product", "")),
                order.get("qty", order.get("quantity", "")),
                order.get("fillshares", order.get("filled_qty", "")),
                order.get("unfilledshares", order.get("pending_qty", "")),
                order.get("prctyp", order.get("order_type", "")),
                order.get("prc", order.get("price", "")),
                order.get("trgprc", order.get("trigger_price", "")),
                order.get("avgprc", order.get("average_price", "")),
                order.get("status", order.get("ordstatus", "")),
                order.get("remarks", ""),
                order.get("norentm", order.get("order_time", "")),
            ])
        if rows:
            return self.book.write_block(sheet, 2, "A", rows)
        sheet.range("A2").value = "No broker orders returned"
        return 0

    def publish_positions(self, gateway=None) -> int:
        sheet = self.book.sheet(POSITION_SHEET)
        frame = self.positions.to_frame()
        self.book.write_row(sheet, 1, list(frame.columns))
        count = self.book.write_block(sheet, 2, "A", frame.itertuples(index=False, name=None))
        if gateway is not None:
            broker = self.book.sheet("BrokerPositions")
            self.book.write_row(broker, 1, ["Exchange", "Symbol", "Net Qty", "Buy Qty",
                                           "Sell Qty", "Average Price", "LTP", "P&L", "Product"])
            broker.range("A2:I1001").clear_contents()
            rows = [[p.get("exch", ""), p.get("tsym", ""), p.get("netqty", ""),
                     p.get("daybuyqty", ""), p.get("daysellqty", ""),
                     p.get("netavgprc", p.get("buyavgprc", "")), p.get("lp", ""),
                     p.get("urmtom", p.get("rpnl", "")), p.get("prd", "")]
                    for p in gateway.positions()[:1000]]
            self.book.write_block(broker, 2, "A", rows)
        return count

    def publish_account_snapshots(self, gateway) -> None:
        """Refresh read-only broker holdings and funds sheets."""
        holdings = self.book.sheet("Holdings")
        self.book.write_row(holdings, 1, ["Exchange", "Symbol", "Quantity", "Average Price", "LTP", "P&L"])
        holdings.range("A2:F1001").clear_contents()
        rows = [[h.get("exch", ""), h.get("tsym", ""), h.get("holdqty", h.get("quantity", "")),
                 h.get("upldprc", h.get("avgprc", "")), h.get("lp", ""),
                 h.get("urmtom", h.get("rpnl", ""))] for h in gateway.holdings()[:1000]]
        self.book.write_block(holdings, 2, "A", rows)
        funds = self.book.sheet("Funds")
        self.book.write_row(funds, 1, ["Limit", "Value"])
        funds.range("A2:B1001").clear_contents()
        limits = gateway.limits()
        fund_rows = []
        for key, value in (limits or {}).items():
            if isinstance(value, dict):
                fund_rows.extend([[f"{key}.{subkey}", subvalue] for subkey, subvalue in value.items()])
            else:
                fund_rows.append([key, value])
        self.book.write_block(funds, 2, "A", fund_rows)

    # ------------------------------------------------------------------ #
    def loop(self, gateway, interval: Optional[float] = None, scanner=None) -> None:
        interval = interval or self.config.poll_interval
        last_feed_report = 0.0
        while True:
            try:
                self.sync()

                # Publish market data before broker REST snapshots. A slow or
                # failed order/position/holdings request must not block LTP.
                if scanner is not None:
                    live = self.live_sheet or self.book.sheet("LiveIndicators")
                    live.range("A1:Z1001").clear_contents()
                    self.book.write_frame(live, scanner.frame(), first_row=1, first_col="A")
                    now = time.monotonic()
                    if now - last_feed_report >= 15:
                        live_count = sum(
                            1 for value in scanner.snapshots.values()
                            if float(value.get("ltp", 0) or 0) > 0
                        )
                        self.log.info(
                            "live feed status: received_ticks=%s, tracked_symbols=%s, symbols_with_ltp=%s",
                            scanner.ticks, len(scanner.keys), live_count
                        )
                        last_feed_report = now

                self.publish()
                self.publish_orderbook(gateway)
                self.publish_positions(gateway)
                self.publish_account_snapshots(gateway)
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
