"""``taskrunner run`` -- execute tasks.

The interesting parts are not argparse, and that is the lesson: argparse hands you
a namespace, and everything that is not argument parsing belongs in a different
module entirely.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ..cli import TASKS
from ..errors import TaskRunnerError
from ..exit_codes import ExitCode
from ..validation import validate_run_args

__all__ = ["cmd_run", "execute"]

#: Stand-in for real work. A real tool would shell out here, honour --timeout, and
#: forward signals. None of that belongs in the parser module.
DRY_RUN_REASON = "dry-run"

#: Tasks that always fail, so the demo can show exit code 6 (partial success)
#: and exit code 3 (everything failed) without needing a broken project.
ALWAYS_FAILING = frozenset({"docs"})
DRY_RUN_FAILURES = frozenset({"test"})


def execute(task: str, *, dry_run: bool, retries: int) -> dict[str, object]:
    """Run one task and return a result record.

    Returns data, never prints and never exits -- so the rendering and exit-code
    decisions stay in :func:`cmd_run`, and this stays trivially testable.

    ``DRY_RUN_FAILURES`` stands in for the real failure paths (a non-zero exit
    from the subprocess, a timeout) so the demo can exercise partial success
    without needing a task that actually fails.
    """
    if dry_run:
        status = "skipped" if task not in DRY_RUN_FAILURES else "failed"
        return {"task": task, "status": status, "reason": DRY_RUN_REASON}

    started = time.monotonic()
    # Placeholder for the real work: a subprocess.run with `timeout` and signal
    # forwarding. Note that the *exit status* decides success, never the presence
    # of a flag like `--release-safe`, which is how a task becomes unsafe by
    # accident.
    status = "failed" if task in ALWAYS_FAILING else "ok"
    return {
        "task": task,
        "status": status,
        "attempts": retries,
        "seconds": round(time.monotonic() - started, 4),
    }


def cmd_run(args: argparse.Namespace) -> int:
    """Run the requested tasks in order, and choose an exit code for the batch."""
    tasks: list[str] = list(getattr(args, "tasks", []) or [])

    # Unknown task names are a *configuration* error, not a usage error: the
    # command line was well-formed, the name simply does not exist. Checked here
    # rather than in `type=` so the message can list the alternatives.
    require_known_tasks(tasks)

    # Every cross-argument rule in one place, before anything executes.
    validate_run_args(args)

    results = [execute(task, dry_run=args.dry_run, retries=args.retries)
               for task in tasks]

    failed = [r for r in results if r["status"] == "failed"]

    as_json = getattr(args, "json", False) or getattr(args, "format", "text") == "json"
    if as_json:
        json.dump(
            {
                "results": results,
                "dry_run": bool(args.dry_run),
                "summary": f"{len(results) - len(failed)}/{len(results)} succeeded",
            },
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    elif not getattr(args, "quiet", False):
        for row in results:
            marker = "ok  " if row["status"] in ("ok", "skipped") else "FAIL"
            print(f"  {marker} {row['task']}")
        print(f"{len(results) - len(failed)}/{len(results)} succeeded")

    # Exit codes are part of the API (lesson 32). Distinguishing "some failed"
    # from "everything failed" lets a caller retry differently.
    if not failed:
        return ExitCode.OK
    if len(failed) == len(results):
        return ExitCode.DATA
    return ExitCode.PARTIAL


def require_known_tasks(tasks: list[str]) -> None:
    """Raise for an unknown task name, with a suggestion for near misses.

    Kept out of ``type=`` on purpose: ``type=`` sees one token and cannot consult
    the task catalogue... well, it could, but making validity depend on shared
    mutable state in a converter is worse than a check after parsing, where the
    error can mention the alternatives.
    """
    from ..suggest import closest_match

    problems = []
    for task in tasks:
        if task in TASKS:
            continue
        near = closest_match(task, TASKS)
        hint = f" Did you mean {near[0]!r}?" if near else ""
        problems.append(f"unknown task {task!r}.{hint}")
    if problems:
        raise TaskRunnerError("; ".join(problems), ExitCode.CONFIG)
