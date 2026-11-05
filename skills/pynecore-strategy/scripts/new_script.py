#!/usr/bin/env python3
"""
Scaffold a new Pyne strategy, indicator or library from this skill's templates.

Standard library only.

    python3 new_script.py my_breakout --kind strategy --title "Donchian Breakout"
    python3 new_script.py rsi_bands --kind indicator
    python3 new_script.py utils --kind library --publisher mylib     # -> scripts/lib/mylib/utils/v1.py
    python3 new_script.py my_breakout --workdir /path/to/workdir --force

The workdir is discovered like PyneCore does (a `.pyne` marker in the current directory or a
parent, else a `workdir/` child); pass --workdir to override. Missing `scripts/`, `data/`,
`output/`, `config/` folders and the `.pyne` marker are created on demand (--create).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TEMPLATES = {
    "strategy": ("strategy_template.py", "Strategy Template"),
    "indicator": ("indicator_template.py", "Indicator Template"),
    "library": ("library_template.py", "Library Template"),
}


def find_workdir(start: Path) -> Path | None:
    current = start.resolve()
    for _ in range(10):
        if (current / ".pyne").is_file():
            return current
        child = current / "workdir"
        if (child / ".pyne").is_file() or (child / "scripts").is_dir():
            return child
        if current.parent == current:
            break
        current = current.parent
    return None


def ensure_workdir(path: Path) -> None:
    for sub in ("scripts/lib", "data", "output", "config"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    marker = path / ".pyne"
    if not marker.exists():
        marker.write_text('workdir_version = "1.0"\n', encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="file stem, snake_case (becomes scripts/<name>.py)")
    parser.add_argument("--kind", choices=sorted(TEMPLATES), default="strategy")
    parser.add_argument("--title", help="script title shown in outputs (default: derived from name)")
    parser.add_argument("--workdir", type=Path, help="target workdir (default: discovered)")
    parser.add_argument("--publisher", default="local", help="library namespace folder under scripts/lib (library kind)")
    parser.add_argument("--version", type=int, default=1, help="library version number (library kind)")
    parser.add_argument("--create", action="store_true", help="create the workdir structure when missing")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z_][a-z0-9_]*", args.name):
        raise SystemExit("name must be a Python identifier in snake_case (letters, digits, underscores)")

    workdir = args.workdir or find_workdir(Path.cwd())
    if workdir is None:
        if args.create:
            workdir = Path.cwd() / "workdir"
        else:
            raise SystemExit("no workdir found; pass --workdir PATH or --create to make ./workdir")
    if args.create or not (workdir / "scripts").is_dir():
        ensure_workdir(workdir)

    template_name, default_title = TEMPLATES[args.kind]
    template = Path(__file__).resolve().parent.parent / "templates" / template_name
    if not template.is_file():
        raise SystemExit(f"template missing: {template}")

    title = args.title or args.name.replace("_", " ").title()
    text = template.read_text(encoding="utf-8").replace(default_title, title)

    if args.kind == "library":
        dest = workdir / "scripts" / "lib" / args.publisher / args.name / f"v{args.version}.py"
        import_line = f"from lib.{args.publisher}.{args.name}.v{args.version} import <function>"
    else:
        dest = workdir / "scripts" / f"{args.name}.py"
        import_line = None

    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} exists; use --force to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"created {dest}")
    if import_line:
        print(f"import it from a script with:  {import_line}")
    else:
        print(f"run it with:  PYNE_NO_LOGO=1 pyne --workdir {workdir} run {args.name} <data>")
        print("the first run writes scripts/%s.toml with every input and setting" % args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
