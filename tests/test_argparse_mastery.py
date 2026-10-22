"""Tests for :mod:`argparse_mastery`.

Organised by module rather than by test type, so a failure points at the file
that broke. The case study in ``case_studies/taskrunner/tests`` covers the
integration-level behaviour; this suite covers the toolkit's pieces.

Run with::

    python -m pytest tests -q
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from argparse_mastery import (
    ConfigResolver,
    ExitCode,
    FriendlyArgumentParser,
    ParseFailure,
    bounded_float,
    bounded_int,
    builders,
    byte_size,
    closest_match,
    completion,
    csv_list,
    duration,
    enum_by_name,
    existing_dir,
    existing_file,
    formatters,
    hex_color,
    json_object,
    key_value,
    output_path,
    parse_or_exit,
    parse_or_raise,
    port,
    positive_int,
    run_cli,
    suggest,
    testing,
    url,
)
from argparse_mastery import actions as A
from argparse_mastery.config import (
    coerce_like,
    discover_config,
    flatten_mapping,
    load_config_file,
    preparse_flags,
    set_defaults_from_config,
    typed_defaults,
)
from argparse_mastery.errors import UserError, catch_errors
from argparse_mastery.formatters import combined_formatter, terminal_width
from argparse_mastery.logging_setup import JsonFormatter, level_for
from argparse_mastery.types import describe_type, parser_type_table

# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


class TestTypes:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", 1), ("8080", 8080), ("65535", 65535)],
    )
    def test_port_accepts(self, raw, expected):
        assert port(raw) == expected

    @pytest.mark.parametrize("raw", ["0", "65536", "-1", "http", ""])
    def test_port_rejects(self, raw):
        with pytest.raises(argparse.ArgumentTypeError):
            port(raw)

    def test_positive_int_message_teaches(self):
        with pytest.raises(argparse.ArgumentTypeError, match="greater than 0"):
            positive_int("0")

    def test_bounded_int_factory_closes_over_bounds(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port", type=bounded_int(1, 65535))
        assert p.parse_args(["--port", "80"]).port == 80
        with pytest.raises(SystemExit):
            p.parse_args(["--port", "70000"])

    def test_bounded_int_names_the_unit(self):
        with pytest.raises(argparse.ArgumentTypeError, match="workers"):
            bounded_int(1, 64, unit="workers")("99")

    def test_bounded_float_rejects_nan_and_inf(self):
        """The comparison trap: NaN passes every naive range check."""
        f = bounded_float(0.0, 100.0)
        assert f("1.5") == 1.5
        for bad in ("nan", "inf", "-inf", "NaN"):
            with pytest.raises(argparse.ArgumentTypeError):
                f(bad)

    def test_byte_size_distinguishes_decimal_and_binary(self):
        assert byte_size("512") == 512
        assert byte_size("10MB") == 10_000_000
        assert byte_size("1MiB") == 1_048_576
        assert byte_size("1.5GiB") == int(1.5 * 1024**3)

    def test_byte_size_rejects_unknown_unit(self):
        with pytest.raises(argparse.ArgumentTypeError, match="not a known size unit"):
            byte_size("10ZB")

    @pytest.mark.parametrize(
        ("raw", "seconds"),
        [("30s", 30), ("5m", 300), ("1h30m", 5400), ("2d", 172800), ("250ms", 0.25)],
    )
    def test_duration(self, raw, seconds):
        assert duration(raw).total_seconds() == pytest.approx(seconds)

    @pytest.mark.parametrize("raw", ["", "abc", "5x", "m"])
    def test_duration_rejects(self, raw):
        with pytest.raises(argparse.ArgumentTypeError):
            duration(raw)

    def test_duration_returns_timedelta(self):
        """The point of a rich type: no conversion arithmetic downstream."""
        assert isinstance(duration("1m"), __import__("datetime").timedelta)

    def test_enum_by_name_accepts_name_and_value(self):
        import enum

        class Mode(enum.Enum):
            FAST = "fast"
            SAFE = "safe"

        t = enum_by_name(Mode)
        assert t("FAST") is Mode.FAST
        assert t("fast") is Mode.FAST
        with pytest.raises(argparse.ArgumentTypeError, match="valid mode"):
            t("quick")

    def test_enum_by_name_supports_aliases(self):
        import enum

        class Level(enum.Enum):
            LOW = 1
            HIGH = 2

        t = enum_by_name(Level, aliases={"warn": "LOW"})
        assert t("warn") is Level.LOW

    def test_csv_list_empty_input_gives_empty_list(self):
        assert csv_list("") == []
        assert csv_list("a,,b") == ["a", "b"]

    def test_key_value_splits_on_first_equals(self):
        assert key_value("token=a=b") == ("token", "a=b")
        with pytest.raises(argparse.ArgumentTypeError, match="KEY=VALUE"):
            key_value("nokey")

    def test_existing_file_and_dir(self, tmp_path: Path):
        target = tmp_path / "f.txt"
        target.write_text("x", encoding="utf-8")
        assert existing_file(str(target)) == target
        with pytest.raises(argparse.ArgumentTypeError, match="no such file"):
            existing_file(str(tmp_path / "missing"))
        with pytest.raises(argparse.ArgumentTypeError, match="is a directory"):
            existing_file(str(tmp_path))
        assert existing_dir(str(tmp_path)) == tmp_path

    def test_output_path_requires_existing_parent(self, tmp_path: Path):
        good = tmp_path / "out.txt"
        assert output_path(str(good)) == good
        with pytest.raises(argparse.ArgumentTypeError, match="parent directory"):
            output_path(str(tmp_path / "nope" / "out.txt"))
        with pytest.raises(argparse.ArgumentTypeError, match="is a directory"):
            output_path(str(tmp_path))

    def test_port_helper_matches_argparse_port(self):
        from argparse_mastery.types import port as types_port

        assert types_port("80") == port("80")

    def test_url_requires_scheme_and_host(self):
        assert url("http://example.com") == "http://example.com"
        with pytest.raises(argparse.ArgumentTypeError, match="schemes"):
            url("ftp://example.com")
        with pytest.raises(argparse.ArgumentTypeError, match="missing a host"):
            url("http://")

    def test_json_object_inline_and_at_file(self, tmp_path: Path):
        assert json_object('{"a": 1}') == {"a": 1}
        path = tmp_path / "x.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert json_object(f"@{path}") == [1, 2]
        with pytest.raises(argparse.ArgumentTypeError, match="not valid JSON"):
            json_object("{oops")

    def test_hex_color(self):
        assert hex_color("#1a2b3c") == (26, 43, 60)
        assert hex_color("fff") == (255, 255, 255)
        with pytest.raises(argparse.ArgumentTypeError, match="hex colour"):
            hex_color("zzzz")

    def test_describe_type_and_table(self):
        assert "file" in describe_type(argparse.FileType("r"))
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port", type=port)
        p.add_argument("--flag", action="store_true")
        table = {dest: (mv, desc) for dest, mv, desc in parser_type_table(p)}
        assert table["port"][1].startswith("port")
        assert table["flag"][1] == "flag"


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


class TestActions:
    def _parser(self):
        return A.register_all(argparse.ArgumentParser(prog="t", add_help=False))

    def test_append_unique_dedupes_and_preserves_order(self):
        p = self._parser()
        p.add_argument("--tag", action="append_unique", default=[])
        args = p.parse_args(["--tag", "a", "--tag", "b", "--tag", "a"])
        assert args.tag == ["a", "b"]

    def test_append_unique_does_not_pollute_the_default(self):
        """The bug this action exists to avoid."""
        p = self._parser()
        p.add_argument("--tag", action="append_unique", default=[])
        assert p.parse_args(["--tag", "x"]).tag == ["x"]
        assert p.parse_args([]).tag == []          # would be ["x"] without the copy

    def test_csv_action_accepts_three_spellings(self):
        p = self._parser()
        p.add_argument("--tag", action="csv", nargs="*", default=[])
        assert p.parse_args(["--tag", "a,b"]).tag == ["a", "b"]
        assert p.parse_args(["--tag", "a", "b"]).tag == ["a", "b"]
        assert p.parse_args(["--tag", "a", "--tag", "b,c"]).tag == ["a", "b", "c"]

    def test_csv_action_handles_bare_string_values(self):
        """With nargs=None, values is a str -- iterating it would be a bug."""
        p = self._parser()
        p.add_argument("--tag", action="csv")
        assert p.parse_args(["--tag", "a,b"]).tag == ["a", "b"]

    def test_key_value_action_both_forms(self):
        p = self._parser()
        p.add_argument("--set", action="key_value", default={})
        assert p.parse_args(["--set", "a=1", "--set", "b=2"]).set == {"a": "1", "b": "2"}
        assert p.parse_args(["--set", "a=1"]).set == {"a": "1"}

    def test_key_value_action_preserves_inner_equals(self):
        p = self._parser()
        p.add_argument("--set", action="key_value", default={})
        assert p.parse_args(["--set", "token=a=b"]).set == {"token": "a=b"}

    def test_key_value_action_rejects_missing_equals(self):
        p = self._parser()
        p.add_argument("--set", action="key_value", default={})
        with pytest.raises(SystemExit):
            p.parse_args(["--set", "broken"])

    def test_key_value_nargs_two(self):
        p = self._parser()
        p.add_argument("--set", action="key_value", nargs=2)
        assert p.parse_args(["--set", "a", "1"]).set == {"a": "1"}

    def test_path_action_expands_and_dedupes(self, tmp_path: Path):
        p = self._parser()
        p.add_argument("--p", action="paths", default=[])
        a = p.parse_args(["--p", str(tmp_path), "--p", str(tmp_path)])
        assert a.p == [tmp_path]

    def test_env_action_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "s3cret")
        p = self._parser()
        p.add_argument("--token", action="env", envvar="MY_TOKEN")
        assert p.parse_args(["--token"]).token == "s3cret"
        assert p.parse_args([]).token is None

    def test_env_action_no_op_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_TOKEN", raising=False)
        p = self._parser()
        p.add_argument("--token", action="env", envvar="MY_TOKEN")
        assert p.parse_args(["--token"]).token is None

    def test_env_action_required_env_errors(self, monkeypatch):
        monkeypatch.delenv("MY_TOKEN", raising=False)
        p = self._parser()
        p.add_argument("--token", action="env", envvar="MY_TOKEN", required_env=True)
        with pytest.raises(SystemExit):
            p.parse_args(["--token"])

    def test_verbosity_action_is_order_independent(self):
        p = self._parser()
        p.add_argument("-v", dest="loglevel", action="verbosity", default="WARNING")
        p.add_argument("-q", dest="loglevel", action="verbosity")
        assert p.parse_args(["-v"]).loglevel == "INFO"
        assert p.parse_args(["-v", "-v"]).loglevel == "DEBUG"
        assert p.parse_args(["-q"]).loglevel == "ERROR"
        assert p.parse_args(["-v", "-q"]).loglevel == p.parse_args(["-q", "-v"]).loglevel

    def test_existing_file_action(self, tmp_path: Path):
        p = self._parser()
        p.add_argument("--f", action="existing_file")
        target = tmp_path / "x"
        target.write_text("x", encoding="utf-8")
        assert p.parse_args(["--f", str(target)]).f == target
        with pytest.raises(SystemExit):
            p.parse_args(["--f", str(tmp_path / "missing")])

    def test_deprecated_action_warns_and_hides_from_usage(self):
        """Verified behaviour: SUPPRESS removes an option from the *usage* line.

        Also verified: ``Action.format_help`` has **no call sites in argparse**, so
        overriding it only affects custom formatters. See lesson 25.
        """
        import warnings

        p = argparse.ArgumentParser(prog="t", add_help=False)
        p.add_argument("--old", action=A.DeprecatedAction, replacement="--new",
                       hidden=True, help=argparse.SUPPRESS)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert p.parse_args(["--old", "v"]).old == "v"
        assert any("deprecated" in str(w.message) for w in caught)
        assert "--old" not in p.format_usage()
        assert "--old" not in p.format_help()

    def test_deprecated_action_hidden_removes_usage(self):
        p = argparse.ArgumentParser(prog="t", add_help=False)
        p.add_argument("--old", action=A.DeprecatedAction, hidden=True,
                       help=argparse.SUPPRESS)
        assert "--old" not in p.format_usage()

    def test_deprecated_action_format_usage_works_for_flags_only(self):
        """``action.format_usage()`` is honoured only when ``nargs == 0``.

        Verified in ``HelpFormatter._format_actions_usage``: the flag branch calls
        ``action.format_usage()``, while the value-taking branch builds the chunk
        from ``option_strings[0]`` and ignores the action entirely.
        """
        class NoUsage(argparse.Action):
            def __call__(self, p, ns, v, o=None):
                setattr(ns, self.dest, v)
            def format_usage(self):
                return ""

        p = argparse.ArgumentParser(prog="t", add_help=False)
        p.add_argument("--flag", action=NoUsage, nargs=0)
        p.add_argument("--value", action=NoUsage)
        assert "--flag" not in p.format_usage()
        assert "--value" in p.format_usage()      # ignored for value-taking options

    def test_explicitly_given_helper(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--x", default=None)
        assert A.explicitly_given(p.parse_args([]), "x") is False
        assert A.explicitly_given(p.parse_args(["--x", "1"]), "x") is True


# ---------------------------------------------------------------------------
# errors and suggestions
# ---------------------------------------------------------------------------


class TestErrors:
    def test_friendly_parser_suggests_a_flag(self):
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--verbose", action="store_true")
        err = _error(p, ["--verbse"])
        assert "did you mean '--verbose'" in err

    def test_friendly_parser_suggests_a_subcommand(self):
        p = FriendlyArgumentParser(prog="demo")
        sub = p.add_subparsers(dest="command")
        sub.add_parser("status")
        sub.add_parser("list")
        err = _error(p, ["staus"])
        # 3.14's own `suggest_on_error` handles this one, since a subcommand is a
        # `choices` entry; the wording is argparse's, not ours.
        assert "maybe you meant 'status'" in err

    def test_error_goes_to_stderr_and_exits_two(self):
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--n", type=int)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), pytest.raises(SystemExit) as e:
            p.parse_args(["--n", "x"])
        assert e.value.code == 2
        assert out.getvalue() == ""
        assert "error:" in err.getvalue()

    def test_help_note_is_appended(self):
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--n", type=int)
        assert "demo --help" in _error(p, ["--n", "x"])

    def test_help_note_can_be_disabled(self):
        p = FriendlyArgumentParser(prog="demo", add_help_note=False)
        p.add_argument("--n", type=int)
        assert "--help for more" not in _error(p, ["--n", "x"])

    def test_exit_code_can_be_overridden(self):
        p = FriendlyArgumentParser(prog="demo", exit_code=64)
        p.add_argument("--n", type=int)
        with pytest.raises(SystemExit) as e:
            p.parse_args(["--n", "x"])
        assert e.value.code == 64

    def test_parse_or_raise_raises_instead_of_exiting(self):
        """Covers the trailing-argument gap that exit_on_error=False misses."""
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--n", type=int)
        with pytest.raises(ParseFailure) as excinfo:
            parse_or_raise(p, ["--n", "x"])
        assert "invalid int value" in excinfo.value.message

        with pytest.raises(ParseFailure, match="unrecognized arguments"):
            parse_or_raise(p, ["--trailing"])

    def test_parse_or_raise_restores_the_parser(self):
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--n", type=int)
        original_error = p.error
        with pytest.raises(ParseFailure):
            parse_or_raise(p, ["--n", "x"])
        assert p.error == original_error

    def test_parse_or_raise_succeeds(self):
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--n", type=int)
        assert parse_or_raise(p, ["--n", "5"]).n == 5

    def test_parse_or_exit_returns_a_namespace(self):
        p = FriendlyArgumentParser(prog="demo")
        p.add_argument("--n", type=int)
        assert parse_or_exit(p, ["--n", "5"]).n == 5

    def test_catch_errors_maps_user_error_to_a_code(self):
        with pytest.raises(SystemExit) as e, catch_errors("demo"):
            raise UserError("nope", ExitCode.CONFIG)
        assert e.value.code == 4

    def test_catch_errors_lets_bugs_through(self):
        def boom():
            with catch_errors("demo"):
                raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            boom()

    def test_exit_codes_are_the_documented_numbers(self):
        assert (ExitCode.OK, ExitCode.USAGE, ExitCode.PARTIAL,
                ExitCode.INTERRUPTED) == (0, 2, 6, 130)


class TestSuggest:
    def test_typo(self):
        assert closest_match("--verbse", ["--verbose", "--version"]) == ["--verbose"]

    def test_prefix_truncation(self):
        """difflib scores this 0.33; the prefix bonus catches it."""
        assert closest_match("--outp", ["--output-file", "--quiet"]) == ["--output-file"]

    def test_adjacent_transposition(self):
        """difflib scores this 0.60; the transposition bonus catches it."""
        assert closest_match("tset", ["test", "lint"]) == ["test"]

    def test_cutoff_suppresses_nonsense(self):
        assert closest_match("--z", ["--verbose"]) == []

    def test_normalize_false_for_values(self):
        assert closest_match("a.b.c", ["a.b.c"], normalize=False) == ["a.b.c"]

    def test_did_you_mean_formats(self):
        assert suggest.did_you_mean("--qiet", ["--quiet"]) == "did you mean '--quiet'?"
        assert suggest.did_you_mean("--zzz", ["--quiet"]) == ""

    def test_unrecognized_suggestions_distinguishes_flags_and_commands(self):
        out = suggest.unrecognized_suggestions(
            ["--verbse", "stat"], ["--verbose"], known_commands=["status"]
        )
        assert "argument '--verbse'" in out
        assert "command 'stat'" in out

    def test_bare_words_get_no_flag_suggestion(self):
        assert suggest.unrecognized_suggestions(["stat"], ["--status"]) == ""


def _error(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Everything a failing parse wrote to stderr, as one string.

    Returns the *whole* stderr, not just the ``error:`` line, because the parts
    that matter here -- the usage line, the suggestion block and the help hint --
    all live on other lines.
    """
    buf = io.StringIO()
    try:
        with redirect_stderr(buf):
            parser.parse_args(argv)
    except SystemExit:
        pass
    return buf.getvalue()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_flatten_mapping(self):
        assert flatten_mapping({"log": {"level": "debug"}}) == {"log_level": "debug"}
        assert flatten_mapping({"a": [1, 2]}) == {"a": [1, 2]}

    def test_load_json_toml_ini_env(self, tmp_path: Path):
        (tmp_path / "a.json").write_text('{"log_level": "debug"}', encoding="utf-8")
        (tmp_path / "a.toml").write_text('[log]\nlevel = "debug"\n', encoding="utf-8")
        (tmp_path / "a.ini").write_text("[log]\nlevel = debug\n", encoding="utf-8")
        (tmp_path / "a.env").write_text("LOG_LEVEL=debug\n", encoding="utf-8")
        for name in ("a.json", "a.toml", "a.ini", "a.env"):
            assert load_config_file(tmp_path / name) == {"log_level": "debug"}, name

    def test_ini_coerces_scalars(self, tmp_path: Path):
        path = tmp_path / "c.ini"
        path.write_text("[s]\na=1\nb=true\nc=1.5\nd=007\n", encoding="utf-8")
        values = load_config_file(path)
        assert values == {"s_a": 1, "s_b": True, "s_c": 1.5, "s_d": "007"}

    def test_unknown_suffix_is_refused(self, tmp_path: Path):
        path = tmp_path / "c.yaml"
        path.write_text("x: 1", encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported config format"):
            load_config_file(path)

    def test_env_file_handles_export_and_quotes(self, tmp_path: Path):
        path = tmp_path / "c.env"
        path.write_text("export A='1'\n# c\nB=\"2\"\n", encoding="utf-8")
        assert load_config_file(path) == {"a": 1, "b": 2}

    def test_typed_defaults_converts_strings(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port", type=port, default=8080)
        assert typed_defaults(p, {"port": "9000"}) == {"port": 9000}

    def test_typed_defaults_catches_ints_via_str_roundtrip(self):
        """The trap: an int default would skip type= in argparse."""
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port", type=port, default=8080)
        with pytest.raises(ValueError, match="port range"):
            typed_defaults(p, {"port": 70000})

    def test_typed_defaults_checks_choices(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--mode", choices=("a", "b"))
        assert typed_defaults(p, {"mode": "a"}) == {"mode": "a"}
        with pytest.raises(ValueError, match="is not one of"):
            typed_defaults(p, {"mode": "z"})

    def test_typed_defaults_drops_unknown_keys(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port")
        assert typed_defaults(p, {"port": 1, "nope": 2}) == {"port": 1}

    def test_set_defaults_from_config_makes_argv_win(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port", type=port, default=8080)
        set_defaults_from_config(p, {"port": "9000"})
        assert p.parse_args([]).port == 9000
        assert p.parse_args(["--port", "1234"]).port == 1234

    def test_preparse_flags_handles_both_spellings(self):
        assert preparse_flags(["--config", "a"], ["--config"]) == {"config": "a"}
        assert preparse_flags(["--config=a"], ["--config"]) == {"config": "a"}
        assert preparse_flags(["-x"], ["--config"]) == {}

    def test_preparse_flags_does_not_eat_help(self):
        assert preparse_flags(["--help"], ["--config"]) == {}

    def test_discover_config_priority(self, tmp_path: Path):
        path = tmp_path / "c.json"
        path.write_text('{"a": 1}', encoding="utf-8")
        data, _used, source = discover_config(["--config", str(path)],
                                             env={}, prog="demo")
        assert data == {"a": 1} and source == "argv:--config"
        data, _used, source = discover_config([], explicit=str(path), env={}, prog="demo")
        assert source == "--config"
        _, _, source = discover_config([], env={"DEMO_CONFIG": str(path)}, prog="demo", env_prefix="DEMO")
        assert source == "$DEMO_CONFIG"

    def test_discover_config_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        data, used, source = discover_config([], env={}, prog="demo-does-not-exist")
        assert data == {} and used is None and source == "none"

    def test_config_resolver_provenance_and_unused(self):
        r = ConfigResolver(known_keys={"a", "b"})
        r.add_defaults({"a": 1, "b": 2})
        r.add_file({"a": 9, "typo": 0}, "cfg.json")
        r.add_env({"MYAPP_A": "50"}, prefix="MYAPP_")
        resolved = r.resolve()
        assert resolved["a"] == 50       # coerced by the env layer
        assert resolved["b"] == 2
        assert resolved.sources["a"] == "env:MYAPP"
        assert "cfg.json:typo" in resolved.unused

    def test_config_resolver_redacts_in_explain(self):
        r = ConfigResolver(known_keys={"api_token"})
        r.add_defaults({"api_token": "s3cret"})
        text = r.resolve().explain()
        assert "s3cret" not in text
        assert "s3cret" in r.resolve().explain(redact=False)

    def test_coerce_like(self):
        assert coerce_like(3, "5") == 5
        assert coerce_like(False, "yes") is True
        assert coerce_like("web", "api") == "api"


# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------


class TestFormatters:
    def test_terminal_width_has_a_floor(self):
        assert terminal_width() >= 50

    def test_combined_formatter_composes(self):
        Combined = combined_formatter(
            argparse.ArgumentDefaultsHelpFormatter,
            argparse.RawDescriptionHelpFormatter,
        )
        p = argparse.ArgumentParser(prog="x", add_help=False, formatter_class=Combined)
        p.add_argument("--n", type=int, default=3, help="a number")
        out = p.format_help()
        assert "(default: 3)" in out
        p2 = argparse.ArgumentParser(
            prog="x", add_help=False, formatter_class=Combined,
            description="line one\nline two",
        )
        assert "line one\nline two" in p2.description

    def test_sectioned_formatter_inserts_rules(self):
        p = argparse.ArgumentParser(prog="x", add_help=False,
                                    formatter_class=formatters.SectionedHelpFormatter)
        p.add_argument("--a")
        grp = p.add_argument_group("extra")
        grp.add_argument("--b")
        assert "---" in p.format_help()

    def test_examples_formatter_marks_the_epilog(self):
        p = argparse.ArgumentParser(prog="x", add_help=False,
                                    formatter_class=formatters.ExamplesFormatter,
                                    epilog="Examples:\n  x --a 1")
        assert "Examples:" in p.format_help()

    def test_short_metavar_formatter(self):
        p = argparse.ArgumentParser(prog="x", add_help=False,
                                    formatter_class=formatters.ShortMetavarHelpFormatter)
        p.add_argument("--port", type=int)
        assert "int" in p.format_usage()

    def test_two_column_formatter_rewrites_rows(self):
        """`_format_action` override only; it keeps argparse's section layout."""
        p = argparse.ArgumentParser(prog="x", add_help=False,
                                    formatter_class=formatters.TwoColumnFormatter)
        p.add_argument("--a", action="store_true", help="flag a")
        p.add_argument("--pair", nargs=2, help="a pair")
        out = p.format_help()
        assert "--a" in out and "flag a" in out
        assert "--pair PAIR PAIR" in out   # nargs=2 rendered by our own mapping

    def test_iter_visible_actions_skips_suppressed(self):
        p = argparse.ArgumentParser(prog="x")
        p.add_argument("--hidden", action="store_true", help=argparse.SUPPRESS)
        p.add_argument("--shown")
        flags = {f for a in formatters.iter_visible_actions(p) for f in a.option_strings}
        assert "--shown" in flags and "--hidden" not in flags

    def test_known_option_strings_includes_subcommands(self):
        p = argparse.ArgumentParser(prog="x")
        p.add_argument("--global")
        sub = p.add_subparsers(dest="command")
        sub.add_parser("go").add_argument("--local")
        flags = formatters.known_option_strings(p)
        assert "--global" in flags and "--local" in flags

    def test_color_help_formatter_accepts_the_flag_on_all_versions(self):
        f = formatters.ColorHelpFormatter(prog="x", color=False)
        assert f is not None


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


class TestCompletion:
    def _parser(self):
        p = argparse.ArgumentParser(prog="tool")
        p.add_argument("--verbose", action="store_true", help="chatty")
        p.add_argument("--secret", action="store_true", help=argparse.SUPPRESS)
        sub = p.add_subparsers(dest="command")
        child = sub.add_parser("remove", aliases=["rm"], help="remove a thing")
        child.add_argument("--dry-run", action="store_true", help="change nothing")
        return p

    def test_known_subcommands_includes_aliases(self):
        names = completion.known_subcommands(self._parser())
        assert ("rm", "alias for remove") in names

    def test_collect_options_skips_suppressed(self):
        flags = {f for t in completion.collect_options(self._parser())
                 for f in t.flags}
        assert "--verbose" in flags and "--secret" not in flags

    def test_bash_script_shape(self):
        script = completion.bash_completion_script("tool", self._parser())
        assert "complete -F _tool_completion tool" in script
        assert "--verbose" in script
        assert "remove" in script and "rm" in script
        assert "--secret" not in script

    def test_zsh_script_shape(self):
        script = completion.zsh_completion_script("tool", self._parser())
        assert script.startswith("#compdef tool")
        assert "_arguments" in script

    def test_fish_script_shape(self):
        script = completion.fish_completion_script("tool", self._parser())
        assert "complete -c tool" in script
        assert "__fish_seen_subcommand_from" in script

    def test_add_completion_arguments_is_mutually_exclusive(self):
        p = argparse.ArgumentParser(prog="tool", add_help=False)
        completion.add_completion_arguments(p)
        assert p.parse_args(["--bash-completion-script"]).bash_completion_script
        with pytest.raises(SystemExit):
            p.parse_args(["--bash-completion-script", "--zsh-completion-script"])

    def test_subparsers_of_maps_aliases(self):
        mapping = completion.subparsers_of(self._parser())
        assert set(mapping) == {"remove", "rm"}

    def test_shell_quoting_in_generated_scripts(self):
        p = argparse.ArgumentParser(prog="tool")
        p.add_argument("--desc", help="it's a test")
        assert "it" in completion.bash_completion_script("tool", p)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


class TestBuilders:
    def _spec(self):
        return builders.ParserSpec(
            prog="democtl",
            description="demo",
            version="1.0.0",
            args=(
                builders.Option(names=("--log-level",),
                                choices=("debug", "info"), default="info",
                                env="DEMO_LOG_LEVEL", help="verbosity"),
                builders.Flag(names=("--quiet", "-q"), help="less output"),
            ),
            commands=(
                builders.Command(name="status", help="show status",
                                 args=(builders.Flag(names=("--healthy",)),),
                                 func=lambda ns: 0),
                builders.Command(name="remove", aliases=("rm",), help="remove",
                                 func=lambda ns: 0),
            ),
        )

    def test_build_parser_produces_a_working_parser(self):
        p = builders.build_parser(self._spec())
        assert p.prog == "democtl"
        assert p.parse_args(["status", "--healthy"]).healthy is True
        assert p.parse_args(["rm"]).command == "rm"

    def test_version_action(self):
        p = builders.build_parser(self._spec())
        buf = io.StringIO()
        with redirect_stdout(buf), pytest.raises(SystemExit):
            p.parse_args(["--version"])
        assert "democtl 1.0.0" in buf.getvalue()

    def test_env_var_appears_in_help(self):
        assert "(env: DEMO_LOG_LEVEL)" in builders.build_parser(self._spec()).format_help()

    def test_show_only_builds_a_documentation_parser(self):
        p = builders.build_parser(self._spec(), show_only=["log_level"])
        out = p.format_help()
        assert "--log-level" in out
        assert "--quiet" not in out

    def test_show_only_rejects_unknown_dest(self):
        with pytest.raises(KeyError):
            builders.build_parser(self._spec(), show_only=["nope"])

    def test_render_spec_is_markdown(self):
        text = builders.render_spec(self._spec())
        assert text.startswith("# `democtl`")
        assert "--log-level" in text
        assert "remove (rm)" in text

    def test_flag_flavours(self):
        for store, expected in (("store", True), ("store_false", False)):
            spec = builders.Flag(names=("--f",), store=store)
            p = argparse.ArgumentParser(add_help=False)
            p.add_argument(*spec.names, **spec.to_kwargs())
            assert p.parse_args(["--f"]).f is expected
        spec = builders.Flag(names=("-v", "--verbose"), store="count")
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument(*spec.names, **spec.to_kwargs())
        assert p.parse_args(["-v", "-v"]).verbose == 2
        spec = builders.Flag(names=("--b",), store="boolean")
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument(*spec.names, **spec.to_kwargs())
        assert p.parse_args(["--no-b"]).b is False

    def test_option_nargs_and_choices(self):
        spec = builders.Option(names=("--pair",), nargs=2)
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument(*spec.names, **spec.to_kwargs())
        assert p.parse_args(["--pair", "a", "b"]).pair == ["a", "b"]

    def test_dispatch_via_func_default(self):
        called: list[int] = []
        spec = builders.ParserSpec(
            prog="x",
            commands=(builders.Command(name="go", func=lambda ns: called.append(1) or 0),),
        )
        args = builders.build_parser(spec).parse_args(["go"])
        assert args.func(args) == 0
        assert called == [1]

    def test_enforce_dependencies(self):
        spec = builders.ParserSpec(
            prog="x",
            args=(builders.Flag(names=("--tls",), deps=("--url",)),
                  builders.Option(names=("--url",))),
        )
        ns = argparse.Namespace(tls=True, url=None)
        with pytest.raises(ValueError, match="requires --url"):
            builders.enforce_dependencies(ns, spec)
        ns = argparse.Namespace(tls=True, url="http://x")
        builders.enforce_dependencies(ns, spec)

    def test_positional_to_kwargs(self):
        spec = builders.Positional(name="files", nargs="+")
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument(spec.name, **spec.to_kwargs())
        assert p.parse_args(["a", "b"]).files == ["a", "b"]


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


class TestLogging:
    def test_level_for_is_order_independent(self):
        assert level_for() == 30
        assert level_for(verbose=1) == 20
        assert level_for(verbose=2) == 10
        assert level_for(quiet=1) == 40
        assert level_for(verbose=2, quiet=1) == level_for(quiet=1, verbose=2)

    def test_level_for_clamps(self):
        assert level_for(verbose=99) == 10
        assert level_for(quiet=99) == 50

    def test_json_formatter_emits_ndjson_with_extras(self):
        import logging

        record = logging.LogRecord("t", logging.INFO, __file__, 1,
                                    "hello %s", ("world",), None)
        record.path = "/tmp/x"
        out = json.loads(JsonFormatter(program="p").format(record))
        assert out["level"] == "INFO"
        assert out["msg"] == "hello world"
        assert out["program"] == "p"
        assert out["path"] == "/tmp/x"
        assert out["ts"].endswith("Z")

    def test_json_formatter_includes_exceptions(self):
        import logging

        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "oops", (), exc_info)
        out = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in out["exception"]

    def test_add_logging_arguments(self):
        p = argparse.ArgumentParser(prog="x", add_help=False)
        from argparse_mastery.logging_setup import add_logging_arguments

        add_logging_arguments(p)
        assert p.parse_args(["-vv"]).log_level == "DEBUG"
        assert p.parse_args(["-q"]).log_level == "ERROR"
        assert p.parse_args([]).log_level == "WARNING"

    def test_setup_logging_is_idempotent(self):
        import logging

        from argparse_mastery.logging_setup import add_logging_arguments, setup_logging

        p = argparse.ArgumentParser(prog="x", add_help=False)
        add_logging_arguments(p)
        args = p.parse_args(["-vv"])
        setup_logging(args, program="x")
        first = len(logging.getLogger().handlers)
        setup_logging(args, program="x")
        assert len(logging.getLogger().handlers) == first

    def test_is_tty_and_default_format(self):
        from argparse_mastery.logging_setup import default_output_format, is_tty

        assert isinstance(is_tty(), bool)
        assert default_output_format() in ("text", "json")


# ---------------------------------------------------------------------------
# testing helpers
# ---------------------------------------------------------------------------


class TestTestingHelpers:
    def _main(self, *argv):
        parser = argparse.ArgumentParser(prog="x")

        @parser.command_shim if hasattr(parser, "command_shim") else (lambda f: f)
        def _noop():
            pass

        def main(argv_in=None):
            ns = parser.parse_args(argv_in)
            print(f"name={ns.name}")
            return 0

        parser.add_argument("--name", default="x")
        return main

    def test_run_cli_captures_both_streams(self):
        def main(argv=None):
            parser = argparse.ArgumentParser(prog="x")
            parser.add_argument("--name", default="x")
            ns = parser.parse_args(argv)
            print(f"out:{ns.name}")
            print("err:y", file=sys.stderr)
            return 0

        result = run_cli(main, ["--name", "Ada"])
        assert result.ok and result.exit_code == 0
        assert result.stdout == "out:Ada\n"
        assert "err:y" in result.stderr
        assert "Ada" in result.output
        assert result.lines() == ["out:Ada"]

    def test_run_cli_captures_system_exit(self):
        def main(argv=None):
            argparse.ArgumentParser(prog="x").parse_args(argv)
            return 0

        assert run_cli(main, ["--help"]).exit_code == 0

    def test_run_cli_records_usage_error(self):
        def main(argv=None):
            argparse.ArgumentParser(prog="x").parse_args(argv)
            return 0

        result = run_cli(main, ["--nope"])
        assert result.exit_code == 2
        assert "unrecognized" in result.stderr

    def test_run_cli_lets_bugs_propagate(self):
        def main(argv=None):
            raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            run_cli(main, [])

    def test_run_cli_records_namespace(self):
        sink: list[argparse.Namespace] = []
        parser = argparse.ArgumentParser(prog="x")
        parser.add_argument("--verbose", action="store_true")

        def main(argv=None):
            sink.append(parser.parse_args(argv))
            return 0

        result = run_cli(main, ["--verbose"], namespace_sink=sink)
        assert result.namespace is sink[0]
        assert sink[0].verbose is True

    def test_assert_helpers(self):
        def main(argv=None):
            parser = argparse.ArgumentParser(prog="x")
            parser.add_argument("--name", default="x")
            parser.parse_args(argv)
            print("hello world")
            return 0

        testing.assert_ok(run_cli(main, []), contains=["hello", "world"])
        with pytest.raises(AssertionError):
            testing.assert_ok(run_cli(main, ["--nope"]))

        failing = lambda argv=None: 2       # noqa: E731 - a deliberate one-liner
        testing.assert_failed(run_cli(failing, []), exit_code=2)

    def test_split_stderr(self):
        usage, message = testing.split_stderr(
            "usage: x [-h]\nx: error: argument --n: invalid int value: 'a'"
        )
        assert usage.startswith("usage:")
        assert message == "argument --n: invalid int value: 'a'"

    def test_help_snapshot_is_deterministic(self):
        p = argparse.ArgumentParser(prog="x",
                                    formatter_class=argparse.RawDescriptionHelpFormatter,
                                    description="line one\nline two")
        p.add_argument("--a", help="first")
        assert testing.help_snapshot(p) == testing.help_snapshot(p)
        # Pinned width plus RawDescription = byte-identical output every run.
        assert "line one\nline two" in testing.help_snapshot(p)

    def test_help_snapshot_pins_width(self):
        p = argparse.ArgumentParser(prog="x")
        p.add_argument("--a", help="a fairly long help string that will wrap differently "
                                   "depending on how wide the terminal happens to be")
        narrow = testing.help_snapshot(p, width=60)
        wide = testing.help_snapshot(p, width=100)
        assert narrow != wide
        assert max(len(line) for line in narrow.splitlines()) <= 60
        assert narrow.count("\n") > wide.count("\n")

    def test_parse_only(self):
        p = argparse.ArgumentParser(prog="x")
        p.add_argument("--n", type=int, default=1)
        assert testing.parse_only(p, []) .n == 1
        # `--zz` is unrecognised and `1` has no positional to land in, so both\n        # are extras. That ambiguity is exactly why `parse_known_args` needs the\n        # strictness check described in lesson 35.\n        assert testing.parse_only(p, ["--allow-unknown", "--zz", "1"])._extras == ["--zz", "1"]


# ---------------------------------------------------------------------------
# the meta CLI
# ---------------------------------------------------------------------------


class TestMetaCli:
    def test_lesson_catalogue_is_complete(self):
        from argparse_mastery import __main__ as meta

        catalogue = meta.lessons()
        assert len(catalogue) == 37
        missing = [lesson.path.name for lesson in catalogue if not lesson.exists]
        assert missing == [], f"missing lesson files: {missing}"

    def test_lessons_are_numbered_in_order(self):
        from argparse_mastery import __main__ as meta

        assert [l.number for l in meta.lessons()] == list(range(1, 38))

    def test_lesson_type_accepts_several_spellings(self):
        from argparse_mastery import __main__ as meta

        for spelling in ("1", "01", "lesson_01_hello_world.py", "lesson_01_*.py"):
            assert meta.lesson_type(spelling).number == 1
        with pytest.raises(argparse.ArgumentTypeError):
            meta.lesson_type("99")
        with pytest.raises(argparse.ArgumentTypeError):
            meta.lesson_type("nope")

    def test_every_lesson_has_a_demo_argv(self):
        from argparse_mastery import __main__ as meta

        for lesson in meta.lessons():
            assert meta.demo_argv(lesson), lesson.number

    def test_help_and_index(self):
        from argparse_mastery.__main__ import main

        assert run_cli(main, ["--help"]).exit_code == 0
        assert run_cli(main, ["list"]).ok
        assert run_cli(main, ["ls", "--part", "B"]).ok
        assert run_cli(main, ["--help"]).stdout.count("list") >= 1

    def test_explain_rejects_an_unknown_topic(self):
        from argparse_mastery.__main__ import main

        assert run_cli(main, ["explain", "nargs"]).ok
        # `choices=` rejects it before the handler runs, so this is a usage error.
        result = run_cli(main, ["explain", "nonsense"])
        assert result.exit_code == 2
        assert "invalid choice" in result.stderr

    def test_typo_in_a_command_is_suggested(self):
        from argparse_mastery.__main__ import main

        result = run_cli(main, ["cheet"])
        assert "maybe you meant 'cheat'" in result.stderr

    def test_missing_command_is_a_usage_error(self):
        from argparse_mastery.__main__ import main

        result = run_cli(main, [])
        assert result.exit_code == 2

    def test_cheat_and_api(self):
        from argparse_mastery.__main__ import main

        assert "argparse cheatsheet" in run_cli(main, ["cheat"]).stdout
        api = run_cli(main, ["-v", "api"])
        assert api.ok
        assert "bounded_int" in api.stdout

    def test_version_flag(self):
        from argparse_mastery.__main__ import main

        result = run_cli(main, ["--version"])
        assert result.exit_code == 0
        assert "argparse-mastery" in result.stdout


# ---------------------------------------------------------------------------
# cross-cutting: the claims in the docs
# ---------------------------------------------------------------------------


class TestDocumentedClaims:
    """Assert the behaviours the documentation states, so the docs cannot rot."""

    def test_nargs_tuple_is_rejected(self):
        with pytest.raises(ValueError, match="invalid nargs value"):
            argparse.ArgumentParser(add_help=False).add_argument("--x", nargs=(2, 3))

    def test_nargs_zero_rejected_on_store(self):
        with pytest.raises(ValueError, match="nargs for store actions"):
            argparse.ArgumentParser(add_help=False).add_argument("--x", nargs=0)

    def test_required_rejected_on_positional(self):
        with pytest.raises(TypeError, match="invalid argument for positionals"):
            argparse.ArgumentParser(add_help=False).add_argument("x", nargs="?",
                                                                  required=True)

    def test_count_with_none_default_is_handled_in_314(self):
        """Correction of a stale myth: `_CountAction.__call__` guards the None."""
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("-v", action="count", default=None)
        # 3.14 does `count = getattr(...) or 0`, so this is safe. It was not always.
        assert p.parse_args(["-v"]).v == 1
        assert p.parse_args([]).v is None

    def test_custom_count_like_action_still_needs_a_default(self):
        """The hazard is real for *hand-rolled* counters, just not for `count`."""
        class Counter(argparse.Action):
            def __init__(self, option_strings, dest, **kw):
                kw.setdefault("nargs", 0)
                super().__init__(option_strings, dest, **kw)

            def __call__(self, parser, namespace, values, option_string=None):
                setattr(namespace, self.dest, getattr(namespace, self.dest) + 1)

        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("-c", action=Counter, default=None)
        with pytest.raises(TypeError):
            p.parse_args(["-c"])

    def test_subcommand_names_are_not_abbreviated(self):
        p = argparse.ArgumentParser(prog="x")
        sub = p.add_subparsers(dest="command")
        sub.add_parser("remove")
        with pytest.raises(SystemExit):
            p.parse_args(["rem"])

    def test_subparsers_get_help_by_default(self):
        """Correction of a widely repeated myth."""
        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_subparsers(dest="command").add_parser("go")
        buf = io.StringIO()
        with redirect_stdout(buf), pytest.raises(SystemExit) as e:
            p.parse_args(["go", "--help"])
        assert e.value.code == 0
        assert "-h, --help" in buf.getvalue()

    def test_double_dash_works_for_positionals_not_options(self):
        pos = argparse.ArgumentParser(prog="x", add_help=False)
        pos.add_argument("items", nargs="*")
        assert pos.parse_args(["--", "-x"]).items == ["-x"]

        opt = argparse.ArgumentParser(prog="x", add_help=False)
        opt.add_argument("--items", nargs="*")
        with pytest.raises(SystemExit):
            opt.parse_args(["--items", "--", "-x"])

    def test_suppress_removes_the_attribute(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--x", default=argparse.SUPPRESS)
        assert "x" not in vars(p.parse_args([]))
        assert vars(p.parse_args(["--x", "1"]))["x"] == "1"

    def test_string_defaults_are_converted_int_defaults_are_not(self):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--port", type=port, default=1)
        p.set_defaults(port="9000")
        assert p.parse_args([]).port == 9000
        p.set_defaults(port=70000)
        assert p.parse_args([]).port == 70000      # invalid, but accepted

    def test_suppress_removes_from_usage_too(self):
        """Correction of a widely repeated assumption.

        `HelpFormatter.add_argument` skips `help is SUPPRESS` actions entirely, so
        they never reach `_format_args`/`_format_usage` either. They vanish from
        both the usage line and the help table.
        """
        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_argument("--keep")
        p.add_argument("--gone", help=argparse.SUPPRESS)
        assert "--gone" not in p.format_usage()
        assert "--gone" not in p.format_help()
        # and it still works
        assert p.parse_args(["--gone", "1"]).gone == "1"

    def test_action_format_help_has_no_call_sites_in_argparse(self):
        """Correction: overriding `format_help` only affects custom formatters."""
        class CustomHelp(argparse.Action):
            def __call__(self, parser, namespace, values, option_string=None):
                setattr(namespace, self.dest, values)

            def format_help(self):
                return "OVERRIDDEN"

        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_argument("--a", action=CustomHelp, help="original")
        assert "OVERRIDDEN" not in p.format_help()
        assert "original" in p.format_help()

    def test_suggest_on_error_covers_only_choices(self):
        if sys.version_info < (3, 14):
            pytest.skip("3.14+ only")
        p = argparse.ArgumentParser(prog="x", add_help=False, suggest_on_error=True)
        p.add_argument("--mode", choices=("fast", "slow"))
        assert "maybe you meant 'fast'" in _error(p, ["--mode", "fasst"])
        assert "maybe you meant" not in _error(p, ["--verbse"])

    def test_add_argument_deprecated_is_314(self):
        """Correction: `deprecated=True` works for options, not just commands."""
        if sys.version_info < (3, 14):
            pytest.skip("3.14+ only")
        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_argument("--old", deprecated=True, action="store_true")
        err = io.StringIO()
        with redirect_stderr(err):
            assert p.parse_args(["--old"]).old is True
        assert "option '--old' is deprecated" in err.getvalue()
        # deprecated does NOT hide it; SUPPRESS does that
        assert "--old" in p.format_help()

    def test_add_parser_deprecated_is_314(self):
        if sys.version_info < (3, 14):
            pytest.skip("3.14+ only")
        p = argparse.ArgumentParser(prog="x", add_help=False)
        sub = p.add_subparsers(dest="command")
        sub.add_parser("old", deprecated=True)
        err = io.StringIO()
        with redirect_stderr(err):
            args = p.parse_args(["old"])
        assert "deprecated" in err.getvalue()
        assert args.command == "old"

    def test_positional_in_mutually_exclusive_group_breaks_matching(self):
        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_argument("--show", action="store_true")
        group = p.add_mutually_exclusive_group()
        group.add_argument("positional-opt", nargs="?")
        group.add_argument("--quiet", action="store_true")
        with pytest.raises(SystemExit):
            p.parse_args(["run", "build"])

    def test_nargs_n_yields_a_list(self):
        p = argparse.ArgumentParser(prog="x", add_help=False)
        p.add_argument("--coord", nargs=2, type=int)
        assert p.parse_args(["--coord", "3", "4"]).coord == [3, 4]

    def test_subparsers_action_has_no_set_defaults(self):
        p = argparse.ArgumentParser(prog="x", add_help=False)
        sub = p.add_subparsers(dest="command")
        assert not hasattr(sub, "set_defaults")
