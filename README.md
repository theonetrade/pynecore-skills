# pynecore-skills

Agent skills for building trading strategies with [PyneCore](https://pynecore.org): Python
scripts marked `@pyne`, executed bar by bar with series history, persistent state and a
broker emulator, run through the `pyne` CLI, the `ScriptRunner` API or the PyneIDE editor.

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
only the target project. `skills/pynecore-strategy/scripts/pyne_env.py` and
`skills/pynecore-ide/scripts/ide_env.py` report a project's state.

## Layout

```
skills/
├── pynecore-strategy/
│   ├── SKILL.md                      # workflow, essential rules, reference map
│   ├── references/                   # language, strategy-api, inputs-and-settings, ta, lib,
│   │                                 # cli-and-data, programmatic, multi-timeframe,
│   │                                 # live-and-integrations, patterns, pitfalls
│   ├── scripts/                      # pyne_env.py, new_script.py, run_backtest.py, summarize_backtest.py
│   └── templates/                    # strategy, indicator, library, DataFrame bridge, FreqTrade x2,
│                                     # live CCXT, custom feed
├── pynecore-ide/
│   ├── SKILL.md                      # PyneIDE project workflow
│   ├── references/                   # commands-and-settings, project-files
│   └── scripts/ide_env.py            # detect the IDE environment and generated files
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
