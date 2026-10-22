"""Cross-argument validation -- everything argparse cannot express.

argparse's vocabulary is four items: ``required``, mutually exclusive groups,
``nargs``, and ``choices``. Everything a real CLI needs beyond that is code.

The rules here are grouped by *where they belong*, which is the useful taxonomy:

1. **One value** -> a ``type=`` (see ``cli.positive_int``, ``cli.seconds``).
2. **Two arguments** -> here.
3. **A value plus a flag** -> here.
4. **A set of arguments** -> here.

Put a rule in the earliest category that can express it, and the error message is
better for it.

Reporting every problem at once is deliberate. A validator that calls
``parser.error`` on the first violation makes users fix one thing per run, and for
five rules that is five runs.
"""

from __future__ import annotations

import argparse

__all__ = ["ValidationError", "validate_global_args", "validate_run_args"]


class ValidationError(Exception):
    """Collects every cross-argument violation, so they can be reported together."""

    def __init__(self) -> None:
        super().__init__("validation failed")
        self.problems: list[str] = []

    def add(self, message: str) -> None:
        self.problems.append(message)

    def __bool__(self) -> bool:
        return bool(self.problems)

    def __str__(self) -> str:
        return "; ".join(self.problems)


def validate_global_args(args: argparse.Namespace) -> None:
    """Rules about the top-level options only.

    Deliberately narrow: this is called before configuration is layered in, so it
    sees only what came from argv. Rules that involve a configuration value
    belong in :func:`validate_run_args`, which runs later.
    """
    problems = ValidationError()

    # --quiet and --json conflict: machine-readable output is not "quiet", and
    # silently ignoring one of them is worse than refusing.
    if getattr(args, "json", False) and getattr(args, "quiet", False):
        problems.add("--json implies machine-readable output; drop --quiet")

    # A mutually exclusive group already handles -v with -q, so this is free.
    if getattr(args, "verbose", 0) and getattr(args, "quiet", False):
        problems.add("--verbose and --quiet are mutually exclusive")

    if problems:
        raise problems


def validate_run_args(args: argparse.Namespace) -> None:
    """Rules for ``run``: value pairs, flag pairs, and set membership.

    Called immediately after parsing and **before** any task executes, so a
    mistake never leaves half the work done.
    """
    problems = ValidationError()

    retries = getattr(args, "retries", 3)
    timeout = getattr(args, "timeout", 30.0)
    verbose = getattr(args, "verbose", 0)

    # (2) Two arguments interacting.
    if verbose and retries > 5:
        problems.add(
            f"--retries {retries} with {'-' + 'v' * min(verbose, 3)} is noisy; "
            f"use --retries 5 or fewer, or drop --verbose"
        )

    # (3) A value plus a flag.
    if getattr(args, "watch", False) and timeout > 120:
        problems.add(
            f"--watch caps --timeout at 120s (got {timeout:g}); a watched run "
            f"restarts often and long timeouts stall it"
        )

    # (4) A set of arguments: at least one task is required, and they must be known.
    tasks = list(getattr(args, "tasks", []) or [])
    if not tasks:
        problems.add("at least one TASK is required")

    # A destructive task needs an explicit acknowledgement.
    if "release" in tasks and not getattr(args, "yes", False):
        problems.add(
            "the 'release' task is destructive; pass --yes to run it "
            "non-interactively"
        )

    if problems:
        raise problems
