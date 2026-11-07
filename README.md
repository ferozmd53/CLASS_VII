# class_vii

Merged Shoonya (Finvasia) trading toolkit. It combines two open-source projects
that both talk to the same broker API (**NorenRestApi**) into one package with a
single login, a single config file and one set of dependencies.

| Source | What was taken | License |
| --- | --- | --- |
| [PythonTraderExcelTradeTerminal](https://github.com/Indian-Algorithmic-Trading-Community/PythonTraderExcelTradeTerminal) — `Finvasia/` | Excel trade terminal, order book / open position / holdings sheets, target-SL-trailing engine, Telegram + voice alerts, option chain (incl. Pro variant) | Apache-2.0 OR MIT |
| [Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators](https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators) | multi-symbol websocket feed, daily historical loader, TradingView-matching RSI / StochRSI / Bollinger Bands, extreme-reversal signal | MIT |

Both are credited in `NOTICE.md`. Nothing here is a copy-paste merge: the two
codebases were reconciled (see *What changed* below), because side by side they
conflict on login, on the websocket payload and on the Excel layout.

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`xlwings` needs Microsoft Excel and only matters for `terminal` and
`scan --excel`. Everything else runs without it.

## Configure

```bash
cp config/config.example.json config/config.json
```

Fill `config/config.json` — it is git-ignored, and the loader refuses to start
with fields missing. Prefer environment variables in CI:

```bash
export CLASS_VII_USER_ID=FA12345
export CLASS_VII_PASSWORD=...
export CLASS_VII_TOTP_SECRET=...      # base32 secret, or a 6-digit pin
export CLASS_VII_VENDOR_CODE=FA12345_U
export CLASS_VII_API_SECRET=...
```

`paper_trade` defaults to **true**: orders are simulated and logged, never sent.

## Run

```bash
python -m class_vii check       # offline self-test, no broker, no Excel
python -m class_vii login       # verify credentials, print account summary
python -m class_vii scan        # live multi-symbol indicator scanner
python -m class_vii scan --excel --seconds 600
python -m class_vii terminal    # Excel trade terminal (reads the .xlsm)
python -m class_vii chain --symbol NIFTY --strikes 10
```

`terminal` expects the original workbook name (`Finvasia_Trade_Terminal_v3.xlsm`)
or `--workbook <path>`. It reads `Symbol | Qty | BUY/SELL | Target | SL | Trail`
rows on the `Trade_Terminal` sheet and writes status / latest SL / PnL back, plus
the `OrderBook` and `OpenPosition` sheets.

---

## Structure

```
class_vii/
├── class_vii/
│   ├── settings.py        single config object + logging (was scattered globals)
│   ├── auth.py            one Shoonya login: NEW_SESSION | OAUTH | TOKEN
│   ├── instruments.py     instrument master, cached per day, symbol<->token
│   ├── datafeed.py        MarketFeed (websocket quotes) + HistoricalData
│   ├── indicators.py      RSI / StochRSI / BB + reversal signal (TradingView parity)
│   ├── orders.py          OrderGateway: place/modify/cancel/exit-all, paper mode
│   ├── positions.py       PositionBook state machine + trailing stop
│   ├── scanner.py         multi-symbol live scanner
│   ├── terminal.py        Excel trade terminal bridge
│   ├── option_chain.py    option chain snapshot + OI/PCR summary
│   ├── excel_bridge.py    xlwings wrapper, bulk writes, column helpers
│   ├── alerts.py          Telegram + speech, queued off the trading thread
│   └── __main__.py        CLI: check | login | scan | terminal | chain
├── config/config.example.json
├── tests/test_core.py
├── requirements.txt
├── pyproject.toml
└── LICENSE-MIT, LICENSE-APACHE
```

## What changed, and why

**Login.** repo 1 built `login(userid, password, twoFA, vendor_code, api_secret)`
and read cells `B2..B8`; repo 2 scraped an OAuth code from a Selenium window into
`B7` and called `getAccessToken`. They cannot both own the session. `auth.py`
supports both methods plus plain `susertoken` reuse, selected by
`credentials.login_method`, with retry/backoff and no Excel dependency.

**Credentials.** repo 1 printed the userid, password and api_secret straight to
console *and* to the log file
(`print(f"userid=...,password=...,api_secret=...")`). That is gone; credentials
now come from config/env and are never logged. `AppConfig.save()` redacts them.

**Websocket subscription.** repo 2 called `api.subscribe(["NSE|2885", ...])` — a
list of strings, inside `try/except: pass`. NorenApi needs
`[{"exchange": "NSE", "token": "2885"}]`, so its live feed subscribed to nothing
and failed silently. `MarketFeed.subscribe()` builds the documented payload, logs
failures loudly, and replays subscriptions on reconnect (repo 1 cleared
`subs_lst` on open but never re-subscribed).

**History vs live.** repo 2 computed indicators from daily closes and then
appended the live LTP as one more bar, so an intraday tick was compared against
*daily* bands — mathematically the same array, but the signal fired all day off
a single price. `scanner.py` keeps the daily series as the base and recomputes
per tick, and emits a signal once per side change instead of on every tick.

**Excel.** repo 1 hard-coded addresses (`tt.range("a2:d2")`, `f"Ab{idx+2}"`,
`"q4:s1000"`) in ~700 lines of nested `if` blocks. repo 2 recomputed column
letters at import time. `excel_bridge.py` exposes one bulk-write path and
column helpers; `terminal.py` maps a named table onto them.

**Order flow.** repo 1's `place_trade` did SL-M padding but no `algo_id`; repo 2
sent `algo_id` but priced off `ltp ± 5` with no tick rounding. `orders.py` merges
both, rounds to the exchange tick, and adds `paper_trade`.

**State machine.** The position lifecycle is now an explicit
`IDLE → ENTRY_PENDING → ACTIVE → EXIT_PENDING → CLOSED` enum instead of a string
written into a spreadsheet cell and re-read as the source of truth.

**De-duplication.** Both repos defined `Shoonya_login`, `GetToken`,
`place_order`, `live_data`, and their own Excel update loop with different
signatures. Each now exists once.

## Known limitations

- `option_chain.py` centres the strike window on an approximation until the first
  ticks land; Greeks (repo 1 fetched them from a GitHub gist at import time) are
  **not** ported — the chain reports LTP, OI, volume and PCR only.
- `scan` recomputes the indicator frame per tick. That is fine for tens of
  symbols; for hundreds, incremental updates are the next optimisation.
- The Excel terminal expects the repo 1 workbook layout. A different workbook
  needs the `COLUMNS` map in `terminal.py` adjusted.
- Only NSE/BSE equity style symbols are auto-suffixed with `-EQ`; derivatives
  must be given as full trading symbols.

## Tests

```bash
python tests/test_core.py      # or: python -m pytest -q
```

Covers indicator bounds, a real band-reclaim signal, tick rounding, trailing-stop
monotonicity, PnL sign, entry/target/stop exits and paper-mode order routing.

## Disclaimer

Educational use only. Trading through an API can lose money; the upstream
authors and the author of this merge accept no liability. Test in paper mode
first — `paper_trade` is `true` by default for a reason.
