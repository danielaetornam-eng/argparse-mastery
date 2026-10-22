"""``python -m taskrunner``.

Three lines, and the layout matters:

* :func:`main` is imported, not defined here -- there is exactly one ``main``.
* The ``if __name__ == "__main__"`` guard means importing this module has no
  effect.
* ``raise SystemExit(main())`` is the *only* place the process exits.

Everything else lives in ``app.py`` and is importable and testable.
"""

from __future__ import annotations

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
