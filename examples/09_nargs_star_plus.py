"""Lesson 09 - ``nargs='*'`` and ``nargs='+'``: many values, and empty lists.

Run me
------
    python examples/09_nargs_star_plus.py --include a b c
    python examples/09_nargs_star_plus.py --demo

The only difference
-------------------
====================  ====================================  ==============================
``nargs``             Command line                          Missing
====================  ====================================  ==============================
``'*'``                ``--include a b c`` -> ``[a,b,c]``   ``--include`` -> ``[]``
                       ``--include``       -> ``[]``         omitted     -> ``default``
``'+'``                ``--include a b c`` -> ``[a,b,c]``   ``--include`` -> **error**
                       (at least one required)              omitted     -> ``default``
====================  ====================================  ==============================

Reach for ``'+'`` whenever "no values" is meaningless -- a list of files to
process, a list of hosts to ping. Reach for ``'*'`` when an empty list is a
meaningful outcome, or when you want ``--include`` with no arguments to mean
"include nothing, explicitly" as distinct from "not specified".

The default you choose changes the type you get
----------------------------------------------
::

    add_argument("--include", nargs="*")                  # absent -> None
    add_argument("--include", nargs="*", default=[])      # absent -> []
    add_argument("--include", nargs="*", default="")      # absent -> ""   <- a str!

The last one is not a joke. A bare ``default=""`` on a ``nargs='*'`` option
gives you an empty **string**, and then ``for item in args.include`` iterates
zero times by accident rather than by design. It will pass your tests and fail
in production. Use ``default=[]``.

And: ``default=[]`` is a shared mutable, exactly like lesson 07's
``append`` trap. The safe pattern is to copy inside a custom action, or to accept
``None`` and normalise at the top of your handler::

    args.include = args.include or []

Mixing ``nargs`` with ``action``
-------------------------------
``nargs='*'`` with ``action='append'`` collects one list per occurrence::

    --include a b --include c   ->   [['a', 'b'], ['c']]

Usually you want ``action='extend'`` (lesson 07) to get ``['a','b','c']``.

``'*'`` with a positional star and command-line separation
---------------------------------------------------------
With ``nargs=argparse.REMAINDER`` you capture flags too. With ``'*'`` you do not:
``prog --include -x`` tries to parse ``-x`` as an option, not as a value. If your
values may start with a dash, you need ``--`` (lesson 10) or a positional
``REMAINDER``.

A very common practical pattern
-------------------------------
Non-option flags plus a list of files, where order should not matter::

    prog --verbose --include a.txt b.txt --quiet

``parse_args`` rejects this, because it consumes positionals positionally. Two
fixes:

1. ``parser.parse_intermixed_args()`` (3.7+) -- options and positionals may be
   interleaved freely. Cannot be used with ``REMAINDER``/``PARSER`` positionals
   or with subparsers.
2. Put the list in ``nargs='+'`` rather than ``nargs='*'``, and require it last.
   Simple, but users will still try to put a flag after it.

Option 1 is what ``grep``, ``wc`` and ``sort`` need, and it is the reason
``parse_intermixed_args`` exists.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packer",
        description="Lesson 09: nargs='*' and '+'.",
        epilog=(
            "  packer --include a b c   many values\n"
            "  packer --include          '+' rejects this; '*' gives []\n"
            "  packer a.txt --verbose    use parse_intermixed_args for this\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # '+' : at least one. The right choice when an empty list is meaningless.
    include = parser.add_argument(
        "--include",
        nargs="+",
        metavar="PAT",
        help="one or more patterns to include",
    )

    # '*' : zero or more. The right choice when empty is a valid outcome.
    exclude = parser.add_argument(
        "--exclude",
        nargs="*",
        metavar="PAT",
        default=[],
        help="zero or more patterns to exclude",
    )

    # nargs='+' positionals: one or more files.
    files = parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="one or more input files",
    )

    # action='extend' flattens repeated occurrences into one list.
    extra = parser.add_argument(
        "--tag",
        action="extend",
        nargs="*",
        metavar="TAG",
        default=[],
        help="repeatable; results are flattened into one list",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"include : {args.include!r}")
    print(f"exclude : {args.exclude!r}   (never None, because default=[])")
    print(f"files   : {args.files!r}")
    print(f"tag     : {args.tag!r}")
    return 0


def demo() -> int:
    print("=" * 76)
    print("1. '*' accepts zero, '+' does not")
    print("=" * 76)
    star = argparse.ArgumentParser(prog="packer", add_help=False)
    star.add_argument("--items", nargs="*")
    plus = argparse.ArgumentParser(prog="packer", add_help=False)
    plus.add_argument("--items", nargs="+")

    for argv in (["--items"], ["--items", "a"], ["--items", "a", "b"], []):
        print(f"  {argv!s:<22} '*' -> {_value(star, argv)!r:<18} "
              f"'+' -> {_value(plus, argv)!r}")

    print()
    print("=" * 76)
    print("2. default decides whether an absent list is None or []")
    print("=" * 76)
    for default in (None, [], ""):
        p = argparse.ArgumentParser(prog="packer", add_help=False)
        p.add_argument("--items", nargs="*", default=default)
        got = p.parse_args([]).items
        print(f"  default={default!r:<8} -> {got!r:<6} type={type(got).__name__}")
    print()
    print("  default='' iterates zero times by accident, not by design.")
    print("  It passes tests and fails in production. Never.")

    print()
    print("=" * 76)
    print("3. default=[] is a shared mutable -- the append trap again")
    print("=" * 76)
    shared: list[str] = []
    leaky = argparse.ArgumentParser(prog="packer", add_help=False)
    leaky.add_argument("--items", nargs="+", action="append", default=shared)
    print(f"  parse 1 (--items a) : {leaky.parse_args(['--items', 'a']).items!r}")
    print(f"  parse 2 (--items b) : {leaky.parse_args(['--items', 'b']).items!r}")
    print(f"  parse 3 (nothing)   : {leaky.parse_args([]).items!r}")
    print("  Parse 3 should be [] but inherits earlier parses' data.")

    safe = argparse.ArgumentParser(prog="packer", add_help=False)
    safe.add_argument("--items", nargs="+", action="append")
    print()
    print(f"  no default, parse 3  : {safe.parse_args([]).items!r}   (clean)")

    print()
    print("=" * 76)
    print("4. action='append' vs 'extend' with nargs='*'")
    print("=" * 76)
    app = argparse.ArgumentParser(prog="packer", add_help=False)
    app.add_argument("--tag", action="append", nargs="*")
    ext = argparse.ArgumentParser(prog="packer", add_help=False)
    ext.add_argument("--tag", action="extend", nargs="*")
    for argv in (["--tag", "a", "b"], ["--tag", "a", "--tag", "b", "c"]):
        print(f"  {argv!s:<30}")
        print(f"      append -> {app.parse_args(argv).tag!r}")
        print(f"      extend -> {ext.parse_args(argv).tag!r}")

    print()
    print("=" * 76)
    print("5. '--' works on POSITIONALS, not on variable-width OPTIONS")
    print("=" * 76)
    positional = argparse.ArgumentParser(prog="packer", add_help=False)
    positional.add_argument("items", nargs="*")
    for argv in (["-x"], ["a", "-x"], ["--", "-x"], ["a", "--", "-x"]):
        print(f"  positional nargs='*'  {argv!s:<18} -> {_positional(positional, argv)!r}")

    star = argparse.ArgumentParser(prog="packer", add_help=False)
    star.add_argument("--items", nargs="*")
    print()
    for argv in (["--items", "-x"], ["--items", "--", "-x"]):
        print(f"  option nargs='*'       {argv!s:<26} -> {_option(star, argv)!r}")
    print()
    print("  The asymmetry is real and comes from the patterns argparse builds:")
    print("    positional  nargs='*'  ->  (-*[A-]*)   '-' absorbs '--'")
    print("    optional    nargs='*'  ->  (A*)        no room for '-'")
    print()
    print("  So: if values may start with a dash, take them as a POSITIONAL and")
    print("  document '--'. Or use REMAINDER, which needs no marker at all (lesson 10).")

    return 0


def _positional(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).items
    except SystemExit:
        return _error_from(buffer.getvalue())


def _option(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).items
    except SystemExit:
        return _error_from(buffer.getvalue())

    print()
    print("=" * 76)
    print("6. parse_args vs parse_intermixed_args with a 'files' positional")
    print("=" * 76)
    plain = argparse.ArgumentParser(prog="packer", add_help=False)
    plain.add_argument("files", nargs="+")
    plain.add_argument("--verbose", action="store_true")
    plain.add_argument("--quiet", action="store_true")

    for argv in (["--verbose", "a.txt", "b.txt"], ["a.txt", "--verbose", "b.txt"]):
        print(f"  parse_args         {argv!s:<32} -> {_value(plain, argv)!r}")
        print(f"  intermixed         {argv!s:<32} -> "
              f"{plain.parse_intermixed_args(argv)!r}")
    print()
    print("  parse_args rejects the second form: it matches positionals positionally,")
    print("  so 'a.txt' becomes files[0] and '--verbose' stops being an option.")
    print("  parse_intermixed_args exists for exactly this (grep, wc, sort).")

    return 0


def _value(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    """The value of the single argument, or the error message."""
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return vars(parser.parse_args(argv)).get("items")
    except SystemExit:
        return _error_from(buffer.getvalue())


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit:
        pass
    return _error_from(buffer.getvalue())


def _error_from(text: str) -> str:
    for line in text.splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
