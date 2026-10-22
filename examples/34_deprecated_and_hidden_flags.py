"""Lesson 34 - Deprecating flags and commands without breaking users.

Run me
------
    python examples/34_deprecated_and_hidden_flags.py --old-format json --demo
    python examples/34_deprecated_and_hidden_flags.py --format json

The deprecation ladder
----------------------
Removing a flag breaks scripts. The ladder from safest to cleanest:

========================================  ==========================  ==========
Stage                                      User experience              Removal
========================================  ==========================  ==========
1. Documented, no warning                  works, silent               indefinite
2. ``help=argparse.SUPPRESS``               works, invisible            indefinite
3. a formatter marker                     works, marked deprecated    1-2 releases
4. warning on use                           works, warned               1-2 releases
5. ``deprecated=True`` (3.14+, flags too)   works, warned by argparse    when ready
6. hard error                               **breaks**                 now
========================================  ==========================  ==========

Never go straight to 6. A flag someone scripted three years ago is not going to
read your changelog.

Python 3.14: ``deprecated=True`` for commands
----------------------------------------------
Verified on 3.14, ``add_subparsers().add_parser()`` accepts ``deprecated=True``,
which emits at parse time::

    prog: warning: command 'purge' is deprecated

and then **runs the command normally**, exiting 0. It does not hide the command:
pass ``help=argparse.SUPPRESS`` as well for a fully invisible command.

This is the cleanest deprecation story argparse now offers, because the warning is
emitted by argparse itself rather than by handler code that someone has to remember
to call.

For flags too, on 3.14
----------------------
**Correction:** 3.14 does not restrict ``deprecated=`` to commands.
``argparse.Action.__init__`` takes a ``deprecated`` parameter, so an *option* can
carry it::

    parser.add_argument("--old-format", deprecated=True, choices=("json", "text"))

Verified on 3.14.6 for ``store``, ``store_true``, ``count`` and ``nargs=2``::

    prog: warning: option '--old-format' is deprecated

It then behaves like the command form: the option still works, the exit status is
0, and it is **not** hidden from ``--help`` -- add ``help=argparse.SUPPRESS`` for
that, which removes it from the usage line as well (see lesson 06).

Its limitation is the message: fixed wording, no replacement, no removal version.
So a custom Action is still the better choice when the message matters, which is
most of the time:

1. **A custom Action that warns.** :class:`DeprecatedAction` below. Warn through
   :mod:`warnings`, not ``print``, so ``--quiet`` and capture-based tests behave.
2. **Aliasing to a shared dest.** The deprecated spelling and the new one write to
   the same ``dest``, so there is exactly one implementation and the two cannot
   drift apart. This is the part that matters most.
3. **A formatter marker.** Override ``_get_help_string`` on your formatter and
   read a marker off the action -- because overriding ``action.format_help``
   achieves nothing under the stock formatters (lesson 25, correction 1).

Warning correctly
-----------------
``DeprecationWarning`` is **hidden by default** unless it comes from
``__main__``. Since a CLI's deprecation notice is aimed at the *user*, not at a
library author, raise it with ``stacklevel=2`` from the action -- that attributes
it to the caller and makes it visible. Verified behaviour on 3.14::

    prog --old-format json
    # DeprecationWarning: --old-format is deprecated; use --format

Alternatively write to stderr directly with a ``[deprecated]`` prefix, which is
what most mature tools do -- and is visible under ``python -W ignore``. Pick one
and be consistent.

Hidden and deprecated are different things
-------------------------------------------
``help=SUPPRESS`` means "do not show it". A deprecated flag should usually still
be *shown*, marked, for at least one release -- otherwise users have no way to
learn they should stop using it. Hide it in the same release you remove it.
"""

from __future__ import annotations

import argparse
import sys
import warnings


class DeprecatedAction(argparse.Action):
    """A flag that warns, forwards to a shared dest, and marks itself in ``--help``.

    Three overrides, each doing one job:

    * :meth:`__call__` -- warn via :mod:`warnings`, then store. Storing keeps the
      old and new spellings writing to the same ``dest``, so there is one
      implementation and the two cannot drift apart.
    * :meth:`format_help` -- append a marker, so the deprecation is visible
      *before* the user runs anything.
    * :meth:`format_usage` -- return ``""`` to remove it from the usage line,
      which closes the ``help=SUPPRESS`` gap argparse leaves (lesson 25).

    ``hidden=True`` also drops it from the help table, at which point
    ``format_help`` is not called and only ``format_usage`` matters.
    """

    def __init__(self, option_strings: list[str], dest: str, **kwargs: object) -> None:
        self.replacement: str = kwargs.pop("replacement", "")
        self.removed_in: str = kwargs.pop("removed_in", "")
        self.hidden: bool = kwargs.pop("hidden", False)
        super().__init__(option_strings, dest, **kwargs)  # type: ignore[arg-type]

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        parts = [f"{option_string or self.dest} is deprecated"]
        if self.replacement:
            parts.append(f"use {self.replacement} instead")
        if self.removed_in:
            parts.append(f"(removed in {self.removed_in})")
        # `warnings`, not `print`: it is deduplicated, filterable with -W, and
        # visible by default here because stacklevel attributes it to main().
        warnings.warn(" ".join(parts), DeprecationWarning, stacklevel=2)
        setattr(namespace, self.dest, values)

    def format_help(self) -> str:
        if self.hidden:
            return ""
        marker = "[DEPRECATED]"
        if self.replacement:
            marker += f" use {self.replacement}"
        return f"{self.help or ''}  {marker}"

    def format_usage(self) -> str:
        return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ship",
        description="Lesson 34: deprecating things gracefully.",
        epilog=(
            "Deprecations:\n"
            "  --old-format   use --format          (warns, shown in --help)\n"
            "  purge          use prune             (3.14 deprecated=True)\n"
            "  --old-host     hidden escape hatch   (SUPPRESS + format_usage)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # The real option.
    parser.add_argument("--format", choices=("text", "json", "yaml"),
                        default="text", help="output format")

    # Stage 4: visible, marked, warns. Shares `format` with --format above.
    parser.add_argument(
        "--old-format",
        action=DeprecatedAction,
        dest="format",
        choices=("text", "json", "yaml"),
        replacement="--format",
        removed_in="2.0",
        help="choose the output format",
    )

    # Stage 2: hidden escape hatch. SUPPRESS hides the help row; the action's
    # format_usage removes it from the usage line too.
    parser.add_argument(
        "--old-host",
        action=DeprecatedAction,
        dest="old_host",
        replacement="nothing (it was always a mistake)",
        hidden=True,
        help=argparse.SUPPRESS,
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    prune = sub.add_parser("prune", help="remove stale records")
    prune.add_argument("--force", action="store_true", help="do not prompt")
    prune.set_defaults(func=lambda ns: print("prune done"))

    # Stage 5: Python 3.14's native deprecation for a command.
    if sys.version_info >= (3, 14):
        purge = sub.add_parser(
            "purge", deprecated=True, help="remove ALL records (deprecated)"
        )
    else:  # pragma: no cover - 3.11-3.13 fallback
        purge = sub.add_parser("purge", help="remove ALL records (deprecated)")
    purge.set_defaults(func=lambda ns: print("purge done"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    print(f"format={args.format}")
    return args.func(args)


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. Stage 4: visible in --help, marked, warns on use")
    print("=" * 78)
    for line in parser.format_help().splitlines():
        if "format" in line or "old-host" in line:
            print(f"  {line.rstrip()}")
    print()
    print("  --old-host is gone from BOTH the help table and the usage line:")
    print(f"    '--old-host' in usage? {'--old-host' in parser.format_usage()}")
    print()
    print("  Compare a plain help=SUPPRESS action, which leaves the usage line dirty:")
    leaky = argparse.ArgumentParser(prog="ship", add_help=False)
    leaky.add_argument("--keep")
    leaky.add_argument("--gone", help=argparse.SUPPRESS)
    print(f"    {leaky.format_usage().strip()}")

    print()
    print("=" * 78)
    print("2. Both spellings write the same dest")
    print("=" * 78)
    import contextlib
    import io

    for argv in (["--format", "json"], ["--old-format", "json"]):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            args = parser.parse_args(argv)
        note = caught[0].message if caught else None
        print(f"  {argv!s:<26} -> args.format={args.format!r}")
        print(f"  {'':<26}    warning: {note}")
    print()
    print("  One implementation, one dest, no possibility of drift.")

    print()
    print("=" * 78)
    print("3. Python 3.14: add_parser(deprecated=True)")
    print("=" * 78)
    print(f"  running on Python {sys.version_info.major}.{sys.version_info.minor}")
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = _run(parser, ["purge"])
    print(f"  `ship purge` exit code : {code}")
    print(f"  stderr                 : {buffer.getvalue().strip()!r}")
    print()
    print("  argparse emits the warning itself, at parse time, and the command still")
    print("  runs. Exit 0: a deprecation is not a failure.")
    print("  It is NOT hidden from --help -- pass help=SUPPRESS for that.")

    print()
    print("=" * 78)
    print("4. DeprecationWarning visibility")
    print("=" * 78)
    print("  DeprecationWarning is ignored by default unless it originates in")
    print("  __main__. stacklevel=2 in the action attributes it to the caller, which")
    print("  is why it shows up here at all.")
    print()
    print("  If you want it visible under `python -W ignore`, write to stderr instead:")
    print("      print('warning: --old-format is deprecated; use --format',")
    print("            file=sys.stderr)")
    print("  Both are defensible. Whichever you choose, be consistent.")

    print()
    print("=" * 78)
    print("5. The ladder, and where each rung hides in --help")
    print("=" * 78)
    ladder = [
        ("1. documented only", "help='DEPRECATED'", "visible, no marker"),
        ("2. SUPPRESS", "help=argparse.SUPPRESS", "invisible in help AND usage"),
        ("3. marker", "formatter reads action.note", "visible + [DEPRECATED]"),
        ("4. marker + warn", "action.__call__ warns", "visible, marked, warns"),
        ("5. deprecated=True", "3.14, flags and commands", "warns; not hidden"),
        ("6. hard error", "del the argument", "BREAKS scripts"),
    ]
    for stage, how, effect in ladder:
        print(f"  {stage:<20} {how:<28} {effect}")
    print()
    print("  Do not skip to 6. Scripts written against a flag three years ago will")
    print("  not read your changelog.")

    return 0


def _run(parser: argparse.ArgumentParser, argv: list[str]) -> int:
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        try:
            args = parser.parse_args(argv)
            return args.func(args) if hasattr(args, "func") else 0
        except SystemExit as exc:
            return 0 if exc.code is None else int(exc.code)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
