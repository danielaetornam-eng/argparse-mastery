"""Layered configuration with provenance.

Precedence, least specific first::

    code defaults  <  config file  <  environment  <  argv

Two implementation strategies exist (lesson 29), and this module implements the
one that keeps the *sources*:

* ``set_defaults`` + parse argv -- ten lines, argv wins for free, but you cannot
  answer "where did this value come from?".
* An explicit resolver -- more code, and the only approach that supports
  ``config show`` and secret redaction.

A production CLI wants the second. That is what is here.

The type-conversion trap
------------------------
argparse applies ``type=`` to a default **only if the default is a string**. From
Python's own source, at the end of ``parse_known_args``::

    if action.default is not None and isinstance(action.default, str) \\
       and getattr(namespace, action.dest) is action.default:
        setattr(namespace, action.dest, self._get_value(action, action.default))

So a TOML file writing ``retries = 0`` (an int) bypasses every validator, while the
same file writing ``retries = "0"`` runs them. ``validate_against_parser`` closes
the gap by running *every* non-argv value through the parser's own ``type`` and
``choices`` before it is installed.

Unknown keys are collected rather than dropped
---------------------------------------------
A config file whose keys do not match any option does nothing, silently, for
months. :attr:`ConfigLayer.unused` keeps them so ``config show`` can warn. That one
behaviour is worth more than it sounds.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import TaskRunnerError
from .exit_codes import ExitCode

__all__ = [
    "ConfigLayer",
    "SecretRedactor",
    "flatten",
    "load_config_file",
    "validate_against_parser",
]

ENV_PREFIX = "TASKRUNNER_"

#: Substrings that mark a key as sensitive. Crude, and honest about it: it catches
#: api_token, db_password and friends, and it will not catch a secret stored under
#: an innocuous name. That is why redaction is the default, not opt-in.
SECRET_HINTS = ("token", "secret", "password", "passwd", "credential", "key")


def flatten(data: Mapping[str, Any], prefix: str = "", separator: str = "_") -> dict[str, Any]:
    """``{"log": {"level": "debug"}}`` -> ``{"log_level": "debug"}``.

    CLI dests are underscored (``--log-level`` -> ``log_level``) while config files
    nest. Doing the mapping in one documented function beats scattering
    ``cfg["log"]["level"]`` lookups, and it is what lets a nested TOML table drive
    flat command-line options.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        composed = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(flatten(value, composed, separator))
        else:
            out[composed] = value
    return out


def load_config_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a config file, choosing the format by suffix.

    ==========  =========================================================
    Suffix      Format
    ==========  =========================================================
    ``.json``   :mod:`json`
    ``.toml``   :mod:`tomllib` (3.11+, stdlib)
    ``.ini``    :mod:`configparser`; ``[section] key`` becomes ``section_key``
    ``.env``    ``KEY=value`` lines, ``#`` comments, ``export`` tolerated
    ==========  =========================================================

    YAML is deliberately absent: it would add a runtime dependency to the CLI for
    the sake of a config format. Refusing loudly on an unknown suffix is better
    than silently ignoring a file the user believes is being applied.
    """
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise TaskRunnerError(f"config file not found: {file_path}", ExitCode.MISSING)

    suffix = file_path.suffix.lower()
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TaskRunnerError(
            f"{file_path}: not valid UTF-8 ({exc.reason})", ExitCode.CONFIG
        ) from exc

    if suffix == ".json":
        raw = _loads_json(text, file_path)
    elif suffix == ".toml":
        raw = _loads_toml(text, file_path)
    elif suffix in (".ini", ".cfg"):
        raw = _loads_ini(text)
    elif suffix == ".env":
        raw = _loads_dotenv(text)
    else:
        raise TaskRunnerError(
            f"{file_path}: unsupported format {suffix!r}; "
            f"expected .json, .toml, .ini, .cfg or .env",
            ExitCode.CONFIG,
        )

    if not isinstance(raw, dict):
        raise TaskRunnerError(
            f"{file_path}: the top level of a config file must be a mapping",
            ExitCode.CONFIG,
        )
    return flatten(raw)


def _loads_json(text: str, path: Path) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskRunnerError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            ExitCode.CONFIG,
        ) from exc


def _loads_toml(text: str, path: Path) -> Any:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - requires-python is >=3.11
        raise TaskRunnerError(
            "TOML config requires Python 3.11+ (tomllib)", ExitCode.CONFIG
        ) from None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TaskRunnerError(f"{path}: invalid TOML: {exc}", ExitCode.CONFIG) from exc


def _loads_ini(text: str) -> dict[str, Any]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise TaskRunnerError(f"invalid INI: {exc}", ExitCode.CONFIG) from exc
    # `_` rather than `.`, because the flattened form has to match a CLI dest.
    # `[run] retries = 5` becomes `run_retries`, which is exactly `--run-retries`'s
    # dest. Emitting `run.retries` would match no option and be silently ignored.
    return {
        f"{section}_{key}": _coerce(value)
        for section in parser.sections()
        for key, value in parser[section].items()
    }


def _loads_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip().strip("'\"")
    return out


def _coerce(raw: str) -> Any:
    """Turn INI/``.env`` strings into real scalars.

    INI and ``.env`` are string-only formats, so without this every numeric option
    arrives as ``str`` and validators see the wrong type. Deliberately
    conservative: ``"007"`` stays a string (probably a zip code, not the number
    seven) and ``"1e5"`` stays a string (ambiguous with a version).
    """
    lowered = raw.strip().lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", "~", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    if lowered in ("inf", "-inf", "nan") or "e" in lowered or "x" in lowered:
        return raw
    try:
        return float(raw)
    except ValueError:
        return raw


def validate_against_parser(
    parser: argparse.ArgumentParser, values: Mapping[str, Any]
) -> dict[str, Any]:
    """Run every non-argv value through the parser's own ``type`` and ``choices``.

    This is what makes layered configuration *safe*. Without it, a ``.toml`` file
    silently bypasses every validator you wrote, and the difference between a
    ``.json`` file and a ``.toml`` file expressing the same setting is whether your
    validation ran.

    **Which parser you pass matters.** Subcommand options (``--retries``, which
    lives on ``run``) are invisible to the top-level parser, so validating against
    it would drop those keys as "unknown" and skip their validators. Pass a parser
    that knows the whole command -- see
    :func:`taskrunner.app.parser_for_command`.

    Returns the validated mapping, ready for ``parser.set_defaults(**result)``.
    Keys the parser does not declare are dropped here and reported separately by
    :class:`ConfigLayer`.
    """
    actions = {a.dest: a for a in parser._actions if a.dest != argparse.SUPPRESS}
    validated: dict[str, Any] = {}

    for key, value in values.items():
        action = actions.get(key)
        if action is None:
            continue

        if action.type is not None:
            # Validate BOTH string and already-typed values.
            #
            # argparse itself only converts a default when it is a `str`, which is
            # precisely the trap: a `.toml` file writing `retries = 99` arrives as
            # an int and would skip `type=` entirely. Stringifying and re-running
            # the converter closes that gap.
            #
            # The limitation is honest and worth stating: this assumes `type=` is
            # a *string* converter (which the argparse contract guarantees), so
            # round-tripping through `str()` is lossless for the types a config
            # file can hold -- int, float, bool, str, and TOML's date types, which
            # will be rejected unless a custom type accepts them.
            try:
                value = action.type(value if isinstance(value, str) else str(value))
            except (argparse.ArgumentTypeError, ValueError, TypeError) as exc:
                label = ", ".join(action.option_strings) or key
                raise TaskRunnerError(
                    f"{label}: invalid value from configuration: {exc}",
                    ExitCode.CONFIG,
                ) from None

        if action.choices is not None and action.nargs != 0 and value not in action.choices:
            options = ", ".join(str(c) for c in action.choices)
            raise TaskRunnerError(
                f"{key}: {value!r} is not one of: {options}", ExitCode.CONFIG
            )

        validated[key] = value

    return validated


@dataclass
class ConfigLayer:
    """The resolved configuration, plus where each value came from."""

    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    unused: list[str] = field(default_factory=list)
    config_path: Path | None = None

    def explain(self, *, reveal: bool = False) -> str:
        """Render the table behind ``config show``.

        Ordering is by destination, and each row names its source, because
        "why is my setting not taking effect" is the question this command exists
        to answer.
        """
        redactor = SecretRedactor(reveal=reveal)
        lines = []
        if self.config_path:
            lines.append(f"config file: {self.config_path}")
        elif self.sources:
            lines.append("config file: (none)")
        lines.append("")
        width = max((len(k) for k in self.values), default=1)
        for key in sorted(self.values):
            shown = redactor(key, self.values[key])
            lines.append(f"  {key:<{width}}  {shown!r:<24} from {self.sources.get(key, '?')}")
        if self.unused:
            lines += ["", "ignored (no matching option):"]
            lines += [f"  {item}" for item in sorted(self.unused)]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class SecretRedactor:
    """Replace sensitive values with ``***`` unless explicitly revealed."""

    reveal: bool = False

    def __call__(self, key: str, value: Any) -> Any:
        if self.reveal:
            return value
        lowered = key.lower()
        return "***" if any(hint in lowered for hint in SECRET_HINTS) else value


def load_layered_config(
    parser: argparse.ArgumentParser,
    *,
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
    known_keys: set[str] | None = None,
) -> ConfigLayer:
    """Merge the non-argv layers and install them as parser defaults.

    Order of operations, and why each step is where it is:

    1. **Locate** the config file -- the caller normally passes it, having found it
       with a pre-parser (see :func:`taskrunner.cli.find_config_argv`).
    2. **Load** it, choosing the format by suffix.
    3. **Overlay** the environment, which sits *above* the file so CI can override
       a checked-in config without editing it.
    4. **Validate** everything merged, through the parser's own ``type`` and
       ``choices``.
    5. **Install** as defaults -- after which argv wins automatically, because
       argparse overwrites defaults as it consumes tokens.

    Note that step 5 is what removes all the precedence code. Nothing else in this
    function is about ordering between layers; that is argparse's job.
    """
    environment = os.environ if env is None else env
    argv = list(argv or [])

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    unused: list[str] = []
    resolved_path: Path | None = None

    if config_path:
        file_values = load_config_file(config_path)
        resolved_path = Path(config_path).expanduser()
        values.update(file_values)
        sources.update(dict.fromkeys(file_values, f"config:{resolved_path.name}"))

    env_values = {
        key[len(ENV_PREFIX) :].lower(): value
        for key, value in environment.items()
        if key.startswith(ENV_PREFIX)
    }
    # The environment overrides the file.
    values.update(env_values)
    sources.update({k: f"env:${ENV_PREFIX}{k.upper()}" for k in env_values})

    declared = known_keys or {a.dest for a in parser._actions if a.dest != argparse.SUPPRESS}
    unused = [k for k in values if k not in declared]

    validated = validate_against_parser(parser, values)
    for key in validated:
        sources.setdefault(key, "default")

    parser.set_defaults(**validated)
    del argv  # argv precedence is handled by argparse itself

    return ConfigLayer(validated, sources, unused, resolved_path)
