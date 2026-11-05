# pynecore-skills

Agent skills for building trading strategies with [PyneCore](https://pynecore.org): Python
scripts marked `@pyne`, executed bar by bar with series history, persistent state and a
broker emulator, run through the `pyne` CLI or the `ScriptRunner` API.

The skills are **self-contained and global**. Install once, use in every project: no
per-project `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.claude/` or `.cursor/` folders and no files
with machine-specific paths. Each skill discovers the project's workdir, interpreter and CLI at
the start of a task.

| Skill | What it covers |
|-------|----------------|
| [`skills/pynecore-strategy`](skills/pynecore-strategy/SKILL.md) | Writing, backtesting, debugging, optimizing and deploying PyneCore strategies and indicators; CLI and programmatic use; data management; multi-timeframe; live mode; FreqTrade and DataFrame integration |

## Install

Skills follow the open agent-skills layout (`<name>/SKILL.md` plus `references/`, `scripts/`,
`templates/`). Clone the repository and link the skill into the global skill folder of your
agent:

```bash
git clone https://github.com/theonetrade/pynecore-skills.git ~/pynecore-skills
cd ~/pynecore-skills
./install.sh                 # links into ~/.claude/skills, ~/.cursor/skills and ~/.agents/skills (whichever exist or are requested)
./install.sh --claude        # Claude Code only
./install.sh --cursor        # Cursor only
./install.sh --agents        # generic ~/.agents/skills (Codex and other hosts that read it)
./install.sh --target /some/dir/skills
```

`install.sh` creates symlinks, so `git pull` updates the installed skills in place. Use
`--copy` to copy instead of linking (for hosts that do not follow symlinks), and `--uninstall`
to remove what it installed.

### Claude Code as a plugin

The repository is also a Claude Code plugin (`.claude-plugin/plugin.json` with the skill under
`skills/`). From a Claude Code session:

```
/plugin marketplace add theonetrade/pynecore-skills
/plugin install pynecore-skills@pynecore-skills
```

### Manual

Copy or link `skills/pynecore-strategy` to:

| Host | Global skills folder |
|------|----------------------|
| Claude Code | `~/.claude/skills/pynecore-strategy` |
| Cursor | `~/.cursor/skills/pynecore-strategy` |
| Generic agent hosts | `~/.agents/skills/pynecore-strategy` |

A project-local install (`<project>/.claude/skills/...`) works the same way when a team wants
the skill versioned with a repository.

## Requirements in a target project

- Python 3.11+ with `pynesys-pynecore[cli]` (and `[ccxt]` for exchange data) in a virtual
  environment, or a `uv` project depending on it.
- Optionally a PyneCore workdir (`.pyne` marker, `scripts/`, `data/`, `output/`, `config/`).
  The skill can create one.

The bundled `scripts/pyne_env.py` reports all of this for a given project.

## Layout

```
skills/pynecore-strategy/
├── SKILL.md                      # entry point: workflow, essential rules, reference map
├── references/                   # detailed documentation, loaded on demand
│   ├── language.md               # Pyne execution model, Series, Persistent, isolation, na, libraries
│   ├── strategy-api.md           # @script.strategy settings, strategy.* API, fills, statistics
│   ├── inputs-and-settings.md    # input.*, the script .toml, overrides, env vars
│   ├── ta.md                     # all 67 ta.* functions
│   ├── lib.md                    # bar data, time, math, collections, strings, state flags, plots, drawings
│   ├── cli-and-data.md           # workdir, pyne run, pyne data, symbol .toml, outputs
│   ├── programmatic.md           # ScriptRunner, OHLCV, SymInfo, standalone, sweeps
│   ├── multi-timeframe.md        # request.security, lookahead, security data
│   ├── live-and-integrations.md  # live mode, broker, DataFrame bridge, FreqTrade
│   ├── patterns.md               # entry/exit/sizing/filter/state snippets, validation checklist
│   └── pitfalls.md               # symptom -> cause -> fix
├── scripts/                      # tools the agent runs
│   ├── pyne_env.py               # environment discovery (stdlib)
│   ├── new_script.py             # scaffold strategy / indicator / library (stdlib)
│   ├── run_backtest.py           # backtest with overrides, CSV conversion, sweeps (project interpreter)
│   └── summarize_backtest.py     # report from the statistics and trade CSVs (stdlib)
└── templates/                    # runnable starting points
    ├── strategy_template.py
    ├── indicator_template.py
    ├── library_template.py
    ├── pynecore_bridge.py
    ├── freqtrade_indicators_strategy.py
    ├── freqtrade_signals_strategy.py
    ├── live_ccxt.py
    └── custom_feed.py
```

## Versioning

The documentation and templates track PyneCore 6.12. When a newer PyneCore changes an API,
update the affected reference file and the templates together, run the templates against the
demo data (`pyne --recreate-demo plugin list`, then `pyne run strategy_template demo`), and bump
the version in `.claude-plugin/plugin.json`.

## License

MIT, see [LICENSE](LICENSE).
