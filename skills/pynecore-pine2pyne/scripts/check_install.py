#!/usr/bin/env python3
r"""
Verify that the Pine Script transpiler installed in an environment is the PyPI distribution
`pine2pyne` (https://pypi.org/project/pine2pyne) at version 0.2.11 or newer, and not the
abandoned `opencode-pine2pyne` (https://pypi.org/project/opencode-pine2pyne/), which installs the
same import package and the same `pine2pyne` command. Optionally repair the environment:
uninstall both distribution names, then install `pine2pyne>=0.2.11`.

Standard library only. Reads package metadata through the environment's own interpreter
(`importlib.metadata`); the transpiler module is imported only after the verdict is OK.

    python3 check_install.py                          # the environment that owns `pine2pyne` on PATH (or the project venv)
    python3 check_install.py --root /path/to/project  # look for .venv, venv, env, .pyneide/venv under that folder first
    python3 check_install.py --python /path/to/venv/bin/python   # inspect exactly that interpreter
    python3 check_install.py --fix                    # run the repair commands (uv pip or pip) in that environment
    python3 check_install.py --json                   # machine-readable verdict

Exit codes: 0 the right package at a valid version; 1 wrong package, wrong version, not installed,
or the repair failed; 2 usage error (interpreter not found or not runnable).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RIGHT = "pine2pyne"
WRONG = "opencode-pine2pyne"
MIN_VERSION = (0, 2, 11)
PYPI_RIGHT = "https://pypi.org/project/pine2pyne"
PYPI_WRONG = "https://pypi.org/project/opencode-pine2pyne/"
VENV_CANDIDATES = (".venv", "venv", "env", os.path.join(".pyneide", "venv"))
BIN_DIRS = ("bin", "Scripts")
PYTHON_NAMES = ("python", "python3", "python.exe")

PROBE = r"""
import importlib.metadata as m, json, sys
out = {"python": sys.executable, "python_version": "%d.%d.%d" % sys.version_info[:3]}
for dist in ("pine2pyne", "opencode-pine2pyne"):
    try:
        out[dist] = m.version(dist)
    except m.PackageNotFoundError:
        out[dist] = None
try:
    out["providers"] = sorted(m.packages_distributions().get("pine2pyne", []))
except Exception:
    out["providers"] = None
print(json.dumps(out))
"""


def version_tuple(text: str) -> tuple[int, ...]:
    """'0.2.11' -> (0, 2, 11); a pre-release or local suffix is ignored for the comparison."""
    nums = re.match(r"(\d+(?:\.\d+)*)", text)
    return tuple(int(p) for p in nums.group(1).split(".")) if nums else (0,)


def interpreter_of_script(script: Path) -> Path | None:
    """The interpreter that owns a console script: the python next to it (venv and uv-tool layouts;
    uv writes a `#!/bin/sh` relaunch shebang), else a python named by its shebang."""
    for name in PYTHON_NAMES:
        py = script.parent / name
        if py.is_file():
            return py
    try:
        first = script.open("rb").readline().decode("utf-8", "replace").strip()
    except OSError:
        first = ""
    if first.startswith("#!"):
        words = first[2:].split()
        cand = Path(words[-1]) if words and "python" in words[-1] else None
        if cand and cand.is_file():
            return cand
    return None


def find_python(root: Path, explicit: Path | None) -> tuple[Path | None, str]:
    """Pick the interpreter to inspect; returns (path, how it was chosen)."""
    if explicit:
        return (explicit if explicit.is_file() else None), f"--python {explicit}"
    for name in VENV_CANDIDATES:
        for bin_dir in BIN_DIRS:
            for py in PYTHON_NAMES:
                cand = root / name / bin_dir / py
                if cand.is_file() and any((cand.parent / s).is_file() for s in ("pine2pyne", "pine2pyne.exe")):
                    return cand, f"{root / name} (has the pine2pyne command)"
    on_path = shutil.which("pine2pyne")
    if on_path:
        py = interpreter_of_script(Path(on_path))
        if py:
            return py, f"owner of {on_path}"
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        for bin_dir in BIN_DIRS:
            for py in PYTHON_NAMES:
                cand = Path(active) / bin_dir / py
                if cand.is_file():
                    return cand, f"VIRTUAL_ENV {active}"
    for name in VENV_CANDIDATES:
        for bin_dir in BIN_DIRS:
            for py in PYTHON_NAMES:
                cand = root / name / bin_dir / py
                if cand.is_file():
                    return cand, f"{root / name}"
    return Path(sys.executable), "this interpreter (no pine2pyne command found on PATH or in a project venv)"


def probe(python: Path) -> dict:
    try:
        r = subprocess.run([str(python), "-I", "-c", PROBE], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"cannot run {python}: {exc}")
    if r.returncode != 0:
        raise SystemExit(f"{python} failed to report its packages:\n{r.stderr.strip()}")
    return json.loads(r.stdout)


def verdict(info: dict) -> tuple[str, str]:
    """('ok' | 'wrong-package' | 'wrong-version' | 'missing', explanation)."""
    right, wrong, providers = info.get(RIGHT), info.get(WRONG), info.get("providers")
    if wrong:
        return "wrong-package", (f"{WRONG} {wrong} is installed ({PYPI_WRONG}); it is the abandoned line of the project "
                                 f"and shares the import package and the `pine2pyne` command with {RIGHT}"
                                 + (f"; {RIGHT} {right} is installed beside it and its files are mixed with the old ones" if right else ""))
    if providers and any(p != RIGHT for p in providers):
        return "wrong-package", f"the import package `pine2pyne` is provided by {providers}, not only by {RIGHT}"
    if not right:
        return "missing", f"{RIGHT} is not installed in this environment"
    if version_tuple(right) < MIN_VERSION:
        return "wrong-version", (f"{RIGHT} {right} is below {'.'.join(map(str, MIN_VERSION))}: "
                                 f"a lower version marks a wrong installation")
    return "ok", f"{RIGHT} {right}"


def repair_commands(python: Path, info: dict) -> list[list[str]]:
    """Uninstall both names (only the installed ones), then install the right one; uv pip when uv exists
    (uv-created environments have no pip), else python -m pip."""
    present = [d for d in (WRONG, RIGHT) if info.get(d)]
    uv = shutil.which("uv")
    cmds: list[list[str]] = []
    if uv:
        if present:
            cmds.append([uv, "pip", "uninstall", "--python", str(python), *present])
        cmds.append([uv, "pip", "install", "--python", str(python), f"{RIGHT}>={'.'.join(map(str, MIN_VERSION))}"])
    else:
        if present:
            cmds.append([str(python), "-m", "pip", "uninstall", "-y", *present])
        cmds.append([str(python), "-m", "pip", "install", f"{RIGHT}>={'.'.join(map(str, MIN_VERSION))}"])
    return cmds


def command_version(python: Path) -> str | None:
    """`python -m pine2pyne --version`; run only once the metadata verdict is OK."""
    try:
        r = subprocess.run([str(python), "-m", "pine2pyne", "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout or r.stderr).strip() if r.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=f"Check that the installed transpiler is the `{RIGHT}` distribution at version "
                    f">= {'.'.join(map(str, MIN_VERSION))} and not `{WRONG}`; optionally repair it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Standard library only.")[0].strip())
    ap.add_argument("--root", metavar="DIR", default=".",
                    help="project folder searched for .venv, venv, env or .pyneide/venv (default: the current directory)")
    ap.add_argument("--python", metavar="PATH", help="absolute or relative path of the interpreter whose environment to inspect "
                                                     "(overrides --root and the PATH lookup)")
    ap.add_argument("--fix", action="store_true",
                    help=f"run the repair: uninstall {WRONG} and {RIGHT} (those present), then install {RIGHT}>="
                         f"{'.'.join(map(str, MIN_VERSION))}; uses `uv pip --python` when uv is on PATH, else `python -m pip`")
    ap.add_argument("--json", action="store_true", help="print the verdict as one JSON object instead of text")
    args = ap.parse_args(argv)

    # absolute(), not resolve(): a venv's python is a symlink to the base interpreter, and following it
    # would report the base environment's packages instead of the venv's
    python, how = find_python(Path(args.root).absolute(), Path(args.python).absolute() if args.python else None)
    if python is None:
        print(f"interpreter not found: {args.python}", file=sys.stderr)
        return 2
    info = probe(python)
    state, why = verdict(info)
    cmds = repair_commands(python, info) if state != "ok" else []

    if args.fix and state != "ok":
        for cmd in cmds:
            print("+ " + " ".join(cmd), file=sys.stderr)
            r = subprocess.run(cmd)
            if r.returncode != 0:
                print(f"repair command failed with exit {r.returncode}", file=sys.stderr)
                return 1
        info = probe(python)
        state, why = verdict(info)
        cmds = repair_commands(python, info) if state != "ok" else []

    cmd_version = command_version(python) if state == "ok" else None
    result = {"python": info["python"], "python_version": info["python_version"], "chosen": how,
              "pine2pyne": info.get(RIGHT), "opencode-pine2pyne": info.get(WRONG), "providers": info.get("providers"),
              "command_version": cmd_version, "state": state, "why": why,
              "repair": [" ".join(c) for c in cmds]}
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"interpreter : {result['python']} (Python {result['python_version']}; {how})")
        print(f"pine2pyne   : {result['pine2pyne'] or 'not installed'}   {WRONG}: {result['opencode-pine2pyne'] or 'not installed'}")
        print(f"providers of the `pine2pyne` package: {result['providers']}")
        if cmd_version:
            print(f"command     : {cmd_version}")
        print(f"verdict     : {state.upper()} - {why}")
        if cmds:
            print("repair (run with --fix, or by hand):")
            for c in cmds:
                print("  " + " ".join(c))
            print(f"install only {RIGHT} ({PYPI_RIGHT}); never {WRONG}.")
    return 0 if state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
