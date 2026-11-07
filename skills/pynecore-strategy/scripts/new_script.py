#!/usr/bin/env python3
"""
Create a new Pyne script file from one of this skill's templates.

Standard library only.

    python3 new_script.py NAME [--kind KIND] [--title TITLE] [--cli-only] [--workdir DIR] [--create] [--force]

NAME is the file to create: `scripts/NAME.py` (or `scripts/lib/PUBLISHER/NAME/vN.py` for a
library). It is your choice and does not refer to anything that exists; the content comes from
the template selected by --kind:

    --kind strategy           templates/strategy_template.py      full strategy, standalone-runnable
    --kind indicator          templates/indicator_template.py     full indicator, standalone-runnable
    --kind library            templates/library_template.py       shared helpers under scripts/lib/
    --kind minimal            templates/minimal_cli.py            compact workdir strategy, `pyne run` / IDE only
    --kind minimal-indicator  templates/minimal_cli_indicator.py  compact workdir indicator, `pyne run` / IDE only

--title sets the script title in the docstring and decorator (default: derived from NAME).
--cli-only strips the dependency header and the `if __name__ == "__main__":` bootstrap from the
full templates, leaving a workdir-only script.

The workdir is discovered like PyneCore does (a `.pyne` marker in the current directory or a
parent, else a `workdir/` child); pass --workdir to override. Missing `scripts/`, `data/`,
`output/`, `config/` folders and the `.pyne` marker are created on demand (--create).
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import sys
from pathlib import Path

TEMPLATES = {
    "strategy": ("strategy_template.py", "Strategy Template"),
    "indicator": ("indicator_template.py", "Indicator Template"),
    "library": ("library_template.py", "Library Template"),
    "minimal": ("minimal_cli.py", "EMA Crossover Strategy"),
    "minimal-indicator": ("minimal_cli_indicator.py", "EMA Crossover Demo"),
}


def strip_standalone(text: str) -> str:
    """Remove the inline dependency header and the `if __name__ == "__main__":` bootstrap, leaving
    a workdir-only script that runs through `pyne run` or the IDE."""
    lines = text.splitlines()
    if lines and lines[0].startswith("# /// script"):
        end = next((i for i, line in enumerate(lines) if line.strip() == "# ///"), None)
        if end is not None:
            lines = lines[end + 1:]
            while lines and not lines[0].strip():
                lines.pop(0)
    main_idx = next((i for i, line in enumerate(lines) if line.startswith('if __name__ == "__main__":')), None)
    if main_idx is not None:
        lines = lines[:main_idx]
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines) + "\n"


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


def find_pyne(workdir: Path) -> str | None:
    """The project's pyne by full path: a .venv / venv in the workdir or up to two folders above it,
    then the environment of this interpreter (not resolved: a venv's python is a symlink), then PATH."""
    rels = ("bin/pyne", "Scripts/pyne.exe")
    folder = workdir.resolve()
    for _ in range(3):
        for venv in (".venv", "venv"):
            for rel in rels:
                if (folder / venv / rel).is_file():
                    return str(folder / venv / rel)
        folder = folder.parent
    for base in (Path(sys.executable).parent, Path(sys.prefix)):
        for rel in ("pyne", "pyne.exe", *rels):
            if (base / rel).is_file():
                return str(base / rel)
    return shutil.which("pyne")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("name", metavar="NAME",
                        help="name of the file to create, a Python identifier in snake_case: lowercase letters, digits\n"
                             "and underscores, not starting with a digit. Becomes scripts/NAME.py, or\n"
                             "scripts/lib/PUBLISHER/NAME/vN.py for --kind library. Example: donchian_breakout")
    parser.add_argument("--kind", choices=sorted(TEMPLATES), default="strategy", metavar="KIND",
                        help="template to copy:\n"
                             "  strategy           templates/strategy_template.py      full strategy, standalone-runnable\n"
                             "  indicator          templates/indicator_template.py     full indicator, standalone-runnable\n"
                             "  library            templates/library_template.py       helpers under scripts/lib/\n"
                             "  minimal            templates/minimal_cli.py            compact workdir strategy (pyne run / IDE only)\n"
                             "  minimal-indicator  templates/minimal_cli_indicator.py  compact workdir indicator (pyne run / IDE only)\n"
                             "Default: strategy")
    parser.add_argument("--title", metavar="TEXT",
                        help="script title written into the docstring and the @script decorator; any text, quote spaces.\n"
                             "Default: NAME with underscores replaced by spaces and each word capitalized.\n"
                             "Example: --title \"Donchian Breakout\"")
    parser.add_argument("--workdir", type=Path, metavar="DIR",
                        help="target workdir: the folder that holds .pyne and scripts/.\n"
                             "Default: discovered from the current directory upwards (.pyne marker, else a workdir/ child).")
    parser.add_argument("--publisher", default="local", metavar="NAME",
                        help="--kind library only: namespace folder under scripts/lib/, a snake_case identifier.\n"
                             "The module is imported as lib.NAME.<name>.vN. Default: local")
    parser.add_argument("--version", type=int, default=1, metavar="N",
                        help="--kind library only: version number N >= 1, giving the file vN.py. Default: 1")
    parser.add_argument("--create", action="store_true",
                        help="create the workdir structure when missing (scripts/lib, data, output, config, .pyne);\n"
                             "when no workdir is found at all, ./workdir is created")
    parser.add_argument("--force", action="store_true",
                        help="overwrite the target file if it already exists (default: refuse)")
    parser.add_argument("--cli-only", action="store_true",
                        help="remove the inline dependency header (# /// script ... # ///) and the\n"
                             "if __name__ == \"__main__\": block from the full templates, leaving a workdir script that\n"
                             "runs only through pyne run or the IDE. No effect on minimal* and library templates.")
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
    if args.cli_only:
        text = strip_standalone(text)

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
        pyne = find_pyne(workdir)
        print(f"run it with:  PYNE_NO_LOGO=1 {shlex.quote(pyne) if pyne else '<venv>/bin/pyne'} "
              f"--workdir {shlex.quote(str(workdir))} run {args.name} <data>")
        if not pyne:
            print("  (no pyne found in a .venv/venv next to the workdir, beside this interpreter or on PATH;\n"
                  "   install pynesys-pynecore[cli] into the project's environment)")
        print("the first run writes scripts/%s.toml with every input and setting" % args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
