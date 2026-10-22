# Help output

`--help` is the CLI's documentation, its debugging tool and its only
discoverability surface. Everything here is about argparse's help machinery.

## How output is assembled

`ArgumentParser.format_help()` builds a `HelpFormatter` and feeds it four things
in order:

1. `add_usage(usage, actions, groups, prefix)`
2. `add_text(description)`
3. `add_argument(action)` per action; `add_arguments(group)` per group
4. `add_text(epilog)`

Each step calls a `_format_*` method you can override:

| Method | Controls |
|---|---|
| `_format_usage` | the `usage:` line |
| `_format_text` | description/epilog section headers |
| `_fill_text` | the actual line splitting for steps 2 and 4 |
| `_format_action` | one `--flag  help` row |
| `_get_help_string` | the help text for a single action |
| `_expand_help` | `%(default)s` interpolation |
| `_format_args` | the `[-v VALUE]` chunk inside usage |
| `_get_default_metavar_for_optional` / `_positional` | default metavars |
| `_split_lines` | general text wrapping |
| `start_section` | **the** hook for section separators (3.14) |

## The stock formatters

| Class | Behaviour |
|---|---|
| `HelpFormatter` | the default two-column layout |
| `ArgumentDefaultsHelpFormatter` | appends `(default: X)` to every help string |
| `RawDescriptionHelpFormatter` | does not re-wrap description and epilog |
| `RawTextHelpFormatter` | also does not wrap `help=` -- destroys alignment |
| `MetavarTypeHelpFormatter` | metavar defaults to `type.__name__` |

**Use `ArgumentDefaultsHelpFormatter` on anything you ship.** A user should never
have to run `--help` twice to discover that `--retries` defaults to 3.

`RawDescriptionHelpFormatter` is what makes an indented usage example readable.
`RawTextHelpFormatter` is almost never what you want: it also stops wrapping
`help=` strings, which breaks column alignment.

## Composing them

The behaviours live in *different methods*, so multiple inheritance composes them:

```python
class Help(argparse.ArgumentDefaultsHelpFormatter):
    def _fill_text(self, text, width, indent):
        return "".join(indent + line for line in text.splitlines(keepends=True))
```

That is both of the stock behaviours in five lines. For three or more:

```python
def combined_formatter(*classes):
    class _Combined(*classes):
        pass
    return _Combined

Help = combined_formatter(
    ShortMetavarHelpFormatter,                    # leftmost wins
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
)
```

**Leftmost wins** -- the MRO, not any argparse logic. Put the most opinionated
class first.

`argparse_mastery.formatters.combined_formatter` implements this, and
`ExamplesFormatter` is the two-way version spelled out.

## `metavar` first, formatter second

Most "ugly help" is a metavar problem, not a formatter problem:

```python
parser.add_argument("--log-level", metavar="LEVEL")        # [--log-level LEVEL]
parser.add_argument("--define", nargs=2, metavar=("NAME", "VALUE"))
parser.add_argument("--set", metavar="KEY=VALUE")
```

`metavar` changes only the placeholder; `dest` is unaffected. Fix the placeholder
before reaching for a formatter.

`ShortMetavarHelpFormatter` (metavar from `type.__name__`) is a trap: it makes
error messages *worse*. `argument --port: invalid int value: 'http'` becomes
`argument --port: expected one argument`. Use it only where the argument name and
the type genuinely coincide.

## Width

`HelpFormatter(prog, indent_increment=2, max_help_position=24, width=None,
color=True)`.

`width=None` means "ask the terminal, honouring `COLUMNS`". Consequences:

* `prog --help | less` may format differently from `prog --help`.
* Golden-file tests fail on another machine.
* Piped into a file there is no width, and argparse falls back to 80.

`ArgumentParser.format_help()` takes no width, so pinning it means driving the
formatter yourself:

```python
def render_help(parser, *, width=80):
    f = parser.formatter_class(prog=parser.prog, width=width)
    f.add_usage(parser.usage, parser._actions, parser._mutually_exclusive_groups)
    f.add_text(parser.description)
    for action in parser._actions:
        f.add_argument(action)
    f.add_text(parser.epilog)
    return f.format_help()
```

Five lines that make help output stable in CI.
`argparse_mastery.testing.help_snapshot` is this, wrapped.

`max_help_position` (24) is the furthest right column at which help text may
start. Exceed it and help moves to its own line -- correct but ragged for long
flag names. Raise it to 40 or so.

## Colour (3.14+)

`color=True` on the parser or the formatter. Colour is automatic and
auto-disabled: not a TTY, `NO_COLOR`, `PYTHON_COLORS=0`, `TERM=dumb`, and on
Windows without virtual-terminal support. `FORCE_COLOR=1` forces it on.

Redirected output is clean with nothing to implement, which is the important
property. `argparse_mastery.formatters.ColorHelpFormatter` forwards the flag on
3.14+ and accepts-and-drops it on 3.11-3.13.

## Rewriting the layout

Override `_format_action` when you need a column the stock layout has no place
for. Two rules you must handle yourself:

1. **Skip `help=SUPPRESS` entries.** Forgetting this silently exposes hidden flags.
2. **Render `nargs` correctly** -- or delegate:

   ```python
   chunk = formatter._format_args(action, action.metavar)
   ```

   which already handles `REMAINDER`, `PARSER` and the `...` suffixes. Reuse it
   rather than copying the mapping.

Also reuse `_expand_help` for `%(default)s` interpolation, and `_split_lines` for
consistent wrapping.

### Two hooks on `Action` that do nothing

Worth knowing before you write either:

* `action.format_help()` -- **zero call sites in argparse**. It is an extension
  point for third-party formatters. No stock formatter calls it, including
  `ArgumentDefaultsHelpFormatter`.
* `action.format_usage()` -- one call site, and only on the `nargs == 0` branch of
  `_format_actions_usage`. So it works for flags and is ignored for
  value-taking options.

Per-argument help text therefore belongs in `help=`, not in a `format_help`
override. Lesson 25.

## The three help gaps worth closing

### `help=SUPPRESS` leaves the usage line dirty

```
usage: prog [--verbose] [--internal-thing]     <- SUPPRESS did not remove it
```

Close it with an Action whose `format_usage` returns `""` (lesson 25), or a
formatter that filters.

### `--version` on a subcommand

Put it on the top-level parser and it only works *before* the subcommand. To
support both, add it to every subparser too, or handle it in a parent parser.

### Defaults and env vars in the help text

`ArgumentDefaultsHelpFormatter` covers defaults. For env vars, either append in
the `help=` string:

```python
help=f"log level (env: {ENV_PREFIX}LOG_LEVEL)"
```

or derive it: `argparse_mastery.config.explain_env(parser)` generates the
suffixes for a whole parser after the fact. Deriving is better -- the text cannot
fall out of sync with the naming convention.

## Section separators

Three hooks that *look* right and are not:

| Hook | Result on 3.14 |
|---|---|
| `_format_section` | **does not exist** on `HelpFormatter` any more |
| `_format_action` | only called for arguments, never for a heading |
| `_format_text` | items are *queued*, so a flag set at queue time is stale by render time |

`start_section` is the hook, because it is the one place where ordering is still
knowable -- at that moment `self._current_section` is still the parent, so an
extra item added there lands immediately before the nested section:

```python
class SectionedHelpFormatter(argparse.HelpFormatter):
    SEPARATOR = "  " + "-" * 52      # ASCII: see below

    def start_section(self, heading):
        self._rule_armed = True
        super().start_section(heading)

    def _emit_rule_if_armed(self, content=None):
        if content is None or (isinstance(content, list) and not content):
            return
        if getattr(self, "_rule_armed", False):
            self._rule_armed = False
            self._add_item(lambda s: s + "\n", (self.SEPARATOR,))

    def add_argument(self, action):
        self._emit_rule_if_armed(action); super().add_argument(action)

    def add_arguments(self, actions):
        actions = list(actions)
        self._emit_rule_if_armed(actions); super().add_arguments(actions)

    def add_text(self, text):
        self._emit_rule_if_armed(text); super().add_text(text)
```

Two details: arming on the *first non-empty item* stops an empty section (argparse
creates one per `add_argument_group`, and the default `positional arguments:`
group is empty for a flags-only CLI) from rendering a rule above its own heading.
And the separator is ASCII on purpose -- a box-drawing character raises
`UnicodeEncodeError` on a Windows console whose code page cannot represent it,
turning a cosmetic improvement into a crash.

## Anti-patterns

| Anti-pattern | Why |
|---|---|
| `usage="myapp <everything>"` | you now maintain the usage line by hand |
| `RawTextHelpFormatter` | breaks alignment for no benefit |
| `ShortMetavarHelpFormatter` on typed options | worse error messages |
| wrapping `help=` in manual `\n` | argparse already wraps; you break it at one width |
| `add_help=False` without removing the `-h` action | `conflicting option string: -h` |
| golden-file tests with `format_help()` | terminal-dependent width |

Lessons 26, 27, 33; pitfalls 28, 29, 30.