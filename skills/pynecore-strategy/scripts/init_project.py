#!/usr/bin/env python3
"""
Create a ready-to-run PyneCore project: the workdir layout, a Python environment with PyneCore,
the demo script and demo data. No agent instruction files, no editor files; the coding knowledge
lives in this skill.

Standard library only (it drives `uv` or `python -m venv` + `pip` as subprocesses).

    python3 init_project.py DIR                       # create DIR (or fill an existing folder)
    python3 init_project.py DIR --python 3.13         # pin the interpreter version (uv)
    python3 init_project.py DIR --no-ccxt             # without the CCXT exchange-data extra
    python3 init_project.py DIR --template minimal:ema_cross   # also add scripts/ema_cross.py from a template
    python3 init_project.py DIR --no-env --no-demo    # folders and config files only
    python3 init_project.py DIR --smoke               # run the demo once at the end to prove the setup

What is created inside DIR (existing files are never overwritten):

    .pyne                  workdir marker (workdir_version = "1.0")
    scripts/  scripts/lib/ Pyne scripts and libraries; demo.py after the demo step
    data/                  demo.ohlcv + demo.toml after the demo step
    config/                .gitignore (*), api.toml, plugins/<plugin>.toml after the demo step
    output/  output/logs/  run outputs
    cache/
    pyproject.toml         dependency on pynesys-pynecore (uv project)      [--no-env skips]
    .venv/  uv.lock        the environment (uv), or .venv via venv + pip   [--no-env skips]
    pyrightconfig.json     type-checker relaxations for Series             [--no-pyright skips]
    .gitignore             output/, cache/, data/*.ohlcv, .venv/, ...

Afterwards: `<DIR>/.venv/bin/pyne run demo demo` runs the demo; `pyne data download ...` fetches
real candles; new scripts go into scripts/ (see new_script.py).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MARKER_CONTENT = (
    "# PyneCore workdir marker. The version refers to the workdir structure, not your project.\n"
    'workdir_version = "1.0"\n'
)
CONFIG_GITIGNORE = "*\n!.gitignore\n"
API_TOML = '[api]\napi_key = ""\ntimeout = 30\n'
ROOT_GITIGNORE = """# Python
.venv/
venv/
__pycache__/
*.pyc

# PyneCore run artefacts
output/
cache/
*_profile.json

# market data: large binaries; keep data/*.toml (symbol info)
data/*.ohlcv
data/*.extra.csv

# config/ has its own .gitignore (keys live there)
"""
PYRIGHTCONFIG = """{
  "venvPath": ".",
  "venv": ".venv",
  "extraPaths": ["scripts/lib"],
  "typeCheckingMode": "basic",
  "reportIndexIssue": "none",
  "reportAssignmentType": "none",
  "reportRedeclaration": "none",
  "reportArgumentType": "none",
  "exclude": ["data", "output", "cache", "**/__pycache__"],
  "defineConstant": { "TYPECHECKER": "pyright" }
}
"""
TEMPLATE_KINDS = {
    "strategy": "strategy_template.py",
    "indicator": "indicator_template.py",
    "minimal": "minimal_cli.py",
    "minimal-indicator": "minimal_cli_indicator.py",
}
DIRS = ("scripts", "scripts/lib", "data", "config", "config/plugins", "output", "output/logs", "cache")


def log(msg: str) -> None:
    print(f"[init] {msg}")


def create_layout(root: Path) -> list[str]:
    created: list[str] = []
    for d in DIRS:
        path = root / d
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(d + "/")
    for rel, content in ((".pyne", MARKER_CONTENT), ("config/.gitignore", CONFIG_GITIGNORE), ("config/api.toml", API_TOML)):
        path = root / rel
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(rel)
    return created


def project_name(root: Path, explicit: str | None) -> str:
    name = explicit or root.resolve().name
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-").lower() or "pynecore-project"
    return name


def pyproject_text(name: str, spec: str, ccxt: bool) -> str:
    extras = "cli,ccxt" if ccxt else "cli"
    return (
        "[project]\n"
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        'description = "PyneCore trading strategies and indicators"\n'
        'requires-python = ">=3.11"\n'
        "dependencies = [\n"
        f'    "pynesys-pynecore[{extras}]{spec}",\n'
        "]\n"
        "\n"
        "[tool.uv]\n"
        "package = false\n"
    )


def venv_python(root: Path) -> Path | None:
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        if (root / rel).exists():
            return root / rel
    return None


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True, env=env)


def setup_env(root: Path, name: str, spec: str, ccxt: bool, python_version: str | None, use: str, created: list[str]) -> Path:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        pyproject.write_text(pyproject_text(name, spec, ccxt), encoding="utf-8")
        created.append("pyproject.toml")
    else:
        log("pyproject.toml exists, left unchanged")

    uv = shutil.which("uv")
    if use == "uv" and not uv:
        raise SystemExit("uv requested but not found on PATH (install: https://docs.astral.sh/uv/)")
    if uv and use != "pip":
        cmd = [uv, "sync"]
        if python_version:
            cmd += ["--python", python_version]
        run(cmd, root)
    else:
        if python_version:
            log(f"note: --python {python_version} is applied by uv only; using {sys.executable}")
        if not venv_python(root):
            run([sys.executable, "-m", "venv", ".venv"], root)
            created.append(".venv/")
        py = venv_python(root)
        if py is None:
            raise SystemExit("virtual environment was not created")
        extras = "cli,ccxt" if ccxt else "cli"
        run([str(py), "-m", "pip", "install", "--upgrade", "pip"], root)
        run([str(py), "-m", "pip", "install", f"pynesys-pynecore[{extras}]{spec}"], root)
    py = venv_python(root)
    if py is None:
        raise SystemExit("no .venv/bin/python after the environment step")
    return py


def pyne_path(py: Path) -> Path:
    for name in ("pyne", "pyne.exe"):
        candidate = py.parent / name
        if candidate.exists():
            return candidate
    raise SystemExit(f"pyne CLI not found next to {py}; is the [cli] extra installed?")


def create_demo(root: Path, pyne: Path) -> None:
    env = dict(os.environ, PYNE_NO_LOGO="1", NO_COLOR="1")
    run([str(pyne), "--workdir", str(root), "--recreate-demo", "plugin", "list"], root, env)


def add_template(root: Path, spec: str, created: list[str]) -> None:
    kind, _, name = spec.partition(":")
    if kind not in TEMPLATE_KINDS:
        raise SystemExit(f"unknown template kind {kind!r}; choose from {', '.join(TEMPLATE_KINDS)}")
    name = name or kind.replace("-", "_")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise SystemExit(f"template file name must be snake_case: {name!r}")
    src = Path(__file__).resolve().parent.parent / "templates" / TEMPLATE_KINDS[kind]
    dest = root / "scripts" / f"{name}.py"
    if dest.exists():
        log(f"{dest.relative_to(root)} exists, left unchanged")
        return
    shutil.copy2(src, dest)
    created.append(f"scripts/{name}.py")


def smoke_test(root: Path, pyne: Path) -> None:
    env = dict(os.environ, PYNE_NO_LOGO="1", NO_COLOR="1")
    run([str(pyne), "--workdir", str(root), "run", "demo", "demo"], root, env)
    out = root / "output" / "demo.csv"
    if not out.is_file():
        raise SystemExit("smoke test failed: output/demo.csv was not written")
    lines = sum(1 for _ in out.open(encoding="utf-8")) - 1
    log(f"smoke test OK: output/demo.csv has {lines} bars")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("dir", metavar="DIR",
                        help="project folder to create or fill; relative or absolute path. Created if missing;\n"
                             "existing files inside are never overwritten. Example: ~/trading/my-strategies")
    parser.add_argument("--name", metavar="NAME",
                        help="project name written to pyproject.toml (letters, digits, - _ .).\n"
                             "Default: the folder name, lower-cased")
    parser.add_argument("--python", dest="python_version", metavar="X.Y",
                        help="Python version for the environment, e.g. 3.13 (passed to `uv sync --python`;\n"
                             "uv downloads it when needed). Default: uv's default interpreter")
    parser.add_argument("--pynecore", default=">=6.12", metavar="SPEC",
                        help="version specifier appended to pynesys-pynecore in pyproject.toml / pip, e.g. \">=6.12\",\n"
                             "\"==6.12.0\". Default: \">=6.12\"")
    parser.add_argument("--no-ccxt", action="store_true",
                        help="install only the [cli] extra, without [ccxt] (no exchange data download)")
    parser.add_argument("--env", choices=("auto", "uv", "pip"), default="auto", metavar="auto|uv|pip",
                        help="how to build .venv: uv (pyproject.toml + `uv sync`), pip (`python -m venv` + pip install),\n"
                             "auto = uv when found on PATH, else pip. Default: auto")
    parser.add_argument("--no-env", action="store_true",
                        help="skip the environment step (no pyproject.toml, no .venv); also skips demo and smoke test")
    parser.add_argument("--no-demo", action="store_true",
                        help="skip `pyne --recreate-demo`, which writes scripts/demo.py, data/demo.ohlcv + .toml\n"
                             "and config/plugins/<plugin>.toml")
    parser.add_argument("--no-pyright", action="store_true",
                        help="do not write pyrightconfig.json")
    parser.add_argument("--template", action="append", metavar="KIND[:NAME]",
                        help="copy a skill template into scripts/NAME.py. KIND = strategy | indicator | minimal |\n"
                             "minimal-indicator; NAME is snake_case, default = KIND with - replaced by _.\n"
                             "Repeatable. Example: --template minimal:ema_cross")
    parser.add_argument("--smoke", action="store_true",
                        help="after setup, run `pyne run demo demo` and check that output/demo.csv was written")
    args = parser.parse_args()

    root = Path(args.dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    log(f"project folder: {root}")

    created = create_layout(root)
    if not args.no_pyright and not (root / "pyrightconfig.json").exists():
        (root / "pyrightconfig.json").write_text(PYRIGHTCONFIG, encoding="utf-8")
        created.append("pyrightconfig.json")
    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text(ROOT_GITIGNORE, encoding="utf-8")
        created.append(".gitignore")

    pyne: Path | None = None
    if not args.no_env:
        py = setup_env(root, project_name(root, args.name), args.pynecore, not args.no_ccxt, args.python_version, args.env, created)
        pyne = pyne_path(py)
        version = subprocess.run([str(py), "-c", "import importlib.metadata as m; print(m.version('pynesys-pynecore'))"],
                                 capture_output=True, text=True).stdout.strip()
        log(f"environment ready: {py} (pynesys-pynecore {version})")
        if not args.no_demo:
            create_demo(root, pyne)
    for spec in args.template or []:
        add_template(root, spec, created)
    if args.smoke:
        if pyne is None:
            log("smoke test skipped: no environment (--no-env)")
        else:
            smoke_test(root, pyne)

    print()
    print(f"Project: {root}")
    print("Created: " + (", ".join(created) if created else "nothing new (everything existed)"))
    if pyne:
        print("Next   :")
        print(f"  PYNE_NO_LOGO=1 {pyne} --workdir {root} run demo demo")
        print(f"  {pyne} --workdir {root} data download ccxt:BYBIT:BTC/USDT:USDT@1D")
        print("  add scripts with new_script.py, backtest with run_backtest.py, report with summarize_backtest.py")
    else:
        print("Next   : create an environment (uv sync, or python -m venv .venv && pip install \"pynesys-pynecore[cli,ccxt]\")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
