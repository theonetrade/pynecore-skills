# pynecore-fileprovider

PyneCore data provider plugin that serves candles from local files.

```bash
pip install -e .                                    # or: uv pip install -e .
pyne plugin info file
pyne data download file:BTCUSDT@60                  # history from <data>/BTCUSDT_60.csv (or .ohlcv/.json/.txt)
pyne run my_strategy file:BTCUSDT@60                # backtest through the provider
pyne run my_strategy file:BTCUSDT@60 --live         # replay the last `replay_bars` bars as live bars
```

Configuration in `<workdir>/config/plugins/file.toml` (generated on first use):

```toml
root = "/exports"                     # folder searched for <SYMBOL>_<TF>.<ext> and <SYMBOL>.<ext>
files = { "BTCUSDT" = "/exports/btc_1h.csv" }
replay_bars = 200                     # live mode: these last bars are streamed instead of being history
replay_speed = 1.0                    # seconds between replayed bars
emit_ticks = true                     # one intra-bar update before each close
tail = false                          # keep watching the file for appended rows
```

The source timeframe must match the requested one (`@60` for hourly files). CSV/JSON/TXT files
are converted with PyneCore's converter next to the source; the symbol info comes from the
sibling `.toml` when present.
