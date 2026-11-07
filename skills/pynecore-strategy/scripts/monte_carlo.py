#!/usr/bin/env python3
r"""
Monte Carlo robustness tests for a PyneCore strategy. PyneCore has none built in; this script adds two kinds.

trades  Resample the closed trades of a finished backtest (the trade CSV that `pyne run`, run_backtest.py or
        a standalone run writes). Standard library only, any Python 3.11+:

    python3 monte_carlo.py trades output/my_strategy_trade.csv                      # 1000 shuffles of the trade order
    python3 monte_carlo.py trades output/my_strategy --method bootstrap --runs 5000 # draw trades with replacement
    python3 monte_carlo.py trades output/my_strategy --method shuffle --skip 0.1    # and miss 10 % of the trades
    python3 monte_carlo.py trades output/my_strategy --sizing compound --dd-limit 25 --json

prices  Re-run the strategy on perturbed price data through ScriptRunner, the way run_backtest.py runs it. Needs
        the project's interpreter (pynesys-pynecore installed):

    .venv/bin/python monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --method noise --runs 100
    .venv/bin/python monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --noise 0.02,0.05,0.1 --runs 50
    .venv/bin/python monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --method blocks --block 48
    .venv/bin/python monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --method windows --window 2000

What each method answers:
    shuffle    the same trades in another order: how deep the drawdown and how long the losing streak could have
               been with identical total profit (the order of trades is luck, their sum is not)
    bootstrap  trades drawn with replacement: a confidence range of net profit, profit factor, win rate and
               drawdown if future trades come from the same distribution
    --skip P   each trade is dropped with probability P (missed fills, downtime); combines with both
    noise      every bar's high/low/close moved by Gaussian noise scaled to the bar's range, the open kept at the
               noisy previous close plus the original gap (another feed, tick noise): does the edge survive prices
               that are slightly different? It also adds some volatility; see the reference
    blocks     a synthetic history stitched from random blocks of the real bars (block bootstrap of bar shapes
               relative to the previous close): does it survive a market with the same local behaviour but
               another path?
    windows    random contiguous sub-periods of the data: how much does the result depend on the period chosen?

Output: for every metric the original value, the 5/25/50/75/95th percentiles of the simulations and the
original's percentile rank, then the share of runs that lost money and that reached the drawdown limit.
Drawdown is measured on the closed-trade equity curve (as run_backtest.py does), so it is a lower bound of the
intra-trade drawdown. Exit codes: 0 done, 1 bad input or no usable trades / runs, 2 usage error.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                     # summarize_backtest.py / run_backtest.py of this skill

PCTS = (5, 25, 50, 75, 95)


# ----------------------------------------------------------------------------- statistics helpers

def percentile(sorted_values: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default)."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def rank(values: list[float], x: float | None) -> float | None:
    """Share of simulations (in %) at or below x."""
    if x is None or not values:
        return None
    return sum(1 for v in values if v <= x) / len(values) * 100.0


def finite(values) -> list[float]:
    return [v for v in values if v is not None and isinstance(v, (int, float)) and math.isfinite(v)]


def distribution(rows: list[dict], original: dict, keys: list[str]) -> dict:
    out = {}
    for key in keys:
        vals = sorted(finite(r.get(key) for r in rows))
        orig = original.get(key) if original else None
        out[key] = {
            "original": orig,
            **{f"p{p}": percentile(vals, p) for p in PCTS},
            "mean": (sum(vals) / len(vals)) if vals else None,
            # a metric the method cannot change (net profit under shuffle) has no meaningful rank
            "original_rank_pct": (rank(vals, orig) if orig is not None and math.isfinite(orig) and vals
                                  and vals[-1] - vals[0] > 1e-9 * max(1.0, abs(vals[-1])) else None),
            "n": len(vals),
        }
    return out


# ----------------------------------------------------------------------------- trade-list Monte Carlo

def load_trades(path_arg: str, capital: float | None) -> tuple[list[dict], float, str, list[str]]:
    """Closed trades of a trade CSV, the initial capital and the currency. The synthetic exit row of a position
    still open at the end (signal "Open") is left out."""
    import summarize_backtest as sb

    strat_path, trade_path = sb.resolve_paths(path_arg)
    if trade_path is None:
        raise SystemExit(f"no trade CSV found for {path_arg} (expected <name>_trade.csv or <name>_trades.csv; "
                         f"run the strategy first: pyne run ... or run_backtest.py --trade PATH)")
    trades, currency = sb.read_trades(trade_path)
    notes = []
    closed = [t for t in trades if t.get("profit") is not None and (t.get("exit_signal") or "") != "Open"]
    open_count = sum(1 for t in trades if (t.get("exit_signal") or "") == "Open")
    if open_count:
        notes.append(f"{open_count} trade(s) still open at the end of the data left out")
    if capital is None and strat_path is not None:
        stats, _cur = sb.read_stats(strat_path)
        net = stats.get("Net profit", {})
        if net.get("all") and net.get("all_pct"):
            capital = net["all"] / (net["all_pct"] / 100.0)       # the statistics CSV keeps full precision
    if capital is None:
        best = None                                   # the row with the largest cumulative %: least rounding error
        with trade_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                money = next((v for k, v in row.items() if k.startswith("Cumulative profit") and "%" not in k), None)
                pct = row.get("Cumulative profit %")
                try:
                    money_f, pct_f = float(money), float(pct)
                except (TypeError, ValueError):
                    continue
                if pct_f and (best is None or abs(pct_f) > abs(best[1])):
                    best = (money_f, pct_f)
        if best is not None:
            capital = best[0] / (best[1] / 100.0)
            notes.append(f"initial capital estimated from the trade CSV's rounded cumulative % as {capital:,.2f}; "
                         f"pass --capital for the exact value")
    if capital is None or capital <= 0:
        raise SystemExit("cannot determine the initial capital (no statistics CSV next to the trade CSV and no "
                         "cumulative profit in it); pass --capital AMOUNT")
    return closed, float(capital), currency, notes


def equity_path_metrics(pnls: list[float], returns: list[float], capital: float, sizing: str) -> dict:
    equity = peak = capital
    max_dd = max_dd_abs = 0.0
    streak = worst = 0
    gross_win = gross_loss = 0.0
    wins = 0
    for pnl, ret in zip(pnls, returns):
        change = equity * ret if sizing == "compound" else pnl
        equity += change
        if change > 0:
            wins += 1
            gross_win += change
            streak = 0
        else:
            gross_loss -= change
            streak += 1
            worst = max(worst, streak)
        if equity > peak:
            peak = equity
        dd_abs = peak - equity
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs
        if peak > 0:
            max_dd = max(max_dd, dd_abs / peak * 100.0)
    n = len(pnls)
    return {
        "trades": n,
        "net_profit_pct": (equity - capital) / capital * 100.0,
        "final_equity": equity,
        "max_drawdown_pct": max_dd,
        "max_drawdown": max_dd_abs,
        "max_loss_streak": worst,
        "win_rate": wins / n * 100.0 if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def run_trades(args) -> int:
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if not 0.0 <= args.skip < 1.0:
        raise SystemExit("--skip must be a fraction in [0, 1), e.g. 0.1 for 10 %")
    if args.capital is not None and not (math.isfinite(args.capital) and args.capital > 0):
        raise SystemExit(f"--capital must be a positive amount, got {args.capital:g}")
    trades, capital, currency, notes = load_trades(args.path, args.capital)
    if len(trades) < 2:
        raise SystemExit(f"{len(trades)} closed trade(s): nothing to resample")
    if len(trades) < 30:
        notes.append(f"only {len(trades)} closed trades: percentiles of so few trades are rough")
    pnls = [t["profit"] for t in trades]
    # each trade's return on the equity it was opened with (compound sizing re-applies it to the simulated equity)
    returns, equity = [], capital
    for pnl in pnls:
        returns.append(pnl / equity if equity > 0 else 0.0)
        equity += pnl
    rng = random.Random(args.seed)
    original = equity_path_metrics(pnls, returns, capital, args.sizing)
    idx = list(range(len(trades)))
    rows = []
    for _ in range(args.runs):
        if args.method == "shuffle":
            order = idx[:]
            rng.shuffle(order)
        else:
            order = [rng.randrange(len(idx)) for _ in idx]
        if args.skip > 0:
            order = [i for i in order if rng.random() >= args.skip]
        rows.append(equity_path_metrics([pnls[i] for i in order], [returns[i] for i in order], capital, args.sizing))

    keys = ["net_profit_pct", "max_drawdown_pct", "max_loss_streak", "profit_factor", "win_rate", "trades"]
    dist = distribution(rows, original, keys)
    p_loss = sum(1 for r in rows if r["net_profit_pct"] < 0) / len(rows) * 100.0
    p_dd = sum(1 for r in rows if r["max_drawdown_pct"] >= args.dd_limit) / len(rows) * 100.0
    result = {
        "mode": "trades", "method": args.method, "skip": args.skip, "sizing": args.sizing, "runs": args.runs,
        "seed": args.seed, "source": str(args.path), "closed_trades": len(trades), "initial_capital": capital,
        "currency": currency, "metrics": dist, "p_loss_pct": p_loss,
        "dd_limit_pct": args.dd_limit, "p_dd_limit_pct": p_dd, "notes": notes,
    }
    if args.out:
        write_rows(args.out, rows)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        title = f"Monte Carlo: {args.method}" + (f" + skip {args.skip:.0%}" if args.skip else "") + \
                f", {args.runs} runs, {len(trades)} closed trades, sizing {args.sizing}, seed {args.seed}"
        print_report(title, f"initial capital {capital:,.2f} {currency}".rstrip(), dist, keys,
                     p_loss, args.dd_limit, p_dd, notes, args.method)
    return 0


# ----------------------------------------------------------------------------- price-path Monte Carlo

def perturb_noise(bars: list, rng: random.Random, scale: float) -> list:
    """Gaussian noise on high, low and close (standard deviation scale x the bar's range). The open follows the
    noisy previous close plus the bar's original gap, so a continuous market stays gap-free and a gapped one
    keeps its gaps; independent noise on the open would put an artificial gap on every bar."""
    out = []
    prev_orig_close = prev_new_close = None
    for b in bars:
        rng_bar = (b.high - b.low) or abs(b.close) * 1e-4
        h, l, c = (p + rng.gauss(0.0, scale * rng_bar) for p in (b.high, b.low, b.close))
        if prev_new_close is None:
            o = b.open + rng.gauss(0.0, scale * rng_bar)
        else:
            o = prev_new_close + (b.open - prev_orig_close)
        hi, lo = max(o, h, l, c), min(o, h, l, c)
        prev_orig_close = b.close
        if lo <= 0:                                        # never produce non-positive prices
            out.append(b)
            prev_new_close = b.close
            continue
        out.append(b._replace(open=o, high=hi, low=lo, close=c))
        prev_new_close = c
    return out


def perturb_blocks(bars: list, rng: random.Random, block: int) -> list:
    """Block bootstrap of bar shapes relative to the previous close, re-chained from the first close; the
    timestamps stay the original ones so sessions and time filters keep working."""
    n = len(bars)
    rel = [(b.open / p.close, b.high / p.close, b.low / p.close, b.close / p.close, b.volume)
           for p, b in zip(bars, bars[1:]) if p.close > 0]
    if len(rel) < block:
        raise SystemExit(f"--block {block} is longer than the data ({len(rel)} bar changes)")
    seq: list[tuple] = []
    while len(seq) < n - 1:
        start = rng.randrange(0, len(rel) - block + 1)
        seq.extend(rel[start:start + block])
    out = [bars[0]]
    prev = bars[0].close
    for b, (ro, rh, rl, rc, vol) in zip(bars[1:], seq):
        o, h, l, c = prev * ro, prev * rh, prev * rl, prev * rc
        out.append(b._replace(open=o, high=max(o, h, l, c), low=min(o, h, l, c), close=c, volume=vol))
        prev = c
    return out


def perturb_window(bars: list, rng: random.Random, length: int) -> list:
    start = rng.randrange(0, len(bars) - length + 1)
    return bars[start:start + length]


def run_prices(args) -> int:
    try:
        import pynecore  # noqa: F401
    except ImportError:
        raise SystemExit("prices mode needs pynecore: run it with the project's interpreter (.venv/bin/python)")
    import run_backtest as rb

    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    script = Path(args.script)
    if not script.is_file():
        raise SystemExit(f"script not found: {script}")
    inputs, settings = rb.parse_kv(args.input), rb.parse_kv(args.setting)
    bars, syminfo, period, toml = rb.load_data(
        Path(args.data), provider=args.provider, symbol=args.symbol, tz=args.timezone, force=False,
        from_ms=rb.parse_date_ms(args.from_), to_ms=rb.parse_date_ms(args.to))
    window = None
    if args.method == "windows":
        window = args.window or len(bars) // 2
        if not 2 <= window <= len(bars):
            raise SystemExit(f"--window {window}: must be between 2 and the number of bars ({len(bars)})")
    levels: list[float] = []
    if args.method == "noise":                       # --noise means nothing to blocks / windows: not even parsed
        try:
            levels = [float(x) for x in str(args.noise).split(",") if x.strip()]
        except ValueError:
            raise SystemExit(f"--noise {args.noise!r}: one number or a comma-separated list, e.g. 0.1 or 0.02,0.05,0.1")
        if not levels or any(not (math.isfinite(v) and v > 0) for v in levels):
            raise SystemExit("--noise must be > 0 (a fraction of each bar's high-low range, e.g. 0.1 or 0.02,0.05,0.1)")
    if args.method == "blocks" and args.block < 1:
        raise SystemExit("--block must be >= 1 bar")

    def once(data: list) -> dict:
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):   # the script's log lines
            # a list, as pyne run and run_backtest.py pass it: the runner knows the last bar (calc_bars_count)
            return rb.run_once(script, data, syminfo, inputs=inputs, settings=settings)

    t0 = time.monotonic()
    try:
        original = once(bars)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"the original run (unperturbed data) failed: {type(e).__name__}: {e}\n"
                         f"fix the script first: run_backtest.py {script} {args.data}")
    if not original.get("is_strategy"):
        raise SystemExit(f"{script.name} is an indicator: prices mode measures trades, run it on a strategy")
    if (original.get("trades") or 0) < 2:
        raise SystemExit(f"the original run has {original.get('trades') or 0} closed trade(s): nothing to compare "
                         f"(check the inputs, settings and the --from/--to range)")
    # every run gets its own seed drawn from one generator: --seed 42 and --seed 43 share no runs. A noise ladder
    # reuses the same seeds at every level (common random numbers): level L scales the very same Gaussian draws,
    # so the differences between levels come from the noise size, not from different random paths
    master = random.Random(args.seed)
    seeds = [master.getrandbits(64) for _ in range(args.runs)]

    def simulate(make, label: str, extra: dict) -> tuple[list[dict], list[str]]:
        rows_, failed_ = [], []
        for i, run_seed in enumerate(seeds):
            try:
                res = once(make(random.Random(run_seed)))
                rows_.append(extra | {k: res.get(k) for k in ("trades", "net_profit_pct", "profit_factor", "win_rate",
                                                              "max_drawdown_pct", "avg_trade")} | {"seed": run_seed})
            except Exception as e:  # noqa: BLE001  a script can fail on prices it never saw; count it, go on
                failed_.append(f"run seed {run_seed}: {type(e).__name__}: {e}")
            if not args.json and (i + 1) % max(1, args.runs // 10) == 0:
                print(f"  {label}{i + 1}/{args.runs} runs, {time.monotonic() - t0:.0f} s", file=sys.stderr)
        return rows_, failed_

    keys = ["net_profit_pct", "max_drawdown_pct", "profit_factor", "win_rate", "trades"]
    if args.method == "noise" and len(levels) > 1:
        return noise_ladder(args, levels, simulate, bars, original, keys, script, t0)

    if args.method == "noise":
        rows, failed = simulate(lambda rng: perturb_noise(bars, rng, levels[0]), "", {})
    elif args.method == "blocks":
        rows, failed = simulate(lambda rng: perturb_blocks(bars, rng, args.block), "", {})
    else:
        rows, failed = simulate(lambda rng: perturb_window(bars, rng, window), "", {})
    if not rows:
        raise SystemExit("every run failed:\n  " + "\n  ".join(failed[:5]))

    orig = {k: original.get(k) for k in keys}
    if args.method == "windows":
        orig = {}                                    # a sub-period is not comparable with the full history
    for k in ("profit_factor",):
        for r in rows:
            if r.get(k) == math.inf:
                r[k] = None                          # no losing trade: leave it out of the percentiles
    dist = distribution(rows, orig, keys)
    p_loss = sum(1 for r in rows if (r["net_profit_pct"] or 0) < 0) / len(rows) * 100.0
    p_dd = sum(1 for r in rows if (r["max_drawdown_pct"] or 0) >= args.dd_limit) / len(rows) * 100.0
    notes = []
    if failed:
        notes.append(f"{len(failed)} run(s) failed and are left out, e.g. {failed[0]}")
    if (original.get("trades") or 0) < 30:
        notes.append(f"the original run has only {original.get('trades')} closed trades")
    if args.method == "windows":
        notes.append(f"windows of {window} bars out of {len(bars)}; the original column is empty because a "
                     f"sub-period is not comparable with the full history (original net {original.get('net_profit_pct'):.2f} %)")
    result = {
        "mode": "prices", "method": args.method, "runs": args.runs, "ok_runs": len(rows), "seed": args.seed,
        "script": str(script), "data": str(args.data), "bars": len(bars), "period": period,
        "parameters": {"noise": levels[0]} if args.method == "noise" else
                      {"block": args.block} if args.method == "blocks" else {"window": window},
        "original": {k: original.get(k) for k in keys}, "metrics": dist, "p_loss_pct": p_loss,
        "dd_limit_pct": args.dd_limit, "p_dd_limit_pct": p_dd, "seconds": round(time.monotonic() - t0, 1),
        "notes": notes,
    }
    if args.out:
        write_rows(args.out, rows)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        param = {"noise": f"noise {levels[0]} x bar range", "blocks": f"blocks of {args.block} bars",
                 "windows": f"windows of {window} bars"}[args.method]
        title = (f"Monte Carlo: {args.method} ({param}), {len(rows)}/{args.runs} runs on {len(bars)} bars "
                 f"of {Path(args.data).name}, seed {args.seed}, {result['seconds']} s")
        print_report(title, f"script {script.name}", dist, keys, p_loss, args.dd_limit, p_dd, notes, args.method)
    return 0


def noise_ladder(args, levels: list[float], simulate, bars: list, original: dict, keys: list[str], script: Path,
                 t0: float) -> int:
    """The same number of noisy runs at several noise levels. Noise itself degrades most strategies (extra
    volatility, whipsaws), so the useful signal is the shape: a result fitted to the exact prices already drops
    at the smallest level, a sound one degrades smoothly from the original."""
    orig = {k: original.get(k) for k in keys}
    ladder, all_rows, failed_total = [], [], 0
    for level in levels:
        rows, failed = simulate(lambda rng, lv=level: perturb_noise(bars, rng, lv), f"noise {level}: ", {"noise": level})
        failed_total += len(failed)
        all_rows += rows
        if not rows:
            ladder.append({"noise": level, "ok_runs": 0, "failed": len(failed)})
            continue
        dist = distribution(rows, orig, keys)
        ladder.append({
            "noise": level, "ok_runs": len(rows), "failed": len(failed), "metrics": dist,
            "p_loss_pct": sum(1 for r in rows if (r["net_profit_pct"] or 0) < 0) / len(rows) * 100.0,
            "p_dd_limit_pct": sum(1 for r in rows if (r["max_drawdown_pct"] or 0) >= args.dd_limit) / len(rows) * 100.0,
        })
    if not all_rows:
        raise SystemExit("every run failed at every noise level")
    seconds = round(time.monotonic() - t0, 1)
    if args.out:
        write_rows(args.out, all_rows)
    if args.json:
        print(json.dumps({"mode": "prices", "method": "noise", "ladder": ladder, "runs_per_level": args.runs,
                          "common_seeds": True,
                          "seed": args.seed, "script": str(script), "data": str(args.data), "bars": len(bars),
                          "original": orig, "dd_limit_pct": args.dd_limit, "seconds": seconds}, indent=2, default=str))
        return 0
    print(f"Monte Carlo: noise ladder {', '.join(f'{v:g}' for v in levels)} x bar range, {args.runs} runs per level "
          f"(the same run seeds at every level) on {len(bars)} bars of {Path(args.data).name}, seed {args.seed}, "
          f"{seconds} s")
    print(f"script {script.name}; original: net {fmt(orig['net_profit_pct'], 2)} %, max drawdown "
          f"{fmt(orig['max_drawdown_pct'], 2)} %, profit factor {fmt(orig['profit_factor'], 2)}, {fmt(orig['trades'], 0)} trades")
    print(f"{'noise':>7} {'runs':>5} {'net p5':>8} {'median':>8} {'p95':>8} {'rank':>6} {'DD median':>10} "
          f"{'PF median':>10} {'trades':>7} {'losing':>7}")
    for row in ladder:
        if not row["ok_runs"]:
            print(f"{row['noise']:>7g} {0:>5}   every run failed")
            continue
        m = row["metrics"]
        n, r = m["net_profit_pct"], m["net_profit_pct"]["original_rank_pct"]
        print(f"{row['noise']:>7g} {row['ok_runs']:>5} {fmt(n['p5'], 2):>8} {fmt(n['p50'], 2):>8} {fmt(n['p95'], 2):>8} "
              f"{(f'{r:.0f}%' if r is not None else '-'):>6} {fmt(m['max_drawdown_pct']['p50'], 2):>10} "
              f"{fmt(m['profit_factor']['p50'], 2):>10} {fmt(m['trades']['p50'], 0):>7} {row['p_loss_pct']:>6.0f}%")
    first = next((row for row in ladder if row["ok_runs"]), None)
    if first is not None and orig.get("net_profit_pct") is not None:
        n = first["metrics"]["net_profit_pct"]
        inside = n["p5"] <= orig["net_profit_pct"] <= n["p95"]
        print(f"At noise {first['noise']:g} the original net {orig['net_profit_pct']:.2f} % is "
              + ("inside" if inside else "OUTSIDE") + f" the p5..p95 band ({n['p5']:.2f} .. {n['p95']:.2f}).")
    print("Read the shape: inside the band at the smallest level and a steady decline as the noise grows is noise "
          "sensitivity (noise adds volatility and whipsaws); outside the band (above p95) already at the smallest "
          "level means the result depends on the exact prices.")
    if failed_total:
        print(f"! {failed_total} run(s) failed and are left out")
    return 0


# ----------------------------------------------------------------------------- output

LABELS = {
    "net_profit_pct": ("Net profit %", 2), "max_drawdown_pct": ("Max drawdown %", 2),
    "max_loss_streak": ("Losing streak", 0), "profit_factor": ("Profit factor", 2), "win_rate": ("Win rate %", 1),
    "trades": ("Trades", 0),
}


def fmt(v, digits: int) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def print_report(title: str, subtitle: str, dist: dict, keys: list[str], p_loss: float, dd_limit: float,
                 p_dd: float, notes: list[str], method: str) -> None:
    print(title)
    print(subtitle)
    print(f"{'':16} {'original':>10} {'p5':>10} {'p25':>10} {'median':>10} {'p75':>10} {'p95':>10} {'rank':>6}")
    for key in keys:
        label, digits = LABELS[key]
        d = dist[key]
        r = d["original_rank_pct"]
        print(f"{label:16} {fmt(d['original'], digits):>10} " +
              " ".join(f"{fmt(d[f'p{p}'], digits):>10}" for p in PCTS) +
              f" {(f'{r:.0f}%' if r is not None else '-'):>6}")
    print(f"Runs losing money             : {p_loss:.1f} %")
    print(f"Runs reaching {dd_limit:g} % drawdown : {p_dd:.1f} %")
    dd = dist.get("max_drawdown_pct", {})
    if method == "shuffle" and dd.get("p95") is not None:
        print(f"In 95 % of the orders the drawdown stays below {dd['p95']:.2f} % "
              f"(the actual order: {fmt(dd['original'], 2)} %, rank {fmt(dd['original_rank_pct'], 0)} %).")
    print("rank = share of simulations at or below the original value (a drawdown ranked near 0 % means the real "
          "path was one of the luckiest orders).")
    for n in notes:
        print(f"! {n}")


def write_rows(path: Path, rows: list[dict]) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"per-run metrics written to {path}", file=sys.stderr)


# ----------------------------------------------------------------------------- CLI

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True, metavar="MODE")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seed", type=int, default=42, metavar="N",
                        help="random seed (integer); the same seed gives the same simulations, another seed shares\n"
                             "none of them (prices mode draws each run's seed from it; --out lists them). Default: 42")
    common.add_argument("--dd-limit", type=float, default=20.0, metavar="PCT",
                        help="drawdown threshold in percent (float, e.g. 25) for the 'runs reaching ... drawdown'\n"
                             "line. Default: 20")
    common.add_argument("--out", type=Path, metavar="CSV",
                        help="also write one row per simulation (its metrics) to this CSV file; folders are created")
    common.add_argument("--json", action="store_true", help="print the result as one JSON object instead of text")

    t = sub.add_parser("trades", parents=[common], formatter_class=argparse.RawTextHelpFormatter,
                       help="resample the closed trades of a trade CSV (stdlib only)",
                       description="Resample the closed trades of a finished backtest.")
    t.add_argument("path", metavar="PATH",
                   help="the trade CSV (output/<script>_trade.csv of pyne run, <script>_trades.csv of a standalone\n"
                        "run), or the common prefix (output/<script>); the <prefix>_strat.csv next to it supplies\n"
                        "the exact initial capital")
    t.add_argument("--method", choices=("shuffle", "bootstrap"), default="shuffle",
                   help="shuffle: the same trades in a random order (total profit fixed, path varies);\n"
                        "bootstrap: as many trades drawn with replacement (total profit varies). Default: shuffle")
    t.add_argument("--skip", type=float, default=0.0, metavar="P",
                   help="drop each trade with probability P (float in [0, 1), e.g. 0.1) after the method; models\n"
                        "missed fills and downtime. Default: 0")
    t.add_argument("--sizing", choices=("fixed", "compound"), default="fixed",
                   help="fixed: every trade adds its own P&L in money (fixed or cash sizing);\n"
                        "compound: every trade applies its return on the equity it was opened with to the\n"
                        "simulated equity (percent_of_equity sizing). Default: fixed")
    t.add_argument("--runs", type=int, default=1000, metavar="N", help="number of simulations (integer >= 1). Default: 1000")
    t.add_argument("--capital", type=float, metavar="AMOUNT",
                   help="initial capital (float). Default: from the statistics CSV next to the trade CSV, else\n"
                        "estimated from the trade CSV's cumulative profit columns")

    pr = sub.add_parser("prices", parents=[common], formatter_class=argparse.RawTextHelpFormatter,
                        help="re-run the strategy on perturbed price data (needs pynecore)",
                        description="Re-run the strategy through ScriptRunner on perturbed copies of the data.")
    pr.add_argument("script", metavar="SCRIPT", help="the Pyne strategy file, e.g. scripts/my_strategy.py")
    pr.add_argument("data", metavar="DATA",
                    help="a .ohlcv file with its .toml next to it (CSV/JSON/TXT are converted first, as in\n"
                         "run_backtest.py)")
    pr.add_argument("--method", choices=("noise", "blocks", "windows"), default="noise",
                    help="noise: Gaussian noise on high/low/close scaled to the bar's range, the open kept at the\n"
                         "noisy previous close plus the original gap;\n"
                         "blocks: a synthetic history from random blocks of real bars (shapes relative to the\n"
                         "previous close, re-chained, original timestamps);\n"
                         "windows: random contiguous sub-periods. Default: noise")
    pr.add_argument("--runs", type=int, default=100, metavar="N",
                    help="number of perturbed runs (integer >= 1); each is a full backtest. Default: 100")
    pr.add_argument("--noise", default="0.1", metavar="F[,F...]",
                    help="noise: standard deviation as a fraction of each bar's high-low range (float > 0) on high,\n"
                         "low and close; the open follows the noisy previous close plus the original gap. Noise adds\n"
                         "volatility (0.1: return stdev about +3 %%, 0.3: about +23 %%). A comma-separated list\n"
                         "(e.g. 0.02,0.05,0.1) runs --runs simulations per level with the same run seeds at every\n"
                         "level and prints a ladder. Ignored by the other methods. Default: 0.1")
    pr.add_argument("--block", type=int, default=24, metavar="BARS",
                    help="blocks: block length in bars (integer >= 1); longer keeps more of the trends and\n"
                         "volatility clusters. Default: 24")
    pr.add_argument("--window", type=int, metavar="BARS",
                    help="windows: sub-period length in bars (integer >= 2, at most the data). Default: half the data")
    pr.add_argument("--from", "-f", dest="from_", metavar="DATE",
                    help="first bar: YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (UTC). Default: the first bar of DATA")
    pr.add_argument("--to", "-t", metavar="DATE", help="last bar, same format as --from. Default: the last bar of DATA")
    pr.add_argument("--input", "-i", action="append", metavar="NAME=VALUE",
                    help="override a script input by its main() parameter name; repeatable (as in run_backtest.py)")
    pr.add_argument("--setting", "-s", action="append", metavar="NAME=VALUE",
                    help="override a script setting (decorator argument name); repeatable")
    pr.add_argument("--symbol", metavar="SYMBOL", help="symbol for a CSV converted on the fly (as in run_backtest.py)")
    pr.add_argument("--provider", metavar="NAME", help="provider prefix for a CSV converted on the fly")
    pr.add_argument("--timezone", default="UTC", metavar="TZ", help="timezone of naive CSV timestamps. Default: UTC")

    args = parser.parse_args()
    return run_trades(args) if args.mode == "trades" else run_prices(args)


if __name__ == "__main__":
    sys.exit(main())
