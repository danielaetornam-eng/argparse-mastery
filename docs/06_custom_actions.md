# Custom actions

`argparse.Action` in depth. The protocol is one method; the difficulty is in the
details around it.

## The protocol

```python
class _StoreAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
```

That is the whole of `action='store'`. `store_true` is it with `const=True` and
`nargs=0`. So a custom action is "decide what `setattr` should receive", plus
optionally some `__init__` keywords of your own.

## What `self` gives you

Set by `Action.__init__` from `add_argument` keyword arguments:

| Attribute | Notes |
|---|---|
| `self.option_strings` | `['-v', '--verbose']`; empty for positionals |
| `self.dest` | the namespace key |
| `self.nargs` | `0`, `1`, `2`, `'?'`, `'*'`, `'+'`, `N`, `REMAINDER`, `PARSER` |
| `self.const` | value for `nargs=0`, or `nargs='?'` with no token |
| `self.default` | the value present *before* parsing -- your initialiser |
| `self.type` | the `type=` callable, or `None` |
| `self.choices` | allowed values, or `None` |
| `self.required`, `self.help`, `self.metavar` | |

Two are worth using deliberately:

* **`self.default`** is the only reliable way to initialise an accumulator.
* **`self.const`** is what makes `nargs='?'` with a bare flag work.

## What `__call__` receives

```python
def __call__(self, parser, namespace, values, option_string=None):
```

* `values` has already been through `type=` and, if it is a positional, the
  positional matcher. With `nargs` other than `None`/`'?'` it is a **list**; with
  `None` or `'?'` it is a **bare string**. Always check with `isinstance` before
  iterating.
* `option_string` is the spelling that triggered it, which is what makes
  per-spelling behaviour possible.

## The four rules

### 1. `__call__` is not optional

```python
class AnnotatedAction(argparse.Action):
    def format_help(self): ...      # forgot __call__

# parse_args works fine. The first time the flag is used:
# NotImplementedError: .__call__() not defined
```

This bites people who subclass `Action` only to customise presentation.

### 2. Pop custom keywords before `super().__init__`

```python
class Collect(argparse.Action):
    def __init__(self, option_strings, dest, sep=",", **kwargs):
        self.sep = sep                                     # pop FIRST
        super().__init__(option_strings, dest, **kwargs)
```

And pick names that cannot collide with `add_argument`'s own:
`default`, `type`, `choices`, `nargs`, `metavar`, `help`, `required`, `const`,
`action`, `dest`, `version`. `sep`, `low`, `high`, `requires` are safe.

### 3. Copy the accumulator

```python
current = list(getattr(namespace, self.dest, None) or [])
```

argparse puts *your* default object in the namespace. `current.append(...)`
mutates it, and the mutation persists for the life of the process -- which shows
up as "tests pass alone, fail together". Skipping the `list()` is the single most
common custom-action bug.

### 4. Use `parser.error` for user errors

```python
raise argparse.ArgumentError(self, "takes 2-3 values, got 1")
# or
parser.error(f"argument {option_string}: expected KEY=VALUE")
```

`parser.error` prints the usage line and exits 2. Raising anything else produces
a traceback. Note that `ArgumentTypeError` is *not* catchable here -- it belongs
to `type=`, which runs before `__call__`.

## Registering

```python
parser.register("action", "collect", Collect)
parser.add_argument("--tag", action="collect", sep=",")
```

`register` writes to the **parser instance**, so subparsers do not inherit it.
Call it per parser, or pass a parent parser.

The same mechanism works for types: `parser.register("type", "port", port_number)`.

## Recipe: enforcing a count argparse has no syntax for

```python
class Between(argparse.Action):
    def __init__(self, option_strings, dest, low=0, high=None, **kwargs):
        self.low, self.high = low, high
        kwargs["nargs"] = "*"                 # forced: this action is variable-width
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        count = len(values)
        allowed = (f"exactly {self.low}" if self.high == self.low
                   else f"between {self.low} and {self.high}")
        if count < self.low or (self.high is not None and count > self.high):
            raise argparse.ArgumentError(self, f"takes {allowed} values, got {count}")
        setattr(namespace, self.dest, values)

parser.add_argument("--point", action=Between, low=2, high=3, type=int)
```

Use it when `type=` cannot reach: two-argument rules, context-dependent validity,
or value fixing-up.

## Recipe: one action, several spellings, one accumulator

```python
class PairingLevel(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        self._deltas = {f: (1 if f in ("-v", "--verbose") else -1)
                        for f in option_strings}
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", 0)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        # Start from self.default, not the current value, so -vv -q == -q -vv.
        setattr(namespace, self.dest,
                (self.default or 0) + self._deltas.get(option_string or "", 0))
```

## Recipe: two-phase checking

An action's `__call__` runs the moment its token is consumed, so it cannot see
arguments that appear later. The fix is to record now and check after:

```python
class RequireSiblingAction(argparse.Action):
    pending: list[tuple[str, str]] = []

    def __init__(self, option_strings, dest, requires="", **kwargs):
        self.requires = requires
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        if option_string:
            RequireSiblingAction.pending.append((option_string, self.requires))

def check_requirements(namespace):
    problems = [f"{flag} requires {req}"
                for flag, req in RequireSiblingAction.pending
                if not getattr(namespace, req.lstrip("-").replace("-", "_"), None)]
    RequireSiblingAction.pending.clear()
    if problems:
        raise SystemExit("; ".join(problems))
```

Usually a post-parse validation function is simpler. This is worth knowing when
the check genuinely needs `parser`.

## Overriding help from an action

```python
def format_help(self):
    if self.hidden:
        return ""
    return f"{self.help or ''}  [DEPRECATED: use {self.replacement}]"

def format_usage(self):
    return ""      # closes the help=SUPPRESS gap in the usage line
```

Two traps:

* `ArgumentDefaultsHelpFormatter` **bypasses** `action.format_help`, because it
  reads `action.help` directly. Under that formatter, put the text in `help=`.
* `format_usage` returning `""` is the only way to remove an option from the
  usage line; `help=SUPPRESS` only affects the help table.

## Ready-made

`argparse_mastery.actions`:

| Action | Use |
|---|---|
| `AppendUniqueAction` | repeated flag, de-duplicated, no default leak |
| `CsvAction` | `--tag a,b` and `--tag a b` both work |
| `KeyValueAction` | `--set k=v` into a dict |
| `PathAction` | repeated paths, `~` expanded, deduped |
| `EnvAction` | `--token` reads `$TOKEN` |
| `ExistingFileAction` | existence check that composes with any `type=` |
| `VerbosityAction` | `-v`/`-q` onto one level |
| `DeprecatedAction` | warns, forwards to a shared dest, marks itself |

Plus `register_all(parser)` for the string-name form.

Lessons 23, 24, 25; pitfalls 9, 30.