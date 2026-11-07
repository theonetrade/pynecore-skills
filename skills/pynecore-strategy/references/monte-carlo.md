# Monte Carlo robustness tests (`scripts/monte_carlo.py`)

PyneCore has no Monte Carlo of its own. Version 6.12.1 has no module, no `pyne` command and no
documentation page for it; the statistics CSV carries Sharpe and Sortino ratios, nothing
simulated. `scripts/monte_carlo.py` adds two families of tests on top of the files and the
`ScriptRunner` that PyneCore does provide. They answer different questions, so use both.

| Mode | Input | Runs on | Question |
|------|-------|---------|----------|
| `trades` | the trade CSV of a finished backtest | any Python 3.11+, stdlib only | how much of the equity curve was luck of the order and of the particular trades? |
| `prices` | the strategy script and its `.ohlcv` data | the project's interpreter (needs pynecore) | does the edge survive prices that are slightly or structurally different? |

## 1. Trade resampling (`trades`)

```bash
python3 <skill>/scripts/monte_carlo.py trades output/my_strategy_trade.csv            # 1000 shuffles
python3 <skill>/scripts/monte_carlo.py trades output/my_strategy --method bootstrap --runs 5000
python3 <skill>/scripts/monte_carlo.py trades output/my_strategy --skip 0.1          # + 10 % missed trades
python3 <skill>/scripts/monte_carlo.py trades output/my_strategy --sizing compound --dd-limit 25 --json
```

`PATH` is the trade CSV (`output/<script>_trade.csv` from `pyne run`, `--trade` of
`run_backtest.py`, `<script>_trades.csv` of a standalone run) or the common prefix. The
statistics CSV next to it (`<prefix>_strat.csv`) supplies the exact initial capital (net profit
divided by net profit %, which it stores unrounded); without it the capital is estimated from the
trade CSV's rounded cumulative-percent column (a note says so; `--capital` sets it). The
synthetic exit row of a position still open at the end (signal `Open`, unrealized profit) is left
out.

| Option | Values | Effect |
|--------|--------|--------|
| `--method` | `shuffle` (default) | the same trades in a random order: total profit, profit factor and win rate stay, drawdown and losing streaks vary |
| | `bootstrap` | as many trades drawn with replacement: every metric varies; a confidence range for what the same trade distribution can produce |
| `--skip P` | float in `[0, 1)` | after the method, drop each trade with probability `P` (missed fills, downtime) |
| `--sizing` | `fixed` (default) | each trade adds its P&L in money: fixed-quantity or cash sizing |
| | `compound` | each trade's return on the equity it was opened with is applied to the simulated equity: `percent_of_equity` sizing |
| `--runs N` | integer ≥ 1, default 1000 | simulations; 1000 shuffles of 360 trades take 0.2 s |
| `--seed N` | integer, default 42 | same seed, same output (JSON identical across runs) |
| `--dd-limit PCT` | float, default 20 | threshold of the "runs reaching ... drawdown" line |
| `--out CSV` | path | one row per simulation (trades, net %, final equity, max drawdown % and money, losing streak, win rate, profit factor) |
| `--json` | | one object: method, settings, initial capital, per-metric distribution, probabilities, notes |

## 2. Price perturbation (`prices`)

```bash
.venv/bin/python <skill>/scripts/monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --runs 100
.venv/bin/python <skill>/scripts/monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --method blocks --block 48
.venv/bin/python <skill>/scripts/monte_carlo.py prices scripts/my_strategy.py data/BTCUSDT_60.ohlcv --method windows --window 3000
```

Each run is a full backtest through the same `run_once()` as `run_backtest.py` (same metrics,
same `--input`, `--setting`, `--from`, `--to`, `--symbol`, `--provider`, `--timezone`); the
script's log output is silenced, progress goes to stderr. The original data is run first as the
reference. A run that raises (a script meeting prices it never saw) is counted, left out and
named in a note.

| `--method` | Parameter | What it generates |
|------------|-----------|-------------------|
| `noise` (default) | `--noise F` (default 0.1) | every open/high/low/close plus Gaussian noise with a standard deviation of `F` × the bar's high-low range; high and low re-taken as max/min, timestamps and volume kept. Another feed, tick noise |
| `blocks` | `--block BARS` (default 24) | a synthetic history: each bar as ratios to the previous close (open, high, low, close), random blocks of consecutive bars drawn with replacement and re-chained from the first close; original timestamps (sessions and time filters keep working), the blocks' volumes. Same local behaviour, another path |
| `windows` | `--window BARS` (default half the data) | random contiguous sub-periods; the original column stays empty because a sub-period is not comparable with the whole history |

## 3. Reading the report

```
Monte Carlo: shuffle, 1000 runs, 360 closed trades, sizing fixed, seed 42
initial capital 10,000.00 USDT
                   original         p5        p25     median        p75        p95   rank
Net profit %          -4.36      -4.36      -4.36      -4.36      -4.36      -4.36      -
Max drawdown %         5.34       4.49       4.91       5.31       5.75       6.50    51%
Losing streak            10          9         10         12         14         18    28%
...
Runs losing money             : 100.0 %
Runs reaching 20 % drawdown : 0.0 %
In 95 % of the orders the drawdown stays below 6.50 % (the actual order: 5.34 %, rank 51 %).
```

- **original**: the metric of the real backtest (for `trades`, recomputed from the trade list:
  closed trades only, drawdown on the closed-trade equity curve, exactly as `run_backtest.py`).
- **p5 … p95**: percentiles over the simulations (linear interpolation).
- **rank**: share of simulations at or below the original value; `-` when the method cannot
  change the metric (net profit under `shuffle`). A drawdown ranked near 0 % or a profit ranked
  near 100 % means the real history was among the luckiest outcomes: expect worse.
- **Runs losing money / reaching the drawdown limit**: the probabilities a sizing decision needs.

How to act on it:

- **Size for the shuffle p95 drawdown, not the backtest's.** The backtest shows one order of
  trades; `shuffle` p95 is what the same trades can do in an unlucky order (the losing streak
  column likewise: 18 in a row is plausible where the backtest showed 10).
- **`bootstrap` p5 of net profit above zero** is the minimum for calling an edge robust to which
  trades happened; the share of losing runs is the probability the edge is not there at all
  given this sample.
- **`noise`**: the median should stay close to the original. An original ranked above ~80–90 %
  of noisy runs is fitted to the exact prices of this feed (level-based entries, exact
  round-number stops).
- **`blocks`**: profit in most runs means the rules work on the market's behaviour, not on its
  one historical path. Many losing runs with a profitable original point to a regime or a
  single trend carrying the result.
- **`windows`**: the share of profitable windows says how much depends on the period chosen;
  a strategy that only wins on the full history is fragile.

## 4. Verified (PyneCore 6.12.1, `ema_cross` on hourly BTC/USDT, 10 340 bars, 2025-09 → 2026-11)

The strategy (10 % of equity per entry, ATR stop and target) made 360 closed trades plus one
open at the end; `run_backtest.py` reported net −4.36 %, max drawdown 534.55 (5.34 %), profit
factor 0.81.

| Test | Result |
|------|--------|
| `trades`, shuffle, 1000 runs, fixed | original recomputed: net −4.36 %, drawdown 5.34 % (equal to `run_backtest.py`); drawdown p5/p50/p95 4.49/5.31/6.50 %, original rank 51 %; losing streak p95 18 vs 10; net constant (no rank); open trade left out |
| `trades`, bootstrap, 2000 runs, compound | net p5/p50/p95 −8.38/−4.36/−0.23 %, 96.0 % of runs losing; drawdown p95 8.90 %, 10.4 % of runs reach 8 % |
| `trades`, shuffle + `--skip 0.1` | 314–332 trades per run, net p5/p95 −5.17/−2.64 % |
| `trades` without the statistics CSV | capital estimated 9 995.05 from the rounded cumulative %, with a note; `--capital 10000` exact |
| `prices --method noise`, 100 runs | 22 s; net p5/p50/p95 −6.24/−4.81/−3.28 %, original rank 74 %; trades 350–365; 100 % losing |
| `prices --method blocks --block 48`, 100 runs | 21 s; net p5/p95 −7.97/−0.11 %, 96 % losing; drawdown p95 8.80 % |
| `prices --method windows --window 3000`, 100 runs | 6 s; net p5/p95 −3.86/+0.94 %, 84 % of windows losing |
| generated bars (`noise`, `blocks` seeds 1–3) | 0 invalid OHLC bars, standard deviation of hourly log returns 0.0042–0.0047 vs 0.0045, timestamps unchanged; block paths end at 45 053, 118 838, 126 010 vs 76 530 |
| a script that raises on some perturbed runs | 11/20 runs kept, `9 run(s) failed and are left out, e.g. seed 44: RuntimeError ...` |
| same `--seed` twice | identical JSON |

The verdict for this strategy is consistent across all tests: it loses on almost every resampled
or perturbed history, so the backtest's loss is not bad luck.

## 5. Limits

- **Closed-trade drawdown.** Both modes measure drawdown at trade closes, a lower bound of the
  drawdown with open positions marked to market (the statistics CSV has that one).
- **Independence.** `shuffle` and `bootstrap` treat trades as exchangeable: streaks that come
  from market regimes are broken up, so clustered losses are under-represented. `blocks` keeps
  local structure inside each block; longer blocks keep more of it.
- **Sample size.** Below 30 closed trades the percentiles are rough (a note says so); a
  bootstrap of 20 trades mostly re-describes those 20 trades.
- **Sizing model.** `fixed` replays money P&L; for `percent_of_equity` strategies use
  `--sizing compound`, which replays each trade's return on equity (net profit under `shuffle`
  stays fixed either way; profit factor then varies slightly with the equity at each trade).
- **`blocks` price levels drift** (45 k to 126 k above): fixed quantities in contracts and
  absolute price thresholds behave differently on another level; prefer percentage sizing and
  relative rules for this test.
- **Security data.** `prices` passes no `--security` data: same-symbol higher-timeframe contexts
  are built from the perturbed chart bars, cross-symbol contexts are not supported in this mode.
- Neither mode replaces an untouched out-of-sample period or a parameter plateau
  (`run_backtest.py --sweep`); they measure luck and fragility of a result already chosen.
