"""Lesson 33 - ``prog``, ``usage=``, and deterministic help width.

Run me
------
    python examples/33_prog_usage_and_width.py --help
    python examples/33_prog_usage_and_width.py --demo

``prog``: what the help calls the program
----------------------------------------
argparse guesses from ``sys.argv[0]``, which is wrong in three common situations:

============================================  =========================
Invocation                                    Guessed ``prog``
============================================  =========================
``python -m mypkg``                           ``__main__.py`` or ``python``
installed console script                      the full path to the script
symlinked wrapper (``~/.local/bin/kubectl``)   ``kubectl``
============================================  =========================

Set it explicitly. Beyond cosmetics, ``prog`` appears in every error message, and
a hard-coded path leaking into an error is a genuine support problem::

    /home/rob/.venv/bin/myapp: error: ...

Accept it as a parameter so tests can pin it::

    def build_parser(prog: str = "myapp") -> argparse.ArgumentParser:

``usage=``: replacing the whole usage line
-----------------------------------------
argparse builds the usage line from the arguments. ``usage=`` replaces it wholesale
-- which is occasionally right and usually a mistake, because it must then be
maintained by hand and will drift.

Legitimate uses:

* hiding internal arguments from the usage line -- though ``help=SUPPRESS`` already
  does this for you, and does it correctly (see below)
* a usage line that reads better than the generated one::

      usage="myapp [-v] [-q] [--config PATH] <command> [<args>...]"

* ``usage=argparse.SUPPRESS`` to remove the usage section entirely, which is what
  you want if you have replaced ``--help`` with a formatted overview

If you only want to *tweak* the generated line, that is a ``format_usage``
override, not ``usage=``. ``argparse_mastery.formatters.SectionedHelpFormatter``
shows the pattern.

**You usually need neither.** ``help=argparse.SUPPRESS`` removes an argument from
the usage line *and* the help table, because ``HelpFormatter.add_argument`` skips
those actions before they ever reach ``_format_args``. It is the correct tool, and
it is complete -- a widely repeated claim that SUPPRESS leaves the usage line
dirty is wrong.

``max_help_position`` and ``indent_increment``
---------------------------------------------
These go on the formatter and control the two-column layout::

    class Wide(HelpFormatter):
        def __init__(self, prog, **kw):
            kw.setdefault("max_help_position", 40)
            kw.setdefault("indent_increment", 4)
            super().__init__(prog, **kw)

* ``max_help_position`` (default 24) is the maximum column at which help text
  starts. Beyond it, help text moves to its own line.
* ``indent_increment`` (default 2) is the indent for nested groups and wrapped
  help text.

When long flags push past the default, the layout silently switches to one line
per flag with help underneath. That is correct but ugly; raising
``max_help_position`` keeps the table.

Width
-----
``HelpFormatter(width=None)`` means "ask the terminal, honouring ``COLUMNS``".
Consequences:

* ``myapp --help | less`` may format differently from ``myapp --help``.
* Golden-file tests fail on a different machine.
* Output piped into a file has no width at all, and argparse falls back to 80.

For deterministic output you must construct the formatter yourself, because
``ArgumentParser.format_help()`` takes no arguments. ``render_help`` below does it
in the same order ``format_help`` does. This is the single most useful thing to
know for testing help output, and it costs five lines.

``add_help=False``: replacing ``--help``
---------------------------------------
Worth doing when you want a *formatted overview* instead of argparse's table.
Remember to:

1. delete the ``-h``/``--help`` action from ``parser._actions`` (there is no
   public API),
2. add your own ``--help`` with ``action='store_true'``,
3. handle it early in ``main`` and return.

Forgetting (1) produces ``conflicting option string: -h``, which is the usual
symptom.
"""

from __future__ import annotations

import argparse
import sys
from argparse import HelpFormatter


def build_parser(prog: str = "myapp") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Lesson 33: prog, usage, and width.",
        epilog="See also: argparse_mastery.formatters.render_help for fixed width.",
        formatter_class=WideHelpFormatter,
    )
    parser.add_argument("-c", "--config", metavar="PATH", help="configuration file")
    parser.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    parser.add_argument("--reconnect-timeout", type=int, default=30, metavar="SECONDS",
                        help="how long to wait before reconnecting")
    parser.add_argument("command", help="the command to run")
    return parser


class WideHelpFormatter(HelpFormatter):
    """Keep the two-column layout even when option names are long.

    ``max_help_position`` (default 24) is the furthest right column at which help
    text may start. Exceed it and argparse moves help text to its own line, which
    is correct but visually ragged for a parser with long flag names.

    ``indent_increment`` (default 2) controls the indent for wrapped help text and
    nested groups.
    """

    def __init__(self, prog: str, **kwargs: object) -> None:
        kwargs.setdefault("max_help_position", 40)
        kwargs.setdefault("indent_increment", 4)
        super().__init__(prog, **kwargs)  # type: ignore[arg-type]


def render_help(parser: argparse.ArgumentParser, *, width: int | None = 80) -> str:
    """Render ``--help`` with an explicit width, deterministically.

    ``ArgumentParser.format_help()`` takes no arguments -- it builds its own
    formatter with ``width=None``, meaning "ask the terminal". To pin the width
    (for a golden-file test, a generated man page, or a narrow CI pane) you must
    drive the formatter yourself, in the same order ``format_help`` does:

    1. ``add_usage(usage, actions, groups, prefix)``
    2. ``add_text(description)``
    3. ``add_argument`` for each action
    4. ``add_text(epilog)``

    Five lines, several private attributes, and the reason help output stops
    being flaky in CI. Worth keeping in one helper rather than scattered across
    tests.
    """
    formatter = parser.formatter_class(prog=parser.prog, width=width)
    formatter.add_usage(
        parser.usage, parser._actions, parser._mutually_exclusive_groups
    )
    formatter.add_text(parser.description)
    for action in parser._actions:
        formatter.add_argument(action)
    formatter.add_text(parser.epilog)
    return formatter.format_help()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"command={args.command} verbose={args.verbose} config={args.config}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. prog: set it, because argparse's guess is often wrong")
    print("=" * 78)
    print("  Invocation                         argparse would guess")
    print("  ---------------------------------  --------------------------------")
    print("  python -m mypkg                    '__main__.py'")
    print("  ./bin/myapp                        './bin/myapp'")
    print("  ~/.local/bin/myapp (symlink)       the symlink target's path")
    print("  an installed console script        the full path in site-packages")
    print()
    print("  With prog= set:")
    parser = build_parser(prog="myapp")
    print(f"    {parser.format_usage().strip()}")

    print()
    print("=" * 78)
    print("2. usage= replaces the whole line; SUPPRESS removes the section")
    print("=" * 78)
    generated = argparse.ArgumentParser(prog="myapp", add_help=False)
    generated.add_argument("-c", metavar="PATH")
    generated.add_argument("command")
    print(f"  generated : {generated.format_usage().strip()}")

    custom = argparse.ArgumentParser(prog="myapp", usage="myapp [-v] [-c PATH] <cmd> ...")
    print(f"  usage=    : {custom.format_usage().strip()}")

    hidden = argparse.ArgumentParser(prog="myapp", usage=argparse.SUPPRESS)
    hidden.add_argument("--internal")
    print(f"  SUPPRESS  : {hidden.format_usage().strip()!r}  (empty)")

    print()
    print("  usage= is a full replacement you must maintain by hand. To *tweak* the")
    print("  generated line, override format_usage instead.")

    print()
    print("=" * 78)
    print("3. help=SUPPRESS does not remove from the usage line")
    print("=" * 78)
    leaky = argparse.ArgumentParser(prog="myapp", add_help=False)
    leaky.add_argument("--keep")
    leaky.add_argument("--internal", help=argparse.SUPPRESS)
    print(f"  usage: {leaky.format_usage().strip()}")
    print(f"  '--internal' in help? {'--internal' in leaky.format_help()}")
    print()
    print("  This is argparse's known asymmetry. Two ways to close it:")
    print("    - override format_usage and filter SUPPRESS actions")
    print("    - give the Action a format_usage returning '' (lesson 25)")

    print()
    print("=" * 78)
    print("4. max_help_position: two-column vs one-line-per-flag")
    print("=" * 78)
    narrow = argparse.ArgumentParser(
        prog="myapp", add_help=False, formatter_class=HelpFormatter
    )
    for flags in ("-c", "--reconnect-timeout"):
        narrow.add_argument(flags, type=int, help="a help string of moderate length")
    print("  max_help_position=24 (default):")
    for line in narrow.format_help().splitlines():
        print(f"    {line.rstrip()}")
    print()
    wide = argparse.ArgumentParser(
        prog="myapp", add_help=False, formatter_class=WideHelpFormatter
    )
    for flags in ("-c", "--reconnect-timeout"):
        wide.add_argument(flags, type=int, help="a help string of moderate length")
    print("  max_help_position=40:")
    for line in wide.format_help().splitlines():
        print(f"    {line.rstrip()}")

    print()
    print("=" * 78)
    print("5. Width: None means 'ask the terminal'")
    print("=" * 78)
    parser = build_parser()
    for width in (60, 80, 120):
        text = render_help(parser, width=width)
        longest = max(len(line) for line in text.splitlines())
        print(f"  width={width:<4} longest line = {longest}")
    print()
    print("  format_help() cannot take a width, so render_help() drives the")
    print("  formatter by hand. Five lines, and help output stops being flaky.")

    print()
    print("=" * 78)
    print("6. Replacing --help entirely")
    print("=" * 78)
    custom = argparse.ArgumentParser(prog="myapp", add_help=False)
    # Step 1: remove the built-in -h action (there is no public API for this).
    for action in list(custom._actions):
        if isinstance(action, argparse._HelpAction):
            custom._remove_action(action)
    # Step 2: add your own.
    custom.add_argument(
        "-h", "--help", action="store_true", help="show a formatted overview"
    )
    custom.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    custom.add_argument("command", help="the command")
    args = _parse(custom, ["--help", "status"])
    print(f"  parse_args(['--help','status']) -> args.help = {args.help!r}")
    print(f"  (exit status: {_parse(custom, ['--help'])})   <- 'command' is required, so")
    print("   the bare --help run above fails on the missing positional. argparse's")
    print("   own --help short-circuits before required checks; a store_true flag does")
    print("   not, which is a real behavioural difference to be aware of.")
    print()
    print("  Step 1 is required: without it you get")
    print("      ValueError: conflicting option string: -h")
    print("  Handle args.help early in main() and print your own overview.")
    print()
    print("  Note the flag has action='store_true', so it does NOT print and exit")
    print("  the way argparse's own --help does. You control the output format and")
    print("  the exit code -- but you also give up the short-circuit behaviour above.")

    return 0


def _parse(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace | int:
    """Parse, returning the exit code instead of raising SystemExit."""
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        try:
            return parser.parse_args(argv)
        except SystemExit as exc:
            return 0 if exc.code is None else int(exc.code)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
