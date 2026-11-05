#!/usr/bin/env python3
"""
Summarize a PyneCore backtest from its CSV outputs.

Standard library only. Point it at the strategy statistics file written by `pyne run`
(`output/<script>_strat.csv`) or by the standalone runner (`<script>_strat.csv`); the trade
list (`<script>_trade.csv` / `<script>_trades.csv`) is picked up automatically when it sits
next to it.

    python3 summarize_backtest.py output/my_strategy_strat.csv
    python3 summarize_backtest.py output/my_strategy_strat.csv --trades 5 --json
    python3 summarize_backtest.py output/my_strategy          # base name, extensions guessed

The point is a report short enough to paste into a message: the headline metrics, the long /
short split, a sanity block (margin calls, open trades, trade count) and a few trades.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def resolve_paths(arg: str) -> tuple[Path | None, Path | None]:
    p = Path(arg)
    candidates_strat: list[Path] = []
    candidates_trade: list[Path] = []
    if p.suffix == ".csv":
        stem = p.name
        if stem.endswith("_strat.csv"):
            base = p.with_name(stem[: -len("_strat.csv")])
        elif stem.endswith("_trade.csv"):
            base = p.with_name(stem[: -len("_trade.csv")])
        elif stem.endswith("_trades.csv"):
            base = p.with_name(stem[: -len("_trades.csv")])
        else:
            base = p.with_suffix("")
    else:
        base = p
    candidates_strat.append(base.with_name(base.name + "_strat.csv"))
    candidates_trade.append(base.with_name(base.name + "_trade.csv"))
    candidates_trade.append(base.with_name(base.name + "_trades.csv"))
    strat = next((c for c in candidates_strat if c.is_file()), None)
    trade = next((c for c in candidates_trade if c.is_file()), None)
    return strat, trade


def to_num(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def read_stats(path: Path) -> dict[str, dict[str, float | None]]:
    """{metric: {"all": x, "all_pct": y, "long": ..., "long_pct": ..., "short": ..., "short_pct": ...}}"""
    stats: dict[str, dict[str, float | None]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return stats
        for row in reader:
            if not row or not row[0].strip():
                continue
            cells = row + [""] * (7 - len(row))
            stats[row[0].strip()] = {
                "all": to_num(cells[1]), "all_pct": to_num(cells[2]),
                "long": to_num(cells[3]), "long_pct": to_num(cells[4]),
                "short": to_num(cells[5]), "short_pct": to_num(cells[6]),
            }
    return stats


def read_trades(path: Path) -> list[dict]:
    """Pair entry/exit rows into one record per trade."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_num: dict[str, dict] = {}
    for row in rows:
        num = row.get("Trade #", "").strip()
        rec = by_num.setdefault(num, {"num": num})
        kind = row.get("Type", "")
        price_key = next((k for k in row if k.startswith("Price")), "Price")
        profit_key = next((k for k in row if k.startswith("Profit ") and "%" not in k), "Profit")
        if kind.startswith("Entry"):
            rec.update({
                "side": "long" if "long" in kind.lower() else "short",
                "entry_time": row.get("Date/Time"), "entry_price": to_num(row.get(price_key, "")),
                "entry_bar": to_num(row.get("Bar Index", "")), "signal": row.get("Signal"),
                "contracts": to_num(row.get("Contracts", "")),
            })
        else:
            rec.update({
                "exit_type": kind, "exit_signal": row.get("Signal"),
                "exit_time": row.get("Date/Time"), "exit_price": to_num(row.get(price_key, "")),
                "exit_bar": to_num(row.get("Bar Index", "")),
                "profit": to_num(row.get(profit_key, "")), "profit_pct": to_num(row.get("Profit %", "")),
                "cum_profit": to_num(next((row[k] for k in row if k.startswith("Cumulative profit ") and "%" not in k), "")),
                "runup_pct": to_num(row.get("Run-up %", "")), "drawdown_pct": to_num(row.get("Drawdown %", "")),
            })
    trades = [t for t in by_num.values() if t.get("num")]
    trades.sort(key=lambda t: float(t["num"]) if t["num"].replace(".", "", 1).isdigit() else 0)
    return trades


def fmt(value: float | None, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value:.{digits}f}%"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.{digits}f}"


def build_summary(stats: dict, trades: list[dict], currency: str) -> dict:
    def g(metric: str, col: str = "all"):
        return stats.get(metric, {}).get(col)

    summary = {
        "currency": currency,
        "net_profit": g("Net profit"), "net_profit_pct": g("Net profit", "all_pct"),
        "gross_profit": g("Gross profit"), "gross_loss": g("Gross loss"),
        "commission_paid": g("Commission paid"),
        "buy_hold_return": g("Buy & hold return"), "buy_hold_return_pct": g("Buy & hold return", "all_pct"),
        "max_drawdown": g("Max equity drawdown"), "max_drawdown_pct": g("Max equity drawdown", "all_pct"),
        "max_runup": g("Max equity run-up"), "max_runup_pct": g("Max equity run-up", "all_pct"),
        "total_trades": g("Total trades"), "open_trades": g("Total open trades"),
        "winning_trades": g("Winning trades"), "losing_trades": g("Losing trades"),
        "percent_profitable": g("Percent profitable", "all_pct"),
        "avg_trade": g("Avg P&L"), "avg_trade_pct": g("Avg P&L", "all_pct"),
        "avg_win": g("Avg winning trade"), "avg_loss": g("Avg losing trade"),
        "win_loss_ratio": g("Ratio avg win / avg loss"),
        "largest_win": g("Largest winning trade"), "largest_loss": g("Largest losing trade"),
        "avg_bars_in_trade": g("Avg # bars in trades"),
        "sharpe": g("Sharpe ratio"), "sortino": g("Sortino ratio"),
        "profit_factor": g("Profit factor"), "margin_calls": g("Margin calls"),
        "long": {"net_profit": g("Net profit", "long"), "trades": g("Total trades", "long"),
                 "percent_profitable": g("Percent profitable", "long_pct"), "profit_factor": g("Profit factor", "long")},
        "short": {"net_profit": g("Net profit", "short"), "trades": g("Total trades", "short"),
                  "percent_profitable": g("Percent profitable", "short_pct"), "profit_factor": g("Profit factor", "short")},
    }
    if trades:
        closed = [t for t in trades if t.get("profit") is not None]
        exits = {}
        for t in closed:
            exits[t.get("exit_signal") or t.get("exit_type")] = exits.get(t.get("exit_signal") or t.get("exit_type"), 0) + 1
        summary["exit_reasons"] = exits
        summary["first_trade"] = closed[0]["entry_time"] if closed else None
        summary["last_trade"] = closed[-1]["exit_time"] if closed else None
        streak = worst = 0
        for t in closed:
            streak = streak + 1 if (t["profit"] or 0) <= 0 else 0
            worst = max(worst, streak)
        summary["max_consecutive_losses"] = worst
    return summary


def print_report(summary: dict, trades: list[dict], show_trades: int, strat_path: Path, trade_path: Path | None) -> None:
    cur = summary["currency"]
    print(f"Backtest summary  ({strat_path})")
    print("-" * 72)
    print(f"Net profit         {fmt(summary['net_profit']):>14} {cur}   ({fmt(summary['net_profit_pct'], pct=True)})")
    print(f"Buy & hold         {fmt(summary['buy_hold_return']):>14} {cur}   ({fmt(summary['buy_hold_return_pct'], pct=True)})")
    print(f"Max drawdown       {fmt(summary['max_drawdown']):>14} {cur}   ({fmt(summary['max_drawdown_pct'], pct=True)})")
    print(f"Profit factor      {fmt(summary['profit_factor']):>14}      Sharpe {fmt(summary['sharpe'])}   Sortino {fmt(summary['sortino'])}")
    print(f"Trades             {fmt(summary['total_trades'], digits=0):>14}      win rate {fmt(summary['percent_profitable'], pct=True)}"
          f"   W/L {fmt(summary['winning_trades'], digits=0)}/{fmt(summary['losing_trades'], digits=0)}")
    print(f"Avg trade          {fmt(summary['avg_trade']):>14} {cur}   ({fmt(summary['avg_trade_pct'], pct=True)})"
          f"   avg win {fmt(summary['avg_win'])}  avg loss {fmt(summary['avg_loss'])}")
    print(f"Largest win/loss   {fmt(summary['largest_win']):>14} / {fmt(summary['largest_loss'])}"
          f"   avg bars in trade {fmt(summary['avg_bars_in_trade'], digits=1)}")
    print(f"Commission paid    {fmt(summary['commission_paid']):>14} {cur}")
    print(f"Long  : net {fmt(summary['long']['net_profit'])}  trades {fmt(summary['long']['trades'], digits=0)}"
          f"  win rate {fmt(summary['long']['percent_profitable'], pct=True)}  PF {fmt(summary['long']['profit_factor'])}")
    print(f"Short : net {fmt(summary['short']['net_profit'])}  trades {fmt(summary['short']['trades'], digits=0)}"
          f"  win rate {fmt(summary['short']['percent_profitable'], pct=True)}  PF {fmt(summary['short']['profit_factor'])}")
    print("-" * 72)
    warnings = []
    if (summary.get("margin_calls") or 0) > 0:
        warnings.append(f"{int(summary['margin_calls'])} margin call(s): position sizing is too large for the margin settings")
    if (summary.get("open_trades") or 0) > 0:
        warnings.append(f"{int(summary['open_trades'])} trade(s) still open at the end of the data (not in the closed statistics)")
    if (summary.get("total_trades") or 0) < 30:
        warnings.append("fewer than 30 trades: statistics are not reliable")
    if summary.get("commission_paid") == 0:
        warnings.append("commission paid is 0: set commission_type / commission_value for realism")
    if summary.get("max_consecutive_losses"):
        warnings.append(f"max consecutive losses: {summary['max_consecutive_losses']}")
    for w in warnings:
        print(f"! {w}")
    if summary.get("exit_reasons"):
        print("Exit reasons: " + ", ".join(f"{k}={v}" for k, v in summary["exit_reasons"].items()))
    if trades and show_trades > 0:
        closed = [t for t in trades if t.get("profit") is not None]
        head = closed[:show_trades]
        tail = closed[-show_trades:] if len(closed) > 2 * show_trades else []
        print()
        print(f"Trades ({trade_path}):")
        print(f"{'#':>4} {'side':<5} {'entry':<20} {'exit':<20} {'entry px':>12} {'exit px':>12} {'P&L':>12} {'P&L %':>8}  exit")
        for t in head + ([{"num": "..."}] if tail else []) + tail:
            if t.get("num") == "...":
                print("  ...")
                continue
            print(f"{t['num']:>4} {t.get('side', ''):<5} {str(t.get('entry_time', ''))[:19]:<20} {str(t.get('exit_time', ''))[:19]:<20} "
                  f"{fmt(t.get('entry_price'), digits=4):>12} {fmt(t.get('exit_price'), digits=4):>12} "
                  f"{fmt(t.get('profit')):>12} {fmt(t.get('profit_pct'), pct=True):>8}  {t.get('exit_signal') or t.get('exit_type') or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="<script>_strat.csv, <script>_trade.csv or the base path without suffix")
    parser.add_argument("--trades", type=int, default=5, help="show the first/last N trades (default 5, 0 to hide)")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args()

    strat_path, trade_path = resolve_paths(args.path)
    if not strat_path:
        print(f"No statistics CSV found for {args.path}. Run the strategy first (pyne run ...).", file=sys.stderr)
        return 1
    stats = read_stats(strat_path)
    with strat_path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh), [])
    currency = header[1].replace("All", "").strip() if len(header) > 1 else ""
    trades = read_trades(trade_path) if trade_path else []
    summary = build_summary(stats, trades, currency)
    if args.json:
        summary["trades"] = trades
        print(json.dumps(summary, indent=2, default=str))
    else:
        print_report(summary, trades, args.trades, strat_path, trade_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
