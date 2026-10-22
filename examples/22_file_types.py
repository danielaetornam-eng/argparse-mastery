"""Lesson 22 - ``argparse.FileType``, and the file-handle leak.

Run me
------
    python examples/22_file_types.py README.md
    python examples/22_file_types.py --demo

``FileType`` in one paragraph
-----------------------------
``type=argparse.FileType('r')`` opens the file *during parsing* and hands you a
live file object. On failure it raises ``ArgumentTypeError`` with a good message
(``argument infile: can't open 'missing.txt': [Errno 2] ...``). It is convenient
and it is the cause of the most common argparse resource bug.

**3.14 deprecation.** ``argparse.FileType`` now emits
``PendingDeprecationWarning: FileType is deprecated. Simply open files after
parsing arguments.`` on instantiation. The stdlib is telling you to move to fix
1 -- validate a ``Path`` and open it yourself -- and this lesson is that migration.

The leak
--------
argparse never closes what ``FileType`` opened. It cannot: it does not know when
you are done, and the same handle might be reused. So::

    for _ in range(2000):
        parser.parse_args(["in.txt"])

leaks 2000 file descriptors, and on Windows that hits the process limit long
before 2000. In a short-lived CLI nobody notices. In a test suite you get
``ResourceWarning: unclosed file``; in a long-running service you get
``OSError: [Errno 24] Too many open files``.

The leak is not really the point any more
------------------------------------------
``FileType`` is deprecated as of 3.14, so the migration below is no longer a
judgement call: it is what the stdlib asks for. The leak is the historical
justification for it, and still worth knowing because plenty of installed code
predates the deprecation.

Three fixes
-----------
1. **Use a ``type=`` that returns a ``Path``.** Validate, do not open::

       def existing_file(raw):
           p = Path(raw).expanduser()
           if not p.is_file():
               raise argparse.ArgumentTypeError(f"{raw}: no such file")
           return p

       parser.add_argument("infile", type=existing_file)

   Open it where you use it, in a ``with`` block. This is the modern default and
   it is what ``argparse_mastery.types`` provides.

2. **Use ``FileType('w')`` for outputs.** There is no alternative to opening it,
   and the leak matters far less for one output handle. Close it yourself in a
   ``finally``.

3. **Use ``FileType('r')`` only for short-lived scripts**, and only with a single
   file. If you accept many files, use ``nargs='+'`` with ``type=existing_file``
   and open them in a loop.

``FileType`` modes
------------------
=====================  =============================================
``argparse.FileType('r')``  text read (universal newlines)
``argparse.FileType('rb')`` binary read
``argparse.FileType('w')`` text write (truncates)
``argparse.FileType('a')`` text append
=====================  =============================================

``'b'`` is honoured, not ignored: ``FileType('rb')`` gives you bytes. The others
are documented as accepted and ignored, so ``FileType('rt')`` behaves as ``'r'``.

`-`` means stdin/stdout
------------------------
``FileType('r')`` maps the literal ``-`` to ``sys.stdin``, and ``FileType('w')``
maps it to ``sys.stdout``. This is a genuinely valuable behaviour and the reason
many tools document ``-`` as "stdin". Note that the *closed handle* is
``sys.stdout`` itself, so "closing" it is a no-op for ``-`` -- another reason
``FileType`` is awkward to reason about.

Encoding and newline
--------------------
``FileType`` opens with the platform default encoding. If your file is UTF-8 and
the user is on Windows with a legacy code page, you get
``UnicodeDecodeError`` from deep inside a read loop. A ``type=`` returning a
``Path`` lets you specify ``encoding="utf-8"`` explicitly. This is the single most
practical reason to stop using ``FileType``.

A good hybrid
-------------
Validate with ``type=``, then open explicitly, and let a decode error surface as
a clean message::

    try:
        with path.open(encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        parser.error(f"{path}: {exc.strerror}")
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def existing_file(raw: str) -> Path:
    """Validate a path, return it, and open nothing.

    This is the recommended replacement for ``argparse.FileType``. Validation
    still happens at parse time with a good message; the handle is opened by the
    code that needs it, in a ``with`` block, with an explicit encoding.
    """
    path = Path(raw).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"{raw}: no such file")
    if path.is_dir():
        raise argparse.ArgumentTypeError(f"{raw}: is a directory, expected a file")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="count",
        description="Lesson 22: FileType and the handle leak.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # argparse.FileType: opens during parsing, never closes. Convenient, leaky.
    parser.add_argument(
        "legacy_input",
        nargs="?",
        type=argparse.FileType("r", encoding="utf-8"),
        default=None,
        metavar="FILE",
        help="FileType('r'): opened for you, never closed",
    )

    # The recommended shape: validate now, open later.
    parser.add_argument(
        "input",
        nargs="?",
        type=existing_file,
        default=None,
        metavar="PATH",
        help="a Path, opened by us under `with` (recommended)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="where to write (opened in write_text)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.legacy_input is not None:
        # FileType gave us an open handle. We own closing it.
        with args.legacy_input:
            first = args.legacy_input.readline()
        print(f"legacy_input : {args.legacy_input.name!r} first line: {first.strip()!r}")

    if args.input is not None:
        try:
            text = args.input.read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"{args.input}: {exc.strerror}")
        except UnicodeDecodeError as exc:
            parser.error(f"{args.input}: not valid UTF-8 ({exc.reason})")
        print(f"input        : {args.input.name!r} ({len(text)} chars)")

    if args.output is not None:
        args.output.write_text("hello\n", encoding="utf-8")
        print(f"output       : wrote {args.output}")

    return 0


def demo() -> int:
    print("=" * 78)
    print("1. FileType validates and opens, all during parsing")
    print("=" * 78)
    ft = argparse.ArgumentParser(prog="count", add_help=False)
    ft.add_argument("f", type=argparse.FileType("r", encoding="utf-8"))
    print(f"  missing.txt  -> {_msg(ft, ['missing.txt'])}")
    print(f"  a directory  -> {_msg(ft, ['.'])}   (errno text is platform-specific)")
    good = _try(ft, ["README.md"]) if Path("README.md").exists() else None
    if good is None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
            tmp.write("line one\nline two\n")
            temp_name = tmp.name
        print(f"  a real file  -> accepted, handle={_try(ft, [temp_name])!r}")
        Path(temp_name).unlink(missing_ok=True)

    print()
    print("=" * 78)
    print("2. '-' means stdin/stdout")
    print("=" * 78)
    for mode, label in (("r", "read"), ("w", "write")):
        p = argparse.ArgumentParser(prog="count", add_help=False)
        p.add_argument("f", type=argparse.FileType(mode))
        handle = _try(p, ["-"])
        print(f"  FileType({mode!r}) with '-' -> {handle}")
    print()
    print("  'closing' stdin/stdout is a no-op, which is another reason FileType")
    print("  is hard to reason about: you cannot tell from the handle whether you")
    print("  own it.")

    print()
    print("=" * 78)
    print("3. The leak: argparse never closes what FileType opened")
    print("=" * 78)
    leak_parser = argparse.ArgumentParser(prog="count", add_help=False)
    leak_parser.add_argument("f", type=argparse.FileType("r", encoding="utf-8"))
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("x\n")
        name = tmp.name

    # Parse repeatedly and count live handles still bound to the file.
    held = []
    for _ in range(50):
        ns = leak_parser.parse_args([name])
        held.append(ns.f)
    still_open = sum(1 for h in held if not h.closed)
    print(f"  after 50 parses: {still_open} of 50 handles are still open")
    for handle in held:
        handle.close()
    print("  (closed by hand afterwards)")
    Path(name).unlink(missing_ok=True)
    print()
    print("  On Linux the per-process fd limit hides this. On Windows the CRT")
    print("  handle limit is hit much sooner, and pytest surfaces it as")
    print("  ResourceWarning: unclosed file.")

    print()
    print("=" * 78)
    print("4. The recommended replacement: type= returning a Path")
    print("=" * 78)
    target = __file__ if Path(__file__).exists() else "."
    print(f"  existing_file({Path(target).name!r}) -> {existing_file(target)!r}")
    print(f"  existing_file('missing')   -> {_call_error(existing_file, 'missing')}")
    print(f"  existing_file('.')         -> {_call_error(existing_file, '.')}")
    print()
    print("  Same early validation, same good message, no handle, and you choose")
    print("  the encoding when you open it.")

    print()
    print("=" * 78)
    print("5. FileType('rb') really does give bytes")
    print("=" * 78)
    for mode in ("r", "rb"):
        p = argparse.ArgumentParser(prog="count", add_help=False)
        p.add_argument("f", type=argparse.FileType(mode))
        handle = _try(p, [name if Path(name).exists() else __file__])
        if hasattr(handle, "mode"):
            print(f"  FileType({mode!r}) -> mode={handle.mode!r} encoding={getattr(handle, 'encoding', None)!r}")
            handle.close()
    print()
    print("  Only 'b' is honoured. 'rt', 'rU' and friends are accepted and ignored,")
    print("  which argparse's own docstring warns about.")

    return 0


def _msg(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
        return "(accepted)"
    except SystemExit:
        pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return line.split(": error:", 1)[1].strip()
    return "(no error)"


def _try(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    with contextlib.redirect_stderr(io.StringIO()):
        try:
            return parser.parse_args(argv).f
        except SystemExit:
            return "(error)"


def _call_error(func, value: str) -> str:
    try:
        func(value)
    except argparse.ArgumentTypeError as exc:
        return f"ArgumentTypeError: {exc}"
    return "(accepted)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
