# The 37 lessons

Each file is standalone and runnable. Read them in order the first time; after
that use them as reference.

```bash
python examples/<file>.py --help     # what the lesson's parser accepts
python examples/<file>.py --demo     # the lesson explains itself
```

The list is also available from the meta-CLI:

```bash
python -m argparse_mastery list
python -m argparse_mastery show 20
python -m argparse_mastery run 20
```

---

## Part A — The parsing model

| # | File | What it teaches |
|---|---|---|
| 01 | `01_hello_world.py` | The three steps, and how `dest` is derived from the first long option |
| 02 | `02_flags_store_true_false.py` | `store_true`, `store_false`, `count`, `store_const`, `BooleanOptionalAction`, and why two-state booleans are a bug waiting to happen |
| 03 | `03_types_and_defaults.py` | `type=` converts, `default=` supplies; the asymmetry that bites in config files; `default=SUPPRESS` |
| 04 | `04_positionals.py` | Declaration-order matching, why two optional positionals are ambiguous, and that `required` is illegal on a positional |
| 05 | `05_short_and_long_options.py` | `-vv` is not "verbose twice", negative numbers need `=`, abbreviations, `prefix_chars` |
| 06 | `06_metavar_and_dest.py` | `metavar` changes the placeholder, `dest` the attribute, and `help=SUPPRESS` hides from both |
| 07 | `07_builtin_actions.py` | Every stock action side by side, the `append` mutable-default trap, and `set_const` shorthands |

## Part B — nargs

| # | File | What it teaches |
|---|---|---|
| 08 | `08_nargs_optional.py` | `nargs='?'` and the `const` trap; three states from one argument |
| 09 | `09_nargs_star_plus.py` | `'*'` vs `'+'`, the `default=""` trap, and why `--` only helps positionals |
| 10 | `10_nargs_remainder_and_double_dash.py` | `REMAINDER`, the wrapper-tool recipe, `parse_known_args`, and why `except SystemExit` is wrong |
| 11 | `11_nargs_exact_and_shapes.py` | `nargs=N`, and that `nargs=(m, n)` does not exist in argparse |

## Part C — Constraints

| # | File | What it teaches |
|---|---|---|
| 12 | `12_choices.py` | `choices` is `value in choices`; four ways to do it better; what 3.14's `suggest_on_error` actually covers |
| 13 | `13_mutually_exclusive_groups.py` | At most one, exactly one, and the four things groups cannot express |
| 14 | `14_dependent_and_conditional_requirements.py` | "A requires B", cardinality > 1, and reporting every violation at once |

## Part D — Subparsers

| # | File | What it teaches |
|---|---|---|
| 15 | `15_subparsers_basics.py` | The `set_defaults(func=...)` dispatch contract; the parent-option ordering constraint |
| 16 | `16_subparsers_help_and_aliases.py` | `help=` vs `description=`, aliases, and why `parser.aliases` does not exist |
| 17 | `17_nested_subparsers.py` | Two levels of subcommands, the help-tree cost, and when flat names win |
| 18 | `18_parser_parents.py` | Sharing options without duplication; why the parent must be a factory |
| 19 | `19_subcommand_defaults.py` | Per-command defaults, `required=True`, the no-command case, and a default command |

## Part E — Validation

| # | File | What it teaches |
|---|---|---|
| 20 | `20_custom_types.py` | The `type=` contract, six style rules, and why parameterised validators must be factories |
| 21 | `21_bounded_and_cross_field.py` | Bounded numbers, the `NaN` trap, and collecting every violation |
| 22 | `22_file_types.py` | `FileType` validates, opens and never closes; 3.14 deprecates it; what to use instead |

## Part F — Custom actions

| # | File | What it teaches |
|---|---|---|
| 23 | `23_custom_action_basics.py` | The one-method protocol, custom keywords, `option_string`, and copying accumulators |
| 24 | `24_composite_actions.py` | CSV lists, `KEY=VALUE`, append-unique, and env-backed flags |
| 25 | `25_action_help_and_formatting.py` | **Three corrections**: `format_help` is never called, `format_usage` only for flags, and SUPPRESS needs no help |

## Part G — Help output

| # | File | What it teaches |
|---|---|---|
| 26 | `26_help_formatters.py` | The stock formatters, composition via MRO, and pinning the width |
| 27 | `27_custom_help_layout.py` | Rewriting the layout from scratch, and the two rules you must honour yourself |

## Part H — Configuration and environment

| # | File | What it teaches |
|---|---|---|
| 28 | `28_fromfile_prefix_chars.py` | Response files as argv splicing, and the `@` collision |
| 29 | `29_config_file_layering.py` | The two config philosophies, the `str`-only conversion trap, and bootstrapping `--config` |
| 30 | `30_environment_variable_defaults.py` | Env as defaults, naming, coercion, and tri-state booleans |

## Part I — Errors and exits

| # | File | What it teaches |
|---|---|---|
| 31 | `31_error_handling.py` | Three error mechanisms and the narrow scope of `exit_on_error=False` |
| 32 | `32_exit_codes.py` | Exit codes as a public API, partial success, and the three-step `main` |

## Part J — Packaging and polish

| # | File | What it teaches |
|---|---|---|
| 33 | `33_prog_usage_and_width.py` | `prog`, when `usage=` is wrong, and deterministic help for tests |
| 34 | `34_deprecated_and_hidden_flags.py` | The deprecation ladder; 3.14's `deprecated=` works for options too |
| 35 | `35_parse_known_args_migration.py` | Pass-through, two-pass config bootstrapping, and strictness |
| 36 | `36_shell_completion.py` | Generating bash/zsh/fish completion from the parser, including aliases |
| 37 | `37_capstone_taskrunner.py` | Everything in one file: config layering, validation, dispatch, completion, exit codes |

---

## Conventions

* Every module docstring explains the **why**, and names the trap if there is one.
* `build_parser()` is always separate from `main(argv)`, and `main` returns an
  `int` rather than calling `sys.exit` — that is what makes the CLIs testable.
* `--demo` runs the lesson and prints annotated output, so a lesson teaches even
  if you only execute it.
* Deliberate failures inside a demo are captured, so a lesson's output is never
  polluted by argparse's own error text.
* Where a claim in the module docstring contradicts common belief, the demo prints
  the counter-evidence.