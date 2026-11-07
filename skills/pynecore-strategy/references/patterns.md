# Strategy patterns: entries, exits, sizing, filters, state, validation

Copy-ready building blocks for hand-written strategies. All snippets assume the imports of
`templates/strategy_template.py` and live inside `main()` unless noted.

## Contents

- [Signal patterns](#signal-patterns)
- [Entry patterns](#entry-patterns)
- [Exit patterns](#exit-patterns)
- [Position sizing](#position-sizing)
- [Filters](#filters)
- [State tracking](#state-tracking)
- [Multi-timeframe without extra data](#multi-timeframe-without-extra-data)
- [Debug and evidence](#debug-and-evidence)
- [Backtest validation checklist](#backtest-validation-checklist)
- [Common strategy archetypes](#common-strategy-archetypes)

## Signal patterns

```python
# Crossovers (compute first, combine later)
cross_up = ta.crossover(fast, slow)
cross_down = ta.crossunder(fast, slow)

# Breakout of the previous N bars: exclude the current bar with [1]. Index a named Series
# variable; `ta.highest(high, 20)[1]` on the call itself raises TypeError.
hh: Series[float] = ta.highest(high, 20)
ll: Series[float] = ta.lowest(low, 20)
breakout_up = close > hh[1]
breakout_down = close < ll[1]

# Mean reversion with a band
basis, bb_up, bb_lo = ta.bb(close, 20, 2.0)
revert_long = close < bb_lo and ta.rsi(close, 14) < 30

# Momentum regime
mom = ta.roc(close, 20)
regime_up = mom > 0 and close > ta.ema(close, 200)

# Confirmation: N consecutive bars in a condition
count: Series[int] = 0
count = nz(count[1]) + 1 if close > ma else 0
confirmed = count == 3           # exactly on the third bar

# Debounce: at most one signal per K bars
bars_since = ta.barssince(entered)
cooldown_ok = na(bars_since) or bars_since > 5

# Edge detection on any bool
new_regime = regime_up and not regime_up[1]      # needs regime_up: Series[bool]
```

## Entry patterns

```python
# Market entry; the fill happens at the next bar's open
if long_signal and strategy.position_size <= 0:
    strategy.entry("Long", strategy.long)

# Stop entry (breakout order resting above the market); re-issued every bar updates the price
if setup_long and strategy.position_size == 0:
    strategy.entry("Long", strategy.long, stop=hh[1] + syminfo.mintick)
elif strategy.position_size == 0:
    strategy.cancel("Long")                      # no setup anymore: remove the resting order

# Limit entry (buy a pullback)
strategy.entry("Long", strategy.long, limit=ta.ema(close, 20))

# Reversal: an opposite entry closes the open position and opens the new one in one fill
if short_signal:
    strategy.entry("Short", strategy.short)      # closes "Long" if open

# Long-only / short-only guard
if long_signal and allow_long and strategy.position_size <= 0: ...
# or declaratively
strategy.risk.allow_entry_in(strategy.direction.long)

# Scaling in (pyramiding=3 in the decorator)
if add_signal and strategy.position_size > 0 and strategy.opentrades < 3:
    strategy.entry("Long", strategy.long, qty=base_qty / 2)
```

## Exit patterns

```python
# Fixed stop and target around the average entry price, re-issued every bar while in position
if strategy.position_size > 0:
    entry = strategy.position_avg_price
    strategy.exit("XL", "Long", stop=entry - stop_dist, limit=entry + stop_dist * rr)

# Issue the protective exit together with the entry so it is active from the first bar in position
if long_signal:
    strategy.entry("Long", strategy.long)
    strategy.exit("XL", "Long", stop=close - stop_dist, limit=close + stop_dist * rr)

# Trailing stop (ATR distance), expressed in ticks
ticks = atr * 2 / syminfo.mintick
strategy.exit("XL", "Long", trail_points=ticks, trail_offset=ticks)

# Chandelier / structure trailing stop computed by you (stop only moves in your favour)
trail: Persistent[float] = na
if strategy.position_size > 0:
    candidate = ta.highest(high, 10) - atr * 3
    trail = candidate if na(trail) else math.max(trail, candidate)
    strategy.exit("XL", "Long", stop=trail)
else:
    trail = na

# Breakeven after a run-up of 1R
if strategy.position_size > 0 and high >= entry + stop_dist:
    stop_level = math.max(stop_level, entry)

# Signal exit (market, fills next open)
if exit_signal and strategy.position_size > 0:
    strategy.close("Long", comment="Signal")

# Time stop
if strategy.position_size != 0 and bar_index - strategy.opentrades.entry_bar_index(0) >= 20:
    strategy.close_all(comment="Time")

# Partial profit: close half at 1R, keep the rest with a trailing stop
strategy.exit("TP1", "Long", qty_percent=50, limit=entry + stop_dist)
strategy.exit("Trail", "Long", trail_points=ticks, trail_offset=ticks)

# Close everything before the session ends (intraday strategies)
if session.islastbar_regular:
    strategy.close_all(comment="EOD", immediately=True)
```

`strategy.exit` with `from_entry=""` applies to every open entry; naming the entry id keeps
exits per entry. Exits placed for an entry that has not filled yet activate when it fills.

## Position sizing

```python
# Risk-based: lose risk_pct of equity if the stop is hit
risk_cash = strategy.equity * risk_pct / 100
qty = risk_cash / stop_dist if stop_dist > 0 else na
# With margin_long/margin_short = 100 an order whose notional exceeds equity is silently not
# filled: cap it (or lower the margin settings to model leverage)
qty = math.min(qty, strategy.equity * 0.98 / (close * syminfo.pointvalue))
if not na(qty) and qty > 0:
    strategy.entry("Long", strategy.long, qty=qty)

# Volatility targeting: constant dollar volatility per position
target_vol_cash = 200.0
qty = target_vol_cash / atr

# Fixed fraction via settings (no per-entry qty): default_qty_type=percent_of_equity, default_qty_value=10

# Cash per trade: default_qty_type=strategy.cash, default_qty_value=1000

# Cap by a maximum position
strategy.risk.max_position_size(5)

# What the default sizing would buy at this price (for logging or scaling)
default_qty = strategy.default_entry_qty(close)
```

Sizes are floored to `syminfo.mincontract`; a size that rounds to 0 drops the order. Never use
100 % of equity with 100 % margin on a volatile instrument: margin calls will liquidate the
position on adverse moves.

## Filters

```python
# Date window (inputs with input.time defaults)
in_window = time >= start_time and time <= end_time

# Trading session, exchange timezone or an explicit one
in_session = not na(time("", "0930-1600", "America/New_York"))
in_london = not na(time("", "0800-1630", "Europe/London"))

# Weekday
dow = dayofweek(time)
no_fridays = dow != dayofweek.friday          # import dayofweek as a namespace for the constants

# Volatility regime
atr_pct = ta.atr(14) / close * 100
calm = atr_pct < 3

# Trend filter from a slower average
uptrend = close > ta.ema(close, 200)

# Volume filter
vol_ok = volume > ta.sma(volume, 20)

# "Flat only": no new entry while a position is open
flat = strategy.position_size == 0

# Loss streak pause: skip signals after 3 straight losers for 10 bars
losses: Persistent[int] = 0
last_count: Persistent[int] = 0
if strategy.closedtrades > last_count:
    last_count = strategy.closedtrades
    losses = losses + 1 if strategy.closedtrades.profit(strategy.closedtrades - 1) < 0 else 0
paused = losses >= 3
```

## State tracking

```python
# Highest high since the entry
hh_since_entry: Persistent[float] = na
if strategy.position_size > 0:
    hh_since_entry = high if na(hh_since_entry) else math.max(hh_since_entry, high)
else:
    hh_since_entry = na

# Bars in the current trade
bars_in_trade = bar_index - strategy.opentrades.entry_bar_index(0) if strategy.opentrades > 0 else 0

# Detect a new fill (position changed since the previous bar)
prev_size: Persistent[float] = 0.0
just_opened = prev_size == 0 and strategy.position_size != 0
just_closed = prev_size != 0 and strategy.position_size == 0
prev_size = strategy.position_size          # update at the END of main()

# Daily counters that reset with a new day
trades_today: Persistent[int] = 0
if timeframe.change("D"):
    trades_today = 0
if just_opened:
    trades_today += 1

# Rolling window of custom values
window: Persistent[list] = array.new_float(0)
array.push(window, my_value)
if array.size(window) > 50:
    array.shift(window)
pct_rank = array.percentrank(window, array.size(window) - 1) if array.size(window) > 10 else na
```

## Multi-timeframe without extra data

```python
# Previous day's high/low/close on an intraday chart
day_high: Persistent[float] = na
day_low: Persistent[float] = na
prev_day_high: Persistent[float] = na
prev_day_low: Persistent[float] = na
if timeframe.change("D"):
    prev_day_high, prev_day_low = day_high, day_low
    day_high, day_low = high, low
else:
    day_high = math.max(nz(day_high, high), high)
    day_low = math.min(nz(day_low, low), low)

# Opening range of the first 30 minutes
in_or = time < time("D") + 30 * 60 * 1000
or_high: Persistent[float] = na
if timeframe.change("D"):
    or_high = high
elif in_or:
    or_high = math.max(or_high, high)

# Weekly open
week_open: Persistent[float] = na
if timeframe.change("W"):
    week_open = open
```

For true higher-timeframe indicator values (a daily EMA on an hourly chart) use
`request.security()`; see [multi-timeframe.md](multi-timeframe.md).

## Debug and evidence

```python
if long_signal:
    log.info("LONG {0} fast={1} slow={2} atr={3} qty={4}", close, fast, slow, atr, qty)
if bar_index % 500 == 0:
    log.info("bar {0} equity {1} pos {2}", bar_index, strategy.equity, strategy.position_size)
if barstate.islast:
    log.info("net {0} trades {1} dd {2}%", strategy.netprofit, strategy.closedtrades, strategy.max_drawdown_percent)

# Export intermediate values as plot columns; they appear in the plot CSV and in run_iter() plot_data
plot(atr, "ATR")
plot(1 if long_signal else 0, "long_signal")
```

Read the trade CSV to check the exit reasons (`Signal` column carries the `comment_*` text) and
the plot CSV to see indicator values around a questionable trade. `pyne debug ast script.py`
shows the transformed code when a `SyntaxError` from the transformer is unclear.

## Backtest validation checklist

1. **Data**: review `data/<name>.toml` (`mintick`, `pointvalue`, `mincontract`, `basecurrency`,
   `timezone`, sessions). Check the date range and bar count in the run output.
2. **Costs**: commission and slippage set; sizing explicit; no margin calls in the statistics.
3. **Warm-up**: trades do not start on bar 1; `na` guards on every indicator used in a signal.
4. **No lookahead**: no `[-n]` indexes, no `request.security(..., lookahead_on)` with a bare
   value, signals based on the current bar close trade next bar open.
5. **Fills**: spot-check 2 or 3 trades in the trade CSV against the plot CSV (entry at next
   open, stop/target at the expected price).
6. **Trade count**: at least a few dozen trades before trusting a statistic; one huge trade
   should not carry the result.
7. **Robustness**: the result survives small parameter changes (sweep neighbours), another
   period, another symbol. Hold back an out-of-sample period before optimizing. Measure luck
   and fragility with `scripts/monte_carlo.py`: `trades` (size for the shuffle p95 drawdown,
   bootstrap p5 profit above zero) and `prices` (a noise ladder, blocks, windows); see
   [monte-carlo.md](monte-carlo.md).
8. **Report** what was run (script, data, range, settings) and what was checked, with the
   numbers from the statistics CSV.

## Common strategy archetypes

| Archetype | Signal | Exit | Notes |
|-----------|--------|------|-------|
| MA crossover | `crossover(fast, slow)` / `crossunder` | opposite cross, or ATR stop/target | Add a trend filter to avoid chop |
| Donchian breakout | `close > hh[1]` with `hh: Series[float] = ta.highest(high, N)` | `close < ll[1]` (`ll: Series[float] = ta.lowest(low, M)`) or trailing | Classic trend following; expect low win rate, large winners |
| RSI mean reversion | `crossover(rsi, 30)` long, `crossunder(rsi, 70)` exit | time stop + hard stop | Works in ranges; add regime filter |
| Bollinger reversion | `close < lower` buy, `close > basis` exit | stop below band | Pair with low-volatility filter |
| Opening range breakout | break of first-N-minutes range | EOD flat via `session.islastbar_regular` | Needs intraday data and correct session hours in the `.toml` |
| Trend pullback | uptrend by EMA200, `crossover(rsi, 40)` | ATR trailing | Entries with the trend, exits by volatility |
| Volatility breakout | `close > close[1] + k * atr` | fixed R multiple | Size by ATR |
