# Errors, exit codes and testing

## Three error mechanisms, and what each actually covers

### 1. The default: `exit_on_error=True`

`parse_args` calls `parse_known_args`, then reports leftovers via `self.error()`.
`error()` prints usage plus a message to stderr and calls `self.exit(2)`. `exit()`
calls `sys.exit`.

### 2. `exit_on_error=False`

Raises `ArgumentError` instead of exiting -- but the scope is narrower than people
assume:

| Failure | With `exit_on_error=False` |
|---|---|
| bad `type=` / `choices` | `ArgumentError` raised |
| missing required argument | `ArgumentError` raised |
| **unrecognized trailing arguments** | **exits 2** |
| **`--help`, `--version`** | **exits 0** |

Because `parse_args` contains:

```python
args, argv = self.parse_known_args(args, namespace)
if argv:
    self.error(msg % ' '.join(argv))
```

`self.error` is called directly, bypassing `exit_on_error`. And the help/version
actions call `parser.exit()`.

### 3. Override `error` and `exit`

The only approach that catches everything, because everything routes through
them:

```python
class SoftParser(argparse.ArgumentParser):
    def error(self, message): raise argparse.ArgumentError(None, message)
    def exit(self, status=0, message=None): raise SoftExit(status, message or "")
```

Both, because `--help` goes through `exit`, not `error`.
`argparse_mastery.parse_or_raise(parser, argv)` does this for *any* parser, which
is what you want when you do not control the class.

## `ArgumentError` carries structure

```python
exc.argument_name   # '--port', or None for parser-level errors
exc.message         # "argument --port: invalid int value: 'abc'"
```

`argument_name` is genuinely useful -- you can build a per-argument error map for
a frontend. It is `None` when the error came from `parser.error(...)`, so
`message` is the more reliable field.

## What not to do

```python
try:
    args = parser.parse_args()
except SystemExit:
    pass          # `args` does not exist, and --help was swallowed too
```

`--help` exits **0**, so a bare `except` eats it and execution continues with an
undefined variable -- or a stale one from a previous loop iteration.

Catch `ArgumentError`, or use `parse_or_raise`.

## Improving messages

Cheap wins, none of which require overriding anything:

1. **A good `type=`.** You own the whole message, and argparse prepends
   `argument --name: `.
2. **`suggest_on_error=True`** (3.14+): improves `choices` violations only, and
   only when every choice is a `str`.
3. **Overrides for the rest.** See `argparse_mastery.suggest`, which handles the
   most common class -- a mistyped flag -- by post-processing the "unrecognized
   arguments" message. Two algorithmic details it gets right: strip leading
   dashes before comparing, and add bonuses for truncation and adjacent
   transposition, because `difflib`'s contiguous-block ratio is structurally bad
   at both.

```python
class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if "unrecognized arguments:" in message:
            unknown = message.split("unrecognized arguments:", 1)[1].split()
            hint = unrecognized_suggestions(
                unknown, known_option_strings(self),
                known_commands=_subcommand_names(self))
            if hint:
                message += "\n" + hint
        super().error(message)
```

## Exit codes are a public API

argparse uses **2** for a usage error, from the POSIX convention "invoked
incorrectly". Everything else is yours.

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | internal error (a bug; gets a traceback) |
| 2 | usage error -- argparse's own |
| 3 | bad input data |
| 4 | invalid configuration |
| 5 | required input missing |
| 6 | partial success -- some items failed |
| 130 | 128 + SIGINT |

`128 + N` for signal N is a shell convention, so 130 for Ctrl-C is right and
free. The 60-70 range is BSD `sysexits.h` (`EX_USAGE` = 64). Pick one scheme; mixing
conventions is worse than either.

**Never renumber. Never reuse a retired code.** Scripts branch on these.

Why not just 0 and 1? So a script can tell "you typed it wrong" from "the
operation failed":

```bash
if ! myapp sync; then
  if [ $? -eq 2 ]; then echo "check the command line";   # a human must fix it
  else echo "sync failed; retrying"; myapp sync; fi      # automation can retry
fi
```

### Partial success

3 of 10 items failed should exit 6, not 0 and not 1 -- and be paired with
machine-readable output listing which items failed, or the code is unactionable.
Distinguish "some failed" (6) from "everything failed" (3 or a specific code).

### `--help` and `--version` exit 0

They are successful requests. `mytool --help >/dev/null` inside `set -e` must not
fail a build.

## The three-step `main`

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # --help / --version land here, and 0 is correct.
        return 0 if exc.code is None else int(exc.code)
    try:
        return run(args)
    except UserError as exc:
        sys.stderr.write(f"{parser.prog}: error: {exc.message}\n")
        return int(exc.code)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0          # `myapp | head` closed the pipe on purpose
    # Anything else propagates: a bug must stay loud.
```

**`main` returns an int and never calls `sys.exit`.** The entry point raises it.
That separation is what makes the CLI testable in-process.

`argparse_mastery.errors.catch_errors` wraps the whole body so the `try`/`except`
plumbing lives in one place, and turns `UserError` into a message plus a code while
letting genuine bugs raise.

## `BrokenPipeError`

```python
myapp --help | head -5
```

`head` exits, the pipe closes, and your `print` raises. Unhandled, that produces a
traceback and a non-zero status in a `set -o pipefail` script. Catch it and return
0 -- the user got what they asked for. `catch_errors` does this.

## Testing

Three techniques, in increasing fidelity.

### 1. Parse only

```python
def test_defaults():
    args = parser.parse_args([])
    assert args.retries == 3
```

Fast, but misses every bug in `main`.

### 2. Call `main` and capture

The workhorse. `main(argv)` + an `int` return means no subprocess and no
`sys.argv` patching:

```python
@pytest.fixture
def run():
    def invoke(*argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exc:
                code = 0 if exc.code is None else int(exc.code)
        return SimpleNamespace(code=int(code), out=out.getvalue(), err=err.getvalue())
    return invoke

def test_list(run):
    result = run("list")
    assert result.code == ExitCode.OK
    assert "build" in result.out
```

`argparse_mastery.testing.run_cli` is this, plus `assert_ok`, `assert_failed` and
`split_stderr` helpers.

### 3. Subprocess

The only way to test the real process exit status, that `-h` writes to *stdout*
while errors write to *stderr*, and anything depending on `os.getcwd()` or signals.
Worth one or two tests; not worth all of them.

### Help output needs a pinned width

`parser.format_help()` asks the terminal. Render it yourself -- see
[07_help_output.md](07_help_output.md) -- or use `help_snapshot`. Only then is a
golden-file test stable.

### Worth asserting once

Structural properties that no behavioural test would catch:

```python
def test_parents_do_not_share_action_objects():
    first, second = common_arguments(), common_arguments()
    assert {id(a) for a in first._actions}.isdisjoint(id(a) for a in second._actions)

def test_hidden_flags_stay_hidden():
    assert "--secret" not in completion_script(parser)

def test_help_is_deterministic():
    assert render_help(parser, width=80) == render_help(build_parser(), width=80)
```

## Lessons

31, 32; pitfalls 36, 37, 39.