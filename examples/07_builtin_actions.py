"""Lesson 07 - Every built-in action, and when each one is the right answer.

Run me
------
    python examples/07_builtin_actions.py --tag a --tag b -vv --json --mode quiet
    python examples/07_builtin_actions.py --demo

The complete table
------------------
=========================  ==================  ==============================================
``action=``                ``nargs`` implied   Use it when
=========================  ==================  ==============================================
``store`` (default)        1                   an ordinary ``--opt VALUE``
``store_true``             0                   a flag whose meaning is "yes"
``store_false``            0                   a flag whose meaning is "no"
``store_const``            0                   presence implies a fixed non-bool value
``append``                 1                   accumulate into a list
``extend``                 1                   like append but flattens nargs='+' lists
``count``                  0                   repetition means quantity (``-vvv``)
``help``                   --                  print help and exit 0 (added automatically)
``version``                --                  print a version and exit 0
``BooleanOptionalAction``  0                   ``--flag`` / ``--no-flag``, tri-state (3.9+)
=========================  ==================  ==============================================

``append`` vs ``extend`` -- the difference that bites
-----------------------------------------------------
With ``nargs="+"`` both produce a list per occurrence, so the shapes differ::

    action="append", nargs="+"    --tag a b --tag c  ->  [['a','b'], ['c']]
    action="extend", nargs="+"    --tag a b --tag c  ->  ['a','b','c']

``extend`` (3.8+) is almost always what people mean. But ``extend`` with
``nargs=None`` or ``"?"`` behaves differently from ``append``: it stores the
single value directly rather than wrapping it, so mixing them changes the type.
Rule of thumb: ``nargs`` in (None, "?") -> ``append``; ``nargs`` in ("*", "+")
-> ``extend``.

``append`` and the mutable-default trap
---------------------------------------
This is the classic argparse bug::

    parser.add_argument("--tag", action="append")          # default=None, fine
    parser.add_argument("--tag", action="append", default=[])   # DANGEROUS

argparse puts **your** list object into the namespace, then ``append`` mutates
it in place. The default list is now polluted, and it stays polluted for the
lifetime of the process. In a long-running process -- or the second test in a
pytest session -- you get tags from a previous parse that should not be there.

Two safe patterns::

    # 1. Give no default. The attribute is None when absent; you handle it.
    parser.add_argument("--tag", action="append")

    # 2. Copy in a custom action (see lesson 24 and argparse_mastery.actions).
    class AppendUniqueAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            current = list(getattr(namespace, self.dest, None) or [])
            current.append(values)
            setattr(namespace, self.dest, current)

``count`` and the ``default=None`` crash
----------------------------------------
``count`` does ``setattr(ns, dest, getattr(ns, dest) + 1)``. With
``default=None`` that is ``None + 1`` -> ``TypeError``, raised as a raw
traceback rather than a usage error. Always ``default=0``.

``version`` action
------------------
The one piece of argparse worth using on sight::

    parser.add_argument("--version", action="version", version="%(prog)s 2.1.0")

``%(prog)s`` is expanded by argparse to the parser's ``prog``. Other keys work
too: ``version="%(prog)s 2.1.0 (python " + platform.python_version() + ")"``.
The action prints to **stdout** and exits **0** -- correct, because a version
query is a successful request.

A subtlety: put ``--version`` on the *top-level* parser and it only works
*before* the subcommand (``prog --version`` but not ``prog status --version``).
To support both, add it to every subparser too -- see lesson 19.

``store_const`` and ``const`` for ``nargs='?'``
-----------------------------------------------
``const`` is used by three situations, which is why it is worth naming:

* ``nargs=0`` (``store_true`` is shorthand for ``action="store_const",
  const=True````)
* ``nargs='?'`` when the option is given **without** a value
* ``action='store_const'`` explicitly

That last one is the interesting case and it is lesson 08's subject.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kitchen",
        description="Lesson 07: every built-in action.",
        epilog="Try: kitchen --tag a --tag b -vv --json --mode quiet",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--version", action="version", version="%(prog)s 4.2.0")

    # store: the default action. `store` is what you get with no action=.
    parser.add_argument("--name", help="store: an ordinary value")

    # store_true
    parser.add_argument("--dry-run", action="store_true", help="store_true: a flag")

    # store_false -- dest is named for the POSITIVE state, so reading it is clear.
    parser.add_argument(
        "--insecure", action="store_false", dest="secure", help="store_false"
    )

    # store_const: presence implies a fixed value that is not a bool.
    parser.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        default="text",
        help="store_const: implies --format json",
    )

    # append with a single value per occurrence
    parser.add_argument(
        "--tag",
        action="append",
        metavar="TAG",
        help="append: repeatable, one value each time",
    )

    # extend flattens nargs='+' so you get one flat list
    parser.add_argument(
        "--exclude",
        action="extend",
        nargs="+",
        metavar="PAT",
        default=[],
        help="extend: --exclude a b --exclude c gives ['a','b','c']",
    )

    # count
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="count: repeatable, -v -v -v gives 3",
    )

    # BooleanOptionalAction: two spellings, three states.
    parser.add_argument(
        "--color",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="BooleanOptionalAction: --color / --no-color",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for key in sorted(vars(args)):
        print(f"  {key:<10} = {getattr(args, key)!r}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. append vs extend with nargs='+'")
    print("=" * 78)

    app = argparse.ArgumentParser(prog="kitchen", add_help=False)
    app.add_argument("--tag", action="append", nargs="+")
    ext = argparse.ArgumentParser(prog="kitchen", add_help=False)
    ext.add_argument("--tag", action="extend", nargs="+")

    for argv in (["--tag", "a", "b"], ["--tag", "a", "--tag", "b"], []):
        print(f"  argv={argv!s:<30}")
        print(f"      append -> {app.parse_args(argv).tag!r}")
        print(f"      extend -> {ext.parse_args(argv).tag!r}")
    print()
    print("  extend gives you the flat list you almost always wanted.")

    print()
    print("=" * 78)
    print("2. The mutable-default trap with action='append'")
    print("=" * 78)
    shared: list[str] = []
    buggy = argparse.ArgumentParser(prog="kitchen", add_help=False)
    buggy.add_argument("--tag", action="append", default=shared)

    print(f"  first parse  : {buggy.parse_args(['--tag','a']).tag!r}")
    print(f"  second parse : {buggy.parse_args(['--tag','b']).tag!r}")
    print(f"  with nothing : {buggy.parse_args([]).tag!r}")
    print()
    print("  The default list was mutated in place, so the third parse is polluted.")
    print("  In a test suite this looks like 'tests pass alone and fail together'.")

    safe = argparse.ArgumentParser(prog="kitchen", add_help=False)
    safe.add_argument("--tag", action="append")   # no default
    print()
    print(f"  safe, first  : {safe.parse_args(['--tag','a']).tag!r}")
    print(f"  safe, second : {safe.parse_args(['--tag','b']).tag!r}")
    print(f"  safe, empty  : {safe.parse_args([]).tag!r}   <- None, handle it yourself")

    print()
    print("=" * 78)
    print("3. count requires default=0")
    print("=" * 78)
    bad = argparse.ArgumentParser(prog="kitchen", add_help=False)
    bad.add_argument("-v", action="count", default=None)
    try:
        bad.parse_args(["-v"])
    except TypeError as exc:
        print(f"  default=None -> TypeError: {exc}")
    good = argparse.ArgumentParser(prog="kitchen", add_help=False)
    good.add_argument("-v", action="count", default=0)
    print(f"  default=0    -> {good.parse_args(['-v','-v','-v']).v}")

    print()
    print("=" * 78)
    print("4. store_true is sugar for store_const")
    print("=" * 78)
    a = argparse.ArgumentParser(prog="kitchen", add_help=False)
    a.add_argument("--flag", action="store_true")
    b = argparse.ArgumentParser(prog="kitchen", add_help=False)
    b.add_argument("--flag", action="store_const", const=True, default=False)
    print(f"  store_true              -> {a.parse_args(['--flag']).flag!r} / "
          f"{a.parse_args([]).flag!r}")
    print(f"  store_const const=True  -> {b.parse_args(['--flag']).flag!r} / "
          f"{b.parse_args([]).flag!r}")
    print("  Identical. store_const is the general form.")

    print()
    print("=" * 78)
    print("5. --version goes to stdout and exits 0")
    print("=" * 78)
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            build_parser().parse_args(["--version"])
        except SystemExit as exc:
            code = exc.code
    print(f"  output : {buffer.getvalue().strip()!r}")
    print(f"  exit   : {code}   (stdout, not stderr -- a version query succeeds)")

    print()
    print("=" * 78)
    print("6. BooleanOptionalAction: one declaration, two spellings, three states")
    print("=" * 78)
    bool_parser = argparse.ArgumentParser(prog="kitchen", add_help=False)
    bool_parser.add_argument("--color", action=argparse.BooleanOptionalAction, default=None)
    for argv in ([], ["--color"], ["--no-color"]):
        print(f"  {argv!s:<16} -> {bool_parser.parse_args(argv).color!r}")

    print()
    print("=" * 78)
    print("7. store_false: the flag means 'no'")
    print("=" * 78)
    sf = argparse.ArgumentParser(prog="kitchen", add_help=False)
    sf.add_argument("--insecure", action="store_false", dest="secure")
    for argv in ([], ["--insecure"]):
        print(f"  {argv!s:<16} -> secure={sf.parse_args(argv).secure}")
    print("  Name the dest for the POSITIVE state so reads are unambiguous.")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
