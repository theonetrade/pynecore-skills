# PyneIDE commands and settings (extension 0.11.x)

Command Palette titles are prefixed `PyneIDE:`. Command ids are useful for keybindings and
tasks. Only the parts relevant to Pyne (Python) scripts are listed.

## Contents

- [Project and environment](#project-and-environment)
- [Running, debugging, profiling](#running-debugging-profiling)
- [Chart and run controls](#chart-and-run-controls)
- [Scripts, data and output (Pyne view)](#scripts-data-and-output-pyne-view)
- [Analysis and typing](#analysis-and-typing)
- [Settings](#settings)
- [Views, panels, languages](#views-panels-languages)

## Project and environment

| Command (Palette title) | Id | What it does |
|-------------------------|----|--------------|
| Initialize Pyne Project | `pyneide.createWorkspace` | Scaffolds the workdir (`.pyne`, `scripts/` with `demo.py`, `data/demo.*`, `output/`, `config/`), `.vscode/settings.json`, `pyrightconfig.json`, `.vscode/pyne.code-snippets`, plus per-project agent boilerplate that these skills do not use. Re-running keeps user files. |
| Setup Environment | `pyneide.setupEnvironment` | Creates `.pyneide/venv` with `uv` (Python 3.14, PyneCore, debugpy) |
| Remove Environment | `pyneide.removeEnvironment` | Deletes the project's managed environment |
| Reveal Environment Folder | `pyneide.revealEnvironmentFolder` | Opens `.pyneide/venv` |
| Show Environment Log | `pyneide.showEnvironmentLog` | Setup / update failures |
| Environment Menu | `pyneide.environmentMenu` | Status bar menu: environment, plugins, language-server update |
| Manage Plugins | `pyneide.openPlugins` | Install PyneCore provider/broker plugins into the managed env (gives a `pip install` line for user-managed envs) |
| Edit Agent Rules… | `pyneide.editAgentRules` | Opens the IDE's per-project agent preferences file (part of the boilerplate these skills do not use) |
| Open Settings | `pyneide.openSettings` | PyneIDE settings |
| Documentation | `pyneide.openDocs` | Online docs |
| Get Started | `pyneide.openWalkthrough` | Walkthrough |
| Report a Problem | `pyneide.reportProblem` | Opt-in bug report (`pyneide.reportProblem.includeLogs`) |

## Running, debugging, profiling

| Command | Id | Notes |
|---------|----|-------|
| Run Pyne code | `pyneide.runScript` / `pyneide.workspace.runScript` | Editor toolbar button or Pyne tree. Asks for data the first time (`Select Data…`), streams onto the chart, writes `output/` files |
| Debug Pyne code | `pyneide.debugScript` / `pyneide.workspace.debugScript` | Debug adapter type `pyne`; breakpoints in the source, **Pyne** scope with the bar's OHLCV and time, Watch supports `close[1]` |
| Profile Pyne code | `pyneide.profileScript` / `pyneide.workspace.profileScript` | Flame button; per-line share column, hot lines flagged; writes `output/<script>_profile.json`; Python 3.13+ |
| Show Profile / Hide Profile | `pyneide.showProfile` / `pyneide.hideProfile` | Re-show the stored profile for an unchanged script |
| Select Data… | `pyneide.changeRunData` / `pyneide.workspace.selectData` | Data picker, also offers **Download new data…** |
| Edit Inputs… | `pyneide.editInputs` | Form built from the `input.*()` declarations; Save writes `<script>.toml` and re-runs; Reset to defaults |
| Show Data Requirements | `pyneide.showDataRequirements` | The `request.security()` contexts the script needs (like `pyne run --list-data`) |

## Chart and run controls

| Command | Id | Notes |
|---------|----|-------|
| Open Chart | `pyneide.openChart` / `pyneide.workspace.openOutputChart` | Candles + plots, drawings, trade markers; Performance (equity curve), Trades and Stats tables for strategies; Layers popup, measure tool, go-to-date, fullscreen, CSV export |
| Toggle Chart Fullscreen | `pyneide.toggleChartFullscreen` | |
| Choose Bar on Chart… | `pyneide.selectBreakpointBar` | Chart-bar breakpoint: the debugger pauses on that bar |
| Pause Run at Next Bar | `pyneide.pauseRun` | |
| Resume Run | `pyneide.resumeRun` | |
| Next Bar | `pyneide.stepBar` | Step one bar while paused |
| Run to Bar… | `pyneide.runToBar` | Run until a bar index or date |
| Stop Run | `pyneide.cancelRun` | |
| Run Controls | `pyneide.runControlMenu` | Menu with the above |

## Scripts, data and output (Pyne view)

| Command | Id | Notes |
|---------|----|-------|
| New Script… | `pyneide.workspace.createScript` | Indicator / strategy / library skeleton in `scripts/` |
| Refresh | `pyneide.workspace.refresh` | |
| Download Data… | `pyneide.dataDownloadWizard` | Provider, symbol, timeframe, range |
| Open Symbol Browser | `pyneide.openSymbolBrowser` | Browse a provider's symbols with symbol info |
| Update (continue) | `pyneide.dataUpdate` | Fetch new bars for a data file |
| Truncate & Re-download… | `pyneide.dataTruncate` | |
| Download Other Timeframe… | `pyneide.dataDownloadTimeframe` | |
| Preview Chart | `pyneide.dataPreviewChart` | Candles only |
| Open as Table | `pyneide.workspace.openTable` | Read-only OHLCV table |
| Delete Data… / Delete (script, output) | `pyneide.dataDelete`, `pyneide.scriptDelete`, `pyneide.outputDelete` | Also the Delete key in the Pyne tree |
| Edit Symbol Map / Open Symbol Map | `pyneide.editSymbolMap` / `pyneide.openSymbolMap` | `config/symbol_map.toml` for `request.security()` symbols |
| Reveal in Explorer | `pyneide.workspace.revealInExplorer` | |

## Analysis and typing

| Command | Id | Notes |
|---------|----|-------|
| Use PyneIDE for Python Analysis (instead of Pylance) | `pyneide.usePyneAnalysis` | Switches the workspace to the bundled analysis that understands `Series` |
| Restart Pyne Typing (pyright) | `pyneide.pyrightRestart` | |
| Show Pyne Typing Log | `pyneide.pyrightShowLog` | |

The Pyne checker (`pyneide.checker.enabled`) reports structural problems of `@pyne` files in
the Problems panel: missing `main()`, missing `@script` decorator, Series/Persistent misuse,
constructs the runtime would reject at import time. Runtime errors of a run also mark the
failing line in Problems and open it.

## Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `pyneide.workdir` | `""` | Workdir relative to the workspace (`.` = the folder itself) or absolute; empty = auto-detect |
| `pyneide.venvPath` | `""` | Use this virtualenv instead of the managed one (takes precedence over `pythonPath`); install PyneCore and `debugpy` there yourself |
| `pyneide.pythonPath` | `""` | Use this interpreter instead of the managed one |
| `pyneide.pynecore.version` | `""` | Pin the PyneCore release for the project (min 6.11.1); empty = follow latest stable, auto-update daily |
| `pyneide.showAgentFiles` | `false` | Show the generated agent boilerplate in the Explorer |
| `pyneide.run.revealOutputOnLog` | `true` | Reveal the **PyneIDE Run** output channel on the first `log.*` line |
| `pyneide.debug.justMyCode` | `true` | Keep stepping out of PyneCore internals |
| `pyneide.checker.enabled` | `true` | Pyne structure checker |
| `pyneide.pyright.enabled` | `true` | Bundled pyright for Python files in Pyne workspaces |
| `pyneide.chart.candleStyle` | `"candles"` | Price drawing style; chart toolbar writes it |
| `pyneide.chart.colorScheme` | `"classic"` | Up/down colors |
| `pyneide.chart.priceScale` | `"normal"` | `normal`, log or percent; chart buttons write it |
| `pyneide.reportProblem.includeLogs` | `true` | Attach sanitized logs to a report |
| `pyneide.strictCompile`, `pyneide.apiBaseUrl` and the language-server group of settings | | Only relevant to the cloud compiler service for scripts in another language and to its language server; not needed for Pyne scripts. Those scripts are converted locally with `pine2pyne` and the IDE's compile / run / debug commands for them are not used (`../../pynecore-pine2pyne/references/cloud-compiler-override.md` lists the command ids) |

Workspace `.vscode/settings.json` written by Initialize Pyne Project (abridged):

```json
{
  "pyneide.workdir": ".",
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.analysis.extraPaths": ["${workspaceFolder}/scripts/lib"],
  "python.languageServer": "None",
  "files.exclude": { ".agents": true, ".claude": true, ".cursor": true, "AGENTS.md": true,
                     "CLAUDE.md": true, "AGENT_RULES.md": true, ".pyne": true, ".vscode": true,
                     "**/pyrightconfig.json": true, "**/__pycache__": true }
}
```

## Views, panels, languages

- Activity bar **Pyne** → view **Workspace** (`pyneide.workspace`): Scripts, Data, Output.
- Output channels: **PyneIDE Run** (runtime logs, `log.info/warning/error`, runtime errors),
  **PyneIDE Environment** (setup), typing log, language-server log.
- Problems panel: checker diagnostics and the failing line of a runtime error.
- Language ids: `pyne` (a `.py` file whose docstring starts with `@pyne` switches to it
  automatically; the Python extension's Run/Debug buttons do not apply to it), `toml`.
- Debugger type: `pyne`.
- Keybinding: Delete / Backspace in the Pyne tree deletes the selected item.
