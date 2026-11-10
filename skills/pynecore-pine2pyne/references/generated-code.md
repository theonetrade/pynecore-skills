# The generated code, libraries, running it, and the differences that matter

Counts come from transpiling pine2pyne's reference corpus (1005 sources: 471 indicators, 449
strategies, 85 libraries) with pine2pyne 0.2.11 in the default profile; "files" is the number
of generated modules that contain the construct. Every imported PyneCore name was checked to
exist in PyneCore 6.12.1.

## Shape of a module

```python
"""
@pyne edge

This code was compiled by pine2pyne 0.2.11 — the Pine Script to PyneCore Python transpiler.
Run with open-source PyneCore: https://pynecore.org
Source: https://github.com/pine2pyne/pine2pyne
"""
from pynecore import pine_range
from pynecore.core.pine_cast import cast_bool
from pynecore.lib import close, input, plot, script, strategy, ta
from pynecore.types import Persistent, Series


@script.strategy('RSI strat', overlay=True, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10, margin_long=0, margin_short=0, close_by_id=True)
def main(
    len=input.int(14, 'RSI length', minval=1),
    ob=input.float(70, 'Overbought'),
    os=input.float(30, 'Oversold')
):
    r = ta.rsi(close, len)
    trades: Persistent[int] = 0
    if ta.crossover(r, os):
        strategy.entry('L', strategy.long)
        trades = trades + 1


if __name__ == "__main__":
    from pynecore.standalone import run
    run(__file__)
```

(A v5 strategy, verified to run on PyneCore 6.12.1.) Rules visible here: every top-level
`input.*` is a `main()` parameter in source order; v4/v5 strategies get explicit defaults
(`margin_long=0, margin_short=0, close_by_id=True`, and `initial_capital`,
`default_qty_type=strategy.fixed`, `default_qty_value=1` when the source left them out: 359
files); `var` is `Persistent[T]` (608 files) or `PersistentSeries[T]` when read with history
(228); `x += 1` is written `x = x + 1`; v4 and some v5 scripts get `na_bool=True` in the
decorator (391 files) so that `bool` history before warm-up is `na`.

## Imports and helpers the output uses

| Import | Files | Written for |
|--------|------:|-------------|
| `from pynecore import pine_range` | 325 | `for i = a to b [by s]` in v4/v5, and v6 with an end that cannot change: `for i in pine_range(a, b[, s])` |
| `from pynecore import pine_loop` | 51 | v6 `for` whose end expression may change during the loop: `__loop_n__ = pine_loop(a)` + `while` (Pine v6 re-reads the end before each pass) |
| `from pynecore.core.pine_cast import cast_int / cast_bool / cast_float / cast_string` | 153 / 142 / 24 / 1 | `int(x)`, `bool(x)` (v6 semantics; v5 `bool(x)` of a series is written `na(bool) if na(x) else bool(x)`), `float(x)`, `str(x)` |
| `from pynecore.types import Series, Persistent, PersistentSeries, NA, Color, Line, Label, Box, Table, LineFill, Polyline, Matrix, IBPersistent, Footprint` | 735 / 608 / 228 / 203 / 334 / 236 / 205 / 127 / 175 / 40 / 15 / 21 / 15 / 1 | Type annotations; `NA(T)` is a typed `na` value |
| `from pynecore.core.series import inline_series` | 103 | A history index on a call result: `inline_series(ta.atr(100), 1)` for `ta.atr(100)[1]` |
| `from pynecore.core.pine_udt import udt, udt_copy` | 107 / 12 | `@udt` dataclass for `type`; `.copy()` |
| `from pynecore.core.pine_method import method, method_call` | 68 / 114 | `@method` for `method f(T this, ...)`; `method_call('name', obj, ...)` for a method on a receiver of unknown type |
| `from pynecore.core.overload import overload` | 32 | `@overload` for a Pine function declared twice with different parameters |
| `from pynecore.core.pine_export import Exported, export` | 85 | Libraries: `@export` on exported functions, `Exported()` placeholders |
| `from pynecore.core.pine_import import shadowed_namespace` | 15 | A library alias equal to a built-in namespace (see below) |
| `from pynecore.types.chart import ChartPoint` | 23 | `chart.point` values |
| `from dataclasses import field as _dataclass_field` | 29 | UDT field defaults that are built-in variables: `_dataclass_field(default_factory=lambda: close)` |
| `from enum import StrEnum as Enum` | 22 | `enum` declarations |

Names you will meet in the body:

| Pattern | Files | Meaning |
|---------|------:|---------|
| `__block_result_<n>__` | 369 | The value of an `if` / `switch` used as an expression or assigned |
| `__switch__` | 173 | The subject of a `switch x` |
| `iff_<n>` | 46 | A ternary pulled out of a condition (`iff_1 = -1 if pl and na(ph) else dir`) |
| `__hist_<name>__: Series[T] = name[0]` | 33 | A snapshot so that `name[1]` reads the series history after a reassignment |
| `<name>__ren__` | 171 | A user name that collides with a PyneCore namespace or keyword (`xloc`, `lambda`, `alert`) |
| `<name>__global__` | 44 | A global shadowed by a parameter or local of the same name |
| `<name>__<8 digits>__` | 98 | A parameter or local renamed to keep a callee's global read correct (`i__00000200__`) |
| `__input_<n>__=input(...)` | 133 | An input that had no name in the source |
| `__loop_<n>__ = pine_loop(...)` | 51 | The v6 loop object |
| `na(x)`, `NA(T)` | 595 / 203 | `na` test and typed `na` value |
| `string.format(...)`, `math.abs/max/min/pow/round` | 80 / — | `str.format`, the `math.*` spellings |
| `alertcondition(...)`, `alert(...)` | 291 / 130 | Kept as calls; PyneCore logs them |
| `request.security(...)` / `request.security_lower_tf(...)` | 215 / 19 | Need `--security` data files or a provider at run time (`pyne run ... --list-data` shows what is needed) |

## Libraries

```pine
import jdehorty/KernelFunctions/2 as kernels      →   import lib.jdehorty.KernelFunctions.v2 as kernels
import Trading-IQ/ICTlibrary/1 as ict             →   import lib.Trading_IQ.ICTlibrary.v1 as ict
import TradingView/ta/7 as ta                     →   import lib.TradingView.ta.v7 as ta__ren____lib
                                                      ta__ren__ = shadowed_namespace(ta__ren____lib, ta)
```

- The import root is `<workdir>/scripts/`; a library must be at
  `scripts/lib/<user>/<Name>/v<N>.py` with `-` in the user name replaced by `_`. No `__init__.py`
  is needed (namespace packages). pine2pyne never downloads or reads library sources; the
  script's output does not depend on the library being present.
- Missing at run time: `ModuleNotFoundError: No module named 'lib.<user>'`.
- A transpiled library has `@script.library('Name', overlay=...)`, `__all__ = [...]`, one
  `_Protocol<Name>` class per exported type and `@export` on each exported function; it runs
  nowhere by itself and has no `__main__` footer.
- Refused (full run only, not `--validate`): a library name that is not a Python identifier or
  is a keyword, an alias that is a keyword, two imports of one library name that differ in user
  or version.
- A library function's result type is unknown to pine2pyne (it does not read the library), so
  it is treated as a number; a library function returning `bool` is wrapped in `cast_bool`
  where a bool is needed.

## Running the result

```bash
PYNE_NO_LOGO=1 pyne --workdir <workdir> run my_script.py demo          # always with the .py suffix
PYNE_NO_LOGO=1 pyne --workdir <workdir> run my_script.py ccxt_BINANCE_BTC_USDT_60 --from 2025-01-01 --to 2025-06-30
python <workdir>/scripts/my_script.py <workdir>/data/demo.ohlcv          # standalone footer: one argument, a CSV or .ohlcv path;
                                                                        # prints "Running my_script.py on demo (2000 bars)..." and
                                                                        # writes my_script.csv NEXT TO THE SCRIPT (not in output/)
```

`pyne run` writes `output/<name>.csv` (one row per bar with every plot), for a strategy
`output/<name>_strat.csv` and `_trade.csv`, and `scripts/<name>.toml` with the inputs. The
`pynecore-cli` skill has every option (`../../pynecore-cli/references/commands.md`); the
`pynecore-strategy` skill has the wrapper `run_backtest.py` and the report helpers.

Requirements of the generated code:

- PyneCore **6.12.1 or newer**: every name the 1005 corpus modules import exists there, and a
  run of the 920 corpus scripts (indicators and strategies; libraries are not runnable) on
  6.12.1 and again on 6.13.2 over three months of hourly BTCUSDT candles ended with exit 0 for
  794 (the same 794 on both versions, 436 s and 526 s with 8 parallel runs); of the 126
  others, 121 stopped at `ValueError: No OHLCV data found for security context (...)` because
  they call `request.security` and the run had no `--security` files, one hit the 180 s
  timeout, one raised the script's own `runtime.error` (a timeframe check), and three failed
  with a Python error in the generated code (`UnboundLocalError` on a renamed local,
  `IndexError` from `array.get` inside a loop), i.e. 3 of 920 are candidate pine2pyne or
  source bugs. Prefer the newest release: PyneSys issue #107 lists runtime behaviours the
  maintainer fixed in **6.13.2** (v6 `for` edge cases, `bool()` of a constant, ...). pine2pyne's `pynecore_vs_pine.md`
  item 16 notes one v6 `for` header case (a stateful function call in the end bound) that
  PyneCore 6.13's `pine_loop(..., entry=True)` handles and pine2pyne 0.2.11 does not yet write.
- `pip install "pynesys-pynecore[cli]" "typer<0.29"`: with typer 0.29.0 the `pyne` command of
  6.12.1 and 6.13.2 fails at import (`ModuleNotFoundError: No module named 'typer._types'`,
  `pynecore/cli/pluggable.py`). The runtime itself (standalone footer, `ScriptRunner`) is
  unaffected.
- Python 3.11+. On 3.13 a parameter annotation such as `dict[str, Color]` is evaluated at
  definition time, which is why pine2pyne imports `Color` for map value types.

## Differences from the cloud compiler and from Pine (what to expect in results)

pine2pyne's default output follows the cloud compiler's layout but **Pine's semantics** where
the two disagree; the evidence (TradingView measurements, probes, runtime tests) is in
pine2pyne's `docs/pynecore_vs_pine.md`, and the items were reported to PyneSys as issues #107
and #108. The ones that change numbers:

| Topic | Pine | What pine2pyne writes |
|-------|------|-----------------------|
| v6 `for` end bound | Re-read before every pass | `pine_loop` + `while` (v4/v5: `pine_range`, end read once) |
| v5 `bool(x)` | Exact; `bool(na)` is `na` | `na(bool) if na(x) else bool(x)`; `cast_bool` only where the result is the same |
| v4/v5 `bool` | Tri-state, history `na` before warm-up | `na_bool=True` where the script reads `na` of a bool |
| v6 bool `na` arm (`c ? b : na`) | `false` | `False` |
| v4/v5 `and` / `or` | Strict: the right operand runs every bar | Stateful operands hoisted before the expression, evaluated every bar |
| `strategy.entry/close(..., when=c)` (v4/v5) | Arguments every bar, order only when `c` | `if c:` around the call, stateful arguments hoisted |
| v4 `offset(s, n)`, `label.style_labelup` | Valid v4 | `s[n]`, `label.style_label_up` |
| User type or variable named like a namespace (`session`, `fill`) | Allowed | `<name>__ren__` |
| `strategy(...)` positional arguments past the 9th | Pine's order | Passed by keyword |
| v4/v5 `strategy.exit(stop=..., loss=...)` | The price parameter wins over its tick twin | `loss=x + (0.0 if na(stop) else na)` |
| A callee reading a global named like a parameter | The global | Parameter renamed `<name>__<id>__` |
| `ta.change(x)` as a ternary arm or inside `math.abs/max` in a condition | The numeric change | The number (the cloud compiler wrapped it in `cast_bool`) |
| `max(0, floor(a / b))` | `floor` first | `math.floor(a / b)` (no `cast_int` truncation first) |
| v4 `transp` (`bgcolor(c, 50)`, `plot(..., transp=tp)`, `color.new (c, 50)` with a space) | Transparency | `color.new(c, t)` in all three cases |
| Stateful user method on an untyped receiver | State per call site | Called directly, not through `method_call` (which shares state) |
| `ta.*` call under a condition | History advances only when called | Kept in place + `conditional-history-call` warning |

Things pine2pyne does not change (identical in Pine and PyneCore, or not measured): `x += <if>`
evaluation order, string literals continued over lines, `x == na` on numbers. Read
`docs/pynecore_vs_pine.md` section 3 in the pine2pyne repository before claiming a bar-by-bar
match on those.
