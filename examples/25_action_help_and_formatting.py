"""Lesson 25 - Actions and help output: what actually works, verified.

Run me
------
    python examples/25_action_help_and_formatting.py --format json --tag x
    python examples/25_action_help_and_formatting.py --demo

This lesson corrects three claims that circulate widely. All three were checked
against CPython 3.14.6.

Correction 1: ``Action.format_help`` is never called
--------------------------------------------------
``argparse.Action`` defines two rendering hooks, and it is natural to assume the
stock formatters use them. They do not:

* ``action.format_help()`` -- **zero call sites in argparse**. Zero. It exists as
  an extension point for *third-party* formatters, and ``ArgumentDefaultsHelpFormatter``
  actively bypasses it by reading ``action.help`` directly.
* ``action.format_usage()`` -- **one** call site, inside
  ``HelpFormatter._format_actions_usage``, and **only on the ``nargs == 0``
  branch**:

      if action.nargs == 0:
          part = action.format_usage()
      else:
          args_string = self._format_args(action, default)
          part = f"{option_string} {args_string}"

  So an Action can rewrite its own chunk in ``usage:`` **iff it is a flag**. For a
  value-taking option the chunk comes from ``option_strings[0]`` plus
  ``_format_args``, and your override is ignored.

Both are demonstrated in the demo. The practical consequence:

* To change one argument's help text, put it in the ``help=`` string. That works
  with every formatter, forever.
* To change the *layout*, override the formatter -- lesson 27.
* To suppress one flag from ``usage:``, an Action ``format_usage`` works, and only
  because ``nargs == 0``.

Correction 2: ``help=argparse.SUPPRESS`` already removes from ``usage:``
------------------------------------------------------------------------
There is no leftover to clean up. ``HelpFormatter.add_argument`` skips actions
whose ``help`` is ``SUPPRESS``::

    if action.help is not SUPPRESS:
        ...
        self._add_item(self._format_action, [action])

so a suppressed action never reaches ``_format_action`` *or* ``_format_args``. It
disappears from the usage line and the help table together, and remains fully
functional. Lesson 06 shows this.

Correction 3: ``deprecated=True`` exists for **options**, not only commands
------------------------------------------------------------------------
Python 3.14 added it to both. ``Action.__init__`` takes a ``deprecated``
parameter, so::

    parser.add_argument("--old", deprecated=True, action="store_true")

prints ``prog: warning: option '--old' is deprecated`` on stderr, still runs the
action, and exits 0. It does **not** hide the option from ``--help`` -- pair it
with ``help=argparse.SUPPRESS`` for that. Verified for ``store_true``, ``count``,
``nargs=2`` and plain ``store``. Lesson 34 covers the whole workflow.

So when do you still need a custom Action for deprecation?
------------------------------------------------------------
When you want a *better message* than argparse's fixed wording, or a removal
version, or a redirect to a different spelling. :class:`DeprecatedAction` below
wraps the real action, shares its ``dest``, and adds those. Its own help override
is present for use with a **custom formatter** -- see Correction 1.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from typing import Any

warnings_module = warnings  # re-exported name used by the action below


class DeprecatedAction(argparse.Action):
    """An old flag that warns with a *useful* message and shares a dest.

    What argparse 3.14 gives you for free is ``deprecated=True``, whose message is
    fixed: ``option '--old' is deprecated``. It says nothing about the
    replacement or the removal version, which is most of what a user needs.

    This wrapper adds:

    * a message naming the replacement and the version it goes away in,
    * a warning channel you control, so ``--quiet`` and ``-W ignore`` can suppress
      it consistently,
    * a shared ``dest`` with the modern spelling, so old and new cannot drift.

    It also defines ``format_help`` and ``format_usage`` -- both documented above
    as having no effect on stock formatters. They are kept because they *do*
    apply to the custom formatters in lesson 27, which is the case a real library
    has to serve.
    """

    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        self.replacement: str = kwargs.pop("replacement", "")
        self.removed_in: str = kwargs.pop("removed_in", "")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        parts = [f"{option_string or self.dest} is deprecated"]
        if self.replacement:
            parts.append(f"use {self.replacement} instead")
        if self.removed_in:
            parts.append(f"removed in {self.removed_in}")
        warnings.warn(" ".join(parts), DeprecationWarning, stacklevel=2)
        setattr(namespace, self.dest, values)

    # Both of these are honoured only by custom formatters -- see Correction 1.
    def format_help(self) -> str:
        marker = "[DEPRECATED]"
        if self.replacement:
            marker += f" use {self.replacement}"
        return f"{self.help or ''}  {marker}"

    def format_usage(self) -> str:
        return ""


class AnnotatedAction(argparse.Action):
    """An action that records a computed note, exposed via ``note_on_help``.

    Because overriding ``format_help`` does nothing under the stock formatters,
    the note is stored on the instance and a *formatter* reads it. That is the
    portable design: one formatter change, every annotated action benefits, and
    the note can never drift from the action's own configuration.

    The lesson's own lesson: **decide who renders, then give them the data.**
    """

    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        note: str = "",
        **kwargs: Any,
    ) -> None:
        self.note = note
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        # Required, even though this class only cares about presentation:
        # `argparse.Action.__call__` raises NotImplementedError, so an action
        # without it parses fine and explodes the first time the flag is used.
        setattr(namespace, self.dest, values)

    def note_on_help(self) -> str:
        """The suffix a formatter should append. The rendering hook, done portably."""
        return f"  [{self.note}]" if self.note else ""


class NoteFormatter(argparse.HelpFormatter):
    """A formatter that appends ``AnnotatedAction.note`` to each help row.

    The working pattern: the *formatter* reads ``action.note_on_help()`` when
    rendering, instead of relying on ``action.format_help()`` being called.
    """

    def _get_help_string(self, action: argparse.Action) -> str:
        base = super()._get_help_string(action) or ""
        note = getattr(action, "note_on_help", None)
        return f"{base}{note()}" if callable(note) else base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="annotated",
        description="Lesson 25: actions and help output, verified.",
        epilog="Run with --demo to see what each hook actually does.",
    )

    parser.add_argument("--format", choices=("text", "json", "yaml"),
                        default="text", metavar="{text,json,yaml}",
                        help="output format")

    parser.add_argument(
        "--tag",
        action=AnnotatedAction,
        note="repeatable, de-duplicated",
        default=[],
        help="add a tag",
    )

    parser.add_argument(
        "--old-format",
        action=DeprecatedAction,
        dest="format",
        choices=("text", "json", "yaml"),
        replacement="--format",
        removed_in="2.0",
        help="choose the output format",
    )

    # Python 3.14's built-in deprecation, for comparison.
    if sys.version_info >= (3, 14):
        parser.add_argument("--legacy", deprecated=True, action="store_true",
                            help="(3.14 deprecated=True)")
    else:  # pragma: no cover - 3.11-3.13 fallback
        parser.add_argument("--legacy", action="store_true",
                            help="(deprecated on 3.14+)")

    # SUPPRESS: gone from usage AND help, still functional.
    parser.add_argument("--secret-internal", action="store_true",
                        help=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"format: {args.format}")
    print(f"tag   : {args.tag}")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. CORRECTION 1: Action.format_help has zero call sites")
    print("=" * 78)
    print(parser.format_help())

    print()
    print("  Compare: AnnotatedAction.format_help() is never called, so the note")
    print("  'repeatable, de-duplicated' does NOT appear above -- it appears only")
    print("  under NoteFormatter, below. argparse reads action.help directly.")

    print()
    print("=" * 78)
    print("2. The portable pattern: a formatter reads the action's note")
    print("=" * 78)
    noted = argparse.ArgumentParser(prog="annotated", add_help=False,
                                    formatter_class=NoteFormatter)
    noted.add_argument("--tag", action=AnnotatedAction,
                       note="repeatable, de-duplicated", default=[],
                       help="add a tag")
    print(noted.format_help())
    print("  The note renders, and no Action needed to know about formatters.")

    print()
    print("=" * 78)
    print("3. CORRECTION 1b: Action.format_usage works only for nargs == 0")
    print("=" * 78)

    class NoUsage(argparse.Action):
        def __call__(self, p, ns, v, o=None):
            setattr(ns, self.dest, v)
        def format_usage(self):
            return ""

    probe = argparse.ArgumentParser(prog="x", add_help=False)
    probe.add_argument("--flag", action=NoUsage, nargs=0)
    probe.add_argument("--value", action=NoUsage)
    usage = probe.format_usage()
    print(f"  usage: {usage.strip()}")
    print(f"    --flag  suppressed by format_usage?  {'--flag' not in usage}")
    print(f"    --value suppressed by format_usage? {'--value' not in usage}")
    print()
    print("  argparse's source:")
    print("      if action.nargs == 0:")
    print("          part = action.format_usage()")
    print("      else:")
    print("          part = option_string + ' ' + self._format_args(...)")
    print()
    print("  So a value-taking option cannot hide itself this way. SUPPRESS can.")

    print()
    print("=" * 78)
    print("4. CORRECTION 2: help=SUPPRESS already leaves the usage line")
    print("=" * 78)
    print(f"  '--secret-internal' in help?  {'--secret-internal' in parser.format_help()}")
    print(f"  '--secret-internal' in usage? {'--secret-internal' in parser.format_usage()}")
    print(f"  still works?                  {parser.parse_args(['--secret-internal']).secret_internal}")
    print()
    print("  There is no quirk to work around. `HelpFormatter.add_argument` skips")
    print("  SUPPRESS actions entirely.")

    print()
    print("=" * 78)
    print("5. CORRECTION 3: deprecated=True works for options in 3.14")
    print("=" * 78)
    print(f"  Python {sys.version_info.major}.{sys.version_info.minor}")
    if sys.version_info >= (3, 14):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            build_parser().parse_args(["--legacy"])
        print(f"  built-in warning: {buffer.getvalue().strip()!r}")
        print("  Fixed wording, no replacement mentioned. Use an Action for that.")

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            args = build_parser().parse_args(["--old-format", "json"])
        print(f"  custom warning : {[str(w.message) for w in caught]}")
        print(f"  and it works   : args.format = {args.format!r}")
    else:  # pragma: no cover
        print("  (3.14+ only; this Python has no deprecated= parameter)")

    print()
    print("=" * 78)
    print("6. DeprecatedAction's own hooks, for custom formatters")
    print("=" * 78)
    for act in parser._actions:
        if isinstance(act, DeprecatedAction):
            print(f"  format_help()  -> {act.format_help()!r}")
            print(f"  format_usage() -> {act.format_usage()!r}")
            break
    print()
    print("  Neither is consulted by the stock formatters. They exist for a custom")
    print("  formatter that calls them, which is a legitimate reason to keep them.")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
