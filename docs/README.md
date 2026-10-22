# Documentation

Prose that complements the runnable material. The **lessons** in `../examples/`
are executable and verified; these documents explain *why*, and carry the tables
and the reference material that does not belong in a runnable script.

## How to read this repository

```bash
export PYTHONPATH=src                 # no install needed
python -m argparse_mastery list        # the lesson index
python -m argparse_mastery show 20     # print one lesson's source
python -m argparse_mastery run 20      # run a lesson's demo
python -m argparse_mastery explain nargs
python -m argparse_mastery cheat       # the one-page cheatsheet
python -m argparse_mastery verify      # run every lesson, report failures
```

## Reading paths

**You have 15 minutes.** Read `01_the_parsing_model.md`, then lessons 1-4, then
`12_cheatsheet.md`. That covers 80% of real argparse use.

**You are writing a CLI now.** Lessons 1-8, 12-13, 15-20, then
`11_pattern_cookbook.md`. Skip the formatter and completion lessons until you
need them.

**You are fixing a confusing bug.** Go straight to `13_pitfalls.md`. Every entry
is a real behaviour, verified on Python 3.14, with the symptom stated first.

**You are building something large.** Read `01`, `05`, `06`, `09`, `11` and then
the case study in `../case_studies/taskrunner/`.

**You want the reference.** `12_cheatsheet.md` and the per-module docstrings in
`../src/argparse_mastery/`.

## The documents

| File | Covers |
|---|---|
| [01_the_parsing_model.md](01_the_parsing_model.md) | What a parser is, the parse loop, `Namespace`, dest derivation, the algorithm underneath |
| [02_actions_and_types.md](02_actions_and_types.md) | Every built-in action; writing `type=`; writing `Action` |
| [03_nargs.md](03_nargs.md) | The complete `nargs` table, the matching regexes, `REMAINDER`, `--` |
| [04_choices_and_groups.md](04_choices_and_groups.md) | `choices`, mutually exclusive groups, and what neither can express |
| [05_subparsers.md](05_subparsers.md) | Subcommands, aliases, nesting, parents, dispatch, help trees |
| [06_custom_actions.md](06_custom_actions.md) | The `Action` protocol in depth, with reusable implementations |
| [07_help_output.md](07_help_output.md) | HelpFormatter, composition, colour, fixed width, custom layouts |
| [08_config_and_env.md](08_config_and_env.md) | Config files, environment variables, precedence, the `type=` trap |
| [09_errors_exits_testing.md](09_errors_exits_testing.md) | `exit_on_error`, `ArgumentError`, exit codes, three test techniques |
| [10_python_314_features.md](10_python_314_features.md) | `suggest_on_error`, `color`, `add_parser(deprecated=True)`, everything else 3.14 added |
| [11_pattern_cookbook.md](11_pattern_cookbook.md) | Worked recipes: 20 complete patterns, each in a few lines |
| [12_cheatsheet.md](12_cheatsheet.md) | One page. Every keyword, every action, every nargs value |
| [13_pitfalls.md](13_pitfalls.md) | 40 verified behaviours that surprise people, symptom first |

## Version notes

Everything here was written and **executed against CPython 3.14.6**. Where a
behaviour differs on 3.11-3.13 it is flagged. The version-sensitive items are:

* `suggest_on_error` -- 3.14+ (lessons 12, 31; `10_python_314_features.md`)
* `color` on parsers and formatters -- 3.14+ (lesson 26)
* `add_parser(deprecated=True)` -- 3.14+ (lesson 34)
* `add_subparsers(help=...)` -- 3.13+
* `tomllib` for TOML config -- 3.11+

`requires-python = ">=3.11"` and the toolkit passes version-sensitive kwargs only
when they exist, so the whole repository runs on 3.11.

## A note on accuracy

Every behavioural claim in these documents was checked by running it. Where this
repository contradicts something widely repeated -- and it does, in five places --
the contradiction is called out explicitly in [13_pitfalls.md](13_pitfalls.md)
rather than quietly corrected. The five:

1. `nargs=(m, n)` is **not supported** by argparse. (click and typer accept it.)
2. Subparser `-h` **works by default**. (The "add_help defaults to False" claim
   is a myth.)
3. Subcommand names are **not abbreviated**. (`prog rem` does not find `remove`.)
4. `action='extend'` **flattens**, `action='append'` does not.
5. `suggest_on_error` covers **only** `choices` violations, and only when every
   choice is a `str`.