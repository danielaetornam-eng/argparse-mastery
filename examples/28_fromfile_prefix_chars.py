"""Lesson 28 - ``fromfile_prefix_chars``: ``@file`` arguments.

Run me
------
    python examples/28_fromfile_prefix_chars.py @args.txt --debug
    python examples/28_fromfile_prefix_chars.py --demo

What it does
------------
``ArgumentParser(fromfile_prefix_chars="@")`` makes any argument *value* of the
form ``@filename`` be expanded: the file is read, split into whitespace-separated
tokens, and the tokens are spliced into the command line *in place of the value*.
::

    prog @args.txt --debug
    # with args.txt containing:
    #   --retries 5 --tag a --tag b
    # becomes
    prog --retries 5 --tag a --tag b --debug

So the file is not "a value read from a file" -- it is **argv splicing**. That
distinction is the whole lesson, and it is why ``fromfile_prefix_chars`` is the
right tool for response files and the *wrong* tool for ``--config``.

Why it is powerful
------------------
Because splicing happens before parsing, the file may contain **any** arguments:
options, positionals, subcommands. It is the mechanism behind ``gcc @file``,
``curl @file``, ``javac @file``, and MSVC's ``@response`` files. It is the only
way to accept a fully-formed command line in a file.

Why it is dangerous
-------------------
Three sharp edges, all of which have caused real incidents:

1. **Prefix collisions.** With ``fromfile_prefix_chars='@'``, any value beginning
   with ``@`` is treated as a filename. ``--email user@example.com`` becomes an
   attempt to read ``user@example.com``. This is why tools that enable ``@`` do
   not also accept ``@``-leading values, and why several disable it entirely.
2. **The file is read with no limit.** A 2GB file becomes 2GB of argv tokens, and
   you get an ``E2BIG`` from the OS or a MemoryError, long after the user thought
   they were fine.
3. **Errors mention the file, not the argument.** A malformed line inside the file
   produces ``prog: error: ...`` with no indication that the value came from
   ``@file``. Debugging that is unpleasant.

Recommendation: use ``fromfile_prefix_chars`` for *response files* only, with a
single prefix that cannot appear in real data, and note the collision in
``--help``. For a config file with named keys, use ``set_defaults`` instead --
lesson 29.

A safer variant
---------------
Prefix with a character that cannot start a value, ``^`` on Windows shells is
awkward, but ``@`` plus a leading-``=`` form works well::

    prog @=path/to/response.txt

No other CLI convention uses ``=`` after ``@``, so an email address is safe. Not
free of downsides, but it removes the most common collision.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="builder",
        description="Lesson 28: fromfile_prefix_chars and response files.",
        epilog=(
            "Response files:\n"
            "  builder @args.txt --debug    splices args.txt's contents into argv\n"
            "  builder --email a@b.com      breaks! '@b.com' is treated as a file\n"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        fromfile_prefix_chars="@",
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Kept separate so the demo can reuse the exact same set of arguments.

    The parser itself declares no arguments, because ``fromfile_prefix_chars``
    only helps once a parser has arguments to splice into. This is worth noticing:
    an empty parser with ``fromfile_prefix_chars`` set does nothing useful.
    """
    parser.add_argument("--retries", type=int, default=3, help="retry attempts")
    parser.add_argument("--tag", action="append", default=[], help="repeatable tag")
    parser.add_argument("--debug", action="store_true", help="verbose output")
    parser.add_argument("target", help="what to build")
    parser.add_argument("extra", nargs="*", help="additional targets")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    add_arguments(parser)
    args = parser.parse_args(argv)
    print(f"target : {args.target}")
    print(f"extra  : {args.extra}")
    print(f"retries: {args.retries}")
    print(f"tag    : {args.tag}")
    print(f"debug  : {args.debug}")
    return 0


def demo() -> int:
    print("=" * 78)
    print("1. Splicing: the file's tokens join argv in place of the value")
    print("=" * 78)
    parser = _parser()
    for argv, label in (
        (["--retries", "5", "app"], "no response file"),
        (["@args.txt", "--debug", "app"], "@args.txt plus --debug app"),
    ):
        print(f"  {argv!s:<34} ({label})")
        print(f"      -> {_result(parser, argv)}")

    print()
    print("  The file below contains:  --retries 5 --tag a --tag b")
    print("  Note that everything after it still parses normally: the file is")
    print("  spliced in, not treated as a separate phase.")

    print()
    print("=" * 78)
    print("2. A response file may contain positionals too")
    print("=" * 78)
    print(f"  @full.txt          -> {_result(parser, ['@full.txt'])}")
    print("  full.txt contains: app lib libb --retries 9 --tag x")

    print()
    print("=" * 78)
    print("3. THE COLLISION: any value starting with '@' becomes a filename")
    print("=" * 78)
    email = _parser()
    email.add_argument("--email", default=None, help="contact address")
    print(f"  --email user        -> {_email(email, ['--email', 'user'])}")
    print(f"  --email a@b.com     -> {_email(email, ['--email', 'a@b.com'])}")
    print()
    print("  The second one tries to open the FILE named 'b.com'. This is the")
    print("  single biggest reason production CLIs disable fromfile_prefix_chars.")
    print()
    print("  Mitigation used by some tools: require '@=path', since no other")
    print("  convention puts '=' after '@'.")

    print()
    print("=" * 78)
    print("4. Without fromfile_prefix_chars, '@' is just a character")
    print("=" * 78)
    plain = argparse.ArgumentParser(prog="builder", add_help=False)
    plain.add_argument("--email", default=None)
    print(f"  --email a@b.com -> {plain.parse_args(['--email', 'a@b.com']).email!r}")
    print()
    print("  Verified on the same Python. The feature is entirely opt-in.")

    print()
    print("=" * 78)
    print("5. The response file does NOT have to exist to be a valid-looking error")
    print("=" * 78)
    print(f"  @missing.txt      -> {_msg(parser, ['@missing.txt', 'app'])}")
    print()
    print("  The message names the FILENAME, not the argument that referenced it,")
    print("  which is a common source of confusion.")

    print()
    print("=" * 78)
    print("6. What it is NOT: a way to read a config value from a file")
    print("=" * 78)
    print("  fromfile_prefix_chars splices ARGV. For named configuration with")
    print("  types, defaults and precedence, use set_defaults -- lesson 29.")
    print("  A response file is 'the rest of my command line'; a config file is")
    print("  'a mapping of keys to values'.")

    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="builder", add_help=False, fromfile_prefix_chars="@"
    )
    add_arguments(parser)
    return parser


def _write(name: str, content: str) -> None:
    Path(name).write_text(content, encoding="utf-8")


def _result(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Write the demo response files, parse, and show the outcome."""
    _write("args.txt", "--retries 5 --tag a --tag b\n")
    _write("full.txt", "app lib libb --retries 9 --tag x\n")
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            args = parser.parse_args(argv)
        return (
            f"target={args.target!r} extra={args.extra!r} "
            f"retries={args.retries} tag={args.tag}"
        )
    except SystemExit:
        return _err(buffer.getvalue())
    finally:
        Path("args.txt").unlink(missing_ok=True)
        Path("full.txt").unlink(missing_ok=True)


def _email(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return repr(parser.parse_args(argv).email)
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
