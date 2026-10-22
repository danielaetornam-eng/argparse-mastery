# The one-page cheatsheet

```
add_argument(flags, dest=, nargs=, const=, default=, type=, choices=,
             required=, help=, metavar=, action=, version=)
```

## Actions

| `action=` | `nargs` | Use it for |
|---|---|---|
| `store` (default) | 1 | an ordinary `--opt VALUE` |
| `store_true` | 0 | a flag whose meaning is "yes" |
| `store_false` | 0 | a flag whose meaning is "no"; name the dest positively |
| `store_const` | 0 or `?` | presence implies a fixed non-bool value |
| `append` | any | accumulate into a list, one entry per occurrence |
| `extend` | any | accumulate, flattening `nargs='+'` results into one list |
| `count` | 0 | repetition means quantity (`-vvv`); **`default=0` mandatory** |
| `help` | -- | print to stdout, exit 0 (automatic) |
| `version` | -- | print to stdout, exit 0 |
| `BooleanOptionalAction` | 0 | `--flag` / `--no-flag`, tri-state `None` (3.9+) |

## `nargs`

| Value | Consumes |
|---|---|
| `None` (default) | exactly 1 |
| `0` | nothing (flag actions only; `store` rejects it at build time) |
| `?` | 0 or 1 -- use `const=` so the bare form works |
| `*` | 0 or more |
| `+` | 1 or more |
| `N` (int) | exactly N, as a **list** |
| `argparse.REMAINDER` | everything left, flags included (positionals only) |
| `argparse.PARSER` | everything left, recording subparser claims |
| `argparse.SUPPRESS` | omit the attribute entirely |
| `(m, n)` | **NOT SUPPORTED** -- click/typer accept it, argparse does not |

## Parser methods

```
add_argument / add_argument_group / add_mutually_exclusive_group
add_subparsers(dest=, required=, aliases=)
set_defaults(...)          install defaults for anything, not just arguments
get_default(dest)          read one default back
parse_args / parse_known_args / parse_intermixed_args
parse_known_intermixed_args
format_help / format_usage / print_help / print_usage
error / exit / exit_on_error
register('action'|'type', name, cls)
conflicts / _remove_action / _add_container_actions   (private; read before use)
```

## Command-line forms

```
prog --port 8080                 prog --port=8080
prog -p8080                      prog -p 8080
prog -v -v -v                    prog -vvv        <- NOT equivalent; -vv breaks
prog --                          everything after is positional
prog -abc                        bundled short flags, all flag-shaped
prog --opt=-1                    negative numbers need '='
@file.txt                        only with fromfile_prefix_chars='@'
```

## ArgumentParser constructor

```
prog=  description=  epilog=  parents=[]  formatter_class=
prefix_chars='-'  fromfile_prefix_chars=None  argument_default=None
conflict_handler='error'  add_help=True  allow_abbrev=True  exit_on_error=True
# 3.14+: suggest_on_error=False  color=True
```

## Formatters

| Class | Behaviour |
|---|---|
| `HelpFormatter` | the default |
| `ArgumentDefaultsHelpFormatter` | appends `(default: X)` |
| `RawDescriptionHelpFormatter` | does not re-wrap description/epilog |
| `RawTextHelpFormatter` | also does not wrap `help=` (destroys alignment) |
| `MetavarTypeHelpFormatter` | metavar defaults to `type.__name__` |

Methods worth overriding: `_format_usage`, `_format_text`, `_fill_text`,
`_format_action`, `_get_help_string`, `_expand_help`, `_format_args`,
`_get_default_metavar_for_optional` / `_positional`, `_split_lines`.

## Types

```
def my_type(raw: str) -> T:
    try:
        return convert(raw)
    except (argparse.ArgumentTypeError, ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is bad; expected ...")

# parameterised: must be a FACTORY, because type= gets one positional arg
parser.add_argument("--port", type=bounded_int(1, 65535))
```

Anything other than `ArgumentTypeError`/`ValueError`/`TypeError` escapes
`parse_args` as a traceback.

## Exit codes

```
0 ok   1 internal error   2 usage error (argparse)   3 bad data
4 bad config   5 missing input   6 partial success   130 SIGINT
```

## Precedence, lowest first

```
code defaults  <  config file  <  environment  <  argv
```

Install every non-argv layer with `set_defaults`, and argv wins for free.

## The five things people get wrong

1. `nargs=(m, n)` does not exist in argparse.
2. `default=[]` with `action='append'` is a shared mutable that leaks between parses.
3. `action='count'` with `default=None` raises `TypeError`.
4. `--opt -1` fails; use `--opt=-1`.
5. `set_defaults(x=<int>)` skips `type=`, and `set_defaults(x="<int>")` does not.

## The pattern that ends every review

```python
def build_parser() -> argparse.ArgumentParser: ...
def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None: ...
def cmd_x(args: argparse.Namespace) -> int: ...
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)   # accept argv; never read sys.argv
    validate(args, parser)           # before any side effect
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
```

See `../src/argparse_mastery/` for implementations of every row above.