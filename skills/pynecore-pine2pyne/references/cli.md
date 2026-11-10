# The `pine2pyne` command and the Python API

Verified by running the PyPI wheel of pine2pyne 0.2.11.
The command is the console script of the `pine2pyne` distribution; `python -m pine2pyne` runs
the same `main()`. The transpiler imports nothing from PyneCore and touches no network.

## Synopsis

```
pine2pyne [-h] [-o OUTPUT] [--validate] [--profile {pynecomp,legacy}] [-v] [--version] input [input ...]
```

| Argument / option | Format | Meaning |
|-------------------|--------|---------|
| `input` | One or more paths to Pine source files, or quoted glob patterns (`"src/*.pine"`, `"lib/**/v?.pine"`; `*` and `?` are expanded by pine2pyne with Python's `glob`, so quote them to keep the shell out). Any extension is accepted (`ma.txt` works); the output stem is the input stem | What to transpile. A directory, `-` (stdin is not supported) or a missing file is `Error: <X> is not a file or valid pattern` (exit 1, nothing processed); a pattern without matches is `Error: No input files found` |
| `-o OUTPUT`, `--output OUTPUT` | A file path or a directory path | Where to write. Omitted: the code is printed to stdout (with several inputs, each file's code in turn, then the summary). See the table below |
| `--validate` | flag | Run the parse, repair, semantic checks and warnings, write nothing, print `✓ <file> - Valid syntax`. A pre-check for a batch or a CI hook; the full run's exit code is the verdict on a file |
| `--profile {pynecomp,legacy}` | one of the two names; default `pynecomp` | Output layout: the cloud compiler's (`pynecomp`) or pine2pyne's historical one (`legacy`) |
| `-v`, `--verbose` | flag | Prints `Reading <file>...`, `Transpiling...` (or `Tokenizing...` / `Parsing...` with `--validate`), `Writing to <path>...`, `Skipping <file> (comment-only file)`, and the Python traceback of an unexpected error |
| `--version` | flag | Prints `pine2pyne <version>` and exits 0. **It does not name the distribution**: the abandoned `opencode-pine2pyne` prints the same form (`pine2pyne 0.2.4`). Use `importlib.metadata` or `scripts/check_install.py` to identify the package |
| `-h`, `--help` | flag | Usage and the epilog examples |

## Where the output goes

| Command | Result |
|---------|--------|
| `pine2pyne a.pine` | Code on stdout, nothing written; `pine2pyne a.pine > a.py` gives a clean file (errors go to stderr) |
| `pine2pyne a.pine -o out/a.py` | `out/a.py`; missing parent directories are created; stdout: `✓ a.pine → out/a.py` |
| `pine2pyne a.pine -o existing_dir` | `existing_dir/a.py` (one input, `-o` is an existing directory) |
| `pine2pyne a.pine b.pine -o out/` | `out/a.py`, `out/b.py`; a missing `out` is created as a directory, **even when its name ends in `.py`** |
| `pine2pyne "src/*.pine" -o out/` | One `<stem>.py` per match |
| `pine2pyne a.pine b.pine -o existing_file.py` | `Error: Multiple input files require output to be a directory`, exit 1, nothing processed |

The output name is always `<input stem>.py`; an input with no extension or another extension
keeps its stem. Two inputs with the same stem in one batch overwrite each other.

## Batches

Every input is processed even when one fails; after more than one input a blank line and
`Summary: N succeeded, M failed` are printed on stdout. A file holding only comments and blank
lines counts as succeeded and writes nothing (`-v` says `Skipping ... (comment-only file)`). A
non-comment source for which the transpiler produced an empty program is a failure:
`✗ <file>: transpiler produced no output for non-comment source (unsupported construct
silently dropped)`.

## Streams and exit codes

- stdout: generated code (no `-o`), `✓` lines, the summary, `-v` progress lines.
- stderr: `✗` error lines, `⚠` warning lines, `Error:` input/output errors.
- Exit `0`: every input was transpiled or validated (warnings allowed). Exit `1`: at least one
  input failed, an input is not a file or pattern, no input matched, or several inputs were
  given with `-o` naming an existing file. (After a pipe such as `| tail`, the shell reports the
  last command's status, not pine2pyne's; use `PIPESTATUS` or run without the pipe.)

## Output profiles and headers

`pynecomp` (default) writes the cloud compiler's layout: one `from pynecore.lib import ...`
line (sorted, wrapped at 100 characters), helper imports, the decorator, `def main(` with one
input per line, two blank lines around top-level definitions, and the standalone footer. Its
docstring header names pine2pyne (the cloud compiler's own header is replaced on purpose, so a
pine2pyne file never hashes equal to a cloud-compiled one):

```python
"""
@pyne edge

This code was compiled by pine2pyne 0.2.11 — the Pine Script to PyneCore Python transpiler.
Run with open-source PyneCore: https://pynecore.org
Source: https://github.com/pine2pyne/pine2pyne
"""
from pynecore.lib import close, color, input, plot, script, ta


@script.indicator("My MA", overlay=True)
def main(
    length=input.int(20, "Length")
):
    ma = ta.sma(close, length)
    plot(ma, 'MA', color=color.blue)


if __name__ == "__main__":
    from pynecore.standalone import run
    run(__file__)
```

`legacy` writes `@pyne` (not `@pyne edge`), the header `This code was compiled by Pine2Pyne —
the Pine Script to PyneCore's Python compiler.`, parenthesised multi-line imports, and no
`__main__` footer. Both run on PyneCore; use the default unless the user wants to diff against
old pine2pyne output. (The `--profile` option is absent from the usage line of pine2pyne's
README; `pine2pyne --help` of 0.2.11 shows it.)

## Warnings and the warning filters

Warnings are Python `UserWarning`s of class `pine2pyne.SemanticWarning`, printed by the CLI as
`⚠ <file>:<line>:<col>: <message> [<code>]`; the file is still written and the exit code stays
0. The standard filters apply to the whole process:

```bash
PYTHONWARNINGS=error  pine2pyne s.pine -o s.py   # a warning becomes "Pine refuses: <text> (a warning, made an error by the warning filters) [<code>]", exit 1
PYTHONWARNINGS=ignore pine2pyne s.pine -o s.py   # nothing printed
python -W ignore::pine2pyne.errors.SemanticWarning -m pine2pyne s.pine -o s.py   # only pine2pyne's warnings silenced
```

A refused file reports its error only; its warnings are not printed. The parser also emits
plain Python warnings (not `SemanticWarning`) for a decorator argument given twice or a
positional argument after a keyword argument.

## Python API

```python
from pine2pyne import transpile, Pine2PyneError, SemanticError, SemanticWarning, UnsupportedFeatureError
import warnings

source = open("my_script.pine", encoding="utf-8").read()
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    try:
        code = transpile(source)                      # str; '' for a comment-only source
        # transpile(source, profile="legacy") for the other layout
    except SemanticError as e:                        # a refusal: e.code, e.message, e.line, e.column, e.more (further refusals)
        print(f"{e.code} at {e.line}:{e.column}: {e.message}")
    except UnsupportedFeatureError as e:              # e.feature, e.line, e.column
        print(e)
    except Pine2PyneError as e:                       # LexerError, ParserError, TransformerError, CodeGenError too
        print(e)                                      # 'Error at 34:108: Unexpected token in expression: COMMA'
for w in caught:
    if isinstance(w.message, SemanticWarning):
        print(w.message.code, w.message.line, w.message.column, w.message.text)
```

- `transpile(source, profile=None)`: `profile` is `'pynecomp'` (default), `'legacy'`, or a
  `pine2pyne.OutputProfile` member. Raises a `Pine2PyneError` subclass on failure.
- `pine2pyne.di.get_transpiler(profile)` returns the pipeline object with `.transpile(source)`
  and `.validate(source)`; `validate` is what `--validate` calls (no emit stage).
- Error classes (`pine2pyne.errors`): `Pine2PyneError(message, line=None, column=None,
  source_file=None)` with `str(e)` as `Error at <line>:<column>: <message>`; subclasses
  `LexerError`, `ParserError`, `TransformerError`, `CodeGenError`, `UnsupportedFeatureError`
  (`str(e)` is `Unsupported feature: <feature>`), `SemanticError` (`str(e)` is
  `Pine refuses: <message> [<code>]`, plus ` (and 1 more refusal)` / ` (and 2 more refusals)`
  when `more` is set).
- `pine2pyne.__version__` is the installed version string. The module also exports `Lexer`,
  `Parser`, `Transformer`, `CodeGenerator`, `tokenize`, `parse`, `generate_code`,
  `OutputProfile`, `Transpiler`, `PyneTransformedScript` for tooling that needs the stages.
- A repaired source that still fails raises the first error with the repaired chain's last
  error as `__cause__` (the CLI prints it as `after repairing it as valid Pine, the source
  stops at: ...`).
