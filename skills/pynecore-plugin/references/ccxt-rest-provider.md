# CCXT over REST polling (`templates/pynecore-ccxtrest`)

Plugin name `ccxtrest`, class `CCXTRestProvider(CCXTProvider)`.

## What it is for

The built-in `ccxt` plugin streams live bars through ccxt.pro websockets. Where websockets are
blocked (corporate networks, some hosts), where an exchange has no ccxt.pro support, or where a
spot pair fails on the `default_type` mismatch, this plugin delivers the same live mode by
polling `fetch_ohlcv` over REST. Everything else is inherited from `CCXTProvider`: symbol
listing, symbol info, history download, the per-exchange configuration tables.

It is also the template for any venue that only offers polling: replace `_fetch` with your
client call and keep the task + queue structure.

## Install and use

```bash
pip install -e <skill>/templates/pynecore-ccxtrest     # needs pynesys-pynecore[ccxt]
pyne data download ccxtrest:BINANCE:BTC/USDT@60 --from 2024-01-01     # same files as ccxt would write, prefix ccxtrest_
pyne run my_strategy ccxtrest:BINANCE:BTC/USDT@1 --live -f -500
<python> run_paper.py my_strategy ccxtrest:BINANCE:BTC/USDT@1 --from -300 --bars 5
```

Provider strings are the ccxt ones (`ccxtrest:EXCHANGE:SYMBOL[:SETTLE]@TF`, `multi_broker`
inherited). Data files are `data/ccxtrest_<EXCHANGE>_<SYMBOL>_<TF>.ohlcv`, separate from the
`ccxt_...` files.

## Configuration

Two files are involved, by inheritance:

- `config/plugins/ccxtrest.toml`: the dataclass fields of `CCXTRestConfig(CCXTConfig)`:
  `apiKey`, `secret`, `password`, `sandbox`, `default_type`, `symbol_map` (inherited, mostly
  irrelevant without websockets) and the plugin's own `poll_interval` (default 5 s),
  `request_timeout` (20 s), `poll_limit` (5 candles per poll).
- `config/plugins/ccxt.toml`: the **exchange tables** (`[binance] urls = ..., options = ...`).
  `CCXTProvider.__init__` reads that file by its fixed name, so a `[binance]` override of the
  public REST host applies to this plugin as well. Verified: with
  `urls = { api = { public = "https://data-api.binance.vision/api/v3" } }` in `ccxt.toml` the
  REST plugin downloaded and polled Binance through that host.

`poll_interval` must stay well below the runner's grace period for a missed bar (15 s for
1-minute bars, up to 30 s for longer ones): the runner synthesises a zero-volume bar when a
close is more than `2·period + grace` late, and a real close arriving after that is dropped.
5 s is a good default for 1-minute bars; one poll per `poll_interval` is one REST request, so
mind the exchange's rate limits for several instances.

## How it works

```
connect()            load_markets (in a thread, with timeout), check the symbol, create asyncio.Queue,
                     start _poll_loop() as a task
_poll_loop()         every poll_interval: fetch_ohlcv(symbol, tf, limit=poll_limit) in a thread with
                     request_timeout; candles[:-1] are final (the last one is forming or not rolled over yet):
                     new closed bars -> queue (is_closed=True); the forming candle -> queue (is_closed=False)
                     only when its values changed; an exception -> queue, loop ends
watch_ohlcv()        await queue.get(); re-raise an exception item (the runner then reconnects)
backfill_closed_bars fetch_ohlcv(since=since_ms) -> closed candles after since_ms (the bars that closed while
                     the history was being prepared)
disconnect()         cancel the task
```

Design points:

- **History ends at the last closed bar**: `download_ohlcv` clamps `time_to` to the last
  completed slot before calling the inherited download. The stock ccxt plugin stores the candle
  still forming when a range ends "now" (and `continue` never rewrites it); this plugin follows
  the closed-bars-only rule of the provider contract instead.
- **Task + queue** keeps `watch_ohlcv` cancellation-safe: the runner awaits it with a timeout of
  at most 2 s and cancels it; a `queue.get()` loses nothing, whereas a `to_thread(fetch_ohlcv)`
  awaited directly would be abandoned mid-flight.
- **Closed detection** uses "there is a newer candle after it", not the clock alone: right after
  a boundary the REST answer may still end with the old candle until the exchange opens the next
  one; treating it as closed early would publish pre-close values. `ts + period <= now` (exchange
  time, `adjustForTimeDifference`) is a second guard.
- **First poll** only records the latest closed timestamp; the warm-up and `backfill_closed_bars`
  already cover the past, so no stale closed bars are replayed on connect.
- The forming bar is re-sent only when a value changed, which keeps the script's intra-bar
  executions proportional to real quote changes while still feeding the dead-feed watchdog.
- No ccxt.pro client is created, so the spot/`default_type` websocket problem of the built-in
  plugin does not arise.

## Verified (PyneCore 6.12.1, Binance spot through the public data host)

| Check | Result |
|-------|--------|
| `pyne plugin list` | `ccxtrest  CCXT REST  v0.1.0  [provider]` |
| `pyne data download ccxtrest:BINANCE:BTC/USDT@60 --from 3` | `ccxtrest_BINANCE_BTC_USDT_60.ohlcv` + `.toml`, `ccxtrest.toml` generated |
| `run_paper.py ema_cross ccxtrest:BINANCE:BTC/USDT@1 --from -300 --bars 2` | 341 bars appended to the cache, connect, two closed 1-minute bars 12:42 and 12:43 UTC delivered within seconds of their close, graceful shutdown, exit 0 |
| `plugin_check.py ccxtrest --symbol BINANCE:BTC/USDT --timeframe 1 --bars 20 --live-seconds 80` | all static checks pass (`multi_broker=True`, `feed_timeout_bars=30` inherited, `backfill_closed_bars` overridden), symbol info `BINANCE:BTCUSDT mintick 0.01`, download of the last 20 closed bars, live stream: 2 closed bars and 16 intra-bar updates in 80 s, first update immediately |

## Adapting the template

- Another REST-only venue: subclass `ProviderPlugin`/`LiveProviderPlugin` directly, implement the
  five history members, keep `connect/_poll_loop/watch_ohlcv/backfill_closed_bars` and replace
  `_fetch` with the venue call returning `[ts_ms, o, h, l, c, v]` rows (newest last).
- Slower venues: raise `request_timeout`, keep `poll_interval` under the grace period, and set
  `poll_limit` so that two polls cannot miss a close (`poll_limit >= poll_interval / period + 2`).
- Ticks instead of candles: aggregate trades into the forming bar inside the loop and emit
  `is_closed=False` snapshots; emit `is_closed=True` when the clock passes the slot end.
