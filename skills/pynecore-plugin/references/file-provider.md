# File provider: candles from local files (`templates/pynecore-fileprovider`)

Plugin name `file`, class `FileProvider(LiveProviderPlugin[FileConfig])`.

## What it is for

- Backtests and `pyne data download` on data that is not in PyneCore's format yet: terminal
  exports, vendor CSVs, parquet files written by pandas or a lake, SQLite databases filled by a
  collector.
- **One source file, several timeframes.** `file:BTCUSDT@240` or `file:BTCUSDT@1D` on an hourly
  file aggregates inside the provider: the history with PyneCore's aggregator into a cached file,
  the live replay bar by bar with PyneCore's HTF aggregator. No separate `pyne data aggregate`
  step and no second copy of the data to keep in sync.
- A **deterministic live source**: the last `replay_bars` bars of a file are streamed as live
  bars, paced, with intra-bar updates before each close, so a strategy's live-mode code
  (`barstate.isrealtime`, fills on ticks, notifications, broker-free paper logic) is exercised
  without an exchange. Re-stamped onto the clock, as PyneCore's live runner requires.
- A **bridge from another process**: with `tail = true` rows appended to the file by anything
  else (a collector script, a terminal, a cron job) are streamed as they appear.
- The **price feed of the simulated broker** (`templates/pynecore-simbroker`, `feed = "file"`):
  a whole `pyne run --broker` lifecycle offline, see `sim-broker.md`.

## Install and use

```bash
pip install -e <skill>/templates/pynecore-fileprovider        # or scaffold a copy: new_plugin.py fileprovider DEST --name NAME
pip install pyarrow                                          # only for .parquet sources (pandas works too)
pyne plugin info file
pyne data download file:BTCUSDT@60                 # <data>/BTCUSDT_60.csv (or .ohlcv/.json/.txt/.parquet/.sqlite) -> data/file_BTCUSDT_60.ohlcv + .toml
pyne data download file:BTCUSDT@240                # same hourly file, aggregated to 4h -> data/file_BTCUSDT_240.ohlcv
pyne run my_strategy file:BTCUSDT@60 -f -500       # backtest through the provider
pyne run my_strategy file:BTCUSDT@1D --live        # daily bars replayed from the hourly file (replay_bars > 0), Ctrl+C to stop
<python> <skill>/../pynecore-strategy/scripts/run_paper.py my_strategy file:BTCUSDT@60 --from -300 --bars 100   # paper run, bar limit
pyne data download file --list-symbols             # the stems of every source file in the data folder
```

**Which file.** The provider string symbol is a name (`BTCUSDT`); the file is found through, in
order: the `files` map, the symbol itself when it is a path ending in a known extension, then
`<root>/<SYMBOL>_<TF>.<ext>` and `<root>/<SYMBOL>.<ext>` for the requested timeframe, and finally
**`<root>/<SYMBOL>_<tf>.<ext>` with a lower timeframe that aggregates into the requested one**
(the largest such `tf` wins, a `.ohlcv` before a convertible export). Extensions: `.ohlcv`,
`.csv`, `.json`, `.txt`, `.parquet`, `.pq`, `.sqlite`, `.sqlite3`, `.db`. With `root` empty the
folders tried are the data folder the provider was built for, the running CLI's data folder,
`$PYNE_WORK_DIR/data` and `./data` (so `--list-symbols`, which builds the provider without a
data folder, and tools that probe with a temporary folder still find the workdir's files). When
nothing fits, the `ProviderError` lists every path tried and, for files of the symbol that exist
but cannot serve the timeframe, the reason (`Target timeframe (90) must be evenly divisible by
source timeframe (60)`, `Target timeframe (30) must be larger than source timeframe (60)`).

## Configuration (`config/plugins/file.toml`)

| Key | Default | Meaning |
|-----|---------|---------|
| `root` | `""` (= `data/`) | Folder searched for `<SYMBOL>_<TF>.<ext>` / `<SYMBOL>.<ext>` / `<SYMBOL>_<lower tf>.<ext>` |
| `files` | `{}` | Explicit map `{ "BTCUSDT" = "/exports/btc_1h.parquet" }` |
| `source_timezone` | `"UTC"` | Timezone of naive timestamps in CSV/JSON/TXT (handed to PyneCore's converter), parquet and SQLite sources |
| `sqlite_table` | `"candles"` | SQLite: table read with `SELECT *`; column names are detected case-insensitively |
| `sqlite_query` | `""` | SQLite: a `SELECT` used instead of the table (join, `WHERE symbol = ...`, renamed columns) |
| `replay_bars` | `0` | Live mode: the last N **source** bars are streamed instead of being history |
| `replay_from` | `""` | Live mode: stream bars from this UTC time (`YYYY-MM-DD[ HH:MM:SS]`); overrides `replay_bars` |
| `replay_speed` | `1.0` | Seconds between replayed source bars (`0` = as fast as the runner takes them) |
| `replay_realtime` | `true` | Re-stamp replayed bars onto the clock (see below); `false` only for runners without the idle-bar watchdog |
| `emit_ticks` | `true` | `is_closed=False` updates before each close: one (open → mid price, half volume) without aggregation, one per source bar with it |
| `tail` | `false` | After the file is exhausted, poll it and stream appended rows (original timestamps) |
| `poll_interval` | `2.0` | Seconds between polls when tailing |
| `symbol_map` | `{}` | Inherited from `LiveProviderConfig`; `request.security()` symbol translation in live runs |

## How it works

**Sources.** `.ohlcv` files are read directly with `OHLCVReader`. CSV/JSON/TXT files are passed
to PyneCore's `DataConverter().convert_to_ohlcv(path, provider="file", symbol=..., timezone=...)`,
which writes `<stem>.ohlcv` + `<stem>.toml` next to the source (column detection, timeframe
inference, tick-size analysis, 24/7 or detected sessions) and skips the work when the `.ohlcv`
is newer than the source. **Parquet and SQLite** are converted by the plugin itself into the same
`<stem>.ohlcv` next to the source, also skipped while the cache is newer than the source:

- Parquet is read with `pyarrow.parquet.read_table` (fallback `pandas.read_parquet`, a datetime
  index named like a time column is reset into a column); neither installed → `ProviderError`
  naming `pip install pyarrow`.
- SQLite is opened read-only (`file:...?mode=ro`) with the standard library; `sqlite_query` or
  `SELECT * FROM "<sqlite_table>"`; a missing table yields a `ProviderError` that names the two
  config keys.
- Columns are matched case-insensitively: time from `timestamp | time | date | datetime | ts |
  open_time | t`, prices from `open/high/low/close` or `o/h/l/c`, volume from `volume | vol | v
  | base_volume` (optional, else 0). Timestamps may be datetimes (naive ones in
  `source_timezone`), ISO strings, or epoch seconds, milliseconds, microseconds or nanoseconds
  (told apart by magnitude). Rows are sorted, duplicate timestamps dropped, high/low widened to
  cover open and close.
- The period is inferred from the median bar distance (`1`, `60`, `1D`, `1W`, `1M`, `S` for
  sub-minute); the tick size from the writer's own price analysis (`set_tick_info`).

The sibling `.toml` is also the symbol info the provider returns (`period` replaced by the
requested timeframe); without one a 24/7 crypto-like `SymInfo` is synthesised from the file's
tick grid. So the symbol info of the generated `data/file_<SYMBOL>_<TF>.toml` can be edited once
and is reused.

**Aggregation.** When the source period differs from the requested timeframe the provider calls
`pynecore.core.aggregator.validate_aggregation(source, target)` (target larger and a multiple of
the source, otherwise the error above). History then comes from
`aggregate_ohlcv(source, "<stem>_agg_<TF>.ohlcv", target_tf, tz, session_starts, opening_hours,
sym_type, source_tf, prefix)`, rebuilt only when the source is newer than the cache; the
aggregated file is PyneCore's own output, so sessions, daily/weekly/monthly boundaries and
timezone follow the symbol info. In the live replay every source bar goes through
`pynecore.core.htf_aggregator.HTFAggregator(timeframe, tz, session_starts, chart_span_ms,
opening_hours, grid_mode).update(ts, o, h, l, c, v)`: the developing bar is emitted as an
`is_closed=False` update (cumulative volume, as exchanges send it), the completing source bar
closes the target bar. The replay cut is moved forward to the first source bar that opens a new
target period, so the history ends with a complete target bar and the replay starts on a period
boundary (`_agg_` caches are excluded from `--list-symbols`).

**History (`download_ohlcv`).** Bars in `[time_from, time_to]` are read from the (aggregated)
file and written in pages of 5000 through `save_ohlcv_data`. Bars reserved for the replay
(`replay_bars` / `replay_from`) are excluded: with `replay_bars = 150` on a 10 340-bar hourly file
the hourly download holds 10 190 bars, the 4h download 2548 of 2585 periods (verified).
`fetch_all_by_default = True`, so a download without `--from` takes the whole file.

**Live replay (`connect` / `watch_ohlcv`).** `connect()` reloads the source rows after the
history end (or after the replay cut when no download happened in this process), computes the
current target slot, builds the HTF aggregator when needed and marks the provider connected.
Each `watch_ohlcv` call hands out one update, waiting `replay_speed` between source bars in
sleeps of at most 1.5 s so the runner's cancellation never loses progress. Re-stamping: the first
replayed source bar gets the timestamp of the target slot open now, each following bar one
source period later; the OHLCV values are the file's. When the rows are exhausted the provider
parks (or tails); stop the run with Ctrl+C, or use `--bars / --duration` of
`../../pynecore-strategy/scripts/run_paper.py`.

**Why re-stamp.** PyneCore's live runner synthesises a zero-volume bar whenever the last closed
bar's timestamp lies more than two periods plus a grace period in the past, and then drops every
real bar whose timestamp is not newer. Original 2025 timestamps streamed in 2026 would be buried
instantly. With `replay_realtime = true` the replay looks like a market happening now; the price
path is historical, the clock is current. Time-of-day logic in the script therefore sees today's
times, not the file's (set `replay_realtime = false` only when driving `ScriptRunner` yourself
without `live_ohlcv_generator`).

`feed_timeout_bars = None`: a paced replay is silent between bars by design, so the dead-feed
watchdog is disabled for this plugin.

## Verified (PyneCore 6.12.1, hourly BTC/USDT CSV with 10 340 rows, `replay_bars = 150`, `replay_speed = 0.3`)

| Check | Result |
|-------|--------|
| `pyne plugin info file` | capabilities `provider`, 13 config fields listed |
| `pyne data download file:BTCUSDT@60` | `file_BTCUSDT_60.ohlcv` 10 190 bars, `.toml` with `[download] provider = "file:BTCUSDT@60"`, `prefix FILE`, `mintick 0.01`, `period 60` |
| `pyne data download file:BTCUSDT@240` (only `BTCUSDT_60.csv` exists) | `BTCUSDT_60_agg_240.ohlcv` 2585 bars, every bar equal to a manual 4h fold of the CSV (0 mismatches); `file_BTCUSDT_240.ohlcv` 2548 bars ending on a complete 4h bar, `.toml` `period = "240"` |
| `pyne data download file:BTCUSDT@1D` | 425 daily bars; `@30` and `@90` fail with the aggregator's reason for `BTCUSDT_60.csv` |
| `pyne data download file:BTCP@60` on `BTCP_60.parquet` (pyarrow, `timestamp[ms, UTC]` column) | converted to `BTCP_60.ohlcv`, 10 190 bars downloaded, `minmove 1 / pricescale 100`; `file:BTCP@240` aggregates the converted file |
| `pyne data download file:BTCS@60` on `BTCS.sqlite` (table `candles`, upper-case columns, epoch seconds) | identical 10 190 bars; `sqlite_query` with aliased columns and a `WHERE` verified directly; wrong table → `ProviderError` naming `sqlite_table` / `sqlite_query` |
| `pyne run ema_cross file:BTCUSDT@240 -f -300` | backtest on the aggregated history completed |
| `pyne run feed_probe file:BTCUSDT@240 --live -f -50`, SIGINT after 45 s | first live bar stamped on the current 4h slot, then 3 intra-bar updates (cumulative volume) + 1 closed bar per period: 21 closed bars, 64 updates, no synthesised bars |
| `plugin_check.py file --symbol BTCUSDT --timeframe 240 --bars 40 --live-seconds 10` | all static checks, symbol info, 2548-bar download, live stream 9 closed bars / 25 updates in 10 s: `OK` |
| `run_paper.py ema_cross file:BTCUSDT@60 --from -300 --bars 100` (`replay_speed 0.05`) | 100 live bars re-stamped from the current hour, 3 paper trades with stop/target exits, 1 catch-up bar |
| `pyne run sim_probe simbroker:BTCUSDT@60 --broker` with `feed = "file"` | the simulated broker ran a full live lifecycle on the replay (see `sim-broker.md`) |

## Adapting the template

- **Another tabular format** (Arrow IPC, HDF5, a vendor dump): add its suffix to
  `SOURCE_SUFFIXES`, write a `_read_<format>(path) -> (columns, rows)` and dispatch to it in
  `_ohlcv_source()`; `_rows_to_ohlcv()` does the column matching, timestamp normalisation,
  period inference and the `.ohlcv` cache.
- **A database with many symbols in one table**: set `sqlite_query` with a `WHERE` on the symbol
  column, or map each symbol to its own `files` entry; for another engine (PostgreSQL, DuckDB)
  replace `_read_sqlite` with the driver's cursor, the rest is unchanged.
- **Different symbol metadata**: write a `.toml` next to the source (`SymInfo(...).save_toml()`)
  or build the `SymInfo` in `_base_symbol_info()` from your own catalogue; sessions and tick size
  drive aggregation boundaries, fills and rounding.
- **A live collector**: have the other process append rows to a CSV; run with `tail = true`,
  `replay_bars = 0`, and the provider streams each new row (its own timestamp, so the rows must
  be current). With a higher requested timeframe the appended rows are aggregated the same way.
