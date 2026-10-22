"""Lesson 32 - Exit codes as an API.

Run me
------
    python examples/32_exit_codes.py --demo
    python examples/32_exit_codes.py; echo "exit was $?"

The rule
--------
**Your exit code is part of your public interface.** Scripts branch on it, CI
systems gate on it, and operators build dashboards from it. Changing a code later
is a breaking change of exactly the same kind as renaming a flag.

What the conventions are
------------------------
argparse uses **2** for a usage error, and that comes from POSIX: a command was
invoked incorrectly. It is not 1. Everything else is yours to choose.

=========================  ====  ==========================================
Code                       Num   Use it when
=========================  ====  ==========================================
success                    0     the work completed
generic failure            1     something went wrong, no better code fits
usage error                2     bad command line (argparse's choice)
bad input data             3     a file was malformed, a record failed
configuration invalid      4     a config file or flag combination is wrong
required input missing     5     file not found, key absent
partial success             6     some items succeeded, some failed
SIGINT                     130   128 + 2, what Python already raises
=========================  ====  ==========================================

``128 + N`` for signal N is a shell convention, so 130 for Ctrl-C is right and
free. Everything in the 60-70 range is BSD ``sysexits.h``: ``EX_USAGE`` is 64,
``EX_DATAERR`` is 65, and so on. Using that range is a legitimate choice, but pick
one scheme and stay in it. Mixing conventions is worse than either.

Why not just use 0 and 1
-----------------------
Because then a script cannot distinguish "you typed it wrong" from "the
operation failed", and cannot retry the second without re-running the first.
Concretely::

    if ! myapp sync; then
        if [ $? -eq 2 ]; then
            echo "check the command line"      # a human must fix it
        else
            echo "sync failed; retrying"       # automation can retry
            myapp sync
        fi
    fi

The three-step ``main``
-----------------------
::

    def main(argv: Sequence[str] | None = None) -> int:
        parser = build_parser()
        args = parser.parse_args(argv)      # -> SystemExit(2) on usage errors
        return run(args)                    # -> your codes

``main`` **returns** a code; it does not call ``sys.exit``. The entry point raises
it::

    if __name__ == "__main__":
        raise SystemExit(main())

That separation is what makes the CLI testable: ``run_cli(main, argv)`` captures
the return value without a subprocess (see ``argparse_mastery.testing``).

``--help`` and ``--version`` exit 0
-----------------------------------
They are *successful requests*. Verified: the help action calls
``parser.exit(0, ...)`` and the version action does the same. Never map them to a
non-zero code -- ``mytool --help >/dev/null`` in a ``set -e`` script should not
fail the build.

Partial success
---------------
A batch operation where 3 of 10 items failed should exit 6, not 0 and not 1. It
lets a caller decide: retry the failures, or treat the run as a failure, without
parsing your output. And it must be paired with a machine-readable summary --
``--json`` output listing which items failed -- or the code is unactionable.

Adding new codes safely
-----------------------
Never renumber. Never reuse a retired code for something else. Document them in
``--help`` (the epilog is the right place) and in the README, because users
cannot run ``myapp`` to find out.
"""

from __future__ import annotations

import argparse
import enum
import subprocess
import sys
from collections.abc import Sequence


class ExitCode(enum.IntEnum):
    """Exit statuses as a closed set. This is a public API; do not renumber."""

    OK = 0
    GENERIC = 1
    USAGE = 2          # argparse's own code for a bad command line
    DATA = 3
    CONFIG = 4
    MISSING = 5
    PARTIAL = 6
    INTERRUPTED = 130  # 128 + SIGINT


class UserError(Exception):
    """An error caused by the user's input, carrying the code to exit with."""

    def __init__(self, message: str, code: ExitCode = ExitCode.DATA) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class AppError(Exception):
    """A bug in the program. Gets a traceback and code 1."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="batch",
        description="Lesson 32: exit codes.",
        epilog=(
            "exit codes:\n"
            "  0   success\n"
            "  1   unexpected internal error\n"
            "  2   usage error (bad command line)\n"
            "  3   bad input data\n"
            "  4   invalid configuration\n"
            "  5   required input missing\n"
            "  6   partial success (some items failed)\n"
            "  130 interrupted\n"
        ),
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="files to process")
    parser.add_argument("--fail-on", metavar="NAME", default=None,
                        help="make the named item fail (for demonstration)")
    parser.add_argument("--limit", type=int, default=10, metavar="N",
                        help="how many items to process")
    return parser


def process(name: str) -> None:
    """Raise :class:`UserError` for input problems; let bugs escape as-is."""
    if name == "missing.txt":
        raise UserError(f"{name}: no such file", ExitCode.MISSING)
    if name == "corrupt.bin":
        raise UserError(f"{name}: not a recognised format", ExitCode.DATA)
    if name == "bad.conf":
        raise UserError(f"{name}: section [x] has no key", ExitCode.CONFIG)


def run(args: argparse.Namespace) -> int:
    """Process each file, tallying outcomes into one exit code.

    Shows the partial-success design: keep going after an item fails, remember
    which failed, and let the caller decide what the run means.
    """
    failures: list[tuple[str, str]] = []
    for name in args.files[: args.limit]:
        if args.fail_on and name == args.fail_on:
            failures.append((name, "requested failure"))
            continue
        try:
            process(name)
        except UserError as exc:
            failures.append((name, exc.message))
        except AppError:
            # A bug. Do NOT swallow it into the failure tally: re-raise so it
            # gets a traceback and exits 1. Hiding bugs behind code 6 makes
            # them unfindable.
            raise
        except OSError as exc:
            failures.append((name, exc.strerror or str(exc)))

    for name, reason in failures:
        print(f"error: {name}: {reason}", file=sys.stderr)

    if not failures:
        return ExitCode.OK
    if len(failures) == len(args.files[: args.limit]):
        # Everything failed. That is a whole-run failure, not a partial one, so
        # report the most specific code available rather than 6.
        return ExitCode.DATA
    print(f"note: {len(failures)} of {len(args.files[: args.limit])} failed",
          file=sys.stderr)
    return ExitCode.PARTIAL


def main(argv: Sequence[str] | None = None) -> int:
    """Return an exit code; never call ``sys.exit`` here.

    This is the shape that makes the CLI testable in-process: a test can call
    ``main([...])`` and assert on the returned integer, with no subprocess and no
    SystemExit handling. See ``argparse_mastery.testing.run_cli``.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # --help and --version land here. Their status passes through unchanged,
        # and it is 0, which is correct: asking for help is a satisfied request.
        return 0 if exc.code is None else int(exc.code)

    try:
        return run(args)
    except UserError as exc:
        sys.stderr.write(f"{parser.prog}: error: {exc.message}\n")
        return int(exc.code)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return int(ExitCode.INTERRUPTED)
    except BrokenPipeError:
        # `myapp ... | head` closes the pipe early. Exit quietly and successfully
        # so `set -o pipefail` scripts do not see spurious noise.
        return int(ExitCode.OK)
    except AppError:
        # A genuine bug. Let it propagate: the traceback is the deliverable.
        raise


def demo() -> int:
    print("=" * 78)
    print("1. argparse already reserves 2 for usage errors")
    print("=" * 78)
    parser = build_parser()
    for argv in (["--limit", "x", "a.txt"], ["a.txt", "--nope"]):
        print(f"  {' '.join(argv):<28} -> {_code(parser, argv)}")
    print()
    print("  Do not use 2 for your own errors: a script needs to tell 'you typed it")
    print("  wrong' from 'the operation failed'.")

    print()
    print("=" * 78)
    print("2. --help and --version exit 0")
    print("=" * 78)
    for argv in (["--help"], ["a.txt", "--help"]):
        print(f"  {argv!s:<22} -> {_code(parser, argv)}")
    print()
    print("  `myapp --help > /dev/null` inside `set -e` must not fail a build.")

    print()
    print("=" * 78)
    print("3. The full range, and what each code means")
    print("=" * 78)
    for member in ExitCode:
        note = {
            ExitCode.OK: "success",
            ExitCode.GENERIC: "unexpected internal error",
            ExitCode.USAGE: "bad command line (argparse)",
            ExitCode.DATA: "input file malformed",
            ExitCode.CONFIG: "configuration invalid",
            ExitCode.MISSING: "required input absent",
            ExitCode.PARTIAL: "some items failed",
            ExitCode.INTERRUPTED: "128 + SIGINT",
        }[member]
        print(f"  {member.value:>4}  {member.name:<13} {note}")

    print()
    print("=" * 78)
    print("4. Whole-run failure vs partial success")
    print("=" * 78)
    scenarios = [
        (["ok1.txt", "ok2.txt"], ExitCode.OK, "everything fine"),
        (["ok1.txt", "corrupt.bin", "ok2.txt"], ExitCode.PARTIAL, "one of three failed"),
        (["corrupt.bin", "missing.txt"], ExitCode.DATA, "everything failed"),
    ]
    for argv, expected, label in scenarios:
        got = main(argv)
        print(f"  {argv!s:<46} -> {got:<3} ({label})")
    print()
    print("  Everything-failed reports DATA, not PARTIAL: 'some items failed' is")
    print("  false, and a caller retrying a totally failed batch needs different")
    print("  handling from one retrying a partial batch.")

    print()
    print("=" * 78)
    print("5. A bug is not a partial failure")
    print("=" * 78)
    parser = build_parser()
    args = parser.parse_args(["x.txt"])
    print("  If process() raised AppError (a bug), run() re-raises it.")
    print("  It gets a traceback and exit 1 -- not lumped into the failure tally,")
    print("  because code 6 says 'some things failed', not 'we are broken'.")

    print()
    print("=" * 78)
    print("6. Verifying the real process exit code in a subprocess")
    print("=" * 78)
    print("  main() returns a code; the entry point raises it. A test that needs the")
    print("  real process status uses subprocess:")
    for argv in (["--help"], ["--limit", "x", "a.txt"], ["corrupt.bin"]):
        print(f"    {argv!s:<26} -> {_subprocess_code(argv)}")

    print()
    print("=" * 78)
    print("7. Pipefail and BrokenPipeError")
    print("=" * 78)
    print("  main() catches BrokenPipeError and returns OK, because:")
    print("      myapp --help | head -5")
    print("  closes the pipe early on purpose. Reporting failure there is noise.")
    print("  argparse_mastery.errors.catch_errors does the same thing.")

    return 0


def _code(parser: argparse.ArgumentParser, argv: list[str]) -> int:
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            parser.parse_args(argv)
            return 0
        except SystemExit as exc:
            return 0 if exc.code is None else int(exc.code)


def _subprocess_code(argv: list[str]) -> int:
    """Run this file as a real process and read its exit status."""
    completed = subprocess.run(
        [sys.executable, str(_this_file()), *argv],
        capture_output=True,
        text=True,
    )
    return completed.returncode


def _this_file() -> str:
    import pathlib

    return str(pathlib.Path(__file__).resolve())


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
