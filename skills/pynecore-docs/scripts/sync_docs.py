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

The new snapshot is built in a temporary folder and replaces the target only when complete, so
removed pages do not linger and a failed download leaves the old snapshot intact. A target that
is not empty and holds no MANIFEST.json is refused, as is a --from folder equal to or nested with
the target. MANIFEST.json records the version, source reference, date and a SHA-256 per file.
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
import urllib.error
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


def check_dest(dest: Path, source: Path | None) -> None:
    """Refuse a target that is not ours to replace, before anything is downloaded or deleted."""
    if dest.exists() and not dest.is_dir():
        raise SystemExit(f"--dest {dest}: exists and is not a folder")
    if dest.is_dir() and any(dest.iterdir()) and not (dest / MANIFEST_NAME).is_file():
        raise SystemExit(f"--dest {dest}: the folder is not empty and holds no {MANIFEST_NAME}, so it is not a "
                         f"documentation snapshot; refusing to replace it (choose a new or empty folder)")
    if source is not None and (source == dest or dest in source.parents or source in dest.parents):
        raise SystemExit(f"--from {source} and --dest {dest} are the same folder or one contains the other; "
                         f"copy from another folder, or write the snapshot elsewhere with --dest")


def replace_dest(stage: Path, dest: Path) -> None:
    """Swap the finished snapshot in: the old one is removed only after the new one is complete."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage, dest)


def copy_tree(source: Path, dest: Path, exclude: tuple[str, ...]) -> dict[str, str]:
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--tag", metavar="TAG",
                              help="git tag of a PyneCore release, with the leading v, e.g. v6.12.0. Downloads\n"
                                   "https://github.com/PyneSys/pynecore/archive/refs/tags/TAG.tar.gz (network needed)\n"
                                   "and takes docs/, LICENSE and NOTICE from it.")
    source_group.add_argument("--from", dest="from_dir", metavar="DIR",
                              help="local folder holding the documentation pages (README.md plus the section folders),\n"
                                   "for example a PyneCore checkout's docs/. LICENSE and NOTICE are taken from DIR or its\n"
                                   "parent when present; a manifest.json there supplies the version.")
    source_group.add_argument("--check", action="store_true",
                              help="verify the bundled snapshot: every file in docs/MANIFEST.json must exist with the\n"
                                   "recorded SHA-256, and no extra files may be present. Exit code 1 on any problem.")
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="PyneCore version recorded as pynecoreVersion in MANIFEST.json, e.g. 6.12.0.\n"
                             "Default: TAG without the leading v, else the source's manifest.json, else \"unknown\"")
    parser.add_argument("--commit", metavar="SHA",
                        help="git commit hash recorded as sourceCommit in MANIFEST.json.\n"
                             "Default: from the source's manifest.json when present, else null")
    parser.add_argument("--license", dest="license_file", metavar="PATH",
                        help="path of the LICENSE file to copy into the snapshot.\n"
                             "Default: the release's LICENSE, or LICENSE found in DIR or its parent")
    parser.add_argument("--notice", dest="notice_file", metavar="PATH",
                        help="path of the NOTICE file to copy into the snapshot.\n"
                             "Default: the release's NOTICE, or NOTICE found in DIR or its parent")
    parser.add_argument("--keep-all", action="store_true",
                        help="also copy the pages excluded by default (getting-started/converting-from-pine.md,\n"
                             "cli/compile.md, logo.svg): they cover a cloud compiler for scripts in another language")
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parent.parent / "docs", metavar="DIR",
                        help="target folder. It is replaced only when it is new, empty or an earlier snapshot (holds\n"
                             "MANIFEST.json), and only after the new snapshot is complete; it must not be the --from folder\n"
                             "or inside it. Point it elsewhere to preview a snapshot. Default: <skill>/docs")
    args = parser.parse_args()

    if args.check or not (args.tag or args.from_dir):
        if not args.check:
            parser.print_usage()
            print("one of --tag, --from or --check is required", file=sys.stderr)
            return 2
        return check(args.dest)

    exclude = () if args.keep_all else DEFAULT_EXCLUDE
    dest = args.dest.expanduser().resolve()
    check_dest(dest, Path(args.from_dir).expanduser().resolve() if args.from_dir else None)
    with tempfile.TemporaryDirectory(prefix="pynecore-docs-") as tmp:
        tmp_path = Path(tmp)
        release_dir = tmp_path / "release"
        release_dir.mkdir()
        stage = tmp_path / "snapshot"
        license_file = Path(args.license_file) if args.license_file else None
        notice_file = Path(args.notice_file) if args.notice_file else None
        ref = commit = None
        if args.tag:
            try:
                source, dl_license, dl_notice = download_release(args.tag, release_dir)
            except (urllib.error.URLError, TimeoutError, OSError, tarfile.TarError) as e:
                raise SystemExit(f"download of the {args.tag} release failed: {e}\n"
                                 f"the snapshot in {dest} is unchanged. Check the tag (git ls-remote --tags "
                                 f"https://github.com/PyneSys/pynecore) or copy from a local folder with --from")
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

        files = copy_tree(source, stage, exclude)
        pages = sum(1 for f in files if f.endswith(".md"))
        if pages == 0:
            raise SystemExit(f"no Markdown pages found in {source}; the snapshot in {dest} is unchanged")
        for name, path in (("LICENSE", license_file), ("NOTICE", notice_file)):
            if path and path.is_file():
                shutil.copy2(path, stage / name)
                files[name] = sha256(stage / name)
            else:
                print(f"warning: no {name} file found; add one, the documentation is Apache-2.0 licensed", file=sys.stderr)
        excluded = [e for e in exclude if (source / e).exists()]
        write_manifest(stage, version, ref, commit, excluded, files)
        replace_dest(stage, dest)

    print(f"snapshot written to {dest}: {pages} pages, PyneCore {version}" + (f" ({ref})" if ref else ""))
    if excluded:
        print("excluded: " + ", ".join(excluded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
