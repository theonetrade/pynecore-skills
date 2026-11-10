# Files in a PyneIDE project

What **Initialize Pyne Project** creates, what each file is for, and what an agent may change.

## Contents

- [Layout](#layout)
- [Workdir files](#workdir-files)
- [Environment: `.pyneide/`](#environment-pyneide)
- [Editor settings](#editor-settings)
- [Agent boilerplate (disposable)](#agent-boilerplate-disposable)
- [Snippets](#snippets)
- [`.gitignore` recommendations](#gitignore-recommendations)

## Layout

```
<workspace>/
├── .pyne                      # workdir marker; the workspace is the workdir
├── scripts/                   # Pyne scripts; demo.py; <name>.toml settings
│   └── lib/                   # libraries (empty at first)
├── data/                      # demo.ohlcv + demo.toml; downloaded/converted data
├── config/
│   ├── .gitignore             # "*" + "!.gitignore": nothing in config/ is versioned (keys live here)
│   ├── api.toml               # [api] api_key = "" / timeout = 30 (cloud compiler service; unused: see ../../pynecore-pine2pyne/SKILL.md)
│   └── plugins/               # ccxt.toml, replay.toml, ... one per installed provider/broker plugin
├── output/                    # run outputs (csv, ndjson, profile json)
│   └── logs/
├── cache/
├── pyrightconfig.json         # type-checker relaxations for Series
├── .pyneide/                  # managed environment (venv/, env.json), gitignored
├── .vscode/settings.json      # pyneide.workdir, interpreter, files.exclude
├── .vscode/pyne.code-snippets # editor snippets
└── AGENTS.md, CLAUDE.md, AGENT_RULES.md, .agents/, .claude/, .cursor/
                               # per-project agent boilerplate; replaced by these global skills
```

`.pyne` contains:

```toml
# PyneCore workdir marker. The version refers to the workdir structure, not your project.
workdir_version = "1.0"
```

`pyneide.workdir` may point elsewhere (for example `workdir/`); then the workdir files live
there and the workspace root keeps only the editor files. `scripts/ide_env.py --create`
reproduces the workdir part of this layout (marker, folders, `config/.gitignore`,
`config/api.toml`) without touching existing files.

## Workdir files

| File | Content | Edit? |
|------|---------|-------|
| `.pyne` | `workdir_version = "1.0"` | no |
| `scripts/demo.py` | Demo indicator (two EMAs) | delete or replace freely |
| `scripts/<name>.py` | Your Pyne scripts | yes |
| `scripts/<name>.toml` | `[script]` settings and `[inputs.<param>] value =`; written on first run and by **Edit Inputs…** | yes (values only) |
| `scripts/lib/<publisher>/<name>/v<N>.py` | Libraries, imported as `lib.<publisher>.<name>.v<N>` | yes |
| `data/demo.ohlcv`, `data/demo.toml` | 2000 synthetic daily bars, 24/7 symbol `DEMO:DEMOBTC` | keep for smoke tests |
| `data/<name>.ohlcv` + `.toml` | Market data and symbol info | review the `.toml`; edit when the heuristics are wrong |
| `data/<name>.extra.csv` | Extra per-bar columns (optional) | generated |
| `output/*` | `<script>.csv`, `_strat.csv`, `_trade.csv`, `_viz.ndjson`, `_profile.json`, `logs/` | generated |
| `config/plugins/ccxt.toml` | CCXT provider: `apiKey`, `secret`, `password`, `sandbox`, `default_type` (`spot`/`swap`/`future`/`margin`), `symbol_map` | yes; never commit keys |
| `config/plugins/replay.toml` | Replay provider fixture path (tests) | rarely |
| `config/symbol_map.toml` | `request.security()` symbol translations (**Edit Symbol Map**) | yes |
| `config/api.toml` | `[api] api_key = "" / timeout = 30`; key for an optional cloud compiler service; not needed for Pyne scripts, and conversions from the charting platform's language use the local `pine2pyne` instead (`../../pynecore-pine2pyne/SKILL.md`) | leave, key empty |
| `config/.gitignore` | `*` and `!.gitignore`: keeps every config file (and the keys in `plugins/`) out of git | keep |

## Environment: `.pyneide/`

```
.pyneide/
├── .gitignore        # "*": nothing inside is versioned
├── env.json          # {"schema": 2, "python": "3.14", "pynecore": "6.12.1", "debugpy": "1.8.21",
│                     #  "selection": "", "checkedAt": 1793855111288}
└── venv/             # uv-created virtualenv: python, pyne, debugpy
    └── bin/pyne, bin/python
```

- `selection` is `""` for the managed environment, otherwise the user-provided path from
  `pyneide.venvPath` / `pyneide.pythonPath`.
- `checkedAt` is the last update check (epoch ms). PyneCore updates install automatically, at
  most daily, unless `pyneide.pynecore.version` pins a release.
- Python installations and `uv` live outside the project and are shared between projects; only
  the venv is per project.
- The IDE verifies but never installs into a user-provided environment; install
  `pynesys-pynecore[cli]` and `debugpy` there yourself.

## Editor settings

`.vscode/settings.json` as generated (the interpreter line reflects the project's chosen
environment):

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "python.analysis.extraPaths": ["${workspaceFolder}/scripts/lib"],
  "pyneide.workdir": ".",
  "files.exclude": {
    ".agents": true, ".claude": true, ".cursor": true,
    "AGENTS.md": true, "CLAUDE.md": true, "AGENT_RULES.md": true,
    "**/__pycache__": true, "**/pyrightconfig.json": true, ".vscode": true, ".pyne": true
  },
  "python.languageServer": "None"
}
```

`pyrightconfig.json` as PyneIDE writes it for a managed environment:

```json
{
  "typeCheckingMode": "basic",
  "enableReachabilityAnalysis": false,
  "defineConstant": { "TYPECHECKER": "pyright", "PYNEIDE": true },
  "reportIndexIssue": "none",
  "reportRedeclaration": "none",
  "pythonVersion": "3.14",
  "exclude": ["data", "output", "**/__pycache__", ".pyneide"],
  "ignore": [".pyneide"],
  "venvPath": "<absolute path of the workspace>/.pyneide",
  "venv": "venv"
}
```

A hand-written equivalent for a project with its own `.venv` adds `"extraPaths": ["scripts/lib"]`,
`"reportAssignmentType": "none"`, `"reportArgumentType": "none"` and points `venvPath`/`venv` at
that environment.

These make `close[1]` and other Series idioms type-check; without them Pylance reports false
errors. PyneIDE's own analysis (bundled pyright + Pyne checker) replaces Pylance in Pyne
workspaces and defers when pyright/basedpyright is already configured.

## Agent boilerplate (disposable)

Initialize Pyne Project also writes `AGENTS.md`, `CLAUDE.md` (which includes `AGENTS.md`),
`AGENT_RULES.md`, and copies of the IDE's own skills under `.agents/skills/`, `.claude/skills/`
and `.cursor/skills/` with a hash manifest `.agents/pyneide-skills.json`. Those skills hold
absolute paths of this machine and duplicate what the global `pynecore-*` skills provide.

- Nothing in these skills reads or needs those files.
- Delete them, or add them to `.gitignore`. Re-running Initialize Pyne Project recreates them;
  the IDE hides them in the Explorer unless `pyneide.showAgentFiles` is on.
- If a team wants shared, repository-level agent instructions, write them in a plain
  `AGENTS.md` of your own instead of the generated block.

## Snippets

`.vscode/pyne.code-snippets` (scope `python,pyne`), rewritten on re-initialization unless the
`// pyneide-snippets: <hash>` marker line is deleted:

| Prefix | Inserts |
|--------|---------|
| `indicator` | Full indicator file skeleton |
| `strategy` | Full strategy file skeleton (explicit `default_qty_type` / `default_qty_value`) |
| `library` | Library module skeleton (`@pyne lib`, `__all__`) |
| `inputgroup` | Grouped `input.*` block for the `main()` signature |
| `macross` | Fast/slow MA crossover with signals and plots |
| `rsi` | RSI with `hline` bands and fill |
| `bb` | Bollinger bands with fill |
| `macd` | MACD line, signal, histogram |
| `atrstop` | ATR-based stop levels |
| `plotcond` | Plot colored by a condition + `bgcolor` |
| `plotshape` | Buy/sell markers |
| `alertcond` | `alertcondition` + `alert()` |
| `htf` | Higher-timeframe value through `request.security` with the `close[1]` idiom |
| `lastlabel` | One label kept on the last bar (`Persistent[Label]`) |
| `entryexit` | Entry with ATR stop and R-multiple target |
| `varcount` | `Persistent[int]` counter |
| `series` | `Series[float]` with previous-bar access |
| `barssince` | `ta.barssince` + `ta.valuewhen` |
| `table` | Info table on the last bar |

## `.gitignore` recommendations

```gitignore
.pyneide/            # already ignored from inside; add for clarity
.venv/
__pycache__/
output/
cache/
*_profile.json
data/*.ohlcv         # large binaries; keep data/*.toml
data/*.extra.csv
config/api.toml
config/brokers.toml
config/plugins/      # may hold exchange keys
# generated agent boilerplate, replaced by the global skills
AGENTS.md
CLAUDE.md
AGENT_RULES.md
.agents/
.claude/
.cursor/
```

Keep `scripts/*.toml` and `data/*.toml` under version control: they define inputs, settings and
symbol info that the results depend on.
