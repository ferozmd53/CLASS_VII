"""class_vii - merged Shoonya (Finvasia) trading toolkit.

Combines:

* ``PythonTraderExcelTradeTerminal/Finvasia`` (Apache-2.0 / MIT, Python Trader)
  - Excel trade terminal, order book, open positions, holdings, option chains,
    target/SL/trailing logic, Telegram + voice alerts.
* ``ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators`` (MIT)
  - multi-symbol websocket feed, daily historical loader, TradingView-matching
    RSI / Stochastic RSI / Bollinger Bands and the extreme-reversal signal.

Both talk to the same broker (Shoonya, NorenRestApi). See ``README.md``.
"""
from __future__ import annotations

__version__ = "1.0.0"

from .settings import (  # noqa: F401
    AlertConfig,
    AppConfig,
    Credentials,
    ScannerConfig,
    TradingConfig,
    configure_logging,
)

__all__ = [
    "__version__",
    "AlertConfig",
    "AppConfig",
    "Credentials",
    "ScannerConfig",
    "TradingConfig",
    "configure_logging",
]
