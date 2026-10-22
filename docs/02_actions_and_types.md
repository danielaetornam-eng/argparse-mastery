# Actions and types

Two hooks for transforming user input, and they run in a fixed order. Every
lesson here has a runnable counterpart in `../examples/`.

## `type=`: convert and validate one token

A `type=` callable takes a string and returns a Python object, or raises. It is
the most under-used feature in argparse, and the reason is that most people stop
at `type=int`.

```
raw token -> type= -> choices= -> action.__call__ -> namespace
```

The contract:

* Return the **richest type available**. `type=port` returns an `int` that is
  already range-checked; `type=duration` returns a `timedelta` so no handler ever
  converts units; `type=existing_file` returns a `Path` and opens nothing.
* Raise `argparse.ArgumentTypeError` (or `ValueError`/`TypeError`) with a message
  that **teaches**. argparse prepends `argument --opt: `, so never write that
  prefix yourself.
* Never let another exception escape. argparse catches exactly three types;
  anything else becomes a traceback that looks like a crash in your program.

```python
def port(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not an integer (expected something like 8080)"
        ) from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is outside the valid port range 1-65535")
    return value
```

`from None` suppresses the chained `ValueError`, which in a CLI is noise.

### `type=` is a factory when parameterised

`add_argument` calls `type` with **one positional argument**, so bounds cannot be
passed alongside it:

```python
def bounded_int(minimum, maximum, *, unit=""):
    def parse(raw): ...
    return parse

parser.add_argument("--port",   type=bounded_int(1, 65535))
parser.add_argument("--workers", type=bounded_int(1, 64, unit="workers"))
```

This is the single most reusable argparse idiom. See
`argparse_mastery.types.bounded_int`.

### Where `type=` cannot reach

* **Two-argument rules.** It sees one token. Use a validation function.
* **Non-argv layers.** It does not run on a non-string default (pitfall 8), and
  never on config-file values. Validate those explicitly.
* **Values from other arguments.** It has no namespace. Use an Action.

### `choices`, and why `type=` usually beats it

`choices` compares with `in`, so it works on any container -- including
`range(1, 65536)` -- and it validates the *converted* value. Two reasons to
prefer a `type=` anyway:

1. The error message. `argument --port: invalid int value: 'http'` versus
   anything you write.
2. 3.14's `suggest_on_error` only fires for `choices`, and only when every choice
   is a `str`.

The strongest combination is `type=` for conversion and validation, `choices=` for
the *enumerable* cases where argparse's listing of valid values in `usage:` is the
right affordance.

## `action=`: decide what lands in the namespace

One method is the whole protocol:

```python
def __call__(self, parser, namespace, values, option_string=None):
    setattr(namespace, self.dest, values)
```

`_StoreAction` is exactly that. `store_true` is it with `const=True, nargs=0`.

### The full built-in table

| Action | `nargs` | Namespace result | Watch out for |
|---|---|---|---|
| `store` | 1 | the value | -- |
| `store_true` | 0 | `True` | cannot distinguish "not passed" |
| `store_false` | 0 | `False` | name the dest positively |
| `store_const` | 0 or `?` | `const` | `const` must satisfy `choices` |
| `append` | any | list, one entry per occurrence | **mutates a list default** |
| `extend` | any | flattened list | type differs from `append` at `nargs` in (None, `?`) |
| `count` | 0 | integer | 3.14 guards `default=None`; prefer `default=0` |
| `help` | -- | prints, `exit(0)` | stdout |
| `version` | -- | prints, `exit(0)` | stdout |
| `BooleanOptionalAction` | 0 | `True`/`False`/`None` | 3.9+ |

### Writing one

```python
class Collect(argparse.Action):
    def __init__(self, option_strings, dest, sep=",", **kwargs):
        self.sep = sep                      # pop custom kwargs BEFORE super()
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        current = list(getattr(namespace, self.dest, None) or [])   # copy!
        for item in str(values).split(self.sep):
            if item and item not in current:
                current.append(item)
        setattr(namespace, self.dest, current)
```

Four rules:

1. **Pop your custom keywords before `super().__init__`.** argparse does not know
   them.
2. **Choose non-colliding names.** `default`, `type`, `choices`, `nargs`,
   `metavar`, `help`, `required`, `const`, `action`, `dest` all belong to
   `add_argument`. `sep`, `low`, `high`, `requires` are safe.
3. **Copy the accumulator.** `list(getattr(ns, self.dest, None) or [])` is the
   idiom; without it you mutate the shared default.
4. **`__call__` is not optional.** A class that only overrides `format_help`
   parses fine and then raises `NotImplementedError` the first time the flag is
   used.

### Registering a string name

```python
parser.register("action", "collect", Collect)
parser.add_argument("--tag", action="collect", sep=",")
```

`register` stores on the **parser instance**, so subparsers do not inherit it.
Call it per parser, or pass a parent parser.

### Using `parser` from inside an action

Only `parser.error()` produces a proper usage error with the usage line and exit
2. Raising anything else gives a traceback. That is the one thing you cannot do
from a `type=` function, and the main reason to reach for an Action.

### `option_string` for per-spelling behaviour

The same action object receives the spelling that triggered it, which is how one
action can implement `-v`, `--verbose`, `-q` and `--quiet` meaningfully. See
pattern 12 in [11_pattern_cookbook.md](11_pattern_cookbook.md).

### Overriding help from an action

`format_help` appends to the help column; `format_usage` returning `""` removes
the option from the usage line. Both are useful, and both have a trap:

* `ArgumentDefaultsHelpFormatter` **bypasses** `action.format_help`, because it
  reads `action.help` directly. Put per-argument text in `help=` instead.
* `format_usage` is the only way to close the `help=SUPPRESS` gap in the usage
  line.

## Ready-made implementations

`argparse_mastery.types`: `positive_int`, `bounded_int`, `bounded_float`,
`enum_by_name`, `csv_list`, `key_value`, `regex_string`, `duration`,
`byte_size`, `existing_file`, `existing_dir`, `output_path`, `port`, `url`,
`json_object`, `hex_color`.

`argparse_mastery.actions`: `AppendUniqueAction`, `CsvAction`,
`KeyValueAction`, `PathAction`, `EnvAction`, `ExistingFileAction`,
`VerbosityAction`, `DeprecatedAction`, plus `register_all`.

Lessons 02, 03, 07, 12, 20-25.