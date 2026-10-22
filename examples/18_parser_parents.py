"""Lesson 18 - Parent parsers: sharing arguments without duplicating them.

Run me
------
    python examples/18_parser_parents.py remote add origin --verbose
    python examples/18_parser_parents.py --demo

The mechanism
-------------
``parents=[...]`` copies actions from one parser to another::

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true")
    common.add_argument("--profile", default="default")

    remote = sub.add_parser("remote", parents=[common])

Why you need it
---------------
Recall from lesson 15 that a parent's options must precede the command:
``prog --verbose status`` works, ``prog status --verbose`` does not. Users will
always try the second form. ``parents=`` solves it: the shared options are
declared **on each subparser**, so they work in the position users expect.

This is the standard fix and the reason ``add_help=False`` appears on parent
parsers everywhere -- otherwise the child would get a second ``-h``.

The four rules that matter
--------------------------
1. **The parent must be built with ``add_help=False``** if the child keeps its
   own help. Otherwise ``prog remote --help`` reports a conflicting option.
2. **Parents share action *objects*, not copies.** ``parents=`` does
   ``self._add_container_actions(parent)``, which inserts the *same* Action
   instances. This means:
   * ``action='append'`` with a list default on a parent is shared across every
     child that inherits it -- one child's tags leak into another's.
   * The parent's default must be a value you are happy to see everywhere.
   * Never register the same parent on a parser twice.
3. **Order matters for defaults.** Because the same Action object is reused, a
   child calling ``set_defaults(profile='prod')`` mutates the shared action, and
   every sibling sees it. Prefer passing a different default in the child's own
   ``add_argument`` when you need to vary it, or use a *function* as a parent.
4. **Subparsers are created per parent**, so a three-level tree needs the parent
   applied at each level.

A factory beats a module-level parent
-------------------------------------
Because of rule 2, a **function that builds a fresh parent** is safer::

    def common_arguments() -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--verbose", "-v", action="store_true")
        p.add_argument("--profile", default="default")
        return p

    remote = sub.add_parser("remote", parents=[common_arguments()])

Each child gets its own Action objects, so defaults cannot leak. This costs one
function call per parser and removes an entire category of bug. Prefer it.

The alternative: parse in two passes
------------------------------------
``parser.parse_known_args`` twice is a way to accept global options in any
position, at the cost of real complexity. Reach for ``parents=`` first.
"""

from __future__ import annotations

import argparse
import sys


def common_arguments() -> argparse.ArgumentParser:
    """Build a FRESH parent parser.

    A function rather than a module-level constant, because ``parents=``
    reuses Action *objects*: a shared ``default=[]`` list would be mutated by
    every child that inherits it. Building fresh costs nothing and eliminates the
    whole class of bug.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="chatty output",
    )
    parent.add_argument(
        "--profile",
        default="default",
        metavar="NAME",
        help="configuration profile",
    )
    return parent


def output_arguments() -> argparse.ArgumentParser:
    """A second parent, to show that several can be combined."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    return parent


def cmd_remote_add(args: argparse.Namespace) -> int:
    print(f"remote add: {args.name} (verbose={args.verbose} profile={args.profile})")
    return 0


def cmd_remote_list(args: argparse.Namespace) -> int:
    print(f"remote list: origin (format={args.format})")
    return 0


def cmd_remote_prune(args: argparse.Namespace) -> int:
    print(f"remote prune: dry_run={args.dry_run}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet",
        description="Lesson 18: shared arguments via parents=.",
    )

    # Global options on the top level. These still must precede the command.
    parser.add_argument("--config", metavar="PATH", help="global config file")

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    remote = sub.add_parser(
        "remote",
        help="manage remotes",
        parents=[common_arguments(), output_arguments()],
    )
    remote_sub = remote.add_subparsers(
        dest="subcommand", metavar="<action>", required=True
    )

    r_add = remote_sub.add_parser("add", parents=[common_arguments()])
    r_add.add_argument("name", help="remote name")
    r_add.set_defaults(func=cmd_remote_add)

    r_list = remote_sub.add_parser("list", parents=[output_arguments()])
    r_list.set_defaults(func=cmd_remote_list)

    r_prune = remote_sub.add_parser("prune", parents=[common_arguments()])
    r_prune.add_argument("--dry-run", action="store_true", help="show only")
    r_prune.set_defaults(func=cmd_remote_prune)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def demo() -> int:
    print("=" * 78)
    print("1. parents= lets a shared option work AFTER the command")
    print("=" * 78)
    parser = build_parser()
    cases = [
        ["remote", "add", "origin", "--verbose"],
        ["remote", "add", "--verbose", "origin"],
        ["remote", "add", "origin", "-v", "--profile", "prod"],
        ["--config", "a.toml", "remote", "add", "origin"],
    ]
    for argv in cases:
        args = parser.parse_args(argv)
        print(f"  {argv!s:<54} verbose={args.verbose!s:<6} profile={args.profile!r}")

    print()
    print("=" * 78)
    print("2. Rule 1: the parent must not have its own -h")
    print("=" * 78)
    try:
        bad_parent = argparse.ArgumentParser()  # add_help=True by default
        bad_parent.add_argument("--shared")
        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_subparsers(dest="command").add_parser("go", parents=[bad_parent])
    except argparse.ArgumentError as exc:
        print(f"  argparse.ArgumentError: {exc}")
    print("  add_help=False on the parent avoids the duplicate -h.")

    print()
    print("=" * 78)
    print("3. Rule 2: parents share Action OBJECTS, not copies")
    print("=" * 78)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--tag", action="append", default=[])
    left = argparse.ArgumentParser(prog="x", add_help=False)
    left.add_subparsers(dest="c").add_parser("a", parents=[shared])
    right = argparse.ArgumentParser(prog="x", add_help=False)
    right.add_subparsers(dest="c").add_parser("b", parents=[shared])

    print(f"  left.a  parse --tag one -> {left.parse_args(['a', '--tag', 'one']).tag!r}")
    print(f"  right.b parse (nothing) -> {right.parse_args(['b']).tag!r}")
    print()
    print("  The second parse sees 'one'. Same list object, two parsers.")
    print("  Building a fresh parent per child (a factory) avoids this entirely.")

    print()
    print("=" * 78)
    print("4. Rule 3: set_defaults on a child mutates the shared action")
    print("=" * 78)
    shared2 = argparse.ArgumentParser(add_help=False)
    shared2.add_argument("--profile", default="default")
    top = argparse.ArgumentParser(prog="x", add_help=False)
    sub = top.add_subparsers(dest="c")
    a = sub.add_parser("a", parents=[shared2])
    b = sub.add_parser("b", parents=[shared2])
    a.set_defaults(profile="prod")
    print("  after a.set_defaults(profile='prod'):")
    print(f"    a's default is now {top.parse_args(['a']).profile!r}")
    print(f"    b's default is now {top.parse_args(['b']).profile!r}   <- leaked")
    print()
    print("  Pass the differing default in the child's own add_argument instead,")
    print("  or use a factory so each child gets its own Action.")

    print()
    print("=" * 78)
    print("5. Rule 4: apply the parent at every level you need it")
    print("=" * 78)
    print(f"  prog remote add origin --verbose   -> {parser.parse_args(['remote','add','origin','--verbose']).verbose}")
    print(f"  prog remote --verbose add origin   -> {_error(parser, ['remote', '--verbose', 'add', 'origin'])}")
    print()
    print("  'remote' has the parent, so --verbose is valid right after it.")
    print("  But it cannot appear after 'add', because 'add' has its own parser")
    print("  and the shared flag was not attached there.")
    print("  That is the rule to remember: a shared option works at exactly the")
    print("  depths where its parent was attached.")

    return 0


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
