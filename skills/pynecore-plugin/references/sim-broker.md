# Simulated broker: a BrokerPlugin with an offline conformance lab (`templates/pynecore-simbroker`)

Plugin name `simbroker`, class `SimBroker(BrokerPlugin[SimBrokerConfig], CLIPlugin)`. Capabilities
`provider`, `broker`, `cli`.

## What it is for

- **A broker plugin to copy.** It implements the whole `BrokerPlugin` contract of PyneCore 6.12
  (order intents, journal rows, incremental fills with stable ids, venue-driven cancels, external
  intervention, capabilities) against a venue that lives in-process, so every line of the
  contract is visible and runnable. `new_plugin.py simbroker DEST --name NAME` scaffolds it;
  replace the venue calls by your exchange SDK and keep the engine-facing code.
- **A dress rehearsal of `pyne run --broker`.** Candles come from any installed provider plugin
  (`feed = "ccxt" | "ccxtrest" | "file"`), orders are matched against those candles with fees,
  slippage, OCA brackets and trailing stops, and the state survives restarts. Contract validation,
  the SQLite journal, restart adoption and quarantine all run exactly as with a real venue; only
  the money is simulated.
- **An example of the offline conformance lab** (`pynecore.testing.broker_lab`): a suite file
  that drives the real plugin through the real sync engine with scripted steps and oracles.

## Install and use

```bash
pip install -e <skill>/templates/pynecore-simbroker            # into the environment that has pyne
pyne plugin info simbroker                                     # capabilities provider, broker, cli
pyne run my_strategy simbroker:BINANCE:BTC/USDT@1 --broker -f -500 --no-plot     # feed = "ccxt" (default)
pyne run my_strategy simbroker:BTCUSDT@60 --broker -f -300 --no-plot            # feed = "file": fully offline
pyne simbroker status                                          # position, cash, realized pnl, working orders
pyne simbroker cancel-order sim-3                              # act outside the bot: the run enters quarantine
pyne simbroker reset --yes                                     # fresh account
python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode smoke
python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode extended --seed 1337
```

The symbol after `simbroker:` is passed to the feed plugin unchanged (`multi_broker = False`), so
it is written in the feed's notation. The feed's own `config/plugins/<feed>.toml` applies (Binance
host overrides in `ccxt.toml`, `replay_bars` in `file.toml`). With `feed = "ccxt"` a spot pair
such as `BINANCE:BTC/USDT` streams only with `default_type = "spot"` in `ccxt.toml` (with the
default `swap` the Binance websocket rejects the symbol and reconnects forever); `feed =
"ccxtrest"` has no such switch to set.

## Configuration (`config/plugins/simbroker.toml`)

| Key | Default | Meaning |
|-----|---------|---------|
| `feed` | `"ccxt"` | Provider plugin that supplies the candles |
| `account` | `"demo"` | State file `cache/simbroker_<account>.json`, run identity `simbroker-<account>` |
| `initial_balance` | `10000.0` | Cash of a fresh account, in `quote_currency` |
| `quote_currency` | `"USDT"` | Currency of balance and fees |
| `fee_rate` | `0.0004` | Fee as a fraction of the filled notional, on every fill |
| `slippage_ticks` | `0` | Ticks against the order on market, stop and trailing fills |
| `fill_on_ticks` | `true` | Match on every intra-bar update; `false`: only on closed bars |
| `mintick` | `0.0` | Price grid for slippage; `0` = the symbol info's |
| `symbol_map` | `{}` | Inherited from `LiveProviderConfig` |

## Layout

Paths below are inside the template folder, `templates/pynecore-simbroker/` of this skill.

| File | Contents |
|------|----------|
| `src/pynecore_simbroker/config.py` | `SimBrokerConfig(LiveProviderConfig)` |
| `src/pynecore_simbroker/venue.py` | `SimOrder` (an `ExchangeOrder` plus leg, OCA and trailing data), `SimState` (orders, position per owner, cash, pending events, fault-injection counters the lab's reference venue also has), `SimVenue` (JSON persistence, matching, OCA, fill ledger) |
| `src/pynecore_simbroker/plugin.py` | `SimBroker`: feed delegation, intents → venue orders, journal rows, events, capabilities, `pyne simbroker` commands; `PLUGIN_NAME` constant the file names derive from |
| `broker_lab/suite.py` | `build_suite(mode, seed)` for `python -m pynecore.testing.broker_lab run` |

## How the contract is implemented

**Identity and feed.** `__init__` sets `self._account_id = "simbroker-<account>"` before anything
else; the run identity (`<script>@simbroker-<account>:<SYMBOL>:<TF>` in the resume command) is
derived from it before `connect()`. `_feed()` loads the feed plugin with `load_plugin(cfg.feed)`,
runs `ensure_config` on the feed's own TOML, instantiates it with the same symbol, timeframe and
data folder and redirects its writer to the broker's data file
(`simbroker_<SYMBOL>_<TF>.ohlcv`); this redirect is repeated on every `download_ohlcv` because
the CLI swaps the provider's writer before each download. `feed_timeout_bars` is copied from the
feed (`None` for the file provider's paced replay). History, symbol info, `connect`, `watch_ohlcv`
and `backfill_closed_bars` are delegated; every update also goes through `_on_bar`.

**Matching (`SimVenue.on_price`).** On each update (or closed bar only, `fill_on_ticks = false`)
every open order of the account is tested in placement order: market at close ± slippage; limit
buy when `low <= price`, sell when `high >= price`, at the limit price; stop buy when
`high >= stop`, sell when `low <= stop`, at stop ± slippage; trailing stop activates at its
activation price and then follows the running high/low at `trail_offset`. A reduce-only order is
capped by the owner's exposure and cancelled with `CANCEL_REASON_VENUE_POSITION_CLEARED` when
nothing is left to reduce. After a fill the OCA siblings are reduced or cancelled with
`CANCEL_REASON_VENUE_OCA`. Fees, VWAP entry price and realized PnL are kept in the state. Each
fill produces one `OrderEvent` with incremental `fill_qty` and a stable `fill_id` (`"<order id>:<n>"`),
queued per owner and drained by `watch_orders`.

**Intents → orders (`_record`).** One venue order per leg; the client order id is
`envelope.client_order_id(KIND_ENTRY | KIND_EXIT_TP | KIND_EXIT_SL | KIND_CLOSE)`. A repeated id
whose order is open or filled returns that order (idempotent re-dispatch); an id spent by a
cancelled or rejected order raises `ClientOrderIdSpentError`, so the engine re-dispatches under a
fresh retry sequence instead of adopting a dead order. `ensure_venue_writes_allowed()` runs before
every write. After placing, `store_ctx.upsert_order(coid, symbol=, side=, qty=, state=,
intent_key=, exchange_order_id=, from_entry=, pine_entry_id=, filled_qty=)` writes the journal
row for the leg; without it a later `set_filled` on that client id fails with "missing required
fields".

**The journal's fill cursor.** Every fill of the run's own order updates its row:
`store_ctx.set_filled(coid, cumulative filled_qty)`, called from the venue's fill listener
(`SimBroker._journal_fill`) before the event is queued. The engine never updates this column
itself, and at startup it sums it over the run's live rows (buys +, sells −) to know which part
of the venue position is this run's. A row left at 0 makes a restart log `startup adoption
limited to run-owned exposure (exchange=0.01, owned=0.0, ...)`: the strategy believes it is flat
while the venue holds its position and sends its entry again on every bar. Rows are not closed
after a fill; entry and exit rows net out.

**Amend and cancel.** `modify_entry` / `modify_exit` amend the open legs in place; an exit amend
after a partial fill sets `remaining_qty = intent qty` and `qty = filled + intent qty`, adds
missing legs through `_record` and cancels dropped levels. `execute_cancel_with_outcome` returns
`CANCEL_CONFIRMED`, `ALREADY_FILLED` or `UNKNOWN`; `cancel_broker_order_ref` cancels by venue id.
`get_open_orders` returns the owner's orders, `get_position` the account-wide position (`None`
when flat), `get_balance` the cash in `quote_currency`.

**External intervention.** The state file is shared with the `pyne simbroker` commands. On every
update `_on_bar` calls `venue.reload_if_changed()`; an order the CLI cancelled is reported once:
a synthesised `cancelled` event with `from_disappearance_tracker=True` plus
`report_external_intervention(message, context, client_order_id=, exchange_order_id=,
changed_at_ms=)`. The engine then quarantines the run (verified below).

**Capabilities.** `NATIVE`: `stop_order`, `trailing_stop`, `tp_sl_bracket`,
`partial_qty_bracket_exit` (+ `_pyramiding`), `oca_cancel`, `amend_order`, `reduce_only`,
`watch_orders`, `fetch_position`, `idempotency`, `short_selling`; `SOFTWARE`: `cancel_all`.
`fill_report_grace_s = 5.0` (fills are published within one feed update).

**CLI.** `pyne simbroker status [-a ACCOUNT]` (position, cash, realized PnL, every order with
state, side, quantity, filled, leg), `pyne simbroker cancel-order ORDER_ID [-a ACCOUNT]`,
`pyne simbroker reset [--yes] [-a ACCOUNT]`. The commands read the workdir through the CLI's
`app_state` (`config_dir`, `cache_dir`), so they work from any directory with `--workdir`.

## The offline lab (`broker_lab/suite.py`)

```bash
python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode smoke            # 10 scenarios
python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode extended --seed 1337   # + 9 pairwise cases
```

- `OfflineSimBroker(SimBroker)` is the real plugin without a feed (`connect` / `disconnect` only
  flip the flag); the lab feeds prices with its own `price` step.
- `SimBrokerProfile(ReferenceVenueProfile)` reuses PyneCore's step vocabulary (`entry`, `exit`,
  `amend_exit`, `cancel`, `sync`, `deliver`, `restart`, `fill`, `duplicate_event`, `expect`, ...)
  and its invariants, because `SimState` carries the same attribute names as the lab's reference
  venue (`orders`, `position`, `position_owners`, `pending_events`, `calls`, `reject_entries`,
  `skip_entries`, `read_error`, `stale_position`). It adds the `price` step (one feed update
  matched by the venue) and routes the `fill` step through `SimVenue.fill` so OCA reduction,
  fill ids and the journal's fill cursor are the plugin's own (the profile does not call
  `set_filled` on the plugin's behalf; PyneCore's reference profile does, which is why a lab
  run alone cannot catch a plugin that forgets it: test a restart, see below).
- `DoubleFillProfile` is the **control**: it reports one execution twice under two fill ids
  (breaking the stable-fill-id rule); the scenario declares `expected_violation="position
  mismatch"` and passes only when the oracle catches it, which proves the oracle is awake.
- Smoke scenarios: `restart-adopts-working-order`, `market-entry-fills-on-next-price`,
  `bracket-covers-position`, `take-profit-fill-cancels-stop`, `stop-loss-fill-cancels-take-profit`,
  `partial-fill-keeps-coverage`, `cancelled-entry-leaves-nothing`, `duplicate-fill-event-is-ignored`,
  `two-runs-share-the-account`, `control-duplicate-fill-id-is-detected`. Extended adds
  `pairwise_cases({entry: market|limit|stop, exit: tp|sl|both, fill: full|partial}, seed)`.
- Steps after a partial exit fill use `check_invariants=False` (`_quiet`): the logical exit is
  oversized until the strategy re-issues it on the next bar, exactly as in a real run, and the
  suite re-issues it with the remaining quantity before the next check.

## Verified (PyneCore 6.12.1, scratch venv, `feed = "file"` on the hourly BTC/USDT replay, `account = "sim"`)

| Check | Result |
|-------|--------|
| `validate_plugin_contract(SimBroker(...), require_account_id=True)` | 0 errors, 0 warnings |
| `plugin_check.py simbroker` | static checks OK: capabilities broker, live-provider, cli; CLI commands `status`, `cancel-order`, `reset` |
| lab `--mode smoke` | 10/10 PASS (control scenario detected as expected) |
| lab `--mode extended --seed 1337` | 19/19 PASS |
| `pyne run sim_probe simbroker:BTCUSDT@60 --broker -f -300 --no-plot`, SIGINT after the replay | 38 entries dispatched, 37 exit brackets, 222 fill events, graceful stop, exit 0; `output/logs/broker.sqlite` and `cache/simbroker_sim.json` written |
| `run_live.py scripts/sim_probe_wide.py simbroker:BTCUSDT@60 --from -300 --yes` (venv `python`, `PATH` without `pyne`), SIGINT after 14 s, the same command again for 20 s | first start: entry filled, bracket placed; journal row of the entry at `filled_qty 0.01`. Second start: `position size mismatch (exchange=0.01, internal=0.0) — adopting exchange`, no new entry, the bracket re-asserted under the same client ids (no new venue orders), exit 0 both times. Before the fill-cursor fix the second start logged `startup adoption limited to run-owned exposure (owned=0.0)` and the strategy sent its entry on each of 64 bars |
| `run_live.py ... --yes`, `pyne simbroker cancel-order` on the take-profit, SIGINT | quarantine within 2 s, the wrapper's `! QUARANTINE` frame on stderr and the resume command in its summary, graceful stop, exit 0; `pyne broker resume ...` then the same command: `quarantine resumed ... the bot takes its position and protection from the venue`, `resume completed: the bot took the venue over` |
| `run_live.py scripts/sim_probe.py simbroker:BINANCE:BTC/USDT@1 --from -100 --yes` with `feed = "ccxtrest"` (real Binance 1-minute bars through the public data host), 5.5 min | 3 market entries filled (each logged on the bar after its dispatch), 2 brackets, one take-profit and one stop-loss fill each cancelling its sibling (`CANCEL_REASON_VENUE_OCA`), no ERROR, graceful stop, exit 0 |
| `pyne simbroker cancel-order sim-2` while the run holds a take-profit (`sim_probe_wide`) | within one update: `sync engine quarantined: kind=external_intervention quarantine_id=98d9d499 — order sim-2 (X, tp) was cancelled outside the bot (context policy=stop)` and the line `pyne broker resume 'sim_probe_wide@simbroker-sim:BTCUSDT:60' 98d9d499`; the following `CANCELLED` event is logged as `unexpected cancel for intent X|L` |
| OCA sibling cancelled by the venue after a stop-loss fill (`CANCEL_REASON_VENUE_OCA`) | engine logs `external cancel observed (...)` at INFO and books the position to 0; no quarantine |
| `new_plugin.py simbroker DEST --name myvenue --class MyVenueBroker` | renamed package, entry point `myvenue = "pynecore_myvenue:MyVenueBroker"`, sources parse |

## Adapting the template to a real venue

1. Keep `plugin.py`'s engine-facing methods; replace `SimVenue` calls by SDK calls. The client
   order id must be echoed by the venue (`ExchangeOrder.client_order_id`); if the venue cannot
   store it, keep a local map and treat its loss as a reconciliation case.
2. `watch_orders` must yield **incremental** fills with a stable `fill_id`; if the venue reports
   cumulative quantities, diff them yourself (`SimVenue._fill` shows the bookkeeping). Before the
   event goes out, write the order's **cumulative** filled quantity to the journal
   (`store_ctx.set_filled(coid, filled)`), or restart adoption sees the run as flat.
3. Venue-driven cancels need a reason from `pynecore.core.broker` (`CANCEL_REASON_VENUE_OCA`,
   `CANCEL_REASON_VENUE_POSITION_CLEARED`), otherwise the engine cannot tell them from an
   intervention.
4. Anything the venue does that the bot did not ask for (a cancel in the web UI, a liquidation)
   goes through `report_external_intervention`; the engine decides between quarantine and resume.
5. `get_capabilities` must say `SOFTWARE` or `UNSUPPORTED` for what the venue lacks; the engine
   emulates `SOFTWARE` capabilities (brackets as separate orders, software OCA) and refuses to
   run scripts that need an `UNSUPPORTED` one.
6. Run the lab first (`OfflineSimBroker` shows how to cut the feed), then
   `plugin_check.py NAME`, then `pyne run ... --broker` on a demo account; the bundled
   documentation
   ([../../pynecore-docs/docs/development/broker-plugin-authoring.md](../../pynecore-docs/docs/development/broker-plugin-authoring.md),
   [../../pynecore-docs/docs/advanced/broker-plugin-testing.md](../../pynecore-docs/docs/advanced/broker-plugin-testing.md))
   lists the release criteria.
