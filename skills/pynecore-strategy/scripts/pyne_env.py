#!/usr/bin/env python3
"""
Locate the PyneCore environment for the current project and print it.

Standard library only; run it with any Python 3.11+:

    python3 pyne_env.py            # human-readable report
    python3 pyne_env.py --json     # machine-readable
    python3 pyne_env.py --root /path/to/project

It answers the questions an agent has before touching a project:

- which directory is the PyneCore workdir (the folder with a `.pyne` marker, or a `workdir/`
  child), and whether it exists at all;
- which Python interpreter has `pynecore` installed, and which version;
- where the `pyne` CLI is (next to that interpreter), or whether `uv run pyne` applies;
- which scripts and data files the workdir already has;
- whether a local copy of the PyneCore documentation is available (the PyneIDE editor
  extension bundles one).

Nothing here is project-specific; the same file works for every project.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

VENV_CANDIDATES = (".venv", "venv", ".pyneide/venv", "env", ".env")
DOC_GLOBS = (
    "~/.vscode/extensions/pynesys.pyneide-*/resources/pynecore/docs",
    "~/.vscode-insiders/extensions/pynesys.pyneide-*/resources/pynecore/docs",
    "~/.cursor/extensions/pynesys.pyneide-*/resources/pynecore/docs",
    "~/.windsurf/extensions/pynesys.pyneide-*/resources/pynecore/docs",
)


def find_workdir(start: Path) -> tuple[Path | None, str]:
    """Replicate PyneCore's workdir discovery: `.pyne` marker in the directory, else a
    `workdir/` child, walking up to 10 levels."""
    current = start.resolve()
    for _ in range(10):
        if (current / ".pyne").is_file():
            return current, "marker"
        child = current / "workdir"
        if (child / ".pyne").is_file() or (child.is_dir() and (child / "scripts").is_dir()):
            return child, "workdir-child"
        if current.parent == current:
            break
        current = current.parent
    return None, "none"


def python_in_venv(venv: Path) -> Path | None:
    for rel in ("bin/python", "bin/python3", "Scripts/python.exe"):
        candidate = venv / rel
        if candidate.exists():
            return candidate
    return None


def probe_python(python: Path | str) -> dict | None:
    """Return {"version": ..., "path": ...} when `pynecore` imports under this interpreter."""
    code = (
        "import importlib.metadata as m, pynecore, sys, json;"
        "print(json.dumps({'version': m.version('pynesys-pynecore'),"
        " 'package': pynecore.__file__, 'python_version': sys.version.split()[0]}))"
    )
    try:
        out = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def find_python(root: Path, workdir: Path | None) -> list[dict]:
    """Every interpreter that can import pynecore, best candidate first."""
    seen: set[str] = set()
    found: list[dict] = []

    def consider(python: Path | str, how: str) -> None:
        key = str(python)
        if key in seen:
            return
        seen.add(key)
        info = probe_python(python)
        if info:
            info.update({"python": key, "how": how})
            found.append(info)

    active = os.environ.get("VIRTUAL_ENV")
    if active:
        py = python_in_venv(Path(active))
        if py:
            consider(py, "VIRTUAL_ENV")

    search_dirs = [root]
    if workdir and workdir != root:
        search_dirs.append(workdir)
        search_dirs.append(workdir.parent)
    for base in search_dirs:
        for name in VENV_CANDIDATES:
            py = python_in_venv(base / name)
            if py:
                consider(py, f"{name} under {base}")

    for exe in ("python3", "python"):
        which = shutil.which(exe)
        if which:
            consider(which, "PATH")
    consider(sys.executable, "this interpreter")
    return found


def find_pyne_cli(python: str | None) -> dict:
    result: dict = {"pyne": None, "uv_run": False}
    if python:
        bindir = Path(python).parent
        for name in ("pyne", "pyne.exe"):
            candidate = bindir / name
            if candidate.exists():
                result["pyne"] = str(candidate)
                break
    if not result["pyne"]:
        which = shutil.which("pyne")
        if which:
            result["pyne"] = which
    if shutil.which("uv"):
        result["uv_available"] = True
    return result


def uses_uv(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deps = " ".join(data.get("project", {}).get("dependencies", []))
    return "pynecore" in deps and ((root / "uv.lock").is_file() or "tool" in data and "uv" in data["tool"])


def list_scripts(workdir: Path) -> list[dict]:
    scripts_dir = workdir / "scripts"
    items: list[dict] = []
    if not scripts_dir.is_dir():
        return items
    for path in sorted(scripts_dir.glob("*.py")):
        kind = "python"
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            head = ""
        if "@pyne" in head.split('"""')[1] if head.count('"""') >= 2 else False:
            kind = "pyne"
        if "@script.strategy" in head or "script.strategy(" in head:
            role = "strategy"
        elif "@script.indicator" in head or "script.indicator(" in head:
            role = "indicator"
        elif "@script.library" in head or "script.library(" in head:
            role = "library"
        else:
            role = "unknown"
        items.append({
            "name": path.stem,
            "path": str(path),
            "kind": kind,
            "role": role,
            "toml": (path.with_suffix(".toml")).is_file(),
        })
    return items


def list_data(workdir: Path) -> list[dict]:
    data_dir = workdir / "data"
    items: list[dict] = []
    if not data_dir.is_dir():
        return items
    for path in sorted(data_dir.glob("*.ohlcv")):
        entry: dict = {"name": path.stem, "ohlcv": str(path), "size_bytes": path.stat().st_size}
        toml_path = path.with_suffix(".toml")
        if toml_path.is_file():
            try:
                sym = tomllib.loads(toml_path.read_text(encoding="utf-8")).get("symbol", {})
                entry.update({
                    "ticker": sym.get("ticker"), "prefix": sym.get("prefix"),
                    "period": sym.get("period"), "type": sym.get("type"),
                    "currency": sym.get("currency"), "mintick": sym.get("mintick"),
                    "timezone": sym.get("timezone"),
                })
            except (OSError, ValueError):
                entry["toml_error"] = True
        else:
            entry["toml_missing"] = True
        items.append(entry)
    for path in sorted(data_dir.glob("*.csv")):
        if path.name.endswith(".extra.csv"):
            continue
        items.append({"name": path.stem, "csv": str(path), "note": "convert with `pyne data convert-from` or pass directly to `pyne run`"})
    return items


def find_docs() -> list[str]:
    hits: list[str] = []
    for pattern in DOC_GLOBS:
        for match in sorted(glob.glob(os.path.expanduser(pattern)), reverse=True):
            if (Path(match) / "README.md").is_file():
                hits.append(match)
    return hits


def build_report(root: Path) -> dict:
    workdir, how = find_workdir(root)
    pythons = find_python(root, workdir)
    best_python = pythons[0]["python"] if pythons else None
    cli = find_pyne_cli(best_python)
    report = {
        "root": str(root.resolve()),
        "workdir": str(workdir) if workdir else None,
        "workdir_found_by": how,
        "workdir_layout": {
            name: (workdir / name).is_dir() if workdir else False
            for name in ("scripts", "data", "output", "config")
        } if workdir else None,
        "python": best_python,
        "pynecore_version": pythons[0]["version"] if pythons else None,
        "python_version": pythons[0]["python_version"] if pythons else None,
        "python_found_by": pythons[0]["how"] if pythons else None,
        "other_pythons": [p["python"] for p in pythons[1:]],
        "pyne_cli": cli.get("pyne"),
        "uv_project": uses_uv(root),
        "uv_available": bool(shutil.which("uv")),
        "scripts": list_scripts(workdir) if workdir else [],
        "data": list_data(workdir) if workdir else [],
        "local_docs": find_docs(),
    }
    return report


def print_human(r: dict) -> None:
    print(f"Project root : {r['root']}")
    if r["workdir"]:
        print(f"Workdir      : {r['workdir']}  (found by {r['workdir_found_by']})")
        missing = [k for k, v in (r["workdir_layout"] or {}).items() if not v]
        if missing:
            print(f"               missing subfolders: {', '.join(missing)}")
    else:
        print("Workdir      : NOT FOUND")
        print("               create one: mkdir -p workdir/scripts/lib workdir/data workdir/output workdir/config"
              " && printf 'workdir_version = \"1.0\"\\n' > workdir/.pyne")
    if r["python"]:
        print(f"Python       : {r['python']}  (pynecore {r['pynecore_version']}, python {r['python_version']}, via {r['python_found_by']})")
    else:
        print("Python       : no interpreter with pynecore found")
        print("               install: pip install \"pynesys-pynecore[cli,ccxt]\"  (or: uv add \"pynesys-pynecore[cli,ccxt]\")")
    if r["pyne_cli"]:
        print(f"pyne CLI     : {r['pyne_cli']}")
    elif r["uv_project"]:
        print("pyne CLI     : use `uv run pyne ...` (uv project)")
    else:
        print("pyne CLI     : not found (pip install \"pynesys-pynecore[cli]\")")
    if r["scripts"]:
        print("Scripts      :")
        for s in r["scripts"]:
            print(f"               {s['name']:<28} {s['role']:<10} {'(.toml)' if s['toml'] else ''}")
    if r["data"]:
        print("Data         :")
        for d in r["data"]:
            if "ohlcv" in d:
                print(f"               {d['name']:<40} {d.get('prefix') or ''}:{d.get('ticker') or ''} "
                      f"{d.get('period') or ''} {d.get('type') or ''} {'(no .toml!)' if d.get('toml_missing') else ''}")
            else:
                print(f"               {d['name']:<40} CSV, not converted yet")
    if r["local_docs"]:
        print(f"Local docs   : {r['local_docs'][0]}")
    else:
        print("Local docs   : none bundled locally; rely on this skill's references/")
    if r["pyne_cli"] and r["workdir"]:
        print()
        print("Example run  :")
        print(f"  PYNE_NO_LOGO=1 {r['pyne_cli']} --workdir {r['workdir']} run <script> <data>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="project directory to inspect (default: current)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a report")
    args = parser.parse_args()
    report = build_report(Path(args.root))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
