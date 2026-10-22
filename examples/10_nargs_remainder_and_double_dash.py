"""Lesson 10 - ``REMAINDER``, ``parse_known_args``, and the ``--`` separator.

Run me
------
    python examples/10_nargs_remainder_and_double_dash.py run -- --verbose -x
    python examples/10_nargs_remainder_and_double_dash.py --demo

Three mechanisms for "I do not know what comes next"

1. ``nargs=REMAINDER`` -- take everything, including things that look like options
2. ``parse_known_args`` -- return the leftovers instead of failing
3. ``--`` -- the conventional end-of-options marker

``REMAINDER``
-------------
Declared on a **positional** only. It consumes every remaining token verbatim::

    parser.add_argument("cmd")
    parser.add_argument("args", nargs=argparse.REMAINDER)

    prog run -v --x          -> cmd='run', args=['-v', '--x']
    prog run a b c           -> cmd='run', args=['a', 'b', 'c']

This is the *only* way to forward arguments to another program from argparse::

    subprocess.run([shutil.which("ssh"), *args.ssh_args])

The alternative, and its trap::

    nargs=argparse.REMAINDER    # captures flags AND everything
    nargs='+'                  # captures only non-flag-looking tokens

If you use ``'+'`` and pass ``prog run --verbose``, argparse will try to parse
``--verbose`` as your own flag and either consume it or reject it. With
``REMAINDER`` you get exactly what the user typed.

Caveat: ``REMAINDER`` is greedy and slightly unintuitive about the *first* token.
With ``prog --verbose run a b`` where ``--verbose`` is one of your own options,
it is consumed before ``run`` is seen. That is usually what you want.

``PARSER`` -- the confusing sibling
-----------------------------------
``nargs=argparse.PARSER`` also consumes everything, but *records* which subparser
claimed each token. For a single-level parser you almost always want
``REMAINDER``. ``PARSER`` matters for tools that chain into another argparse
parser::

    inner = argparse.ArgumentParser()
    inner.add_argument("--x", type=int)
    # outer sees nargs=PARSER, calls inner.parse_known_args on the remainder

In practice, ``subprocess`` + explicit re-parsing is clearer. Know it exists.

``parse_known_args``
--------------------
Returns ``(namespace, extras)`` instead of failing on tokens it does not
recognise::

    args, extras = parser.parse_known_args()

This is the foundation of the two-pass migration described in lesson 35, and the
right tool for "my CLI has some flags of its own and passes the rest to a
subprocess". The bad version of this idea is

    try:
        args = parser.parse_args()
    except SystemExit:
        pass          # now you have no args at all, and --help exits 0 silently

which turns a typo into a silent no-op.

The danger of ``parse_known_args``: a typo in *your* flag is indistinguishable
from an intended pass-through. ``prog --verboze`` succeeds and silently ignores
the flag. Mitigate by validating ``extras`` yourself when you know they must be
empty, or by only using pass-through in a leaf command.

The ``--`` separator
--------------------
``--`` means "everything after this is positional, even if it starts with a
dash". This is a POSIX convention every Unix user knows::

    prog -- -weird-filename
    prog grep -rn -- --include='*.py' .

**It only works for positionals.** Verified behaviour in 3.14:

==========================================  ==============================
Declaration and command line                Result
==========================================  ==============================
``items`` positional ``nargs='*'``: ``-- -x``    ``['-x']``
``items`` positional ``nargs='*'``: ``a -x``     error (unrecognized ``-x``)
``--items`` option ``nargs='*'``: ``-- -x``      error (``--`` itself is left over)
``items`` ``nargs=REMAINDER``: ``-x``           ``['-x']``, no marker needed
``items`` ``nargs=REMAINDER``: ``-- -x``        ``['--', '-x']`` -- keeps ``--``
==========================================  ==============================

The reason is the patterns from lesson 09: a positional's ``'*'`` pattern is
``(-*[A-]*)``, which has a slot for the ``-`` that ``--`` becomes, while an
option's is ``(A*)``, which does not.

``REMAINDER`` is therefore strictly better than ``'--'`` whenever you can use it:
it needs no marker *and* it keeps the literal ``--`` so the child program sees
exactly what was typed.

``parse_known_args`` + ``REMAINDER`` is the classic "wrapper" recipe
--------------------------------------------------------------------
::

    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("cmd", nargs="?")
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    argv = ([args.cmd] if args.cmd else []) + args.passthrough

    if argv and argv[0] == "--":
        argv = argv[1:]

This is how ``ssh``, ``docker run``, ``kubectl exec`` and every "wrapper tool"
CLI is built. Note the explicit ``--`` strip: argparse keeps it in
``REMAINDER``.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wrapper",
        description="Lesson 10: passing arguments through to another program.",
        epilog=(
            "  wrapper run -v --x      args are captured verbatim by REMAINDER\n"
            "  wrapper -- run -v       the leading '--' is also captured; strip it\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Our own options live on the top-level parser, so they must come BEFORE the
    # subcommand token. This ordering constraint is the price of REMAINDER.
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="our own config file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="our own verbosity flag",
    )

    # The command to run.
    cmd = parser.add_argument(
        "cmd",
        nargs="?",
        default=None,
        metavar="COMMAND",
        help="command to run; everything after it is passed through",
    )

    # Everything else, verbatim. REMAINDER is the only positional nargs that
    # accepts tokens beginning with '-'.
    passthrough = parser.add_argument(
        "passthrough",
        nargs=argparse.REMAINDER,
        metavar="...",
        help="arguments forwarded to COMMAND, unmodified",
    )

    return parser


def forwarded_argv(args: argparse.Namespace) -> list[str]:
    """Assemble the argv to forward, dropping argparse's ``--`` marker.

    ``nargs=REMAINDER`` keeps a leading ``--`` in the list. Subprocess would
    pass it on, and the child program would treat it as its own end-of-options
    marker -- which is usually harmless but is never what the user meant to
    forward. Strip it explicitly.
    """
    argv: list[str] = []
    if args.cmd:
        argv.append(args.cmd)
    argv.extend(args.passthrough)
    if argv and argv[0] == "--":
        argv = argv[1:]
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"our config : {args.config!r}")
    print(f"our verbose: {args.verbose}")
    print(f"passthrough: {args.passthrough!r}")
    print(f"to forward : {forwarded_argv(args)!r}")
    if not args.cmd:
        print("no command given; would show help", file=sys.stderr)
        return 2
    return 0


def demo() -> int:
    print("=" * 76)
    print("1. REMAINDER captures options; '+' does not")
    print("=" * 76)

    rem = argparse.ArgumentParser(prog="wrapper", add_help=False)
    rem.add_argument("cmd", nargs="?")
    rem.add_argument("rest", nargs=argparse.REMAINDER)

    plus = argparse.ArgumentParser(prog="wrapper", add_help=False)
    plus.add_argument("cmd", nargs="?")
    plus.add_argument("rest", nargs="+")

    for argv in (["run", "-v", "--x", "a"], ["run", "a", "b"]):
        print(f"  argv={argv!s:<28}")
        print(f"      REMAINDER -> {vars(rem.parse_args(argv))['rest']!r}")
        print(f"      '+'       -> {_rest_of(plus, argv)!r}")

    print()
    print("  REMAINDER is the only positional nargs that accepts '-flag' tokens.")
    print("  It is declared on positionals only; add_argument('--x', nargs=REMAINDER)")
    print("  is a TypeError.")

    print()
    print("=" * 76)
    print("2. Our own options are consumed BEFORE REMAINDER starts")
    print("=" * 76)
    parser = build_parser()
    for argv in (
        ["--verbose", "run", "-v"],
        ["run", "--verbose"],
        ["--config", "a.toml", "run", "-q"],
    ):
        ns = parser.parse_args(argv)
        print(f"  {argv!s:<34} verbose={ns.verbose!s:<6} "
              f"config={ns.config!r:<10} forward={forwarded_argv(ns)!r}")

    print()
    print("  Row 2 is the surprise: '--verbose' after the command is NOT ours.")
    print("  It goes into passthrough. This ordering constraint is inherent to")
    print("  REMAINDER and cannot be configured away.")

    print()
    print("=" * 76)
    print("3. The '--' marker survives REMAINDER, so strip it yourself")
    print("=" * 76)
    for argv in (["--", "run", "-v"], ["run", "-v"]):
        ns = parser.parse_args(argv)
        print(f"  {argv!s:<24} passthrough={ns.passthrough!r:<22} "
              f"forward={forwarded_argv(ns)!r}")

    print()
    print("=" * 76)
    print("4. parse_known_args returns leftovers instead of failing")
    print("=" * 76)
    known = argparse.ArgumentParser(prog="wrapper", add_help=False)
    known.add_argument("--config", metavar="PATH")
    known.add_argument("files", nargs="*")

    for argv in (["a.txt", "b.txt"], ["a.txt", "--unknown", "b.txt"], ["--verboze", "a.txt"]):
        ns, extras = known.parse_known_args(argv)
        print(f"  {argv!s:<34} files={ns.files!r:<18} extras={extras!r}")
    print()
    print("  Row 3 is the danger: '--verboze' is a typo, but parse_known_args")
    print("  cannot tell it from an intended pass-through. If you know extras must")
    print("  be empty, check them:  if extras: parser.error(...)")

    print()
    print("=" * 76)
    print("5. Why not try/except around parse_args?")
    print("=" * 76)
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            parser.parse_args(["--verboze", "run"])
        except SystemExit as exc:
            code = exc.code
    print(f"  parse_args(['--verboze','run']) -> SystemExit({code})")
    print(f"  stderr: {buffer.getvalue().splitlines()[-1]}")
    print("  You now have NO namespace at all. --help also exits 0, so a")
    print("  bare `except SystemExit: pass` silently swallows --help too.")
    print()
    print("  Use parse_known_args, or parse_or_raise (argparse_mastery.errors).")

    print()
    print("=" * 76)
    print("6. '--' only helps POSITIONALS; REMAINDER needs no marker")
    print("=" * 76)
    positional = argparse.ArgumentParser(prog="wrapper", add_help=False)
    positional.add_argument("items", nargs="*")
    for argv in (["a", "-x"], ["--", "-x"]):
        print(f"  positional nargs='*'  {argv!s:<20} -> {_positional(positional, argv)!r}")

    option = argparse.ArgumentParser(prog="wrapper", add_help=False)
    option.add_argument("--items", nargs="*")
    print(f"  option nargs='*'       {'--items -- -x':<20} -> {_positional(option, ['--items', '--', '-x'])!r}")

    rem2 = argparse.ArgumentParser(prog="wrapper", add_help=False)
    rem2.add_argument("items", nargs=argparse.REMAINDER)
    for argv in (["-x"], ["echo", "-x"], ["echo", "--", "-x"]):
        print(f"  positional REMAINDER  {argv!s:<20} -> {_positional(rem2, argv)!r}")
    print()
    print("  REMAINDER captures flag-shaped tokens verbatim -- but only *after* the")
    print("  first one. A leading '-x' is still classified as an option, because the")
    print("  pattern is '(-*[A-]*)': the '-' slot absorbs '--' but a bare '-x' is 'O'.")
    print("  Once 'echo' has been consumed as an 'A', the pattern is '.*' and")
    print("  everything after it is taken verbatim.")
    print()
    print("  Which is exactly why wrapper tools declare `cmd` first and REMAINDER")
    print("  second -- the pattern above is `(-*[A-]*)` for cmd then `(.*)` for rest.")


def _positional(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).items
    except SystemExit:
        return _error_from(buffer.getvalue())


def _rest_of(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return vars(parser.parse_args(argv)).get("rest")
    except SystemExit:
        return _error_from(buffer.getvalue())


def _rest(parser: argparse.ArgumentParser, argv: list[str]) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv).items
    except SystemExit:
        return _error_from(buffer.getvalue())


def _error_from(text: str) -> str:
    for line in text.splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
