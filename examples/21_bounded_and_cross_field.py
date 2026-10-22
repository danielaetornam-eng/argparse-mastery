"""Lesson 21 - Bounded numbers and cross-field validation.

Run me
------
    python examples/21_bounded_and_cross_field.py --workers 8 --start-port 9000
    python examples/21_bounded_and_cross_field.py --demo

Three escalating places to put a numeric constraint
---------------------------------------------------
1. **``choices=range(...)``** -- when the set is small and enumerable. Cleanest
   error message argparse can produce.
2. **A ``type=`` factory** -- when the set is a contiguous range. This is lesson
   20's pattern applied to numbers, and it is right far more often than people
   use it.
3. **A validation function** -- when the rule needs two arguments, or the value's
   meaning depends on something else (``--start-port`` plus ``--ports``).

Why ``choices=range()`` is not enough
-------------------------------------
It composes badly. ``choices=range(1, 65)`` rejects ``"8"`` only because
``type=int`` ran first. Add a unit to the error message and you need a ``type=``
anyway. And for a range like "any port from --start-port to --start-port+count",
``choices`` cannot express it.

``NaN`` and ``inf`` deserve a mention
-------------------------------------
``float("nan")`` and ``float("inf")`` parse successfully and break every
comparison you write afterwards, silently. A bounded ``float`` ``type=`` must
reject them explicitly:

    if value != value:            # NaN
    if value in (float("inf"), float("-inf")):

That is a real bug class in tools that accept numeric user input, and argparse
gives you no protection.

The cross-field rules
---------------------
The pattern that generalises: collect violations, report them all.

::

    def validate(args, parser):
        problems = []
        if args.ports > args.available:
            problems.append(...)
        if args.start_port + args.ports > 65535:
            problems.append(...)
        if problems:
            parser.error("; ".join(problems))

Reporting every problem at once is a deliberate UX choice. The alternative --
``parser.error`` on the first violation -- turns a five-minute fix into five
runs, and users read that as "this tool is broken".

Where to call ``validate``
--------------------------
Immediately after ``parse_args``, before any side effect::

    args = parser.parse_args(argv)
    validate(args, parser)      # before opening files, sockets, or databases
    return run(args)

Keep the function pure: it takes the namespace and the parser and only reports.
That makes every rule independently testable, which matters more than it sounds
when you have eight of them.

A reusable composition
----------------------
``argparse_mastery.errors`` and ``argparse_mastery.types.validate_all`` compose
named rules into one reporter, so each rule can live in its own module and be
tested on its own. Worth reaching for past about five rules.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable


def bounded_int(minimum: int, maximum: int, *, unit: str = "") -> Callable[[str], int]:
    """``type=`` factory for an inclusive integer range, with a named unit.

    The message is the point: ``128 workers is outside the allowed range
    1-64 workers`` tells the user what to do, where ``invalid int value: '128'``
    tells them nothing.
    """
    suffix = f" {unit}" if unit else ""

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{raw!r} is not an integer (expected a whole number)"
            ) from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"{value}{suffix} is outside the allowed range "
                f"{minimum}-{maximum}{suffix}"
            )
        return value

    return parse


def bounded_float(
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    unit: str = "",
) -> Callable[[str], float]:
    """``type=`` factory for a float range that also rejects NaN and infinity.

    ``float("nan")`` compares False against everything, so every range check you
    write would silently pass it. ``float("inf")`` likewise. Both parse from user
    input without complaint, so a bounded float ``type=`` is the *only* place
    they can be caught.
    """
    suffix = f" {unit}" if unit else ""

    def parse(raw: str) -> float:
        try:
            value = float(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{raw!r} is not a number") from None
        if value != value:  # NaN is the only value not equal to itself
            raise argparse.ArgumentTypeError("NaN is not an acceptable value")
        if value in (float("inf"), float("-inf")):
            raise argparse.ArgumentTypeError(
                "infinity is not an acceptable value"
            )
        if minimum is not None and value < minimum:
            raise argparse.ArgumentTypeError(
                f"{value}{suffix} is below the minimum of {minimum}{suffix}"
            )
        if maximum is not None and value > maximum:
            raise argparse.ArgumentTypeError(
                f"{value}{suffix} is above the maximum of {maximum}{suffix}"
            )
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scale",
        description="Lesson 21: bounded numbers and cross-field rules.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--workers", type=bounded_int(1, 64, unit="workers"),
                        default=4, metavar="N", help="worker processes")
    parser.add_argument("--timeout", type=bounded_float(0.0, 3600.0, unit="s"),
                        default=30.0, metavar="SECONDS", help="per-request timeout")
    parser.add_argument("--memory", type=bounded_float(0.0, unit="GiB"),
                        default=1.0, metavar="GIB", help="memory ceiling")
    parser.add_argument("--start-port", type=bounded_int(1024, 65535),
                        default=8000, metavar="PORT", help="first port")
    parser.add_argument("--ports", type=bounded_int(1, 1000),
                        default=1, metavar="N", help="how many ports to use")
    parser.add_argument("--level", type=int, choices=range(0, 6), default=3,
                        metavar="{0..5}", help="a small enumerable set")
    return parser


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Every rule that needs two arguments, reported together.

    Pure function: reads the namespace, reports problems, raises via
    ``parser.error``. No side effects, so it is trivial to test and safe to call
    from anywhere.
    """
    problems: list[str] = []

    # A derived range: the last port must still be valid.
    last = args.start_port + args.ports - 1
    if last > 65535:
        problems.append(
            f"--start-port {args.start_port} plus --ports {args.ports} "
            f"reaches {last}, above the maximum port 65535"
        )

    # A budget rule: workers and memory must be consistent.
    if args.memory < args.workers * 0.25:
        problems.append(
            f"--memory {args.memory}GiB is too small for --workers {args.workers} "
            f"(need at least {args.workers * 0.25:.2f}GiB)"
        )

    # A rule that only applies conditionally.
    if args.timeout > 60 and args.level >= 4:
        problems.append(
            f"--level {args.level} caps --timeout at 60s, got {args.timeout}"
        )

    if problems:
        parser.error("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate(args, parser)
    print(f"workers  : {args.workers}")
    print(f"timeout  : {args.timeout}")
    print(f"memory   : {args.memory}")
    print(f"ports    : {args.start_port}-{args.start_port + args.ports - 1}")
    print("all constraints satisfied")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. choices=range vs a bounded type=")
    print("=" * 78)
    small = argparse.ArgumentParser(prog="scale", add_help=False)
    small.add_argument("--level", type=int, choices=range(0, 6))
    bounded = argparse.ArgumentParser(prog="scale", add_help=False)
    bounded.add_argument("--workers", type=bounded_int(1, 64, unit="workers"))
    print(f"  choices=range(0,6), --level 9  -> {_msg(small, ['--level','9'])}")
    print(f"  bounded_int,      --workers 128 -> {_msg(bounded, ['--workers','128'])}")
    print(f"  bounded_int,      --workers abc -> {_msg(bounded, ['--workers','abc'])}")
    print()
    print("  choices is excellent for small enumerable sets; a bounded type=")
    print("  wins as soon as you want a unit in the message or a big range.")

    print()
    print("=" * 78)
    print("2. NaN and infinity: float() accepts them, comparisons do not")
    print("=" * 78)
    for raw in ("nan", "inf", "-inf", "1.5", "99999"):
        print(f"  --timeout {raw:<8} -> {_msg(parser, ['--timeout', raw])}")
    print()
    print("  NaN < 0 is False and NaN > 3600 is False, so a naive range check")
    print("  accepts it. Then every comparison in your code silently misbehaves.")
    print("  A bounded float type= is the only place to catch this.")

    print()
    print("=" * 78)
    print("3. Cross-field rules: all violations reported at once")
    print("=" * 78)
    argv = ["--workers", "64", "--memory", "1", "--start-port", "65530",
            "--ports", "100", "--timeout", "120", "--level", "5"]
    print(f"  argv = {argv}\n")
    print(f"  -> {_validate_msg(parser, argv)}")
    print()
    print("  Compare with an implementation that calls parser.error on the first")
    print("  failure: the user would need five runs. Reporting everything is a")
    print("  deliberate UX decision and it is cheap.")

    print()
    print("=" * 78)
    print("4. Pure validate() is easy to test")
    print("=" * 78)
    ns = parser.parse_args(["--workers", "2", "--memory", "4"])
    print(f"  args = workers={ns.workers} memory={ns.memory}")
    print("  validate(args, parser) returns None for a valid combination -> no output")
    _quiet(parser, ns)

    print()
    print("=" * 78)
    print("5. Validating before side effects")
    print("=" * 78)
    print("  parser.parse_args(argv)")
    print("      validate(args, parser)   <-- here, not in the handler")
    print("      run(args)                <-- nothing has been touched yet")
    print()
    print("  A late check means the user discovers a typo after files are written")
    print("  or a connection is opened. Order is the whole point.")

    return 0


def _msg(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
        return "(accepted)"
    except SystemExit:
        pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return line.split(": error:", 1)[1].strip()
    return "(no error)"


def _validate_msg(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            validate(parser.parse_args(argv), parser)
            return "(accepted)"
        except SystemExit:
            pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return line.split(": error:", 1)[1].strip()
    return "(no error)"


def _quiet(parser: argparse.ArgumentParser, ns: argparse.Namespace) -> None:
    import contextlib
    import io

    with contextlib.redirect_stderr(io.StringIO()):
        try:
            validate(ns, parser)
        except SystemExit:
            print("  validate() raised (unexpected)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
