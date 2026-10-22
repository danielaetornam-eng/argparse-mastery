"""Lesson 23 - The Action protocol: subclassing ``argparse.Action``.

Run me
------
    python examples/23_custom_action_basics.py --tag x --tag y
    python examples/23_custom_action_basics.py --demo

The protocol is tiny
--------------------
Every ``Action`` -- built-in or custom -- does one thing: write a value into the
namespace when argparse hands it a matched token::

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)

``_StoreAction`` (``action='store'``) is exactly that. So is ``_StoreTrueAction``,
with ``const=True`` and ``nargs=0``. Writing a custom action is never
"subclassing a mystery class" -- it is "decide what should land in the namespace".

The minimum viable subclass
---------------------------
::

    class Doubler(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, int(values) * 2)

    parser.add_argument("--count", type=int, action=Doubler)

Note it can ignore ``type``: use ``int(values)`` directly if you want the raw
string. Deciding whether ``type`` runs first is a real design question, and the
answer differs per action. Being explicit (``values`` is already ``int`` if you
set ``type=int``) documents the decision.

Custom keyword arguments
------------------------
``add_argument`` forwards unknown keywords to the action class, so
``action=Collect, sep=','`` works. They arrive in ``__init__`` and you **must pop
them before calling ``super().__init__``**::

    def __init__(self, option_strings, dest, sep=",", **kwargs):
        self.sep = sep
        super().__init__(option_strings, dest, **kwargs)

Pick names that cannot collide with ``add_argument``'s own parameters
(``default``, ``type``, ``choices``, ``nargs``, ``metavar``, ``help``,
``required``, ``const``, ``action``, ``dest``, ``version``). ``sep`` is safe;
``dest`` or ``default`` would silently do the wrong thing.

Registering a string name
-------------------------
::

    parser.register("action", "collect", Collect)
    parser.add_argument("--tag", action="collect")

``register`` stores the class on the *parser instance*, so it must be called on
every parser you create -- including subparsers. See
:func:`register_all` in ``argparse_mastery.actions``.

What you get from ``self``
--------------------------
``option_strings``, ``dest``, ``nargs``, ``const``, ``default``, ``type``,
``choices``, ``required``, ``help``, ``metavar``. Two are worth using
deliberately:

* ``self.const`` -- the value for ``nargs=0`` or ``nargs='?'`` with no token.
* ``self.default`` -- the value present before parsing. This is how you detect
  "first occurrence" and initialise an accumulator.

``option_string`` is per-spelling
--------------------------------
The same Action can behave differently for ``-v`` and ``--verbose`` by checking
``option_string``. That is how a single action implements both a flag and its
negation, and how ``-q``/``-v`` map to one level variable.

The mutable-default trap, handled properly
------------------------------------------
The canonical accumulator bug::

    parser.add_argument("--tag", action="append", default=[])

``append`` mutates *your* list, so the default is polluted and leaks across parses
in one process. The fix inside a custom action is to copy::

    current = list(getattr(namespace, self.dest, None) or [])

Compare three parses in the demo to see the difference. This is the pattern every
custom accumulating action should use, and it is why
``argparse_mastery.actions.AppendUniqueAction`` copies.
"""

from __future__ import annotations

import argparse
import sys


class Doubler(argparse.Action):
    """The minimum viable custom action.

    Deliberately ignores ``type``: ``values`` arrives as whatever ``type``
    produced (a ``str`` here). Writing ``int(values)`` makes the conversion
    explicit; a real action would either rely on ``type=int`` or document that it
    does its own conversion.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, int(values) * 2)


class Collect(argparse.Action):
    """Accumulate values into a list, de-duplicating, without the default leak.

    Demonstrates every part of the protocol:

    * a **custom keyword** (``sep``) popped before ``super().__init__``,
    * an **initialiser** taken from ``self.default``,
    * a **copy** so the default object is never mutated,
    * ``option_string`` available for per-spelling behaviour.
    """

    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        sep: str = ",",
        **kwargs: object,
    ) -> None:
        # Pop custom keywords BEFORE super().__init__: argparse does not know
        # about `sep`, and passing it through would be a TypeError.
        self.sep = sep
        super().__init__(option_strings, dest, **kwargs)  # type: ignore[arg-type]

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        # Copy, never mutate: the namespace may be holding `self.default`.
        current = list(getattr(namespace, self.dest, None) or [])
        for item in values.split(self.sep):
            item = item.strip()
            if item and item not in current:
                current.append(item)
        setattr(namespace, self.dest, current)


class FirstWins(argparse.Action):
    """Keep the first occurrence; warn (via ``parser.error``) on a repeat.

    Shows the use of ``parser`` from inside ``__call__`` -- which is the only way
    to produce a proper usage error from an action, because only ``parser.error``
    prints the usage line and picks the right exit status.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error(
                f"{option_string} may be given only once "
                f"(already set to {getattr(namespace, self.dest)!r})"
            )
        setattr(namespace, self.dest, values)


class PairingLevel(argparse.Action):
    """``-v`` and ``-q`` writing to one integer, order-independent.

    ``option_string`` is what makes this possible: one Action, two spellings, and
    ``self.const`` is fixed so the sign comes from ``option_string`` rather than
    from ``values``.

    The starting point comes from ``self.default`` each time rather than from the
    current namespace value, which is why ``-q -v`` and ``-v -q`` agree.
    """

    def __init__(self, option_strings: list[str], dest: str, **kwargs: object) -> None:
        # Class-level constants are not allowed in argparse actions, so build the
        # map in __init__ from the option strings we were given.
        self._deltas = {
            flag: (1 if flag in ("-v", "--verbose") else -1) for flag in option_strings
        }
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", 0)
        kwargs.setdefault("metavar", "")
        super().__init__(option_strings, dest, **kwargs)  # type: ignore[arg-type]

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        start = getattr(self, "default", 0) or 0
        delta = self._deltas.get(option_string or "", 0)
        setattr(namespace, self.dest, start + delta)


def register_all(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Make these actions available by string name on this parser.

    ``register`` is per-parser instance, so subparsers do **not** inherit it. Call
    this for every parser you build, or pass a parent parser (lesson 18).
    """
    parser.register("action", "doubler", Doubler)
    parser.register("action", "collect", Collect)
    parser.register("action", "first_wins", FirstWins)
    parser.register("action", "level", PairingLevel)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = register_all(
        argparse.ArgumentParser(
            prog="actions",
            description="Lesson 23: the Action protocol.",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
    )

    # --- the minimum viable action --------------------------------------
    parser.add_argument("--count", type=int, action=Doubler, metavar="N",
                        help="stored doubled (action=Doubler)")

    # --- accumulating, de-duplicating, with a custom keyword -------------
    parser.add_argument("--tag", action="collect", sep=",", default=[],
                        metavar="LIST", help="comma-separated, de-duplicated")

    # --- a different separator, proving the keyword reached the action ----
    parser.add_argument("--path", action="collect", sep=":", default=[],
                        metavar="LIST", help="colon-separated list")

    # --- single-occurrence enforcement -----------------------------------
    parser.add_argument("--config", action="first_wins", metavar="PATH",
                        help="may be given at most once")

    # --- one action, two spellings, one accumulator ----------------------
    parser.add_argument("-v", "--verbose", dest="loglevel", action="level",
                        help="increase verbosity")
    parser.add_argument("-q", "--quiet", dest="loglevel", action="level",
                        help="decrease verbosity")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for key in sorted(vars(args)):
        print(f"  {key:<10} = {getattr(args, key)!r}")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. A built-in action is three lines")
    print("=" * 78)
    print("  argparse._StoreAction.__call__:")
    print("      setattr(namespace, self.dest, values)")
    print("  argparse._StoreTrueAction subclasses it with const=True, nargs=0.")
    print("  So 'action=store_true' == 'action=store_const, const=True, nargs=0'.")

    print()
    print("=" * 78)
    print("2. The minimum viable custom action")
    print("=" * 78)
    for value in ("5", "x"):
        print(f"  --count {value:<4} -> count={_count(parser, value)!r}")

    print()
    print("=" * 78)
    print("3. An accumulating action that does not corrupt its default")
    print("=" * 78)
    print(f"  parse 1 (--tag a,b)  -> tag={parser.parse_args(['--tag','a,b']).tag!r}")
    print(f"  parse 2 (--tag b,c)  -> tag={parser.parse_args(['--tag','b,c']).tag!r}")
    print(f"  parse 3 (nothing)    -> tag={parser.parse_args([]).tag!r}   <- clean")

    print()
    print("  argparse's own append with a list default:")
    leaky = argparse.ArgumentParser(prog="actions", add_help=False)
    leaky.add_argument("--tag", action="append", default=[])
    print(f"  parse 1 (--tag a)    -> tag={leaky.parse_args(['--tag','a']).tag!r}")
    print(f"  parse 2 (--tag b)    -> tag={leaky.parse_args(['--tag','b']).tag!r}")
    print(f"  parse 3 (nothing)    -> tag={leaky.parse_args([]).tag!r}   <- polluted")
    print()
    print("  Same fix in argparse's own code would be one line:")
    print("      current = list(getattr(namespace, self.dest, None) or [])")

    print()
    print("=" * 78)
    print("4. Custom keywords reach __init__ -- and must be popped")
    print("=" * 78)
    print(f"  --tag a,b    (sep=',') -> {parser.parse_args(['--tag','a,b']).tag!r}")
    print(f"  --path x:y   (sep=':') -> {parser.parse_args(['--path','x:y']).path!r}")
    print()
    print("  Try replacing self.sep with kwargs['sep'] and super() will raise")
    print("  TypeError: __init__() got an unexpected keyword argument 'sep'.")

    print()
    print("=" * 78)
    print("5. parser.error from inside an action")
    print("=" * 78)
    for argv in (["--config", "a.toml"], ["--config", "a.toml", "--config", "b.toml"]):
        print(f"  {argv!s:<44} -> {_msg(parser, argv)}")
    print()
    print("  Only parser.error prints the usage line and exits 2. An action that")
    print("  raises something else produces a traceback instead.")

    print()
    print("=" * 78)
    print("6. option_string: one action, two spellings, order-independent")
    print("=" * 78)
    for argv in ([], ["-v"], ["-vv"], ["-q"], ["-v", "-q"], ["-q", "-v"]):
        print(f"  {argv!s:<14} -> loglevel={parser.parse_args(argv).loglevel}")
    print()
    print("  Both orders give 0, because each call starts from self.default rather")
    print("  than from the current value. Starting from the current value would")
    print("  make '-v -q' = 0 and '-q -v' = 0 here but -1 for three flags -- order")
    print("  would start to matter.")

    print()
    print("=" * 78)
    print("7. register() is per-parser, so subparsers do not inherit")
    print("=" * 78)
    top = register_all(argparse.ArgumentParser(prog="actions", add_help=False))
    sub = top.add_subparsers(dest="command")
    leaf = sub.add_parser("go")
    print(f"  'go --tag a' -> {_msg(top, ['go', '--tag', 'a'])}")
    print()
    print("  Fix: register on every parser, or use a parent parser (lesson 18).")

    return 0


def _count(parser: argparse.ArgumentParser, value: str) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(["--count", value]).count
    except SystemExit:
        return _err(buffer.getvalue())


def _msg(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
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
