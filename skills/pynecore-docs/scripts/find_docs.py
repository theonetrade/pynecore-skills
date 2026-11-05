#!/usr/bin/env python3
"""
Work with the PyneCore documentation bundled in this skill (the `docs/` folder next to `scripts/`).

Standard library only. Nothing outside this repository is read, except the target project's
virtual environment when reporting the installed PyneCore version.

    python3 find_docs.py                       # where the docs are, documented vs installed version
    python3 find_docs.py --list                # every page with its title
    python3 find_docs.py --grep "lookahead"    # search all pages (case-insensitive regex)
    python3 find_docs.py --grep "exit\\(" --context 2
    python3 find_docs.py --page cli/run.md     # absolute path of one page
    python3 find_docs.py --page cli/run.md --print
    python3 find_docs.py --online cli/run.md   # URL of the online copy of a page
    python3 find_docs.py --json

    --docs DIR   use another docs folder (for example a PyneCore checkout's docs/)
    --root DIR   project whose interpreter is probed for the installed version (default: cwd)
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

ONLINE_BASE = "https://pynecore.org/docs/"
VENV_CANDIDATES = (".venv", "venv", "env")
BUNDLED_DOCS = Path(__file__).resolve().parent.parent / "docs"
MANIFEST_NAME = "MANIFEST.json"


def read_manifest(docs: Path) -> dict:
    path = docs / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def installed_runtime_version(root: Path) -> dict | None:
    code = "import importlib.metadata as m; print(m.version('pynesys-pynecore'))"
    candidates: list[str] = []
    for name in VENV_CANDIDATES:                      # the project's own environment first
        candidates.append(str(root / name / "bin" / "python"))
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        candidates.append(str(Path(active) / "bin" / "python"))
    for exe in ("python3", "python"):
        w = shutil.which(exe)
        if w:
            candidates.append(w)
    for py in candidates:
        if not Path(py).exists():
            continue
        try:
            out = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0:
            return {"python": py, "version": out.stdout.strip()}
    return None


def page_title(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:1500]
    except OSError:
        return ""
    m = re.search(r'^title:\s*"?(.*?)"?\s*$', head, re.M)
    if m:
        return m.group(1)
    m = re.search(r"^#\s+(.*)$", head, re.M)
    return m.group(1) if m else ""


def list_pages(docs: Path) -> list[tuple[str, str]]:
    return [(str(p.relative_to(docs)), page_title(p)) for p in sorted(docs.rglob("*.md"))]


def grep(docs: Path, pattern: str, context: int) -> int:
    rx = re.compile(pattern, re.I)
    hits = 0
    for path in sorted(docs.rglob("*.md")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if rx.search(line):
                hits += 1
                rel = path.relative_to(docs)
                if context:
                    lo, hi = max(0, i - context), min(len(lines), i + context + 1)
                    print(f"--- {rel}:{i + 1}")
                    for j in range(lo, hi):
                        print(f"{'>' if j == i else ' '} {lines[j]}")
                else:
                    print(f"{rel}:{i + 1}: {line.strip()[:200]}")
    return hits


def online_url(rel: str) -> str:
    rel = rel.replace("\\", "/")
    if rel.endswith("README.md"):
        rel = rel[: -len("README.md")]
    elif rel.endswith(".md"):
        rel = rel[:-3] + "/"
    return ONLINE_BASE + rel.lstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--docs", type=Path, default=BUNDLED_DOCS, metavar="DIR",
                        help="documentation folder to use: a folder holding README.md and the section folders\n"
                             "(reference/, cli/, ...), for example a PyneCore checkout's docs/.\n"
                             "Default: the snapshot bundled in this skill (<skill>/docs)")
    parser.add_argument("--root", default=".", metavar="DIR",
                        help="project folder whose virtual environment (.venv, venv, env; then $VIRTUAL_ENV, then PATH\n"
                             "python) is asked for the installed pynesys-pynecore version, shown next to the documented\n"
                             "one. Default: the current directory")
    parser.add_argument("--list", action="store_true",
                        help="print every page as REL_PATH followed by its title, one per line")
    parser.add_argument("--grep", metavar="REGEX",
                        help="Python regular expression matched case-insensitively against every line of every page;\n"
                             "escape regex characters (strategy\\.exit\\( ). Prints REL_PATH:LINE: text per hit;\n"
                             "exit code 1 when nothing matches. Example: --grep \"lookahead_(on|off)\"")
    parser.add_argument("--context", type=int, default=0, metavar="N",
                        help="with --grep: also print N lines before and after each hit (integer >= 0). Default: 0")
    parser.add_argument("--page", metavar="REL_PATH",
                        help="one page given relative to the docs folder, e.g. reference/lib/ta.md or cli/run\n"
                             "(the .md suffix may be omitted). Prints its absolute path; exit code 1 if absent.")
    parser.add_argument("--print", action="store_true",
                        help="with --page: print the page's Markdown content instead of its path")
    parser.add_argument("--online", metavar="REL_PATH",
                        help="print the URL of the online copy of a page given as for --page:\n"
                             "section/page.md -> https://pynecore.org/docs/section/page/ (README.md -> the section URL)")
    parser.add_argument("--json", action="store_true",
                        help="print the summary as one JSON object (docs, documented_version, source_ref,\n"
                             "snapshot_date, pages, runtime, online)")
    args = parser.parse_args()

    if args.online:
        print(online_url(args.online))
        return 0

    docs = args.docs.resolve()
    if not (docs / "README.md").is_file():
        print(f"documentation folder not found: {docs}", file=sys.stderr)
        print("restore it with: python3 sync_docs.py --tag v<pynecore version>   (or --from <docs folder>)", file=sys.stderr)
        print(f"online copy: {ONLINE_BASE}", file=sys.stderr)
        return 1
    manifest = read_manifest(docs)

    if args.page:
        path = docs / args.page
        if not path.is_file() and not args.page.endswith(".md"):
            path = docs / (args.page + ".md")
        if not path.is_file():
            print(f"page not found: {args.page}  (try --list or --grep)", file=sys.stderr)
            return 1
        print(path.read_text(encoding="utf-8", errors="replace") if args.print else path)
        return 0

    if args.grep:
        hits = grep(docs, args.grep, args.context)
        print(f"{hits} hit(s) in {docs}", file=sys.stderr)
        return 0 if hits else 1

    if args.list:
        for rel, title in list_pages(docs):
            print(f"{rel:<48} {title}")
        return 0

    runtime = installed_runtime_version(Path(args.root))
    pages = sum(1 for _ in docs.rglob("*.md"))
    if args.json:
        print(json.dumps({"docs": str(docs), "documented_version": manifest.get("pynecoreVersion"),
                          "source_ref": manifest.get("sourceRef"), "snapshot_date": manifest.get("snapshotDate"),
                          "pages": pages, "runtime": runtime, "online": ONLINE_BASE}, indent=2))
        return 0

    version = manifest.get("pynecoreVersion") or "unknown"
    print(f"Docs folder      : {docs}")
    print(f"Documented ver.  : PyneCore {version} ({manifest.get('sourceRef') or '-'}; {pages} pages; snapshot {manifest.get('snapshotDate') or '-'})")
    if runtime:
        same = runtime["version"] == version
        print(f"Installed runtime: pynesys-pynecore {runtime['version']} via {runtime['python']}"
              + ("" if same else "   <- differs from the documented version; verify changed APIs against the package"))
    else:
        print("Installed runtime: no interpreter with pynecore found in this project")
    print(f"Online mirror    : {ONLINE_BASE}  (section/page.md -> section/page/)")
    print("Refresh snapshot : python3 sync_docs.py --tag v<version>")
    print("Start pages      : reference/script-format.md, strategy.md, reference/lib/strategy.md, reference/lib/ta.md, cli/run.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
