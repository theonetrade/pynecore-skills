#!/usr/bin/env python3
r"""
Download exchange candles through CCXT into the PyneCore workdir and keep them current, so
backtests run offline from `data/<name>.ohlcv` instead of downloading again on every run.

Run it with the Python interpreter that has `pynecore` and `ccxt` installed (the project's venv
with `pynesys-pynecore[cli,ccxt]`):

    .venv/bin/python download_candles.py BTC/USDT                        # Binance spot, daily -> data/ccxt_BINANCE_BTC_USDT_1D.ohlcv
    .venv/bin/python download_candles.py BTC/USDT ETH/USDT -tf 60 --from 2024-01-01
    .venv/bin/python download_candles.py BTC/USDT -tf 60                 # later: appends only the bars after the last stored one
    .venv/bin/python download_candles.py BTC/USDT -tf 60 --status        # no network: what the cache holds, how far behind it is
    .venv/bin/python download_candles.py BTC/USDT -tf 60 --truncate --from 2022-01-01   # start over from an earlier date
    .venv/bin/python download_candles.py BTC/USDT:USDT -tf 15 --exchange bybit          # USDT perpetual on another exchange
    .venv/bin/python download_candles.py --list-symbols USDT             # the exchange's symbols containing USDT

The files are exactly what `pyne data download ccxt:BINANCE:BTC/USDT@60` writes:
`data/ccxt_<EXCHANGE>_<SYMBOL>_<TF>.ohlcv` plus `.toml` (symbol info and a `[download]` section
with the provider string). `pyne run my_strategy ccxt_BINANCE_BTC_USDT_60`, `run_backtest.py`
and `pyne data download data/ccxt_BINANCE_BTC_USDT_60.ohlcv` all work on them.

Caching rules (PyneCore's own): the file only grows at the end. Without --from (= continue) the
download resumes at the last stored bar, so a scheduled update costs one request. A --from
earlier than the file's first bar is refused unless --truncate is given, because bars cannot be
inserted in front. By default only closed bars are stored: the bar still forming at run time is
left out, because a bar once stored is never rewritten by a later continue (`--to now` stores
it anyway). `pyne run SCRIPT ccxt:...` (a provider string instead of a file) is NOT a cache: it
downloads the bars again on every run; pass the file name instead.

Exit code 0 when every symbol succeeded, 1 when any failed (the others are still processed),
2 for a usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROVIDER = "ccxt"
DEFAULT_EXCHANGE = "binance"
DEFAULT_TIMEFRAME = "1D"
WORKDIR_MARKER = ".pyne"
TF_RE = re.compile(r"^(\d+)([SDWM]?)$")
UNIT_MS = {"S": 1_000, "": 60_000, "D": 86_400_000, "W": 7 * 86_400_000}


# ----------------------------------------------------------------------------- workdir / arguments

def find_workdir(start: Path) -> Path | None:
    """Same rule as the pyne CLI: $PYNE_WORK_DIR, else the first folder upwards (10 levels) that
    holds the .pyne marker, else its workdir/ child."""
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


def normalize_timeframe(text: str) -> str:
    tf = text.strip().upper()
    m = TF_RE.match(tf)
    if not m or int(m.group(1)) <= 0:
        raise SystemExit(f"bad timeframe {text!r}; use minutes (1, 5, 15, 60, 240) or 1S, 1D, 1W, 1M")
    return tf


def period_ms(period: str | None) -> int | None:
    """Fixed bar length in milliseconds; None for months (calendar length) or unknown."""
    if not period:
        return None
    m = TF_RE.match(period.upper())
    if not m or m.group(2) == "M":
        return None
    return int(m.group(1)) * UNIT_MS[m.group(2)]


def parse_datetime(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(text)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def parse_from(text: str) -> datetime | str:
    t = text.strip()
    if t.lower() == "continue":
        return "continue"
    if t.isdigit():
        return now_utc() - timedelta(days=int(t))
    try:
        return parse_datetime(t)
    except ValueError:
        raise SystemExit(f"bad --from {text!r}; use continue, YYYY-MM-DD, 'YYYY-MM-DD HH:MM:SS' or a number of days back")


def closed_bars_cutoff(tf: str, now: datetime) -> datetime:
    """Latest bar-open time whose bar has already closed. The downloader stores bars that open at
    or before this instant, so the bar still forming is left out."""
    pm = period_ms(tf)
    if pm is not None:
        return now - timedelta(milliseconds=pm)
    # months: exchanges open the monthly bar on the 1st at 00:00 UTC
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start - timedelta(milliseconds=1)


def parse_to(text: str | None, tf: str) -> datetime:
    now = now_utc()
    if text is None or text.strip().lower() == "closed":
        return closed_bars_cutoff(tf, now)
    t = text.strip()
    if t.lower() == "now":
        return now
    if t.isdigit():
        return now - timedelta(days=int(t))
    try:
        return parse_datetime(t)
    except ValueError:
        raise SystemExit(f"bad --to {text!r}; use closed, now, YYYY-MM-DD, 'YYYY-MM-DD HH:MM:SS' or a number of days back")


def iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def provider_string(exchange: str, symbol: str, tf: str) -> str:
    return f"{PROVIDER}:{exchange.upper()}:{symbol}@{tf}"


# ----------------------------------------------------------------------------- pynecore glue

def load_config(workdir: Path | None):
    """config/plugins/ccxt.toml of the workdir (created with commented defaults when missing,
    exactly as the pyne CLI does); plain defaults when there is no config folder."""
    from pynecore.providers.ccxt import CCXTConfig

    if workdir is None or not (workdir / "config").is_dir():
        return CCXTConfig()
    from pynecore.core.config import ensure_config

    path = workdir / "config" / "plugins" / f"{PROVIDER}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return ensure_config(CCXTConfig, path)


def exchange_table_warning(workdir: Path | None, data_dir: Path) -> str | None:
    """PyneCore's CCXT provider reads the exchange tables ([okx], [bybit]: keys, sandbox, hosts, options,
    proxies) from <data folder>/../config/plugins/ccxt.toml, whatever workdir the caller uses. A data folder
    outside the workdir therefore downloads without the workdir's tables; say so before it happens."""
    if workdir is None:
        return None
    ours = workdir / "config" / "plugins" / "ccxt.toml"
    used = data_dir.parent / "config" / "plugins" / "ccxt.toml"
    if not ours.is_file() or (used.is_file() and used.resolve() == ours.resolve()):
        return None
    import tomllib

    def tables(path: Path) -> set[str]:
        try:
            return {k for k, v in tomllib.loads(path.read_text(encoding="utf-8")).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            return set()
    missing = sorted(tables(ours) - (tables(used) if used.is_file() else set()))
    if not missing:
        return None                                  # the file PyneCore reads has every table the workdir has
    where = f"{used} has no " if used.is_file() else f"{used} does not exist, so no "
    return (f"warning: --data-dir {data_dir} is outside the workdir: PyneCore reads the exchange tables from "
            f"{used}, not from {ours}; {where}{', '.join('[' + n + ']' for n in missing)} (keys, sandbox, hosts, "
            f"options) for this run. Use a data folder inside the workdir (the default <workdir>/data) or put "
            f"the tables into {used}")


def candle_path(exchange: str, symbol: str, tf: str, data_dir: Path) -> Path:
    from pynecore.providers.ccxt import CCXTProvider

    return CCXTProvider.get_ohlcv_path(f"{exchange}:{symbol}", tf, data_dir)


def file_status(path: Path, now_ms: int) -> dict:
    from pynecore.core.download_info import read_download_provider
    from pynecore.core.ohlcv import OHLCVReader, record_count

    toml = path.with_suffix(".toml")
    info: dict = {
        "file": str(path), "name": path.stem, "exists": path.is_file(), "toml": toml.is_file(),
        "bars": 0, "period": None, "first": None, "last": None, "dense": None,
        "bytes": path.stat().st_size if path.is_file() else 0,
        "provider_string": read_download_provider(toml) if toml.is_file() else None,
        "behind_bars": None, "last_bar_open": False,
    }
    if not info["exists"] or record_count(path) == 0:
        return info
    with OHLCVReader(path) as reader:
        info.update(bars=reader.size, period=reader.period, first=iso(reader.start_timestamp),
                    last=iso(reader.end_timestamp), dense=reader.dense)
        pm = period_ms(reader.period)
        if pm and reader.end_timestamp:
            # closed bars that are missing after the last stored one; the bar still forming does not count
            info["behind_bars"] = max(0, (now_ms - reader.end_timestamp) // pm - 1)
            info["last_bar_open"] = reader.end_timestamp + pm > now_ms
    return info


class Progress:
    def __init__(self, label: str, quiet: bool):
        self.label = label
        self.quiet = quiet
        self.tty = sys.stderr.isatty()
        self.last_pct = -1

    def start(self, plan) -> None:
        if self.quiet:
            return
        if plan.fetch_all:
            print(f"  {self.label}: downloading everything the exchange has ...", file=sys.stderr, flush=True)
        else:
            print(f"  {self.label}: downloading {plan.time_from:%Y-%m-%d %H:%M} -> {plan.time_to:%Y-%m-%d %H:%M} UTC",
                  file=sys.stderr, flush=True)

    def __call__(self, p) -> None:
        if self.quiet or not p.total_seconds:
            return
        pct = min(100, int(p.elapsed_seconds * 100 / p.total_seconds))
        if self.tty:
            print(f"\r  {self.label}: {pct:3d}%  {p.current:%Y-%m-%d %H:%M}", end="", file=sys.stderr, flush=True)
        elif pct // 10 != self.last_pct // 10:
            print(f"  {self.label}: {pct}%  {p.current:%Y-%m-%d %H:%M}", file=sys.stderr, flush=True)
        self.last_pct = pct

    def finish(self) -> None:
        if self.tty and not self.quiet and self.last_pct >= 0:
            print("\r" + " " * 70 + "\r", end="", file=sys.stderr, flush=True)


def download_one(symbol: str, *, exchange: str, tf: str, data_dir: Path, config, time_from, time_to: datetime,
                 truncate: bool, chunk_size: int | None, refresh_info: bool, quiet: bool) -> dict:
    from pynecore.core.download_runner import DownloadConflictError, download_to_file
    from pynecore.core.ohlcv import OHLCVReader, record_count
    from pynecore.providers.ccxt import CCXTProvider

    provider = CCXTProvider(symbol=f"{exchange}:{symbol}", timeframe=tf, ohlcv_dir=data_dir, config=config)
    path = provider.ohlcv_path
    assert path is not None
    before = record_count(path)
    # symbol info first (from the existing .toml, or fetched): an unknown or misspelled symbol fails
    # here, before any bar is stored, and the .toml pyne needs is guaranteed to exist
    syminfo = provider.get_symbol_info(force_update=refresh_info)
    pstring = provider_string(exchange, symbol, tf)
    if time_from == "continue" and before and not truncate:
        with OHLCVReader(path) as reader:
            last_ts = reader.end_timestamp
        if last_ts is not None and last_ts >= int(time_to.timestamp() * 1000):
            # the file already holds a bar at or after the requested end: the bar still forming, stored by
            # `pyne data download` (which ends at "now") or by --to now. Nothing can be appended yet.
            status = file_status(path, now_ms())
            status.update(symbol=symbol, exchange=exchange, timeframe=tf, ok=status["toml"], bars_before=before,
                          bars_added=0, downloaded_from=iso(last_ts), downloaded_to=f"{time_to:%Y-%m-%d %H:%M:%S}",
                          skipped="the file already reaches the requested end; nothing to append yet")
            return status
    progress = Progress(f"{symbol} {tf}", quiet)
    try:
        result = download_to_file(
            provider, time_from=time_from, time_to=time_to, truncate=truncate, chunk_size=chunk_size,
            syminfo=syminfo, on_start=progress.start, on_progress=progress, on_conflict="abort",
            provider_string=pstring,
        )
    except DownloadConflictError as e:
        progress.finish()
        c = e.conflict
        return {"symbol": symbol, "exchange": exchange, "timeframe": tf, "ok": False, "file": str(path),
                "error": f"the file starts at {c.existing_start:%Y-%m-%d %H:%M} UTC and bars cannot be inserted before it; "
                         f"re-run with --truncate to download again from {c.time_from:%Y-%m-%d %H:%M}, "
                         f"or drop --from to continue at the last stored bar"}
    progress.finish()
    status = file_status(path, now_ms())
    status.update(symbol=symbol, exchange=exchange, timeframe=tf, ok=status["toml"], bars_before=before,
                  bars_added=result.bars_written, downloaded_from=f"{result.time_from:%Y-%m-%d %H:%M:%S}",
                  downloaded_to=f"{result.time_to:%Y-%m-%d %H:%M:%S}")
    if result.syminfo is not None:
        status["syminfo"] = {"prefix": result.syminfo.prefix, "ticker": result.syminfo.ticker,
                             "mintick": result.syminfo.mintick, "mincontract": result.syminfo.mincontract,
                             "currency": result.syminfo.currency}
    return status


def list_symbols(exchange: str, tf: str, data_dir: Path, config, filters: list[str]) -> int:
    from pynecore.providers.ccxt import CCXTProvider

    # a placeholder symbol with the data folder, so the exchange table of config/plugins/ccxt.toml
    # (custom hosts, keys) applies to the listing too; no file is created before a download
    provider = CCXTProvider(symbol=f"{exchange}:LIST", timeframe=tf, ohlcv_dir=data_dir, config=config)
    symbols = provider.get_list_of_symbols()
    if filters:
        wanted = [f.lower() for f in filters]
        symbols = [s for s in symbols if any(w in s.lower() for w in wanted)]
    for s in sorted(symbols):
        print(s)
    print(f"{len(symbols)} symbol(s) on {exchange}", file=sys.stderr)
    return 0


# ----------------------------------------------------------------------------- output

def fmt_int(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def print_status(st: dict, data_dir: Path) -> None:
    rel = Path(st["file"]).name
    print(f"{st['symbol']} {st['timeframe']} on {st['exchange']}")
    if not st["exists"] or not st["bars"]:
        print(f"  file   : {data_dir / rel}   (not downloaded yet)")
        return
    print(f"  file   : {data_dir / rel}   {fmt_int(st['bytes'])} bytes   .toml: {'yes' if st['toml'] else 'MISSING'}"
          + (f"   [download] {st['provider_string']}" if st["provider_string"] else "   [download] section missing"))
    dense = {True: "yes", False: "no (gaps)", None: "unknown"}[st["dense"]]
    print(f"  bars   : {fmt_int(st['bars'])}   period {st['period']}   {st['first']} -> {st['last']} UTC   gap-free: {dense}")
    notes = []
    if st["behind_bars"]:
        notes.append(f"{fmt_int(st['behind_bars'])} closed bar(s) missing after the last stored one: run again without --from to append them")
    if st["last_bar_open"]:
        notes.append("the last stored bar is still forming; its values will not be updated (re-download with --truncate --to closed when it matters)")
    if not st["toml"]:
        notes.append("no .toml next to the file: pyne cannot run on it; re-run the download (symbol info is fetched first)")
    for n in notes:
        print(f"  ! {n}")
    print(f"  next   : pyne run <script> {st['name']}")


def print_result(st: dict, data_dir: Path) -> None:
    if not st["ok"]:
        print(f"{st['symbol']} {st['timeframe']} on {st['exchange']}: FAILED")
        print(f"  ! {st['error']}")
        return
    print_status(st, data_dir)
    added = st["bars_added"]
    print(f"  added  : {fmt_int(added)} new bar(s) ({fmt_int(st['bars_before'])} before)   "
          f"window {st['downloaded_from']} -> {st['downloaded_to']} UTC"
          + ("   (already up to date)" if added == 0 and st["bars_before"] else ""))
    if st.get("skipped"):
        print(f"  note   : {st['skipped']}")


# ----------------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("symbols", nargs="*", metavar="SYMBOL",
                        help="one or more instruments in CCXT's unified form BASE/QUOTE for spot (BTC/USDT, ETH/BTC) or\n"
                             "BASE/QUOTE:SETTLE for a perpetual swap (BTC/USDT:USDT). Case-sensitive, exactly as the\n"
                             "exchange lists it (--list-symbols shows the valid names). Exchange tickers without a slash\n"
                             "(BTCUSDT) are not accepted. Each symbol becomes one file: data/ccxt_<EXCHANGE>_<BASE>_<QUOTE>_<TF>.ohlcv.\n"
                             "With --list-symbols the SYMBOL arguments are case-insensitive substrings that filter the list.")
    parser.add_argument("--exchange", "-x", default=DEFAULT_EXCHANGE, metavar="ID",
                        help="CCXT exchange id, lowercase: binance (default), bybit, okx, kucoin, kraken, coinbase, gate,\n"
                             "htx, bitget, mexc, ... (`pyne data download ccxt --list-brokers` prints all). Public candles\n"
                             "need no API key; keys, sandbox mode and per-exchange options live in config/plugins/ccxt.toml.")
    parser.add_argument("--timeframe", "-tf", default=DEFAULT_TIMEFRAME, metavar="TF",
                        help="bar size in PyneCore notation: a number of minutes (1, 5, 15, 60, 240) or 1S (seconds),\n"
                             "1D, 1W, 1M (days, weeks, months; multiplier allowed: 3D). Only sizes the exchange serves.\n"
                             f"Default: {DEFAULT_TIMEFRAME}")
    parser.add_argument("--from", "-f", dest="from_", default="continue", metavar="START",
                        help="first bar to download (UTC), one of:\n"
                             "  continue                 resume at the last stored bar; with no file yet: one year back (default)\n"
                             "  YYYY-MM-DD               a date, e.g. 2024-01-01\n"
                             "  \"YYYY-MM-DD HH:MM:SS\"    a date and time (quote it)\n"
                             "  NUMBER                   that many days back from now, e.g. 90\n"
                             "A START before the first stored bar is an error unless --truncate is given.")
    parser.add_argument("--to", "-t", default=None, metavar="END",
                        help="last bar to download (UTC), one of:\n"
                             "  closed                   the last bar that has already closed (default): no half-formed bar is stored\n"
                             "  now                      include the bar still forming (it keeps its values at download time forever)\n"
                             "  YYYY-MM-DD | \"YYYY-MM-DD HH:MM:SS\" | NUMBER   a date, a date and time, or days back from now")
    parser.add_argument("--truncate", action="store_true",
                        help="delete the stored bars first and download the whole range again (the .toml is kept).\n"
                             "Needed to extend a file backwards or to replace a bar that was stored while still forming.")
    parser.add_argument("--chunk-size", type=int, metavar="N",
                        help="bars per exchange request (integer > 0). Default: the exchange's own limit known to PyneCore\n"
                             "(binance 1000, bybit 200, kraken 720, kucoin 1500, coinbase 300; others 100).")
    parser.add_argument("--refresh-info", action="store_true",
                        help="fetch the symbol info again and rewrite data/<name>.toml (tick size, lot step, fees).\n"
                             "Default: the existing .toml is kept; it is created on the first download.")
    parser.add_argument("--status", action="store_true",
                        help="do not download; print what the cache holds for each SYMBOL (file, bars, first/last bar,\n"
                             "gap-free flag, closed bars missing since the last stored one). Needs no network.")
    parser.add_argument("--list-symbols", action="store_true",
                        help="print the exchange's symbols (one per line, sorted) and exit; SYMBOL arguments filter them.\n"
                             "Example: --list-symbols BTC/ USDT")
    parser.add_argument("--workdir", type=Path, metavar="DIR",
                        help="PyneCore workdir (folder with the .pyne marker; its data/ and config/ are used). Default:\n"
                             "$PYNE_WORK_DIR, else the first folder from the current directory upwards holding .pyne,\n"
                             "else its workdir/ child (the pyne CLI rule).")
    parser.add_argument("--data-dir", type=Path, metavar="DIR",
                        help="folder for the .ohlcv/.toml files; created if missing. Default: <workdir>/data. PyneCore reads\n"
                             "the exchange tables of ccxt.toml from DIR/../config/plugins/, so a folder outside the workdir\n"
                             "runs without the workdir's [exchange] tables (a warning says so)")
    parser.add_argument("--quiet", "-q", action="store_true", help="no progress output on stderr")
    parser.add_argument("--json", action="store_true",
                        help="print the results as a JSON list (one object per SYMBOL, same keys as the text output:\n"
                             "symbol, exchange, timeframe, ok, file, name, bars, bars_added, first, last, period, dense,\n"
                             "behind_bars, last_bar_open, provider_string, error)")
    args = parser.parse_args()

    if not args.symbols and not args.list_symbols:
        parser.error("give at least one SYMBOL (e.g. BTC/USDT), or --list-symbols")
    exchange = args.exchange.strip().lower()
    tf = normalize_timeframe(args.timeframe)
    if args.chunk_size is not None and args.chunk_size <= 0:
        parser.error("--chunk-size must be a positive integer")

    workdir = args.workdir.resolve() if args.workdir else find_workdir(Path.cwd())
    if args.data_dir:
        data_dir = args.data_dir.resolve()
    elif workdir is not None:
        data_dir = workdir / "data"
    else:
        raise SystemExit("no PyneCore workdir found (no .pyne marker from the current directory upwards and no workdir/ child);\n"
                         "run from inside the project, pass --workdir DIR or --data-dir DIR, or create a project with init_project.py")

    try:
        import pynecore  # noqa: F401
    except ImportError:
        raise SystemExit("pynecore is not importable: run this script with the project's interpreter "
                         "(.venv/bin/python) that has pynesys-pynecore[cli,ccxt] installed")

    if args.status:
        now = now_ms()
        results = []
        for symbol in args.symbols:
            st = file_status(candle_path(exchange, symbol, tf, data_dir), now)
            st.update(symbol=symbol, exchange=exchange, timeframe=tf, ok=st["exists"] and bool(st["bars"]) and st["toml"])
            results.append(st)
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            for st in results:
                print_status(st, data_dir)
        return 0 if all(r["ok"] for r in results) else 1

    warning = exchange_table_warning(workdir, data_dir)
    if warning:
        print(warning, file=sys.stderr)

    try:
        config = load_config(workdir)
        if args.list_symbols:
            return list_symbols(exchange, tf, data_dir, config, args.symbols)
    except ImportError as e:
        raise SystemExit(f"{e}\ninstall the exchange support: pip install \"pynesys-pynecore[ccxt]\" (or uv add ...)")
    except Exception as e:  # provider/network errors carry their own message
        raise SystemExit(f"{type(e).__name__}: {e}")

    data_dir.mkdir(parents=True, exist_ok=True)
    time_from = parse_from(args.from_)
    time_to = parse_to(args.to, tf)
    results = []
    for symbol in args.symbols:
        try:
            st = download_one(symbol, exchange=exchange, tf=tf, data_dir=data_dir, config=config,
                              time_from=time_from, time_to=time_to, truncate=args.truncate,
                              chunk_size=args.chunk_size, refresh_info=args.refresh_info, quiet=args.quiet)
        except ImportError as e:
            raise SystemExit(f"{e}\ninstall the exchange support: pip install \"pynesys-pynecore[ccxt]\" (or uv add ...)")
        except KeyboardInterrupt:
            raise
        except Exception as e:  # one failing symbol must not stop the others
            msg = f"{type(e).__name__}: {e}"
            if "Unknown symbol" in msg:
                msg += "  (use the exchange's unified name, e.g. BTC/USDT or BTC/USDT:USDT; see --list-symbols)"
            st = {"symbol": symbol, "exchange": exchange, "timeframe": tf, "ok": False,
                  "file": str(candle_path(exchange, symbol, tf, data_dir)), "error": msg}
        results.append(st)
        if not args.json:
            print_result(st, data_dir)
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    failed = [r["symbol"] for r in results if not r["ok"]]
    if failed and not args.json:
        print(f"{len(failed)} of {len(results)} symbol(s) failed: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
