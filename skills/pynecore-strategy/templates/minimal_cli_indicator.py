"""
@pyne
EMA Crossover Demo

A workdir script: it lives in <workdir>/scripts/ and runs only through the CLI or the IDE
(`pyne run minimal_cli_indicator <data>`). No standalone bootstrap, no dependency header. Same shape as the
demo that `pyne --recreate-demo` and PyneIDE put into scripts/demo.py.
"""
from pynecore.lib import script, input, plot, color, ta, close
from pynecore.types import Series


@script.indicator(title="EMA Crossover Demo", shorttitle="EMA Demo", overlay=True)
def main(
    src: Series[float] = input.source(close, title="Price Source"),
    fast_length: int = input.int(12, title="Fast EMA Length", minval=1),
    slow_length: int = input.int(26, title="Slow EMA Length", minval=1),
):
    fast_ema = ta.ema(src, fast_length)
    slow_ema = ta.ema(src, slow_length)

    plot(fast_ema, title="Fast EMA", color=color.blue)
    plot(slow_ema, title="Slow EMA", color=color.red)
