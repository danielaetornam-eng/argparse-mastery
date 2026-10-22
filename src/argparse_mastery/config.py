"""Configuration layering: defaults < file < environment < command line.

The two philosophies, and why this module implements both
--------------------------------------------------------
**Philosophy A -- "argparse-native".** Parse the config file first, install it
with :meth:`ArgumentParser.set_defaults`, then parse the real ``argv``. Command
line wins *for free*, because argparse overwrites defaults as it consumes
tokens::

    args, _ = parser.parse_known_args()
    parser.set_defaults(**{k: v for k, v in file_config.items() if hasattr(args, k)})
    args = parser.parse_args()

No merge code, no precedence bugs. The cost is one quirk, documented under
*Defaults bypass type conversion* below.

**Philosophy B -- "explicit resolution".** Keep each layer separate, then merge
with declared precedence and *record the provenance* of every value so
``myapp config show`` can explain where a setting came from. More code, more
control, and the only approach that supports ``--show-config`` and secret
redaction. :class:`ConfigResolver` is this.

When to use which
-----------------
* A single-file, developer tool: Philosophy A. It is ten lines.
* Anything with profiles, ``--show-config``, or secrets: Philosophy B.

Precedence, most specific last
-----------------------------
::

    hard-coded defaults  <  --profile defaults  <  config file  <  environment  <  argv

The default file lives at ``$XDG_CONFIG_HOME/<prog>/config.toml`` (or
``~/.config/<prog>/config.toml`` on Linux, ``%APPDATA%\\<prog>\\config.toml`` on
Windows), with the conventional CLI overrides:
``--config PATH``, ``--profile NAME``, ``$<PROG>_CONFIG``, ``$<PROG>_PROFILE``.

The defaults-bypass-type-conversion trap
----------------------------------------
argparse applies ``type=`` to a default **only if the default is a ``str``**
(and only if the value in the namespace is still identical to the default).
Verified on 3.14::

    parser.add_argument("--port", type=int, default=1)
    parser.set_defaults(port="8080")   # str  -> converted -> 8080  (int)
    parser.set_defaults(port=8080)     # int  -> skipped   -> 8080  (int, by luck)

So a TOML file that writes ``port = 8080`` bypasses your validator, and one that
writes ``port = "8080"`` runs it. Two files with the same meaning behave
differently. :func:`typed_defaults` fixes this by validating every value from a
non-argv layer through the parser's own ``type`` callables. Use it.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "ENV_PREFIX",
    "ConfigResolver",
    "SourcedValue",
    "default_config_path",
    "discover_config",
    "load_config_file",
    "parse_key_value_overrides",
    "typed_defaults",
]

#: Prefix for environment variables, e.g. ``MYAPP_LOG_LEVEL``.
ENV_PREFIX = "MYAPP"

#: Suffix for the environment variable naming an explicit config file.
CONFIG_ENV_SUFFIX = "CONFIG"

#: Suffix for the environment variable naming the active profile.
PROFILE_ENV_SUFFIX = "PROFILE"


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def load_config_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a config file into a flat ``dict``.

    Formats are chosen by **suffix**, so a user is never asked to guess:

    ==========  ==========================================================
    Suffix      Format
    ==========  ==========================================================
    ``.json``   :mod:`json` (stdlib, no dependencies)
    ``.toml``   :mod:`tomllib` (stdlib since 3.11; read-only, as intended)
    ``.ini``    :mod:`configparser`; ``[section] key`` becomes ``section_key``
    ``.cfg``    same as ``.ini``
    ``.env``    ``KEY=value`` lines, ``#`` comments, ``export`` tolerated
    ==========  ==========================================================

    Nested structures are flattened with ``_`` (see :func:`flatten_mapping`),
    because only the flattened form can match a CLI dest: ``--log-level`` gives
    ``log_level``, and ``[log] level = x`` gives ``log_level`` too.

    INI and ``.env`` are string-only formats, so their scalars are coerced to
    ``bool``/``int``/``float``/``None`` on the way in. Without that, every numeric
    option arrives as a ``str`` and your validators see the wrong type.

    Raises :class:`ValueError` for an unknown suffix -- refusing loudly beats
    silently ignoring a config file the user believes is being applied.
    """
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise ValueError(f"config file not found: {file_path}")

    suffix = file_path.suffix.lower()
    text = file_path.read_text(encoding="utf-8")

    if suffix == ".json":
        data = json.loads(text)
    elif suffix == ".toml":
        # tomllib is stdlib from 3.11, which is this package's floor, so no
        # version gate is needed here; the import stays local because it is only
        # reachable on the .toml branch.
        import tomllib

        data = tomllib.loads(text)
    elif suffix in (".ini", ".cfg"):
        data = _load_ini(text)
    elif suffix == ".env":
        data = _load_dotenv(text)
    else:
        raise ValueError(
            f"unsupported config format {suffix!r} for {file_path}; "
            f"expected one of .json, .toml, .ini, .cfg, .env"
        )

    if not isinstance(data, dict):
        raise ValueError(f"{file_path}: top level of a config file must be a mapping")
    return flatten_mapping(data)


def _load_ini(text: str) -> dict[str, Any]:
    """Read INI into ``{"section_key": value}``, coercing obvious scalars.

    ``_`` rather than ``.`` as the joiner, because the flattened key has to match
    a CLI dest: ``[run] retries = 5`` must become ``run_retries``.

    A ``DEFAULT`` section is merged into every other section by configparser,
    which is almost always what an INI author intends, so it is preserved.
    """
    parser = configparser.ConfigParser()
    parser.read_string(text)
    out: dict[str, Any] = {}
    for section in parser.sections():
        for key, raw in parser[section].items():
            out[f"{section}_{key}"] = _coerce_scalar(raw)
    return out


def _load_dotenv(text: str) -> dict[str, Any]:
    """Read ``KEY=value`` lines, honouring ``#`` comments and optional ``export``.

    Keys are lower-cased, because ``.env`` conventionally uses upper case and the
    destination it has to match is a lower-case CLI dest: ``LOG_LEVEL=debug``
    becomes ``log_level``.
    """
    out: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip().lower()
        if key:
            out[key] = _coerce_scalar(value.strip().strip("'\""))
    return out


def _coerce_scalar(raw: str) -> Any:
    """Turn ``"true"``/``"42"``/``"null"`` into real Python values.

    INI and ``.env`` are string-only formats, so without this every numeric
    option arrives as ``str`` and your validators see the wrong type. Note the
    deliberate conservatism, because a wrong guess here is worse than leaving a
    string:

    * ``"007"`` stays a string -- far more likely a zip code or an identifier
      than the number seven.
    * ``"1e5"`` stays a string -- ambiguous with a version.
    * ``"0x1f"`` stays a string -- ambiguous with a hash prefix.
    """
    lowered = raw.strip().lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", "~", ""):
        return None

    text = raw.strip()
    if not text:
        return raw

    # Reject leading zeros before int(), which would happily accept them.
    unsigned = text[1:] if text[:1] in "+-" else text
    if len(unsigned) > 1 and unsigned[0] == "0" and unsigned[1] != ".":
        return raw

    try:
        return int(text)
    except ValueError:
        pass
    if "e" in lowered or "x" in lowered:
        return raw
    try:
        return float(text)
    except ValueError:
        return raw


def flatten_mapping(
    data: Mapping[str, Any],
    *,
    separator: str = "_",
    prefix: str = "",
) -> dict[str, Any]:
    """Flatten nested mappings into ``dotted`` dests.

    ``{"log": {"level": "debug"}}`` -> ``{"log_level": "debug"}``.

    The separator is configurable because CLI dests are conventionally
    underscored (``--log-level`` -> ``log_level``), while config files
    conventionally nest. Mapping nested ``log.level`` to ``log_level`` is the
    bridge, and doing it in one documented function beats scattering ``["log"]
    ["level"]`` indexing across the codebase.

    Lists and scalars pass through untouched.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        composed = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(flatten_mapping(value, separator=separator, prefix=composed))
        else:
            out[composed] = value
    return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def default_config_path(prog: str = "myapp", *, env: Mapping[str, str] | None = None) -> Path:
    """Return the conventional per-user config path for ``prog``.

    Uses ``XDG_CONFIG_HOME`` on every platform (Windows honours it too, which is
    a genuine convenience for people who share a config across a WSL/Windows
    setup), falling back to ``~/.config``.
    """
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / prog / "config.toml"


def discover_config(
    argv: Sequence[str] | None = None,
    *,
    prog: str = "myapp",
    explicit: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    env_prefix: str = ENV_PREFIX,
) -> tuple[dict[str, Any], Path | None, str]:
    """Find and load the config file, returning ``(data, path_used, source)``.

    Resolution order (first hit wins):

    1. the ``explicit`` argument (typically ``--config``)
    2. ``--config PATH`` appearing anywhere in ``argv`` (hand-rolled scan)
    3. ``$<PREFIX>_CONFIG``
    4. ``$XDG_CONFIG_HOME/<prog>/config.toml``, if it exists

    Step 2 exists because of a bootstrapping problem: you need the config file
    *before* you can build the parser, but ``--config`` is one of the parser's
    own options. Two solutions, both legitimate:

    * the manual scan above (what this function does), or
    * a tiny throwaway "pre-parser" whose only job is to find ``--config``.

    See :func:`preparse_flags` for the throwaway-parser variant, which is more
    robust because it handles ``--config=x``, abbreviations, and ``--`` exactly
    the way argparse does.
    """
    env = os.environ if env is None else env
    argv = list(sys.argv[1:] if argv is None else argv)

    chosen: str | None = None
    source = ""

    if explicit is not None:
        chosen, source = str(explicit), "--config"
    elif (found := _scan_for_flag(argv, "--config")) is not None:
        chosen, source = found, "argv:--config"
    elif (from_env := env.get(f"{env_prefix}_{CONFIG_ENV_SUFFIX}")) is not None:
        chosen, source = from_env, f"${env_prefix}_{CONFIG_ENV_SUFFIX}"

    if chosen is not None:
        return load_config_file(chosen), Path(chosen).expanduser(), source

    fallback = default_config_path(prog, env=env)
    if fallback.is_file():
        return load_config_file(fallback), fallback, "xdg"

    return {}, None, "none"


def preparse_flags(argv: Sequence[str], flags: Iterable[str]) -> dict[str, str]:
    """Parse a handful of flags *before* the real parser exists.

    A throwaway parser is more faithful than string scanning because it inherits
    argparse's abbreviation, ``=``, and ``--`` semantics::

        pre = argparse.ArgumentParser(add_help=False)
        pre.add_argument("--config")
        pre.add_argument("--profile")
        found, _ = pre.parse_known_args(argv)

    Two caveats to internalise:

    * ``add_help=False`` is essential, otherwise ``-h`` is consumed here and the
      real parser never sees it.
    * ``allow_abbrev=False`` avoids surprises where ``--conf`` silently matches
      ``--config`` in the pre-parser but not in the real one. Set it and keep the
      two parsers' spelling rules identical.

    Returns only the flags found; unknown tokens are ignored, which is what makes
    it safe to run before full validation.
    """
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    for flag in flags:
        pre.add_argument(flag, default=None)
    known, _ = pre.parse_known_args(list(argv))
    return {k: v for k, v in vars(known).items() if v is not None}


def _scan_for_flag(argv: Sequence[str], flag: str) -> str | None:
    """Minimal ``flag value`` / ``flag=value`` scan. See :func:`preparse_flags`."""
    for index, token in enumerate(argv):
        if token == flag:
            return argv[index + 1] if index + 1 < len(argv) else None
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


# ---------------------------------------------------------------------------
# Explicit resolution with provenance
# ---------------------------------------------------------------------------


class SourcedValue(Mapping[str, Any]):
    """A single resolved value plus the layer it came from.

    ``SourcedValue`` is a ``Mapping`` over its one item so it can be splatted
    (``namespace, _ = defaults.resolve()``) while still answering "where did
    this come from?". That one capability is what powers ``--show-config``.
    """

    __slots__ = ("key", "source", "value")

    def __init__(self, key: str, value: Any, source: str) -> None:
        self.key = key
        self.value = value
        self.source = source

    def __iter__(self):
        yield self.key

    def __len__(self) -> int:
        return 1

    def __getitem__(self, item: str) -> Any:
        if item == self.key:
            return self.value
        raise KeyError(item)

    def __repr__(self) -> str:
        shown = "***" if _looks_secret(self.key) else repr(self.value)
        return f"{self.key}={shown} (from {self.source})"

    def __str__(self) -> str:
        return str(self.value)


_SECRET_HINTS = ("password", "passwd", "secret", "token", "key", "credential", "api")


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


class ConfigResolver:
    """Layered configuration with recorded provenance.

    ::

        resolver = ConfigResolver(prog="myapp")
        resolver.add_defaults({"log_level": "warning", "retries": 3})
        resolver.add_file(load_config_file(path), "config.toml")
        resolver.add_env(os.environ, prefix="MYAPP")
        resolved = resolver.resolve()

        setattr(ns, **{k: v for k, v in resolved.items()})
        resolved.sources["log_level"]   # -> "defaults" or "config.toml" or "env:MYAPP_LOG_LEVEL"

    Only keys that a parser actually declares are carried through
    (``known_keys``), so a typo'd key in a config file is reported rather than
    silently ignored. Ignoring unknown keys is how a config file ends up not
    doing anything and nobody noticing for months.
    """

    def __init__(
        self,
        *,
        prog: str = "myapp",
        known_keys: Iterable[str] | None = None,
        separator: str = "_",
    ) -> None:
        self.prog = prog
        self._known_keys = set(known_keys) if known_keys is not None else None
        self._separator = separator
        self._layers: list[tuple[str, Mapping[str, Any]]] = []

    # -- layer construction ----------------------------------------------

    def add_defaults(self, data: Mapping[str, Any]) -> ConfigResolver:
        """Lowest-priority layer: hard-coded values in code."""
        self._layers.append(("defaults", data))
        return self

    def add_file(self, data: Mapping[str, Any], source: str = "config file") -> ConfigResolver:
        """A config file layer. Later ``add_file`` calls win over earlier ones."""
        self._layers.append((source, flatten_mapping(data, separator=self._separator)))
        return self

    def add_env(
        self,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str | None = None,
    ) -> ConfigResolver:
        """Environment layer. ``MYAPP_LOG_LEVEL=debug`` -> key ``log_level``.

        Scans ``$<PREFIX>_*`` only, so unrelated variables can never collide.
        A var whose prefix-stripped name is not a known key is reported by
        :meth:`resolve` as an unused variable rather than dropped.
        """
        env = os.environ if env is None else env
        # Accept "MYAPP" or "MYAPP_"; the separator is added once.
        stem = prefix if prefix.endswith("_") else prefix + "_"
        collected: dict[str, Any] = {}
        for name, value in env.items():
            if name.startswith(stem):
                collected[name[len(stem) :].lower()] = _coerce_scalar(value)
        self._layers.append((f"env:{stem.strip('_')}", collected))
        return self

    def add_overrides(self, data: Mapping[str, Any], source: str = "argv") -> ConfigResolver:
        """Highest-priority layer: values parsed from the command line."""
        self._layers.append((source, data))
        return self

    # -- resolution ------------------------------------------------------

    def resolve(self) -> ResolvedConfig:
        """Collapse the layers, later layers winning.

        Returns a :class:`ResolvedConfig` mapping with a parallel ``sources``
        dict. Because this method *replaces* values rather than mutating them,
        there is exactly one place where precedence is decided -- which is the
        whole point.
        """
        winner: dict[str, Any] = {}
        origin: dict[str, str] = {}
        unused: list[str] = []

        for source, layer in self._layers:
            for key, value in layer.items():
                if self._known_keys is not None and key not in self._known_keys:
                    unused.append(f"{source}:{key}")
                    continue
                winner[key] = value
                origin[key] = source

        return ResolvedConfig(winner, origin, unused, self)

    def unknown_keys(self) -> set[str]:
        """Keys declared by any layer that no parser actually accepts."""
        if self._known_keys is None:
            return set()
        known: set[str] = set()
        for _, layer in self._layers:
            known.update(layer)
        return known - self._known_keys


class ResolvedConfig(dict):
    """Result of :meth:`ConfigResolver.resolve`: a dict plus provenance."""

    def __init__(
        self,
        values: Mapping[str, Any],
        sources: Mapping[str, str],
        unused: Sequence[str],
        resolver: ConfigResolver,
    ) -> None:
        super().__init__(values)
        self.sources = dict(sources)
        self.unused = list(unused)
        self._resolver = resolver

    def explain(self, *, redact: bool = True) -> str:
        """Render a human/``--show-config`` view with source attribution."""
        lines = ["effective configuration (lowest precedence first):", ""]
        for key in sorted(self):
            value = self[key]
            shown = "***" if (redact and _looks_secret(key)) else repr(value)
            lines.append(f"  {key} = {shown}")
            lines.append(f"      from: {self.sources.get(key, '?')}")
        if self.unused:
            lines += ["", "ignored keys (no matching CLI option):"]
            lines += [f"  {item}" for item in sorted(self.unused)]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wiring the layers into argparse
# ---------------------------------------------------------------------------


def typed_defaults(parser: argparse.ArgumentParser, values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and convert a non-argv layer through the parser's own ``type``.

    Fixes the trap documented in the module docstring. For each key present in
    ``values`` *and* declared on ``parser``:

    * if the action has a ``type``, run it -- on strings *and* on already-typed
      values, via a ``str()`` round-trip. argparse only converts a ``str`` default,
      so without the round-trip an int from TOML would skip the validator.
    * if the value fails ``choices``, raise immediately,
    * otherwise pass it through untouched.

    Returns the validated mapping, ready for ``parser.set_defaults(**result)``.

    The round-trip's limit, stated plainly: ``type=`` is contractually a *string*
    converter, so this is lossless for the types a config file holds -- int, float,
    bool, str. A type that deliberately parses non-strings would need its own
    handling here.

    ::

        parser = build_parser()
        file_values = load_config_file(path)
        parser.set_defaults(**typed_defaults(parser, file_values))
        args = parser.parse_args()   # argv still wins
    """
    validated: dict[str, Any] = {}
    actions = {a.dest: a for a in parser._actions if a.dest != argparse.SUPPRESS}

    for key, value in values.items():
        action = actions.get(key)
        if action is None:
            continue

        if action.type is not None:
            try:
                raw = value if isinstance(value, str) else str(value)
                value = action.type(raw)
            except (argparse.ArgumentTypeError, ValueError, TypeError) as exc:
                label = ", ".join(action.option_strings) or key
                raise ValueError(f"{label}: invalid value from config file: {exc}") from None

        if action.choices is not None and action.nargs != 0 and value not in action.choices:
            options = ", ".join(str(c) for c in action.choices)
            raise ValueError(f"{key}: {value!r} from config file is not one of: {options}")

        validated[key] = value

    return validated


def set_defaults_from_config(
    parser: argparse.ArgumentParser,
    values: Mapping[str, Any],
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Install ``values`` as parser defaults, returning what was applied.

    This is the whole of "Philosophy A" in a reusable, validated form. The
    return value is useful for diagnostics: ``applied`` names the keys that
    matched a real option, and anything else in ``values`` is a typo you can warn
    about.
    """
    applied = typed_defaults(parser, values) if validate else dict(values)
    parser.set_defaults(**applied)
    return applied


def parse_key_value_overrides(pairs: Iterable[str]) -> dict[str, str]:
    """Turn ``KEY=VALUE`` strings into a dict, used for ``--set`` style options.

    Accepts repeated flags because ``action='append'`` produced the list::

        parser.add_argument("--set", action="append", default=[])
        # --set retries=5 --set region=eu  ->  {"retries": "5", "region": "eu"}

    Raises :class:`ValueError` on a malformed pair, naming the offending token --
    an error that tells you *which* pair is wrong beats one that does not.
    """
    out: dict[str, str] = {}
    for token in pairs:
        key, sep, value = str(token).partition("=")
        if not sep or not key.strip():
            raise ValueError(f"{token!r} is not KEY=VALUE")
        out[key.strip()] = value
    return out


def coerce_like(sample: Any, raw: str) -> Any:
    """Parse ``raw`` into the same Python type as ``sample``.

    A pragmatic converter for config values whose type should follow whatever the
    default was::

        retries = coerce_like(3, "5")          # 5
        verbose = coerce_like(False, "yes")    # True
        name = coerce_like("web", "api")       # "api"

    Useful when a config layer can only supply strings (``.env``, ``.ini``) but
    the layer below it is typed. Document its limits: it handles bool/int/float/
    str and leaves everything else as ``str``.
    """
    if isinstance(sample, bool):
        return _coerce_scalar(raw)
    if isinstance(sample, int):
        return int(raw)
    if isinstance(sample, float):
        return float(raw)
    return raw


def install_env_backed_defaults(
    parser: argparse.ArgumentParser,
    *,
    prefix: str | None = None,
    env: Mapping[str, str] | None = None,
    coerce: Callable[[Any, str], Any] | None = None,
) -> list[str]:
    """Use environment variables as the source of each option's default.

    This is the simplest correct env integration, and it composes perfectly with
    argparse precedence: env becomes the *default*, so ``--flag`` on the command
    line overrides it with no extra code.

    The catch, and why this is better as a helper than as a habit: the resulting
    ``--help`` no longer shows the real default (argparse prints the value it was
    given), so mention the env var in the help string::

        parser.add_argument(
            "--token",
            default=os.environ.get("MYAPP_TOKEN"),
            help="API token (env: MYAPP_TOKEN)",
        )

    Or use :func:`~argparse_mastery.config.explain_env` to generate that help
    suffix automatically.

    Returns the list of options that were given an env-derived default, so you
    can log or print it under ``--show-config``.
    """
    env = os.environ if env is None else env
    prefix = prefix or f"{parser.prog.upper().replace('-', '_')}_"
    coerce = coerce or coerce_like
    applied: list[str] = []

    for action in parser._actions:
        if action.dest == argparse.SUPPRESS or action.option_strings is None:
            continue
        name = f"{prefix}{action.dest.upper()}"
        if name not in env:
            continue
        raw = env[name]
        value = coerce(action.default, raw) if action.type is None else action.type(raw)
        action.default = value
        applied.append(action.dest)

    return applied


def explain_env(parser: argparse.ArgumentParser, *, prefix: str | None = None) -> str:
    """Return an ``(env: VAR)`` suffix naming the env var for every option.

    Paste it into help strings so ``--help`` documents the environment layer::

        from argparse import _  # noqa: PLC2701 - or just inline the text
        help=f"log verbosity{explain_env(parser)}"

    In practice you will inline ``f"(env: {prefix}{dest.upper()})"`` per argument;
    this helper exists for generating whole-parser help text after the fact.
    """
    prefix = prefix or f"{parser.prog.upper().replace('-', '_')}_"
    rows = []
    for action in parser._actions:
        if action.help is argparse.SUPPRESS or not action.option_strings:
            continue
        rows.append(f"{action.dest}: ${prefix}{action.dest.upper()}")
    return "; ".join(rows)
