#!/usr/bin/env python3
r"""
Inspect a viz NDJSON file written by `pyne run ... --viz` (or ScriptRunner(viz_path=...)).

Standard library only; it never imports pynecore, so it runs with any Python 3.11+ and is the
way to check what a script drew without a chart renderer.

    python3 viz_inspect.py output/my_strategy_viz.ndjson                 # summary: header, plots, bars, drawings, journal
    python3 viz_inspect.py output/my_strategy_viz.ndjson --plot fast     # value statistics of one plot id
    python3 viz_inspect.py output/my_strategy_viz.ndjson --bar 120       # the bar record with index 120 and its journal events
    python3 viz_inspect.py output/my_strategy_viz.ndjson --drawings labels   # every label of the end-of-run snapshot
    python3 viz_inspect.py output/my_strategy_viz.ndjson --events        # every journal event (needs --viz-journal at run time)
    python3 viz_inspect.py output/my_strategy_viz.ndjson --json          # the summary as JSON

Record types of the stream (one JSON object per line, field "t"): hdr, meta, bar, ev, drawings, end.
Exit codes: 0 ok, 1 the file is missing or is not a viz stream, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DRAWING_TYPES = ("lines", "labels", "boxes", "tables", "polylines", "linefills")


def _iso(ms: int | float | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def load(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"cannot read {path}: {e}")
    records = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{n}: not JSON ({e})")
    if not records or records[0].get("t") != "hdr":
        raise SystemExit(f"{path}: not a viz stream (first record must be \"t\": \"hdr\")")
    return records


def summarize(records: list[dict]) -> dict:
    hdr = records[0]
    counts = Counter(r.get("t") for r in records)
    metas: dict[str, dict] = {}
    for r in records:
        if r.get("t") == "meta":
            metas[r["id"]] = r                      # a repeated id updates the earlier record
    bars = [r for r in records if r.get("t") == "bar"]
    color_changes: Counter = Counter()
    fired: Counter = Counter()
    for b in bars:
        for pid in b.get("v", {}):
            fired[pid] += 1
        for pid in b.get("c", {}):
            color_changes[pid] += 1
    events = [r for r in records if r.get("t") == "ev"]
    snap = next((r for r in records if r.get("t") == "drawings"), None)
    end = next((r for r in records if r.get("t") == "end"), None)
    return {
        "file_version": hdr.get("v"),
        "script": hdr.get("script", {}),
        "syminfo": hdr.get("syminfo", {}),
        "journal": hdr.get("journal"),
        "record_counts": dict(counts),
        "plots": [
            {"id": pid, "kind": m.get("kind"), "title": m.get("title"), "style": m.get("style"),
             "color": m.get("color"), "dynamic": bool(m.get("dynamic")), "bars_with_value": fired.get(pid, 0),
             "color_changes": color_changes.get(pid, 0)}
            for pid, m in metas.items()
        ],
        "bars": {
            "count": len(bars),
            "first_index": bars[0]["i"] if bars else None,
            "last_index": bars[-1]["i"] if bars else None,
            "first_time": bars[0]["time"] if bars else None,
            "last_time": bars[-1]["time"] if bars else None,
        },
        "journal_events": dict(Counter(f"{e.get('obj')}:{e.get('op')}" for e in events)),
        "drawings": {k: len(snap.get(k, [])) for k in DRAWING_TYPES} if snap else None,
        "end_bars": end.get("bars") if end else None,
        "complete": end is not None,
    }


def print_summary(s: dict) -> None:
    sc, si = s["script"], s["syminfo"]
    print(f"viz v{s['file_version']}  {sc.get('type')} '{sc.get('title')}'  overlay={sc.get('overlay')}  journal={s['journal']}"
          f"  {'complete' if s['complete'] else 'INCOMPLETE (no end record)'}")
    print(f"symbol {si.get('tickerid')}  timeframe {si.get('timeframe')}  mintick {si.get('mintick')}  tz {si.get('timezone')}")
    print("limits " + "  ".join(f"{k}={sc[k]}" for k in ("max_lines_count", "max_labels_count", "max_boxes_count", "max_polylines_count") if k in sc))
    print("records " + "  ".join(f"{k}={v}" for k, v in s["record_counts"].items()))
    b = s["bars"]
    print(f"bars {b['count']}  index {b['first_index']}..{b['last_index']}  time {_iso(b['first_time'])} .. {_iso(b['last_time'])} UTC"
          + (f"  (end record says {s['end_bars']})" if s["end_bars"] not in (None, b["count"]) else ""))
    print("plots:")
    for p in s["plots"]:
        extra = f"  style={p['style']}" if p["style"] else ""
        print(f"  {p['id']:<16} {p['kind']:<8}{extra}  color={p['color']}  dynamic={p['dynamic']}"
              f"  bars_with_value={p['bars_with_value']}  color_changes={p['color_changes']}")
    if s["drawings"] is not None:
        print("drawings at end of run: " + "  ".join(f"{k}={v}" for k, v in s["drawings"].items()))
    if s["journal_events"]:
        print("journal events: " + "  ".join(f"{k}={v}" for k, v in sorted(s["journal_events"].items())))
    elif s["journal"]:
        print("journal events: none")
    else:
        print("journal: off (run with --viz-journal to get per-bar create/update/delete events)")


def print_plot(records: list[dict], pid: str) -> int:
    metas = [r for r in records if r.get("t") == "meta" and r["id"] == pid]
    if not metas:
        ids = sorted({r["id"] for r in records if r.get("t") == "meta"})
        print(f"no plot with id {pid!r}; ids: {', '.join(ids)}")
        return 1
    print("meta:", json.dumps(metas[-1], ensure_ascii=False))
    values = [(b["i"], b["time"], b["v"].get(pid)) for b in records if b.get("t") == "bar" and pid in b.get("v", {})]
    nums = [(i, t, v) for i, t, v in values if isinstance(v, (int, float))]
    nulls = len(values) - len(nums)
    print(f"bars with the plot: {len(values)}  numeric: {len(nums)}  null/na: {nulls}")
    if nums:
        lo = min(nums, key=lambda x: x[2]); hi = max(nums, key=lambda x: x[2])
        print(f"min {lo[2]} at bar {lo[0]} ({_iso(lo[1])})  max {hi[2]} at bar {hi[0]} ({_iso(hi[1])})")
        print(f"first numeric {nums[0][2]} at bar {nums[0][0]}  last {nums[-1][2]} at bar {nums[-1][0]}")
        truthy = sum(1 for _, _, v in nums if v)
        print(f"non-zero values: {truthy}" + ("  (a marker plot: this is how many bars fired)" if metas[-1].get("kind") in ("shape", "char", "arrow") else ""))
    changes = [(b["i"], b["c"][pid]) for b in records if b.get("t") == "bar" and pid in b.get("c", {})]
    if changes:
        print(f"color changes: {len(changes)}  first {changes[0]}  last {changes[-1]}")
    return 0


def print_bar(records: list[dict], index: int) -> int:
    bar = next((b for b in records if b.get("t") == "bar" and b["i"] == index), None)
    if bar is None:
        print(f"no bar record with index {index}")
        return 1
    print(json.dumps(bar, ensure_ascii=False))
    evs = [e for e in records if e.get("t") == "ev" and e["i"] == index]
    print(f"journal events on this bar: {len(evs)}")
    for e in evs:
        print("  " + _event_line(e))
    return 0


def _event_line(e: dict) -> str:
    s = e.get("s") or {}
    coords = {k: s[k] for k in ("x1", "y1", "x2", "y2", "x", "y", "left", "top", "right", "bottom", "text", "position") if k in s}
    return f"bar {e['i']:<6} {e['op']:<6} {e['obj']:<9} id={e['id']} {json.dumps(coords, ensure_ascii=False) if coords else ''}"


def print_events(records: list[dict]) -> int:
    evs = [e for e in records if e.get("t") == "ev"]
    if not evs:
        print("no journal events (the run was made without --viz-journal, or nothing was drawn)")
        return 0
    for e in evs:
        print(_event_line(e))
    print(f"{len(evs)} events")
    return 0


def print_drawings(records: list[dict], kind: str) -> int:
    snap = next((r for r in records if r.get("t") == "drawings"), None)
    if snap is None:
        print("no drawings snapshot (the run did not finish)")
        return 1
    objs = snap.get(kind, [])
    print(f"{len(objs)} {kind}")
    for o in objs:
        o = dict(o)
        cells = o.pop("cells", None)
        states = {k: o.pop(k) for k in ("line1_state", "line2_state") if k in o}
        print("  " + json.dumps(o, ensure_ascii=False))
        for k, v in states.items():
            print(f"     {k}: {json.dumps(v, ensure_ascii=False)}")
        if cells is not None:
            for c in cells:
                print(f"     cell[{c['col']},{c['row']}] {c.get('text')!r}" + (f" merge={c['merge']}" if "merge" in c else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("file", type=Path, metavar="FILE",
                        help="path to the NDJSON written by `pyne run --viz` (default name output/<script>_viz.ndjson) or by\n"
                             "ScriptRunner(viz_path=...)")
    parser.add_argument("--plot", metavar="ID",
                        help="plot id as the meta records name it: the plot title (plot(x, \"fast\") -> fast), or the ordinal id of a\n"
                             "title-less call: bgcolor#0, barcolor#0, hline#0, fill#0 (numbered in call order)")
    parser.add_argument("--bar", type=int, metavar="I", help="0-based bar index: print that bar record and its journal events")
    parser.add_argument("--drawings", choices=DRAWING_TYPES, metavar="TYPE",
                        help="list the end-of-run snapshot of one drawing type: " + " | ".join(DRAWING_TYPES))
    parser.add_argument("--events", action="store_true", help="list every journal event (bar, op, object type, id, coordinates)")
    parser.add_argument("--json", action="store_true", help="print the summary as one JSON object instead of text")
    args = parser.parse_args()

    records = load(args.file)
    if args.plot is not None:
        return print_plot(records, args.plot)
    if args.bar is not None:
        return print_bar(records, args.bar)
    if args.drawings:
        return print_drawings(records, args.drawings)
    if args.events:
        return print_events(records)
    summary = summarize(records)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print_summary(summary)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:                      # output piped into head / less that quit early
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
