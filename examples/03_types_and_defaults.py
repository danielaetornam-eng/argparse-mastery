"""Lesson 03 - ``type=`` and ``default=`` are two different jobs.

Run me
------
    python examples/03_types_and_defaults.py --port 8080 --ratio 0.25
    python examples/03_types_and_defaults.py --demo

``type`` converts. ``default`` supplies. They are applied at different times,
to different things, and confusing the two produces a bug that only appears when
someone configures rather than types.

The pipeline, in order
----------------------
1. argparse finds an option and consumes 1 token (``--port 8080`` -> ``"8080"``).
2. ``type`` is applied: ``int("8080")`` -> ``8080``. If it raises, argparse
   formats ``argument --port: invalid int value: 'http'`` and exits 2.
3. ``choices`` is checked against the *converted* value.
4. The result is written to the namespace.
5. ``default`` is used **only if step 2 never ran**, i.e. the argument was absent.

So: ``type`` is a converter for user input; ``default`` is a value that skipped
the converter entirely.

The trap everyone hits once
---------------------------
argparse applies ``type`` to a default **only if the default is a string**::

    parser.add_argument("--port", type=int, default=1)

    parser.set_defaults(port="8080")   # str -> converter runs  -> 8080  (int)
    parser.set_defaults(port=8080)     # int -> converter SKIPPED -> 8080  (int)

Both end up as ``8080``, which is why the bug hides. But now add a validator::

    def port(raw): ...
    parser.add_argument("--port", type=port, default=1)
    parser.set_defaults(port=70000)    # int: validator NEVER RUNS

A value from a config file silently skips every check you wrote. This is the
single most important gotcha in argparse, and
``argparse_mastery.config.typed_defaults`` exists to fix it. See lesson 29.

``default=SUPPRESS``
--------------------
The one genuinely under-used default::

    parser.add_argument("--config", default=argparse.SUPPRESS)

If the user does not pass it, the attribute **does not exist at all**. Then
``getattr(args, "config", None)`` is a reliable "was it passed?" test -- with no
sentinel, no leakage, and nothing extra in ``vars(args)`` to filter out.

``default=None`` cannot do this, because ``None`` is a legitimate value users
may legitimately pass. ``SUPPRESS`` is how argparse says "absence".

Order of keyword arguments does not matter
------------------------------------------
``type``, ``default``, ``choices``, ``required`` and friends are all keyword
arguments collected into one dict, so this is identical either way::

    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--port", default=8080, type=int)

What *does* have order is: positionals in ``add_argument``, option strings on
the left, and the order arguments appear in ``--help``.
"""

from __future__ import annotations

import argparse
import sys


def port_number(raw: str) -> int:
    """A ``type=`` callable: one string in, one Python object out.

    Raises :class:`argparse.ArgumentTypeError` to reject input. argparse turns
    that into ``argument --port: <message>`` -- note it replaces the useless
    stock ``invalid int value: 'http'``.
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is outside the valid range 1-65535")
    return value


def ratio(raw: str) -> float:
    """A second ``type=``, to show that converters compose with ``choices``.

    ``choices`` is checked *after* conversion, so ``choices=(0.0, 0.5, 1.0)``
    validates the parsed float, not the string you typed.
    """
    try:
        return float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a number") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server",
        description="Lesson 03: type= converts, default= supplies.",
        epilog=(
            "type= runs only on values the user typed.\n"
            "default= is inserted verbatim when the argument is absent.\n"
            "That asymmetry is why config-file values need explicit validation."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--port",
        type=port_number,
        default=8080,
        help="TCP port to listen on",
    )
    parser.add_argument(
        "--ratio",
        type=ratio,
        choices=(0.0, 0.25, 0.5, 0.75, 1.0),
        default=0.5,
        help="sampling ratio; choices are checked after conversion",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="localhost",
        help="a plain str type is the default and may be omitted",
    )
    parser.add_argument(
        "--tags",
        type=str,
        nargs="*",
        default=[],
        help="zero or more tags",
    )

    # SUPPRESS is the cleanest "was it passed?" mechanism there is. The attribute
    # simply does not exist unless the flag was given.
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="config file to load (attribute is absent if not given)",
    )

    # `required=True` on an option that has a default is legal but usually a
    # mistake: it forces the user to type something that already has a value.
    # Required + SUPPRESS default is the sensible pairing -- required means
    # required, and no silly default is advertised in --help.
    parser.add_argument(
        "--token",
        metavar="SECRET",
        default=argparse.SUPPRESS,
        help="API token; absent from the namespace if not supplied",
    )
    parser.set_defaults(token=None, required_token=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    print("namespace:")
    for key in sorted(vars(args)):
        print(f"  {key:<10} = {getattr(args, key)!r}   ({type(getattr(args, key)).__name__})")

    print()
    print(f"port is an int?      {isinstance(args.port, int)}")
    print(f"config present?      {'config' in vars(args)}")
    print(f"config or fallback?  {getattr(args, 'config', '(absent)')}")
    return 0


def demo() -> int:
    print("=" * 74)
    print("1. type= runs on the token the user typed")
    print("=" * 74)
    parser = build_parser()
    args = parser.parse_args(["--port", "9000"])
    print(f"  --port 9000 -> args.port = {args.port!r}  type={type(args.port).__name__}")

    print()
    print("=" * 74)
    print("2. default= is inserted verbatim, WITHOUT conversion")
    print("=" * 74)
    args = parser.parse_args([])
    print(f"  (nothing)   -> args.port = {args.port!r}  type={type(args.port).__name__}")

    print()
    print("=" * 74)
    print("3. The trap: a non-str default skips the validator entirely")
    print("=" * 74)
    p = argparse.ArgumentParser(prog="trap", add_help=False)
    p.add_argument("--port", type=port_number, default=8080)

    p.set_defaults(port="443")
    print(f"  set_defaults(port='443')  -> {p.parse_args([]).port!r}   (converter RAN)")

    p.set_defaults(port=443)
    print(f"  set_defaults(port=443)    -> {p.parse_args([]).port!r}   (converter SKIPPED)")

    p.set_defaults(port=70000)  # would be rejected by port_number
    print(f"  set_defaults(port=70000)  -> {p.parse_args([]).port!r}   (INVALID, accepted!)")
    print()
    print("  A config file writing `port = 70000` slips through every check.")
    print("  Fix: argparse_mastery.config.typed_defaults() validates the whole map.")

    print()
    print("=" * 74)
    print("4. default=SUPPRESS removes the attribute entirely")
    print("=" * 74)
    for argv in ([], ["--config", "a.toml"], ["--config", "b.toml"]):
        ns = build_parser().parse_args(argv)
        print(f"  argv={argv!s:<24} 'config' in vars: {'config' in vars(ns)!s:<6} "
              f"getattr fallback: {getattr(ns, 'config', '(absent)')!r}")

    print()
    print("  With default=None you could not distinguish 'not given' from 'given None'.")
    print("  SUPPRESS is the only value that means 'absence'.")

    print()
    print("=" * 74)
    print("5. A good type= error message vs argparse's stock one")
    print("=" * 74)
    print("  With your own type=")
    for flag, value in (("--port", "http"), ("--ratio", "abc")):
        print(f"    {flag} {value:<6} -> {_error_line(build_parser(), [flag, value])}")

    print("  With type=int (argparse's wording)")
    plain = argparse.ArgumentParser(prog="server", add_help=False)
    plain.add_argument("--port", type=int)
    print(f"    --port http  -> {_error_line(plain, ['--port', 'http'])}")

    print()
    print("  Same rejection, but one message names the problem and the other does not.")
    print("  That is the entire value of a custom type=.")

    return 0


def _error_line(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Return just the ``error: ...`` line from a failing parse.

    argparse prints a (possibly wrapped) usage block first, so grabbing the last
    line is not reliable; we match on the ``: error:`` marker instead.
    """
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
