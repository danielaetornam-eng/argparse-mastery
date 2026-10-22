# Pitfalls: 40 verified behaviours that surprise people

Every entry below was **executed on CPython 3.14.6**. Symptom first, cause second,
fix third. Where this contradicts something widely repeated, the contradiction is
noted -- five of these are corrections of common myths.

---

## The parsing core

### 1. `--vv` is not "verbose twice"

**Symptom.** `error: argument -v: expected one argument`.

**Cause.** argparse scans a single-dash token for *short option characters*.
`-vv` becomes `-v` followed by the value `v`. For `nargs=0` actions the second
character is read as a value.

**Fix.** Use `-v -v -v`. There is no bundled-short-flag syntax in argparse.

---

### 2. `--opt -1` fails

**Symptom.** `error: argument --offset: expected one argument`.

**Cause.** `-1` matches the option prefix, so argparse offers to consume it as
one.

**Fix.** `--offset=-1`. Document the `=` form in `--help`. Several stdlib tools do.

---

### 3. Long options are abbreviated by default

**Symptom.** `--verb` works when you expected it to be an error.

**Cause.** `allow_abbrev=True` accepts any unambiguous long prefix.

**Fix.** `ArgumentParser(allow_abbrev=False)` when exact spelling matters. Note it
also stops `--opt` matching a sole `--option`, which some users rely on.

---

### 4. Two optional positionals: you cannot give only the second

**Symptom.** `prog <second>` fills the first.

**Cause.** Positional matching is positional and greedy.

**Fix.** Make them options, or use subparsers.

---

### 5. `required=True` is rejected on a positional

**Symptom.** `TypeError: 'required' is an invalid argument for positionals`, at
**build** time.

**Cause.** Correct by design: positional-ness already guarantees presence.

**Fix.** None needed. Failing at build time is the good outcome.

---

### 6. `nargs=0` is rejected on `store`

**Symptom.** `ValueError: nargs for store actions must be != 0`.

**Cause.** `nargs=0` means "takes no value", which `store` cannot satisfy.

**Fix.** `store_true`, `store_const`, or `append`.

---

### 7. Two variable-width positionals cannot be resolved

**Symptom.** `prog x y` -> `error: the following arguments are required: b`.

**Cause.** With `nargs='+'` for both `a` and `b`, nothing marks where `a` ends.

**Fix.** Use options: the flag *is* the boundary.

---

## Values

### 8. `type=` does not run for non-string defaults

**Symptom.** A config file writes `retries = 0` and your range check never fires.

**Cause.** argparse converts a default only when it is a `str` *and* the
namespace value is still that object.

```python
parser.add_argument("--port", type=port, default=8080)
parser.set_defaults(port="8080")   # str -> converted -> 8080 (validated)
parser.set_defaults(port=8080)     # int -> SKIPPED  -> 8080 (unvalidated)
```

**Fix.** Validate every non-argv layer explicitly.
`argparse_mastery.config.typed_defaults`.

---

### 9. `default=[]` with `action='append'` leaks between parses

**Symptom.** The third parse in one process shows the first two parses' data.

**Cause.** argparse puts *your* list in the namespace and `append` mutates it.

**Fix.** Omit the default, or copy inside a custom action.

---

### 10. `action='count'` with `default=None` is *fine* on 3.14

**Correction.** The long-standing advice "`count` needs `default=0` or you get
`TypeError`" is no longer true. `_CountAction.__call__` guards it:

```python
count = getattr(namespace, self.dest, None)
if count is None:
    count = 0
setattr(namespace, self.dest, count + 1)
```

**Still use `default=0`**, because the hazard is live for a *hand-rolled* counter
action, which nobody guards -- and a test for it is one line. The lesson is
"verify the stdlib guards its own actions; yours will not guard themselves", not
"count is broken".

---

### 11. `action='extend'` flattens, `append` nests

**Symptom.** `--tag a b --tag c` gives `[['a','b'],['c']]`.

**Cause.** That is what `append` does with `nargs='+'`.

**Fix.** `action='extend'` gives `['a','b','c']`.

---

### 12. `nargs='*'` with `default=""` gives a string

**Symptom.** `for x in args.include:` iterates zero times by accident.

**Cause.** `""` is a valid `str` default and is not converted.

**Fix.** `default=[]` or no default.

---

### 13. `nargs=N` always yields a list

**Symptom.** `x, y = args.coord` raises on a `nargs='+'` version of the same option.

**Cause.** `nargs=2` guarantees length and gives `['3','4']`, not `(3, 4)`.

**Fix.** Either. If you want a tuple, use a `type=` on a single token.

---

### 14. `NaN` and `inf` pass every range check

**Symptom.** `--timeout nan` is accepted, then every comparison misbehaves.

**Cause.** `float("nan")` compares False against everything.

**Fix.** Reject explicitly in a `type=`. argparse offers no protection.

---

### 15. `FileType` never closes what it opens

**Symptom.** `ResourceWarning` in tests; `OSError: Too many open files` in a
long-running process.

**Cause.** argparse cannot know when you are done.

**Fix.** `type=` returning a `Path`, and open it yourself under `with`.

---

### 16. `FileType('r')` opens with the platform encoding

**Symptom.** `UnicodeDecodeError` on a Windows code page.

**Cause.** No encoding is specified.

**Fix.** Pass `encoding=` to `FileType`, or use a `Path`-returning `type=`.

---

## Constraints

### 17. `nargs=(m, n)` does not exist in argparse

**Symptom.** `ValueError: invalid nargs value` at build time.

**Cause.** The supported set is closed: `None`, `0`, `int`, `?`, `*`, `+`,
`REMAINDER`, `PARSER`, `SUPPRESS`.

**Correction.** click and typer *do* accept `nargs=(m,n)`. Coming from those
libraries, writing it in argparse fails loudly -- which is the good outcome.

**Fix.** `nargs='*'` plus a check, a custom Action, or a single comma-separated
token with a validating `type=`.

---

### 18. `allow_abbrev` does not apply to subcommands

**Symptom.** `prog rem` -> `invalid choice: 'rem'`.

**Cause.** A subcommand is a `choices` entry, and `choices` uses `in`, an exact
comparison.

**Fix.** Declare every spelling as an alias.

---

### 19. "At least one of" cannot be expressed

**Symptom.** A mutually exclusive group allows zero.

**Cause.** A group is at-most-one; `required=True` is exactly-one. "At least one"
is a third thing.

**Fix.** Validate after parsing.

---

### 20. Exactly-two-of-four cannot be expressed

**Cause.** No cardinality above 1.

**Fix.** Count the flags yourself.

---

### 21. Dependencies between options cannot be expressed

**Symptom.** `--tls` silently does nothing without `--url`.

**Fix.** A validation function. See lesson 14.

---

### 22. Never pass an iterator to `choices`

**Symptom.** An empty `{...}` in the usage line.

**Cause.** argparse joins the choices to build the metavar, consuming the
generator.

**Fix.** `list(...)` or a tuple.

---

## Subparsers

### 23. A parent's options must precede the subcommand

**Symptom.** `prog status --verbose` -> `unrecognized arguments`.

**Cause.** The subcommand token is a positional, and positionals are matched after
all options are consumed.

**Fix.** Use `parents=[...]` on each subparser, or `parse_intermixed_args` (which
does not support subparsers).

---

### 24. MYTH: subparser `-h` needs `add_help=True`

**Symptom.** none -- `prog status --help` works.

**Correction.** `_SubParsersAction.add_parser` passes `**kwargs` straight to the
parser class, and `ArgumentParser`'s own default is `add_help=True`.

**Fix.** Nothing. You *can* remove it per subparser with `add_help=False`.

---

### 25. `args.command` records the spelling used

**Symptom.** Logs full of `rm`, `delete`, `remove` as if they were different
commands.

**Cause.** Aliases write the user's spelling.

**Fix.** Normalise before logging.

---

### 26. `_SubParsersAction` has no `set_defaults`

**Symptom.** `AttributeError: '_SubParsersAction' object has no attribute
'set_defaults'`.

**Cause.** It is an `Action`, not a parser.

**Fix.** Loop over `sub.choices.values()`, or use `parents=`.

---

### 27. A positional in a mutually exclusive group breaks matching

**Symptom.** `prog run build` -> `invalid choice: 'build'`.

**Cause.** The group adds an extra positional to the match, so the remaining
positionals become ambiguous.

**Fix.** Option-only groups.

---

## Help

### 28. `help=SUPPRESS` *does* remove from the usage line

**Correction.** A widely repeated claim says SUPPRESS leaves the usage line
dirty. It does not. `HelpFormatter.add_argument` skips those actions before they
reach anything:

```python
def add_argument(self, action):
    if action.help is not SUPPRESS:
        ...
        self._add_item(self._format_action, [action])
```

So they disappear from the usage line and the help table together, and stay
functional. Verified on 3.14.6.

---

### 29. `format_help()` takes no width

**Symptom.** Golden-file help tests fail on a different terminal.

**Cause.** `format_help()` builds its own formatter with `width=None`.

**Fix.** Drive the formatter yourself in the same order, or use
`argparse_mastery.testing.help_snapshot`.

---

### 30. `action.format_help()` is never called at all

**Symptom.** A custom per-action help override does nothing, under *any* stock
formatter.

**Cause.** `action.format_help()` has **zero call sites in argparse**. It is an
extension point for third-party formatters. `ArgumentDefaultsHelpFormatter` is
not special here -- it is the same for every formatter, because none of them call
it.

**Related.** `action.format_usage()` has exactly **one** call site, in
`HelpFormatter._format_actions_usage`, and only on the `nargs == 0` branch:

```python
if action.nargs == 0:
    part = action.format_usage()
else:
    part = f"{option_string} {self._format_args(action, default)}"
```

So an Action can rewrite its own chunk in `usage:` iff it is a **flag**.

**Fix.** Per-argument text goes in `help=`; layout changes go in the formatter.

---

## Files and config

### 31. `--` only helps positionals

**Symptom.** `--items -- -x` -> `unrecognized arguments: -- -x`.

**Cause.** An option's `nargs='*'` pattern is `(A*)`, with no slot for the `-` that
`--` becomes; a positional's is `(-*[A-]*)`, which has one.

**Fix.** Take such values as a positional, or use `REMAINDER`.

---

### 32. `REMAINDER` keeps the literal `--`

**Symptom.** The child program sees `--` it was not given.

**Cause.** `REMAINDER` is verbatim.

**Fix.** Strip it before forwarding. `parse_known_args` does strip it.

---

### 33. `fromfile_prefix_chars='@'` breaks `@` values

**Symptom.** `--email a@b.com` tries to read the file `b.com`.

**Cause.** Any value starting with `@` becomes a filename.

**Fix.** `@=path`, or do not enable the feature.

---

### 34. `prefix_chars='-+'` does not alias existing options

**Symptom.** `+v` is rejected after declaring only `-v`/`--verbose`.

**Cause.** `prefix_chars` changes which characters may *begin* an option; the rest
must still match a declared option string.

**Also.** `--` stops working, because `-` is no longer a prefix character.

---

### 35. `parse_known_args` cannot tell a typo from pass-through

**Symptom.** `--verboze` succeeds and does nothing.

**Fix.** If extras must be empty, check them. Or use `parse_args`.

---

### 36. `except SystemExit: pass` swallows `--help`

**Symptom.** Help is ignored, and `args` does not exist afterwards.

**Cause.** `--help` exits **0**.

**Fix.** Catch `ArgumentError`, or use `parse_or_raise`.

---

## Exit codes

### 37. `--help` exits 0, errors exit 2

Not a bug. But `parser.print_help()` from your own code goes to **stdout**; if you
are returning a non-zero status, print to stderr instead.

---

### 38. `argparse.REMAINDER` as the first positional misses a leading flag

**Symptom.** `prog -x` -> `unrecognized arguments: -x`, but `prog echo -x` works.

**Cause.** The pattern `(-*[A-]*)` requires one `A` before `.*` takes over.

**Fix.** Declare a command positional first. Every wrapper tool does.

---

## Environment

### 39. `bool("false")` is `True`

**Symptom.** `MYAPP_DEBUG=false` enables debug.

**Cause.** `bool()` on a non-empty string.

**Fix.** An explicit truthy/falsy parser. Reject anything else.

---

### 40. Python 3.14's `suggest_on_error` covers only `choices`

**Symptom.** `--mode fastt` gets a suggestion; `--verbse` does not.

**Cause.** `_check_value` is the only call site, and it requires every choice to be
a `str`. So an `Enum` choices list gets no suggestion either.

**Fix.** `argparse_mastery.suggest` for the flag and subcommand cases, driven from
a `FriendlyArgumentParser.error` override.

---

### 41. `add_parser(aliases=...)` does not set `parser.aliases`

**Symptom.** `sub.add_parser("remove", aliases=["rm"])` then
`getattr(rm_parser, "aliases", ())` returns `()`. Code that "handles aliases"
runs fine and never sees one.

**Cause.** `add_parser` pops `aliases` out of the kwargs and writes them into the
subparsers action's `_name_parser_map`. The parser object is never touched.

**Fix.** Derive them from insertion order -- `add_parser` writes the canonical
key first and the aliases after, all pointing at the same parser object:

```python
seen = {}
for name, sub in action._name_parser_map.items():
    if id(sub) in seen:
        aliases[name] = seen[id(sub)]
    else:
        seen[id(sub)] = name
```

`argparse_mastery.completion.subcommand_names` does this.

### 42. `FileType` is deprecated in 3.14

**Symptom.** `PendingDeprecationWarning: FileType is deprecated. Simply open files
after parsing arguments.` on instantiation.

**Fix.** `type=` returning a `Path`, and open it yourself under `with`. This is
no longer a judgement call about file-descriptor leaks -- it is what the stdlib
asks for. Lesson 22.

### 43. `HelpFormatter` has no `_format_section` on 3.14

**Symptom.** A formatter that overrides `_format_section` to insert separators
between help sections silently does nothing.

**Cause.** Section rendering moved into the nested `_Section` class;
`start_section` now just queues `section.format_help` as an item.

**Fix.** Override `start_section` and add an item to `self._current_section`
before calling `super()`. `argparse_mastery.formatters.SectionedHelpFormatter`
does this, arming the rule so empty sections do not get one.

---

## And one that is not a pitfall at all

**`suggest_on_error=True`, `color=True` and `add_parser(deprecated=True)` are
3.14 features.** On 3.11-3.13, pass them conditionally -- which is why
`argparse_mastery.FriendlyArgumentParser` takes `suggest_on_error` as a keyword
and forwards it only when `sys.version_info >= (3, 14)`. See
[10_python_314_features.md](10_python_314_features.md).