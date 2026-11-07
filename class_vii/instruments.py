"""Instrument-token master: download, cache and resolve symbols.

repo 1 re-downloaded every exchange dump on every start (2-3 minutes) and kept
six DataFrames in globals. Here the dumps are downloaded once per day, cached
as CSV, and exposed through a single lookup object.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import settings as S

CACHE_DIR = S.ROOT / "Instrument"

# Columns kept from the Shoonya dumps - the full files are much wider.
KEEP_COLUMNS = (
    "Exchange",
    "Token",
    "LotSize",
    "Symbol",
    "TradingSymbol",
    "Instrument",
    "OptionType",
    "StrikePrice",
    "TickSize",
    "Expiry",
)


def parse_symbol(raw: str) -> Tuple[str, str]:
    """Accept ``NSE:UPL-EQ``, ``NSE|2885`` or ``UPL-EQ``.

    Returns ``(exchange, tradingsymbol_or_token)``.
    """
    text = str(raw or "").strip().upper()
    if not text:
        raise ValueError("empty symbol")
    if ":" in text:
        exchange, rest = text.split(":", 1)
        return exchange.strip(), rest.strip()
    if "|" in text:
        exchange, rest = text.split("|", 1)
        return exchange.strip(), rest.strip()
    return "NSE", text


class InstrumentMaster:
    """Lazy, disk-cached instrument lookup."""

    def __init__(
        self,
        exchanges: Iterable[str] = ("NSE", "BSE", "NFO", "BFO", "CDS", "MCX"),
        logger: Optional[logging.Logger] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.exchanges = [e.upper() for e in exchanges]
        self.log = logger or logging.getLogger("class_vii.instruments")
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.rows: Dict[str, List[dict]] = {}

    # ------------------------------------------------------------------ #
    def load(self, force: bool = False) -> "InstrumentMaster":
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for exchange in self.exchanges:
            self.rows[exchange] = self._load_exchange(exchange, force)
            self.log.info("%s: %s instruments", exchange, len(self.rows[exchange]))
        return self

    def _load_exchange(self, exchange: str, force: bool) -> List[dict]:
        target = self.cache_dir / f"{exchange}_symbols.csv"
        if target.exists() and not force and self._is_fresh(target):
            return self._read_csv(target)

        try:
            records = self._download(exchange)
            self._write_csv(target, records)
            return records
        except Exception as exc:  # noqa: BLE001 - fall back to any stale cache
            self.log.warning("%s dump failed (%s)", exchange, exc)
            if target.exists():
                self.log.warning("%s: using stale cache", exchange)
                return self._read_csv(target)
            return []

    @staticmethod
    def _is_fresh(path: Path) -> bool:
        return date.fromtimestamp(path.stat().st_mtime) >= date.today()

    def _download(self, exchange: str) -> List[dict]:
        import requests

        url = f"{S.INSTRUMENT_BASE}{exchange}_symbols.txt.zip"
        self.log.info("downloading %s", url)
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            name = archive.namelist()[0]
            text = archive.read(name).decode("utf-8", errors="replace")

        reader = csv.DictReader(io.StringIO(text))
        records: List[dict] = []
        for row in reader:
            record = {key: row.get(key, "") for key in KEEP_COLUMNS if key in row}
            record.setdefault("Exchange", exchange)
            records.append(record)
        return records

    @staticmethod
    def _read_csv(path: Path) -> List[dict]:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _write_csv(path: Path, records: List[dict]) -> None:
        if not records:
            return
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)

    # ------------------------------------------------------------------ #
    def token_for(self, exchange: str, symbol: str) -> Optional[str]:
        """Resolve a trading symbol (``UPL-EQ``, ``NIFTY28NOV24C24000``) to a token."""
        exchange = exchange.upper()
        wanted = symbol.upper().strip()
        for row in self.rows.get(exchange, []):
            if str(row.get("TradingSymbol", "")).upper() == wanted:
                return row.get("Token")
        return None

    def symbol_for(self, exchange: str, token: str) -> Optional[str]:
        for row in self.rows.get(exchange, []):
            if str(row.get("Token")) == str(token):
                return row.get("TradingSymbol")
        return None

    def lot_size(self, exchange: str, symbol: str) -> Optional[int]:
        for row in self.rows.get(exchange, []):
            if str(row.get("TradingSymbol", "")).upper() == symbol.upper():
                try:
                    return int(float(row.get("LotSize") or 0)) or None
                except (TypeError, ValueError):
                    return None
        return None

    def expiries(self, exchange: str, symbol: str, instrument: str = "OPTSTK") -> List[str]:
        found = set()
        for row in self.rows.get(exchange, []):
            if str(row.get("Symbol", "")).upper() != symbol.upper():
                continue
            if instrument and str(row.get("Instrument", "")).upper() != instrument.upper():
                continue
            expiry = str(row.get("Expiry", "")).strip()
            if expiry:
                found.add(expiry)
        return sorted(found)

    def option_chain(
        self, exchange: str, symbol: str, expiry: str, instrument: str = "OPTSTK"
    ) -> List[dict]:
        rows = [
            row
            for row in self.rows.get(exchange, [])
            if str(row.get("Symbol", "")).upper() == symbol.upper()
            and str(row.get("Instrument", "")).upper() == instrument.upper()
            and str(row.get("Expiry", "")).strip() == str(expiry).strip()
        ]
        return sorted(rows, key=lambda r: float(r.get("StrikePrice") or 0))
