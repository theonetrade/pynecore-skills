# Environment: variables, the workdir, files, terminals, exit codes

## Contents

- [Environment variables](#environment-variables)
- [Workdir discovery](#workdir-discovery)
- [What `pyne` creates and rewrites](#what-pyne-creates-and-rewrites)
- [Config files](#config-files)
- [Output files](#output-files)
- [Terminal dependence and prompts](#terminal-dependence-and-prompts)
- [Exit codes](#exit-codes)
- [Logging](#logging)
- [Shell completion](#shell-completion)

## Environment variables

Read by PyneCore 6.12 (from its sources). Boolean switches are "set to any value" unless a
value is given.

| Variable | Effect | Default |
|----------|--------|---------|
| `PYNE_WORK_DIR` | The workdir, same as `--workdir` | auto-detected |
| `PYNE_DATA_DIR` | Folder for the `.ohlcv`/`.toml` data files instead of `<workdir>/data`; config and credentials still come from `<workdir>/config` | `<workdir>/data` |
| `PYNE_NO_LOGO`, `PYNE_QUIET` | No ASCII logo at startup (either one) | logo on |
| `NO_COLOR` | No colours in the CLI output (Rich) | colours on a terminal |
| `COLUMNS` | Width used by the help panels and log console (set `160` so option names are not wrapped) | terminal width |
| `PYNE_NO_COLOR_LOG=1` | Plain log handler for script `log.*` output instead of the Rich one | Rich handler |
| `PYNE_SAVE_SCRIPT_TOML=0` | Do not (re)write `scripts/<script>.toml` on import | `1`, written on every run |
| `PYNESYS_API_KEY` | Cloud compiler key (`run --api-key`, `compile`); irrelevant for `.py` scripts | `config/api.toml` |
| `PYNE_HEARTBEAT_INTERVAL` | Seconds between `still running` lines of a live run whose spinner is suppressed (not a terminal); `0` disables | `30` |
| `PYNE_NO_LIVE_SPINNER=1` | Suppress the live spinner even on a terminal (durable logs) | spinner on a terminal |
| `PYNE_SECURITY_LOG` | File path: the `request.security()` subprocesses log there | off |
| `PYNESYS_MAX_SECURITY_CONTEXTS` | Maximum number of security contexts a script may open | `64` |
| `PYNE_NO_SECURITY_BATCH=1` | Keep every security context on the per-bar round trip instead of batching | batched |
| `PYNE_ALLOW_LOOKAHEAD` | Lets `request.security(..., lookahead_on)` step into the period containing the chart bar (the future leak PyneCore otherwise refuses); for measuring its effect only | off |
| `PYNE_AST_DEBUG=1` | Print the transformed code of every `@pyne` module that is transformed (what `pyne debug ast` sets) | off |
| `PYNE_AST_DEBUG_RAW` | `1` or a file path: print the exact emission with literal slot indexes | off |
| `PYNE_AST_SAVE=1` | Save transformed modules under `/tmp/pyne/` | off |
| `PYNE_AST_TIMING=1` | Wall time of every transform step on stderr | off |
| `PYNE_AST_SEQUENTIAL=1` | Run fused transform phases as separate passes (reference mode) | fused |
| `PYNE_AST_BACKEND` | `native` (default), `python`, or `verify` (run both, compare) for the transform steps | `native` |
| `PYNE_NO_NATIVE_MATH`, `PYNE_NO_NATIVE_COLOR` | Use the pure-Python implementations instead of the compiled extensions | native |
| `PYNE_NO_TYPE_PIN=1` | Ignore overload type pins (diagnostics) | pins on |
| `PYNE_TIMENOW_MS` | Set by `pyne run` for its own subprocesses to pin `timenow` to the last bar in backtests; not for users | |

The `PYNE_AST_*`, `PYNE_NO_NATIVE_*`, `PYNE_NO_TYPE_PIN`, `PYNE_NO_SECURITY_BATCH` and
`PYNE_ALLOW_LOOKAHEAD` switches are diagnostics; a result produced with them is not the
production behaviour.

## Workdir discovery

1. `--workdir DIR` (a global option, before the command), else `PYNE_WORK_DIR`.
2. Otherwise, starting at the current directory and walking up through 10 levels: a directory
   that contains a `.pyne` marker file (`workdir_version = "1.0"`, major version 1) is the
   workdir; else a `workdir/` child of that directory, if it exists.
3. Nothing found: `./workdir` (the current directory's child).

A workdir path that does not exist is created only after `Working directory '...' does not
exist. Do you want to create it? [y/N]`; without a terminal the answer is no and the command
aborts (exit 1, nothing created). Create the folder first, or point at an existing one.

`pyne` itself does not treat the project root as special: the workdir can be the project
folder (marker at the root, the layout PyneIDE and this repository's skills use) or a
`workdir/` subfolder.

## What `pyne` creates and rewrites

The startup step of every command (also `pyne COMMAND --help`) runs after the workdir is
resolved:

| Path | Action |
|------|--------|
| `.pyne` | Written if missing |
| `scripts/lib/` | Created if missing |
| `scripts/demo.py` | Written only when this call created the workdir, or with `--recreate-demo` (an EMA 12/26 crossover indicator with `@pyne edge`) |
| `data/` | Created if missing |
| `data/demo.ohlcv`, `data/demo.toml` | Same rule as `demo.py`: 2000 synthetic daily bars from 2020-01-01, seed 42, symbol `DEMO:DEMOBTC`, 24/7, `mintick 0.01`, fees 0.1 % / 0.05 % |
| `output/logs/` | Created if missing |
| `cache/` | Created if missing (runtime state such as auth sessions) |
| `config/plugins/<plugin>.toml` | Generated for every installed plugin from its `Config` dataclass when missing, or regenerated with `--recreate-provider-config` (user values and extra tables preserved) |
| `config/api.toml` | `[api]\napi_key = ""\ntimeout = 30` when missing, or with `--recreate-api-config` |
| `output/logs/error.log` | Deleted at startup; receives the traceback of an uncaught exception |

User files are never overwritten except through the explicit `--recreate-*` options.

## Config files

| File | Written by | Read by |
|------|-----------|---------|
| `config/api.toml` | startup, `--recreate-api-config` | the cloud compiler only |
| `config/plugins/<plugin>.toml` | startup, `--recreate-provider-config` | `data download`, `run` with a provider string, live and broker runs. Keys, `sandbox`, `symbol_map`, `default_type` (ccxt; `swap` by default, `spot` for spot pairs in live mode) and, for `ccxt`, per-exchange tables passed to the client as is |
| `config/symbol_map.toml` | `pyne data map` (or by hand) | `request.security()` resolution in backtests and live runs |
| `config/brokers.toml` | first broker run | cross-broker defaults for live trading |
| `scripts/<script>.toml` | `pyne run` on every run (unless `PYNE_SAVE_SCRIPT_TOML=0`) | `pyne run`: `[script]` settings and `[inputs.<name>] value = ...` |
| `data/<name>.toml` | `data download`, `convert-from`, `aggregate`, `run` on a CSV | every run on that data: symbol info; `[download]` section keeps the provider string |

`config/` holds credentials: keep it out of version control (`config/.gitignore` with `*` and
`!.gitignore` is the layout PyneIDE and `init_project.py` write).

## Output files

| File | Command | Content |
|------|---------|---------|
| `output/<script>.csv` | `run` (not with `--no-plot`) | `time,open,high,low,close,volume` and one column per plot title, `NaN` for missing values; closed bars only in live mode |
| `output/<script>_strat.csv` | `run` of a strategy | Performance metrics (net profit, drawdown, run-up, profit factor, Sharpe, Sortino, trade counts, long/short split, commission, margin calls); rewritten after every closed bar in live mode |
| `output/<script>_trade.csv` | `run` of a strategy | One row per entry and exit: trade number, bar index, type, signal, date/time, price, contracts, profit, cumulative profit, run-up, drawdown |
| `output/<script>_viz.ndjson` | `run --viz` | Plot styles, per-bar values and colours, drawings (journal with `--viz-journal`) |
| `output/<script>_profile.json` | `run --profile` | Per-line time and counts, time by category |
| `output/logs/error.log` | any | Last uncaught exception |
| `output/logs/broker.sqlite` | `run --broker` | Broker run journal (orders, fills, quarantines) |
| `data/<provider>_<SYMBOL>_<TF>.ohlcv` + `.toml` (+ `.extra.csv`) | `data download`, `run` with a provider string | Candles (binary, Unix milliseconds), symbol info, extra per-bar fields |
| `<source>.ohlcv` + `.toml` | `data convert-from`, `run` on a CSV | Converted data next to the source |
| `<source>.csv` / `.json` | `data convert-to` | Exported candles |

## Terminal dependence and prompts

| Situation | On a terminal | Without one (agent shell, CI) |
|-----------|---------------|-------------------------------|
| Workdir does not exist | `Do you want to create it? [y/N]` | `Aborted.`, exit 1 |
| `data download --from` before the stored file's first bar | `Do you want to continue?` then truncate | `Aborted.`, exit 1 (pass `--truncate`, or drop `--from`) |
| `data aggregate` output exists | `Overwrite?` | `Aborted.`, exit 1 (pass `-o`) |
| `data download` without a symbol | symbol browser TUI | exit 2 (`--list-symbols`, or a full provider string) |
| Progress bars, spinners | shown | suppressed; live runs print a heartbeat every 30 s |
| Colours, logo | on | colours off with `NO_COLOR`; logo still printed unless `PYNE_NO_LOGO` |
| Script `log.*` lines | Rich handler on stdout | still stdout: redirect it, or set `PYNE_NO_COLOR_LOG=1` for a plain handler on stderr |

## Exit codes

| Code | Meaning | Examples |
|------|---------|----------|
| 0 | success | a completed run, `pyne` without a command (help) |
| 1 | error | `Script file '...' not found!`, `Data file not found`, `Symbol info file ... not found!`, provider errors, `Aborted.` prompts, `No broker store at ...` |
| 2 | usage | missing argument (`pyne run` alone), unknown option or bad value (`BadParameter`), symbol browser without a terminal |

Verified on 6.12 with a missing script (1), an aborted workdir prompt (1), `pyne run` without
arguments (2), `pyne data download ccxt` with stdin redirected (2), an impossible aggregation
(1) and a normal run (0).

## Logging

- Script output (`log.info/warning/error`, `[BROKER]` lines) goes through the `pyne_core_logger`
  logger with PyneCore's own handler, by default a Rich handler that writes to **stdout**;
  `PYNE_NO_COLOR_LOG=1` switches to a plain `logging.StreamHandler` (stderr). Capture stdout
  when parsing a run's log.
- Live and broker runs also write durable logs under `output/logs/`; a quarantine is logged as
  two single-line records (`sync engine quarantined: kind=... quarantine_id=...` and
  `... resume command: pyne broker resume RUN_ID QUARANTINE_ID`), repeated every 30 minutes
  while it holds.
- `PYNE_SECURITY_LOG=path` collects the logs of the `request.security()` subprocesses.
- Uncaught exceptions: `output/logs/error.log` (overwritten per invocation).

## Shell completion

`pyne --install-completion` installs completion for the current shell (bash, zsh, fish,
PowerShell; a new shell session is needed); `pyne --show-completion` prints the script to
install by hand. Completion requests set `_TYPER_COMPLETE_ARGS`, under which the logo is not
printed.
