#!/usr/bin/env python3
"""
Refresh the PyneCore documentation snapshot bundled in this skill (the `docs/` folder).

Standard library only. The snapshot is what `find_docs.py` and the agent read; nothing outside
this repository is consulted at run time.

    python3 sync_docs.py --tag v6.12.0                   # download the PyneCore release from GitHub (needs network)
    python3 sync_docs.py --from /path/to/pynecore/docs   # copy from a local checkout or any docs folder
    python3 sync_docs.py --check                         # verify docs/ against docs/MANIFEST.json

Options:
    --version X.Y.Z   documented PyneCore version written to MANIFEST.json (default: from the tag,
                      or from a manifest.json next to the source folder, or "unknown")
    --commit SHA      source commit written to MANIFEST.json
    --license PATH, --notice PATH
                      license files to copy next to the pages (default: LICENSE / NOTICE found in
                      the source folder or its parent; the GitHub download carries them)
    --keep-all        also copy the pages excluded by default (they cover converting scripts
                      written in another language through a cloud compiler, not Python work)
    --dest DIR        target folder (default: <skill>/docs)

The script wipes the target folder first so removed pages do not linger, copies every file,
and writes MANIFEST.json with the version, source reference, date and a SHA-256 per file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_EXCLUDE = (
    "getting-started/converting-from-pine.md",
    "cli/compile.md",
    "logo.svg",
)
GITHUB_TARBALL = "https://github.com/PyneSys/pynecore/archive/refs/tags/{tag}.tar.gz"
MANIFEST_NAME = "MANIFEST.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def download_release(tag: str, workdir: Path) -> tuple[Path, Path | None, Path | None]:
    url = GITHUB_TARBALL.format(tag=tag)
    archive = workdir / "pynecore.tar.gz"
    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as resp, archive.open("wb") as out:
        shutil.copyfileobj(resp, out)
    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(workdir, filter="data")
        else:  # pragma: no cover - Python < 3.12
            tar.extractall(workdir)
    roots = [p for p in workdir.iterdir() if p.is_dir()]
    if not roots:
        raise SystemExit("archive did not contain a top-level folder")
    root = roots[0]
    docs = root / "docs"
    if not docs.is_dir():
        raise SystemExit(f"no docs/ folder in {root}")
    license_file = root / "LICENSE" if (root / "LICENSE").is_file() else None
    notice_file = root / "NOTICE" if (root / "NOTICE").is_file() else None
    return docs, license_file, notice_file


def detect_version(source: Path) -> tuple[str | None, str | None, str | None]:
    """Version, ref and commit from a manifest.json next to the source folder, if any."""
    for candidate in (source / "manifest.json", source.parent / "manifest.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return data.get("pynecoreVersion"), data.get("sourceRef"), data.get("sourceCommit")
            except (OSError, ValueError):
                pass
    return None, None, None


def copy_tree(source: Path, dest: Path, exclude: tuple[str, ...]) -> dict[str, str]:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    files: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        if rel in exclude or rel == MANIFEST_NAME:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files[rel] = sha256(target)
    return files


def write_manifest(dest: Path, version: str, ref: str | None, commit: str | None, excluded: list[str], files: dict[str, str]) -> None:
    manifest = {
        "pynecoreVersion": version,
        "sourceRef": ref,
        "sourceCommit": commit,
        "source": "https://github.com/PyneSys/pynecore (docs/), Apache-2.0; see LICENSE and NOTICE in this folder",
        "snapshotDate": dt.date.today().isoformat(),
        "excluded": excluded,
        "files": files,
    }
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def check(dest: Path) -> int:
    manifest_path = dest / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"no {MANIFEST_NAME} in {dest}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected: dict[str, str] = manifest.get("files", {})
    problems = 0
    for rel, digest in expected.items():
        path = dest / rel
        if not path.is_file():
            print(f"missing  {rel}")
            problems += 1
        elif sha256(path) != digest:
            print(f"changed  {rel}")
            problems += 1
    for path in dest.rglob("*"):
        if path.is_file():
            rel = path.relative_to(dest).as_posix()
            if rel not in expected and rel != MANIFEST_NAME:
                print(f"extra    {rel}")
                problems += 1
    print(f"{len(expected)} files in manifest, PyneCore {manifest.get('pynecoreVersion')}, snapshot {manifest.get('snapshotDate')}: "
          + ("OK" if not problems else f"{problems} problem(s)"))
    return 0 if problems == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--tag", help="PyneCore git tag to download, e.g. v6.12.0")
    source_group.add_argument("--from", dest="from_dir", help="local docs folder to copy")
    source_group.add_argument("--check", action="store_true", help="verify the bundled snapshot")
    parser.add_argument("--version")
    parser.add_argument("--commit")
    parser.add_argument("--license", dest="license_file")
    parser.add_argument("--notice", dest="notice_file")
    parser.add_argument("--keep-all", action="store_true")
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parent.parent / "docs")
    args = parser.parse_args()

    if args.check or not (args.tag or args.from_dir):
        if not args.check:
            parser.print_usage()
            print("one of --tag, --from or --check is required", file=sys.stderr)
            return 2
        return check(args.dest)

    exclude = () if args.keep_all else DEFAULT_EXCLUDE
    with tempfile.TemporaryDirectory(prefix="pynecore-docs-") as tmp:
        tmp_path = Path(tmp)
        license_file = Path(args.license_file) if args.license_file else None
        notice_file = Path(args.notice_file) if args.notice_file else None
        ref = commit = None
        if args.tag:
            source, dl_license, dl_notice = download_release(args.tag, tmp_path)
            license_file = license_file or dl_license
            notice_file = notice_file or dl_notice
            version = args.version or args.tag.lstrip("v")
            ref = args.tag
        else:
            source = Path(args.from_dir).resolve()
            if not source.is_dir():
                raise SystemExit(f"not a directory: {source}")
            m_version, m_ref, m_commit = detect_version(source)
            version = args.version or m_version or "unknown"
            ref = m_ref or (f"v{version}" if version != "unknown" else None)
            commit = m_commit
            for name, current in (("LICENSE", license_file), ("NOTICE", notice_file)):
                if current is None:
                    for cand in (source / name, source.parent / name):
                        if cand.is_file():
                            if name == "LICENSE":
                                license_file = cand
                            else:
                                notice_file = cand
                            break
        commit = args.commit or commit

        files = copy_tree(source, args.dest, exclude)
        for name, path in (("LICENSE", license_file), ("NOTICE", notice_file)):
            if path and path.is_file():
                shutil.copy2(path, args.dest / name)
                files[name] = sha256(args.dest / name)
            else:
                print(f"warning: no {name} file found; add one, the documentation is Apache-2.0 licensed", file=sys.stderr)
        excluded = [e for e in exclude if (source / e).exists()]
        write_manifest(args.dest, version, ref, commit, excluded, files)

    pages = sum(1 for f in files if f.endswith(".md"))
    print(f"snapshot written to {args.dest}: {pages} pages, PyneCore {version}" + (f" ({ref})" if ref else ""))
    if excluded:
        print("excluded: " + ", ".join(excluded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
