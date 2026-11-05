"""
@pyne
Library Template

Reusable, stateful helpers shared by several scripts. A library is an ordinary Pyne module
(the @pyne docstring, imports from pynecore.lib) whose `main()` carries @script.library.
Place it under <workdir>/scripts/lib/<publisher>/<name>/v<N>.py and import it from a script:

    from lib.local.utils.v1 import zscore, bars_above, atr_stop

Each function keeps its own Series / Persistent state per call site in the importing script,
exactly like the built-in ta.* functions. Exported functions must not capture per-bar values
or objects created at module level or inside main(); module-level constants are fine.

The docstring may also read ``@pyne lib`` (the spelling PyneIDE's `library` snippet uses), with
or without the @script.library main(); both forms load and behave the same.
"""
from pynecore.lib import script, ta, high, low, na
from pynecore.types import Series, Persistent

__all__ = ["zscore", "bars_above", "atr_stop", "donchian"]

DEFAULT_LENGTH = 20   # constants may be referenced by exported functions


def zscore(src: Series[float], length: int = DEFAULT_LENGTH) -> float:
    """How many standard deviations `src` is from its moving average."""
    mean = ta.sma(src, length)
    sd = ta.stdev(src, length)
    return (src - mean) / sd if sd > 0 else 0.0


def bars_above(src: Series[float], level: float) -> int:
    """Consecutive bars `src` has stayed above `level` (0 when below)."""
    count: Persistent[int] = 0
    count = count + 1 if src > level else 0
    return count


def atr_stop(is_long: bool, length: int = 14, mult: float = 2.0) -> float:
    """Chandelier-style stop: highest high minus ATR x mult (long) or lowest low plus it (short)."""
    atr = ta.atr(length)
    hh = ta.highest(high, length)
    ll = ta.lowest(low, length)
    if na(atr):
        return na
    return hh - atr * mult if is_long else ll + atr * mult


def donchian(length: int = DEFAULT_LENGTH) -> tuple[float, float, float]:
    """Upper, lower and middle channel of the last `length` bars (previous bars only).

    Index a named Series variable, never a call expression: `ta.highest(high, n)[1]` raises
    `TypeError: 'float' object is not subscriptable`.
    """
    hh: Series[float] = ta.highest(high, length)
    ll: Series[float] = ta.lowest(low, length)
    upper = hh[1]
    lower = ll[1]
    return upper, lower, (upper + lower) / 2


@script.library("Library Template")
def main():
    pass
