#!/usr/bin/env python3
"""
Probe a PyneCore project: its workdir structure, what it contains, and which interpreter runs it.

Standard library only; run it with any Python 3.11+. Only the project folder is inspected.

    python3 pyne_env.py            # human-readable report
    python3 pyne_env.py --json     # machine-readable
    python3 pyne_env.py --root /path/to/project

The workdir is the folder with the `.pyne` marker, or one that already has the PyneCore layout,
or a `workdir/` child of the project:

    <workdir>/
    ├── .pyne          # marker: workdir_version = "1.0"
    ├── scripts/       # Pyne scripts (.py) + <name>.toml settings; lib/ for libraries
    ├── data/          # <name>.ohlcv + <name>.toml symbol info
    ├── config/        # .gitignore, api.toml, plugins/<plugin>.toml, symbol_map.toml
    ├── output/        # run outputs, logs/
    └── cache/

The report lists what is present and missing in that layout, the scripts with their role, the
data files with symbol and timeframe, the configured plugins, and the interpreter that can
import `pynecore` (the project's `.venv`, `venv` or `env` first, then `$VIRTUAL_ENV`, then PATH)
with its `pyne` command, ending with a ready-to-run `pyne run` line.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

MARKER = ".pyne"
REQUIRED_DIRS = ("scripts", "data", "config")
OPTIONAL_DIRS = ("output", "cache", "scripts/lib", "config/plugins", "output/logs")
VENV_CANDIDATES = (".venv", "venv", "env")


# ---------------------------------------------------------------- workdir and structure

def has_layout(path: Path) -> bool:
    return all((path / d).is_dir() for d in REQUIRED_DIRS)


def find_workdir(start: Path) -> tuple[Path | None, str]:
    current = start.resolve()
    for _ in range(10):
        if (current / MARKER).is_file():
            return current, "marker"
        child = current / "workdir"
        if (child / MARKER).is_file() or has_layout(child):
            return child, "workdir/ child"
        if has_layout(current):
            return current, "scripts/ data/ config/ present (no marker)"
        if current.parent == current:
            break
        current = current.parent
    return None, "not found"


def marker_version(workdir: Path) -> str | None:
    path = workdir / MARKER
    if not path.is_file():
        return None
    try:
        return str(tomllib.loads(path.read_text(encoding="utf-8")).get("workdir_version"))
    except (OSError, ValueError):
        return "unreadable"


def structure(workdir: Path) -> dict:
    return {
        "marker": (workdir / MARKER).is_file(),
        "marker_version": marker_version(workdir),
        "required": {d: (workdir / d).is_dir() for d in REQUIRED_DIRS},
        "optional": {d: (workdir / d).is_dir() for d in OPTIONAL_DIRS},
    }


# ---------------------------------------------------------------- contents

def script_role(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:6000]
    except OSError:
        return "unreadable"
    parts = head.split('"""')
    if not (len(parts) >= 2 and parts[1].lstrip().startswith("@pyne")):
        return "python (no @pyne marker)"
    for role in ("strategy", "indicator", "library"):
        if f"script.{role}(" in head:
            return role
    return "pyne (no @script decorator)"


def list_scripts(workdir: Path) -> list[dict]:
    folder = workdir / "scripts"
    if not folder.is_dir():
        return []
    return [{"name": p.stem, "path": str(p), "role": script_role(p), "toml": p.with_suffix(".toml").is_file()}
            for p in sorted(folder.glob("*.py"))]


def list_libraries(workdir: Path) -> list[str]:
    folder = workdir / "scripts" / "lib"
    if not folder.is_dir():
        return []
    return [str(p.relative_to(workdir / "scripts")) for p in sorted(folder.rglob("*.py"))]


def list_data(workdir: Path) -> list[dict]:
    folder = workdir / "data"
    items: list[dict] = []
    if not folder.is_dir():
        return items
    for path in sorted(folder.glob("*.ohlcv")):
        entry: dict = {"name": path.stem, "ohlcv": str(path), "size_bytes": path.stat().st_size, "toml": path.with_suffix(".toml").is_file()}
        if entry["toml"]:
            try:
                sym = tomllib.loads(path.with_suffix(".toml").read_text(encoding="utf-8")).get("symbol", {})
                entry.update({"prefix": sym.get("prefix"), "ticker": sym.get("ticker"), "period": sym.get("period"),
                              "type": sym.get("type"), "currency": sym.get("currency"), "mintick": sym.get("mintick"),
                              "timezone": sym.get("timezone")})
            except (OSError, ValueError):
                entry["toml_error"] = True
        items.append(entry)
    for path in sorted(folder.glob("*.csv")):
        if not path.name.endswith(".extra.csv"):
            items.append({"name": path.stem, "csv": str(path), "csv_not_converted": True})
    return items


def read_config(workdir: Path) -> dict:
    folder = workdir / "config"
    info: dict = {"present": folder.is_dir(), "gitignore": (folder / ".gitignore").is_file(),
                  "api_toml": (folder / "api.toml").is_file(), "symbol_map": (folder / "symbol_map.toml").is_file(),
                  "plugins": [], "symbol_map_entries": 0}
    plugins = folder / "plugins"
    if plugins.is_dir():
        info["plugins"] = sorted(p.stem for p in plugins.glob("*.toml"))
    if info["symbol_map"]:
        try:
            info["symbol_map_entries"] = len(tomllib.loads((folder / "symbol_map.toml").read_text(encoding="utf-8")).get("symbol_map", {}))
        except (OSError, ValueError):
            pass
    return info


# ---------------------------------------------------------------- interpreter

def python_in_venv(venv: Path) -> Path | None:
    for rel in ("bin/python", "bin/python3", "Scripts/python.exe"):
        if (venv / rel).exists():
            return venv / rel
    return None


def probe_python(python: Path | str) -> dict | None:
    code = ("import importlib.metadata as m, pynecore, sys, json;"
            "print(json.dumps({'version': m.version('pynesys-pynecore'), 'python_version': sys.version.split()[0]}))")
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

    # the project's own environments come first: an activated venv of another project must not win
    bases = [root]
    if workdir and workdir != root:
        bases += [workdir, workdir.parent]
    for base in bases:
        for name in VENV_CANDIDATES:
            py = python_in_venv(base / name)
            if py:
                consider(py, f"{name} under {base}")
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        py = python_in_venv(Path(active))
        if py:
            consider(py, "VIRTUAL_ENV (activated shell environment)")
    for exe in ("python3", "python"):
        which = shutil.which(exe)
        if which:
            consider(which, "PATH")
    consider(sys.executable, "this interpreter")
    return found


def find_pyne_cli(python: str | None) -> str | None:
    if python:
        for name in ("pyne", "pyne.exe"):
            candidate = Path(python).parent / name
            if candidate.exists():
                return str(candidate)
    return shutil.which("pyne")


def uses_uv(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deps = " ".join(data.get("project", {}).get("dependencies", []))
    return "pynecore" in deps and (root / "uv.lock").is_file()


# ---------------------------------------------------------------- report

def build_report(root: Path) -> dict:
    workdir, how = find_workdir(root)
    pythons = find_python(root, workdir)
    best = pythons[0] if pythons else None
    report: dict = {
        "root": str(root.resolve()),
        "workdir": str(workdir) if workdir else None,
        "workdir_found_by": how,
        "structure": structure(workdir) if workdir else None,
        "scripts": list_scripts(workdir) if workdir else [],
        "libraries": list_libraries(workdir) if workdir else [],
        "data": list_data(workdir) if workdir else [],
        "config": read_config(workdir) if workdir else None,
        "python": best["python"] if best else None,
        "pynecore_version": best["version"] if best else None,
        "python_version": best["python_version"] if best else None,
        "python_found_by": best["how"] if best else None,
        "other_pythons": [p["python"] for p in pythons[1:]],
        "pyne_cli": find_pyne_cli(best["python"] if best else None),
        "uv_project": uses_uv(root),
        "uv_available": bool(shutil.which("uv")),
    }
    if report["structure"]:
        s = report["structure"]
        report["missing_required"] = ([MARKER] if not s["marker"] else []) + [d for d, ok in s["required"].items() if not ok]
        report["missing_optional"] = [d for d, ok in s["optional"].items() if not ok]
    return report


def print_human(r: dict) -> None:
    print(f"Project root : {r['root']}")
    if not r["workdir"]:
        print("Workdir      : NOT FOUND (no .pyne marker and no scripts/ data/ config/ layout)")
        print("               create one: mkdir -p scripts/lib data config/plugins output/logs cache"
              " && printf 'workdir_version = \"1.0\"\\n' > .pyne")
    else:
        s = r["structure"]
        print(f"Workdir      : {r['workdir']}  (found by {r['workdir_found_by']})")
        print(f"  .pyne      : {'present, workdir_version=' + str(s['marker_version']) if s['marker'] else 'missing'}")
        print("  required   : " + "  ".join(f"{d}/ {'ok' if ok else 'MISSING'}" for d, ok in s["required"].items()))
        print("  optional   : " + "  ".join(f"{d}/ {'ok' if ok else '-'}" for d, ok in s["optional"].items()))
        if r["missing_required"]:
            steps = []
            dirs = [d for d in r["missing_required"] if d != MARKER]
            if dirs:
                steps.append("mkdir -p " + " ".join(dirs))
            if MARKER in r["missing_required"]:
                steps.append("printf 'workdir_version = \"1.0\"\\n' > .pyne")
            print(f"               fix (inside {r['workdir']}): " + " && ".join(steps))
        c = r["config"]
        if c and c["present"]:
            print(f"Config       : plugins {', '.join(c['plugins']) or 'none'}; symbol_map.toml "
                  f"{str(c['symbol_map_entries']) + ' entries' if c['symbol_map'] else 'absent'}; "
                  f"api.toml {'present' if c['api_toml'] else 'absent'}; .gitignore {'present' if c['gitignore'] else 'absent (config/ may hold keys)'}")
        if r["scripts"]:
            print("Scripts      :")
            for sc in r["scripts"]:
                print(f"               {sc['name']:<28} {sc['role']:<28} {'(.toml)' if sc['toml'] else ''}")
        else:
            print("Scripts      : none")
        if r["libraries"]:
            print("Libraries    : " + ", ".join(r["libraries"]))
        if r["data"]:
            print("Data         :")
            for d in r["data"]:
                if d.get("csv_not_converted"):
                    print(f"               {d['name']:<40} CSV, not converted (pass it to `pyne run` or `pyne data convert-from`)")
                else:
                    print(f"               {d['name']:<40} {d.get('prefix') or ''}:{d.get('ticker') or ''} "
                          f"{d.get('period') or ''} {d.get('type') or ''} {'' if d['toml'] else 'MISSING .toml'}")
        else:
            print("Data         : none (pyne --recreate-demo plugin list, or pyne data download ...)")
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
    if r["pyne_cli"] and r["workdir"]:
        print()
        print("Example run  :")
        print(f"  PYNE_NO_LOGO=1 {r['pyne_cli']} --workdir {r['workdir']} run <script> <data>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--root", default=".", metavar="DIR",
                        help="project folder to inspect; relative or absolute path. The workdir is searched in DIR\n"
                             "and up to 10 parent folders (.pyne marker, the scripts/ data/ config/ layout, or a\n"
                             "workdir/ child); virtual environments are looked up in DIR, the workdir and its parent.\n"
                             "Default: the current directory")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object (root, workdir, structure, scripts, data, config, python,\n"
                             "pynecore_version, pyne_cli, ...) instead of the text report")
    args = parser.parse_args()
    report = build_report(Path(args.root))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
