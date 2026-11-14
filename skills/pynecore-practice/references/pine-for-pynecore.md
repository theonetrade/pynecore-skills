# Writing the Pine so that it converts and runs

Habits that make `content/<name>.pine` convert on the first try and run on PyneCore without
surprises. They are ordinary good Pine; nothing here depends on a particular transpiler version.

## Structure

- One declaration per file, first statement after `//@version=6`: `indicator(...)`,
  `strategy(...)` or `library(...)`. Its arguments must be literals or top-level constants
  (TradingView requires `const` there too); they become the `@script.*(...)` decorator.
- Prefer `//@version=6`. v4 and v5 convert too, but v6 semantics (lazy `and`/`or`, two-state
  `bool`, re-read `for` bounds) are the ones PyneCore implements natively, so the generated code
  carries fewer compatibility shims.
- Every `input.*()` at the top level, with a literal default, a title, and `minval` / `maxval` /
  `options` where they apply. Each becomes a `main()` parameter and a section of
  `scripts/<name>.toml`; a title is what the user sees in PyneIDE's **Edit Inputs…**.
- Call `ta.*` and other stateful functions at the top level, on every bar, and branch on the
  result. A `ta.*` call inside `if` or a ternary advances its history only on the bars where it
  runs (on TradingView as well); pine2pyne writes it as is and warns `conditional-history-call`.
- Declare persistent state with a type: `var float stopPrice = na`, `var int count = 0`. It
  becomes `Persistent[float]` / `Persistent[int]`.
- Give every `plot()` / `plotshape()` / `plotchar()` a unique title: the titles are the column
  names of `output/<name>.csv` and the only way to find a series in it.
- Give every order an id and refer to it: `strategy.entry("L", strategy.long)`,
  `strategy.exit("L exit", "L", stop=..., limit=...)`, `strategy.close("L")`. v4/v5 `when=`
  arguments convert, but an `if` around the call is clearer and is what the output contains.

## Pine rules TradingView enforces (and so does the converter)

Fix these in the Pine source; they are not transpiler limits: assigning to a history reference
(`x[1] := ...`); `:=` on a variable that was never declared; reading a variable declared inside
a block that has ended; a function that modifies a global or its own parameter; a function
declared inside a function; recursion; `[]` applied twice (`close[1][2]`); a history index on
a loop counter or on a UDT field in v6 (`(t[1]).x`); an input whose `defval` reads another
input; a type, enum or function named like a built-in namespace (`ta`, `math`, `str`);
redeclaring a variable in one scope (a warning, the later one wins).

## Libraries

- Your own library lives at `content/lib/<user>/<Name>/v<N>.pine` and is imported as
  `import <user>/<Name>/<N> as alias`; `<user>` is any folder name you choose for local
  libraries (`mine` in the template), `<N>` is the version in the path. A `-` in `<user>`
  becomes `_` in the Python module path.
- A third-party library from TradingView: save its source under the same scheme with the
  publisher's name (`content/lib/jdehorty/KernelFunctions/v2.pine` for
  `import jdehorty/KernelFunctions/2`). pine2pyne never downloads anything.
- The library file has `library("Name")`, `export` on every public function; `// @function` /
  `// @description` comments are kept as documentation. The helper converts libraries before
  scripts, so the import resolves on the first run.

## Data the script needs

- `request.security(symbol, timeframe, expr)` needs that data at run time: a higher timeframe of
  the chart symbol can be built from the chart bars, other symbols or D/W/M notations need a
  file passed with `--security 'TF=<file>'`. `pyne run <name>.py <data> --list-data` prints what
  is needed before the first real run.
- Backtest on cached data files (`data/<name>.ohlcv`), not on a provider string: a provider
  string re-downloads and overwrites the cache on every run.

## After generation

The generated `scripts/<name>.py` is verified against PyneCore before it is trusted
(`../../pynecore-pine2pyne/SKILL.md`, section 3.3) and then run. Fix findings in the Pine source
and re-convert; the only file you edit is the one in `content/`.
