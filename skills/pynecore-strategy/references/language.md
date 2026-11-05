# Pyne language rules: how a PyneCore script executes

Pyne code is Python syntax with bar-by-bar execution semantics added by an import-time AST
transformation. Reading this page once prevents most bugs in hand-written strategies.

## Contents

- [File anatomy](#file-anatomy)
- [Execution model](#execution-model)
- [`Series[T]`: values with history](#seriest-values-with-history)
- [`Persistent[T]` and `IBPersistent[T]`: state across bars](#persistentt-and-ibpersistentt-state-across-bars)
- [Function isolation: every call site has its own state](#function-isolation-every-call-site-has-its-own-state)
- [Conditionals, short-circuiting and stateful calls](#conditionals-short-circuiting-and-stateful-calls)
- [`na` and numeric semantics](#na-and-numeric-semantics)
- [Numbers: ints are floats](#numbers-ints-are-floats)
- [Module-level objects are read-only inside functions](#module-level-objects-are-read-only-inside-functions)
- [Plot-family calls must be unconditional](#plot-family-calls-must-be-unconditional)
- [Reserved names](#reserved-names)
- [Libraries](#libraries)
- [User-defined types and enums](#user-defined-types-and-enums)
- [What plain Python you may use](#what-plain-python-you-may-use)
- [Type checking in editors](#type-checking-in-editors)

## File anatomy

```python
# optional inline script metadata for `uv run` may precede the docstring (comments are fine)

"""
@pyne
One-line description. Anything after the marker is documentation.
"""
from pynecore.lib import script, input, strategy, ta, close, high, low, volume, plot, color, na, nz, log
from pynecore.types import Series, Persistent, IBPersistent


@script.strategy("Title", overlay=True, initial_capital=10_000)      # or @script.indicator / @script.library
def main(
    length: int = input.int(20, "Length", minval=1),                 # inputs = parameters with input.* defaults
):
    ...                                                              # runs once per bar
    return {"Name": value}                                           # optional extra plot columns


if __name__ == "__main__":                                           # optional: python my_script.py data.csv
    from pynecore.standalone import run
    run(__file__)
```

Rules for the marker: the **first statement** of the file is a module docstring, its **first
non-whitespace token** is `@pyne` followed by whitespace or the end of the docstring. A docstring
that mentions `@pyne` later in the text does not count, and `@pynex` does not match. Without
the marker the file is ordinary Python and nothing below applies. (`@pyne edge` selects a
restricted syntax subset meant for generated code; write plain `@pyne`.)

Imports: runtime names from `pynecore.lib` (functions, bar series, namespaces such as `ta`,
`strategy`, `math`, `color`), annotations from `pynecore.types` (`Series`, `Persistent`,
`IBPersistent`, `Color`, `NA`, `Line`, `Label`, `Box`, `Table`). `pynecore` itself re-exports
`Series` and `Persistent`. `from pynecore.lib import *` works but hides which names you use;
prefer explicit imports. The string namespace is `string`, not `str`.

Exactly one `main()` decorated with `@script.indicator(...)`, `@script.strategy(...)` or
`@script.library(...)`. Decorator arguments are the script's settings (see
[strategy-api.md](strategy-api.md) and [inputs-and-settings.md](inputs-and-settings.md)).

## Execution model

The runtime iterates over the bars in time order and calls `main()` once per bar with the
built-in series (`open`, `high`, `low`, `close`, `volume`, `time`, `bar_index`, ...) set to that
bar. Your code describes what happens on **the current bar**; the runtime keeps the history and
the state. Nothing is vectorized: a loop over bars does not exist in your code.

Strategies: after `main()` returns, the broker emulator processes the orders placed during the
call (market orders fill at the next bar's open unless `process_orders_on_close` or
`immediately` is used; limit/stop orders fill when a later bar reaches the price). See
[strategy-api.md](strategy-api.md#when-orders-fill).

A bar can be executed more than once only in special modes (`calc_on_order_fills`,
`calc_on_every_history_tick`, live ticks). Then `Persistent` state rolls back to the bar's
opening state before each re-execution and `IBPersistent` does not.

## `Series[T]`: values with history

A built-in series or any variable annotated `Series[T]` keeps one value per bar in a circular
buffer and supports `[n]` to read `n` bars back:

```python
spread: Series[float] = high - low       # assignment runs every bar and pushes the new value
prev_spread = spread[1]                  # one bar ago; na before enough history
avg_spread = (spread + spread[1] + spread[2]) / 3
window = spread[0:20]                    # read-only view of the last 20 values (PyneCore extension)
```

- Used without `[ ]` a series behaves like a plain value of type `T`. Arithmetic, comparisons
  and function calls get the current bar's value.
- Only variables you **index** need the annotation. `ma = ta.sma(close, 20)` is a plain float;
  `ma: Series[float] = ta.sma(close, 20)` lets you write `ma[1]`.
- Index a **named** series variable. Indexing a call expression directly
  (`ta.highest(high, 20)[1]`, `ta.sma(close, 20)[1]`) raises
  `TypeError: 'float' object is not subscriptable` at run time: assign to a `Series[...]`
  variable first, then index it.
- Annotate at the first assignment and assign on every bar (unconditionally), otherwise the
  history has gaps.
- `x[0]` is the current value, `x[-1]` is `na` (never the future). Reading beyond the buffer is
  `na`, not an error. The default buffer is 5000 bars; `max_bars_back(x, n)` resizes one series.
- A series declared with `= na` takes its type from the first typed value stored.

Pattern, previous-bar comparison: `rising = close > close[1]`.
Pattern, counting consecutive bars (series form):

```python
count: Series[int] = 0
count = nz(count[1]) + 1 if close > open else 0
```

## `Persistent[T]` and `IBPersistent[T]`: state across bars

A plain local variable is re-created on every bar. `Persistent[T]` initializes once (the first
time the statement runs) and keeps its value from bar to bar:

```python
highest_since_entry: Persistent[float] = na
trades_today: Persistent[int] = 0
levels: Persistent[list] = array.new_float(0)       # containers too

if strategy.position_size == 0:
    highest_since_entry = na
else:
    highest_since_entry = high if na(highest_since_entry) else math.max(highest_since_entry, high)
```

- Declare it inside the function that uses it (`main()` or a helper). Each call site of a
  helper gets its own copy.
- A `Persistent` holding a list/dict/object keeps the **same object**; mutate it in place.
- `IBPersistent[T]` is the same but survives intra-bar re-execution (it counts ticks or fills
  within a bar). In a plain backtest the two behave identically.
- Combine with `Series` when you need both history and persistence:
  `PersistentSeries[T]` exists for that, but it is rarely needed.

## Function isolation: every call site has its own state

`ta.*` functions, `fixnan()`, `math.sum()`, `math.random()` and your own functions that
declare `Series` or `Persistent` variables are **stateful**. State belongs to the **call
site**, not to the function:

```python
def ema_slope(src: Series[float], length: int) -> float:
    e: Series[float] = ta.ema(src, length)
    return e - e[1]

s1 = ema_slope(close, 20)      # its own ta.ema and its own `e` history
s2 = ema_slope(volume, 20)     # independent state
```

A call site inside a loop keeps one state shared by all iterations of that bar (iterating
`for length in (10, 20, 50): ta.sma(close, length)` does **not** give three averages; write
three calls, or build a helper that takes a list and uses `array.*`).

Calling a stateful function through a variable, callback or method (`f = ta.sma; f(close, 20)`)
still works but is resolved at run time and slower. Recursion and functions stored in containers
share state.

## Conditionals, short-circuiting and stateful calls

Because state is per call site and advances only when the call executes, a stateful call must
execute on **every bar** to have a correct history. Python `and`/`or` short-circuit, `if`
branches skip code, and conditional expressions evaluate one side only, so:

```python
# WRONG: ta.crossover runs only on bars where `ready` is true -> gapped history, wrong result
if ready and ta.crossover(fast, slow):
    ...

# RIGHT: compute first, then combine
cross_up = ta.crossover(fast, slow)
if ready and cross_up:
    ...
```

Same for `ta.highest(high, 20)` inside an `if`, `ta.atr(14)` only when in position, and so on.
Compute all indicators at the top of `main()`, unconditionally, then use the plain values in
conditions. A condition that is **constant for the whole run** (an input such as
`ta.ema(src, trend_len) if use_trend else na`) is harmless because the call site either always
runs or never runs; still, the simpler pattern is to always compute and decide later.

## `na` and numeric semantics

- `na` is the missing value. Numeric `na` is `float('nan')`; string `na` is `""`; `bool` has no
  `na` (it reads as `False`); drawings and objects have an `NA` object.
- Test with `na(x)`; never `x == na` (always false) and never `x is None`.
- `nz(x, default)` substitutes; `fixnan(x)` holds the last valid value (stateful).
- Arithmetic propagates `na`. Comparisons with `na` are `False`, so `if rsi > 70:` is simply
  false during warm-up, and a numeric `na` used directly as a condition counts as `False`.
- Division by zero does not raise: `x / 0` is `inf`, `0 / 0` is `nan`, both are `na` to `na()`.
- A variable that starts as `na` and is assigned later takes the type of its assignments; keep
  one type per variable.
- Reading history before it exists (`close[1]` on bar 0) is `na`.

Warm-up idiom: `ready = not na(slow_ma) and not na(atr)` before trading.

## Numbers: ints are floats

Every number runs as a double. An `int`-typed value (`bar_index`, `math.floor()`,
`array.size()`, `int(x)`) is a Python `float` with an integral value: `bar_index` prints as
`12.0` in an f-string. Consequences:

- Format with `string.tostring(bar_index)` or `f"{bar_index:.0f}"`.
- `int(x)` truncates toward zero and keeps `na` as `na`.
- Indexes into Python lists, `range()` bounds and series offsets are truncated for you.
- Use `round(x)` when you need a true Python `int` for a dict key shared with an `int` literal.

## Module-level objects are read-only inside functions

Define constants, colors, arrays or objects at module level and read them anywhere, but never
**write** to them from inside a function: assignment, `global`, augmented assignment, `del`,
mutating method calls (`append`, `pop`, `clear`, ...) and mutating `array.*`/`map.*`/`matrix.*`
calls on a module-level name raise a `SyntaxError` at load time. State belongs in
`Persistent` variables inside the function: a module-level counter would count executions
instead of bars whenever a bar is re-executed.

```python
LEVELS = array.from_items(1.0, 2.0)   # fine to define and read
RISK_PCT = 1.0                         # fine

def main():
    array.push(LEVELS, close)          # SyntaxError: module-level object modified in a function
```

## Plot-family calls must be unconditional

`plot`, `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `plotbar`, `bgcolor`, `barcolor`,
`fill`, `hline`, `alertcondition` must be called in the direct body of `main()`, on every bar,
before any `return`. Inside `if`, loops, helpers or conditional expressions they are a
`SyntaxError` at load time. Pass a conditional **value** instead:

```python
plot(stop_level if strategy.position_size != 0 else na, "Stop")
plotshape(long_signal, "Long")                       # bool series: 1/0 in the CSV
bgcolor(color.new(color.green, 90) if in_session else na)
```

Drawing objects (`label.new`, `line.new`, ...) and `log.*` have no such restriction.

## Reserved names

Identifiers containing a middle dot (`·`) and the transform's own double-underscore names
(`__state__`, `__bool1__`, `__cmp3__`, `__pyne_<name>__`) are rejected. Other dunder names are
fine. Avoid shadowing imported built-ins (`time`, `open`, `close`, `input`, `string`, `math`)
with your own variables: `open` and `time` are especially easy to clobber.

## Libraries

Shared stateful helpers live in `<workdir>/scripts/lib/<publisher>/<name>/v<N>.py`:

```python
"""
@pyne
"""
from pynecore.lib import script, ta
from pynecore.types import Series, Persistent

__all__ = ["zscore"]

def zscore(src: Series[float], length: int) -> float:
    mean = ta.sma(src, length)
    sd = ta.stdev(src, length)
    return (src - mean) / sd if sd > 0 else 0.0

@script.library("My utils")
def main():
    pass
```

Import from a script with `from lib.local.utils.v1 import zscore` (the `scripts/` folder is on
the import path during `pyne run` and `ScriptRunner` runs). Each imported function keeps
per-call-site state in the importing script. Exported functions may use module-level
constants but must not capture objects or per-bar values created at module level or in the
library's `main()`; that is rejected at load time. `templates/library_template.py` is a
complete example and `scripts/new_script.py --kind library` scaffolds one.

## User-defined types and enums

```python
from pynecore.core.pine_udt import udt
from enum import StrEnum

@udt
class Trade:
    entry: float = na
    stop: float = na
    bars: int = 0

    def risk(self) -> float:
        return self.entry - self.stop

class Side(StrEnum):
    Long = "Long"
    Short = "Short"
```

`@udt` gives `na`-aware defaults and copy semantics; methods may hold state like any function.
Keep instances across bars in a `Persistent[Trade]`. `input.enum(Side.Long, "Side")` exposes an
enum as an input.

## What plain Python you may use

Everything, with the state rules above. Standard library modules, `import pandas` or
`requests`, classes, comprehensions, `match`, exceptions, f-strings are all allowed in a plain
`@pyne` script. Keep in mind:

- The script runs once per bar; do expensive one-time work lazily behind a `Persistent[bool]`
  flag or at module level (read-only afterwards).
- Network calls or notifications belong behind `barstate.isrealtime` so a backtest does not
  fire them on every historical bar.
- `print()` works but `log.info()` adds the bar timestamp and index.
- Python `and`/`or` short-circuit; see the stateful-call rule above.

## Type checking in editors

Static checkers flag `close[1]` (a float that is also indexable). PyCharm understands
PyneCore's stubs out of the box. For Pylance/Pyright put this `pyrightconfig.json` in the
project root:

```json
{
  "typeCheckingMode": "basic",
  "reportIndexIssue": "none",
  "reportAssignmentType": "none",
  "reportRedeclaration": "none",
  "reportArgumentType": "none",
  "defineConstant": { "TYPECHECKER": "pyright" }
}
```

The red squiggles are false positives of static analysis, not errors; the runtime is the judge.
