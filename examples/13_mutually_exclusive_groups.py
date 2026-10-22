"""Lesson 13 - Mutually exclusive groups, and the four things they cannot do.

Run me
------
    python examples/13_mutually_exclusive_groups.py --json
    python examples/13_mutually_exclusive_groups.py --demo

What a group does
-----------------
``add_mutually_exclusive_group()`` enforces "at most one of these". Give it
``required=True`` and it enforces "exactly one". The message argparse produces is
good::

    prog: error: argument --json: not allowed with argument --yaml

Two properties worth knowing:

* Members can be **positional** as well as optional, though mixing the two in
  one group is confusing in help output.
* You can nest groups, but there is no way to say "A and B, or C and D" without
  two sibling groups plus post-validation.

What groups cannot do
---------------------
This is the important half, because argparse's vocabulary is small and these
gaps are where real CLIs go wrong.

1. **"At least one of" cannot be expressed.**
   A mutually exclusive group with ``required=False`` allows zero.
   Workaround: make it ``required=True`` *only if* zero really is invalid; if
   zero is allowed sometimes, validate after parsing.

2. **"Exactly two of these four" cannot be expressed.**
   There is no cardinality beyond 0-or-1. Post-validate with a counter.

3. **Dependencies ("``--json`` requires ``--pretty``") cannot be expressed.**
   Nothing in argparse links two arguments. See lesson 14.

4. **Conditional membership cannot be expressed.**
   "``--range`` and ``--point`` are exclusive, but only when ``--geom`` is set"
   needs a custom check.

The workaround pattern that scales
----------------------------------
Two-phase validation: let argparse handle what it can, then run one function that
reports **all** remaining violations at once::

    def validate(args, parser) -> None:
        problems = []
        selected = [name for name, on in FLAG_NAMES if getattr(args, dest_of(name))]
        if len(selected) != 1:
            problems.append("exactly one output format is required: "
                            + ", ".join(FLAG_NAMES))
        if args.range and not args.geom:
            problems.append("--range requires --geom")
        if problems:
            parser.error("; ".join(problems))

Reporting every problem at once is a real usability win: the alternative --
``parser.error`` on the first failure -- makes users fix one thing per run, and
for a five-flag interface that is five runs.

Required groups and ``--help``
------------------------------
``group = parser.add_mutually_exclusive_group(required=True)`` makes argparse
demand a member even for ``prog --help``? No: ``--help`` short-circuits before
required checking, so ``prog --help`` still works. Verify it in the demo, because
it is a reasonable thing to worry about.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render",
        description="Lesson 13: mutually exclusive groups.",
        epilog=(
            "  render --json             one output format\n"
            "  render --json --yaml      error: --json not allowed with --yaml\n"
            "  render                    error: one of --json/--yaml/--toml is required\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- required group: exactly one of three ---------------------------
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--json", action="store_true", help="JSON output")
    output.add_argument("--yaml", action="store_true", help="YAML output")
    output.add_argument("--toml", action="store_true", help="TOML output")

    # --- optional group: at most one of two ----------------------------
    # Used for "override by an alternative source".
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-file", metavar="PATH", help="read input from a file")
    source.add_argument("--from-url", metavar="URL", help="read input from a URL")

    # --- a group built into an argument group --------------------------
    # Nesting for organisation: the exclusivity still works, and --help shows
    # the flags under their own heading rather than an anonymous block.
    sizing = parser.add_argument_group("sizing")
    dimensions = sizing.add_mutually_exclusive_group()
    dimensions.add_argument("--width", type=int, metavar="PX", help="fixed width")
    dimensions.add_argument("--auto", action="store_true", help="size automatically")

    return parser


#: The output-format flags, kept beside the parser so validation and help cannot
#: drift apart. This "declare once, derive twice" habit is what stops argparse
#: configurations from rotting.
OUTPUT_FLAGS = ("json", "yaml", "toml")


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Everything a mutually exclusive group cannot express, in one place.

    Reports *all* violations at once. See the lesson docstring for why that
    matters: the alternative makes users play whack-a-mole across runs.
    """
    problems: list[str] = []

    # "At least one" -- legal only because the group above is required=True.
    # Remove that and this becomes necessary.
    if not any(getattr(args, name) for name in OUTPUT_FLAGS):
        problems.append(
            "at least one output format is required: " + ", ".join(OUTPUT_FLAGS)
        )

    # A dependency between two options. argparse has no syntax for this.
    if args.width is not None and args.auto:
        problems.append("--auto cannot be combined with --width")

    if args.from_file and args.from_url:
        problems.append("--from-file and --from-url are mutually exclusive")

    # A range check that involves two arguments.
    if args.width is not None and not 1 <= args.width <= 4096:
        problems.append(f"--width must be between 1 and 4096, got {args.width}")

    if problems:
        parser.error("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate(args, parser)

    chosen = [name for name in OUTPUT_FLAGS if getattr(args, name)]
    print(f"output format : {chosen[0]}")
    print(f"from-file     : {args.from_file}")
    print(f"from-url      : {args.from_url}")
    print(f"width         : {args.width}")
    print(f"auto          : {args.auto}")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. required=True means EXACTLY one -- zero is an error too")
    print("=" * 78)
    for argv in ([], ["--json"], ["--json", "--yaml"], ["--json", "--yaml", "--toml"]):
        print(f"  {argv!s:<34} -> {_try(parser, argv)}")

    print()
    print("=" * 78)
    print("2. --help still works even though the group is required")
    print("=" * 78)
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            parser.parse_args(["--help"])
        except SystemExit as exc:
            code = exc.code
    print(f"  prog --help -> exit {code}, {len(buffer.getvalue().splitlines())} lines printed")
    print("  argparse's help action fires before any required check.")

    print()
    print("=" * 78)
    print("3. The optional group allows zero -- 'at least one' is NOT expressible")
    print("=" * 78)
    print(f"  nothing given -> from-file={parser.parse_args(['--json']).from_file!r} "
          f"from-url={parser.parse_args(['--json']).from_url!r}")
    print("  Both None. A group enforces at-most-one; required=True enforces exactly-one.")
    print("  There is no 'at least one' in argparse. See validate() for the fix.")

    print()
    print("=" * 78)
    print("4. Groups can be nested inside argument groups")
    print("=" * 78)
    for line in parser.format_help().splitlines():
        if line.strip().startswith(("-", "sizing")):
            print(f"  {line.rstrip()}")

    print()
    print("=" * 78)
    print("5. Rules argparse cannot express, handled by validate()")
    print("=" * 78)
    checks = [
        (["--json", "--auto", "--width", "3"], "dependency-ish rule"),
        (["--json", "--from-file", "a", "--from-url", "b"], "already covered by a group"),
        (["--json", "--width", "99999"], "range involving an option"),
    ]
    for argv, label in checks:
        print(f"  {argv!s:<44} ({label})")
        print(f"      validate -> {_validate_err(parser, argv)}")

    print()
    print("=" * 78)
    print("6. Positional members of a group work, but read badly")
    print("=" * 78)
    pos = argparse.ArgumentParser(prog="render", add_help=False)
    group = pos.add_mutually_exclusive_group()
    group.add_argument("first", nargs="?")
    group.add_argument("--second")
    print(f"  usage: {pos.format_usage().strip()}")
    print(f"  a --second b -> {_try(pos, ['a', '--second', 'b'])}")
    print("  Mixing positionals and options in one group is legal but confusing")
    print("  in help output. Prefer all-options.")

    return 0


def _try(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
        return "accepted"
    except SystemExit:
        return _err(buffer.getvalue())


def _validate_err(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Run parse + validate with stderr captured, and describe the outcome.

    Parsing happens *inside* the capture, because a group violation is reported
    by argparse itself and would otherwise print over the lesson output.
    """
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            validate(parser.parse_args(argv), parser)
            return "accepted"
        except SystemExit:
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
