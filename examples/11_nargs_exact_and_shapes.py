"""Lesson 11 - Exact counts: ``nargs=N`` works, ``nargs=(m, n)`` does not.

Run me
------
    python examples/11_nargs_exact_and_shapes.py --coord 3 4 --point 1 2 3
    python examples/11_nargs_exact_and_shapes.py --demo

What argparse supports
----------------------
============================  ==========================================
Declaration                   Result
============================  ==========================================
``nargs=None`` (default)      exactly 1 value
``nargs=0``                   no value (flag actions only)
``nargs=N`` (an int)          exactly N values, as a **list**
``nargs='?'``                 0 or 1
``nargs='*'``                 0 or more
``nargs='+'``                 1 or more
``nargs=REMAINDER``           everything left, flags included
``nargs=PARSER``              everything left, recording subparser claims
``nargs=(m, n)``              **NOT SUPPORTED** -- see below
============================  ==========================================

The ``(m, n)`` mistake
----------------------
``nargs=(2, 3)`` is a natural guess -- ``nargs`` is an int-or-constant parameter,
and a range feels like the obvious generalisation. **argparse rejects it while
building the parser**::

    add_argument("--point", nargs=(2, 3))
    # ValueError: invalid nargs value

The reason is visible in argparse's own source. The final ``else`` branch of
``_get_nargs_pattern`` -- the one that handles every integer -- ends with::

    nargs_pattern = '([AO]{%d})' % nargs

``%d`` requires a number, and so does the earlier ``range(action.nargs)`` in
``HelpFormatter._format_args``. There is no tuple case anywhere. The set of
supported values is closed, and it is exactly the table above.

This is worth internalising because ``nargs=(m,n)`` **is** supported by click,
typer and docopt. Coming from those libraries, writing it in argparse fails
loudly -- which is the good outcome. Be suspicious of any tutorial that shows it.

Three correct ways to express "between 2 and 3 values"
-----------------------------------------------------
1. **Take ``'*'`` and validate.** Simplest, and the error message is yours::

       parser.add_argument("--point", nargs="*", type=int)
       ...
       if not 2 <= len(args.point) <= 3:
           parser.error("--point takes 2 or 3 values")

2. **A custom Action that enforces the range during the match.** Better: the
   error is attributed to the argument::

       class Between(argparse.Action):
           def __init__(self, option_strings, dest, low, high, **kw):
               self.low, self.high = low, high
               super().__init__(option_strings, dest, nargs="*", **kw)
           def __call__(self, parser, namespace, values, option_string=None):
               if not self.low <= len(values) <= self.high:
                   raise argparse.ArgumentError(
                       self, f"takes {self.low}-{self.high} values, got {len(values)}")
               setattr(namespace, self.dest, values)

3. **One token carrying its own structure** -- ``--point 1,2,3``, with a ``type=``
   that parses and range-checks. Best when the values are short, because it
   removes the ambiguity entirely (see :func:`coord_tuple`).

``nargs=N`` always yields a list
-------------------------------
::

    add_argument("--coord", nargs=2, type=int)
    # --coord 3 4   ->  args.coord == [3, 4]      NOT (3, 4)

``x, y = args.coord`` therefore works -- and only because the arity is
guaranteed. With ``nargs='+'`` the same unpack raises
``ValueError: too many values to unpack (expected 2)`` at runtime instead. An
exact count is a real constraint, not just a convenience.

The error messages argparse produces
------------------------------------
===================================  ===========================================
Command line                          Message
===================================  ===========================================
``--coord 3``                          ``expected 2 arguments``
``--coord 3 4 5``                      ``unrecognized arguments: 5``
===================================  ===========================================

Both are decent. The second is worth understanding: extra tokens are not an
error *about* ``--coord``, they become leftovers that ``parse_args`` reports at
the end. That is why the message says "unrecognized" rather than "too many".

Two variable-width positionals cannot be resolved
-------------------------------------------------
::

    parser.add_argument("a", nargs="+")
    parser.add_argument("b", nargs="+")
    # prog x y  ->  error: the following arguments are required: b

There is no marker telling argparse where ``a`` ends and ``b`` begins, and it
refuses to guess. Two **options** work fine, because the flag itself is the
boundary. So: variable-width arguments must be options, not positionals.
"""

from __future__ import annotations

import argparse
import sys


class Between(argparse.Action):
    """An action enforcing "between ``low`` and ``high`` values".

    Demonstrates the clean way to add a constraint argparse has no syntax for:

    * ``__init__`` takes your own keyword arguments and **pops** them before
      calling ``super()``, because argparse does not know about them.
    * ``__call__`` receives the already-collected values and validates.
    * Raising :class:`argparse.ArgumentError` (rather than ``ArgumentTypeError``)
      attributes the failure to *this argument*, producing
      ``argument --point: takes 2-3 values, got 1``.

    ``action=Between, low=2, high=3`` is the whole usage. Note that the custom
    keyword names (``low``, ``high``) must not collide with ``add_argument``'s own
    parameters (``type``, ``nargs``, ``choices``, ...).
    """

    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        low: int = 0,
        high: int | None = None,
        **kwargs: object,
    ) -> None:
        self.low = low
        self.high = high
        # nargs is forced: this action only makes sense for variable-width input.
        kwargs["nargs"] = "*"
        super().__init__(option_strings, dest, **kwargs)  # type: ignore[arg-type]

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: list[str],
        option_string: str | None = None,
    ) -> None:
        count = len(values)
        allowed = (
            f"exactly {self.low}" if self.high == self.low
            else f"between {self.low} and {self.high}"
        )
        if count < self.low or (self.high is not None and count > self.high):
            raise argparse.ArgumentError(
                self, f"takes {allowed} values, got {count}"
            )
        setattr(namespace, self.dest, values)


def coord_tuple(raw: str) -> tuple[int, int]:
    """A ``type=`` that returns a real tuple from a single token.

    ``--coord 3,4`` and ``--coord 3 4`` become interchangeable, the arity is
    enforced in the error message, and the result unpacks naturally. Worth doing
    whenever the count is small and fixed: it converts "a list of unknown length
    that I will index into" into a value with a real type.
    """
    parts = raw.replace(",", " ").split()
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a coordinate; expected 'X,Y' or 'X Y'"
        )
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} has non-integer components") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geometry",
        description="Lesson 11: fixed counts, and the nargs=(m,n) myth.",
        epilog=(
            "  geometry --coord 3 4        exactly two values (a list)\n"
            "  geometry --point 1 2 3      two or three values (custom action)\n"
            "  geometry --atleast 1 2 3    one or more values\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # nargs=N. Result is a LIST whose length is guaranteed.
    parser.add_argument(
        "--coord",
        nargs=2,
        type=int,
        metavar=("X", "Y"),
        help="exactly two integers",
    )

    # "Between 2 and 3", the feature argparse's nargs cannot express.
    parser.add_argument(
        "--point",
        action=Between,
        low=2,
        high=3,
        type=int,
        metavar="N",
        help="two or three integers",
    )

    # "At least one": nargs='+' already does this.
    parser.add_argument(
        "--atleast",
        nargs="+",
        type=int,
        metavar="N",
        help="one or more integers",
    )

    # An exact count on a positional.
    parser.add_argument(
        "origin",
        nargs=2,
        type=int,
        metavar=("X", "Y"),
        help="the origin, as two integers",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    print(f"coord   : {args.coord!r}   (a list, because nargs=2)")
    print(f"point   : {args.point!r}")
    print(f"atleast : {args.atleast!r}")
    print(f"origin  : {args.origin!r}")

    x, y = args.coord or (0, 0)
    print(f"unpacked: x={x}, y={y}   (safe only because the arity is exact)")

    return 0


def demo() -> int:
    print("=" * 78)
    print("1. nargs=(m, n) is rejected at BUILD time")
    print("=" * 78)
    for spec in [(2, 3), (1, None), (0, 2)]:
        try:
            argparse.ArgumentParser(prog="geometry", add_help=False).add_argument(
                "--point", nargs=spec
            )
            print(f"  nargs={spec!r:<10} accepted (unexpected)")
        except ValueError as exc:
            print(f"  nargs={spec!r:<10} ValueError: {exc}")
    print()
    print("  click and typer accept nargs=(m, n); argparse does not. The supported")
    print("  set is closed: None, 0, int, '?', '*', '+', REMAINDER, PARSER, SUPPRESS.")
    print()
    print("  Why: argparse's int branch ends with '([AO]{%d})' % nargs, and the")
    print("  help formatter uses range(action.nargs). Neither has a tuple case.")

    print()
    print("=" * 78)
    print("2. nargs=N yields a list of exactly that length")
    print("=" * 78)
    p = argparse.ArgumentParser(prog="geometry", add_help=False)
    p.add_argument("--coord", nargs=2, type=int)
    for argv in (["--coord", "3"], ["--coord", "3", "4"], ["--coord", "3", "4", "5"]):
        print(f"  {argv!s:<26} -> {_coord(p, argv)}")
    print()
    print("  Note the two different messages: too few is 'expected 2 arguments',")
    print("  too many is 'unrecognized arguments: 5' -- extras are leftovers that")
    print("  parse_args reports at the end, not a complaint about --coord.")

    print()
    print("=" * 78)
    print("3. Exact count makes unpacking safe; '+' does not")
    print("=" * 78)
    exact = argparse.ArgumentParser(prog="geometry", add_help=False)
    exact.add_argument("--coord", nargs=2, type=int)
    star = argparse.ArgumentParser(prog="geometry", add_help=False)
    star.add_argument("--coord", nargs="+", type=int)
    print(f"  nargs=2,  ['--coord','3','4']   -> x, y = {exact.parse_args(['--coord','3','4']).coord}  ok")
    print("  nargs='+',['--coord','3','4','5'] -> unpacking would raise ValueError")
    print(f"  nargs='+',['--coord','3','4','5'] -> {star.parse_args(['--coord','3','4','5']).coord!r}")
    print("  An exact count is a real constraint, not just a convenience.")

    print()
    print("=" * 78)
    print("4. Three correct ways to say 'between 2 and 3'")
    print("=" * 78)

    # (a) custom action
    act = argparse.ArgumentParser(prog="geometry", add_help=False)
    act.add_argument("--point", action=Between, low=2, high=3, type=int)
    for argv in (["--point", "1"], ["--point", "1", "2"], ["--point", "1", "2", "3"],
                 ["--point", "1", "2", "3", "4"]):
        print(f"  (a) custom action  {argv!s:<28} -> {_point(act, argv)}")

    # (b) nargs='*' plus manual validation
    val = argparse.ArgumentParser(prog="geometry", add_help=False)
    val.add_argument("--point", nargs="*", type=int)
    print()
    print("  (b) nargs='*' + manual check:")
    for argv in (["--point", "1"], ["--point", "1", "2", "3"]):
        ns = val.parse_args(argv)
        ok = 2 <= len(ns.point) <= 3
        print(f"      {argv!s:<28} -> {ns.point!r}  "
              f"{'valid' if ok else 'parser.error(--point takes 2 or 3 values)'}")

    # (c) one token
    single = argparse.ArgumentParser(prog="geometry", add_help=False)
    single.add_argument("--coord", type=coord_tuple)
    print()
    print("  (c) one comma-separated token:")
    for argv in (["--coord", "3,4"], ["--coord", "3", "4"], ["--coord", "3"]):
        print(f"      {argv!s:<28} -> {_tuple(single, argv)}")
    print("      --coord 3,4 and --coord 3 4 both work, and the value is a tuple.")

    print()
    print("=" * 78)
    print("5. The regex argparse actually builds")
    print("=" * 78)
    print("  _get_nargs_pattern() is the algorithm. Reconstructed:")
    for action in build_parser()._actions:
        if action.dest in ("help", "origin") and not action.option_strings:
            continue
        if action.dest == "help":
            continue
        print(f"    {action.dest:<10} nargs={action.nargs!s:<10} {_pattern(action)}")

    print()
    print("=" * 78)
    print("6. Two variable-width POSITIONALS cannot be resolved")
    print("=" * 78)
    two = argparse.ArgumentParser(prog="geometry", add_help=False)
    two.add_argument("a", nargs="+")
    two.add_argument("b", nargs="+")
    print(f"  prog x y  -> {_error(two, ['x', 'y'])}")
    print("  No marker says where 'a' ends. argparse refuses to guess.")
    print()
    opts = argparse.ArgumentParser(prog="geometry", add_help=False)
    opts.add_argument("--a", nargs="+")
    opts.add_argument("--b", nargs="+")
    ns = opts.parse_args(["--a", "x", "--b", "y", "z"])
    print(f"  --a x --b y z -> a={ns.a!r} b={ns.b!r}   (the flag is the marker)")
    print("  So: variable-width arguments must be options, not positionals.")

    return 0


def _pattern(action: argparse.Action) -> str:
    """Render the pattern argparse's ``_get_nargs_pattern`` produces."""
    nargs = action.nargs
    if nargs is None:
        return "([A])"
    if nargs == 0:
        return "()"
    if nargs == "?":
        return "(A?)"
    if nargs == "*":
        return "(A*)"
    if nargs == "+":
        return "(A+)"
    if nargs == argparse.REMAINDER:
        return "([AO]*)"
    if isinstance(nargs, int):
        return f"([AO]{{{nargs}}})"
    return "?"


def _coord(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).coord
    except SystemExit:
        return _err(buffer.getvalue())


def _point(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).point
    except SystemExit:
        return _err(buffer.getvalue())


def _tuple(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).coord
    except SystemExit:
        return _err(buffer.getvalue())


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit:
        pass
    return _err(buffer.getvalue())


def _err(text: str) -> str:
    for line in text.splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
