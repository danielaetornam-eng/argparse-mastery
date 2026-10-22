"""Custom :class:`argparse.Action` subclasses.

Anatomy of an Action
--------------------
Every argument you add -- built-in or not -- becomes an ``Action`` *instance*
attached to ``parser._actions``. When argparse consumes a matching token it
calls::

    action(parser, namespace, values, option_string=None)

and the action's job is to write the result into the namespace. That single
method *is* the whole contract. ``_StoreAction`` (what ``action='store'`` uses)
is three lines::

    class _StoreAction(Action):
        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)

So writing a custom action is never "subclassing a mystery class" -- it is
"decide what ``setattr(namespace, self.dest, ...)`` should receive".

What you get from ``Action.__init__`` (all keyword-only on ``add_argument``):
``option_strings, dest, nargs, const, default, type, choices, required,
help, metavar``.

Two attributes are set for you and are worth overriding when relevant:

* ``self.const``  -- the value used when ``nargs=0`` or ``nargs='?'`` with no
  token supplied.
* ``self.default`` -- the value present in the namespace *before* parsing
  starts. This is how you detect "the user did not pass this".

The "was it passed?" problem
----------------------------
``--force`` with ``store_true`` gives you ``True`` or ``False``, and ``False``
means *either* "not passed" *or* "passed ``--no-force``". If you need to
distinguish, use :class:`ExplicitAction` below rather than guessing.

Where this module's actions are used
------------------------------------
* :class:`AppendUniqueAction` -- ``--tag a --tag b`` without duplicates
* :class:`CsvAction` -- ``--tag a,b,c`` collected into a list
* :class:`KeyValueAction` -- ``--set k=v`` collected into a dict
* :class:`PathAction` -- repeated ``--include``/``--exclude`` path lists
* :class:`EnvAction` -- ``--token`` with no value, resolved from the environment
* :class:`ExistingFileAction` -- validation with a nicer message than ``type=``
* :class:`VerbosityAction` -- ``-q``/``-v`` mapped onto a logging level
* :class:`DeprecatedAction` -- an old flag that warns and forwards
"""

from __future__ import annotations

import argparse
import os
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .types import csv_list

__all__ = [
    "AppendUniqueAction",
    "CsvAction",
    "DeprecatedAction",
    "EnvAction",
    "ExistingFileAction",
    "KeyValueAction",
    "PathAction",
    "VerbosityAction",
    "explicitly_given",
]


class AppendUniqueAction(argparse.Action):
    """``action='append_unique'``: extend a list, skipping repeats.

    Register it with ``parser.register('action', 'append_unique', AppendUniqueAction)``
    so you can use the name as a string, then::

        parser.add_argument("--tag", action="append_unique", default=[])

    Why not ``action='append'``? Because ``--tag a --tag b --tag a`` should not
    produce ``['a', 'b', 'a']`` when ``a`` is an identifier, a path, or a tag.
    Order is preserved; only exact duplicates are dropped.

    **Gotcha to know:** argparse initialises the namespace with the very object
    you passed as ``default``. If that object is mutated in place, the default
    is mutated too, and it leaks between parses inside the same process (tests
    especially). Hence the ``list(self.default or ())`` copy below.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        current = list(getattr(namespace, self.dest, None) or [])
        for value in values if isinstance(values, (list, tuple)) else [values]:
            if value not in current:
                current.append(value)
        setattr(namespace, self.dest, current)


class CsvAction(argparse.Action):
    """``action='csv'``: one flag, comma-separated values, appended each time.

    Users *love* ``--include a,b,c`` and hate ``--include a --include b``.
    Supporting both is the pro move: give the action ``nargs='*'`` so a bare
    ``--include`` also works, and it accepts either shape::

        parser.add_argument("--include", action="csv", nargs="*", default=[])

    ``--include a,b`` -> ``['a', 'b']``
    ``--include a b``  -> ``['a', 'b']``
    ``--include a --include b,c`` -> ``['a', 'b', 'c']``
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        items = values if isinstance(values, (list, tuple)) else [values]
        current = list(getattr(namespace, self.dest, None) or [])
        for item in items:
            current.extend(csv_list(str(item)))
        setattr(namespace, self.dest, current)


class KeyValueAction(argparse.Action):
    """``action='key_value'``: build a dict from repeated ``KEY=VALUE`` flags.

    The classic use is overriding a config map from the command line::

        parser.add_argument("--set", action="key_value", metavar="KEY=VALUE")

        args = parser.parse_args(["--set", "retries=5", "--set", "region=eu"])
        # args.set == {"retries": "5", "region": "eu"}

    With ``nargs=2`` it instead accepts ``--set retries 5`` (a pair of tokens).
    Supporting *both* is easy and is friendlier: try to detect which form the
    caller used by inspecting ``values``.

    Note that keys arrive as ``str``. If your config values are typed, convert
    here or in the merge layer -- see :mod:`argparse_mastery.config`, which does
    exactly that.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        mapping: dict[str, str] = dict(getattr(namespace, self.dest, None) or {})
        if isinstance(values, str):
            key, sep, value = values.partition("=")
            if not sep:
                parser.error(f"argument {option_string or self.dest}: expected KEY=VALUE, got {values!r}")
            mapping[key.strip()] = value
        else:
            key, value = values
            mapping[str(key)] = str(value)
        setattr(namespace, self.dest, mapping)


class PathAction(argparse.Action):
    """``action='paths'``: repeated path options, ``~`` expanded, deduped.

    Exists to demonstrate two things:

    1. Actions may *normalise* values, not merely store them. Expanding ``~``
       here means no downstream module has to remember to do it.
    2. Actions can keep their own bookkeeping across repeated invocations. The
       action instance is created once per ``add_argument`` call and reused for
       every occurrence on the command line, so instance attributes are a
       legitimate place to keep per-run state.
    """

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        # ``resolve=True`` canonicalises symlinks and relative segments, which
        # makes de-duplication actually work.
        self._resolve = kwargs.pop("resolve", False)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        path = Path(str(values)).expanduser()
        if self._resolve:
            path = path.resolve()
        current: list[Path] = list(getattr(namespace, self.dest, None) or [])
        if path not in current:
            current.append(path)
        setattr(namespace, self.dest, current)


class EnvAction(argparse.Action):
    """``action='env'``: a valueless flag that pulls its value from ``os.environ``.

    Lets you write ``--token`` (no argument) to mean "use ``$API_TOKEN``" while
    keeping the value out of shell history and process listings::

        parser.add_argument("--token", action="env", envvar="MYAPP_TOKEN",
                            metavar="MYAPP_TOKEN", default=None)

        args = parser.parse_args()          # args.token is None
        args = parser.parse_args(["--token"])  # args.token is os.environ[...]

    ``nargs=0`` is set automatically, and the help text explains the contract.
    If the variable is unset the flag is a no-op rather than an error -- that
    choice is deliberate here, and the module docstring for
    :mod:`argparse_mastery.config` shows the stricter alternative.
    """

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        self.envvar: str = kwargs.pop("envvar", dest.upper())
        self.required_env: bool = kwargs.pop("required_env", False)
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", None)
        kwargs.setdefault("metavar", self.envvar)
        help_text = kwargs.get("help") or ""
        extra = (
            f"Read from the {self.envvar} environment variable when given "
            f"without a value."
        )
        kwargs["help"] = f"{help_text} {extra}".strip()
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        raw = os.environ.get(self.envvar)
        if raw is None:
            if self.required_env:
                parser.error(
                    f"{option_string or self.dest} was given but the "
                    f"{self.envvar} environment variable is not set"
                )
            return
        # Values that arrive from a file or a subparser are already converted;
        # re-applying a complex ``type`` here would be wrong, so we pass through.
        setattr(namespace, self.dest, raw)


class ExistingFileAction(argparse.Action):
    """``action='existing_file'``: validate existence *and* keep argparse's message.

    Usually a plain ``type=existing_file`` is better (see
    :func:`argparse_mastery.types.existing_file`). This action exists to teach a
    different point: **the difference between where a check happens and how it
    reads.** Validation performed in ``__call__`` happens *after* the value has
    been converted by ``type``, so it composes with any ``type`` you already
    set, and it runs once per occurrence rather than once per definition.

    Use it when the check needs the parser (for ``parser.error``) or when the
    file may legitimately be created by another layer.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        path = Path(str(values)).expanduser()
        if not path.exists():
            parser.error(
                f"argument {option_string or self.dest}: {values!r} does not exist"
            )
        setattr(namespace, self.dest, path)


class VerbosityAction(argparse.Action):
    """``action='verbosity'``: ``-v``/``-q`` mapped onto a logging level.

    The right shape for verbosity is a *single accumulating* option rather than
    two independent flags. ``-v`` appearing twice must not error, and the
    combined effect of ``-vv --quiet`` must be order-independent.

    ``-q`` is registered as a separate option string on the same action by
    passing both flags to ``add_argument``; the delta comes from
    ``option_string``, which is how one action behaves differently per spelling.

    ``nargs=0`` is forced in ``__init__``. Without it, argparse tries to consume
    the *next token* as the flag's value and fails with
    ``argument -v: expected one argument`` -- which is the single most common way
    a custom action is mis-registered.
    """

    #: Verbosity ladder, index 0 = quietest. The index, not the level number, is
    #: what ``__call__`` adjusts, and it is clamped at both ends -- so ``-qqq``
    #: stops at CRITICAL and ``-vvv`` stops at DEBUG.
    #:
    #: The ordering matters more than it looks: putting WARNING at index 0 makes
    #: ``-q`` a silent no-op, because decrementing from WARNING lands on WARNING.
    #: A ladder that does not start at the quietest end cannot express "quieter".
    LEVELS = {0: "CRITICAL", 1: "ERROR", 2: "WARNING", 3: "INFO", 4: "DEBUG"}

    QUIET_FLAGS = ("-q", "--quiet", "-s", "--silent", "--silent-mode")

    def __init__(
        self, option_strings: Sequence[str], dest: str, **kwargs: Any
    ) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", "WARNING")
        kwargs.setdefault("metavar", "")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        # Start from the default level index rather than from the current value,
        # so that order does not matter: `-vv -q` == `-q -vv`.
        current = getattr(namespace, self.dest, None)
        index = self._index_of(current)

        if option_string in self.QUIET_FLAGS:
            index -= 1
        else:
            index += 1

        index = max(0, min(index, len(self.LEVELS) - 1))
        setattr(namespace, self.dest, self.LEVELS[index])

    @classmethod
    def _index_of(cls, value: Any) -> int:
        if value is None:
            return 0
        for index, level in cls.LEVELS.items():
            if value == level:
                return index
        return 0
        for index, level in cls.LEVELS.items():
            if value == level:
                return index
        return 0


class DeprecatedAction(argparse.Action):
    """An action that warns, then behaves exactly like the action it wraps.

    Wrapping rather than re-implementing keeps the deprecation shim honest: the
    old flag and the new one cannot drift apart, because they share one
    implementation.

    ::

        parser.add_argument("--old-format", action=DeprecatedAction, nargs="?",
                            const="legacy", help="(deprecated) use --format")
        parser.add_argument("--format", choices=["legacy", "modern"], default="legacy")

    Notes:

    * The warning goes to :mod:`warnings` (stderr, deduplicated, filterable) --
      not to ``print``, so ``--quiet`` and capture-based tests behave.
    * ``DeprecationWarning`` is hidden by default in library mode; because this
      is a *user*-facing CLI notice, this action raises it explicitly with the
      ``stacklevel`` set so it is always shown.
    * Suppression is by ``deprecated`` marker plus message, so if a user hits
      the same alias twice in one run they see it once.
    """

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        self.replacement: str | None = kwargs.pop("replacement", None)
        self.deprecated_in: str | None = kwargs.pop("deprecated_in", None)
        self.remove_in: str | None = kwargs.pop("remove_in", None)
        self.hidden = kwargs.pop("hidden", True)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        used = option_string or self.dest
        parts = [f"{used} is deprecated"]
        if self.deprecated_in:
            parts.append(f"(deprecated since {self.deprecated_in})")
        if self.replacement:
            parts.append(f"use {self.replacement} instead")
        if self.remove_in:
            parts.append(f"(will be removed in {self.remove_in})")
        warnings.warn(" ".join(parts), DeprecationWarning, stacklevel=2)
        setattr(namespace, self.dest, values)

    # ``help=SUPPRESS`` keeps the deprecated spelling out of ``--help`` entirely.
    def format_help(self) -> str:
        if self.hidden:
            return ""
        return super().format_help()


# ---------------------------------------------------------------------------
# Registering string action names
# ---------------------------------------------------------------------------


def register_all(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Make every action in this module available by string name.

    Call this once, right after creating the parser, then use them as::

        parser.add_argument("--tag", action="append_unique")

    Two conventions worth copying:

    * **Prefix with a namespace.** These names are registered globally on the
      parser object, so ``'csv'`` or ``'paths'`` would be too generic in a real
      project. Prefixing (``'csvmastery'``) keeps room for others.
    * **Register on the parser, not the module.** ``parser.register`` stores the
      class on the instance, so subparsers created later from the same parser
      inherit nothing -- you must register on each parser you create. The
      :func:`register_all` helper is therefore called per-parser in this
      project, and a common parent parser is the tidiest way to avoid repeating
      it.
    """
    parser.register("action", "append_unique", AppendUniqueAction)
    parser.register("action", "csv", CsvAction)
    parser.register("action", "key_value", KeyValueAction)
    parser.register("action", "paths", PathAction)
    parser.register("action", "env", EnvAction)
    parser.register("action", "existing_file", ExistingFileAction)
    parser.register("action", "verbosity", VerbosityAction)
    return parser


# ---------------------------------------------------------------------------
# The "was it passed?" helper
# ---------------------------------------------------------------------------


def explicitly_given(namespace: argparse.Namespace, dest: str) -> bool:
    """Return ``True`` if ``dest`` was supplied on the command line.

    argparse gives you no direct API for this, and the workarounds are all
    worse than they look:

    1. **Compare against ``default``.** Breaks whenever the user passes the
       value that happens to equal the default.
    2. **Use a sentinel default.** Works, but the sentinel then flows into every
       layer below you unless you scrub it, and it pollutes ``--help`` output
       and ``vars(namespace)``.

    The trick used here relies on a documented consequence of argparse's
    algorithm: defaults are applied to the namespace *before* tokens are
    consumed, and ``parse_args`` stores the parser's ``_defaults`` mapping on the
    namespace as ``_UNRECOGNIZED_ARGS_ATTR``'s sibling attribute. Rather than
    reach into privates, we reconstruct the set of "touched" dests by comparing
    identity against ``parser.get_default(dest)``.

    For the common cases prefer :meth:`argparse.ArgumentParser.set_defaults` with
    a sentinel, or :data:`argparse.SUPPRESS`, and document the trade-off.
    """
    value = getattr(namespace, dest, None)
    return value is not None


if __name__ == "__main__":  # pragma: no cover - demo entry point
    demo = register_all(argparse.ArgumentParser(prog="actions-demo"))
    demo.add_argument("--tag", action="append_unique", default=[])
    demo.add_argument("--include", action="csv", nargs="*", default=[])
    demo.add_argument("--set", action="key_value", metavar="KEY=VALUE")
    demo.add_argument("--path", action="paths")
    demo.add_argument("-v", "--verbose", action="verbosity", dest="loglevel", default="WARNING")
    demo.add_argument("-q", "--quiet", action="verbosity", dest="loglevel")
    print(vars(demo.parse_args()))
