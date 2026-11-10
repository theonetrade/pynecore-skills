# Refusals, warnings and unsupported features

Every message below was produced by pine2pyne 0.2.11 on a minimal source; `<name>` stands for
the identifier of the source. Positions are `line:column` of the Pine source.

## Refusals (`Pine refuses: <message> [<code>]`, exit 1, nothing written)

These mirror rules of TradingView's compiler: the same source would not compile on the chart
either. Fix the Pine source; there is no pine2pyne option that lifts them.

| Code | Message | Versions | Fix |
|------|---------|----------|-----|
| `history-target` | `cannot assign with ':=' to a history reference (x[n])` | all | Assign to the variable, not to `x[1]` |
| `reassigned-undeclared` | `'<name>' is not declared before this ':='; TradingView refuses a reassignment of an undeclared variable` | all | Declare with `=` first (`x = 0.0`, then `x := ...`) |
| `duplicate-input` | `'<name>' is already declared in this scope (an input and another declaration cannot share a name)` | all | Rename one of them |
| `discard-read` | `'_' discards a value and cannot be read` | v5, v6 (v4 accepted) | Give the tuple element a name |
| `out-of-scope` | `'<name>' is declared only inside a block or function that has ended here; TradingView reports it as undeclared` | all | Declare the variable before the block, assign inside with `:=` |
| `named-like-namespace` | `an enum cannot be named '<ns>', the name of a built-in namespace` (also for a type, function or variable named `ta`, `math`, `str`, ...) | all | Rename |
| `udt-field-default` | A field default that is not a literal, a constant or a built-in variable (`float x = close` is fine and becomes `_dataclass_field(default_factory=lambda: close)`) | all | Set the field in the constructor call instead |
| `input-argument-reads-input` | `the 'defval' argument of input '<b>' reads the input '<a>'; TradingView requires a const argument there` | all | Use a constant; derive the value after the inputs |
| `nested-function` | `function '<g>' is declared inside a function; TradingView allows a function declaration only at global scope` | all | Move the function to the top level |
| `global-modified-in-function` | `a function cannot modify the global variable '<g>'` | all | Return the value and assign it at global scope, or use `var` arrays / objects |
| `parameter-modified` | `a function cannot modify its parameter '<x>'` | all | Copy to a local first (`y = x`) |
| `double-history` | `the [] history operator can be used only once on the same value (close[1][2])` | all | `close[3]`, or assign `close[1]` to a variable and index that |
| `history-on-udt-field` | `the [] history operator cannot be used on a field of a user-defined type from Pine v6; write (t[1]).x for the field of the object 1 bar(s) back, or assign the field to a variable and index that variable` | v6 only (v5 accepted) | As the message says |
| `loop-counter-history` | `Variable "<i>" doesn't have history values` | v6 only (v5 accepted) | Keep the previous value in a variable instead of `i[1]` |
| `recursion` | `function '<f>' calls itself (directly or through another function); TradingView does not allow recursion` | all | Rewrite as a loop |

When several rules fire, the first is reported with ` (and N more refusal(s))`; the
`SemanticError.more` list holds the rest.

## Warnings (`⚠ <file>:<line>:<col>: <message> [<code>]`, file written, exit 0)

| Code | Message | What it means |
|------|---------|---------------|
| `conditional-history-call` | `'<ta.fn>' is called only when its condition holds: its series history differs from an unconditional call (the output keeps the call where it is, as TradingView does; TradingView advises calling it on every bar, in the global scope)` | A `ta.*` (or other stateful) call inside `if`, a ternary branch or a function called conditionally. Its internal series advances only on the bars where it runs, on TradingView and in PyneCore alike. Leave it when the Pine author relied on that; hoist it to global scope when they expected an every-bar series. The most common warning: 2265 occurrences in 339 of the 1005 corpus files |
| `redeclared` | `'<name>' is already declared in this scope; TradingView refuses a redeclaration, the later one is used here` | Two `x = ...` in one scope; the second wins |
| `used-before-declaration` | `'<name>' is read before its declaration` | Reads `na` (or fails) on TradingView |
| `self-reference` | `'<name>' is read in its own declaration` | `y = y[1] + 1` style; TradingView refuses it |

`PYTHONWARNINGS=error` turns any of them into `Pine refuses: <message> (a warning, made an
error by the warning filters) [<code>]` with exit 1. A refused file prints its error only, not
its warnings.

## Unsupported features (`Unsupported feature: <text>`, exit 1)

Valid Pine that pine2pyne cannot express. Most are raised at emit time, so **`--validate` does
not report them**; only a full run does.

| Text | Rewrite |
|------|---------|
| `a history index on an expression in <position> (assign the expression to a variable first)` | `v = f(x)` then `v[1]` |
| `the tuple function <name> has an if/switch tail without else, and its tuple width is unknown (give it an else that returns the tuple)` | Add the `else` branch returning a tuple of the same width |
| `<Type> as the value of an if/switch expression branch` | Assign the object in the branch and return a plain value |
| `the default of source input '<a>' reads the input '<b>'; a default that follows another input's setting has no PyneCore equivalent` | Give `input.source` a built-in default (`close`) |
| `import <user>/<Lib>/<N>: "<Lib>" is not a Python module name` (a library name that is not an identifier or is a Python keyword, e.g. `class`) | The library cannot be imported under that name; rename the library module and the import |
| `import ... as <alias>: "<alias>" is a Python keyword` | Choose another alias |
| `two imports of the library "<Lib>" differ in user or version ...` | Import one version of a library name |
| `export function ... would be emitted as ...` | A library export whose name collides in Python; rename the function |
| `assignment with <op> ...` | A compound assignment on a target the emitter cannot write; expand it (`x := x op y`) |
| `TransformerError: <subject> uses '<name>', which is not a constant declared at the top level; ...` | A decorator / input argument that reads a non-constant; use a literal or top-level constant |
| `TransformerError: array.new<T>: 'T' is not a type the script declares or imports, nor a built-in type` | Declare the type or fix the spelling |
| `CodeGenError: Unsupported statement type` / `Unsupported expression type` / `the generated code is not valid Python: ...` | Reduce the source to the construct and report it to <https://github.com/pine2pyne/pine2pyne/issues> |

A library alias equal to a built-in namespace is **not** an error: `import TradingView/ta/7 as
ta` is emitted as `import lib.TradingView.ta.v7 as ta__ren____lib` plus
`ta__ren__ = shadowed_namespace(ta__ren____lib, ta)` (from `pynecore.core.pine_import`), so both
the library and the built-in stay reachable.

## Syntax errors and the parse repairs

A syntax error reads `Error at <line>:<col>: <text>` (`Unexpected token in expression: COMMA`,
`Unclosed string`, ...). Before giving up, pine2pyne retries with repairs that TradingView's
editor tolerates: a non-breaking space (`\xa0`) as whitespace, CRLF inside strings, a unary
`+x`, `.99` written for `0.99`, `a = 1, b = 2` on one top-level line, a wrapped line that
starts with an operator (joined to the previous line), and a one-line assignment after `=>`
in a `switch` arm. When the repaired source fails too, the original error is printed first and
the repaired chain's last error after it:

```
✗ ict.pine: Error at 34:108: Unexpected token in expression: COMMA
  after repairing it as valid Pine, the source stops at: Error at 138:59: Unexpected token in expression: COMMA
```

Read the second position as the real location when the first one looks fine.

## Other failure lines

| Line | Cause |
|------|-------|
| `✗ <file>: transpiler produced no output for non-comment source (unsupported construct silently dropped)` | The program came out empty although the source has code; bisect the source, report upstream |
| `✗ <file>: File not found` | The file vanished between listing and reading |
| `✗ <file>: Unexpected error: <text>` | An internal exception; `-v` prints the traceback to attach to a bug report |
| `Error: <X> is not a file or valid pattern` | A directory, `-`, or a missing path as input; nothing is processed |
| `Error: No input files found` | A pattern matched nothing |
| `Error: Multiple input files require output to be a directory` | `-o` is an existing file and there are several inputs |
