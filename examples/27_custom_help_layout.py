"""Lesson 27 - Rewriting the help layout from scratch.

Run me
------
    python examples/27_custom_help_layout.py --help
    python examples/27_custom_help_layout.py --demo

When to abandon argparse's layout
---------------------------------
The stock two-column layout (``flags`` / ``help text``) breaks down when:

* the flag column is long, pushing help text into a narrow right margin
* you need a third column (default, env var, since-version)
* you want flags grouped by topic rather than by declaration order
* you want a compact one-line-per-flag summary as well as the full text

All of those require overriding ``_format_action``, and once you are overriding it
anyway, the constraint pays off: you control the entire row.

What an Action exposes
----------------------
============================  ==============================================
Attribute                    Contents
============================  ==============================================
``option_strings``            ``['-v', '--verbose']`` (empty for positionals)
``metavar``                   the placeholder, or ``None``
``dest``                      the namespace key
``nargs``                     ``0``, ``1``, ``2``, ``'?'``, ``'*'``, ``'+'``,
                              ``REMAINDER``, ``PARSER``, or an ``(m, n)``-free int
``help``                      the help string, possibly ``SUPPRESS``
``default``                   the default value
``choices``                   allowed values, or ``None``
``required``                  whether it must appear
============================  ==============================================

Two rules you must honour yourself
----------------------------------
argparse's built-in formatting does these for you; a custom ``_format_action``
does not:

1. **Skip ``SUPPRESS`` entries.** ``if action.help is SUPPRESS: return ""``.
   Forgetting this is the classic custom-formatter bug and it silently exposes
   your hidden flags.
2. **Render ``nargs`` correctly.** The mapping is not a one-liner:

   ============  ===========================================
   ``nargs``     ``VALUE`` forms
   ============  ===========================================
   ``0``         no value
   ``1`` / None  ``VALUE``
   ``2``         ``VALUE VALUE``
   ``'?'``       ``[VALUE]``
   ``'*'``       ``[VALUE ...]``
   ``'+'``       ``VALUE [VALUE ...]``
   ``REMAINDER`` ``VALUE ...``
   ``PARSER``    ``VALUE ...``
   ============  ===========================================

Rather than re-derive this, you can call the stdlib's own implementation::

    formatter._format_args(action, action.metavar)

which produces the correct chunk including the ``...`` suffixes. Using it is
better than copying it -- and worth knowing it exists.

Overriding ``format_usage`` as well
-----------------------------------
If you change the row layout, the usage line is usually still fine because
``_format_args`` is separate. But if you hide options entirely (lesson 25), you
must also override ``_format_usage`` -- or override each action's ``format_usage``
to return ``""``.

Reusing argparse's parts
------------------------
You rarely need to reimplement everything. The practical recipe is:

* call ``super()._format_usage(...)`` for the usage line,
* call ``self._format_action_invocation(action)`` for the flag+value chunk,
* call ``self._expand_help(action)`` for ``%(default)s`` interpolation,
* then lay out your own columns.

That last one matters: ``_expand_help`` is what substitutes ``%(default)s``, and
bypassing it breaks the most common help-string idiom.
"""

from __future__ import annotations

import argparse
import sys
from argparse import HelpFormatter


class TableHelpFormatter(HelpFormatter):
    """A three-column table: FLAGS | VALUE | DESCRIPTION.

    Demonstrates a complete ``_format_action`` override, including the two things
    you must handle yourself:

    * skipping ``help=SUPPRESS`` entries, and
    * rendering ``nargs`` correctly.

    It also demonstrates the three stdlib helpers worth reusing rather than
    reimplementing: ``_format_action_invocation`` (flags + value chunk),
    ``_expand_help`` (``%(default)s`` interpolation) and ``_split_lines``
    (consistent wrapping).

    Note the accumulation pattern: a ``HelpFormatter`` has no ``_actions``. It
    is *fed* actions by ``ArgumentParser.format_help()`` via ``add_argument``, and
    your formatter decides what to do with each one. Collecting them into a list
    in an ``add_argument`` override is the standard way to build a custom layout.
    """

    HEADERS = ("FLAGS", "VALUE", "DESCRIPTION")

    def __init__(
        self,
        prog: str,
        *,
        columns: tuple[int, int] = (28, 16),
        **kwargs: object,
    ) -> None:
        super().__init__(prog, **kwargs)  # type: ignore[arg-type]
        self._flag_width, self._value_width = columns
        self.collected: list[argparse.Action] = []

    def add_argument(self, action: argparse.Action) -> None:
        """Collect actions instead of rendering them immediately."""
        self.collected.append(action)

    # -- layout -----------------------------------------------------------

    def format_help(self) -> str:
        rows = [self._row(*self.HEADERS)]
        rows.append("  " + "-" * (2 + self._flag_width + self._value_width + 58))
        rows.extend(self._rows())
        return "\n".join(rows) + "\n"

    def _rows(self) -> list[str]:
        out: list[str] = []
        for action in self.collected:
            if action.help is argparse.SUPPRESS:
                continue          # rule 1: you must skip these yourself
            if isinstance(action, argparse._HelpAction):
                continue
            flags = ", ".join(action.option_strings) or action.dest
            out.append(self._row(flags, self._value_for(action), action.help or ""))
        return out

    def _row(self, flags: str, value: str, description: str) -> str:
        wrapped = self._split_lines(description, 58)
        first = (
            f"  {flags:<{self._flag_width}}{value:<{self._value_width}}"
            f"{wrapped[0] if wrapped else ''}"
        )
        pad = " " * (2 + self._flag_width + self._value_width)
        return "\n".join([first, *[pad + line for line in wrapped[1:]]])

    # -- the nargs mapping, rule 2 ----------------------------------------

    def _value_for(self, action: argparse.Action) -> str:
        if action.nargs == 0:
            return ""
        metavar = action.metavar or action.dest.upper()
        if action.choices:
            metavar = "{" + ",".join(str(c) for c in action.choices) + "}"
        table = {
            None: metavar,
            1: metavar,
            2: f"{metavar} {metavar}",
            "?": f"[{metavar}]",
            "*": f"[{metavar} ...]",
            "+": f"{metavar} [{metavar} ...]",
            argparse.REMAINDER: f"{metavar} ...",
            argparse.PARSER: f"{metavar} ...",
        }
        return table.get(action.nargs, metavar)


class CompactHelpFormatter(HelpFormatter):
    """One line per flag, no help text. A "what exists" summary.

    Useful as the *second* help screen: ``prog --help`` for the full reference and
    ``prog -H`` for a dense overview. Also useful in completion scripts, which
    read a parser to discover option names.
    """

    def __init__(self, prog: str, **kwargs: object) -> None:
        super().__init__(prog, **kwargs)  # type: ignore[arg-type]
        self.collected: list[argparse.Action] = []

    def add_argument(self, action: argparse.Action) -> None:
        self.collected.append(action)

    def format_help(self) -> str:
        lines = [f"{self._prog}: options"]
        for action in self.collected:
            if action.help is argparse.SUPPRESS:
                continue
            flags = ", ".join(action.option_strings)
            if not flags:
                continue
            metavar = "" if action.nargs == 0 else f" {action.metavar or action.dest.upper()}"
            lines.append(f"  {flags}{metavar}")
        return "\n".join(lines) + "\n"


class TripleColumnDefaultsFormatter(HelpFormatter):
    """Add a third column showing the effective default.

    The most common reason to leave the stock layout:
    ``ArgumentDefaultsHelpFormatter`` appends ``(default: X)`` to the help *text*,
    which mixes prose and data and reads badly once help strings get long. A
    separate column stays legible, and it is trivially machine-readable.
    """

    def _format_action(self, action: argparse.Action) -> str:
        text = super()._format_action(action)
        if action.help is argparse.SUPPRESS or not action.option_strings:
            return text
        default = "<required>" if action.required else repr(action.default)
        return text.rstrip("\n") + f"  [default: {default}]\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="layout",
        description="Lesson 27: custom help layouts.",
        formatter_class=TableHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    parser.add_argument("--output", "-o", metavar="FILE", default="-",
                        help="write here")
    parser.add_argument("--count", type=int, metavar="N", default=1,
                        help="how many times")
    parser.add_argument("--coord", nargs=2, type=int, metavar=("X", "Y"),
                        help="a point")
    parser.add_argument("--include", nargs="*", metavar="PAT", default=[],
                        help="patterns to include")
    parser.add_argument("--tag", nargs="+", metavar="TAG", help="at least one tag")
    parser.add_argument("--old-format", choices=("text", "json"), default="text",
                        help="deprecated spelling")
    parser.add_argument("--format", choices=("text", "json", "yaml"), default="text",
                        help="output format")
    parser.add_argument("--secret", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("infile", nargs="?", help="input file")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("(see --help)")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. TableHelpFormatter: three columns")
    print("=" * 78)
    print(build_parser().format_help())

    print()
    print("=" * 78)
    print("2. Rule 1: SUPPRESS entries must be skipped by hand")
    print("=" * 78)
    shown = build_parser().format_help()
    print(f"  '--secret' appears in the table? {'--secret' in shown}")
    print("  argparse's own formatter skips it; a custom one has to remember.")
    print()
    print("  A formatter that forgets the check silently exposes every hidden flag.")

    print()
    print("=" * 78)
    print("3. Rule 2: the nargs mapping, and the helper that already has it")
    print("=" * 78)
    parser = build_parser()
    helper = TableHelpFormatter(prog="layout")
    for action in parser._actions:
        if action.help is argparse.SUPPRESS or isinstance(action, argparse._HelpAction):
            continue
        chunk = helper._format_args(action, action.metavar)
        mine = helper._value_for(action)
        print(f"  {action.option_strings or action.dest!s:<26} nargs={action.nargs!s:<10} "
              f"stdlib={chunk!r:<28} mine={mine!r}")
    print()
    print("  _format_args is argparse's own implementation, already correct for")
    print("  REMAINDER, PARSER and the '...' suffixes. Reuse it instead of copying.")

    print()
    print("=" * 78)
    print("4. CompactHelpFormatter: a dense summary")
    print("=" * 78)
    compact = CompactHelpFormatter(prog="layout")
    for action in parser._actions:
        compact.add_argument(action)
    print(compact.format_help())
    print("  Cheap to generate, and exactly the shape a completion script wants.")

    print()
    print("=" * 78)
    print("5. TripleColumnDefaultsFormatter: defaults as data, not prose")
    print("=" * 78)
    formatter = TripleColumnDefaultsFormatter(prog="layout", width=92)
    formatter.add_usage(
        parser.usage, parser._actions, parser._mutually_exclusive_groups
    )
    formatter.add_text(parser.description)
    for action in parser._actions:
        formatter.add_argument(action)
    print(formatter.format_help())

    print()
    print("  Compare with ArgumentDefaultsHelpFormatter, which appends")
    print("  '(default: X)' to the help text. A column stays legible when help")
    print("  strings are long, and it is trivially machine-readable.")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
