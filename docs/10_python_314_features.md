# New in Python 3.14

Four argparse changes, all verified on 3.14.6. Each is a small addition; two of
them close gaps people had been working around for years, and one is close to
useless if you read the source.

Version gating, since this repository supports 3.11+:

```python
if sys.version_info >= (3, 14):
    kwargs["suggest_on_error"] = True
```

or, as `argparse_mastery.FriendlyArgumentParser` does, accept the keyword and
forward it only when it exists.

---

## 1. `suggest_on_error`

```python
parser = argparse.ArgumentParser(suggest_on_error=True)
parser.add_argument("--mode", choices=("fast", "balanced", "safe"))
```

```
$ prog --mode fastt
prog: error: argument --mode: invalid choice: 'fastt', maybe you meant 'fast'?
               (choose from 'fast', 'balanced', 'safe')
```

**What it actually covers.** Read `_check_value` in 3.14's source and the whole
implementation is nine lines:

```python
if self.suggest_on_error and isinstance(value, str):
    if all(isinstance(choice, str) for choice in action.choices):
        import difflib
        suggestions = difflib.get_close_matches(value, action.choices, 1)
        if suggestions:
            args['closest'] = suggestions[0]
            msg = 'invalid choice: %(value)r, maybe you meant %(closest)r? ...'
```

Three consequences, all verified:

1. **Only `choices` violations.** Every other error -- a bad `type=`, an
   unrecognized option, a missing required argument -- is unchanged.
2. **Only when every choice is a `str`.** An `Enum` list gets no suggestion,
   because `difflib` cannot compare non-strings. So `choices=list(Mode)` -- the
   pattern people adopt precisely *because* it gives typed values -- silently opts
   out of the feature.
3. **Only one suggestion.** `get_close_matches(..., 1)`, not `n`.

**What it does not cover.** The most common typo class of all: mistyped flags.

```
$ prog --verbse
prog: error: unrecognized arguments: --verbse      <- no suggestion
```

That message comes from the final check in `parse_args`, which calls
`self.error()` with a string it composed itself. Nothing there is suggestion-
aware, and nothing is likely to change that soon.

**Closing the gap.** Override `error()` and post-process:

```python
class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if "unrecognized arguments:" in message:
            unknown = message.split("unrecognized arguments:", 1)[1].split()
            hint = unrecognized_suggestions(
                unknown, known_option_strings(self),
                known_commands=subcommand_names(self),
            )
            if hint:
                message += "\n" + hint
        super().error(message)
```

`argparse_mastery.FriendlyArgumentParser` does this, and
`argparse_mastery.suggest` implements the matching. Two details it gets right:

* **Strip leading dashes before comparing.** `difflib` compares whole strings, so
  `--outp` scores 0.33 against `--output-file` while the match is obvious to a
  human.
* **Add a prefix bonus.** The most common flag typo is truncation, which
  `difflib` structurally cannot see.
* **Add a transposition bonus.** Adjacent swaps (`tset` for `test`) score 0.6 --
  below any sane cutoff -- because the matching runs are 1 and 2 characters.
  Both bonuses compose with the ratio score rather than replacing it.

---

## 2. Colour

```python
parser = argparse.ArgumentParser(color=True)
formatter = MyFormatter(prog="x", color=False)     # HelpFormatter too
```

argparse now colours option strings and error text. What matters more is **when it
turns itself off**, because that is the whole design. From `_colorize.can_colorize`:

| Condition | Colour |
|---|---|
| `PYTHON_COLORS=0` | off |
| `PYTHON_COLORS=1` | **on** |
| `NO_COLOR` set (any value) | off |
| `FORCE_COLOR` set (any value) | **on** |
| `TERM=dumb` | off |
| `sys.flags.ignore_environment` (`-E`) | skip all of the above |
| not a TTY | off |
| Windows, no virtual-terminal support | off |

Two consequences worth designing around:

* **Redirected output is uncoloured automatically.** So
  `prog --help > file` is clean and `prog --help | less -R` works, with nothing for
  you to implement.
* **`FORCE_COLOR=1` forces it on.** That is how to inspect the codes:

  ```bash
  FORCE_COLOR=1 prog --help | cat -v
  ```

  Colours come from the `_colorize` theme, so they follow the terminal's palette
  rather than being hard-coded.

`argparse_mastery.formatters.ColorHelpFormatter` forwards `color` on 3.14+ and
accepts-and-drops it on 3.11-3.13, so one codebase can request colour everywhere
without version sniffing at the call sites.

---

## 3. `add_parser(deprecated=True)`

```python
sub = parser.add_subparsers()
sub.add_parser("purge", deprecated=True, help=argparse.SUPPRESS)
```

```
$ prog purge
prog: warning: command 'purge' is deprecated
$ echo $?
0
```

Verified behaviour:

* The warning is emitted at **parse time**, to stderr, by argparse itself.
* The command **runs normally** and the exit status is **0**.
* It is **not hidden** from `--help` unless you also pass `help=SUPPRESS`.
* Aliases do not appear in the warning.

This is the cleanest deprecation story argparse now offers, because the warning
cannot be forgotten in a handler that someone forgot to wire up.

### It works for **options** too

`argparse.Action.__init__` takes a `deprecated` parameter, so:

```python
parser.add_argument("--old-format", deprecated=True, choices=("json", "text"))
# prog: warning: option '--old-format' is deprecated
```

Verified on 3.14.6 for `store`, `store_true`, `count` and `nargs=2`. Same
behaviour as the command form: the option still works, exit status 0, and it is
**not** hidden from `--help` (add `help=SUPPRESS` for that).

Its limitation is the message: fixed wording, no replacement, no removal version.
For a better message, a custom Action -- `argparse_mastery.actions.DeprecatedAction`
warns through `warnings` (filterable with `-W`, deduplicated) and shares a `dest`
with the modern spelling so the two cannot drift. Lesson 34.

---

## 4. `add_subparsers(help=...)`

```python
sub = parser.add_subparsers(dest="command", help="available commands")
```

A heading for the subparser block in the parent parser's help, rather than a
repeating one-line entry per command. Requires 3.13+.

Why it matters: with `metavar="<command>"` (3.7+) you get a compact usage line
but the individual commands disappear from the parent's help. Before 3.13 the only
options were the compact usage line *or* a full list, with no way to have both.
Now you can have both. See
`argparse_mastery.builders._supports_subparser_help`.

---

## Unchanged, but worth re-verifying on 3.14

These were myths or version-sensitive claims that this repository checks
explicitly:

| Claim | Reality on 3.14.6 |
|---|---|
| subparser `-h` needs `add_help=True` | **false** -- it works by default |
| `allow_abbrev` shortens subcommands | **false** -- `choices` is exact |
| `nargs=(m, n)` works | **false** -- `ValueError: invalid nargs value` |
| `suggest_on_error` improves all errors | **false** -- only `choices` |
| `nargs=0` on `store` means "optional" | **false** -- `ValueError` at build time |
| type converts non-string defaults | **false** -- only `str` defaults |
| `--` works with `nargs='*'` on an option | **false** -- only on positionals |
| `count` with `default=None` raises | **false** -- 3.14 guards it |
| `help=SUPPRESS` leaves the usage line | **false** -- it does not |
| `add_argument(deprecated=...)` does not exist | **false** -- it does, 3.14 |
| `action.format_help()` is called | **false** -- zero call sites |
| `parser.aliases` holds subcommand aliases | **false** -- it does not |
| `HelpFormatter._format_section` exists | **false** -- removed in 3.14 |

## Also deprecated in 3.14

`argparse.FileType` now emits:

```
PendingDeprecationWarning: FileType is deprecated.
Simply open files after parsing arguments.
```

Validate a `Path` with a `type=` and open it yourself under `with`. This was
already the recommended practice because of the file-handle leak; it is now the
stdlib asking for it. Lesson 22.