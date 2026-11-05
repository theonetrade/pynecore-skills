#!/usr/bin/env python3
r"""
Paper-trade a Pyne strategy on live exchange candles: warm-up from the local candle cache, then
the provider's live stream. Orders are simulated by PyneCore's strategy engine; nothing is sent
to any venue. The live-data counterpart of run_backtest.py: same overrides, same summary.

Run it with the Python interpreter that has `pynecore` (and `ccxt` for exchanges) installed:

    .venv/bin/python run_paper.py SCRIPT.py ccxt:BINANCE:BTC/USDT@1 --bars 30
    .venv/bin/python run_paper.py SCRIPT.py ccxt:BYBIT:BTC/USDT:USDT@5 --from -1000 --duration 4h \
        --input fast_len=10 --setting default_qty_value=5
    .venv/bin/python run_paper.py SCRIPT.py replay:TEST@1 --bars 100 --json      # deterministic fixture, no network

What happens:

1. The provider plugin named in DATA is loaded (`ccxt` is built in; `pyne plugin list` shows
   the rest) and the cache file `data/<provider>_<SYMBOL>_<TF>.ohlcv` is brought up to the last
   closed bar by appending (`continue`). The file is never truncated: unlike
   `pyne run SCRIPT ccxt:... --live`, which rewrites that file with just the run's range.
2. The last N closed bars of the file are the warm-up (--from -N, default 500): the script runs
   over them with `barstate.ishistory`, strategy orders suppressed, so the paper position starts
   flat.
3. The live stream starts (`LIVE_TRANSITION`): every bar close runs the strategy, fills are
   checked against the intra-bar ticks, one line per closed bar is printed, and
   `output/<script>_paper_strat.csv` (rewritten per bar) + `_paper_trade.csv` (appended) are
   written like `pyne run` does.
4. The run stops after --bars closed live bars, after --duration, or on Ctrl+C; the websocket is
   closed gracefully and the summary (trades, net profit, drawdown, open position) is printed.

Overrides: --input / --setting exactly as in run_backtest.py (parameter names of main() and
decorator argument names, Python literals). Nothing is written to the script's .toml.

Not covered here: `request.security()` contexts in live mode (use `pyne run ... --live`, which
resolves them through the plugin's symbol map) and real orders (run_live.py).
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from run_backtest import fmt, parse_kv, summarize_trades  # noqa: E402  (same folder of this skill)
except ImportError:  # pragma: no cover
    raise SystemExit("run_backtest.py must sit next to run_paper.py (both ship in the pynecore-strategy skill)")

WORKDIR_MARKER = ".pyne"
TF_RE = re.compile(r"^(\d+)([SDWM]?)$")
UNIT_MS = {"S": 1_000, "": 60_000, "D": 86_400_000, "W": 7 * 86_400_000}
DURATION_RE = re.compile(r"^(\d+)([smhd])$")


# ----------------------------------------------------------------------------- arguments

def find_workdir(start: Path) -> Path | None:
    env = os.environ.get("PYNE_WORK_DIR")
    if env:
        return Path(env).expanduser().resolve()
    current = start.resolve()
    for _ in range(10):
        if (current / WORKDIR_MARKER).is_file():
            return current
        child = current / "workdir"
        if child.is_dir():
            return child
        if current.parent == current:
            break
        current = current.parent
    return None


def period_ms(period: str | None) -> int | None:
    if not period:
        return None
    m = TF_RE.match(period.upper())
    if not m or m.group(2) == "M":
        return None
    return int(m.group(1)) * UNIT_MS[m.group(2)]


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def parse_from(text: str) -> tuple[str, int]:
    """'-500' -> ('bars', 500); '30' -> ('ms', now-30d); 'YYYY-MM-DD[ HH:MM:SS]' -> ('ms', ts)."""
    t = text.strip()
    if re.fullmatch(r"-\d+", t):
        n = int(t[1:])
        if n <= 0:
            raise SystemExit("--from -N needs N >= 1")
        return "bars", n
    if t.isdigit():
        return "ms", int((datetime.now(timezone.utc) - timedelta(days=int(t))).timestamp() * 1000)
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return "ms", int(datetime.strptime(t, f).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit(f"bad --from {text!r}; use -N (bars), N (days back), YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS'")


def parse_duration(text: str | None) -> float | None:
    if text is None:
        return None
    m = DURATION_RE.match(text.strip().lower())
    if not m:
        raise SystemExit(f"bad --duration {text!r}; use Ns, Nm, Nh or Nd (e.g. 90s, 30m, 4h, 1d)")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def closed_bars_cutoff(tf: str, now: datetime) -> datetime:
    pm = period_ms(tf)
    if pm is not None:
        return now - timedelta(milliseconds=pm)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start - timedelta(milliseconds=1)


def ts_text(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------- provider + cache

def load_provider(spec: str, workdir: Path):
    """Provider plugin instance bound to the symbol, plus the parsed string and the plugin name."""
    from pynecore.core.config import ensure_config
    from pynecore.core.plugin import PluginNotFoundError, load_plugin
    from pynecore.core.plugin.live_provider import LiveProviderPlugin
    from pynecore.core.provider_string import is_provider_string, parse_provider_string

    if not is_provider_string(spec):
        raise SystemExit(f"DATA must be a provider string like ccxt:BINANCE:BTC/USDT@1, got {spec!r} "
                         f"(a file cannot stream; backtest files with run_backtest.py)")
    name = spec.split(":", 1)[0].lower()
    try:
        cls = load_plugin(name)
    except PluginNotFoundError as e:
        raise SystemExit(str(e))
    if not (isinstance(cls, type) and issubclass(cls, LiveProviderPlugin)):
        raise SystemExit(f"plugin {name!r} cannot stream live candles (not a LiveProviderPlugin)")
    try:
        ps = parse_provider_string(spec, require_timeframe=True, multi_broker=bool(getattr(cls, "multi_broker", False)))
    except ValueError as e:
        raise SystemExit(f"bad provider string {spec!r}: {e}")
    if not ps.symbol:
        raise SystemExit(f"provider string {spec!r} names no symbol")
    config = None
    config_cls = getattr(cls, "Config", None)
    if config_cls is not None:
        path = workdir / "config" / "plugins" / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        config = ensure_config(config_cls, path)
    if name == "ccxt" and config is not None and ":" not in ps.symbol and getattr(config, "default_type", "") == "swap":
        # CCXT unified symbols without :SETTLE are spot markets. PyneCore's ccxt config defaults to
        # default_type = "swap" (used for the websocket client only); with it the exchange rejects a
        # spot symbol in the live stream ("does not have market symbol BTC/USDT") and the runner
        # reconnects forever. Set default_type = "spot" in config/plugins/ccxt.toml to make it permanent.
        config.default_type = "spot"
        print("note   : default_type set to spot for this run (symbol has no :SETTLE suffix)", file=sys.stderr)
    data_dir = workdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    provider = cls(symbol=ps.provider_symbol, timeframe=ps.timeframe, ohlcv_dir=data_dir, config=config)
    return provider, ps, name


def update_cache(provider, provider_string: str, tf: str, start: tuple[str, int], quiet: bool):
    """Append the closed bars missing from the cache file (download only the warm-up range when the
    file does not exist yet); returns the SymInfo."""
    from pynecore.core.download_runner import InvalidTimeRangeError, download_to_file
    from pynecore.core.ohlcv import record_count

    syminfo = provider.get_symbol_info()
    cutoff = closed_bars_cutoff(tf, datetime.now(timezone.utc).replace(second=0, microsecond=0))
    time_from: datetime | str = "continue"
    assert provider.ohlcv_path is not None
    if record_count(provider.ohlcv_path) == 0:
        kind, value = start
        if kind == "ms":
            time_from = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            pm = period_ms(tf)
            # +10 % and +10 bars so a few missing bars (maintenance, illiquid moments) still leave N
            time_from = cutoff - (timedelta(milliseconds=pm * (int(value * 1.1) + 10)) if pm else timedelta(days=31 * (value + 2)))
    try:
        result = download_to_file(provider, time_from=time_from, time_to=cutoff, syminfo=syminfo,
                                  on_conflict="abort", provider_string=provider_string)
        if not quiet:
            print(f"cache  : {result.ohlcv_path.name}  +{result.bars_written} closed bar(s)", file=sys.stderr)
    except InvalidTimeRangeError:
        pass  # the file already reaches the cutoff (a forming bar stored by pyne); nothing to append
    return syminfo


def warmup_bars(path: Path, tf: str, start: tuple[str, int]) -> list:
    from pynecore.core.ohlcv import OHLCVReader, record_count

    if record_count(path) == 0:
        return []
    kind, value = start
    pm = period_ms(tf)
    with OHLCVReader(path) as reader:
        end_ts = reader.end_timestamp
        if pm and end_ts + pm > now_ms():
            end_ts -= 1  # the last stored bar is still forming: it arrives through the live stream instead
        if kind == "bars":
            start_ts = reader.start_timestamp if pm is None else max(reader.start_timestamp, end_ts - pm * (value + 10))
            return list(reader.read_from(start_ts, end_ts))[-value:]
        return list(reader.read_from(max(reader.start_timestamp, value), end_ts))


# ----------------------------------------------------------------------------- run

class _OncePerMessage(logging.Filter):
    """Lets a repeated framework warning (e.g. queue backpressure on every bar) through once."""

    def __init__(self):
        super().__init__()
        self.seen: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        key = record.getMessage().split(":", 1)[0]
        if record.levelno >= logging.ERROR or key not in self.seen:
            self.seen.add(key)
            return True
        return False


def configure_logging(verbose: bool) -> None:
    """Route every PyneCore log line to stderr (its own Rich handler writes to stdout, which would
    corrupt --json) and keep only warnings unless --verbose."""
    level = logging.INFO if verbose else logging.WARNING
    try:
        from pynecore.lib.log import PineLogFormatter  # importing installs PyneCore's handler; replaced below
        formatter: logging.Formatter = PineLogFormatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="[%Y-%m-%d %H:%M:%S%z]")
    except Exception:  # noqa: BLE001
        formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    for name in ("pyne_core_logger", "pynecore"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.addHandler(handler)
        lg.propagate = False
        lg.setLevel(level)
        if not verbose:
            lg.addFilter(_OncePerMessage())


class StopTimer:
    """Raises KeyboardInterrupt in the main thread when the duration is over."""

    def __init__(self, seconds: float | None):
        self.fired = False
        self._timer = None
        if seconds:
            import _thread

            def fire():
                self.fired = True
                _thread.interrupt_main()
            self._timer = threading.Timer(seconds, fire)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()


def position_state(runner) -> dict:
    pos = getattr(runner.script, "position", None)
    if pos is None:
        return {}
    return {"size": getattr(pos, "size", None), "equity": getattr(pos, "equity", None),
            "net_profit": getattr(pos, "netprofit", None), "open_profit": getattr(pos, "openprofit", None),
            "open_trades": len(getattr(pos, "open_trades", []) or [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("script", metavar="SCRIPT",
                        help="path to the Pyne script (.py with a @pyne docstring and @script.strategy(...) main();\n"
                             "an @script.indicator(...) also runs and prints its plot values per live bar).\n"
                             "A bare name is looked up in <workdir>/scripts/. Example: scripts/my_strategy.py")
    parser.add_argument("data", metavar="DATA",
                        help="provider string PROVIDER[:EXCHANGE]:SYMBOL[:SETTLE]@TIMEFRAME, the same form pyne run uses:\n"
                             "  ccxt:BINANCE:BTC/USDT@1        Binance spot, 1-minute bars (public data, no key)\n"
                             "  ccxt:BYBIT:BTC/USDT:USDT@5     Bybit USDT perpetual, 5-minute bars\n"
                             "  replay:NAME@TF                 PyneCore's fixture replay plugin (config/plugins/replay.toml)\n"
                             "TIMEFRAME: minutes as a number (1, 5, 15, 60, 240) or 1D, 1W. A data file is not accepted.")
    parser.add_argument("--from", "-f", dest="from_", default="-500", metavar="START",
                        help="warm-up taken from the cache file before going live, one of:\n"
                             "  -N                       the last N closed bars (default -500; use >= 2x the longest lookback)\n"
                             "  N                        all cached bars of the last N days\n"
                             "  YYYY-MM-DD | \"YYYY-MM-DD HH:MM:SS\"   all cached bars from that UTC time\n"
                             "Fewer bars than asked means the cache is shorter: fill it first with download_candles.py.")
    parser.add_argument("--bars", type=int, default=0, metavar="N",
                        help="stop after N closed live bars (integer >= 1). Default 0: no bar limit")
    parser.add_argument("--duration", metavar="TIME",
                        help="stop after this much wall-clock time: Ns | Nm | Nh | Nd, e.g. 90s, 30m, 4h, 1d.\n"
                             "Default: run until --bars is reached or Ctrl+C")
    parser.add_argument("--input", "-i", action="append", metavar="NAME=VALUE",
                        help="override one input of main(): NAME = the parameter name, VALUE = a Python literal\n"
                             "(10, 2.5, true|false, EMA or \"EMA\", na). Repeatable. Example: -i fast_len=10")
    parser.add_argument("--setting", "-s", action="append", metavar="NAME=VALUE",
                        help="override one @script.strategy() setting for this run: initial_capital, default_qty_type\n"
                             "(percent_of_equity|fixed|cash), default_qty_value, commission_type (percent|cash_per_order|\n"
                             "cash_per_contract), commission_value, slippage, pyramiding, margin_long, margin_short,\n"
                             "calc_on_every_tick, ... Repeatable. Example: -s default_qty_value=5 -s commission_value=0.05")
    parser.add_argument("--strat", type=Path, metavar="PATH",
                        help="statistics CSV, rewritten after every closed bar. Default: <workdir>/output/<script>_paper_strat.csv")
    parser.add_argument("--trade", type=Path, metavar="PATH",
                        help="trade list CSV, appended on every fill. Default: <workdir>/output/<script>_paper_trade.csv")
    parser.add_argument("--plot", type=Path, metavar="PATH",
                        help="also write the per-bar plot CSV to PATH (grows for as long as the run lasts). Default: off")
    parser.add_argument("--shutdown-timeout", type=float, default=30.0, metavar="SEC",
                        help="seconds to wait for the provider's graceful disconnect when stopping (float; 0 = forever).\n"
                             "Default: 30")
    parser.add_argument("--workdir", type=Path, metavar="DIR",
                        help="PyneCore workdir (its data/, config/, output/ are used). Default: $PYNE_WORK_DIR, else the\n"
                             "first folder from the current directory upwards holding .pyne, else its workdir/ child")
    parser.add_argument("--quiet", "-q", action="store_true", help="no per-bar lines, only the summary")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="show PyneCore's own INFO log lines (connect, reconnect, shutdown) on stderr")
    parser.add_argument("--json", action="store_true",
                        help="print the summary as one JSON object (script, data, symbol, timeframe, history_bars,\n"
                             "live_bars, started, stopped, stop_reason, position, trades/net_profit/... as run_backtest.py,\n"
                             "outputs) instead of text; per-bar lines are suppressed")
    args = parser.parse_args()

    configure_logging(args.verbose)
    if args.bars < 0:
        parser.error("--bars must be >= 0")
    quiet = args.quiet or args.json
    duration = parse_duration(args.duration)
    start = parse_from(args.from_)
    inputs = parse_kv(args.input)
    settings = parse_kv(args.setting)

    workdir = args.workdir.resolve() if args.workdir else find_workdir(Path.cwd())
    if workdir is None:
        raise SystemExit("no PyneCore workdir found (no .pyne marker from the current directory upwards, no workdir/ child);\n"
                         "run from inside the project, pass --workdir DIR, or create one with init_project.py")
    script = Path(args.script)
    if not script.is_file():
        candidate = workdir / "scripts" / (args.script if args.script.endswith(".py") else args.script + ".py")
        if candidate.is_file():
            script = candidate
        else:
            raise SystemExit(f"script not found: {args.script}")
    script = script.resolve()
    output_dir = workdir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    strat_path = args.strat or output_dir / f"{script.stem}_paper_strat.csv"
    trade_path = args.trade or output_dir / f"{script.stem}_paper_trade.csv"

    try:
        import pynecore  # noqa: F401
    except ImportError:
        raise SystemExit("pynecore is not importable: run this script with the project's interpreter (.venv/bin/python)")
    from pynecore.core.live_runner import live_ohlcv_generator
    from pynecore.core.script_runner import ScriptRunner
    from pynecore.lib import barstate  # module flags the runner sets per bar: ishistory / isrealtime

    provider, ps, name = load_provider(args.data, workdir)
    provider_string = f"{name}:{ps.provider_symbol}@{ps.timeframe}"
    assert provider.ohlcv_path is not None
    try:
        syminfo = update_cache(provider, provider_string, ps.timeframe, start, quiet)
    except Exception as e:  # provider / network errors carry their own message
        raise SystemExit(f"cannot prepare the warm-up data: {type(e).__name__}: {e}")
    bars = warmup_bars(provider.ohlcv_path, ps.timeframe, start)
    if not bars:
        raise SystemExit(f"no closed bars in {provider.ohlcv_path}; download history first: "
                         f"download_candles.py {ps.symbol} -tf {ps.timeframe}" + (f" --exchange {ps.broker.lower()}" if ps.broker else ""))
    if start[0] == "bars" and len(bars) < start[1]:
        print(f"! only {len(bars)} cached bars for a warm-up of {start[1]}; extend the cache with download_candles.py",
              file=sys.stderr)
    last_hist_ts = bars[-1].timestamp
    if not quiet:
        print(f"warm-up: {len(bars)} bars  {ts_text(bars[0].timestamp)} -> {ts_text(last_hist_ts)} UTC   "
              f"({syminfo.prefix}:{syminfo.ticker} {ps.timeframe})", file=sys.stderr)
        print("live   : connecting ...", file=sys.stderr)

    live_iter = None
    run_gen = None
    started = datetime.now(timezone.utc)
    stop_reason = "completed"
    trades: list = []
    live_bars = 0
    history_bars = 0
    catchup_bars = 0   # bars that closed while connecting: processed as history, orders still suppressed
    last_plot: dict = {}
    timer = StopTimer(duration)
    try:
        live_iter = live_ohlcv_generator(provider=provider, symbol=ps.symbol, timeframe=ps.timeframe, syminfo=syminfo,
                                         last_historical_timestamp=last_hist_ts, shutdown_timeout=args.shutdown_timeout)
        runner = ScriptRunner(script, itertools.chain(bars, live_iter), syminfo,
                              last_bar_index=len(bars) - 1, last_bar_time=last_hist_ts, live=True,
                              inputs=inputs or None, settings=settings or None,
                              plot_path=args.plot, strat_path=strat_path, trade_path=trade_path,
                              chart_provider_name=name, chart_provider_instance=provider,
                              time_from=datetime.fromtimestamp(bars[0].timestamp / 1000, tz=timezone.utc),
                              config_dir=workdir / "config")
        runner.start_broker()  # no-op without a broker plugin: paper fills only
        run_gen = runner.run_iter()
        for item in run_gen:
            candle, plot_data = item[0], item[1]
            new_trades = list(item[2]) if len(item) == 3 else []
            trades.extend(new_trades)
            last_plot = dict(plot_data)
            if not barstate.isrealtime:
                history_bars += 1
                if candle.timestamp > last_hist_ts:
                    catchup_bars += 1
                continue
            live_bars += 1
            if not quiet:
                state = position_state(runner)
                line = f"{ts_text(candle.timestamp)}  close={candle.close:g}"
                if state:
                    line += f"  pos={fmt(state['size'], 6)}  equity={fmt(state['equity'])}  open={state['open_trades']}"
                else:
                    vals = ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in plot_data.items())
                    line += f"  {vals}"
                for t in new_trades:
                    line += f"\n    closed {t.entry_id} -> {t.exit_id}: size {fmt(t.size, 6)} profit {fmt(t.profit)}"
                print(line, flush=True)
            if args.bars and live_bars >= args.bars:
                stop_reason = f"{args.bars} live bars"
                break
    except KeyboardInterrupt:
        stop_reason = "duration reached" if timer.fired else "interrupted (Ctrl+C)"
    except Exception as e:
        stop_reason = f"error: {type(e).__name__}: {e}"
    finally:
        timer.cancel()
        if run_gen is not None:
            try:
                run_gen.close()    # lets the runner flush and close its CSV writers
            except (RuntimeError, Exception):  # noqa: BLE001  (teardown must not hide the run result)
                pass
        if live_iter is not None:
            try:
                live_iter.close()  # stops the stream thread and disconnects the provider
            except RuntimeError:
                pass
    stopped = datetime.now(timezone.utc)

    runner_obj = locals().get("runner")
    state = position_state(runner_obj) if runner_obj is not None else {}
    open_trades = list(getattr(getattr(runner_obj.script, "position", None), "open_trades", []) or []) if runner_obj else []
    initial_capital = getattr(runner_obj.script, "initial_capital", None) if runner_obj else None
    is_strategy = bool(state)
    summary = {
        "script": str(script), "data": provider_string, "symbol": f"{syminfo.prefix}:{syminfo.ticker}",
        "timeframe": ps.timeframe, "mode": "paper", "history_bars": history_bars, "catchup_bars": catchup_bars,
        "live_bars": live_bars,
        "started": started.isoformat(timespec="seconds"), "stopped": stopped.isoformat(timespec="seconds"),
        "stop_reason": stop_reason, "inputs": inputs, "settings": settings, "position": state,
        "last_values": {k: (None if isinstance(v, float) and v != v else v) for k, v in last_plot.items()},
        "outputs": {"strat": str(strat_path), "trade": str(trade_path), "plot": str(args.plot) if args.plot else None},
    }
    if is_strategy:
        summary.update(summarize_trades(trades, open_trades, initial_capital))
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print("-" * 70)
        print(f"Paper run : {script.name} on {provider_string}   {history_bars} history bars"
              + (f" ({catchup_bars} of them closed while connecting: orders suppressed)" if catchup_bars else "")
              + f", {live_bars} live bars")
        print(f"Period    : {started:%Y-%m-%d %H:%M:%S} -> {stopped:%H:%M:%S} UTC   stopped: {stop_reason}")
        if is_strategy:
            print(f"Position  : size {fmt(state['size'], 6)}   equity {fmt(state['equity'])}   open profit {fmt(state['open_profit'])}   "
                  f"open trades {state['open_trades']}")
            print(f"Closed    : {summary['trades']} trade(s)   net {fmt(summary['net_profit'])}   PF {fmt(summary['profit_factor'])}   "
                  f"win rate {fmt(summary['win_rate'])} %   max DD {fmt(summary['max_drawdown_pct'])} %")
            if summary["trades"] == 0:
                print("  (no paper trade closed in this run; the simulated position above is what the strategy holds now)")
        elif last_plot:
            print("Last plot : " + ", ".join(f"{k}={fmt(v, 6)}" for k, v in last_plot.items()))
        print(f"Files     : {strat_path}  {trade_path}" + (f"  {args.plot}" if args.plot else ""))
        print("Simulated fills only. Real orders: run_live.py (broker plugin required).")
    return 0 if not stop_reason.startswith("error") else 1


if __name__ == "__main__":
    sys.exit(main())
