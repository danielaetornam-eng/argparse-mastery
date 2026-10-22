"""Lesson 30 - Environment variables as defaults.

Run me
------
    MYAPP_LOG_LEVEL=debug python examples/30_environment_variable_defaults.py --demo
    python examples/30_environment_variable_defaults.py --log-level debug

The simplest correct integration
--------------------------------
Use ``os.environ`` as the *default*::

    parser.add_argument(
        "--log-level",
        default=os.environ.get("MYAPP_LOG_LEVEL", "warning"),
    )

That is the whole mechanism, and it is better than it looks. Because the
environment value becomes the ``default``, argparse's precedence falls out
correctly with no extra code::

    defaults  <  environment  <  argv

``prog --log-level debug`` overrides the variable, because an argv token always
overwrites a default. Nothing else is needed.

Three ways, and how to choose
----------------------------
1. **As the default** (above). Best for one-off tools. Weakness: the value is a
   raw string, so ``type=`` still converts it when argv supplies it but *not* when
   the environment supplies it -- the same asymmetry as lesson 03's ``default``.
   Verified: argparse only converts a default when it is a ``str`` **and** the
   namespace value is still that same object. So ``os.environ`` strings *are*
   converted. Environment integration is the one case where that quirk helps.

2. **A custom ``action='env'``** -- a valueless flag that pulls from the
   environment (lesson 24). Best for secrets: ``--token`` reads ``$TOKEN``, so the
   value never appears in shell history or ``ps`` output.

3. **An explicit env layer with provenance** --
   :func:`apply_env_layer` below. Best when you want ``--show-config``, and the
   only approach that can distinguish "the environment said 3" from "the default
   is 3".

The naming convention
---------------------
``MYAPP_LOG_LEVEL=debug`` maps to ``--log-level`` by upper-casing and replacing
hyphens with underscores, with a configurable prefix. This is what ``git``,
``docker``, ``npm``, ``uv`` and nearly everything else does. Supporting *both*
``MYAPP_`` and the bare name is a common convenience; supporting unprefixed names
in a tool that might be installed twice is how ``PATH``-adjacent confusion starts.
Pick one and document it.

Always document it in ``--help``
--------------------------------
This is the step people skip, and it is the step that matters. A flag with a
hidden environment source is undiscoverable::

    parser.add_argument("--log-level", default=..., help="log level (env: MYAPP_LOG_LEVEL)")

``argparse_mastery.config.explain_env`` generates those suffixes for a whole
parser after the fact, which is useful when you have twenty options.

Coercion
--------
An environment value is always a string. ``MYAPP_WORKERS=8`` must become ``8``.
``int(env_default)`` raises at import time if the variable is malformed, which is
a terrible failure mode -- the tool will not start at all. Convert inside a
``type=`` call so the user gets a proper message::

    def env_int(name: str, fallback: int) -> Callable[[str], int]:
        def factory(raw: str) -> int:
            try:
                return int(raw)
            except ValueError:
                raise argparse.ArgumentTypeError(
                    f"${name}={raw!r} is not an integer"
                ) from None
        return factory

Or, more simply, validate env values after parsing and report them the same way as
bad argv.

Booleans deserve special care
----------------------------
``MYAPP_DEBUG=1``, ``=true``, ``=yes``, ``=on`` -- users will try all of them, and
``bool("false")`` is ``True``, which is the classic bug. Parse explicitly
(``argparse_mastery.logging_setup.env_flag``).

Precedence for booleans with a tri-state
----------------------------------------
With ``BooleanOptionalAction`` and ``default=None`` you get three states, so the
layering is unambiguous::

    MYAPP_COLOR=always   -> env layer sets it
    --color              -> True
    --no-color           -> False
    (nothing)            -> None, so fall back to isatty()

That is only possible because the default is ``None``. With a two-state boolean
the layers are indistinguishable.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping


def env_int(name: str, fallback: int) -> Callable[[str], int]:
    """A ``type=`` that reports env-variable problems as usage errors.

    ``int(os.environ["X"])`` raises at import time and stops the tool starting.
    Converting inside ``type=`` means the user gets
    ``argument --count: $MYAPP_COUNT='abc' is not an integer`` instead.
    """
    del fallback  # the fallback is handled by the caller via default=

    def parse(raw: str) -> int:
        try:
            return int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"${name}={raw!r} is not an integer"
            ) from None

    return parse


def env_flag(name: str, *, default: bool = False) -> Callable[[str], bool]:
    """Parse a boolean env variable, rejecting anything ambiguous.

    ``bool("false")`` is ``True``. Users write ``=0``, ``=no``, ``=off`` and
    ``=False``, so all of them must be honoured -- and an unrecognised value must
    be an error rather than a silent ``True``.
    """
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off"}

    def parse(raw: str) -> bool:
        lowered = raw.strip().lower()
        if lowered in truthy:
            return True
        if lowered in falsy:
            return False
        raise argparse.ArgumentTypeError(
            f"${name}={raw!r} is not a boolean; use 1/0, true/false, yes/no, on/off"
        )

    del default
    return parse


def apply_env_layer(
    parser: argparse.ArgumentParser,
    env: Mapping[str, str],
    *,
    prefix: str = "MYAPP_",
) -> dict[str, str]:
    """Install environment values as parser defaults. Returns what was applied.

    Because the values become *defaults*, argv still wins with no extra code, and
    the precedence ``defaults < env < argv`` holds automatically.

    The one subtlety: for each action, the env var name is
    ``<prefix><DEST.UPPER()>``, so ``--log-level`` reads ``MYAPP_LOG_LEVEL``.
    Values are coerced through the action's own ``type`` when it has one, so
    ``MYAPP_TIMEOUT=2.5`` becomes a float rather than the string ``"2.5"``.

    This is the shape to use when you also want to *record* which options had an
    environment source -- the return value is exactly that list, which is what
    ``--show-config`` needs.
    """
    applied: dict[str, str] = {}
    for action in parser._actions:
        if action.dest == argparse.SUPPRESS or not action.option_strings:
            continue
        name = f"{prefix}{action.dest.upper()}"
        if name not in env:
            continue

        raw = env[name]
        value: object = raw
        if action.type is not None:
            try:
                # Convert now so the namespace holds the right type even when the
                # value came from the environment rather than from argv.
                value = action.type(raw)  # type: ignore[misc]
            except (argparse.ArgumentTypeError, ValueError, TypeError):
                # Leave it as the raw string; validate after parsing so the error
                # is reported in one place with the other config errors.
                value = raw
        action.default = value
        applied[action.dest] = name
    return applied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myapp",
        description="Lesson 30: environment variables as defaults.",
        epilog=(
            "Environment:\n"
            "  MYAPP_LOG_LEVEL   MYAPP_WORKERS   MYAPP_TIMEOUT\n"
            "  MYAPP_COLOR       MYAPP_QUIET\n"
            "\n"
            "Precedence: defaults < environment < argv\n"
            "Run with --show-config to see which options came from where."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log-level", default="warning",
                        metavar="LEVEL", help="log level (env: MYAPP_LOG_LEVEL)")
    parser.add_argument("--workers", type=int, default=4, metavar="N",
                        help="worker count (env: MYAPP_WORKERS)")
    parser.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS",
                        help="request timeout (env: MYAPP_TIMEOUT)")
    parser.add_argument("--color", action=argparse.BooleanOptionalAction,
                        default=None,
                        help="colorize (env: MYAPP_COLOR=1/0; tri-state)")
    parser.add_argument("--quiet", action="store_true", default=False,
                        help="suppress output (env: MYAPP_QUIET)")
    parser.add_argument("--show-config", action="store_true",
                        help="print the resolved configuration")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    applied = apply_env_layer(parser, os.environ, prefix="MYAPP_")
    args = parser.parse_args(argv)

    if args.show_config:
        print("resolved configuration:")
        for key in sorted(vars(args)):
            source = f"${applied[key]}" if key in applied else (
                "argv" if key in sys.argv else "default"
            )
            print(f"  {key:<12} = {getattr(args, key)!r:<10} from {source}")
        return 0

    print(f"log_level: {args.log_level}")
    print(f"workers  : {args.workers}")
    print(f"timeout  : {args.timeout}")
    print(f"color    : {args.color!r}   (None means 'not specified')")
    print(f"quiet    : {args.quiet}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. An env value as a default: argv still wins, for free")
    print("=" * 78)
    os.environ["MYAPP_LOG_LEVEL"] = "debug"
    p = build_parser()
    apply_env_layer(p, os.environ, prefix="MYAPP_")
    for argv in ([], ["--log-level", "error"]):
        print(f"  env='debug', argv={argv!s:<26} -> log_level="
              f"{p.parse_args(argv).log_level!r}")
    print()
    print("  No merge code: the environment became the default, and an argv token")
    print("  always overwrites a default.")
    del os.environ["MYAPP_LOG_LEVEL"]

    print()
    print("=" * 78)
    print("2. A string default IS converted by type= (a useful quirk)")
    print("=" * 78)
    p = build_parser()
    p.set_defaults(timeout="2.5")
    print(f"  set_defaults(timeout='2.5') -> {p.parse_args([]).timeout!r}  "
          f"({type(p.parse_args([]).timeout).__name__})")
    p.set_defaults(timeout=2.5)
    print(f"  set_defaults(timeout=2.5)   -> {p.parse_args([]).timeout!r}")
    print()
    print("  argparse converts a default only when it is a str AND the namespace")
    print("  value is still that same object. Environment strings therefore get")
    print("  converted -- which is why the env-as-default trick is safe.")
    print("  Lesson 29's TOML int defaults are the case that is NOT safe.")

    print()
    print("=" * 78)
    print("3. Naming: prefix + upper + hyphens to underscores")
    print("=" * 78)
    p = build_parser()
    applied = apply_env_layer(
        p,
        {
            "MYAPP_LOG_LEVEL": "info",
            "MYAPP_WORKERS": "8",
            "MYAPP_TIMEOUT": "1.5",
            "PATH": "/ignored",
            "WORKERS": "999",
        },
        prefix="MYAPP_",
    )
    args = p.parse_args([])
    for dest, var in sorted(applied.items()):
        print(f"  ${var:<20} -> args.{dest:<10} = {getattr(args, dest)!r}")
    print()
    print("  Only MYAPP_-prefixed names are considered, so PATH and a bare WORKERS")
    print("  are untouched. This is why an unprefixed convention is a bad idea.")

    print()
    print("=" * 78)
    print("4. Booleans: bool('false') is True")
    print("=" * 78)
    for raw in ("1", "true", "yes", "on", "0", "false", "no", "off", "maybe"):
        try:
            print(f"  env_flag('X')({raw!r:<8}) -> {env_flag('X')(raw)!r}")
        except argparse.ArgumentTypeError as exc:
            print(f"  env_flag('X')({raw!r:<8}) -> ERROR: {exc}")
    print()
    print("  Every one of those spellings is something a user will type.")

    print()
    print("=" * 78)
    print("5. Tri-state booleans make the layering unambiguous")
    print("=" * 78)
    p = build_parser()
    for env, argv, label in (
        ({}, [], "nothing"),
        ({"MYAPP_COLOR": "0"}, [], "env=0"),
        ({"MYAPP_COLOR": "1"}, [], "env=1"),
        ({}, ["--color"], "--color"),
        ({}, ["--no-color"], "--no-color"),
        ({"MYAPP_COLOR": "1"}, ["--no-color"], "env=1 + --no-color"),
    ):
        apply_env_layer(p, env, prefix="MYAPP_")
        print(f"  env={env!s:<26} argv={argv!s:<16} -> color={p.parse_args(argv).color!r}   ({label})")
    print()
    print("  The last row is the one a two-state boolean cannot express: the argv")
    print("  flag must win over the environment, and it does.")

    print()
    print("=" * 78)
    print("6. Documenting the environment in --help")
    print("=" * 78)
    for line in build_parser().format_help().splitlines():
        if "env:" in line:
            print(f"  {line.rstrip()}")
    print()
    print("  argparse_mastery.config.explain_env(parser) generates these suffixes")
    print("  for a whole parser after the fact.")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
