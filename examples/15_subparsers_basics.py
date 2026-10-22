"""Lesson 15 - Subparsers, and the ``set_defaults(func=...)`` dispatch contract.

Run me
------
    python examples/15_subparsers_basics.py status --watch
    python examples/15_subparsers_basics.py --demo

The anatomy
-----------
::

    sub = parser.add_subparsers(dest="command")          # 1. create the group
    p = sub.add_parser("status", help="show status")     # 2. one parser per command
    p.add_argument("--watch", action="store_true")
    p.set_defaults(func=cmd_status)                      # 3. attach the handler
    args = parser.parse_args()                           # 4. parse
    args.func(args)                                      # 5. dispatch

Step 3 is the part that is not obvious. ``add_subparsers`` returns an *action*
(``_SubParsersAction``), and the parser it creates for each command is an
ordinary ``ArgumentParser``. Nothing connects a command name to a function
unless you do it. ``set_defaults(func=...)`` is the connector, and it is why
``main`` can have exactly one dispatch site::

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)

Compare with the alternative, the ``if/elif`` ladder::

    if args.command == "status":  return cmd_status(args)
    elif args.command == "start":  return cmd_start(args)

Both work. The ladder puts every command name in two places (the subparser
declaration and the branch), so adding a command means finding both, and
forgetting the branch produces a silent ``None`` return. ``set_defaults`` has
one place. With enough commands the difference is not a matter of taste.

``dest`` is how argparse records the choice
------------------------------------------
``dest="command"`` puts the chosen name in the namespace. With no ``dest``,
there is no record of which command ran -- which defeats the point.

``parser_class``
----------------
Subparsers are created with ``type(self)`` by default, so a subclass of
``ArgumentParser`` propagates automatically::

    parser.add_subparsers(parser_class=argparse.ArgumentParser)

is only needed to *disable* that, or to pin an exact class. Verified on 3.14:

* ``prog status --help`` **works out of the box**. There is a widely repeated
  claim that ``add_help`` defaults to ``False`` on subparsers; it does not.
  ``_SubParsersAction.add_parser`` passes ``**kwargs`` straight to the parser
  class, and ``ArgumentParser``'s own default is ``add_help=True``.
* ``add_help=False`` on the *parent* does not disable it on subparsers -- each
  subparser is its own ``ArgumentParser`` with its own defaults.
* You *can* remove it per subparser with ``add_parser("status", add_help=False)``,
  which is occasionally useful for a command whose flags are self-evident.

Python 3.14 addition
---------------------
``add_parser`` accepts ``deprecated=True``, which makes the command print
``prog: warning: command 'old' is deprecated`` to stderr and still run it. See
lesson 34 for the full deprecation workflow.

The option-ordering constraint
------------------------------
Options on the **parent** parser must appear **before** the command::

    prog --verbose status        works
    prog status --verbose        --verbose is the subcommand's, not ours

If you need the parent's options to work after the command too, either add them
to each subparser (a parent parser -- lesson 18) or use
``parse_intermixed_args`` (which does not support subparsers). This is not a
limitation you can configure away; it follows from the subcommand token being a
positional.

Subcommand names are matched exactly
------------------------------------
A subcommand is a ``choices`` entry, and ``choices`` uses ``in`` -- an exact
comparison. ``allow_abbrev`` applies to long *options*, not to subcommand names::

    prog rem      -> error: invalid choice: 'rem' (choose from 'remove', 'list')

If you want short spellings, declare them with ``aliases=`` (lesson 16). That is
the supported mechanism, and it shows up in ``--help``.
"""

from __future__ import annotations

import argparse
import sys


def cmd_status(args: argparse.Namespace) -> int:
    """Handler for ``status``. Same signature for every command."""
    print(f"status: healthy={args.healthy} retries={args.retries}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    print(f"start: service={args.service!r} daemon={args.daemon}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    print("stop: no arguments")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet",
        description="Lesson 15: subparsers and the func dispatch contract.",
        epilog=(
            "Parent options must precede the command:\n"
            "  fleet --json status      works\n"
            "  fleet status --json      --json is status's option, not fleet's"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # A global option. Note the ordering constraint documented above.
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output (global; before the command)",
    )

    # add_subparsers returns an action, not a parser. `dest` records the choice.
    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        # parser_class=argparse.ArgumentParser  -> gives every subparser add_help=True
        help="available commands",
    )

    status = sub.add_parser(
        "status",
        help="show service status",
        description="Report health for one or all services.",
        add_help=True,
    )
    status.add_argument(
        "--watch",
        action="store_true",
        help="poll instead of exiting",
    )
    status.add_argument(
        "--retries",
        type=int,
        default=3,
        help="how many times to retry a probe",
    )
    # Every attribute a handler reads must be declared here. `args.healthy`
    # without a matching add_argument (or set_defaults) is an AttributeError at
    # dispatch time -- and only for the code path that reads it, which is exactly
    # the kind of bug tests miss when they only check parsing.
    status.add_argument(
        "--healthy",
        action="store_true",
        help="report the service as healthy",
    )
    status.set_defaults(func=cmd_status)

    start = sub.add_parser("start", help="start a service", add_help=True)
    start.add_argument("service", help="service name")
    start.add_argument(
        "--daemon",
        action="store_true",
        help="detach from the terminal",
    )
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="stop a service", add_help=True)
    stop.set_defaults(func=cmd_stop)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # The one-line dispatch. `hasattr(args, "func")` is the test for "a command
    # was chosen", because set_defaults(func=...) is what adds the attribute.
    if not hasattr(args, "func"):
        parser.print_help()
        return 2

    return args.func(args)


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. The dispatch: one call site, no if/elif ladder")
    print("=" * 78)
    for argv in (
        ["status"],
        ["status", "--watch", "--retries", "5"],
        ["start", "api", "--daemon"],
        ["stop"],
        ["--json", "status"],
    ):
        args = parser.parse_args(argv)
        handler = getattr(args, "func", None)
        print(f"  {argv!s:<40} command={args.command!r:<10} "
              f"func={getattr(handler, '__name__', None)}")
    print()
    print("  'command' comes from dest=; 'func' comes from set_defaults.")

    print()
    print("=" * 78)
    print("2. Subparsers get their own -h by default (a common myth)")
    print("=" * 78)
    for argv in (["status", "--help"], ["start", "--help"]):
        out = _capture(parser, argv)
        first = next((ln for ln in out.splitlines() if ln.startswith("usage:")), "")
        print(f"    {argv!s:<24} -> {first}")
    print()
    print("  Even with add_help=False on the PARENT:")
    bare = argparse.ArgumentParser(prog="fleet", add_help=False)
    bare_sub = bare.add_subparsers(dest="command")
    bare_sub.add_parser("status")
    out = _capture(bare, ["status", "--help"])
    first = next((ln for ln in out.splitlines() if ln.startswith("usage:")), "")
    print(f"    {'status --help':<24} -> {first}")
    print()
    print("  _SubParsersAction.add_parser passes kwargs straight to the parser class,")
    print("  and ArgumentParser's own default is add_help=True. You can still remove it")
    print("  deliberately:  sub.add_parser('status', add_help=False)")

    print()
    print("=" * 78)
    print("3. Parent options must precede the command")
    print("=" * 78)
    for argv in (["--json", "status"], ["status", "--json"]):
        ns = _try(parser, argv)
        if isinstance(ns, argparse.Namespace):
            print(f"  {argv!s:<26} -> json={ns.json!r}")
        else:
            print(f"  {argv!s:<26} -> {ns}")
    print()
    print("  Row 2 fails: --json belongs to the parent, and the parent's options are")
    print("  consumed before the command token. Not configurable away; see lesson 18.")

    print()
    print("=" * 78)
    print("4. add_subparsers returns an ACTION, and it shows up in _actions")
    print("=" * 78)
    for action in parser._actions:
        kind = type(action).__name__
        extra = ""
        if isinstance(action, argparse._SubParsersAction):
            extra = f"  dest={action.dest!r} choices={sorted(action.choices)}"
        print(f"  {kind:<22}{extra}")

    print()
    print("=" * 78)
    print("5. What each subparser owns: description, epilog, formatter, help")
    print("=" * 78)
    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    status_parser = sub_action.choices["status"]
    print(f"  prog status --help  ->  {status_parser.format_usage().strip()}")
    print(f"  description         ->  {status_parser.description!r}")
    print(f"  prog fleet --help   ->  {parser.format_usage().strip()}")

    print()
    print("=" * 78)
    print("6. No command given: hasattr(args,'func') is your test")
    print("=" * 78)
    for argv in ([], ["--json"]):
        args = parser.parse_args(argv)
        print(f"  {argv!s:<12} -> command={args.command!r} "
              f"has func={hasattr(args, 'func')}")
    print()
    print("  Alternative: add_subparsers(required=True), which makes argparse")
    print("  report it. See lesson 19 for the trade-off.")

    return 0


def _try(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace | str:
    """Parse with stderr captured; return the namespace or the error message."""
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv)
    except SystemExit:
        return _error_from(buffer.getvalue())


def _capture(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            parser.parse_args(argv)
        except SystemExit:
            pass
    return buffer.getvalue()


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit:
        pass
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
