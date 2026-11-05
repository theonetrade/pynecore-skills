"""
Bridge between pandas DataFrames and PyneCore's OHLCV iterator.

Drop this file next to the code that owns the DataFrame (a FreqTrade strategy, a research
notebook, a batch job) and call:

    plots = run_indicator(df, Path("scripts/rsi.py"), pair="BTC/USDT", timeframe="1h")
    df["rsi"] = plots["RSI"]

    plots, closed_trades, open_trades = run_strategy(df, Path("scripts/my_strategy.py"),
                                                      pair="BTC/USDT", timeframe="1h",
                                                      inputs={"fast_len": 10},
                                                      settings={"default_qty_value": 10})

Requires: pandas, pynesys-pynecore>=6.12.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from pynecore.core.script_runner import ScriptRunner
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

# Framework timeframe string -> PyneCore period (minutes as a string, or D/W/M)
TIMEFRAME_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "8h": "480", "12h": "720",
    "1d": "D", "3d": "3D", "1w": "W", "1M": "M",
}


def dataframe_to_ohlcv(df: pd.DataFrame) -> list[OHLCV]:
    """Rows of a DataFrame with open/high/low/close/volume columns as OHLCV objects.

    Bar times come from a ``date`` column (where FreqTrade keeps them) or a DatetimeIndex.
    Naive times are read as UTC. PyneCore timestamps are UNIX milliseconds.
    """
    if "date" in df.columns:
        dates = pd.DatetimeIndex(df["date"])
    elif isinstance(df.index, pd.DatetimeIndex):
        dates = df.index
    else:
        raise ValueError("the DataFrame needs a 'date' column or a DatetimeIndex")
    if dates.tz is None:
        dates = dates.tz_localize("UTC")
    timestamps = (dates - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)
    return [
        OHLCV(timestamp=int(ts), open=float(r.open), high=float(r.high), low=float(r.low),
              close=float(r.close), volume=float(r.volume))
        for ts, r in zip(timestamps, df.itertuples())
    ]


def create_syminfo(pair: str = "BTC/USDT", timeframe: str = "1h", *, prefix: str = "EXCHANGE",
                   mintick: float = 0.01, mincontract: float | None = None) -> SymInfo:
    """A 24/7 crypto SymInfo for a pair. Adjust mintick / mincontract to the venue's filters:
    they drive price rounding and lot rounding of simulated fills."""
    base, quote = pair.split("/") if "/" in pair else (pair[:-4], pair[-4:])
    pricescale = int(round(1 / mintick))
    return SymInfo(
        prefix=prefix, description=f"{base} / {quote}", ticker=pair.replace("/", ""),
        currency=quote, basecurrency=base, period=TIMEFRAME_MAP.get(timeframe, "60"),
        type="crypto", mintick=mintick, pricescale=pricescale, minmove=1, pointvalue=1.0,
        mincontract=mincontract if mincontract is not None else (0.00001 if base == "BTC" else 0.0001),
        timezone="UTC", volumetype="base", opening_hours=[], session_starts=[], session_ends=[],
    )


def _collect_plots(results: dict[str, list], plot_data: dict) -> None:
    for key, value in plot_data.items():
        results.setdefault(key, []).append(value)


def _to_series(results: dict[str, list], index) -> dict[str, pd.Series]:
    return {key: pd.Series(values, index=index[:len(values)], dtype="float64" if all(isinstance(v, (int, float)) for v in values) else object)
            for key, values in results.items()}


def run_indicator(df: pd.DataFrame, script_path: Path, pair: str = "BTC/USDT", timeframe: str = "1h",
                  inputs: dict[str, Any] | None = None, settings: dict[str, Any] | None = None,
                  syminfo: SymInfo | None = None) -> dict[str, pd.Series]:
    """Run an indicator script over the whole frame; one pandas Series per plot title.

    Run it over the full history every time: the script's state (averages, Persistent
    variables) depends on every earlier bar, so processing only the new rows gives different
    values. A few thousand bars take tens of milliseconds.
    """
    runner = ScriptRunner(script_path=script_path, ohlcv_iter=dataframe_to_ohlcv(df),
                          syminfo=syminfo or create_syminfo(pair, timeframe),
                          inputs=inputs, settings=settings)
    results: dict[str, list] = {}
    for _candle, plot_data in runner.run_iter():
        _collect_plots(results, plot_data)
    return _to_series(results, df.index)


def run_strategy(df: pd.DataFrame, script_path: Path, pair: str = "BTC/USDT", timeframe: str = "1h",
                 inputs: dict[str, Any] | None = None, settings: dict[str, Any] | None = None,
                 syminfo: SymInfo | None = None) -> tuple[dict[str, pd.Series], list, list]:
    """Run a strategy script; returns (plots, closed_trades, open_trades).

    ``closed_trades`` are Trade objects in exit order (fields: entry_bar_index, exit_bar_index,
    entry_price, exit_price, size, profit, cum_profit, ...). ``open_trades`` is the position
    still held after the last bar. To hand signals to an executor, plot them in the script on
    the bar they fire (see freqtrade_signals_strategy.py) instead of deriving them from the
    trade list, whose entries are only known once the trade has closed.
    """
    runner = ScriptRunner(script_path=script_path, ohlcv_iter=dataframe_to_ohlcv(df),
                          syminfo=syminfo or create_syminfo(pair, timeframe),
                          inputs=inputs, settings=settings)
    results: dict[str, list] = {}
    closed: list = []
    for _candle, plot_data, new_trades in runner.run_iter():
        _collect_plots(results, plot_data)
        closed.extend(new_trades)
    return _to_series(results, df.index), closed, list(runner.script.position.open_trades)
