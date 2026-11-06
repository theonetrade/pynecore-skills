#!/usr/bin/env python3
r"""
Check an installed PyneCore plugin the way the `pyne` CLI and the live runner will use it.

Run it with the interpreter of the environment where the plugin and pynecore are installed:

    .venv/bin/python plugin_check.py file                                  # load, capabilities, config, contract
    .venv/bin/python plugin_check.py file --symbol BTCUSDT --timeframe 60 --bars 50     # + symbol info and a download
    .venv/bin/python plugin_check.py ccxtrest --symbol BINANCE:BTC/USDT --timeframe 1 --bars 20 --live-seconds 90
    .venv/bin/python plugin_check.py telegram --json

Static checks (always): the entry point resolves to one class (name conflicts are reported),
the capabilities (provider / live provider / broker / cli), remaining abstract methods, the
class attributes the framework reads (multi_broker, fetch_all_by_default, feed_timeout_bars,
reconnect delays), the Config dataclass fields and the config file path in the workdir.

Probe (with --symbol): the provider is built against a temporary workdir whose data/ is empty
and whose config/ links to the project's config/ (nothing is written into the project; plugins
that read config relative to the data folder, like the ccxt family, still find it).
update_symbol_info() is checked for the fields a run needs, download_ohlcv() fetches the last
--bars bars and the resulting file is checked (count, ascending, period, closed). With
--live-seconds the live stream is started through PyneCore's own live_ohlcv_generator for that
long and the updates are counted. $PYNE_WORK_DIR is set to the workdir for the probe so plugins
that resolve their config through it (notifiers, the file provider's data folder) agree.

Exit code 0 when every check passed, 1 otherwise, 2 for a usage error.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKDIR_MARKER = ".pyne"


def find_workdir(start: Path) -> Path | None:
    env = os.environ.get("PYNE_WORK_DIR")
    if env:
        return Path(env).expanduser().resolve()
    cur = start.resolve()
    for _ in range(10):
        if (cur / WORKDIR_MARKER).is_file():
            return cur
        if (cur / "workdir").is_dir():
            return cur / "workdir"
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


class Report:
    def __init__(self):
        self.items: list[dict] = []
        self.failed = 0

    def ok(self, what: str, detail: str = "") -> None:
        self.items.append({"status": "ok", "check": what, "detail": detail})

    def warn(self, what: str, detail: str = "") -> None:
        self.items.append({"status": "warn", "check": what, "detail": detail})

    def fail(self, what: str, detail: str = "") -> None:
        self.failed += 1
        self.items.append({"status": "FAIL", "check": what, "detail": detail})

    def print(self) -> None:
        for it in self.items:
            mark = {"ok": " ok ", "warn": "warn", "FAIL": "FAIL"}[it["status"]]
            print(f"[{mark}] {it['check']}" + (f": {it['detail']}" if it["detail"] else ""))


def static_checks(name: str, rep: Report, workdir: Path | None) -> type | None:
    from pynecore.core.plugin import (CLIPlugin, LiveProviderPlugin, PluginNameConflictError, PluginNotFoundError,
                                      ProviderPlugin, discover_plugin_entry_points, get_plugin_metadata, load_plugin)
    try:
        from pynecore.core.plugin.broker import BrokerPlugin
    except ImportError:  # pragma: no cover
        BrokerPlugin = None  # type: ignore[assignment]

    eps = discover_plugin_entry_points().get(name, [])
    if not eps:
        rep.fail("entry point", f"no installed plugin named {name!r} (pyne plugin list shows the installed ones)")
        return None
    if len(eps) > 1:
        rep.fail("entry point", f"name declared by {len(eps)} packages: " + ", ".join(e.value for e in eps))
        return None
    ep = eps[0]
    meta = get_plugin_metadata(ep)
    rep.ok("entry point", f"{ep.value}  package {meta['package']} {meta['version']}  needs PyneCore {meta['requires_pynecore'] or 'any'}")
    try:
        cls = load_plugin(name)
    except (PluginNotFoundError, PluginNameConflictError, Exception) as e:  # noqa: BLE001
        rep.fail("load", f"{type(e).__name__}: {e}")
        return None
    caps = []
    if isinstance(cls, type):
        if BrokerPlugin is not None and issubclass(cls, BrokerPlugin):
            caps.append("broker")
        if issubclass(cls, LiveProviderPlugin):
            caps.append("live-provider")
        elif issubclass(cls, ProviderPlugin):
            caps.append("provider")
        if issubclass(cls, CLIPlugin):
            caps.append("cli")
    if not caps:
        rep.fail("capabilities", f"{cls!r} subclasses none of ProviderPlugin / LiveProviderPlugin / BrokerPlugin / CLIPlugin")
        return None
    rep.ok("capabilities", ", ".join(caps) + (f"  display name {cls.plugin_name!r}" if getattr(cls, "plugin_name", "") else ""))
    abstract = sorted(getattr(cls, "__abstractmethods__", set()))
    if abstract:
        rep.fail("abstract methods", "still abstract: " + ", ".join(abstract))
    else:
        rep.ok("abstract methods", "all implemented")
    if issubclass(cls, ProviderPlugin):
        rep.ok("provider attributes", f"multi_broker={cls.multi_broker}  fetch_all_by_default={cls.fetch_all_by_default}  timezone={cls.timezone!r}")
        try:
            sig = inspect.signature(cls.__init__)
            params = [p for p in sig.parameters if p != "self"]
            missing = [p for p in ("symbol", "timeframe", "ohlcv_dir", "config") if p not in params]
            if missing:
                rep.fail("constructor", f"keyword parameters missing: {missing} (the framework calls cls(symbol=, timeframe=, ohlcv_dir=, config=))")
            else:
                rep.ok("constructor", "symbol=, timeframe=, ohlcv_dir=, config= accepted")
        except (TypeError, ValueError):
            rep.warn("constructor", "signature not inspectable")
    if issubclass(cls, LiveProviderPlugin):
        hooks = [h for h in ("backfill_closed_bars", "on_disconnect", "on_reconnect", "can_shutdown")
                 if getattr(cls, h) is not getattr(LiveProviderPlugin, h)]
        rep.ok("live attributes", f"feed_timeout_bars={cls.feed_timeout_bars}  initial_connect_timeout={cls.initial_connect_timeout}s  "
                                  f"reconnect_delay={cls.reconnect_delay}s..{cls.max_reconnect_delay}s  overridden hooks: {', '.join(hooks) or 'none'}")
    if issubclass(cls, CLIPlugin):
        try:
            app = cls.cli()
            cmds = []
            if app is not None:
                cmds = [getattr(c, "name", None) or getattr(c.callback, "__name__", "?") for c in getattr(app, "registered_commands", [])]
            params = {c: [o.decls[0] for o in cls.cli_params(c)] for c in ("run", "data download")}
            rep.ok("cli", f"commands: {cmds or 'none'}  injected options: {params}"
                   + ("  (note: pyne run parses injected options but does not hand them to plugins in 6.12)" if params["run"] else ""))
        except Exception as e:  # noqa: BLE001
            rep.fail("cli", f"cli()/cli_params() raised {type(e).__name__}: {e}")
    cfg_cls = getattr(cls, "Config", None)
    if cfg_cls is None:
        rep.warn("config", "no Config dataclass: the plugin has no config/plugins/<name>.toml")
    else:
        import dataclasses
        if not dataclasses.is_dataclass(cfg_cls):
            rep.fail("config", f"{cfg_cls!r} is not a dataclass")
        else:
            fields = [f"{f.name}" for f in dataclasses.fields(cfg_cls)]
            path = (workdir / "config" / "plugins" / f"{name}.toml") if workdir else None
            rep.ok("config", f"{cfg_cls.__name__}({', '.join(fields)})  file: {path if path else '(no workdir found)'}"
                   + ("" if path is None else ("  exists" if path.is_file() else "  not created yet (pyne writes it at startup)")))
    return cls


def probe(cls, name: str, symbol: str, timeframe: str, bars: int, live_seconds: float, workdir: Path | None,
          rep: Report) -> None:
    from pynecore.core.ohlcv import OHLCVReader
    from pynecore.core.plugin import LiveProviderPlugin, ProviderPlugin
    from pynecore.lib.timeframe import in_seconds

    if not issubclass(cls, ProviderPlugin):
        rep.warn("probe", "not a data provider; --symbol ignored")
        return
    config = None
    cfg_cls = getattr(cls, "Config", None)
    if cfg_cls is not None and workdir is not None:
        from pynecore.core.config import ensure_config
        path = workdir / "config" / "plugins" / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        config = ensure_config(cfg_cls, path)
    elif cfg_cls is not None:
        config = cfg_cls()
    # A temporary workdir: its data/ receives the probe's files, its config/ is a link to the real one, because
    # some providers (the ccxt family) read extra configuration relative to ohlcv_dir (ohlcv_dir/../config).
    tmp = Path(tempfile.mkdtemp(prefix="pyne-plugin-check-"))
    data_dir = tmp / "data"
    data_dir.mkdir()
    if workdir is not None and (workdir / "config").is_dir():
        try:
            (tmp / "config").symlink_to(workdir / "config", target_is_directory=True)
        except OSError:
            shutil.copytree(workdir / "config", tmp / "config")
    try:
        try:
            provider = cls(symbol=symbol, timeframe=timeframe, ohlcv_dir=data_dir, config=config)
        except Exception as e:  # noqa: BLE001
            rep.fail("instantiate", f"{type(e).__name__}: {e}")
            return
        rep.ok("instantiate", f"ohlcv_path {provider.ohlcv_path.name if provider.ohlcv_path else None}  xchg_timeframe {provider.xchg_timeframe!r}")
        tf_ms = int(in_seconds(timeframe) * 1000)

        try:
            info = provider.get_symbol_info()
        except Exception as e:  # noqa: BLE001
            rep.fail("update_symbol_info", f"{type(e).__name__}: {e}")
            return
        problems = []
        if str(info.period) != str(timeframe):
            problems.append(f"period {info.period!r} != requested {timeframe!r}")
        if not (info.mintick and info.mintick > 0):
            problems.append("mintick must be > 0")
        if not info.opening_hours or not info.session_starts or not info.session_ends:
            problems.append("opening_hours / session_starts / session_ends must be populated (also for 24/7 markets)")
        if not info.ticker or not info.prefix:
            problems.append("prefix and ticker must be set")
        (rep.fail if problems else rep.ok)("symbol info", "; ".join(problems) if problems else
                                           f"{info.prefix}:{info.ticker} type={info.type} mintick={info.mintick} mincontract={info.mincontract} "
                                           f"currency={info.currency} tz={info.timezone} sessions={len(info.opening_hours)}")

        now = datetime.now(timezone.utc)
        time_from = now - timedelta(milliseconds=tf_ms * (bars + 2))
        try:
            with provider as writer:
                provider.download_ohlcv(time_from.replace(tzinfo=None), now.replace(tzinfo=None))
                written = writer.size
        except Exception as e:  # noqa: BLE001
            rep.fail("download_ohlcv", f"{type(e).__name__}: {e}")
            return
        assert provider.ohlcv_path is not None
        with OHLCVReader(provider.ohlcv_path) as reader:
            rows = list(reader.read_from(reader.start_timestamp, reader.end_timestamp)) if reader.size else []
            period = reader.period
        if not rows:
            rep.fail("download_ohlcv", "no bars written for the requested window")
            return
        ascending = all(a.timestamp < b.timestamp for a, b in zip(rows, rows[1:]))
        last_open = rows[-1].timestamp
        forming = last_open + tf_ms > int(now.timestamp() * 1000)
        detail = (f"{written} bar(s) {datetime.fromtimestamp(rows[0].timestamp / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
                  f"{datetime.fromtimestamp(last_open / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC  period {period}")
        if not ascending:
            rep.fail("download_ohlcv", detail + "  timestamps not strictly ascending")
        elif forming:
            rep.warn("download_ohlcv", detail + "  the last bar is still forming: download_ohlcv should return closed bars only")
        else:
            rep.ok("download_ohlcv", detail)

        if live_seconds > 0:
            if not issubclass(cls, LiveProviderPlugin):
                rep.warn("live", "not a live provider; --live-seconds ignored")
                return
            from pynecore.core.live_runner import live_ohlcv_generator
            counts = {"closed": 0, "intrabar": 0}
            first_at = None
            monotonic = True
            last_ts = None
            t0 = time.monotonic()
            gen = None
            try:
                gen = live_ohlcv_generator(provider=provider, symbol=provider.symbol or symbol, timeframe=timeframe,
                                           syminfo=info, last_historical_timestamp=last_open, shutdown_timeout=10.0)
                import threading

                def stop_later():
                    time.sleep(live_seconds)
                    try:
                        gen.close()
                    except Exception:  # noqa: BLE001
                        pass
                threading.Thread(target=stop_later, daemon=True).start()
                for item in gen:
                    if not hasattr(item, "timestamp"):
                        continue                      # LIVE_TRANSITION sentinel
                    if first_at is None:
                        first_at = time.monotonic() - t0
                    key = "closed" if item.is_closed else "intrabar"
                    counts[key] += 1
                    if key == "closed":
                        if last_ts is not None and item.timestamp <= last_ts:
                            monotonic = False
                        last_ts = item.timestamp
            except Exception as e:  # noqa: BLE001
                rep.fail("live stream", f"{type(e).__name__}: {e}")
                return
            detail = (f"{counts['closed']} closed bar(s), {counts['intrabar']} intra-bar update(s) in {live_seconds:.0f}s; "
                      f"first update after {first_at:.1f}s" if first_at is not None else
                      f"no update within {live_seconds:.0f}s (expected when the bar is longer than the probe)")
            if not monotonic:
                rep.fail("live stream", detail + "  closed bars not ascending")
            else:
                (rep.ok if first_at is not None else rep.warn)("live stream", detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("name", metavar="NAME", help="plugin name as `pyne plugin list` shows it (the entry-point key), e.g. file, ccxtrest, telegram")
    parser.add_argument("--symbol", metavar="SYMBOL",
                        help="probe with this symbol in the plugin's own notation (BTCUSDT for the file plugin, BINANCE:BTC/USDT for\n"
                             "a multi-broker ccxt-based plugin): symbol info, a download, optionally the live stream")
    parser.add_argument("--timeframe", "-tf", default="60", metavar="TF",
                        help="timeframe for the probe in PyneCore notation (1, 5, 60, 1D, ...). Default: 60")
    parser.add_argument("--bars", type=int, default=50, metavar="N", help="bars to download in the probe (integer >= 1). Default: 50")
    parser.add_argument("--live-seconds", type=float, default=0.0, metavar="SEC",
                        help="run the live stream for this many seconds and count updates (float; 0 = skip). Use >= 2 bar\n"
                             "lengths to see a closed bar. Default: 0")
    parser.add_argument("--workdir", type=Path, metavar="DIR",
                        help="workdir whose config/plugins/<NAME>.toml configures the plugin. Default: $PYNE_WORK_DIR, else the\n"
                             ".pyne marker upwards from the current directory, else its workdir/ child; none: plugin defaults")
    parser.add_argument("--json", action="store_true", help="print the checks as a JSON list of {status, check, detail}")
    args = parser.parse_args()
    if args.bars < 1:
        parser.error("--bars must be >= 1")
    if not re.match(r"^[A-Za-z][\w-]*$", args.name):
        parser.error("NAME must be a plugin name, e.g. file")

    try:
        import pynecore  # noqa: F401
    except ImportError:
        raise SystemExit("pynecore is not importable: run this script with the interpreter that has pynesys-pynecore and the plugin installed")

    workdir = args.workdir.resolve() if args.workdir else find_workdir(Path.cwd())
    if workdir is not None:
        os.environ.setdefault("PYNE_WORK_DIR", str(workdir))
    rep = Report()
    cls = static_checks(args.name, rep, workdir)
    if cls is not None and args.symbol:
        probe(cls, args.name, args.symbol, args.timeframe, args.bars, args.live_seconds, workdir, rep)
    elif cls is not None and args.live_seconds:
        rep.warn("probe", "--live-seconds needs --symbol")
    if args.json:
        print(json.dumps(rep.items, indent=2))
    else:
        rep.print()
        print(f"{'OK' if not rep.failed else f'{rep.failed} check(s) FAILED'}: {args.name}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
