"""Lesson 24 - Composite actions: CSV, KEY=VALUE, append-unique, env.

Run me
------
    python examples/24_composite_actions.py --tag a,b --tag c --set retries=5
    python examples/24_composite_actions.py --demo

What makes an action "composite"
--------------------------------
It collects more than one token into a single structured value. Four shapes cover
almost every real CLI:

======================================  ==================  ==========================
Shape                                    Example              Result
======================================  ==================  ==========================
comma-separated in one token            ``--tag a,b,c``      ``['a','b','c']``
repeated flag, accumulate               ``--set k=1 --set j``  ``{'k':'1','j':'2'}``
repeated flag, de-duplicate              ``--include a --include a``  ``['a']``
valueless flag reading the environment  ``--token``           ``$API_TOKEN``
======================================  ==================  ==========================

``CsvAction``: one flag, many values
------------------------------------
Users prefer ``--tag a,b,c`` to ``--tag a --tag b``. Support both by pairing the
action with ``nargs='*'``::

    class CsvAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            items = values if isinstance(values, list) else [values]
            current = list(getattr(namespace, self.dest, None) or [])
            for item in items:
                current.extend(p for p in str(item).split(",") if p)
            setattr(namespace, self.dest, current)

    parser.add_argument("--tag", action="csv", nargs="*", default=[])

Then all three forms work:

* ``--tag a,b``      -> ``['a','b']``
* ``--tag a b``       -> ``['a','b']``
* ``--tag a --tag b,c`` -> ``['a','b','c']``

The ``isinstance`` check is not defensive noise: with ``nargs=None`` or ``'?',
``values`` is a bare string, and ``"abc".split(",")`` would iterate characters.

``KeyValueAction``: building a dict from flags
---------------------------------------------
The ``--set KEY=VALUE`` pattern is how you override configuration from the command
line. Split on the **first** ``=`` only (see lesson 20). Also support ``nargs=2``
so ``--set key value`` works, by branching on whether ``values`` is a string.

The key insight: **key-value flags and config files have the same shape**, which
is what makes the layering in ``argparse_mastery.config`` work. A config file
gives you a dict, ``--set`` gives you a dict, and merging them is trivial.

``AppendUniqueAction``: repetition without duplicates
----------------------------------------------------
Identical to ``AppendUniqueAction`` in ``argparse_mastery.actions``. Included
here so you can read the whole idea in one place: copy the current list, add only
what is missing, write it back.

When is de-duplication right? When the value is an *identifier* -- a tag, a path,
a hostname. Never when it is a *quantity* (``--retry 3 --retry 3`` might mean six).

``EnvAction``: a valueless flag that reads the environment
---------------------------------------------------------
``--token`` with no value, resolving to ``$MYAPP_TOKEN``. This keeps secrets out
of shell history and out of ``ps`` output. Two decisions to make explicitly:

* **What if the variable is unset?** This implementation makes the flag a no-op
  (``required_env=False``). The alternative is an error, which is arguably better
  for a required token -- see the constructor.
* **Does ``type`` run?** Not here. Values arriving from other layers may already
  be converted, so re-applying a complex ``type`` would be wrong. Pass through.

Best practice: pair it with an explicit ``--token-file`` for the case where the
token really is on the command line, and document both in ``--help``.
"""

from __future__ import annotations

import argparse
import os
import sys


class CsvAction(argparse.Action):
    """Accept ``--tag a,b``, ``--tag a b``, and ``--tag a --tag b,c`` alike.

    The ``isinstance`` branch is essential rather than defensive: with
    ``nargs=None`` or ``nargs='?'``, ``values`` is a bare string, and iterating a
    string character by character is a silent, spectacular bug.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        items = values if isinstance(values, (list, tuple)) else [values]
        current = list(getattr(namespace, self.dest, None) or [])
        for item in items:
            current.extend(part for part in str(item).split(",") if part)
        setattr(namespace, self.dest, current)


class KeyValueAction(argparse.Action):
    """Accumulate ``KEY=VALUE`` into a dict. Supports ``--set k=v`` and ``--set k v``.

    The ``partition('=')`` split is on the first ``=`` only, so
    ``--set token=a=b`` correctly yields ``{'token': 'a=b'}``. Splitting on every
    ``=`` breaks passwords, and passwords are exactly what this flag is for.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        mapping: dict[str, str] = dict(getattr(namespace, self.dest, None) or {})
        if isinstance(values, str):
            key, sep, value = values.partition("=")
            if not sep:
                parser.error(
                    f"argument {option_string or self.dest}: "
                    f"expected KEY=VALUE, got {values!r}"
                )
            mapping[key.strip()] = value
        else:
            key, value = values  # type: ignore[misc]
            mapping[str(key)] = str(value)
        setattr(namespace, self.dest, mapping)


class AppendUniqueAction(argparse.Action):
    """Accumulate into a list, skipping exact duplicates, preserving order.

    Right when the value is an identifier (tag, path, host). Wrong when it is a
    quantity, where ``--retry 3 --retry 3`` might legitimately mean six.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        current = list(getattr(namespace, self.dest, None) or [])
        for value in values if isinstance(values, (list, tuple)) else [values]:
            if value not in current:
                current.append(value)
        setattr(namespace, self.dest, current)


class EnvAction(argparse.Action):
    """A valueless flag whose value comes from an environment variable.

    ``--token`` reads ``$MYAPP_TOKEN``. Keeps secrets out of shell history and out
    of ``ps`` output, which is worth a small amount of surprise.

    Two decisions this class makes explicitly:

    * ``nargs=0`` is forced -- the flag takes no value.
    * If the variable is unset the flag is a **no-op** by default
      (``required_env=False``). Pass ``required_env=True`` to make it a usage
      error instead; for a genuinely required secret that is the better default.

    ``type`` is deliberately not applied: values can arrive pre-converted from
    other layers, and re-running a complex converter would be wrong.
    """

    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        **kwargs: object,
    ) -> None:
        self.envvar: str = kwargs.pop("envvar", dest.upper())  # type: ignore[assignment]
        self.required_env: bool = bool(kwargs.pop("required_env", False))
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", None)
        kwargs.setdefault("metavar", self.envvar)
        base_help = kwargs.get("help") or ""
        extra = f"Read from ${self.envvar} when given without a value."
        kwargs["help"] = f"{base_help} {extra}".strip()
        super().__init__(option_strings, dest, **kwargs)  # type: ignore[arg-type]

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        raw = os.environ.get(self.envvar)
        if raw is None:
            if self.required_env:
                parser.error(
                    f"{option_string or self.dest} was given but ${self.envvar} is not set"
                )
            return
        setattr(namespace, self.dest, raw)


def register_all(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.register("action", "csv", CsvAction)
    parser.register("action", "key_value", KeyValueAction)
    parser.register("action", "append_unique", AppendUniqueAction)
    parser.register("action", "env", EnvAction)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = register_all(
        argparse.ArgumentParser(
            prog="composite",
            description="Lesson 24: composite actions.",
            epilog="Try: composite --tag a,b --tag c --set retries=5 --token",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
    )

    parser.add_argument("--tag", action="csv", nargs="*", default=[],
                        metavar="LIST", help="a,b or a b or --tag a --tag b")
    parser.add_argument("--set", action="key_value", nargs="?", default={},
                        metavar="KEY=VALUE", help="override a setting")
    parser.add_argument("--include", action="append_unique", default=[],
                        metavar="PATH", help="repeatable, de-duplicated")
    parser.add_argument("--token", action="env", envvar="COMPOSITE_TOKEN",
                        help="secret; read from the environment")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"tag     : {args.tag!r}")
    print(f"set     : {args.set!r}")
    print(f"include : {args.include!r}")
    shown = "***" if args.token else None
    print(f"token   : {shown!r}  (from ${'COMPOSITE_TOKEN'})")
    return 0


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. CsvAction: three spellings, one list")
    print("=" * 78)
    for argv in (
        ["--tag", "a,b"],
        ["--tag", "a", "b"],
        ["--tag", "a", "--tag", "b,c"],
        ["--tag"],
        [],
    ):
        print(f"  {argv!s:<32} -> tag={parser.parse_args(argv).tag!r}")

    print()
    print("  The isinstance branch matters: with nargs=None, values is a string,")
    print("  and 'abc'.split(',') is fine but iterating 'abc' is not.")

    print()
    print("=" * 78)
    print("2. KeyValueAction: split on the FIRST '=' only")
    print("=" * 78)
    for argv in (
        ["--set", "retries=5"],
        ["--set", "retries=5", "--set", "region=eu"],
        ["--set", "token=a=b=c"],
        ["--set", "broken"],
    ):
        result = _result(parser, argv, "set")
        print(f"  {argv!s:<44} -> {result!r}")

    print()
    print("  'token=a=b=c' is the case that catches people. Passwords contain '='.")

    print()
    print("  --set with nargs=2 also works, because the action branches on whether")
    print("  values is a string or a pair:")
    pair = register_all(argparse.ArgumentParser(prog="composite", add_help=False))
    pair.add_argument("--set", action="key_value", nargs=2, metavar=("KEY", "VALUE"))
    print(f"    --set retries 5 -> {pair.parse_args(['--set', 'retries', '5']).set!r}")

    print()
    print("=" * 78)
    print("3. AppendUniqueAction vs append")
    print("=" * 78)
    dupes = ["--include", "a", "--include", "b", "--include", "a"]
    print(f"  append_unique: {parser.parse_args(dupes).include!r}")
    stock = argparse.ArgumentParser(prog="composite", add_help=False)
    stock.add_argument("--include", action="append", default=[])
    print(f"  append       : {stock.parse_args(dupes).include!r}")
    print()
    print("  'a' appears once. Order is preserved; only exact duplicates are dropped.")

    print()
    print("=" * 78)
    print("4. EnvAction: --token with no value")
    print("=" * 78)
    os.environ["COMPOSITE_TOKEN"] = "s3cret"
    for argv in ([], ["--token"]):
        print(f"  {argv!s:<12} -> token={parser.parse_args(argv).token!r}")
    del os.environ["COMPOSITE_TOKEN"]
    print(f"  --token (unset env) -> token={parser.parse_args(['--token']).token!r}")
    print("  a no-op, because required_env defaults to False")

    strict = register_all(argparse.ArgumentParser(prog="composite", add_help=False))
    strict.add_argument("--token", action="env", envvar="COMPOSITE_TOKEN",
                        required_env=True)
    print(f"  required_env=True, unset -> {_msg(strict, ['--token'])}")
    print()
    print("  Note the help text the action generated:")
    for line in parser.format_help().splitlines():
        if "--token" in line:
            print(f"    {line.rstrip()}")

    print()
    print("=" * 78)
    print("5. All four at once")
    print("=" * 78)
    os.environ["COMPOSITE_TOKEN"] = "s3cret"
    argv = ["--tag", "a,b", "--tag", "c", "--set", "retries=5",
            "--include", "x", "--include", "x", "--token"]
    args = parser.parse_args(argv)
    print(f"  tag     = {args.tag!r}")
    print(f"  set     = {args.set!r}")
    print(f"  include = {args.include!r}")
    print(f"  token   = {'set' if args.token else None!r}")
    del os.environ["COMPOSITE_TOKEN"]

    return 0


def _result(parser: argparse.ArgumentParser, argv: list[str], attr: str) -> object:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return getattr(parser.parse_args(argv), attr)
    except SystemExit:
        return _err(buffer.getvalue())


def _msg(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            parser.parse_args(argv)
        return "(accepted)"
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
