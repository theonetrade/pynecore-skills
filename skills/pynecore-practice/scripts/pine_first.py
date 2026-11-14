#!/usr/bin/env python3
r"""
Drive the Pine-first flow of a PyneCore workdir: the Pine source of every script lives in
`content/<name>.pine` (libraries in `content/lib/<user>/<Name>/v<N>.pine`), the local `pine2pyne`
writes the same-named Pyne script to `scripts/<name>.py` (`scripts/lib/<user>/<Name>/v<N>.py`),
and `pyne run <name>.py DATA` runs it. The script converts what is out of date, checks what the
result needs, runs it and prints the figures. It never calls `pyne compile` or any network service.

Standard library only. Reads only the workdir; writes only under `content/` (with --init /
--template) and `scripts/`, and lets `pyne run` write `output/` and `scripts/<name>.toml`.

    python3 pine_first.py --init                         # create content/, content/lib/, scripts/lib/ in the workdir
    python3 pine_first.py --template strategy my_strat   # content/my_strat.pine from templates/strategy_template.pine
    python3 pine_first.py my_strat                       # convert content/my_strat.pine -> scripts/my_strat.py if it is newer
    python3 pine_first.py my_strat --run demo            # convert if needed, then `pyne run my_strat.py demo`, print the figures
    python3 pine_first.py --all --run demo               # every content/*.pine (libraries first)
    python3 pine_first.py my_strat --force --run demo -- --from 2024-01-01 --to 2024-12-31 --no-plot
    python3 pine_first.py my_strat --workdir /path/to/workdir --pine2pyne /path/to/venv/bin/pine2pyne

NAME is the stem of `content/NAME.pine` (a path to the .pine works too). Arguments after `--` go to
`pyne run` unchanged. Libraries under content/lib/ are always considered first, so a script finds
them on sys.path (`scripts/` is the import root).

Exit codes: 0 everything converted (and ran) fine; 1 a conversion or a run failed, or the installed
transpiler is not `pine2pyne` >= 0.2.11; 2 usage error (no workdir, unknown name, bad template).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
TEMPLATES = SKILL / "templates"
PINE2PYNE_SKILL_SCRIPTS = SKILL.parent / "pynecore-pine2pyne" / "scripts"  # sibling skill, when installed
VENV_CANDIDATES = (".venv", "venv", "env", os.path.join(".pyneide", "venv"))
BIN_DIRS = ("bin", "Scripts")
LIB_IMPORT_RE = re.compile(r"^\s*import\s+lib\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)\.v(\d+)\b", re.M)
TOML_VALUE_RE = re.compile(r"^\[inputs\.(\w+)\]|^value\s*=\s*(.+)$", re.M)
METRICS = ("Net profit", "Total trades", "Percent profitable", "Profit factor", "Max equity drawdown", "Avg P&L")


# ----------------------------------------------------------------------------- locate things

def find_workdir(given: str | None) -> Path | None:
    if given:
        p = Path(given).absolute()
        return p if (p / ".pyne").is_file() or p.is_dir() else None
    for cand in (Path.cwd(), Path.cwd() / "workdir"):
        if (cand / ".pyne").is_file():
            return cand.absolute()
    return None


def find_tool(name: str, explicit: str | None, workdir: Path) -> Path | None:
    """--option, then a project venv in the workdir or its parent, then PATH."""
    if explicit:
        p = Path(explicit).absolute()
        return p if p.is_file() else None
    for base in (workdir, workdir.parent):
        for venv in VENV_CANDIDATES:
            for bin_dir in BIN_DIRS:
                for exe in (name, name + ".exe"):
                    cand = base / venv / bin_dir / exe
                    if cand.is_file():
                        return cand
    found = shutil.which(name)
    return Path(found) if found else None


def check_install(pine2pyne: Path) -> str | None:
    """Verdict of the pine2pyne skill's check_install.py for the environment that owns the command,
    or None when that skill is not installed beside this one."""
    if not (PINE2PYNE_SKILL_SCRIPTS / "check_install.py").is_file():
        return None
    sys.path.insert(0, str(PINE2PYNE_SKILL_SCRIPTS))
    sys.dont_write_bytecode = True
    import check_install as ci  # noqa: E402
    owner = ci.interpreter_of_script(pine2pyne)
    if owner is None:
        return "cannot find the interpreter that owns the pine2pyne command; run check_install.py by hand"
    info = ci.probe(owner)
    state, why = ci.verdict(info)
    return None if state == "ok" else why


# ----------------------------------------------------------------------------- the flow

def stale(src: Path, out: Path, force: bool) -> bool:
    return force or not out.is_file() or src.stat().st_mtime > out.stat().st_mtime


def convert(pine2pyne: Path, src: Path, out: Path, workdir: Path) -> bool:
    """Run pine2pyne from the workdir with relative paths, so its ✓ / ✗ lines stay short."""
    out.parent.mkdir(parents=True, exist_ok=True)
    rel = lambda q: os.path.relpath(q, workdir)  # noqa: E731
    r = subprocess.run([str(pine2pyne), rel(src), "-o", rel(out)], cwd=workdir)
    return r.returncode == 0


def library_sources(workdir: Path) -> list[tuple[Path, Path]]:
    """(content/lib/<user>/<Name>/v<N>.pine, scripts/lib/<user>/<Name>/v<N>.py) pairs; `-` in a folder name
    becomes `_` in the module path, as pine2pyne spells the import."""
    pairs = []
    for src in sorted((workdir / "content" / "lib").rglob("v*.pine")):
        rel = src.relative_to(workdir / "content" / "lib")
        parts = [p.replace("-", "_") for p in rel.parts[:-1]] + [rel.stem + ".py"]
        pairs.append((src, workdir / "scripts" / "lib" / Path(*parts)))
    return pairs


def notices(workdir: Path, name: str) -> list[str]:
    out, notes = workdir / "scripts" / f"{name}.py", []
    if (workdir / "scripts" / f"{name}.pine").is_file():
        notes.append(f"scripts/{name}.pine exists: a bare `pyne run {name}` would resolve to it and reach the cloud "
                     f"compiler; keep Pine sources in content/ only (move or delete the copy)")
    if (workdir / "scripts" / f"{name}.py.map").is_file():
        notes.append(f"scripts/{name}.py.map is a stale cloud-compile source map; ignored by PyneIDE, leave or delete it")
    text = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
    for user, module, version in LIB_IMPORT_RE.findall(text):
        lib = workdir / "scripts" / "lib" / user / module / f"v{version}.py"
        if not lib.is_file():
            notes.append(f"missing library module lib.{user}.{module}.v{version}: put its Pine source at "
                         f"content/lib/{user}/{module}/v{version}.pine and run again")
    toml = workdir / "scripts" / f"{name}.toml"
    if toml.is_file():
        current, overrides = None, []
        for m in TOML_VALUE_RE.finditer(toml.read_text(encoding="utf-8", errors="replace")):
            if m.group(1):
                current = m.group(1)
            elif current:
                overrides.append(f"{current} = {m.group(2).strip()}")
        if overrides:
            notes.append(f"scripts/{name}.toml overrides the Pine defaults: {', '.join(overrides)} "
                         f"(a run uses these values, not the ones in content/{name}.pine)")
    return notes


def run_script(pyne: Path, workdir: Path, name: str, data: str, extra: list[str]) -> int:
    env = dict(os.environ, PYNE_NO_LOGO="1", NO_COLOR="1")
    cmd = [str(pyne), "--workdir", str(workdir), "run", f"{name}.py", data, *extra]
    print("+ " + " ".join(cmd[cmd.index("run"):]), flush=True)
    r = subprocess.run(cmd, env=env)
    return r.returncode


def _num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    return str(int(f)) if f == int(f) else f"{f:.4f}"


def figures(workdir: Path, name: str) -> list[str]:
    lines = []
    strat = workdir / "output" / f"{name}_strat.csv"
    plots = workdir / "output" / f"{name}.csv"
    if strat.is_file():
        with strat.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        if rows:
            head = rows[0]  # Metric, All <ccy>, All %, Long <ccy>, Long %, Short <ccy>, Short %
            for row in rows[1:]:
                if row and row[0] in METRICS:
                    # columns 1 and 2 are "All <currency>" and "All %"; a metric fills one or both
                    vals = [f"{_num(v)}{' %' if i == 1 else ''}" for i, v in enumerate(row[1:3]) if v != ""]
                    lines.append(f"  {row[0]}: {' / '.join(vals)}")
        trades = workdir / "output" / f"{name}_trade.csv"
        if trades.is_file():
            with trades.open(encoding="utf-8") as f:
                n = max(sum(1 for _ in f) - 1, 0)
            lines.append(f"  trades file: output/{name}_trade.csv ({n} rows)")
    if plots.is_file():
        with plots.open(encoding="utf-8") as f:
            header = f.readline().strip()
        lines.append(f"  plots: output/{name}.csv columns: {header}")
    return lines


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(
        description="Pine-first flow: content/<name>.pine -> scripts/<name>.py with the local pine2pyne, then pyne run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Standard library only.")[0].strip())
    ap.add_argument("names", nargs="*", metavar="NAME",
                    help="stem of content/NAME.pine, or a path to a .pine file under content/ (several allowed)")
    ap.add_argument("--workdir", metavar="DIR",
                    help="the PyneCore workdir (holds .pyne, content/, scripts/, data/); default: the current folder or its workdir/ child with a .pyne marker")
    ap.add_argument("--init", action="store_true", help="create content/, content/lib/ and scripts/lib/ in the workdir (never overwrites)")
    ap.add_argument("--template", choices=("strategy", "indicator", "library"), metavar="{strategy,indicator,library}",
                    help="with one NAME: copy templates/<kind>_template.pine to content/NAME.pine (library: content/lib/mine/NAME/v1.pine); refuses to overwrite")
    ap.add_argument("--all", action="store_true", help="every content/*.pine (libraries under content/lib/ are always included first)")
    ap.add_argument("--force", action="store_true", help="convert even when scripts/<name>.py is newer than its Pine source")
    ap.add_argument("--run", metavar="DATA",
                    help="after a successful conversion run `pyne run <name>.py DATA` for each NAME; DATA is a data file name in data/ (`demo`), a path, or a provider string; pass more pyne options after `--`")
    ap.add_argument("--pyne", metavar="PATH", help="path of the pyne command (default: a project venv in or above the workdir, then PATH)")
    ap.add_argument("--pine2pyne", metavar="PATH", help="path of the pine2pyne command (default: same lookup)")
    ap.add_argument("--no-install-check", action="store_true",
                    help="skip the check that the transpiler is the pine2pyne distribution >= 0.2.11 (needs the pynecore-pine2pyne skill installed beside this one)")
    args = ap.parse_args(argv)

    workdir = find_workdir(args.workdir)
    if workdir is None:
        print("no workdir: pass --workdir DIR (a folder with the .pyne marker) or run from one", file=sys.stderr)
        return 2
    content = workdir / "content"
    if args.init:
        for d in (content / "lib", workdir / "scripts" / "lib"):
            d.mkdir(parents=True, exist_ok=True)
        print(f"ready: {content}/ (Pine sources), {content}/lib/<user>/<Name>/v<N>.pine (libraries), "
              f"{workdir}/scripts/ (generated Pyne, do not edit)")
    if args.template:
        if len(args.names) != 1:
            print("--template needs exactly one NAME", file=sys.stderr)
            return 2
        src = TEMPLATES / f"{args.template}_template.pine"
        name = args.names[0]
        dest = content / "lib" / "mine" / name / "v1.pine" if args.template == "library" else content / f"{name}.pine"
        if dest.exists():
            print(f"{dest} exists; not overwritten", file=sys.stderr)
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        if args.template == "library":
            text = text.replace('library("Helpers")', f'library("{name}")')
        dest.write_text(text, encoding="utf-8")
        print(f"wrote {dest.relative_to(workdir)} from {src.name}; edit it, then run this script with the name to convert and run it")
        if not args.run:
            return 0
    if not content.is_dir():
        print(f"{content} does not exist: run with --init first, or create content/ and put the Pine sources there", file=sys.stderr)
        return 2

    pine2pyne = find_tool("pine2pyne", args.pine2pyne, workdir)
    if pine2pyne is None:
        print("pine2pyne not found: `pip install \"pine2pyne>=0.2.11\"` / `uv tool install \"pine2pyne>=0.2.11\"`, or --pine2pyne PATH", file=sys.stderr)
        return 1
    if not args.no_install_check:
        why = check_install(pine2pyne)
        if why:
            print(f"installed transpiler rejected: {why}", file=sys.stderr)
            return 1

    # which scripts
    targets: list[tuple[str, Path]] = []
    if args.all:
        targets = [(p.stem, p) for p in sorted(content.glob("*.pine"))]
    for n in args.names:
        p = Path(n)
        src = p if p.suffix == ".pine" and p.is_file() else content / f"{n}.pine"
        if not src.is_file():
            print(f"no Pine source {src}", file=sys.stderr)
            return 2
        if (src.stem, src) not in targets:
            targets.append((src.stem, src))
    if not targets and not args.init and not args.template:
        print("nothing to do: give NAME(s) or --all", file=sys.stderr)
        return 2

    failed = 0
    # libraries first
    for src, out in library_sources(workdir):
        if stale(src, out, args.force):
            if not convert(pine2pyne, src, out, workdir):
                failed += 1
    # scripts
    built: list[str] = []
    for name, src in targets:
        out = workdir / "scripts" / f"{name}.py"
        if stale(src, out, args.force):
            if convert(pine2pyne, src, out, workdir):
                built.append(name)
            else:
                failed += 1
                print(f"✗ {name}: not converted; fix the Pine source or port it by hand (pynecore-pine2pyne skill, section 3.2)",
                      file=sys.stderr)
                continue
        else:
            print(f"= scripts/{name}.py is up to date with content/{name}.pine")
            built.append(name)
        for note in notices(workdir, name):
            print(f"note: {note}")

    if args.run and built:
        pyne = find_tool("pyne", args.pyne, workdir)
        if pyne is None:
            print("pyne not found: install pynesys-pynecore[cli] in the project venv or pass --pyne PATH", file=sys.stderr)
            return 1
        for name in built:
            rc = run_script(pyne, workdir, name, args.run, extra)
            if rc != 0:
                failed += 1
                print(f"✗ {name}: pyne run exited {rc} (see output/logs/error.log)", file=sys.stderr)
                continue
            for line in figures(workdir, name):
                print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
