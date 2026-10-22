"""Command handlers and the dispatch table.

Every handler has the same signature::

    def cmd_x(args: argparse.Namespace) -> int

and returns an exit code. Handlers never build a parser and never call
``sys.exit`` -- which makes each one a plain function that a test can call with a
synthetic namespace.

Lazy resolution
---------------
``cli.COMMANDS`` stores ``(module_name, function_name)`` strings, and
:func:`dispatch` imports on demand. That keeps ``taskrunner list`` from importing
the modules ``taskrunner run`` needs, which matters more than it sounds once a
command starts pulling in a database driver or an HTTP client.

The registry is also the plugin seam: a plugin registers ``(module, function)``
and gets the same dispatch, the same help, and the same completion for free.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable

__all__ = ["HANDLERS", "dispatch", "resolve"]

#: ``module.function`` name -> the callable, populated on first use.
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {}


def resolve(module_name: str, function_name: str) -> Callable[[argparse.Namespace], int]:
    """Import ``module_name`` and return ``function_name`` from it, once.

    Cached in :data:`HANDLERS` keyed by the dotted path. The cache makes the second
    call free; the key includes the module so two commands in one module share it.
    """
    key = f"{module_name}.{function_name}"
    cached = HANDLERS.get(key)
    if cached is not None:
        return cached

    module = importlib.import_module(f".{module_name}", __package__)
    handler = getattr(module, function_name)
    if not callable(handler):  # pragma: no cover - guards a typo in the registry
        raise TypeError(f"{key} is not callable")
    HANDLERS[key] = handler
    return handler


def dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Run the selected command. Returns 2 when no command was chosen.

    Help goes to **stderr** here, not stdout: "no command" is a usage error, and
    the exit status says so. Printing to stdout would let a shell script mistake a
    failure for a successful ``--help``. The distinction is the same one argparse
    itself draws -- ``--help`` goes to stdout and exits 0, errors go to stderr and
    exit 2.

    One dispatch site, and it never falls through to an ``if/elif`` ladder. If the
    registry and the parser ever disagreed, you would get an ``AttributeError`` at
    startup rather than a silently unhandled command.
    """
    import sys

    spec = getattr(args, "_handler", None)
    if spec is None:
        parser.print_help(sys.stderr)
        return 2
    module_name, function_name = spec
    handler = resolve(module_name, function_name)
    result = handler(args)
    return 2 if result is None else int(result)
