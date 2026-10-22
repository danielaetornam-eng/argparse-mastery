"""Lesson 06 - ``metavar``, ``dest``, and hiding things from ``--help``.

Run me
------
    python examples/06_metavar_and_dest.py --dest out.txt --force
    python examples/06_metavar_and_dest.py --demo

Three keywords that change what the user sees without changing what you get.

``metavar``: the placeholder, not the name
------------------------------------------
By default argparse invents the placeholder from ``dest``::

    add_argument("--log-level")   ->  [--log-level LOG_LEVEL]

``metavar`` replaces that placeholder::

    add_argument("--log-level", metavar="LEVEL")  ->  [--log-level LEVEL]

``metavar`` affects **only the usage line and the help output**. The namespace
key is still ``dest``. This is the single best readability lever you have: users
read ``usage:``, not your Python.

Rules for choosing metavars:

* Use **uppercase, short, domain-specific** names: ``FILE``, ``PATH``, ``LEVEL``,
  ``N``, ``TIMEOUT``, ``KEY=VALUE``.
* For options, keep it singular: ``--include FILE`` (repeatable), not ``FILES``.
* For positionals with ``nargs`` > 1, the metavar repeats and gains ``...``:
  ``--coord X Y`` or with ``nargs='*'`` you get ``[FILE ...]``.
* Never name a metavar after the Python type unless it helps the user
  (``--port PORT`` beats ``--port 8080``-style noise; ``--port int`` is worse
  than ``--port PORT``).

``dest``: control the namespace key
----------------------------------
By default ``dest`` is derived. Override it when the derivation is wrong::

    add_argument("--log-level", dest="log_level")     # same as derived
    add_argument("-l", "--log-level", dest="level")   # shorter attribute
    add_argument("--dry-run", dest="dry_run")         # same as derived
    add_argument("-vvv", dest="verbose")              # the -vv trap, fixed

Override ``dest`` whenever the option string and the attribute you want to read
differ. Two reasons this matters beyond aesthetics:

* **Renaming safety.** Users' scripts depend on the *spelling*, not your
  attribute name, so you can change ``dest`` freely. Keeping them aligned means
  every rename is a breaking change.
* **Underscore vs dash.** ``--dry-run`` -> ``dry_run`` always. Never write
  ``args.dry-run``.

Hiding arguments
----------------
``help=argparse.SUPPRESS`` removes an argument from the help output **and from the
usage line**, while leaving it fully functional::

    add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    # usage: prog        <- --debug is gone from here too
    # prog --debug       -> still works

The mechanism: ``HelpFormatter.add_argument`` skips actions whose ``help`` is
``SUPPRESS``, so they never reach ``_format_action`` *or* ``_format_args``. That
means SUPPRESS is complete -- there is no leftover in the usage line. (A widely
repeated claim says otherwise; it is wrong on every version this repository
tests. See demo section 4.)

The right use is an escape hatch: a flag that bypasses validation, changes a
default, or dumps internals -- things you support but do not advertise, because
advertising them makes people depend on them. ``--unsafe`` in a tool with a safe
default, or an internal ``--for-tests-only``.

Note the difference from ``dest=argparse.SUPPRESS``, which is a different feature:
that omits the *namespace attribute* when the argument is absent (lesson 03), and
is how you detect "was it passed?". The two are independent and can be combined.

Both at once
------------
``dest=argparse.SUPPRESS`` on an *optional* also means the attribute only exists
if the flag is passed -- ``--debug`` then gives ``args.debug=True`` and omitting
it leaves no attribute at all. Convenient, but it makes every read site need
``getattr(args, "debug", False)``. Prefer ``action="store_true"`` plus
``getattr``, or ``BooleanOptionalAction`` with ``default=None``.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ship",
        description="Lesson 06: metavar, dest, and SUPPRESS.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # metavar changes the placeholder only. dest is still `log_level`.
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        choices=("debug", "info", "warning", "error"),
        default="warning",
        help="verbosity of the log output",
    )

    # dest overrides the derived attribute. `-n/--name` would give `name`;
    # here we deliberately read `args.output` instead, because the option
    # is about an output file, not a generic name.
    parser.add_argument(
        "-d",
        "--dest",
        dest="output",
        metavar="PATH",
        default="-",
        help="where to write (default: stdout)",
    )

    # A multi-value option: metavar repeats per token and gains an ellipsis.
    parser.add_argument(
        "--define",
        nargs=2,
        metavar=("NAME", "VALUE"),
        action="append",
        default=[],
        help="define a variable (repeatable)",
    )

    # SUPPRESS: fully functional, absent from the help text.
    parser.add_argument(
        "--force",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # A visible alias for the same dest, to show that help= controls only the
    # *short* spelling's visibility when they differ.
    parser.add_argument(
        "--legacy-output",
        dest="output",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"log_level : {args.log_level!r}")
    print(f"output    : {args.output!r}   (dest='output', options were --dest/--legacy-output)")
    print(f"define    : {args.define!r}")
    print(f"force     : {getattr(args, 'force', False)}   (hidden but functional)")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 76)
    print("1. metavar changes the placeholder; dest changes the attribute")
    print("=" * 76)
    print(f"{'option':<18} {'metavar':<18} {'dest':<12} defaults to")
    print("-" * 76)
    for action in parser._actions:
        if action.dest == "help" or not action.option_strings:
            continue
        default = action.metavar if action.metavar is not None else action.dest.upper()
        print(f"{action.option_strings[0]:<18} {default!s:<18} {action.dest:<12} "
              f"{action.default!r}")
    print()
    print("  `--log-level LEVEL` in the usage line; `args.log_level` in your code.")

    print()
    print("=" * 76)
    print("2. Tuple metavars for nargs=2 -- one name per token")
    print("=" * 76)
    pair = argparse.ArgumentParser(prog="demo", add_help=False)
    pair.add_argument("--define", nargs=2)
    pair.add_argument("--tuple-meta", nargs=2, metavar=("NAME", "VALUE"))
    print("  nargs=2, no metavar :")
    print("    " + "\n    ".join(pair.format_usage().splitlines()[:4]))
    print("  nargs=2, metavar=('NAME','VALUE') :")
    print("    " + "\n    ".join(pair.format_usage().splitlines()[:4]))

    print()
    print("=" * 76)
    print("3. dest override: two spellings, one attribute")
    print("=" * 76)
    ns = parser.parse_args(["--dest", "a.txt"])
    print(f"  --dest a.txt        -> output={ns.output!r}")
    ns = parser.parse_args(["--legacy-output", "b.txt"])
    print(f"  --legacy-output b   -> output={ns.output!r}   (hidden spelling)")
    ns = parser.parse_args(["--dest", "c.txt", "--legacy-output", "d.txt"])
    print(f"  both                -> output={ns.output!r}   (last one on the line wins)")

    print()
    print("=" * 76)
    print("4. help=SUPPRESS removes it from the usage line too")
    print("=" * 76)
    hidden = argparse.ArgumentParser(prog="ship", add_help=False)
    hidden.add_argument("--keep", help="visible")
    hidden.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    print(f"  usage: {hidden.format_usage().strip()}")
    print(f"  '--force' in help?  {'--force' in hidden.format_help()}")
    print(f"  '--force' in usage? {'--force' in hidden.format_usage()}")
    print()
    print("  SUPPRESS is complete: HelpFormatter.add_argument skips the action, so it")
    print("  never reaches _format_action or _format_args. It still works:")
    print(f"    parse_args(['--force']).force -> {hidden.parse_args(['--force']).force}")

    print()
    print("=" * 76)
    print("5. dest=SUPPRESS is a DIFFERENT feature")
    print("=" * 76)
    quiet = argparse.ArgumentParser(prog="demo", add_help=False)
    quiet.add_argument("--mode", choices=["a", "b"], dest=argparse.SUPPRESS)
    for argv in ([], ["--mode", "a"]):
        ns = quiet.parse_args(argv)
        print(f"  argv={argv!s:<16} vars(ns)={vars(ns)}")
    print("  With dest=SUPPRESS the attribute exists ONLY when the flag is given.")

    print()
    print("=" * 76)
    print("6. --define with nargs=2 and action='append'")
    print("=" * 76)
    for argv in (
        ["--define", "a", "1"],
        ["--define", "a", "1", "--define", "b", "2"],
    ):
        print(f"  {argv!s:<44} -> {parser.parse_args(argv).define!r}")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
