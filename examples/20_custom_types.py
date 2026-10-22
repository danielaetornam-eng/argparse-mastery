"""Lesson 20 - Writing ``type=`` callables properly.

Run me
------
    python examples/20_custom_types.py --port 8080 --size 10MB --timeout 1h30m
    python examples/20_custom_types.py --demo

The whole contract
------------------
A ``type=`` callable takes one string and returns a Python object, or raises to
reject the input. argparse formats ``ArgumentTypeError`` (or ``ValueError`` /
``TypeError``) into ``argument --opt: <your message>``.

Everything else is style. The style rules are what turn a validator into
documentation.

Rule 1: return the richest type available
-----------------------------------------
``type=int`` returns an int and you re-parse later. ``type=port`` returns an
int *and* has already guaranteed the range. A ``type=`` that returns ``timedelta``
or ``Path`` or an ``Enum`` moves work out of your handlers and into the one place
where it is impossible to forget. This is the single highest-value habit.

Rule 2: the message must teach
------------------------------
Compare::

    argument --port: invalid int value: 'http'      # argparse's, with type=int
    argument --port: 'http' is not an integer        # yours
    argument --port: 'http' is not an integer        # yours, better
        (expected a whole number like 8080)

The second version tells the user what shape of input would work. Include an
example value. It costs nothing and it is the difference between a user
retrying correctly and a user filing a bug.

Rule 3: never let a non-``ArgumentTypeError`` escape
----------------------------------------------------
argparse catches ``ArgumentTypeError``, ``TypeError`` and ``ValueError``. Anything
else -- ``KeyError``, a custom exception, ``SystemExit`` -- escapes
``parse_args`` as a traceback, which looks like a crash in your program. If your
validator calls something that may raise anything, wrap it::

    try:
        return _do_the_thing(raw)
    except Exception as exc:                # noqa: BLE001 - deliberate boundary
        raise argparse.ArgumentTypeError(str(exc)) from exc

Rule 4: use ``from None`` to suppress chaining
----------------------------------------------
``raise ... from None`` hides the ``ValueError`` that triggered the retry. In a
CLI, the traceback is noise; the message is the product.

Rule 5: make it a factory when it is parameterised
--------------------------------------------------
``type=`` receives exactly one positional argument, so bounds cannot be passed
alongside it. Return a closure::

    def bounded_int(minimum, maximum, *, unit=""):
        def parse(raw): ...
        return parse

Then ``type=bounded_int(1, 65535)``. This is the pattern behind
``argparse_mastery.types``.

Rule 6: one ``type=`` per concept, composed
-------------------------------------------
``--size 10MB`` -> ``byte_size``; ``--timeout 1h30m`` -> ``duration``. Neither
handler contains a parse. Because the type callable returns a usable object,
``args.timeout.total_seconds()`` is all the calling code needs.

Where the boundaries are
------------------------
* ``type=`` sees one token. Two-argument rules cannot live here -- use a
  validation function (lesson 14).
* ``type=`` runs on argv values. It does **not** run on config-file values,
  because a non-string default bypasses it entirely (lesson 03 and 29). Validate
  the other layers separately.
* ``type=`` cannot depend on other arguments. If it needs to, use an Action, which
  gets the namespace.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta


def port(raw: str) -> int:
    """A complete validator: converts, range-checks, and teaches on failure.

    Note the three-part structure worth copying: try to convert, reject out of
    range, and mention a valid example in the message.
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not an integer (expected something like 8080)"
        ) from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(
            f"{value} is outside the valid port range 1-65535"
        )
    return value


_SIZE_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3,
               "kib": 1024, "mib": 1024**2, "gib": 1024**3}
_SIZE_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*")


def byte_size(raw: str) -> int:
    """Parse ``512``, ``10MB``, ``1.5GiB`` into a byte count.

    Distinguishes 1000-based (``MB``) from 1024-based (``MiB``) deliberately:
    treating them as the same is how a "10GB limit" quietly becomes 9.3GB.
    """
    match = _SIZE_RE.fullmatch(raw)
    if match is None:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a size (expected 512, 10MB, or 1.5GiB)"
        )
    number, unit = match.groups()
    key = (unit or "b").lower()
    if key not in _SIZE_UNITS:
        raise argparse.ArgumentTypeError(
            f"{unit!r} is not a size unit; choose from {', '.join(sorted(_SIZE_UNITS))}"
        )
    return int(float(number) * _SIZE_UNITS[key])


_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|[smhdw])?", re.IGNORECASE)
_DURATION_SCALE = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def duration(raw: str) -> timedelta:
    """Parse ``30s``, ``5m``, ``1h30m``, ``2d`` into a ``timedelta``.

    The returned object does the conversion for you downstream
    (``args.timeout.total_seconds()``), which is the point: this type removes
    arithmetic from every call site.
    """
    text = raw.strip().lower().replace(" ", "")
    if not text:
        raise argparse.ArgumentTypeError("duration cannot be empty")

    total = 0.0
    position = 0
    matched = False
    for match in _DURATION_RE.finditer(text):
        if match.start() != position:
            bad = text[position : match.start()]
            raise argparse.ArgumentTypeError(
                f"{raw!r} is not a duration; unexpected {bad!r} "
                f"(try 30s, 5m, 1h30m, 2d)"
            )
        total += float(match.group(1)) * _DURATION_SCALE[(match.group(2) or "s").lower()]
        position = match.end()
        matched = True
    if not matched or position != len(text):
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a duration (try 30s, 5m, 1h30m, 2d)"
        )
    return timedelta(seconds=total)


def bounded_int(minimum: int, maximum: int, *, unit: str = "") -> type:
    """Return a ``type=`` factory: one argument's bounds, as a closure.

    ``type=`` is called with a single positional argument, so a parameterised
    validator *must* be a factory. This is the most reusable argparse idiom:

        parser.add_argument("--port", type=bounded_int(1, 65535))
        parser.add_argument("--level", type=bounded_int(0, 5, unit="levels"))

    Naming the unit lets the error message say ``5 workers`` instead of ``5``.
    """
    suffix = f" {unit}" if unit else ""

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{raw!r} is not an integer (expected a number "
                f"between {minimum} and {maximum})"
            ) from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"{value}{suffix} is outside the allowed range "
                f"{minimum}-{maximum}{suffix}"
            )
        return value

    return parse


def key_value(raw: str) -> tuple[str, str]:
    """Parse one ``KEY=VALUE`` pair, splitting on the FIRST ``=`` only.

    Splitting on every ``=`` breaks passwords and tokens, which routinely contain
    one. This is a small example of a lesson that is easy to get wrong twice:
    whenever you split a structured string, decide which occurrence you mean and
    say so in a comment.
    """
    key, sep, value = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(
            f"{raw!r} must be written as KEY=VALUE (for example, retries=5)"
        )
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError(f"{raw!r} has an empty key")
    return key, value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server",
        description="Lesson 20: custom type= callables.",
        epilog="Try: server --port 8080 --size 10MB --timeout 1h30m",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", type=port, default=8080, metavar="PORT",
                        help="listen port")
    parser.add_argument("--size", type=byte_size, default=1024, metavar="SIZE",
                        help="buffer size, e.g. 10MB")
    parser.add_argument("--timeout", type=duration, default="30s", metavar="DURATION",
                        help="e.g. 30s, 5m, 1h30m")
    parser.add_argument("--workers", type=bounded_int(1, 64, unit="workers"),
                        default=4, metavar="N", help="worker count")
    parser.add_argument("--set", action="append", type=key_value, default=[],
                        metavar="KEY=VALUE", help="override a config value")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    print(f"port     : {args.port!r}   ({type(args.port).__name__})")
    print(f"size     : {args.size!r}  ({args.size / 1024:.0f} KiB)")
    print(f"timeout  : {args.timeout!r}  ({args.timeout.total_seconds():.0f}s)")
    print(f"workers  : {args.workers!r}")
    print(f"set      : {dict(args.set)!r}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. Rule 1-2: a good type= teaches")
    print("=" * 78)
    mine = argparse.ArgumentParser(prog="server", add_help=False)
    mine.add_argument("--port", type=port)
    stock = argparse.ArgumentParser(prog="server", add_help=False)
    stock.add_argument("--port", type=int)
    for value in ("http", "70000"):
        print(f"  --port {value}")
        print(f"      mine  : {_msg(mine, ['--port', value])}")
        print(f"      int   : {_msg(stock, ['--port', value])}")

    print()
    print("=" * 78)
    print("2. Rule 5: parameterised validators are factories")
    print("=" * 78)
    print("  type= is called with one positional argument, so bounds must be closed")
    print("  over. bounded_int(1, 65535) returns a callable:")
    workers = argparse.ArgumentParser(prog="server", add_help=False)
    workers.add_argument("--workers", type=bounded_int(1, 64, unit="workers"))
    level = argparse.ArgumentParser(prog="server", add_help=False)
    level.add_argument("--level", type=bounded_int(0, 5, unit="levels"))
    for p, flag, value in ((workers, "--workers", "128"), (level, "--level", "9")):
        print(f"      {flag} {value:<5} -> {_msg(p, [flag, value])}")

    print()
    print("=" * 78)
    print("3. Rich return types move work out of your handlers")
    print("=" * 78)
    for raw, value in (("30s", duration), ("5m", duration), ("1h30m", duration), ("2d", duration)):
        got = value(raw)
        print(f"  duration({raw!r:<8}) -> {got!r}  total_seconds()={got.total_seconds():.0f}")
    print()
    for raw in ("512", "10MB", "1.5GiB"):
        got = byte_size(raw)
        print(f"  byte_size({raw!r:<7}) -> {got:>10,} bytes")
    print()
    print("  Compare with returning an int and converting later: every call site")
    print("  would have to remember the unit, and one of them will not.")

    print()
    print("=" * 78)
    print("4. Rule 6: splitting on the right occurrence")
    print("=" * 78)
    for raw in ("token=s3cr=et", "nokey", "=value"):
        try:
            got = key_value(raw)
            print(f"  key_value({raw!r:<16}) -> {got!r}")
        except argparse.ArgumentTypeError as exc:
            print(f"  key_value({raw!r:<16}) -> ArgumentTypeError: {exc}")

    print()
    print("=" * 78)
    print("5. Rule 3: only three exception types are safe inside type=")
    print("=" * 78)
    print("  argparse catches: ArgumentTypeError, TypeError, ValueError")
    print("  Anything else escapes parse_args as a traceback.\n")

    def leaky(raw: str) -> int:
        return {"a": 1}[raw]        # raises KeyError

    bad = argparse.ArgumentParser(prog="server", add_help=False)
    bad.add_argument("--x", type=leaky)
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            bad.parse_args(["--x", "b"])
            print("  (no exception)")
        except KeyError:
            print("  KeyError escaped parse_args -- looks like a crash in your program.")
        except SystemExit:
            print(f"  caught by argparse: {buffer.getvalue().splitlines()[-1]}")

    def wrapped(raw: str) -> int:
        try:
            return {"a": 1}[raw]
        except KeyError:
            raise argparse.ArgumentTypeError(f"{raw!r} is not a key") from None

    good = argparse.ArgumentParser(prog="server", add_help=False)
    good.add_argument("--x", type=wrapped)
    with contextlib.redirect_stderr(buffer):
        try:
            good.parse_args(["--x", "b"])
        except SystemExit:
            print(f"  with the wrapper   : {buffer.getvalue().splitlines()[-1]}")

    print()
    print("=" * 78)
    print("6. Where type= does NOT run")
    print("=" * 78)
    p = argparse.ArgumentParser(prog="server", add_help=False)
    p.add_argument("--port", type=port, default=8080)
    p.set_defaults(port=70000)     # int default: conversion is SKIPPED
    print(f"  set_defaults(port=70000) -> {p.parse_args([]).port!r}  (invalid, accepted!)")
    p.set_defaults(port="70000")    # str default: conversion RUNS
    print(f"  set_defaults(port='70000') -> {_msg(p, [])}")
    print()
    print("  Config files and env defaults flow through here too. That is why")
    print("  argparse_mastery.config.typed_defaults validates non-argv layers.")

    return 0


def _msg(parser: argparse.ArgumentParser, argv: list[str]) -> str:
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
            return line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
