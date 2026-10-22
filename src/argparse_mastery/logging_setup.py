"""Turning parsed flags into logging configuration.

Every serious CLI needs the same four global options, with a specific
interaction rule between them that is easy to get wrong:

* ``-v`` / ``--verbose`` -- more output (repeatable)
* ``-q`` / ``--quiet`` -- less output (repeatable)
* ``--log-format`` -- human or structured output
* ``--log-file`` -- where records go, in addition to stderr

The rule: **verbosity and quiet must not be two independent booleans.** If they
are, then ``-vvq`` and ``-q -v -v`` must be made equal by hand, and ``--quiet``
overriding ``--verbose`` needs a documented precedence. :class:`VerbosityAction`
in :mod:`argparse_mastery.actions` solves it by accumulating a single integer
level on *one* action, which is order-independent by construction.

This module covers the other half: a formatter that renders records for humans
and one that emits newline-delimited JSON for machines, and a ``setup_logging``
that honours ``--quiet`` in the strong sense -- suppressing *our* output on
stderr while leaving third-party library chatter alone.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, TextIO

__all__ = [
    "JsonFormatter",
    "add_logging_arguments",
    "level_for",
    "setup_logging",
]

LOG = logging.getLogger("argparse_mastery")


#: Verbosity ladder, index 0 = quietest. Both directions clamp into it, which is
#: what makes ``-vv -q`` and ``-q -vv`` agree by construction.
_LEVEL_LADDER: tuple[int, ...] = (
    logging.CRITICAL,
    logging.ERROR,
    logging.WARNING,
    logging.INFO,
    logging.DEBUG,
)


def level_for(verbose: int = 0, quiet: int = 0, *, base: int = logging.WARNING) -> int:
    """Compute a log level from counts, the way ``grep -v``/``grep -q`` compose.

    Both counters adjust an *index* into a fixed ladder, and the result is clamped
    at both ends. Doing it this way -- rather than arithmetic on the level numbers
    with independent clamps -- is what makes the result order-independent and makes
    both extremes behave:

    ================  ==========
    Flags             Level
    ================  ==========
    *(none)*          WARNING
    ``-v``            INFO
    ``-vv``           DEBUG
    ``-vvv``          DEBUG (clamped)
    ``-q``            ERROR
    ``-qq``           CRITICAL
    ``-qqq``          CRITICAL (clamped)
    ``-vv -q``        WARNING  (order-independent)
    ================  ==========

    Note that ``-vvv`` clamps at DEBUG rather than going lower. Going below DEBUG
    would mean ``NOTSET``, which enables *everything* including third-party
    libraries -- a debugging foot-gun rather than a feature.
    """
    if base not in _LEVEL_LADDER:
        raise ValueError(f"base level {base!r} is not on the verbosity ladder")
    index = _LEVEL_LADDER.index(base) + int(verbose) - int(quiet)
    return _LEVEL_LADDER[max(0, min(index, len(_LEVEL_LADDER) - 1))]


class JsonFormatter(logging.Formatter):
    """Newline-delimited JSON log records for log shippers and CI.

    One JSON object per line (NDJSON) is the format ``jq``, Vector, Fluent Bit
    and CloudWatch all read natively. Beyond ``json.dumps`` of the standard
    record attributes, two details matter:

    * **Extras are promoted to top-level keys.** The standard way to attach
      context is ``logger.info("moved", extra={"path": str(p)})``, and a
      formatter that ignored ``extra`` would silently lose it.
    * **A stable ``exception`` field.** ``exc_info`` becomes a string, so a
      failing run in CI shows a usable traceback without a multi-line string
      breaking the one-record-per-line contract.
    """

    #: Attributes present on every LogRecord; anything else came from ``extra``.
    _standard = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def __init__(self, *, program: str = "app", include: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.program = program
        self.include = include

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "program": self.program,
        }
        if self.include:
            payload.update({k: getattr(record, k, None) for k in self.include})
        for key, value in record.__dict__.items():
            if key not in self._standard and not key.startswith("_"):
                payload[key] = _jsonable(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, sort_keys=True)


def _jsonable(value: Any) -> Any:
    """Best-effort conversion so ``extra=`` never breaks a log line."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def add_logging_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the standard verbosity/logging options to ``parser``.

    Idempotent-safe to call on a shared "common arguments" parent parser (see
    :mod:`examples.18_parser_parents`). Registers :class:`VerbosityAction` under
    the name ``verbosity`` via ``parser.register`` so the string form works.
    """
    from .actions import VerbosityAction

    parser.register("action", "verbosity", VerbosityAction)

    group = parser.add_argument_group("logging")
    group.add_argument(
        "-v",
        "--verbose",
        action="verbosity",
        dest="log_level",
        nargs=0,
        # The ladder stores level *names*, so the default must be a name too --
        # a bare `logging.WARNING` (an int) would not be recognised by the action
        # and every `-q` would be a no-op.
        default="WARNING",
        metavar="",
        help="increase verbosity; repeatable (-v INFO, -vv DEBUG)",
    )
    group.add_argument(
        "-q",
        "--quiet",
        action="verbosity",
        dest="log_level",
        nargs=0,
        metavar="",
        help="decrease verbosity; repeatable (-q ERROR, -qq CRITICAL)",
    )
    group.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="record format (default: %(default)s)",
    )
    group.add_argument(
        "--log-file",
        metavar="PATH",
        default=None,
        help="also append records to PATH",
    )
    return parser


def setup_logging(
    args: argparse.Namespace,
    *,
    program: str = "app",
    stream: TextIO | None = None,
    default_level: int = logging.WARNING,
) -> int:
    """Configure logging from parsed args and return the effective level.

    Idempotent: calling twice does not double-handlers. This matters because
    ``setup_logging`` runs once in ``main`` and again (with a forced level) when
    a ``--trace`` subcommand turns on :mod:`tracebacks` debugging.

    Handler policy:

    * one :class:`~logging.StreamHandler` on stderr, replaced on each call
    * an optional file handler with ``delay=True`` so ``--log-file`` never
      creates a file unless a record is actually emitted -- no empty files on a
      successful no-op run
    * ``root`` is configured, not just ``__name__``, so third-party libraries
      stop emitting their own INFO chatter
    """
    level = getattr(args, "log_level", default_level)
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
    # Accept both names and numbers, so a caller can pass either.
    if not isinstance(level, int):
        level = int(default_level)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_argparse_mastery", False):
            root.removeHandler(handler)
            handler.close()

    fmt = "%(message)s" if getattr(args, "log_format", "text") == "text" else None

    stream_handler: logging.Handler
    if fmt is None:
        stream_handler = logging.StreamHandler(stream or sys.stderr)
        stream_handler.setFormatter(JsonFormatter(program=program))
    else:
        stream_handler = logging.StreamHandler(stream or sys.stderr)
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
            if level <= logging.INFO
            else logging.Formatter("%(message)s")
        )

    stream_handler._argparse_mastery = True  # type: ignore[attr-defined]
    root.addHandler(stream_handler)
    root.setLevel(level)

    log_file = getattr(args, "log_file", None)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8", delay=True)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        file_handler._argparse_mastery = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    return level


def is_tty(stream: TextIO | None = None) -> bool:
    """Whether ``stream`` is an interactive terminal.

    Use this to switch between human output (boxes, colours, alignment) and
    machine output (JSON lines). argparse cannot do this for you, but argparse
    *can* default it::

        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="json" if not sys.stdout.isatty() else "text",
        )
    """
    target = stream or sys.stdout
    try:
        return bool(target.isatty())
    except Exception:  # pragma: no cover - exotic stream objects
        return False


def default_output_format(*, text: str = "text", machine: str = "json") -> str:
    """Pick an output format based on whether stdout is a terminal.

    The single most user-friendly default a CLI can have: humans get tables,
    pipes and CI get parseable records. See :func:`is_tty`.
    """
    return text if is_tty() else machine


def env_flag(name: str, *, default: bool = False) -> bool:
    """Read a boolean flag from the environment.

    ``1/true/yes/on`` and ``0/false/no/off`` are accepted case-insensitively.
    Anything else raises :class:`ValueError` rather than silently becoming
    ``False`` -- a typo'd env var must not look like a deliberate opt-out.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean; use 1/0, true/false, yes/no, on/off")
