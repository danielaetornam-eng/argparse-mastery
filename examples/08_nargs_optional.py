"""Lesson 08 - ``nargs='?'``: zero or one, and the ``const`` trap.

Run me
------
    python examples/08_nargs_optional.py --color
    python examples/08_nargs_optional.py --color always --demo

The three-way branch
--------------------
``nargs='?'`` gives you an option with three distinct outcomes, which is more than
any other ``nargs`` value:

=========================  ==========================  ======================
Command line                ``args.color``             ``const`` used?
=========================  ==========================  ======================
(absent)                    ``default``                 no
``--color``                 ``const``                   **yes**
``--color always``          ``"always"``                no
=========================  ==========================  ======================

If you do not set ``const``, the middle row raises::

    prog: error: argument --color: expected one argument

That is the single most common ``nargs='?'`` surprise, and the whole point of
the pattern is that ``--color`` alone *should* work.

The idiomatic shape
-------------------
::

    parser.add_argument(
        "--color",
        nargs="?",
        const="always",       # what --color alone means
        default="auto",       # what omitting it means
        choices=("always", "never", "auto"),
    )

Now all three spellings are valid and all three land in ``choices``:

    prog                  -> "auto"
    prog --color          -> "always"
    prog --color never    -> "never"

Note that ``choices`` is checked against the value in all three rows, including
the ``const``. If ``const`` is not in ``choices`` you get a runtime error on
``prog --color`` that is baffling to read, because the user did nothing wrong.

This pattern is how you support ``--json``/``--json=2``/``--json=compact`` in a
single argument.

A flag needs no ``nargs`` -- this is not one of those cases
-----------------------------------------------------------
``--verbose`` is a *flag*: it takes no value. ``action="store_true"`` implies
``nargs=0``. Reaching for ``nargs='?'`` on a boolean is a mistake, and it shows:
you have to invent a ``const`` that is not a boolean, and ``choices`` no longer
reflects the type.

``nargs='?'`` and following positionals
--------------------------------------
This is the genuinely confusing corner. With an optional option and a positional
that both want the next token::

    parser.add_argument("--from")          # nargs=None: exactly 1
    parser.add_argument("archive")         # nargs='?'

    prog --from a            -> from="a"        archive=None
    prog --from              -> error (--from needs a value)
    prog a                   -> error (archive is positional; a is the archive)

argparse resolves the ambiguity with a greedy regex match over the whole command
line, and ``nargs='?'`` participates in that match. In practice: **avoid a
required option immediately followed by an optional positional.** If you cannot,
give the option a distinct name and check both dests yourself after parsing.

What ``nargs='?'`` means for ``nargs=None``
-------------------------------------------
``None`` and ``0`` are different: ``None`` means "exactly one" for an action, and
``nargs=0`` means "take no argument" (which is what ``store_true`` uses). Passing
``nargs=0`` to a plain ``store`` action gives you an option that can never be
satisfied -- the flag appears in help but can never be supplied usefully.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="printer",
        description="Lesson 08: nargs='?' and the const trap.",
        epilog=(
            "Three states, one argument:\n"
            "  printer                  -> color='auto'\n"
            "  printer --color          -> color='always'\n"
            "  printer --color never    -> color='never'\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # The canonical nargs='?' with const. All three rows are valid and all three
    # satisfy `choices`.
    parser.add_argument(
        "--color",
        nargs="?",
        const="always",
        default="auto",
        choices=("always", "never", "auto"),
        metavar="WHEN",
        help="colorize output; bare --color means 'always'",
    )

    # A verbosity ladder that also accepts an explicit level. `-v`, `-vv` style
    # repetition is NOT possible with nargs='?' (see lesson 05), so this is a
    # different shape: one option, an optional value.
    parser.add_argument(
        "--verbosity",
        "-V",
        nargs="?",
        const="info",
        default="warning",
        choices=("debug", "info", "warning", "error"),
        metavar="LEVEL",
        help="bare -V means 'info'",
    )

    # POSITIONAL nargs='?': one token or none, using `default`.
    source = parser.add_argument(
        "source",
        nargs="?",
        default="-",
        metavar="FILE",
        help="file to read; '-' means stdin (default)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"color    : {args.color!r}")
    print(f"verbosity: {args.verbosity!r}")
    print(f"source   : {args.source!r}")
    return 0


def demo() -> int:
    print("=" * 74)
    print("1. The three-way branch, with const set")
    print("=" * 74)
    parser = build_parser()
    for argv, note in (
        ([], "absent -> default"),
        (["--color"], "bare -> const"),
        (["--color", "never"], "with value"),
    ):
        print(f"  {argv!s:<20} {note:<20} -> {parser.parse_args(argv).color!r}")

    print()
    print("=" * 74)
    print("2. Without const, the bare form is an error")
    print("=" * 74)
    no_const = argparse.ArgumentParser(prog="printer", add_help=False)
    no_const.add_argument("--color", nargs="?", default="auto")
    print(f"  {'--color':<20} {'(no const)':<20} -> {_error(no_const, ['--color'])}")
    print(f"  {'--color never':<20} {'(no const)':<20} -> {no_const.parse_args(['--color','never']).color!r}")
    print()
    print("  'expected one argument' is argparse's way of saying you forgot const.")
    print("  If you never want a bare form, use plain store (nargs=None) instead.")

    print()
    print("=" * 74)
    print("3. const must satisfy choices, or bare --color fails confusingly")
    print("=" * 74)
    bad = argparse.ArgumentParser(prog="printer", add_help=False)
    bad.add_argument("--color", nargs="?", const="YES", default="auto", choices=("always", "never", "auto"))
    print("  const='YES', choices=(always,never,auto)")
    print(f"    --color       -> {_error(bad, ['--color'])}")
    print("  Keep const inside choices. The user did nothing wrong here.")

    print()
    print("=" * 74)
    print("4. nargs='?' on an OPTION vs a POSITIONAL")
    print("=" * 74)
    print(f"  {'[]':<22} -> color={parser.parse_args([]).color!r} source={parser.parse_args([]).source!r}")
    print(f"  {'[in.txt]':<22} -> color={parser.parse_args(['in.txt']).color!r} source={parser.parse_args(['in.txt']).source!r}")
    print("  Positional nargs='?' consumes the next token; option nargs='?' only")
    print("  consumes a token that immediately follows the option.")

    print()
    print("=" * 74)
    print("5. A bare flag must NOT use nargs='?'")
    print("=" * 74)
    flag = argparse.ArgumentParser(prog="printer", add_help=False)
    flag.add_argument("--verbose", action="store_true")
    print(f"  store_true         -> {flag.parse_args(['--verbose']).verbose!r}")
    weird = argparse.ArgumentParser(prog="printer", add_help=False)
    weird.add_argument("--verbose", nargs="?", const=True, default=False)
    print(f"  nargs='?' const=True-> {weird.parse_args(['--verbose']).verbose!r}  "
          f"(works, but now verbose can also be the STRING 'loud')")
    print(f"                              {weird.parse_args(['--verbose','loud']).verbose!r}")
    print("  Use store_true or BooleanOptionalAction for booleans.")

    print()
    print("=" * 74)
    print("6. nargs=0 is rejected outright on a store action")
    print("=" * 74)
    print("  nargs=0 means 'takes no value', which is meaningless for store.")
    print("  argparse catches it while building the parser:")
    try:
        argparse.ArgumentParser(prog="printer", add_help=False).add_argument(
            "--thing", nargs=0
        )
    except ValueError as exc:
        for line in str(exc).split("; "):
            print(f"    {line}")
    print()
    print("  Use action='store_true', 'store_const', or 'append' instead.")
    print()
    print("  Note that None and 0 are NOT interchangeable:")
    print("    nargs=None  -> 'exactly one token'")
    print("    nargs=0     -> 'no token' (flag actions only)")

    return 0


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """The ``error: ...`` line from a failing parse, with usage suppressed."""
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
            return line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
