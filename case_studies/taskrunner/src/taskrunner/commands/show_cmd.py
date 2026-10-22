"""``taskrunner show`` -- describe one task."""

from __future__ import annotations

import argparse

from ..cli import TASKS
from ..errors import TaskRunnerError
from ..exit_codes import ExitCode
from ..suggest import closest_match

__all__ = ["cmd_show", "describe_task"]

#: Extra prose per task. Separate from TASKS so the one-line summaries stay short
#: enough for the `list` table.
DETAILS: dict[str, list[str]] = {
    "build": ["compiles every source file", "writes to build/"],
    "test": ["runs the test suite", "exits non-zero on failure"],
    "lint": ["checks style only", "never modifies files"],
    "docs": ["builds HTML into site/"],
    "clean": ["removes build/ and site/", "safe to run at any time"],
    "release": ["tags and publishes", "requires --yes to run non-interactively"],
}


def describe_task(name: str) -> dict[str, object]:
    """Return the record for one task, or raise with a suggestion."""
    if name not in TASKS:
        near = closest_match(name, TASKS)
        hint = f" Did you mean {near[0]!r}?" if near else ""
        raise TaskRunnerError(f"unknown task {name!r}.{hint}", ExitCode.CONFIG)
    return {
        "name": name,
        "summary": TASKS[name],
        "details": DETAILS.get(name, []),
    }


def cmd_show(args: argparse.Namespace) -> int:
    """Print a task's summary and details."""
    record = describe_task(args.task)
    if getattr(args, "format", "text") == "json":
        import json

        print(json.dumps(record, indent=2, sort_keys=True))
        return ExitCode.OK

    print(f"{record['name']}: {record['summary']}")
    for line in record["details"]:
        print(f"    {line}")
    return ExitCode.OK
