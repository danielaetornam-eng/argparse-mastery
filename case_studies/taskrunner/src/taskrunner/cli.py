"""``build_parser`` -- the entire command-line surface, in one place.

This module deliberately imports **no command implementations**. Handlers are
registered by name from :mod:`taskrunner.commands`, which imports this module.
That direction means:

* ``cli.py`` stays importable with no command dependencies,
* adding a command is one entry in :data:`COMMANDS` plus one file,
* a slow or heavy command module is only imported when its subcommand is used
  (see :func:`taskrunner.commands.dispatch`).

Structural decisions worth studying
----------------------------------
* **One parser per subcommand**, each with its own description, epilog and
  formatter. Help quality is a direct consequence.
* **Parent parsers as factories** for shared options, so Action objects -- and
  therefore mutable defaults -- are never shared between parsers (lesson 18).
* **Handlers attached with ``set_defaults(func=...)``**, so ``main`` has one
  dispatch site and adding a command cannot produce a half-wired branch.
* **The task catalogue as data**, so the CLI, the completion script and the
  documentation all derive from one source.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .exit_codes import describe_exit_codes

__all__ = [
    "COMMANDS",
    "TASKS",
    "build_parser",
    "find_config_argv",
    "task_name",
]

#: The task catalogue. One dict so ``list``, ``show``, ``explain`` and the
#: completion script cannot disagree about what exists.
TASKS: dict[str, str] = {
    "build": "compile the project",
    "test": "run the test suite",
    "lint": "check style",
    "docs": "build the documentation",
    "clean": "remove build artefacts",
    "release": "cut a release (requires --yes)",
}

#: ``command name -> (module, function, help, aliases)``. The handler is resolved
#: lazily by :func:`taskrunner.commands.dispatch`, so this table is cheap to import.
COMMANDS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "list": ("list_cmd", "cmd_list", "list available tasks", ()),
    "run": ("run_cmd", "cmd_run", "run one or more tasks", ("exec",)),
    "show": ("show_cmd", "cmd_show", "describe a task", ("describe",)),
    "explain": ("explain_cmd", "cmd_explain", "explain a concept", ()),
    "config": ("config_cmd", "cmd_config", "inspect configuration", ()),
}

_TASK_NAME_RE = re.compile(r"[a-z][a-z0-9-]*")


def task_name(raw: str) -> str:
    """``type=`` for task names: lowercase, dashes, starting with a letter.

    Validating at parse time gives ``argument TASK: 'Build_All' is not a valid task
    name; use lowercase letters, digits and dashes`` instead of a vague
    "unknown task" later. Returns a plain ``str`` rather than a ``str`` subclass so
    ``--json`` output stays JSON-serialisable.
    """
    if not _TASK_NAME_RE.fullmatch(raw):
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a valid task name; use lowercase letters, digits and "
            f"dashes, starting with a letter (for example 'build-all')"
        )
    return raw


def find_config_argv(argv: list[str]) -> str | None:
    """Find ``--config`` before the real parser exists.

    The bootstrapping problem: you need the config file to build the parser, but
    ``--config`` is one of the parser's own options. A throwaway pre-parser is the
    faithful solution, because it inherits argparse's handling of ``--config=x``,
    ``--config x``, ``--`` and prefixes.

    Two details that are easy to get wrong:

    * ``add_help=False`` is essential, or ``-h`` is consumed here and the real
      parser never sees it.
    * ``allow_abbrev=False`` keeps the pre-parser's spelling rules identical to the
      real one, so ``--conf`` cannot match here and then fail there.
    """
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config", default=None)
    known, _ = pre.parse_known_args(argv)
    return known.config


# ---------------------------------------------------------------------------
# Parent parsers -- factories, not constants (lesson 18)
# ---------------------------------------------------------------------------


def _global_options() -> argparse.ArgumentParser:
    """Options accepted before the subcommand."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--config", metavar="PATH", default=None,
        help="config file (.json/.toml/.ini/.env)",
    )
    verbosity = parent.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="repeat for more output (-v, -vv)",
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress progress output (implies --json-safe)",
    )
    parent.add_argument(
        "--json", action="store_true",
        help="machine-readable output on stdout",
    )
    parent.add_argument(
        "--dry-run", action="store_true",
        help="report what would happen; change nothing",
    )
    return parent


def _output_options() -> argparse.ArgumentParser:
    """Options shared by commands that emit records."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--format", choices=("text", "json", "yaml"), default="text",
        metavar="{text,json,yaml}", help="output format (--json is shorthand)",
    )
    parent.add_argument(
        "--sort", choices=("name", "recent"), default="name",
        help="ordering for list-style output",
    )
    return parent


def _run_options() -> argparse.ArgumentParser:
    """Options shared by the task-running commands."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--watch", action="store_true",
        help="re-run on change instead of exiting",
    )
    parent.add_argument(
        "--retries", type=positive_int, default=3, metavar="N",
        help="retry attempts per task (1-10)",
    )
    parent.add_argument(
        "--timeout", type=float, default=30.0, metavar="SECONDS",
        help="per-task timeout",
    )
    parent.add_argument(
        "--shell", metavar="SHELL", default=None,
        help="shell used to interpret task bodies",
    )
    return parent


def positive_int(raw: str) -> int:
    """``type=`` factory example: convert, range-check, and teach on failure."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not an integer (expected a whole number like 3)"
        ) from None
    if not 1 <= value <= 10:
        raise argparse.ArgumentTypeError(
            f"{value} is outside the allowed range 1-10"
        )
    return value


def seconds(raw: str) -> float:
    """``type=`` that also rejects NaN and infinity.

    ``float("nan")`` and ``float("inf")`` parse successfully and break every
    comparison made afterwards, silently. A bounded float ``type=`` is the only
    place they can be caught.
    """
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a number") from None
    if value != value:
        raise argparse.ArgumentTypeError("NaN is not an acceptable value")
    if value in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("infinity is not an acceptable value")
    if not 0 < value <= 3600:
        raise argparse.ArgumentTypeError(f"{value} must be between 0 and 3600 seconds")
    return value


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def build_parser(prog: str = "taskrunner") -> argparse.ArgumentParser:
    """Construct the whole command-line surface.

    Import cost matters here: only the parser is built, no handler modules are
    imported. ``argparse`` itself is cheap; keeping the graph acyclic is what keeps
    startup fast.
    """
    from .formatting import TaskRunnerHelpFormatter

    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run named project tasks.",
        epilog=_build_epilog(),
        formatter_class=TaskRunnerHelpFormatter,
        parents=[_global_options()],
    )
    parser.add_argument(
        "--version", action="version",
        version=f"%(prog)s {_version()}",
    )

    # Completion, as three options in an option-only mutually exclusive group.
    #
    # Do NOT try to save keystrokes by making this one positional: a positional
    # inside a mutually exclusive group corrupts the positionals pattern, and
    # `taskrunner run build` then fails with "invalid choice: 'build'".
    scripts = parser.add_mutually_exclusive_group()
    scripts.add_argument("--bash-completion-script", action="store_true",
                         help="print a bash completion script and exit")
    scripts.add_argument("--zsh-completion-script", action="store_true",
                         help="print a zsh completion script and exit")
    scripts.add_argument("--fish-completion-script", action="store_true",
                         help="print a fish completion script and exit")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    for name, (_module, _func, help_text, aliases) in COMMANDS.items():
        child = _build_subparser(subparsers, name, help_text, aliases)

        if name == "list":
            child.add_argument("--filter", metavar="PATTERN", default=None,
                               help="only tasks whose name contains PATTERN")
        elif name == "run":
            child.add_argument("tasks", nargs="+", metavar="TASK", type=task_name,
                               help="one or more task names")
        elif name == "show":
            child.add_argument("task", metavar="TASK", type=task_name,
                               help="task to describe")
        elif name == "explain":
            child.add_argument("concept", metavar="CONCEPT",
                               choices=("dry-run", "precedence", "exit-codes", "layers"),
                               help="concept to explain")
        elif name == "config":
            config_sub = child.add_subparsers(dest="subcommand",
                                               metavar="<action>", required=True)
            show = config_sub.add_parser("show", help="print the resolved configuration")
            show.add_argument("--reveal", action="store_true",
                              help="show secret values (dangerous)")
            show.set_defaults(_handler=("config_cmd", "cmd_config"))

    return parser


def _build_subparser(
    subparsers: argparse._SubParsersAction,
    name: str,
    help_text: str,
    aliases: tuple[str, ...],
) -> argparse.ArgumentParser:
    """Create one subparser with the parents it needs and its own help text.

    Split out so the per-command arguments in :func:`build_parser` stay a flat
    readable list rather than being buried in constructor calls.
    """
    kwargs: dict[str, object] = {
        "help": help_text,
        "aliases": list(aliases),
        "add_help": True,
    }

    if name in ("list", "show", "explain", "config"):
        kwargs["parents"] = [_output_options()]
        kwargs["description"] = _describe(name)
    elif name == "run":
        kwargs["parents"] = [_run_options(), _output_options()]
        kwargs["description"] = _describe(name)
        kwargs["epilog"] = (
            "examples:\n"
            "  taskrunner run build\n"
            "  taskrunner run --watch --retries 5 test lint\n"
            "  taskrunner --json --dry-run release\n"
        )

    child = subparsers.add_parser(name, **kwargs)
    # The handler is resolved lazily; see taskrunner.commands.dispatch.
    module, func, _, _ = COMMANDS[name]
    child.set_defaults(_handler=(module, func), _command_name=name)
    return child


def _describe(command: str) -> str:
    """Per-subcommand descriptions. Kept as prose because ``--help`` is docs."""
    return {
        "list": "List every task this project defines.",
        "run": (
            "Run tasks in the order given.\n"
            "\n"
            "With --watch the sequence repeats until interrupted. With --dry-run\n"
            "nothing is executed; each task is reported as it would be."
        ),
        "show": "Describe one task: what it does and what it depends on.",
        "explain": "Explain a concept this tool uses.",
        "config": "Inspect the resolved configuration and where each value came from.",
    }[command]


def _build_epilog() -> str:
    """Examples plus the generated exit-code table."""
    return "\n".join(
        [
            "examples:",
            "  taskrunner list",
            "  taskrunner show build",
            "  taskrunner run --watch test",
            "  taskrunner --dry-run release",
            "  taskrunner config show",
            "",
            describe_exit_codes(),
            "",
            "configuration precedence (least specific first):",
            "  code defaults  <  config file  <  environment  <  command line",
        ]
    )


def _version() -> str:
    from . import __version__

    return __version__


def repo_root() -> Path:
    """Where the tool looks for ``taskrunner.toml``.

    Kept separate so it is a one-line change if the convention changes, and so
    tests can monkeypatch it.
    """
    return Path.cwd()
