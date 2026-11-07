# Multi-timeframe and multi-symbol data: `request.security()`

## Contents

- [Basics](#basics)
- [Supplying the data](#supplying-the-data)
- [Lookahead and gaps](#lookahead-and-gaps)
- [Confirmation timing](#confirmation-timing)
- [Lower timeframe intrabars](#lower-timeframe-intrabars)
- [Dependent and conditional calls](#dependent-and-conditional-calls)
- [Alternatives that need no extra data](#alternatives-that-need-no-extra-data)
- [Limitations](#limitations)
- [Debugging security contexts](#debugging-security-contexts)

## Basics

```python
from pynecore.lib import script, request, syminfo, ta, close, plot, barmerge
from pynecore.types import Series


@script.strategy("HTF trend filter", overlay=True)
def main():
    daily_ema: Series[float] = request.security(syminfo.tickerid, "1D", ta.ema(close, 50))
    hourly_rsi = request.security(syminfo.tickerid, "60", ta.rsi(close, 14))
    spx = request.security("SP:SPX", "", close)        # another symbol, same timeframe ("" = chart's)
    ...
```

`request.security(symbol, timeframe, expression, gaps=barmerge.gaps_off,
lookahead=barmerge.lookahead_off, ignore_invalid_symbol=False, currency=None)`

- `symbol`: `syminfo.tickerid` for the chart's own symbol, or a `"PREFIX:TICKER"` string.
- `timeframe`: `"1"`, `"5"`, `"60"`, `"240"`, `"1D"`, `"1W"`, `"1M"`; `""` means the chart's.
- `expression`: any expression; it is evaluated **in the requested context**, bar by bar on that
  context's own data, with its own `ta.*` state. Each call runs in a separate OS process with
  its own history.

The result type is the expression's type; a tuple expression returns a tuple. Drawing
constructors are not allowed inside the expression.

## Supplying the data

Each context needs its own `.ohlcv` + `.toml` pair. Three ways to connect them:

1. **Per run**: `pyne run s.py EURUSD_5 --security "1D=EURUSD_1D" --security "SP:SPX:1D=capitalcom_US500_1D"`
   or `ScriptRunner(security_data={"1D": "data/EURUSD_1D", "SP:SPX": "data/capitalcom_US500_1D"})`
   (base paths without extension). Keys: `"TIMEFRAME"` (any symbol at that timeframe),
   `"SYMBOL"` (any timeframe), `"SYMBOL:TIMEFRAME"` (exact).
2. **Symbol map** in `config/symbol_map.toml`, written once with
   `pyne data map "SP:SPX" "capitalcom:US500"`; the data file name is derived from the provider's
   naming rules. Per-timeframe override: key `"SP:SPX:1D"`.
3. **Aggregate on the fly** is not done for security contexts: a daily context on a 5-minute
   chart needs the daily file. Create it with `pyne data aggregate EURUSD_5 -tf 1D`.

`pyne run s.py DATA --list-data` prints what a script needs.

## Lookahead and gaps

| `lookahead=` | Backtest | Live | Use |
|--------------|----------|------|-----|
| `barmerge.lookahead_off` (default) | last **closed** higher-timeframe bar | same | normal case, repaint-free |
| `barmerge.lookahead_last_closed` | same as `lookahead_off` | same | explicit "last closed" intent |
| `barmerge.lookahead_on` | bare value: the developing bar of the open period, built from the chart bars so far, unconfirmed; `[1]` and older: the context's own closed bars (see below) | same | the `close[1]` idiom: `request.security(sym, "D", close[1], lookahead=barmerge.lookahead_on)` reads the previous completed period exactly |

PyneCore never exposes future data: with `lookahead_on` a bare `close` is the developing
period's current value, never its final close.

Closed periods of a `lookahead_on` context are its own timeframe's bars (6.12.1). When the chart
bars cover a period exactly, their fold *is* that bar. When they do not (a missing chart bar,
the chart starting inside the period, a chart timeframe that does not divide the requested one),
the closed bar is read from the context's data (`--security`), and on the period's last chart
bar the value already equals that closed bar; without security data the incomplete fold is
used. `lookahead_off` reads the security data whenever it is given. Verified on a 1-hour chart
with a 4-hour `--security` file whose closes were shifted by +1000: with all four hours present
`close[1]` under `lookahead_on` showed the chart fold while `lookahead_off` showed the shifted
file; with one hour removed from the chart, `close[1]` switched to the shifted value. So when the
security file and the chart data disagree (another feed, other session bounds), `lookahead_on`
`[1]` and `lookahead_off` can differ on exactly covered periods; keep both from the same source. For cross-symbol higher-timeframe requests with
`lookahead_on` the developing bar cannot be built, so the value is `na` until the period closes.

`gaps=barmerge.gaps_off` (default) forward-fills the last value between higher-timeframe
closes; `barmerge.gaps_on` returns `na` on chart bars where no new higher-timeframe value
arrived.

`ignore_invalid_symbol=True` returns `na` instead of failing when the symbol has no data.
`currency="USD"` converts the result with `request.currency_rate()` when rate data is present.

## Confirmation timing

A chart bar sees the higher-timeframe bar whose **scheduled close is at or before the chart
bar's own close**. On a 5-minute chart the daily value changes on the 23:55 bar for a 24/7
market, or on the bar that closes the session for an exchange with hours. Weekly and monthly
bars close at the end of their last trading session. Daily/weekly/monthly values of a different
trading calendar arrive one chart bar later than you might expect. Holidays are not inferred
from the data: a shortened week shows up one consumer bar later rather than being guessed.

A same-timeframe different-symbol context is confirmed on every bar.

## Lower timeframe intrabars

`request.security_lower_tf(symbol, timeframe, expression)` returns a **list** of the
expression's values for the intrabars that closed inside the current chart bar (empty when none
did). Use `array.*` or plain Python on it. An intrabar straddling the chart bar's close is
delivered on the next chart bar.

## Dependent and conditional calls

- Calls inside `if` / loops work, but a security expression that varies between iterations of a
  loop on the same bar raises an error.
- A later call may read an earlier call's result inside its expression:
  `sma_m = request.security(sym, "M", ta.sma(rsi_m, 14))` where `rsi_m` is an earlier security
  result. Order producers before consumers.
- Nested calls are supported.

## Alternatives that need no extra data

For many strategies a higher-timeframe filter can be built on the chart data itself:

- `timeframe.change("D")` is true on the first bar of a new day; combine with `Persistent`
  variables to accumulate the day's high/low/open.
- `time("D")` returns the open time of the current day's bar, `time_close("D")` its close.
- Resample explicitly: keep a `Persistent` running value and reset it on `timeframe.change(tf)`.

This avoids extra processes and data files and is often what a strategy actually needs (a
daily VWAP, the previous day's range, a weekly open).

## Limitations

- Standalone `python script.py data.csv` does not support security data; use `pyne run` or
  `ScriptRunner(security_data=...)`.
- Chart types other than Heikin Ashi (`ticker.heikinashi(symbol)`) are not supported in
  requests.
- Cross-symbol higher-timeframe `lookahead_on` returns `na` inside an open period.
- Every context is an extra OS process: dozens of calls mean dozens of processes.

## Debugging security contexts

`log.*` output inside a security context is suppressed. Redirect it to a file:

```bash
PYNE_SECURITY_LOG=security.log pyne run s.py EURUSD_5 --security "1D=EURUSD_1D"
```

Each line is prefixed with `[SYMBOL TIMEFRAME]`.
