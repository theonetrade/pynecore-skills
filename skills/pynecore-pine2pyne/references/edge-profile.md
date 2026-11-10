# The `@pyne edge` header: what it promises, what may be written under it

pine2pyne writes `@pyne edge` at the top of every generated file, as the cloud compiler does. The
word is not a label: it puts the file under a **restricted, fail-closed language profile** that
PyneCore's import hook gates and PyneIDE's Edge linter checks. A file that keeps the header must
stay inside the profile, hand-written additions included. This page is the profile as shipped
with PyneCore 6.13.2 (`pynecore/transformers/pine_edge_gate.py`, `edge_rules.json`, revision
**2026.11.1**) and PyneIDE 0.14.1 (`python/pyneide_edge_rules.py`, `python/pyneide_series.py`),
with the 6.12.1 differences (revision 2026.10.2); every statement below was run on
2026-11-10. The PyneCore docs describe the profile in `overview/pyne-code.md` ("What does
`edge` mean?"), `reference/script-format.md` ("The `edge` Variant") and
`advanced/ast-transformations.md` (the gate's place in the pipeline).

## 1. Three headers, one runtime

| Header | Who writes it | What PyneCore does | What PyneIDE does |
|--------|---------------|--------------------|-------------------|
| `@pyne` | A hand-written script | The Pyne transforms; every Python construct the runtime can execute is accepted; typed diagnostics are collected but never raise | Recognised as a Pyne script (Explorer badge `@pyne`, Pyne workspace tree, Run / Debug Pyne code, the Pyne structure checker). The Edge linter is **off**; the file is outside the web/bot contract |
| `@pyne edge` | The cloud compiler, pine2pyne's default profile, a hand-written script that wants the contract | The same transforms **plus the edge gate** on the tree as written; its findings join the typed diagnostics; `PYNE_EDGE_STRICT=1` turns the first one into an error | Badge `@pyne edge`; the Edge linter runs (`pyne-edge-*` diagnostics, fail-closed) |
| `@pyne lib` | PyneCore's own builtin machines (`pynecore/lib/*.py`) | Series semantics of a builtin machine; not gated | Badge `@pyne lib`; `main()` not required |

How the word is read. PyneCore (`core/import_hook.py`, `_module_mode`): the module's first
statement must be a string, matching `\s*@pyne(?:[ \t]+(?P<mode>\w+))?(\s|$)`. PyneIDE
(`pyneDetect.ts`, mirrored in `pyneide_series.py`): the first 4096 bytes, leading `#` comment
lines allowed, then the opening quotes, optional whitespace, `@pyne`, spaces or tabs, `edge`,
then whitespace, the closing quote or the end. pine2pyne's header
(`"""`, newline, `@pyne edge`, newline, the three header lines, `"""`) matches both; so does
`"""@pyne edge"""` on one line. `pine2pyne --profile legacy` writes plain `@pyne`: a legacy
file is not gated.

## 2. What `edge` buys, and why the header stays

- PyneCore's gate: "An edge module is a promise: everything in it is Pine, so it runs on the
  fast path, compiles ahead of time and runs on the web." PyneIDE's rules file: Edge scripts
  "are guaranteed to run on constrained runtimes (the web/WASM executor, a possible future
  static compiler) in addition to normal CPython/pynecore"; "the linter is FAIL-CLOSED: anything
  this module does not explicitly allow is an error."
- PyneIDE's own quick-fix on an edge violation is named **"Convert to full @pyne (removes
  web/bot compatibility)"**: it deletes the word. That is the cost of leaving the profile, in
  the IDE's words.
- The allowed set is "what the pynecomp emitter can produce" (PyneIDE's rules file: an AST
  census over 140 compiled scripts), plus a few hand-curated extras. pine2pyne reproduces the
  cloud compiler's layout, so its output passes the gate with **0 findings**: verified for an
  indicator, a strategy with `if`-values, loops and `Persistent` state, a library with
  `@export` and its `Protocol` shims, a `@udt` type, and a closure reading history.
- The docs: "Removing `edge` therefore changes the program's language-profile contract, not just
  its label"; "such a program must not be assumed portable to a runtime or compiler that
  supports only edge code." Keep the header on every generated file and on every file ported
  by hand from Pine; write the hand-written part inside the profile (section 6).

## 3. How the gate runs, and why a second run shows nothing

- **When**: at import, in `_analyse_tree`, on the tree **as written** (before the transforms
  inject state parameters, security reads and temporaries), only when the mode word is `edge`.
  The structural findings are merged with the typed diagnostics of the analysis half.
- **Output**: nothing by default. `PYNE_TYPE_DIAG=1` prints every diagnostic of every module
  (any header) to stderr as `<path>:<line>:<col>: <message> [<reason>@<line>:<col> <node>] fix:
  <hint>`. `PYNE_EDGE_STRICT=1` raises `PineTypeError` on the first diagnostic of an edge module
  (`pyne run` exits 1 with the message, e.g. `PineTypeError: importing 'json' is not Pine --
  only the PyneCore API and lib.* libraries are available`); a plain `@pyne` module keeps running
  and keeps the list. Both switches are off by default, so **a script that runs is not proof
  that it meets the profile** (the docs say the same).
- **The cache trap**: PyneCore stores the transformed module in
  `scripts/__pycache__/<name>.cpython-3xx.pyc`, keyed by the source and the pipeline hash, not
  by these environment variables. A second run of an unchanged file loads the cache, skips the
  transform and prints **nothing**, whatever the variables say (verified: four lines on the
  first run, zero on the next). Before a check, delete `scripts/__pycache__` or edit the file,
  or use the two cache-free routes below.
- **`pyne debug ast`** transforms without the cache: `PYNE_TYPE_DIAG=1 pyne --workdir <wd>
  debug ast <name>.py` prints both halves (structural and typed) and then the transformed code;
  `PYNE_EDGE_STRICT` does not raise there.
- **`scripts/edge_check.py <file>...`** runs the structural half through the interpreter of the
  project's PyneCore (`--root`, `--python`), prints the profile revision that PyneCore carries,
  the header mode of each file and every finding in the gate's format; exit 1 on a finding. A
  file without the `edge` word is gated anyway, so a hand-written script can be checked before
  the word is added. The typed half (`unknown-call`, `unknown-name`, ...) needs `debug ast`.
- Reason codes. Runtime: `edge-syntax`, `edge-import`, `edge-decorator`, `edge-class`,
  `edge-name`, `edge-call`, `edge-lambda`, `edge-assert`, `edge-subscript`, `edge-builtin`,
  `edge-attr-store`. PyneIDE: the same with the prefix `pyne-edge-` (`pyne-edge-func-attr` for
  the attribute store). Every message ends in "is not Pine" and carries a fix hint.

## 4. The profile, revision 2026.11.1 (fail-closed: what is not listed is an error)

**Statements and expressions.** Allowed node types: `Module`, `FunctionDef`, `ClassDef`,
`Import`, `ImportFrom`, `Assign`, `AnnAssign`, `AugAssign`, `Expr`, `Return`, `Pass`, `If`,
`IfExp`, `For`, `While`, `Break`, `Continue`, `Assert`, `BinOp`, `BoolOp`, `UnaryOp`,
`Compare`, `Call`, `Attribute`, `Subscript` (read only), `Name`, `Constant`, `Tuple`, `Lambda`
(one place, below), `arg`, `keyword`, `alias`. Rejected, with the message the gate prints:

| Construct | Message | Pine form |
|-----------|---------|-----------|
| `[a, b]`, `{k: v}`, `{a, b}` | a list / dict / set literal is not Pine | `array.from_items(a, b)` (Pine's `array.from`), `array.new_float(n, v)`, `map.new()`; the one allowed list is `__all__ = ['f', ...]` of string constants at module level |
| `[f(x) for x in ...]`, `{...}` comprehensions, `(x for x in ...)` | a list / set / dict comprehension, a generator expression is not Pine | a `for` loop with an accumulator or `array.*` calls |
| `f"{x}"` | an f-string is not Pine | `str.format("{0}", x)`, `str.tostring(x)`, `+` |
| `(y := f(x))`, `*args` in a call, `arr[1:3]` | a `:=` assignment, a starred expression, a slice is not Pine | assign first; pass the arguments one by one; `array.slice` |
| `try` / `except` / `raise`, `with`, `del`, `global`, `nonlocal`, `match`, `yield`, `async` / `await` | 'try', 'raise', 'with', ... is not Pine | `na(x)` / `nz(x, d)` checks (PyneCore turns division by zero into `na`); `Persistent` state at `main()` level instead of `global` |
| `for ... else`, `while ... else` | a loop's else clause is not Pine | put the code after the loop |
| `a < b < c` | a chained comparison is not Pine | `a < b and b < c` |
| `f(**kw)` | keyword unpacking is not Pine | pass the arguments one by one |
| `arr[i] = v` | subscript assignment is not Pine | `array.set(arr, i, v)` |
| `assert cond, f"..."` | an assert message other than a string literal is not Pine | `assert cond, "text"` (2026.09.1: `assert` itself is allowed) |

**Operators.** Allowed: `+ - * / // % **`, unary `- + not`, `and or`, `== != < <= > >= in not
in` (`in` since 2026.09.2, membership in a tuple of same-typed constants). Rejected: `& | ^ <<
>> ~ @`, `is`, `is not`.

**Imports.** Any `import` / `from ... import` whose module starts with `pynecore` or `lib`
(`import lib.jdehorty.KernelFunctions.v2 as kernels`, `from pynecore.lib import close, ta`,
`from pynecore import pine_range`, `from pynecore.core.pine_udt import udt`); `from dataclasses
import dataclass, field`; `from typing import Protocol, Any`; `from __future__ import
annotations`. Rejected: a relative import, a star import (`from pynecore.lib import *`, since
2026.10.1), every other module (`import json`, `import math`, `import numpy`: "only the
PyneCore API and lib.* libraries are available"). Pine's `math.*`, `str.*`, `array.*`,
`map.*`, `matrix.*` live in `pynecore.lib`.

**Calls.** A bare name may be called only when it is a function the module defines, a name the
module imports, or one of eleven builtins: `abs`, `bool`, `enumerate`, `float`, `int`, `len`,
`max`, `min`, `print`, `range`, `str`. Everything else is "calling 'x' is not Pine": `round`
(removed in 2026.11.1: Python rounds half to even and returns `int`, Pine's `math.round` rounds
half away from zero), `sorted`, `sum`, `any`, `all`, `list`, `dict`, `set`, `tuple`,
`isinstance`, `type`, `getattr` / `setattr` / `hasattr`, `open`, `zip`, `map`, `filter`,
`reversed`, `exec` / `eval`. Attribute chains are fine (`ta.sma(...)`, `kernels.f(...)`,
`obj.method()`); calling a computed expression (`f()()`, `(a or b)()`) is not.

**Names.** `input` and `open` are Python builtins too: read without `from pynecore.lib import
input` / `open` (or a module-wide lib import, or the script's own binding) they resolve to
Python's and are reported as `edge-builtin` with the import to add (2026.10.1). Double-underscore
names belong to the pipeline and are rejected, except `__all__`, `__name__`, `__file__`,
`__call__` and the compiler's temporaries: `__block_result__` / `__block_result_<n>__`,
`__block_keep_<n>__`, `__switch__` / `__switch_<n>__`, `__loop_<n>__`, `__input_<n>__`,
`__var_init_<n>__`, `__hist_<name>__`. Assigning an attribute on a function or a module (`f.x =
1`) is "functions and modules are not objects".

**Functions.** Plain positional parameters with defaults only: no `*args`, `**kwargs`,
keyword-only or positional-only parameters. Decorators: a *called* `@script.indicator(...)` /
`@script.strategy(...)` / `@script.library(...)` on `main`, and the bare names `@method`,
`@export`, `@overload` imported from `pynecore.core.*`. Any other decorator (`@staticmethod`,
`@functools.cache`, `@property`) is rejected.

**Classes.** A class is Pine's `type`: exactly one decorator, `@udt` (or `@dataclass`), no base
class and no keywords; the body holds a docstring, annotated fields (`x: float = 0.0`), `pass`,
and methods written as undecorated `def` with the receiver first (`def midpoint(self)`, since
2026.10.2). `lambda` is allowed in one place only: `field(default_factory=lambda: ...)` as a
field default. Inheritance, `@staticmethod` / `@classmethod`, class-level statements other than
fields and methods are out. One exception for libraries: `class _ProtocolF(Protocol)` with
ellipsis-body `__call__` declarations, the typing shim the compiler emits for each export.

Everything Python offers beyond this list (exceptions, context managers, comprehensions,
inheritance, external packages, the rest of the builtins) belongs to plain `@pyne` only, and
with it the file leaves the web/bot contract.

## 5. Profile revisions, and what they mean for pine2pyne output

| Revision | Change | Ships in |
|----------|--------|----------|
| 2026.09.1 | `assert` allowed (string-literal message) | |
| 2026.09.2 | `in` / `not in` allowed | |
| 2026.10.1 | `open` / `input` read without the lib import is an error; a star import is an error | |
| 2026.10.2 | Methods may be declared inside a `@udt` class body | PyneCore 6.12.1 (`edge_rules.json`) |
| 2026.11.1 | Python's `round()` no longer allowed (use `math.round`); the temporaries `__var_init_<n>__` and `__hist_<name>__` recognised | PyneCore 6.13.2, PyneIDE 0.14.1 |

Both PyneCore and PyneIDE carry their own copy of the rules, pinned by revision, so the
profile a file is judged by is the one of the PyneCore the project runs and of the PyneIDE
installed. Verified consequence for pine2pyne 0.2.11: when a nested function reads the history
of a `main()` variable (`f() => avg[1]`), pine2pyne emits `__hist_avg__: Series = avg[0]`. On
PyneCore 6.13.2 the file has 0 findings; on 6.12.1 (revision 2026.10.2) the gate reports `the
name '__hist_avg__' is not Pine [edge-name]` twice, the file still runs by default and fails
under `PYNE_EDGE_STRICT=1`. `scripts/edge_check.py` prints the revision the project's PyneCore
carries; a project on 6.12.x that converts such sources should move to PyneCore 6.13.2 (with
`typer<0.29`, see SKILL.md section 2). pine2pyne itself never calls Python's `round`: Pine's
`round` becomes `math.round`.

## 6. Porting by hand inside the profile

When pine2pyne refuses a construct and the missing part is written by hand (SKILL.md 3.2),
write it as Pine would be written in Python, not as Python:

| Habit | Inside the profile |
|-------|--------------------|
| `vals = [1, 2, 3]`, `vals.append(x)`, `vals[0] = y`, `vals[1:]` | `vals = array.from_items(1, 2, 3)` / `array.new_float(0)`, `array.push(vals, x)`, `array.set(vals, 0, y)`, `array.get(vals, 0)`, `array.slice(vals, 1, array.size(vals))` |
| `d = {}`; `d[k] = v`; `k in d` | `d = map.new()`, `map.put(d, k, v)`, `map.contains(d, k)`, `map.get(d, k)` |
| `sum(xs)`, `max(xs)`, `sorted(xs)`, `any(...)` | `array.sum(xs)`, `array.max(xs)`, `array.sort(xs)`, a `for` loop with a flag (`max(a, b)` on scalars is allowed) |
| `f"RSI {r:.2f}"`, `"%d" % n`, `"{}".format(x)` | `str.format("RSI {0}", r)`, `str.tostring(r, "#.##")`, `"a" + str.tostring(n)` |
| `try: x = a / b except ZeroDivisionError: x = 0` | `x = a / b` is `na` for `b == 0` in PyneCore; `nz(a / b, 0)` |
| `round(x)`, `round(x, 2)` | `math.round(x)`, `math.round(x, 2)` |
| `import math`, `math.sqrt(x)` | `from pynecore.lib import math`, `math.sqrt(x)`; `statistics` / `numpy` have no place, use `ta.*` and `array.*` |
| `global total` inside a helper | keep the state in `main()` as `Persistent[T]` / `var` and pass values as arguments, or return them |
| `a < b < c` | `a < b and b < c` |
| `lambda x: x * 2` | `def twice(x): return x * 2` |
| `class Box(Base):` with `@staticmethod` | `@udt class Box:` with annotated fields and receiver-first methods, no inheritance |
| `isinstance(x, float)`, `getattr(obj, name)` | not expressible; model the cases as UDT fields or separate variables |
| `print(...)` for debugging | allowed; `log.info(...)` is the Pine way and lands in the run log |
| `open` / `input` read as Pine builtins | add them to the `from pynecore.lib import ...` line |

## 7. Checklist for a file that was edited by hand

1. The first statement is still the docstring `"""` / `@pyne edge` / `"""` that pine2pyne wrote
   (or `"""@pyne edge"""` for a file written from scratch).
2. `python3 <skill>/scripts/edge_check.py scripts/<name>.py --root <project>` prints `0 findings`
   for the file and names the revision of the project's PyneCore; the project's PyneIDE should be
   on the same revision (0.14.1 is 2026.11.1).
3. `PYNE_TYPE_DIAG=1 pyne --workdir <wd> debug ast <name>.py` shows no `unknown-call` /
   `unknown-name` line about the hand-written part.
4. `rm -rf <wd>/scripts/__pycache__ && PYNE_EDGE_STRICT=1 pyne --workdir <wd> run <name>.py demo`
   completes.
5. In PyneIDE the Problems panel shows no `pyne-edge-*` entry for the file; the quick-fix
   "Convert to full @pyne" is not what a ported Pine script wants.
6. The report names the constructs that were rewritten into Pine form and the profile revision
   the file was checked against.
