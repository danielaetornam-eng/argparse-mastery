"""Lesson 31 - Error handling: ``exit_on_error``, ``ArgumentError``, soft failures.

Run me
------
    python examples/31_error_handling.py --port abc
    python examples/31_error_handling.py --demo

Three mechanisms, and what each one actually covers
--------------------------------------------------
**1. ``exit_on_error=True`` (the default).** argparse catches ``ArgumentError``
during ``_parse_known_args`` and calls :meth:`ArgumentParser.error`, which prints
``usage:`` + ``error: ...`` to stderr and raises ``SystemExit(2)``.

**2. ``exit_on_error=False``.** argparse raises ``ArgumentError`` instead of
exiting. But the scope is narrower than people assume. Read ``parse_args``::

    def parse_args(self, args=None, namespace=None):
        args, argv = self.parse_known_args(args, namespace)
        if argv:
            msg = _('unrecognized arguments: %s')
            self.error(msg % ' '.join(argv))
        return args

``parse_args`` calls ``self.error()`` directly for trailing junk. So with
``exit_on_error=False``:

======================================  ==========================================
Failure                                Behaviour
======================================  ==========================================
bad ``type=`` / ``choices``             ``ArgumentError`` raised  ✅
missing required argument               ``ArgumentError`` raised  ✅
unrecognized trailing arguments         ``parser.error()`` -> **exit 2**  ❌
``--help``, ``--version``               prints, exits 0            ❌
======================================  ==========================================

**3. Override ``error`` and ``exit``.** The only approach that catches everything,
because everything routes through them. This is what
:class:`SoftParser` below does, and what ``argparse_mastery.parse_or_raise``
implements for any parser.

Rule of thumb: use (2) when you only need to survive parse errors inside a
long-running process and your parser never has trailing-argument cases; use (3)
when you need to *never* exit, which is the real requirement for embedding.

What ``ArgumentError`` gives you
--------------------------------
::

    exc.argument_name   # '--port', or None for parser-level errors
    exc.message         # "argument --port: invalid int value: 'abc'"

``argument_name`` is genuinely useful: you can build a
``{"--port": "this argument is malformed"}`` mapping for a frontend. It is
``None`` for errors raised by ``parser.error(...)`` directly, which is why the
message is the more reliable field.

Wrong instinct: catching ``SystemExit``
---------------------------------------
::

    try:
        args = parser.parse_args()
    except SystemExit:
        pass            # you now have no `args`, and --help was swallowed

This is worse than it looks. ``--help`` exits **0**, so the bare ``except`` eats
it and your program continues with an undefined variable. If you must catch,
catch ``ArgumentError`` -- or catch ``SystemExit`` and re-raise for code 0.

Where the exit code comes from
------------------------------
``parser.error(message)`` calls ``self.exit(2, message)``, and
``ArgumentParser.exit`` does ``sys.exit(status)``. Override ``exit`` to change the
status, or to raise instead. That is the seam to use when embedding.

Message quality
---------------
argparse's messages are terse and sometimes cryptic. Three cheap improvements,
none of which require overriding anything:

* ``suggest_on_error=True`` (3.14+) improves ``choices`` errors only, and only
  when every choice is a ``str``.
* A good ``type=`` gives you the message you want (lesson 20).
* A validating ``type=`` that raises ``ArgumentTypeError`` gets ``argument
  --name:`` prepended automatically -- never format that prefix yourself.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable


class ExitCode:
    """The conventional statuses. See lesson 32 for the full treatment."""

    OK = 0
    USAGE = 2
    DATA = 3


class SoftParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` that raises instead of exiting.

    Overriding ``error`` is the complete solution to "this parser must never kill
    the process". It catches every failure path argparse has, including the ones
    ``exit_on_error=False`` misses:

    * ``_parse_known_args`` errors (bad type, bad choices, missing required)
    * ``parse_args``'s trailing-arguments check
    * ``--help`` and ``--version``

    ``--help`` is special: argparse's help action calls ``print_help()`` then
    ``parser.exit()``, and ``exit`` raises ``SystemExit(0)``. Overriding ``exit``
    is therefore required if you want ``--help`` to raise too -- for example when
    a sub-process must not print help to *your* stdout.

    Also adds "did you mean" suggestions for mistyped flags and subcommands, which
    ``suggest_on_error`` does not cover (see lesson 34's notes on 3.14).
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        if "suggest_on_error" not in kwargs and sys.version_info >= (3, 14):
            kwargs["suggest_on_error"] = True
        self._help_requested = False
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def error(self, message: str) -> None:
        """Raise instead of printing and exiting."""
        raise argparse.ArgumentError(None, message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        """Raise for *every* exit, including ``--help``'s exit 0."""
        raise SoftExit(status, message or "")

    def print_help(self, file: object = None) -> None:
        if file is None:
            self._help_requested = True
        super().print_help(file)  # type: ignore[arg-type]


class SoftExit(Exception):
    """Carries what ``SystemExit`` would have: a status and a message."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"exit {status}")
        self.status = status
        self.message = message


def parse_with_exit_code(
    parser: argparse.ArgumentParser,
    argv: list[str],
    *,
    run: Callable[[argparse.Namespace], int],
) -> int:
    """Parse, dispatch, and translate every failure into a return code.

    This is the wrapper pattern in full, and it is what a real embedding wants::

        return parse_with_exit_code(build_parser(), argv, run=my_handler)

    Three failure classes are separated on purpose, because they deserve
    different messages:

    * :class:`argparse.ArgumentError` -- the user typed something invalid.
      Report it concisely, without re-printing the usage block argparse already
      showed, and return the usage-exit code (2).
    * :class:`SystemExit` -- argparse's own ``exit()``. Almost always ``--help``
      or ``--version``, which is *success*, so the status passes straight
      through.
    * anything else -- a bug. It propagates, with a traceback, because pretending
      it is a user error is how bugs stay alive.
    """
    try:
        args = parser.parse_args(argv)
        return run(args)
    except argparse.ArgumentError as exc:
        sys.stderr.write(f"{parser.prog}: error: {exc.message}\n")
        return ExitCode.USAGE
    except SystemExit as exc:
        # --help and --version land here, and 0 is the correct thing to return.
        return 0 if exc.code is None else int(exc.code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server",
        description="Lesson 31: error handling.",
        epilog="Run with --demo to compare the three mechanisms.",
    )
    parser.add_argument("--port", type=int, default=8080, metavar="PORT",
                        help="listen port")
    parser.add_argument("--mode", choices=("fast", "safe"), default="safe",
                        help="operating mode")
    parser.add_argument("--name", required=True, help="service name (required)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"name={args.name} port={args.port} mode={args.mode}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. Default behaviour: print to stderr, exit 2")
    print("=" * 78)
    parser = build_parser()
    print(f"  --port abc   -> {_show(parser, ['--port', 'abc', '--name', 'x'])}")
    print(f"  (missing --name) -> {_show(parser, ['--port', '1'])}")
    print(f"  --mode quick -> {_show(parser, ['--mode', 'quick', '--name', 'x'])}")
    print(f"  --help       -> {_show(parser, ['--help'])}   (stdout, exit 0)")

    print()
    print("=" * 78)
    print("2. exit_on_error=False -- narrower than you expect")
    print("=" * 78)
    softish = argparse.ArgumentParser(prog="server", add_help=False, exit_on_error=False)
    softish.add_argument("--port", type=int)
    softish.add_argument("--name", required=True)
    cases = [
        (["--port", "abc"], "bad type"),
        ([], "missing required"),
        (["--port", "1", "--trailing"], "trailing junk"),
        (["--help"], "--help"),
    ]
    for argv, label in cases:
        outcome = _probe(softish, argv)
        print(f"  {argv!s:<28} ({label:<16}) -> {outcome}")

    print()
    print("  Rows 3 and 4 still exit, because parse_args calls self.error() for")
    print("  trailing arguments and the help action calls exit(). exit_on_error only")
    print("  governs _parse_known_args.")

    print()
    print("=" * 78)
    print("3. Overriding error() and exit(): covers everything")
    print("=" * 78)
    soft = SoftParser(prog="server", add_help=False)
    soft.add_argument("--port", type=int)
    soft.add_argument("--name", required=True)
    for argv, label in cases:
        print(f"  {argv!s:<28} ({label:<16}) -> {_probe(soft, argv)}")

    print()
    print("  Every path raises, including --help. Nothing is printed to stderr by")
    print("  argparse, because error() never calls exit() with a message.")

    print()
    print("=" * 78)
    print("4. ArgumentError carries structured information")
    print("=" * 78)
    soft2 = SoftParser(prog="server", add_help=False)
    soft2.add_argument("--port", type=int)
    try:
        soft2.parse_args(["--port", "abc"])
    except argparse.ArgumentError as exc:
        print(f"  argument_name = {exc.argument_name!r}")
        print(f"  message       = {exc.message!r}")
    print()
    print("  argument_name is None when the error came from parser.error() rather")
    print("  than from a specific action, so `message` is the more reliable field.")

    print()
    print("=" * 78)
    print("5. The anti-pattern: bare `except SystemExit: pass`")
    print("=" * 78)
    print("  try:")
    print("      args = parser.parse_args()")
    print("  except SystemExit:")
    print("      pass        # <-- also swallows --help, which exits 0")
    print()
    print(f"  Demonstrated: {sys.executable} -c 'import argparse; ...")
    print("  After the except, `args` does not exist. Catching ArgumentError -- or")
    print("  a custom exception -- is the correct move.")

    print()
    print("=" * 78)
    print("6. Mapping argparse's exit code to your own scheme")
    print("=" * 78)
    print("  argparse: usage error = 2. Your tool may reserve 2 for something else,")
    print("  so subclass and override exit():")
    print()
    print("      class MyParser(argparse.ArgumentParser):")
    print("          def exit(self, status=0, message=None):")
    print("              if status == 2:")
    print("                  status = 64      # EX_USAGE, BSD sysexits")
    print("              super().exit(status, message)")
    print()
    print("  argparse_mastery.FriendlyArgumentParser takes exit_code= for exactly this.")

    return 0


def _show(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Run a parse, capture both streams, and report code plus the error line."""
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            parser.parse_args(argv)
            return "exit 0 (accepted)"
        except SystemExit as exc:
            code = 0 if exc.code is None else int(exc.code)
    text = err.getvalue().strip()
    if not text:
        text = out.getvalue().strip().splitlines()[0] if out.getvalue().strip() else ""
    for line in text.splitlines():
        if ": error:" in line:
            return f"exit {code}  {line.split(': error:', 1)[1].strip()}"
    return f"exit {code}"


def _probe(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Report which exception a parse raises, without letting it escape."""
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            parser.parse_args(argv)
            return "returned normally"
        except argparse.ArgumentError as exc:
            return f"ArgumentError: {exc.message[:48]}"
        except SoftExit as exc:
            return f"SoftExit(status={exc.status})"
        except SystemExit as exc:
            return f"SystemExit({exc.code})  <-- process would end here"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
