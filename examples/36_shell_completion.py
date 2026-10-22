"""Lesson 36 - Shell completion generated from the parser.

Run me
------
    python examples/36_shell_completion.py --bash-completion-script
    python examples/36_shell_completion.py --demo

How completion works
-------------------
argparse knows nothing about your shell. Completion is a small program the shell
runs when the user presses :kbd:`Tab`, and the shell hands it:

* ``cur``   -- the word being typed
* ``prev``  -- the previous word
* whether it is completing the **first** word (a command name)

The script echoes candidate completions, one per line, to stdout. Each shell has
its own conventions, hence three generators.

The highest-value target
------------------------
**Subcommand names.** ``git rem<Tab>`` -> ``remote``. That single case delivers
most of the benefit, and it is easy because argparse gives you the names:

    sub_action.choices          # {'add': parser, 'remove': parser, ...}
    sub_parser.aliases          # ('rm', 'delete')

After that: flag names (``--verb<Tab>`` -> ``--verbose``), then option values --
which argparse cannot help with unless you inspect ``choices`` or the ``type=``
callable yourself. Files are the shell's default fallback, so always fall back to
``compgen -f``.

Install patterns
----------------
bash::

    myapp --bash-completion-script > /etc/bash_completion.d/myapp
    # or, without root:
    myapp --bash-completion-script >> ~/.bashrc

zsh (any directory on ``$fpath``)::

    myapp --zsh-completion-script > ~/.zsh/completions/_myapp

fish::

    myapp --fish-completion-script > ~/.config/fish/completions/myapp.fish

Exposing the flags
------------------
::

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bash-completion-script", action="store_true",
                       help="print a bash completion script to stdout and exit")
    group.add_argument("--zsh-completion-script", action="store_true", ...)
    group.add_argument("--fish-completion-script", action="store_true", ...)

A mutually exclusive group is exactly right: at most one may be given, and argparse
enforces it with a message you do not have to write. Handle them **before**
``parse_args`` if you must (they are consumed by the parser anyway), or right
after -- but keep them off subparsers, or ``myapp cmd --bash-completion-script``
will not work.

Why generate instead of hand-writing
------------------------------------
Hand-written completion rots the moment you rename a flag, and nothing catches
it. Generated completion cannot rot, because it is derived from the same parser
object that does the parsing. The cost is touching ``parser._actions`` -- a
private attribute -- which is why :func:`safe_walk` degrades to an empty list
rather than raising inside the user's shell.

The one thing argparse still cannot do
--------------------------------------
Completion is not the same as validation, and argparse does not know which
values a custom ``type=`` accepts. If you want value completion for
``--port``, expose a machine-readable list (``myapp --list-ports``) and have the
script call it. That is what ``--help`` as structured data is for.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Target:
    """One completable thing discovered from a parser."""

    flags: tuple[str, ...]
    takes_value: bool = False
    metavar: str = ""
    help: str = ""


def safe_walk(parser: argparse.ArgumentParser) -> Iterator[argparse.Action]:
    """Yield every action, recursing into subparsers, without ever raising.

    Touches ``parser._actions``, which is private. A completion script runs inside
    the user's shell at an arbitrary moment; if introspection ever fails, the
    right outcome is "no completions", not a traceback in their terminal.
    """
    actions = getattr(parser, "_actions", None)
    if not actions:
        return
    for action in actions:
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for sub in getattr(action, "choices", {}).values():
                yield from safe_walk(sub)


def subcommands(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """``(name, help)`` for every subcommand **and alias**.

    Aliases cannot be read off the subparser: ``add_parser(aliases=[...])`` pops
    them out of the kwargs and writes them into ``_name_parser_map``, so the
    parser object never gains an ``aliases`` attribute and
    ``getattr(sub, "aliases", ())`` silently returns ``()``. That failure mode is
    invisible -- the code runs, and never offers a single alias.

    The derivation that works is insertion order: ``add_parser`` writes the
    canonical key first and the aliases afterwards, all pointing at the same
    parser object, and ``dict`` preserves order. So for each distinct parser, the
    first key mapping to it is canonical and the rest are aliases.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            rows: list[tuple[str, str]] = []
            seen: dict[int, str] = {}
            for name, sub in action._name_parser_map.items():
                key = id(sub)
                if key in seen:
                    rows.append((name, f"alias for {seen[key]}"))
                else:
                    seen[key] = name
                    description = sub.description or ""
                    rows.append((name, description.splitlines()[0] if description else ""))
            return rows
    return []


def options_of(parser: argparse.ArgumentParser) -> list[Target]:
    """Every visible option of the top-level parser, as completion targets."""
    out: list[Target] = []
    for action in parser._actions:
        if action.help is argparse.SUPPRESS or not action.option_strings:
            continue
        out.append(
            Target(
                flags=tuple(action.option_strings),
                takes_value=action.nargs != 0,
                metavar=action.metavar or (action.dest.upper() if action.nargs != 0 else ""),
                help=(action.help or "").splitlines()[0],
            )
        )
    return out


def bash_script(prog: str, parser: argparse.ArgumentParser) -> str:
    """A ``complete -F`` bash function implementing the three useful cases."""
    commands = " ".join(name for name, _ in subcommands(parser))
    flags = " ".join(f for target in options_of(parser) for f in target.flags)
    func = f"_{prog.replace('-', '_')}_completion"

    return f"""# bash completion for {prog}
# Generated from the parser -- do not edit by hand.
# Install:  {prog} --bash-completion-script > /etc/bash_completion.d/{prog}

{func}() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # Case 1: user typed a bare dash -- offer flags.
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "{flags}" -- "$cur") )
        return 0
    fi

    # Case 2: completing the command name.
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "{commands}" -- "$cur") )
        return 0
    fi

    # Case 3: default to filenames.
    COMPREPLY=( $(compgen -f -- "$cur") )
    return 0
}}
complete -F {func} {prog}
"""


def zsh_script(prog: str, parser: argparse.ArgumentParser) -> str:
    """A zsh ``#compdef`` function using the declarative ``_arguments``."""
    specs: list[str] = []
    for target in options_of(parser):
        message = target.help.replace("'", "").replace(":", " ")
        value = f":{target.metavar}:_files" if target.takes_value else ""
        for flag in target.flags:
            specs.append(f"    '{flag}[{message}]{value}'")
    commands = " ".join(name for name, _ in subcommands(parser))

    return f"""#compdef {prog}
# zsh completion for {prog}
# Install:  {prog} --zsh-completion-script > ~/.zsh/completions/_{prog}

_arguments -s \\
{chr(10).join(specs)}{chr(10) if specs else ''}    '1: :({commands})' \\
    '*::arg:->args'

case $state in
    args) _files && return 0 ;;
esac
"""


def fish_script(prog: str, parser: argparse.ArgumentParser) -> str:
    """fish ``complete`` lines, one per argument."""
    commands = " ".join(name for name, _ in subcommands(parser))
    lines = [
        f"# fish completion for {prog}",
        "# Generated from the parser -- do not edit by hand.",
    ]
    for target in options_of(parser):
        description = target.help.replace("'", "")[:60]
        long = target.flags[-1].lstrip("-")
        if target.takes_value:
            lines.append(
                f"complete -c {prog} -l {long} -r -f -d '{description}'"
            )
        else:
            lines.append(
                f"complete -c {prog} -l {long} -f -d '{description}'"
            )
    if commands:
        lines.append(f"complete -c {prog} -f -a '{commands}'")
        lines.append(f"complete -c {prog} -n '__fish_seen_subcommand_from {commands}' -F")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool",
        description="Lesson 36: shell completion generated from the parser.",
        epilog=(
            "Install:\n"
            "  tool --bash-completion-script >> ~/.bashrc\n"
            "  tool --zsh-completion-script  > ~/.zsh/completions/_tool\n"
            "  tool --fish-completion-script > ~/.config/fish/completions/tool.fish"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # The completion flags, in a mutually exclusive group: at most one may be
    # given, and argparse produces the error message for free.
    scripts = parser.add_mutually_exclusive_group()
    scripts.add_argument("--bash-completion-script", action="store_true",
                          help="print a bash completion script and exit")
    scripts.add_argument("--zsh-completion-script", action="store_true",
                          help="print a zsh completion script and exit")
    scripts.add_argument("--fish-completion-script", action="store_true",
                          help="print a fish completion script and exit")

    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="repeatable verbosity")
    parser.add_argument("--format", choices=("text", "json", "yaml"),
                        default="text", help="output format")
    parser.add_argument("--output", metavar="FILE", default=None,
                        help="write here instead of stdout")
    parser.add_argument("--secret-internal", action="store_true",
                        help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, help_text, aliases in (
        ("status", "show status", ()),
        ("remove", "remove a thing", ("rm", "delete")),
        ("list", "list things", ("ls",)),
    ):
        child = sub.add_parser(name, aliases=list(aliases), help=help_text)
        child.add_argument("--dry-run", action="store_true", help="change nothing")
        child.set_defaults(func=lambda ns: None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Completion is handled before dispatch: the scripts must reach stdout
    # untouched, with no logging, no progress bars, and exit 0.
    if args.bash_completion_script:
        sys.stdout.write(bash_script(parser.prog, parser))
        return 0
    if args.zsh_completion_script:
        sys.stdout.write(zsh_script(parser.prog, parser))
        return 0
    if args.fish_completion_script:
        sys.stdout.write(fish_script(parser.prog, parser))
        return 0

    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    print(f"command={args.command} verbose={args.verbose} format={args.format}")
    return args.func(args)


def demo() -> int:
    parser = build_parser()

    print("=" * 78)
    print("1. Subcommand names and aliases -- the highest-value target")
    print("=" * 78)
    for name, help_text in subcommands(parser):
        print(f"  {name:<12} {help_text}")
    print()
    print("  Aliases come from `_name_parser_map` insertion order -- the parser object")
    print("  itself has no `aliases` attribute. Prefix abbreviation does NOT apply to")
    print("  subcommands (lesson 16), so every spelling must be declared.")

    print()
    print("=" * 78)
    print("2. Options discovered from parser._actions")
    print("=" * 78)
    for target in options_of(parser):
        value = f" <{target.metavar}>" if target.takes_value else ""
        print(f"  {', '.join(target.flags):<24}{value:<18} {target.help[:32]}")
    print()
    print(f"  '--secret-internal' included? "
          f"{any('secret' in f for t in options_of(parser) for f in t.flags)}")
    print("  help=SUPPRESS keeps it out of completions, which is correct: if it is")
    print("  not in --help, users should not be offered it either.")

    print()
    print("=" * 78)
    print("3. The generated bash script")
    print("=" * 78)
    print(bash_script(parser.prog, parser))

    print("=" * 78)
    print("4. The generated zsh script")
    print("=" * 78)
    print(zsh_script(parser.prog, parser))

    print()
    print("=" * 78)
    print("5. The generated fish script")
    print("=" * 78)
    print(fish_script(parser.prog, parser))

    print()
    print("=" * 78)
    print("6. safe_walk degrades instead of raising")
    print("=" * 78)
    print(f"  safe_walk on a real parser : {len(list(safe_walk(parser)))} actions")
    print(f"  safe_walk on a fake object : "
          f"{len(list(safe_walk(object())))} actions  (no _actions attribute)")
    print()
    print("  A completion script runs inside the user's shell at an arbitrary")
    print("  moment. 'No completions' is the right failure; a traceback is not.")

    print()
    print("=" * 78)
    print("7. What argparse still cannot complete")
    print("=" * 78)
    print("  Values. A custom type= has no declared vocabulary, so the script")
    print("  cannot know what --port accepts. Two options:")
    print("    - fall back to filenames (what these scripts do)")
    print("    - expose the vocabulary yourself: `tool --list-ports`")
    print()
    print("  The second is the honest design and is the same idea as '--help as")
    print("  structured data': make the machine-readable surface a real interface.")

    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(main())
