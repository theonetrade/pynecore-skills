# `ta` namespace: technical analysis functions

All 67 functions of `pynecore.lib.ta`. Every function keeps its own state per call site, so
call it on **every bar** at the same place in `main()`; calling a `ta.*` function inside an
`if` branch that runs only sometimes gives it a gapped history and wrong values.

Numeric results are `float` (an int-typed result is a float with an integral value) and `na`
(`float('nan')`) until the warm-up window is full. Tuple results unpack:
`macd_line, signal, hist = ta.macd(close, 12, 26, 9)`.

Import: `from pynecore.lib import ta`.

## Contents

- [Moving averages](#moving-averages)
- [Momentum and rate of change](#momentum-and-rate-of-change)
- [Volatility and bands](#volatility-and-bands)
- [Trend and direction](#trend-and-direction)
- [Crossovers and signals](#crossovers-and-signals)
- [Highs, lows, extremes](#highs-lows-extremes)
- [Statistics](#statistics)
- [Volume](#volume)
- [Stochastic and price position](#stochastic-and-price-position)
- [Pivots](#pivots)
- [Aggregation](#aggregation)
- [Numerical caveats](#numerical-caveats)

## Moving averages

| Call | Returns | Notes |
|------|---------|-------|
| `ta.sma(source, length)` | float | Simple moving average |
| `ta.ema(source, length)` | float | alpha = 2 / (length + 1) |
| `ta.rma(source, length)` | float | alpha = 1 / length; what `rsi` and `atr` use internally |
| `ta.wma(source, length)` | float | Linear weights, newest bar heaviest |
| `ta.hma(source, length)` | float | Hull moving average |
| `ta.vwma(source, length)` | float | Volume-weighted |
| `ta.swma(source)` | float | Fixed 4-bar symmetric weights 1/6, 2/6, 2/6, 1/6 |
| `ta.alma(series, length, offset=0.85, sigma=6.0, floor=False)` | float | Arnaud Legoux |
| `ta.linreg(source, length, offset)` | float | Least-squares line value at `offset` bars back |

Recursive averages (`ema`, `rma`, and everything built on them: `rsi`, `atr`, `macd`,
`supertrend`, `dmi`) produce a value from the first bars but carry the influence of their
starting state for a long time. Give them warm-up: roughly 2x the longest length, more if the
exact values matter (for example when comparing two runs with different history length).

## Momentum and rate of change

| Call | Returns | Notes |
|------|---------|-------|
| `ta.rsi(source, length)` | float 0..100 | Relative strength index |
| `ta.mom(source, length)` | float | `source - source[length]` |
| `ta.roc(source, length)` | float | Percent change over `length` bars |
| `ta.macd(source, fastlen, slowlen, siglen)` | (macd, signal, hist) | |
| `ta.cmo(series, length)` | float -100..100 | Chande momentum oscillator |
| `ta.tsi(source, short_length, long_length)` | float -1..1 | True strength index |
| `ta.cog(source, length)` | float | Center of gravity |
| `ta.rci(source, length)` | float -100..100 | Rank correlation index |

## Volatility and bands

| Call | Returns | Notes |
|------|---------|-------|
| `ta.atr(length)` | float | Average true range (RMA of true range); no `source` argument |
| `ta.tr(handle_na=True)` | float | True range of the current bar |
| `ta.stdev(source, length, biased=True)` | float | Standard deviation |
| `ta.variance(source, length, biased=True)` | float | |
| `ta.dev(source, length)` | float | Mean absolute deviation from the SMA |
| `ta.bb(series, length, mult)` | (middle, upper, lower) | Bollinger bands |
| `ta.bbw(series, length, mult)` | float | Band width as a fraction of the middle band |
| `ta.kc(series, length, mult, useTrueRange)` | (middle, upper, lower) | Keltner channel (ATR distance) |
| `ta.kcw(series, length, mult, useTrueRange)` | float | Keltner width |

## Trend and direction

| Call | Returns | Notes |
|------|---------|-------|
| `ta.cci(source, length)` | float | Commodity channel index |
| `ta.dmi(diLength, adxSmoothing)` | (plus_di, minus_di, adx) | Directional movement |
| `ta.supertrend(factor, atrPeriod)` | (line, direction) | `direction` is `1` (down-trend, line above price) or `-1` (up-trend, line below price); check the sign convention on your data before trusting it |
| `ta.sar(start, inc, max)` | float | Parabolic SAR, typically `(0.02, 0.02, 0.2)` |
| `ta.rising(source, length)` | bool | Current value above every one of the previous `length` values |
| `ta.falling(source, length)` | bool | Mirror of `rising` |

## Crossovers and signals

| Call | Returns | Notes |
|------|---------|-------|
| `ta.crossover(a, b)` | bool | `a > b and a[1] <= b[1]` |
| `ta.crossunder(a, b)` | bool | `a < b and a[1] >= b[1]` |
| `ta.cross(a, b)` | bool | Either direction |
| `ta.barssince(condition)` | int | Bars since `condition` was last true; `na` if never |
| `ta.change(source, length=1)` | float or bool | `source - source[length]`; for a bool source, whether it changed |
| `ta.valuewhen(condition, source, occurrence)` | float | `source` at the n-th most recent bar where `condition` was true (`0` = most recent) |

`b` in `crossover`/`crossunder` can be a constant: `ta.crossover(rsi, 30)`.

## Highs, lows, extremes

| Call | Returns | Notes |
|------|---------|-------|
| `ta.highest(source, length)` | float | Highest over the window; `ta.highest(length)` form uses `high` |
| `ta.lowest(source, length)` | float | Lowest over the window |
| `ta.highestbars(source, length)` | int | Offset (negative or zero) of the highest value |
| `ta.lowestbars(source, length)` | int | Offset of the lowest value |
| `ta.max(source)` | float | All-time maximum since the first bar |
| `ta.min(source)` | float | All-time minimum |
| `ta.range(source, length)` | float | `highest - lowest` over the window |

## Statistics

| Call | Returns | Notes |
|------|---------|-------|
| `ta.correlation(source1, source2, length)` | float -1..1 | |
| `ta.percentile_linear_interpolation(source, length, percentage)` | float | |
| `ta.percentile_nearest_rank(source, length, percentage)` | float | |
| `ta.percentrank(source, length)` | float 0..100 | Share of prior values at or below the current one |
| `ta.median(source, length)` | float | |
| `ta.mode(source, length)` | float | Most frequent value, smallest on ties |

## Volume

Parameterless functions read the built-in `volume` and price series.

| Call | Returns | Notes |
|------|---------|-------|
| `ta.mfi(series, length)` | float 0..100 | Money flow index |
| `ta.obv()` | float | On-balance volume |
| `ta.vwap(source=None, anchor=None, stdev_mult=None)` | float or (vwap, upper, lower) | Session-anchored by default; pass `stdev_mult` to get bands |
| `ta.pvt()` | float | Price-volume trend |
| `ta.accdist()` | float | Accumulation/distribution |
| `ta.wad()` | float | Williams A/D |
| `ta.wvad()` | float | Williams variable A/D |
| `ta.pvi()` | float | Positive volume index |
| `ta.nvi()` | float | Negative volume index |
| `ta.iii()` | float | Intraday intensity index |

## Stochastic and price position

| Call | Returns | Notes |
|------|---------|-------|
| `ta.stoch(source, high, low, length)` | float 0..100 | Raw %K; smooth it with `ta.sma` for %K/%D |
| `ta.wpr(length)` | float -100..0 | Williams %R |

## Pivots

| Call | Returns | Notes |
|------|---------|-------|
| `ta.pivothigh(leftbars, rightbars)` | float or na | Uses `high`; the value is known only `rightbars` bars after the pivot bar |
| `ta.pivothigh(source, leftbars, rightbars)` | float or na | Custom source |
| `ta.pivotlow(leftbars, rightbars)` | float or na | Uses `low` |
| `ta.pivotlow(source, leftbars, rightbars)` | float or na | |
| `ta.pivot_point_levels(type, anchor, developing=False)` | list[float] (11 levels) | Daily/weekly/monthly pivot levels |

A pivot is confirmed with a delay of `rightbars` bars. Combine with `fixnan()` to hold the last
confirmed pivot: `last_ph = fixnan(ta.pivothigh(5, 5))`.

## Aggregation

| Call | Returns | Notes |
|------|---------|-------|
| `ta.cum(source)` | float | Running sum since the first bar |

## Numerical caveats

- Comparisons in `crossover`/`crossunder` are strict and have no tolerance. Two computed
  series that are equal to within floating-point noise can flip the result on a single bar.
  Where that matters, round both operands (`math.round(x, 4)`) before comparing.
- Division by zero does not raise inside Pyne code: `x / 0` gives signed infinity, `0 / 0`
  gives `nan`, and `na()` treats both as missing.
- `ta.*` state belongs to the call site. Two different lines calling `ta.sma(close, 20)` are two
  independent averages, which is what you want. The same line executed in a loop keeps one
  state shared by all iterations of that bar (three `ta.ema` lengths in a loop came out
  identical; `ta.sma` of a series happens to survive it because it re-sums the window).
