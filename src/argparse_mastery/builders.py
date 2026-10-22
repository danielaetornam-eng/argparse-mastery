"""Declarative parser construction.

The problem this solves
-----------------------
By ~40 arguments, ``build_parser()`` becomes a wall of ``add_argument`` calls
that nobody can review. Three options, in order of how much machinery they add:

1. **Functions.** ``def add_io_arguments(p): ...`` grouped by concern. Zero
   indirection, and the right answer until grouping stops being enough.
2. **Parent parsers.** ``add_io_arguments(parents=[io_parser])``. Solves
   *sharing*; see :mod:`examples.18_parser_parents`.
3. **A spec + a builder.** Describe arguments as data, render them with a
   function. Solves *bulk*, *consistency* and *documentation* at once: the spec
   can be diffed, listed by ``--help``, and turned into JSON for other tooling.

:func:`build_parser` is option 3, implemented with nothing but ``dataclasses``.
It is not a framework to adopt wholesale -- read it for the patterns and copy the
two or three pieces you need.

What it demonstrates
--------------------
* **Dataclass-driven argument definition.** ``Flag(...)``, ``Option(...)``,
  ``Positional(...)`` are just containers that render to ``add_argument`` kwargs.
* **Two-pass help rendering** (``show_only``): build a parser containing only the
  arguments you want to document. Lets you generate per-topic reference docs
  from the same source of truth as the CLI.
* **Subcommand composition** from :class:`Command` objects, which is how you
  build a plugin registry without importing every plugin eagerly.
* **Defaults derived from field metadata**, so a class of arguments (all the
  logging flags, say) is one declaration.

The honest limitation
---------------------
A spec DSL costs indirection. If your parser has fewer than ~30 arguments and no
need for generated docs, plain ``add_argument`` calls are clearer, and you should
keep them. Use this when the *bulk* or the *reuse* justifies it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Command",
    "Flag",
    "Option",
    "ParserSpec",
    "Positional",
    "build_parser",
    "render_spec",
]


# ---------------------------------------------------------------------------
# The spec objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Flag:
    """A boolean option: ``--verbose``, i.e. one that takes no value.

    ``store`` selects the boolean flavour:

    * ``'store'``   -> ``store_true``; ``default=False``
    * ``'store_false'`` -> ``store_false``; ``default=True``
    * ``'count'``   -> ``count``; ``default=0`` (``-vvv``)
    * ``'boolean'`` -> :class:`~argparse.BooleanOptionalAction`, giving
      ``--cache``/``--no-cache`` and a tri-state ``None`` default

    ``deps`` records that this flag is only meaningful with another argument.
    argparse cannot enforce dependencies between options, so the pattern is:
    validate after parsing (see :func:`enforce_dependencies`). Recording the
    relationship in the spec keeps the check next to the definition.
    """

    names: tuple[str, ...]
    help: str = ""
    store: str = "store"
    default: Any = None
    metavar: str | None = None
    group: str | None = None
    required: bool = False
    deps: tuple[str, ...] = ()
    hidden: bool = False

    def to_kwargs(self) -> dict[str, Any]:
        """Render to :meth:`argparse.ArgumentParser.add_argument` keyword args.

        This method is the entire abstraction: everything else in the spec layer
        exists to produce this dict.
        """
        kwargs: dict[str, Any] = {}
        if self.store == "store":
            kwargs["action"] = "store_true"
            kwargs.setdefault("default", self.default if self.default is not None else False)
        elif self.store == "store_false":
            kwargs["action"] = "store_false"
            kwargs.setdefault("default", self.default if self.default is not None else True)
        elif self.store == "count":
            kwargs["action"] = "count"
            kwargs.setdefault("default", self.default or 0)
        elif self.store == "boolean":
            kwargs["action"] = argparse.BooleanOptionalAction
            kwargs.setdefault("default", self.default)
        else:  # pragma: no cover - guarded by Literal in practice
            raise ValueError(f"unknown flag flavour {self.store!r}")

        if self.help:
            kwargs["help"] = self.help
        if self.metavar is not None:
            kwargs["metavar"] = self.metavar
        if self.required:
            kwargs["required"] = True
        if self.hidden:
            kwargs["help"] = argparse.SUPPRESS
        return kwargs


@dataclass(frozen=True, slots=True)
class Option:
    """An option that takes a value: ``--port 8080`` or ``--port=8080``."""

    names: tuple[str, ...]
    type: Callable[[str], Any] | None = None
    default: Any = None
    nargs: int | str | None = None
    choices: Iterable[Any] | None = None
    help: str = ""
    metavar: str | None = None
    group: str | None = None
    required: bool = False
    action: str | type[argparse.Action] | None = None
    const: Any = None
    deps: tuple[str, ...] = ()
    env: str | None = None
    hidden: bool = False

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.type is not None:
            kwargs["type"] = self.type
        if self.default is not None:
            kwargs["default"] = self.default
        elif self.nargs is None:
            # argparse's sentinel is None too, so distinguish "unset" from
            # "explicitly None" by only passing it when nargs implies a default.
            kwargs["default"] = None
        if self.nargs is not None:
            kwargs["nargs"] = self.nargs
        if self.choices is not None:
            kwargs["choices"] = list(self.choices)
        if self.action is not None:
            kwargs["action"] = self.action
        if self.const is not None:
            kwargs["const"] = self.const
        if self.metavar is not None:
            kwargs["metavar"] = self.metavar
        if self.required:
            kwargs["required"] = True
        help_text = self.help
        if self.env:
            # Name the env var in help: the single most forgotten piece of
            # documentation for an option that has two sources.
            help_text = f"{help_text} (env: {self.env})".strip()
        if help_text:
            kwargs["help"] = help_text
        if self.hidden:
            kwargs["help"] = argparse.SUPPRESS
        return kwargs


@dataclass(frozen=True, slots=True)
class Positional:
    """A positional argument.

    ``nargs`` defaults to ``None`` (exactly one token), matching argparse.
    ``default`` only has an effect when ``nargs`` allows zero tokens -- with
    ``nargs=None`` a missing positional is an error, and passing a ``default``
    does not make it optional. That asymmetry trips people up constantly, so it
    is stated here rather than discovered.
    """

    name: str
    nargs: int | str | None = None
    type: Callable[[str], Any] | None = None
    default: Any = None
    choices: Iterable[Any] | None = None
    help: str = ""
    metavar: str | None = None
    group: str | None = None

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.nargs is not None:
            kwargs["nargs"] = self.nargs
        if self.type is not None:
            kwargs["type"] = self.type
        if self.default is not None:
            kwargs["default"] = self.default
        if self.choices is not None:
            kwargs["choices"] = list(self.choices)
        if self.metavar is not None:
            kwargs["metavar"] = self.metavar
        if self.help:
            kwargs["help"] = self.help
        return kwargs


ArgSpec = Flag | Option | Positional


@dataclass(frozen=True, slots=True)
class Command:
    """A subcommand: a name, its own arguments, and the function it dispatches to.

    Keeping ``func`` in the spec (rather than a big ``if`` ladder in ``main``)
    is the single cleanest argparse dispatch pattern::

        args = parser.parse_args()
        if not hasattr(args, "func"):
            parser.print_help()
            return 2
        return args.func(args)

    ``aliases`` are registered on the subparser, so ``prog st`` works.
    ``hidden=True`` keeps a command out of ``--help`` while remaining callable --
    the correct treatment for deprecated commands.
    """

    name: str
    args: tuple[ArgSpec, ...] = ()
    func: Callable[[argparse.Namespace], int] | None = None
    help: str = ""
    description: str | None = None
    epilog: str | None = None
    aliases: tuple[str, ...] = ()
    hidden: bool = False
    parent: argparse.ArgumentParser | None = None

    @property
    def display_name(self) -> str:
        """Name plus aliases, formatted for help output."""
        return f"{self.name} ({', '.join(self.aliases)})" if self.aliases else self.name


@dataclass(frozen=True, slots=True)
class ParserSpec:
    """The complete description of a CLI: global args, groups, and commands."""

    prog: str
    description: str = ""
    epilog: str | None = None
    args: tuple[ArgSpec, ...] = ()
    commands: tuple[Command, ...] = ()
    subcommand_dest: str = "command"
    subcommand_metavar: str | None = None
    subcommand_required: bool = False
    formatter_class: type[argparse.HelpFormatter] = argparse.ArgumentDefaultsHelpFormatter
    version: str | None = None
    parents: tuple[argparse.ArgumentParser, ...] = ()
    prefix_chars: str = "-"
    allow_abbrev: bool = True


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def build_parser(
    spec: ParserSpec,
    *,
    show_only: Sequence[str] | None = None,
) -> argparse.ArgumentParser:
    """Render a :class:`ParserSpec` into a live parser.

    ``show_only`` builds a parser containing **only** the named arguments (by
    ``dest``). Commands are skipped entirely. The purpose is documentation
    generation: emit a reference page per topic from the same spec the CLI runs,
    so the docs cannot drift from the parser::

        # docs/logging.md, generated
        parser = build_parser(APP_SPEC, show_only=["log_level", "log_format"])
        print(parser.format_help())

    Named arguments not in the spec raise :class:`KeyError`, so a renamed dest
    breaks the docs build loudly at generation time rather than producing a page
    with a missing section.
    """
    known_dests = {_dest_of(a) for a in spec.args}
    if show_only is not None:
        missing = [name for name in show_only if name not in known_dests]
        if missing:
            raise KeyError(
                f"show_only names dests the spec does not declare: {missing}"
            )

    parser = argparse.ArgumentParser(
        prog=spec.prog,
        description=spec.description or None,
        epilog=spec.epilog,
        formatter_class=spec.formatter_class,
        prefix_chars=spec.prefix_chars,
        allow_abbrev=spec.allow_abbrev,
        parents=list(spec.parents),
        add_help=True,
    )
    if spec.version:
        parser.add_argument("--version", action="version", version=f"%(prog)s {spec.version}")

    groups: dict[str, argparse._ArgumentGroup] = {}
    for arg in spec.args:
        if show_only is not None and _dest_of(arg) not in show_only:
            continue
        _add_arg(parser, groups, arg)

    if show_only is not None:
        # Documentation mode: no subcommands, and no "required" complaints.
        return parser

    if spec.commands:
        subparsers = parser.add_subparsers(
            dest=spec.subcommand_dest,
            metavar=spec.subcommand_metavar or "<command>",
            required=spec.subcommand_required,
            # `help=` on add_subparsers requires 3.13+; silently skip otherwise so
            # one ParserSpec works across versions.
            **({"help": "available commands"} if _supports_subparser_help() else {}),
        )
        for command in spec.commands:
            _add_command(subparsers, command)

    return parser


def _add_arg(
    parser: argparse.ArgumentParser,
    groups: dict[str, argparse._ArgumentGroup],
    arg: ArgSpec,
) -> None:
    """Add one spec entry to ``parser`` or to its named group."""
    target = parser
    if arg.group:
        target = groups.setdefault(arg.group, parser.add_argument_group(arg.group))

    kwargs = arg.to_kwargs()
    if isinstance(arg, Positional):
        target.add_argument(arg.name, **kwargs)
    else:
        names = list(arg.names)
        # dest is explicit so `prog --log-level` and a config file key both map
        # to `log_level` without relying on argparse's hyphen->underscore rule.
        dest = names[-1].lstrip("-").replace("-", "_")
        target.add_argument(*names, dest=dest, **kwargs)


def _first_line(text: str | None) -> str:
    """First line of ``text``, or an empty string.

    ``help=`` on a subparser is truncated to one line by argparse, so a summary has
    to come from somewhere. ``("" or "").splitlines()[0]`` raises ``IndexError``,
    which is a crash at build time for a Command that supplies neither field.
    """
    if not text:
        return ""
    return text.splitlines()[0] if text.strip() else ""


def _add_command(
    subparsers: argparse._SubParsersAction,
    command: Command,
) -> None:
    """Create a subparser for ``command`` and fill it from its spec."""
    kwargs: dict[str, Any] = {
        # `help` must be a single line; argparse truncates the rest. Derive it
        # defensively so a Command with no help and no description does not blow
        # up on `"".splitlines()[0]`.
        "help": command.help or _first_line(command.description),
        "description": command.description,
        "epilog": command.epilog,
        "aliases": list(command.aliases),
    }
    if command.hidden:
        kwargs["help"] = argparse.SUPPRESS
    sub = subparsers.add_parser(command.name, **kwargs)

    groups: dict[str, argparse._ArgumentGroup] = {}
    for arg in command.args:
        _add_arg(sub, groups, arg)

    if command.func is not None:
        # The dispatch hook. Every command's function takes the namespace and
        # returns an exit code, so `main` has exactly one call site.
        sub.set_defaults(func=command.func)


def _dest_of(arg: ArgSpec) -> str:
    """The namespace key an :class:`ArgSpec` will populate."""
    if isinstance(arg, Positional):
        return arg.name.replace("-", "_")
    return arg.names[-1].lstrip("-").replace("-", "_")


def _supports_subparser_help() -> bool:
    """``add_subparsers(help=...)`` exists from 3.13; skip it below that.

    One ``ParserSpec`` should render on every supported Python, so the keyword is
    dropped rather than raising ``TypeError`` on 3.11/3.12.
    """
    return sys.version_info >= (3, 13)


def render_spec(spec: ParserSpec, *, width: int = 80) -> str:
    """Render a spec as Markdown-ish text, without executing any command.

    Useful for ``myapp --help-docs`` and for reviewing an API surface in a pull
    request. It renders *declarations*, not argparse's own help, so it stays
    accurate even for a spec you have not wired into a CLI yet.
    """
    lines: list[str] = [f"# `{spec.prog}`", ""]
    if spec.description:
        lines += [spec.description, ""]

    if spec.args:
        lines += ["## Options", ""]
        for arg in sorted(spec.args, key=_dest_of):
            lines += _render_arg(arg, width)

    if spec.commands:
        lines += ["## Commands", ""]
        for command in spec.commands:
            title = command.display_name
            lines.append(f"### `{title}`")
            lines.append("")
            if command.help:
                lines += [command.help, ""]
            for arg in command.args:
                lines += _render_arg(arg, width)

    return "\n".join(lines).rstrip() + "\n"


def _render_arg(arg: ArgSpec, width: int) -> list[str]:
    if isinstance(arg, Positional):
        signature = arg.name if arg.nargs is None else f"{arg.name} [{arg.nargs}]"
    elif isinstance(arg, Flag):
        signature = " ".join(arg.names)
    else:
        metavar = arg.metavar or (arg.names[-1].lstrip("-").upper())
        signature = f"{' '.join(arg.names)} {metavar}"
    out = [f"- `{signature}`"]
    if arg.help:
        wrapped = _wrap(arg.help, width - 4)
        out[0] += f" -- {wrapped[0]}"
        out += [f"  {line}" for line in wrapped[1:]]
    elif getattr(arg, "env", None):
        out[0] += f" -- (env: {arg.env})"
    return [*out, ""]


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=max(20, width)) or [text]


# ---------------------------------------------------------------------------
# Cross-argument dependency checks
# ---------------------------------------------------------------------------


def enforce_dependencies(namespace: argparse.Namespace, spec: ParserSpec) -> None:
    """Raise if a flag was used without the argument it depends on.

    ``Flag(deps=("--insecure",))`` documents "only valid with ``--insecure``".
    argparse has no native support for dependencies *between* options -- only
    ``required`` on positionals/options individually and mutually exclusive
    groups. So the check is explicit, and it reports **all** violations at once
    rather than making the user discover them one run at a time.

    Note the deliberate choice to call ``parser.error``-equivalent behaviour
    rather than ``raise SystemExit`` directly: :func:`main` stays the only place
    that decides how to exit.
    """
    problems: list[str] = []
    for arg in (*spec.args, *(a for c in spec.commands for a in c.args)):
        deps = getattr(arg, "deps", ())
        if not deps:
            continue
        present = getattr(namespace, _dest_of(arg), None)
        # A non-default truthy value means "the user supplied it". This is
        # approximate; see `argparse_mastery.actions.explicitly_given` for the
        # honest discussion of that problem.
        if present and not all(getattr(namespace, _dest_of_name(d), None) for d in deps):
            missing = [d for d in deps if not getattr(namespace, _dest_of_name(d), None)]
            problems.append(f"{' '.join(arg.names) if not isinstance(arg, Positional) else arg.name} requires {', '.join(missing)}")

    if problems:
        raise ValueError("; ".join(problems))


def _dest_of_name(name: str) -> str:
    return name.lstrip("-").replace("-", "_")


# ---------------------------------------------------------------------------
# A complete worked example
# ---------------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> int:
    print(f"status: healthy={args.healthy} retries={args.retries}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    if args.yes:
        print("reset: done (no prompt)")
    else:
        print("reset: would reset; pass --yes to confirm")
        return 1
    return 0


def demo_spec() -> ParserSpec:
    """The spec used by the doctest-style examples in this module's docs."""
    return ParserSpec(
        prog="democtl",
        description="Demonstrates declarative parser construction.",
        version="1.0.0",
        args=(
            Option(
                names=("--log-level",),
                choices=("debug", "info", "warning", "error"),
                default="warning",
                env="DEMOCTL_LOG_LEVEL",
                help="logging verbosity",
            ),
            Flag(names=("--quiet", "-q"), help="suppress progress output"),
        ),
        commands=(
            Command(
                name="status",
                help="show service status",
                func=_cmd_status,
                args=(Flag(names=("--healthy",), help="assume healthy"),),
            ),
            Command(
                name="reset",
                help="reset state (destructive)",
                func=_cmd_reset,
                args=(Flag(names=("--yes", "-y"), help="skip the confirmation prompt"),),
            ),
        ),
        subcommand_metavar="<command>",
    )


if __name__ == "__main__":  # pragma: no cover - demo entry point
    built = build_parser(demo_spec())
    print(built.format_help())
    print("-" * 70)
    print(render_spec(demo_spec()))
