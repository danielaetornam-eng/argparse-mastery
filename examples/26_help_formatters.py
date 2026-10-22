"""Lesson 26 - HelpFormatter subclasses, and composing them.

Run me
------
    python examples/26_help_formatters.py --help
    python examples/26_help_formatters.py --demo

How help output is assembled
----------------------------
``ArgumentParser.format_help()`` creates a :class:`argparse.HelpFormatter` and
feeds it, in this order:

1. ``add_usage(usage, actions, groups, prefix)``
2. ``add_text(description)``
3. ``add_argument(action)`` for every action, ``add_arguments(group)`` per group
4. ``add_text(epilog)``

Each step calls a ``_format_*`` method you can override:

===================================  ===========================================
Method                               Controls
===================================  ===========================================
``_format_usage``                     the ``usage:`` line
``_format_text``                      description and epilog rendering
``_fill_text``                        the actual line-splitting for step 2/4
``_format_action``                    one ``--flag  help`` row
``_get_help_string``                  the help text for a single action
``_expand_help``                      ``%(default)s`` interpolation
``_format_args``                      the ``[-v VALUE]`` chunk inside usage
``_get_default_metavar_for_*``        default metavar names
``_split_lines``                      general text wrapping
===================================  ===========================================

The key compositional fact: **different formatters override different methods.**
That is why combining two behaviours is often just multiple inheritance -- see
:func:`combined_formatter`.

The five stock formatters
-------------------------
======================================  ====================================
Class                                    Behaviour
======================================  ====================================
``HelpFormatter``                        the default
``ArgumentDefaultsHelpFormatter``        appends ``(default: X)``
``RawDescriptionHelpFormatter``          does not re-wrap description
``RawTextHelpFormatter``                 does not re-wrap help either
``MetavarTypeHelpFormatter``             metavar defaults to ``type.__name__``
======================================  ====================================

``ArgumentDefaultsHelpFormatter`` is the single highest-value one: a user should
never have to run ``--help`` twice to discover that ``--retries`` defaults to 3.
Use it (or a subclass of it) on anything you ship.

Composing formatters
--------------------
Because the behaviours live in different methods, a left-to-right MRO chain
composes them::

    class Help(combined_formatter(
        ShortMetavarHelpFormatter,                     # _get_default_metavar_*
        argparse.ArgumentDefaultsHelpFormatter,        # _get_help_string
        argparse.RawDescriptionHelpFormatter,          # _fill_text
    )):
        pass

**Leftmost wins**, which is counter-intuitive. Put the most opinionated class
first.

RawDescription vs RawText
-------------------------
``RawDescriptionHelpFormatter`` overrides ``_fill_text``, affecting
``description`` and ``epilog`` only. ``RawTextHelpFormatter`` also overrides
``_split_lines``, affecting ``help=`` strings too -- which destroys column
alignment. Almost always you want the former.

Width: pin it in tests
----------------------
``HelpFormatter(prog, indent_increment=2, max_help_position=24, width=None,
color=True)``. With ``width=None`` argparse asks the terminal, so ``--help``
output differs between a pipe and a terminal and between machines. For golden-file
tests, construct the formatter with an explicit width::

    formatter = parser.formatter_class(prog=parser.prog, width=80)
    text = parser.format_help(formatter)

Python 3.14: colour
------------------
3.14 added ``color=True`` to both ``ArgumentParser`` and ``HelpFormatter``.
Colour is applied automatically and auto-disabled when the stream is not a TTY,
when ``NO_COLOR`` is set, when ``PYTHON_COLORS=0``, when ``TERM=dumb``, and on
Windows terminals without virtual-terminal support. ``FORCE_COLOR=1`` forces it
on, which is how you inspect the ANSI codes::

    FORCE_COLOR=1 python prog.py --help | cat -v
"""

from __future__ import annotations

import argparse
import sys
from argparse import (
    ArgumentDefaultsHelpFormatter,
    HelpFormatter,
    RawDescriptionHelpFormatter,
)


def combined_formatter(*classes: type[HelpFormatter]) -> type[HelpFormatter]:
    """Compose formatter classes left to right. **Leftmost wins.**

    argparse has no multiple-inheritance helper, but a plain MRO chain is enough
    because the behaviours live in different methods. Put the most opinionated
    class first.
    """

    class _Combined(*classes):  # type: ignore[misc]
        """Dynamically composed. See :func:`combined_formatter`."""

    return _Combined


class SectionedHelpFormatter(HelpFormatter):
    """Insert a horizontal rule before each argument-group heading.

    The smallest useful override that produces a noticeably better result:
    group headings already exist, but consecutive groups with the same title, or a
    group with no title, run together. A box-drawing rule separates them and stays
    legible in monochrome terminals and in pasted issues.
    """

    SEPARATOR = "  " + "\u2500" * 52

    def _format_action(self, action: argparse.Action) -> str:
        text = super()._format_action(action)
        # A heading row has help text but no option strings and no type -- that is
        # how argparse renders group titles. Skip -h and --version.
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            return text
        if text.startswith("  ") and not action.option_strings and action.help:
            return f"{self.SEPARATOR}\n{text}"
        return text


class ExamplesFormatter(ArgumentDefaultsHelpFormatter):
    """Defaults shown, description and epilog un-wrapped.

    A one-method demonstration of composition: ``ArgumentDefaultsHelpFormatter``
    overrides ``_get_help_string`` and ``RawDescriptionHelpFormatter`` overrides
    ``_fill_text``. Different methods, so a single subclass can have both.

    In production, prefer ``combined_formatter(ExamplesFormatter,
    RawDescriptionHelpFormatter)`` so the pieces stay independently recognisable.
    """

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        return "".join(indent + line for line in text.splitlines(keepends=True))


class ShortMetavarHelpFormatter(HelpFormatter):
    """Use ``type.__name__`` as the default metavar.

    ``MetavarTypeHelpFormatter`` does this for optionals only, and can surprise on
    positionals. This covers both.

    **Read the caveat before using it:** a metavar named after a type makes error
    messages worse. ``argument --port: invalid int value: 'http'`` becomes
    ``argument --port: expected one argument``. Prefer explicit ``metavar=`` on
    the arguments where the name and type differ, and use this class only when
    they genuinely coincide.
    """

    def _get_default_metavar_for_optional(self, action: argparse.Action) -> str:
        return self._name(action)

    def _get_default_metavar_for_positional(self, action: argparse.Action) -> str:
        return self._name(action)

    @staticmethod
    def _name(action: argparse.Action) -> str:
        if action.type is None:
            return action.dest.upper()
        return getattr(action.type, "__name__", None) or str(action.type)


def build_parser(formatter: type[HelpFormatter] = ExamplesFormatter) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fmt",
        description=(
            "A description that is long enough to show re-wrapping.\n"
            "The second line starts immediately after the first, and with the "
            "default formatter it is folded into a single paragraph that wraps to "
            "the terminal width instead."
        ),
        epilog=(
            "Examples:\n"
            "  fmt --retries 5 --timeout 30\n"
            "  fmt --tag a --tag b\n"
            "\n"
            "This epilog keeps its line breaks because the formatter does not\n"
            "re-wrap free text."
        ),
        formatter_class=formatter,
    )
    # HelpFormatter.__init__ already sets width from the terminal when width=None;
    # our explicit width wins because it is passed through kwargs.
    parser.add_argument("--retries", type=int, default=3,
                        help="how many times to retry")
    parser.add_argument("--timeout", type=int, default=30, metavar="SECONDS",
                        help="per-request timeout")
    parser.add_argument("--tag", default="", help="a tag; repeatable in practice")
    parser.add_argument("--ratio", type=float, default=0.5,
                        help="sampling ratio")

    group = parser.add_argument_group("advanced")
    group.add_argument("--json", action="store_true", help="JSON output")
    group.add_argument("--dry-run", action="store_true", help="change nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("(see --help)")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. Default HelpFormatter: description re-wrapped, no defaults shown")
    print("=" * 78)
    print(build_parser(HelpFormatter).format_help())

    print()
    print("=" * 78)
    print("2. composed(Defaults, RawDescription): both behaviours")
    print("=" * 78)
    Both = combined_formatter(
        ExamplesFormatter,
        RawDescriptionHelpFormatter,
    )
    print(build_parser(Both).format_help())

    print()
    print("  Leftmost wins. Try swapping the order and the description re-wraps.")
    print("  The reason order matters is the MRO, not any argparse logic.")

    print()
    print("=" * 78)
    print("3. SectionedHelpFormatter: rules between groups")
    print("=" * 78)
    print(build_parser(SectionedHelpFormatter).format_help())

    print()
    print("=" * 78)
    print("4. ShortMetavarHelpFormatter -- and why it can hurt")
    print("=" * 78)
    print(build_parser(ShortMetavarHelpFormatter).format_help())
    print()
    plain = argparse.ArgumentParser(prog="fmt", add_help=False)
    plain.add_argument("--port", type=int)
    typed = argparse.ArgumentParser(
        prog="fmt", add_help=False, formatter_class=ShortMetavarHelpFormatter
    )
    typed.add_argument("--port", type=int)
    print("  usage lines:")
    print(f"    default : {plain.format_usage().strip()}")
    print(f"    typed   : {typed.format_usage().strip()}")
    print("  error messages for the same mistake:")
    print(f"    default : {_err(plain, ['--port', 'http'])}")
    print(f"    typed   : {_err(typed, ['--port', 'http'])}")
    print()
    print("  The typed version's message is strictly worse. Prefer explicit")
    print("  metavar= on the arguments where the two differ.")

    print()
    print("=" * 78)
    print("5. RawDescriptionHelpFormatter vs RawTextHelpFormatter")
    print("=" * 78)
    raw_desc = argparse.ArgumentParser(
        prog="fmt", add_help=False,
        formatter_class=RawDescriptionHelpFormatter,
        description="line one\nline two",
    )
    raw_text = argparse.ArgumentParser(
        prog="fmt", add_help=False,
        formatter_class=argparse.RawTextHelpFormatter,
        description="line one\nline two",
    )
    for label, p in (("RawDescription", raw_desc), ("RawText", raw_text)):
        print(f"  {label:<16} description: {p.description.splitlines()!r}")
    print()
    print("  RawDescription overrides _fill_text (description/epilog only).")
    print("  RawText also overrides _split_lines, which also affects help strings")
    print("  and destroys column alignment. You almost always want the former.")

    print()
    print("=" * 78)
    print("6. Pin the width for deterministic help output")
    print("=" * 78)
    parser = build_parser()
    for width in (60, 100):
        text = render_help(parser, width=width)
        longest = max(len(line) for line in text.splitlines())
        print(f"  width={width:<4} longest line={longest}")
    print()
    print("  With width=None the output depends on the terminal, so golden-file")
    print("  tests must render help themselves -- see render_help() above and")
    print("  argparse_mastery.testing.help_snapshot.")

    return 0


def render_help(parser: argparse.ArgumentParser, *, width: int | None = 80) -> str:
    """Render ``--help`` with an explicitly-constructed formatter.

    ``ArgumentParser.format_help()`` takes no arguments: it creates its own
    formatter with ``width=None``, which means "ask the terminal". To control the
    width -- for a golden-file test, a narrow CI pane, or a generated man page --
    you must drive the formatter yourself, in the same order ``format_help`` does:

    1. ``add_usage(usage, actions, groups, prefix)``
    2. ``add_text(description)``
    3. ``add_argument`` for each action
    4. ``add_text(epilog)``

    Five lines that unlock deterministic help output. Worth knowing: several
    private attributes are being touched, which is why it is better kept in one
    helper than spread across a test suite.
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


def _err(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
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
