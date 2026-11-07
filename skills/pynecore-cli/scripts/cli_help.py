#!/usr/bin/env python3
r"""
Inspect the `pyne` CLI installed in a project: locate the executable, print its version and
command tree with every option, dump all --help pages, or check that references/commands.md
still describes the same commands and options.

Standard library only. Reads the project's virtual environment (to find `pyne`) and runs
`pyne ... --help` with a throw-away workdir, so nothing in the project is touched.

    python3 cli_help.py                       # which pyne, PyneCore version, commands and their options
    python3 cli_help.py --root /path/to/project
    python3 cli_help.py --pyne /path/to/.venv/bin/pyne
    python3 cli_help.py --dump                # the full --help text of pyne and of every (sub)command
    python3 cli_help.py --command "data download"   # one command's help text
    python3 cli_help.py --check               # compare with references/commands.md; exit 1 on drift
    python3 cli_help.py --json                # the command tree as JSON

Exit codes: 0 ok, 1 pyne not found / help failed / drift found (--check), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VENV_CANDIDATES = (".venv", "venv", "env")
BIN_DIRS = ("bin", "Scripts")
REFERENCE = Path(__file__).resolve().parent.parent / "references" / "commands.md"
BOX_CHARS = "│╭╮╰╯─┃┏┓┗┛━"
PANEL_TITLE_RE = re.compile(r"^\s*╭─+\s*(.+?)\s*─+╮?\s*$")
PANEL_END_RE = re.compile(r"^\s*╰─*╯?\s*$")
OPTION_RE = re.compile(r"^\s*\*?\s*(--[A-Za-z][\w-]*)(?:\s+(-[A-Za-z][\w-]*))?")
ARGUMENT_RE = re.compile(r"^\s*\*?\s*([a-z][\w-]*)\s+<[^>]+>")
COMMAND_RE = re.compile(r"^\s*([a-z][\w-]*)\s{2,}\S")
MD_OPTION_RE = re.compile(r"(?<![\w-])(--[a-z][\w-]*)")


# ----------------------------------------------------------------------------- locate pyne

def find_pyne(root: Path, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit if explicit.is_file() else None
    candidates: list[Path] = []
    for name in VENV_CANDIDATES:  # the project's own environment first
        for bin_dir in BIN_DIRS:
            candidates += [root / name / bin_dir / "pyne", root / name / bin_dir / "pyne.exe"]
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        for bin_dir in BIN_DIRS:
            candidates += [Path(active) / bin_dir / "pyne", Path(active) / bin_dir / "pyne.exe"]
    for c in candidates:
        if c.is_file():
            return c
    on_path = shutil.which("pyne")
    return Path(on_path) if on_path else None


def pynecore_version(pyne: Path) -> str | None:
    """Version of pynesys-pynecore in the environment that owns `pyne` (its interpreter sits next to it)."""
    for name in ("python", "python3", "python.exe"):
        py = pyne.parent / name
        if py.is_file():
            try:
                out = subprocess.run([str(py), "-c", "import importlib.metadata as m; print(m.version('pynesys-pynecore'))"],
                                     capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                return None
            return out.stdout.strip() if out.returncode == 0 else None
    return None


# ----------------------------------------------------------------------------- run --help

class Cli:
    def __init__(self, pyne: Path):
        self.pyne = pyne
        self.tmp = Path(tempfile.mkdtemp(prefix="pyne-cli-help-"))  # pyne completes the structure of an EXISTING workdir
        self.env = {**os.environ, "PYNE_NO_LOGO": "1", "NO_COLOR": "1", "COLUMNS": "180", "TERM": "dumb",
                    "PYNE_WORK_DIR": str(self.tmp)}

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plugin_groups(self) -> set[str]:
        """Command groups that installed CLI plugins add to pyne (`pyne plugin list --json`, capability cli).
        They are not PyneCore's commands, so the reference check leaves them out."""
        out = subprocess.run([str(self.pyne), "plugin", "list", "--json"], capture_output=True, text=True, timeout=180,
                             env=self.env, stdin=subprocess.DEVNULL)
        try:
            data = json.loads(out.stdout[out.stdout.index("{"):])
        except ValueError:
            raise RuntimeError(f"pyne plugin list --json gave no JSON ({out.returncode}): {out.stderr.strip()[:300]}")
        return {p["name"] for p in data.get("plugins", []) if "cli" in (p.get("capabilities") or [])}

    def help(self, path: list[str]) -> str:
        cmd = [str(self.pyne), *path, "--help"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=self.env)
        if out.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed ({out.returncode}): {out.stderr.strip()[:300]}")
        return out.stdout


def strip_boxes(text: str) -> str:
    return "\n".join(line.translate({ord(c): " " for c in BOX_CHARS}).rstrip() for line in text.splitlines())


def parse_help(text: str) -> dict:
    """Sections of a help page: options (long -> short), arguments, sub-commands."""
    options: dict[str, str | None] = {}
    arguments: list[str] = []
    commands: dict[str, str] = {}
    panel = None
    for raw in text.splitlines():
        m = PANEL_TITLE_RE.match(raw)
        if m:
            panel = m.group(1).strip().lower()
            continue
        if PANEL_END_RE.match(raw):
            panel = None
            continue
        plain = raw.translate({ord(c): " " for c in BOX_CHARS})
        if panel is None:  # plain click layout without rich: "Options:" / "Commands:" headers
            low = plain.strip().lower()
            if low in ("options:", "commands:", "arguments:"):
                panel = low[:-1]
            continue
        if "option" in panel:
            m = OPTION_RE.match(plain)
            if m and m.group(1) != "--help":
                options[m.group(1)] = m.group(2)
        elif "argument" in panel:
            m = ARGUMENT_RE.match(plain)
            if m:
                arguments.append(m.group(1))
        elif "command" in panel:
            m = COMMAND_RE.match(plain)
            if m:
                commands[m.group(1)] = plain.strip()[len(m.group(1)):].strip()
    return {"options": options, "arguments": arguments, "commands": commands}


def build_tree(cli: Cli, path: list[str] | None = None) -> dict:
    path = path or []
    text = cli.help(path)
    parsed = parse_help(text)
    node = {"path": " ".join(path), "help": text, "options": parsed["options"], "arguments": parsed["arguments"],
            "children": {}}
    for name in parsed["commands"]:
        node["children"][name] = build_tree(cli, path + [name])
    return node


def walk(node: dict):
    yield node
    for child in node["children"].values():
        yield from walk(child)


# ----------------------------------------------------------------------------- reference check

def parse_reference(md_path: Path) -> dict[str, set[str]]:
    """Command path -> option names mentioned in its section ('' for the root pyne section)."""
    sections: dict[str, set[str]] = {}
    current: str | None = None
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            title = line[3:].strip().replace("`", "")
            m = re.match(r"^pyne\b\s*([a-z][\w -]*)?", title)
            if m:
                current = (m.group(1) or "").split("(")[0].strip()
                sections.setdefault(current, set())
            else:
                current = None
            continue
        if current is not None:
            sections[current].update(o for o in MD_OPTION_RE.findall(line) if o != "--help")
    return sections


def check(tree: dict, md_path: Path, plugin_groups: set[str]) -> int:
    if not md_path.is_file():
        print(f"reference not found: {md_path}", file=sys.stderr)
        return 1
    ref = parse_reference(md_path)
    problems = 0
    skipped = sorted(g for g in plugin_groups if g in tree["children"])
    for node in walk(tree):
        path = node["path"]
        if path.split(" ", 1)[0] in plugin_groups:     # added by an installed CLI plugin, not part of PyneCore
            continue
        if node["children"] and path:  # a command group (data, plugin, ...) has no section of its own
            continue
        if path not in ref:
            print(f"UNDOCUMENTED COMMAND  pyne {path}".rstrip())
            problems += 1
            continue
        live = set(node["options"])
        documented = ref[path]
        for opt in sorted(live - documented):
            print(f"UNDOCUMENTED OPTION   pyne {path} {opt}".replace("pyne  ", "pyne "))
            problems += 1
        for opt in sorted(documented - live):
            print(f"stale option (warning) pyne {path} {opt}: not in the installed CLI".replace("pyne  ", "pyne "))
    live_paths = {n["path"] for n in walk(tree)}
    for path in sorted(ref):
        if path and path not in live_paths:
            print(f"stale section (warning) pyne {path}: not in the installed CLI")
    if skipped:
        print(f"skipped plugin command group(s): {', '.join('pyne ' + g for g in skipped)} (installed CLI plugins)")
    if problems:
        print(f"{problems} difference(s): update references/commands.md", file=sys.stderr)
        return 1
    count = sum(1 for n in walk(tree)
                if (not n["children"] or not n["path"]) and n["path"].split(" ", 1)[0] not in plugin_groups)
    print(f"OK: {count} documented command(s) match the installed CLI")
    return 0


# ----------------------------------------------------------------------------- output

def print_tree(tree: dict, version: str | None, pyne: Path, plugin_groups: set[str] = frozenset()) -> None:
    print(f"pyne     : {pyne}")
    print(f"PyneCore : {version or 'unknown (no interpreter next to pyne)'}")
    print(f"global   : {', '.join(sorted(tree['options'])) or '-'}")
    for node in walk(tree):
        if not node["path"]:
            continue
        if node["children"]:
            note = "  (added by a CLI plugin)" if node["path"] in plugin_groups else ""
            print(f"{node['path']:<18} group: {', '.join(node['children'])}{note}")
            continue
        opts = ", ".join(f"{k}{'/' + v if v else ''}" for k, v in sorted(node["options"].items()))
        args = " ".join(a.upper() for a in node["arguments"])
        print(f"{node['path']:<18} {args:<22} {opts}")


def to_json(tree: dict) -> dict:
    return {"path": tree["path"], "options": tree["options"], "arguments": tree["arguments"],
            "children": {k: to_json(v) for k, v in tree["children"].items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--root", default=".", metavar="DIR",
                        help="project folder whose environment holds pyne: .venv, venv or env (bin/ or Scripts/), then\n"
                             "$VIRTUAL_ENV, then pyne on PATH. Default: the current directory")
    parser.add_argument("--pyne", type=Path, metavar="PATH", help="use this pyne executable instead of searching")
    parser.add_argument("--dump", action="store_true", help="print the full --help text of pyne and of every command and subcommand")
    parser.add_argument("--command", metavar="PATH",
                        help="print one command's help text; PATH is space-separated, e.g. run, \"data download\", \"plugin list\"")
    parser.add_argument("--check", action="store_true",
                        help=f"compare the installed commands and options with {REFERENCE.name} of this skill:\n"
                             "an option or command missing from the reference exits 1 (lines starting with UNDOCUMENTED);\n"
                             "options the reference lists but the CLI lacks are warnings. Command groups added by installed\n"
                             "CLI plugins (pyne plugin list --json, capability cli) are skipped and named")
    parser.add_argument("--reference", type=Path, default=REFERENCE, metavar="MD",
                        help="the Markdown file for --check (sections '## `pyne <command>`' holding `--option` names).\n"
                             "Default: references/commands.md of this skill")
    parser.add_argument("--json", action="store_true", help="print the command tree (options, arguments, children) as JSON")
    args = parser.parse_args()

    pyne = find_pyne(Path(args.root), args.pyne)
    if pyne is None:
        print("pyne not found: no .venv/venv/env with pynesys-pynecore[cli] under --root, no $VIRTUAL_ENV, nothing on PATH",
              file=sys.stderr)
        return 1
    cli = Cli(pyne)
    try:
        if args.command:
            print(strip_boxes(cli.help(args.command.split())))
            return 0
        tree = build_tree(cli)
        if args.dump:
            for node in walk(tree):
                print(f"{'=' * 20} pyne {node['path']} --help".replace("pyne  ", "pyne "))
                print(strip_boxes(node["help"]))
            return 0
        if args.json:
            print(json.dumps({"pyne": str(pyne), "pynecore": pynecore_version(pyne), **to_json(tree)}, indent=2))
            return 0
        if args.check:
            return check(tree, args.reference, cli.plugin_groups())
        print_tree(tree, pynecore_version(pyne), pyne, cli.plugin_groups())
        return 0
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        cli.close()


if __name__ == "__main__":
    sys.exit(main())
