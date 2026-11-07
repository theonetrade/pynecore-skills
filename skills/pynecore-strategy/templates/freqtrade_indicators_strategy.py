"""
FreqTrade strategy that uses PyneCore indicator scripts as data sources.

Pattern 1 of the FreqTrade integration: PyneCore computes the indicators (any script that
plots values), the entry/exit rules stay in FreqTrade's own DataFrame logic.

Install into user_data/strategies/ together with pynecore_bridge.py and a scripts/ folder:

    user_data/strategies/
    ├── freqtrade_indicators_strategy.py
    ├── pynecore_bridge.py
    └── scripts/
        ├── rsi.py
        └── bollinger.py

    pip install "pynesys-pynecore>=6.12"
    freqtrade backtesting --strategy PyneIndicatorStrategy
    freqtrade lookahead-analysis --strategy PyneIndicatorStrategy    # expect "no bias detected"
    freqtrade recursive-analysis --strategy PyneIndicatorStrategy --startup-candle 100 200 400

populate_indicators() receives the full DataFrame on every new candle; the bridge re-runs the
scripts over all of it, which is both correct (the scripts are stateful) and cheap (about 20 ms
per pair for 1000 candles of RSI + Bollinger; FreqTrade 2026.10, PyneCore 6.12.1).

Installing with uv from inside a FreqTrade checkout: its pyproject.toml sets
`[tool.uv] exclude-newer = "1 week"`, which hides a PyneCore release younger than a week; add
`--exclude-newer-package "pynesys-pynecore=false"`, run uv from another folder, or use pip (a date
instead of false means the start of that day and still hides a release uploaded that day).
"""
from pathlib import Path

import pandas as pd

try:
    from freqtrade.strategy import IStrategy
except ImportError as exc:  # pragma: no cover
    raise ImportError("FreqTrade is not installed; this file runs inside FreqTrade") from exc

from pynecore_bridge import run_indicator

SCRIPTS_DIR = Path(__file__).parent / "scripts"


class PyneIndicatorStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    # Not just the longest lookback: recursive indicators (RSI, EMA, ATR) keep converging after it.
    # With 100 candles RSI(14) still differed by 0.006-0.015 % from a 400-candle start (OKX, Gate 1h);
    # with 200, 0.000 %.
    startup_candle_count = 200
    minimal_roi = {"0": 0.10, "60": 0.05, "120": 0.02}
    stoploss = -0.05
    can_short = False

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        pair = metadata.get("pair", "BTC/USDT")

        rsi = run_indicator(dataframe, SCRIPTS_DIR / "rsi.py", pair=pair, timeframe=self.timeframe,
                            inputs={"length": 14})
        dataframe["rsi"] = rsi.get("RSI")

        bb = run_indicator(dataframe, SCRIPTS_DIR / "bollinger.py", pair=pair, timeframe=self.timeframe)
        dataframe["bb_upper"] = bb.get("Upper")
        dataframe["bb_basis"] = bb.get("Basis")
        dataframe["bb_lower"] = bb.get("Lower")
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["rsi"] < 30) & (dataframe["close"] <= dataframe["bb_lower"] * 1.01),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["rsi"] > 70) & (dataframe["close"] >= dataframe["bb_upper"] * 0.99),
            "exit_long",
        ] = 1
        return dataframe
