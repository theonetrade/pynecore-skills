# Indicators: writing, configuring, checking and reusing them

An indicator is a Pyne script whose `main()` is decorated with `@script.indicator(...)`: it runs
once per bar, computes values and publishes them through the plot family or a returned dict; it
places no orders. This page collects everything indicator-specific and links to the references
that hold the general rules. Behaviour marked as verified was checked on PyneCore 6.12.1 with the
probes listed at the end.

## Contents

- [1. Anatomy](#1-anatomy)
- [2. The `@script.indicator` settings](#2-the-scriptindicator-settings)
- [3. Writing the calculation](#3-writing-the-calculation)
- [4. Higher timeframes](#4-higher-timeframes)
- [5. Output](#5-output)
- [6. Running and checking an indicator](#6-running-and-checking-an-indicator)
- [7. Reusing an indicator](#7-reusing-an-indicator)
- [8. Pitfalls](#8-pitfalls)
- [9. Verified](#9-verified)

## 1. Anatomy

```python
"""
@pyne
"""
from pynecore.lib import script, input, ta, close, plot, hline, color
from pynecore.types import Series


@script.indicator("RSI", shorttitle="RSI", overlay=False, precision=2)
def main(
    src: Series[float] = input.source(close, "Source"),
    length: int = input.int(14, "Length", minval=1),
):
    rsi: Series[float] = ta.rsi(src, length)     # every bar, unconditionally
    plot(rsi, "RSI", color=color.purple)         # a column of the plot CSV, a series of the viz stream
    hline(70, "Overbought")
    hline(30, "Oversold")
    return {"rsi_slope": rsi - rsi[1]}           # extra values without a plot style
```

Scaffold one with `python3 <skill>/scripts/new_script.py NAME --kind indicator` (the standalone
`templates/indicator_template.py`: RSI, signal line, markers, levels, fill, background, returned
dict) or `--kind minimal-indicator` (the workdir-only `templates/minimal_cli_indicator.py`).
Inputs are `main()` parameters ([inputs-and-settings.md](inputs-and-settings.md)); the file rules
(marker docstring, imports, reserved names) are in [language.md](language.md).

## 2. The `@script.indicator` settings

Signature in 6.12.1: `indicator(title='', shorttitle='', overlay=False, format=format.inherit,
precision=None, scale=None, max_bars_back=0, timeframe=None, timeframe_gaps=True,
explicit_plot_zorder=False, max_lines_count=50, max_labels_count=50, max_boxes_count=50,
calc_bars_count=0, max_polylines_count=50, dynamic_requests=False, behind_chart=True,
na_bool=False)`. What each one does in PyneCore, which renders no chart:

| Setting | Effect in PyneCore 6.12.1 |
|---------|---------------------------|
| `title`, `shorttitle` | names; in the `--viz` header (the script `.toml` does not list them) |
| `overlay`, `format`, `precision`, `explicit_plot_zorder` | display hints for a renderer: written to the `--viz` header (`hdr.script`), no effect on values |
| `max_lines_count`, `max_labels_count`, `max_boxes_count`, `max_polylines_count` | drawing caps (default 50, at most 500): past the cap the oldest object is deleted ([drawings-and-viz.md](drawings-and-viz.md)) |
| `timeframe`, `timeframe_gaps` | **implemented**: the whole script runs on a higher timeframe built from the chart bars (section 4) |
| `max_bars_back` | stored, **no effect**: every series keeps 5000 bars of history whatever is declared (section 3) |
| `calc_bars_count` | **does not shorten the chart run** (all bars are calculated); it only limits the feeds of `request.security` contexts. Limit the chart with `--from` / `--to` |
| `na_bool` | keeps the older three-state bool (a bool can be `na`, e.g. before warm-up); off by default, rarely needed in new code |
| `scale`, `behind_chart`, `dynamic_requests` | accepted and stored, no effect |

Every setting except the two names is listed, commented out with its default, in the `[script]`
table of `scripts/<name>.toml` and can be overridden there or per run (`run_backtest.py --setting`,
`ScriptRunner(settings=...)`) without editing the code: `--setting 'timeframe="240"'` turned a plain
indicator into a 4-hour one (on 860 hourly bars `bar_index` ended at 214 instead of 859).
Precedence and the file format: [inputs-and-settings.md](inputs-and-settings.md).

## 3. Writing the calculation

**Built-ins first.** `ta.*` covers 67 indicators ([ta.md](ta.md)); the bridge to TA-Lib agrees to
4e-14 for RSI and 3e-8 for Bollinger Bands (section 9). Call every stateful function
(`ta.*`, your own functions with `Series`/`Persistent` state) **on every bar, at the top of
`main()`**, and use the results in conditions afterwards. A call that runs only on some bars
keeps a gapped history: an SMA(5) evaluated only on even bars differed from the every-bar SMA on
all 989 compared bars, by up to 1591 price units. The rules (call-site state, loops sharing one
state, short-circuit `and`/`or`) are in [language.md](language.md#conditionals-short-circuiting-and-stateful-calls).

**Your own recursive indicator.** Keep the state in `Persistent` variables and the history in
`Series`; seed it the way the reference does. A hand-written Wilder RSI:

```python
change: Series[float] = close - close[1]
up = max(change, 0.0) if not na(change) else na
down = max(-change, 0.0) if not na(change) else na
avg_up: Persistent[float] = na
avg_down: Persistent[float] = na
count: Persistent[int] = 0
sum_up: Persistent[float] = 0.0
sum_down: Persistent[float] = 0.0
if not na(up):
    count += 1
    if count <= length:                    # seed: simple average of the first `length` changes
        sum_up += up
        sum_down += down
        if count == length:
            avg_up, avg_down = sum_up / length, sum_down / length
    else:                                  # Wilder smoothing (RMA)
        avg_up = (avg_up * (length - 1) + up) / length
        avg_down = (avg_down * (length - 1) + down) / length
rsi = na if na(avg_up) else (100.0 if avg_down == 0 else 100.0 - 100.0 / (1.0 + avg_up / avg_down))
```

It matches `ta.rsi(close, 14)` exactly (difference 0 on 1986 bars, first value on the same bar,
14). The `if` here is safe because it guards plain arithmetic on `Persistent` variables, not a
stateful call.

**Warm-up and `na`.** Every indicator starts with `na` until its window is full (RSI(14): bar 14,
RSI(21): bar 21). Guard derived values with `na(x)` / `nz(x, v)`; recursive indicators (EMA, RMA,
RSI, ATR) keep converging after the first value, so compare or trade only after a few times their
length ([ta.md](ta.md#numerical-caveats)).

**History depth.** `x[n]` reaches 5000 bars back at most. Beyond that it returns `na` without an
error (`close[5500]` on 10 340 bars: `na`; `close[4999]`: the right value). Neither
`max_bars_back=6000` in the decorator nor `max_bars_back(close, 6000)` extends it; on a series of
your own, `max_bars_back(x, 6000)` raises `ValueError: The max_bars_back cannot exceed 5000!`.
For longer look-backs aggregate (a daily series via `request.security`) or accumulate in
`Persistent` state.

## 4. Higher timeframes

Two ways, for different needs:

| | `@script.indicator(..., timeframe="240")` | `request.security(syminfo.tickerid, "240", expr)` |
|-|-----|-----|
| What runs on the higher timeframe | the whole `main()` | one expression |
| Bars `main()` runs on | once per **completed** higher-timeframe bar, built from the chart bars | every chart bar |
| `bar_index`, `[1]`, `ta.*` state | advance once per higher-timeframe bar | chart bars; the expression's own context advances per its bars |
| Values on the chart bars inside an open period | `na` with `timeframe_gaps=True` (default), the last completed value with `False` | per `gaps` / `lookahead` ([multi-timeframe.md](multi-timeframe.md)) |
| Mix several timeframes in one script | no | yes |
| Extra data needed | none | none for the chart symbol |

Verified on 2000 hourly bars with `timeframe="240"`: 498 values, each equal to the same script
run on real 4-hour bars, placed on the chart bar whose close ends the 4-hour period (the 03:00 bar
for the 00:00–04:00 period, 07:00 for the next); `bar_index` ends at 499; `timeframe_gaps=False`
filled 1989 of the bars. Chart bars before the first complete period produce no output row (1997
rows for 2000 bars). `syminfo.period` keeps the chart timeframe while `timeframe.period` reports
the script's (`timeframe.in_seconds(timeframe.period)` 14400, `timeframe.in_seconds(syminfo.period)`
3600).

## 5. Output

- **Plot family** (`plot`, `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `plotbar`,
  `bgcolor`, `barcolor`, `hline`, `fill`): unconditional calls in `main()`'s body, the value made
  conditional (`plot(x if show else na, "X")`); signatures in [lib.md](lib.md#output-the-plot-family).
- **Returned dict**: `return {"name": value}` adds values without a style; they appear next to the
  plots in the plot CSV and in `run_iter()`, as a column of `NaN` even when the value is `na` on
  every bar.
- **Plot CSV** (`pyne run` → `output/<script>.csv`): `time,open,high,low,close,volume`, then one
  column per plot title in call order, then the dict keys; `NaN` for `na`. `plotcandle`/`plotbar`
  add four columns `<title> (open|high|low|close)`; `bgcolor`, `hline`, `fill` add none.
- **Styles** (colors, widths, shapes) and drawing objects exist only in the `--viz` NDJSON
  ([drawings-and-viz.md](drawings-and-viz.md)).

## 6. Running and checking an indicator

```bash
pyne run my_indicator BTCUSDT_60 --from 2026-01-01           # output/my_indicator.csv
pyne run my_indicator BTCUSDT_60 --viz                       # + styles and drawings
pyne run my_indicator ccxt:BINANCE:BTC/USDT@60 --live        # streaming, values per closed and live bar
<python> <skill>/scripts/run_backtest.py scripts/my_indicator.py data/BTCUSDT_60.ohlcv --input length=21
```

`run_backtest.py` on an indicator prints every plot column with its first bar holding a value and
its last value (`rsi_ta  first valid bar: 21  last: 34.775291`); `--json` gives the same as data.
In Python, `ScriptRunner(...).run_iter()` yields `(candle, plot_data)` per bar, `plot_data` holding
the plot titles and the dict keys ([programmatic.md](programmatic.md#scriptrunner)).

How to trust a new indicator:

1. **Against a reference.** Recompute it with the built-in `ta.*` (when your version is a
   variant), TA-Lib or pandas on the same bars, and compare after the warm-up; a difference above
   about 1e-9 relative on a non-recursive formula is a bug.
2. **Warm-up.** The first valid bar must equal the expected window (`run_backtest.py` prints it);
   recursive formulas need their seed done as the reference does (simple average first, as above).
3. **Edge bars.** Zero volume, `high == low`, gaps: check divisions (`avg_down == 0` above).
4. **Repainting.** The value of a closed bar must not change later: no `request.security(...,
   lookahead_on)` with a bare value, no future references (see [multi-timeframe.md](multi-timeframe.md)).

## 7. Reusing an indicator

- **In another script**: a script's plots cannot be imported. Move the calculation into a library
  function (`scripts/lib/<publisher>/<name>/v1.py`, [language.md](language.md#libraries)) and call
  it from the indicator and the strategy. Each call site keeps its own state: two calls of a
  bands function with lengths 20 and 50 matched their inline `ta.sma` exactly.
- **In a strategy**: call the same library function, or copy the calculation; compute it every
  bar and decide afterwards ([strategy-api.md](strategy-api.md)).
- **From pandas / FreqTrade**: `templates/pynecore_bridge.py` runs an indicator script over a
  DataFrame and returns its plots as Series (`run_indicator(df, path, pair=, timeframe=,
  inputs=)`); `templates/freqtrade_indicators_strategy.py` uses it
  ([live-and-integrations.md](live-and-integrations.md)).
- **Sharing a value across timeframes**: `request.security` of the expression, not of the script.

## 8. Pitfalls

| Symptom | Cause | Fix |
|---------|-------|-----|
| Values differ from the reference only on some bars | a `ta.*` call inside `if`, `and`/`or` or a conditional expression | compute every bar first, select afterwards |
| Three moving averages in a loop come out identical | one call site in a loop shares one state | three calls, or an `array`-based helper |
| `x[5500]` is `na` although the data has 10 000 bars | history is capped at 5000 bars per series | aggregate, or keep the needed value in `Persistent` state |
| `ValueError: The max_bars_back cannot exceed 5000!` | `max_bars_back(series, n)` with `n` > 5000 | at most 5000 |
| `calc_bars_count` set, every bar still calculated | it only cuts `request.security` feeds | `--from` / `--to` |
| An indicator with `timeframe=` shows values on one bar in four | `timeframe_gaps=True` (the default) | `timeframe_gaps=False` to repeat the last value |
| `SyntaxError` naming `plot` | plot-family call inside `if`, a loop or a helper | unconditional call, conditional value |
| A level, fill or background is missing from the plot CSV | `hline`, `fill`, `bgcolor` have no column | `plot()` the value, or return it in the dict |
| `TypeError: 'float' object is not subscriptable` | indexing a call result (`ta.sma(close, 5)[1]`) | assign to a `Series[float]` first, then `x[1]` |

## 9. Verified

PyneCore 6.12.1, hourly BTC/USDT, `ScriptRunner` and `pyne run`.

| Check | Result |
|-------|--------|
| `timeframe="240"` on 2000 hourly bars | 498 values, all equal to the same script on real 4-hour bars, stamped on the closing hour of each period; `bar_index` 0..499; 1997 output rows; `timeframe_gaps=False`: 1989 bars with a value |
| `calc_bars_count=100` on 2000 bars | 2000 rows, `bar_index` 0..1999: no effect on the chart run |
| history depth on 10 340 bars | `close[4999]` correct; `close[5500]` `na` by default, with `max_bars_back=6000` in the decorator and after `max_bars_back(close, 6000)`; on a user series `max_bars_back(x, 6000)` raises the 5000 error |
| hand-written Wilder RSI vs `ta.rsi` | difference 0 on 1986 bars, both first valid on bar 14 |
| `ta.sma(close, 5)` called only on even bars vs every bar | 0 of 989 even bars equal, maximum difference 1591.17 |
| library `bands()` called twice (20 on close, 50 on high) | basis equal to the inline `ta.sma` on every bar (difference 0) |
| returned dict | its key appears in `run_iter()` `plot_data` and as the last column of `output/<script>.csv`; a key that is `na` on every bar still gets its column (`NaN`) |
| plot CSV of `plot` ×2, `fill`, `hline`, `bgcolor` and a dict | header `time,open,high,low,close,volume,fast,slow,always_na,tf_sec,chart_sec`: no column for `fill`, `hline`, `bgcolor` |
| `--setting 'timeframe="240"'` on a plain indicator, 860 hourly bars | `bar_index` ends at 214 (857 rows) instead of 859; inside, `timeframe.period` is 14400 s and `syminfo.period` 3600 s |
| `run_backtest.py` on an indicator with `--input length=21` | `first valid bar: 21` for the hand-written and the built-in RSI, same last value 34.775291 |
| bridge vs TA-Lib on 1000 bars | RSI 4.3e-14, Bollinger upper band 3.1e-8; 14.6 ms per pair for RSI + Bollinger |
