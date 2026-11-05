#!/usr/bin/env python3
r"""
Run a Pyne strategy live against a broker through `pyne run --broker`: preflight checks, an
explicit confirmation, the exact command, a timestamped log file, and the quarantine resume
command surfaced when the bot stops trading. REAL ORDERS ARE PLACED.

Run it with the project's interpreter; `pyne` is taken from the same environment:

    .venv/bin/python run_live.py SCRIPT.py bybit:BTCUSDT@60 --from -1000         # preflight + dry run, nothing starts
    .venv/bin/python run_live.py SCRIPT.py bybit:BTCUSDT@60 --from -1000 --yes   # start trading
    .venv/bin/python run_live.py SCRIPT.py capitalcom:EURUSD@15 --run-label a --yes
    .venv/bin/python run_live.py --list-brokers                                   # installed broker plugins

Why a wrapper: real-money runs need the CLI's broker machinery (order sync engine, SQLite run
journal in output/logs/broker.sqlite, quarantine, graceful shutdown), which has no public Python
API. run_backtest.py and run_paper.py drive ScriptRunner directly; this script drives `pyne`.

Preflight, all of which must pass before anything starts:
- `pyne` exists next to this interpreter (or on PATH) and the workdir is found;
- SCRIPT exists and declares @script.strategy (an indicator cannot trade);
- DATA names an installed plugin that subclasses BrokerPlugin. The built-in `ccxt` is data-only;
  broker plugins are separate packages (pynesys-pynecore-bybit, -capitalcom, -ctrader, ...);
- config/plugins/<plugin>.toml exists and has at least one active (uncommented) setting; a
  sandbox / demo / testnet switch set to true is reported, otherwise the run is marked REAL MONEY;
- scripts/<script>.toml is shown: it is the only source of inputs and settings for a live run.
  There are no --input/--setting overrides here on purpose: what trades is what is on disk.
Without --yes the plan and the exact pyne command are printed and the exit code is 2.

While running, pyne's output is streamed and copied to output/logs/live_<script>_<UTC time>.log.
A quarantine (`sync engine quarantined: kind=... quarantine_id=...` followed by its
`resume command: pyne broker resume RUN_ID QUARANTINE_ID`) is repeated in a frame on stderr;
the process stays alive and does not trade until you resume it. Ctrl+C or SIGTERM is forwarded
to pyne for a graceful shutdown; positions and orders stay as they are on the venue. The exit
code is pyne's.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKDIR_MARKER = ".pyne"
SANDBOX_KEYS = ("sandbox", "demo", "testnet", "paper", "use_demo", "is_demo", "test_mode")
SECRET_KEY_RE = re.compile(r"key|secret|password|token|passphrase", re.I)
QUARANTINE_RE = re.compile(r"quarantin", re.I)
RESUME_RE = re.compile(r"resume command:\s*(pyne broker resume .+?)\s*$")
BROKER_PACKAGES = "pynesys-pynecore-bybit, pynesys-pynecore-capitalcom, pynesys-pynecore-ctrader"


# ----------------------------------------------------------------------------- discovery

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


def find_pyne(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.is_file():
            raise SystemExit(f"--pyne {explicit}: not a file")
        return explicit.resolve()
    beside = Path(sys.executable).resolve().parent / "pyne"
    if beside.is_file():
        return beside
    on_path = shutil.which("pyne")
    if on_path:
        return Path(on_path)
    raise SystemExit("the pyne CLI was not found next to this interpreter or on PATH; run this script with the\n"
                     "project's .venv/bin/python (pynesys-pynecore[cli]) or pass --pyne /path/to/pyne")


def script_kind(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "@pyne" not in text[:2000]:
        return "not-pyne"
    if re.search(r"@script\.strategy\s*\(", text):
        return "strategy"
    if re.search(r"@script\.indicator\s*\(", text):
        return "indicator"
    if re.search(r"@script\.library\s*\(", text) or re.search(r"^\s*@pyne\s+lib", text, re.M):
        return "library"
    return "unknown"


def broker_plugins() -> list[tuple[str, type]]:
    from pynecore.core.plugin import discover_plugins, load_plugin
    from pynecore.core.plugin.broker import BrokerPlugin

    found = []
    for name in sorted(discover_plugins()):
        try:
            cls = load_plugin(name)
        except Exception:  # a broken third-party plugin must not hide the others
            continue
        if isinstance(cls, type) and issubclass(cls, BrokerPlugin):
            found.append((name, cls))
    return found


def resolve_broker(spec: str):
    from pynecore.core.plugin import PluginNotFoundError, load_plugin
    from pynecore.core.plugin.broker import BrokerPlugin
    from pynecore.core.provider_string import is_provider_string, parse_provider_string

    if not is_provider_string(spec):
        raise SystemExit(f"DATA must be a provider string like bybit:BTCUSDT@60, got {spec!r}")
    name = spec.split(":", 1)[0].lower()
    try:
        cls = load_plugin(name)
    except PluginNotFoundError as e:
        raise SystemExit(f"{e}\nbroker plugins are separate packages: {BROKER_PACKAGES}")
    if not (isinstance(cls, type) and issubclass(cls, BrokerPlugin)):
        installed = ", ".join(n for n, _ in broker_plugins()) or "none installed"
        raise SystemExit(f"plugin {name!r} is a data provider, not a broker: it cannot place orders.\n"
                         f"Installed broker plugins: {installed}. Packages: {BROKER_PACKAGES}.\n"
                         f"For simulated trading on this feed use run_paper.py.")
    try:
        ps = parse_provider_string(spec, require_timeframe=True, multi_broker=bool(getattr(cls, "multi_broker", False)))
    except ValueError as e:
        raise SystemExit(f"bad provider string {spec!r}: {e}")
    if not ps.symbol:
        raise SystemExit(f"provider string {spec!r} names no symbol")
    return name, cls, ps


def inspect_plugin_config(workdir: Path, name: str) -> dict:
    import tomllib

    path = workdir / "config" / "plugins" / f"{name}.toml"
    info: dict = {"path": str(path), "exists": path.is_file(), "active": [], "sandbox": None, "error": None}
    if not path.is_file():
        return info
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        info["error"] = str(e)
        return info

    def walk(table: dict, prefix: str) -> None:
        for key, value in table.items():
            full = f"{prefix}{key}"
            if isinstance(value, dict):
                walk(value, full + ".")
                continue
            if value in ("", None, {}, []):
                continue
            shown = "***" if SECRET_KEY_RE.search(key) else repr(value)
            info["active"].append(f"{full} = {shown}")
            if key.lower() in SANDBOX_KEYS and isinstance(value, bool):
                info["sandbox"] = value if info["sandbox"] is None else (info["sandbox"] and value)

    walk(data, "")
    return info


def script_settings_text(workdir: Path, script: Path) -> tuple[Path, str | None]:
    toml = script.with_suffix(".toml")
    if not toml.is_file():
        toml = workdir / "scripts" / (script.stem + ".toml")
    if not toml.is_file():
        return toml, None
    lines = [ln for ln in toml.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return toml, "\n".join(lines[:60]) + ("\n..." if len(lines) > 60 else "")


# ----------------------------------------------------------------------------- running

def stream_process(cmd: list[str], log_path: Path, shutdown_timeout: float, echo=print) -> dict:
    """Run cmd, copy its output to log_path and the console, collect quarantine notices,
    forward Ctrl+C / SIGTERM for a graceful stop. Returns exit code, log path, notices."""
    env = {**os.environ, "PYNE_NO_LOGO": "1", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    quarantines: list[str] = []
    resumes: list[str] = []
    started = time.monotonic()
    interrupted = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)

    def forward_stop(signum, _frame):
        nonlocal interrupted
        interrupted += 1
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT if interrupted == 1 else signal.SIGTERM)
        echo(f"[run_live] stop requested ({signal.Signals(signum).name}); waiting for pyne's graceful shutdown "
             f"(up to {shutdown_timeout:.0f}s, press again to terminate)", file=sys.stderr)

    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, forward_stop)
        except (ValueError, OSError):
            pass
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"# {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC  {' '.join(cmd)}\n")
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                log.flush()
                echo(line, end="")
                stripped = line.rstrip()
                if QUARANTINE_RE.search(stripped):
                    quarantines.append(stripped)
                    m = RESUME_RE.search(stripped)
                    if m:
                        resumes.append(m.group(1))
                        frame = "!" * 78
                        echo(f"\n{frame}\n! QUARANTINE: the bot stays alive but does not trade until you run:\n!   {m.group(1)}\n"
                             f"! (on this machine, same workdir; settle the venue state first)\n{frame}\n", file=sys.stderr)
            try:
                proc.wait(timeout=max(shutdown_timeout, 1.0) + 15.0)
            except subprocess.TimeoutExpired:
                echo("[run_live] pyne did not exit after the shutdown timeout; terminating", file=sys.stderr)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return {"exit_code": proc.returncode, "log": str(log_path), "seconds": round(time.monotonic() - started, 1),
            "quarantine_lines": quarantines, "resume_commands": sorted(set(resumes)), "interrupted": interrupted > 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("script", nargs="?", metavar="SCRIPT",
                        help="path to the strategy: a .py with a @pyne docstring and @script.strategy(...) on main().\n"
                             "A bare name is looked up in <workdir>/scripts/. Its scripts/<name>.toml holds the inputs and\n"
                             "settings that trade (edit that file; this script never changes it).")
    parser.add_argument("data", nargs="?", metavar="DATA",
                        help="provider string of a BROKER plugin: PLUGIN[:ACCOUNT]:SYMBOL@TIMEFRAME in the plugin's own\n"
                             "symbol notation, e.g. bybit:BTCUSDT@60, capitalcom:EURUSD@15, ctrader:EURUSD@5.\n"
                             "TIMEFRAME: minutes as a number (1, 5, 15, 60, 240) or 1D, 1W. ccxt:... is refused (data only).")
    parser.add_argument("--yes", action="store_true",
                        help="actually start the run. Without it: preflight, plan and the exact pyne command, exit code 2")
    parser.add_argument("--from", "-f", dest="from_", default="-500", metavar="START",
                        help="history the strategy warms up on before trading, passed to pyne run: -N bars (default -500;\n"
                             "use >= 2x the longest lookback), N days back, or a UTC date YYYY-MM-DD")
    parser.add_argument("--run-label", metavar="LABEL",
                        help="distinguishes parallel instances of the same strategy+account+symbol+timeframe; becomes\n"
                             "part of the broker run id (...#LABEL). Letters, digits, - and _ ")
    parser.add_argument("--shutdown-timeout", type=float, default=120.0, metavar="SEC",
                        help="pyne's graceful-shutdown budget on stop (float seconds; 0 = wait forever). Default: 120")
    parser.add_argument("--timeframe", "-tf", metavar="TF",
                        help="chart timeframe larger than the data's (pyne run -tf), e.g. 60 on 1-minute data. Default: none")
    parser.add_argument("--security", action="append", metavar="KEY=NAME",
                        help="request.security() data mapping passed to pyne run -sec: KEY = TIMEFRAME | SYMBOL:TIMEFRAME,\n"
                             "NAME = a data name in data/. Repeatable. Contexts without a mapping are resolved live through\n"
                             "the plugin's symbol map (config/plugins/<plugin>.toml, config/symbol_map.toml)")
    parser.add_argument("--plot", type=Path, metavar="PATH",
                        help="write the plot CSV to PATH (grows without bound in a live run). Default: --no-plot is passed")
    parser.add_argument("--strat", type=Path, metavar="PATH",
                        help="statistics CSV path (pyne run -sp). Default: <workdir>/output/<script>_strat.csv")
    parser.add_argument("--trade", type=Path, metavar="PATH",
                        help="trade CSV path (pyne run -tp). Default: <workdir>/output/<script>_trade.csv")
    parser.add_argument("--log-ohlcv", action="store_true",
                        help="keep pyne's per-bar OHLCV log lines (default: --no-log-ohlcv is passed to keep the log readable)")
    parser.add_argument("--log-dir", type=Path, metavar="DIR",
                        help="folder for the run log live_<script>_<UTC time>.log. Default: <workdir>/output/logs")
    parser.add_argument("--workdir", type=Path, metavar="DIR",
                        help="PyneCore workdir (passed as pyne --workdir). Default: $PYNE_WORK_DIR, else the first folder\n"
                             "from the current directory upwards holding .pyne, else its workdir/ child")
    parser.add_argument("--pyne", type=Path, metavar="PATH",
                        help="the pyne executable to use. Default: the one next to this interpreter, else `pyne` on PATH")
    parser.add_argument("--list-brokers", action="store_true",
                        help="list the installed plugins that can place orders (BrokerPlugin subclasses) and exit")
    args = parser.parse_args()

    try:
        import pynecore  # noqa: F401
    except ImportError:
        raise SystemExit("pynecore is not importable: run this script with the project's interpreter (.venv/bin/python)")

    if args.list_brokers:
        from pynecore.core.plugin import get_plugin_summary
        found = broker_plugins()
        if not found:
            print(f"no broker plugin installed (only data providers). Packages: {BROKER_PACKAGES}")
            return 1
        for name, cls in found:
            print(f"{name:<14} {get_plugin_summary(cls)}")
        return 0
    if not args.script or not args.data:
        parser.error("SCRIPT and DATA are required (or --list-brokers)")

    # --- preflight ---------------------------------------------------------------------------
    problems: list[str] = []
    pyne = find_pyne(args.pyne)
    workdir = args.workdir.resolve() if args.workdir else find_workdir(Path.cwd())
    if workdir is None:
        raise SystemExit("no PyneCore workdir found (no .pyne marker from the current directory upwards, no workdir/ child);\n"
                         "run from inside the project or pass --workdir DIR")
    script = Path(args.script)
    if not script.is_file():
        candidate = workdir / "scripts" / (args.script if args.script.endswith(".py") else args.script + ".py")
        if candidate.is_file():
            script = candidate
        else:
            raise SystemExit(f"script not found: {args.script}")
    script = script.resolve()
    kind = script_kind(script)
    if kind != "strategy":
        problems.append(f"{script.name} is {'not a Pyne script' if kind == 'not-pyne' else 'an ' + kind}; "
                        f"live trading needs @script.strategy(...)")
    name, cls, ps = resolve_broker(args.data)
    cfg = inspect_plugin_config(workdir, name)
    if not cfg["exists"]:
        problems.append(f"{cfg['path']} is missing: create it with `pyne --recreate-provider-config` and fill in the credentials")
    elif cfg["error"]:
        problems.append(f"{cfg['path']} is not valid TOML: {cfg['error']}")
    elif not cfg["active"]:
        problems.append(f"{cfg['path']} has no active setting (everything is commented out): no credentials configured")
    toml_path, toml_text = script_settings_text(workdir, script)
    if not re.fullmatch(r"-?\d+|\d{4}-\d{2}-\d{2}", args.from_.strip()):
        problems.append(f"--from {args.from_!r}: use -N bars, N days or YYYY-MM-DD")
    if args.run_label and not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_label):
        problems.append("--run-label: letters, digits, - and _ only")

    cmd = [str(pyne), "--workdir", str(workdir), "run", str(script), args.data, "--broker", "-f", args.from_.strip(),
           "--shutdown-timeout", str(args.shutdown_timeout)]
    if args.run_label:
        cmd += ["--run-label", args.run_label]
    if args.timeframe:
        cmd += ["-tf", args.timeframe]
    for sec in args.security or []:
        cmd += ["-sec", sec]
    cmd += ["--plot", str(args.plot)] if args.plot else ["--no-plot"]
    if args.strat:
        cmd += ["--strat", str(args.strat)]
    if args.trade:
        cmd += ["--trade", str(args.trade)]
    if not args.log_ohlcv:
        cmd.append("--no-log-ohlcv")

    money = "SANDBOX / DEMO account" if cfg["sandbox"] else "REAL MONEY (no sandbox/demo switch set to true in the plugin config)"
    print(f"Strategy : {script}   ({kind})")
    print(f"Broker   : {name} ({cls.__name__})   symbol {ps.provider_symbol}   timeframe {ps.timeframe}   -> {money}")
    print(f"Config   : {cfg['path']}" + (f"   active: {', '.join(cfg['active'])}" if cfg["active"] else ""))
    print(f"Settings : {toml_path}" + ("" if toml_text is not None else "   (missing: the script's defaults trade; pyne writes it on first run)"))
    if toml_text:
        print("  " + toml_text.replace("\n", "\n  "))
    print(f"Workdir  : {workdir}   journal output/logs/broker.sqlite   outputs output/{script.stem}_strat.csv, _trade.csv")
    print(f"Command  : {' '.join(cmd)}")
    for p in problems:
        print(f"! {p}")
    if problems:
        print(f"{len(problems)} problem(s): fix them before starting", file=sys.stderr)
        return 1
    if not args.yes:
        print("Dry run: add --yes to start. Test the logic first with run_paper.py on the same feed.")
        return 2

    log_dir = args.log_dir or workdir / "output" / "logs"
    log_path = log_dir / f"live_{script.stem}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.log"
    print(f"Starting ; log {log_path}   stop with Ctrl+C (graceful; positions stay on the venue)", flush=True)
    result = stream_process(cmd, log_path, args.shutdown_timeout)
    print("-" * 70)
    print(f"pyne exited with code {result['exit_code']} after {result['seconds']} s   log: {result['log']}")
    if result["resume_commands"]:
        print("Quarantine resume command(s) seen in this run:")
        for c in result["resume_commands"]:
            print(f"  {c}")
    elif result["quarantine_lines"]:
        print(f"{len(result['quarantine_lines'])} quarantine-related line(s) in the log")
    return int(result["exit_code"] or 0)


if __name__ == "__main__":
    sys.exit(main())
