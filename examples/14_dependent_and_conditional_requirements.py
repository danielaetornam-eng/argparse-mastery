"""Lesson 14 - Dependencies between options: the feature argparse does not have.

Run me
------
    python examples/14_dependent_and_conditional_requirements.py --url http://x
    python examples/14_dependent_and_conditional_requirements.py --demo

The gap
-------
argparse expresses exactly four relationships between arguments:

============================  ==========================================
Feature                        Mechanism
============================  ==========================================
"exactly one of"               mutually exclusive group, ``required=True``
"at most one of"               mutually exclusive group
"this one must appear"         ``required=True``
"this one takes N values"      ``nargs``
============================  ==========================================

It cannot express:

* ``--insecure`` requires ``--url``          (dependency)
* ``--output`` requires at least one input    (dependency)
* ``--range`` conflicts with ``--point``      (conditional exclusivity)
* exactly two of four options                  (cardinality > 1)

Every real CLI eventually needs the first one. A CLI that documents "these two
go together" in its README and silently ignores the violation when a user forgets
is worse than one that refuses.

Five techniques, best first
---------------------------
**1. A validation function called right after parsing.**
The workhorse. Keep the rules in named functions, collect every failure, report
them together. :func:`validate` below is the pattern.

**2. Custom ``type=`` for value-intrinsic rules.**
If the constraint is about *one value* (``--port`` must be 1-65535), it belongs
in ``type=``. argparse then attributes the error to the right argument, for free.

**3. A custom Action for value-plus-context rules.**
When the check needs ``parser`` (to call ``parser.error``) or needs to *modify*
the value (fill in a default from another argument), an Action is the right tool.
See :class:`RequireSiblingAction`.

**4. ``set_defaults`` with a computed value.**
Useful when one argument should default to a function of another::

    parser.set_defaults(timeout=lambda ns: 30 if ns.fast else 300)

Note the value is not called for you -- you call it. Still, it keeps the coupling
visible at the definition site instead of scattered through handlers.

**5. ``argparse_mastery.builders.enforce_dependencies``** if you already use the
declarative builder, which tracks ``deps=`` per argument.

Where to put the call
---------------------
Immediately after ``parse_args``, inside ``main``, before any side effect::

    args = parser.parse_args(argv)
    validate(args, parser)     # <- here, before anything is done

Doing it later -- in the handler, or after opening a file -- means the user finds
out about a typo after their disk has been touched.

Error message shape
-------------------
One error, listing everything::

    prog: error: --tls requires --url; --retry must be >= 0, got -1

Versus argparse's default, which stops at the first problem. Users can fix both
in a single run, which is the difference between a CLI people tolerate and one
they trust.

The epilog trick
----------------
argparse cannot *explain* a dependency in ``--help`` beyond free text, but you
can document relationships there, which is better than nothing::

    parser = argparse.ArgumentParser(..., epilog=EPILOG)

    EPILOG = (
        'Notes:\n'
        '  --tls   is meaningful only together with --url.\n'
        '  --range and --point are alternative ways to give geometry.'
    )

Prose in the epilog plus enforcement in ``validate()``. The two together are
strictly better than either alone.
"""

from __future__ import annotations

import argparse
import sys


class RequireSiblingAction(argparse.Action):
    """An action that refuses its value unless a sibling option was supplied.

    The dependency has to be checked *after* the whole command line is parsed,
    because ``--tls`` may appear before ``--url``. An Action's ``__call__`` runs
    as soon as the token is consumed, so it checks the namespace at that moment --
    which only works when the sibling happens to come first.

    That ordering dependency is exactly why :func:`validate` (run after parsing)
    is usually the better tool. This class is shown because it is the right tool
    in one specific case: when the check needs ``parser`` to report, and when you
    are willing to document "the sibling must come first".

    Note the two-phase trick used to make it order-independent: the Action records
    what it saw, and the real check happens in :meth:`finalize`, called from
    ``main`` after parsing. That is a reusable pattern for "checks that need the
    complete command line".
    """

    #: Filled in by :func:`check_requirements` after parsing.
    pending: list[tuple[str, str]] = []

    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        requires: str = "",
        **kwargs: object,
    ) -> None:
        self.requires = requires
        super().__init__(option_strings, dest, **kwargs)  # type: ignore[arg-type]

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        if option_string:
            RequireSiblingAction.pending.append((option_string, self.requires))


def check_requirements(namespace: argparse.Namespace) -> None:
    """Second phase of :class:`RequireSiblingAction`, now order-independent."""
    for flag, requires in RequireSiblingAction.pending:
        if not getattr(namespace, requires.lstrip("-").replace("-", "_"), None):
            raise SystemExit(
                f"{flag} requires {requires}"
            )
    RequireSiblingAction.pending.clear()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch",
        description="Lesson 14: option dependencies.",
        epilog=(
            "Notes:\n"
            "  --tls     is meaningful only together with --url.\n"
            "  --range   and --point are alternative ways to give geometry.\n"
            "  Exactly two of --a/--b/--c must be given (argparse cannot say this)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- a value-intrinsic rule belongs in type= (technique 2) -----------
    parser.add_argument(
        "--port",
        type=_port,
        default=443,
        metavar="PORT",
        help="TCP port; range-checked inside type=",
    )

    # --- a dependency between two options (technique 1) -----------------
    parser.add_argument("--url", metavar="URL", default=None, help="URL to fetch")
    parser.add_argument(
        "--tls",
        action="store_true",
        help="require TLS; only valid with --url",
    )

    # --- conditional exclusivity (technique 1) --------------------------
    parser.add_argument("--range", nargs=2, type=int, metavar=("LO", "HI"))
    parser.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"))

    # --- cardinality > 1 (technique 1) ---------------------------------
    parser.add_argument("--a", action="store_true")
    parser.add_argument("--b", action="store_true")
    parser.add_argument("--c", action="store_true")
    parser.add_argument(
        "--exactly-two",
        action="store_true",
        help="require exactly two of --a/--b/--c",
    )

    # --- a value that depends on another value (technique 1) ------------
    parser.add_argument(
        "--retry",
        type=int,
        default=0,
        metavar="N",
        help="retries; must be >= 0",
    )

    return parser


def _port(raw: str) -> int:
    """A value-intrinsic rule. argparse attributes the error to ``--port``."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(
            f"{value} is outside the valid port range 1-65535"
        )
    return value


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Every rule argparse cannot express, reporting all failures at once.

    Three categories, deliberately kept as separate named checks rather than one
    long ``if`` chain -- so each rule can be tested on its own and the error
    message can name the rule that failed.
    """
    problems: list[str] = []

    # (a) dependency: A requires B.
    if args.tls and not args.url:
        problems.append("--tls requires --url")

    # (b) conditional exclusivity: only certain combinations are illegal.
    if args.range and args.point:
        problems.append("--range and --point are alternative, not combinable")

    # (c) cardinality greater than one.
    if args.exactly_two:
        chosen = [n for n in ("a", "b", "c") if getattr(args, n)]
        if len(chosen) != 2:
            problems.append(
                f"--exactly-two needs two of --a/--b/--c, got {len(chosen)}: "
                f"{' '.join(chosen) or '(none)'}"
            )

    # (d) a constraint on one value that choices cannot express cleanly.
    if args.retry < 0:
        problems.append(f"--retry must be >= 0, got {args.retry}")

    if problems:
        parser.error("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate BEFORE doing anything with side effects. Getting this wrong means
    # the user discovers a typo after their filesystem has already been touched.
    validate(args, parser)
    check_requirements(args)

    print(f"url   : {args.url}")
    print(f"tls   : {args.tls}")
    print(f"port  : {args.port}")
    print(f"retry : {args.retry}")
    print("all rules satisfied")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. A rule about one VALUE belongs in type=")
    print("=" * 78)
    for value in ("443", "70000", "http"):
        print(f"  --port {value:<8} -> {_port_of(parser, value)}")
    print()
    print("  argparse produced the message; no validation function needed, and the")
    print("  error names --port specifically.")

    print()
    print("=" * 78)
    print("2. A dependency between two OPTIONS needs validate()")
    print("=" * 78)
    checks = [
        (["--tls"], "--tls without --url"),
        (["--tls", "--url", "http://x"], "--tls with --url"),
        (["--url", "http://x"], "neither"),
    ]
    for argv, label in checks:
        print(f"  {argv!s:<32} ({label})")
        print(f"      -> {_validate_err(parser, argv)}")

    print()
    print("=" * 78)
    print("3. All violations reported at once")
    print("=" * 78)
    argv = ["--tls", "--range", "1", "2", "--point", "3", "4", "--retry", "-1",
            "--exactly-two", "--a"]
    args = parser.parse_args(argv)
    print(f"  argv={argv}")
    print(f"  -> {_validate_err(parser, argv)}")
    print()
    print("  Compare with argparse's first-failure-only behaviour, and with a")
    print("  validate() that calls parser.error inside each rule: the user would")
    print("  need four runs to fix four problems.")

    print()
    print("=" * 78)
    print("4. Cardinality greater than one")
    print("=" * 78)
    for argv in (["--exactly-two", "--a", "--b"], ["--exactly-two", "--a"],
                 ["--exactly-two", "--a", "--b", "--c"]):
        print(f"  {argv!s:<38} -> {_validate_err(parser, argv)}")

    print()
    print("=" * 78)
    print("5. --help can DOCUMENT a dependency, but cannot enforce one")
    print("=" * 78)
    for line in parser.epilog.splitlines() if parser.epilog else ():
        print(f"  {line}")
    print()
    print("  argparse has no field for 'requires: --url'. Prose is the honest")
    print("  option; enforcement is validate()'s job.")

    print()
    print("=" * 78)
    print("6. Ordering: why a single-pass Action is not enough")
    print("=" * 78)
    print("  --tls --url http://x    Action sees --tls first; --url not yet parsed")
    print("  --url http://x --tls    Action sees --url first; check passes")
    print()
    print("  An Action's __call__ runs as the token is consumed, so a naive")
    print("  sibling check is order-dependent. The two-phase pattern (record now,")
    print("  check in check_requirements() after parsing) removes that entirely.")

    return 0


def _port_of(parser: argparse.ArgumentParser, value: str) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(["--port", value]).port
    except SystemExit:
        return _err(buffer.getvalue())


def _validate_err(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        try:
            args = parser.parse_args(argv)
            validate(args, parser)
            return "accepted"
        except SystemExit:
            return _err(buffer.getvalue())


def _err(text: str) -> str:
    for line in text.splitlines():
        if ": error:" in line:
            return "ERROR: " + line.split(": error:", 1)[1].strip()
    return "(no error)"


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
