"""
FreqTrade strategy that executes the signals of a PyneCore strategy script.

Pattern 2 of the FreqTrade integration: the Pyne strategy decides, FreqTrade executes.

The Pyne script must plot its decisions on the bar they are made, one column per signal with
1 on the signal bar and 0 elsewhere:

    enter_long = ta.crossover(fast, slow)
    exit_long = ta.crossunder(fast, slow)
    if enter_long:
        strategy.entry("Long", strategy.long)
    if exit_long:
        strategy.close("Long")
    plot(1 if enter_long else 0, "enter_long")
    plot(1 if exit_long else 0, "exit_long")
    plot(1 if enter_short else 0, "enter_short")
    plot(1 if exit_short else 0, "exit_short")

Do not rebuild signals from the trade list: a Trade is reported only on the bar where it
closes, so writing it back to its entry bar marks a bar with knowledge that did not exist yet
(a backtest then looks better than anything achievable live), and the position still open at
the end is missing altogether.

    user_data/strategies/
    ├── freqtrade_signals_strategy.py
    ├── pynecore_bridge.py
    └── scripts/my_strategy.py

Two classes, because FreqTrade refuses to load a strategy with `can_short = True` in a spot
config ("Short strategies cannot run in spot markets"), and spot is its default trading mode:

    PyneStrategySignals        long only (enter_long / exit_long); spot or futures config
    PyneStrategySignalsShort   also enter_short / exit_short; needs a futures (or margin) config:
                                   "trading_mode": "futures", "margin_mode": "isolated",
                                   pairs as "BTC/USDT:USDT"

    pip install "pynesys-pynecore>=6.12"         # into FreqTrade's environment
    freqtrade backtesting --strategy PyneStrategySignals
    freqtrade backtesting --strategy PyneStrategySignalsShort -c config_futures.json
    freqtrade lookahead-analysis --strategy PyneStrategySignals    # expect "no bias detected"

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

from pynecore_bridge import run_strategy

SCRIPT = Path(__file__).parent / "scripts" / "my_strategy.py"
INPUTS = {}                                    # keyed by the script's main() parameter names
SETTINGS = {"default_qty_type": "percent_of_equity", "default_qty_value": 10}

SIGNAL_COLUMNS = {                              # plot title -> FreqTrade column
    "enter_long": "enter_long",
    "exit_long": "exit_long",
    "enter_short": "enter_short",
    "exit_short": "exit_short",
}


class PyneStrategySignals(IStrategy):
    """Long side of the Pyne script's signals; loads with a spot or a futures config."""
    INTERFACE_VERSION = 3

    timeframe = "1h"
    startup_candle_count = 200      # at least the script's warm-up; check with recursive-analysis
    minimal_roi = {"0": 100}        # the Pyne script controls exits
    stoploss = -0.10                # safety net only
    can_short = False               # True only in PyneStrategySignalsShort (futures / margin)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        pair = metadata.get("pair", "BTC/USDT")
        plots, _closed, _open = run_strategy(dataframe, SCRIPT, pair=pair, timeframe=self.timeframe,
                                             inputs=INPUTS, settings=SETTINGS)
        for title, column in SIGNAL_COLUMNS.items():
            series = plots.get(title)
            dataframe[f"pyne_{column}"] = series.fillna(0).astype(int) if series is not None else 0
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[dataframe["pyne_enter_long"] == 1, "enter_long"] = 1
        if self.can_short:
            dataframe.loc[dataframe["pyne_enter_short"] == 1, "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[dataframe["pyne_exit_long"] == 1, "exit_long"] = 1
        if self.can_short:
            dataframe.loc[dataframe["pyne_exit_short"] == 1, "exit_short"] = 1
        return dataframe


class PyneStrategySignalsShort(PyneStrategySignals):
    """Both sides of the Pyne script's signals. FreqTrade loads it only with a futures or margin
    config ("trading_mode": "futures", "margin_mode": "isolated", pairs like "BTC/USDT:USDT")."""
    can_short = True
