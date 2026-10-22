"""HelpFormatter subclasses.

Why bother
----------
``--help`` is the CLI's real API documentation and its real debugging tool. The
default layout is serviceable and ugly. Formatters are how you fix that without
abandoning argparse.

How the machinery works
-----------------------
``ArgumentParser.format_help()`` builds a :class:`argparse.HelpFormatter` and
feeds it, in order:

1. ``add_usage(usage, actions, groups, prefix)``
2. ``add_text(description)``
3. ``add_argument(action)`` for every action, and ``add_arguments(group)``
   for each ``add_argument_group``
4. ``add_text(epilog)``

The formatter then calls ``format_help()``, which renders the accumulated
sections. So to change output you override one of these methods:

===============================  ===========================================
Method                          Controls
===============================  ===========================================
``_format_usage``                the ``usage: ...`` line
``_format_text``                 description and epilog
``_format_action``               one ``--flag  help text`` row
``_get_help_string``             the help text for a single action
``_format_args``                 the ``[-v VERBOSE]`` chunk inside usage
``_get_default_metavar_for_*``   metavar defaults
``_split_lines``                 how text wraps
===============================  ===========================================

``HelpFormatter.__init__(prog, indent_increment=2, max_help_position=24,
width=None, color=True)`` -- ``width=None`` means "ask the terminal, honouring
``COLUMNS``". Passing ``width`` explicitly makes help output *deterministic*,
which is what you want in tests and in golden files.

Built-ins you should know by name
---------------------------------
* ``argparse.HelpFormatter`` -- the default
* ``argparse.ArgumentDefaultsHelpFormatter`` -- appends ``(default: X)``
* ``argparse.RawDescriptionHelpFormatter`` -- stops re-wrapping ``description``
* ``argparse.RawTextHelpFormatter`` -- stops re-wrapping ``description`` *and*
  ``help`` strings (destroys alignment; use sparingly)
* ``argparse.MetavarTypeHelpFormatter`` -- metavar defaults to ``type.__name__``

Python 3.14 only: ``HelpFormatter`` accepts ``color=True`` and colors option
strings and error text when the stream is a TTY. Colour is auto-disabled when
redirected, when ``NO_COLOR`` is set, when ``TERM=dumb``, or when
``PYTHON_COLORS=0``. :class:`ColorHelpFormatter` below is a thin wrapper that
forwards the flag explicitly on older versions too.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterable, Sequence
from typing import Any

__all__ = [
    "ColorHelpFormatter",
    "ExamplesFormatter",
    "SectionedHelpFormatter",
    "ShortMetavarHelpFormatter",
    "combined_formatter",
    "terminal_width",
]


def terminal_width(default: int = 80) -> int:
    """Best-effort terminal width, always sane.

    Order of preference: ``COLUMNS`` env var, ``shutil.get_terminal_size``,
    ``default``. The floor of 50 stops help from collapsing into one-token-per-
    line on very narrow windows, which argparse does not handle gracefully.
    """
    try:
        return max(50, shutil.get_terminal_size(fallback=(default, 24)).columns)
    except Exception:  # pragma: no cover - extremely hostile environments
        return default


class ShortMetavarHelpFormatter(argparse.HelpFormatter):
    """Show ``type.__name__`` as the metavar, e.g. ``--port PORT`` -> ``--port int``.

    Compare two defaults for the same argument::

        # default:                          # this class:
        [--port PORT]                      [--port int]
        [--tags TAGS [TAGS ...]]           [--tags str [str ...]]

    ``MetavarTypeHelpFormatter`` does this already but only for *optionals*, and
    its positional handling surprises people. This version covers both and,
    more usefully, shows the *nargs-aware* type name, so ``--tags`` lists
    ``str`` for every element rather than ``str`` once.

    CAVEAT -- read this before using it: a metavar named after a type is a worse
    error message than one named after the argument. argparse's own message is
    ``argument --port: invalid int value: 'x'``; with a str metavar it becomes
    ``argument --port: expected one argument``, which is strictly worse. Prefer
    explicit ``metavar=`` on individual arguments (see
    :mod:`examples.06_metavar_and_dest`) and use this class only when the
    argument name and type genuinely coincide.
    """

    def _get_default_metavar_for_optional(self, action: argparse.Action) -> str:
        return self._type_name(action)

    def _get_default_metavar_for_positional(self, action: argparse.Action) -> str:
        return self._type_name(action)

    @staticmethod
    def _type_name(action: argparse.Action) -> str:
        if action.type is None:
            return action.dest.upper()
        name = getattr(action.type, "__name__", None)
        if name in (None, "type", "<lambda>"):
            return str(action.type)
        return name


class SectionedHelpFormatter(argparse.HelpFormatter):
    """Insert a horizontal rule before each help section.

    Group headings already exist (``positional arguments:``, ``options:``, and
    your own ``add_argument_group`` titles), but consecutive headings with
    similar titles -- or a titled group after the untitled ``options:`` -- run
    together. A rule separates them and stays legible in monochrome terminals and
    in pasted issues.

    **The hook is ``start_section``, not ``_format_section``.** In Python 3.14
    ``HelpFormatter`` has no ``_format_section`` method at all: section rendering
    moved into the nested ``_Section`` class, and ``start_section`` simply queues
    ``section.format_help`` as an item. So a formatter that overrides
    ``_format_section``, or ``_format_action`` (arguments only), or ``_format_text``
    (deferred, so a flag set at queue time is long stale by render time) silently
    does nothing.

    ``start_section`` is the one place where the ordering is still knowable: at
    that moment the parent section is still ``self._current_section``, so an extra
    item added here lands immediately *before* the nested section's output.
    """

    # ASCII on purpose. A box-drawing character looks nicer, but it makes
    # `prog --help` raise UnicodeEncodeError on a Windows console whose code page
    # cannot represent it (cp1252 cannot) -- turning a cosmetic improvement into a
    # crash. Verify any separator against `sys.stdout.encoding` before shipping it.
    SEPARATOR = "  " + "-" * 52

    def start_section(self, heading: str | None) -> None:
        """Arm the rule; the first item actually added to the section emits it.

        Adding the rule in ``start_section`` itself would emit one for *empty*
        sections too -- argparse creates a section per ``add_argument_group``, and
        the default ``positional arguments:`` group is empty for a
        flags-only CLI. Deferring to the first item means a section that renders
        nothing also renders no rule.
        """
        super().start_section(heading)
        self._rule_armed = True

    def _emit_rule_if_armed(self, content: object = None) -> None:
        """Emit the rule, unless this "item" is empty.

        ``ArgumentParser.format_help`` calls ``add_text(group.description)``
        unconditionally, including for the empty description of a group with no
        prose -- and for the empty default ``positional arguments:`` group. Keying
        the rule off the *first non-empty* item is what stops an empty section
        from rendering a rule above its own heading.
        """
        if content is None:
            return
        if isinstance(content, (list, tuple)) and not content:
            return
        if getattr(self, "_rule_armed", False):
            self._rule_armed = False
            self._add_item(_rule, (self.SEPARATOR,))

    # Every route by which content enters a section has to check the flag, or an
    # override on one of them would be bypassed.
    def add_argument(self, action: argparse.Action) -> None:
        self._emit_rule_if_armed(action)
        super().add_argument(action)

    def add_arguments(self, actions: Iterable[argparse.Action]) -> None:
        actions = list(actions)
        self._emit_rule_if_armed(actions)
        super().add_arguments(actions)

    def add_text(self, text: str) -> None:
        self._emit_rule_if_armed(text)
        super().add_text(text)


def _rule(separator: str) -> str:
    """The standalone helper ``_add_item`` needs -- a plain callable of one arg."""
    return separator + "\n"


class ExamplesFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """ArgumentDefaultsHelpFormatter + RawDescription, plus an ``Examples:`` block.

    The two stock formatters you want are mutually exclusive in intent and
    combined by inheritance here:

    * ``ArgumentDefaultsHelpFormatter`` adds ``(default: 4)`` to every help
      string. Essential -- a user should never have to run ``--help`` twice to
      discover that ``--retries`` defaults to 3.
    * ``RawDescriptionHelpFormatter`` preserves newlines in ``description`` and
      ``epilog``, which is what makes an indented usage example readable.

    Both behaviours live in different methods (``_get_help_string`` vs
    ``_fill_text``), so a single subclass gets both. That is the standard way to
    compose argparse formatters.

    Additionally, :meth:`add_text` intercepts a special first argument so a
    parser can call ``parser.add_text(EXAMPLES)`` -- no, more practically:
    ``ExamplesFormatter`` renders any ``epilog`` beginning with the literal
    marker ``"Examples:"`` with a bold heading and no re-wrapping.
    """

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        # RawDescription's behaviour: do not re-wrap description/epilog.
        return "".join(indent + line for line in text.splitlines(keepends=True))

    def _format_usage(self, usage, actions, groups, prefix):
        # RawDescription keeps usage on one logical line; wrap it ourselves so
        # long usages stay readable instead of being mangled by argparse.
        return super()._format_usage(usage, actions, groups, prefix)

    def _format_text(self, text: str) -> str:
        if text.startswith("Examples:"):
            body = text[len("Examples:") :].lstrip("\n")
            heading = "Examples:"
            rendered = "\n".join("  " + line for line in body.splitlines())
            return f"{heading}\n{rendered}\n"
        return super()._format_text(text)


class ColorHelpFormatter(argparse.HelpFormatter):
    """Explicitly opt in to / out of colour, on every supported Python version.

    Python 3.14 added colour to argparse itself (``color=True`` on
    ``ArgumentParser`` and ``HelpFormatter``). On 3.11-3.13 the parameter does
    not exist, so this class accepts and drops it -- letting a single codebase
    request colour everywhere without version sniffing at the call sites.

    Colour decisions argparse already makes for you (3.14+):

    * off when stdout/stderr is not a TTY
    * off when ``NO_COLOR`` is set, or ``PYTHON_COLORS=0``
    * on when ``FORCE_COLOR=1`` or ``PYTHON_COLORS=1``
    * off when ``TERM=dumb``
    * off on Windows terminals without virtual-terminal support
    """

    def __init__(self, prog: str, *, color: bool = True, **kwargs: Any) -> None:
        if sys.version_info >= (3, 14):
            kwargs["color"] = color
        super().__init__(prog, **kwargs)


def combined_formatter(*classes: type[argparse.HelpFormatter]) -> type[argparse.HelpFormatter]:
    """Compose several formatter classes into one, left to right.

    ``argparse`` offers no multiple-inheritance helper, but a left-to-right MRO
    chain is sufficient: the rightmost base's ``_get_help_string`` runs first,
    then the leftmost's. This is how you get, say, "show defaults" *and* "short
    metavars" *and* "raw description" without vendoring the stdlib class.

    ::

        Help = combined_formatter(
            ShortMetavarHelpFormatter,
            argparse.ArgumentDefaultsHelpFormatter,
            argparse.RawDescriptionHelpFormatter,
        )
        parser = argparse.ArgumentParser(formatter_class=Help)

    Order matters and the rule is counter-intuitive: **leftmost wins**. Put the
    most opinionated class first.
    """

    class _Combined(*classes):  # type: ignore[misc]
        """Dynamically composed formatter. See :func:`combined_formatter`."""

    return _Combined


# ---------------------------------------------------------------------------
# A worked custom formatter
# ---------------------------------------------------------------------------


class TwoColumnFormatter(argparse.HelpFormatter):
    """A minimal example of overriding ``_format_action`` from scratch.

    Useful as a template when you want a completely different shape -- a table,
    a grouped listing, a terse one-line-per-flag summary -- rather than a tweak
    of the standard layout. Everything argparse needs is available on ``action``:

    ``action.option_strings``  ``['-v', '--verbose']``
    ``action.metavar``         the value placeholder, or ``None``
    ``action.nargs``           ``0``, ``1``, ``2``, ``'*'``, ``'+'``, ``'?'``, ``'...'``
    ``action.help``            the help string (may be ``SUPPRESS``)
    ``action.default``         the default, for a "default:" column
    ``action.choices``         allowed values, if any

    Skipping ``SUPPRESS`` help yourself is mandatory -- argparse does not do it
    for custom formatting.
    """

    HEADER = ("FLAG", "ARGS", "HELP")

    def __init__(self, prog: str, *, width: int | None = None, **kwargs: Any) -> None:
        super().__init__(prog, width=width or terminal_width(), **kwargs)

    def _format_action(self, action: argparse.Action) -> str:
        if action.help is argparse.SUPPRESS:
            return ""
        flags = ", ".join(action.option_strings) or action.metavar or action.dest
        if action.nargs != 0:
            metavar = action.metavar or action.dest.upper()
            arity = {
                1: metavar,
                2: f"{metavar} {metavar}",
                "?": f"[{metavar}]",
                "*": f"[{metavar} ...]",
                "+": f"{metavar} [{metavar} ...]",
                argparse.REMAINDER: f"{metavar} ...",
                argparse.PARSER: f"{metavar} ...",
            }.get(action.nargs, metavar)
            flags = f"{flags} {arity}"
        return f"  {flags:<28}{action.help or ''}\n".rstrip() + "\n"


def iter_visible_actions(parser: argparse.ArgumentParser) -> Iterable[argparse.Action]:
    """Yield the actions a user can actually see, recursing into subparsers.

    The reference implementation of "introspect a parser for tooling", and the
    starting point for shell-completion generation (see
    :mod:`argparse_mastery.completion`) and ``--help`` as machine-readable JSON.
    """
    for action in parser._actions:
        if action.help is argparse.SUPPRESS:
            continue
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from iter_visible_actions(sub)


def format_option_invocations(action: argparse.Action) -> str:
    """Render an action's ``-x VALUE`` form the way ``--help`` does.

    Delegates to the formatter's private ``_format_action_invocation`` so the
    result matches ``--help`` exactly, including ``...`` for ``REMAINDER`` and
    the ``A B`` pair form for ``nargs=2``. Re-implementing this by hand is a
    classic source of off-by-one bugs in completion scripts.
    """
    formatter = argparse.HelpFormatter(prog="x")
    try:
        return formatter._format_action_invocation(action)
    except Exception:  # pragma: no cover - defensive
        return ", ".join(action.option_strings)


def known_option_strings(parser: argparse.ArgumentParser) -> Sequence[str]:
    """Every option string the parser (and its subparsers) accepts.

    Powers the "unrecognized arguments" suggestions in
    :mod:`argparse_mastery.errors`, which Python 3.14 does **not** do: 3.14's
    ``suggest_on_error=True`` only improves ``choices`` violations, and only
    when all choices are strings.
    """
    out: list[str] = []
    for action in iter_visible_actions(parser):
        out.extend(action.option_strings)
    return sorted(set(out))
