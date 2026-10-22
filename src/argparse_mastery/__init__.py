"""``argparse_mastery`` -- a toolkit layered over the standard library's argparse.

Nothing here is required to use argparse. Everything here is *made of* argparse,
and exists to be read as worked examples of the parts of the module that the
official docs mention in one sentence.

Start here
----------
* :mod:`argparse_mastery.types` -- custom ``type=`` callables
* :mod:`argparse_mastery.actions` -- custom :class:`argparse.Action` subclasses
* :mod:`argparse_mastery.formatters` -- help output
* :mod:`argparse_mastery.errors` -- errors, suggestions, exit codes
* :mod:`argparse_mastery.config` -- config file + environment layering
* :mod:`argparse_mastery.builders` -- declarative, data-driven parsers
* :mod:`argparse_mastery.completion` -- shell completion from introspection
* :mod:`argparse_mastery.testing` -- capturing stdout/stderr and exit codes
* :mod:`argparse_mastery.cli` -- the dispatch and ``main()`` shape

The version floor
-----------------
Python 3.11. Three capabilities are used conditionally so the code also runs on
3.11-3.13:

* ``tomllib`` for TOML config (3.11+, stdlib)
* ``add_subparsers(help=...)`` (3.13+) is skipped on older versions
* ``suggest_on_error`` and ``color`` (3.14+) are passed only when present
"""

from __future__ import annotations

from .actions import (
    AppendUniqueAction,
    CsvAction,
    DeprecatedAction,
    EnvAction,
    ExistingFileAction,
    KeyValueAction,
    PathAction,
    VerbosityAction,
    register_all,
)
from .builders import Command, Flag, Option, ParserSpec, Positional, build_parser, render_spec
from .cli import make_main, run_command
from .completion import (
    add_completion_arguments,
    bash_completion_script,
    collect_options,
    fish_completion_script,
    known_subcommands,
    zsh_completion_script,
)
from .config import (
    ConfigResolver,
    default_config_path,
    discover_config,
    flatten_mapping,
    load_config_file,
    preparse_flags,
    set_defaults_from_config,
    typed_defaults,
)
from .errors import (
    ExitCode,
    FriendlyArgumentParser,
    ParseFailure,
    UserError,
    catch_errors,
    parse_or_exit,
    parse_or_raise,
)
from .formatters import (
    ColorHelpFormatter,
    ExamplesFormatter,
    SectionedHelpFormatter,
    ShortMetavarHelpFormatter,
    combined_formatter,
    terminal_width,
)
from .logging_setup import add_logging_arguments, level_for, setup_logging
from .suggest import closest_match, did_you_mean, unrecognized_suggestions
from .testing import CliResult, assert_failed, assert_ok, run_cli
from .types import (
    bounded_float,
    bounded_int,
    byte_size,
    csv_list,
    duration,
    enum_by_name,
    existing_dir,
    existing_file,
    hex_color,
    json_object,
    key_value,
    output_path,
    port,
    positive_int,
    regex_string,
    url,
)

__version__ = "1.0.0"

__all__ = [
    # types
    "bounded_float",
    "bounded_int",
    "byte_size",
    "csv_list",
    "duration",
    "enum_by_name",
    "existing_dir",
    "existing_file",
    "hex_color",
    "json_object",
    "key_value",
    "output_path",
    "port",
    "positive_int",
    "regex_string",
    "url",
    # actions
    "AppendUniqueAction",
    "CsvAction",
    "DeprecatedAction",
    "EnvAction",
    "ExistingFileAction",
    "KeyValueAction",
    "PathAction",
    "VerbosityAction",
    "register_all",
    # formatters
    "ColorHelpFormatter",
    "ExamplesFormatter",
    "SectionedHelpFormatter",
    "ShortMetavarHelpFormatter",
    "combined_formatter",
    "terminal_width",
    # errors
    "ExitCode",
    "FriendlyArgumentParser",
    "ParseFailure",
    "UserError",
    "catch_errors",
    "parse_or_exit",
    "parse_or_raise",
    # config
    "ConfigResolver",
    "default_config_path",
    "discover_config",
    "flatten_mapping",
    "load_config_file",
    "preparse_flags",
    "set_defaults_from_config",
    "typed_defaults",
    # builders
    "Command",
    "Flag",
    "Option",
    "ParserSpec",
    "Positional",
    "build_parser",
    "render_spec",
    # completion
    "add_completion_arguments",
    "bash_completion_script",
    "collect_options",
    "fish_completion_script",
    "known_subcommands",
    "zsh_completion_script",
    # logging + dispatch
    "add_logging_arguments",
    "level_for",
    "make_main",
    "run_command",
    "setup_logging",
    # suggestions
    "closest_match",
    "did_you_mean",
    "unrecognized_suggestions",
    # testing
    "CliResult",
    "assert_failed",
    "assert_ok",
    "run_cli",
]
