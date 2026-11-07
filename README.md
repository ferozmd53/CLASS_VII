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

This is the simple workflow: install once, prepare your Shoonya settings and
Excel workbook, then start the terminal with **one command**.

### 1. Install CLASS_VII

Open CMD and run:

```bat
mkdir C:\CLASS_VII
cd /d C:\CLASS_VII
py -3.14 -m pip install --upgrade "class-vii[all] @ git+https://github.com/ferozmd53/CLASS_VII.git"
```

Git for Windows must be installed and available on PATH.

### 2. Download the two starter files

Run these commands in the same CMD window:

```bat
curl.exe -L https://raw.githubusercontent.com/ferozmd53/CLASS_VII/main/run.py -o run.py
curl.exe -L https://raw.githubusercontent.com/ferozmd53/CLASS_VII/main/config/config.example.json -o config.json
```

### 3. Enter your Shoonya credentials

Open `C:\CLASS_VII\config.json` in Notepad and replace the sample values:

- `user_id`: your Shoonya client ID
- `password`: your Shoonya password
- `totp_secret`: your base32 TOTP secret (or the supported 6-digit PIN)
- `vendor_code`: your Shoonya vendor code
- `api_secret`: your Shoonya API secret

Keep `login_method` as `NEW_SESSION` for normal password + TOTP login.
Do not post this file or commit it to GitHub.

### 4. Put your Excel workbook beside run.py

Copy `Finvasia_Trade_Terminal_v3.xlsm` into `C:\CLASS_VII\`, so the folder
contains:

```text
C:\CLASS_VII\
    run.py
    config.json
    Finvasia_Trade_Terminal_v3.xlsm
```

The workbook must use the expected terminal layout, including the
`Trade_Terminal`, `OrderBook` and `OpenPosition` sheets. If your workbook has
a different filename, edit the `WORKBOOK` line near the top of `run.py`.

### 5. Start the Excel terminal

```bat
cd /d C:\CLASS_VII
py -3.14 run.py
```

That is the normal start command. The script logs in to Shoonya and opens the
Excel terminal. Keep Excel available while it runs; press **Ctrl+C** in CMD to
stop it.

### Minimal run.py example

The repository includes this small launcher. You do not need to write it
yourself when you download it in step 2:

```python
from pathlib import Path
from class_vii.__main__ import main

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    raise SystemExit(main([
        "--config", str(ROOT / "config.json"),
        "terminal",
        "--workbook", str(ROOT / "Finvasia_Trade_Terminal_v3.xlsm"),
    ]))
```

### Other commands (optional)

Run these from CMD after installation:

```bat
py -3.14 -m class_vii --version
py -3.14 -m class_vii check
py -3.14 -m class_vii --config config.json login
py -3.14 -m class_vii --config config.json scan
py -3.14 -m class_vii --config config.json scan --excel --seconds 600
py -3.14 -m class_vii --config config.json chain --symbol NIFTY --strikes 10
```

`check` is an offline self-test. `login` verifies the Shoonya connection.
`scan` runs the live multi-symbol scanner; set the symbols in
`config.json` under `scanner.symbols`. `chain` prints an option-chain
snapshot.

**Safety:** `paper_trade` is `true` in the starter config. Keep it true
while testing; orders are simulated. Do not switch to live order placement
until you have checked the workbook, quantities, product type and order logic.

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
