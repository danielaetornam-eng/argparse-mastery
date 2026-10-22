# argparse-mastery

**A codebase for learning `argparse` by reading it.** Thirty-seven annotated,
runnable lessons; a reusable toolkit built from them; a production-shaped case
study; and full prose documentation.

Every behavioural claim in this repository was **executed** against CPython
3.14.6. Where the material contradicts something widely repeated, it says so
explicitly rather than quietly repeating the myth — there are
[ten of them](#corrections), and they are the most valuable part.

```bash
export PYTHONPATH=src                    # no install required
python -m argparse_mastery list           # the lesson index
python -m argparse_mastery show 20        # print one lesson's source
python -m argparse_mastery run 20         # run a lesson's demo
python -m argparse_mastery explain nargs  # prose on one feature
python -m argparse_mastery cheat          # the one-page cheatsheet
python -m argparse_mastery verify         # run every lesson, report failures
```

Prefer `pip install -e .` if you have setuptools; then the `argparse-mastery`
command works from anywhere.

---

## Why this exists

`argparse` is the most-used and least-read module in the Python standard
library. Its documentation is a reference manual: it tells you every parameter
exists and none of them why. The result is a generation of CLIs that share the
same dozen bugs — leaked file handles, a `nargs` range that doesn't exist, config
files that silently bypass validation.

This repository is organised around the questions the manual doesn't answer:

- **Why does `--dry-run` become `dry_run`?** Because dest is derived from the first
  long option. Lesson 01.
- **Why does my config file skip my validation?** Because argparse converts a
  default only when it is a `str`. Lessons 03, 29.
- **What can a mutually exclusive group *not* do?** "At least one", "exactly two
  of four", "A requires B". Lessons 13, 14.
- **Why can't I hide a flag from `--help`?** You can, and it is `help=SUPPRESS` —
  the claim that it leaves the usage line dirty is wrong. Lessons 06, 25.
- **How do I know *where* an option's value came from?** Keep the layers and merge
  them with provenance. `argparse_mastery.config`.

## Layout

```
argparse-mastery/
  README.md                  this file
  pyproject.toml             packaging, ruff and pytest config
  conftest.py                makes src/ importable with no install
  src/argparse_mastery/      the toolkit -- every module is a worked example
    types.py                 custom type= callables
    actions.py               custom Action subclasses
    formatters.py            help output
    suggest.py               "did you mean" for flags and subcommands
    errors.py                error handling, exit codes, soft failures
    config.py                config file + environment layering
    builders.py              declarative, data-driven parsers
    completion.py            shell completion from introspection
    logging_setup.py         verbosity flags -> log level
    testing.py               capturing stdout/stderr and exit codes in tests
    cli.py                   the dispatch contract and main() shape
    __main__.py              the reading CLI (itself an argparse showcase)
  examples/                  37 numbered lessons, each runnable
  docs/                      14 prose documents
  case_studies/taskrunner/   a production CLI in a real module layout
  tests/                     164 tests for the toolkit
  case_studies/taskrunner/tests/   70 tests for the case study
```

## The 37 lessons

| Part | Lessons | Covers |
|---|---|---|
| **A. The parsing model** | 1–7 | `Namespace`, dest derivation, `type` vs `default`, positionals, short/long options, `metavar`/`dest`/`SUPPRESS`, every built-in action |
| **B. nargs** | 8–11 | `?` and `const`, `*` vs `+`, `REMAINDER` and `--`, exact counts |
| **C. Constraints** | 12–14 | `choices`, mutually exclusive groups, option dependencies |
| **D. Subparsers** | 15–19 | dispatch, aliases, nesting, parent parsers, defaults |
| **E. Validation** | 20–22 | custom `type=`, bounded and cross-field, `FileType` |
| **F. Custom actions** | 23–25 | the `Action` protocol, composite actions, help hooks |
| **G. Help output** | 26–27 | formatters and composition, custom layouts |
| **H. Config & env** | 28–30 | `fromfile_prefix_chars`, config layering, env defaults |
| **I. Errors & exits** | 31–32 | `exit_on_error`, `ArgumentError`, exit codes as an API |
| **J. Packaging** | 33–37 | `prog`/`usage`/width, deprecation, `parse_known_args`, completion, capstone |

Each lesson is one file with:
- a module docstring that explains the *why*, including the traps;
- runnable `build_parser()` / `main(argv)` so you can experiment;
- a `--demo` mode that runs itself and prints annotated output.

```bash
python examples/20_custom_types.py --demo
python examples/20_custom_types.py --port 8080 --size 10MB --timeout 1h30m
```

## Documentation

| File | Covers |
|---|---|
| [`docs/README.md`](docs/README.md) | index and reading paths |
| [`docs/01_the_parsing_model.md`](docs/01_the_parsing_model.md) | what a parser is, the parse loop, the algorithm underneath |
| [`docs/02_actions_and_types.md`](docs/02_actions_and_types.md) | every built-in action; writing `type=`; writing `Action` |
| [`docs/03_nargs.md`](docs/03_nargs.md) | the complete table, the matching regexes, `REMAINDER`, `--` |
| [`docs/04_choices_and_groups.md`](docs/04_choices_and_groups.md) | `choices`, groups, and what neither can express |
| [`docs/05_subparsers.md`](docs/05_subparsers.md) | commands, aliases, nesting, parents, dispatch, help trees |
| [`docs/06_custom_actions.md`](docs/06_custom_actions.md) | the `Action` protocol in depth |
| [`docs/07_help_output.md`](docs/07_help_output.md) | formatters, composition, colour, width, custom layouts |
| [`docs/08_config_and_env.md`](docs/08_config_and_env.md) | config files, env vars, precedence, the `type=` trap |
| [`docs/09_errors_exits_testing.md`](docs/09_errors_exits_testing.md) | `exit_on_error`, exit codes, three test techniques |
| [`docs/10_python_314_features.md`](docs/10_python_314_features.md) | `suggest_on_error`, `color`, `deprecated=`, `FileType` |
| [`docs/11_pattern_cookbook.md`](docs/11_pattern_cookbook.md) | 20 complete recipes, each a few lines |
| [`docs/12_cheatsheet.md`](docs/12_cheatsheet.md) | one page: every keyword, action, nargs value |
| [`docs/13_pitfalls.md`](docs/13_pitfalls.md) | 43 verified behaviours that surprise people, symptom first |

## Reading paths

**15 minutes.** `docs/01`, then lessons 1–4, then `docs/12_cheatsheet.md`. That is
80% of real argparse use.

**Writing a CLI now.** Lessons 1–8, 12–13, 15–20, then
`docs/11_pattern_cookbook.md`. Skip the formatter and completion lessons until you
need them.

**Fixing a confusing bug.** Go straight to `docs/13_pitfalls.md`. Every entry is
symptom → cause → fix, and all 43 were executed.

**Building something large.** `docs/01`, `docs/05`, `docs/06`, `docs/09`, then the
case study.

## Corrections

The most useful thing here is what it says is *wrong*. All ten were verified:

| Claim | Reality on CPython 3.14.6 |
|---|---|
| `nargs=(m, n)` works | **No** — `ValueError: invalid nargs value`. click/typer accept it; argparse does not |
| Subparser `-h` needs `add_help=True` | **No** — it works by default |
| `allow_abbrev` shortens subcommands | **No** — `choices` is an exact match |
| `suggest_on_error` improves all errors | **No** — only `choices`, and only when every choice is a `str` |
| `help=SUPPRESS` leaves the usage line | **No** — it does not |
| `action.format_help()` is called by the formatters | **No** — zero call sites in argparse |
| `count` with `default=None` raises `TypeError` | **No** — 3.14 guards it |
| `add_argument(deprecated=...)` does not exist | **No** — it does, in 3.14 |
| `parser.aliases` holds subcommand aliases | **No** — `add_parser` never sets it |
| `HelpFormatter._format_section` exists | **No** — removed; use `start_section` |

Details and the working substitutes are in
[`docs/13_pitfalls.md`](docs/13_pitfalls.md) and lesson 25.

## The toolkit

Everything the lessons build, extracted into an importable package with **zero
runtime dependencies**:

```python
import argparse
from argparse_mastery import (
    FriendlyArgumentParser,      # errors + "did you mean" + custom exit codes
    bounded_int, duration,       # type= callables
    register_all,                # the actions, by string name
    parse_or_raise,              # parse without exiting, ever
    set_defaults_from_config,    # safe config layering
    add_completion_arguments,    # shell completion flags
    run_cli, assert_ok,          # testing helpers
)

parser = FriendlyArgumentParser(prog="myapp", description="...")
parser.add_argument("--port", type=bounded_int(1, 65535, unit="port"), default=8080)
parser.add_argument("--tag", action="append_unique", default=[])
args = parse_or_raise(parser)   # raises ParseFailure; never SystemExit
```

## The case study

`case_studies/taskrunner/` is lesson 37 split into the module layout you would
actually ship: parser, config, validation, errors, exit codes, formatting, lazy
command dispatch, and a test suite in three techniques.

```bash
export PYTHONPATH=src:case_studies/taskrunner/src
python -m taskrunner --help
python -m taskrunner list
python -m taskrunner --json run --dry-run build lint
python -m taskrunner config show
```

Its README explains the four structural rules the layout enforces.

## Tests

```bash
python -m pytest -q                      # 234 tests
python -m argparse_mastery verify        # every lesson runs and exits 0
python -m ruff check .                   # lint (if ruff is installed)
```

The suite asserts the documented claims, so if a future Python changes one of the
behaviours above, the failure says exactly which claim rotted.

## Requirements

Python 3.11+. Three features are used conditionally so the whole repository runs on
3.11–3.13: `suggest_on_error` and `color` (3.14+), `add_subparsers(help=...)`
(3.13+), and `tomllib` (3.11+). `requires-python = ">=3.11"`.

`pytest` is needed only for the tests. The lessons and the toolkit need nothing
beyond the standard library — which is the point.

## Licence

MIT. The code is here to be read, copied and adapted.