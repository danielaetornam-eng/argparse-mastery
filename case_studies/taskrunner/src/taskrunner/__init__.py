"""``taskrunner`` -- the case study: a production-shaped argparse CLI.

This is lesson 37 split into the module layout you would actually ship:

==========================  ====================================================
Module                      Responsibility
==========================  ====================================================
``cli.py``                  ``build_parser()`` -- the whole parser tree
``commands/``               one module per subcommand, one function per handler
``config.py``               layered configuration with provenance
``exit_codes.py``           the public exit-code enum
``errors.py``               user-facing error type
``app.py``                  ``main()``: the entry point, owns nothing but wiring
==========================  ====================================================

The division that matters
-------------------------
* **The parser module never imports command modules.** It attaches functions by
  name via a small registry, so adding a command is one entry plus one file, and
  the parser stays importable without pulling in command dependencies.
* **Command modules never build a parser.** They receive a namespace and return an
  exit code. That makes every handler a plain unit-testable function.
* **Only ``app.main`` decides how to exit.** Everything else returns.

Read the modules in that order.
"""

from __future__ import annotations

from .cli import build_parser
from .config import ConfigLayer, load_layered_config
from .errors import TaskRunnerError
from .exit_codes import ExitCode

__version__ = "1.0.0"

__all__ = [
    "ConfigLayer",
    "ExitCode",
    "TaskRunnerError",
    "__version__",
    "build_parser",
    "load_layered_config",
]
