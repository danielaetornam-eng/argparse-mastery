"""Root conftest.

Its only job is to make ``src/`` importable so the test suite (and the example
smoke tests) run straight from a clone with no ``pip install`` step.

This is a *teaching* repository, so we deliberately avoid requiring an install
before anything works. The ``pyproject.toml`` still declares a proper
``console_scripts`` entry point so you can also ``pip install -e .`` and get the
``argparse-mastery`` command.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
_CASE_STUDIES = _ROOT / "case_studies" / "taskrunner" / "src"

for _path in (_SRC, _CASE_STUDIES):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
