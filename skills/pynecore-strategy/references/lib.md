# `pynecore.lib` reference: bar data, time, na helpers, math, collections, strings, state flags, output

Everything a script imports comes from `pynecore.lib` (functions, built-in series, namespaces)
and `pynecore.types` (annotations). `ta` and `strategy` have their own pages
([ta.md](ta.md), [strategy-api.md](strategy-api.md)).

## Contents

- [Bar data](#bar-data)
- [Time and date](#time-and-date)
- [na handling](#na-handling)
- [`math`](#math)
- [`barstate`](#barstate)
- [`timeframe`](#timeframe)
- [`syminfo`](#syminfo)
- [`session`](#session)
- [`array`, `map`, `matrix`](#array-map-matrix)
- [`string`](#string)
- [`color`](#color)
- [Output: the plot family](#output-the-plot-family)
- [Drawings: `label`, `line`, `box`, `table`, `polyline`, `linefill`](#drawings-label-line-box-table-polyline-linefill)
- [`log`, `alert`, `runtime`](#log-alert-runtime)
- [`request`](#request)
- [`extra_fields`](#extra_fields)
- [Constant namespaces](#constant-namespaces)

## Bar data

`from pynecore.lib import open, high, low, close, volume, hl2, hlc3, ohlc4, hlcc4, bar_index,
last_bar_index, last_bar_time`

All are series: `close` is the current bar's close, `close[1]` the previous one, `close[n]`
`n` bars back (`na` before enough history). `bar_index` starts at 0. `last_bar_index` /
`last_bar_time` are the dataset's last bar in a backtest (and the current bar when the runner
cannot know the end). Negative offsets never read the future: `close[-1]` is `na`.

## Time and date

`time`, `time_close`, `time_tradingday`, `timenow`, `updatetime`, `year`, `month`,
`weekofyear`, `dayofmonth`, `dayofweek`, `hour`, `minute`, `second`, `timestamp`.

Bare names are the current bar's values (UNIX **milliseconds** for times; calendar parts in the
exchange timezone `syminfo.timezone`). Called, they are functions:

| Call | Result |
|------|--------|
| `time(timeframe="", session=None, timezone=None, bars_back=0, timeframe_bars_back=0)` | Open time of the bar of `timeframe` containing this bar; `na` when `session` is given and the bar is outside it |
| `time_close(...)` same parameters | Close time |
| `time("D")`, `time("D", timeframe_bars_back=1)` | Today's open; yesterday's open |
| `time("", "0930-1600", "America/New_York")` | `na` outside the session: `in_session = not na(time("", sess, tz))` |
| `time_tradingday` | 00:00 UTC of the trading day the bar belongs to |
| `timenow` | Current time; pinned to the last bar's open during `pyne run` backtests |
| `year(t=None, timezone=None)`, `month(...)`, `dayofweek(...)`, `hour(...)`, ... | Parts of `t` (default: current bar) |
| `timestamp("2024-01-05")`, `timestamp("2024-01-05T15:30:00+02:00")` | Date string to ms (no offset = UTC) |
| `timestamp(2024, 1, 5, 9, 30)`, `timestamp("America/New_York", 2024, 1, 5, 9, 30)` | Components, exchange timezone unless given |

Session strings: `"0930-1600"`, `"0930-1600:23456"` (days: 1 = Sunday ... 7 = Saturday),
`"0400-0700,0900-1300:23456"`, `"2200-0200"` (crosses midnight), `"24x7"`, `""`/`"regular"`/
`"extended"` (the symbol's own hours).

`dayofweek` is both the bar's weekday and a namespace of constants (`dayofweek.monday` ... ,
Sunday = 1). Import an alias if you need both: `from pynecore.lib import dayofweek as dow`.

Date-range filter idiom:

```python
in_range = time >= start_ts and time <= end_ts
```

## na handling

`na`, `na(x)`, `nz(x, replacement=0.0)`, `fixnan(x)`.

- `na` written bare is the missing value (`x: float = close if ok else na`). A numeric `na` is
  `float('nan')`; a string `na` is `""`; bool has no `na` (it is `False`).
- `na(x)` is true for `nan`, `inf`, `-inf`, `""` and `NA` objects. Never compare `x == na`.
- `nz(x)` replaces `na` with `0.0` (or the given replacement); `nz(high[1], high)`.
- `fixnan(x)` holds the last non-`na` value; it is stateful, call it every bar.

Arithmetic propagates `na`; every comparison with `na` is `False`; a numeric `na` as a
condition counts as `False` inside Pyne code.

## `math`

`abs`, `sign`, `ceil`, `floor`, `round(x, precision=None)`, `round_to_mintick(x)`,
`max(*xs)`, `min(*xs)`, `avg(*xs)`, `sum(source, length)`, `sqrt`, `pow`, `exp`, `log`,
`log10`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `toradians`, `todegrees`,
`random(min=0, max=1, seed=na)`; constants `math.e`, `math.pi`, `math.phi`, `math.rphi`.

`math.sum(volume, 20)` is a rolling sum (stateful). `math.round_to_mintick(price)` snaps a
computed level to the symbol's tick grid before using it as a limit/stop price.

## `barstate`

`barstate.isfirst`, `islast`, `isconfirmed`, `ishistory`, `isrealtime`, `isnew`,
`islastconfirmedhistory`.

Backtest: `ishistory`, `isconfirmed`, `isnew` are `True` on every bar, `isrealtime` `False`,
`islast` `True` only on the final bar. Live mode: historical bars as above with `islast` `False`,
then `isrealtime` `True`, `isconfirmed` only on the closing update of a bar, `isnew` on the first
update of a bar. Use `barstate.islast` for one-time summaries (a final table, a log line).

## `timeframe`

`timeframe.period` (`"60"`, `"D"`), `timeframe.multiplier`, `timeframe.main_period`,
`timeframe.change("D")` (true on the first bar of each new period of that timeframe, must be the
chart's or larger), `timeframe.in_seconds("240")`, `timeframe.from_seconds(3600)`,
`timeframe.isintraday()`, `isminutes()`, `isseconds()`, `isdaily()`, `isweekly()`,
`ismonthly()`, `isdwm()`.

## `syminfo`

`syminfo.tickerid` / `ticker` (`"PREFIX:SYMBOL"`), `prefix`, `root`, `description`, `currency`,
`basecurrency`, `type`, `period`, `timezone`, `session`, `volumetype`, `mintick`, `pricescale`,
`minmove`, `pointvalue`, `mincontract`, `country`, `sector`, `industry`, analyst target fields.

Values come from the data file's `.toml`. Tick math: `ticks = distance / syminfo.mintick`.

## `session`

`session.ismarket`, `ispremarket`, `ispostmarket`, `isfirstbar`, `islastbar`,
`isfirstbar_regular`, `islastbar_regular`; constants `session.regular`, `session.extended`.
Driven by the symbol's opening hours in its `.toml`; for 24/7 crypto `ismarket` is always true.

## `array`, `map`, `matrix`

Arrays are Python lists; maps are dicts; matrices are `pynecore.types.Matrix`. Both the
functional API and native Python operations work on them.

Creation: `array.new_float(size=0, initial_value=na)`, `new_int`, `new_bool`, `new_string`,
`new_color`, `new_label`, `new_line`, `new_box`, `new_table`, `new_linefill`,
`array.from_items(1.0, 2.0)`.

Access/modify: `get`, `set`, `push`, `pop`, `shift`, `unshift`, `insert`, `remove`, `clear`,
`fill`, `size`, `first`, `last`, `slice` (a live view), `copy`, `concat`, `reverse`,
`sort(id, order.ascending)`, `sort_indices`, `join`.

Search: `includes`, `indexof`, `lastindexof`, `binary_search`, `binary_search_leftmost`,
`binary_search_rightmost`.

Statistics: `sum`, `avg`, `min(id, nth=0)`, `max(id, nth=0)`, `median`, `mode`, `stdev`,
`variance`, `covariance`, `range`, `abs`, `standardize`, `percentile_linear_interpolation`,
`percentile_nearest_rank`, `percentrank`, `every`, `some`.

Map: `map.new()`, `put`, `get`, `contains`, `remove`, `clear`, `size`, `keys`, `values`,
`copy`, `put_all`.

A collection that must survive across bars is a `Persistent[list]` / `Persistent[dict]`
declared inside `main()`:

```python
closes: Persistent[list] = array.new_float(0)
array.push(closes, close)
if array.size(closes) > 20:
    array.shift(closes)
```

## `string`

The namespace is `string` (not `str`): `from pynecore.lib import string`.
`tostring(value, format=None)`, `format("{0} / {1}", a, b)`, `format_time(ms, "yyyy-MM-dd HH:mm", tz)`,
`tonumber`, `length`, `contains`, `startswith`, `endswith`, `pos`, `substring`, `replace`,
`replace_all`, `split`, `upper`, `lower`, `trim`, `match` (regex), `repeat`.

Ints are floats at run time, so an f-string prints `bar=12.0`; use `string.tostring(bar_index)`
or a format spec (`f"{bar_index:.0f}"`) in text.

## `color`

Constants: `color.aqua`, `black`, `blue`, `fuchsia`, `gray`, `green`, `lime`, `maroon`,
`navy`, `olive`, `orange`, `purple`, `red`, `silver`, `teal`, `white`, `yellow`.
Functions: `color.new(c, transp)`, `color.rgb(r, g, b, transp=0)`,
`color.from_gradient(value, bottom, top, bottom_color, top_color)`, `color.r/g/b/t(c)`.
Type: `from pynecore.types import Color` (or `color.Color`).

## Output: the plot family

`plot`, `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `plotbar`, `bgcolor`, `barcolor`,
`fill`, `hline`, `alertcondition`.

**Rule:** every call of this family must be in the direct body of `main()`, unconditional, on
every bar, before any early `return`. A call inside `if`, a loop, a helper or a conditional
expression is a `SyntaxError` at load time. Make the **value** conditional instead:
`plot(level if show else na, "Level")`, `bgcolor(color.new(color.red, 85) if alert else na)`.

| Call | CSV column | Notes |
|------|------------|-------|
| `plot(series, title=None, color=None, linewidth=1, style=None, trackprice=False, histbase=0.0, offset=0, join=False, show_last=None, display=None, format=None, precision=None, force_overlay=False)` | yes, `title` | returns a `Plot` for `fill()`; styles `plot.style_line`, `style_linebr`, `style_stepline`, `style_steplinebr`, `style_stepline_diamond`, `style_histogram`, `style_columns`, `style_area`, `style_areabr`, `style_circles`, `style_cross` (`plot.linestyle_solid/dashed/dotted` are defined but `plot()` takes no `linestyle` in 6.12.1) |
| `plotshape(series, title=None, style=shape.xcross, location=location.abovebar, color=None, text=None, textcolor=None, size=size.auto, offset=0, ...)` | yes, 1/0 for bools or the raw number | marker where `series` is true |
| `plotchar(series, title=None, char="★", location=..., color=..., text=..., ...)` | yes | |
| `plotarrow(series, title=None, colorup=None, colordown=None, minheight=5, maxheight=100, ...)` | yes | |
| `plotcandle(o, h, l, c, title=None, color=None, wickcolor=None, bordercolor=None, ...)`, `plotbar(o, h, l, c, ...)` | four columns `<title> (open)` ... | |
| `bgcolor(color, offset=0, ...)`, `barcolor(color, ...)` | no | `na` leaves the bar unpainted |
| `fill(plot1, plot2, color)`, `fill(hline1, hline2, color)`, gradient form `fill(p1, p2, top_value, bottom_value, top_color, bottom_color)` | no | |
| `hline(price, title="", color=color.blue, linestyle=hline.style_solid, linewidth=1)` | no | constant color only; `hline.style_solid/dashed/dotted` |
| `alertcondition(condition, title=None, message=None)` | no | accepted, does nothing |

Alternatively `main()` returns a dict `{"Title": value}`; both mechanisms can be combined (an
indicator's whole output side, settings and checks: [indicators.md](indicators.md)). Plot
values are what `ScriptRunner.run_iter()` yields as `plot_data` and what the plot CSV contains,
so plotting a signal (`plot(1 if long_signal else 0, "enter_long")`) is the way to export
decisions to external code.

## Drawings: `label`, `line`, `box`, `table`, `polyline`, `linefill`

Full object APIs exist (`label.new(x, y, text, ...)`, `line.new(x1, y1, x2, y2, ...)`,
`box.new(left, top, right, bottom, ...)`, `table.new(position, columns, rows, ...)` +
`table.cell(...)`, `polyline.new(points, ...)`, `linefill.new(l1, l2, color)`, with
`set_*`/`get_*`/`delete`/`copy` and `.all`; coordinates by `xloc.bar_index` or `xloc.bar_time`,
`chart.point` objects for the point forms). PyneCore draws nothing itself; drawings are state
that `--viz` exports as NDJSON for a renderer, and objects past `max_lines_count` /
`max_labels_count` / `max_boxes_count` / `max_polylines_count` (default 50) are deleted oldest
first. Signatures, the NDJSON format, reading it in Python, `scripts/viz_inspect.py` and the
verified behaviour: [drawings-and-viz.md](drawings-and-viz.md).

## `log`, `alert`, `runtime`

- `log.info("Bar {0}: close={1}", bar_index, close)`, `log.warning(...)`, `log.error(...)`:
  console lines with timestamp and bar index. Placeholders are `{0}`, `{1}`. Gate them
  (`if bar_index % 100 == 0`, `if signal`) to avoid thousands of lines; they appear in
  `pyne run` output (and `output/logs/`). Suppressed inside security contexts unless
  `PYNE_SECURITY_LOG` is set.
- `alert("message", freq=alert.freq_once_per_bar)` prints a highlighted line; `freq` is
  accepted but not enforced. For real notifications call your own Python (HTTP, Telegram, ...)
  from the script, guarded with `barstate.isrealtime` so history does not spam.
- `runtime.error("message")` stops the run with that message.
- `print()` works too but has no bar context.

## `request`

`request.security(...)`, `request.security_lower_tf(...)`, `request.currency_rate(from, to)`
are implemented (see [multi-timeframe.md](multi-timeframe.md)). `request.dividends`,
`splits`, `earnings`, `financial`, `economic`, `quandl`, `seed`, `footprint` have no data
source: most return `na` only with `ignore_invalid_symbol=True`, otherwise they raise.

## `extra_fields`

Columns beyond OHLCV in a converted CSV are available per bar in the dict `extra_fields`:

```python
from pynecore.lib import extra_fields
sentiment: Series[float] = extra_fields["sentiment"]     # the annotation gives it history
regime: str = extra_fields["regime"]
```

Numeric columns come as floats (`nan` for empty), others as strings. Provider extras (bid/ask)
appear the same way when downloaded with `--extra-data`.

## Constant namespaces

| Namespace | Members |
|-----------|---------|
| `display` | `all`, `none`, `pane`, `data_window`, `price_scale`, `status_line`, `pine_screener` (combinable with `+`) |
| `format` | `inherit`, `price`, `volume`, `percent`, `mintick` |
| `location` | `abovebar`, `belowbar`, `top`, `bottom`, `absolute` |
| `shape` | `xcross`, `cross`, `circle`, `triangleup`, `triangledown`, `flag`, `arrowup`, `arrowdown`, `square`, `diamond`, `labelup`, `labeldown` |
| `size` | `auto`, `tiny`, `small`, `normal`, `large`, `huge` |
| `position` | `top_left`, `top_center`, `top_right`, `middle_*`, `bottom_*` |
| `xloc` / `yloc` | `bar_index`, `bar_time` / `price`, `abovebar`, `belowbar` |
| `extend` | `none`, `left`, `right`, `both` |
| `order` | `ascending`, `descending` |
| `barmerge` | `gaps_off`, `gaps_on`, `lookahead_off`, `lookahead_on`, `lookahead_last_closed` |
| `currency` | `USD`, `EUR`, `USDT`, `BTC`, `ETH`, ... `NONE` |
| `text` | `align_left/center/right/top/bottom`, `wrap_none/auto`, `format_none/bold/italic` |
| `font` | `family_default`, `family_monospace` |
| `scale` | `right`, `left`, `none` |
| `line` / `polyline` / `hline` styles | `style_solid`, `style_dotted`, `style_dashed`, plus `style_arrow_left/right/both` for `line` and `polyline` |
| `label` styles | `style_label_up/down/left/right/center`, `style_label_upper_left/upper_right/lower_left/lower_right`, `style_arrowup/arrowdown`, `style_triangleup/triangledown`, `style_circle`, `style_square`, `style_diamond`, `style_flag`, `style_cross`, `style_xcross`, `style_text_outline`, `style_none` |
| `chart` | `chart.point` (constructors `new/from_index/from_time/now/copy`), `bg_color` `#000000`, `fg_color` `#FFFFFF`, `is_standard` `True`, `is_heikinashi/is_kagi/is_linebreak/is_pnf/is_range/is_renko` `False`, `right_visible_bar_time` = the last bar's time, `left_visible_bar_time` = 20 periods before it (verified) |
