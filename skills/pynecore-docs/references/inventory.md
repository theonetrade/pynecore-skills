# PyneCore documentation inventory

Every page of the documentation snapshot in this skill's `docs/` folder. The documented
PyneCore version is in `docs/MANIFEST.json`; `scripts/sync_docs.py --tag v<version>` refreshes
the snapshot and keeps this layout. The online copy of `section/page.md` is
`https://pynecore.org/docs/section/page/`.

## Top level

| Page | Title |
|------|-------|
| `README.md` | PyneCore Documentation (index) |
| `scripting.md` | Scripting with PyneCore: script structure, inputs, Series/Persistent, module-level objects, output methods, patterns |
| `strategy.md` | Strategy Development: decorator settings, `.toml`, entries/exits, sizing, properties, examples, backtesting outputs, best practices |
| `debugging.md` | Debugging: `log.*`, limiting output, IDE debugging and `__state__`, `pyne debug ast`, `PYNE_AST_*`, security-context logs |
| `profiling.md` | Profiling: `pyne run --profile`, reading the numbers, the profile JSON |
| `faq.md` | FAQ |

## overview/

| Page | Title |
|------|-------|
| `overview/README.md` | Overview |
| `overview/what-is-pynecore.md` | What is PyneCore |
| `overview/pyne-code.md` | What is Pyne code: Python syntax, Pyne execution rules, the `edge` subset |
| `overview/core-concepts.md` | Core Concepts: AST transformation, Persistent/IBPersistent, Series, function isolation, `na` |
| `overview/compatibility.md` | Compatibility: feature status per module, "no lookahead, ever", precision, resolved limitations |
| `overview/differences.md` | Differences from the upstream language: structure, scope, types, `na`, arrays/maps |
| `overview/configuration.md` | Configuring PyneCore: workdir layout, symbol `.toml`, script `.toml`, symbol map, environment variables |
| `overview/project-structure.md` | Project Structure of the PyneCore package: core, transformers, lib, types, cli, providers |
| `overview/symbol-map.md` | Symbol Map: `config/symbol_map.toml` schema, precedence, backtest vs live |
| `overview/ecosystem.md` | Ecosystem: PyneCore, compiler service, Edge, validation report |
| `overview/versioning.md` | Versioning Policy |

## getting-started/

| Page | Title |
|------|-------|
| `getting-started/README.md` | Getting Started |
| `getting-started/installation.md` | Installing PyneCore: pip extras, workdir, demo data |
| `getting-started/first-script.md` | Your First PyneCore Script: marker, indicator, inputs, Series, Persistent, strategy, functions, standalone bootstrap |
| `getting-started/ide-setup.md` | IDE Setup: PyCharm, VS Code + Pylance `pyrightconfig.json`, stubs |

(The folder also holds a page about converting scripts from another language through the
compiler service; it is not relevant to Python-only work.)

## cli/

| Page | Title |
|------|-------|
| `cli/README.md` | Command Line Interface |
| `cli/basics.md` | CLI Basic Usage: command structure, global options, workdir, path handling, environment variables |
| `cli/run.md` | Running Scripts: `pyne run` arguments, provider mode, options, `--timeframe`, `--security`, profiling, outputs, troubleshooting |
| `cli/data.md` | Data Management: `download`, `convert-to`, `convert-from`, `aggregate`, file naming, `.ohlcv` format, providers |
| `cli/symbol-browser.md` | Symbol Browser TUI for `pyne data download` |
| `cli/broker.md` | Broker Run Management: quarantine, `pyne broker resume` |

`cli/compile.md` (`pyne compile`, the cloud compiler for scripts written in another language) is
excluded from the snapshot (`docs/MANIFEST.json` → `excluded`), so `find_docs.py --page` does not
find it; `find_docs.py --online cli/compile.md` prints its online URL, and the option reference is
in [the pynecore-cli skill](../../pynecore-cli/references/commands.md#pyne-compile).

## reference/

| Page | Title |
|------|-------|
| `reference/README.md` | Reference |
| `reference/script-format.md` | Script Format: marker, imports, decorators and their parameters, `main()`, inputs, return value, reserved names |
| `reference/types.md` | Type System: int/float/bool/string/color/na, Series, Persistent, IBPersistent, array/matrix/map, drawings, UDT, enums, Source |
| `reference/inputs.md` | Input Functions: every `input.*` with parameters |
| `reference/lib/README.md` | Library Reference index |
| `reference/lib/builtins.md` | Global functions: `time`, `time_close`, `timestamp`, date parts, session strings, `na`/`nz`/`fixnan`, `max_bars_back`, plot family |
| `reference/lib/ta.md` | `ta`: all 67 technical analysis functions |
| `reference/lib/strategy.md` | `strategy`: entry/exit/close/order/cancel, fill timing, variables, constants, compatibility |
| `reference/lib/strategy_opentrades.md` | `strategy.opentrades` |
| `reference/lib/strategy_closedtrades.md` | `strategy.closedtrades` |
| `reference/lib/strategy_risk.md` | `strategy.risk` |
| `reference/lib/request.md` | `request`: security, security_lower_tf, currency_rate, unsupported feeds |
| `reference/lib/math.md` | `math` |
| `reference/lib/array.md` | `array` |
| `reference/lib/map.md` | `map` |
| `reference/lib/matrix.md` | `matrix` |
| `reference/lib/str.md` | `string` (named `str` in the page) |
| `reference/lib/color.md` | `color` |
| `reference/lib/barstate.md` | `barstate` |
| `reference/lib/timeframe.md` | `timeframe` |
| `reference/lib/syminfo.md` | `syminfo` |
| `reference/lib/session.md` | `session` |
| `reference/lib/chart.md` | `chart`: type flags, colors, visible range, `chart.point` |
| `reference/lib/plot.md` | `plot`: parameters, style constants, unconditional-call rule |
| `reference/lib/hline.md` | `hline` |
| `reference/lib/label.md` | `label` |
| `reference/lib/line.md` | `line` |
| `reference/lib/box.md` | `box` |
| `reference/lib/table.md` | `table` |
| `reference/lib/polyline.md` | `polyline` |
| `reference/lib/linefill.md` | `linefill` |
| `reference/lib/log.md` | `log` |
| `reference/lib/alert.md` | `alert` |
| `reference/lib/runtime.md` | `runtime` |
| `reference/lib/ticker.md` | `ticker` (Heikin Ashi and other chart-type tickers) |
| `reference/lib/footprint.md` | `footprint` (no data source) |
| `reference/lib/volume_row.md` | `volume_row` |
| `reference/lib/adjustment.md` | `adjustment` constants |
| `reference/lib/barmerge.md` | `barmerge` constants |
| `reference/lib/currency.md` | `currency` constants |
| `reference/lib/dayofweek.md` | `dayofweek` constants |
| `reference/lib/display.md` | `display` constants |
| `reference/lib/dividends.md` | `dividends` constants |
| `reference/lib/earnings.md` | `earnings` constants |
| `reference/lib/extend.md` | `extend` constants |
| `reference/lib/font.md` | `font` constants |
| `reference/lib/format.md` | `format` constants |
| `reference/lib/location.md` | `location` constants |
| `reference/lib/order.md` | `order` constants |
| `reference/lib/pivotpointtype.md` | `pivotpointtype` constants |
| `reference/lib/position.md` | `position` constants |
| `reference/lib/scale.md` | `scale` constants |
| `reference/lib/shape.md` | `shape` constants |
| `reference/lib/size.md` | `size` constants |
| `reference/lib/splits.md` | `splits` constants |
| `reference/lib/text.md` | `text` constants |
| `reference/lib/xloc.md` | `xloc` constants |
| `reference/lib/yloc.md` | `yloc` constants |

## lib/

| Page | Title |
|------|-------|
| `lib/README.md` | Library |
| `lib/reference.md` | Library Reference: usage, module system, combining functional and Python styles, examples |
| `lib/request-security.md` | `request.security()`: data preparation, key matching, lookahead/gaps, confirmation timing, lower timeframe, dependent calls, limitations |
| `lib/request-data.md` | `request.currency_rate()` and corporate events |

## programmatic/

| Page | Title |
|------|-------|
| `programmatic/README.md` | Programmatic Usage |
| `programmatic/script-runner.md` | ScriptRunner API: constructor, inputs/settings overrides, precedence, security data, `run_iter`, live mode, Trade fields, CSV and viz output |
| `programmatic/data-and-syminfo.md` | Data & SymInfo: `OHLCV`, converters, readers, `SymInfo` fields, session schedules, extended hours, period values |
| `programmatic/integration-patterns.md` | Integration Patterns: DataFrame bridge, FreqTrade, live feed, multiple indicators, parameter optimization |
| `programmatic/visual-output.md` | Visual Output (viz NDJSON): format, ordinal ids, journal, accessors |

## advanced/

| Page | Title |
|------|-------|
| `advanced/README.md` | Advanced |
| `advanced/ast-transformations.md` | AST Transformation: marker modes, pipeline steps, reserved names, each transformer |
| `advanced/function-isolation.md` | Function Isolation: state vectors, call routing, loops, methods |
| `advanced/bar-magnifier.md` | Bar Magnifier: intrabar fills, `--timeframe`, `calc_on_order_fills`, `calc_on_every_history_tick` |
| `advanced/live-mode.md` | Live Mode: phases, barstate values, intra-bar updates, order processing, CLI options, limitations |
| `advanced/extra-fields.md` | Extra Fields: extra CSV columns, sidecar format |
| `advanced/request-security-internals.md` | `request.security()` Internals: processes, shared memory, transformation |
| `advanced/ohlcv-reader-writer.md` | OHLCV Reader/Writer: binary format |
| `advanced/csv-reader-writer.md` | Fast CSV Reader/Writer |
| `advanced/broker-plugin-testing.md` | How Broker Plugins Are Tested |

## development/

| Page | Title |
|------|-------|
| `development/README.md` | Development |
| `development/plugin-system.md` | Plugin System: providers, brokers, discovery |
| `development/broker-plugin-authoring.md` | Broker Plugin Authoring Guide |
| `development/testing-system.md` | Testing System Overview |
| `development/contributing.md` | Contributing to PyneCore |
| `development/tradingview-time-bars-back.md` | `time()` / `time_close()` with `timeframe_bars_back` on D, W and M grids |
