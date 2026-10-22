"""Lesson 19 - Subcommand defaults, ``required=True``, and the no-command case.

Run me
------
    python examples/19_subcommand_defaults.py --json remote-add origin
    python examples/19_subcommand_defaults.py --demo

Four ways to give a subcommand a default
---------------------------------------
**1. In the subparser itself.** The obvious way::

    sub.add_parser("status").set_defaults(retries=3)

**2. On the parent's ``set_defaults``, keyed by ``dest`` of the subparsers action.**
This is the useful trick. argparse has no "apply to subcommand X" API, but you
can set a default whose key only exists once a particular subparser ran::

    parser.set_defaults(**{f"timeout_{name}": 30 for name, _ in COMMANDS.items()})

**3. By looping over the subparsers.** There is **no** ``set_defaults`` on the
``add_subparsers`` result -- ``_SubParsersAction`` is an Action, not a parser, so
it has no such method (calling it raises ``AttributeError``). The supported way
is to iterate the children::

    for child in sub.choices.values():
        child.set_defaults(color=True)

That is exactly what ``parents=`` does for you, with the shared-object caveats
from lesson 18. Which is why parents is usually the better tool.

**4. By making an option optional and defaulting it.** Often the best answer:
``--retries`` with ``default=3`` inside the subparser, rather than a hidden
default plus a required flag.

Where defaults must NOT come from
----------------------------------
A default that is a **mutable** object is shared with the namespace and mutated
by ``append``/``extend``/in-place actions. This leaks across parses inside one
process -- most visibly in tests, where a second test sees the first test's
data. Three fixes: no default at all (``None``), a factory-built parent
(lesson 18), or copy inside your Action.

``required=True`` on subparsers
------------------------------
``add_subparsers(required=True)`` makes argparse complain when no command is
given::

    prog: error: the following arguments are required: <command>

Three philosophies for ``prog`` with no arguments:

================================  ==============================================
Approach                          Behaviour
================================  ==============================================
``required=True``                 usage error, exit 2
``not required`` + ``print_help`` help on stderr, exit 2
``not required`` + default func   run a default command (``prog`` == ``prog help``)
================================  ==============================================

All three are defensible. The choice is a statement about whether the tool is a
*command* or a *tool*. ``git`` with no arguments prints usage and exits 1. ``npm``
with no arguments prints help and exits 0. ``docker`` with no arguments prints
help. Pick one deliberately and be consistent across your subcommands.

Exit code detail: when you print help yourself, decide whether to return 0 or 2.
Returning 2 is correct if "no command" is a usage error; returning 0 is correct
if the tool considers "show me help" a satisfied request. Scripts branch on this.

Providing a default command
---------------------------
Set a ``func`` at the top level via ``set_defaults`` so that a bare ``prog`` runs
help. This also gives every command a fallback that a child can override::

    parser.set_defaults(func=lambda ns: (parser.print_help(), 2)[1])

Lambdas with side effects are bad practice; this one is fine and idiomatic, but
prefer a named function in real code.
"""

from __future__ import annotations

import argparse
import sys


def cmd_status(args: argparse.Namespace) -> int:
    print(f"status: retries={args.retries} quiet={args.quiet}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    print(f"push: remote={args.remote} retries={args.retries}")
    return 0


def cmd_help(args: argparse.Namespace) -> int:
    args.parser.print_help()
    return 0


def build_parser(require_command: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ship",
        description="Lesson 19: subcommand defaults.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=require_command,
        parser_class=argparse.ArgumentParser,
    )

    status = sub.add_parser("status", help="show status")
    status.add_argument("--retries", type=int, default=3, help="probe attempts")
    status.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="suppress per-probe output",
    )
    status.set_defaults(func=cmd_status)

    push = sub.add_parser("push", help="push to a remote")
    push.add_argument("remote")
    # This command deliberately does NOT declare --retries. See demo section 3.
    push.set_defaults(func=cmd_push)

    help_cmd = sub.add_parser("help", help="show this help")
    help_cmd.set_defaults(func=cmd_help)

    # A default applied to EVERY command. There is no `sub.set_defaults` --
    # _SubParsersAction is an Action, not a parser -- so loop over the children.
    # `parents=` is the tidier way to achieve the same thing.
    for child in sub.choices.values():
        child.set_defaults(color=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(require_command=False)
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        # No command given. Print help and return 2 -- "no command" is a usage
        # error, and a script should be able to tell that apart from success.
        parser.print_help(sys.stderr)
        return 2

    return args.func(args)


def demo() -> int:
    print("=" * 78)
    print("1. Defaults set on the add_subparsers ACTION reach every child")
    print("=" * 78)
    parser = build_parser()
    for argv in (["status"], ["push", "origin"], ["help"]):
        args = parser.parse_args(argv)
        print(f"  {argv!s:<18} color={args.color!r}  command={args.command!r}")
    print()
    print("  _SubParsersAction has no set_defaults -- it is an Action, not a parser.")
    print("  `sub.set_defaults(...)` raises AttributeError. Loop over sub.choices,")
    print("  or use parents= (lesson 18).")

    print()
    print("=" * 78)
    print("2. Per-subcommand defaults")
    print("=" * 78)
    args = parser.parse_args(["status"])
    print(f"  status: retries={args.retries} (from the subparser's own default)")
    args = parser.parse_args(["status", "--retries", "9"])
    print(f"  status --retries 9 -> retries={args.retries}")

    print()
    print("=" * 78)
    print("3. Defaults are per-subparser, not shared across them")
    print("=" * 78)
    args = parser.parse_args(["push", "origin"])
    print(f"  push origin -> has 'retries'? {'retries' in vars(args)}")
    print()
    print("  'push' never declared --retries, so there is no attribute at all --")
    print("  not None, absent. Code that does args.retries raises AttributeError.")
    print("  Use getattr(args, 'retries', 3) when reading across commands, or share")
    print("  the option with parents= (lesson 18).")

    print()
    print("=" * 78)
    print("4. The mutable-default leak, demonstrated")
    print("=" * 78)
    leaky = argparse.ArgumentParser(prog="ship", add_help=False)
    leaky.add_argument("--tag", action="append", default=[])
    print(f"  parse 1 (--tag a) : {leaky.parse_args(['--tag','a']).tag!r}")
    print(f"  parse 2 (--tag b) : {leaky.parse_args(['--tag','b']).tag!r}")
    print(f"  parse 3 (nothing) : {leaky.parse_args([]).tag!r}   <- polluted")
    print()
    clean = argparse.ArgumentParser(prog="ship", add_help=False)
    clean.add_argument("--tag", action="append")
    print(f"  no default, p3    : {clean.parse_args([]).tag!r}   <- clean")

    print()
    print("=" * 78)
    print("5. required=True vs printing help yourself")
    print("=" * 78)
    required = build_parser(require_command=True)
    optional = build_parser(require_command=False)

    print(f"  required=True, no args  -> {_error(required, [])}")
    print()
    ns = optional.parse_args([])
    print(f"  required=False, no args -> command={ns.command!r} "
          f"has func={hasattr(ns, 'func')}")
    print("  main() then prints help and returns 2.")
    print()
    print("  Both are legitimate. required=True gives argparse's message (better),")
    print("  which is why it is the default choice for a tool with real commands.")

    print()
    print("=" * 78)
    print("6. A default command: bare `prog` behaves like `prog help`")
    print("=" * 78)
    defaulted = build_parser(require_command=False)
    defaulted.set_defaults(func=cmd_help, parser=defaulted)
    ns = defaulted.parse_args([])
    print(f"  bare prog -> command={ns.command!r} func={ns.func.__name__}")
    ns = defaulted.parse_args(["status"])
    print(f"  prog status -> command={ns.command!r} func={ns.func.__name__}  (child overrides)")
    print()
    print("  Set the fallback with set_defaults(func=...) on the PARENT; each")
    print("  subparser's own set_defaults then overrides it.")

    return 0


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
    except SystemExit:
        pass
    for line in buffer.getvalue().splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
