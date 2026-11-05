# Strategy API: `@script.strategy`, `strategy.*`, order fills, sizing, statistics

Everything a strategy script needs beyond the language itself. Import with
`from pynecore.lib import script, strategy`.

## Contents

- [The decorator and its settings](#the-decorator-and-its-settings)
- [Order functions](#order-functions)
- [When orders fill](#when-orders-fill)
- [Position sizing](#position-sizing)
- [Commission, slippage, margin](#commission-slippage-margin)
- [Position and performance variables](#position-and-performance-variables)
- [Open and closed trade queries](#open-and-closed-trade-queries)
- [Risk rules](#risk-rules)
- [Statistics and trade CSV files](#statistics-and-trade-csv-files)
- [Re-execution flags](#re-execution-flags)

## The decorator and its settings

```python
@script.strategy(
    "Title",                       # title; shorttitle="..." optional
    overlay=True,                  # draw on the price pane (affects viz only)
    initial_capital=10_000,
    currency=currency.USD,         # default currency.NONE; no conversion of results is done
    default_qty_type=strategy.percent_of_equity,   # or strategy.fixed, strategy.cash
    default_qty_value=10,          # 10 % of equity per entry
    pyramiding=1,                  # max entries in the same direction; 0 means 1
    commission_type=strategy.commission.percent,   # .cash_per_order, .cash_per_contract
    commission_value=0.05,         # 0.05 % per fill
    slippage=2,                    # ticks added against you on every fill
    margin_long=100.0, margin_short=100.0,   # % of position value that must be covered; 100 = no leverage
    process_orders_on_close=False,
    close_entries_rule="FIFO",     # or "ANY"
    calc_on_order_fills=False,
    calc_on_every_tick=False,      # live mode only
    calc_on_every_history_tick=False,
    use_bar_magnifier=True,        # only active when the run supplies finer data via --timeframe
    risk_free_rate=2.0,            # % for the Sharpe/Sortino ratios
    max_bars_back=0,               # 0 = automatic
    backtest_fill_limits_assumption=0,   # ticks price must pierce a limit for it to fill
    fill_orders_on_standard_ohlc=False,
    close_by_id=False,             # one close order per entry id per bar (legacy behaviour)
    na_bool=False,                 # three-state bool (legacy behaviour); keep False
)
def main(...):
```

Defaults when omitted: `initial_capital=100000`, `default_qty_type=percent_of_equity`,
`default_qty_value=100`, `commission_value=0.0`, `slippage=0`, `pyramiding=0` (one entry).

**The sizing default is dangerous.** 100 % of equity per entry with 100 % margin leaves no room
for an adverse move, so losing positions get liquidated by margin calls, and the trade list fills
with `Margin call` rows. Always set `default_qty_type` and `default_qty_value` explicitly (or
pass `qty=` on every entry), and set a realistic commission.

Every decorator argument is also a key in the `[script]` section of the script's `.toml` and in
`ScriptRunner(settings={...})`. Constant-valued settings accept the constant or its string:
`strategy.fixed` or `"fixed"`, `strategy.commission.percent` or `"percent"`.

## Order functions

| Function | Purpose |
|----------|---------|
| `strategy.entry(id, direction, qty=na, limit=None, stop=None, oca_name=None, oca_type=None, comment=None, alert_message=None, disable_alert=False)` | Open or add to a position. `direction` is `strategy.long` or `strategy.short`. Without `limit`/`stop` it is a market order. An entry in the opposite direction reverses the position (closes the open one and opens the new one in a single fill). Calling it again with the same `id` while unfilled modifies the pending order. |
| `strategy.order(id, direction, qty=na, limit=None, stop=None, oca_name=None, oca_type=None, comment=None, ...)` | Raw order: it does not reverse, it just adds the signed quantity. Use for scaling or hedging-style logic. |
| `strategy.exit(id, from_entry="", qty=na, qty_percent=na, profit=na, limit=na, loss=na, stop=na, trail_price=na, trail_points=na, trail_offset=na, oca_name="", comment="", comment_profit="", comment_loss="", comment_trailing="", alert_message="", ...)` | Price-based exits: take-profit (`profit` in ticks or `limit` price), stop-loss (`loss` in ticks or `stop` price), trailing stop (`trail_price` or `trail_points` to arm, `trail_offset` ticks behind the extreme). `from_entry` targets one entry id; empty means every open entry. Re-issue it every bar while the position is open; the pending exit is modified in place. |
| `strategy.close(id, comment="", qty=na, qty_percent=na, alert_message="", immediately=False, disable_alert=False)` | Market order closing what entry `id` opened (fully or partially). |
| `strategy.close_all(comment="", alert_message="", immediately=False, disable_alert=False)` | Market order closing the whole position. Does nothing when flat. |
| `strategy.cancel(id)` | Cancel every pending order with that id. |
| `strategy.cancel_all()` | Cancel every pending order. |
| `strategy.default_entry_qty(fill_price)` | The quantity a default-sized entry would get at `fill_price` (price snapped to tick, size floored to lot). |

Constants: `strategy.long`, `strategy.short`; `strategy.fixed`, `strategy.cash`,
`strategy.percent_of_equity`; `strategy.commission.percent`, `.cash_per_order`,
`.cash_per_contract`; `strategy.direction.long`, `.short`, `.all`; `strategy.oca.none`,
`.cancel`, `.reduce`.

Ticks: `profit=100` means 100 x `syminfo.mintick`. Convert a price distance with
`distance / syminfo.mintick`. Prefer the price forms (`limit=`, `stop=`) when you compute levels
from ATR or structure; they are easier to read and do not depend on tick size.

`alert_message` and `disable_alert` are accepted but PyneCore does not dispatch order-fill
alerts; put notifications in your own Python code instead.

### Several closes on one bar

By default every `strategy.close()` statement places its own order, so three partial closes
written as three statements all fill on the same bar. The same statement executed again on the
same bar (inside a loop) modifies its own order. `close_by_id=True` switches to one close order
per entry id per bar, where the last call wins.

## When orders fill

Order **creation** (inside `main()`) and order **filling** (by the broker emulator after
`main()` returns) are separate steps.

| Order | Fills |
|-------|-------|
| Market (`entry`/`order` without price, `close`, `close_all`) | At the **open of the next bar**. |
| Market with `process_orders_on_close=True` | Gets an extra fill attempt at the **close of the bar that placed it**. |
| `close(..., immediately=True)` / `close_all(immediately=True)` | At the close of the current bar regardless of `process_orders_on_close`; `strategy.position_size` still shows the old position for the rest of this bar's calculation. |
| Limit / stop / trailing (`exit`, `entry` with `limit`/`stop`) | On the first later bar (or the same bar after a `process_orders_on_close` fill) whose range reaches the price. Fill price is the order price, or the bar open when the bar gaps through it. |

Within one bar the emulator does not know the real price path. It assumes
`open -> high -> low -> close` when the open is nearer the high than the low, otherwise
`open -> low -> high -> close`. When both a stop and a target sit inside one bar, that
assumption decides which fills. Run with finer data and `--timeframe` (bar magnifier) to resolve
that with real sub-bars; see [cli-and-data.md](cli-and-data.md).

Consequences for strategy code:

- A signal computed on bar N trades at bar N+1's open. That is realistic: the bar's close is not
  known until the bar is over.
- Read `strategy.position_size` to know the **current** state before placing new orders; it
  already reflects fills from earlier bars.
- Do not expect `strategy.closedtrades` to increase on the bar you called `close()`.
- Signals meant for an external executor must be plotted on the bar that generated them, not
  reconstructed from the trade list (see the integration notes in
  [live-and-integrations.md](live-and-integrations.md)).

## Position sizing

Three sizing modes for `default_qty_type`:

| Mode | `default_qty_value` means |
|------|---------------------------|
| `strategy.fixed` | Units (contracts, coins, shares) per entry |
| `strategy.cash` | Account currency per entry; units = cash / fill price |
| `strategy.percent_of_equity` | Percent of current equity per entry |

Per-entry `qty=` overrides the default. Sizes are floored to the lot step
(`syminfo.mincontract`); money-based sizes also depend on `syminfo.pointvalue`. Check the
symbol's `.toml` when sizes look wrong. An `na`, zero, negative or infinite `qty` drops the order
silently, so guard computed sizes:

```python
risk_cash = strategy.equity * risk_pct / 100
stop_distance = atr * atr_mult
qty = risk_cash / stop_distance if stop_distance > 0 else na
if long_signal and not na(qty) and qty > 0:
    strategy.entry("L", strategy.long, qty=qty)
```

**Margin caps the notional.** With `margin_long` / `margin_short` = 100 (the default) the
position value cannot exceed the equity; an entry whose notional is larger is **not filled at
all**, with no error. Risk-based sizing hits this quickly (1 % risk with a 0.2 % stop asks for
5x the equity). Cap the quantity at the affordable notional, or lower the margin percentages
to model leverage (`margin_long=20` allows 5x):

```python
qty = math.min(qty, strategy.equity * 0.98 / (close * syminfo.pointvalue))
```

`pyramiding` caps how many entries can stack in one direction. With the default (1) a second
`entry` in the same direction is ignored while a position is open; a different-direction entry
reverses.

## Commission, slippage, margin

- `commission_type=strategy.commission.percent`, `commission_value=0.1` charges 0.1 % of the
  fill value on every fill (entry and exit). `cash_per_order` charges a flat amount per fill,
  `cash_per_contract` per unit.
- `slippage=N` moves every fill N ticks against you.
- `margin_long` / `margin_short` are the percent of the position value the account must hold.
  `100` means fully funded, `50` means 2x leverage, `0` disables margin checks (and makes
  `strategy.opentrades.capital_held` report `na`). When equity cannot cover the required margin the
  emulator liquidates part of the position (a `Margin call` row in the trade list).
- Monetary results are in the symbol's quote currency scaled by `pointvalue`; the `currency`
  setting does not convert them.

## Position and performance variables

Read inside `main()`; all reflect the state **after** the previous bar's fills.

| Variable | Meaning |
|----------|---------|
| `strategy.position_size` | > 0 long, < 0 short, 0 flat (units) |
| `strategy.position_avg_price` | Average entry price; `na` when flat |
| `strategy.position_entry_name` | Entry id of the first open trade; `""` when flat |
| `strategy.opentrades` | Number of open (filled, not closed) trades |
| `strategy.closedtrades` | Number of closed trades so far |
| `strategy.openprofit`, `strategy.openprofit_percent` | Unrealized P&L (currency, % of initial capital) |
| `strategy.netprofit`, `strategy.netprofit_percent` | Realized P&L |
| `strategy.grossprofit`, `strategy.grossloss` (+ `_percent`) | Sum of winners, sum of losers (loss is positive) |
| `strategy.wintrades`, `strategy.losstrades`, `strategy.eventrades` | Counts |
| `strategy.avg_trade`, `strategy.avg_winning_trade`, `strategy.avg_losing_trade` (+ `_percent`) | Averages; `avg_losing_trade` is positive, its `_percent` negative |
| `strategy.equity` | `initial_capital + netprofit + openprofit` |
| `strategy.max_drawdown`, `strategy.max_drawdown_percent` | Peak-to-trough equity drawdown so far |
| `strategy.max_runup`, `strategy.max_runup_percent` | |
| `strategy.max_contracts_held_all`, `_long`, `_short` | |
| `strategy.margin_liquidation_price` | Price at which a margin call liquidates; `na` when flat or no margin |
| `strategy.initial_capital`, `strategy.account_currency` | Settings |

## Open and closed trade queries

`strategy.opentrades.<field>(n)` and `strategy.closedtrades.<field>(n)` read trade `n`
(0-based). Fields: `entry_id`, `entry_price`, `entry_bar_index`, `entry_time` (ms),
`entry_comment`, `size` (signed), `profit`, `profit_percent`, `commission`, `max_runup`,
`max_runup_percent`, `max_drawdown`, `max_drawdown_percent`. Closed trades add `exit_id`,
`exit_price`, `exit_bar_index`, `exit_time`, `exit_comment`. `strategy.opentrades.capital_held`
is the summed entry value of open trades.

Typical uses: bars-in-trade (`bar_index - strategy.opentrades.entry_bar_index(0)`), time stop,
breakeven after a run-up, or a "last trade was a loss" filter:

```python
if strategy.closedtrades > 0:
    last_was_loss = strategy.closedtrades.profit(strategy.closedtrades - 1) < 0
```

## Risk rules

Declared once per bar (they are idempotent); the emulator enforces them after fills.

| Call | Effect |
|------|--------|
| `strategy.risk.allow_entry_in(strategy.direction.long)` | Only long entries (`.short`, `.all`) |
| `strategy.risk.max_position_size(contracts)` | Cap on position size |
| `strategy.risk.max_drawdown(value, type=strategy.percent_of_equity)` | Close everything and stop trading at that drawdown (`strategy.cash` for an absolute amount) |
| `strategy.risk.max_intraday_loss(value, type=...)` | Same, for a single day's loss |
| `strategy.risk.max_cons_loss_days(count)` | Stop after N losing days in a row |
| `strategy.risk.max_intraday_filled_orders(count)` | Cap fills per day |

## Statistics and trade CSV files

Written by `pyne run` into `output/` and by `ScriptRunner(strat_path=..., trade_path=...)`.

`<script>_strat.csv` has one metric per row with `All`, `Long`, `Short` columns in currency and
percent: Net profit, Gross profit/loss, Commission paid, Buy & hold return, Max equity run-up and
drawdown, Max contracts held, Total/open/winning/losing trades, Percent profitable, Avg P&L, Avg
winning/losing trade, Ratio avg win / avg loss, Largest winning/losing trade (and percent), Avg #
bars in trades (all/winning/losing), Sharpe ratio, Sortino ratio, Profit factor, Margin calls.

`<script>_trade.csv` has two rows per trade (entry and exit) with: Trade #, Bar Index, Type
(`Entry long`, `Exit long`, `Entry short`, `Exit short`, `Margin call`), Signal (the id or
comment), Date/Time, Price, Contracts, Profit, Profit %, Cumulative profit (and %), Run-up,
Drawdown (and %).

`<script>.csv` is the plot file: `time, open, high, low, close, volume` followed by one column
per `plot*()` title (or returned dict key). Missing values are `NaN`.

Use `scripts/summarize_backtest.py` to turn these into a short report.

## Re-execution flags

- `calc_on_order_fills=True`: `main()` runs again right after each fill inside a bar, so a
  strategy can react to its own fill (place an exit, add a position) in the same bar. On each
  re-execution `Persistent` variables roll back to the bar-open state; `IBPersistent` variables
  keep their value. Each pass sees the bar built up to the fill point, not the completed bar.
- `calc_on_every_history_tick=True`: four passes per historical bar (open, nearer extreme,
  farther extreme, close) or one per sub-bar with the magnifier. Mostly for research.
- `calc_on_every_tick=True`: live mode only, run the body on every price update of the forming
  bar.

Default strategies get exactly one execution per bar with the complete bar, which is what a
backtest should normally use.
