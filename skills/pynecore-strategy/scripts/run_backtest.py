#!/usr/bin/env python3
r"""
Backtest a Pyne script on a data file from the command line, with input/setting overrides,
date filters and parameter sweeps. Prints a compact summary and optionally JSON.

Run it with the Python interpreter that has `pynecore` installed (the project's venv):

    .venv/bin/python run_backtest.py SCRIPT.py DATA.csv
    .venv/bin/python run_backtest.py SCRIPT.py DATA.ohlcv \
        --input fast_len=10 --input slow_len=30 \
        --setting default_qty_value=10 --setting commission_value=0.05 \
        --from 2023-01-01 --to 2024-06-30 --strat out/stats.csv --trade out/trades.csv
    .venv/bin/python run_backtest.py SCRIPT.py DATA.ohlcv \
        --sweep fast_len=5:30:5 --sweep slow_len=20,40,60 --metric profit_factor --min-trades 30

Data: an `.ohlcv` file with its `.toml` next to it, or a CSV/JSON/TXT that is converted in
place first (the `.ohlcv` and `.toml` are written next to the source; symbol guessed from the
file name unless --symbol / --provider are given). Review the generated `.toml`.

Overrides: --input uses the `main()` parameter names, --setting the decorator argument names;
unknown keys fail loudly. Values are parsed as Python literals (`10`, `2.5`, `true`, `"EMA"`),
unquoted text stays a string. Nothing is written into the script's `.toml`.

Sweep: --sweep name=start:stop:step (inclusive, ints or floats) or name=a,b,c; several --sweep
options form the cartesian product. Points are ranked by --metric, --min-trades hides noisy
points, --top limits the table. Prefer a plateau of good neighbours over the single best cell.

Output: a summary of trades, net profit, drawdown (closed-trade equity curve), profit factor,
win rate, averages, commission and the long/short split; for an indicator, the plot columns with
their first valid bar and last value. --plot/--strat/--trade write the same CSV files as
`pyne run`. --json prints everything as JSON.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_value(text: str):
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("na", "nan", "none"):
        return float("nan")
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def parse_kv(items: list[str] | None) -> dict:
    result: dict = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"expected name=value, got {item!r}")
        name, value = item.split("=", 1)
        result[name.strip()] = parse_value(value)
    return result


def parse_sweep(spec: str) -> tuple[str, list]:
    if "=" not in spec:
        raise SystemExit(f"expected name=start:stop:step or name=a,b,c, got {spec!r}")
    name, rhs = spec.split("=", 1)
    name = name.strip()
    rhs = rhs.strip()
    if ":" in rhs:
        parts = rhs.split(":")
        if len(parts) not in (2, 3):
            raise SystemExit(f"bad range {spec!r}")
        start, stop = parse_value(parts[0]), parse_value(parts[1])
        step = parse_value(parts[2]) if len(parts) == 3 else 1
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (start, stop, step)):
            raise SystemExit(f"bad range {spec!r}: start, stop and step must be finite numbers (name=start:stop[:step]); "
                             f"for other values list them: name=a,b,c")
        if step == 0:
            raise SystemExit(f"bad range {spec!r}: step must not be 0")
        if (stop - start) / step < 0:
            raise SystemExit(f"bad range {spec!r}: the step goes away from stop (use a negative step to count down)")
        # count the points instead of accumulating the step: 0.1 + 0.1 + 0.1 > 0.3 in floating point
        count = int(math.floor((stop - start) / step + 1e-9)) + 1
        if count > 10_000:
            raise SystemExit(f"range too large: {spec} ({count} points)")
        is_int = all(isinstance(x, int) for x in (start, stop, step))
        values = [start + i * step if is_int else round(start + i * step, 10) for i in range(count)]
        return name, values
    return name, [parse_value(p) for p in rhs.split(",") if p.strip()]


def parse_date_ms(text: str | None) -> int | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit(f"bad date {text!r}; use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (UTC)")


def load_data(data: Path, *, provider: str | None, symbol: str | None, tz: str, force: bool,
              from_ms: int | None, to_ms: int | None):
    from pynecore.core.data_converter import DataConverter
    from pynecore.core.ohlcv import OHLCVReader
    from pynecore.core.syminfo import SymInfo

    if not data.exists():
        raise SystemExit(f"data file not found: {data}")
    ohlcv = data
    toml_existed = data.with_suffix(".toml").is_file()
    if data.suffix.lower() != ".ohlcv":
        DataConverter().convert_to_ohlcv(data, force=force, provider=provider, symbol=symbol, timezone=tz)
        ohlcv = data.with_suffix(".ohlcv")
    toml = ohlcv.with_suffix(".toml")
    if not toml.is_file():
        raise SystemExit(f"symbol info not found: {toml}")
    syminfo = SymInfo.load_toml(toml)
    if (symbol or provider) and (toml_existed or data.suffix.lower() == ".ohlcv"):
        # PyneCore never overwrites an existing symbol .toml (it may hold hand-edited metadata), also not
        # with --force-convert: --symbol / --provider only shape a .toml that is generated
        differs = [f"{flag} {want!r} vs {have!r}" for flag, want, have in
                   (("--symbol", symbol, syminfo.ticker), ("--provider", provider, syminfo.prefix))
                   if want and str(want).upper() != str(have).upper()]
        if differs:
            print(f"warning: {toml} already exists and is used as it is ({'; '.join(differs)}): --symbol / --provider "
                  f"only apply when the .toml is generated. Rename or delete it to regenerate", file=sys.stderr)
    with OHLCVReader(ohlcv) as reader:
        start = reader.start_timestamp if from_ms is None else max(reader.start_timestamp, from_ms)
        end = reader.end_timestamp if to_ms is None else min(reader.end_timestamp, to_ms)
        bars = list(reader.read_from(start, end))
        period = reader.period
    if not bars:
        raise SystemExit("no bars in the selected range")
    return bars, syminfo, period, toml


def run_once(script: Path, bars, syminfo, *, inputs: dict, settings: dict,
             plot_path: Path | None = None, strat_path: Path | None = None, trade_path: Path | None = None,
             security: dict | None = None) -> dict:
    from pynecore.core.script_runner import ScriptRunner

    try:
        runner = ScriptRunner(
            script_path=script, ohlcv_iter=bars, syminfo=syminfo,
            inputs=inputs or None, settings=settings or None,
            plot_path=plot_path, strat_path=strat_path, trade_path=trade_path,
            security_data=security or None,
        )
    except ValueError as e:              # an --input / --setting name the script does not have
        raise SystemExit(f"error: {e}")
    trades: list = []
    last_plot: dict = {}
    n_bars = 0
    is_strategy = False
    first_values: dict = {}
    for item in runner.run_iter():
        n_bars += 1
        if len(item) == 3:
            is_strategy = True
            _candle, plot_data, new_trades = item
            trades.extend(new_trades)
        else:
            _candle, plot_data = item
        last_plot = dict(plot_data)
        for key, value in plot_data.items():
            if key not in first_values and isinstance(value, float) and not math.isnan(value):
                first_values[key] = n_bars - 1
    open_trades = []
    position = getattr(runner.script, "position", None)
    if position is not None and hasattr(position, "open_trades"):
        open_trades = list(position.open_trades)
    initial_capital = getattr(runner.script, "initial_capital", None)
    result = {
        "script": str(script), "bars": n_bars, "is_strategy": is_strategy,
        "inputs": inputs, "settings": settings,
        "plots": sorted(last_plot.keys()),
        "first_valid_bar": first_values,
        "last_values": {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in last_plot.items()},
    }
    if is_strategy:
        result.update(summarize_trades(trades, open_trades, initial_capital))
    return result


def summarize_trades(trades, open_trades, initial_capital) -> dict:
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit <= 0]
    gross_profit = sum(t.profit for t in wins)
    gross_loss = -sum(t.profit for t in losses)
    net = gross_profit - gross_loss
    peak = initial_capital or 0.0
    equity = initial_capital or 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for t in trades:
        equity += t.profit
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100 if peak else 0.0
    longs = [t for t in trades if t.size > 0]
    shorts = [t for t in trades if t.size < 0]
    bars_in_trade = [t.exit_bar_index - t.entry_bar_index for t in trades]
    streak = worst = 0
    for t in trades:
        streak = streak + 1 if t.profit <= 0 else 0
        worst = max(worst, streak)
    return {
        "initial_capital": initial_capital,
        "trades": len(trades), "open_trades": len(open_trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100) if trades else None,
        "net_profit": net,
        "net_profit_pct": (net / initial_capital * 100) if initial_capital else None,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else None),
        "avg_trade": (net / len(trades)) if trades else None,
        "avg_win": (gross_profit / len(wins)) if wins else None,
        "avg_loss": (-gross_loss / len(losses)) if losses else None,
        "largest_win": max((t.profit for t in trades), default=None),
        "largest_loss": min((t.profit for t in trades), default=None),
        "max_drawdown": max_dd, "max_drawdown_pct": max_dd_pct,
        "commission": sum(t.commission for t in trades),
        "avg_bars_in_trade": (sum(bars_in_trade) / len(bars_in_trade)) if bars_in_trade else None,
        "max_consecutive_losses": worst,
        "long": {"trades": len(longs), "net_profit": sum(t.profit for t in longs)},
        "short": {"trades": len(shorts), "net_profit": sum(t.profit for t in shorts)},
        "first_entry": datetime.fromtimestamp(trades[0].entry_time / 1000, tz=timezone.utc).isoformat() if trades else None,
        "last_exit": datetime.fromtimestamp(trades[-1].exit_time / 1000, tz=timezone.utc).isoformat() if trades else None,
    }


def fmt(v, digits=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf"
        return f"{v:,.{digits}f}"
    return str(v)


def print_single(res: dict, syminfo, period: str, toml: Path) -> None:
    print(f"Script : {res['script']}")
    print(f"Data   : {syminfo.prefix}:{syminfo.ticker}  period={period}  bars={res['bars']}  ({toml})")
    if res["inputs"]:
        print(f"Inputs : {res['inputs']}")
    if res["settings"]:
        print(f"Settings: {res['settings']}")
    if not res["is_strategy"]:
        print("Indicator run. Plot columns and first bar with a value:")
        for key in res["plots"]:
            print(f"  {key:<30} first valid bar: {res['first_valid_bar'].get(key, '-')}   last: {fmt(res['last_values'].get(key), 6)}")
        return
    print("-" * 70)
    print(f"Net profit       {fmt(res['net_profit']):>14}   ({fmt(res['net_profit_pct'])} % of {fmt(res['initial_capital'], 0)})")
    print(f"Max drawdown     {fmt(res['max_drawdown']):>14}   ({fmt(res['max_drawdown_pct'])} %, closed-trade equity curve)")
    print(f"Profit factor    {fmt(res['profit_factor']):>14}   gross +{fmt(res['gross_profit'])} / -{fmt(res['gross_loss'])}")
    print(f"Trades           {res['trades']:>14}   win rate {fmt(res['win_rate'])} %   W/L {res['wins']}/{res['losses']}   open at end: {res['open_trades']}")
    print(f"Avg trade        {fmt(res['avg_trade']):>14}   avg win {fmt(res['avg_win'])}   avg loss {fmt(res['avg_loss'])}")
    print(f"Largest win/loss {fmt(res['largest_win']):>14} / {fmt(res['largest_loss'])}   avg bars in trade {fmt(res['avg_bars_in_trade'], 1)}")
    print(f"Commission       {fmt(res['commission']):>14}   max consecutive losses {res['max_consecutive_losses']}")
    print(f"Long  : {res['long']['trades']} trades, net {fmt(res['long']['net_profit'])}     Short : {res['short']['trades']} trades, net {fmt(res['short']['net_profit'])}")
    print(f"Period : {res['first_entry']} -> {res['last_exit']}")
    warn = []
    if res["trades"] < 30:
        warn.append("fewer than 30 trades: not statistically meaningful")
    if res["commission"] == 0:
        warn.append("commission is 0: set commission_type/commission_value")
    if res["open_trades"]:
        warn.append(f"{res['open_trades']} position(s) still open at the end are not in these numbers")
    for w in warn:
        print(f"! {w}")
    if res["plots"]:
        print(f"Plots  : {', '.join(res['plots'])}")


METRICS = {
    "net_profit": (lambda r: r.get("net_profit"), True),
    "profit_factor": (lambda r: r.get("profit_factor"), True),
    "win_rate": (lambda r: r.get("win_rate"), True),
    "avg_trade": (lambda r: r.get("avg_trade"), True),
    "max_drawdown_pct": (lambda r: r.get("max_drawdown_pct"), False),
    "trades": (lambda r: r.get("trades"), True),
}


def metric_value(res: dict, name: str):
    getter, _ = METRICS[name]
    v = getter(res)
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return v


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("script", metavar="SCRIPT",
                        help="path to the Pyne script: a .py file whose first docstring starts with @pyne\n"
                             "and whose main() carries @script.strategy(...) or @script.indicator(...).\n"
                             "Example: scripts/my_strategy.py")
    parser.add_argument("data", metavar="DATA",
                        help="market data file, one of:\n"
                             "  NAME.ohlcv                 PyneCore binary candles; NAME.toml (symbol info) must sit next to it\n"
                             "  NAME.csv | .json | .txt    converted in place to NAME.ohlcv + NAME.toml before the run;\n"
                             "                             columns: timestamp|time|date, open, high, low, close, volume\n"
                             "Example: data/BTCUSDT_1h.csv")
    parser.add_argument("--input", "-i", action="append", metavar="NAME=VALUE",
                        help="override one input of main(). NAME = the parameter name in main(...) (not the title).\n"
                             "VALUE = a Python literal: 10 (int), 2.5 (float), true|false (bool), EMA or \"EMA\" (str),\n"
                             "na (missing). Repeat the option for several inputs.\n"
                             "Example: -i fast_len=10 -i slow_len=30 -i ma_type=EMA")
    parser.add_argument("--setting", "-s", action="append", metavar="NAME=VALUE",
                        help="override one @script.strategy()/@script.indicator() setting for this run only.\n"
                             "NAME = the decorator argument: initial_capital, default_qty_type, default_qty_value,\n"
                             "commission_type, commission_value, slippage, pyramiding, margin_long, margin_short,\n"
                             "process_orders_on_close, calc_on_order_fills, use_bar_magnifier, ...\n"
                             "VALUE = literal as for --input; constants as their string names:\n"
                             "  default_qty_type = percent_of_equity | fixed | cash\n"
                             "  commission_type  = percent | cash_per_order | cash_per_contract\n"
                             "Repeatable. Example: -s default_qty_value=10 -s commission_value=0.05")
    parser.add_argument("--from", dest="from_", metavar="DATE",
                        help="first bar to include (UTC), one of: YYYY-MM-DD | YYYY-MM-DDTHH:MM:SS |\n"
                             "\"YYYY-MM-DD HH:MM:SS\" (quote the form with a space). Default: first bar of the file.")
    parser.add_argument("--to", metavar="DATE",
                        help="last bar to include (UTC), same formats as --from. Default: last bar of the file.")
    parser.add_argument("--plot", type=Path, metavar="PATH",
                        help="write the per-bar plot values as CSV to PATH (columns: time, open, high, low, close,\n"
                             "volume, then one column per plot title). The folder must exist.")
    parser.add_argument("--strat", type=Path, metavar="PATH",
                        help="write the statistics CSV to PATH (same layout as pyne run's <script>_strat.csv)")
    parser.add_argument("--trade", type=Path, metavar="PATH",
                        help="write the trade list CSV to PATH (same layout as pyne run's <script>_trade.csv)")
    parser.add_argument("--security", action="append", metavar="KEY=BASE_PATH",
                        help="data for one request.security() context. KEY = TIMEFRAME (1D, 60, 1W) | SYMBOL\n"
                             "(SP:SPX) | SYMBOL:TIMEFRAME (SP:SPX:1D). BASE_PATH = data file path without extension;\n"
                             "BASE_PATH.ohlcv and BASE_PATH.toml must exist. Repeatable.\n"
                             "Example: --security 1D=data/EURUSD_1D")
    parser.add_argument("--symbol", metavar="SYMBOL",
                        help="symbol written into the generated .toml when DATA is a CSV/JSON/TXT, e.g. BTC/USDT,\n"
                             "EURUSD. Default: guessed from the file name (BTCUSDT.csv, BINANCE_ETHUSDT_1h.csv, EUR_USD.csv).")
    parser.add_argument("--provider", metavar="NAME",
                        help="provider name recorded in the generated .toml for a CSV/JSON/TXT, e.g. binance.\n"
                             "Default: guessed from the file name, else none.")
    parser.add_argument("--timezone", default="UTC", metavar="TZ",
                        help="IANA timezone of naive timestamps in a CSV/JSON/TXT, e.g. UTC, Europe/London,\n"
                             "America/New_York. Timestamps that carry an offset are used as is. Default: UTC.")
    parser.add_argument("--force-convert", action="store_true",
                        help="redo the CSV/JSON/TXT conversion even when NAME.ohlcv is newer than the source")
    parser.add_argument("--sweep", action="append", metavar="NAME=RANGE",
                        help="sweep one input over several values; NAME as for --input. RANGE is one of:\n"
                             "  START:STOP          integers START..STOP inclusive, step 1\n"
                             "  START:STOP:STEP     inclusive; all ints -> ints, any float -> floats; negative STEP counts down\n"
                             "  V1,V2,V3            explicit list of literals (as for --input)\n"
                             "Repeat for several inputs: every combination is run (cartesian product).\n"
                             "Example: --sweep fast_len=5:30:5 --sweep slow_len=20,40,60")
    parser.add_argument("--metric", default="net_profit", choices=sorted(METRICS), metavar="METRIC",
                        help="how sweep points are ranked (best first):\n"
                             "  net_profit | profit_factor | win_rate | avg_trade | trades   higher is better\n"
                             "  max_drawdown_pct                                         lower is better\n"
                             "Default: net_profit")
    parser.add_argument("--min-trades", type=int, default=0, metavar="N",
                        help="hide sweep points with fewer than N closed trades (integer >= 0). Default: 0")
    parser.add_argument("--top", type=int, default=15, metavar="N",
                        help="number of sweep rows to print (integer >= 1). Default: 15")
    parser.add_argument("--json", action="store_true",
                        help="print the result as JSON instead of text (single run: one object; sweep: a list)")
    args = parser.parse_args()

    script = Path(args.script)
    if not script.is_file():
        raise SystemExit(f"script not found: {script}")
    inputs = parse_kv(args.input)
    settings = parse_kv(args.setting)
    security = parse_kv(args.security) if args.security else None
    bars, syminfo, period, toml = load_data(
        Path(args.data), provider=args.provider, symbol=args.symbol, tz=args.timezone,
        force=args.force_convert, from_ms=parse_date_ms(args.from_), to_ms=parse_date_ms(args.to),
    )

    # With --json, stdout carries only the JSON: the script's log.* lines (PyneCore's console handler
    # writes to stdout), alert() and print() output go to stderr while the script runs.
    quiet = (lambda: contextlib.redirect_stdout(sys.stderr)) if args.json else contextlib.nullcontext

    for out in (args.plot, args.strat, args.trade):
        if out is not None:
            Path(out).expanduser().parent.mkdir(parents=True, exist_ok=True)

    if not args.sweep:
        with quiet():
            res = run_once(script, bars, syminfo, inputs=inputs, settings=settings,
                           plot_path=args.plot, strat_path=args.strat, trade_path=args.trade, security=security)
        if args.json:
            print(json.dumps(res, indent=2, default=str))
        else:
            print_single(res, syminfo, period, toml)
        return 0

    names, value_lists = zip(*(parse_sweep(s) for s in args.sweep))
    combos = list(itertools.product(*value_lists))
    print(f"Sweep: {len(combos)} runs over {', '.join(names)} on {len(bars)} bars", file=sys.stderr)
    rows = []
    for combo in combos:
        point = dict(zip(names, combo))
        with quiet():
            res = run_once(script, bars, syminfo, inputs={**inputs, **point}, settings=settings, security=security)
        res["point"] = point
        if not res["is_strategy"]:
            raise SystemExit(f"--sweep ranks trades, and {script.name} is an indicator (no trades): run it once per value "
                             f"with --input {', '.join(names)}=... instead, or sweep a strategy")
        rows.append(res)
    ranked = [r for r in rows if r.get("trades", 0) >= args.min_trades and metric_value(r, args.metric) is not None]
    _, higher_is_better = METRICS[args.metric]
    ranked.sort(key=lambda r: metric_value(r, args.metric), reverse=higher_is_better)
    if args.json:
        print(json.dumps([{**r["point"], **{k: r.get(k) for k in ("trades", "net_profit", "net_profit_pct", "profit_factor",
                                                                   "win_rate", "max_drawdown_pct", "avg_trade")}} for r in ranked],
                         indent=2, default=str))
        return 0
    header = "  ".join(f"{n:>10}" for n in names)
    print(f"{header}  {'trades':>6}  {'net':>12}  {'net %':>8}  {'PF':>6}  {'win %':>6}  {'maxDD %':>8}  {'avg':>10}")
    for r in ranked[: args.top]:
        vals = "  ".join(f"{str(r['point'][n]):>10}" for n in names)
        print(f"{vals}  {r['trades']:>6}  {fmt(r['net_profit']):>12}  {fmt(r['net_profit_pct']):>8}  {fmt(r['profit_factor']):>6}  "
              f"{fmt(r['win_rate'], 1):>6}  {fmt(r['max_drawdown_pct']):>8}  {fmt(r['avg_trade']):>10}")
    skipped = len(rows) - len(ranked)
    if skipped:
        reason = f"fewer than {args.min_trades} closed trades or no {args.metric}" if args.min_trades > 0 else f"no {args.metric} (no closed trade)"
        print(f"({skipped} point(s) hidden: {reason})")
    if not ranked:
        print("no sweep point produced a value to rank", file=sys.stderr)
        return 1
    print("Prefer a plateau of good neighbours over the single best cell; validate out of sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
