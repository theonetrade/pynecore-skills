# Inputs and settings: `input.*`, the script `.toml`, runtime overrides

## Contents

- [Declaring inputs](#declaring-inputs)
- [Input functions](#input-functions)
- [The script's `.toml` file](#the-scripts-toml-file)
- [Precedence](#precedence)
- [Programmatic overrides](#programmatic-overrides)
- [Environment variables](#environment-variables)

## Declaring inputs

Inputs are the parameters of `main()`, with an `input.*()` call as the default value:

```python
from pynecore.lib import script, input, close, ta, strategy, color
from pynecore.types import Series


@script.strategy("Example", overlay=True)
def main(
    src: Series[float] = input.source(close, "Source"),
    fast: int = input.int(9, "Fast length", minval=1, maxval=200, group="Averages"),
    slow: int = input.int(21, "Slow length", minval=1, maxval=400, group="Averages"),
    ma_type: str = input.string("EMA", "MA type", options=("SMA", "EMA", "WMA")),
    risk_pct: float = input.float(1.0, "Risk per trade %", minval=0.1, maxval=10, step=0.1),
    allow_short: bool = input.bool(True, "Allow shorts"),
    start: int = input.time(timestamp("2020-01-01"), "Start date"),
    sess: str = input.session("0930-1600", "Session"),
    htf: str = input.timeframe("D", "Trend timeframe"),
    up_color: color.Color = input.color(color.teal, "Up color"),
):
```

Rules that matter:

- The parameter **name** is the key everywhere else: the `[inputs.<name>]` section of the `.toml`
  and the `inputs={"name": value}` dict of `ScriptRunner`. The `title` is only a label.
- An `input.*()` call inside the body of `main()` returns its default and is not configurable.
  Keep inputs in the signature.
- Validation bounds (`minval`, `maxval`, `step`, `options`) are metadata; they are written into
  the `.toml` and shown by tooling. Clamp values yourself if an out-of-range value would break
  the logic.
- A `Series[float]` annotation on a `source` input lets you index its history (`src[1]`).

## Input functions

| Function | Value type | Extra parameters |
|----------|-----------|------------------|
| `input(defval, title=None, tooltip=None, inline=None, group=None, display=None)` | inferred from `defval` | |
| `input.int(defval, title=None, minval=, maxval=, step=, options=, ...)` | int | |
| `input.float(defval, title=None, minval=, maxval=, step=, options=, ...)` | float | |
| `input.bool(defval, title=None, ...)` | bool | |
| `input.string(defval, title=None, options=("A", "B"), ...)` | str | `options` is a tuple |
| `input.source(defval, title=None, ...)` | float series | `defval` is `close`, `open`, `high`, `low`, `hl2`, `hlc3`, `ohlc4`, `hlcc4` (the object or its name as a string) |
| `input.color(defval, title=None, ...)` | `Color` | |
| `input.enum(defval, title=None, options=, ...)` | a `StrEnum` member | define `class Side(StrEnum): Long = "Long"` |
| `input.timeframe(defval, title=None, options=, ...)` | str | timeframe string such as `"60"`, `"D"` |
| `input.symbol(defval, title=None, ...)` | str | |
| `input.session(defval, title=None, options=, ...)` | str | `"0930-1600"`, `"0930-1600:23456"` |
| `input.time(defval, title=None, ...)` | int (ms) | use `timestamp("2024-01-01")` as default |
| `input.price(defval, title=None, ...)` | float | |
| `input.text_area(defval, title=None, ...)` | str | |

Common keyword parameters: `title`, `tooltip`, `inline` (inputs sharing a value sit on one
row), `group` (section name), `confirm`, `display`.

Everything that is not an input (lookback caps, magic numbers you do not want exposed) can be a
plain module-level constant. Module-level objects are read-only inside functions; see
[language.md](language.md).

## The script's `.toml` file

The first time a script is imported (by `pyne run`, by `ScriptRunner`, or by `import_script`)
PyneCore writes `<script>.toml` next to it:

```toml
# Indicator / Strategy / Library Settings

[script]
#overlay = true
#initial_capital = 100000
#default_qty_type = "percent_of_equity"
#default_qty_value = 100
#commission_type = "percent"
#commission_value = 0.0
#process_orders_on_close = false
# ... every other setting, commented out

# Input Settings

[inputs.fast]
# Input metadata, cannot be modified
# input_type: "int"
#     defval: 9
#      title: "Fast length"
#     minval: 1
# Change here to modify the input value
#value =
```

- Uncomment a `[script]` line and change its value to override a decorator setting.
- Set `value = 12` under `[inputs.fast]` to override an input.
- The file is rewritten on every import with your overrides preserved and every other line
  commented out, so it stays a complete template.
- Set `PYNE_SAVE_SCRIPT_TOML=0` to stop PyneCore from writing it (read-only script folders,
  tests).

Treat the `.toml` as part of the strategy: commit it, and when you change the code's inputs,
check that the `.toml` still names them (a renamed parameter leaves a stale section behind that
is simply ignored).

## Precedence

Lowest to highest:

1. The script itself: decorator arguments and `input.*()` defaults.
2. The `.toml` next to the script.
3. `ScriptRunner(inputs=..., settings=...)` for that run only.

Programmatic overrides are never written back to the `.toml`.

## Programmatic overrides

```python
from pynecore.lib import strategy

runner = ScriptRunner(
    script_path, candles, syminfo,
    inputs={"fast": 12, "slow": 26},                 # main() parameter names
    settings={                                       # decorator argument names
        "initial_capital": 50_000,
        "default_qty_type": strategy.percent_of_equity,
        "default_qty_value": 10,
        "commission_type": strategy.commission.percent,
        "commission_value": 0.05,
        "process_orders_on_close": True,
    },
)
```

- An unknown input key raises `ValueError` listing the script's input names; an unknown setting
  key raises `ValueError` too. Typos fail loudly.
- `runner.script.settable_fields()` lists every setting name; `runner.script.<name>` reads the
  effective value and `runner.script.default(name)` the decorator's.
- Overrides apply to the main script only, not to libraries it imports. Security contexts
  inherit the same overrides.

To read a script's declarations without running it:

```python
from pynecore.core.script_runner import import_script

module = import_script(Path("my_strategy.py"))
script = module.main.script
for name, data in script.inputs.items():
    print(name, data.input_type, data.defval, data.title)
```

## Environment variables

| Variable | Effect |
|----------|--------|
| `PYNE_WORK_DIR` | Working directory (same as `pyne --workdir`) |
| `PYNE_DATA_DIR` | Use another folder for OHLCV data |
| `PYNE_SAVE_SCRIPT_TOML=0` | Do not write script `.toml` files |
| `PYNE_NO_LOGO=1` / `PYNE_QUIET=1` | Suppress the CLI logo (use in agent runs) |
| `NO_COLOR=1` | Plain output |
| `PYNE_SECURITY_LOG=path` | Redirect `log.*` output of `request.security` contexts to a file |
| `PYNE_AST_DEBUG=1`, `PYNE_AST_SAVE=1` | Dump the transformed source (debugging) |
