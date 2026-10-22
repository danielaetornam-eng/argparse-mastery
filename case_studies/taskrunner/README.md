# Case study: `taskrunner`

A complete, production-shaped argparse CLI, split into the module layout you
would actually ship. Read it after the 37 lessons in `examples/`.

## Why this exists

Lesson 37 puts everything in one file, which is the right shape for reading and the
wrong shape for shipping. This is the same CLI with a real layout:

```
src/taskrunner/
  __init__.py         public API surface
  __main__.py         python -m taskrunner
  cli.py              build_parser() -- the entire command-line surface
  app.py              main() -- the entry point; owns nothing but wiring
  config.py           layered configuration with provenance
  validation.py       cross-argument rules argparse cannot express
  errors.py           TaskRunnerError, carrying an exit code
  exit_codes.py       the frozen exit-code enum
  formatting.py       the composed HelpFormatter
  suggest.py          "did you mean" for flags, commands and task names
  compact.py          a counter-example: a one-line-per-flag formatter
  commands/
    __init__.py       lazy handler dispatch (the plugin seam)
    list_cmd.py
    run_cmd.py
    show_cmd.py
    explain_cmd.py
    config_cmd.py
tests/
  test_cli.py
```

## The four rules the layout enforces

1. **`cli.py` imports no command modules.** Handlers are referenced by
   `("module", "function")` and resolved lazily in `commands/__init__.py`. Adding
   a command is one entry plus one file, and `taskrunner list` does not import what
   `taskrunner run` needs.
2. **Command modules never build a parser.** Each handler is
   `def cmd_x(args: Namespace) -> int` -- a plain function a test can call with a
   synthetic namespace.
3. **Only `app.main` decides how to exit.** `main` returns an int; the entry point
   raises it. It accepts `argv` as a parameter and never reads `sys.argv` itself.
4. **Validation happens before side effects**, in one place that reports every
   violation at once.

## Try it

```bash
# From the repo root, no install needed:
export PYTHONPATH=src:case_studies/taskrunner/src

python -m taskrunner --help
python -m taskrunner list
python -m taskrunner --json list
python -m taskrunner show build
python -m taskrunner run --dry-run build lint
python -m taskrunner explain precedence
python -m taskrunner config show
python -m taskrunner --bash-completion-script
```

Or install it:

```bash
pip install -e case_studies/taskrunner
taskrunner list
```

## A sample config file

`taskrunner.toml` in the working directory is picked up automatically:

```toml
retries = 5
timeout = 2.5
format = "text"
json = false

# Nested tables are flattened, so this sets `dry_run`:
[prefs]
dry_run = true

# This key matches no option and is reported by `config show`
# instead of being silently ignored:
colour = true
```

Verify the layering:

```bash
python -m taskrunner config show            # file layer, with sources
TASKRUNNER_TIMEOUT=99 python -m taskrunner config show   # env beats file
python -m taskrunner --timeout 1 config show            # argv beats both
```

The `retries = 5` value is worth noticing: it arrives from TOML as an **int**, so
argparse's `type=` would *not* run on it. `config.validate_against_parser` runs it
through the parser's own validator anyway. Without that, a `.toml` file would
silently bypass every check you wrote, while the equivalent `.json` file (which
arrives as a string) would not.

## Tests

```bash
python -m pytest case_studies/taskrunner/tests -q
```

The suite demonstrates the three test techniques from `argparse_mastery.testing`:
parse-only assertions, in-process `main(argv)` calls with captured output, and one
subprocess test for the real exit status.

## What to read, in order

1. `cli.py::build_parser` -- the whole surface, and why it imports nothing.
2. `app.py::main` -- the order of operations, and why each step is where it is.
3. `config.py::validate_against_parser` -- the type-conversion trap, closed.
4. `validation.py` -- the four categories of rule, and where each belongs.
5. `commands/__init__.py` -- lazy dispatch, and the plugin seam.