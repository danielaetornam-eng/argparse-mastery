# Subparsers

## The anatomy

```python
sub = parser.add_subparsers(dest="command", required=True)
p = sub.add_parser("status", help="show status", description="...", add_help=True)
p.add_argument("--watch", action="store_true")
p.set_defaults(func=cmd_status)

args = parser.parse_args(argv)
if not hasattr(args, "func"):
    parser.print_help(sys.stderr)
    return 2
return args.func(args)
```

Five steps, and step 3 is the one nobody knows about: **nothing connects a
command name to a function unless you connect it.** `set_defaults(func=...)` is
the connector.

## Why `set_defaults(func=...)` beats an if-ladder

```python
if args.command == "status":  return cmd_status(args)
elif args.command == "start":  return cmd_start(args)
```

Both work. The ladder puts every command name in two places, so adding one means
finding both -- and forgetting the branch produces a silent `None` return. The
`set_defaults` form has one place, and a missing wiring is an `AttributeError` at
startup rather than a runtime surprise.

## Facts that are not in the docs

### `dest` records the choice

Without `dest=`, nothing records which command ran, which defeats the point.
`args.command` is that record.

### Parent options must precede the subcommand

```
prog --verbose status      works
prog status --verbose      unrecognized arguments
```

The subcommand token is a positional, and argparse consumes options before
matching positionals. This is not configurable away. To accept the second form,
declare the option on each subparser:

```python
def common_arguments() -> argparse.ArgumentParser:      # a FACTORY, see below
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-v", "--verbose", action="store_true")
    return p

sub.add_parser("status", parents=[common_arguments()])
```

### MYTH: subparser `-h` needs `add_help=True`

It works by default. `_SubParsersAction.add_parser` forwards `**kwargs` to the
parser class, and `ArgumentParser`'s own default is `add_help=True`. You can
remove it per subparser with `add_help=False`.

### Aliases, and no abbreviation

```python
sub.add_parser("remove", aliases=["rm", "delete"])
```

`prog rm` works and `args.command == "rm"` -- the spelling the *user* typed.
Normalise before logging.

And `prog rem` does **not** work. A subcommand is a `choices` entry and `choices`
uses `in`. `allow_abbrev` applies to long *options*, not to subcommand names. Every
spelling a user might type has to be declared.

### `parents=` shares Action objects

`parents=` does `self._add_container_actions(parent)`, which inserts the *same*
Action instances. Consequences:

* An `action='append'` with a list default is shared across every child, and one
  child's data leaks into another's.
* `child.set_defaults(x=...)` mutates the shared action, and siblings see it.

Fix both with a **factory**:

```python
def common_arguments() -> argparse.ArgumentParser:
    ...            # a fresh parser per call
```

One function call per parser, and an entire category of bug disappears.

### `_SubParsersAction` has no `set_defaults`

```python
sub.set_defaults(color=True)     # AttributeError
```

It is an `Action`, not a parser. Loop instead:

```python
for child in sub.choices.values():
    child.set_defaults(color=True)
```

Which is exactly what `parents=` does, with the shared-object caveats above.

### Defaults are per-subparser, not shared

```python
args = parser.parse_args(["push", "origin"])
args.retries      # AttributeError -- `push` never declared it
```

Not `None`: *absent*. Code that reads across commands needs
`getattr(args, "retries", 3)`, or the option declared everywhere.

### `parser_class`

Defaults to `type(self)`, so a subclass propagates. Override only to force
`argparse.ArgumentParser`. The signature in 3.14 is
`add_parser(name, *, deprecated=False, **kwargs)`.

## Nesting

```python
remote = sub.add_parser("remote", parents=[common_arguments()])
remote_sub = remote.add_subparsers(dest="remote_command", required=True)
add = remote_sub.add_parser("add")
add.add_argument("name"); add.add_argument("url")
```

Pure composition -- call `add_subparsers` on a subparser. Three consequences:

1. **Only the leaf has a `func`.** `prog remote` alone fails.
2. **Use distinct `dest`s** at each level, or the second overwrites the first.
3. **Help becomes a tree.** `prog remote add --help` needs three `--help`
   invocations to discover.

Nesting is right when the first level is a noun and the second a verb (`git remote
add`). Wrong as a way to group options -- use `add_argument_group`, which changes
nothing about parsing. Three levels is where help becomes unusable; past that, a
command registry keyed by path segments is clearer than `add_subparsers` four
times.

## Help quality

```python
sub.add_parser(
    "status",
    help="show service status",              # ONE LINE, for `prog --help`
    description="Report health for one or all services.\n\nExamples:\n  ...",
    epilog="Exit codes: 0 healthy, 1 unhealthy, 2 usage error.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
```

`help=` and `description=` do different jobs. `help=` is the one-line summary in
the parent's help -- argparse uses only its first line. `description` is the
full text in the child's own `--help`. Forgetting `help=` gives a blank summary,
which is worse than not listing the command at all.

`metavar="<command>"` replaces the noisy default usage line:

```
without:  usage: prog [-h] {status,start,stop,deploy,logs,config,version} ...
with:     usage: prog [-h] <command> ...
```

Both still list the commands in the help body; only the usage line differs.

## Deprecating a command

3.14's native path:

```python
sub.add_parser("purge", deprecated=True, help=argparse.SUPPRESS)
```

`prog: warning: command 'purge' is deprecated` on stderr, then it runs, exit 0.
`deprecated=True` does not hide it -- add `help=SUPPRESS` for that.

On 3.11-3.13, or to control the message:

```python
purge = sub.add_parser("purge", help=argparse.SUPPRESS)
purge.set_defaults(func=cmd_purge, _deprecated="1.2 -> use 'prune'")
# and warn in main, after parsing, where the full namespace is available
```

## Completion

Subcommand names are the highest-value completion target, and argparse gives you
them:

```python
sub_action.choices                          # {'add': parser, 'rm': parser}
getattr(sub_action.choices['add'], 'aliases', ())   # aliases, if any
```

Note `getattr` with a default: a parser without aliases has no `aliases`
attribute at all, and that trips people up. Then generate the script (lesson 36).

## Testing subparsers

```python
def test_missing_command(capsys):
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args([])
    assert excinfo.value.code == 2
    assert "the following arguments are required" in capsys.readouterr().err

def test_unknown_command():
    with pytest.raises(SystemExit):
        parser.parse_args(["staus"])
```

And the structural checks worth asserting once:

```python
def test_parents_do_not_share_action_objects():
    first, second = common_arguments(), common_arguments()
    assert {id(a) for a in first._actions}.isdisjoint({id(a) for a in second._actions})
```

Lessons 15-19; pitfalls 18, 23-27.