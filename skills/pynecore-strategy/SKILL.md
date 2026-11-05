---
name: pynecore-strategy
description: Design, write, backtest, debug, optimize and deploy trading strategies and indicators as PyneCore (Pyne) Python scripts, and run them through the `pyne` CLI or the `ScriptRunner` API. Use this skill whenever a task involves PyneCore, Pyne code, a `@pyne` docstring, `pynecore.lib`, `ta.*` / `strategy.*` calls in Python, `pyne run`, `.ohlcv` data files, a PyneCore workdir (`scripts/`, `data/`, `output/`), backtests or parameter sweeps of a Python trading strategy, `request.security` multi-timeframe logic, live CCXT feeds through PyneCore, or FreqTrade integration with PyneCore scripts, even when the user just says "write me a strategy", "backtest this idea", "why does my strategy not trade" or "add a stop loss" in a project that contains PyneCore files.
---

# PyneCore strategy development

PyneCore runs **Pyne code**: Python files whose docstring starts with `@pyne`, executed bar by
bar with series history, persistent state and a broker emulator. This skill is self-contained
and installed globally: it needs no per-project configuration files. Everything project-specific
(interpreter, CLI, workdir, data) is discovered at the start of a task.

## 1. Discover the environment first

Run the bundled probe (standard library only) from the project root and read its output:

```bash
python3 <skill>/scripts/pyne_env.py            # add --json for machine-readable output
```

It reports the workdir (the folder holding the `.pyne` marker, or a `workdir/` child), the
interpreter that can import `pynecore` and its version, the `pyne` CLI path, existing scripts
and data files with their symbol/timeframe, and any locally bundled PyneCore documentation.

Rules that follow from it:

- Call the CLI by its resolved path (`<venv>/bin/pyne`, or `uv run pyne` in a uv project) and
  run Python tooling with the same interpreter. The agent shell does not have the venv active.
- Always set `PYNE_NO_LOGO=1` (and `NO_COLOR=1` when parsing output) on CLI calls.
- No workdir: create one non-interactively
  (`mkdir -p workdir/scripts/lib workdir/data workdir/output workdir/config && printf 'workdir_version = "1.0"\n' > workdir/.pyne`)
  or pass `--workdir`. No PyneCore: `pip install "pynesys-pynecore[cli,ccxt]"` (or `uv add ...`).
- No data: `pyne --recreate-demo plugin list` creates `data/demo.ohlcv` (2000 synthetic daily
  bars) for smoke tests; real data comes from `pyne data download ccxt:BYBIT:BTC/USDT:USDT@1D` or
  `pyne data convert-from file.csv`. **Open the generated `data/<name>.toml` and check
  `mintick`, `pointvalue`, `mincontract`, `basecurrency`, `timezone`** before trusting results.
- If the probe lists a local documentation folder, consult it for details this skill does not
  cover; otherwise the `references/` of this skill are the documentation.

## 2. Workflow for a strategy task

1. **Pin down the idea** in one paragraph: instrument(s) and timeframe, entry rule, exit rule
   (stop, target, trailing, time, signal), position sizing, filters (trend, session, dates),
   long/short permissions, costs. Ask only when the ambiguity changes the code materially;
   otherwise choose sensible defaults and state them.
2. **Scaffold**: `python3 <skill>/scripts/new_script.py <name> --kind strategy --title "..."`
   copies `templates/strategy_template.py` into `scripts/`. For a one-off experiment outside a
   workdir, copy the template anywhere; it is standalone-runnable (`python my.py data.csv`).
3. **Write the script** following the structure `inputs -> indicators (every bar) -> filters ->
   signals -> sizing -> orders -> exits -> outputs`. Apply the language rules in section 3.
4. **Run it** on data (section 4). A syntax check is not a result; the first run also writes
   `scripts/<name>.toml` with every input and setting.
5. **Verify** (section 5): trade count, costs, warm-up, fills, no lookahead, numbers from the
   statistics CSV. Fix and rerun until the behaviour matches the idea.
6. **Report**: script path, data and date range, settings used, key metrics, what was checked,
   what remains unverified (for example anything that needs live data or a chart renderer).

For indicators the same flow applies without the order/exit steps; for libraries use
`--kind library` and import with `from lib.<publisher>.<name>.v1 import ...`.

## 3. Language essentials (the rules that break hand-written code)

Full detail: [references/language.md](references/language.md).

- **File**: first statement is a docstring starting with `@pyne`; imports from `pynecore.lib`
  (`script, input, strategy, ta, close, high, low, volume, time, plot, color, na, nz, log`) and
  `pynecore.types` (`Series, Persistent, IBPersistent`); one `main()` decorated with
  `@script.strategy(...)`, `@script.indicator(...)` or `@script.library(...)`; inputs are
  `main()` parameters with `input.*()` defaults; optional `return {"Name": value}` adds plot
  columns; optional `if __name__ == "__main__": from pynecore.standalone import run; run(__file__)`.
- **One execution per bar.** `main()` runs once per bar with the built-in series set to that
  bar. There is no loop over bars in your code.
- **`Series[T]`** gives history: `x: Series[float] = ...` then `x[1]`. Built-ins (`close[1]`)
  are already series. Annotate at the first assignment and assign every bar. `x[-1]` is `na`.
  Index named variables, never call expressions: `ta.highest(high, 20)[1]` raises
  `TypeError: 'float' object is not subscriptable`; write `hh: Series[float] = ta.highest(high, 20)`
  then `hh[1]`.
- **`Persistent[T]`** keeps a value across bars (initialized once); declare it inside the
  function that uses it. `IBPersistent[T]` additionally survives intra-bar re-execution.
- **Stateful calls are per call site and must run every bar.** Compute every `ta.*` value at the
  top of `main()` unconditionally, then combine: `cross = ta.crossover(a, b)` first, `if ready
  and cross:` after. Never put a `ta.*` call behind `if`, after `and`/`or`, or inside a
  conditional expression that varies bar to bar. A call site in a loop keeps one shared state.
- **`na`**: numeric `na` is `nan`. Test with `na(x)`, replace with `nz(x, v)`, hold with
  `fixnan(x)`. Comparisons with `na` are `False`; guard signals with `not na(...)` for warm-up.
  Outside Pyne code (your Python consumer) use `math.isnan`.
- **Ints are floats** (`bar_index` is `12.0`); format with `string.tostring` or `:.0f`.
- **Module-level objects are read-only inside functions**; state goes in `Persistent`.
- **Plot-family calls (`plot`, `plotshape`, `bgcolor`, `fill`, `hline`, ...) are unconditional
  and live in `main()`'s body**; make the value conditional (`plot(x if cond else na, "X")`).
- The string namespace is `string`; arrays are Python lists (`array.*` or list methods).

## 4. Strategy essentials and running

Full detail: [references/strategy-api.md](references/strategy-api.md),
[references/cli-and-data.md](references/cli-and-data.md),
[references/programmatic.md](references/programmatic.md).

- **Set sizing and costs explicitly** in the decorator: `default_qty_type=strategy.percent_of_equity,
  default_qty_value=10, commission_type=strategy.commission.percent, commission_value=0.05,
  slippage=1`. The default (100 % of equity, no commission) produces margin calls and fantasy
  results. With `margin_long/margin_short=100` a position's notional cannot exceed equity:
  cap risk-based quantities (`qty = math.min(qty, strategy.equity * 0.98 / (close * syminfo.pointvalue))`)
  or lower the margin settings to model leverage.
- **Fills**: market orders fill at the **next bar's open**; `strategy.exit` stop/limit orders
  fill when a later bar reaches the price (`profit`/`loss`/`trail_*` are in ticks, `limit`/`stop`
  are prices). Issue the protective `strategy.exit` on the signal bar together with the entry
  and re-issue it every bar in position. `strategy.position_size` reflects fills from earlier
  bars. An opposite `strategy.entry` reverses the position.
- **Running**:

  ```bash
  PYNE_NO_LOGO=1 <pyne> run <script> <data> [--from 2023-01-01 --to 2024-12-31] [--timeframe 60] [--security "1D=name"]
  <python> <skill>/scripts/run_backtest.py <script.py> <data.ohlcv|csv> [--input k=v] [--setting k=v] [--sweep k=a:b:step] [--json]
  ```

  `pyne run` writes `output/<script>.csv` (plots), `_strat.csv` (statistics), `_trade.csv`
  (trades). `run_backtest.py` prints a compact summary, accepts overrides without touching the
  script's `.toml`, converts CSV on the fly and sweeps parameters. `--timeframe` with finer data
  enables the bar magnifier for exact intrabar fills.
- **Overrides**: `scripts/<name>.toml` (`[script]` settings, `[inputs.<param>] value =`) or
  `ScriptRunner(inputs={param: v}, settings={setting: v})`. Keys are parameter / decorator
  names, unknown keys raise.
- **Programmatic**: `ScriptRunner(script_path, ohlcv_iter, syminfo, ...)` yields
  `(candle, plot_data)` or `(candle, plot_data, new_trades)` per bar; `OHLCV` timestamps are
  milliseconds; `SymInfo.load_toml()` or build one by hand. Materialize bars in a list for
  reuse.

## 5. Verify before reporting

Use `python3 <skill>/scripts/summarize_backtest.py output/<script>_strat.csv` for a report
with warnings, then check:

1. Data `.toml` reviewed; date range and bar count as intended.
2. Commission and slippage non-zero; sizing explicit; **0 margin calls**.
3. Trades start after warm-up; no `na` leaks into signals.
4. No lookahead: no bare values from `request.security(..., lookahead_on)`, breakout levels use
   `[1]`, signals from the current close trade on the next open.
5. Two or three trades spot-checked in `_trade.csv` against `_plot` values (entry at next open,
   exits at the expected level, `Signal` column shows the intended exit reason).
6. Enough trades to mean anything (dozens); result survives neighbouring parameters and another
   period; keep an out-of-sample period untouched when optimizing.

Report what was run and what was checked. Never present a syntax check or an unverified
optimization as a validated strategy. See [references/pitfalls.md](references/pitfalls.md) for
symptom-to-fix tables.

## 6. Reference map

| Need | Read |
|------|------|
| Language rules, Series/Persistent, isolation, na, libraries, UDTs | `references/language.md` |
| `@script.strategy` settings, `strategy.*` orders/variables/risk, fills, statistics CSV | `references/strategy-api.md` |
| Inputs, the script `.toml`, overrides, env vars | `references/inputs-and-settings.md` |
| All 67 `ta.*` functions with signatures | `references/ta.md` |
| Bar data, time/session functions, `math`, `array`/`map`, `string`, `barstate`, `timeframe`, `syminfo`, plots, drawings, `log` | `references/lib.md` |
| Workdir, `pyne run` options, data download/convert/aggregate, symbol `.toml`, bar magnifier, outputs | `references/cli-and-data.md` |
| `ScriptRunner`, `OHLCV`, `SymInfo`, standalone scripts, trades, sweeps | `references/programmatic.md` |
| `request.security`, lookahead modes, security data, symbol map | `references/multi-timeframe.md` |
| Live mode, broker plugins, DataFrame bridge, FreqTrade, notifications | `references/live-and-integrations.md` |
| Entry/exit/sizing/filter/state snippets, validation checklist, archetypes | `references/patterns.md` |
| Symptom -> cause -> fix tables | `references/pitfalls.md` |

## 7. Bundled tools and templates

| File | Use |
|------|-----|
| `scripts/pyne_env.py` | Discover workdir, interpreter, CLI, scripts, data, local docs (stdlib) |
| `scripts/new_script.py` | Scaffold a strategy / indicator / library into the workdir (stdlib) |
| `scripts/run_backtest.py` | Backtest with overrides, date range, CSV conversion, sweeps, JSON (needs the project interpreter) |
| `scripts/summarize_backtest.py` | Turn `_strat.csv` + `_trade.csv` into a short report with warnings (stdlib) |
| `templates/strategy_template.py` | Complete strategy: EMA cross + trend filter, ATR stop/target, risk sizing, trailing, time stop, plots, logs |
| `templates/indicator_template.py` | Indicator with plots, markers, hlines, fill, returned dict |
| `templates/library_template.py` | Shared stateful helpers under `scripts/lib/` |
| `templates/pynecore_bridge.py` | pandas DataFrame -> OHLCV, run indicator/strategy, back to Series |
| `templates/freqtrade_indicators_strategy.py`, `templates/freqtrade_signals_strategy.py` | The two FreqTrade integration patterns |
| `templates/live_ccxt.py` | Live candles from an exchange through CCXT in live mode |
| `templates/custom_feed.py` | Any Python iterable of OHLCV as the data source |

Templates are tested against PyneCore 6.12. When the installed version differs, check
`pyne run --help` and `runner.script.settable_fields()` before using a setting or flag you have
not seen in this skill.
