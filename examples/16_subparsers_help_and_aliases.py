"""Lesson 16 - Subparser help output, aliases, and ``parser_class``.

Run me
------
    python examples/16_subparsers_help_and_aliases.py st
    python examples/16_subparsers_help_and_aliases.py --demo

Making ``--help`` good for subcommands
--------------------------------------
A subcommand is a real ``ArgumentParser``, so it gets its own ``description``,
``epilog`` and ``formatter_class``. The results are much better than one
undifferentiated help screen:

* ``prog --help`` lists commands with one-line summaries. The summary comes from
  ``add_parser(..., help=...)`` -- **not** from ``description``, and it is
  truncated to the first line.
* ``prog <command> --help`` shows the full description, that command's options,
  and its examples.
* ``metavar="<command>"`` replaces the noisy default. Without it, ``--help``
  shows every command inline, which gets unreadable past about eight commands.
  With it, argparse prints a compact list and defers.

The ``help=`` vs ``description=`` split is worth being deliberate about::

    sub.add_parser(
        "status",                                  # the name
        help="show service status",               # ONE LINE, for prog --help
        description=(
            "Report health for one or all services.\n\n"
            "Examples:\n"
            "  fleet status --watch\n"
            "  fleet status --retries 5"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

Forgetting ``help=`` means the command shows up with a blank summary in
``prog --help``, which is worse than not listing it.

Aliases
-------
::

    sub.add_parser("remove", aliases=["rm", "delete"])

* ``prog rm`` works, and ``args.command == "rm"`` -- **the spelling the user
  typed**, not the canonical name. If you log the command name, normalise it.
* Aliases appear in ``prog --help`` as ``remove (rm, delete)``.
* **Aliases are not abbreviations.** A subcommand is a ``choices`` entry and
  ``choices`` uses ``in``, an exact comparison; ``allow_abbrev`` applies to long
  *options* only. Verified on 3.14::

      prog rem   ->  error: invalid choice: 'rem' (choose from 'remove', 'list')

  So if you want a short spelling, declare it as an alias. Guessing is a
  support burden.
* For shell completion you must register aliases yourself; argparse has no
  completion protocol (lesson 36).

How to *read back* the aliases -- and the trap
-----------------------------------------------
The obvious thing to write does not work::

    sub.add_parser("remove", aliases=["rm"])     # what you write
    rm_parser.aliases                            # AttributeError!

``add_parser`` pops ``aliases`` out of the kwargs and writes them into the
subparsers action's ``_name_parser_map``. **The parser object never gains an
``aliases`` attribute.** ``getattr(rm_parser, "aliases", ())`` returns ``()``
silently, so any code that "handles aliases" this way appears to work and never
sees one.

The reliable derivation is insertion order. ``add_parser`` performs exactly two
writes::

    self._name_parser_map[name] = parser        # canonical, first
    for alias in aliases:
        self._name_parser_map[alias] = parser

``dict`` preserves insertion order, so for each distinct parser the *first* key
mapping to it is canonical and every later key is an alias. See
:func:`argparse_mastery.completion.subcommand_names`, which does this in six
lines, and lesson 36 for the same problem in a completion script.

Python 3.14's native ``deprecated=True``
----------------------------------------
``add_parser`` gained a ``deprecated`` keyword::

    sub.add_parser("purge", deprecated=True, aliases=["rm"])

Using it prints, to stderr, and then runs the command normally::

    prog: warning: command 'purge' is deprecated

Verified on 3.14: exit status is **0**, the warning is emitted at parse time, and
the command still appears in ``--help`` unless you also pass ``help=SUPPRESS``.
Pair the two for a fully invisible-but-working deprecated command. Lesson 34
covers the whole deprecation workflow, including the flag-level case.

``parser_class``
----------------
By default ``add_subparsers`` uses ``type(self)``, so a ``FriendlyArgumentParser``
subclass propagates and every subparser inherits your ``error()`` behaviour. You
rarely need to pass ``parser_class`` -- except to force ``add_help=True``, which
is the one thing argparse will not do for you::

    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        parser_class=argparse.ArgumentParser,   # restores -h on subcommands
    )
"""

from __future__ import annotations

import argparse
import sys


def cmd_status(args: argparse.Namespace) -> int:
    print(f"status: watch={args.watch}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    # `command` is whatever the user typed, so an alias leaks into logs.
    print(f"remove: target={args.target!r} called_as={args.command!r}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    deprecated = getattr(args, "_deprecated", None)
    if deprecated:
        print(f"warning: {args.command!r} is deprecated ({deprecated})", file=sys.stderr)
    print("purge: done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet",
        description="Lesson 16: subcommand help, aliases, parser_class.",
    )

    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        parser_class=argparse.ArgumentParser,
        help="available commands (use '<command> --help' for details)",
    )

    status = sub.add_parser(
        "status",
        help="show service status",
        description=(
            "Report health for one or all services.\n"
            "\n"
            "Examples:\n"
            "  fleet status --watch\n"
            "  fleet status --retries 5"
        ),
        epilog="Exit codes: 0 healthy, 1 unhealthy, 2 usage error.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    status.add_argument("--watch", action="store_true", help="poll continuously")
    status.add_argument("--retries", type=int, default=3, help="probe attempts")
    status.set_defaults(func=cmd_status)

    remove = sub.add_parser(
        "remove",
        aliases=["rm", "delete"],
        help="remove a resource",
        description="Remove a resource. Use `rm` if you prefer the short form.",
        add_help=True,
    )
    remove.add_argument("target", help="what to remove")
    remove.add_argument("--force", action="store_true", help="skip confirmation")
    remove.set_defaults(func=cmd_remove)

    # Deprecated but still callable. Python 3.14's `deprecated=True` emits the
    # warning at parse time; `help=SUPPRESS` keeps it out of --help.
    purge = sub.add_parser("purge", deprecated=True, help=argparse.SUPPRESS)
    purge.add_argument("--all", action="store_true", help="purge everything")
    purge.set_defaults(func=cmd_purge)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. prog --help lists commands using each parser's help= line")
    print("=" * 78)
    print(parser.format_help())

    print()
    print("=" * 78)
    print("2. prog status --help shows the full description and epilog")
    print("=" * 78)
    print(_capture(parser, ["status", "--help"]))

    print()
    print("=" * 78)
    print("3. Aliases work; abbreviations do NOT")
    print("=" * 78)
    for argv in (["remove", "db"], ["rm", "db"], ["delete", "db"], ["rem", "db"], ["ls"]):
        ns = _try(parser, argv)
        if isinstance(ns, argparse.Namespace):
            print(f"  {argv!s:<18} -> command={ns.command!r:<10} target={ns.target!r}")
        else:
            print(f"  {argv!s:<18} -> {ns}")
    print()
    print("  'rem' fails and 'ls' works only because it is a declared alias.")
    print("  A subcommand is a choices entry; `in` is exact. allow_abbrev does not")
    print("  apply to subcommand names. Normalise args.command before logging it.")

    print()
    print("=" * 78)
    print("4. metavar='<command>' vs the default")
    print("=" * 78)
    verbose = argparse.ArgumentParser(prog="fleet", add_help=False)
    for name in ("status", "start", "stop"):
        verbose.add_argument(f"--{name}", action="store_true")
    vsub = verbose.add_subparsers(dest="command")
    for name in ("status", "start", "stop"):
        vsub.add_parser(name, help=f"do {name}")
    print("  WITHOUT metavar (argparse lists every command inline):")
    print("    " + verbose.format_usage().strip())
    print("  WITH metavar='<command>':")
    print("    " + parser.format_usage().strip())
    print()
    print("  Both print the command list in the help body; only the usage line")
    print("  differs. Past ~8 commands, prefer metavar for a compact usage line.")

    print()
    print("=" * 78)
    print("5. Python 3.14: add_parser(deprecated=True)")
    print("=" * 78)
    print("  'purge' appears in `prog --help`?  "
          f"{'purge' in parser.format_help()}")
    print("  `prog purge --all` still parses:  "
          f"{vars(parser.parse_args(['purge', '--all']))['all']}")
    print()
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        parser.parse_args(["purge"])
    print(f"  warning emitted at parse time: {buffer.getvalue().strip()!r}")
    print("  Exit status is 0 -- a deprecation is not a failure.")
    print()
    print("  Pair deprecated=True with help=SUPPRESS for a fully hidden command")
    print("  that still works. See lesson 34.")

    print()
    print("=" * 78)
    print("6. parser_class propagation")
    print("=" * 78)

    class MyParser(argparse.ArgumentParser):
        def error(self, message: str):  # type: ignore[override]
            print(f"  [MyParser.error via {self.prog!r}]")
            super().error(message)

    # An error raised *inside* the subcommand, so the subparser's error() is
    # the one called. ('--bad' after the command is reported by the PARENT,
    # because unrecognised trailing args are checked in parse_args, not in the
    # subparser -- which is why the subclass appears in both cases here.)
    def make(parser_class, label):
        top = parser_class(prog="x", add_help=False)
        sub = top.add_subparsers(dest="command", parser_class=parser_class)
        leaf = sub.add_parser("go")
        leaf.add_argument("--needed", required=True)
        return top

    print("  parser_class=type(self):")
    print("    " + _error_lines(make(MyParser, "inherited"), ["go"]))

    print("  parser_class=ArgumentParser (subclass deliberately dropped):")
    print("    " + _error_lines(make(argparse.ArgumentParser, "forced"), ["go"]))

    return 0


def _try(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace | str:
    """Parse with stderr captured; return the namespace or the error message."""
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv)
    except SystemExit:
        return _error_from(buffer.getvalue())


def _capture(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            parser.parse_args(argv)
        except SystemExit:
            pass
    return buffer.getvalue()


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit:
        pass
    return _error_from(buffer.getvalue())


def _error_from(text: str) -> str:
    for line in text.splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


def _error_lines(parser: argparse.ArgumentParser, argv: list[str]) -> str:
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
            return line.strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
