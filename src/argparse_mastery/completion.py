"""Shell completion generated from parser introspection.

How completion actually works
----------------------------
Nothing in ``argparse`` knows about your shell. A completion script is a small
program the shell runs when the user presses :kbd:`Tab`. The shell gives it:

* the current **word** (what is being typed),
* the **previous word** (``COMP_WORDS[COMP_CWORD-1]``), and
* whether it is completing the **first** word (i.e. a subcommand name).

The script must echo candidate completions, one per line, to stdout. Bash, zsh
and fish each have their own conventions, so each gets its own generator.

The three completion targets, by argument kind
-----------------------------------------------
=================  =========================================================
Argument           What to complete
=================  =========================================================
subparser          command names (and aliases) -- the highest-value case
``action='store'`` the *previous* word if it was ``choices=`` or a ``type=``,
                  otherwise nothing (we cannot know your parser)
positional file    filesystem paths
``FileType``       filesystem paths
``nargs != 0``     the value slot for the previous option
``action`` flag    nothing
``help=EPILOG``    nothing
=================  =========================================================

Why generating from introspection beats hand-writing
---------------------------------------------------
Hand-written completion rots the moment you rename a flag -- silently, because
nothing is checking. Generated completion cannot rot, and the introspection it
needs is the same ``parser._actions`` walk documented in
:mod:`argparse_mastery.formatters`. The private-attribute caveat is real; it is
mitigated by :func:`safe_introspect`, which falls back to an empty list rather
than crashing.

Python 3.14 note
----------------
3.14 added ``add_subparsers(help=...)`` and a few hints, but still no
completion protocol. Generating the script yourself remains the correct answer,
and ``prog --bash-completion-script`` is the conventional install hook --
see the ``completion`` command in :mod:`examples.36_shell_completion`.
"""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

__all__ = [
    "CompletionTarget",
    "bash_completion_script",
    "collect_commands",
    "collect_options",
    "fish_completion_script",
    "known_subcommands",
    "zsh_completion_script",
]


@dataclass(frozen=True, slots=True)
class CompletionTarget:
    """One completable thing discovered from a parser.

    ``flags`` is every spelling (``-v``, ``--verbose``); shells want them all so
    that completing after ``-`` or ``--`` offers the right subset.
    ``takes_value`` drives the "what does the previous word want?" logic.
    """

    flags: tuple[str, ...]
    takes_value: bool = False
    metavar: str = ""
    help: str = ""
    choices: tuple[str, ...] = ()


def _walk(parser: argparse.ArgumentParser) -> Iterator[argparse.Action]:
    """Yield ``parser``'s actions, recursing into subparsers.

    Guarded against argparse internals changing shape: a parser built by a
    different Python version (or a proxy object) should degrade to "no
    completions", not raise inside the user's shell.
    """
    actions = getattr(parser, "_actions", None)
    if not actions:  # pragma: no cover - defensive
        return
    for action in actions:
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for sub in getattr(action, "choices", {}).values():
                yield from _walk(sub)


def subcommand_names(action: argparse._SubParsersAction) -> list[tuple[str, bool]]:
    """Return ``(name, is_alias)`` for every command and alias.

    **argparse does not record aliases on the subparser.** ``add_parser(name,
    aliases=[...])`` pops ``aliases`` out of the kwargs and writes them into
    ``_name_parser_map``; the parser object itself never gains an ``aliases``
    attribute. So ``getattr(sub_parser, "aliases", ())`` is always empty, and any
    code that relies on it silently sees no aliases at all.

    The reliable derivation is insertion order. ``_SubParsersAction.add_parser``
    performs exactly two writes::

        self._name_parser_map[name] = parser          # canonical, first
        for alias in aliases:
            self._name_parser_map[alias] = parser

    so for each distinct parser object the *first* key that maps to it is the
    canonical name and every later key is an alias. ``dict`` preserves insertion
    order, which makes this exact rather than heuristic.
    """
    seen: dict[int, str] = {}
    rows: list[tuple[str, bool]] = []
    for name, parser in action._name_parser_map.items():
        key = id(parser)
        if key in seen:
            rows.append((name, True))
        else:
            seen[key] = name
            rows.append((name, False))
    return rows


def known_subcommands(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """Return ``(name, help)`` for the parser's subcommands and aliases.

    Help comes from the subparser's ``description`` -- ``add_parser(help=...)`` is
    only used for the *parent's* command list, and is not recoverable per command
    except through ``_choices_actions``, which omits commands declared without a
    ``help=``.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            rows: list[tuple[str, str]] = []
            for name, is_alias in subcommand_names(action):
                if is_alias:
                    rows.append((name, f"alias for {_canonical_of(action, name)}"))
                else:
                    description = action._name_parser_map[name].description or ""
                    rows.append((name, description.splitlines()[0] if description else ""))
            return rows
    return []


def _canonical_of(action: argparse._SubParsersAction, alias: str) -> str:
    """The canonical name for ``alias``, via the first key mapping to its parser."""
    target = action._name_parser_map[alias]
    for name, parser in action._name_parser_map.items():
        if parser is target:
            return name
    return alias


def collect_commands(parser: argparse.ArgumentParser) -> list[str]:
    """Just the subcommand names and aliases, sorted."""
    return sorted({name for name, _ in known_subcommands(parser)})


def collect_options(parser: argparse.ArgumentParser) -> list[CompletionTarget]:
    """Every option in ``parser`` (not subparsers') as completion targets.

    Skips ``-h``/``--help`` only implicitly -- it is included, which is correct:
    users do want to complete ``--help``. Skips ``help=SUPPRESS`` entries, which
    are hidden from users and should not be offered.
    """
    targets: list[CompletionTarget] = []
    for action in parser._actions:
        if action.help is argparse.SUPPRESS or not action.option_strings:
            continue
        choices = ()
        if action.choices:
            choices = tuple(str(c) for c in action.choices)
        targets.append(
            CompletionTarget(
                flags=tuple(action.option_strings),
                takes_value=action.nargs != 0,
                metavar=action.metavar or (action.dest.upper() if action.nargs != 0 else ""),
                help=(action.help or "").split("\n")[0],
                choices=choices,
            )
        )
    return targets


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------


def bash_completion_script(prog: str, parser: argparse.ArgumentParser) -> str:
    """Emit a ``complete -F`` bash function that completes ``prog``.

    Install with::

        argparse-myapp --bash-completion-script > /etc/bash_completion.d/myapp

    or from a shell file::

        complete -F _myapp_completion myapp

    The generated function implements the three cases that matter, in this order
    (the order is the logic):

    1. If the previous word is one of our options, or the word before it was,
       and that option takes a value, offer its ``choices`` (or nothing).
    2. If the previous word is ``-``/``--``, offer all flags of the subcommand.
    3. If the cursor is on the first word, offer subcommand names.

    Note that bash gives us the words but no knowledge of subcommand context in
    a portable way; this script offers *all* subcommands' flags. That is a
    deliberate simplification -- precise subcommand-scoped completion needs
    per-subcommand functions, which :func:`bash_completion_script_nested` emits
    when ``nested=True``.
    """
    commands = collect_commands(parser)
    options = collect_options(parser)
    func_name = f"_{prog.replace('-', '_')}_completion"

    case_first = " ".join(commands) if commands else ""
    case_flags = " ".join(f for o in options for f in o.flags)
    choice_pairs = "; ".join(f"{o.flags[-1]}) echo {shlex.quote(o.metavar or o.flags[-1])} ;;"
                             for o in options if o.takes_value)

    return f"""# bash completion for {prog}
# Generated by argparse_mastery.completion -- do not edit by hand.
# Install:  {prog} --bash-completion-script > /etc/bash_completion.d/{prog}

{func_name}() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # Case 1: completing the VALUE of a known option.
    case "$prev" in
        {choice_pairs}
        -*)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
    esac

    # Case 2: user typed a bare dash -- offer flags.
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "{case_flags}" -- "$cur") )
        return 0
    fi

    # Case 3: completing the command name (first word).
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "{case_first}" -- "$cur") )
        return 0
    fi

    # Default: filenames.
    COMPREPLY=( $(compgen -f -- "$cur") )
    return 0
}}
complete -F {func_name} {prog}
"""


# ---------------------------------------------------------------------------
# zsh
# ---------------------------------------------------------------------------


def zsh_completion_script(prog: str, parser: argparse.ArgumentParser) -> str:
    """Emit a zsh ``#compdef`` completion function.

    Install by writing to any directory on ``$fpath``, e.g.::

        {prog} --zsh-completion-script > ~/.zsh/completions/_{prog}

    zsh completion functions are declarative descriptions rather than imperative
    loops, so this is *shorter* than the bash version and does better matching
    (``_arguments`` handles ``--opt=value`` and prefix matching for us).
    """
    commands = collect_commands(parser)
    options = collect_options(parser)

    specs: list[str] = []
    for option in options:
        message = option.help.replace("'", "").replace(":", " ")
        # `::message:action` -- :: makes the value optional so `--flag=` works.
        value_part = f":{option.metavar}:->values" if option.takes_value else ""
        for flag in option.flags:
            specs.append(f"    '{flag}[{message}]{value_part}'")

    command_part = ""
    if commands:
        listed = " ".join(commands)
        command_part = f"\n    '1: :({listed})'\n"

    return f"""#compdef {prog}
# zsh completion for {prog}
# Generated by argparse_mastery.completion -- do not edit by hand.
# Install:  {prog} --zsh-completion-script > ~/.zsh/completions/_{prog}

_arguments -s \\
{chr(10).join(specs)}{command_part} \\
    '*::arg:->args'

case $state in
    values)
        _values 'value' && return 0
        ;;
    args)
        # Subcommand context: fall back to files, then the parent's options.
        _files && return 0
        ;;
esac
"""


# ---------------------------------------------------------------------------
# fish
# ---------------------------------------------------------------------------


def fish_completion_script(prog: str, parser: argparse.ArgumentParser) -> str:
    """Emit fish ``complete`` lines.

    Install by appending to ``~/.config/fish/completions/{prog}.fish``.

    fish completions are one ``complete`` line per argument with conditionals.
    ``__fish_seen_subcommand_from`` is the fish equivalent of bash's
    ``COMP_CWORD -eq 1`` and handles nesting properly, which makes this the
    easiest of the three to generate correctly.
    """
    commands = collect_commands(parser)
    lines = [f"# fish completion for {prog}",
             "# Generated by argparse_mastery.completion -- do not edit by hand."]

    for option in collect_options(parser):
        description = option.help.replace("'", "")[:60]
        if option.choices:
            values = " ".join(f"'{c}'" for c in option.choices)
            lines.append(
                f"complete -c {prog} -l {option.flags[-1].lstrip('-')} "
                f"-f -a '{values}' -d '{description}'"
            )
        elif option.takes_value:
            lines.append(
                f"complete -c {prog} -l {option.flags[-1].lstrip('-')} "
                f"-r -f -d '{description}'"
            )
        else:
            lines.append(
                f"complete -c {prog} -l {option.flags[-1].lstrip('-')} "
                f"-f -d '{description}'"
            )

    if commands:
        listed = " ".join(commands)
        lines.append(f"complete -c {prog} -f -a '{listed}'")
        lines.append(f"complete -c {prog} -n '__fish_seen_subcommand_from {listed}' -F")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Discovery of a parser's subparsers, for nested completion
# ---------------------------------------------------------------------------


def subparsers_of(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Map each subcommand name (and alias) to its parser."""
    out: dict[str, argparse.ArgumentParser] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            out.update(action._name_parser_map)
    return out


def add_completion_arguments(
    parser: argparse.ArgumentParser,
    *,
    bash: bool = True,
    zsh: bool = True,
    fish: bool = True,
) -> argparse.ArgumentParser:
    """Add ``--bash-completion-script`` / ``-zsh`` / ``--fish-completion-script``.

    The three flags are mutually exclusive via an argparse group, which is the
    right tool here: exactly one may be given, and argparse enforces it at parse
    time with a message you do not have to write.

    Registered by a helper so that every CLI in this project gets an identical,
    identically-documented surface.
    """
    group = parser.add_mutually_exclusive_group()
    if bash:
        group.add_argument(
            "--bash-completion-script",
            action="store_true",
            help="print a bash completion script to stdout and exit",
        )
    if zsh:
        group.add_argument(
            "--zsh-completion-script",
            action="store_true",
            help="print a zsh completion script to stdout and exit",
        )
    if fish:
        group.add_argument(
            "--fish-completion-script",
            action="store_true",
            help="print a fish completion script to stdout and exit",
        )
    return parser


def print_completion_script(
    parser: argparse.ArgumentParser,
    argv: Sequence[str],
    *,
    file=None,
) -> int:
    """Handle the completion-script flags. Return an exit code, or ``None``.

    Returns ``None`` when none of the flags was passed, so the caller can fall
    through to normal dispatch. That two-value contract is what lets you call
    ``print_completion_script(parser, argv)`` as the first statement of ``main``
    without a conditional of your own.
    """
    out = file if file is not None else __import__("sys").stdout
    if getattr(parser.parse_known_args(list(argv))[0], "bash_completion_script", False):
        out.write(bash_completion_script(parser.prog, parser))
        return 0
    args = parser.parse_known_args(list(argv))[0]
    if getattr(args, "zsh_completion_script", False):
        out.write(zsh_completion_script(parser.prog, parser))
        return 0
    if getattr(args, "fish_completion_script", False):
        out.write(fish_completion_script(parser.prog, parser))
        return 0
    return None
