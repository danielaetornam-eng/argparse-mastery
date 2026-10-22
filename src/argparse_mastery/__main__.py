"""The reading CLI: ``python -m argparse_mastery`` / ``argparse-mastery``.

This module is itself a worked example. It uses, in production code, everything
the lessons teach:

* ``add_subparsers`` with aliases, ``metavar``, and a shared ``parser_class``
* a **nested** subparser (``explain formatter``)
* ``action='count'`` for ``-v``
* ``action='append'`` with ``nargs='*'``
* a mutually exclusive group
* ``metavar=``, ``help=argparse.SUPPRESS`` for internal flags, and ``dest=`` renaming
* ``type=`` returning a rich object (:class:`Lesson`)
* ``choices`` of ints with custom error text
* ``formatter_class`` combining defaults + raw description
* ``suggest_on_error`` (3.14+) and :class:`FriendlyArgumentParser` suggestions
* the ``set_defaults(func=...)`` dispatch contract
* ``exit_on_error`` overridden to raise, so ``--help`` still exits 0

Commands
--------
=========================  ==================================================
``list``                   index of all lessons, grouped by part
``show N``                 print lesson N's source
``run N``                  run lesson N against its built-in demo argv
``run N -- ARGS``          run lesson N with your own argv
``explain TOPIC``          prose explanation of one argparse feature
``api``                    map of this toolkit's public API
``cheat``                  print the one-page cheat sheet
=========================  ==================================================
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import ExitCode, FriendlyArgumentParser, UserError
from .formatters import ExamplesFormatter, terminal_width

VERSION = "1.0.0"

_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _ROOT / "examples"
_DOCS = _ROOT / "docs"

#: ``(part, title, filename)`` in reading order. Kept explicit rather than
#: globbed so the order of the curriculum is a design decision, not an accident
#: of the filesystem, and so renames are caught here.
_LESSON_INDEX: tuple[tuple[str, str, str], ...] = (
    ("A. The parsing model", "What a parser is and what it returns", "01_hello_world.py"),
    ("A. The parsing model", "Flags: store_true, store_false, BooleanOptionalAction", "02_flags_store_true_false.py"),
    ("A. The parsing model", "type= and default= are two different jobs", "03_types_and_defaults.py"),
    ("A. The parsing model", "Positionals, ordering, and required-ness", "04_positionals.py"),
    ("A. The parsing model", "Short and long options, prefix_chars", "05_short_and_long_options.py"),
    ("A. The parsing model", "metavar, dest, and hiding arguments from help", "06_metavar_and_dest.py"),
    ("A. The parsing model", "Every built-in action, with when to use each", "07_builtin_actions.py"),
    ("B. nargs", "nargs='?' zero-or-one, and the const trap", "08_nargs_optional.py"),
    ("B. nargs", "nargs='*' vs '+', and empty-list defaults", "09_nargs_star_plus.py"),
    ("B. nargs", "REMAINDER, parse_known_args, and the -- separator", "10_nargs_remainder_and_double_dash.py"),
    ("B. nargs", "Exact counts and the (1,2) shape form", "11_nargs_exact_and_shapes.py"),
    ("C. Constraints", "choices, and doing it better", "12_choices.py"),
    ("C. Constraints", "Mutually exclusive groups, and their limits", "13_mutually_exclusive_groups.py"),
    ("C. Constraints", "Dependencies between options: the missing feature", "14_dependent_and_conditional_requirements.py"),
    ("D. Subparsers", "Subcommands and the func dispatch contract", "15_subparsers_basics.py"),
    ("D. Subparsers", "Help output, aliases, and parser_class", "16_subparsers_help_and_aliases.py"),
    ("D. Subparsers", "Nesting subcommands two levels deep", "17_nested_subparsers.py"),
    ("D. Subparsers", "Parent parsers for shared arguments", "18_parser_parents.py"),
    ("D. Subparsers", "Defaults, required=True, and the no-command case", "19_subcommand_defaults.py"),
    ("E. Validation", "Writing custom type= callables", "20_custom_types.py"),
    ("E. Validation", "Bounded numbers and cross-field validation", "21_bounded_and_cross_field.py"),
    ("E. Validation", "argparse.FileType and the file-handle leak", "22_file_types.py"),
    ("F. Custom actions", "The Action protocol: subclassing argparse.Action", "23_custom_action_basics.py"),
    ("F. Custom actions", "Composite actions: CSV, KEY=VALUE, append-unique", "24_composite_actions.py"),
    ("F. Custom actions", "Actions that change their own help output", "25_action_help_and_formatting.py"),
    ("G. Help output", "HelpFormatter subclasses and composition", "26_help_formatters.py"),
    ("G. Help output", "Rewriting the layout from scratch", "27_custom_help_layout.py"),
    ("H. Config & env", "fromfile_prefix_chars and @file arguments", "28_fromfile_prefix_chars.py"),
    ("H. Config & env", "Config file layering and the type-conversion trap", "29_config_file_layering.py"),
    ("H. Config & env", "Environment variables as defaults", "30_environment_variable_defaults.py"),
    ("I. Errors & exits", "exit_on_error, ArgumentError, and soft failures", "31_error_handling.py"),
    ("I. Errors & exits", "Exit codes as an API", "32_exit_codes.py"),
    ("J. Packaging", "prog, usage=, and deterministic help width", "33_prog_usage_and_width.py"),
    ("J. Packaging", "Deprecating flags without breaking users", "34_deprecated_and_hidden_flags.py"),
    ("J. Packaging", "parse_known_args and the two-pass migration", "35_parse_known_args_migration.py"),
    ("J. Packaging", "Shell completion generated from the parser", "36_shell_completion.py"),
    ("J. Packaging", "Capstone: a complete multi-command CLI", "37_capstone_taskrunner.py"),
)

_TOPICS: dict[str, str] = {
    "type": textwrap.dedent("""
        `type=` is a one-argument function that converts a string into a Python
        object. It returns the object, or raises ArgumentTypeError / ValueError /
        TypeError to reject the input. argparse formats the exception into
        "argument --opt: <your message>".

        Key properties:
          * It sees one token at a time, so it can never express rules that
            involve two arguments.
          * It runs at parse time, so downstream code can trust the type.
          * Because `type` is called with a single positional argument, a
            parameterised validator must be a *factory* that returns a closure:
            type=bounded_int(1, 65535).
          * A bare `type=` name is fine: type=int, type=Path. Anything callable
            works, including classes with __call__.
        See examples/20_custom_types.py and argparse_mastery.types.
    """).strip(),
    "nargs": textwrap.dedent("""
        `nargs` controls how many tokens an argument consumes:
          None  (default) exactly one          --port 8080
          '?'   zero or one                    (default used when absent)
          '*'   zero or more                   --include a b c
          '+'   one or more                    --include a b c
          N     exactly N                      --coord 3 4
          N...  N or more                      (a N-tuple in 3.7+)
          argparse.REMAINDER  everything left over, options included
          argparse.PARSER     the rest is a nested parser's argv

        The three traps:
          1. `nargs='?'` with a following positional is ambiguous; argparse
             resolves it with a greedy pattern match that can surprise you.
          2. `default=None` with nargs='*' gives [] at best -- but if you set
             `default="x"` you get the string "x", not ["x"].
          3. Options that look like negative numbers are treated as options, so
             `--offset -1` fails. Use `--offset=-1` or a custom prefix_char.
        See examples/08..11.
    """).strip(),
    "subparsers": textwrap.dedent("""
        `add_subparsers()` attaches a whole nested parser to one token. The
        canonical wiring:

            sub = parser.add_subparsers(dest="command", required=True)
            p = sub.add_parser("status", help="show status")
            p.set_defaults(func=cmd_status)
            p.add_argument("--watch", action="store_true")

        Facts that are not in the docs but matter:
          * `dest` is what records the chosen command name in the namespace.
          * Each subparser gets its OWN help, description, epilog, formatter.
          * Aliases: add_parser("rm", aliases=["remove"]).
          * `required=True` (3.7+) fails at parse time when no command is given.
          * set_defaults(func=...) on the subparser is what enables one
            dispatch site in main().
          * Options placed on the PARENT must come BEFORE the subcommand on the
            command line: `prog --verbose status`, not `prog status --verbose`.
            Repeat the option on the subparser, or use parse_intermixed_args.
        See examples/15..19.
    """).strip(),
    "actions": textwrap.dedent("""
        An `Action` is the object that writes a value into the namespace. The
        whole protocol is one method:

            def __call__(self, parser, namespace, values, option_string=None):
                setattr(namespace, self.dest, values)

        Subclass argparse.Action and override __call__ (and optionally
        __init__ to accept your own keyword arguments, which must be popped
        before super().__init__). Then:

            parser.add_argument("--tag", action=MyAction, nargs="+")
            # or register a name:
            parser.register("action", "my", MyAction)
            parser.add_argument("--tag", action="my")

        Because argparse passes your extra kwargs straight to add_argument,
        keep the custom keyword names distinctive to avoid colliding with
        add_argument's own parameters (default, type, choices, ...).
        See examples/23..25 and argparse_mastery.actions.
    """).strip(),
    "errors": textwrap.dedent("""
        Three layers:

        1. exit_on_error=True (default): argparse prints
           "usage: ..." + "prog: error: ..." to stderr and raises SystemExit(2).
        2. exit_on_error=False: argparse raises ArgumentError instead of
           exiting. Note the narrower scope -- it affects _parse_known_args;
           the final "unrecognized arguments" check in parse_args, and --help,
           still call parser.error and exit.
        3. Override parser.error and parser.exit. This is the only approach
           that catches every path, including -h.

        Python 3.14 added `suggest_on_error=True`, which ONLY improves
        `choices` violations ("invalid choice: 'fasst', maybe you meant 'fast'?")
        and only when every choice is a str. It does not touch "unrecognized
        arguments" -- see argparse_mastery.suggest for that.
        See examples/31, argparse_mastery.errors.
    """).strip(),
    "help": textwrap.dedent("""
        Help text is assembled by a HelpFormatter in this order:
          usage -> description -> arguments -> argument groups -> epilog

        Override _format_usage, _format_text, _format_action,
        _get_help_string, _format_args, or _get_default_metavar_for_* to change
        any part. Pin the width for deterministic tests.

        Always use argparse.ArgumentDefaultsHelpFormatter for anything with
        non-obvious defaults, and add `metavar=` where the dest name would
        confuse a user. Python 3.14 colours help automatically when the stream is
        a TTY; pass color=False to disable.
        See examples/26, 27, 33, argparse_mastery.formatters.
    """).strip(),
    "defaults": textwrap.dedent("""
        `default=` sets the namespace value when the argument is absent. Three
        behaviours that surprise people:

        * argparse applies `type=` to a default ONLY if the default is a str.
          set_defaults(port="8080") with type=int -> 8080 (int).
          set_defaults(port=8080)   -> 8080, bypassing your validator.
          This is why config-file values must be validated explicitly.
        * `default=argparse.SUPPRESS` omits the attribute entirely, so
          getattr(ns, "x", fallback) works and the key never appears in
          vars(namespace). This is the cleanest "was it passed?" test.
        * Mutating a list default mutates the default object, and it leaks
          between parse calls in one process. Pass a fresh list, or copy it in
          your action.
        See examples/03, 29, argparse_mastery.config.
    """).strip(),
    "completion": textwrap.dedent("""
        argparse has no completion protocol. Generate a script from parser
        introspection (parser._actions and _SubParsersAction.choices) and expose
        it as `prog --bash-completion-script`. Subcommand names are the
        highest-value completion; flag names come next; argparse cannot know
        what values a `type=` accepts unless you inspect it yourself.
        See examples/36, argparse_mastery.completion.
    """).strip(),
    "bool": textwrap.dedent("""
        Three ways to spell a boolean:
          store_true / store_false -- two separate flags. The classic bug is
            that False means both "not passed" and "explicitly disabled".
          argparse.BooleanOptionalAction (3.9+) -- one add_argument call gives
            --feature / --no-feature and a tri-state default of None. This is
            the correct modern choice.
          count -- for repetition: -vv. Note count's default must not be None
            (None + 1 raises TypeError).
        See examples/02.
    """).strip(),
    "intermixed": textwrap.dedent("""
        `parse_intermixed_args()` (3.7+) allows positionals and optionals to be
        freely interleaved: `prog --verbose input.txt -n 5` works, which
        parse_args rejects because it tries to match positionals positionally.

        It raises TypeError if any positional uses nargs=REMAINDER or PARSER,
        and it cannot be used with subparsers. Use it for "files plus flags" CLIs
        such as grep/wc.
        See examples/10.
    """).strip(),
}

_CHEATSHEET = """\
argparse cheatsheet
===================

add_argument(flags, dest=, nargs=, const=, default=, type=, choices=,
             required=, help=, metavar=, action=, version=)

ACTIONS (stock)          MEANING
  store (default)        store the value
  store_true            flag, default False
  store_false           flag, default True
  store_const           always `const`
  append                accumulate into a list
  extend                accumulate, flattening nargs='+' results
  count                 count occurrences
  help / version        print and exit 0
  BooleanOptionalAction --flag / --no-flag, tri-state (3.9+)

NARGS
  None      exactly 1        '?'      0 or 1
  '*'       0 or more        '+'      1 or more
  N         exactly N        N...     N or more
  REMAINDER all remaining    PARSER   nested parser argv

PARSER METHODS
  add_argument / add_argument_group / add_mutually_exclusive_group
  add_subparsers(dest=, required=, aliases=)
  set_defaults(...)         install defaults for anything, not just args
  get_default(dest)         read one default back
  parse_args / parse_known_args / parse_intermixed_args
  format_help / format_usage / print_help / print_usage
  error / exit / exit_on_error
  register('action'|'type', name, cls)
  parse_known_intermixed_args

EXAMPLES
  prog --port 8080                 prog --port=8080
  prog -p8080                      prog -p 8080
  prog --                          everything after is positional
  prog -vvv                        count
  prog --opt=-1                    negative numbers need '='
  @file.txt                        only with fromfile_prefix_chars

ENV / CONFIG
  os.environ.get('X') as default   simplest, always composes
  parser.set_defaults(**cfg)      file layer, CLI wins for free
  preparse_flags(argv, ['--config'])   bootstrap a config file

ERRORS
  usage errors      -> stderr, SystemExit(2)
  type/choices      -> ArgumentTypeError / _check_value
  soft failures     -> exit_on_error=False, catch ArgumentError
  custom            -> subclass and override error() and exit()
  3.14+             -> suggest_on_error=True (choices only), color=True

EXIT CODES
  0 ok   2 usage   1 general error   130 Ctrl-C
"""


@dataclass(frozen=True, slots=True)
class Lesson:
    """One lesson file, resolved on disk.

    Returning a rich object from ``type=`` is the pattern: everything downstream
    gets the resolved path and number for free, and the parse still fails early
    with a clean message if the file is missing.
    """

    number: int
    part: str
    title: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.number:02d}. {self.title}"

    @property
    def exists(self) -> bool:
        return self.path.is_file()


@lru_cache(maxsize=1)
def lessons() -> tuple[Lesson, ...]:
    """The lesson catalogue, built once and cached."""
    out: list[Lesson] = []
    for index, (part, title, filename) in enumerate(_LESSON_INDEX, start=1):
        out.append(Lesson(index, part, title, _EXAMPLES / filename))
    return tuple(out)


def lesson_type(raw: str) -> Lesson:
    """``type=`` for lesson references: accepts ``7``, ``07``, or ``lesson_07_*``.

    Demonstrates the ideal ``type=``: accept more spellings than required, accept
    both a bare number and a filename fragment, and produce a *domain object*
    rather than a string the caller has to re-resolve.
    """
    text = raw.strip()
    if text.lower().startswith("lesson_"):
        text = text[len("lesson_") :]
    if text.endswith(".py"):
        text = text[:-3]
    digits = re.match(r"^0*(\d+)", text)
    if digits is None:
        raise argparse.ArgumentTypeError(
            f"{raw!r} is not a lesson number; try 1, 01, or 'lesson_01_*'"
        )
    number = int(digits.group(1))
    catalogue = lessons()
    if not 1 <= number <= len(catalogue):
        raise argparse.ArgumentTypeError(
            f"lesson {number} does not exist; valid range is 1-{len(catalogue)}"
        )
    return catalogue[number - 1]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    """Print the lesson index, grouped by part."""
    width = terminal_width()
    current_part = None
    for lesson in lessons():
        if args.part and lesson.part.split(". ")[0].lower() not in args.part.lower():
            continue
        if lesson.part != current_part:
            current_part = lesson.part
            print(f"\n{current_part}")
            print("-" * len(current_part))
        number = f"{lesson.number:>2}"
        if args.verbose:
            line = f"  {number}  {lesson.title}  [{lesson.path.name}]"
        else:
            line = f"  {number}  {lesson.title}"
        print(textwrap.fill(line, width=width, subsequent_indent=" " * (len(number) + 4)))
    print(f"\n{len(lessons())} lessons.  Read one:  argparse-mastery show 1")
    return ExitCode.OK


def cmd_show(args: argparse.Namespace) -> int:
    """Print a lesson's source to stdout."""
    lesson: Lesson = args.lesson
    if not lesson.exists:
        raise UserError(f"lesson file is missing: {lesson.path}", ExitCode.MISSING)
    if args.pager:
        return _page(lesson.path.read_text(encoding="utf-8"))
    sys.stdout.write(lesson.path.read_text(encoding="utf-8"))
    return ExitCode.OK


def cmd_run(args: argparse.Namespace) -> int:
    """Execute a lesson with its own demo argv, or with argv you supply."""
    lesson: Lesson = args.lesson
    if not lesson.exists:
        raise UserError(f"lesson file is missing: {lesson.path}", ExitCode.MISSING)

    if args.argv:
        argv = list(args.argv)
    else:
        argv = demo_argv(lesson)

    print(f"# {lesson.label}")
    print(f"# $ python examples/{lesson.path.name} {' '.join(argv)}\n")
    completed = subprocess.run(
        [sys.executable, str(lesson.path), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        print(f"# exit status {completed.returncode}", file=sys.stderr)
    return completed.returncode


def cmd_explain(args: argparse.Namespace) -> int:
    """Print the prose explanation for one argparse feature."""
    topic = args.topic.lower()
    if topic not in _TOPICS:
        known = ", ".join(sorted(_TOPICS))
        raise UserError(f"unknown topic {args.topic!r}; known topics: {known}", ExitCode.CONFIG)
    print(f"# argparse: {topic}\n")
    print(_TOPICS[topic])
    if args.verbose:
        print("\nRelated lessons:")
        for lesson in lessons():
            print(f"  {lesson.number:>2}  {lesson.title}")
    return ExitCode.OK


def cmd_api(args: argparse.Namespace) -> int:
    """Print the toolkit's public API, grouped by module."""
    import argparse_mastery

    print(f"argparse_mastery {argparse_mastery.__version__}\n")
    by_module: dict[str, list[str]] = {}
    for name in argparse_mastery.__all__:
        obj = getattr(argparse_mastery, name)
        module = getattr(obj, "__module__", "argparse_mastery").rsplit(".", 1)[-1]
        by_module.setdefault(module, []).append(name)

    for module in sorted(by_module):
        print(f"{module}")
        print("-" * len(module))
        for name in sorted(by_module[module]):
            obj = getattr(argparse_mastery, name)
            doc = (obj.__doc__ or "").strip().splitlines()
            summary = doc[0] if doc else ""
            if summary and args.verbose:
                print(f"  {name}({_signature(obj)})")
                print(f"      {summary}")
            else:
                print(f"  {name}")
        print()
    return ExitCode.OK


def cmd_cheat(args: argparse.Namespace) -> int:
    """Print the one-page cheat sheet."""
    text = _CHEATSHEET
    if args.pager:
        return _page(text)
    print(text)
    return ExitCode.OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Run every lesson and report which ones fail."""
    failures: list[tuple[int, int]] = []
    for lesson in lessons():
        if not lesson.exists:
            failures.append((lesson.number, ExitCode.MISSING))
            print(f"{lesson.number:>2}  MISSING  {lesson.path.name}")
            continue
        completed = subprocess.run(
            [sys.executable, str(lesson.path), *demo_argv(lesson)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        status = "ok" if completed.returncode == 0 else f"exit {completed.returncode}"
        if completed.returncode != 0:
            failures.append((lesson.number, completed.returncode))
        print(f"{lesson.number:>2}  {status:<8} {lesson.path.name}")

    print()
    if failures:
        print(f"{len(failures)} of {len(lessons())} lessons failed: {failures}")
        return ExitCode.PARTIAL
    print(f"all {len(lessons())} lessons ran cleanly")
    return ExitCode.OK


def _signature(obj: object) -> str:
    import inspect

    try:
        return str(inspect.signature(obj))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "(...)"


def _page(text: str) -> int:
    """Pipe through the system pager when one is available."""
    import shutil
    import subprocess

    pager = shutil.which("less") or shutil.which("more")
    if pager is None or not sys.stdout.isatty():
        print(text)
        return ExitCode.OK
    subprocess.run([pager, "-R"], input=text.encode("utf-8"), check=False)
    return ExitCode.OK


#: argv used by ``run``/``verify`` when you supply none. Every entry must be a
#: command line the lesson's own parser accepts, exit 0 -- these double as a smoke
#: test, which is why ``verify`` reports a non-zero status for any of them failing.
_DEMO_ARGV: dict[int, list[str]] = {
    1: ["--name", "Ada"],
    2: ["--verbose"],
    3: ["--port", "8080", "--ratio", "0.5"],
    4: ["src.py"],
    5: ["-v", "--version-info"],
    6: ["--dest", "report.txt"],
    7: ["--tag", "a", "--tag", "b", "-v"],
    8: ["--color"],
    9: ["x.txt", "--include", "a", "--exclude", "b"],
    10: ["run", "-v"],
    11: ["--coord", "3", "4", "0", "0"],
    12: ["--naive", "fast", "--typed", "FAST", "--level", "3"],
    13: ["--json"],
    14: ["--tls", "--url", "http://x"],
    15: ["status"],
    16: ["rm", "db"],
    17: ["remote", "add", "origin", "git@host:repo.git"],
    18: ["remote", "add", "origin", "--verbose"],
    19: ["status"],
    20: ["--port", "8080", "--size", "10MB"],
    21: ["--workers", "4", "--memory", "4"],
    22: ["pyproject.toml"],
    23: ["--tag", "x"],
    24: ["--tag", "a,b"],
    25: ["--format", "json", "--tag", "x"],
    26: ["--help"],
    27: ["--help"],
    28: ["--debug", "app"],
    29: ["--workers", "8"],
    30: ["--log-level", "debug"],
    31: ["--name", "x"],
    32: ["ok1.txt"],
    33: ["--help"],
    34: ["prune"],
    35: ["--new", "1"],
    36: ["--bash-completion-script"],
    37: ["list"],
}


def demo_argv(lesson: Lesson) -> list[str]:
    """The argv used when ``run`` is given no arguments of its own."""
    return _DEMO_ARGV.get(lesson.number, ["--help"])


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> FriendlyArgumentParser:
    """Construct the reading CLI.

    Annotated with the constructs it demonstrates; read top to bottom as a
    worked example of subparsers, groups, metavars, hidden flags and
    ``set_defaults(func=...)`` dispatch.
    """
    parser = FriendlyArgumentParser(
        prog="argparse-mastery",
        description=(
            "Learn argparse by reading runnable examples.\n"
            "Start with:  argparse-mastery list   then   argparse-mastery show 1"
        ),
        epilog=(
            "examples:\n"
            "  argparse-mastery list --part B          lessons in part B only\n"
            "  argparse-mastery show 20                print lesson 20's source\n"
            "  argparse-mastery run 20                 run lesson 20's demo\n"
            "  argparse-mastery run 20 -- --port 9000  run it with your own argv\n"
            "  argparse-mastery explain nargs          prose on one feature\n"
            "  argparse-mastery verify                 run every lesson, report failures\n"
        ),
        formatter_class=ExamplesFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show extra detail (filenames, signatures)",
    )

    commands = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
        parser_class=FriendlyArgumentParser,
        help="available commands",
    )

    # -- list ---------------------------------------------------------------
    list_cmd = commands.add_parser(
        "list",
        aliases=["ls"],
        help="show the lesson index",
        description="Show every lesson, grouped by part.",
        epilog="Tip: pipe to less -R, or use `show` to read one lesson fully.",
    )
    list_cmd.add_argument(
        "--part",
        metavar="LETTER",
        help="filter to one part (A, B, C, ...)",
    )
    list_cmd.set_defaults(func=cmd_list)

    # -- show ---------------------------------------------------------------
    show_cmd = commands.add_parser(
        "show",
        help="print a lesson's source",
        description="Print the full, heavily-commented source of one lesson.",
    )
    show_cmd.add_argument(
        "lesson",
        type=lesson_type,
        metavar="LESSON",
        help="lesson number: 1-37, or a fragment like lesson_20_*",
    )
    show_cmd.add_argument(
        "-p",
        "--pager",
        action="store_true",
        help="pipe through less if available",
    )
    show_cmd.set_defaults(func=cmd_show)

    # -- run ----------------------------------------------------------------
    run_cmd = commands.add_parser(
        "run",
        help="execute a lesson",
        description=(
            "Run a lesson script. Without -- ARGS, each lesson's built-in demo "
            "argv is used."
        ),
        formatter_class=ExamplesFormatter,
    )
    run_cmd.add_argument("lesson", type=lesson_type, metavar="LESSON", help="lesson to run")
    run_cmd.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        metavar="-- ARGS",
        help="arguments for the lesson (everything after --)",
    )
    run_cmd.set_defaults(func=cmd_run)

    # -- explain (nested subparsers) ---------------------------------------
    explain_cmd = commands.add_parser(
        "explain",
        help="prose explanation of one argparse feature",
        description="Deeper explanation of a single argparse feature.",
    )
    explain_topics = explain_cmd.add_subparsers(
        dest="topic",
        metavar="<topic>",
        required=True,
        parser_class=FriendlyArgumentParser,
        help="topics",
    )
    topic_handlers = {
        "type": cmd_explain,
        "nargs": cmd_explain,
        "subparsers": cmd_explain,
        "actions": cmd_explain,
        "errors": cmd_explain,
        "help": cmd_explain,
        "defaults": cmd_explain,
        "completion": cmd_explain,
        "bool": cmd_explain,
        "intermixed": cmd_explain,
    }
    for name, handler in topic_handlers.items():
        topic = explain_topics.add_parser(
            name,
            help=f"how {name} works",
            description=f"argparse {name}",
        )
        topic.add_argument(
            "--verbose", action="store_true", help="list related lessons"
        )
        topic.set_defaults(func=handler)

    # -- api / cheat / verify ------------------------------------------------
    api_cmd = commands.add_parser("api", help="map the argparse_mastery toolkit")
    api_cmd.set_defaults(func=cmd_api)

    cheat_cmd = commands.add_parser(
        "cheat",
        aliases=["cheatsheet"],
        help="print the one-page cheatsheet",
        formatter_class=ExamplesFormatter,
    )
    cheat_cmd.add_argument("-p", "--pager", action="store_true", help="pipe through less")
    cheat_cmd.set_defaults(func=cmd_cheat)

    verify_cmd = commands.add_parser(
        "verify",
        help="run every lesson and report failures",
        description="Executes each lesson with its demo argv. Used by CI.",
    )
    verify_cmd.set_defaults(func=cmd_verify)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``argparse-mastery`` console script.

    Follows the convention this whole repository teaches: ``argv`` is a
    parameter, the return value is an exit code, and ``SystemExit`` is raised by
    the caller (or by argparse for ``--help``).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return ExitCode.USAGE

    try:
        return int(args.func(args))
    except UserError as exc:
        sys.stderr.write(f"{parser.prog}: error: {exc.message}\n")
        return int(exc.exit_code)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return int(ExitCode.INTERRUPTED)
    except BrokenPipeError:  # pragma: no cover - `| head` closes the pipe
        return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
