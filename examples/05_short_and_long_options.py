"""Lesson 05 - Short and long options, multi-character short options, prefix_chars.

Run me
------
    python examples/05_short_and_long_options.py -v 2 -o out.txt in.txt
    python examples/05_short_and_long_options.py --demo

Adding several option strings
-----------------------------
``add_argument("-v", "--verbose", ...)`` registers both, and they write to the
same ``dest``. Which one becomes the dest is decided by the rule from lesson 01:
first long option, else first short.

======================  ==============  ==========================================
Declared                dest            Note
======================  ==============  ==========================================
``"-v", "--verbose"``   ``verbose``     long wins
``"--verbose", "-v"``   ``verbose``     long still wins -- order is irrelevant
``"-v"``                ``v``           no long form, so the short one is it
``"-vv"``               ``v``           see the trap below
======================  ==============  ==========================================

The ``-vv`` trap
---------------
argparse treats ``-vv`` as ``-v v``, not as "verbose twice". Its option parser
scans a single-dash argument for *short* option characters::

    parser.add_argument("-v", action="count")
    # "-vv"      -> it tries to look up "-v", then "-v" again
    # "-abc"     -> looks up -a, -b, -c

Because the *same* character appears twice, argparse first consumes one ``-v``
and then treats the second ``v`` as the **value** of ``-v``. For a
``store_true``/``count`` action that needs a value, you get::

    $ prog -vv
    prog: error: argument -v: expected one argument

The correct spellings for repetition are ``-v -v`` and ``--verbose --verbose``
(plus ``-vvv`` if you declare ``-vvv`` explicitly, which does *not* work because
the chars are consumed individually).

Multi-character single-dash options
-----------------------------------
Some tools use ``-vvv``, ``-ddd``, ``-json``. argparse supports this only via
``prefix_chars``, and even then with caveats::

    parser = ArgumentParser(prefix_chars="-+")
    parser.add_argument("-vvv", action="count")   # now a real, distinct option

This is fragile and non-standard. Prefer ``-v -v -v`` and ``--verbose``. If you
must support it, declare every accepted spelling explicitly.

``prefix_chars``
----------------
The default is ``"-"``: options start with one or two dashes, ``-`` and ``--``.
Changing it to ``"-+"`` lets you **declare** options that begin with ``+``
(Java/GNU style)::

    parser = ArgumentParser(prefix_chars="-+")
    parser.add_argument("+quiet", "-q", action="store_true")   # now valid

Three consequences, all of which surprise people:

* It does **not** make ``+v`` an alias for ``-v``. ``prefix_chars`` changes which
  characters may *begin* an option; the rest must still match a declared option
  string exactly (subject to ``allow_abbrev``).
* ``--`` (the end-of-options separator) stops working. ``-`` is not in
  ``prefix_chars``, so argparse cannot recognise the separator and treats ``--``
  as an unrecognized argument.
* Grouped short flags (``-abc`` meaning ``-a -b -c``) only work when ``-`` is in
  ``prefix_chars``.

Negative numbers
----------------
Because ``-1`` looks like an option, this fails::

    $ prog --offset -5
    prog: error: argument --offset: expected one argument

Three fixes, best first:

1. ``prog --offset=-5``  -- always works, no configuration
2. ``type=lambda s: int(s)`` plus a check: works only if argparse can be taught
   that ``-5`` is not an option. It cannot, so this does not fix it on its own.
3. ``prefix_chars="-+"`` and declare ``--offset`` -- still does not fix it,
   because ``-5`` still matches the option prefix.

The robust fix is #1, plus documenting ``--offset=-5`` in ``--help``. Several
stdlib tools do exactly this (e.g. ``cut``, ``sort``).

Abbreviation: ``allow_abbrev``
------------------------------
By default, argparse accepts unambiguous long-option prefixes::

    parser.add_argument("--verbose", "--version", ...)
    $ prog --verb     -> works (unambiguous prefix)

Turn it off with ``allow_abbrev=False`` when exact spelling matters -- CI
scripts that should fail loudly on a renamed flag, or CLIs with many
similarly-named options. Note that turning it off also stops ``prog --opt`` from
working when the only match is ``--option``, which some users rely on.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="greet",
        description="Lesson 05: option strings, short flags, and prefix_chars.",
        epilog=(
            "Negative numbers need '=':\n"
            "  greet --offset=-5      works\n"
            "  greet --offset -5      error: argument --offset: expected one argument"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Long + short. dest is `verbose` (the long form wins), regardless of order.
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="chatty output",
    )

    # Two short options that share a dest. Both are counted independently.
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="-",
        metavar="FILE",
        help="write to FILE instead of stdout",
    )

    # Two short aliases sharing a dest, and a const for the flag form. The
    # pre-3.9 BooleanOptionalAction pattern; see lesson 02.
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        help="suppress output",
    )

    # A negative number, demonstrating the '=' requirement.
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="an integer, possibly negative (use --offset=-5)",
    )

    # An option whose long name is a prefix of another's, to show how
    # allow_abbrev interacts. `--vers` is unambiguous here; `--verb` is not,
    # because both --verbose and --version match it.
    parser.add_argument(
        "--version-info",
        action="store_true",
        help="show extended version information",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"verbose       : {args.verbose}")
    print(f"output        : {args.output!r}")
    print(f"quiet         : {args.quiet}")
    print(f"offset        : {args.offset}")
    print(f"version_info  : {args.version_info}")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 76)
    print("1. dest is derived from the first LONG option, else the first short")
    print("=" * 76)
    for action in parser._actions:
        if not action.option_strings:
            continue
        if action.dest == "help":
            continue
        print(f"  {action.option_strings!s:<28} -> dest={action.dest!r}")

    print()
    print("=" * 76)
    print("2. -vv is NOT 'verbose twice'")
    print("=" * 76)
    for argv in (["-v"], ["-v", "-v"], ["-vv"], ["--verbose", "--verbose"]):
        ns = _try_parse(parser, argv)
        if ns is None:
            print(f"  {argv!s:<28} ERROR: {_last_error(parser, argv)}")
        else:
            print(f"  {argv!s:<28} ok, verbose={ns.verbose}")
    print()
    print("  argparse reads '-vv' as '-v' followed by the value 'v'.")
    print("  There is no bundled-short-flag syntax in argparse. Use -v -v.")

    print()
    print("=" * 76)
    print("3. Negative numbers need '='")
    print("=" * 76)
    for argv in (["--offset", "-5"], ["--offset=-5"], ["--offset", "-"]):
        ns = _try_parse(parser, argv)
        if ns is None:
            print(f"  {argv!s:<22} ERROR: {_last_error(parser, argv)}")
        else:
            print(f"  {argv!s:<22} ok, offset={ns.offset!r}")

    print()
    print("=" * 76)
    print("4. Abbreviation: --verb is unambiguous, --verbos is not")
    print("=" * 76)
    for argv in (["--verb"], ["--verbose"], ["--vers"]):
        ns = _try_parse(parser, argv)
        print(f"  {argv!s:<16} {'accepted' if ns else 'ERROR: ' + _last_error(parser, argv)}")
    print()
    print("  With allow_abbrev=False, prefixes are rejected -- exact spelling only.")

    strict = argparse.ArgumentParser(prog="greet", add_help=False, allow_abbrev=False)
    strict.add_argument("--verbose", action="store_true")
    strict.add_argument("--version-info", action="store_true")
    for argv in (["--verb"], ["--verbose"], ["--version-i"]):
        ns = _try_parse(strict, argv)
        print(f"  allow_abbrev=False, {argv!s:<14} "
              f"{'accepted' if ns else 'ERROR'}")

    print()
    print("=" * 76)
    print("5. prefix_chars='-+' lets you DECLARE +options (and nothing more)")
    print("=" * 76)
    plus = argparse.ArgumentParser(prog="greet", prefix_chars="-+")
    plus.add_argument("-v", "--verbose", action="store_true")
    print("  Declared only: -v, --verbose")
    for argv in (["-v"], ["--verbose"], ["+v"], ["+verbose"]):
        ns = _try_parse(plus, argv)
        print(f"    {argv!s:<12} {'accepted' if ns else 'ERROR: ' + _last_error(plus, argv)}")
    print()
    print("  prefix_chars changes which characters may BEGIN an option; the rest must")
    print("  still match a declared option string. '+v' is not '-v', so it is rejected.")
    print()
    plus2 = argparse.ArgumentParser(prog="greet", prefix_chars="-+")
    plus2.add_argument("+quiet", "-q", action="store_true")
    print("  Declared explicitly: +quiet, -q")
    for argv in (["-q"], ["+quiet"]):
        ns = _try_parse(plus2, argv)
        print(f"    {argv!s:<12} {'accepted, quiet=' + str(ns.quiet) if ns else 'ERROR'}")
    print()
    print("  And '--' stops working, because '-' is not in prefix_chars:")
    p3 = argparse.ArgumentParser(prog="greet", prefix_chars="-+")
    p3.add_argument("rest", nargs="*")
    for argv in (["--", "a", "b"], ["a", "b"]):
        ns = _try_parse(p3, argv)
        print(f"    {argv!s:<12} rest={ns.rest if ns else 'ERROR'}")

    print()
    print("=" * 76)
    print("6. Bundled short flags DO work: -qv")
    print("=" * 76)
    bundled = argparse.ArgumentParser(prog="greet", add_help=False)
    bundled.add_argument("-q", "--quiet", action="store_true")
    bundled.add_argument("-v", "--verbose", action="store_true")
    for argv in (["-qv"], ["-vq"], ["-q"]):
        ns = bundled.parse_args(argv)
        print(f"  {argv!s:<8} -> quiet={ns.quiet} verbose={ns.verbose}")
    print()
    print("  This works because both actions take no value. '-qo out.txt' does not,")
    print("  because the bundled short flag consumes the next token as its value.")

    return 0


def _try_parse(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace | None:
    """Parse, or return ``None`` instead of exiting.

    Used throughout the demos so that deliberately-invalid command lines do not
    print argparse's own error text over the lesson output.
    """
    import contextlib
    import io

    with contextlib.redirect_stderr(io.StringIO()):
        try:
            return parser.parse_args(argv)
        except SystemExit:
            return None


def _last_error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
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
