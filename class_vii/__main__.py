"""Command line entry points.

    python -m class_vii login          # verify credentials only
    python -m class_vii scan           # multi-symbol live indicator scanner
    python -m class_vii terminal       # Excel trade terminal
    python -m class_vii chain          # option chain snapshot
    python -m class_vii check          # offline self-test, no network
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from . import settings as S


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="class_vii",
        description="Merged Shoonya toolkit: Excel trade terminal + live scanner.",
    )
    parser.add_argument("--version", action="version", version=f"class_vii {__version__}")
    parser.add_argument("--config", help="optional path to JSON settings", default=None)
    parser.add_argument(
        "--credentials-workbook",
        help="read Shoonya credentials from the User_Credential worksheet",
        default=None,
    )
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="log in and print the account summary")

    scan = sub.add_parser("scan", help="run the multi-symbol live scanner")
    scan.add_argument("--symbols", nargs="*", help="override symbols from config")
    scan.add_argument("--excel", action="store_true", help="mirror output into Excel")
    scan.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = forever)")

    term = sub.add_parser("terminal", help="run the Excel trade terminal")
    term.add_argument("--workbook", help="path to the .xlsm workbook")

    chain = sub.add_parser("chain", help="print one option-chain snapshot")
    chain.add_argument("--exchange", default="NFO")
    chain.add_argument("--symbol", default="NIFTY")
    chain.add_argument("--expiry", default="")
    chain.add_argument("--strikes", type=int, default=10)
    chain.add_argument("--instrument", default="OPTIDX")

    sub.add_parser("check", help="offline sanity check (no broker calls)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = S.AppConfig.load(args.config)
    if args.credentials_workbook:
        _load_credentials_from_workbook(config, args.credentials_workbook)
    log = S.configure_logging(
        getattr(__import__("logging"), args.log_level.upper(), 20),
        config.alerts.log_dir,
    )
    log.info("class_vii %s starting: %s", __version__, args.command)

    if args.command == "check":
        return _check(config, log)

    session = _login(config, log)
    if session is None:
        return 2

    if args.command == "login":
        print(json.dumps(
            {
                "user": session.credentials.user_id,
                "client_name": session.client_name,
                "method": session.credentials.login_method,
                "connected": session.is_connected,
            },
            indent=2,
        ))
        return 0

    if args.command == "scan":
        return _run_scan(config, session, args, log)

    if args.command == "terminal":
        return _run_terminal(config, session, args, log)

    if args.command == "chain":
        return _run_chain(config, session, args, log)

    return 1


# --------------------------------------------------------------------------- #
def _load_credentials_from_workbook(config: S.AppConfig, workbook_path: str) -> None:
    """Load Shoonya credentials from the CLASS_VII workbook without editing it."""
    from pathlib import Path
    from openpyxl import load_workbook

    path = Path(workbook_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"credentials workbook not found: {path}")

    book = load_workbook(path, read_only=True, data_only=True, keep_vba=True)
    try:
        if "User_Credential" not in book.sheetnames:
            raise ValueError("Workbook is missing the 'User_Credential' worksheet")
        sheet = book["User_Credential"]

        def cell_text(address: str) -> str:
            value = sheet[address].value
            return "" if value is None else str(value).strip()

        method = cell_text("B4").upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "NEWSESSION": "NEW_SESSION",
            "NEW_SESSION": "NEW_SESSION",
            "OAUTH": "OAUTH",
            "TOKEN": "TOKEN",
            "EXISTING_TOKEN": "TOKEN",
        }
        if method not in aliases:
            raise ValueError(
                "User_Credential!B4 must be New_Session, OAUTH or TOKEN"
            )

        creds = config.credentials
        creds.user_id = cell_text("B2")
        creds.login_method = aliases[method]

        if creds.login_method == "NEW_SESSION":
            creds.password = cell_text("B3")
            creds.totp_secret = cell_text("B5")
            creds.vendor_code = cell_text("B6")
            creds.api_secret = cell_text("B7")
        elif creds.login_method == "OAUTH":
            creds.password = cell_text("B3")
            creds.totp_secret = cell_text("B5")
            creds.vendor_code = cell_text("B6")
            creds.api_secret = cell_text("B7")
            # B10 is a one-time OAuth code and is deliberately not reused.
            creds.access_token = ""
        else:
            creds.access_token = cell_text("B8")
    finally:
        book.close()

    creds.validate()


def _login(config: S.AppConfig, log):
    from .auth import LoginError, ShoonyaSession

    session = ShoonyaSession(config.credentials, log)
    try:
        session.connect()
    except (LoginError, ValueError) as exc:
        log.error("cannot continue without a session: %s", exc)
        return None
    return session


def _alerts(config: S.AppConfig, log):
    from .alerts import AlertManager

    manager = AlertManager(config.alerts, log)
    manager.start()
    return manager


def _run_scan(config: S.AppConfig, session, args, log) -> int:
    from .datafeed import HistoricalData, MarketFeed
    from .excel_bridge import ExcelBridge
    from .instruments import InstrumentMaster
    from .scanner import SymbolScanner

    alerts = _alerts(config, log)
    master = InstrumentMaster(logger=log).load()

    symbols = args.symbols or config.scanner.symbols
    if not symbols:
        log.error("no symbols: pass --symbols or set scanner.symbols in config.json")
        return 2

    feed = MarketFeed(session.api, log)
    scanner = SymbolScanner(
        feed, HistoricalData(session.api, config.scanner, log), master,
        config.scanner, alerts, log,
    )

    scanner_ref = {"scanner": None}

    def on_tick(quote, message):
        key = f"{quote.exchange}|{quote.token}"
        scanner.on_tick(key, quote.ltp)

    feed.on_tick = on_tick
    feed.start()
    scanner.add_symbols(symbols)
    scanner_ref["scanner"] = scanner

    bridge = None
    if args.excel:
        bridge = ExcelBridge(config.scanner.workbook, log).open()

    deadline = time.time() + args.seconds if args.seconds else None
    last_print = 0.0
    try:
        while deadline is None or time.time() < deadline:
            if bridge is not None:
                sheet = bridge.sheet("Live", header=scanner.headers())
                bridge.write_block(sheet, 2, "A", scanner.frame().itertuples(index=False, name=None))
                bridge.save()
            if time.time() - last_print >= 10:
                last_print = time.time()
                log.info("ticks=%s symbols=%s", scanner.ticks, len(scanner.keys))
            time.sleep(config.scanner.excel_update_interval)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        feed.stop()
        if bridge is not None:
            bridge.close()
        alerts.stop()
    return 0


def _run_terminal(config: S.AppConfig, session, args, log) -> int:
    from .datafeed import MarketFeed
    from .excel_bridge import ExcelBridge
    from .instruments import InstrumentMaster, parse_symbol
    from .orders import OrderGateway
    from .positions import PositionBook
    from .terminal import ExcelTerminal

    alerts = _alerts(config, log)
    gateway = OrderGateway(
        session.api, config.trading, log, alerts,
        algo_id=config.credentials.vendor_code,
    )
    book = PositionBook(gateway, config.trading, alerts, log)

    workbook = ExcelBridge(args.workbook or config.terminal_workbook, log)
    if not Path(workbook.path).exists():
        log.error("workbook not found: %s", workbook.path)
        return 2
    workbook.open()

    terminal = ExcelTerminal(workbook, book, config.trading, log).attach()
    master = InstrumentMaster(logger=log).load()
    feed = MarketFeed(session.api, log)

    def on_tick(quote, message):
        key = f"{quote.exchange}|{quote.token}"
        book.on_tick(key, quote.ltp)

    feed.on_tick = on_tick
    feed.start()

    symbols = []
    for row in terminal.read_rows():
        symbols.append(f"{row['exchange']}:{row['symbol']}")
    if symbols:
        feed.subscribe(symbols, master=master)

    try:
        terminal.loop(gateway)
    finally:
        feed.stop()
        workbook.close()
        alerts.stop()
    return 0


def _run_chain(config: S.AppConfig, session, args, log) -> int:
    from .datafeed import MarketFeed
    from .instruments import InstrumentMaster
    from .option_chain import ChainConfig, OptionChain

    master = InstrumentMaster(logger=log).load()
    feed = MarketFeed(session.api, log).start()
    chain = OptionChain(
        master, feed,
        ChainConfig(
            exchange=args.exchange.upper(),
            symbol=args.symbol.upper(),
            expiry=args.expiry,
            strikes=args.strikes,
            instrument=args.instrument.upper(),
        ),
        log,
    )
    try:
        frame = chain.build()
        print(frame.to_string(index=False))
        print(json.dumps(chain.summary(frame), indent=2, default=str))
    finally:
        feed.stop()
    return 0


def _check(config: S.AppConfig, log) -> int:
    """Offline verification: no broker, no Excel, no network."""
    import numpy as np
    import pandas as pd

    from . import indicators
    from .orders import round_to_tick
    from .positions import Position, State, TrailingStop

    # Deterministic series -> indicator sanity.
    frame = pd.DataFrame({"close": np.linspace(100, 140, 120)})
    frame = indicators.add_indicators(frame)
    assert frame["RSI"].between(0, 100).all(), "RSI out of range"
    assert frame["STOCH_RSI"].between(0, 100).all(), "StochRSI out of range"
    # Warm-up rows are NaN by design; compare only computed rows.
    bands = frame.dropna(subset=["BB_UPPER", "BB_MIDDLE", "BB_LOWER"])
    assert len(bands) > 50, "too few computed bands"
    assert (bands["BB_UPPER"] >= bands["BB_MIDDLE"]).all()
    assert (bands["BB_MIDDLE"] >= bands["BB_LOWER"]).all()
    assert round_to_tick(101.017, 0.05) == 101.0

    # Trailing stop must only ever move in the favourable direction.
    trail = TrailingStop(step_pct=1.0)
    stop = trail.next_stop("BUY", 100.0, 99.0, 105.0)
    assert stop >= 99.0, "trailing stop moved backwards"

    position = Position(symbol="TEST-EQ", entry_price=100.0, qty=1, state=State.CLOSED,
                        exit_price=104.0)
    assert position.pnl == 4.0, position.pnl

    print("offline self-check passed")
    print(json.dumps(
        {
            "config_file": str(S.DEFAULT_CONFIG_FILE),
            "has_credentials": not config.credentials.missing(),
            "paper_trade": config.trading.paper_trade,
            "symbols": config.scanner.symbols,
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
