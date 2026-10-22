"""``taskrunner explain`` -- prose for the tool's own concepts.

The "explain a concept" subcommand pattern is worth copying: it documents the
decisions that are *not* visible in ``--help`` (precedence, exit codes, dry-run
semantics) at the point of use, which is where users actually look.
"""

from __future__ import annotations

import argparse

from ..exit_codes import ExitCode

__all__ = ["CONCEPTS", "cmd_explain"]

CONCEPTS: dict[str, str] = {
    "dry-run": (
        "--dry-run makes every operation report what it would do without doing\n"
        "it. It is the single most valuable flag in any tool that mutates state,\n"
        "because it converts 'trust me' into 'show me'.\n"
        "\n"
        "Under --dry-run the exit code reflects what *would* have happened, so it\n"
        "is safe to run in CI as a check."
    ),
    "precedence": (
        "Configuration is layered, least specific first:\n"
        "\n"
        "  code defaults  <  config file  <  environment  <  command line\n"
        "\n"
        "Each non-argv layer is installed as an argparse *default*, so the command\n"
        "line wins automatically. There is no merge code, which means there is no\n"
        "merge code to get wrong.\n"
        "\n"
        "Run `taskrunner config show` to see the resolved values and their sources."
    ),
    "exit-codes": (
        "Exit codes are part of this tool's public interface. Scripts branch on\n"
        "them, so they are frozen:\n"
        "\n"
        "   0  success\n"
        "   1  unexpected internal error (a bug; gets a traceback)\n"
        "   2  usage error -- a bad command line\n"
        "   3  bad input data\n"
        "   4  invalid configuration\n"
        "   5  required input missing\n"
        "   6  partial success -- some items failed\n"
        " 130  interrupted\n"
        "\n"
        "Code 2 is argparse's own convention for a malformed command line, and this\n"
        "tool does not reuse it for anything else."
    ),
    "layers": (
        "The layers, most specific last:\n"
        "\n"
        "  command line   typed by argparse, validated by type= at parse time\n"
        "  environment    TASKRUNNER_* variables, coerced through the same type=\n"
        "  config file    --config, validated through the parser's own type=\n"
        "  code defaults  hard-coded in cli.py\n"
        "\n"
        "Values from the environment and the config file are validated explicitly,\n"
        "because argparse applies type= to a default only when that default is a\n"
        "string. A .toml file writing `retries = 0` would otherwise skip the\n"
        "validator entirely."
    ),
}


def cmd_explain(args: argparse.Namespace) -> int:
    """Print the explanation for one concept."""
    text = CONCEPTS[args.concept]
    if getattr(args, "format", "text") == "json":
        import json

        print(json.dumps({"concept": args.concept, "explanation": text}, indent=2))
        return ExitCode.OK
    print(text)
    return ExitCode.OK
