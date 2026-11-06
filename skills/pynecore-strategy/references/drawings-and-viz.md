# Drawings and the viz NDJSON stream

PyneCore renders no chart. Drawing objects (`line`, `label`, `box`, `table`, `polyline`,
`linefill`) and the styles of the plot family are **state**: the script creates and mutates it,
and `pyne run --viz` (or `ScriptRunner(viz_path=...)`) serializes it to an NDJSON file for a
renderer or an analysis script of your own. `scripts/viz_inspect.py` summarizes such a file
without a renderer. Everything below was verified on PyneCore 6.12.1 with the probe described at
the end; the bundled documentation page is `programmatic/visual-output.md` (`pynecore-docs`).

## 1. Drawing objects

| Object | Create | Mutate / read | Constants |
|--------|--------|---------------|-----------|
| `line` | `line.new(x1, y1, x2, y2, xloc=xloc.bar_index, extend=extend.none, color=color.blue, style=line.style_solid, width=1, force_overlay=False)` or `line.new(point1, point2, ...)` with `chart.point`s | `set_x1/x2/y1/y2/xy1/xy2/xloc/extend/color/style/width/first_point/second_point`, `get_x1/y1/x2/y2`, `get_price(id, x)` (extrapolates along the line), `delete`, `copy`, `line.all` | `line.style_solid/dotted/dashed/arrow_left/arrow_right/arrow_both`; `extend.none/left/right/both`; `xloc.bar_index/bar_time` |
| `label` | `label.new(x, y, text="", xloc=, yloc=yloc.price, color=color.blue, style=label.style_label_down, textcolor=color.white, size=size.normal, textalign=text.align_center, tooltip="", text_font_family=, force_overlay=, text_formatting=)` or `label.new(point, text, ...)` | `set_text/x/y/xy/point/xloc/yloc/color/style/textcolor/size/textalign/tooltip/text_font_family/text_formatting`, `get_text/x/y`, `delete`, `copy`, `label.all` | 21 `label.style_*` (`label_up/down/left/right/center`, `arrowup/down`, `triangleup/down`, `circle`, `square`, `diamond`, `flag`, `cross`, `xcross`, `text_outline`, `none`, ...); `yloc.price/abovebar/belowbar`; `size.tiny/small/normal/large/huge/auto` |
| `box` | `box.new(left, top, right, bottom, border_color=color.blue, border_width=1, border_style=line.style_solid, extend=, xloc=, bgcolor=color.blue, text="", text_size=size.auto, text_color=color.black, text_halign=, text_valign=, text_wrap=, text_font_family=, force_overlay=, text_formatting=)` or `box.new(top_left, bottom_right, ...)` | `set_left/top/right/bottom/lefttop/rightbottom/top_left_point/bottom_right_point/xloc/extend/bgcolor/border_*/text*`, `get_left/top/right/bottom`, `delete`, `copy`, `box.all` | `text.align_left/center/right/top/bottom`, `text.wrap_none/auto`, `text.format_none/bold/italic` |
| `table` | `table.new(position, columns, rows, bgcolor=None, frame_color=None, frame_width=0, border_color=None, border_width=0, force_overlay=False)` | `table.cell(t, col, row, text="", width=0, height=0, text_color=color.black, text_halign=, text_valign=, text_size=size.normal, bgcolor=None, tooltip="", text_font_family=, text_formatting=)`, `cell_set_*`, `merge_cells`, `clear`, `set_position/bgcolor/border_*/frame_*`, `delete`, `table.all` | `position.top_left/top_center/top_right/middle_left/middle_center/middle_right/bottom_left/bottom_center/bottom_right` |
| `polyline` | `polyline.new(points, curved=False, closed=False, xloc=, line_color=color.blue, fill_color=None, line_style=polyline.style_solid, line_width=1, force_overlay=False)` with a `list` of `chart.point` | `delete`, `polyline.all` (no setters: recreate) | |
| `linefill` | `linefill.new(line1, line2, color)` | `set_color`, `get_line1/get_line2`, `delete`, `linefill.all` | |
| `chart.point` | `chart.point.new(time, index, price)`, `chart.point.from_index(index, price)`, `chart.point.from_time(time, price)`, `chart.point.now(price)` (current bar), `chart.point.copy(p)` | fields `.time`, `.index`, `.price` | a point made with `from_index` has `time = na` and vice versa; the object's `xloc` decides which field is used |

Rules that matter in practice:

- **Coordinates.** `x` is a bar index (`xloc.bar_index`, the default) or a bar open time in
  milliseconds (`xloc.bar_time`, use `time`); floats are truncated, `na` stays `na`. `y` is a
  price, untouched. A future `x` (`bar_index + 10`) is allowed.
- **Handles across bars** live in `Persistent` variables (`trend: Persistent[line.Line | None] =
  None`); a handle created in one bar and mutated on the next is the normal way to extend a
  trend line, move a box or refresh a table. `table.new` once, `table.cell` every bar.
- **Object caps.** `@script.indicator/strategy(max_lines_count=, max_labels_count=,
  max_boxes_count=, max_polylines_count=)`, default 50 each, upper bound 500 (also the fallback
  in `request.security` contexts). Past the cap the **oldest object is deleted** (FIFO), so a
  label-per-signal script keeps only the last N labels; delete explicitly (`box.delete(old)`)
  when the replacement policy should be yours. `linefill` and `table` have no cap.
- **Libraries.** Inside a library's `main()` every `*.new` returns `na`; drawings belong to the
  script.
- **Cost.** Each object is a Python dataclass plus a registry entry; with `--viz-journal` every
  mutated object is re-serialized per bar. In a backtest-only strategy prefer `plot()` columns
  and `log.*`; draw when a renderer or an analysis will read the NDJSON.
- **Plot family** (`plot`, `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `plotbar`,
  `bgcolor`, `barcolor`, `hline`, `fill`) stays unconditional in `main()`; its styles (colors,
  widths, shapes) exist **only** in the viz stream, the plot CSV holds the numbers.

## 2. Producing the stream

```bash
pyne run my_script DATA --viz                      # output/<script>_viz.ndjson
pyne run my_script DATA --viz-path out/viz.ndjson  # explicit path (implies --viz)
pyne run my_script DATA --viz-journal              # + per-bar create/update/delete events (implies --viz)
```

`--no-plot` (no plot CSV) and `--viz` are independent. In Python: `ScriptRunner(script, bars,
syminfo, viz_path=Path(...), viz_journal=True)`; with `viz_journal=True` and **no** `viz_path`
the journal stays in memory and is handed to `runner.viz_events = callback` as one list of
events per bar. After the run (or inside the loop) `runner.plot_meta` is `{id: PlotMeta}` (kind,
title, color, style, ...), `runner.drawings()` the same snapshot the file ends with, and the
registries `line.all()`, `label.all()`, ... are still readable. The stream is appended bar by
bar, so in live mode it grows without bound like the plot CSV.

## 3. The format (version 2)

One JSON object per line, tagged by `"t"`. Order: `hdr`, the initial `meta` records, then `bar`
records (each followed by that bar's `ev` records when journaling; a `meta` may be re-emitted
before a bar), then `drawings`, then `end`.

| Record | Fields (verified) |
|--------|-------------------|
| `hdr` | `v: 2`; `script: {type, title, shorttitle, overlay, format, precision, explicit_plot_zorder, max_lines_count, max_labels_count, max_boxes_count, max_polylines_count}`; `syminfo: {tickerid, prefix, ticker, timeframe, mintick, timezone, type}`; `journal: bool` |
| `meta` | `id`, `kind` (`plot`, `shape`, `char`, `arrow`, `candle`, `bar`, `bgcolor`, `barcolor`, `hline`, `fill`), `title`, `display` (list of locations), `dynamic: true` when the color varies per bar, then kind-specific style: `style`, `color` (`#RRGGBBAA`), `linewidth`, `location`, `size`, `char`, `price`, `linestyle`, `plot1/plot2`, `hline1/hline2`, ... A **repeated `id` replaces** the earlier record (a plot becomes dynamic later, a fill's color changes) |
| `bar` | `i` (0-based bar index), `time` (ms), `v: {plot_id: value}` for the plots that fired (`null` for `na`; bool markers as `0`/`1`), `c: {plot_id: color}` **only for colors that changed** since the last emitted value (`null` = unpainted `bgcolor`/`barcolor`) |
| `ev` | journal only: `i`, `op` (`create`, `update`, `delete`), `obj` (`line`, `label`, `box`, `table`, `polyline`, `linefill`), `id` (the object's `vid`), `s` (full state, omitted on `delete`) |
| `drawings` | end-of-run snapshot: `lines`, `labels`, `boxes`, `tables` (with `cells`, merged ranges as `merge: [c0, r0, c1, r1]`), `polylines` (`points: [{index, time, price}]`), `linefills` (`line1`, `line2` ids plus `line1_state`, `line2_state`) |
| `end` | `bars`: number of `bar` records |

Ids: a plot's id is its **title** (duplicate titles get a numeric suffix); `plotcandle`/`plotbar`
add `"<title> (open|high|low|close)"` keys to `v`; title-less calls get per-family ordinals in
call order, reset every bar: `bgcolor#0`, `barcolor#0`, `hline#0`, `fill#0`. Drawing ids are
`vid`s, monotonically increasing across all types; `copy()` gets a fresh one.

Colors: a plot's first color is its static `meta.color`; later colors are compared by object
identity, so `color.lime if up else color.red` makes the plot **dynamic** and its color is then
written to `c` whenever the encoded value changes. A reader carries the last emitted color
forward and falls back to `meta.color` before the first change.

Serialized states (as the probe produced them):

```json
{"id":4,"x1":10,"y1":79970.0,"x2":283,"y2":76530.0,"xloc":"bar_index","extend":"right","color":"#9C27B0FF","style":"solid","width":2,"force_overlay":false}
{"id":13,"x":90,"y":80704.86,"text":"up","xloc":"bar_index","yloc":"belowbar","color":"#4CAF50FF","style":"label_up","textcolor":"#FFFFFFFF","size":"normal","textalign":"center","tooltip":"","text_font_family":"default","force_overlay":false,"text_formatting":[]}
{"id":42,"left":270,"top":80356.01,"right":280,"bottom":75641.97,"border_color":"#787B86FF","border_width":1,"border_style":"solid","extend":"none","xloc":"bar_index","bgcolor":"#787B8626","text":"10 bars","text_size":"auto","text_color":"#363A45FF","text_halign":"center","text_valign":"center","text_wrap":"none","text_font_family":"default","text_formatting":[],"force_overlay":false}
{"id":1,"position":"top_right","columns":2,"rows":2,"bgcolor":"#363A4580","frame_color":null,"frame_width":0,"border_color":null,"border_width":1,"force_overlay":false,"cells":[{"col":0,"row":0,"text":"close","width":0.0,"height":0.0,"text_color":"#FFFFFFFF","text_halign":"center","text_valign":"center","text_size":"normal","bgcolor":null,"tooltip":"","text_font_family":"default","text_formatting":[]}, ...]}
{"id":43,"points":[{"index":279,"time":null,"price":75882.0}, ...],"curved":false,"closed":false,"xloc":"bar_index","line_color":"#00BCD4FF","fill_color":null,"line_style":"","line_width":2,"force_overlay":false}
{"id":46,"line1":44,"line2":45,"color":"#08998133","line1_state":{...},"line2_state":{"id":45,"x1":1793905200000,"y1":75882.0,"x2":1793905200000,"y2":75882.0,"xloc":"bar_time", ...}}
```

A line with `xloc: "bar_time"` carries millisecond timestamps in `x1`/`x2`; a renderer maps
them to bars through the `bar.time` values. Quirk: `polyline.line_style` serializes as `""` in
6.12.1 (the polyline style constants carry no name); treat `""` as solid.

## 4. Reading the stream

```python
import json
from collections import Counter

recs = [json.loads(l) for l in open("output/my_script_viz.ndjson")]
hdr, end = recs[0], recs[-1]
meta = {}
for r in recs:
    if r["t"] == "meta":
        meta[r["id"]] = r                      # last one wins
bars = [r for r in recs if r["t"] == "bar"]
fast = [(b["time"], b["v"].get("fast")) for b in bars]           # None where na
color = meta["slow"]["color"]; slow_colors = []
for b in bars:                                                 # carry the last color forward
    color = b.get("c", {}).get("slow", color)
    slow_colors.append(color)
events = Counter((e["obj"], e["op"]) for e in recs if e["t"] == "ev")
snapshot = next(r for r in recs if r["t"] == "drawings")       # lines, labels, boxes, tables, polylines, linefills
```

`python3 <skill>/scripts/viz_inspect.py FILE` prints the same digest (header, plots with
dynamic flags and change counts, bar range, drawing counts, journal counts); `--plot ID` gives
value statistics, `--bar I` one bar with its events, `--drawings TYPE` the snapshot of one type,
`--events` the journal, `--json` the summary for another program.

## 5. Verified (PyneCore 6.12.1, hourly BTC/USDT, 284 bars)

Probe: an overlay indicator with two EMAs (one dynamically colored), a `plotshape`, a conditional
`bgcolor`, an `hline`, a `fill`, a trend line created on bar 30 and extended with `set_xy2` every
bar, a label per EMA cross (`max_labels_count=10`), a box replaced every 10 bars with explicit
`box.delete`, a 2×2 table refreshed every bar, and on the last bar a 3-point polyline, a
`bar_time` line through `chart.point.from_time` / `chart.point.now` and a linefill.

| Check | Result |
|-------|--------|
| `pyne run draw_probe BTCUSDT_60 --from 2026-10-25 --viz-journal --no-plot` | 906 records: `hdr` 1, `meta` 8, `bar` 284, `ev` 611, `drawings` 1, `end` 1; 437 kB |
| `meta` | `fast` static; `slow` emitted twice, the second with `dynamic: true` (12 color changes in `c`); `bgcolor#0` dynamic with `null` on unpainted bars; `fill#0` re-emitted once; `hline#0` |
| Journal | `line`: 3 create, 253 update (one per bar after bar 30); `table`: 1 create, 283 update; `box`: 28 create, 27 delete; `label`: 12 create, 2 delete (cap 10 removed the two oldest); `polyline`, `linefill`: 1 create each |
| `drawings` snapshot | lines 3, labels 10, boxes 1, tables 1 (4 cells), polylines 1, linefills 1 with embedded line states; `hdr.script` shows `max_lines_count 20`, `max_labels_count 10` |
| `ScriptRunner(viz_journal=True)` without `viz_path`, `runner.viz_events = batches.append` on 121 bars | 121 batches, 244 events; `runner.plot_meta` 6 ids with `kind`/`color`; `runner.drawings()` and `line.all()` etc. valid after the loop; `line.get_price(l, x2 + 5)` extrapolates |
| `viz_inspect.py` on the file (`summary`, `--plot slow`, `--plot up`, `--bar 31`, `--drawings linefills`, `--events`, `--json`) | all paths print; a CSV passed by mistake exits 1 with the line number; unknown plot id lists the ids and exits 1 |

## 6. Pitfalls

| Symptom | Cause | Fix |
|---------|-------|-----|
| Labels or lines disappear from the snapshot after a while | `max_*_count` (default 50) deletes the oldest object | Raise the cap in the decorator (≤ 500) or delete your own obsolete objects |
| No `drawings`, no colors in the output folder | The run was made without `--viz`; the plot CSV never carries styles | `--viz` / `--viz-journal`, or `viz_path=` in `ScriptRunner` |
| Thousands of `ev` records per bar | A table or line rewritten every bar with `--viz-journal` | Journal only when a renderer needs the history; otherwise the end snapshot is enough |
| `line.new` returned `na` | Called inside a library's `main()` | Draw in the script, return coordinates from the library |
| Reader sees `null` colors | `bgcolor`/`barcolor` unpainted (`na`) on that bar | Treat `null` as "no paint", carry other colors forward |
| Polyline style is an empty string | 6.12.1 serialization of `polyline.style_*` | Treat `""` as solid |
