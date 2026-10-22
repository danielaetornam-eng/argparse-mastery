"""Lesson 04 - Positionals: ordering, optional-ness, and how argparse matches them.

Run me
------
    python examples/04_positionals.py build src/ output/
    python examples/04_positionals.py --demo

The rule that governs everything here
-------------------------------------
**argparse matches positionals in the order you declared them, against the
remaining tokens, using a regex built from each positional's ``nargs``.**

That single sentence explains every surprise in this lesson.

Optional positionals
--------------------
A positional with ``nargs="?"`` may be omitted. The important subtlety is that
the *last* declared positional is the one that gets to be optional. Declared as::

    source   nargs="?"      # may be absent
    dest     nargs="?"      # may be absent

then ``prog a`` gives ``source="a"``, ``dest=None`` -- which is almost never what
a user means. They meant ``source="a"`` and *no* ``dest``, or ``a`` and the
default for the second. There is no way for argparse to know, and it always
fills greedily from the left.

Order of declaration vs order on the command line
------------------------------------------------
This is fixed and people fight it::

    prog one two      # exactly
    prog two one      # still exactly -- just assigned differently

There is no "flexible" positional in argparse. If you need it, accept
``nargs="*"`` and validate yourself, or switch to subparsers.

``required`` on a positional
----------------------------
``required=True`` is not a legal keyword for positionals at all -- argparse
rejects it while the parser is being *built*::

    add_argument("x", nargs="?", required=True)
    # TypeError: 'required' is an invalid argument for positionals

That is argparse being helpful: the check is positional, so it is always
satisfied. Failing at definition time rather than at parse time means your
program breaks at startup, where you will see it, instead of at the moment a
user omits an argument.

Multiple positionals, mixed optionality
---------------------------------------
The legal shapes are::

    prog A                    source required, dest absent
    prog A B                  both required
    prog A [B]                dest optional  <- the common shape
    prog [A] [B]              both optional   <- always fills left to right

The last one is why ``prog`` alone with two ``nargs='?'`` positionals is
confusing: you cannot express "only the second may be given".
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="builder",
        description="Lesson 04: positional arguments.",
        epilog=(
            "Positional args are matched in declaration order, not by name.\n"
            "Only a trailing nargs='?' positional can be omitted on its own."
        ),
    )

    # Required positional: nargs=None (the default) means exactly one token.
    source = parser.add_argument(
        "source",
        help="source file to read (required, exactly one token)",
    )

    # Optional trailing positional. This is the shape that behaves as expected.
    dest = parser.add_argument(
        "dest",
        nargs="?",
        default="-",
        help="where to write; defaults to stdout ('-')",
    )

    # 'src' is the metavar; the dest is still `paths`. Demonstrates that a
    # readable placeholder and a predictable attribute name are independent.
    paths = parser.add_argument(
        "paths",
        nargs="*",
        metavar="SRC",
        help="additional inputs (zero or more)",
    )

    # `help` on a positional is legal but only shown by RawTextHelpFormatter or a
    # custom one. See lesson 27. Worth knowing: it is not an error to pass it.
    source.help = source.help  # no-op, explicit for the reader

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"source : {args.source!r}   (required)")
    print(f"dest   : {args.dest!r}   (optional, default '-')")
    print(f"paths  : {args.paths!r}   (zero or more)")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 76)
    print("1. The same two positionals, filled greedily from the left")
    print("=" * 76)
    print(f"{'argv':<34} {'source':<14} {'dest':<10} paths")
    print("-" * 76)
    for argv in (
        [],
        ["a"],
        ["a", "b"],
        ["a", "b", "c"],
        ["a", "b", "c", "d"],
    ):
        try:
            ns = parser.parse_args(argv)
        except SystemExit:
            print(f"{argv!s:<34} (error: the required 'source' is missing)")
            continue
        print(f"{argv!s:<34} {ns.source!r:<14} {ns.dest!r:<10} {ns.paths!r}")

    print()
    print("Note row 2: `a` fills `source`, and `dest` takes its default.")
    print("argparse does not skip over a filled positional to reach a later one.")

    print()
    print("=" * 76)
    print("2. required=True is rejected for positionals at BUILD time")
    print("=" * 76)
    try:
        argparse.ArgumentParser(prog="x", add_help=False).add_argument(
            "src", nargs="?", required=True
        )
    except TypeError as exc:
        print(f"  TypeError: {exc}")
    print("  It fails when the parser is built, not when the user runs it.")
    print("  Compare: an OPTION may be required=True, and may combine with")
    print("  default=SUPPRESS so --help does not advertise a meaningless default.")

    print()
    print("=" * 76)
    print("3. Positionals are positional: no keyword form exists")
    print("=" * 76)
    for argv in (["--source", "a"], ["src=a"]):
        print(f"  $ builder {' '.join(argv)}")
        print(f"    {_error_line(parser, argv)}")
    print("  If you want named inputs, they are OPTIONS, not positionals.")

    print()
    print("=" * 76)
    print("4. Two optional positionals: you cannot give only the second")
    print("=" * 76)
    both = argparse.ArgumentParser(prog="two", add_help=False)
    both.add_argument("a", nargs="?")
    both.add_argument("b", nargs="?")
    print(f"  argv=[]        -> a={both.parse_args([]).a!r}      b={both.parse_args([]).b!r}")
    print("  argv=['x']     -> a='x'      b=None")
    print("  There is no syntax for 'b only'. Use --b or subparsers instead.")

    print()
    print("=" * 76)
    print("5. How argparse actually matches: the regex it builds")
    print("=" * 76)
    for action in parser._actions:
        if action.option_strings:
            continue
        pattern = _nargs_pattern(action.nargs)
        print(f"  {action.dest:<8} nargs={action.nargs!s:<5} matches {pattern}")

    return 0


def _nargs_pattern(nargs: int | str | None) -> str:
    """Describe how a positional consumes tokens, in regex terms.

    Reproduces argparse's internal ``_get_nargs_pattern`` so you can reason about
    matching without reading the source. The labels:
        A  one token
        B  then one more
        -A one token only if it has not already been consumed
        *A any number, including none
        +A one or more
    """
    if nargs is None:
        return "A  (exactly one)"
    if nargs == argparse.OPTIONAL:
        return "-A (one, or none -> default)"
    if nargs == argparse.ZERO_OR_MORE:
        return "*A (any number, including none)"
    if nargs == argparse.ONE_OR_MORE:
        return "+A (one or more)"
    if nargs == argparse.REMAINDER:
        return "([-AO]*) (everything left, options included)"
    if nargs == argparse.PARSER:
        return "([-AO]*) (rest is a nested parser's argv)"
    if isinstance(nargs, int):
        return "A" * nargs + f"  (exactly {nargs})"
    return f"?{nargs}"


def _error_line(parser: argparse.ArgumentParser, argv: list[str]) -> str:
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
