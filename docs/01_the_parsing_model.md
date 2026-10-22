# The parsing model

Everything else in this repository is detail. This document is the model the
details hang off, and it is short.

## The three objects

```
ArgumentParser   the container: knows its arguments, its groups, its formatter
  Action         one argument: knows its option strings, nargs, type, dest
Namespace        the result: an object whose attributes are the dests
```

`ArgumentParser` is mostly configuration. `Action` is where the behaviour lives.
`Namespace` is just a container -- `argparse.Namespace` subclasses nothing but
`object` and adds `__contains__`/`__eq__`/`__repr__`.

## What `parse_args` actually does

Pseudocode, faithful to CPython's `_parse_known_args`:

```
1. seed the namespace with every action's default
2. scan argv left to right:
     a. token looks like an option  -> resolve it to an Action, consume its values
     b. token looks positional     -> add to the positional pool
3. build a regex from every positional's nargs, match it against the pool
4. consume the matched positionals
5. report leftover tokens as "unrecognized arguments"
```

Two consequences worth internalising:

* **Options are resolved in step 2, positionals in step 3.** They are different
  mechanisms. That is why `prog --verbose status` works and `prog status --verbose`
  does not -- and it is not configurable.
* **Step 1 is where `type=` is *not* applied to non-string defaults.** The one
  exception is at the very end, where argparse converts a default that is still a
  `str` and still identical to the namespace value. This single asymmetry is the
  root of the biggest configuration bug in argparse (see
  [08_config_and_env.md](08_config_and_env.md)).

## How `dest` is derived

```
take the first option string starting with '--', else the first one
strip leading dashes
replace '-' with '_'
```

```
--dry-run              -> dry_run
-n/--name              -> name
--name -n              -> name          (order is irrelevant)
-v                     -> v
-vv                    -> v             (and then it wants a value -- see lesson 05)
infile (positional)    -> infile
```

Override with `dest=`. Override `dest` when the derived name is wrong, never
because you find it inelegant -- every rename is a breaking change for anything
reading `vars(namespace)`.

## The Action protocol

One method. `_StoreAction` is the whole of `action='store'`:

```python
class _StoreAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
```

Everything else is variation on it:

| Action | `nargs` | What it does |
|---|---|---|
| `store` | 1 | `setattr(ns, dest, values)` |
| `store_true` | 0 | `setattr(ns, dest, True)` |
| `store_false` | 0 | `setattr(ns, dest, False)` |
| `store_const` | 0 or `?` | `setattr(ns, dest, const)` |
| `append` | any | `setattr(ns, dest, getattr(ns, dest) + [values])` -- **mutates the default** |
| `extend` | any | flattens one level, then appends |
| `count` | 0 | `setattr(ns, dest, getattr(ns, dest) + 1)` -- **fails if default is `None`** |
| `BooleanOptionalAction` | 0 | two option strings, tri-state default |
| `help` | -- | print to stdout, `exit(0)` |
| `version` | -- | print to stdout, `exit(0)` |

Because the protocol is one method, custom actions are not "subclassing a
mystery class". They are "decide what lands in the namespace", plus optionally
some `__init__` keyword arguments of your own.

## The two conversion hooks, and which runs first

For every consumed token, in this order:

```
raw string
  -> type=        (raise ArgumentTypeError / ValueError / TypeError to reject)
  -> choices      (`value in choices`, exact, on the CONVERTED value)
  -> action.__call__   (writes into the namespace)
```

`choices` validates the *converted* value, which is its most useful property and
the reason `choices=range(1, 65536)` works with `type=int`.

## The three phases you cannot see

1. **Defaults** are installed before any token is read.
2. **Values** are converted as tokens are consumed.
3. **Defaults are re-converted** at the end if they are still `str` and still
   identical to what is in the namespace.

Phase 3 is why `set_defaults(port="8080")` is validated and
`set_defaults(port=8080)` is not. It is also why env-var-as-default works
correctly while TOML ints do not.

## `format_help` in one paragraph

A `HelpFormatter` is fed four things in order -- usage, description, arguments,
epilog -- and each step calls a `_format_*` method. Every help customisation is
an override of one of those methods, which is why
[07_help_output.md](07_help_output.md) is organised by method rather than by
desired outcome.

## Error handling in one paragraph

`parse_args` calls `parse_known_args`, then reports leftovers via
`self.error()`. `error()` prints usage plus a message to stderr and calls
`self.exit(2)`. `exit()` calls `sys.exit`. Three seams, three behaviours, and
`exit_on_error` only intercepts the middle one -- see
[09_errors_exits_testing.md](09_errors_exits_testing.md).

## Where to go next

* The vocabulary in detail: [02](02_actions_and_types.md), [03](03_nargs.md),
  [04](04_choices_and_groups.md), [05](05_subparsers.md)
* The reference: [12_cheatsheet.md](12_cheatsheet.md)
* The surprises: [13_pitfalls.md](13_pitfalls.md)