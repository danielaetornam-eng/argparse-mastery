"""Lesson 02 - Booleans: store_true, store_false, count, BooleanOptionalAction.

Run me
------
    python examples/02_flags_store_true_false.py --verbose --verbose
    python examples/02_flags_store_true_false.py --color --demo

A boolean flag is the most common argument in every CLI ever written, and the
naive version has a bug baked into it.

The bug
-------
``--verbose`` with ``action="store_true"`` defaults to ``False``. So::

    args.verbose   # False

means *either* "you did not pass ``--verbose``" *or* "you passed something that
set it to False". If the only way to set it False is to omit the flag, there is
no ambiguity and everything is fine. The moment you also offer ``--quiet``, or
load a config file, or want ``--verbose=false``, the ambiguity arrives.

Three correct answers
---------------------
1. **Two separate flags** (``--verbose`` / ``--no-verbose``) with different
   dests. Simple, and you must remember to handle both dests.

2. **``argparse.BooleanOptionalAction`` (3.9+)** -- the correct modern choice.
   One ``add_argument`` call generates both spellings automatically::

       parser.add_argument("--color", action=argparse.BooleanOptionalAction)
       # --color      -> True
       # --no-color   -> False
       # (omitted)    -> None  <- a THIRD state, distinct from False

   That tri-state default of ``None`` is the whole point: it lets you say "not
   specified, so fall back to the config file".

3. **``action="count"``** for repetition::

       parser.add_argument("-v", "--verbose", action="count", default=0)
       # (none) -> 0,  -v -> 1,  -vv -> 2,  -vvv -> 3

   ``default=0`` is the clearest spelling. **Correction:** on Python 3.14
   ``default=None`` is safe -- ``_CountAction.__call__`` does
   ``count = getattr(namespace, self.dest, None) or 0`` before incrementing. The
   ``None + 1`` ``TypeError`` is a historical hazard, and it is still live for a
   *hand-rolled* counter action, which nobody guards. Use ``default=0`` and the
   question never arises.

The ``store_const`` family
-------------------------
For flags whose presence means "use this specific value", ``store_const`` beats
``store_true`` plus a branch::

    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--json", action="store_const", const="json", dest="format")

Two ``add_argument`` calls writing to the *same* ``dest`` is not a conflict:
argparse only complains when the same *option string* is registered twice. This
is the idiomatic way to build ``--json`` shorthands and ``-q``/``-d`` verbosity
ladders.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flags",
        description="Lesson 02: every correct way to spell a boolean flag.",
        epilog=(
            "The tri-state point:\n"
            "  --color      -> args.color is True\n"
            "  --no-color   -> args.color is False\n"
            "  omitted      -> args.color is None  (not False!)\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ---- 1. repetition: count -------------------------------------------
    # `default=0` is the clearest spelling. On 3.14 `default=None` also works,
    # because `_CountAction.__call__` guards the None -- but see the demo.
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase verbosity; repeatable (-v, -vv, -vvv)",
    )

    # ---- 2. one flag, two spellings, tri-state ---------------------------
    # Python 3.9+. Gives --color and --no-color from a single declaration.
    parser.add_argument(
        "--color",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="colorize output (--no-color to disable)",
    )

    # ---- 3. a shorthand writing to another option's dest -----------------
    # Declared AFTER --format so the real option's default is the one that
    # applies. The explicit dest= documents the coupling; the comment in --format
    # documents the intent.
    parser.add_argument(
        "--format",
        choices=("text", "json", "yaml"),
        default="text",
        help="output format (--json is shorthand for --format json)",
    )
    parser.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help="shorthand for --format json",
    )

    # ---- 4. a pair of opposites sharing a dest --------------------------
    # The pre-3.9 idiom for a verbosity ladder. Both flags write to
    # `verbosity`; no conflict because they are different option strings.
    parser.add_argument(
        "-q",
        "--quiet",
        dest="verbosity",
        action="store_const",
        const=-1,
        default=0,
        help="decrease verbosity",
    )
    parser.add_argument(
        "-d",
        "--debug",
        dest="verbosity",
        action="store_const",
        const=1,
        help="increase verbosity (shorter than -v)",
    )

    # ---- 5. store_false: the flag means "turn it off" ---------------------
    # Use when the natural spelling is negative. Usually BooleanOptionalAction is
    # better, but a lone `--no-such-thing` reads well.
    parser.add_argument(
        "--safe-mode",
        action="store_false",
        dest="unsafe",
        help="disable the safety checks (default: safe)",
    )

    # ---- 6. a flag that looks boolean but is not -------------------------
    # This is the shape people reach for when they want `--verbose true`. Do not:
    # it makes typing longer and puts a string where a bool belongs.
    # BooleanOptionalAction exists for exactly this need.
    parser.add_argument(
        "--color-always",
        choices=("true", "false", "auto"),
        default="auto",
        metavar="{true,false,auto}",
        help="deliberately stringly-typed, to show why you should not",
    )

    return parser


def resolve_color(args: argparse.Namespace) -> str:
    """The reason tri-state matters: ``None`` can mean "ask the config file".

    With a two-state boolean you cannot write this correctly. With
    ``BooleanOptionalAction`` it is four lines, and CLI-over-config precedence is
    explicit rather than accidental.
    """
    if args.color is True:
        return "always"
    if args.color is False:
        return "never"
    return "auto-detect from isatty"


def resolve_verbosity(args: argparse.Namespace) -> str:
    """Fold the ``count`` and the ``-q``/``-d`` ladder into one number.

    Demonstrates that you do not have to choose one boolean mechanism for a
    whole program. Combine a repeatable ``count`` with a pair of ``store_const``
    flags writing to a different dest, then combine them here.
    """
    total = args.verbose + args.verbosity
    return {-2: "silent", -1: "quiet", 0: "normal", 1: "verbose", 2: "debug"}.get(
        total, "trace"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    print(f"verbose count  : {args.verbose}   (-v -v -v -> 3)")
    print(f"color          : {args.color!r}   (None means 'not specified')")
    print(f"color policy   : {resolve_color(args)}")
    print(f"output format  : {args.format!r}")
    print(f"effective verb : {resolve_verbosity(args)}")
    print(f"unsafe         : {args.unsafe}   (store_false default)")
    print(f"color_always   : {args.color_always!r}")
    return 0


def demo() -> int:
    parser = build_parser()

    rows = [
        [],
        ["-v"],
        ["-vvv"],
        ["--color"],
        ["--no-color"],
        ["--json"],
        ["-q", "-d"],
        ["--safe-mode"],
    ]

    print(f"{'argv':<26} {'verbose':>7} {'color':>7} {'format':>7} {'verbosity':>10} {'unsafe':>7}")
    print("-" * 74)
    for argv in rows:
        args = parser.parse_args(argv)
        print(
            f"{argv!s:<26} {args.verbose:>7} {args.color!s:>7} "
            f"{args.format:>7} {args.verbosity:>10} {args.unsafe!s:>7}"
        )

    print()
    print("Read the `color` column across rows 1-5:")
    print("  omitted      -> None")
    print("  --color      -> True")
    print("  --no-color   -> False")
    print("Three distinct states. A store_true flag only has two.")

    print()
    print("Tri-state resolution (why None is useful):")
    for argv in ([], ["--color"], ["--no-color"]):
        print(f"  {argv!s:<16} -> {resolve_color(parser.parse_args(argv))}")

    print()
    print("`--json` writes to the SAME dest as `--format`:")
    print(f"  --format json -> format={parser.parse_args(['--format','json']).format!r}")
    print(f"  --json        -> format={parser.parse_args(['--json']).format!r}")
    print(f"  neither       -> format={parser.parse_args([]).format!r}")

    print()
    print("`count` with default=None is safe on 3.14 -- but a hand-rolled counter is not:")
    builtin = argparse.ArgumentParser(prog="ok", add_help=False)
    builtin.add_argument("-v", action="count", default=None)
    print(f"  action='count', default=None -> {builtin.parse_args(['-v']).v!r}   "
          f"(3.14 guards it)")

    class Counter(argparse.Action):
        """A counter written the obvious way -- which is the hazard."""

        def __init__(self, option_strings, dest, **kw):
            kw.setdefault("nargs", 0)
            super().__init__(option_strings, dest, **kw)

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, getattr(namespace, self.dest) + 1)

    custom = argparse.ArgumentParser(prog="boom", add_help=False)
    custom.add_argument("-c", action=Counter, default=None)
    try:
        custom.parse_args(["-c"])
    except TypeError as exc:
        print(f"  custom counter, default=None -> TypeError: {exc}")
    print("  argparse guards its own action; nobody guards yours.")
    print("  default=0 makes the question disappear.")

    print()
    print("Effective verbosity combining both mechanisms:")
    for argv in ([], ["-vv"], ["-q"], ["-d"], ["-vv", "-q"], ["-d", "-v"]):
        print(f"  {argv!s:<16} -> {resolve_verbosity(parser.parse_args(argv))}")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
