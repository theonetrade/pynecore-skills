#!/usr/bin/env python3
"""
Detect a PyneIDE project and report its environment.

Standard library only. Only the project folder is inspected: `.pyneide/` (the IDE-managed
environment and its `env.json`) and the `pyneide.*` keys of `.vscode/settings.json`. No agent
instruction files, no editor installation, nothing outside the project.

    python3 ide_env.py                 # report for the current directory
    python3 ide_env.py --root PATH     # another workspace
    python3 ide_env.py --json

Reports: whether PyneIDE manages this project, the interpreter the IDE uses (managed
`.pyneide/venv` or a user-provided one from `pyneide.venvPath` / `pyneide.pythonPath`), the
installed PyneCore and debugpy versions, the `pyne` executable, and the workdir (from
`pyneide.workdir` or the `.pyne` marker).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def read_jsonc(path: Path) -> dict:
    """VS Code settings may contain comments and trailing commas."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(text)
    except ValueError:
        return {}


def python_in(venv: Path) -> Path | None:
    for rel in ("bin/python", "bin/python3", "Scripts/python.exe"):
        if (venv / rel).exists():
            return venv / rel
    return None


def probe(python: Path) -> dict:
    code = ("import json,sys,importlib.metadata as m\n"
            "def v(n):\n"
            "    try: return m.version(n)\n"
            "    except Exception: return None\n"
            "print(json.dumps({'python': sys.version.split()[0], 'pynecore': v('pynesys-pynecore'), 'debugpy': v('debugpy')}))")
    try:
        out = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return {}


def find_workdir(root: Path, setting: str | None) -> Path | None:
    if setting:
        return Path(setting) if os.path.isabs(setting) else (root / setting).resolve()
    current = root.resolve()
    for _ in range(10):
        if (current / ".pyne").is_file():
            return current
        if (current / "workdir" / ".pyne").is_file():
            return current / "workdir"
        if current.parent == current:
            break
        current = current.parent
    return None


def build(root: Path) -> dict:
    settings = read_jsonc(root / ".vscode" / "settings.json")
    pyneide_settings = {k: v for k, v in settings.items() if k.startswith("pyneide.")}
    env_json: dict = {}
    env_file = root / ".pyneide" / "env.json"
    if env_file.is_file():
        try:
            env_json = json.loads(env_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    python: Path | None = None
    source = None
    if pyneide_settings.get("pyneide.venvPath"):
        p = Path(pyneide_settings["pyneide.venvPath"])
        python = python_in(p if p.is_absolute() else root / p)
        source = "pyneide.venvPath"
    elif pyneide_settings.get("pyneide.pythonPath"):
        p = Path(pyneide_settings["pyneide.pythonPath"])
        python = p if p.is_absolute() else root / p
        source = "pyneide.pythonPath"
    elif (root / ".pyneide" / "venv").is_dir():
        python = python_in(root / ".pyneide" / "venv")
        source = "managed .pyneide/venv"
    if python is None:
        for name in (".venv", "venv"):
            cand = python_in(root / name)
            if cand:
                python, source = cand, f"{name} (no PyneIDE environment)"
                break

    versions = probe(python) if python and python.exists() else {}
    pyne = None
    if python:
        for name in ("pyne", "pyne.exe"):
            if (python.parent / name).exists():
                pyne = str(python.parent / name)
                break

    return {
        "root": str(root.resolve()),
        "pyneide_project": bool(env_file.is_file() or (root / ".pyneide").is_dir() or pyneide_settings),
        "workdir": str(find_workdir(root, pyneide_settings.get("pyneide.workdir") or None) or "") or None,
        "workdir_setting": pyneide_settings.get("pyneide.workdir"),
        "python": str(python) if python else None,
        "python_source": source,
        "versions": versions,
        "env_json": env_json,
        "pyne": pyne,
        "pyneide_settings": pyneide_settings,
        "interpreter_setting": settings.get("python.defaultInterpreterPath"),
        "pyrightconfig": (root / "pyrightconfig.json").is_file(),
    }


def print_report(r: dict) -> None:
    print(f"Workspace        : {r['root']}")
    print(f"PyneIDE project  : {'yes' if r['pyneide_project'] else 'no (no .pyneide/ folder or pyneide.* settings)'}")
    print(f"Workdir          : {r['workdir'] or 'not found'}" + (f"  (pyneide.workdir = {r['workdir_setting']!r})" if r['workdir_setting'] else ""))
    v = r["versions"]
    if r["python"]:
        print(f"Interpreter      : {r['python']}  [{r['python_source']}]")
        print(f"                   python {v.get('python') or '?'}, pynecore {v.get('pynecore') or 'NOT INSTALLED'}, debugpy {v.get('debugpy') or 'missing'}")
    else:
        print("Interpreter      : none found; run PyneIDE: Setup Environment or set pyneide.venvPath")
    print(f"pyne CLI         : {r['pyne'] or 'not found next to the interpreter'}")
    if r["env_json"]:
        e = r["env_json"]
        print(f".pyneide/env.json: python {e.get('python')}, pynecore {e.get('pynecore')}, debugpy {e.get('debugpy')}, selection {e.get('selection') or 'managed'}")
    if r["pyneide_settings"]:
        print("Settings         : " + ", ".join(f"{k}={v!r}" for k, v in r["pyneide_settings"].items()))
    print(f"pyrightconfig    : {'present' if r['pyrightconfig'] else 'absent'}")
    if r["pyne"] and r["workdir"]:
        print()
        print(f"Run from a shell : PYNE_NO_LOGO=1 {r['pyne']} --workdir {r['workdir']} run <script> <data>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build(Path(args.root))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
