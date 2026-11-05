# `pyne` commands (PyneCore 6.12)

Taken from the installed CLI's `--help` pages and the PyneCore documentation, checked by
running the commands. `scripts/cli_help.py --check` compares this file with the installed CLI.

## Contents

- [Conventions: value formats, paths, exit codes](#conventions-value-formats-paths-exit-codes)
- [`pyne` (global options)](#pyne-global-options)
- [`pyne run`](#pyne-run)
- [`pyne data download`](#pyne-data-download)
- [`pyne data convert-from`](#pyne-data-convert-from)
- [`pyne data convert-to`](#pyne-data-convert-to)
- [`pyne data aggregate`](#pyne-data-aggregate)
- [`pyne data map`](#pyne-data-map)
- [`pyne plugin list`](#pyne-plugin-list)
- [`pyne plugin info`](#pyne-plugin-info)
- [`pyne debug ast`](#pyne-debug-ast)
- [`pyne benchmark`](#pyne-benchmark)
- [`pyne broker resume`](#pyne-broker-resume)
- [`pyne compile`](#pyne-compile)
- [Commands and options added by plugins](#commands-and-options-added-by-plugins)

## Conventions: value formats, paths, exit codes

| Format | Meaning | Where |
|--------|---------|-------|
| `DATE` | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`, UTC; quote the form with a space | `run --from/--to`, `data download --from/--to` |
| `DAYS` | positive integer, that many days back from now | `run --from/--to`, `data download --from` (`--to NUMBER` of download counts from the start date) |
| `-BARS` | negative integer, that many bars before now; provider mode of `run` only | `run --from` |
| `continue` | resume after the last stored bar | `data download --from` (its default) |
| `TIMEFRAME` | minutes as a number (`1`, `5`, `15`, `60`, `240`), or a multiplier with `S`, `D`, `W`, `M` (`5S`, `1D`, `3D`, `1W`, `1M`) | `--timeframe/-tf` everywhere, `@TF` in provider strings |
| provider string | `PROVIDER[:BROKER]:SYMBOL[:SETTLE][@TIMEFRAME]`, e.g. `ccxt:BINANCE:BTC/USDT@60`, `ccxt:BYBIT:BTC/USDT:USDT@1`, `capitalcom:EURUSD@15`. `BROKER` only for multi-broker plugins (`ccxt`). `@TIMEFRAME` is required in `run`, optional in `download` (then `--timeframe`, default `1D`). The provider name is case-insensitive | `run DATA`, `data download PROVIDER` |
| security mapping | `"KEY=VALUE"` with `KEY` = `TIMEFRAME` (`1D`), `SYMBOL` (`SP:SPX`) or `SYMBOL:TIMEFRAME` (`SP:SPX:1D`); `VALUE` = a data name in `data/` or an `.ohlcv` path (backtest), a plugin-native symbol such as `binance:ETH/USDT` with `--live` | `run --security` |
| script path | a path, or a bare name resolved in `<workdir>/scripts/` (`.py` may be omitted) | `run`, `debug ast`, `benchmark` |
| data path | a path, or a bare name resolved in `<workdir>/data/` (`.ohlcv` first, then `.csv`) | `run`, `data convert-to`, `data aggregate`, `benchmark` |
| output path | used exactly as given; a bare file name is written to the current directory, the defaults go to `<workdir>/output/` | `run --plot/--strat/--trade/--viz-path`, `aggregate -o` |

Exit codes: `0` success; `1` an error reported on stderr (file not found, provider or runtime
error, an aborted confirmation prints `Aborted.`); `2` usage error (missing argument, unknown
option or value, a symbol browser that cannot open without a terminal). Uncaught exceptions are
also written to `<workdir>/output/logs/error.log`.

## `pyne` (global options)

```
pyne [--workdir DIR] [--recreate-demo] [--recreate-provider-config] [--recreate-api-config]
     [--install-completion] [--show-completion] [-h] COMMAND [ARGS]...
```

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--workdir` | `-w` | directory | auto-detected (env `PYNE_WORK_DIR`) | The workdir for this invocation; a missing directory is created after a confirmation |
| `--recreate-demo` | | flag | | Rewrite `scripts/demo.py`, `data/demo.ohlcv` and `data/demo.toml` even in an existing workdir |
| `--recreate-provider-config` | | flag | | Regenerate every `config/plugins/<plugin>.toml`; user values and extra tables are kept |
| `--recreate-api-config` | | flag | | Rewrite `config/api.toml` with the empty template |
| `--install-completion` | | flag | | Install shell completion for the current shell |
| `--show-completion` | | flag | | Print the completion script |
| `--help` | `-h` | flag | | Help of `pyne` or of the command that follows |

The global options belong to `pyne` itself and must precede the command. Without a command,
`pyne` prints its help and exits 0. With a command, the startup step runs first: the workdir is
resolved and its structure completed (see `environment.md`), then the command executes.

## `pyne run`

```
pyne run [OPTIONS] SCRIPT DATA
```

| Argument | Format |
|----------|--------|
| `SCRIPT` | A `.py` Pyne script, as a path or a bare name in `scripts/`. (A `.pine` source is handed to the cloud compiler first, see `pyne compile`; not used for Python work.) |
| `DATA` | A data file (`.ohlcv`, or `.csv` / `.json` / `.txt`, converted in place to `.ohlcv` + `.toml` with the symbol guessed from the file name), or a provider string with `@TIMEFRAME` |

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--from` | `-f` | `DATE` \| `DAYS` \| `-BARS` | first bar of the file; provider mode `-500` | First bar to process. `-BARS` is accepted only with a provider string (exactly N real bars are fetched, extending the request through gaps) |
| `--to` | `-t` | `DATE` \| `DAYS` | last bar of the file, or now | Last bar to process |
| `--live` | `-l` | flag | | Keep running on the provider's live stream after the history (provider string required) |
| `--timeframe` | `-tf` | `TIMEFRAME` | the data's | Chart timeframe. Must be the data timeframe or an exact multiple of it. A strategy with `use_bar_magnifier=True` then sees chart-TF bars and fills on the finer data (bar magnifier); otherwise the data is aggregated on the fly |
| `--plot` | `-pp` | path | `output/<script>.csv` | Plot CSV: `time,open,high,low,close,volume` + one column per plot title |
| `--no-plot` | | flag | | Do not write the plot CSV (live runs: the file grows without bound). Not with `--plot` |
| `--strat` | `-sp` | path | `output/<script>_strat.csv` | Strategy statistics CSV (strategies only; rewritten after every closed bar in live mode) |
| `--trade` | `-tp` | path | `output/<script>_trade.csv` | Trade list CSV, entry/exit rows per trade |
| `--viz` | `-vz` | flag | | Write plot styles and drawings as NDJSON to `output/<script>_viz.ndjson` |
| `--viz-path` | | path | | NDJSON path (implies `--viz`) |
| `--viz-journal` | | flag | | Record per-bar drawing create/update/delete events (implies `--viz`) |
| `--api-key` | `-a` | string | `config/api.toml`, env `PYNESYS_API_KEY` | Key for the cloud compiler; irrelevant for `.py` scripts |
| `--broker` | | flag | | Route orders to the venue through a broker plugin (the provider string must name one); implies `--live` |
| `--run-label` | | string | | Distinguishes parallel instances of one strategy + account + symbol + timeframe; stored in the broker run id as `...#LABEL` |
| `--shutdown-timeout` | | float seconds | `120.0` | Graceful shutdown budget on stop; `0` waits forever |
| `--no-log-ohlcv` | | flag | | No per-bar OHLCV log line in live mode |
| `--security` | `-sec` | `"KEY=VALUE"` | | Data for one `request.security()` context; repeatable |
| `--list-data` | | flag | | Print the data the script's `request.security()` calls need and exit without running |
| `--profile` | | flag | | Per-line time and call counts; summary after the run, `output/<script>_profile.json`. Backtests only, Python 3.13+ |
| `--profile-top` | | integer >= 0 | `15` | Lines listed in the profile summary |

Behaviour:

- **Data file**: `DATA.toml` must sit next to `DATA.ohlcv`, otherwise `Symbol info file '...'
  not found!`. A CSV/JSON/TXT is converted first (an existing `.toml` is kept and must match the
  data's timeframe); review the generated symbol info before trusting a backtest.
- **Provider string**: the provider plugin downloads the requested range (default the last 500
  real bars) into `data/<provider>_<SYMBOL>_<TF>.ohlcv`, **replacing that file**, refreshes the
  symbol info from the venue, then runs. With `--live` the run continues on the websocket
  stream: the historical phase suppresses strategy orders, the live phase simulates fills
  (paper) unless `--broker` attaches a broker plugin. Transient provider outages are retried.
- **Script settings**: `scripts/<script>.toml` (inputs and decorator settings) is read and
  rewritten on every run unless `PYNE_SAVE_SCRIPT_TOML=0`.
- `--live` with a data file: `Error: --live is only available in provider mode.` `--profile`
  with `--live`/`--broker`: refused. `--from` with a negative number in file mode: `Error: Days
  cannot be negative ...`.
- Ctrl+C (and SIGTERM in a broker run) stops a live run gracefully within `--shutdown-timeout`.
- Live runs print a `still running` heartbeat every 30 s when stdout is not a terminal
  (`PYNE_HEARTBEAT_INTERVAL`), log `[BROKER]` lines for connection events and, for broker runs,
  quarantine notices with the `pyne broker resume ...` command to run.

Files written: `output/<script>.csv` (unless `--no-plot`), `output/<script>_strat.csv` and
`_trade.csv` (strategies), `output/<script>_viz.ndjson` (with `--viz`),
`output/<script>_profile.json` (with `--profile`), `scripts/<script>.toml`, `output/logs/`
(live and broker logs, `broker.sqlite` journal for `--broker`), the provider data file in
provider mode.

Errors: `Script file '<path>' not found!`, `Data file not found: <name>`, `Symbol info file
'<path>' not found!`, `Error: Invalid date or number: '<value>'`, timeframe not a multiple of
the data's, missing `--security` data (`--list-data` shows what is needed). All exit 1.

## `pyne data download`

```
pyne data download [OPTIONS] [PROVIDER]
```

| Argument | Format |
|----------|--------|
| `PROVIDER` | A provider name (`ccxt`) with `--symbol`/`--timeframe`; a provider string (`ccxt:BINANCE:BTC/USDT@60`); or the path of an already downloaded `.ohlcv`/`.toml` file, re-downloaded with the provider string stored in its `[download]` section. Omitted on a terminal: interactive picker. Omitted without a terminal: `Error: Provider is required in non-interactive mode.` (exit 2) |

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--symbol` | `-s` | `[BROKER:]SYMBOL` | from the provider string | Instrument, e.g. `BYBIT:BTC/USDT:USDT`; ignored when the string already has one |
| `--timeframe` | `-tf` | `TIMEFRAME` | `1D` | Bar size; ignored when the string has `@TIMEFRAME` |
| `--from` | `-f` | `DATE` \| `NUMBER` (days back) \| `continue` | `continue` | Start. `continue` resumes at the last stored bar; with no file yet it means one year back |
| `--to` | `-t` | `DATE` \| `NUMBER` (days from the start) | now | End; a future end is clamped to now |
| `--list-brokers` | `-lb` | flag | | Print the brokers/exchanges of a multi-broker provider (`pyne data download ccxt --list-brokers`) and exit |
| `--list-symbols` | `-ls` | flag | | Print the provider's (or broker's) symbols and exit: `ccxt:BYBIT --list-symbols`, `capitalcom --list-symbols` |
| `--symbol-info` | `-si` | flag | | Print the derived symbol info and exit |
| `--force-save-info` | `-fi` | flag | | Fetch the symbol info again and rewrite the `.toml` |
| `--truncate` | `-tr` | flag | | Delete the stored bars before downloading |
| `--chunk-size` | `-cs` | integer | the exchange's known limit | Bars per request (overrides the per-exchange table; needed where limits depend on the timeframe) |
| `--extra-data` | `-ed` | flag | | Also fetch the provider's extra per-bar fields (ask/bid/spread) into `<name>.extra.csv`; ignored by providers without them (`ccxt`) |

Behaviour:

- Output `data/<provider>_<SYMBOL>_<TF>.ohlcv` + `.toml`; `/` and `:` in the symbol become
  `_`, letters are upper-cased (`ccxt_BYBIT_BTC_USDT_USDT_1D`). The `.toml` gets a `[download]
  provider = "..."` section so the file can be continued by path.
- Symbol info is fetched (and the `.toml` written) before the bars, so an interrupted download
  leaves a resumable file.
- The file only grows at the end. A `--from` before the first stored bar prints `The start
  date ... is before the start of the existing file ... If you continue, the file will be
  truncated.` and asks `Do you want to continue?` (abort without a terminal, exit 1).
- The default `--to` is now, so a bar still forming is stored with its partial values; a later
  `continue` starts at that bar and never rewrites it. Pass `--to` one bar back when the file
  must hold closed bars only, or `--truncate` later.
- Provider configuration: `config/plugins/<provider>.toml`; for `ccxt` an exchange table
  (`[binance]`) is passed to the CCXT client as is (keys, `sandbox`, custom `urls`, `options`).
- Terminal only: without a symbol the **symbol browser** opens (list pane with `/` filter,
  `Enter` opens a wizard with Timeframe / From / To / Truncate fields, `Esc` back, `q` quit; a
  progress strip shows the download). `--timeframe/--from/--to` pre-fill the wizard.

Errors: `Error: File not found: <path>` and `... has no [download] provider section` for the
path form; `Unknown symbol '<s>' on <exchange>`; `Unknown exchange '<id>'. Run pyne data
download ccxt --list-brokers`; `Error: Symbol is required (or use --list-symbols for
non-interactive listing).`; network errors after five retries.

## `pyne data convert-from`

```
pyne data convert-from [OPTIONS] FILE_PATH
```

| Argument | Format |
|----------|--------|
| `FILE_PATH` | Path to a CSV, JSON or TXT file with `timestamp|time|date|datetime, open, high, low, close, volume` columns (ISO strings with or without offset, or epoch seconds/milliseconds) |

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--provider` | `-p` | any name | guessed from the file name | Provider recorded in the `.toml` (`prefix`) |
| `--symbol` | `-s` | `BASE/QUOTE` or ticker | guessed from the file name | Symbol recorded in the `.toml` |
| `--timezone` | `-tz` | IANA name | `UTC` | Timezone of naive timestamps |

Writes `<stem>.ohlcv` and `<stem>.toml` next to the source; the timeframe, tick size, symbol
type (forex / crypto / other) and opening hours are derived from the data. Recognised names:
`BTCUSDT.csv`, `EUR_USD.csv`, `ccxt_BYBIT_BTC_USDT.csv`, `BINANCE_ETHUSDT_1h.csv`,
`capitalcom_EURUSD.csv`. Columns beyond OHLCV become extra fields. Error: `Could not detect
symbol from filename '<name>'!` (pass `--symbol`). An existing `.toml` is kept and must match
the data's timeframe.

## `pyne data convert-to`

```
pyne data convert-to [OPTIONS] OHLCV_PATH
```

| Argument | Format |
|----------|--------|
| `OHLCV_PATH` | An `.ohlcv` file (path or bare name in `data/`; the extension may be omitted) |

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--format` | `-f` | `csv` \| `json` | `csv` | Output format |
| `--as-datetime` | `-dt` | flag | | ISO datetimes instead of Unix millisecond timestamps |

Writes `<stem>.csv` or `<stem>.json` next to the source with the columns
`timestamp,open,high,low,close,volume`.

## `pyne data aggregate`

```
pyne data aggregate [OPTIONS] SOURCE --timeframe TF
```

| Argument | Format |
|----------|--------|
| `SOURCE` | Source `.ohlcv` (path or bare name in `data/`); its `.toml` supplies the source timeframe and timezone |

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--timeframe` | `-tf` | `TIMEFRAME` (required) | | Target timeframe, larger than the source's |
| `--output` | `-o` | path | source name with the timeframe suffix replaced | Output `.ohlcv`; the `.toml` is copied with `period` updated |

Only upscaling: intraday and daily targets must be exact multiples of the source; weekly and
monthly targets are calendar periods built from intraday, daily or the same unit
(`1M -> 3M`), never from weekly (a week can straddle two months). Open = first, High = max,
Low = min, Close = last, Volume = sum; daily/weekly/monthly bars follow the trading day of the
symbol's timezone. An existing output asks `Overwrite?` (abort without a terminal). Errors:
`Source file not found`, `Source must be .ohlcv format`, `Metadata file not found`,
incompatible timeframes.

## `pyne data map`

```
pyne data map TV_SYMBOL PROVIDER_SYMBOL
```

| Argument | Format |
|----------|--------|
| `TV_SYMBOL` | The symbol as written in scripts, `PREFIX:TICKER`, optionally `:TIMEFRAME` for a per-timeframe override (`NASDAQ:AAPL`, `NASDAQ:AAPL:60`) |
| `PROVIDER_SYMBOL` | Provider-qualified native symbol, `provider:rest` (`capitalcom:AAPL`, `ccxt:BYBIT:BTC/USDT:USDT`) |

Adds or updates one entry of `[symbol_map]` in `config/symbol_map.toml`, creating the file
with a documented header and preserving other entries and comments. Both backtests
(data file `<provider>_<SYMBOL>_<TF>`) and live runs (plugin-native symbol) use the map for
`request.security()` contexts that were not mapped explicitly on the `run` command line.

## `pyne plugin list`

```
pyne plugin list [--type TYPE] [--json]
```

| Option | Short | Value | Default | Effect |
|--------|-------|-------|---------|--------|
| `--type` | `-t` | `provider` \| `broker` \| `cli` \| ... | all | Only plugins with that capability |
| `--json` | | flag | | Machine-readable output |

Text output lists `name  display name  vVERSION  [capabilities]` with a one-line summary;
name conflicts (two packages declaring one plugin name) are listed and make that name
unloadable. JSON: `{"plugins": [{"name", "display_name", "version", "capabilities": [...],
"summary", "package", "conflict"}], "errors": [...]}`. PyneCore 6.12 ships `ccxt` (provider)
and `replay` (fixture provider for tests); broker plugins are separate packages
(`pynesys-pynecore-bybit`, `-capitalcom`, `-ctrader`) installed with `pip`/`uv` into the same
environment. There is no `pyne plugin install`.

## `pyne plugin info`

```
pyne plugin info NAME
```

Prints package, version, description, required PyneCore version, capabilities, entry point,
the plugin's documentation, and the config fields with their defaults (what
`config/plugins/<name>.toml` accepts). Errors: `Plugin '<name>' is not installed. Available
plugins: ...`, ambiguous name declared by several packages.

## `pyne debug ast`

```
pyne debug ast SCRIPT
```

Prints the transformed source of a `.py` script (what PyneCore actually executes: persistent
state slots, isolated function instances, series indexing) without running it. The dump is
produced by the import transform, so **nothing is printed when the script's bytecode cache is
current**: `touch` the file (or delete its `__pycache__` entry) first. Names refer to
`scripts/`; the `.py` extension may be omitted. Equivalent environment switches:
`PYNE_AST_DEBUG=1` (every transformed module during a normal run), `PYNE_AST_DEBUG_RAW`,
`PYNE_AST_SAVE=1` (write to `/tmp/pyne/`).

## `pyne benchmark`

```
pyne benchmark [SCRIPT] [DATA] [--iterations N] [--candles N] [--warmup N] [--no-output]
```

| Argument / option | Short | Value | Default | Effect |
|-------------------|-------|-------|---------|--------|
| `SCRIPT` | | script path or name | `demo` | Script to time |
| `DATA` | | data path or name | `demo` | Data file |
| `--iterations` | `-i` | integer | `10` | Measured runs |
| `--candles` | `-c` | integer | `5000` | Bars processed per run |
| `--warmup` | `-w` | integer | `2` | Unmeasured runs first |
| `--no-output` | | flag | | Do not write the CSV outputs during the runs |

Prints a table: average import, run and total time, min/max run time, standard deviation,
candles per second, time per candle. A simple script processes several hundred thousand bars
per second.

## `pyne broker resume`

```
pyne broker resume RUN_ID QUARANTINE_ID
```

| Argument | Format |
|----------|--------|
| `RUN_ID` | The run id exactly as printed in the quarantine log line, e.g. `'my_strategy@bybit-demo-123:BTCUSDT.P:60'` (quote it) |
| `QUARANTINE_ID` | The quarantine id from the same line (`9f3a01bc`) |

Run it on the machine and with the workdir of the bot. A running bot picks the request up
within seconds, stops and restarts itself in place; a stopped bot resumes at its next start.
The request lifts only the quarantine it names: a stale id fails and lists the open
quarantines. Errors: `No broker store at <workdir>/output/logs/broker.sqlite`, `No open
quarantine <id> for run '<run_id>'.` followed by the open ones (exit 1). The `cli/broker.md`
documentation page explains what a resume accepts on the venue (position, orders, stop-loss
and take-profit re-assert).

## `pyne compile`

```
pyne compile [--output PATH] [--strict] [--force] [--api-key KEY] [--usage] [SCRIPT]
```

Converts scripts written in the charting platform's own language into Pyne through the PyneSys
cloud compiler (`--api-key`/`-a`, env `PYNESYS_API_KEY`, or `config/api.toml`; `--output`/`-o`,
`--strict`/`-s`, `--force`/`-f`, `--usage`/`-u`; without a script it prints API usage). It is not
used for Python work and is listed only so that the drift check of `cli_help.py` covers the
whole CLI.

## Commands and options added by plugins

A plugin that implements `CLIPlugin.cli()` adds a command group named after the plugin
(`pyne <plugin> <subcommand>`); one that implements `cli_params()` injects options into `run`
or `data download` (they appear in those commands' `--help`). Names that collide with a
built-in command or an existing option are skipped with a warning. `pyne --help` and `pyne run
--help` therefore show the complete surface of the installed environment; `cli_help.py --dump`
captures it.
