"""Unit tests - run with ``python -m pytest`` or ``python tests/test_core.py``."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from class_vii import indicators
from class_vii.excel_bridge import column_letter, column_number, column_span
from class_vii.instruments import parse_symbol
from class_vii.orders import OrderGateway, round_to_tick
from class_vii.positions import Position, PositionBook, State, TrailingStop
from class_vii import settings as S


class FakeGateway:
    """Records orders instead of sending them."""

    def __init__(self):
        self.orders = []
        self._n = 0

    def place(self, exchange, symbol, qty, side, price=None, order_type=None, ltp=0.0,
              remarks="test"):
        from class_vii.orders import OrderResult

        self._n += 1
        result = OrderResult(f"SIM{self._n}", symbol, side, qty, ltp or 100.0)
        self.orders.append(result)
        return result


# --------------------------------------------------------------------------- #
def test_columns():
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    assert column_number("AA") == 27
    assert column_span("Y", 3) == ["Y", "Z", "AA"]


def test_parse_symbol():
    assert parse_symbol("NSE:UPL-EQ") == ("NSE", "UPL-EQ")
    assert parse_symbol("NFO|2885") == ("NFO", "2885")
    assert parse_symbol("reliance-eq") == ("NSE", "RELIANCE-EQ")


def test_indicators_bounds_and_ordering():
    frame = pd.DataFrame({"close": np.linspace(100, 150, 200)})
    frame = indicators.add_indicators(frame)
    frame = indicators.add_signals(frame)
    assert frame["RSI"].between(0, 100).all()
    assert frame["STOCH_RSI"].between(0, 100).all()
    bands = frame.dropna(subset=["BB_UPPER", "BB_MIDDLE", "BB_LOWER"])
    assert len(bands) > 100
    assert (bands["BB_UPPER"] >= bands["BB_MIDDLE"]).all()
    assert (bands["BB_MIDDLE"] >= bands["BB_LOWER"]).all()


def test_rsi_reference_values():
    # Classic Wilder example: RSI(14) on a rising series trends to ~100.
    series = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28])
    value = indicators.rsi(series, 14).iloc[-1]
    assert 0 <= value <= 100
    assert value > 50, "rising series should give RSI above 50"


def test_buy_signal_fires_on_band_reclaim():
    # Steady 100, a slide that closes under the lower band, then a reclaim bar.
    closes = [100.0] * 40 + [99, 97, 95, 93, 91, 89, 87, 85] + [88.0]
    frame = pd.DataFrame({"close": closes})
    frame = indicators.add_indicators(frame)
    frame = indicators.add_signals(frame, stoch_lower=30, stoch_upper=70)
    assert frame["BUY_SIGNAL"].iloc[-1], frame.tail(3).to_string()
    assert frame["SIGNAL"].iloc[-1] == "BUY"


def test_round_to_tick():
    assert round_to_tick(101.017, 0.05) == 101.0
    assert round_to_tick(101.03, 0.05) == 101.05


def test_trailing_stop_monotonic():
    trail = TrailingStop(step_pct=1.0)
    stop = 99.0
    for ltp in (100, 101, 102, 103, 104):
        new_stop = trail.next_stop("BUY", 100.0, stop, ltp)
        assert new_stop >= stop
        stop = new_stop


def test_position_pnl_sign():
    long_pos = Position("X", entry_price=100.0, exit_price=110.0, qty=2, state=State.CLOSED)
    short_pos = Position("X", side="SELL", entry_price=100.0, exit_price=90.0, qty=2,
                         state=State.CLOSED)
    assert long_pos.pnl == 20.0
    assert short_pos.pnl == 20.0


def test_entry_then_target_exit():
    gateway = FakeGateway()
    trading = S.TradingConfig(paper_trade=True, trail_enabled=False)
    book = PositionBook(gateway, trading)
    book.upsert("TEST-EQ", "NSE", qty=1, entry_signal=True)

    assert book.on_tick("NSE:TEST-EQ", 100.0) == "entered"
    position = book.positions["NSE:TEST-EQ"]
    assert position.state is State.ACTIVE
    position.target = 105.0

    assert book.on_tick("NSE:TEST-EQ", 106.0) == "exited"
    assert position.state is State.CLOSED


def test_stop_loss_exit():
    gateway = FakeGateway()
    trading = S.TradingConfig(paper_trade=True, trail_enabled=False)
    book = PositionBook(gateway, trading)
    book.upsert("TEST-EQ", "NSE", qty=1, entry_signal=True)
    book.on_tick("NSE:TEST-EQ", 100.0)
    position = book.positions["NSE:TEST-EQ"]
    position.latest_stop = 98.0
    assert book.on_tick("NSE:TEST-EQ", 97.5) == "exited"


def test_credentials_validation():
    creds = S.Credentials(user_id="FA1", password="p", totp_secret="ABCD",
                          vendor_code="FA1_U", api_secret="s")
    assert creds.missing() == []
    creds.password = ""
    assert "password" in creds.missing()


def test_gateway_paper_mode():
    trading = S.TradingConfig(paper_trade=True, product_type="MIS")
    gateway = OrderGateway(api=None, trading=trading)
    result = gateway.place("NSE", "TEST-EQ", 1, "BUY", ltp=250.0)
    assert result.ok
    assert result.paper is True
    assert gateway.product_for("NSE") == "I"
    assert gateway.paper_orders


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed} tests passed")


def test_scanner_ticks_update_one_daily_candle():
    from datetime import datetime
    import logging
    from class_vii.scanner import SymbolScanner
    scanner = SymbolScanner.__new__(SymbolScanner)
    scanner.feed = object()
    scanner.history = type("History", (), {})()
    scanner.master = object()
    scanner.config = S.ScannerConfig(bb_length=2, rsi_length=2, stoch_length=2, sma_stoch_length=1)
    scanner.alerts = None
    scanner.log = logging.getLogger("test.scanner")
    scanner.keys = {"TEST-EQ": "NSE|1"}
    scanner.snapshots = {}
    scanner.ticks = 0
    scanner._signalled = {}
    today = datetime.now().date()
    frame = pd.DataFrame([
        {"datetime": pd.Timestamp(today)-pd.Timedelta(days=1), "open":98.0, "high":100.0, "low":97.0, "close":99.0, "volume":5},
        {"datetime": pd.Timestamp(today), "open":100.0, "high":101.0, "low":99.0, "close":100.0, "volume":8},
    ])
    scanner.history.cache = {"TEST-EQ": {"frame":frame, "closes":frame.close.tolist()}}
    scanner.on_tick("NSE|1", 102.0)
    scanner.on_tick("NSE|1", 98.0)
    updated = scanner.history.cache["TEST-EQ"]["frame"]
    assert len(updated) == 2
    assert float(updated.iloc[-1]["high"]) == 102.0
    assert float(updated.iloc[-1]["low"]) == 98.0
    assert float(updated.iloc[-1]["close"]) == 98.0
