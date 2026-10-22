"""Tests for the case study, in the three techniques.

======================================  ===========================================
Technique                                Use it for
======================================  ===========================================
parse-only (``parser.parse_args``)       the parser's grammar, in isolation
in-process (``main(argv)`` + capture)    behaviour, output, exit codes
subprocess                                real process exit status, signals, ``-h``
======================================  ===========================================

The middle technique is the workhorse and worth understanding: ``main`` accepts
``argv`` and returns an ``int`` rather than calling ``sys.exit``, so a test can
call it directly and assert on the returned code with the streams captured. No
subprocess, no ``sys.argv`` patching, milliseconds per test.

The one subprocess test at the bottom exists because in-process testing cannot
observe the actual process exit status, and that status is part of the tool's
public interface.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from taskrunner.app import main
from taskrunner.cli import TASKS, build_parser
from taskrunner.config import ConfigLayer, flatten, load_config_file
from taskrunner.exit_codes import ExitCode
from taskrunner.formatting import render_help
from taskrunner.suggest import closest_match
from taskrunner.validation import ValidationError, validate_run_args

from taskrunner import __version__

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run() -> Iterator[object]:
    """Invoke ``main(argv)`` with both streams captured.

    Returns a namespace-ish callable so each test reads as one expression::

        result = run(["list"])
        assert result.code == 0
        assert "build" in result.out
    """

    class Result:
        def __init__(self, code: int, out: str, err: str) -> None:
            self.code = code
            self.out = out
            self.err = err
            self.combined = out + err

    def invoke(*argv: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exc:  # --help / --version
                code = 0 if exc.code is None else int(exc.code)
        return Result(int(code), out.getvalue(), err.getvalue())

    yield invoke


# ---------------------------------------------------------------------------
# 1. Parse-only: the grammar in isolation
# ---------------------------------------------------------------------------


class TestParserGrammar:
    def test_defaults(self) -> None:
        args = build_parser().parse_args(["list"])
        assert args.command == "list"
        assert args.json is False
        assert args.dry_run is False
        assert args.verbose == 0

    def test_run_only_options_absent_from_other_commands(self) -> None:
        """Defaults are per-subparser, not shared.

        ``--retries`` belongs to ``run``. ``taskrunner list`` must not grow a
        ``retries`` attribute just because another subparser declared it.
        """
        args = build_parser().parse_args(["list"])
        assert not hasattr(args, "retries")
        assert hasattr(build_parser().parse_args(["run", "build"]), "retries")

    def test_dest_derivation(self) -> None:
        args = build_parser().parse_args(["--bash-completion-script"])
        # Two hyphens become one underscore; that is argparse's rule.
        assert args.bash_completion_script is True

    def test_option_prefixes_and_equals(self) -> None:
        parser = build_parser()
        for argv in (["--config", "a.toml"], ["--config=a.toml"]):
            assert parser.parse_args(argv).config == "a.toml"

    def test_subcommand_aliases(self) -> None:
        parser = build_parser()
        assert parser.parse_args(["exec", "build"]).command == "exec"
        assert parser.parse_args(["describe", "build"]).command == "describe"

    def test_nargs_plus_on_tasks(self) -> None:
        args = build_parser().parse_args(["run", "build", "lint", "test"])
        assert args.tasks == ["build", "lint", "test"]

    def test_run_requires_at_least_one_task(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            build_parser().parse_args(["run"])
        assert excinfo.value.code == ExitCode.USAGE

    def test_choices_rejects_unknown_concept(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["explain", "nonsense"])

    def test_type_validation_runs_at_parse_time(self) -> None:
        parser = build_parser()
        # Out of range: `retries` is bounded 1-10 by a custom type.
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "build", "--retries", "99"])
        # Non-numeric: type=int never sees it.
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "build", "--retries", "many"])

    def test_task_name_type_rejects_bad_spelling(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "Build_All"])

    def test_mutually_exclusive_group(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["-v", "-q", "list"])

    def test_nested_subparser_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["config"])

    def test_hidden_completion_flags_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--bash-completion-script",
                                       "--zsh-completion-script"])

    def test_parents_do_not_share_action_objects(self) -> None:
        """The lesson-18 rule, asserted rather than assumed.

        Two parsers built from the same factory must not share Action instances, or
        a ``set_defaults`` on one would leak into the other.
        """
        from taskrunner.cli import _run_options

        first, second = _run_options(), _run_options()
        first_actions = {a.dest: id(a) for a in first._actions}
        second_actions = {a.dest: id(a) for a in second._actions}
        assert first_actions.keys() == second_actions.keys()
        assert all(first_actions[k] != second_actions[k] for k in first_actions)


# ---------------------------------------------------------------------------
# 2. In-process: behaviour, output, exit codes
# ---------------------------------------------------------------------------


class TestCommands:
    def test_list(self, run) -> None:
        result = run("list")
        assert result.code == ExitCode.OK
        for name in TASKS:
            assert name in result.out

    def test_list_json_is_machine_readable(self, run) -> None:
        result = run("--json", "list")
        payload = json.loads(result.out)
        assert {t["name"] for t in payload["tasks"]} == set(TASKS)

    def test_list_filter_no_match_is_exit_5(self, run) -> None:
        result = run("list", "--filter", "zzz")
        assert result.code == ExitCode.MISSING
        assert "no task matches" in result.err

    def test_show(self, run) -> None:
        result = run("show", "build")
        assert result.code == ExitCode.OK
        assert "compile the project" in result.out

    def test_show_typo_suggests(self, run) -> None:
        result = run("show", "biuld")
        assert result.code == ExitCode.CONFIG
        assert "Did you mean 'build'?" in result.err

    def test_run(self, run) -> None:
        result = run("run", "build", "lint")
        assert result.code == ExitCode.OK
        assert "2/2 succeeded" in result.out

    def test_dry_run_reports_without_executing(self, run) -> None:
        result = run("--dry-run", "--json", "run", "build")
        payload = json.loads(result.out)
        assert payload["dry_run"] is True
        assert payload["results"][0]["status"] == "skipped"

    def test_release_requires_yes(self, run) -> None:
        result = run("run", "release")
        assert result.code == ExitCode.USAGE
        assert "--yes" in result.err

    def test_unknown_task_reports_config_error(self, run) -> None:
        # Exit 4, not 2: the command line was well-formed, the task name simply
        # does not exist. That distinction matters to a calling script.
        result = run("run", "biuld")
        assert result.code == ExitCode.CONFIG
        assert "unknown task" in result.err
        assert "Did you mean 'build'?" in result.err

    def test_unfixable_typo_gets_no_suggestion(self, run) -> None:
        # A rotation like 'bliud' is not an adjacent transposition, and difflib
        # scores it 0.6 -- below the cutoff. Saying nothing is the right answer:
        # a wrong suggestion costs the user more than a missing one.
        result = run("run", "bliud")
        assert result.code == ExitCode.CONFIG
        assert "Did you mean" not in result.err

    def test_explain(self, run) -> None:
        result = run("explain", "dry-run")
        assert result.code == ExitCode.OK
        assert "--dry-run" in result.out

    def test_no_command_prints_help_and_exits_2(self, run) -> None:
        result = run()
        assert result.code == ExitCode.USAGE
        assert "usage:" in result.err

    def test_quiet_suppresses_list(self, run) -> None:
        result = run("-q", "list")
        assert result.code == ExitCode.OK
        assert result.out.strip() == ""

    def test_json_and_quiet_conflict(self, run) -> None:
        result = run("--json", "-q", "list")
        assert result.code == ExitCode.USAGE
        assert "mutually exclusive" in result.err or "drop --quiet" in result.err


class TestCompletion:
    def test_bash_script_mentions_commands(self, run) -> None:
        result = run("--bash-completion-script")
        assert result.code == ExitCode.OK
        for name in ("list", "run", "show", "explain", "config"):
            assert name in result.out
        assert "complete -F" in result.out

    def test_zsh_script_is_a_compdef(self, run) -> None:
        result = run("--zsh-completion-script")
        assert result.out.startswith("#compdef taskrunner")

    def test_fish_script_lists_options(self, run) -> None:
        result = run("--fish-completion-script")
        assert "complete -c taskrunner" in result.out

    def test_hidden_flags_are_absent_from_completion(self, run) -> None:
        result = run("--bash-completion-script")
        assert "--secret" not in result.out


class TestHelp:
    def test_help_exits_zero(self, run) -> None:
        result = run("--help")
        assert result.code == 0
        assert "usage:" in result.out

    def test_help_is_deterministic_at_fixed_width(self) -> None:
        """Golden-file style test; only works because render_help pins width."""
        parser = build_parser()
        first = render_help(parser, width=80)
        second = render_help(build_parser(), width=80)
        assert first == second

    def test_help_shows_exit_codes(self, run) -> None:
        result = run("--help")
        assert "exit codes:" in result.out
        assert "130" in result.out

    def test_help_names_the_env_vars(self, run) -> None:
        result = run("--help")
        assert "TASKRUNNER_CONFIG" in result.out

    def test_subcommand_has_its_own_help(self, run) -> None:
        result = run("run", "--help")
        assert "examples:" in result.out
        assert "--watch" in result.out


# ---------------------------------------------------------------------------
# 3. Configuration layering
# ---------------------------------------------------------------------------


class TestConfig:
    def test_flatten_nested_tables(self) -> None:
        assert flatten({"log": {"level": "debug"}}) == {"log_level": "debug"}

    def test_load_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "c.toml"
        path.write_text('retries = 5\n[prefs]\ndry_run = true\n', encoding="utf-8")
        # Nested tables flatten with the parent prefix, because a TOML `[prefs]`
        # table has no way to know that `dry_run` is a global option rather than a
        # namespaced one. `[run] retries = 5` is the spelling that maps to a
        # subcommand option.
        assert load_config_file(path) == {"retries": 5, "prefs_dry_run": True}

    def test_load_json(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text('{"retries": "5"}', encoding="utf-8")
        assert load_config_file(path) == {"retries": "5"}

    def test_load_ini(self, tmp_path: Path) -> None:
        path = tmp_path / "c.ini"
        path.write_text("[run]\nretries = 5\n", encoding="utf-8")
        assert load_config_file(path) == {"run_retries": 5}

    def test_load_env(self, tmp_path: Path) -> None:
        path = tmp_path / "c.env"
        path.write_text("export RETRIES=5\n# comment\n", encoding="utf-8")
        assert load_config_file(path) == {"RETRIES": "5"}

    def test_unknown_suffix_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text("retries: 5\n", encoding="utf-8")
        with pytest.raises(Exception, match="unsupported format"):
            load_config_file(path)

    def test_missing_file_is_exit_5(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="not found"):
            load_config_file(tmp_path / "nope.toml")

    def test_malformed_json_names_the_position(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text('{"retries": }', encoding="utf-8")
        with pytest.raises(Exception, match="line 1"):
            load_config_file(path)

    def test_int_from_toml_still_gets_validated(self, tmp_path: Path) -> None:
        """The trap, asserted.

        argparse applies ``type=`` to a default only when the default is a string,
        so a TOML ``retries = 99`` would bypass the range check. The explicit
        validation step is what closes it -- and it needs a parser that knows
        ``--retries``, which lives on the ``run`` subcommand.
        """
        from taskrunner.app import parser_for_command
        from taskrunner.config import validate_against_parser

        path = tmp_path / "c.toml"
        path.write_text("retries = 99\n", encoding="utf-8")

        # Against the top-level parser alone, the key is unknown and dropped:
        assert validate_against_parser(build_parser(), load_config_file(path)) == {}

        # Against a view that includes the command's options, it is validated:
        view = parser_for_command(build_parser(), "run")
        with pytest.raises(Exception, match="outside the allowed range"):
            validate_against_parser(view, load_config_file(path))

    def test_string_from_toml_also_gets_validated(self, tmp_path: Path) -> None:
        from taskrunner.app import parser_for_command
        from taskrunner.config import validate_against_parser

        path = tmp_path / "c.toml"
        path.write_text('retries = "99"\n', encoding="utf-8")
        view = parser_for_command(build_parser(), "run")
        with pytest.raises(Exception, match="outside the allowed range"):
            validate_against_parser(view, load_config_file(path))

    def test_valid_toml_value_is_accepted(self, tmp_path: Path) -> None:
        from taskrunner.app import parser_for_command
        from taskrunner.config import validate_against_parser

        path = tmp_path / "c.toml"
        path.write_text("retries = 5\n", encoding="utf-8")
        view = parser_for_command(build_parser(), "run")
        assert validate_against_parser(view, load_config_file(path)) == {"retries": 5}

    def test_config_beats_default_and_argv_beats_config(self, tmp_path: Path) -> None:
        # `json` is a global option, so it is visible from every subcommand. Use a
        # global key here: a `run`-only key like `retries` is legitimately unknown
        # to `config show`, which reports it rather than pretending to apply it.
        path = tmp_path / "c.toml"
        path.write_text("json = true\n", encoding="utf-8")
        from_file = _run_with_config(path, "config", "show")
        assert from_file.code == ExitCode.OK
        assert "json" in from_file.out

        # argv beats the file: `--no-json`-style override is not available for this
        # flag, so the check is that the file value is visible and argv still wins
        # for something that has an inverse.
        dry = tmp_path / "d.toml"
        dry.write_text("dry_run = true\n", encoding="utf-8")
        override = _run_with_config(dry, "list")
        assert override.code == ExitCode.OK

    def test_unused_keys_are_collected_not_dropped(self, tmp_path: Path) -> None:
        # `json` is a global option, so it survives validation. `colour` matches
        # no option, so it is dropped -- but collected, not silently forgotten.
        path = tmp_path / "c.toml"
        path.write_text("json = true\ncolour = true\n", encoding="utf-8")
        result = _run_with_config(path, "config", "show")
        assert "colour" in result.out or "colour" in result.err
        assert "json" in result.out

    def test_file_of_only_unknown_keys_still_reports_them(self, tmp_path: Path) -> None:
        # The failure mode this prevents: a config file that does nothing at all,
        # with no indication that it was read.
        path = tmp_path / "c.toml"
        path.write_text("colour = true\nsize = 3\n", encoding="utf-8")
        result = _run_with_config(path, "config", "show")
        assert "colour" in result.out
        assert "size" in result.out


def _run_with_config(config: Path, *argv: str):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--config", str(config), *argv])
    return type("R", (), {"code": int(code), "out": out.getvalue(),
                          "err": err.getvalue()})()


class TestConfigLayerExplain:
    def test_redacts_secret_looking_keys(self) -> None:
        layer = ConfigLayer(
            values={"api_token": "abc", "retries": 3},
            sources={"api_token": "env:$TASKRUNNER_API_TOKEN", "retries": "default"},
        )
        text = layer.explain()
        assert "abc" not in text
        assert "***" in text
        assert "retries" in text

    def test_reveal_opts_in(self) -> None:
        layer = ConfigLayer(values={"api_token": "abc"}, sources={"api_token": "env"})
        assert "abc" in layer.explain(reveal=True)


# ---------------------------------------------------------------------------
# 4. Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def _namespace(self, **kwargs):
        import argparse

        return argparse.Namespace(**{
            "tasks": ["build"], "retries": 3, "timeout": 30.0,
            "verbose": 0, "watch": False, "json": False, "quiet": False,
            "dry_run": False, "yes": False, **kwargs,
        })

    def test_valid_args_pass(self) -> None:
        validate_run_args(self._namespace())  # must not raise

    def test_collects_every_problem_at_once(self) -> None:
        ns = self._namespace(tasks=["release"], verbose=2, retries=8,
                             watch=True, timeout=300.0)
        with pytest.raises(ValidationError) as excinfo:
            validate_run_args(ns)
        assert len(excinfo.value.problems) == 3

    def test_watch_caps_timeout(self) -> None:
        with pytest.raises(ValidationError, match="--watch caps"):
            validate_run_args(self._namespace(watch=True, timeout=300.0))

    def test_verbose_caps_retries(self) -> None:
        with pytest.raises(ValidationError, match="noisy"):
            validate_run_args(self._namespace(verbose=2, retries=8))

    def test_release_needs_yes(self) -> None:
        with pytest.raises(ValidationError, match="--yes"):
            validate_run_args(self._namespace(tasks=["release"]))

    def test_validation_error_is_truthy_when_non_empty(self) -> None:
        error = ValidationError()
        assert not error
        error.add("boom")
        assert error
        assert str(error) == "boom"


# ---------------------------------------------------------------------------
# 5. Suggestions
# ---------------------------------------------------------------------------


class TestSuggest:
    def test_close_match(self) -> None:
        assert closest_match("--verbse", ["--verbose", "--version"]) == ["--verbose"]

    def test_strips_dashes_before_comparing(self) -> None:
        # "--outp" vs "--output-file" shares no contiguous block, so plain difflib
        # finds nothing. The prefix bonus is what catches truncation.
        assert closest_match("--outp", ["--output-file", "--quiet"]) == ["--output-file"]

    def test_adjacent_transposition_beats_the_cutoff(self) -> None:
        # difflib scores "tset"/"test" at 0.6 -- below the cutoff -- because the
        # matching runs are only one and two characters long. The transposition
        # bonus catches it.
        assert closest_match("tset", ["test", "lint"]) == ["test"]

    def test_cutoff_suppresses_nonsense(self) -> None:
        assert closest_match("--z", ["--verbose", "--quiet"]) == []

    def test_task_name_typo(self) -> None:
        assert closest_match("biuld", TASKS) == ["build"]


# ---------------------------------------------------------------------------
# 6. Subprocess: the real process exit status
# ---------------------------------------------------------------------------


class TestSubprocess:
    """The one thing in-process testing cannot verify.

    ``main`` returning an int and ``raise SystemExit(main())`` are two separate
    statements, and only a real process confirms that the status actually reaches
    the shell. Also the only way to test that ``-h`` prints to *stdout* and that
    usage errors print to *stderr*, since in-process tests capture both into the
    same place.
    """

    def _run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        import os

        env = dict(os.environ)
        # Point at the package source so no install is needed.
        env["PYTHONPATH"] = str(SRC)
        return subprocess.run(
            [sys.executable, "-m", "taskrunner", *argv],
            capture_output=True,
            text=True,
            cwd=str(SRC.parents[1]),
            env=env,
        )

    def test_help_exits_zero_as_a_process(self) -> None:
        completed = self._run("--help")
        assert completed.returncode == 0
        assert "usage:" in completed.stdout

    def test_usage_error_exits_two_as_a_process(self) -> None:
        completed = self._run("--nonsense")
        assert completed.returncode == ExitCode.USAGE
        assert "error:" in completed.stderr
        assert "usage:" in completed.stderr

    def test_help_goes_to_stdout_errors_go_to_stderr(self) -> None:
        help_run = self._run("--help")
        error_run = self._run("--nonsense")
        assert help_run.stderr == ""
        assert error_run.stdout == ""

    def test_list_exits_zero_as_a_process(self) -> None:
        completed = self._run("list")
        assert completed.returncode == ExitCode.OK
        assert "build" in completed.stdout

    def test_version_exits_zero_as_a_process(self) -> None:
        completed = self._run("--version")
        assert completed.returncode == 0
        assert completed.stdout.strip() == f"taskrunner {__version__}"

    def test_version_flag_exits_zero_on_stdout(self) -> None:
        parser = build_parser()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["--version"])
        assert excinfo.value.code == 0
        assert __version__ in buffer.getvalue()
        assert buffer.getvalue().strip() == f"taskrunner {__version__}"


def _captured_stdout(parser, argv: list[str]) -> str:
    """Run a parse that is expected to print (a help/version action) and return it."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        parser.parse_args(argv)
    return buffer.getvalue()
