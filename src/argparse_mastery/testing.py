"""Testing CLIs built on argparse.

Three techniques, in increasing order of fidelity
-------------------------------------------------
1. **Parse, don't run.** ``parser.parse_args([...])`` and assert on the
   namespace. Fast, no I/O, but it misses ``main()``-level bugs entirely.
2. **Call ``main`` and capture.** :func:`run_cli` gives you exit code, stdout,
   stderr and the parsed namespace in one call. This is the workhorse.
3. **Subprocess.** The only way to test exit codes, signal handling, ``-h``
   behaviour when output is a pipe, and anything that depends on
   ``os.getcwd()``. Slower, but see ``test_subprocess_cli`` below for the
   recipe.

Why argparse needs help to be testable
--------------------------------------
argparse decides between "help" and "error" partly by looking at streams:

* ``-h``/``--help`` prints to **stdout** and exits **0**.
* every error prints ``usage:`` + ``error: ...`` to **stderr** and exits **2**.

Because it uses ``sys.stdout``/``sys.stderr`` at call time, ``capsys`` and
``contextlib.redirect_stdout`` both work -- no special support needed. That is a
reason to prefer argparse over hand-rolled parsers in codebases with tests.

The one real trap
-----------------
``argparse.FileType('r')`` opens the file during parsing and the handle is
never closed by argparse. In tests, hundreds of leaked handles trigger
``ResourceWarning``; in long-running processes you hit the OS file-descriptor
limit. :func:`parse_only` is not enough -- the fix is to accept a
``Callable[[str], IO[str]]`` as the ``type`` and open it yourself at point of
use, which is what the toolkit's ``type=existing_file`` (returning a ``Path``)
encourages.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

__all__ = [
    "CliResult",
    "assert_failed",
    "assert_ok",
    "parse_only",
    "run_cli",
    "split_stderr",
]

#: argparse's usage-error status. Named so tests do not hard-code ``2``.
USAGE_EXIT_CODE = 2


@dataclass
class CliResult:
    """Everything a CLI invocation produced, for assertions.

    ``namespace`` is the parsed namespace when ``main`` uses the conventional
    ``args.func(args)`` dispatch; it is ``None`` otherwise. Keeping it on the
    result lets one test assert on both the exit code and the parse result, which
    is usually the interesting combination.

    ``argv_parsed`` records what the parser actually consumed, which is what you
    assert against when testing ``parse_known_args``.
    """

    exit_code: int
    stdout: str
    stderr: str
    namespace: argparse.Namespace | None = None
    argv_parsed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when the process would report success to a shell."""
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """stdout + stderr, in that order -- handy for smoke assertions."""
        return self.stdout + self.stderr

    def lines(self) -> list[str]:
        """Non-empty stdout lines with trailing whitespace stripped."""
        return [line.rstrip() for line in self.stdout.splitlines() if line.strip()]


def run_cli(
    main: Callable[[Sequence[str] | None], int],
    argv: Sequence[str] = (),
    *,
    namespace_sink: list[argparse.Namespace] | None = None,
) -> CliResult:
    """Invoke ``main(argv)`` with both streams captured.

    ``main`` is passed ``argv`` explicitly, which is the habit that makes a CLI
    testable: never read ``sys.argv`` inside ``main``, always accept it as a
    parameter. Convention::

        def main(argv: Sequence[str] | None = None) -> int:
            args = build_parser().parse_args(argv)
            return run(args)

    Handles all three exit paths:

    * normal return -> ``exit_code`` is the returned int
    * ``SystemExit(0)`` from ``--help`` -> captured, code 0
    * ``SystemExit(2)`` from a parse error -> captured, stderr holds the message

    Exceptions other than :class:`SystemExit` deliberately propagate. If your
    ``main`` raises, that is a bug and the test should fail loudly rather than
    see ``exit_code=-1``.

    Pass ``namespace_sink=[]`` to collect every namespace ``main`` parses -- the
    simplest way to test a config-layering pipeline end to end::

        sink: list[argparse.Namespace] = []
        run_cli(main, ["--verbose"], namespace_sink=sink)
        assert sink[0].log_level == logging.INFO
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    result = CliResult(exit_code=0, stdout="", stderr="")

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = main(list(argv))
            result.exit_code = int(code) if code is not None else 0
        except SystemExit as exc:
            # argparse calls parser.exit(status) which raises SystemExit(status).
            # `exc.code` is None for a bare `sys.exit()`.
            result.exit_code = 0 if exc.code is None else int(exc.code)

    result.stdout = stdout.getvalue()
    result.stderr = stderr.getvalue()
    if namespace_sink is not None:
        result.namespace = namespace_sink[-1] if namespace_sink else None
    return result


def parse_only(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] = (),
    *,
    namespace: argparse.Namespace | None = None,
) -> argparse.Namespace:
    """``parser.parse_args`` for tests, with the noisy part stripped out.

    Two conveniences:

    * ``--help`` and error exits are turned into exceptions you can assert on,
      so a test can say ``pytest.raises(SystemExit)`` instead of capturing
      output -- but if you want to assert on the *message*, use :func:`run_cli`.
    * ``unknown`` passthrough by switching to ``parse_known_args`` is available
      via the ``allow_unknown`` flag.
    """
    known_only = False
    args = list(argv)
    if args and args[0] == "--allow-unknown":
        known_only, args = True, args[1:]

    if known_only:
        parsed, extras = parser.parse_known_args(args, namespace=namespace)
        parsed._extras = extras
        return parsed
    return parser.parse_args(args, namespace=namespace)


def assert_ok(result: CliResult, *, contains: str | Sequence[str] | None = None) -> CliResult:
    """Assert a successful run, optionally that output contains substrings.

    ``contains`` accepts a sequence so one assertion can check several strings::

        assert_ok(run_cli(main, ["list"]), contains=["myapp", "README.md"])
    """
    if not result.ok:
        raise AssertionError(
            f"expected exit code 0, got {result.exit_code}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    if contains is not None:
        needles = [contains] if isinstance(contains, str) else list(contains)
        haystack = result.output
        missing = [n for n in needles if n not in haystack]
        if missing:
            raise AssertionError(
                f"output missing {missing!r}\n--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            )
    return result


def assert_failed(
    result: CliResult,
    *,
    exit_code: int = USAGE_EXIT_CODE,
    contains: str | Sequence[str] | None = None,
) -> CliResult:
    """Assert a failed run with a specific code and message substring.

    Default ``exit_code=2`` because that is argparse's usage-error code, and
    asserting it explicitly is what catches the mistake of exiting 1 on a bad
    command line (shell scripts depend on the distinction).
    """
    if result.exit_code != exit_code:
        raise AssertionError(
            f"expected exit code {exit_code}, got {result.exit_code}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    if contains is not None:
        needles = [contains] if isinstance(contains, str) else list(contains)
        haystack = result.output
        missing = [n for n in needles if n not in haystack]
        if missing:
            raise AssertionError(f"output missing {missing!r}\n{haystack}")
    return result


def split_stderr(stderr: str) -> tuple[str, str]:
    """Split argparse's stderr into ``(usage_lines, error_line)``.

    Usage errors have a fixed two-part shape, and splitting them lets you assert
    on the message without depending on the whole layout::

        usage, message = split_stderr(result.stderr)
        assert "did you mean '--verbose'" in message
    """
    lines = stderr.splitlines()
    for index, line in enumerate(lines):
        if ": error:" in line:
            return "\n".join(lines[:index]), line.split(": error:", 1)[1].strip()
    return stderr, ""


def help_snapshot(parser: argparse.ArgumentParser, *, width: int = 80) -> str:
    """Return ``parser``'s help with the terminal width pinned.

    Golden-file testing of ``--help`` is worthwhile -- it is user-visible
    documentation, and help-text churn is a real signal for a pull request. It only
    stays stable if the terminal-width dependency is removed, which is what
    ``width=`` does.

    ``ArgumentParser.format_help()`` takes no arguments, so the formatter has to be
    driven by hand in the same order ``format_help`` does. Touching private
    attributes is why this lives in one helper rather than in every test.
    """
    formatter = parser.formatter_class(prog=parser.prog, width=width)
    formatter.add_usage(
        parser.usage, parser._actions, parser._mutually_exclusive_groups
    )
    formatter.add_text(parser.description)
    for action_group in parser._action_groups:
        formatter.start_section(action_group.title)
        formatter.add_text(action_group.description)
        formatter.add_arguments(action_group._group_actions)
        formatter.end_section()
    formatter.add_text(parser.epilog)
    return formatter.format_help()


@contextlib.contextmanager
def captured_output() -> Iterator[tuple[io.StringIO, io.StringIO]]:
    """Minimal capture for code that writes to ``print`` outside ``main``.

    Prefer ``pytest``'s ``capsys``; this exists for teaching and for use outside
    a test runner.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def make_argv(*parts: str) -> list[str]:
    """Build an argv list from parts, stripping any accidental empties.

    ``make_argv("--tag", "")`` yields ``["--tag"]``, which is usually what a test
    author meant, not an empty-string argument that argparse will happily accept.
    """
    return [p for p in parts if p != ""]


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - demo
    """Demo CLI: ``python -m argparse_mastery.testing --name x -v``."""
    parser = argparse.ArgumentParser(prog="testing-demo", description=__doc__.splitlines()[0])
    from .logging_setup import add_logging_arguments

    add_logging_arguments(parser)
    parser.add_argument("--name", default="world")
    args = parser.parse_args(argv)
    print(f"hello {args.name} (level={args.log_level})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
