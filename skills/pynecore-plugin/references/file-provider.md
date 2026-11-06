# File provider: candles from local files (`templates/pynecore-fileprovider`)

Plugin name `file`, class `FileProvider(LiveProviderPlugin[FileConfig])`.

## What it is for

- Backtests and `pyne data download` on exports that are not in PyneCore's format yet
  (terminal exports, vendor CSVs, data engineered in pandas and saved as CSV/JSON).
- A **deterministic live source**: the last `replay_bars` bars of a file are streamed as live
  bars, paced, with an optional intra-bar tick before each close, so a strategy's live-mode code
  (`barstate.isrealtime`, fills on ticks, notifications, broker-free paper logic) is exercised
  without an exchange. Re-stamped onto the clock, as PyneCore's live runner requires.
- A **bridge from another process**: with `tail = true` rows appended to the file by anything
  else (a collector script, a terminal, a cron job) are streamed as they appear.

## Install and use

```bash
pip install -e <skill>/templates/pynecore-fileprovider        # or scaffold a copy: new_plugin.py fileprovider DEST --name NAME
pyne plugin info file
pyne data download file:BTCUSDT@60                 # <data>/BTCUSDT_60.csv (or .ohlcv/.json/.txt) -> data/file_BTCUSDT_60.ohlcv + .toml
pyne run my_strategy file:BTCUSDT@60 -f -500       # backtest through the provider
pyne run my_strategy file:BTCUSDT@60 --live        # replay (replay_bars > 0 in the config), Ctrl+C to stop
<python> run_paper.py my_strategy file:BTCUSDT@60 --from -300 --bars 100     # paper run with a bar limit
```

The provider string symbol is a name (`BTCUSDT`); the file is found through, in order: the
`files` map, the symbol itself when it is a path ending in `.ohlcv/.csv/.json/.txt`, then
`<root>/<SYMBOL>_<TF>.<ext>` and `<root>/<SYMBOL>.<ext>`. With `root` empty the folders tried
are the data folder the provider was built for, the running CLI's data folder,
`$PYNE_WORK_DIR/data` and `./data` (so `--list-symbols`, which builds the provider without a
data folder, and tools that probe with a temporary folder still find the workdir's files).
Timeframe: the file's period must equal the requested one (`@60` for hourly data); otherwise a
`ProviderError` names both and points at `pyne data aggregate`.

## Configuration (`config/plugins/file.toml`)

| Key | Default | Meaning |
|-----|---------|---------|
| `root` | `""` (= `data/`) | Folder searched for `<SYMBOL>_<TF>.<ext>` / `<SYMBOL>.<ext>` |
| `files` | `{}` | Explicit map `{ "BTCUSDT" = "/exports/btc_1h.csv" }` |
| `source_timezone` | `"UTC"` | Timezone of naive timestamps in CSV/JSON/TXT (handed to PyneCore's converter) |
| `replay_bars` | `0` | Live mode: the last N bars are streamed instead of being history |
| `replay_from` | `""` | Live mode: stream bars from this UTC time (`YYYY-MM-DD[ HH:MM:SS]`); overrides `replay_bars` |
| `replay_speed` | `1.0` | Seconds between replayed bars (`0` = as fast as the runner takes them) |
| `replay_realtime` | `true` | Re-stamp replayed bars onto the clock (see below); `false` only for runners without the idle-bar watchdog |
| `emit_ticks` | `true` | One `is_closed=False` update (open → mid price, half volume) before each close |
| `tail` | `false` | After the file is exhausted, poll it and stream appended rows (original timestamps) |
| `poll_interval` | `2.0` | Seconds between polls when tailing |
| `symbol_map` | `{}` | Inherited from `LiveProviderConfig`; `request.security()` symbol translation in live runs |

## How it works

**Sources.** `.ohlcv` files are read directly with `OHLCVReader`. CSV/JSON/TXT files are passed
to PyneCore's `DataConverter().convert_to_ohlcv(path, provider="file", symbol=..., timezone=...)`,
which writes `<stem>.ohlcv` + `<stem>.toml` next to the source (column detection, timeframe
inference, tick-size analysis, 24/7 or detected sessions) and skips the work when the `.ohlcv`
is newer than the source. The sibling `.toml` is also the symbol info the provider returns
(`period` replaced by the requested timeframe); without one a 24/7 crypto-like `SymInfo` is
synthesised from the file's tick grid. So the symbol info of the generated
`data/file_<SYMBOL>_<TF>.toml` can be edited once and is reused.

**History (`download_ohlcv`).** Bars in `[time_from, time_to]` are read from the file and written
in pages of 5000 through `save_ohlcv_data`. Bars reserved for the replay (`replay_bars` /
`replay_from`) are excluded: with `replay_bars = 200` on a 10 340-bar file the download holds
10 140 bars (verified). `fetch_all_by_default = True`, so a download without `--from` takes the
whole file.

**Live replay (`connect` / `watch_ohlcv`).** `connect()` reloads the rows after the history end
(or after the replay cut when no download happened in this process), computes the current bar
slot and marks the provider connected. Each `watch_ohlcv` call hands out one update: a tick
(when `emit_ticks`) then the closed bar, waiting `replay_speed` between bars in sleeps of at
most 1.5 s so the runner's cancellation never loses progress. Re-stamping: the first replayed bar
gets the timestamp of the slot open now, each following bar one period later; the OHLCV values
are the file's. When the rows are exhausted the provider parks (or tails); stop the run with
Ctrl+C, or use `run_paper.py --bars / --duration`.

**Why re-stamp.** PyneCore's live runner synthesises a zero-volume bar whenever the last closed
bar's timestamp lies more than two periods plus a grace period in the past, and then drops every
real bar whose timestamp is not newer. Original 2025 timestamps streamed in 2026 would be buried
instantly. With `replay_realtime = true` the replay looks like a market happening now; the price
path is historical, the clock is current. Time-of-day logic in the script therefore sees today's
times, not the file's (set `replay_realtime = false` only when driving `ScriptRunner` yourself
without `live_ohlcv_generator`).

`feed_timeout_bars = None`: a paced replay is silent between bars by design, so the dead-feed
watchdog is disabled for this plugin.

## Verified (PyneCore 6.12.1, hourly BTC/USDT CSV with 10 340 rows)

| Check | Result |
|-------|--------|
| `pyne plugin info file` | capabilities `provider`, 11 config fields listed |
| `pyne data download file:BTCUSDT@60` with `replay_bars = 200` | `file_BTCUSDT_60.ohlcv` 10 140 bars, `.toml` with `[download] provider = "file:BTCUSDT@60"`, `prefix FILE`, `mintick 0.01`, `period 60` |
| `pyne run ema_cross file:BTCUSDT@60 -f -500` | backtest completed, 13 trades |
| `run_paper.py ema_cross file:BTCUSDT@60 --from -300 --bars 100` (`replay_speed 0.05`) | 100 live bars re-stamped from the current hour, 3 paper trades with stop/target exits, 1 catch-up bar |
| `pyne run notify_probe file:BTCUSDT@60 --live -f -200`, SIGINT after 18 s | 85 live bars processed, graceful shutdown, exit 0 |

## Adapting the template

- **Other formats** (parquet, SQLite, a vendor API dump): replace `_ohlcv_source()` by code that
  produces `OHLCV` objects (or writes a `.ohlcv` with `OHLCVWriter(path, period)` once) and keep
  the rest.
- **Different symbol metadata**: write a `.toml` next to the source (`SymInfo(...).save_toml()`)
  or build the `SymInfo` in `update_symbol_info()` from your own catalogue; sessions and tick
  size drive fills and rounding.
- **Several timeframes from one file**: aggregate with `pyne data aggregate` into separate
  files, or implement the aggregation in `download_ohlcv` with
  `pynecore.core.aggregator`.
- **A live collector**: have the other process append rows to a CSV; run with `tail = true`,
  `replay_bars = 0`, and the provider streams each new row (its own timestamp, so the rows must
  be current).
