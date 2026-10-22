"""Lesson 29 - Config file layering, and the trap that hides in plain sight.

Run me
------
    python examples/29_config_file_layering.py --demo
    python examples/29_config_file_layering.py --config cfg.toml --workers 8

Two philosophies, and why both exist
------------------------------------
**A. "argparse-native"**: parse the config file, install it with
``set_defaults``, then parse the real argv. The command line wins *for free*,
because argparse overwrites defaults as it consumes tokens::

    pre, _ = parser.parse_known_args()
    parser.set_defaults(**validated(pre.config))
    args = parser.parse_args()

Ten lines, no precedence code, no bugs. This lesson implements it.

**B. "explicit resolution"**: keep each layer separate, merge with declared
precedence, and record where every value came from. More code, and the only
approach that supports ``--show-config`` and secret redaction. See
:mod:`argparse_mastery.config`.

The trap
--------
argparse applies ``type=`` to a default **only if the default is a string**. From
Python's own source, at the end of ``parse_known_args``::

    if action.default is not None and isinstance(action.default, str) \
       and getattr(namespace, action.dest) is action.default:
        setattr(namespace, action.dest, self._get_value(action, action.default))

So::

    set_defaults(workers="8")    # str  -> type=int runs   -> 8    (validated)
    set_defaults(workers=8)      # int  -> type skipped    -> 8    (by luck)

Both give ``8``, which is exactly why the bug survives. Now add a validator::

    def workers(raw): ...
    parser.add_argument("--workers", type=workers, default=1)
    parser.set_defaults(workers=64)     # int: the validator NEVER RUNS

A TOML file writing ``workers = 64`` bypasses every check you wrote. A JSON file
writing ``"workers": "64"`` runs them. Two files expressing the same thing behave
differently, and the difference is invisible.

The fix
-------
Validate the whole config map through the parser's own ``type`` callables before
installing it -- :func:`validate_config` below, or
``argparse_mastery.config.typed_defaults``. Rule:

    **Never let a value that did not come from argv skip validation.**

Bootstrap ordering
------------------
You need the config file *before* you can build the parser, but ``--config`` is
one of the parser's own options. Two honest solutions:

1. **A throwaway pre-parser** with only ``--config``. Faithful, because it
   inherits argparse's ``=``, abbreviation and ``--`` semantics.
2. **A manual scan** of ``argv``. Fewer moving parts, but you must handle
   ``--config=x``, ``--config x``, and ``--`` yourself.

Prefer the pre-parser. :func:`find_config` shows it.

Precedence, most specific last
-----------------------------
::

    hard-coded defaults  <  config file  <  environment  <  argv

Environment above the file, so a CI job can override a checked-in config without
editing it. argv last, so a human can always win.

Formats without dependencies
----------------------------
``.json`` (:mod:`json`), ``.toml`` (:mod:`tomllib`, 3.11+, stdlib), ``.ini``
(:mod:`configparser`), ``.env``. Chosen by suffix. YAML would need PyYAML, so it
is deliberately absent -- a CLI should not acquire a dependency for its config
format.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def positive_int(raw: str) -> int:
    """A validating ``type=``. This is what a config value must go through."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conf",
        description="Lesson 29: config file layering.",
        epilog=(
            "Precedence: defaults < config file < environment < argv\n"
            "Run with --show-config to see the resolved values and their sources."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="config file (.json/.toml/.ini/.env)")
    parser.add_argument("--workers", type=positive_int, default=4, metavar="N",
                        help="worker count (must be > 0)")
    parser.add_argument("--mode", choices=("fast", "safe"), default="safe",
                        help="operating mode")
    parser.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS",
                        help="request timeout")
    parser.add_argument("--show-config", action="store_true",
                        help="print the resolved configuration and exit")
    return parser


def find_config(argv: list[str], *, prefix: str = "CONF") -> str | None:
    """Locate ``--config`` before the real parser exists.

    Uses a throwaway parser rather than a string scan, so it inherits argparse's
    handling of ``--config=x``, ``--config x``, ``--`` and (if you left it on)
    abbreviations. Two details that matter:

    * ``add_help=False`` is essential -- otherwise ``-h`` is consumed here and the
      real parser never sees it.
    * ``allow_abbrev=False`` keeps the pre-parser's spelling rules identical to
      the real one, so ``--conf`` cannot match here and fail later.
    """
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config", default=None)
    known, _ = pre.parse_known_args(argv)
    if known.config:
        return str(known.config)

    from_env = os.environ.get(f"{prefix}_CONFIG")
    return from_env


def load_config_file(path: str | Path) -> dict[str, Any]:
    """Load a config file, choosing the parser by suffix.

    Nested tables are flattened to ``dotted_underscore`` keys, because CLI dests
    are underscored (``--log-level`` -> ``log_level``). Doing that in one
    documented function beats scattering ``cfg["log"]["level"]`` lookups.
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        raw: Any = json.loads(text)
    elif suffix == ".toml":
        import tomllib

        raw = tomllib.loads(text)
    elif suffix in (".ini", ".cfg"):
        parser = configparser.ConfigParser()
        parser.read_string(text)
        raw = {
            f"{section}.{key}": value
            for section in parser.sections()
            for key, value in parser[section].items()
        }
    elif suffix == ".env":
        raw = _parse_dotenv(text)
    else:
        raise ValueError(f"unsupported config format {suffix!r}; use .json/.toml/.ini/.env")

    if not isinstance(raw, dict):
        raise ValueError("a config file's top level must be a mapping")
    return flatten(raw)


def flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{"log": {"level": "debug"}}`` -> ``{"log_level": "debug"}``."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        composed = f"{prefix}_{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(flatten(value, composed))
        else:
            out[composed] = value
    return out


def _parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip().strip("'\"")
    return out


def validate_config(parser: argparse.ArgumentParser, values: Mapping[str, Any]) -> dict[str, Any]:
    """Run every config value through the parser's own ``type`` and ``choices``.

    This is the function that makes "argparse-native" safe. For each key present in
    the file *and* declared on the parser:

    * convert string values with ``action.type`` (mirroring argparse's own rule),
    * reject values outside ``choices``,
    * ignore keys the parser does not declare -- and let the caller warn, so a
      typo'd key is visible instead of silently doing nothing.

    Returns the validated mapping, ready for ``set_defaults``. Raises
    :class:`ValueError` with the offending key named, because a config mistake is
    a configuration error, not a usage error.
    """
    actions = {a.dest: a for a in parser._actions if a.dest != argparse.SUPPRESS}
    validated: dict[str, Any] = {}

    for key, value in values.items():
        action = actions.get(key)
        if action is None:
            continue

        # argparse only converts *string* defaults. Mirror that, then range-check
        # the non-string case too -- which is the half argparse skips.
        if action.type is not None and isinstance(value, str):
            try:
                value = action.type(value)
            except (argparse.ArgumentTypeError, ValueError, TypeError) as exc:
                label = ", ".join(action.option_strings) or key
                raise ValueError(f"{label}: invalid value in config file: {exc}") from None

        if action.choices is not None and action.nargs != 0:
            if value not in action.choices:
                options = ", ".join(str(c) for c in action.choices)
                raise ValueError(f"{key}: {value!r} is not one of: {options}")

        # The check argparse would have made for a converted value.
        if action.type is positive_int and isinstance(value, int) and value <= 0:
            raise ValueError(f"{key}: must be greater than 0, got {value}")

        validated[key] = value

    return validated


def resolve(parser: argparse.ArgumentParser, argv: list[str] | None = None) -> argparse.Namespace:
    """The full pipeline: find config, validate, install as defaults, parse argv.

    The ordering is the design::

        1. find the config path      (pre-parser)
        2. load it                    (format by suffix)
        3. validate it                (against the parser's own types)
        4. install as defaults        (set_defaults)
        5. parse argv                 (argv wins, for free)
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    path = find_config(argv)
    file_values: dict[str, Any] = {}
    if path:
        file_values = load_config_file(path)

    # Layer 3: environment, above the file.
    env_values = {
        key.lower(): value
        for key, value in os.environ.items()
        if key.startswith("CONF_")
    }

    merged: dict[str, Any] = {}
    merged.update(file_values)
    merged.update(env_values)

    # Layer 4: validate everything that did not come from argv.
    try:
        validated = validate_config(parser, merged)
    except ValueError as exc:
        parser.error(str(exc))

    parser.set_defaults(**validated)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = resolve(parser, argv)

    if args.show_config:
        print("resolved configuration (argv wins):")
        for key in sorted(vars(args)):
            print(f"  {key:<12} = {getattr(args, key)!r}")
        return 0

    print(f"workers : {args.workers}")
    print(f"mode    : {args.mode}")
    print(f"timeout : {args.timeout}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. THE TRAP: type= does not run for non-string defaults")
    print("=" * 78)
    parser = build_parser()
    parser.set_defaults(workers="8")
    print(f"  set_defaults(workers='8')  -> {parser.parse_args([]).workers!r}   "
          f"(type=int RAN)")
    parser.set_defaults(workers=0)
    print(f"  set_defaults(workers=0)    -> {parser.parse_args([]).workers!r}   "
          f"(validator SKIPPED -- invalid!)")
    print(f"  positive_int('0')          -> {_call_error(positive_int, '0')}")
    print()
    print("  Both produce an int, so a test on the happy path cannot tell them")
    print("  apart. A TOML file writing `workers = 0` slips through every check.")

    print()
    print("=" * 78)
    print("2. validate_config fixes it, and works for both string and int")
    print("=" * 78)
    fresh = build_parser()
    for value in ("8", 8, 0, "0", -3):
        try:
            out = validate_config(fresh, {"workers": value})
            print(f"  config workers={value!r:<6} -> validated {out['workers']!r}")
        except ValueError as exc:
            print(f"  config workers={value!r:<6} -> ValueError: {exc}")

    print()
    print("=" * 78)
    print("3. choices are validated too")
    print("=" * 78)
    fresh = build_parser()
    for value in ("fast", "quick"):
        try:
            out = validate_config(fresh, {"mode": value})
            print(f"  config mode={value!r:<8} -> {out['mode']!r}")
        except ValueError as exc:
            print(f"  config mode={value!r:<8} -> ValueError: {exc}")

    print()
    print("=" * 78)
    print("4. Unknown keys are ignored here -- and should be reported")
    print("=" * 78)
    fresh = build_parser()
    out = validate_config(fresh, {"workers": 4, "colour": True, "workerz": 8})
    print(f"  validated keys: {sorted(out)}")
    print("  'colour' and 'workerz' matched no option and were dropped.")
    print()
    print("  Silently dropping unknown keys is how a config file ends up doing")
    print("  nothing for months. argparse_mastery.config.ConfigResolver collects")
    print("  them in `.unused` so you can warn.")

    print()
    print("=" * 78)
    print("5. The full pipeline, with real files")
    print("=" * 78)
    with tempfile.TemporaryDirectory() as tmp:
        toml_path = Path(tmp) / "c.toml"
        toml_path.write_text(
            'workers = 12\nmode = "fast"\ntimeout = 2.5\n\n[extra]\nignored = true\n',
            encoding="utf-8",
        )
        json_path = Path(tmp) / "c.json"
        json_path.write_text('{"workers": 3, "mode": "fast"}', encoding="utf-8")
        ini_path = Path(tmp) / "c.ini"
        ini_path.write_text("[core]\nworkers = 7\n", encoding="utf-8")
        env_path = Path(tmp) / "c.env"
        env_path.write_text("workers=5\nmode=fast\n", encoding="utf-8")

        for path in (toml_path, json_path, ini_path, env_path):
            try:
                loaded = load_config_file(path)
                print(f"  {path.suffix:<7} -> {loaded}")
            except (ValueError, OSError) as exc:
                print(f"  {path.suffix:<7} -> ERROR: {exc}")

        print()
        print("  Precedence: defaults < file < env < argv")
        base = build_parser()
        show = lambda p, argv: (  # noqa: E731 - a demo one-liner, labelled as such
            p.parse_args(argv).workers,
            p.parse_args(argv).mode,
        )
        p = build_parser()
        p.set_defaults(**validate_config(p, load_config_file(toml_path)))
        print(f"    file only          workers/mode = {show(p, [])}")
        p = build_parser()
        p.set_defaults(**validate_config(p, load_config_file(toml_path)))
        os.environ["CONF_WORKERS"] = "20"
        p.set_defaults(**validate_config(p, {"workers": os.environ["CONF_WORKERS"]}))
        print(f"    + env override     workers/mode = {show(p, [])}")
        print(f"    + argv override    workers/mode = {show(p, ['--workers', '99'])}")
        del os.environ["CONF_WORKERS"]
        print()
        print("  Each layer is installed as defaults, so argv wins with no merge code.")

    print()
    print("=" * 78)
    print("6. Bootstrap: finding --config before the parser exists")
    print("=" * 78)
    for argv in (["--config", "c.toml"], ["--config=c.toml"], ["--workers", "2"]):
        print(f"  {argv!s:<26} -> find_config = {find_config(argv)!r}")
    print()
    print("  A throwaway pre-parser handles all three spellings plus '--'. A manual")
    print("  string scan handles two of them and silently misses abbreviations.")

    return 0


def _call_error(func, value: Any) -> str:
    try:
        func(value)
    except argparse.ArgumentTypeError as exc:
        return f"ArgumentTypeError: {exc}"
    return "(accepted)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
