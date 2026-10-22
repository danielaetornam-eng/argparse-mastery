"""Exit codes -- a public API.

This is a separate module for one reason: ``cli.py``, ``commands/*`` and
``app.py`` all need it, and none of them should import each other to get it.
A 20-line enum in its own module is cheaper than any alternative.

The numbers are frozen. Scripts branch on them; changing one is a breaking
change. See lesson 32 for the reasoning and ``docs/09_errors_exits_testing.md``.
"""

from __future__ import annotations

import enum

__all__ = ["ExitCode", "describe_exit_codes"]


class ExitCode(enum.IntEnum):
    """Exit statuses. Documented in ``--help`` and in the README."""

    OK = 0
    GENERIC = 1        # a bug; gets a traceback
    USAGE = 2          # bad command line (argparse's own code)
    DATA = 3           # input malformed or unparseable
    CONFIG = 4         # configuration invalid
    MISSING = 5        # required input absent
    PARTIAL = 6        # some items succeeded, some failed
    INTERRUPTED = 130  # 128 + SIGINT


def describe_exit_codes() -> str:
    """Return the exit-code table as help text for the parser epilog.

    Generating it from the enum rather than hand-writing it means the docs cannot
    drift from the code -- the same argument as keeping the task catalogue in one
    dict (see ``cli.py``).
    """
    rows = {
        ExitCode.OK: "success",
        ExitCode.GENERIC: "unexpected internal error",
        ExitCode.USAGE: "usage error (bad command line)",
        ExitCode.DATA: "bad input data",
        ExitCode.CONFIG: "invalid configuration",
        ExitCode.MISSING: "required input missing",
        ExitCode.PARTIAL: "partial success (some items failed)",
        ExitCode.INTERRUPTED: "interrupted",
    }
    width = max(len(name) for name in ExitCode.__members__)
    lines = ["exit codes:"]
    lines += [f"  {code.value:>4}  {name:<{width}}  {rows[code]}" for name, code in ExitCode.__members__.items()]
    return "\n".join(lines)
