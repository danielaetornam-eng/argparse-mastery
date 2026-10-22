"""Lesson 35 - ``parse_known_args`` and the two-pass migration.

Run me
------
    python examples/35_parse_known_args_migration.py --new 1 --legacy-thing x
    python examples/35_parse_known_args_migration.py --demo

The signature
-------------
``parse_known_args()`` returns ``(namespace, extras)`` instead of failing on
tokens it does not recognise. That is the whole feature.

Three legitimate uses
--------------------
1. **Pass-through.** Your CLI has its own flags and forwards the rest to a
   subprocess. ``extras`` is the argv you hand on.
2. **Progressive migration.** You are renaming a flag. Accept the new one strictly
   and route the old one through a deprecation shim (lesson 34), removing the shim
   after two releases.
3. **Plugin flags.** A core parser plus plugin-provided options. Validate that
   ``extras`` is empty, or you have silently mistyped a flag.

The illegitimate use, and its failure mode
------------------------------------------
::

    try:
        args = parser.parse_args()
    except SystemExit:
        pass          # now `args` does not exist, and --help was swallowed too

This is worse than not catching. ``--help`` exits **0**, so a bare
``except SystemExit`` eats it and execution continues with an undefined variable --
or, worse, a stale one from an earlier loop iteration.

If you must catch something, catch :class:`argparse.ArgumentError`, or use
``argparse_mastery.parse_or_raise``, which reconciles the "trailing arguments go
through ``error()``" gap by overriding ``error`` as well.

The dangerous property of ``parse_known_args``
----------------------------------------------
**A typo in your own flag is indistinguishable from intended pass-through.**

::

    prog --verboze file.txt     -> succeeds, --verboze silently ignored

That is fine for a wrapper tool and unacceptable for anything else. Mitigations:

* If ``extras`` must be empty, check it::

      if extras:
          parser.error(f"unrecognized arguments: {' '.join(extras)}")

* Better: keep using ``parse_args`` for strict parsers, and reserve
  ``parse_known_args`` for commands whose whole purpose is pass-through.
* For a genuinely mixed parser, ``allow_abbrev=False`` plus the check above gives
  a strict mode that is still useful.

Two-pass config bootstrapping
----------------------------
The other common use: you need ``--config`` before the parser exists, but
``--config`` is one of the parser's options (lesson 29). Two passes solve it
without a throwaway parser::

    known, _ = parser.parse_known_args()      # pass 1: cheap, tolerant
    parser.set_defaults(**load(known.config))
    args = parser.parse_args()                # pass 2: strict, final

Pass 1 must be tolerant -- it only runs to find the config path. Pass 2 is the
one that validates, so users still get proper errors for everything else. A
throwaway pre-parser (lesson 29's ``find_config``) is slightly cleaner, since it
avoids running the real parser's validation twice, but this version is fine and
more common in the wild.

Intermixed
----------
``parse_intermixed_args`` (3.7+) also returns no extras -- it raises ``TypeError``
if the parser uses ``nargs=REMAINDER`` or ``PARSER`` positionals or subparsers. It
exists for "options and files in any order" (grep, wc, sort) and is covered in
lesson 09.
"""

from __future__ import annotations

import argparse
import sys
import warnings


def build_parser() -> argparse.ArgumentParser:
    """The parser, including the deprecated ``--old`` spelling of ``--new``.

    Note what the deprecated flag does and does not do: it writes to the same
    ``dest`` (``new``), so there is one implementation, and it is
    ``help=argparse.SUPPRESS`` so it is absent from ``--help``. What it does *not*
    do is warn -- the warning lives in :func:`migrate`, because neither ``type=``
    nor ``help=`` can produce one. That is the honest limitation of flag
    deprecation in argparse: ``deprecated=True`` exists for *subcommands* (3.14+),
    not for options. See lesson 34.
    """
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="Lesson 35: parse_known_args and two-pass bootstrapping.",
        epilog=(
            "  migrate --new 2 --unknown-flag x   extras=['--unknown-flag','x']\n"
            "  migrate --new 2 --strict --unknown-flag x   error: unrecognized...\n"
        ),
    )
    parser.add_argument("--new", type=int, default=None, metavar="N",
                        help="the new spelling")
    parser.add_argument("--old", type=int, default=None, metavar="N",
                        help="deprecated spelling of --new (hidden; warns on use)")
    parser.add_argument("--strict", action="store_true",
                        help="reject any leftover arguments")
    return parser


def build_parser_clean() -> argparse.ArgumentParser:
    """The real parser: one ``--old`` that warns, and a strictness switch."""
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="Lesson 35: parse_known_args and two-pass bootstrapping.",
        epilog=(
            "  migrate --new 2 --unknown-flag x   extras=['--unknown-flag','x']\n"
            "  migrate --new 2 --strict --unknown-flag x   error: unrecognized..."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--new", type=int, default=None, metavar="N",
                        help="the current spelling")
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="config file (found in pass 1)")
    parser.add_argument("--strict", action="store_true",
                        help="reject any leftover arguments instead of forwarding them")
    parser.add_argument("--verbose", action="store_true", help="chatty output")

    # The deprecated spelling: same dest, warns, hidden.
    parser.add_argument(
        "--old",
        dest="new",
        type=int,
        default=None,
        metavar="N",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(_deprecated_used=False)

    return parser


def migrate(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """The pass-through form: parse what we know, hand back the rest."""
    parser = build_parser_clean()
    args, extras = parser.parse_known_args(argv)
    if "--old" in (argv or sys.argv[1:]):
        warnings.warn(
            "--old is deprecated; use --new (removed in 2.0)",
            DeprecationWarning,
            stacklevel=2,
        )
    return args, extras


def migrate_strict(argv: list[str] | None = None) -> int:
    """The strict form: leftovers are an error unless ``--strict`` is off."""
    parser = build_parser_clean()
    args, extras = parser.parse_known_args(argv)

    if extras and args.strict:
        parser.error(f"unrecognized arguments: {' '.join(extras)}")

    print(f"new   : {args.new!r}")
    print(f"config: {args.config!r}")
    print(f"extras: {extras!r}")
    return 0


def bootstrap_two_pass(argv: list[str] | None = None) -> argparse.Namespace:
    """Pass 1 finds the config, installs it as defaults; pass 2 validates.

    Pass 1 must be tolerant -- its only job is discovery. Pass 2 is the strict one,
    so users still get proper errors for everything except the config path itself.
    """
    parser = build_parser_clean()

    # Pass 1: tolerant. Only discovery matters here.
    known, _ = parser.parse_known_args(argv)

    if known.config:
        # Pretend this came from load_config_file(); see lesson 29 for the real
        # thing, including the validation that non-argv values require.
        parser.set_defaults(new=99)

    # Pass 2: strict, final, and the only one whose errors the user sees.
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return migrate_strict(argv)


def demo() -> int:
    print("=" * 78)
    print("1. The signature: (namespace, extras)")
    print("=" * 78)
    parser = build_parser_clean()
    for argv in (
        ["--new", "2"],
        ["--new", "2", "--unknown-flag", "x"],
        ["--new", "2", "--", "--not-mine"],
        [],
    ):
        args, extras = parser.parse_known_args(argv)
        print(f"  {argv!s:<38} new={args.new!r:<6} extras={extras!r}")

    print()
    print("=" * 78)
    print("2. parse_args on the same input")
    print("=" * 78)
    for argv in (["--new", "2"], ["--new", "2", "--unknown-flag", "x"]):
        print(f"  {argv!s:<38} -> {_show(parser, argv)}")

    print()
    print("  The first row is the problem: a typo in YOUR flag is indistinguishable")
    print("  from intended pass-through. `--verboze` succeeds and does nothing.")

    print()
    print("=" * 78)
    print("3. Strict mode: reject leftovers")
    print("=" * 78)
    for argv in (
        ["--new", "2", "--unknown-flag", "x"],
        ["--new", "2", "--strict", "--unknown-flag", "x"],
        ["--new", "2", "--strict"],
    ):
        print(f"  {argv!s:<46} -> exit {_run(argv)}")

    print()
    print("  Turning strictness on is a one-line check and removes the whole")
    print("  typo-swallowing failure mode. Worth having even when most of your")
    print("  commands do pass through.")

    print()
    print("=" * 78)
    print("4. '--' and extras")
    print("=" * 78)
    args, extras = parser.parse_known_args(["--new", "2", "--", "--not-mine", "x"])
    print("  argv = ['--new','2','--','--not-mine','x']")
    print(f"  new    = {args.new!r}")
    print(f"  extras = {extras!r}")
    print()
    print("  argparse REMOVES the '--' from extras and leaves everything after it,")
    print("  so a pass-through argv needs no cleanup -- unlike nargs=REMAINDER (lesson 10).")

    print()
    print("=" * 78)
    print("5. The two-pass bootstrap")
    print("=" * 78)
    print("  migrate --new 2                 -> "
          f"{bootstrap_two_pass(['--new', '2']).new!r}")
    print("  migrate --config c.json --new 2 -> "
          f"{bootstrap_two_pass(['--config', 'c.json', '--new', '2']).new!r}")
    print()
    print("  Pass 1 found --config and installed a default of 99; pass 2 let the")
    print("  explicit --new 2 win. That is the whole mechanism -- defaults lose to")
    print("  argv automatically.")

    print()
    print("=" * 78)
    print("6. The anti-pattern, and why --help makes it worse")
    print("=" * 78)
    print("  try:")
    print("      args = parser.parse_args()")
    print("  except SystemExit:")
    print("      pass")
    print()
    print("  --help exits 0, so the bare except swallows it too, and `args` either")
    print("  does not exist or holds a value from a previous iteration. Catch")
    print("  ArgumentError instead -- or use argparse_mastery.parse_or_raise,")
    print("  which also closes the trailing-arguments gap.")

    print()
    print("=" * 78)
    print("7. A rule of thumb")
    print("=" * 78)
    print("  use parse_args           unless the command's job is to forward argv")
    print("  use parse_known_args     for leaf 'wrapper' commands only")
    print("  check `extras`          whenever your tool is not a wrapper")
    print("  use --strict             to turn strictness on per-invocation")

    return 0


def _show(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            parser.parse_args(argv)
            return "(accepted)"
        except SystemExit as exc:
            code = 0 if exc.code is None else int(exc.code)
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return f"{code}  {line.split(': error:', 1)[1].strip()}"
    return str(code)


def _run(argv: list[str]) -> int:
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return migrate_strict(argv)
        except SystemExit as exc:
            return 0 if exc.code is None else int(exc.code)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
