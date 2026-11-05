# /// script
# requires-python = ">=3.11"
# dependencies = ["pynesys-pynecore[cli]>=6.12"]
# ///

"""
@pyne
Indicator Template

RSI with overbought/oversold levels and a smoothed signal line. Shows the three output
mechanisms: plot(), the plot family for markers and background, and the returned dict.
"""
from pynecore.lib import (
    script, input, ta, close, na, plot, plotshape, bgcolor, hline, fill, color, shape, location,
)
from pynecore.types import Series


@script.indicator("Indicator Template", shorttitle="RSI+", overlay=False)
def main(
    src: Series[float] = input.source(close, "Source"),
    length: int = input.int(14, "RSI length", minval=1),
    smooth: int = input.int(5, "Signal smoothing", minval=1),
    overbought: float = input.float(70.0, "Overbought", minval=50, maxval=100),
    oversold: float = input.float(30.0, "Oversold", minval=0, maxval=50),
):
    rsi: Series[float] = ta.rsi(src, length)
    signal: Series[float] = ta.sma(rsi, smooth)
    cross_up = ta.crossover(rsi, oversold)       # computed every bar, before any condition
    cross_down = ta.crossunder(rsi, overbought)

    plot(rsi, "RSI", color=color.purple, linewidth=2)
    plot(signal, "Signal", color=color.gray)
    upper = hline(overbought, "Overbought", color=color.red)
    lower = hline(oversold, "Oversold", color=color.green)
    fill(upper, lower, color.new(color.purple, 90))
    plotshape(cross_up, "Exit oversold", style=shape.triangleup, location=location.bottom, color=color.green)
    plotshape(cross_down, "Exit overbought", style=shape.triangledown, location=location.top, color=color.red)
    bgcolor(color.new(color.red, 85) if rsi > overbought else
            color.new(color.green, 85) if rsi < oversold else na)

    return {
        "Oversold flag": 1 if rsi < oversold else 0,
        "Overbought flag": 1 if rsi > overbought else 0,
    }


if __name__ == "__main__":
    from pynecore.standalone import run
    run(__file__)
