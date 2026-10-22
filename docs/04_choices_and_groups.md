# `choices` and mutually exclusive groups

argparse's entire vocabulary for "these arguments relate to each other" is one
group type plus `required`. This document is about using it well and knowing
exactly where it stops.

## `choices`

One sentence: argparse checks `value in choices` on the value **after** `type=`.

Everything follows from that.

### What that buys you

```python
parser.add_argument("--port", type=int, choices=range(1, 65536), default=8080)
parser.add_argument("--level", type=int, choices=range(0, 6), default=3)
parser.add_argument("--tag",  choices=("stable", "beta"), default="stable")
```

* `choices` accepts any container: list, tuple, set, dict, `range`.
* The comparison is on the converted value, so `choices=range(...)` works with
  `type=int`.
* The valid values appear in the usage line: `--port {1,2,3,...}` -- which for a
  large range is noise, which is why an explicit `metavar` usually reads better.

### What it costs

The error message is terse:

```
error: argument --mode: invalid choice: 'fastt' (choose from 'fast', 'slow')
```

3.14's `suggest_on_error=True` improves it:

```
error: argument --mode: invalid choice: 'fastt', maybe you meant 'fast'?
       (choose from 'fast', 'slow')
```

But reading `_check_value`, that is the *entire* feature: it fires only for
`choices`, only when every choice is a `str`, and returns one suggestion. So
`choices=list(Mode)` opts out of it -- which matters, because typed enums are the
pattern people adopt precisely to get better values.

### Doing it better

| Goal | Tool |
|---|---|
| Convert to a domain type | `type=` returning an `Enum` member (lesson 12) |
| Explain what is wrong | a validating `type=` (lesson 20) |
| Document the valid set in `usage:` | `metavar="{fast,slow}"` |
| Accept any case | normalise in `type=` before `choices` runs |
| Enumerate a large or computed set | a `type=` factory, not `choices` |

The strongest combination is `type=` for conversion and validation plus
`choices=` only where argparse's inline listing is genuinely the right affordance.

### Never pass an iterator

```python
parser.add_argument("--mode", choices=(c for c in ("fast", "slow")))
# usage: prog [--mode {}]
```

argparse joins the choices to build the metavar, consuming the generator. Always
pass a concrete sequence.

## Mutually exclusive groups

```python
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument("--json", action="store_true")
group.add_argument("--yaml", action="store_true")
group.add_argument("--toml", action="store_true")
```

* Enforces "at most one". `required=True` makes it "exactly one".
* The message is good: `argument --json: not allowed with argument --yaml`.
* `--help` still works with `required=True`, because the help action fires before
  the required check.
* Groups nest, and can live inside `add_argument_group` for organisation.
* Members can be positionals, but mixing positionals and options in one group
  reads badly in help output.

### One option-only rule that is not obvious

**Do not put a positional in a mutually exclusive group.** Verified:

```python
p = argparse.ArgumentParser()
p.add_argument("--show", action="store_true")
g = p.add_mutually_exclusive_group()
g.add_argument("completion-positional", nargs="?")
g.add_argument("--quiet", action="store_true")

p.parse_args(["run", "build"])
# error: unrecognized arguments: build
```

The group's extra positional leaves the remaining positionals ambiguous. The
lesson-37 capstone was written with a single positional completion selector, hit
exactly this, and was rewritten to three options. Option-only groups are safe.

## The four things groups cannot do

| Needed | Expressible? | Do this |
|---|---|---|
| at most one | yes | mutually exclusive group |
| exactly one | yes | `required=True` |
| at least one | **no** | validate after parsing |
| exactly two of four | **no** | count them yourself |
| A requires B | **no** | validate after parsing |
| A conflicts with B, but only if C | **no** | validate after parsing |

## The validation pattern

```python
def validate(args, parser) -> None:
    problems = []

    # "at least one"
    if not any(getattr(args, f) for f in OUTPUT_FLAGS):
        problems.append("one of " + ", ".join(OUTPUT_FLAGS) + " is required")

    # cardinality > 1
    if args.exactly_two:
        chosen = [n for n in ("a", "b", "c") if getattr(args, n)]
        if len(chosen) != 2:
            problems.append(f"--exactly-two needs two of --a/--b/--c, got {len(chosen)}")

    # dependency
    if args.tls and not args.url:
        problems.append("--tls requires --url")

    # conditional exclusivity
    if args.range and args.point:
        problems.append("--range and --point are alternative, not combinable")

    if problems:
        parser.error("; ".join(problems))
```

**Collect every problem, then report once.** This is the single most valuable
habit in the list. The alternative -- `parser.error` inside each rule -- makes
users fix one thing per run, and for five rules that is five runs.

**Call it before any side effect.** A late check means the user finds out about a
typo after their disk has been touched.

**Where each rule belongs**, so the error message is as good as possible:

1. about one value -> `type=`
2. about two values -> validation function
3. about a value and a flag -> validation function
4. about a set of flags -> validation function

## Dependencies, if you want them declarative

```python
@dataclass(frozen=True, slots=True)
class Option:
    names: tuple[str, ...]
    deps: tuple[str, ...] = ()      # "--tls requires --url", recorded here

# after parsing:
problems = [f"{o.names[0]} requires {d}" for o in spec if o.deps and getattr(ns, dest(o), None)
            and not all(getattr(ns, d.lstrip('-').replace('-','_'), None) for d in o.deps)]
```

`argparse_mastery.builders.enforce_dependencies` implements this. Keeping the
relationship next to the definition stops it rotting, and `epilog` prose documents
it for `--help` -- argparse has no field for "requires:".

Lessons 12, 13, 14; pitfalls 18-21, 27.