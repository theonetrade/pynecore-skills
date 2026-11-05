# /// script
# requires-python = ">=3.11"
# dependencies = ["pynesys-pynecore[cli]>=6.12"]
# ///

"""
@pyne
Strategy Template

Trend-following skeleton: fast/slow EMA crossover entries, an optional slower EMA as a trend
filter, ATR-based stop and target, risk-based position sizing, optional trailing stop and time
stop. Every block is independent; replace the signal block with your own idea and keep the rest.

Structure (keep this order):
  inputs -> indicators (every bar) -> filters -> signals -> sizing -> orders -> exits -> outputs
"""
from pynecore.lib import (
    script, input, strategy, ta, close, bar_index, time, timestamp, na, nz, math,
    plot, plotshape, bgcolor, color, shape, location, log, syminfo, barstate, string,
)
from pynecore.types import Series, Persistent


@script.strategy(
    "Strategy Template",
    overlay=True,
    initial_capital=10_000,
    default_qty_type=strategy.percent_of_equity,   # used when risk_pct is 0
    default_qty_value=10,                           # 10 % of equity per entry
    pyramiding=1,
    commission_type=strategy.commission.percent,
    commission_value=0.05,                          # 0.05 % per fill
    slippage=1,                                     # ticks against you on every fill
)
def main(
    src: Series[float] = input.source(close, "Source"),
    fast_len: int = input.int(12, "Fast EMA", minval=1, group="Signal"),
    slow_len: int = input.int(26, "Slow EMA", minval=2, group="Signal"),
    trend_len: int = input.int(100, "Trend EMA (0 = off)", minval=0, group="Filters"),
    allow_long: bool = input.bool(True, "Allow longs", group="Filters"),
    allow_short: bool = input.bool(True, "Allow shorts", group="Filters"),
    start_time: int = input.time(timestamp("2000-01-01"), "Start", group="Filters"),
    end_time: int = input.time(timestamp("2100-01-01"), "End", group="Filters"),
    atr_len: int = input.int(14, "ATR length", minval=1, group="Risk"),
    stop_mult: float = input.float(2.0, "Stop distance = ATR x", minval=0.1, step=0.1, group="Risk"),
    reward_ratio: float = input.float(1.5, "Target = stop x (0 = no target)", minval=0.0, step=0.1, group="Risk"),
    risk_pct: float = input.float(1.0, "Risk per trade % of equity (0 = default qty)", minval=0.0, maxval=100.0, step=0.1, group="Risk"),
    use_trailing: bool = input.bool(False, "Trail the stop by the stop distance", group="Risk"),
    max_bars_in_trade: int = input.int(0, "Time stop in bars (0 = off)", minval=0, group="Risk"),
    verbose: bool = input.bool(False, "Log signals", group="Debug"),
):
    # ---- indicators: always computed, on every bar, in the same place --------------------
    fast: Series[float] = ta.ema(src, fast_len)
    slow: Series[float] = ta.ema(src, slow_len)
    trend: Series[float] = ta.ema(src, trend_len if trend_len > 0 else 1)
    atr: Series[float] = ta.atr(atr_len)
    cross_up = ta.crossover(fast, slow)       # stateful: never hide these behind a condition
    cross_down = ta.crossunder(fast, slow)

    # ---- filters -----------------------------------------------------------------------
    use_trend = trend_len > 0
    in_window = time >= start_time and time <= end_time
    ready = not na(fast) and not na(slow) and not na(atr) and (not use_trend or not na(trend))
    trend_up = not use_trend or src > trend
    trend_down = not use_trend or src < trend

    # ---- signals -----------------------------------------------------------------------
    long_signal = ready and in_window and allow_long and trend_up and cross_up
    short_signal = ready and in_window and allow_short and trend_down and cross_down

    # ---- sizing: risk a fixed share of equity over the stop distance ----------------------
    stop_dist = atr * stop_mult
    qty: float = na                           # na -> the decorator's default sizing applies
    if risk_pct > 0 and not na(stop_dist) and stop_dist > 0:
        qty = strategy.equity * risk_pct / 100 / stop_dist
        # With margin_long / margin_short = 100 (no leverage) an order whose notional exceeds
        # the equity is not filled at all, silently. Cap the size; lower the margin settings in
        # the decorator to model leverage instead.
        qty = math.min(qty, strategy.equity * 0.98 / (close * syminfo.pointvalue))

    # ---- orders ------------------------------------------------------------------------
    # Levels are set on the signal bar from the close (the fill happens at the next bar's
    # open) and kept in Persistent variables so the exit can be re-issued every bar.
    stop_level: Persistent[float] = na
    target_level: Persistent[float] = na
    entry_bar: Persistent[int] = 0

    if long_signal and strategy.position_size <= 0:
        stop_level = close - stop_dist
        target_level = close + stop_dist * reward_ratio if reward_ratio > 0 else na
        entry_bar = bar_index
        strategy.entry("Long", strategy.long, qty=qty)
        if verbose:
            log.info("LONG signal @ {0}  stop {1}  target {2}", close, stop_level, target_level)
    elif short_signal and strategy.position_size >= 0:
        stop_level = close + stop_dist
        target_level = close - stop_dist * reward_ratio if reward_ratio > 0 else na
        entry_bar = bar_index
        strategy.entry("Short", strategy.short, qty=qty)
        if verbose:
            log.info("SHORT signal @ {0}  stop {1}  target {2}", close, stop_level, target_level)

    # ---- exits: issued with the entry (active as soon as it fills) and re-issued every bar
    trail_ticks: float = stop_dist / syminfo.mintick if use_trailing else na
    if long_signal or strategy.position_size > 0:
        strategy.exit("Exit Long", "Long", stop=stop_level, limit=target_level,
                      trail_points=trail_ticks, trail_offset=trail_ticks,
                      comment_loss="SL", comment_profit="TP", comment_trailing="Trail")
    if short_signal or strategy.position_size < 0:
        strategy.exit("Exit Short", "Short", stop=stop_level, limit=target_level,
                      trail_points=trail_ticks, trail_offset=trail_ticks,
                      comment_loss="SL", comment_profit="TP", comment_trailing="Trail")

    if max_bars_in_trade > 0 and strategy.position_size != 0 and bar_index - entry_bar >= max_bars_in_trade:
        strategy.close_all(comment="Time stop")

    # ---- outputs: plot-family calls stay unconditional; the values carry the condition -----
    in_position = strategy.position_size != 0
    plot(fast, "Fast EMA", color=color.teal)
    plot(slow, "Slow EMA", color=color.orange)
    plot(trend if use_trend else na, "Trend EMA", color=color.gray)
    plot(stop_level if in_position else na, "Stop", color=color.red, style=plot.style_linebr)
    plot(target_level if in_position else na, "Target", color=color.green, style=plot.style_linebr)
    plotshape(long_signal, "Long signal", style=shape.triangleup, location=location.belowbar, color=color.green)
    plotshape(short_signal, "Short signal", style=shape.triangledown, location=location.abovebar, color=color.red)
    bgcolor(color.new(color.green, 92) if strategy.position_size > 0 else
            color.new(color.red, 92) if strategy.position_size < 0 else na)

    if barstate.islast:
        log.info("Done: net {0}, trades {1}, win rate {2}%, max DD {3}%",
                 string.tostring(strategy.netprofit, "#.##"), strategy.closedtrades,
                 string.tostring(100 * strategy.wintrades / strategy.closedtrades if strategy.closedtrades > 0 else 0, "#.#"),
                 string.tostring(strategy.max_drawdown_percent, "#.##"))

    return {
        "Equity": strategy.equity,
        "Position": strategy.position_size,
    }


if __name__ == "__main__":
    from pynecore.standalone import run
    run(__file__)
