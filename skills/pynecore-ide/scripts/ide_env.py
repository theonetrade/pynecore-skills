#!/usr/bin/env python3
"""
Check (and optionally create) the workdir structure that PyneIDE and the `pyne` CLI require.

Standard library only. Reads nothing but the project folder itself.

    python3 ide_env.py                 # report for the current directory
    python3 ide_env.py --root PATH     # another project
    python3 ide_env.py --create        # create every missing folder and file, never overwrite
    python3 ide_env.py --json

The structure PyneIDE's "Initialize Pyne Project" writes (and `pyne` auto-detects):

    <workdir>/
    ├── .pyne                 # marker: workdir_version = "1.0"
    ├── scripts/              # Pyne scripts (.py) and their .toml settings
    │   └── lib/              # libraries, imported as lib.<publisher>.<name>.v<N>
    ├── data/                 # <name>.ohlcv + <name>.toml pairs
    ├── config/               # .gitignore (*), api.toml, plugins/<plugin>.toml, symbol_map.toml
    │   └── plugins/
    ├── output/               # run outputs
    │   └── logs/
    └── cache/

The workdir is the project folder itself when it holds the `.pyne` marker, otherwise its
`workdir/` child. Exit code 0 when everything required is present, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

MARKER = ".pyne"
MARKER_CONTENT = (
    "# PyneCore workdir marker. The version refers to the workdir structure, not your project.\n"
    'workdir_version = "1.0"\n'
)
REQUIRED_DIRS = ("scripts", "data", "config", "output", "cache")
RECOMMENDED_DIRS = ("scripts/lib", "config/plugins", "output/logs")
CONFIG_GITIGNORE = "*\n!.gitignore\n"
API_TOML = '[api]\napi_key = ""\ntimeout = 30\n'


def locate_workdir(root: Path) -> tuple[Path, str]:
    if (root / MARKER).is_file():
        return root, "marker in root"
    child = root / "workdir"
    if (child / MARKER).is_file():
        return child, "workdir/ child with marker"
    if child.is_dir() and (child / "scripts").is_dir():
        return child, "workdir/ child without marker"
    if (root / "scripts").is_dir() and (root / "data").is_dir():
        return root, "root without marker"
    return root, "not found"


def marker_version(workdir: Path) -> str | None:
    path = workdir / MARKER
    if not path.is_file():
        return None
    try:
        return str(tomllib.loads(path.read_text(encoding="utf-8")).get("workdir_version"))
    except (OSError, ValueError):
        return "unreadable"


def script_role(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:6000]
    except OSError:
        return "unreadable"
    first_doc = head.split('"""')
    is_pyne = len(first_doc) >= 2 and first_doc[1].lstrip().startswith("@pyne")
    if not is_pyne:
        return "python (no @pyne marker)"
    for role in ("strategy", "indicator", "library"):
        if f"script.{role}(" in head:
            return role
    return "pyne (no @script decorator)"


def scan(workdir: Path) -> dict:
    report: dict = {"marker": {"present": (workdir / MARKER).is_file(), "version": marker_version(workdir)}}
    report["dirs"] = {d: (workdir / d).is_dir() for d in REQUIRED_DIRS}
    report["recommended_dirs"] = {d: (workdir / d).is_dir() for d in RECOMMENDED_DIRS}
    report["config"] = {
        "gitignore": (workdir / "config" / ".gitignore").is_file(),
        "api_toml": (workdir / "config" / "api.toml").is_file(),
        "symbol_map": (workdir / "config" / "symbol_map.toml").is_file(),
        "plugins": sorted(p.name for p in (workdir / "config" / "plugins").glob("*.toml")) if (workdir / "config" / "plugins").is_dir() else [],
    }
    scripts: list[dict] = []
    if (workdir / "scripts").is_dir():
        for path in sorted((workdir / "scripts").glob("*.py")):
            scripts.append({"name": path.stem, "role": script_role(path), "toml": path.with_suffix(".toml").is_file()})
        libs = [str(p.relative_to(workdir / "scripts")) for p in sorted((workdir / "scripts" / "lib").rglob("*.py"))] if (workdir / "scripts" / "lib").is_dir() else []
        report["libraries"] = libs
    report["scripts"] = scripts
    data: list[dict] = []
    if (workdir / "data").is_dir():
        for path in sorted((workdir / "data").glob("*.ohlcv")):
            entry: dict = {"name": path.stem, "toml": path.with_suffix(".toml").is_file()}
            if entry["toml"]:
                try:
                    sym = tomllib.loads(path.with_suffix(".toml").read_text(encoding="utf-8")).get("symbol", {})
                    entry.update({"ticker": sym.get("ticker"), "period": sym.get("period"), "type": sym.get("type")})
                except (OSError, ValueError):
                    entry["toml_error"] = True
            data.append(entry)
        for path in sorted((workdir / "data").glob("*.csv")):
            if not path.name.endswith(".extra.csv"):
                data.append({"name": path.stem, "csv_not_converted": True})
    report["data"] = data
    report["output_files"] = sum(1 for p in (workdir / "output").glob("*") if p.is_file()) if (workdir / "output").is_dir() else 0
    report["pyrightconfig"] = (workdir / "pyrightconfig.json").is_file()
    missing = [MARKER] * (not report["marker"]["present"]) + [d for d, ok in report["dirs"].items() if not ok]
    report["missing_required"] = missing
    report["missing_recommended"] = [d for d, ok in report["recommended_dirs"].items() if not ok] + \
        [name for name, ok in (("config/.gitignore", report["config"]["gitignore"]), ("config/api.toml", report["config"]["api_toml"]))
         if not ok and report["dirs"]["config"]]
    return report


def create_missing(workdir: Path) -> list[str]:
    created: list[str] = []
    for d in REQUIRED_DIRS + RECOMMENDED_DIRS:
        path = workdir / d
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(d + "/")
    marker = workdir / MARKER
    if not marker.exists():
        marker.write_text(MARKER_CONTENT, encoding="utf-8")
        created.append(MARKER)
    gitignore = workdir / "config" / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(CONFIG_GITIGNORE, encoding="utf-8")
        created.append("config/.gitignore")
    api = workdir / "config" / "api.toml"
    if not api.exists():
        api.write_text(API_TOML, encoding="utf-8")
        created.append("config/api.toml")
    return created


def print_report(root: Path, workdir: Path, how: str, r: dict, created: list[str]) -> None:
    print(f"Project  : {root}")
    print(f"Workdir  : {workdir}  ({how})")
    if created:
        print("Created  : " + ", ".join(created))
    mark = r["marker"]
    print(f".pyne    : {'present, workdir_version=' + str(mark['version']) if mark['present'] else 'MISSING'}")
    print("Required : " + "  ".join(f"{d}/ {'ok' if ok else 'MISSING'}" for d, ok in r["dirs"].items()))
    print("Optional : " + "  ".join(f"{d}/ {'ok' if ok else '-'}" for d, ok in r["recommended_dirs"].items())
          + f"  config/.gitignore {'ok' if r['config']['gitignore'] else '-'}  config/api.toml {'ok' if r['config']['api_toml'] else '-'}"
          + f"  pyrightconfig.json {'ok' if r['pyrightconfig'] else '-'}")
    if r["config"]["plugins"] or r["config"]["symbol_map"]:
        print("Config   : plugins " + (", ".join(r["config"]["plugins"]) or "none") + ("; symbol_map.toml" if r["config"]["symbol_map"] else ""))
    if r["scripts"]:
        print("Scripts  :")
        for s in r["scripts"]:
            print(f"           {s['name']:<30} {s['role']:<28} {'(.toml)' if s['toml'] else ''}")
    else:
        print("Scripts  : none")
    if r.get("libraries"):
        print("Libraries: " + ", ".join(r["libraries"]))
    if r["data"]:
        print("Data     :")
        for d in r["data"]:
            if d.get("csv_not_converted"):
                print(f"           {d['name']:<30} CSV, not converted (pass it to `pyne run` or `pyne data convert-from`)")
            else:
                print(f"           {d['name']:<30} {d.get('ticker') or '?'} {d.get('period') or '?'} {d.get('type') or ''} {'' if d['toml'] else 'MISSING .toml'}")
    else:
        print("Data     : none (pyne --recreate-demo plugin list, or pyne data download ...)")
    print(f"Output   : {r['output_files']} file(s)")
    if r["missing_required"]:
        print("Missing  : " + ", ".join(r["missing_required"]) + "   -> run with --create")
    elif r["missing_recommended"]:
        print("Note     : optional items absent: " + ", ".join(r["missing_recommended"]) + "   (--create adds them)")
    else:
        print("Structure: complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="project folder (default: current directory)")
    parser.add_argument("--create", action="store_true", help="create missing folders and files; existing files are never touched")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workdir, how = locate_workdir(root)
    created: list[str] = []
    if args.create:
        created = create_missing(workdir)
        if created:
            workdir, how = locate_workdir(root)
    report = scan(workdir)
    if args.json:
        print(json.dumps({"root": str(root), "workdir": str(workdir), "found_by": how, "created": created, **report}, indent=2))
    else:
        print_report(root, workdir, how, report, created)
    return 0 if not report["missing_required"] else 1


if __name__ == "__main__":
    sys.exit(main())
