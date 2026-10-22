"""``taskrunner list`` -- enumerate the tasks."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..cli import TASKS
from ..errors import TaskRunnerError
from ..exit_codes import ExitCode

__all__ = ["cmd_list"]


def collect(filter_pattern: str | None) -> list[dict[str, Any]]:
    """Return matching tasks as records, sorted by name.

    Separated from rendering so the JSON and text paths cannot diverge -- a
    recurring CLI bug where ``--json`` and the human output list different things.
    """
    rows = [
        {"name": name, "description": desc}
        for name, desc in sorted(TASKS.items())
        if not filter_pattern or filter_pattern in name
    ]
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    """List available tasks. Honours ``--json``, ``--quiet`` and ``--sort``."""
    rows = collect(getattr(args, "filter", None))

    if not rows:
        # An empty list is not an error unless a filter was given and matched
        # nothing -- in which case the user has a typo and should be told.
        if getattr(args, "filter", None):
            raise TaskRunnerError(
                f"no task matches {args.filter!r}", ExitCode.MISSING
            )
        rows = []

    if getattr(args, "sort", "name") == "recent":
        rows.reverse()

    if getattr(args, "json", False) or getattr(args, "format", "text") == "json":
        json.dump({"tasks": rows}, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return ExitCode.OK

    if not rows:
        # Not --quiet, and nothing to say: stay silent rather than print a
        # pointless "no tasks".
        return ExitCode.OK

    width = max(len(row["name"]) for row in rows)
    if not getattr(args, "quiet", False):
        for row in rows:
            print(f"  {row['name']:<{width}}  {row['description']}")
        print(f"\n{len(rows)} task(s). Run one with: taskrunner run <TASK>")
    return ExitCode.OK
