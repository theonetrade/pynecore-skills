# Pitfalls: what goes wrong in hand-written PyneCore strategies, and the fix

Read this before declaring a strategy finished. Each entry: symptom, cause, fix.

## Script does not run or is not recognized

| Symptom | Cause | Fix |
|---------|-------|-----|
| Script runs as plain Python / "no main function" | Missing or malformed `@pyne` marker | First statement must be a docstring whose first token is `@pyne` |
| `SyntaxError: ... modified inside a function` | Writing to a module-level list/dict/object from `main()` or a helper | Make it a `Persistent[...]` inside the function |
| `SyntaxError` naming `plot`/`bgcolor`/`plotshape` | Plot-family call inside `if`, loop, helper or conditional expression | Call unconditionally in `main()`; pass `na` as the value to hide |
| `ValueError: unknown input` from `ScriptRunner(inputs=...)` | Key is the input title, not the parameter name | Use the `main()` parameter name |
| `ValueError` about settings | Typo in a decorator setting key | Check `runner.script.settable_fields()` |
| `Script file ... not found` | Bare name not in `workdir/scripts/`, or wrong workdir | Pass the path, or `--workdir`; run `scripts/pyne_env.py` |
| `Symbol info file ... not found` | `.ohlcv` without its `.toml` | Re-download (`--force-save-info`) or write the `.toml` by hand |
| `pyne: command not found` | Shell does not have the venv on PATH | Call `<venv>/bin/pyne` or `uv run pyne` |
| Import of a library fails | Not under `scripts/lib/...` or wrong module path | `from lib.<publisher>.<name>.v<N> import ...`; `scripts/` is the import root |

## Wrong indicator values

| Symptom | Cause | Fix |
|---------|-------|-----|
| An indicator jumps or lags randomly | A `ta.*` call inside an `if`, after `and`, or in a conditional expression; it ran only on some bars | Compute every indicator unconditionally at the top of `main()` |
| Several EMAs / RSIs (or your own stateful helpers) in a loop are identical or wrong | One call site in a loop = one shared state | Separate calls, or compute from an `array` |
| `x[1]` is always `na` or equals `x` | Variable not annotated `Series[...]`, or assigned conditionally | Annotate at first assignment and assign every bar |
| `TypeError: 'float' object is not subscriptable` | Indexing a call result directly (`ta.highest(high, 20)[1]`) | Assign to a `Series[float]` variable first, then index it |
| A counter resets every bar | Plain local variable | `Persistent[int]` |
| A counter grows too fast | `Persistent` incremented in a helper called from several sites, or a module-level object | One `Persistent` per logical counter, inside the right function |
| `x == na` never true | `nan != nan` | `na(x)` |
| `if value:` misbehaves in plain Python code consuming results | `bool(nan)` is `True` outside Pyne code | `math.isnan(v)` in Python consumers |
| First bars have no values | Normal warm-up | Guard signals with `not na(...)`; feed enough history (2x longest length; more for recursive averages) |
| Values differ between two runs with different history length | Recursive averages (EMA/RMA/RSI/ATR) remember their start | Give more warm-up bars; compare only after the warm-up |

## Wrong trades

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Margin call` rows, huge losses | Default sizing 100 % of equity with 100 % margin | Set `default_qty_type`/`default_qty_value` or `qty=`; keep risk per trade small |
| No trades at all | `qty` is `na`/0/negative (dropped silently), signal never true, or filters too strict | Log `qty` and the signal components; check `ready` guards |
| Signals fire (plot/log shows them) but no trades | Order notional exceeds what equity covers at `margin_long`/`margin_short` = 100; the order is not filled, silently. Typical with risk-based sizing on low-priced instruments (forex) or expensive ones (BTC) | Cap `qty` at `strategy.equity * 0.98 / (close * syminfo.pointvalue)`, or lower the margin settings to model leverage |
| Entry on the same bar as the signal | Expecting same-bar fills | Market orders fill at the next bar's open; only `process_orders_on_close` or `immediately=True` change that |
| Stop not active on the first bar in position | Exit issued only when `position_size != 0` (one bar after the entry) | Issue `strategy.exit` on the signal bar too (it activates when the entry fills) |
| Stop/target never fills | Wrong units: `profit`/`loss`/`trail_*` are ticks, `limit`/`stop` are prices | Convert with `/ syminfo.mintick` or use the price forms |
| Stop drifts with volatility | Recomputing the stop from the current ATR every bar | Freeze the distance in a `Persistent` on the signal bar |
| Both stop and target hit in one bar, "wrong" one chosen | Intrabar path unknown; heuristic decides | Run with finer data and `--timeframe` (bar magnifier) |
| Second entry ignored | `pyramiding` default is 0 (one entry per direction) | Raise `pyramiding` or close before re-entering |
| Position reverses unexpectedly | `strategy.entry` in the opposite direction reverses | Guard with `position_size` checks or use `strategy.order` |
| Exit by signal happens but stats show more trades than expected | Partial closes each count | Check the trade CSV `Signal` column |
| Position still open at the end of the data | Expected | Statistics ignore it; `open_trades` in `run_backtest.py` reports it |
| Sizes slightly off | Lot rounding to `mincontract`, crypto precision from `basecurrency` | Fix the symbol `.toml` |
| P&L in the wrong currency scale | `pointvalue` or `currency` in the `.toml` wrong; `currency=` setting does not convert | Fix the `.toml`; compute conversions yourself |
| Strategy stops trading part-way through the data, no error | A `strategy.risk.max_drawdown` / `max_cons_loss_days` halt is permanent; `max_intraday_loss` and `max_intraday_filled_orders` block the rest of the day | Look for `Close Position (Max ...)` rows in `<script>_trade.csv`; loosen the rule or the test period |
| `max_drawdown` did not fire although the open position lost more than the threshold | The rule is evaluated only on a bar where a trade closed (realized peak reference) | Add a protective exit; the rule is a backstop, not a stop-loss |
| Short entry while long only closes the long | `strategy.risk.allow_entry_in(strategy.direction.long)`: opposite entries close instead of reversing | Intended; drop the rule for reversal strategies |
| Entries are smaller than `qty` | `strategy.risk.max_position_size` trims orders to the cap | Raise the cap or size below it |
| `alert_message` of a risk rule never appears | Stored but not emitted in 6.12.1 | Log the condition yourself (`log.warning`) or read the trade CSV comments |

## Lookahead and repainting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Too-good-to-be-true results | Future data | No negative offsets (`x[-1]` is `na` anyway); exclude the current bar from breakout levels (`hh: Series[float] = ta.highest(high, 20)`, then `close > hh[1]`); use `barmerge.lookahead_off` or the `close[1]` idiom in `request.security` |
| Live results worse than backtest | Signals derived from the trade list, or from a developing higher-timeframe bar | Plot signals on the bar they fire; use closed higher-timeframe values |
| Pivot-based signals fire "early" in the backtest | `ta.pivothigh(l, r)` is known only `r` bars later and is reported with that delay already; a different source may have been used | Trust the delay; do not shift the value back |

## Data and environment

| Symptom | Cause | Fix |
|---------|-------|-----|
| Sessions/daily boundaries off by hours | Wrong `timezone` in the `.toml`, or CSV timestamps misread | Set `--timezone` on conversion; fix the `.toml` |
| Weekend/holiday gaps counted as bars | They are not; only real bars exist | Use `time`-based logic, not `bar_index` arithmetic, for calendar spans |
| `--timeframe` rejected | Not an exact multiple of the data timeframe | Choose a compatible chart timeframe |
| `request.security` fails | Missing data for that context | `--security "1D=name"`, `security_data=`, or `pyne data map`; check `--list-data` |
| Every run hits the exchange; slow; numbers differ between runs | `pyne run SCRIPT ccxt:...` (provider string) downloads the bars again on every run | Download once (`pyne data download`, `download_candles.py`), run on the file name; see [cli-and-data.md](cli-and-data.md#candle-cache-download-once-update-run-offline) |
| A cached file suddenly holds only a few hundred bars | `pyne run SCRIPT ccxt:...` (provider mode, also with `--live`) rewrote `data/ccxt_<...>.ohlcv` with the run's range | Re-download (`download_candles.py SYMBOL -tf TF --truncate --from DATE`); paper-trade with `run_paper.py`, which only appends |
| Last bar of a cached file has a wrong close/volume that never changes | It was stored while still forming; `continue` never rewrites a stored bar | Store closed bars only (`download_candles.py` default `--to closed`, or `--to` one bar back); repair with `--truncate` |
| Live run on a spot pair logs `does not have market symbol BTC/USDT` and reconnects forever | `default_type = "swap"` (PyneCore's default in `config/plugins/ccxt.toml`) is applied to the websocket client; on Binance a spot symbol is then unknown (OKX accepted it) | Set `default_type = "spot"` for spot pairs (`run_paper.py` does it for symbols without `:SETTLE`); perpetuals keep `swap` and the `:SETTLE` suffix |
| `pyne data download` refuses a `--from` before the file's first bar | The file only grows at the end | `--truncate` and download again from the earlier date |
| Binance download fails to resolve or connect | `api.binance.com` unreachable from this network | `[binance]` table in `config/plugins/ccxt.toml` with `urls.api.public = "https://data-api.binance.vision/api/v3"` (public spot data, no keys) |
| Logo and spinner pollute captured output | CLI decorations | `PYNE_NO_LOGO=1`, `NO_COLOR=1` |
| `.toml` next to the script keeps changing in git | Rewritten on every import | Commit it once; set `PYNE_SAVE_SCRIPT_TOML=0` in CI |
| Editor shows many red errors on `close[1]` | Static type checker false positives | Add the `pyrightconfig.json` from [language.md](language.md); PyCharm needs nothing |

## Reporting mistakes

- Reporting a syntax-only check as a "working strategy". Run it on data and quote the
  statistics CSV.
- Quoting net profit without trade count, drawdown, commission and the data range.
- Optimizing on the full dataset and calling the result validated.
- Changing the `.toml` symbol info to make numbers look right without saying so.
