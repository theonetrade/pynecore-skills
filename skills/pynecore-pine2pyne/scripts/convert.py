#!/usr/bin/env python3
r"""
Transpile Pine Script sources into a PyneCore workdir with the local `pine2pyne` command and
report what the result still needs: library modules under scripts/lib/, a stale `.py.map` left by
a cloud compile, a same-stem `.pine` in scripts/ (which makes a bare-name `pyne run` reach the
cloud compiler), and the exact `pyne run` command to use. Never calls `pyne compile` or any
network service.

Standard library only. Runs check_install.py first (same folder) and refuses to continue when the
installed transpiler is not `pine2pyne` >= 0.2.11.

    python3 convert.py my_script.pine --workdir /path/to/workdir            # -> scripts/my_script.py
    python3 convert.py a.pine b.pine --workdir WD                             # -> scripts/a.py, scripts/b.py
    python3 convert.py "src/*.pine" --workdir WD                              # a quoted glob, expanded here
    python3 convert.py KernelFunctions.pine --workdir WD --lib jdehorty/KernelFunctions/2
                                                                              # -> scripts/lib/jdehorty/KernelFunctions/v2.py
    python3 convert.py my_script.pine --workdir WD --name renamed             # output stem
    python3 convert.py my_script.pine --workdir WD --validate                 # checks only, nothing written
    python3 convert.py my_script.pine --workdir WD --pine2pyne /path/to/venv/bin/pine2pyne

Exit codes: 0 every input converted (warnings allowed; missing libraries are reported, not fatal);
1 a conversion failed or the installed transpiler is wrong; 2 usage error (no workdir, bad --lib).
"""
from __future__ import annotations

import argparse
import glob
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True  # no __pycache__ in the skill folder
import check_install  # noqa: E402  (sibling script)

LIB_IMPORT_RE = re.compile(r"^\s*import\s+lib\.([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\.v(\d+)\b", re.M)
LIB_SPEC_RE = re.compile(r"^([A-Za-z0-9_\-]+)/([A-Za-z_][\w]*)/(\d+)$")


def find_workdir(given: str | None) -> Path | None:
    """--workdir, else the current folder or its workdir/ child when it holds the .pyne marker."""
    if given:
        return Path(given).resolve()
    for cand in (Path.cwd(), Path.cwd() / "workdir"):
        if (cand / ".pyne").is_file():
            return cand.resolve()
    return None


def find_pine2pyne(explicit: str | None, workdir: Path) -> tuple[Path | None, Path | None]:
    """(command, interpreter that owns it): --pine2pyne, else a project venv next to the workdir, else PATH."""
    if explicit:
        p = Path(explicit).absolute()  # not resolve(): keep the venv path, see check_install.py
        return (p if p.is_file() else None), (check_install.interpreter_of_script(p) if p.is_file() else None)
    for base in (workdir, workdir.parent):
        for name in check_install.VENV_CANDIDATES:
            for bin_dir in check_install.BIN_DIRS:
                for exe in ("pine2pyne", "pine2pyne.exe"):
                    cand = base / name / bin_dir / exe
                    if cand.is_file():
                        return cand, check_install.interpreter_of_script(cand)
    on_path = shutil.which("pine2pyne")
    if on_path:
        return Path(on_path), check_install.interpreter_of_script(Path(on_path))
    return None, None


def expand_inputs(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pat in patterns:
        p = Path(pat)
        if p.is_file():
            files.append(p)
        elif "*" in pat or "?" in pat:
            files += [Path(m) for m in sorted(glob.glob(pat, recursive=True)) if Path(m).is_file()]
        else:
            raise SystemExit(f"{pat} is not a file or a glob pattern")
    if not files:
        raise SystemExit("no input files")
    return files


def output_path(workdir: Path, src: Path, lib: str | None, name: str | None) -> Path:
    if lib:
        m = LIB_SPEC_RE.match(lib)
        if not m:
            raise SystemExit(f"--lib must be <user>/<Name>/<version> as written in the Pine import line, got: {lib}")
        user, module, version = m.group(1).replace("-", "_"), m.group(2), m.group(3)
        return workdir / "scripts" / "lib" / user / module / f"v{version}.py"
    return workdir / "scripts" / f"{name or src.stem}.py"


def library_status(py_file: Path, workdir: Path) -> list[tuple[str, Path, bool]]:
    """Every `import lib.<user>.<Name>.v<N>` of a generated file with the expected path and whether it exists."""
    text = py_file.read_text(encoding="utf-8", errors="replace")
    rows = []
    for user, module, version in LIB_IMPORT_RE.findall(text):
        path = workdir / "scripts" / "lib" / user / module / f"v{version}.py"
        rows.append((f"lib.{user}.{module}.v{version}", path, path.is_file()))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Transpile Pine sources into a PyneCore workdir with the local pine2pyne and report what the result needs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Standard library only.")[0].strip())
    ap.add_argument("inputs", nargs="+", metavar="INPUT",
                    help="Pine source file paths, or quoted glob patterns (`\"src/*.pine\"`, `\"**/*.pine\"`) expanded by this script")
    ap.add_argument("--workdir", metavar="DIR",
                    help="the PyneCore workdir (holds .pyne, scripts/, data/); default: the current folder or its workdir/ child when one has the .pyne marker")
    ap.add_argument("--lib", metavar="USER/NAME/N",
                    help="the input is a library: write it to scripts/lib/<USER with - as _>/<NAME>/v<N>.py; USER/NAME/N exactly as in the Pine `import` line (one input only)")
    ap.add_argument("--name", metavar="STEM", help="output file stem for a single non-library input (default: the input's stem)")
    ap.add_argument("--profile", choices=("pynecomp", "legacy"), default="pynecomp",
                    help="pine2pyne output profile (default: pynecomp)")
    ap.add_argument("--validate", action="store_true", help="run pine2pyne --validate on each input; write nothing")
    ap.add_argument("--pine2pyne", metavar="PATH",
                    help="path of the pine2pyne command (default: a project venv next to the workdir, then PATH)")
    ap.add_argument("--skip-install-check", action="store_true",
                    help="do not verify the distribution/version first (only for environments already checked in this session)")
    args = ap.parse_args(argv)

    workdir = find_workdir(args.workdir)
    if workdir is None:
        print("no workdir: pass --workdir DIR (a folder with the .pyne marker, scripts/ and data/)", file=sys.stderr)
        return 2
    if (args.lib or args.name) and len(args.inputs) != 1:
        print("--lib and --name take exactly one input", file=sys.stderr)
        return 2
    try:
        inputs = expand_inputs(args.inputs)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    if (args.lib or args.name) and len(inputs) != 1:
        print("--lib and --name take exactly one input", file=sys.stderr)
        return 2

    exe, owner = find_pine2pyne(args.pine2pyne, workdir)
    if exe is None:
        print("pine2pyne command not found: install it (`pip install \"pine2pyne>=0.2.11\"` or `uv tool install \"pine2pyne>=0.2.11\"`) "
              "or pass --pine2pyne PATH", file=sys.stderr)
        return 1
    if not args.skip_install_check:
        if owner is None:
            print(f"cannot find the interpreter that owns {exe}; run check_install.py --python <interpreter> by hand "
                  f"or pass --skip-install-check", file=sys.stderr)
            return 1
        info = check_install.probe(owner)
        state, why = check_install.verdict(info)
        if state != "ok":
            print(f"installed transpiler rejected: {why}", file=sys.stderr)
            for c in check_install.repair_commands(owner, info):
                print("  " + " ".join(c), file=sys.stderr)
            return 1
        print(f"pine2pyne {info['pine2pyne']} at {exe}", flush=True)  # before the subprocess output

    failed = 0
    written: list[Path] = []
    for src in inputs:
        out = output_path(workdir, src, args.lib, args.name)
        cmd = [str(exe), str(src), "--profile", args.profile]
        if args.validate:
            cmd.append("--validate")
        else:
            cmd += ["-o", str(out)]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            failed += 1
            continue
        if not args.validate and out.is_file():
            written.append(out)

    for out in written:
        stem = out.stem
        if out.parent == workdir / "scripts":
            pine_twin = out.with_suffix(".pine")
            if pine_twin.is_file():
                print(f"note: {pine_twin.name} sits next to {out.name}: run it as `pyne run {out.name} <data>` (with the suffix), "
                      f"a bare `{stem}` resolves to the .pine and the cloud compiler")
            map_file = out.parent / f"{out.name}.map"
            if map_file.is_file():
                print(f"note: {map_file.name} is a cloud-compile source map; it is stale now and ignored by PyneIDE (leave it)")
        for module, path, exists in library_status(out, workdir):
            rel = path.relative_to(workdir)
            print(f"{'ok     ' if exists else 'MISSING'} {module} -> {rel}" + ("" if exists else
                  f"   (transpile the library source: convert.py <Lib>.pine --workdir {workdir} --lib <user>/<Name>/<N>)"))
    if written and not args.validate:
        scripts = [w for w in written if w.parent == workdir / "scripts"]
        if scripts:
            print("run: PYNE_NO_LOGO=1 pyne --workdir " + str(workdir) + " run " + scripts[0].name + " <data>")
    if len(inputs) > 1:
        print(f"Summary: {len(inputs) - failed} succeeded, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
