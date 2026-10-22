"""Lesson 12 - ``choices``, and the four ways to do it better.

Run me
------
    python examples/12_choices.py --mode fast --level 3
    python examples/12_choices.py --demo

``choices`` in one sentence
--------------------------
argparse compares the value against ``choices`` with ``in`` and ``not in``.
That single fact explains everything about its behaviour, good and bad.

* ``choices=("fast", "slow")`` and the user typed ``--mode fastt``:
  ``'fastt' in ('fast','slow')`` is False, so it errors.
* ``choices=range(1, 11)`` works, because ``5 in range(1,11)`` is True.
* ``choices=[1, "1"]`` matches ``1`` before ``"1"``, because ``in`` uses ``==``
  and the first match wins.
* The check happens **after** ``type=``, so you validate a converted ``int``, not
  the string you typed. That is the single most useful property of ``choices``.

The error message
-----------------
::

    prog: error: argument --mode: invalid choice: 'fastt'
                           (choose from 'fast', 'slow')

Accurate, unhelpful. Python 3.14 improves it with ``suggest_on_error=True``::

    prog: error: argument --mode: invalid choice: 'fastt', maybe you meant 'fast'?
                           (choose from 'fast', 'slow')

Verified limitation of that feature: it fires **only** for ``choices`` violations,
and **only** when every choice is a ``str``. An ``Enum`` list gets no suggestion,
because ``difflib`` cannot compare non-strings. So ``suggest_on_error`` is not a
substitute for converting values to strings first.

Four upgrades, in order of preference
-------------------------------------
**1. Turn an Enum into strings via ``type=`` (best).**
Then ``choices`` receives real ``Enum`` members, the error message lists names
rather than ``'Mode.FAST'``, and your code reads ``args.mode is Mode.FAST``
instead of ``args.mode == "fast"``. See :func:`enum_type`.

**2. ``type=`` that raises with a good message.**
Drop ``choices`` entirely. A validating ``type=`` can say exactly what is wrong
and suggest alternatives itself.

**3. A ``metavar`` that lists the options.**
``metavar="{fast,slow}"`` makes the *usage line* self-documenting even when the
user never reads the error. argparse will also accept it as the metavar for a
positional.

**4. A case-insensitive ``type=``.**
``choices`` is case-sensitive; users will type ``FAST``. Normalise in ``type=``
before ``choices`` runs. Note this makes the error message report the normalised
value, not what the user typed -- a small honesty cost worth paying.

The Enum footgun
----------------
``choices=list(Mode)`` works, but::

    prog: error: argument --mode: invalid choice: 'aa' (choose from 'Mode.FAST', 'Mode.SLOW')
    usage: prog [--mode {Mode.FAST,Mode.SLOW}]

Two problems: the user must type ``Mode.FAST``, and the error compares the string
``'aa'`` against enum members, so the suggestion machinery cannot help. Fix with
``type=``.

Also: ``choices`` accepts **any container** -- list, tuple, set, dict, a generator's
``list()``, even a ``range``. But argparse prints ``', '.join(repr(c) for c in
choices)`` in the error and ``'{' + ','.join(...) + '}'`` in the usage line, so
it will consume an iterator once and then show an empty set. Always pass a
concrete list or tuple.
"""

from __future__ import annotations

import argparse
import enum
import sys
from collections.abc import Callable
from typing import Any


class Mode(enum.Enum):
    """A domain enum. The values are the spellings on the command line."""

    FAST = "fast"
    BALANCED = "balanced"
    SAFE = "safe"

    def __str__(self) -> str:
        return self.value


def enum_type(enum_cls: type[enum.Enum]) -> Callable[[str], Any]:
    """A ``type=`` that maps a string onto an ``Enum`` member, case-insensitively.

    Combine with ``choices=list(enum_cls)`` and you get validation, a readable
    help string, and *typed* values in your handler::

        parser.add_argument("--mode", type=enum_type(Mode),
                            choices=list(Mode), default=Mode.BALANCED,
                            metavar="{fast,balanced,safe}")

    Why normalise in ``type=`` rather than write a ``choices`` of strings: the
    namespace ends up holding ``Mode.FAST``, so no downstream code can compare
    against a misspelled string literal. ``args.mode is Mode.FAST`` is
    unfalsifiable; ``args.mode == "fast"`` is not.

    Accepting any case is a courtesy. The cost is that the error message reports
    ``'FAStT'`` rather than what the user typed -- argparse converts before it
    validates, so the message shows the converted value. We work around that by
    including the valid spellings in the message.
    """
    by_name = {member.name.lower(): member for member in enum_cls}
    by_value = {
        str(member.value).lower(): member
        for member in enum_cls
        if isinstance(member.value, (str, int))
    }

    def parse(raw: str) -> Any:
        key = raw.lower()
        if key in by_name:
            return by_name[key]
        if key in by_value:
            return by_value[key]
        options = ", ".join(str(m.value) for m in enum_cls)
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a valid {enum_cls.__name__.lower()} (choose from {options})"
        )

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runner",
        description="Lesson 12: choices, done properly.",
        epilog="Try: runner --mode FAST --level 3",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- the four styles, side by side ---------------------------------
    parser.add_argument(
        "--naive",
        choices=("fast", "balanced", "safe"),
        default="balanced",
        metavar="{fast,balanced,safe}",
        help="a plain choices list: fine for a handful of strings",
    )

    parser.add_argument(
        "--typo",
        choices=("fast", "balanced", "safe"),
        default="balanced",
        metavar="{fast,balanced,safe}",
        help="type any of these; argparse reports the mistake for you",
    )

    parser.add_argument(
        "--typed",
        type=enum_type(Mode),
        choices=list(Mode),
        default=Mode.BALANCED,
        metavar="{fast,balanced,safe}",
        help="Enum-backed: the namespace holds Mode members, not strings",
    )

    parser.add_argument(
        "--level",
        type=int,
        choices=range(0, 6),
        default=3,
        metavar="{0..5}",
        help="choices accepts any container, including range()",
    )

    # A type= that validates, with NO choices at all. The error message is
    # entirely yours, so it can teach.
    parser.add_argument(
        "--threads",
        type=_positive_even_or_one,
        default=1,
        metavar="N",
        help="a positive even number, or 1 (no choices= needed)",
    )

    return parser


def _positive_even_or_one(raw: str) -> int:
    """``type=`` as a complete validator: no ``choices``, full control of the message."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not an integer (try 1, 2, 4, 8)"
        ) from None
    if value == 1:
        return 1
    if value <= 0 or value % 2:
        raise argparse.ArgumentTypeError(
            f"{value} must be a positive even number, or 1 (try 1, 2, 4, 8)"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"naive  : {args.naive!r}")
    print(f"typo   : {args.typo!r}")
    print(f"typed  : {args.typed!r}   (a Mode member: {type(args.typed).__name__})")
    print(f"level  : {args.level!r}   (choices=range(0,6))")
    print(f"threads: {args.threads!r}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. choices is `value in choices` -- so any container works")
    print("=" * 78)
    p = argparse.ArgumentParser(prog="runner", add_help=False)
    p.add_argument("--level", type=int, choices=range(0, 6), default=3)
    for value in ("0", "3", "5", "6", "99"):
        print(f"  --level {value:<4} -> {_level(p, value)}")
    print()
    print("  choices is checked AFTER type=, so it validates the int, not '3'.")

    print()
    print("=" * 78)
    print("2. choices=list(Enum) makes the user type Enum.FAST")
    print("=" * 78)
    enum_plain = argparse.ArgumentParser(prog="runner", add_help=False)
    enum_plain.add_argument("--mode", choices=list(Mode), default=Mode.BALANCED)
    print("  usage: " + enum_plain.format_usage().strip())
    print(f"  --mode fast -> {_mode(enum_plain, 'fast')}")
    print(f"  --mode FAST -> {_mode(enum_plain, 'FAST')}")
    print()
    print("  Two problems: the spelling is an implementation detail, and the error")
    print("  compares a str against enum members so suggest_on_error cannot help.")

    print()
    print("=" * 78)
    print("3. type=enum_type(Mode) fixes both")
    print("=" * 78)
    enum_typed = argparse.ArgumentParser(prog="runner", add_help=False)
    enum_typed.add_argument(
        "--mode",
        type=enum_type(Mode),
        choices=list(Mode),
        default=Mode.BALANCED,
        metavar="{fast,balanced,safe}",
    )
    print("  usage: " + enum_typed.format_usage().strip())
    for value in ("fast", "FAST", "Mode.FAST", "safe", "fastt"):
        print(f"  --mode {value:<10} -> {_mode(enum_typed, value)}")
    print()
    print("  And the namespace holds real members:")
    print(f"    vars(...).mode is Mode.FAST -> {enum_typed.parse_args(['--mode','fast']).mode is Mode.FAST}")

    print()
    print("=" * 78)
    print("4. A type= validator needs no choices at all")
    print("=" * 78)
    validator = argparse.ArgumentParser(prog="runner", add_help=False)
    validator.add_argument("--threads", type=_positive_even_or_one, default=1)
    for value in ("1", "4", "3", "0", "abc"):
        print(f"  --threads {value:<6} -> {_threads(validator, value)}")

    print()
    print("=" * 78)
    print("5. suggest_on_error (3.14+): what it does and does not cover")
    print("=" * 78)
    suggest_parser = argparse.ArgumentParser(prog="runner", add_help=False, suggest_on_error=True)
    suggest_parser.add_argument("--mode", choices=("fast", "balanced", "safe"))
    print("  all-str choices:")
    print(f"    --mode fastt -> {_mode_err(suggest_parser, ['--mode', 'fastt'])}")

    silent = argparse.ArgumentParser(prog="runner", add_help=False, suggest_on_error=True)
    silent.add_argument("--mode", choices=list(Mode))
    print("  non-str choices (Enum):")
    print(f"    --mode fastt -> {_mode_err(silent, ['--mode', 'fastt'])}")
    print()
    print("  difflib compares strings only, so an Enum choices list gets no suggestion.")
    print("  Converting to str via type= is what makes the feature fire.")

    print()
    print("=" * 78)
    print("6. Never pass an iterator to choices")
    print("=" * 78)
    iter_parser = argparse.ArgumentParser(prog="runner", add_help=False)
    iter_parser.add_argument("--mode", choices=(c for c in ("fast", "slow")))
    print("  usage: " + iter_parser.format_usage().strip())
    print()
    print("  argparse joins the choices to build the metavar, consuming the generator.")
    print("  Pass a list or tuple, always.")

    return 0


def _level(parser: argparse.ArgumentParser, value: str) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(["--level", value]).level
    except SystemExit:
        return _err(buffer.getvalue())


def _mode(parser: argparse.ArgumentParser, value: str) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return repr(parser.parse_args(["--mode", value]).mode)
    except SystemExit:
        return _err(buffer.getvalue())


def _threads(parser: argparse.ArgumentParser, value: str) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(["--threads", value]).threads
    except SystemExit:
        return _err(buffer.getvalue())


def _mode_err(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
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
