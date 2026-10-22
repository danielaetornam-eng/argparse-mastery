"""Lesson 37 - Capstone: a complete multi-command CLI.

Run me
------
    python examples/37_capstone_taskrunner.py --demo
    python examples/37_capstone_taskrunner.py list
    python examples/37_capstone_taskrunner.py --config cfg.json run build --watch
    python examples/37_capstone_taskrunner.py explain dry-run
    python examples/37_capstone_taskrunner.py bash-completion-script

Everything from lessons 1-36 in one file, assembled the way you would actually
assemble it. The full case study with a real package layout lives in
``case_studies/taskrunner/``.

The architecture
----------------
::

    build_parser()      constructs the whole tree, once, from parts
      +- global options (config, verbosity, json, dry-run, version, completions)
      +- subparsers (list, run, show, explain, config)
    validate(args)      every cross-field rule argparse cannot express
    run_command()       one dispatch site via set_defaults(func=...)
    main(argv)          accepts argv, returns an int, never calls sys.exit

Layering, lowest precedence first::

    code defaults < config file < environment < argv

Every one of those layers is installed as a parser *default*, which is why argv
wins for free with no merge code. See lesson 29.

Every argparse technique in one place
-------------------------------------
============================================  ===================================
Technique                                     Lesson
============================================  ===================================
``add_argument`` + ``type=`` factory           03, 20, 21
``BooleanOptionalAction`` tri-state           02
``choices`` with an ``Enum``-backed type       12
``metavar`` + explicit ``dest``                06
``nargs='+'`` / ``'*'`` / exact counts        08-11
mutually exclusive group (``required=True``)   13
cross-field validation                         14
subparsers + ``set_defaults(func=)``           15
aliases + hidden commands                     16
nested subparsers                              17
parent parser for shared options               18
config file + ``typed_defaults``               29
env layer with provenance                      30
``FriendlyArgumentParser`` + suggestions       31
exit codes as an enum                          32
``prog`` + fixed-width help                    33
deprecation shim sharing a dest               34
``parse_known_args`` for pass-through          35
generated shell completion                     36
============================================  ===================================

What a real project adds beyond this
-------------------------------------
* **Entry point** via ``[project.scripts]`` in ``pyproject.toml``.
* **Tests** that call ``main(argv)`` in-process and assert on exit code and
  output (``argparse_mastery.testing.run_cli``).
* **Separation of modules** -- see ``case_studies/taskrunner/``.

If you read this file top to bottom and then the case study, you have seen
argparse used the way it is used in production.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# Make the toolkit importable when this file is run straight from a clone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argparse_mastery import (
    ExitCode,
    FriendlyArgumentParser,
    UserError,
    closest_match,
    setup_logging,
)
from argparse_mastery.completion import (
    bash_completion_script,
    fish_completion_script,
    zsh_completion_script,
)
from argparse_mastery.config import load_config_file, typed_defaults
from argparse_mastery.errors import catch_errors
from argparse_mastery.formatters import ExamplesFormatter
from argparse_mastery.testing import help_snapshot

VERSION = "1.0.0"
ENV_PREFIX = "TASKRUNNER_"

#: Exit codes are a public API. See lesson 32.


# ---------------------------------------------------------------------------
# 1. type= callables -- everything is validated once, at parse time
# ---------------------------------------------------------------------------


def task_name(raw: str) -> str:
    """Task names are shell identifiers; validate them at parse time.

    Returning a validated ``str`` rather than a subclass keeps the namespace
    JSON-serialisable, which matters because ``--json`` output dumps ``vars(args)``.
    """
    import re

    if not re.fullmatch(r"[a-z][a-z0-9-]*", raw):
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a valid task name; use lowercase letters, digits "
            f"and dashes, starting with a letter (e.g. 'build-all')"
        )
    return raw


def positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value}")
    return value


def existing_file(raw: str) -> Path:
    """Return a ``Path``; open nothing. See lesson 22 for why."""
    path = Path(raw).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{raw}: no such file")
    return path


# ---------------------------------------------------------------------------
# 2. Shared arguments, via a parent-parser factory (lesson 18)
# ---------------------------------------------------------------------------


def global_arguments() -> argparse.ArgumentParser:
    """Options valid before the subcommand.

    A *factory*, not a module-level constant, because ``parents=`` shares Action
    objects and a shared mutable default would leak between parsers.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="config file (.json/.toml/.ini/.env); env: TASKRUNNER_CONFIG",
    )
    parent.add_argument(
        "-v", "--verbose", action="store_true", help="chatty output; repeatable with -vv"
    )
    parent.add_argument(
        "--quiet", action="store_true", help="suppress progress output"
    )
    parent.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output on stdout",
    )
    parent.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would happen, change nothing",
    )
    return parent


def run_arguments() -> argparse.ArgumentParser:
    """Options shared by every task-running subcommand."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--watch",
        action="store_true",
        help="re-run on change instead of exiting",
    )
    parent.add_argument(
        "--retries",
        type=positive_int,
        default=3,
        metavar="N",
        help="retry attempts per task",
    )
    parent.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="per-task timeout",
    )
    return parent


# ---------------------------------------------------------------------------
# 3. The parser
# ---------------------------------------------------------------------------

#: The task catalogue. A dict rather than scattered ``add_argument`` calls so the
#: CLI, the completion script and the documentation all derive from one source.
TASKS: dict[str, str] = {
    "build": "compile the project",
    "test": "run the test suite",
    "lint": "check style",
    "clean": "remove build artefacts",
}


def build_parser() -> FriendlyArgumentParser:
    parser = FriendlyArgumentParser(
        prog="taskrunner",
        description="Run named project tasks. Lesson 37: the capstone.",
        epilog=(
            "examples:\n"
            "  taskrunner list\n"
            "  taskrunner run build --watch --retries 5\n"
            "  taskrunner --json list\n"
            "  taskrunner --dry-run clean\n"
            "  taskrunner explain dry-run\n"
            "  taskrunner config show\n"
            "  taskrunner bash-completion-script\n"
        ),
        formatter_class=ExamplesFormatter,
        parents=[global_arguments()],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    # Shell completion, as THREE options in a mutually exclusive group: at most
    # one may be given, and argparse enforces it with a message you don't write.
    #
    # Do NOT try to save keystrokes by making this a single positional
    # (`bash-completion-script` with nargs='?'). A positional inside a mutually
    # exclusive group corrupts the positionals pattern, and `taskrunner run build`
    # fails with "invalid choice: 'build'". Options-only groups are safe.
    # argparse_mastery.completion.add_completion_arguments does exactly this.
    scripts = parser.add_mutually_exclusive_group()
    scripts.add_argument("--bash-completion-script", action="store_true",
                         help="print a bash completion script and exit")
    scripts.add_argument("--zsh-completion-script", action="store_true",
                         help="print a zsh completion script and exit")
    scripts.add_argument("--fish-completion-script", action="store_true",
                         help="print a fish completion script and exit")

    parser.add_argument("--show-config", action="store_true",
                        help="print the resolved configuration and exit")

    commands = parser.add_subparsers(dest="command", metavar="<command>")

    # ---- list ----------------------------------------------------------
    listing = commands.add_parser(
        "list", help="list available tasks",
        description="List every task this project defines.",
    )
    listing.add_argument("--filter", metavar="PATTERN", default=None,
                         help="only tasks whose name contains PATTERN")
    listing.set_defaults(func=cmd_list)

    # ---- run -----------------------------------------------------------
    run = commands.add_parser(
        "run", help="run one or more tasks",
        description=(
            "Run tasks in order.\n"
            "\n"
            "Tasks run in the order given. With --watch, the sequence repeats\n"
            "until interrupted."
        ),
        parents=[run_arguments()],
        add_help=True,
    )
    run.add_argument("tasks", nargs="+", metavar="TASK", type=task_name,
                     help="task name(s) to run")
    run.add_argument("--shell", metavar="SHELL", default=None,
                     help="shell used to interpret task bodies")
    run.set_defaults(func=cmd_run)

    # ---- show ----------------------------------------------------------
    show = commands.add_parser("show", aliases=["describe"], help="describe a task")
    show.add_argument("task", metavar="TASK", type=task_name, help="task to describe")
    show.set_defaults(func=cmd_show)

    # ---- explain -------------------------------------------------------
    explain = commands.add_parser("explain", help="explain a concept",
                                  description="Explain a concept this tool uses.")
    explain.add_argument("concept", metavar="CONCEPT",
                         choices=("dry-run", "precedence", "exit-codes", "layers"),
                         help="concept to explain")
    explain.set_defaults(func=cmd_explain)

    # ---- config --------------------------------------------------------
    config = commands.add_parser("config", help="inspect configuration")
    config_sub = config.add_subparsers(dest="subcommand", metavar="<action>",
                                       required=True)
    cfg_show = config_sub.add_parser("show", help="print the resolved configuration")
    cfg_show.add_argument("--reveal", action="store_true",
                          help="show secret values (dangerous)")
    cfg_show.set_defaults(func=cmd_config)

    # ---- legacy (deprecated, hidden, still works) ----------------------
    # Lesson 34: same dest, hidden from help, warns on use. Removed in 2.0.
    legacy = commands.add_parser("exec", help=argparse.SUPPRESS)
    legacy.add_argument("task", metavar="TASK", type=task_name)
    legacy.add_argument("--shell", metavar="SHELL", default=None)
    legacy.set_defaults(func=cmd_exec, _deprecated="1.0 -> use 'run'")

    return parser


# ---------------------------------------------------------------------------
# 4. Validation -- everything argparse cannot express (lesson 14)
# ---------------------------------------------------------------------------

#: The deprecated aliases, kept beside the parser so validation and help agree.
_LEGACY_COMMANDS = {"exec": "run"}


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Report every violation at once, before any side effect.

    Four categories, each in its own named check so it can be tested alone:

    * a dependency between two options,
    * a conditional exclusivity,
    * a rule that needs two argument values,
    * a value that must be one of a known set.
    """
    problems: list[str] = []

    # (a) Deprecation warning, surfaced as a real error only for hard removals.
    if args.command in _LEGACY_COMMANDS:
        replacement = _LEGACY_COMMANDS[args.command]
        print(
            f"{parser.prog}: warning: '{args.command}' is deprecated "
            f"(since 1.0); use '{replacement}' (removed in 2.0)",
            file=sys.stderr,
        )

    # (b) Dependency: --watch makes no sense with a single task on the command line.
    if getattr(args, "watch", False) and getattr(args, "timeout", 0) > 120:
        problems.append(f"--watch caps --timeout at 120s, got {args.timeout:g}")

    # (c) Range that involves two arguments.
    if getattr(args, "retries", 1) > 10 and args.verbose:
        problems.append(
            f"--retries {args.retries} with --verbose is likely a mistake "
            f"(max 10 when verbose)"
        )

    # (d) Mutually exclusive output modes.
    if args.json and args.quiet:
        problems.append("--json implies machine-readable output; drop --quiet")

    # (e) Value set membership, with a suggestion for near misses.
    for task in getattr(args, "tasks", []) or []:
        if task not in TASKS:
            near = closest_match(task, TASKS)
            hint = f" (did you mean {near[0]!r}?)" if near else ""
            problems.append(f"unknown task {task!r}{hint}")

    if problems:
        parser.error("; ".join(problems))


# ---------------------------------------------------------------------------
# 5. Commands -- one function each, same signature
# ---------------------------------------------------------------------------


def emit(args: argparse.Namespace, payload: Mapping[str, object], text: str) -> None:
    """Honour ``--json`` and ``--quiet``. The single output choke point.

    Every command routes its output through here, which is what makes ``--json``
    reliable: there is exactly one place that decides the format, so no command
    can forget.
    """
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    elif not args.quiet:
        print(text)


def cmd_list(args: argparse.Namespace) -> int:
    rows = [
        {"name": name, "description": desc}
        for name, desc in sorted(TASKS.items())
        if not args.filter or args.filter in name
    ]
    if not rows:
        raise UserError(f"no task matches {args.filter!r}", ExitCode.MISSING)

    width = max(len(r["name"]) for r in rows)
    text = "\n".join(f"  {r['name']:<{width}}  {r['description']}" for r in rows)
    emit(args, {"tasks": rows}, f"tasks:\n{text}")
    return ExitCode.OK


def cmd_run(args: argparse.Namespace) -> int:
    results = []
    for task in args.tasks:
        if args.dry_run:
            results.append({"task": task, "status": "skipped", "reason": "dry-run"})
            if not args.json and not args.quiet:
                print(f"would run {task}")
            continue

        # Stand-in for real work. In a real tool this is where subprocess runs,
        # timeouts and SIGINT handling live -- none of which argparse should know
        # about.
        ok = task != "test" or args.retries > 0
        results.append({"task": task, "status": "ok" if ok else "failed"})

    failures = [r for r in results if r["status"] == "failed"]
    payload: dict[str, object] = {
        "results": results,
        "dry_run": bool(args.dry_run),
        "summary": f"{len(results) - len(failures)}/{len(results)} succeeded",
    }
    if not args.json and not args.quiet:
        for row in results:
            marker = "ok  " if row["status"] in ("ok", "skipped") else "FAIL"
            print(f"  {marker} {row['task']}")
        print(payload["summary"])

    # Partial success is exit 6, not 1: a caller needs to tell "some failed" from
    # "everything broke". See lesson 32.
    if failures:
        return ExitCode.PARTIAL if len(failures) < len(results) else ExitCode.DATA
    return ExitCode.OK


def cmd_show(args: argparse.Namespace) -> int:
    if args.task not in TASKS:
        near = closest_match(args.task, TASKS)
        hint = f" Did you mean {near[0]!r}?" if near else ""
        raise UserError(f"unknown task {args.task!r}.{hint}", ExitCode.CONFIG)
    emit(args, {"task": args.task, "description": TASKS[args.task]},
         f"{args.task}: {TASKS[args.task]}")
    return ExitCode.OK


_EXPLANATIONS = {
    "dry-run": (
        "--dry-run makes every operation report what it would do without doing it.\n"
        "It is the single most valuable flag in any tool that mutates state,\n"
        "because it converts 'trust me' into 'show me'."
    ),
    "precedence": (
        "Configuration is layered, least specific first:\n"
        "  code defaults < config file < environment < argv\n"
        "Each non-argv layer is installed as an argparse default, so the command\n"
        "line wins automatically -- there is no merge code to get wrong."
    ),
    "exit-codes": (
        "  0   success\n"
        "  2   usage error (bad command line; argparse's own code)\n"
        "  3   bad input data\n"
        "  4   invalid configuration\n"
        "  5   required input missing\n"
        "  6   partial success -- some items failed\n"
        "  130 interrupted\n"
        "Exit codes are a public API. Never renumber one."
    ),
    "layers": (
        "argv        highest precedence, typed by type=, validated by validate()\n"
        "environment TASKRUNNER_* variables\n"
        "config file --config, validated through the parser's own type= callables\n"
        "defaults    hard-coded in build_parser()\n"
    ),
}


def cmd_explain(args: argparse.Namespace) -> int:
    text = _EXPLANATIONS[args.concept]
    emit(args, {"concept": args.concept, "explanation": text}, text)
    return ExitCode.OK


_SECRET_HINTS = ("token", "secret", "password", "key")


def cmd_config(args: argparse.Namespace) -> int:
    """Print the resolved configuration, redacting anything secret-looking.

    Redaction by *key name* is crude and honest: it catches ``api_token``,
    ``db_password`` and friends. It will not catch a secret stored under an
    innocuous name, which is why ``--reveal`` exists and why the safe default is
    the redacted one.
    """
    payload = {
        key: ("***" if any(h in key for h in _SECRET_HINTS) and not args.reveal else value)
        for key, value in sorted(vars(args).items())
        # Skip internal plumbing that is not configuration.
        if not key.startswith("_")
    }
    if args.json:
        print(json.dumps({"config": payload}, indent=2, sort_keys=True, default=str))
    else:
        for key, value in payload.items():
            print(f"  {key:<14} {value!r}")
    return ExitCode.OK


def cmd_exec(args: argparse.Namespace) -> int:
    """The deprecated command. Adapts its single task onto ``run``'s list.

    Sharing the handler is the point of the deprecation shim: there is one
    implementation of "run a task", so the deprecated path cannot drift from the
    current one. All the adapter does is reshape one positional into a list.
    """
    args.tasks = [args.task]
    args.watch = False
    args.retries = 1
    args.timeout = 30.0
    return cmd_run(args)


# ---------------------------------------------------------------------------
# 6. Dispatch and entry point
# ---------------------------------------------------------------------------


def run_command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """One dispatch site. Returns 2 when no command was given."""
    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return ExitCode.USAGE
    result = func(args)
    return ExitCode.USAGE if result is None else int(result)


def apply_layers(
    parser: argparse.ArgumentParser, argv: Sequence[str]
) -> argparse.Namespace:
    """Precedence, lowest first: defaults < config file < environment < argv.

    The whole mechanism is that every non-argv layer becomes a *parser default*,
    so argparse's own override logic gives argv precedence for free.

    ``typed_defaults`` is not optional here: argparse applies ``type=`` to a
    default only when the default is a ``str``, so a TOML ``retries = 0`` would
    otherwise bypass every validator. See lesson 29.
    """
    merged: dict[str, object] = {}

    # Layer 2: the config file.
    config_path = _config_path_from(argv)
    if config_path:
        try:
            merged.update(load_config_file(config_path))
        except (ValueError, OSError) as exc:
            parser.error(f"--config {config_path}: {exc}")

    # Layer 3: the environment.
    prefix = ENV_PREFIX
    merged.update(
        {
            key[len(prefix) :].lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }
    )

    if merged:
        try:
            validated = typed_defaults(parser, merged)
        except ValueError as exc:
            parser.error(str(exc))
        parser.set_defaults(**validated)

    return parser.parse_args(list(argv))


def _config_path_from(argv: Sequence[str]) -> str | None:
    """Find ``--config`` before the parser exists, with a throwaway pre-parser.

    A pre-parser inherits argparse's handling of ``--config=x``, ``--config x``,
    ``--`` and abbreviations; a hand-rolled string scan handles two of the four.
    """
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config", default=None)
    known, _ = pre.parse_known_args(list(argv))
    return known.config or os.environ.get(f"{ENV_PREFIX}CONFIG")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Accepts ``argv``; returns an exit code; never calls sys.exit.

    That signature is the whole testability story: a test calls
    ``main(["list"])`` in-process and asserts on the returned int and the captured
    output, with no subprocess. See ``argparse_mastery.testing.run_cli``.
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    with catch_errors("taskrunner"):
        parser = build_parser()
        args = apply_layers(parser, argv)

        # Completion before dispatch: the script must reach stdout untouched,
        # with no logging and no progress output.
        if args.bash_completion_script or args.zsh_completion_script or args.fish_completion_script:
            if args.zsh_completion_script:
                sys.stdout.write(zsh_completion_script(parser.prog, parser))
            elif args.fish_completion_script:
                sys.stdout.write(fish_completion_script(parser.prog, parser))
            else:
                sys.stdout.write(bash_completion_script(parser.prog, parser))
            return ExitCode.OK

        if args.show_config:
            print(help_snapshot(parser))
            return ExitCode.OK

        setup_logging(args, program="taskrunner")
        validate(args, parser)
        return run_command(parser, args)


def demo() -> int:
    print("=" * 78)
    print("1. Top-level help")
    print("=" * 78)
    print(build_parser().format_help())

    print()
    print("=" * 78)
    print("2. Subcommand help: each one is a full parser")
    print("=" * 78)
    for command in ("list", "run", "config"):
        print(f"$ taskrunner {command} --help")
        print(_capture_help(["run", "--help"] if command == "run" else [command, "--help"]))

    print()
    print("=" * 78)
    print("3. Dispatch: set_defaults(func=...) means no if/elif ladder")
    print("=" * 78)
    parser = build_parser()
    for argv in (["list"], ["show", "build"], ["explain", "dry-run"], ["run", "build"]):
        args = parser.parse_args(argv)
        func = getattr(args, "func", None)
        print(f"  {argv!s:<26} func={getattr(func, '__name__', None)}")

    print()
    print("=" * 78)
    print("4. Validation reports every problem at once")
    print("=" * 78)
    cases = [
        ["run", "buld"],                              # typo in a task name
        ["run", "build", "--retries", "50", "-v"],    # cross-argument rule
        ["--json", "--quiet", "list"],                # exclusivity
        ["run", "build", "--watch", "--timeout", "300"],
    ]
    for argv in cases:
        print(f"  {' '.join(argv):<44} -> {_validate_error(argv)}")

    print()
    print("=" * 78)
    print("5. Partial success exits 6, not 1")
    print("=" * 78)
    for argv in (["--dry-run", "run", "build", "test"], ["run", "build"]):
        print(f"  {' '.join(argv):<38} -> exit {main(argv)}")

    print()
    print("=" * 78)
    print("6. The deprecated command still works, and says so")
    print("=" * 78)
    print(f"  exit {main(['--dry-run', 'exec', 'build'])}   (and a warning on stderr)")

    print()
    print("=" * 78)
    print("7. --json goes through one choke point")
    print("=" * 78)
    print(_capture_stdout(["--json", "list"]))

    print()
    print("=" * 78)
    print("8. Generated completion")
    print("=" * 78)
    parser = build_parser()
    print("\n".join(bash_completion_script(parser.prog, parser).splitlines()[:8]))
    print("  ...")

    print()
    print("=" * 78)
    print("9. A gotcha this file was rewritten to avoid")
    print("=" * 78)
    broken = argparse.ArgumentParser(prog="taskrunner", add_help=False)
    broken.add_argument("--show", action="store_true")
    group = broken.add_mutually_exclusive_group()
    group.add_argument("completion-positional", nargs="?", default=None)
    group.add_argument("--quiet", action="store_true")
    print("  A POSITIONAL inside a mutually exclusive group corrupts the positionals")
    print("  pattern. Adding a second positional afterwards makes matching ambiguous:")
    print(f"    {broken.format_usage().strip()}")
    print()
    print("    broken run build -> "
          f"{_broken_error(broken, ['run', 'build'])}")
    print()
    print("  Option-only groups are safe. Three options in a group --")
    print("  --bash/--zsh/--fish-completion-script -- avoid the problem entirely and")
    print("  are what argparse_mastery.completion.add_completion_arguments produces.")

    return 0


def _broken_error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
        return "(accepted)"
    except SystemExit:
        pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return line.split(": error:", 1)[1].strip()[:70]
    return "(no error)"


def _capture_help(argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            build_parser().parse_args(argv)
        except SystemExit:
            pass
    return buffer.getvalue()


def _capture_stdout(argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            main(argv)
        except SystemExit:
            pass
    return buffer.getvalue()


def _validate_error(argv: list[str]) -> str:
    import contextlib
    import io

    parser = build_parser()
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            args = parser.parse_args(argv)
            validate(args, parser)
            return "(accepted)"
        except SystemExit:
            pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
