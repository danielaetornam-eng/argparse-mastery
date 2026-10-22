"""A parser that fits on one screen: a custom formatter for a *simple* CLI.

The case study uses a composed formatter with three behaviours. This is the
counter-example, included because "write your own layout" is a real option and
should be presented honestly.

The trade-off
-------------
**Pros**: total control, no argparse table at all, trivially predictable output.

**Cons**: you lose argparse's wrapping logic, its ``nargs`` rendering and its
mutually-exclusive-group markers, and you must handle ``help=SUPPRESS`` yourself.
For a small, stable argument set that is a good trade; for a growing CLI it is not.

Two rules you must honour yourself, both covered in lesson 27:

1. Skip ``help=SUPPRESS`` actions.
2. Render ``nargs`` correctly -- or delegate to
   ``HelpFormatter._format_args``, which already handles every case.
"""

from __future__ import annotations

import argparse

__all__ = ["OneLineFormatter"]


class OneLineFormatter(argparse.HelpFormatter):
    """One line per flag, no wrapping, no two-column table.

    Shows the minimal viable custom ``_format_help``: collect actions in
    ``add_argument`` and render them however you like.
    """

    def __init__(self, prog: str, **kwargs: object) -> None:
        super().__init__(prog, **kwargs)  # type: ignore[arg-type]
        self.collected: list[argparse.Action] = []

    def add_argument(self, action: argparse.Action) -> None:
        """Collect instead of rendering immediately.

        A ``HelpFormatter`` has no ``_actions``: it is *fed* actions by
        ``ArgumentParser.format_help()``. Collecting them here is the standard
        way to build a custom layout.
        """
        self.collected.append(action)

    def format_help(self) -> str:
        lines = [f"usage: {self._prog} [OPTIONS]", ""]
        for action in self.collected:
            # Rule 1: you must skip these yourself.
            if action.help is argparse.SUPPRESS:
                continue
            if isinstance(action, argparse._HelpAction):
                continue
            flags = ", ".join(action.option_strings) or action.dest
            # Rule 2: delegate the value rendering to argparse's own helper.
            invocation = self._format_args(action, action.metavar) if action.nargs else ""
            line = f"  {flags} {invocation}".rstrip()
            if action.help:
                line += f"   {action.help.splitlines()[0]}"
            lines.append(line.rstrip())
        return "\n".join(lines) + "\n"
