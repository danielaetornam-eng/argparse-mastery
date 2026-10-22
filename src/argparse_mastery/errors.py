"""Error handling, exit codes, and the parsing entry points you should use.

Three mechanisms, and when each applies
--------------------------------------
1. **``exit_on_error=True`` (default).** argparse catches ``ArgumentError`` and
   calls :meth:`ArgumentParser.error`, which prints ``usage:`` + ``error: ...``
   to stderr and raises ``SystemExit(2)``. Right for nearly all CLIs.
2. **``exit_on_error=False``.** argparse raises ``ArgumentError`` instead of
   exiting. Useful in servers, REPLs, long-running processes, and tests -- you
   want to show the error and keep going, not kill the process. Note the
   narrower scope: it affects ``_parse_known_args``. Errors raised earlier, in
   ``parse_args`` itself (``parse_args`` calling into ``parse_known_args`` and
   then erroring on extras), and ``--help``/``--version``, still call
   ``parser.error`` and exit. See :func:`parse_or_raise` below, which reconciles
   this by overriding ``error``.
3. **Custom ``parser.error`` / ``parser.exit``.** Subclass, or monkeypatch on the
   instance, to control wording and exit status. This is the escape hatch that
   handles everything, including ``-h``.

Why ``SystemExit(2)`` for usage errors is correct
-------------------------------------------------
``2`` is the POSIX convention for "the command line was wrong". Your own domain
failures should use a *different* code, or shell scripts cannot distinguish
"you typed it wrong" from "the operation failed". See :class:`ExitCode`.

The custom error subclass
-------------------------
:meth:`FriendlyArgumentParser.error` shows the pattern used throughout this
project:

* keep argparse's ``usage:`` line -- it is the single most useful part,
* add "did you mean" suggestions for both flags and subcommands,
* point at ``--help``,
* allow the exit code to be overridden per parser.
"""

from __future__ import annotations

import argparse
import contextlib
import enum
import os
import sys
from collections.abc import Iterator, Sequence
from typing import Any, NoReturn

from . import suggest as _suggest
from .formatters import known_option_strings

__all__ = [
    "ExitCode",
    "FriendlyArgumentParser",
    "ParseFailure",
    "UserError",
    "catch_errors",
    "parse_or_exit",
    "parse_or_raise",
]


class ExitCode(enum.IntEnum):
    """Exit statuses, as a closed set.

    Scripts depend on these numbers, so treat them as part of your public API:
    add new codes, never renumber existing ones.

    ============  ====  ==================================================
    Code          Num   Meaning
    ============  ====  ==================================================
    ``OK``           0  success
    ``USAGE``        2  bad command line (argparse's own convention)
    ``DATA``         3  input file malformed / unparseable
    ``CONFIG``       4  configuration invalid
    ``MISSING``      5  required input absent (file not found, no such key)
    ``PARTIAL``      6  work completed but some items failed
    ``INTERRUPTED``  130  SIGINT (128 + 2), what Python already raises
    ============  ====  ==================================================
    """

    OK = 0
    USAGE = 2
    DATA = 3
    CONFIG = 4
    MISSING = 5
    PARTIAL = 6
    INTERRUPTED = 130


class UserError(Exception):
    """An error caused by the user's input rather than by a bug.

    Carries the ``ExitCode`` your ``main`` should return, which is how a
    ``--help``-worthy message and an internal crash end up distinguishable by a
    calling script.
    """

    def __init__(self, message: str, exit_code: ExitCode = ExitCode.DATA) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class ParseFailure(Exception):
    """Raised instead of ``SystemExit`` when parsing fails.

    Wraps the original :class:`argparse.ArgumentError` so you can inspect
    ``argument_name`` and ``message`` programmatically without scraping text.
    """

    def __init__(self, error: argparse.ArgumentError, argv: Sequence[str]) -> None:
        super().__init__(str(error))
        self.error = error
        self.argv = list(argv)
        self.argument_name: str | None = (
            error.argument_name if error is not None else None
        )
        self.message: str = (error.message if error is not None else str(error)) or ""


class FriendlyArgumentParser(argparse.ArgumentParser):
    """``ArgumentParser`` with better errors, still 100% argparse-compatible.

    Everything is implemented by overriding :meth:`error` and :meth:`exit`, which
    argparse calls in every failure path. Subclassing is preferable to
    monkeypatching because subparsers inherit the behaviour automatically via
    ``parser_class`` and because ``type(self).mro`` stays honest.

    Features added here:

    * "did you mean" suggestions for mistyped **flags** and **subcommands**
      (see :mod:`argparse_mastery.suggest`)
    * a ``--help`` hint appended to every error
    * ``exit_code=`` override, so a wrapper can map usage errors to its own scheme
    * ``prefix_char`` detection so the suggestion text uses the right punctuation
    * ``error()`` raises :class:`ParseFailure` instead of exiting when
      ``soft_errors=True`` -- the escape hatch for REPLs and servers
    """

    def __init__(
        self,
        *args: Any,
        exit_code: int = ExitCode.USAGE,
        soft_errors: bool = False,
        add_help_note: bool = True,
        **kwargs: Any,
    ) -> None:
        # ``suggest_on_error`` exists only in 3.14+. Passing it unconditionally
        # would break 3.11-3.13, which this project supports.
        if "suggest_on_error" not in kwargs and sys.version_info >= (3, 14):
            kwargs["suggest_on_error"] = True
        self._exit_code = exit_code
        self._soft_errors = soft_errors
        self._add_help_note = add_help_note
        super().__init__(*args, **kwargs)

    # -- the two methods everything routes through ------------------------

    def error(self, message: str) -> NoReturn:
        """Print usage + an improved error, then exit (or raise in soft mode).

        Called by argparse for: missing required args, invalid choices, bad
        ``type=`` values, unrecognized arguments, mutually-exclusive violations,
        and anything your own code raises via ``parser.error(...)``.
        """
        if self._soft_errors:
            raise ParseFailure(argparse.ArgumentError(None, message), argv=[])

        lines = [self.format_usage().rstrip(), f"{self.prog}: error: {message}"]

        suggestions = self._suggestions_for(message)
        if suggestions:
            lines.append(suggestions)

        if self._add_help_note:
            lines.append(f"Try '{self.prog} --help' for more information.")

        self.exit(self._exit_code, "\n".join(lines) + "\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        """Write ``message`` to stderr, then ``SystemExit``.

        Overriding this (rather than calling ``sys.exit`` yourself) is what makes
        ``-h``/``--version`` respect a non-default exit code, and it gives tests
        one place to capture output.
        """
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)

    # -- suggestion plumbing ----------------------------------------------

    def _suggestions_for(self, message: str) -> str:
        """Extract candidate tokens from an argparse message and suggest for them.

        argparse's "unrecognized arguments" message contains the raw tokens, which
        makes it the one message we can meaningfully improve. Every other
        message (invalid choice, invalid int value) is already attached to a
        specific option and argparse 3.14's ``suggest_on_error`` already covers
        choices, so we leave those alone.
        """
        marker = "unrecognized arguments:"
        if marker not in message:
            return ""
        tail = message.split(marker, 1)[1].strip()
        unknown = tail.split()
        if not unknown:
            return ""

        return _suggest.unrecognized_suggestions(
            unknown,
            known_option_strings(self),
            known_commands=_subcommand_names(self),
        )


def _subcommand_names(parser: argparse.ArgumentParser) -> list[str]:
    """Return the subcommand names and aliases a parser offers, if any.

    Aliases are derived from ``_name_parser_map`` insertion order rather than from
    a parser attribute, because ``add_parser(aliases=...)`` never records them on
    the parser object -- see :func:`argparse_mastery.completion.subcommand_names`.
    """
    from .completion import subcommand_names

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return [name for name, _is_alias in subcommand_names(action)]
    return []


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def parse_or_raise(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    """Parse ``argv``, raising :class:`ParseFailure` instead of exiting.

    The clean way to embed a parser in something that must not die: a web
    service, a daemon, a Jupyter cell, an editor language server::

        try:
            args = parse_or_raise(parser)
        except ParseFailure as failure:
            return 400, failure.message

    Implemented by temporarily forcing ``exit_on_error=False`` *and* overriding
    ``error`` on the instance, because ``parse_args`` has a final
    "unrecognized arguments" check that calls ``error()`` regardless of
    ``exit_on_error``.
    """
    original_exit_on_error = parser.exit_on_error
    original_error = parser.error
    parser.exit_on_error = False
    parser.error = _raise_parse_failure  # type: ignore[method-assign]
    try:
        return parser.parse_args(argv)
    except argparse.ArgumentError as error:
        raise ParseFailure(error, argv or []) from None
    finally:
        parser.exit_on_error = original_exit_on_error
        parser.error = original_error  # type: ignore[method-assign]


def _raise_parse_failure(message: str) -> NoReturn:
    raise argparse.ArgumentError(None, message)


def parse_or_exit(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    """Parse ``argv`` on a parser that already raises :class:`ParseFailure`.

    Convenience wrapper that raises a clean ``SystemExit(code)`` with a
    ``prog: error: ...`` line -- suitable for ``if __name__ == "__main__"``.
    """
    try:
        return parse_or_raise(parser, argv)
    except ParseFailure as failure:
        sys.stderr.write(f"{parser.prog}: error: {failure.message}\n")
        sys.stderr.write(parser.format_usage())
        raise SystemExit(ExitCode.USAGE) from None


@contextlib.contextmanager
def catch_errors(
    prog: str = "program",
    *,
    exit_code: ExitCode = ExitCode.DATA,
) -> Iterator[None]:
    """Turn :class:`UserError` into a clean message + exit code.

    Keeps ``main()`` free of try/except plumbing and guarantees that internal
    errors still produce a traceback while user errors do not::

        def main(argv=None) -> int:
            with catch_errors(__package__ or "app"):
                args = build_parser().parse_args(argv)
                return run(args)
    """
    try:
        yield
    except UserError as exc:
        sys.stderr.write(f"{prog}: error: {exc.message}\n")
        raise SystemExit(exc.exit_code) from None
    except KeyboardInterrupt:
        sys.stderr.write(f"\n{prog}: interrupted\n")
        raise SystemExit(ExitCode.INTERRUPTED) from None
    except BrokenPipeError:
        # `prog | head` closes the pipe early. Exit quietly and successfully:
        # the standard trick so `set -o pipefail` scripts do not see noise.
        try:
            sys.stdout = open(os.devnull, "w")  # noqa: SIM115
        except OSError:  # pragma: no cover - devnull always exists
            pass
        else:
            sys.stdout.flush()
        raise SystemExit(ExitCode.OK) from None
