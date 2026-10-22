"""Lesson 01 - The minimum viable parser, and what it actually returns.

Run me
------
    python examples/01_hello_world.py --name Ada
    python examples/01_hello_world.py --help

The three ideas
---------------
1. You build a parser with :class:`argparse.ArgumentParser`.
2. You describe arguments with ``add_argument``.
3. ``parse_args`` returns an :class:`argparse.Namespace` -- an object whose
   attributes are named after the *longest* option string.

Step 3 is the one that surprises people. Watch what happens to ``dest`` here::

    parser.add_argument("--dry-run")
    ...
    args.dry_run          # NOT args.dry-run

``--dry-run`` becomes ``dry_run``: argparse strips the leading dashes and
replaces internal hyphens with underscores. That transformation is the single
most useful fact in this lesson. Every other naming decision follows from it.

Where the option's name comes from
----------------------------------
argparse computes ``dest`` like this:

* Take the **first long option** (one starting with ``--``). If none exists,
  take the first short option.
* Strip leading dashes.
* Replace ``-`` with ``_``.

So:

===================================  ==================
Declared                             Namespace attribute
===================================  ==================
``--dry-run``                        ``dry_run``
``-n``                               ``n``
``-n/--name``                        ``name``
``-vv``                              ``v`` (see lesson 07)
``"infile"`` (positional)            ``infile``
===================================  ==================

Two names beat one
------------------
Always declare both a short and a long form for anything users type often. The
long form is what appears in docs and scripts; the short form is what appears in
a shell. The one exception is ``-h``, which argparse gives you for free.

Why ``parse_args`` reads ``sys.argv``
-------------------------------------
``parse_args(argv=None)`` uses ``sys.argv[1:]`` when you pass nothing. Pass an
explicit list instead and the function becomes testable -- a habit this whole
repository follows::

    main(argv=None) -> parser.parse_args(argv)

so a test can call ``main(["--name", "Ada"])`` with no ``sys.argv`` patching.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    """Build the parser.

    Splitting parser construction into its own function is *the* structural
    habit worth forming, for three reasons:

    * it can be called more than once (argparse parsers accumulate state),
    * it can be unit-tested without running anything,
    * ``--help`` and the real parse share one definition, so they cannot drift.
    """
    parser = argparse.ArgumentParser(
        # prog is what the help and usage lines call the program. If omitted,
        # argparse guesses from sys.argv[0], which is `python` when you run
        # `python -m ...` and the full path when installed. Set it explicitly.
        prog="greet",
        description="Greet someone. Lesson 01: the shape of every argparse CLI.",
        epilog="Try: greet --name Ada --greeting Hello",
    )

    # An option (also called a flag or a switch): starts with a dash, and
    # because it takes a value it must be given one.
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default="world",
        help="who to greet",
    )

    # `store_true` makes it a boolean flag that takes NO value:
    #     greet --shout
    # not `greet --shout true`. That is lesson 02's whole subject.
    parser.add_argument(
        "--shout",
        action="store_true",
        help="uppercase the greeting",
    )

    # A positional (not called a flag): position is the name. Optional if it has
    # a default.
    parser.add_argument(
        "extra",
        nargs="?",
        default=None,
        help="an optional trailing word, e.g. a team name",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse and act. Returns an exit code; never calls sys.exit itself."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # `vars(namespace)` turns the Namespace into a plain dict. Do this in a
    # REPL when you are unsure what a parser produced -- it is the fastest way to
    # see exactly which attributes exist.
    print("namespace as dict:", vars(args))
    print("attribute access: ", f"args.name={args.name!r} args.shout={args.shout!r}")

    greeting = f"Hello, {args.name}"
    if args.extra:
        greeting = f"{greeting} ({args.extra})"
    if args.shout:
        greeting = greeting.upper()
    print(greeting)
    return 0


def demo() -> int:
    """Show the parser's behaviour from three different command lines."""
    parser = build_parser()

    print("=" * 72)
    print("1. defaults only -- nothing supplied")
    print("=" * 72)
    print(parser.parse_args([]))

    print()
    print("=" * 72)
    print("2. options in all three accepted spellings")
    print("=" * 72)
    for argv in (["--name", "Ada"], ["-n", "Ada"], ["--name=Ada"]):
        args = parser.parse_args(argv)
        print(f"  argv={argv!r:<24} -> args.name = {args.name!r}")

    print()
    print("=" * 72)
    print("3. dest derivation, demonstrated")
    print("=" * 72)
    for action in parser._actions:
        if action.dest == "help":
            continue
        print(f"  declared {action.option_strings!r:<18} -> dest={action.dest!r}")

    print()
    print("=" * 72)
    print("4. what a missing required value looks like (exit status 2)")
    print("=" * 72)
    print("  $ greet --name")
    print("  " + "\n  ".join(_error_for(parser, ["--name"])))
    return 0


def _error_for(parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """Run a failing parse, capture stderr, and return it as a list of lines."""
    import io
    from contextlib import redirect_stderr

    buffer = io.StringIO()
    try:
        with redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit as exc:
        return [
            f"usage: {parser.prog} ...",
            f"error: (exit status {exc.code})",
            *buffer.getvalue().splitlines(),
        ]
    return ["(unexpectedly succeeded)"]


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
