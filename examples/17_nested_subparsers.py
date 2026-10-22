"""Lesson 17 - Nesting subparsers two levels deep.

Run me
------
    python examples/17_nested_subparsers.py remote add origin git@host:repo.git
    python examples/17_nested_subparsers.py --demo

When nesting is the right call
-------------------------------
``git remote add origin URL`` and ``kubectl config set-context NAME ...`` are the
canonical shapes. Nest when the *first* level is a noun and the second level is a
verb operating on that noun. Do **not** nest merely to group options -- use
``add_argument_group`` instead, which changes nothing about parsing.

Nesting is pure composition
---------------------------
There is no ``add_nested_subparsers``. You call ``add_subparsers`` on a
subparser, exactly as you did on the top-level parser::

    remote = sub.add_parser("remote", add_help=True)
    remote_sub = remote.add_subparsers(dest="remote_command", required=True)
    add = remote_sub.add_parser("add", add_help=True)
    add.add_argument("name")
    add.add_argument("url")

Everything from lesson 15 applies unchanged, and ``set_defaults(func=...)`` from
the leaf is what the top-level ``main`` dispatches to. Three consequences:

1. **Only the leaf has a ``func``.** ``prog remote`` alone fails, because the
   inner group is ``required=True``.
2. **Namespace keys must not collide.** Both levels write to the namespace. If
   the top level uses ``dest="command"`` and the middle uses
   ``dest="action"``, you get ``args.command == 'remote'`` and
   ``args.action == 'add'``. Use distinct names -- and read them in the handler
   rather than assuming ``args.command`` identifies the operation.
3. **Help output has a tree.** ``prog --help`` shows only the top level. You must
   discover the second level by reading ``prog remote --help``. That is the cost
   of nesting, and it is why some tools add ``prog remote add --help`` to their
   docs, and why flat commands often win.

Depth limits
------------
Three levels is where help becomes unusable and the ``hasattr(args, "func")``
chain gets silly. If you need four, a command *registry* keyed by path segments
is clearer than ``add_subparsers`` four times.

An alternative that scales better
---------------------------------
Build the parser from a nested data structure and generate it::

    COMMANDS = {
        "remote": {
            "add": {"args": [("name",), ("url",)], "help": "add a remote"},
            "remove": {...},
        },
    }

That is what ``argparse_mastery.builders`` demonstrates. It keeps command
definitions as data, which means plugins can contribute without import-time
coupling.

Before you nest: the flat alternative
--------------------------------------
::

    remote-add, remote-remove, remote-list

Flat names keep help on one screen, need no ``dest`` juggling, and compose fine
with shell completion. Many modern tools (``gh``, ``docker``'s flat-ish surface,
modern ``ruff``) choose flat or a single nesting level for exactly this reason.
"""

from __future__ import annotations

import argparse
import sys


def cmd_remote_add(args: argparse.Namespace) -> int:
    print(f"remote add: {args.name} -> {args.url} (fetch={args.fetch})")
    return 0


def cmd_remote_remove(args: argparse.Namespace) -> int:
    print(f"remote remove: {args.name}")
    return 0


def cmd_remote_list(args: argparse.Namespace) -> int:
    print("remote list: origin, upstream")
    return 0


def cmd_local_name(args: argparse.Namespace) -> int:
    print(f"config set: {args.key}={args.value!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitish",
        description="Lesson 17: nested subparsers.",
        epilog=(
            "Only the leaf has a func:\n"
            "  gitish remote add NAME URL   -> args.command='remote', "
            "args.subcommand='add'\n"
            "  gitish remote                -> error (inner group is required)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # ---- level 1: remote ----------------------------------------------
    remote = sub.add_parser(
        "remote",
        help="manage remotes",
        description="Add, remove and list remotes.",
        add_help=True,
    )
    # A distinct dest, so both levels can be read in the handler.
    remote_sub = remote.add_subparsers(
        dest="subcommand",
        metavar="<action>",
        required=True,
    )

    r_add = remote_sub.add_parser("add", help="add a remote", add_help=True)
    r_add.add_argument("name", help="remote name")
    r_add.add_argument("url", help="remote URL")
    r_add.add_argument(
        "--fetch",
        action="store_true",
        help="fetch immediately after adding",
    )
    r_add.set_defaults(func=cmd_remote_add)

    r_rm = remote_sub.add_parser("remove", aliases=["rm"], help="remove a remote", add_help=True)
    r_rm.add_argument("name", help="remote name")
    r_rm.set_defaults(func=cmd_remote_remove)

    r_ls = remote_sub.add_parser("list", aliases=["ls"], help="list remotes", add_help=True)
    r_ls.set_defaults(func=cmd_remote_list)

    # ---- level 1: config, with a leaf that is not a verb ---------------
    config = sub.add_parser("config", help="read and write config", add_help=True)
    config_sub = config.add_subparsers(dest="subcommand", metavar="<action>", required=True)

    c_set = config_sub.add_parser("set", help="set a config key", add_help=True)
    c_set.add_argument("key", help="dotted config key")
    c_set.add_argument("value", help="new value")
    c_set.set_defaults(func=cmd_local_name)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # required=True on the top-level group already guarantees a command, and
    # every leaf sets `func`. The hasattr check stays because it costs nothing
    # and keeps main() robust if a leaf is added without a handler.
    if not hasattr(args, "func"):
        parser.print_help()
        return 2

    return args.func(args)


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. Two levels, one dispatch site")
    print("=" * 78)
    cases = [
        ["remote", "add", "origin", "git@host:repo.git", "--fetch"],
        ["remote", "rm", "origin"],
        ["remote", "list"],
        ["config", "set", "user.email", "a@b.c"],
    ]
    for argv in cases:
        args = parser.parse_args(argv)
        print(f"  {argv!s:<56}")
        print(f"      command={args.command!r:<10} subcommand={args.subcommand!r:<10} "
              f"func={args.func.__name__}")

    print()
    print("=" * 78)
    print("2. Only the leaf has a func; intermediate levels must not be given")
    print("=" * 78)
    for argv in (["remote"], ["remote", "--help"]):
        print(f"  {argv!s:<22} -> {_error(parser, argv)}")
    print()
    print("  With required=True on the inner group, argparse reports it for you.")
    print("  Without it, `prog remote` would print help -- which is a legitimate")
    print("  choice for a container command that does nothing itself.")

    print()
    print("=" * 78)
    print("3. dest collisions across levels")
    print("=" * 78)
    args = parser.parse_args(["remote", "add", "o", "u"])
    print(f"  args.command    = {args.command!r}    (top-level dest)")
    print(f"  args.subcommand = {args.subcommand!r}   (inner dest)")
    print()
    print("  If both levels used dest='command', the second would overwrite the")
    print("  first and you would lose the path. Use distinct dests.")

    print()
    print("=" * 78)
    print("4. The help tree: three --help invocations to discover one command")
    print("=" * 78)
    print("  $ gitish --help")
    for line in parser.format_help().splitlines():
        if line.strip().startswith(("<command>", "remote", "config")):
            print(f"      {line.rstrip()}")
    print("  $ gitish remote --help")
    for line in _capture(parser, ["remote", "--help"]).splitlines():
        if line.strip().startswith(("<action>", "add", "remove", "list")):
            print(f"      {line.rstrip()}")
    print("  $ gitish remote add --help")
    for line in _capture(parser, ["remote", "add", "--help"]).splitlines():
        if line.strip().startswith("usage:"):
            print(f"      {line.rstrip()}")
    print()
    print("  This is the real cost of nesting. The flat alternative --")
    print("  remote-add, remote-remove, remote-list -- keeps everything on one screen.")

    print()
    print("=" * 78)
    print("5. Nesting is not 'add_argument_group'")
    print("=" * 78)
    print("  add_argument_group: heading only. Parsing is identical.")
    print("  add_subparsers:     a real parser, its own options, its own --help.")
    print()
    print("  Use a group to organise; use subparsers to add a command token.")

    return 0


def _capture(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            parser.parse_args(argv)
        except SystemExit:
            pass
    return buffer.getvalue()


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit:
        pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
