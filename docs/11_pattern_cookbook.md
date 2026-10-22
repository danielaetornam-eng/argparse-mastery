# The pattern cookbook

Twenty complete recipes. Each is a few lines, each names the technique, and each
links to the lesson that teaches it. Copy the shape, not the code.

---

## 1. The canonical `main`

The shape every CLI in this repository uses.

```python
def build_parser() -> argparse.ArgumentParser: ...
def validate(args, parser) -> None: ...
def cmd_x(args: argparse.Namespace) -> int: ...

def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)      # accept argv; never read sys.argv
    validate(args, parser)              # before any side effect
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
```

`argv` as a parameter and an `int` return make the CLI testable in-process.
Lesson 15.

---

## 2. A bounded integer with a good error message

```python
def bounded_int(minimum, maximum, *, unit=""):
    suffix = f" {unit}" if unit else ""
    def parse(raw):
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{raw!r} is not an integer (expected {minimum}-{maximum})") from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"{value}{suffix} is outside the allowed range {minimum}-{maximum}{suffix}")
        return value
    return parse

parser.add_argument("--workers", type=bounded_int(1, 64, unit="workers"))
```

A *factory*, because `type=` receives exactly one positional argument. Lessons 20-21.

---

## 3. An Enum option that users type as plain strings

```python
class Mode(enum.Enum):
    FAST = "fast"; SAFE = "safe"

def enum_type(cls):
    by_name = {m.name.lower(): m for m in cls}
    def parse(raw):
        if raw.lower() in by_name:
            return by_name[raw.lower()]
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a valid {cls.__name__.lower()}; choose from "
            + ", ".join(m.name for m in cls))
    return parse

parser.add_argument("--mode", type=enum_type(Mode), choices=list(Mode),
                    default=Mode.SAFE, metavar="{FAST,SAFE}")
```

Better than `choices=list(Mode)`, which makes users type `Mode.FAST` and gets no
`did you mean` from 3.14's `suggest_on_error` (it requires `str` choices).
Lesson 12.

---

## 4. A boolean with three states

```python
parser.add_argument("--color", action=argparse.BooleanOptionalAction, default=None)
# absent -> None   --color -> True   --no-color -> False
```

The tri-state is the point: `None` means "not specified, ask the config file".
Lesson 02.

---

## 5. One flag, many values, in every spelling

```python
class CsvAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        items = values if isinstance(values, (list, tuple)) else [values]
        current = list(getattr(namespace, self.dest, None) or [])
        for item in items:
            current.extend(p for p in str(item).split(",") if p)
        setattr(namespace, self.dest, current)

parser.register("action", "csv", CsvAction)
parser.add_argument("--tag", action="csv", nargs="*", default=[])
```

`--tag a,b`, `--tag a b` and `--tag a --tag b,c` all work. The `isinstance`
branch is mandatory: with `nargs=None`, `values` is a bare string. Lesson 24.

---

## 6. Repeatable flag that de-duplicates

```python
class AppendUniqueAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        current = list(getattr(namespace, self.dest, None) or [])
        for v in values if isinstance(values, (list, tuple)) else [values]:
            if v not in current:
                current.append(v)
        setattr(namespace, self.dest, current)
```

The `list(...)` copy is the fix for the shared-mutable-default leak. Lessons 07, 24.

---

## 7. Building a dict from repeated flags

```python
class KeyValueAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        mapping = dict(getattr(namespace, self.dest, None) or {})
        if isinstance(values, str):
            key, sep, value = values.partition("=")   # FIRST '=' only
            if not sep:
                parser.error(f"argument {option_string}: expected KEY=VALUE")
            mapping[key.strip()] = value
        else:
            key, value = values
            mapping[str(key)] = str(value)
        setattr(namespace, self.dest, mapping)

parser.add_argument("--set", action="append", type=lambda s: s, default=[])
```

`--set k=v` and `--set k v` both work; splitting on the *first* `=` keeps
passwords intact. Lessons 20, 24.

---

## 8. `KEY=VALUE` in one token, validated

```python
def key_value(raw):
    key, sep, value = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"{raw!r} must be written as KEY=VALUE")
    if not key.strip():
        raise argparse.ArgumentTypeError(f"{raw!r} has an empty key")
    return key.strip(), value

parser.add_argument("--set", action="append", type=key_value, default=[])
```

Lesson 20.

---

## 9. A human-scale duration

```python
def duration(raw):
    # tokenise, validate every token, sum
    ...
    return timedelta(seconds=total)

parser.add_argument("--timeout", type=duration, default="30s")
```

Return the *object*, not the number. `args.timeout.total_seconds()` in the
handler; no arithmetic scattered across call sites. Lesson 20.

---

## 10. A value that reads from the environment

```python
class EnvAction(argparse.Action):
    def __init__(self, option_strings, dest, **kw):
        self.envvar = kw.pop("envvar", dest.upper())
        self.required_env = kw.pop("required_env", False)
        kw.setdefault("nargs", 0); kw.setdefault("default", None)
        super().__init__(option_strings, dest, **kw)

    def __call__(self, parser, namespace, values, option_string=None):
        raw = os.environ.get(self.envvar)
        if raw is None:
            if self.required_env:
                parser.error(f"{option_string} given but ${self.envvar} is unset")
            return
        setattr(namespace, self.dest, raw)

parser.add_argument("--token", action="env", envvar="MYAPP_TOKEN")
```

Keeps secrets out of shell history and `ps`. Lessons 24, 30.

---

## 11. An accumulating verbosity level

```python
def level_for(verbose=0, quiet=0, base=logging.WARNING):
    level = base - 10 * quiet + 10 * verbose
    return max(min(level, logging.CRITICAL), logging.CRITICAL)

parser.add_argument("-v", "--verbose", action="count", default=0)
parser.add_argument("-q", "--quiet", action="count", default=0)
# both sum into one level: order-independent by construction
```

Two `count` actions beat two booleans, because the order of `-vv -q` and
`-q -vv` cannot disagree. Lesson 02.

---

## 12. One action, two spellings, one accumulator

```python
class PairingLevel(argparse.Action):
    def __init__(self, option_strings, dest, **kw):
        self._deltas = {f: (1 if f in ("-v", "--verbose") else -1)
                        for f in option_strings}
        kw.setdefault("nargs", 0); kw.setdefault("default", 0)
        super().__init__(option_strings, dest, **kw)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest,
                (self.default or 0) + self._deltas.get(option_string or "", 0))

parser.add_argument("-v", "--verbose", dest="loglevel", action=PairingLevel)
parser.add_argument("-q", "--quiet", dest="loglevel", action=PairingLevel)
```

Starting from `self.default` rather than the current value is what makes the order
irrelevant. Lesson 23.

---

## 13. A flag that accepts a value only sometimes

```python
parser.add_argument("--color", nargs="?", const="always", default="auto",
                    choices=("always", "never", "auto"), metavar="WHEN")
# absent -> auto   --color -> always   --color never -> never
```

Three states from one argument. `const` must satisfy `choices` or the bare form
fails at run time. Lesson 08.

---

## 14. A shorthand flag for another option

```python
parser.add_argument("--format", choices=("text", "json"), default="text")
parser.add_argument("--json", action="store_const", const="json", dest="format")
```

Two option strings writing the same dest is not a conflict. Declare the real
option first. Lesson 07.

---

## 15. Report every cross-argument violation at once

```python
def validate(args, parser):
    problems = []
    if args.watch and args.timeout > 120:
        problems.append(f"--watch caps --timeout at 120s, got {args.timeout:g}")
    if args.retries > 5 and args.verbose:
        problems.append(f"--retries {args.retries} with --verbose is noisy")
    if problems:
        parser.error("; ".join(problems))
```

One fix, one test, and the user fixes everything in a single run. Lesson 21.

---

## 16. Find the config file before the parser exists

```python
pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
pre.add_argument("--config")
known, _ = pre.parse_known_args(argv)
```

`add_help=False` or `-h` is consumed here. `allow_abbrev=False` or the two parsers
disagree about abbreviations. Lessons 29, 35.

---

## 17. Layered configuration with the command line winning

```python
parser = build_parser()
config_path = find_config(argv)
merged = {}
if config_path:
    merged.update(load_config_file(config_path))
merged.update({k[len(P):].lower(): v for k, v in os.environ.items() if k.startswith(P)})
parser.set_defaults(**validate_against(parser, merged))
args = parser.parse_args(argv)
```

Every non-argv layer becomes a *default*, so argparse's override logic gives argv
precedence for free. The explicit `validate_against` is not optional -- see
pattern 18. Lesson 29.

---

## 18. Validate config values that bypassed `type=`

```python
def validate_against(parser, values):
    actions = {a.dest: a for a in parser._actions}
    out = {}
    for key, value in values.items():
        action = actions.get(key)
        if action is None:
            continue
        if action.type is not None:
            try:
                # str() round-trip is what catches an already-typed value.
                value = action.type(value if isinstance(value, str) else str(value))
            except (argparse.ArgumentTypeError, ValueError, TypeError) as exc:
                raise SystemExit(f"{key}: invalid value from config: {exc}")
        if action.choices is not None and value not in action.choices:
            raise SystemExit(f"{key}: {value!r} is not one of {list(action.choices)}")
        out[key] = value
    return out
```

argparse applies `type=` to a default only when it is a `str`. A TOML
`retries = 0` arrives as an int and skips every check. `str()` round-tripping is
the cheapest correct fix. Lesson 29, pattern 8 of `13_pitfalls.md`.

---

## 19. Cross-field validation from dataclasses

```python
@dataclass(frozen=True, slots=True)
class Option:
    names: tuple[str, ...]
    type: Callable[[str], Any] | None = None
    default: Any = None
    deps: tuple[str, ...] = ()          # dependencies argparse cannot express

    def to_kwargs(self) -> dict[str, Any]:
        out = {}
        if self.type is not None: out["type"] = self.type
        if self.default is not None: out["default"] = self.default
        return out

def build(spec) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=spec.prog)
    for option in spec.options:
        parser.add_argument(*option.names, **option.to_kwargs())
    return parser
```

The spec is data, so it can be diffed, documented and tested without parsing.
`argparse_mastery.builders` is the full version. Lesson 20.

---

## 20. A hidden flag that is also hidden from the usage line

```python
class HiddenFlag(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
    def format_usage(self):
        return ""      # closes the help=SUPPRESS gap
    def format_help(self):
        return ""

parser.add_argument("--escape-hatch", action=HiddenFlag, default=False,
                    help=argparse.SUPPRESS)
```

`help=SUPPRESS` alone leaves the flag in `usage:`. `format_usage` returning `""`
removes it. Lessons 06, 25.

---

## Bonus: shell completion from introspection

```python
sub = parser.add_subparsers(dest="command")
# sub.choices  -> {'add': parser, 'rm': parser}
# getattr(sub.choices['add'], 'aliases', ())  -> ('a',)

def bash_script(prog, parser):
    commands, flags = collect(parser)
    return f"complete -F _{prog}_completion {prog}\n" \
           f"  ...COMPREPLY=( $(compgen -W '{commands}' -- \"$cur\") )"
```

Generation cannot rot; hand-written completion always does. Lesson 36.

---

## Bonus: parse in a REPL without killing it

```python
parser = FriendlyArgumentParser(exit_on_error=True)   # or exit_on_error=False
try:
    args = parse_or_raise(parser)
except ParseFailure as failure:
    print(failure.message, failure.argument_name)
```

`exit_on_error=False` alone is not enough: `parse_args`'s trailing-argument check
calls `error()` regardless. `parse_or_raise` overrides both. Lesson 31.
`argparse_mastery.errors`.