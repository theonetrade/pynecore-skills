# /// script
# requires-python = ">=3.11"
# dependencies = ["pynesys-pynecore[cli]>=6.12"]
# ///

"""
Feed candles from any Python source (API, database, DataFrame, generator) into a Pyne script.

    uv run custom_feed.py            # or: .venv/bin/python custom_feed.py

No files are involved: build OHLCV objects (timestamps in UNIX milliseconds) and a SymInfo by
hand, pass any iterable to ScriptRunner. Replace generate_candles() with your source.
"""
import math
import random
from pathlib import Path

from pynecore.core.script_runner import ScriptRunner
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

SCRIPT = Path(__file__).parent / "strategy_template.py"


def generate_candles(n: int = 500) -> list[OHLCV]:
    """Deterministic synthetic hourly candles (seeded) standing in for a real source."""
    rng = random.Random(42)
    out: list[OHLCV] = []
    price = 42_000.0
    momentum = 0.0
    t0 = 1_704_067_200_000                # 2024-01-01 00:00 UTC in ms
    for i in range(n):
        momentum = momentum * 0.95 + rng.gauss(0, 50)
        price = max(price + momentum + math.sin(i / 25) * 40, 1000.0)
        spread = abs(momentum) + rng.uniform(40, 120)
        o = price + rng.uniform(-spread * 0.3, spread * 0.3)
        c = price + rng.uniform(-spread * 0.3, spread * 0.3)
        out.append(OHLCV(timestamp=t0 + i * 3_600_000, open=o, high=max(o, c) + rng.uniform(0, spread * 0.4),
                         low=min(o, c) - rng.uniform(0, spread * 0.4), close=c, volume=rng.uniform(500, 5000)))
    return out


def main() -> None:
    syminfo = SymInfo(
        prefix="BINANCE", description="Bitcoin / Tether", ticker="BTCUSDT",
        currency="USDT", basecurrency="BTC", period="60", type="crypto",
        mintick=0.01, pricescale=100, minmove=1, pointvalue=1.0, mincontract=0.00001,
        timezone="UTC", volumetype="base", opening_hours=[], session_starts=[], session_ends=[],
    )
    candles = generate_candles()
    runner = ScriptRunner(SCRIPT, candles, syminfo,
                          settings={"default_qty_type": "percent_of_equity", "default_qty_value": 10})
    trades = []
    for item in runner.run_iter():
        if len(item) == 3:
            trades.extend(item[2])
    print(f"{len(candles)} bars, {len(trades)} closed trades, net {sum(t.profit for t in trades):+.2f}")
    for t in trades[:5]:
        side = "LONG" if t.size > 0 else "SHORT"
        print(f"  {side:<5} bars {t.entry_bar_index}->{t.exit_bar_index}  {t.entry_price:.2f}->{t.exit_price:.2f}  P&L {t.profit:+.2f}  ({t.exit_comment})")


if __name__ == "__main__":
    main()
