# pynecore-fileprovider

PyneCore data provider plugin that serves candles from local files: `.ohlcv`, CSV, JSON, TXT,
parquet and SQLite, aggregated to a higher timeframe when the requested one is larger than the
file's.

```bash
pip install -e .                                    # or: uv pip install -e .   (+ pip install pyarrow for .parquet)
pyne plugin info file
pyne data download file:BTCUSDT@60                  # history from <data>/BTCUSDT_60.csv (or .ohlcv/.json/.txt/.parquet/.sqlite)
pyne data download file:BTCUSDT@240                 # the same hourly file aggregated to 4h
pyne run my_strategy file:BTCUSDT@60                # backtest through the provider
pyne run my_strategy file:BTCUSDT@1D --live         # replay the last `replay_bars` source bars as live (daily) bars
```

Configuration in `<workdir>/config/plugins/file.toml` (generated on first use):

```toml
root = "/exports"                     # folder searched for <SYMBOL>_<TF>.<ext>, <SYMBOL>.<ext> and <SYMBOL>_<lower tf>.<ext>
files = { "BTCUSDT" = "/exports/btc_1h.parquet" }
sqlite_table = "candles"              # or sqlite_query = "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = 'BTCUSDT'"
replay_bars = 200                     # live mode: these last source bars are streamed instead of being history
replay_speed = 1.0                    # seconds between replayed source bars
emit_ticks = true                     # intra-bar updates before each close
tail = false                          # keep watching the file for appended rows
```

CSV/JSON/TXT files are converted with PyneCore's converter next to the source; parquet and SQLite
by this plugin, into the same `<stem>.ohlcv` cache. The requested timeframe may equal the file's
or be a multiple of it (`validate_aggregation`): history is aggregated once into
`<stem>_agg_<TF>.ohlcv`, the live replay bar by bar with PyneCore's HTF aggregator. The symbol
info comes from the sibling `.toml` when present.
