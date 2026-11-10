#!/usr/bin/env python3
r"""
Check Pyne files against the `@pyne edge` profile with the edge gate of the PyneCore installed in a
project environment (`pynecore.transformers.pine_edge_gate`), without running the scripts and
without the bytecode cache that hides the gate's findings on a second `pyne run`.

Prints the PyneCore version, the profile revision the gate carries (`edge_rules.json`), the header
mode of each file (`@pyne edge`, `@pyne`, `@pyne lib`, or no marker) and every structural finding in
the gate's own format (`<path>:<line>:<col>: <message> [<reason>@<line>:<col> <node>] fix: <hint>`).
A file without the `edge` word is gated anyway, so a hand-written script can be checked before the
word is added. The typed half of the diagnostics (unknown calls and names, types) is not run here:
`PYNE_TYPE_DIAG=1 pyne --workdir <wd> debug ast <file>` prints both halves.

Standard library only; PyneCore is imported by the project's interpreter, never by this script.

    python3 edge_check.py scripts/my_script.py                  # interpreter: a venv under . or .., else VIRTUAL_ENV
    python3 edge_check.py scripts/*.py --root /path/to/project    # look for .venv, venv, env, .pyneide/venv there
    python3 edge_check.py scripts/my_script.py --python /path/to/venv/bin/python
    python3 edge_check.py scripts/my_script.py --json

Exit codes: 0 no finding in any file; 1 at least one finding (or a file that does not parse);
2 usage error (no interpreter with PyneCore found, file missing).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True  # no __pycache__ in the skill folder
import check_install  # noqa: E402  (sibling script: venv layout constants)

PROBE = r"""
import ast, importlib.metadata as m, json, re, sys
try:
    from pynecore.transformers.pine_edge_gate import EDGE_RULES_VERSION, gate_module, render_diags
except Exception as exc:  # no PyneCore, or one without the gate (before 6.12)
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"})); sys.exit(0)
try:
    version = m.version("pynesys-pynecore")
except m.PackageNotFoundError:
    version = None
out = {"python": sys.executable, "pynecore": version, "profile": EDGE_RULES_VERSION, "files": []}
for path in sys.argv[1:]:
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        out["files"].append({"path": path, "mode": None, "parse_error": str(exc), "findings": []}); continue
    mode = "none"
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        magic = re.match(r"\s*@pyne(?:[ \t]+(?P<mode>\w+))?(\s|$)", first.value.value)
        if magic:
            mode = magic.group("mode") or "pyne"
    diags = gate_module(tree, src)
    out["files"].append({"path": path, "mode": mode, "parse_error": None,
                         "findings": render_diags(diags, path).splitlines() if diags else []})
print(json.dumps(out))
"""

MODE_LABEL = {"edge": "@pyne edge", "pyne": "@pyne (no edge word; gated anyway)",
              "lib": "@pyne lib (gated anyway)", "none": "no @pyne marker (not a Pyne module; gated anyway)"}


def has_pynecore(python: Path) -> bool:
    try:
        r = subprocess.run([str(python), "-I", "-c", "import pynecore.transformers.pine_edge_gate"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def find_python(root: Path, explicit: Path | None) -> tuple[Path | None, str]:
    """An interpreter that imports pynecore's edge gate: --python, a venv under root or its parent, VIRTUAL_ENV."""
    if explicit:
        return (explicit if explicit.is_file() else None), f"--python {explicit}"
    for base in (root, root.parent):
        for name in check_install.VENV_CANDIDATES:
            for bin_dir in check_install.BIN_DIRS:
                for py in check_install.PYTHON_NAMES:
                    cand = base / name / bin_dir / py
                    if cand.is_file() and has_pynecore(cand):
                        return cand, f"{base / name}"
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        for bin_dir in check_install.BIN_DIRS:
            for py in check_install.PYTHON_NAMES:
                cand = Path(active) / bin_dir / py
                if cand.is_file() and has_pynecore(cand):
                    return cand, f"VIRTUAL_ENV {active}"
    return None, "no interpreter with PyneCore found"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check Pyne files against the @pyne edge profile with the installed PyneCore's edge gate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Standard library only")[0].strip())
    ap.add_argument("files", nargs="+", metavar="FILE", help="Pyne .py files to check")
    ap.add_argument("--root", metavar="DIR", default=".",
                    help="project folder whose venv (.venv, venv, env, .pyneide/venv; also in the parent folder) "
                         "holds PyneCore (default: the current directory)")
    ap.add_argument("--python", metavar="PATH", help="interpreter of the environment that holds PyneCore")
    ap.add_argument("--json", action="store_true", help="print the result as one JSON object")
    args = ap.parse_args(argv)

    files = [Path(f) for f in args.files]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("file not found: " + ", ".join(map(str, missing)), file=sys.stderr)
        return 2
    # absolute(), not resolve(): a venv's python is a symlink to the base interpreter
    python, how = find_python(Path(args.root).absolute(), Path(args.python).absolute() if args.python else None)
    if python is None:
        print(f"{how}: pass --python <venv>/bin/python of an environment with pynesys-pynecore >= 6.12",
              file=sys.stderr)
        return 2
    try:
        r = subprocess.run([str(python), "-I", "-c", PROBE, *map(str, files)],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"cannot run {python}: {exc}", file=sys.stderr)
        return 2
    if r.returncode != 0:
        print(f"{python} failed:\n{r.stderr.strip()}", file=sys.stderr)
        return 2
    result = json.loads(r.stdout)
    if "error" in result:
        print(f"{python}: {result['error']} (PyneCore >= 6.12 with its edge gate is needed)", file=sys.stderr)
        return 2
    result["chosen"] = how
    bad = sum(1 for f in result["files"] if f["findings"] or f["parse_error"])
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"pynecore {result['pynecore']}, edge profile {result['profile']}, "
              f"interpreter {result['python']} ({how})")
        for f in result["files"]:
            if f["parse_error"]:
                print(f"{f['path']}: does not parse: {f['parse_error']}")
                continue
            n = len(f["findings"])
            print(f"{f['path']}: {MODE_LABEL.get(f['mode'], f['mode'])}, "
                  f"{n} finding{'s' if n != 1 else ''}")
            for line in f["findings"]:
                print("  " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
