"""``taskrunner config`` -- inspect the resolved configuration.

Backed by :class:`taskrunner.config.ConfigLayer`, which is installed into the
namespace by ``app.main``. Keeping the layer on the namespace (rather than in a
module global) is what makes the handler testable: a test can attach a synthetic
layer and call ``cmd_config`` directly.
"""

from __future__ import annotations

import argparse
import json

from ..config import ConfigLayer
from ..exit_codes import ExitCode

__all__ = ["cmd_config"]


def _layer(args: argparse.Namespace) -> ConfigLayer:
    """Return the resolved layer, or an empty one when running without ``main``.

    ``getattr`` rather than direct access so the handler does not explode when
    called from a test with a hand-built namespace.
    """
    layer = getattr(args, "_config_layer", None)
    return layer if layer is not None else ConfigLayer()


def cmd_config(args: argparse.Namespace) -> int:
    """Print the resolved configuration, with secrets redacted by default."""
    layer = _layer(args)

    if getattr(args, "subcommand", None) != "show":
        # `required=True` on the inner group already prevents this; the guard is
        # for direct calls from tests.
        return ExitCode.USAGE

    if getattr(args, "format", "text") == "json":
        print(json.dumps({"config": layer.values}, indent=2, sort_keys=True, default=str))
        return ExitCode.OK

    if not layer.values:
        # Silence here would read as "nothing is configured", which is a different
        # and more worrying statement than "no config file was found". So the two
        # cases are distinguished: no file at all, versus a file whose keys all
        # missed. The second is a bug in the config, and it must say so.
        if layer.unused:
            print("Config file read, but none of its keys matched an option:")
            for item in sorted(layer.unused):
                print(f"  {item}")
            print("\nEvery option is at its code default.")
            return ExitCode.CONFIG
        print("No configuration layer was loaded.")
        print("  searched: --config, $TASKRUNNER_CONFIG, ./taskrunner.toml")
        print("  every option is currently at its code default")
        return ExitCode.OK

    print(layer.explain(reveal=getattr(args, "reveal", False)))
    if layer.unused:
        print("\nThese keys matched no option and had no effect.")
    return ExitCode.OK
