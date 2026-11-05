# /// script
# requires-python = ">=3.11"
# dependencies = ["pynesys-pynecore[cli]>=6.12", "ccxt"]
# ///

"""
Run a Pyne script on live exchange candles with CCXT, from Python, in live mode.

    uv run live_ccxt.py            # or: .venv/bin/python live_ccxt.py

The feed yields the closed history first, then LIVE_TRANSITION, then every new candle once it
has closed. With live=True the runner executes a live bar as soon as it arrives (without it the
runner reads one bar ahead to know which bar is last, and every result would come one candle
late). Public market data only, no API key.

For a strategy script the same feed works: orders are suppressed on the historical bars and
simulated (paper trading) from the transition on. For real orders use `pyne run --broker` with
a broker plugin.
"""
import time
from pathlib import Path

import ccxt

from pynecore.core.script_runner import ScriptRunner, LIVE_TRANSITION
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1m"             # CCXT notation
PERIOD = "1"                 # the same timeframe as a PyneCore period string ("60" = 1h, "D" = 1 day)
HISTORY_BARS = 300           # warm-up: at least 2x the longest lookback in the script
LIVE_BARS = 3                # stop after this many new closed bars (0 = run forever)
POLL_INTERVAL_SEC = 10
SCRIPT = Path(__file__).parent / "indicator_template.py"   # any @pyne script


def fetch_closed_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int) -> list[OHLCV]:
    """The most recent closed candles; the still-forming last candle is dropped."""
    bar_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
    return [
        OHLCV(timestamp=bar[0], open=bar[1], high=bar[2], low=bar[3], close=bar[4], volume=bar[5])
        for bar in raw if bar[0] + bar_ms <= now_ms
    ][-limit:]


def live_feed(exchange: ccxt.Exchange, symbol: str, timeframe: str, history: int, live_bars: int, poll_sec: int):
    candles = fetch_closed_ohlcv(exchange, symbol, timeframe, limit=history)
    last_ts = 0
    for candle in candles:
        last_ts = candle.timestamp
        yield candle
    yield LIVE_TRANSITION
    received = 0
    while live_bars <= 0 or received < live_bars:
        time.sleep(poll_sec)
        for candle in fetch_closed_ohlcv(exchange, symbol, timeframe, limit=5):
            if candle.timestamp > last_ts:
                last_ts = candle.timestamp
                received += 1
                yield candle
    # A websocket feed would instead yield OHLCV(..., is_closed=False) on every price update
    # of the forming candle and OHLCV(..., is_closed=True) when it closes.


def main() -> None:
    base, quote = SYMBOL.split("/")
    syminfo = SymInfo(
        prefix=EXCHANGE.upper(), description=SYMBOL, ticker=SYMBOL.replace("/", ""),
        currency=quote, basecurrency=base, period=PERIOD, type="crypto",
        mintick=0.01, pricescale=100, minmove=1, pointvalue=1.0, mincontract=0.00001,
        timezone="UTC", volumetype="base", opening_hours=[], session_starts=[], session_ends=[],
    )
    client = getattr(ccxt, EXCHANGE)({"enableRateLimit": True})
    runner = ScriptRunner(
        script_path=SCRIPT,
        ohlcv_iter=live_feed(client, SYMBOL, TIMEFRAME, HISTORY_BARS, LIVE_BARS, POLL_INTERVAL_SEC),
        syminfo=syminfo,
        live=True,
    )
    for i, item in enumerate(runner.run_iter()):
        candle, plot_data = item[0], item[1]
        if i >= HISTORY_BARS - 3:            # last historical bars + every live bar
            values = ", ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in plot_data.items())
            print(f"{candle.timestamp}  close={candle.close:.2f}  {values}")
    print("done")


if __name__ == "__main__":
    main()
