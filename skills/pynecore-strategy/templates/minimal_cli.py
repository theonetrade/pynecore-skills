"""
@pyne
EMA Crossover Strategy

A workdir strategy: it lives in <workdir>/scripts/ and runs only through the CLI or the IDE
(`pyne run minimal_cli <data>`). No standalone bootstrap, no dependency header. The first
run writes scripts/minimal_cli.toml with every input and setting.

Fast/slow EMA crossover, ATR stop and target, explicit sizing and costs.
"""
from pynecore.lib import script, input, strategy, ta, close, na, plot, color
from pynecore.types import Series, Persistent


@script.strategy(
    "EMA Crossover Strategy",
    overlay=True,
    initial_capital=10_000,
    default_qty_type=strategy.percent_of_equity,
    default_qty_value=10,                           # 10 % of equity per entry
    commission_type=strategy.commission.percent,
    commission_value=0.05,
    slippage=1,
)
def main(
    src: Series[float] = input.source(close, "Source"),
    fast_length: int = input.int(12, "Fast EMA", minval=1),
    slow_length: int = input.int(26, "Slow EMA", minval=2),
    atr_length: int = input.int(14, "ATR length", minval=1),
    stop_mult: float = input.float(2.0, "Stop distance = ATR x", minval=0.1, step=0.1),
    reward_ratio: float = input.float(1.5, "Target = stop x (0 = no target)", minval=0.0, step=0.1),
    allow_short: bool = input.bool(True, "Allow shorts"),
):
    # indicators: every bar, before any condition
    fast = ta.ema(src, fast_length)
    slow = ta.ema(src, slow_length)
    atr = ta.atr(atr_length)
    cross_up = ta.crossover(fast, slow)
    cross_down = ta.crossunder(fast, slow)
    ready = not na(fast) and not na(slow) and not na(atr)

    # levels set on the signal bar (the fill is at the next bar's open), kept across bars
    stop_level: Persistent[float] = na
    target_level: Persistent[float] = na
    stop_dist = atr * stop_mult

    long_signal = ready and cross_up
    short_signal = ready and cross_down and allow_short

    if long_signal and strategy.position_size <= 0:
        stop_level = close - stop_dist
        target_level = close + stop_dist * reward_ratio if reward_ratio > 0 else na
        strategy.entry("Long", strategy.long)
    elif short_signal and strategy.position_size >= 0:
        stop_level = close + stop_dist
        target_level = close - stop_dist * reward_ratio if reward_ratio > 0 else na
        strategy.entry("Short", strategy.short)

    # protective exits: issued with the entry, re-issued every bar in position
    if long_signal or strategy.position_size > 0:
        strategy.exit("Exit Long", "Long", stop=stop_level, limit=target_level,
                      comment_loss="SL", comment_profit="TP")
    if short_signal or strategy.position_size < 0:
        strategy.exit("Exit Short", "Short", stop=stop_level, limit=target_level,
                      comment_loss="SL", comment_profit="TP")

    in_position = strategy.position_size != 0
    plot(fast, "Fast EMA", color=color.blue)
    plot(slow, "Slow EMA", color=color.red)
    plot(stop_level if in_position else na, "Stop", color=color.red, style=plot.style_linebr)
    plot(target_level if in_position else na, "Target", color=color.green, style=plot.style_linebr)
