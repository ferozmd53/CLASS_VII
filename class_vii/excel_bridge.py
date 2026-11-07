"""Excel bridge (xlwings).

The two upstream repos treat Excel very differently:

* repo 1 hard-codes cell addresses all over a 3 000-line file
  (``tt.range("a2:d2")``, ``range(f"Ab{idx+2}")``, ``s2``, ``P2`` ...).
* repo 2 computes column letters at import time and rewrites whole blocks.

This module keeps the useful parts - bulk write, fast range round-trips and a
named-column layout - behind one small class. Nothing here touches the broker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

# --------------------------------------------------------------------------- #
# Column helpers (from repo 2, kept because they are correct and useful)
# --------------------------------------------------------------------------- #
def column_letter(index: int) -> str:
    """1 -> 'A', 27 -> 'AA'."""
    if index < 1:
        raise ValueError("column index is 1-based")
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def column_number(letter: str) -> int:
    """'A' -> 1, 'AA' -> 27."""
    total = 0
    for char in str(letter).strip().upper():
        if not char.isalpha():
            raise ValueError(f"bad column letter: {letter!r}")
        total = total * 26 + (ord(char) - ord("A") + 1)
    return total


def column_span(start: str, count: int) -> List[str]:
    base = column_number(start)
    return [column_letter(base + offset) for offset in range(count)]


# --------------------------------------------------------------------------- #
# Layouts
# --------------------------------------------------------------------------- #
LIVE_HEADERS = [
    "LTP", "Open", "High", "Low", "Close", "Volume",
    "RSI", "StochRSI", "SMA Stoch",
    "BB Upper", "BB Middle", "BB Lower",
    "BUY", "SELL", "Signal", "Last Update",
]

HISTORY_HEADERS = [
    "Date", "Open", "High", "Low", "Close", "Volume",
    "RSI", "StochRSI", "SMA Stoch",
    "BB Upper", "BB Middle", "BB Lower",
    "Signal",
]

ORDER_HEADERS = ["Quantity", "BUY Trigger", "BUY Status", "SELL Trigger", "SELL Status"]


@dataclass
class SheetLayout:
    symbol_column: str = "A"
    live_start: str = "B"
    order_start: str = "R"
    history_start: str = "W"

    def range_for(self, start: str, count: int, row: int) -> str:
        columns = column_span(start, count)
        return f"{columns[0]}{row}:{columns[-1]}{row}"

    def cell(self, start: str, offset: int, row: int) -> str:
        return f"{column_letter(column_number(start) + offset)}{row}"


class ExcelBridge:
    """Thin, testable wrapper over an xlwings workbook.

    ``visible=False`` and a headless fallback keep imports working on machines
    where xlwings (and Excel) are unavailable - the trading logic can then run
    purely in-memory.
    """

    def __init__(
        self,
        path: str,
        logger: Optional[logging.Logger] = None,
        visible: bool = False,
        create: bool = True,
    ) -> None:
        self.path = path
        self.log = logger or logging.getLogger("class_vii.excel")
        self.visible = visible
        self._book = None
        self._xw = None
        self._create = create

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return self._book is not None

    def open(self) -> "ExcelBridge":
        if self._book is not None:
            return self
        try:
            import xlwings as xw
        except ImportError as exc:
            raise RuntimeError(
                "xlwings is not installed (pip install xlwings) - Excel output "
                "is unavailable; run with --no-excel instead"
            ) from exc

        self._xw = xw
        try:
            self._book = xw.Book(self.path)
        except Exception:
            if not self._create:
                raise
            self.log.warning("%s not found - creating a new workbook", self.path)
            self._book = xw.Book()
            self._book.save(self.path)
        self._book.app.visible = self.visible
        return self

    def close(self, save: bool = True) -> None:
        if self._book is None:
            return
        try:
            if save:
                self._book.save()
            self._book.close()
        except Exception as exc:  # noqa: BLE001
            self.log.debug("close failed: %s", exc)
        finally:
            self._book = None

    # ------------------------------------------------------------------ #
    def sheet(self, name: str, header: Optional[Sequence[str]] = None):
        if self._book is None:
            self.open()
        names = [s.name for s in self._book.sheets]
        if name not in names:
            sheet = self._book.sheets.add(name, after=self._book.sheets[-1])
            self.log.info("created sheet %s", name)
        else:
            sheet = self._book.sheets[name]
        if header:
            self.write_row(sheet, 1, list(header))
        return sheet

    # ------------------------------------------------------------------ #
    @staticmethod
    def read_range(sheet, address: str):
        return sheet.range(address).value

    @staticmethod
    def write(sheet, address: str, value) -> None:
        sheet.range(address).value = value

    @staticmethod
    def write_row(sheet, row: int, values: Sequence) -> None:
        if not values:
            return
        start = f"A{row}"
        end = f"{column_letter(len(values))}{row}"
        sheet.range(f"{start}:{end}").value = list(values)

    def write_block(self, sheet, first_row: int, first_col: str, rows: Iterable[Sequence]) -> int:
        """Write a 2-D block in ONE call - the fast path repo 2 used."""
        matrix = [list(row) for row in rows]
        if not matrix:
            return 0
        width = max(len(row) for row in matrix)
        for row in matrix:
            row.extend([""] * (width - len(row)))
        start_col = column_number(first_col)
        address = (
            f"{column_letter(start_col)}{first_row}:"
            f"{column_letter(start_col + width - 1)}{first_row + len(matrix) - 1}"
        )
        sheet.range(address).value = matrix
        return len(matrix)

    def write_frame(self, sheet, frame: pd.DataFrame, first_row: int = 1, first_col: str = "A",
                    include_header: bool = True) -> int:
        rows: List[List] = []
        if include_header:
            rows.append(list(frame.columns))
        for record in frame.itertuples(index=False, name=None):
            rows.append(["" if pd.isna(value) else value for value in record])
        return self.write_block(sheet, first_row, first_col, rows)

    # ------------------------------------------------------------------ #
    def save(self) -> None:
        if self._book is not None:
            try:
                self._book.save()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("save failed: %s", exc)
