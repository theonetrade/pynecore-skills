# pynecore-skills

[![skills.sh](https://skills.sh/b/theonetrade/pynecore-skills)](https://skills.sh/theonetrade/pynecore-skills)

Agent skills for building trading strategies with [PyneCore](https://pynecore.org): Python
scripts marked `@pyne`, executed bar by bar with series history, persistent state and a
broker emulator, run through the `pyne` CLI, the `ScriptRunner` API or the PyneIDE editor.

```bash
npx skills add theonetrade/pynecore-skills
```

The skills are **self-contained and global**. Install once, use in every project: no
per-project `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.claude/` or `.cursor/` folders and no files
with machine-specific paths. Each skill discovers the project's workdir, interpreter, CLI,
editor state and local documentation at the start of a task.

| Skill | What it covers |
|-------|----------------|
| [`pynecore-strategy`](skills/pynecore-strategy/SKILL.md) | Writing, backtesting, debugging, optimizing and deploying PyneCore strategies and indicators; language rules; strategy API; CLI and data; `ScriptRunner`; multi-timeframe; live mode; FreqTrade and DataFrame integration; scaffolding, backtest and report tools |
| [`pynecore-ide`](skills/pynecore-ide/SKILL.md) | Working in a project initialized by PyneIDE (the VS Code extension): the managed environment and how an agent shell reaches it, the files the IDE writes into a project (and which of them are disposable boilerplate), editor commands for run / debug / profile / inputs / chart / data, evidence reporting |
| [`pynecore-docs`](skills/pynecore-docs/SKILL.md) | The complete PyneCore documentation bundled in this repository (offline), a topic map and page inventory, grep/print tools, a version check against the installed runtime, and a sync script that refreshes the snapshot from a PyneCore release |

## Install

Skills follow the open agent-skills layout (`<name>/SKILL.md` plus `references/`, `scripts/`,
`templates/`). Clone the repository and link the skills into the global skill folder of your
agent:

```bash
git clone https://github.com/theonetrade/pynecore-skills.git ~/pynecore-skills
cd ~/pynecore-skills
./install.sh                 # links every skill into ~/.claude/skills, ~/.cursor/skills and ~/.agents/skills (whichever exist)
./install.sh --claude        # Claude Code only
./install.sh --cursor        # Cursor only
./install.sh --agents        # generic ~/.agents/skills (Codex and other hosts that read it)
./install.sh --target /some/dir/skills
```

`install.sh` creates one symlink per skill folder, so `git pull` updates the installed skills in
place and a new `skills/<name>/` folder is picked up on the next run. Use `--copy` to copy
instead of linking (for hosts that do not follow symlinks) and `--uninstall` to remove what it
installed.

### Claude Code as a plugin

The repository is also a Claude Code plugin (`.claude-plugin/plugin.json`; every folder under
`skills/` is a skill of the plugin). From a Claude Code session:

```
/plugin marketplace add theonetrade/pynecore-skills
/plugin install pynecore-skills@pynecore-skills
```

### Manual

Copy or link each `skills/<name>` folder to:

| Host | Global skills folder |
|------|----------------------|
| Claude Code | `~/.claude/skills/<name>` |
| Cursor | `~/.cursor/skills/<name>` |
| Generic agent hosts | `~/.agents/skills/<name>` |

A project-local install (`<project>/.claude/skills/...`) works the same way when a team wants
the skills versioned with a repository.

## Requirements in a target project

- Python 3.11+ with `pynesys-pynecore[cli]` (and `[ccxt]` for exchange data) in a virtual
  environment, a `uv` project depending on it, or a PyneIDE-managed `.pyneide/venv`.
- Optionally a PyneCore workdir (`.pyne` marker, `scripts/`, `data/`, `output/`, `config/`).
  The strategy skill can create one.

Nothing else is required: the documentation is part of the repository, and the skills inspect
only the target project. `skills/pynecore-strategy/scripts/init_project.py <dir>` creates a
complete new project (workdir layout, `.venv` with PyneCore through `uv` or `pip`, demo script
and data, `pyrightconfig.json`, `.gitignore`; no agent or editor files);
`skills/pynecore-strategy/scripts/pyne_env.py` reports an existing project's structure,
interpreter, CLI, scripts and data; `skills/pynecore-strategy/scripts/download_candles.py`
downloads and updates exchange candles through CCXT (Binance by default) into the project's
`data/` cache; `run_backtest.py`, `run_paper.py` and `run_live.py` in the same folder run a
strategy on a data file, on a live stream with simulated fills, and against a broker plugin
through `pyne run --broker`; `skills/pynecore-ide/scripts/ide_env.py` verifies (or creates with
`--create`) the workdir structure PyneIDE and the CLI expect.

## Documentation tools

The `pynecore-docs` skill ships the PyneCore documentation as plain Markdown in
`skills/pynecore-docs/docs/` and two standard-library scripts to work with it. Run them with any
Python 3.11+; they read nothing outside the repository except, for the version check, the
target project's virtual environment.

### `find_docs.py`: look things up

```bash
S=skills/pynecore-docs/scripts

python3 $S/find_docs.py                              # docs folder, documented PyneCore version, installed runtime version
python3 $S/find_docs.py --root /path/to/project      # same, probing that project's venv for the installed version
python3 $S/find_docs.py --list                       # every page with its title
python3 $S/find_docs.py --grep "process_orders_on_close"      # case-insensitive regex over all pages: file:line: text
python3 $S/find_docs.py --grep "strategy\.exit\(" --context 2 # with surrounding lines
python3 $S/find_docs.py --page reference/lib/strategy.md      # absolute path of a page (the .md suffix is optional)
python3 $S/find_docs.py --page reference/lib/strategy.md --print   # print the page
python3 $S/find_docs.py --online reference/lib/strategy.md    # URL of the same page on pynecore.org
python3 $S/find_docs.py --json                       # machine-readable summary
python3 $S/find_docs.py --docs /other/docs --list    # use another docs folder (a PyneCore checkout, for example)
```

Exit codes: `0` on success, `1` when the docs folder or the page is missing or a grep has no hits.

Typical flow: `--grep` to find the page, `--page ... --print` to read the relevant section, quote
the signature from it. When the reported runtime version is newer than the documented one,
confirm a changed API against the installed package (`pyne run --help`, `inspect.signature`).

### `sync_docs.py`: refresh or verify the snapshot

```bash
S=skills/pynecore-docs/scripts

python3 $S/sync_docs.py --check                      # verify every file against docs/MANIFEST.json
python3 $S/sync_docs.py --tag v6.12.0                # replace the snapshot with the docs of that PyneCore release (GitHub download)
python3 $S/sync_docs.py --from /path/to/pynecore/docs          # copy from a local checkout or any docs folder
python3 $S/sync_docs.py --from /path/to/docs --version 6.12.0 --commit <sha>   # set the recorded version/commit explicitly
python3 $S/sync_docs.py --tag v6.12.0 --keep-all     # also copy the pages excluded by default
python3 $S/sync_docs.py --tag v6.12.0 --dest /tmp/docs-preview   # write somewhere else first
```

What it does: deletes the target folder, copies the pages plus `LICENSE` and `NOTICE`
(taken from the release archive, or found next to a local source folder; `--license` /
`--notice` override), and writes `MANIFEST.json` with `pynecoreVersion`, `sourceRef`,
`sourceCommit`, `snapshotDate`, the list of excluded pages and a SHA-256 per file. Two pages
about converting scripts written in another language through a cloud compiler are excluded by
default. `--tag` needs network access; `--from` works offline.

Release routine: `sync_docs.py --tag v<new>`, then `sync_docs.py --check`, run
`find_docs.py --grep` on the features you rely on, update the affected `references/` files in
`pynecore-strategy`, and commit the new `docs/` together with those changes.

## Layout

```
skills/
├── pynecore-strategy/
│   ├── SKILL.md                      # workflow, essential rules, reference map
│   ├── references/                   # language, strategy-api, inputs-and-settings, ta, lib,
│   │                                 # cli-and-data, programmatic, multi-timeframe,
│   │                                 # live-and-integrations, patterns, pitfalls
│   ├── scripts/                      # init_project.py, pyne_env.py, new_script.py, download_candles.py,
│   │                                 # run_backtest.py, run_paper.py, run_live.py, summarize_backtest.py
│   └── templates/                    # minimal_cli.py + minimal_cli_indicator.py (workdir-only), full strategy/indicator
│                                     # (standalone-runnable), library, DataFrame bridge, FreqTrade x2,
│                                     # live CCXT, custom feed
├── pynecore-ide/
│   ├── SKILL.md                      # PyneIDE project workflow
│   ├── references/                   # commands-and-settings, project-files
│   └── scripts/ide_env.py            # verify / create the workdir structure PyneIDE requires
└── pynecore-docs/
    ├── SKILL.md                      # offline documentation lookup
    ├── docs/                         # PyneCore documentation snapshot (MANIFEST.json, LICENSE, NOTICE, pages)
    ├── references/inventory.md       # every documentation page by section
    └── scripts/
        ├── find_docs.py              # version check, list, grep, print pages
        └── sync_docs.py              # refresh/verify the snapshot from a PyneCore release or a local folder
```

## Versioning

The references and templates track PyneCore 6.12 and PyneIDE 0.11; `skills/pynecore-docs/docs/MANIFEST.json`
names the documented PyneCore version of the snapshot. When a newer release changes an API or a
command: refresh the snapshot (`python3 skills/pynecore-docs/scripts/sync_docs.py --tag v<version>`),
update the affected reference files and templates, run the templates against the demo data
(`pyne --recreate-demo plugin list`, then `pyne run strategy_template demo`), and bump the
version in `.claude-plugin/plugin.json`.

## License

The skills (everything except `skills/pynecore-docs/docs/`) are MIT, see [LICENSE](LICENSE).
The documentation snapshot in `skills/pynecore-docs/docs/` is PyneCore's own documentation,
Apache-2.0, redistributed unmodified with its `LICENSE` and `NOTICE` in that folder.
