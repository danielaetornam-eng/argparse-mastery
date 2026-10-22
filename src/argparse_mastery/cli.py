"""The dispatch and application-shell layer.

argparse parses. It does not *run* anything. The gap between the two is where
most hand-rolled CLIs grow a bug, so this module makes the pattern explicit.

The dispatch contract
---------------------
Every subcommand's function has the same signature::

    def cmd_status(args: argparse.Namespace) -> int

and the parser wires it in with ``sub.set_defaults(func=cmd_status)``. Then
``main`` has exactly one branch instead of an ``if/elif`` ladder::

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return ExitCode.USAGE
    return args.func(args)

Why ``set_defaults(func=...)`` beats an if-ladder
-------------------------------------------------
* Adding a subcommand is a pure addition: one parser entry, one function.
* Plugins can register commands without importing each other's code.
* The parse error for a missing/unknown subcommand comes from argparse, so it
  matches every other usage error.
* Tests can call ``cmd_status(args)`` directly, or go through :func:`run_command`
  with a synthetic namespace.

The ``hasattr`` guard matters. If you make subparsers ``required=True`` you get
that check from argparse instead, at the cost of a less helpful message when a
user types nothing. :func:`run_command` handles both: it treats a missing
``func`` as "no command given" and prints help.

``main()`` shape
----------------
:func:`make_main` produces the conventional entry point so every example in this
project behaves identically:

* accepts ``argv`` as a parameter (testable),
* returns an ``int`` exit code rather than calling ``sys.exit`` internally
  (testable, composable),
* maps :class:`~argparse_mastery.errors.UserError` to a message + exit code,
* maps ``KeyboardInterrupt`` to 130,
* and is what ``console_scripts`` should point at.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from .errors import ExitCode, UserError, catch_errors
from .logging_setup import setup_logging

__all__ = ["CommandFunc", "make_main", "run_command"]

#: The signature every subcommand function must have.
CommandFunc = Callable[[argparse.Namespace], int]


def run_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    default_code: int = ExitCode.USAGE,
) -> int:
    """Invoke ``args.func(args)`` with the standard no-command behaviour.

    ::

        args = parser.parse_args(argv)
        return run_command(parser, args)

    Returns ``default_code`` (2) after printing help when no subcommand was
    chosen. That is deliberate: ``myapp`` with no arguments is a usage error, not
    a help request. Users who want help ask for ``--help``.
    """
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return default_code
    result = func(args)
    return default_code if result is None else int(result)


def make_main(
    build: Callable[[], argparse.ArgumentParser],
    *,
    program: str | None = None,
    configure_logging: bool = True,
) -> Callable[[Sequence[str] | None], int]:
    """Build a conventional ``main(argv=None) -> int`` from a parser factory.

    ::

        def main(argv: Sequence[str] | None = None) -> int:
            return make_main(build_parser)(argv)

        if __name__ == "__main__":
            raise SystemExit(main())

    ``build`` is a zero-argument *factory*, not a parser instance. That matters:
    a parser instance is mutable, and reusing one across calls leaks state (a
    ``KeyValueAction``'s dict is fine, but ``append`` actions sharing a default
    list are not). Building fresh per call makes ``main`` reentrant.

    Wraps the whole body in :func:`~argparse_mastery.errors.catch_errors`, which
    is what turns :class:`UserError` into ``prog: error: ...`` plus an exit code
    while leaving genuine bugs to raise a traceback.
    """
    prog = program or "app"

    def main(argv: Sequence[str] | None = None) -> int:
        with catch_errors(prog):
            parser = build()
            args = parser.parse_args(argv)
            if configure_logging:
                setup_logging(args, program=prog)
            return run_command(parser, args)

    main.__doc__ = "Entry point. Accepts argv; returns a process exit code."
    return main


def require_at_least_one(*dests: str) -> Callable[[argparse.Namespace], None]:
    """Decorator-style helper asserting that some dests were provided.

    argparse can express "all of these" (``required=True`` on a group of options
    is impossible; mutually exclusive gives "at most one") but not "at least one
    of these". The idiomatic in-stdlib answer is a mutually exclusive group where
    every member is optional, followed by a manual check::

        group = parser.add_mutually_exclusive_group()
        group.add_argument("--from-file")
        group.add_argument("--from-url")
        ...
        if not (args.from_file or args.from_url):
            parser.error("one of --from-file or --from-url is required")

    This helper wraps the manual check so it reads as one expression, and raises
    :class:`UserError` (exit code 4, "configuration invalid") instead of calling
    ``parser.error`` -- letting the caller choose the exit code.
    """

    def check(args: argparse.Namespace) -> None:
        if not any(getattr(args, dest, None) for dest in dests):
            raise UserError(
                f"at least one of {', '.join('--' + d.replace('_', '-') for d in dests)} is required",
                ExitCode.CONFIG,
            )

    return check
