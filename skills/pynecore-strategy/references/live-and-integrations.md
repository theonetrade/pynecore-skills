# Live mode, broker trading and integrations (DataFrames, FreqTrade, notifications)

## Contents

- [Live mode from the CLI](#live-mode-from-the-cli)
- [Paper and live runs with this skill's scripts](#paper-and-live-runs-with-this-skills-scripts)
- [Live mode from Python](#live-mode-from-python)
- [What changes in live mode](#what-changes-in-live-mode)
- [Broker trading](#broker-trading)
- [DataFrame bridge](#dataframe-bridge)
- [FreqTrade](#freqtrade)
- [Notifications and side effects](#notifications-and-side-effects)
- [Deployment notes](#deployment-notes)

## Live mode from the CLI

```bash
pyne run my_strategy ccxt:BYBIT:BTC/USDT:USDT@1 --live -f -500          # 500 bars of history, then stream
pyne run my_strategy ccxt:BINANCE:ETH/USDT@60 --live -f -1000 --no-plot # skip the ever-growing plot CSV
```

- `--live` needs a **provider string**, not a file. The built-in `ccxt` provider streams
  public candles; broker plugins (Bybit, Capital.com, cTrader, ...) are separate packages.
  Provider mode downloads the history again on every start and **overwrites the cached
  `data/<provider>_<SYMBOL>_<TF>.ohlcv` with just that range**; `run_paper.py` below appends to
  the cache instead.
- Spot pairs through `ccxt` need `default_type = "spot"` in `config/plugins/ccxt.toml`: the
  default `"swap"` is applied to the websocket client, which then rejects `BTC/USDT`
  (`does not have market symbol`) and the runner reconnects forever. Perpetuals keep `swap`
  and the `:SETTLE` suffix.
- `-f -N` is the warm-up: at least 2x the longest lookback, more for recursive averages
  (EMA/RMA/RSI/ATR). Default 500.
- Ctrl+C stops with a graceful shutdown (`--shutdown-timeout`).
- Strategy statistics CSV is rewritten after every closed bar; the trade CSV appends.

## Paper and live runs with this skill's scripts

```bash
<python> <skill>/scripts/run_paper.py my_strategy ccxt:BINANCE:BTC/USDT@1 --from -500 --bars 60      # simulated fills
<python> <skill>/scripts/run_paper.py my_strategy ccxt:BYBIT:BTC/USDT:USDT@5 --duration 4h -i fast_length=10 --json
<python> <skill>/scripts/run_live.py  my_strategy bybit:BTCUSDT@60 --from -1000          # preflight + dry run (exit 2)
<python> <skill>/scripts/run_live.py  my_strategy bybit:BTCUSDT@60 --from -1000 --yes    # real orders
<python> <skill>/scripts/run_live.py  --list-brokers
```

**`run_paper.py`** drives `ScriptRunner(live=True)` with PyneCore's own live feed
(`pynecore.core.live_runner.live_ohlcv_generator`), nothing is sent to a venue:

1. the warm-up comes from the candle cache `data/<provider>_<SYMBOL>_<TF>.ohlcv`, which the
   script first brings up to the last closed bar by appending (a missing file is downloaded for
   the warm-up range only, not for a year);
2. the last N closed bars (`--from -N`, default 500) run with orders suppressed, then the
   websocket stream: every bar close runs the strategy, fills are checked tick by tick, one line
   per closed bar is printed, `output/<script>_paper_strat.csv` (rewritten) and
   `_paper_trade.csv` (appended) are written;
3. `--input` / `--setting` override as in `run_backtest.py` without touching the `.toml`;
   `--bars N`, `--duration 4h` or Ctrl+C stop the run, the websocket is closed gracefully and
   the `run_backtest.py` summary (trades, net, drawdown, open position) follows, `--json` for
   machines.

Live bars are not written to the cache; the next run or `download_candles.py` appends them.
Verified on 6.12: Binance spot 1-minute bars over the public websocket (two closed bars,
simulated position, clean disconnect) and four paper trades with tick-level exits on a paced
150-bar replay feed.

**`run_live.py`** launches `pyne run SCRIPT PROVIDER --broker` and refuses to start unless the
script is a strategy, the plugin subclasses `BrokerPlugin` (the built-in `ccxt` is data-only;
broker plugins are `pynesys-pynecore-bybit`, `-capitalcom`, `-ctrader`),
`config/plugins/<plugin>.toml` has active settings, and `--yes` is given. The dry run shows the
sandbox/demo state (REAL MONEY otherwise), the `scripts/<name>.toml` that will trade (no
overrides, on purpose), and the exact command. While running it copies pyne's output to
`output/logs/live_<script>_<UTC time>.log`, frames the `pyne broker resume RUN_ID QUARANTINE_ID`
command when a quarantine starts, forwards Ctrl+C / SIGTERM for a graceful stop (positions stay
on the venue) and returns pyne's exit code. Without a broker plugin installed it could only be
verified up to the launch; the output streaming and quarantine detection were tested on a
stand-in process.

Gotchas:

- Bars that close while the websocket connects are processed as history (orders suppressed);
  the summary reports them as catch-up. A feed that emits everything at once (the `replay`
  plugin's fixtures) is all catch-up: stop it with `--duration`, not `--bars`.
- Spot pairs need `default_type = "spot"` (see above); `run_paper.py` sets it for a symbol
  without `:SETTLE` for the run.
- Binance without `api.binance.com`: a `[binance]` table with `urls.api.public` and
  `urls.api.ws.spot = "wss://data-stream.binance.vision/ws"` in `config/plugins/ccxt.toml`
  (details in [cli-and-data.md](cli-and-data.md#binance-without-apibinancecom)).

## Live mode from Python

The feed is any iterable that yields the closed history, then `LIVE_TRANSITION`, then live
updates; pass `live=True`:

```python
from pynecore.core.script_runner import ScriptRunner, LIVE_TRANSITION
from pynecore.types.ohlcv import OHLCV

def feed():
    yield from history_bars                       # closed bars, oldest first
    yield LIVE_TRANSITION
    while True:
        bar = wait_for_candle()                   # your websocket / REST code
        yield OHLCV(timestamp=bar.ts, open=bar.o, high=bar.h, low=bar.l, close=bar.c,
                    volume=bar.v, is_closed=bar.closed)

runner = ScriptRunner(Path("my_strategy.py"), feed(), syminfo, live=True)
for candle, plot_data, new_trades in runner.run_iter():
    ...                                           # yields on every bar close, immediately
```

- `is_closed=False` is a price update of the forming bar; `is_closed=True` closes it; a new
  timestamp opens a new bar. REST polling that only knows closed candles must drop the
  still-forming last candle (`templates/live_ccxt.py` shows how).
- Without `live=True` the runner reads one bar ahead and every result arrives one candle late.
- `run_iter(on_tick=callback)` exposes intra-bar updates.
- A bar that would move time backwards is dropped with a warning.

## What changes in live mode

| Aspect | Historical phase | Live phase |
|--------|------------------|------------|
| `barstate.ishistory` / `isrealtime` | `True` / `False` | `False` / `True` |
| `barstate.islast` | `False` | `True` |
| `barstate.islastconfirmedhistory` | `True` on the final history bar | `False` |
| `barstate.isconfirmed` | `True` | `True` only on the closing update |
| Strategy orders | **Suppressed** (no phantom trades from history) | Active, simulated (paper) unless a broker plugin is attached |
| Indicator execution | once per bar | on every update of the forming bar |
| Strategy execution | once per bar | on bar close; every tick with `calc_on_every_tick=True` |
| `Persistent` / `IBPersistent` | identical | `Persistent` rolls back between ticks of one bar, `IBPersistent` accumulates |
| Order fills | bar-based | checked against each tick (magnifier-style) at bar close |
| `timenow` | pinned to the last bar during `pyne run` backtests | system clock |

Because history orders are suppressed, a live strategy starts flat even if the history would
have produced a position. Design the logic so it can start mid-trend (do not rely on an initial
entry bar).

## Broker trading

`pyne run my_strategy <provider string> --broker` sends orders to the venue through a
`BrokerPlugin` (implies `--live`). Credentials live in `config/plugins/<plugin>.toml` and
`config/brokers.toml`, never in the script. A bot that detects outside interference with its
orders (manual cancel, moved stop) enters a **quarantine**: the process stays alive, keeps
syncing, refuses to trade, and logs a `pyne broker resume RUN_ID QUARANTINE_ID` command to
continue once you have settled the venue state. Run several instances of one strategy with
`--run-label`.

## DataFrame bridge

`templates/pynecore_bridge.py` converts a pandas DataFrame (`open/high/low/close/volume` with
a `date` column or DatetimeIndex) into `OHLCV` objects, builds a crypto `SymInfo` for a pair
and runs an indicator or a strategy:

```python
plots = run_indicator(df, Path("scripts/rsi.py"), pair="BTC/USDT", timeframe="1h", inputs={"length": 14})
df["rsi"] = plots["RSI"]

plots, closed_trades, open_trades = run_strategy(df, Path("scripts/my_strategy.py"), pair, "1h",
                                                  settings={"default_qty_value": 10})
```

Always run over the **full** history on each call: scripts are stateful and a partial run
gives different values. A few thousand bars take tens of milliseconds.

## FreqTrade

Two patterns, both in `templates/`:

1. **Indicators as data sources** (`freqtrade_indicators_strategy.py`): PyneCore computes
   values, FreqTrade's `populate_entry_trend` / `populate_exit_trend` decide. Most flexible.
2. **Strategy signals** (`freqtrade_signals_strategy.py`): the Pyne strategy decides and
   **plots** its decisions (`plot(1 if enter_long else 0, "enter_long")`) on the bar they fire;
   FreqTrade copies those columns into `enter_long` / `exit_long`. FreqTrade then acts at the
   next candle's open, which matches PyneCore's next-bar fill model.

Do **not** derive signals from the trade list: a trade is reported only when it closes, so
mapping it back to `entry_bar_index` writes knowledge into the past, and the position still
open at the end is missing. Set `startup_candle_count` to at least the warm-up the script
needs, `minimal_roi = {"0": 100}` when the Pyne script owns the exits, and install
`pynesys-pynecore>=6.12` into FreqTrade's environment.

## Notifications and side effects

PyneCore has no webhook. `alert("text")` prints to the console only. A script is Python, so it
can call Telegram, Discord, email or any HTTP endpoint directly:

```python
import os, urllib.request, json

def notify(text: str) -> None:
    url = os.environ.get("NOTIFY_WEBHOOK")
    if url:
        urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"}))

if barstate.isrealtime and long_signal:
    notify(f"LONG {syminfo.ticker} @ {close}")
```

Gate every side effect with `barstate.isrealtime` (and usually `barstate.isconfirmed`) so the
historical bars and backtests stay silent. Keep secrets in environment variables.

## Deployment notes

- Pin the PyneCore version (`pynesys-pynecore==6.12.x`) in the environment that runs live.
- Keep the symbol `.toml` under version control; it drives rounding and sessions.
- Log to `output/logs/` (default) and rotate externally; disable the plot CSV with `--no-plot`.
- Test the logic in paper mode first (`run_paper.py`, or `--live` without `--broker`) and the
  exact broker command with `run_live.py`'s dry run before `--yes`.
- A supervisor (systemd, container restart policy) should restart the process; quarantine
  survives restarts by design.
