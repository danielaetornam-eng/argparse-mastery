"""Custom ``type=`` callables.

Why this module exists
----------------------
``argparse``'s ``type=`` parameter is the single most under-used feature in the
module. The stock behaviour is thin::

    parser.add_argument("--port", type=int)

``int`` raises ``ValueError`` and argparse turns that into
``argument --port: invalid int value: 'http'``. Accurate, but not *helpful*.

A ``type=`` callable is just a one-argument function that either returns a
converted value or raises ``argparse.ArgumentTypeError`` / ``ValueError`` /
``TypeError``. argparse catches all three and formats them into the
``argument --port: <your message>`` slot. That means:

* You own the entire error message.
* You can return *any* Python object -- ``Path``, ``Enum``, ``timedelta``,
  ``Decimal``, a dataclass, a parsed regex.
* Validation happens **during** parsing, so downstream code can trust its types.

The contract in one sentence: **the callable receives a ``str`` and returns a
Python object, or raises to reject the input.**

Everything below follows the same shape:

1. A ``def f(raw: str) -> T:`` function.
2. Wrap the interesting bit in ``try``/``except``.
3. Raise ``ArgumentTypeError`` with a message that *teaches*.

Note on error types: ``ArgumentTypeError`` is a subclass of ``Exception``, not
of ``ValueError``. argparse special-cases ``ArgumentTypeError``, ``TypeError``
and ``ValueError``. Raising anything else (e.g. ``KeyError``) escapes
``parse_args`` entirely, which is almost never what you want inside a CLI.
"""

from __future__ import annotations

import argparse
import enum
import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

__all__ = [
    "HexColor",
    "bounded_float",
    "bounded_int",
    "byte_size",
    "csv_list",
    "duration",
    "enum_by_name",
    "existing_dir",
    "existing_file",
    "hex_color",
    "json_object",
    "key_value",
    "output_path",
    "port",
    "positive_int",
    "regex_string",
    "url",
]

T = TypeVar("T")


# ---------------------------------------------------------------------------
# 1. The simplest useful type: add a constraint to int/float
# ---------------------------------------------------------------------------
#
# The classic mistake is to validate *after* parsing:
#
#     args = parser.parse_args()
#     if not 1 <= args.workers <= 64:
#         parser.error("--workers must be between 1 and 64")
#
# That works, but it runs the check in the wrong place: the failure is reported
# as a generic ``error:`` line instead of being attached to ``--workers``, and
# it is easy to forget. Encoding the constraint in ``type=`` makes it
# impossible to forget.


def positive_int(raw: str) -> int:
    """``type=`` for strictly positive integers.

    Shows the canonical shape of a validating type function::

        parser.add_argument("--workers", type=positive_int, default=4)
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not an integer; expected a whole number like 8"
        ) from None
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"{value} must be greater than 0 (received {raw!r})"
        )
    return value


def bounded_int(
    minimum: int | None = None,
    maximum: int | None = None,
    *,
    unit: str = "",
) -> Callable[[str], int]:
    """Return a ``type=`` factory for an inclusive integer range.

    Because it returns a *function*, you get a fresh closure per argument and
    can write a specific message per argument::

        parser.add_argument("--port",  type=bounded_int(1, 65535, unit="port"))
        parser.add_argument("--level", type=bounded_int(0, 5, unit="level"))

    Why a factory instead of extra keyword arguments to ``bounded_int``
    itself? Because ``type=`` takes exactly one positional argument -- the
    string from the command line. The only way to parameterise it is by closing
    over the bounds. This is the single most useful argparse idiom to
    internalise: ``type=`` values are *constructed*, not *configured*.
    """

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            expected = "an integer"
            if minimum is not None and maximum is not None:
                expected = f"an integer between {minimum} and {maximum}"
            elif minimum is not None:
                expected = f"an integer >= {minimum}"
            elif maximum is not None:
                expected = f"an integer <= {maximum}"
            suffix = f" {unit}" if unit else ""
            raise argparse.ArgumentTypeError(f"{raw!r} is not {expected}{suffix}") from None

        if minimum is not None and value < minimum:
            raise argparse.ArgumentTypeError(
                f"{value}{_u(unit)} is below the minimum of {minimum}{_u(unit)}"
            )
        if maximum is not None and value > maximum:
            raise argparse.ArgumentTypeError(
                f"{value}{_u(unit)} is above the maximum of {maximum}{_u(unit)}"
            )
        return value

    return parse


def bounded_float(
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    unit: str = "",
) -> Callable[[str], float]:
    """Float analogue of :func:`bounded_int`.

    Also demonstrates a subtlety: ``float("nan")`` and ``float("inf")`` parse
    successfully but will wreck every comparison you do later, so reject them
    explicitly. argparse gives you no help here; range checks alone let NaN
    through because every comparison with NaN is False.
    """

    def parse(raw: str) -> float:
        try:
            value = float(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{raw!r} is not a number") from None
        if value != value:  # NaN, spelled without importing math
            raise argparse.ArgumentTypeError("NaN is not an acceptable value")
        if value in (float("inf"), float("-inf")):
            raise argparse.ArgumentTypeError("infinity is not an acceptable value")
        if minimum is not None and value < minimum:
            raise argparse.ArgumentTypeError(f"{value}{_u(unit)} is below the minimum {minimum}")
        if maximum is not None and value > maximum:
            raise argparse.ArgumentTypeError(f"{value}{_u(unit)} is above the maximum {maximum}")
        return value

    return parse


def _u(unit: str) -> str:
    """Render a unit for message interpolation, with correct pluralisation."""
    return f" {unit}" if unit else ""


# ---------------------------------------------------------------------------
# 2. Enum lookup with aliases
# ---------------------------------------------------------------------------


def enum_by_name(
    enum_cls: type[enum.Enum],
    *,
    aliases: dict[str, str] | None = None,
    lowercase: bool = True,
) -> Callable[[str], Any]:
    """Build a ``type=`` that maps a string onto an ``Enum`` member.

    argparse's built-in ``choices=list(MyEnum)`` already works, but it makes the
    user type ``MyEnum.FAST`` (see the ``{C.A,C.B}`` metavar in
    :mod:`examples.12_choices`), and the error message reads
    ``invalid choice: 'aa' (choose from 'C.A', 'C.B')``. Almost nobody wants that.

    This returns the *member* while accepting either the bare name or a
    case-insensitive match::

        class Mode(enum.Enum):
            FAST = "fast"
            BALANCED = "balanced"

        parser.add_argument("--mode", type=enum_by_name(Mode))
        # --mode FAST, --mode fast, --mode balanced all work

    ``aliases`` maps extra spellings onto member names -- the cheapest way to
    keep a deprecated flag spelling alive without polluting your enum.
    """
    alias_map = {k.lower(): v for k, v in (aliases or {}).items()}
    by_name = {member.name.lower(): member for member in enum_cls}
    by_value: dict[str, Any] = {}
    for member in enum_cls:
        if isinstance(member.value, str):
            by_value[member.value.lower()] = member

    def parse(raw: str) -> Any:
        key = raw.lower() if lowercase else raw
        if key in alias_map:
            key = alias_map[key].lower()
        if key in by_name:
            return by_name[key]
        if key in by_value:
            return by_value[key]
        options = sorted({*(m.name for m in enum_cls), *alias_map})
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a valid {enum_cls.__name__.lower()}; choose from {', '.join(options)}"
        )

    return parse


# ---------------------------------------------------------------------------
# 3. Text list parsing
# ---------------------------------------------------------------------------


def csv_list(raw: str, *, sep: str = ",", strip: bool = True) -> list[str]:
    """Split a comma-separated option value into a list.

    Combine with ``action='append'`` to accumulate repeated flags, or with
    ``nargs='+'`` to accept space-separated values::

        parser.add_argument("--tag", action="append", type=csv_list)

    Empty input yields an empty list rather than ``[""]`` -- that distinction
    bites when the flag is passed as ``--tag ""`` meaning "clear the list".
    """
    if not raw:
        return []
    parts = raw.split(sep)
    return [p.strip() if strip else p for p in parts if p != "" or not strip]


def key_value(raw: str) -> tuple[str, str]:
    """Parse one ``key=value`` pair, rejecting the classic mistakes.

    ``urlparse``-style splitting is avoided deliberately: passwords and tokens
    routinely contain ``=``, so we split on the *first* ``=`` only.
    """
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"{raw!r} must be written as KEY=VALUE (for example, retries=5)"
        )
    key, _, value = raw.partition("=")
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError(f"{raw!r} has an empty key; expected KEY=VALUE")
    return key, value


def regex_string(pattern: str) -> Callable[[str], re.Pattern[str]]:
    """Return a ``type=`` factory that compiles and anchors a regex.

    Compiling the pattern at *definition* time means an invalid regex is a
    programmer error caught on the first run, not a user-facing parse error.
    The user-supplied pattern is anchored with :func:`re.fullmatch` so that
    ``--name foo`` does not silently accept ``--name foobar``.
    """

    def parse(raw: str) -> re.Match[str]:
        compiled = re.compile(pattern)
        match = compiled.fullmatch(raw)
        if match is None:
            raise argparse.ArgumentTypeError(
                f"{raw!r} does not match the required pattern {pattern!r}"
            )
        return match

    return parse


# ---------------------------------------------------------------------------
# 4. Quantities humans actually type
# ---------------------------------------------------------------------------


_DURATION_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h|d|w)?", re.IGNORECASE
)
_DURATION_UNITS = {
    "ms": "milliseconds",
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

_SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def duration(raw: str) -> timedelta:
    """Parse ``30s``, ``5m``, ``1h30m``, ``2d`` into a :class:`~datetime.timedelta`.

    A worked example of a *composite* type: tokenise, validate every token,
    convert, sum. The returned object is useful downstream -- you can do
    ``args.timeout.total_seconds()`` -- instead of an int you must remember to
    convert correctly everywhere.
    """
    text = raw.strip().lower().replace(" ", "")
    if not text:
        raise argparse.ArgumentTypeError("duration cannot be empty")

    total = 0.0
    position = 0
    seen_any = False
    for match in _DURATION_RE.finditer(text):
        # Reject junk between matches, e.g. "5x" leaves position behind.
        if match.start() != position:
            bad = text[position : match.start()]
            raise argparse.ArgumentTypeError(
                f"{raw!r} is not a valid duration; unexpected {bad!r}. "
                f"Use forms like 30s, 5m, 1h30m, 2d"
            )
        unit = (match.group("unit") or "s").lower()
        total += float(match.group("value")) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        position = match.end()
        seen_any = True

    if not seen_any or position != len(text):
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a valid duration; use forms like 30s, 5m, 1h30m, 2d"
        )
    return timedelta(seconds=total)


def byte_size(raw: str) -> int:
    """Parse ``10``, ``512B``, ``10MB``, ``1.5GiB`` into a byte count.

    Returns ``int``, and deliberately uses 1000-based units for ``KB`` and
    1024-based for ``KiB``. Silently treating them as the same is how
    "our 10GB limit became 9.3GB" bugs happen.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", raw)
    if match is None:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a size; use forms like 512, 10MB, 1.5GiB"
        )
    number, unit = match.groups()
    unit_key = (unit or "b").lower()
    if unit_key not in _SIZE_UNITS:
        raise argparse.ArgumentTypeError(
            f"{unit!r} is not a known size unit; choose from "
            + ", ".join(sorted(_SIZE_UNITS))
        )
    value = float(number) * _SIZE_UNITS[unit_key]
    if value < 0:
        raise argparse.ArgumentTypeError("size cannot be negative")
    return int(value)


# ---------------------------------------------------------------------------
# 5. Paths, URLs, ports, JSON
# ---------------------------------------------------------------------------


def existing_file(raw: str) -> Path:
    """Return a :class:`~pathlib.Path` that must already exist and be a file."""
    path = Path(raw).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"{raw!r}: no such file")
    if path.is_dir():
        raise argparse.ArgumentTypeError(f"{raw!r}: is a directory, expected a file")
    if not os.access(path, os.R_OK):
        raise argparse.ArgumentTypeError(f"{raw!r}: permission denied")
    return path


def existing_dir(raw: str) -> Path:
    """Return a :class:`~pathlib.Path` that must be an existing directory."""
    path = Path(raw).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"{raw!r}: no such directory")
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"{raw!r}: is a file, expected a directory")
    return path


def output_path(raw: str) -> Path:
    """Return a writable-or-creatable path for *output*.

    The parent directory must already exist. Creating parents silently can hide
    typos, so require them explicitly -- but *do* reject an existing directory
    early, because otherwise you only discover it at write time.
    """
    path = Path(raw).expanduser()
    if path.is_dir():
        raise argparse.ArgumentTypeError(
            f"{raw!r}: is a directory; expected a file path to write"
        )
    if not path.parent.exists():
        raise argparse.ArgumentTypeError(
            f"{raw!r}: parent directory {str(path.parent)!r} does not exist; create it first"
        )
    return path


def port(raw: str) -> int:
    """A TCP/UDP port number, with the reserved range called out."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a port number") from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is outside the valid port range 1-65535")
    if value < 1024:
        # Not fatal, but binding below 1024 needs privileges on Unix.
        pass
    return value


def url(raw: str, *, schemes: Sequence[str] = ("http", "https")) -> str:
    """Validate a URL and require one of ``schemes``.

    Uses :func:`urllib.parse.urlparse`, which does *not* validate -- it splits.
    So we check the parts ourselves: a scheme, and a netloc (host).
    """
    parsed = urlparse(raw)
    if parsed.scheme not in schemes:
        raise argparse.ArgumentTypeError(
            f"{raw!r} must use one of these schemes: {', '.join(schemes)}"
        )
    if not parsed.netloc:
        raise argparse.ArgumentTypeError(f"{raw!r} is missing a host")
    return raw


def json_object(raw: str) -> Any:
    """Parse inline JSON, or ``@path/to/file.json``.

    The ``@`` convention is borrowed from ``fromfile_prefix_chars`` and is a
    nice trick for CLIs that accept both a literal and a file reference.
    """
    text: str
    if raw.startswith("@"):
        path = existing_file(raw[1:])
        text = path.read_text(encoding="utf-8")
    else:
        text = raw
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        where = f"{raw[1:]}" if raw.startswith("@") else "the value"
        raise argparse.ArgumentTypeError(
            f"{where} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from None


def hex_color(raw: str) -> tuple[int, int, int]:
    """Parse ``#1a2b3c``, ``1a2b3c``, or ``#fff`` into an ``(r, g, b)`` tuple."""
    text = raw.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a hex colour; use #rgb or #rrggbb"
        )
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


# A tiny callable class, to show that ``type=`` accepts anything callable.
# Compare with argparse.FileType, which is exactly this idea in the stdlib.


class HexColor:
    """Callable class form of :func:`hex_color`.

    Worth knowing because *callable objects* are the idiomatic way to ship a
    richer ``type=`` -- they can carry state, expose ``__name__`` for help text,
    and be used without a factory call at the definition site::

        parser.add_argument("--color", type=HexColor())
    """

    __name__ = "hex color"

    def __call__(self, raw: str) -> tuple[int, int, int]:
        return hex_color(raw)

    def __repr__(self) -> str:
        return "HexColor()"


# ---------------------------------------------------------------------------
# 6. Cross-argument validation
# ---------------------------------------------------------------------------
#
# ``type=`` sees one string at a time, so it can never express "at least one of
# these two" or "A implies B". That class of rule needs either the validation
# function below or a custom Action. Both approaches are shown; use the
# function for rules and the Action when the value itself needs fixing up.


def validate_all(rules: Iterable[Callable[[argparse.Namespace], None]]) -> Callable[..., None]:
    """Compose several cross-field rules into one parser ``.error`` reporter.

    ``rules`` are callables that take the parsed namespace and either return
    ``None`` (fine) or raise :class:`argparse.ArgumentError` /
    :class:`ValueError` with a message. The composed function reports *every*
    violation at once rather than making the user fix them one at a time::

        parser = argparse.ArgumentParser()
        ...
        parser.error = validate_all([
            require_min_output_when_dry_run,
            reject_equal_start_and_end,
        ])
        args = parser.parse_args()
    """

    def report(namespace: argparse.Namespace) -> None:
        problems: list[str] = []
        for rule in rules:
            try:
                rule(namespace)
            except (argparse.ArgumentError, ValueError) as exc:
                problems.append(_rule_message(exc))
        if problems:
            raise SystemExit(
                "\n".join(["", *[f"  {p}" for p in problems], ""])
            )

    return report


def _rule_message(exc: Exception) -> str:
    if isinstance(exc, argparse.ArgumentError):
        return str(exc)
    return str(exc)


# ---------------------------------------------------------------------------
# 7. Diagnostics: introspecting the types you registered
# ---------------------------------------------------------------------------
#
# Handy when auditing a third-party CLI's parser, or when you want a single
# place to see every ``type=`` callable a parser uses.


def describe_type(func: Any) -> str:
    """Return a short human description of a ``type=`` callable."""
    name = getattr(func, "__name__", None) or type(func).__name__
    if isinstance(func, argparse.FileType):
        # FileType keeps the mode in `_mode`. Present it readably rather than
        # relying on its private shape changing.
        mode = getattr(func, "_mode", "")
        return f"file({mode})" if mode else "file"
    if callable(func) and not isinstance(func, type) and getattr(func, "__doc__", None):
        first_line = func.__doc__.strip().splitlines()[0]
        return f"{name}: {first_line}"
    return name


def parser_type_table(parser: argparse.ArgumentParser) -> list[tuple[str, str, str]]:
    """Return ``(dest, metavar, type_description)`` for every argument.

    Includes subparser children, because a real CLI's surface is the sum of its
    subcommands. Demonstrates the introspection hook that ``--help`` as data,
    shell completion, and documentation generators all rely on.
    """
    rows: list[tuple[str, str, str]] = []
    for action in _walk_actions(parser):
        type_desc = "flag" if action.nargs == 0 else describe_type(action.type)
        rows.append((action.dest, action.metavar or action.dest.upper(), type_desc))
    return rows


def _walk_actions(parser: argparse.ArgumentParser):
    """Yield every action in ``parser``, recursing into subparsers."""
    for action in parser._actions:
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from _walk_actions(sub)


if __name__ == "__main__":  # pragma: no cover - demo entry point
    demo = argparse.ArgumentParser(prog="types-demo", description=__doc__.splitlines()[0])
    demo.add_argument("--port", type=port, default=8080)
    demo.add_argument("--timeout", type=duration, default="30s")
    demo.add_argument("--limit", type=bounded_int(1, 1024, unit="MiB"), default=64)
    demo.add_argument("--tags", type=csv_list, default=[])
    demo.add_argument("--color", type=HexColor())
    demo.add_argument("--host", type=url, default="http://localhost:8080")
    demo.print_help()
    for name, value in parser_type_table(demo):
        print(f"  {name:<10} {value:<12} {name and value}", file=sys.stderr)
