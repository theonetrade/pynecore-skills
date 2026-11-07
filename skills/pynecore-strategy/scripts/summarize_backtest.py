#!/usr/bin/env python3
"""
Summarize a PyneCore backtest from its CSV outputs.

Standard library only. Point it at the statistics file written by `pyne run`
(`output/<script>_strat.csv`) or by a standalone run (`<script>_strat.csv`), at the trade list
(`<script>_trade.csv` / `<script>_trades.csv`), or at the base path; the companion file next to
it is picked up automatically. With only the trade list available, the metrics that can be
derived from trades are computed and the rest is reported as unavailable.

    python3 summarize_backtest.py output/my_strategy_strat.csv
    python3 summarize_backtest.py output/my_strategy --trades 3
    python3 summarize_backtest.py output/my_strategy_strat.csv --json
    python3 summarize_backtest.py output/my_strategy --metric pnl                 # prints one number
    python3 summarize_backtest.py output/my_strategy --metric pnl,max_drawdown_pct,trades

The report: headline metrics, long/short split, a sanity block (margin calls, open trades, trade
count, zero commission, loss streak), exit reasons, and a few trades.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

METRIC_ALIASES = {
    "pnl": "net_profit", "net": "net_profit", "profit": "net_profit",
    "pnl_pct": "net_profit_pct", "net_pct": "net_profit_pct",
    "dd": "max_drawdown", "drawdown": "max_drawdown", "maxdd": "max_drawdown",
    "dd_pct": "max_drawdown_pct", "drawdown_pct": "max_drawdown_pct", "maxdd_pct": "max_drawdown_pct",
    "runup": "max_runup", "runup_pct": "max_runup_pct",
    "pf": "profit_factor",
    "winrate": "percent_profitable", "win_rate": "percent_profitable",
    "trades": "total_trades", "wins": "winning_trades", "losses": "losing_trades",
    "avg": "avg_trade", "commission": "commission_paid", "bh": "buy_hold_return", "bh_pct": "buy_hold_return_pct",
}
METRIC_KEYS = (
    "net_profit", "net_profit_pct", "gross_profit", "gross_loss", "commission_paid",
    "buy_hold_return", "buy_hold_return_pct", "max_drawdown", "max_drawdown_pct", "max_runup", "max_runup_pct",
    "total_trades", "open_trades", "winning_trades", "losing_trades", "percent_profitable",
    "avg_trade", "avg_trade_pct", "avg_win", "avg_loss", "win_loss_ratio", "largest_win", "largest_loss",
    "avg_bars_in_trade", "sharpe", "sortino", "profit_factor", "margin_calls", "max_consecutive_losses",
)


STATS_MARKERS = ("Net profit", "Total closed trades", "Gross profit", "Max equity drawdown")


def resolve_paths(arg: str) -> tuple[Path | None, Path | None]:
    p = Path(arg)
    if p.suffix == ".csv":
        stem = p.name
        for suffix in ("_strat.csv", "_trade.csv", "_trades.csv"):
            if stem.endswith(suffix):
                base = p.with_name(stem[: -len(suffix)])
                break
        else:
            base = p.with_suffix("")
    else:
        base = p
    strat = base.with_name(base.name + "_strat.csv")
    trade = next((c for c in (base.with_name(base.name + "_trade.csv"), base.with_name(base.name + "_trades.csv")) if c.is_file()), None)
    return (strat if strat.is_file() else None), trade


def to_num(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def read_stats(path: Path) -> tuple[dict[str, dict[str, float | None]], str]:
    """{metric: {"all", "all_pct", "long", "long_pct", "short", "short_pct"}}, currency."""
    stats: dict[str, dict[str, float | None]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None) or []
        for row in reader:
            if not row or not row[0].strip():
                continue
            cells = row + [""] * (7 - len(row))
            stats[row[0].strip()] = {
                "all": to_num(cells[1]), "all_pct": to_num(cells[2]),
                "long": to_num(cells[3]), "long_pct": to_num(cells[4]),
                "short": to_num(cells[5]), "short_pct": to_num(cells[6]),
            }
    if not any(marker in stats for marker in STATS_MARKERS):
        raise SystemExit(f"{path}: {'empty file' if not header else 'not a PyneCore statistics CSV'} (expected rows such as "
                         f"'Net profit' and 'Total closed trades', as `pyne run` writes to output/<script>_strat.csv)")
    currency = header[1].replace("All", "").strip() if len(header) > 1 else ""
    return stats, currency


def read_trades(path: Path) -> tuple[list[dict], str]:
    """One record per trade number (an entry row and an exit row; a partial exit gets its own number)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames or []
    if not fields or not {"Trade #", "Type"} <= set(fields) or not any(f.startswith("Price") for f in fields):
        raise SystemExit(f"{path}: {'empty file' if not fields else 'not a PyneCore trade CSV'} (expected the columns "
                         f"'Trade #', 'Type', 'Price ...', as `pyne run` writes to output/<script>_trade.csv)")
    if not rows:
        return [], ""                                # a strategy without trades: header only
    columns = list(rows[0].keys())
    price_key = next((k for k in columns if k.startswith("Price")), "Price")
    profit_key = next((k for k in columns if k.startswith("Profit") and "%" not in k), "Profit")
    cum_key = next((k for k in columns if k.startswith("Cumulative profit") and "%" not in k), "Cumulative profit")
    currency = price_key.replace("Price", "").strip()
    by_num: dict[str, dict] = {}
    for row in rows:
        num = (row.get("Trade #") or "").strip()
        rec = by_num.setdefault(num, {"num": num})
        kind = row.get("Type", "") or ""
        if kind.startswith("Entry"):
            rec.update({
                "side": "long" if "long" in kind.lower() else "short",
                "entry_time": row.get("Date/Time"), "entry_price": to_num(row.get(price_key)),
                "entry_bar": to_num(row.get("Bar Index")), "signal": row.get("Signal"),
                "contracts": to_num(row.get("Contracts")),
            })
        else:
            rec.update({
                "exit_type": kind, "exit_signal": row.get("Signal"),
                "exit_time": row.get("Date/Time"), "exit_price": to_num(row.get(price_key)),
                "exit_bar": to_num(row.get("Bar Index")),
                "profit": to_num(row.get(profit_key)), "profit_pct": to_num(row.get("Profit %")),
                "cum_profit": to_num(row.get(cum_key)),
                "runup_pct": to_num(row.get("Run-up %")), "drawdown_pct": to_num(row.get("Drawdown %")),
            })
    trades = [t for t in by_num.values() if t.get("num")]
    trades.sort(key=lambda t: float(t["num"]) if t["num"].replace(".", "", 1).isdigit() else 0)
    return trades, currency


def trade_derived(trades: list[dict]) -> dict:
    # pyne writes a position still open at the end as a synthetic exit row with the signal "Open"
    # (its profit is unrealized): not a closed trade
    closed = [t for t in trades if t.get("profit") is not None and (t.get("exit_signal") or "") != "Open"]
    open_at_end = sum(1 for t in trades if (t.get("exit_signal") or "") == "Open")
    exits: dict[str, int] = {}
    streak = worst = 0
    peak = equity = dd = 0.0
    for t in closed:
        reason = t.get("exit_signal") or t.get("exit_type") or "?"
        exits[reason] = exits.get(reason, 0) + 1
        streak = streak + 1 if (t["profit"] or 0) <= 0 else 0
        worst = max(worst, streak)
        equity += t["profit"] or 0.0
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {
        "closed": closed, "exit_reasons": exits, "max_consecutive_losses": worst,
        "drawdown_from_trades": dd,
        "open_at_end": open_at_end,
        "first_trade": closed[0]["entry_time"] if closed else None,
        "last_trade": closed[-1]["exit_time"] if closed else None,
    }


def summary_from_stats(stats: dict, trades: list[dict], currency: str) -> dict:
    def g(metric: str, col: str = "all"):
        return stats.get(metric, {}).get(col)

    s = {
        "source": "statistics", "currency": currency,
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
        "max_consecutive_losses": None,
    }
    if trades:
        d = trade_derived(trades)
        s.update({k: d[k] for k in ("exit_reasons", "max_consecutive_losses", "first_trade", "last_trade")})
    return s


def summary_from_trades(trades: list[dict], currency: str) -> dict:
    """Everything the trade list alone can tell; equity-curve metrics stay None."""
    d = trade_derived(trades)
    closed = d["closed"]
    wins = [t for t in closed if t["profit"] > 0]
    losses = [t for t in closed if t["profit"] <= 0]
    gross_profit = sum(t["profit"] for t in wins)
    gross_loss = -sum(t["profit"] for t in losses)

    def side(name: str) -> dict:
        sub = [t for t in closed if t.get("side") == name]
        sw = [t for t in sub if t["profit"] > 0]
        sl = -sum(t["profit"] for t in sub if t["profit"] <= 0)
        return {"net_profit": sum(t["profit"] for t in sub), "trades": len(sub),
                "percent_profitable": (len(sw) / len(sub) * 100) if sub else None,
                "profit_factor": (sum(t["profit"] for t in sw) / sl) if sl > 0 else None}

    return {
        "source": "trades", "currency": currency,
        "net_profit": gross_profit - gross_loss, "net_profit_pct": None,
        "gross_profit": gross_profit, "gross_loss": gross_loss, "commission_paid": None,
        "buy_hold_return": None, "buy_hold_return_pct": None,
        "max_drawdown": d["drawdown_from_trades"], "max_drawdown_pct": None, "max_runup": None, "max_runup_pct": None,
        "total_trades": len(closed), "open_trades": d["open_at_end"],
        "winning_trades": len(wins), "losing_trades": len(losses),
        "percent_profitable": (len(wins) / len(closed) * 100) if closed else None,
        "avg_trade": ((gross_profit - gross_loss) / len(closed)) if closed else None, "avg_trade_pct": None,
        "avg_win": (gross_profit / len(wins)) if wins else None, "avg_loss": (-gross_loss / len(losses)) if losses else None,
        "win_loss_ratio": ((gross_profit / len(wins)) / (gross_loss / len(losses))) if wins and losses and gross_loss else None,
        "largest_win": max((t["profit"] for t in closed), default=None), "largest_loss": min((t["profit"] for t in closed), default=None),
        "avg_bars_in_trade": (sum((t["exit_bar"] or 0) - (t["entry_bar"] or 0) for t in closed) / len(closed)) if closed else None,
        "sharpe": None, "sortino": None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None, "margin_calls": None,
        "long": side("long"), "short": side("short"),
        "exit_reasons": d["exit_reasons"], "max_consecutive_losses": d["max_consecutive_losses"],
        "first_trade": d["first_trade"], "last_trade": d["last_trade"],
    }


def fmt(value, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value:.{digits}f}%"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.{digits}f}"


def print_report(s: dict, trades: list[dict], show_trades: int, strat_path: Path | None, trade_path: Path | None) -> None:
    cur = s["currency"]
    print(f"Backtest summary  ({strat_path or trade_path})")
    if s["source"] == "trades":
        print("! statistics file not found: metrics computed from the trade list; Sharpe, Sortino, buy & hold,\n"
              "  commission, open trades and percent drawdown need <script>_strat.csv")
    print("-" * 72)
    print(f"Net profit         {fmt(s['net_profit']):>14} {cur}   ({fmt(s['net_profit_pct'], pct=True)})")
    print(f"Buy & hold         {fmt(s['buy_hold_return']):>14} {cur}   ({fmt(s['buy_hold_return_pct'], pct=True)})")
    print(f"Max drawdown       {fmt(s['max_drawdown']):>14} {cur}   ({fmt(s['max_drawdown_pct'], pct=True)})"
          + ("   [closed-trade equity curve]" if s["source"] == "trades" else ""))
    print(f"Max run-up         {fmt(s['max_runup']):>14} {cur}   ({fmt(s['max_runup_pct'], pct=True)})")
    print(f"Profit factor      {fmt(s['profit_factor']):>14}      Sharpe {fmt(s['sharpe'])}   Sortino {fmt(s['sortino'])}")
    print(f"Trades             {fmt(s['total_trades'], digits=0):>14}      win rate {fmt(s['percent_profitable'], pct=True)}"
          f"   W/L {fmt(s['winning_trades'], digits=0)}/{fmt(s['losing_trades'], digits=0)}")
    print(f"Avg trade          {fmt(s['avg_trade']):>14} {cur}   ({fmt(s['avg_trade_pct'], pct=True)})"
          f"   avg win {fmt(s['avg_win'])}  avg loss {fmt(s['avg_loss'])}")
    print(f"Largest win/loss   {fmt(s['largest_win']):>14} / {fmt(s['largest_loss'])}"
          f"   avg bars in trade {fmt(s['avg_bars_in_trade'], digits=1)}")
    print(f"Commission paid    {fmt(s['commission_paid']):>14} {cur}")
    for name in ("long", "short"):
        side = s[name]
        print(f"{name.capitalize():<5} : net {fmt(side['net_profit'])}  trades {fmt(side['trades'], digits=0)}"
              f"  win rate {fmt(side['percent_profitable'], pct=True)}  PF {fmt(side['profit_factor'])}")
    print("-" * 72)
    warnings = []
    if (s.get("margin_calls") or 0) > 0:
        warnings.append(f"{int(s['margin_calls'])} margin call(s): position sizing is too large for the margin settings")
    if (s.get("open_trades") or 0) > 0:
        warnings.append(f"{int(s['open_trades'])} trade(s) still open at the end of the data (not in the closed statistics)")
    if (s.get("total_trades") or 0) < 30:
        warnings.append("fewer than 30 trades: statistics are not reliable")
    if s.get("commission_paid") == 0:
        warnings.append("commission paid is 0: set commission_type / commission_value for realism")
    if s.get("max_consecutive_losses"):
        warnings.append(f"max consecutive losses: {s['max_consecutive_losses']}")
    for w in warnings:
        print(f"! {w}")
    if s.get("exit_reasons"):
        print("Exit reasons: " + ", ".join(f"{k}={v}" for k, v in s["exit_reasons"].items()))
    if s.get("first_trade"):
        print(f"Period: {str(s['first_trade'])[:19]} -> {str(s['last_trade'])[:19]}")
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


def resolve_metric(name: str) -> str | None:
    key = name.strip().lower()
    key = METRIC_ALIASES.get(key, key)
    return key if key in METRIC_KEYS else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("path", metavar="PATH",
                        help="what to summarize, one of:\n"
                             "  output/NAME_strat.csv     statistics file written by pyne run\n"
                             "  NAME_strat.csv            statistics file written by a standalone run (python NAME.py data)\n"
                             "  NAME_trade.csv | NAME_trades.csv   the trade list; its _strat.csv is looked up next to it\n"
                             "  output/NAME | NAME        base path without suffix; the suffixes above are tried\n"
                             "With only the trade list present, trade-derived metrics are computed; Sharpe, Sortino,\n"
                             "buy & hold, commission, open trades and percent drawdown are then reported as '-'.")
    parser.add_argument("--trades", type=int, default=5, metavar="N",
                        help="list the first N and the last N closed trades (integer >= 0; 0 hides the trade table).\n"
                             "Default: 5")
    parser.add_argument("--metric", metavar="NAME[,NAME...]",
                        help="print only these metrics as plain numbers, one `name value` per line (just the value when\n"
                             "a single metric is asked), for scripting. Names:\n"
                             "  " + ", ".join(METRIC_KEYS[:10]) + ",\n"
                             "  " + ", ".join(METRIC_KEYS[10:20]) + ",\n"
                             "  " + ", ".join(METRIC_KEYS[20:]) + "\n"
                             "Aliases: pnl, pnl_pct, dd, dd_pct, runup, pf, winrate, trades, wins, losses, avg, commission,\n"
                             "bh, bh_pct. Unavailable values print as 'na'. Example: --metric pnl,max_drawdown_pct,trades")
    parser.add_argument("--json", action="store_true",
                        help="print the summary as one JSON object (metrics, long/short split, exit reasons, parsed trades)")
    args = parser.parse_args()

    strat_path, trade_path = resolve_paths(args.path)
    if not strat_path and not trade_path:
        print(f"No statistics or trade CSV found for {args.path}. Run the strategy first (pyne run ...).", file=sys.stderr)
        return 1
    trades, trade_currency = read_trades(trade_path) if trade_path else ([], "")
    if strat_path:
        stats, currency = read_stats(strat_path)
        summary = summary_from_stats(stats, trades, currency or trade_currency)
    else:
        summary = summary_from_trades(trades, trade_currency)

    if args.metric:
        names = [n for n in args.metric.split(",") if n.strip()]
        keys = [resolve_metric(n) for n in names]
        bad = [n for n, k in zip(names, keys) if k is None]
        if bad:
            print(f"unknown metric(s): {', '.join(bad)}. Valid: {', '.join(METRIC_KEYS)} plus aliases, see --help", file=sys.stderr)
            return 2
        for key in keys:
            value = summary.get(key)
            text = "na" if value is None else (f"{value:.10g}" if isinstance(value, float) else str(value))
            print(text if len(keys) == 1 else f"{key} {text}")
        return 0

    if args.json:
        summary["trades"] = trades
        print(json.dumps(summary, indent=2, default=str))
    else:
        print_report(summary, trades, args.trades, strat_path, trade_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
