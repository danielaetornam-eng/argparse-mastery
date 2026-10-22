"""A composed help formatter, and the reasoning behind the composition.

Three stock behaviours, each of which matters for a command with subcommands:

* ``ArgumentDefaultsHelpFormatter`` -- a user should never have to run ``--help``
  twice to discover that ``--retries`` defaults to 3.
* ``RawDescriptionHelpFormatter`` -- descriptions and epilogs keep their line
  breaks, so the examples block stays readable.
* A third column for the environment variable, when there is one.

Composition works because the behaviours live in *different methods*:
``_get_help_string`` for defaults, ``_fill_text`` for raw text. Leftmost base wins,
so the most opinionated class goes first.

One limitation, verified and worth knowing: a custom ``action.format_help`` is
**bypassed** by ``ArgumentDefaultsHelpFormatter``, because that formatter reads
``action.help`` directly and never calls the method. So per-action help overrides
stop working under it (lesson 25). The robust place to put per-argument text is
the ``help=`` string at the definition site.
"""

from __future__ import annotations

import argparse

__all__ = ["TaskRunnerHelpFormatter", "render_help"]


class TaskRunnerHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """Defaults appended, free text un-wrapped, examples indented."""

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        # RawDescriptionHelpFormatter's behaviour: never re-wrap description or
        # epilog, so the examples block keeps its shape.
        return "".join(indent + line for line in text.splitlines(keepends=True))

    def _get_help_string(self, action: argparse.Action) -> str:
        """Append the default, and the env var when one exists.

        The env var is derived from the dest with the project's prefix, so the help
        text cannot fall out of sync with the naming convention -- the same
        "derive once, render twice" discipline as the exit-code table.

        ``-h`` and ``--version`` are deliberately excluded. They are argparse's own
        actions rather than declared options, so there is no environment variable
        for them, and advertising one would be a small lie in the documentation.
        """
        base = super()._get_help_string(action)
        if not base or action.dest is argparse.SUPPRESS:
            return base or ""
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            return base

        env_suffix = ""
        if action.dest and action.option_strings and not action.required:
            env_suffix = f" [env: TASKRUNNER_{action.dest.upper()}]"
        return f"{base}{env_suffix}"


def render_help(parser: argparse.ArgumentParser, *, width: int | None = 80) -> str:
    """Render ``--help`` with a pinned width, deterministically.

    ``ArgumentParser.format_help()`` takes no arguments -- it builds its own
    formatter with ``width=None``, meaning "ask the terminal". Pinning the width
    (for a golden-file test, a generated man page, or a narrow CI pane) means
    driving the formatter by hand, in the same order ``format_help`` does:

    1. ``add_usage(usage, actions, groups, prefix)``
    2. ``add_text(description)``
    3. ``add_argument`` for each action
    4. ``add_text(epilog)``

    It touches private attributes, which is precisely why it belongs in one helper
    rather than scattered across a test suite. ``tests/test_cli.py`` uses it for
    the help snapshot.
    """
    formatter = parser.formatter_class(prog=parser.prog, width=width)
    formatter.add_usage(
        parser.usage, parser._actions, parser._mutually_exclusive_groups
    )
    formatter.add_text(parser.description)
    for action in parser._actions:
        formatter.add_argument(action)
    formatter.add_text(parser.epilog)
    return formatter.format_help()
