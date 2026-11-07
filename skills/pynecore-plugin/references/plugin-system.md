# The plugin system (PyneCore 6.12)

From the sources of `pynecore.core.plugin`, `pynecore.core.config`, `pynecore.core.live_runner`,
`pynecore.cli` and the bundled documentation
([../../pynecore-docs/docs/development/plugin-system.md](../../pynecore-docs/docs/development/plugin-system.md)), checked by
installing and running the three templates of this skill.

## Contents

- [Packaging and discovery](#packaging-and-discovery)
- [Configuration: the Config dataclass](#configuration-the-config-dataclass)
- [ProviderPlugin: history](#providerplugin-history)
- [How the CLI drives a provider](#how-the-cli-drives-a-provider)
- [LiveProviderPlugin: the stream](#liveproviderplugin-the-stream)
- [What the live runner does with your provider](#what-the-live-runner-does-with-your-provider)
- [CLIPlugin: commands and options](#cliplugin-commands-and-options)
- [BrokerPlugin](#brokerplugin)
- [Notifications and other side channels](#notifications-and-other-side-channels)
- [Development loop](#development-loop)
- [Mistakes seen while building the templates](#mistakes-seen-while-building-the-templates)

## Packaging and discovery

A plugin is a normal Python distribution with one entry point in the group `pyne.plugin`:

```toml
[project]
name = "pynecore-myfeed"
version = "0.1.0"
dependencies = ["pynesys-pynecore[cli]>=6.12,<7"]

[project.entry-points."pyne.plugin"]
myfeed = "pynecore_myfeed:MyfeedProvider"        # key = plugin name, value = module:class

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/pynecore_myfeed"]
```

- The **key** is the plugin name everywhere: provider strings (`myfeed:SYMBOL@TF`), the config
  file (`config/plugins/myfeed.toml`), the data file prefix (`myfeed_SYMBOL_TF.ohlcv`), the CLI
  group of a `CLIPlugin` (`pyne myfeed ...`), `pyne plugin info myfeed`.
- Install into the same environment as `pyne` (`pip install -e DIR`, `uv pip install -e DIR`,
  or `uv add` in a uv project). There is no `pyne plugin install`; `pyne plugin list` only reads
  what is installed.
- Metadata (`pyne plugin list/info`): version and summary from the distribution, the first
  docstring paragraph of the class as the one-line summary, the PyneCore requirement from
  `Requires-Dist` (`Needs PyneCore: >=6.12`). Versions are `PineVersion.Major.Minor`, so `<7`
  pins the current language generation.
- `discover_plugins()` groups entry points by name; a name declared by several installed
  packages is listed as a conflict and `load_plugin(name)` refuses to guess (the plugin is
  unusable until one package is uninstalled).
- The CLI loads **every** installed plugin class at startup (`_register_cli_plugins`) to find
  `CLIPlugin` subclasses; a failing import is logged as a warning and skipped. Import-time side
  effects of a plugin module therefore run on every `pyne` command.
- Capabilities come from the class hierarchy: `ProviderPlugin` → `provider`, `LiveProviderPlugin`
  (a provider that also streams) → `provider` listed, live detected by `issubclass`,
  `BrokerPlugin` → `broker`, `CLIPlugin` → `cli`. Multiple inheritance combines them
  (`class X(LiveProviderPlugin, CLIPlugin)`).
- `plugin_name` on the class is only a display name (`CCXT`, `File`); the entry-point key is the
  identity.

## Configuration: the Config dataclass

```python
@dataclass
class MyfeedConfig(LiveProviderConfig):      # or a plain @dataclass for providers without live mode / CLI plugins
    """One-line description: becomes the header comment of the TOML file."""

    api_key: str = ""
    """Attribute docstring: becomes the comment above the key."""

    poll_interval: float = 5.0
    """Seconds between polls."""

    files: dict[str, str] = field(default_factory=dict)
    """Dict fields render as inline tables: files = { "BTCUSDT" = "/path.csv" }."""


class MyfeedProvider(LiveProviderPlugin[MyfeedConfig]):
    Config = MyfeedConfig
```

- At every CLI start, `config/plugins/<name>.toml` is generated for every installed plugin that
  has a `Config`: all defaults as commented lines (`#api_key = ""`), the class docstring and
  field docstrings as comments. An existing file is regenerated from the dataclass while
  **user values (uncommented keys) and extra tables (`[binance]`) are preserved**;
  `pyne --recreate-provider-config` forces the rewrite.
- The framework calls `ensure_config(cls.Config, path)` and passes the instance as `config=`
  to the constructor. Call it yourself in programmatic code; it caches one instance per
  (class, path).
- `LiveProviderConfig` adds `symbol_map: dict[str, str]`, the translation table
  `request.security()` uses in live mode (`"FX:EURUSD" = "EURUSD"`). Live and broker configs
  must subclass it.
- `pyne plugin info` lists the fields with their defaults; a field whose default is a factory
  (dict, list) is shown as `(required)`.
- Environment overrides are the plugin's own business (read `os.environ` in the plugin, as the
  Telegram template does). Credentials belong in the TOML, which lives in the git-ignored
  `config/` folder.

## ProviderPlugin: history

```python
class MyfeedProvider(ProviderPlugin[MyfeedConfig]):
    Config = MyfeedConfig
    multi_broker = False            # True: "myfeed:BROKER:SYMBOL" splits the broker off and hands you "BROKER:SYMBOL"
    fetch_all_by_default = False    # True: a download without a start date fetches everything, not one year
    timezone = "UTC"

    def __init__(self, *, symbol=None, timeframe=None, ohlcv_dir=None, config=None):
        super().__init__(symbol=symbol, timeframe=timeframe, ohlcv_dir=ohlcv_dir, config=config)
        # super() sets self.symbol, self.timeframe, self.xchg_timeframe (= to_exchange_timeframe(timeframe)),
        # and when ohlcv_dir is given: self.ohlcv_path = get_ohlcv_path(symbol, timeframe, ohlcv_dir) and an
        # OHLCVWriter for it (opened later by the download flow). ohlcv_dir is None when the CLI only lists symbols.
```

The five abstract members and what the framework expects of them:

| Member | Called by | Contract |
|--------|-----------|----------|
| `to_tradingview_timeframe(cls, tf)` | metadata, converters | venue notation → PyneCore notation (`"1h"` → `"60"`, `"1d"` → `"1D"`) |
| `to_exchange_timeframe(cls, tf)` | the constructor (`xchg_timeframe`) | PyneCore → venue (`"60"` → `"1h"`); raise `ValueError` for an unsupported size |
| `get_list_of_symbols(self)` | `pyne data download NAME --list-symbols`, the symbol browser | native symbol names; the instance may have `symbol=None` (or only a broker selector) |
| `update_symbol_info(self)` | `get_symbol_info()` when `<file>.toml` is missing or `--force-save-info` | a **complete** `SymInfo`: `prefix`, `ticker`, `description`, `currency`, `period` (the requested timeframe), `type`, `mintick`, `pricescale`, `minmove`, `pointvalue`, `mincontract` (0 → PyneCore estimates it from volumes), `timezone`, and `opening_hours` / `session_starts` / `session_ends` **also for 24/7 markets** (7 × 00:00–23:59:59). The result is saved next to the data file and reused |
| `download_ohlcv(self, time_from, time_to, on_progress=None, limit=None, with_extra=False)` | `pyne data download`, `pyne run` provider mode, live warm-up, security contexts | fetch `[time_from, time_to]` (naive UTC datetimes) and write **closed** bars with `self.save_ohlcv_data(bar_or_list)`; call `on_progress(datetime)` now and then; `limit` is the user's `--chunk-size`; `with_extra` asks for extra per-bar fields (`.extra.csv`) |

Rules around `save_ohlcv_data`:

- Bars must arrive in strictly ascending timestamp order (Unix **milliseconds**); the writer
  rejects duplicates and OHLC inconsistencies. A continuation download deliberately overlaps the
  stored data: bars at or before `resume_timestamp` (set by the download flow) are dropped
  silently, so you may re-fetch from the last stored bar.
- One call is one durable publication (fsync). Pass a list per page; thousands of single-bar
  calls are slow.
- `extra_fields` on an `OHLCV` go to the `.extra.csv` sidecar, one row per bar.
- Never write the forming bar: `pyne data download` ends at "now" and a later `continue` never
  rewrites a stored bar.

Other base-class members worth knowing: `get_symbol_info(force_update=False)` (TOML cache),
`is_symbol_info_exists()`, `load_ohlcv_data()` → `OHLCVReader`, `resolve_symbol(pine_key)`
(plugin `symbol_map`, then `config/symbol_map.toml`), `construct_pair_symbol(from, to)` for
currency-rate lookups, `get_list_of_brokers()` (classmethod, `--list-brokers` of a
multi-broker provider; returns `Broker(id, name)` tuples), the context manager
(`with provider as writer:` opens the `.ohlcv` writer).

Errors: subclass `ProviderError` for user-actionable failures (unknown symbol, bad credentials);
the CLI prints them as one `Error: ...` line. `TransientProviderError` (or `retryable = True`)
marks faults worth retrying; long-running `--live` / `--broker` runs ride them out, a backtest
download fails fast. Anything else surfaces as a traceback.

## How the CLI drives a provider

| Command | Sequence |
|---------|----------|
| `pyne data download NAME:SYM@TF` | `ensure_config` → `cls(symbol, timeframe, ohlcv_dir=data/, config)` → `get_symbol_info()` (fetch and save the `.toml` first, so an interrupted download stays resumable) → `[download] provider = "..."` written into the `.toml` → `download_ohlcv(from, to)` inside `with provider:`; `from` defaults to `continue` (= last stored bar, or one year back / everything with `fetch_all_by_default`); a `from` before the file start truncates after a confirmation |
| `pyne data download NAME --list-symbols` | `cls(symbol=None or broker, timeframe, config)` **without `ohlcv_dir`** → `get_list_of_symbols()` |
| `pyne run SCRIPT NAME:SYM@TF` | fresh symbol info from the venue, then `download_ohlcv` for the last 500 bars (`-f -N` extends the window until N real bars exist), written to a temporary file that **replaces** `data/NAME_SYM_TF.ohlcv`; then the backtest |
| `pyne run ... --live` | as above, then `live_ohlcv_generator(provider, ...)` (next sections) |
| `request.security()` in live mode | a **security subprocess** builds its own instance from a `PluginSymbol(provider_name, symbol, timeframe, config, ...)`, downloads warm-up in memory and streams with the same contract; `syminfo` may be handed over so no second info request is needed |

`../../pynecore-strategy/scripts/run_paper.py` (the `pynecore-strategy` skill) follows the download → generator path too, but
appends to the cache file instead of replacing it.

## LiveProviderPlugin: the stream

```python
class MyfeedProvider(LiveProviderPlugin[MyfeedConfig]):
    feed_timeout_bars = 3            # None: no dead-feed watchdog (only for feeds that are legitimately silent)
    initial_connect_timeout = 30.0   # seconds for the first connect including transient retries
    reconnect_delay = 1.0            # backoff start; doubles up to max_reconnect_delay (60 s); retried forever
    max_reconnect_delay = 60.0

    async def connect(self) -> None: ...          # first connection AND every reconnect: rebuild all state here
    async def disconnect(self) -> None: ...
    @property
    def is_connected(self) -> bool: ...
    async def watch_ohlcv(self, symbol, timeframe) -> OHLCV: ...   # one update per call; is_closed=True closes the bar
    async def backfill_closed_bars(self, symbol, timeframe, since_ms) -> list[OHLCV]: return []   # optional
    async def on_disconnect(self) -> None: ...    # optional hooks
    async def on_reconnect(self) -> None: ...
    async def can_shutdown(self) -> bool: return True   # polled every second during graceful shutdown
```

- `watch_ohlcv` is called in a loop from a background thread's asyncio loop. Return an `OHLCV`
  with `is_closed=False` for a price update of the forming bar (same timestamp as the bar's
  open), `is_closed=True` when it closes. A new timestamp opens the next bar.
- Blocking SDK calls go through `asyncio.to_thread(...)` **with an enforceable timeout**
  (`asyncio.wait_for`); a worker thread that never returns keeps the interpreter alive after the
  run ended.
- `connect()` must be idempotent and complete: authentication, subscriptions, buffers. The
  runner calls `disconnect()` then `connect()` after any error or stale feed.
- Symbols: `watch_ohlcv` receives the symbol from the provider string (after `normalize_symbol`
  for prefixed forms); live `request.security()` contexts pass through `resolve_symbol`.

## What the live runner does with your provider

Verified in `pynecore/core/live_runner.py` (6.12) and by running the templates:

1. `live_ohlcv_generator(provider, symbol, timeframe, syminfo, last_historical_timestamp, ...)`
   starts a thread, runs `connect()` with retries inside `initial_connect_timeout` and **blocks
   until connected before the warm-up is consumed**, so no bar closing during the warm-up is
   lost. A non-retryable `ProviderError` at this point fails the run at once.
2. Right after connect, if at least two periods passed since `last_historical_timestamp`, it
   awaits `backfill_closed_bars(symbol, timeframe, last_historical_timestamp)` and splices the
   result between history and stream (bars that are not closed, or not after the last history
   bar, are ignored).
3. Bars that arrive before the consumer starts pulling are **catch-up**: processed as history
   with strategy orders suppressed; intra-bar updates in that phase are dropped. When the queue
   is empty `LIVE_TRANSITION` is emitted and the script switches to realtime.
4. **Each `watch_ohlcv()` call is awaited with `asyncio.wait_for(..., timeout)` where the
   timeout is at most 2 s** (and as low as 0.05 s when a bar close is overdue) and the coroutine
   is **cancelled** on expiry. A sleep longer than that never completes; a pending REST request
   loses its result. Keep all progress on `self` (the file template re-checks `self._next_emit_at`
   and sleeps at most 1.5 s), or run a background task that fills an `asyncio.Queue` and let
   `watch_ohlcv` only `await queue.get()` (the REST template).
5. Any exception raised by `watch_ohlcv` (or put into your queue and re-raised) is logged as
   `Connection error (attempt N, offline Ns)` and handled by `on_disconnect()` →
   `disconnect()` → backoff → `connect()` → `on_reconnect()`, **forever**; outside the
   symbol's session hours it waits for the next open instead. There is no give-up, so a
   permanent misconfiguration keeps reconnecting (the ccxt spot/`default_type` case).
6. **Feed watchdog**: no update for `feed_timeout_bars` periods (floor 90 s) during an open
   session → treated as a dead connection → reconnect cycle. Set `None` only for feeds that are
   silent by design (file replay).
7. **Idle-bar synthesis**: when no update arrived and `now >= last_closed.timestamp + 2·period +
   grace` (grace 15–30 s), the runner emits a zero-volume closed bar for the missed slot (so the
   series matches what REST history will later show). The check uses the **bar timestamp**, so
   streaming bars with old timestamps triggers synthesis immediately and then **drops every real
   bar whose timestamp is not newer than the synthesised one**. A replay of historical candles
   must therefore re-stamp them onto the current clock (first bar = the slot open now, then one
   period per bar); a paced replay with original timestamps cannot work with `pyne run --live`.
8. Dedup: updates with `timestamp < last_historical_timestamp` are dropped; closed bars with
   `timestamp <= last closed` are dropped; an update whose timestamp equals the last history bar
   is passed through so the runner can refine a partial warm-up bar. Intra-bar updates are
   capped when the consumer lags (closed bars never).
9. Shutdown (Ctrl+C, SIGTERM, generator closed): `can_shutdown()` polled every second up to
   `--shutdown-timeout`, then `disconnect()`; the thread is joined.

Timestamps are Unix milliseconds everywhere; `pynecore.lib.timeframe.in_seconds("60")` gives
the period length (3600.0) for your own arithmetic.

## CLIPlugin: commands and options

```python
class MyToolPlugin(CLIPlugin[MyConfig]):
    Config = MyConfig

    @staticmethod
    def cli():
        app = typer.Typer(help="My commands")
        @app.command()
        def status(): ...
        return app                               # mounted as: pyne mytool status

    @staticmethod
    def cli_params(command_name: str) -> list[CLIOption]:
        if command_name == "data download":
            return [CLIOption(("--my-flag", "-m"), is_flag=True, default=False, help="...")]
        return []
```

- Only `run` and `data download` accept injected options (`_PLUGGABLE_COMMANDS`). Option names
  that collide with built-in or other plugins' options are skipped with a warning; a CLI group
  named like a built-in command (`run`, `data`, ...) is skipped too.
- Describe options with `CLIOption` (decls, help, default, is_flag, type, choices, metavar,
  required, multiple, hidden, envvar, rich_help_panel); never build parser objects.
- **Where the values go (6.12)**: `data download` copies `ctx.plugin_params` onto the provider
  instance as `provider.plugin_params` before downloading. `run` parses the options (they show
  in `pyne run --help`) but never forwards them to any plugin. A plugin that needs per-run
  switches reads its TOML or environment variables instead.
- `Plugin.plugin_params` is a class attribute dict (`{}` unless set), read per instance.

## BrokerPlugin

`BrokerPlugin(LiveProviderPlugin)` adds order execution: `get_capabilities()` returning
`ExchangeCapabilities` with `CapabilityLevel`s, `execute_entry/exit/close/cancel(envelope)`,
`modify_entry/modify_exit`, `execute_cancel_with_outcome`, `get_position(symbol)`, balance and
order queries, `watch_orders`, an `account_id`, and a `RunContext` (`self.store_ctx`, SQLite
journal in `output/logs/broker.sqlite`) wired in by `ScriptRunner`. The engine owns idempotency,
retries, reconciliation and quarantine; the plugin owns the venue mapping. What the template
(`templates/pynecore-simbroker`, `sim-broker.md`) implements and the lab verifies:

| Rule | In the template |
|------|-----------------|
| `_account_id` is set in `__init__` | the run identity `<script>@<account>:<SYMBOL>:<TF>` is derived from it before `connect()` |
| one venue order per leg, client id `envelope.client_order_id(KIND_ENTRY / KIND_EXIT_TP / KIND_EXIT_SL / KIND_CLOSE)` | `_record()`; a repeated id with an open or filled order returns that order, a spent id raises `ClientOrderIdSpentError` |
| `ensure_venue_writes_allowed()` before every write | first line of `_record()`, `_cancel_matching()`, the amend helpers |
| a journal row for every placed leg | `store_ctx.upsert_order(coid, symbol=, side=, qty=, state=, intent_key=, exchange_order_id=, from_entry=, pine_entry_id=, filled_qty=)` |
| fills are incremental and carry a stable `fill_id` | `SimVenue._fill` emits `OrderEvent(fill_qty=<this fill>, fill_id="<order>:<n>")`; `watch_orders` drains them per owner |
| venue-driven cancels carry a reason | `CANCEL_REASON_VENUE_OCA` after a sibling fill, `CANCEL_REASON_VENUE_POSITION_CLEARED` for a reduce-only order with nothing left |
| anything else the venue did on its own is an intervention | `_on_bar` → `report_external_intervention(...)` plus a `cancelled` event with `from_disappearance_tracker=True`; the engine quarantines and prints the `pyne broker resume` command |
| exit amend after a partial fill | `remaining_qty = intent qty`, `qty = filled + intent qty`, missing legs added, dropped levels cancelled |
| capabilities are honest | `NATIVE` for what the venue matches itself, `SOFTWARE` for `cancel_all` |

Offline testing: `python -m pynecore.testing.broker_lab run SUITE --mode smoke|extended --seed N`
with a suite module exposing `build_suite(mode, seed)`; a `ReferenceVenueProfile` subclass whose
`create_broker(run_name, store_ctx)` returns the real plugin attached to a shared fake venue,
`Scenario(name, profile_factory, seed, steps, runs, expected_violation)` and `Step(kind, run,
values, check_invariants)`. Include a **control** scenario that breaks a rule on purpose with
`expected_violation`, so a silent oracle is noticed. Read the bundled
[../../pynecore-docs/docs/development/broker-plugin-authoring.md](../../pynecore-docs/docs/development/broker-plugin-authoring.md) (contract checklist) and
[../../pynecore-docs/docs/advanced/broker-plugin-testing.md](../../pynecore-docs/docs/advanced/broker-plugin-testing.md) (lab, demo-account matrix, release criteria) before writing a
plugin for a real venue; shipped examples are `pynesys-pynecore-bybit`, `-capitalcom`, `-ctrader`.

## Notifications and other side channels

There is no hook for "a bar closed" or "an order filled" outside the broker engine. What a
plugin can attach to:

| Channel | How to catch it | Notes |
|---------|-----------------|-------|
| `log.info/warning/error(...)` in scripts | `logging.getLogger("pyne_core_logger").addHandler(...)` | records carry the Pine time; `barstate.isrealtime` tells live from history |
| `[BROKER] ...` lines (connect, reconnect, fills, stops), quarantine notices | same logger, emitted by the runner and the broker engine | single-line records; the quarantine line contains the `pyne broker resume ...` command |
| `alert("text")` | wrap `pynecore.lib.alert.alert` (transformed scripts call `lib.alert.alert(...)` through the module) | the original prints `🚨 ALERT: ...` to stdout |
| Trades | the trade CSV (`output/<script>_trade.csv`, appended per fill) or `ScriptRunner.run_iter()` in your own runner | no event API for plugins |

The Telegram template implements the first three (see `telegram-notifier.md`). The handler is
installed at import time because the CLI imports plugins at startup; your own runner must
import the module (or call `install()`).

## Development loop

1. `python3 <skill>/scripts/new_plugin.py TEMPLATE DEST --name NAME`, edit `src/<pkg>/__init__.py`.
2. `pip install -e DEST` into the environment with `pyne` (editable: edits apply without reinstall).
3. `pyne plugin info NAME` (loads the class, shows `Needs PyneCore`, capabilities, config fields).
4. `<python> <skill>/scripts/plugin_check.py NAME --symbol S --timeframe TF --bars 50 [--live-seconds 90]`:
   abstract methods, constructor signature, symbol info completeness, a download into a temp
   folder, ascending and closed bars, then the live stream through PyneCore's own generator.
5. `pyne data download NAME:S@TF`, `pyne run demo NAME:S@TF`,
   `<python> <skill>/../pynecore-strategy/scripts/run_paper.py demo NAME:S@TF --bars 3`.
6. Deterministic live tests: the file template with `replay_bars`, or PyneCore's `replay` plugin
   with a JSON fixture (`config/plugins/replay.toml`, `fixture_path`; eager emission, so its bars
   are catch-up history).
7. Pin `pynesys-pynecore>=6.12,<7`; re-run step 4 after a PyneCore upgrade (the live-runner
   rules above are internal behaviour).

## Mistakes seen while building the templates

| Symptom | Cause | Fix |
|---------|-------|-----|
| `--list-symbols` lists files of the current directory | the provider used `cwd` because `ohlcv_dir` is `None` in listing mode | fall back to the CLI's `app_state.data_dir` or the config's `root` |
| Live replay logs `idle-bar synth emitted` and the strategy sees zero-volume bars, real bars vanish | historical timestamps older than two periods | re-stamp replayed bars onto the clock |
| `watch_ohlcv` never returns a bar, no error | it slept longer than the runner's 2 s timeout and was cancelled every time | keep progress on `self`; sleep ≤ 1.5 s per call; or task + queue |
| `does not have market symbol BTC/USDT`, reconnecting forever | ccxt.pro client built with `default_type = "swap"` for a spot pair | `default_type = "spot"` in `ccxt.toml`; or no websocket client at all (REST template) |
| Download stored a half-formed last bar | `download_ohlcv` forwarded the forming candle | return closed bars only (`candles[:-1]`, or `ts + period <= now`) |
| Notifier plugin silent under a programmatic runner | the module was never imported, or the runner replaced the logger's handlers | `--import pynecore_telegram` / `install()`; remove only PyneCore's own handler |
| Notifier found the wrong `config/plugins/<name>.toml` | the plugin resolved the workdir from `cwd` while the runner used `--workdir` | set `PYNE_WORK_DIR` for the run; the plugin reads `app_state` first when the CLI is present |
| `pyne run --myflag`: option accepted, plugin never sees it | `run` does not forward injected options in 6.12 | TOML / environment switches |
| First `pyne` call after installing the plugin is slow or prints a warning | heavy or failing import at plugin load | keep module import cheap; import SDKs inside methods |
| Broker lab `fill` step fails with `upsert_order ... missing required fields` | the plugin placed a leg without writing its journal row | `store_ctx.upsert_order(...)` with symbol, side, qty, state, intent_key, exchange_order_id for every leg |
| Lab oracle: coverage shortfall after a partial exit fill | exit amend reported the intent quantity as the order's total | `remaining_qty = intent qty`, `qty = filled + intent qty` |
| `pyne data download` through a delegating broker wrote an empty file | the CLI swapped the broker's writer after the feed had been pointed at the old one | re-point the feed's writer inside `download_ohlcv` |
| `file:SYMBOL@240` on an hourly file: "no source" | the resolver only looked for `SYMBOL_240.*` | also scan `SYMBOL_<tf>.*` and accept a `tf` that `validate_aggregation` allows |
| Replay at a higher timeframe starts with a torn first bar | the replay cut fell inside a target period | move the cut to the first source bar that opens a new target period (`Resampler.get_bar_time`) |
