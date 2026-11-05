# CLI and data: the workdir, `pyne run`, `pyne data`, symbol info, output files

## Contents

- [The working directory](#the-working-directory)
- [Finding the `pyne` command](#finding-the-pyne-command)
- [`pyne run`](#pyne-run)
- [Chart timeframe and bar magnifier](#chart-timeframe-and-bar-magnifier)
- [Getting data](#getting-data)
- [Symbol info (`data/<name>.toml`)](#symbol-info-datanametoml)
- [Output files](#output-files)
- [Other commands](#other-commands)

## The working directory

PyneCore organizes a project as a **workdir**:

```
workdir/              # or the project root itself
├── .pyne             # marker: workdir_version = "1.0"
├── scripts/          # Pyne scripts (.py) and their .toml settings
│   └── lib/          # your libraries, importable as lib.<...>
├── data/             # <name>.ohlcv binary candles + <name>.toml symbol info (+ .extra.csv)
├── output/           # <script>.csv, <script>_strat.csv, <script>_trade.csv, logs/
├── config/           # api.toml, plugins/<plugin>.toml, symbol_map.toml, brokers.toml
└── cache/
```

Discovery, starting from the current directory and walking up to 10 parents: a directory
containing `.pyne` is the workdir; otherwise a `workdir/` child of it. Nothing found means the
CLI offers to create `./workdir` (interactive prompt). Override with `pyne --workdir PATH` or
`PYNE_WORK_DIR=PATH`.

Non-interactive creation that the CLI accepts:

```bash
mkdir -p workdir/scripts/lib workdir/data workdir/output workdir/config
printf 'workdir_version = "1.0"\n' > workdir/.pyne
```

Or let the CLI build a complete one with a demo script and demo data:

```bash
pyne --workdir ./workdir --recreate-demo plugin list
```

Or, for a brand-new project including the Python environment, use this skill's
`scripts/init_project.py <dir> --smoke` (layout, `pyproject.toml` + `.venv` with PyneCore, demo,
`pyrightconfig.json`, `.gitignore`; no agent or editor files).

The demo is `scripts/demo.py` on `data/demo.ohlcv` (2000 synthetic daily bars, 24/7). It is the
quickest smoke test for a new strategy: `pyne run my_strategy demo`.

Bare names in commands resolve inside the workdir: a script name looks in `scripts/`
(extension optional), a data name in `data/` (`.ohlcv` first, then `.csv`).

## Finding the `pyne` command

`pyne` is the console script of the `pynesys-pynecore[cli]` package and lives in the virtual
environment that has PyneCore installed: `.venv/bin/pyne`, `venv/bin/pyne`, an IDE-managed
`.pyneide/venv/bin/pyne`, or `uv run pyne` in a `uv` project. The agent's shell usually does not
have that environment activated, so call it by path. `scripts/pyne_env.py` in this skill prints
the resolved paths; `python -m pynecore` does **not** exist.

Install when missing:

```bash
pip install "pynesys-pynecore[cli]"          # CLI
pip install "pynesys-pynecore[cli,ccxt]"     # + CCXT data provider (crypto exchanges)
uv add "pynesys-pynecore[cli,ccxt]"          # in a uv project
```

Python 3.11+ is required; profiling needs 3.13+.

## `pyne run`

```bash
pyne run SCRIPT DATA [options]
```

| Option | Meaning |
|--------|---------|
| `--from, -f` | Start: `YYYY-MM-DD`, `YYYY-MM-DD HH:MM:SS` (UTC), a number of days back (`30`), or in provider mode a negative bar count (`-500`, the default there) |
| `--to, -t` | End: date or days back |
| `--timeframe, -tf` | Chart timeframe larger than the data's (`5`, `60`, `240`, `1D`, `1W`); aggregates on the fly, or enables the bar magnifier for strategies |
| `--plot, -pp PATH` / `--no-plot` | Plot CSV path, or skip it |
| `--strat, -sp PATH`, `--trade, -tp PATH` | Statistics and trade CSV paths |
| `--viz`, `--viz-path PATH`, `--viz-journal` | NDJSON with plot styles and drawings (for your own renderer) |
| `--security, -sec "KEY=data_name"` | Data for `request.security()` contexts; repeatable. KEY is `TIMEFRAME`, `SYMBOL` or `SYMBOL:TIMEFRAME` |
| `--list-data` | Print the data the script's `request.security()` calls need, do not run |
| `--live, -l` | Continue with streaming data after the history (provider mode only) |
| `--broker` | Route orders to a broker plugin (implies `--live`) |
| `--profile`, `--profile-top N` | Per-line timing report (Python 3.13+) |

Explicit output paths are used as given (a bare filename lands in the current directory, not
in `output/`).

`DATA` can be:

- a bare name of a file in `data/`;
- a path to `.ohlcv`, `.csv`, `.json` or `.txt`. Non-`.ohlcv` files are converted automatically
  into `.ohlcv` + `.toml` next to the source, with the symbol guessed from the filename
  (`BTCUSDT.csv`, `BINANCE_ETHUSDT_1h.csv`, `EUR_USD.csv`);
- a provider string `provider:EXCHANGE:SYMBOL[:SETTLE]@TIMEFRAME`, for example
  `ccxt:BYBIT:BTC/USDT:USDT@1D` or `ccxt:BINANCE:ETH/USDT@240`, which downloads first.

Always pass `PYNE_NO_LOGO=1` (or `PYNE_QUIET=1`) in automated runs to keep output parseable.

Examples:

```bash
PYNE_NO_LOGO=1 .venv/bin/pyne run my_strategy demo
PYNE_NO_LOGO=1 .venv/bin/pyne run my_strategy ccxt_BYBIT_BTC_USDT_USDT_1D --from 2023-01-01 --to 2024-12-31
PYNE_NO_LOGO=1 .venv/bin/pyne run my_strategy ./exports/BTCUSDT_1h.csv --strat out/stats.csv --trade out/trades.csv
PYNE_NO_LOGO=1 .venv/bin/pyne run my_strategy ccxt:BYBIT:BTC/USDT:USDT@60 -f -2000
PYNE_NO_LOGO=1 .venv/bin/pyne run mtf_strategy EURUSD_5 --security "1D=EURUSD_1D"
```

## Chart timeframe and bar magnifier

`--timeframe TF` makes the script see bars of `TF` built from finer data. `TF` must be an exact
multiple of the data's timeframe.

- **Indicator, or strategy with `use_bar_magnifier=False`**: plain on-the-fly aggregation.
- **Strategy with `use_bar_magnifier=True` (default)**: the script still runs once per chart
  bar on aggregated OHLCV, but limit/stop/trailing fills are checked against each sub-bar in
  order. This removes the guess about whether the high or the low came first inside a bar, which
  is exactly where a stop and a target in the same bar would otherwise be decided by a heuristic.
  Finer data is more accurate and slower (1-minute data on a daily chart is ~1440 checks per bar;
  60-minute data is a good default).

```bash
pyne run my_strategy BTCUSDT_10m.ohlcv --timeframe 60     # 10m data, 1h chart, magnified fills
pyne run my_indicator BTCUSDT_1m.ohlcv --timeframe 5       # 1m data aggregated to 5m
```

The same from Python: `ScriptRunner(magnifier_iter=<fine bars>, magnifier_source_tf="10")`
while `ohlcv_iter` carries the chart bars.

## Getting data

### Download through a provider

```bash
pyne data download ccxt:BYBIT:BTC/USDT:USDT@1D                 # perpetual, daily
pyne data download ccxt:BINANCE:ETH/USDT@240 --from 2023-01-01  # spot, 4h
pyne data download ccxt:BINANCE:BTC/USDT@60 --from 90           # last 90 days
pyne data download ccxt --list-brokers                          # exchanges
pyne data download ccxt:BYBIT --list-symbols
pyne data download ccxt:BYBIT:BTC/USDT:USDT@1D --symbol-info    # show the derived symbol info
```

Timeframes are PyneCore strings: minutes as a number (`1`, `5`, `15`, `60`, `240`), `1D`, `1W`,
`1M`. The default `--from` is `continue` (resume from the last stored bar, or one year back
when nothing is stored). Files land in `data/` as
`ccxt_BYBIT_BTC_USDT_USDT_1D.ohlcv` + `.toml`. Provider list: `pyne plugin list`; the built-in
`ccxt` provider needs the `ccxt` extra. Other providers (Capital.com, cTrader, Bybit broker,
...) are separate plugins.

### Convert your own CSV / JSON / TXT

```bash
pyne data convert-from ./exports/BTCUSDT_1h.csv                       # symbol guessed from name
pyne data convert-from ./exports/candles.csv --symbol "BTC/USDT" --provider binance --timezone UTC
```

The converter detects columns (`timestamp`/`time`/`date`/`datetime`, `open`, `high`, `low`,
`close`, `volume`), the timeframe, the tick size, the symbol type (forex, crypto, other) and
opening hours, and writes `.ohlcv` + `.toml`. Timestamps may be ISO strings (with or without
offset) or epoch seconds/milliseconds. Columns beyond OHLCV become
`extra_fields` readable inside the script (see [lib.md](lib.md)). An existing `.toml` is kept
and must match the data's timeframe.

### Aggregate to a higher timeframe

```bash
pyne data aggregate ccxt_BINANCE_BTC_USDT_5 -tf 60      # 5m -> 1h
pyne data aggregate capitalcom_EURUSD_1D -tf 1W         # daily -> weekly
```

Only upscaling; the target must be an exact multiple (intraday, daily) or a calendar period
built from intraday/daily data (weekly, monthly). Output name replaces the timeframe suffix;
the `.toml` is copied with `period` updated.

### Export

```bash
pyne data convert-to ccxt_BINANCE_BTC_USDT_1D --format csv --as-datetime
```

### Provider plugin configuration

`pyne --recreate-provider-config` writes one commented `config/plugins/<plugin>.toml` per
installed plugin. For the built-in CCXT provider (`config/plugins/ccxt.toml`) the fields map to
CCXT constructor arguments, so any CCXT option can be added by name:

```toml
#apiKey = ""            # only needed for private endpoints; public candles need no key
#secret = ""
#password = ""          # some exchanges (KuCoin) require it
#sandbox = false        # exchange testnet / demo endpoint
#default_type = "swap"  # "spot" / "swap" / "future" / "margin"
#symbol_map = {}        # {"FX:EURUSD" = "EURUSD"}: script symbol -> exchange symbol for live feeds
```

Keep this folder out of version control; it may hold exchange keys.

### Symbol map (for `request.security`)

```bash
pyne data map "BINANCE:BTCUSDT" "ccxt:BYBIT:BTC/USDT:USDT"
```

Writes `config/symbol_map.toml` so `request.security("BINANCE:BTCUSDT", ...)` finds the data
without a `--security` flag. See [multi-timeframe.md](multi-timeframe.md).

## Symbol info (`data/<name>.toml`)

Every `.ohlcv` has a `.toml` with the instrument's metadata. Scripts read it through `syminfo.*`
and the strategy engine uses it for tick rounding, lot rounding, point value and sessions.

```toml
[symbol]
prefix = "BYBIT"
description = "BTC/USDT:USDT"
ticker = "BTCUSDT"
currency = "USDT"
basecurrency = "BTC"     # bare base asset code; drives crypto quantity rounding
period = "1D"
type = "crypto"          # stock, forex, crypto, futures, index, fund, bond, ...
mintick = 0.1
pricescale = 10
minmove = 1
pointvalue = 1.0
mincontract = 0.001      # smallest tradable quantity step
timezone = "UTC"
session = "regular"
volumetype = "base"
taker_fee = 0.00055      # informational
maker_fee = 0.0002

[[opening_hours]]        # one block per weekday, day 0 = Monday
day = 0
start = "00:00:00"
end = "23:59:59"
# ... session_starts / session_ends blocks, optional session_schedules history,
# optional extended_hours for stocks
```

**Review the generated `.toml` before trusting a backtest.** The downloader and converter derive
it heuristically. Wrong `mintick`, `pointvalue`, `mincontract`, `basecurrency` or `timezone`
silently distorts fills, sizes, P&L and session logic. Edit the file by hand when needed.

## Output files

| File | Content |
|------|---------|
| `output/<script>.csv` | `time,open,high,low,close,volume` + one column per plot title; `NaN` for missing |
| `output/<script>_strat.csv` | Performance metrics (see [strategy-api.md](strategy-api.md#statistics-and-trade-csv-files)) |
| `output/<script>_trade.csv` | Entry/exit rows per trade |
| `output/<script>_viz.ndjson` | Only with `--viz`: plot styles, per-bar values and colors, drawings |
| `output/<script>_profile.json` | Only with `--profile` |
| `output/logs/` | Run logs |

Standalone execution (`python my_strategy.py data.csv`, see
[programmatic.md](programmatic.md)) writes `my_strategy.csv`, `my_strategy_trades.csv` and
`my_strategy_strat.csv` **next to the script** instead (note `_trades` with an `s`).

## Other commands

| Command | Purpose |
|---------|---------|
| `pyne plugin list`, `pyne plugin info NAME` | Installed provider/broker plugins |
| `pyne debug ast SCRIPT` | Print the transformed source of a script (what actually runs) |
| `pyne benchmark ...` | Performance measurement |
| `pyne broker resume RUN_ID QUARANTINE_ID` | Resume a quarantined live broker run |
| `pyne --recreate-provider-config`, `--recreate-api-config` | Regenerate `config/` files |
| `pyne --install-completion` | Shell completion |
