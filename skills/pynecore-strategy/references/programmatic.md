# Programmatic use: `ScriptRunner`, `OHLCV`, `SymInfo`, standalone scripts, sweeps

Run Pyne scripts from your own Python: backtest pipelines, parameter sweeps, bots, notebooks.
The CLI is a thin wrapper around the same classes.

## Contents

- [Standalone execution of a script file](#standalone-execution-of-a-script-file)
- [`ScriptRunner`](#scriptrunner)
- [Data: `OHLCV` objects](#data-ohlcv-objects)
- [Symbol info: `SymInfo`](#symbol-info-syminfo)
- [Reading results](#reading-results)
- [Trades](#trades)
- [Parameter sweeps](#parameter-sweeps)
- [Saving CSV and viz output](#saving-csv-and-viz-output)
- [Importing without running](#importing-without-running)
- [Performance notes](#performance-notes)

## Standalone execution of a script file

Append this to any script and it runs on a data file without a workdir or the CLI:

```python
if __name__ == "__main__":
    from pynecore.standalone import run
    run(__file__)
```

```bash
python my_strategy.py data/BTCUSDT_1h.csv     # or .ohlcv
uv run my_strategy.py data/BTCUSDT_1h.csv     # with inline script metadata, see below
```

It converts a CSV/JSON/TXT in a temporary folder (symbol guessed from the filename), loads
the matching `.toml`, runs every bar and writes `my_strategy.csv`, `my_strategy_trades.csv`,
`my_strategy_strat.csv` next to the script. It does not support `request.security()` data or
`--timeframe`; use `pyne run` or `ScriptRunner` for those.

For a self-contained file that `uv run` can execute anywhere, put inline metadata at the very
top, **before** the `@pyne` docstring (comments are allowed before the docstring):

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["pynesys-pynecore[cli]>=6.12"]
# ///

"""
@pyne
"""
```

## `ScriptRunner`

```python
from pathlib import Path
from pynecore.core.script_runner import ScriptRunner, LIVE_TRANSITION, import_script
from pynecore.core.syminfo import SymInfo
from pynecore.core.ohlcv import OHLCVReader
from pynecore.core.data_converter import DataConverter
from pynecore.types.ohlcv import OHLCV

runner = ScriptRunner(
    script_path: Path,                 # the .py with the @pyne docstring
    ohlcv_iter: Iterable[OHLCV],       # list, generator, OHLCVReader.read_from(...)
    syminfo: SymInfo,
    *,
    inputs: dict | None = None,        # {"param_name": value}; unknown key -> ValueError
    settings: dict | None = None,      # decorator settings; unknown key -> ValueError
    plot_path: Path | None = None,     # plot CSV
    strat_path: Path | None = None,    # statistics CSV
    trade_path: Path | None = None,    # trade list CSV
    viz_path: Path | None = None, viz_journal: bool = False,
    security_data: dict[str, str | Path] | None = None,   # {"1D": "data/EURUSD_1D"} base paths without extension
    magnifier_iter: Iterable[OHLCV] | None = None, magnifier_source_tf: str | None = None,
    last_bar_index: int | None = None, last_bar_time: int | None = None,
    live: bool = False,
    config_dir: Path | None = None,    # workdir config/ for symbol_map.toml
    profile_path: Path | None = None,  # Python 3.13+
)
```

`last_bar_index` / `last_bar_time` matter for `barstate.islast`, `last_bar_index` and
`calc_bars_count`. With a **list or tuple** as `ohlcv_iter` (outside live mode) the runner reads
them from the last element; with a generator or reader pass them yourself
(`reader.size - 1`, `reader.get_last_bar_time(start, end)`), otherwise they track the current
bar.

Minimal backtest from a CSV:

```python
csv_path = Path("data/BTCUSDT_1h.csv")
DataConverter().convert_to_ohlcv(csv_path)          # writes BTCUSDT_1h.ohlcv + .toml next to it (skips if current)
syminfo = SymInfo.load_toml(csv_path.with_suffix(".toml"))

with OHLCVReader(csv_path.with_suffix(".ohlcv")) as reader:
    bars = list(reader.read_from(reader.start_timestamp, reader.end_timestamp))

runner = ScriptRunner(Path("scripts/my_strategy.py"), bars, syminfo,
                      inputs={"fast": 12}, settings={"default_qty_value": 10})
trades = []
for candle, plot_data, new_trades in runner.run_iter():
    trades.extend(new_trades)
```

`DataConverter().convert_to_ohlcv(path, *, force=False, provider=None, symbol=None,
timezone="UTC")`. `OHLCVReader` exposes `size`, `start_timestamp`, `end_timestamp`,
`start_datetime`, `end_datetime`, `period`, `read_from(start_ts, end_ts=None)`,
`get_size(start, end)`, `get_last_bar_time(start, end)`, `save_to_csv(...)`.

`scripts/run_backtest.py` in this skill wraps all of this into one command with input/setting
overrides, date filters and parameter sweeps.

## Data: `OHLCV` objects

```python
from pynecore.types.ohlcv import OHLCV

bar = OHLCV(timestamp=1704067200000,   # epoch MILLISECONDS
            open=42000.0, high=42500.0, low=41800.0, close=42300.0, volume=1000.0,
            extra_fields=None,          # dict of extra per-bar values, optional
            is_closed=True)             # False = update of a forming bar (live mode)
```

Timestamps are milliseconds. CCXT already returns milliseconds; a source in seconds must be
multiplied by 1000. Bars must be in ascending time order with one entry per bar.

From any source:

```python
def from_dataframe(df):                      # pandas with a DatetimeIndex or a "date" column
    idx = pd.DatetimeIndex(df["date"]) if "date" in df.columns else df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    ts = (idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)
    return [OHLCV(int(t), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume))
            for t, r in zip(ts, df.itertuples())]

def from_ccxt(exchange, symbol, timeframe, limit):
    return [OHLCV(timestamp=b[0], open=b[1], high=b[2], low=b[3], close=b[4], volume=b[5])
            for b in exchange.fetch_ohlcv(symbol, timeframe, limit=limit)]

def from_db(cursor):
    cursor.execute("SELECT ts, o, h, l, c, v FROM candles ORDER BY ts")
    for row in cursor:
        yield OHLCV(*row)
```

`templates/custom_feed.py` is a runnable version.

## Symbol info: `SymInfo`

```python
from pynecore.core.syminfo import SymInfo

syminfo = SymInfo.load_toml(Path("data/BTCUSDT_1h.toml"))     # from a converted/downloaded file

syminfo = SymInfo(                                              # by hand
    prefix="BINANCE", description="BTC / USDT", ticker="BTCUSDT",
    currency="USDT", basecurrency="BTC",
    period="60",               # "1", "5", "15", "60", "240", "D", "W", "M"
    type="crypto",             # "stock", "forex", "crypto", "futures", "index", ...
    mintick=0.01, pricescale=100, minmove=1, pointvalue=1.0,
    mincontract=0.00001,       # lot step
    timezone="UTC", volumetype="base",
    opening_hours=[], session_starts=[], session_ends=[],   # empty = 24/7
)
syminfo.save_toml(path)        # write it out
```

Required fields: `prefix`, `description`, `ticker`, `currency`, `period`, `type`, `mintick`,
`pricescale`, `pointvalue`, `mincontract`, `opening_hours`, `session_starts`, `session_ends`.
Forex example: `mintick=0.0001, pricescale=10000, mincontract=1.0, timezone="America/New_York",
volumetype="tick"`. Stocks: `mintick=0.01, pricescale=100, mincontract=1.0,
timezone="America/New_York"` plus real session hours (`SymInfoInterval(day, start, end)` and
`SymInfoSession(day, time)` from `pynecore.core.syminfo`).

`basecurrency` must be the bare asset code (`"BTC"`), it selects the quantity rounding
precision for crypto sizing.

## Reading results

`runner.run_iter()` yields per bar:

- indicator: `(candle, plot_data)`;
- strategy: `(candle, plot_data, new_trades)`.

`plot_data` is `{title: value}` for every `plot*()` call and every key of the dict `main()`
returns. Missing values are a Python `float('nan')`, and **outside** Pyne code Python's own rules
apply: `nan < 30` and `nan > 70` are both `False`, `bool(nan)` is `True`, `nan != x` is `True`.
Always test with `math.isnan(v)` (or `pynecore.lib.na(v)`) before using a value.

`new_trades` holds the trades that **closed** on this bar; each trade appears once, on its exit
bar. Open positions at the end of the data are in `runner.script.position.open_trades`.

`run_iter(copy_results=False)` hands out the runner's own containers for speed; read the values
inside the loop and do not keep the dict/list themselves.

`runner.run()` processes everything without yielding (use it when only the CSV outputs matter).
`runner.script` is the effective settings object (`initial_capital`, `settable_fields()`,
`default(name)`, `inputs`).

## Trades

Fields of a `Trade`: `size` (signed, long > 0), `entry_id`, `entry_bar_index`, `entry_time`
(ms), `entry_price`, `entry_comment`, `exit_id`, `exit_bar_index`, `exit_time`, `exit_price`,
`exit_comment`, `profit`, `profit_percent`, `cum_profit`, `cum_profit_percent`, `max_runup`,
`max_runup_percent`, `max_drawdown`, `max_drawdown_percent`, `commission`.

```python
wins = [t for t in trades if t.profit > 0]
net = sum(t.profit for t in trades)
pf = sum(t.profit for t in wins) / abs(sum(t.profit for t in trades if t.profit <= 0) or 1)
```

For the full statistics table (Sharpe, drawdown on the equity curve, long/short split) pass
`strat_path=` and read the CSV, or use `pynecore.core.strategy_stats.calculate_strategy_statistics`.

## Parameter sweeps

```python
from itertools import product

data = list(bars)                       # materialize once, reuse for every run
results = []
for fast, slow in product(range(5, 30, 5), range(20, 80, 10)):
    if fast >= slow:
        continue
    runner = ScriptRunner(script, data, syminfo, inputs={"fast": fast, "slow": slow},
                          settings={"default_qty_type": "percent_of_equity", "default_qty_value": 10,
                                    "commission_type": "percent", "commission_value": 0.05})
    trades = [t for _, _, new in runner.run_iter() for t in new]
    results.append({"fast": fast, "slow": slow, "trades": len(trades),
                    "pnl": sum(t.profit for t in trades)})
best = max(results, key=lambda r: r["pnl"])
```

Overrides never touch the script's `.toml`. Always sweep **with** realistic commission and
slippage, keep an untouched out-of-sample period, and prefer a parameter plateau (neighbors are
also good) over the single best cell. `scripts/run_backtest.py --sweep` does this from the
command line.

## Saving CSV and viz output

```python
runner = ScriptRunner(script, bars, syminfo,
                      plot_path=Path("out/plot.csv"),
                      strat_path=Path("out/stats.csv"),
                      trade_path=Path("out/trades.csv"),
                      viz_path=Path("out/viz.ndjson"), viz_journal=True)
runner.run()      # files are complete once the iteration finishes
```

The viz NDJSON carries plot styles, per-bar values and colors and drawing objects for a
renderer of your own; `runner.plot_meta` and `runner.drawings()` expose the same state in
memory.

## Importing without running

```python
module = import_script(Path("my_strategy.py"), inputs={...}, settings={...})
script = module.main.script
script.inputs            # {name: InputData(input_type, defval, title, ...)}
script.settable_fields() # every settings key
```

Each call executes the module from the top as a fresh import (and writes the `.toml` unless
`PYNE_SAVE_SCRIPT_TOML=0`).

## Performance notes

- Materialize `list(reader.read_from(...))` once when several scripts or sweep points use the
  same data.
- A simple script does ~1000 bars in a few milliseconds; 5000 bars in tens of milliseconds.
- Frameworks that call you on every new candle with the full history (FreqTrade and the like)
  should re-run the whole script over the full frame rather than only the new bars, because the
  script's state (averages, `Persistent` variables, the position) depends on every earlier bar.
- `profile_path=` (or `pyne run --profile`) shows per-line time and the split between script,
  strategy engine, output and data feed.
