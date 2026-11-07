# class_vii



---

## Install (one command from GitHub)

**Windows (Python 3.14):**

```bat
py -3.14 -m pip install --upgrade "class-vii[all] @ git+https://github.com/ferozmd53/CLASS_VII.git"
```

This installs CLASS_VII directly from GitHub, including the optional Excel and alert dependencies. Git for Windows must be installed and available on PATH.

**Other Python versions / macOS / Linux:**

```bash
python -m pip install --upgrade "class-vii[all] @ git+https://github.com/ferozmd53/CLASS_VII.git"
```

To update later, run the same command again. To verify the installation:

```bat
py -3.14 -m class_vii --version
py -3.14 -m class_vii check
```

**Install from a local clone instead:**

```bash
python -m pip install ".[all]"
```

## Quick start (Windows + Python 3.14)

The normal workflow uses **CLASS_VII.xlsm for Shoonya credentials**. No
`config.json` file is required.

### 1. Install CLASS_VII

Open CMD and run:

```bat
mkdir C:\\CLASS_VII
cd /d C:\\CLASS_VII
py -3.14 -m pip install --upgrade "class-vii[all] @ git+https://github.com/ferozmd53/CLASS_VII.git"
```

Git for Windows must be installed and available on PATH.

### 2. Download the launcher

```bat
curl.exe -L https://raw.githubusercontent.com/ferozmd53/CLASS_VII/main/run.py -o run.py
```

### 3. Add credentials to the Excel workbook

Put your workbook named **`CLASS_VII.xlsm`** in `C:\\CLASS_VII\\`.
Enter your Shoonya details in the existing `User_Credential` worksheet:

| Cell | Field |
|---|---|
| B2 | Shoonya User ID |
| B3 | Password |
| B4 | Login method: `New_Session` (recommended) or `OAUTH` |
| B5 | TOTP secret (used by the browser login) |
| B6 | Client ID / vendor code (for example, ending in `_U`) |
| B7 | API Secret / Secret Code |
| B8 | Existing session token (legacy TOKEN method only) |
| B10 | Not used; OAuth codes are captured fresh and are single-use |

Set B4 to `New_Session` (recommended) or `OAUTH`, and fill B2:B7. At startup,
the launcher opens the workbook visibly in desktop Excel, clears B8 and the old
B10 value, detects the current public IP and saves it to B11. It then opens
Shoonya's hosted OAuth login in Chrome, saves the newly captured one-time code
to B10 for reference, exchanges it for a fresh session, and starts the terminal.
B10 is never read or reused; B8 is always cleared and never used. TOKEN mode is
disabled. The Client ID's current public IP must be whitelisted in Shoonya API
Key Generation.
Keep these credentials private; do not commit your filled workbook to GitHub.

### 4. Start the terminal

Keep these two files in the same folder:

```text
C:\\CLASS_VII\\
    run.py
    CLASS_VII.xlsm
```

Run:

```bat
py -3.14 run.py
```

The launcher reads credentials from `User_Credential` and starts the terminal
using that same workbook. It does not create or require `config.json`.

### Minimal run.py

```python
from pathlib import Path
from class_vii.__main__ import main

WORKBOOK = Path(__file__).resolve().parent / "CLASS_VII.xlsm"

if __name__ == "__main__":
    raise SystemExit(main([
        "--credentials-workbook", str(WORKBOOK),
        "terminal",
        "--workbook", str(WORKBOOK),
    ]))
```

### Other commands (optional)

Run these from CMD after installation:

```bat
py -3.14 -m class_vii --version
py -3.14 -m class_vii check
py -3.14 -m class_vii login
py -3.14 -m class_vii scan
py -3.14 -m class_vii scan --excel --seconds 600
py -3.14 -m class_vii chain --symbol NIFTY --strikes 10
```

`check` is an offline self-test. `login` verifies the Shoonya connection.
`scan` runs the live multi-symbol scanner; pass symbols with `--symbols`, for
example `py -3.14 -m class_vii scan --symbols NSE:RELIANCE-EQ NSE:HCLTECH-EQ`.
`chain` prints an option-chain snapshot.

**Safety:** `paper_trade` defaults to `true` in the package settings, so orders
are simulated by default. Do not enable live order placement until you have
checked the workbook, quantities, product type and order logic.

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

**Login.** Shoonya's current supported flow is OAuth. `auth.py` opens the hosted
Shoonya login in Chrome, enters the workbook login details, captures a fresh
one-time authorization code, exchanges it through the OAuth SDK, and injects the
session token. It does not reuse B10 because authorization codes are single-use.
The main launcher reads credentials from `User_Credential` in `CLASS_VII.xlsm`.

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


## New workbook template

Create a new workbook (the initializer refuses to overwrite an existing path):

```bat
py -3.14 -m class_vii init-workbook --path CLASS_VII_Terminal.xlsx
```

The template includes Dashboard, Watchlist, LiveIndicators, Trade_Terminal,
OrderBook, OpenPosition, BrokerPositions, Holdings, Funds, Alerts and
User_Credential. Enter account credentials yourself; no secrets are generated
or copied. Run the terminal with:

```bat
py -3.14 -m class_vii --credentials-workbook CLASS_VII_Terminal.xlsx terminal --workbook CLASS_VII_Terminal.xlsx
```

OrderBook and BrokerPositions are read-only broker snapshots even in paper
mode. Paper orders are not mixed into the broker OrderBook. The daily scanner
updates today's forming OHLC candle from ticks and recalculates indicators;
these values remain provisional until the daily candle closes.
