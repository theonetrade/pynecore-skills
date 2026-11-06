#!/usr/bin/env python3
r"""
Create a new PyneCore plugin package from one of this skill's templates, renamed to your plugin.

Standard library only. Copies `templates/pynecore-<TEMPLATE>/` to DEST and rewrites the
distribution name, the Python package, the entry-point key (= the plugin name used in provider
strings and in config/plugins/<name>.toml) and the class names.

    python3 new_plugin.py fileprovider ./pynecore-myfeed --name myfeed
    python3 new_plugin.py ccxtrest ./pynecore-restkraken --name restkraken
    python3 new_plugin.py telegram ./pynecore-discord --name discord --class DiscordPlugin
    python3 new_plugin.py simbroker ./pynecore-myvenue --name myvenue --class MyVenueBroker
    python3 new_plugin.py telegram ./pynecore-telegram            # a plain copy, same names as the template

Then, in the environment that has pyne:

    pip install -e ./pynecore-myfeed        # or: uv pip install -e ./pynecore-myfeed
    pyne plugin info myfeed                 # loads the class, shows capabilities and config fields
    <python> <skill>/scripts/plugin_check.py myfeed --symbol X --timeframe 60 --bars 50

Templates (see references/):
    fileprovider   LiveProviderPlugin reading .ohlcv / CSV / JSON / TXT / parquet / SQLite files, aggregating
                   to a higher timeframe on demand: history, paced replay, tail
    ccxtrest       LiveProviderPlugin on top of the built-in ccxt plugin, live bars by REST polling
    telegram       CLIPlugin forwarding log lines, alert() calls and broker events to Telegram
    simbroker      BrokerPlugin + CLIPlugin: a simulated netting venue fed by any provider plugin, with an
                   offline conformance suite (broker_lab/suite.py) for python -m pynecore.testing.broker_lab
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# distribution name, import package, entry-point key, main class, config class, display name
TEMPLATES = {
    "fileprovider": {"dist": "pynecore-fileprovider", "pkg": "pynecore_fileprovider", "key": "file",
                     "cls": "FileProvider", "cfg": "FileConfig", "suffix": "Provider"},
    "ccxtrest": {"dist": "pynecore-ccxtrest", "pkg": "pynecore_ccxtrest", "key": "ccxtrest",
                 "cls": "CCXTRestProvider", "cfg": "CCXTRestConfig", "suffix": "Provider"},
    "telegram": {"dist": "pynecore-telegram", "pkg": "pynecore_telegram", "key": "telegram",
                 "cls": "TelegramPlugin", "cfg": "TelegramConfig", "suffix": "Plugin"},
    "simbroker": {"dist": "pynecore-simbroker", "pkg": "pynecore_simbroker", "key": "simbroker",
                  "cls": "SimBroker", "cfg": "SimBrokerConfig", "suffix": "Broker"},
}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TEXT_SUFFIXES = {".py", ".toml", ".md", ".txt", ".cfg", ".ini"}


def camel(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[_\-]+", name) if part)


def rewrite(text: str, old: dict, new: dict) -> str:
    # longest / most specific tokens first so that "pynecore_fileprovider" is not hit by a shorter replacement
    pairs = [
        (old["pkg"], new["pkg"]),
        (old["dist"], new["dist"]),
        (f'{old["key"]} = "', f'{new["key"]} = "'),                         # entry-point line in pyproject.toml
        (f'plugins/{old["key"]}.toml', f'plugins/{new["key"]}.toml'),       # config path in docstrings and READMEs
        (f'{old["key"]}:', f'{new["key"]}:'),                               # provider strings in examples
        (f'pyne {old["key"]} ', f'pyne {new["key"]} '),                     # CLI group in examples
        (f'pyne plugin info {old["key"]}', f'pyne plugin info {new["key"]}'),
        (f'PLUGIN_NAME = "{old["key"]}"', f'PLUGIN_NAME = "{new["key"]}"'),   # module constant the code derives file names from
        (f'{old["key"]}_<', f'{new["key"]}_<'),                               # file-name patterns in docs: file_<SYMBOL>_<TF>.ohlcv
        (f'{old["key"]}-<', f'{new["key"]}-<'),                               # identity patterns in docs: simbroker-<account>
        (old["cfg"], new["cfg"]),
        (old["cls"], new["cls"]),
    ]
    for a, b in pairs:
        if a != b:
            text = text.replace(a, b)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("template", metavar="TEMPLATE",
                        help="one of: " + ", ".join(TEMPLATES) + "  (a folder under this skill's templates/),\n"
                             "or a path to any plugin template folder that holds a pyproject.toml")
    parser.add_argument("dest", type=Path, metavar="DEST",
                        help="new folder for the plugin package (must not exist unless --force); e.g. ./pynecore-myfeed")
    parser.add_argument("--name", metavar="NAME",
                        help="plugin name: lowercase letters, digits and _ (e.g. myfeed). It becomes the entry-point key\n"
                             "(provider string myfeed:SYMBOL@TF, config/plugins/myfeed.toml), the distribution\n"
                             "pynecore-NAME and the package pynecore_NAME. Default: the template's own names")
    parser.add_argument("--class", dest="cls", metavar="CLASS",
                        help="main class name. Default: CamelCase(NAME) + Provider | Plugin as in the template")
    parser.add_argument("--force", action="store_true", help="replace DEST when it exists")
    args = parser.parse_args()

    if args.template in TEMPLATES:
        src = TEMPLATES_DIR / TEMPLATES[args.template]["dist"]
        old = TEMPLATES[args.template]
    else:
        src = Path(args.template)
        if not (src / "pyproject.toml").is_file():
            raise SystemExit(f"unknown template {args.template!r}; choose {', '.join(TEMPLATES)} or a folder with pyproject.toml")
        old = None
    if not src.is_dir():
        raise SystemExit(f"template folder missing: {src}")

    dest: Path = args.dest
    if dest.exists():
        if not args.force:
            raise SystemExit(f"{dest} exists (use --force to replace it)")
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", ".pytest_cache"))

    if args.name and old is not None:
        if not NAME_RE.match(args.name):
            raise SystemExit("--name: lowercase letters, digits and _ only, starting with a letter")
        new = {"dist": f"pynecore-{args.name.replace('_', '-')}", "pkg": f"pynecore_{args.name}", "key": args.name,
               "cls": args.cls or camel(args.name) + old["suffix"], "cfg": camel(args.name) + "Config"}
        # package folder
        old_pkg_dir = dest / "src" / old["pkg"]
        new_pkg_dir = dest / "src" / new["pkg"]
        if old_pkg_dir.is_dir() and old_pkg_dir != new_pkg_dir:
            old_pkg_dir.rename(new_pkg_dir)
        for path in dest.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                path.write_text(rewrite(path.read_text(encoding="utf-8"), old, new), encoding="utf-8")
        print(f"Created {dest}  (plugin '{new['key']}', package {new['pkg']}, class {new['cls']})")
    else:
        key = old["key"] if old else "<name>"
        print(f"Created {dest}  (copy of the template; plugin '{key}')")
        new = old or {"key": key}

    print("Next:")
    print(f"  pip install -e {dest}            # into the environment that has pyne (or: uv pip install -e {dest})")
    print(f"  pyne plugin info {new['key']}")
    print(f"  edit <workdir>/config/plugins/{new['key']}.toml   # generated with commented defaults on the next pyne call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
