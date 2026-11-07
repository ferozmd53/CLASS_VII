# Credits and attribution

`class_vii` is a merge of two MIT/Apache-licensed open-source projects. Both are
reproduced in derivative form; the original copyright notices are retained.

## 1. Python Trader's Excel Based Trade Terminal (Finvasia)

- Source: https://github.com/Indian-Algorithmic-Trading-Community/PythonTraderExcelTradeTerminal
  (the `Finvasia/` directory: `Finvasia_Core_V3_002.py`, `Finvasia_Trade_Terminal_v3.xlsm`)
- Author: Python Trader — https://t.me/pythontrader , https://youtube.com/@pythontraders
- License: Apache License 2.0 OR MIT (dual, at your option). Full texts in
  `LICENSE-APACHE` and `LICENSE-MIT`.
- Used for: Excel trade terminal, order book / open position / holdings handling,
  target + stop-loss + trailing logic, Telegram and voice alerts, option chain.

## 2. Shoonya Multi-Symbol Live/Historical Data with Indicators

- Source: https://github.com/ferozmd53/Shoonya-Multi-Symbol-Live-Historical-Data-with-Indicators
- Author: ferozmd53
- License: MIT (per the repository `setup.py` metadata)
- Used for: multi-symbol websocket feed, daily historical loader, TradingView
  RSI / Stochastic RSI / Bollinger Bands, extreme-reversal signal definitions.

## Broker

Both projects, and this merge, use the Shoonya (Finvasia) NorenRestApi. Shoonya
is not affiliated with this code. API access requires your own Shoonya account
and an enabled API key.

## Modifications

This is not a verbatim copy. Login, websocket subscription, indicator update
timing, Excel access and order routing were rewritten and consolidated; see the
*What changed, and why* section of `README.md` for the itemised list.
