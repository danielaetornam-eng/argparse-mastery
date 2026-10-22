# Configuration and environment

Two layers that are not argv, and one trap that catches everyone.

## The trap first

argparse applies `type=` to a default **only if the default is a `str`** *and* the
namespace value is still that same object. From CPython's source, at the end of
`parse_known_args`:

```python
if action.default is not None and isinstance(action.default, str) \
   and getattr(namespace, action.dest) is action.default:
    setattr(namespace, action.dest, self._get_value(action, action.default))
```

So:

```python
parser.add_argument("--port", type=port, default=8080)

parser.set_defaults(port="8080")   # str -> converter runs   -> 8080  (validated)
parser.set_defaults(port=8080)     # int -> converter SKIPPED -> 8080  (unvalidated)
```

Both give `8080`, which is exactly why the bug survives a test on the happy path.
Now add a validator and the difference appears:

```python
def port(raw): ...
parser.set_defaults(port=70000)    # int: the validator NEVER RUNS
```

**A `.toml` file writing `port = 70000` bypasses every check you wrote. The
equivalent `.json` file writing `"port": "70000"` does not.** Two files expressing
the same thing behaving differently, invisibly.

Fix: validate every non-argv layer through the parser's own callables.

```python
def typed_defaults(parser, values):
    actions = {a.dest: a for a in parser._actions}
    out = {}
    for key, value in values.items():
        action = actions.get(key)
        if action is None:
            continue
        if action.type is not None:
            try:
                # str() round-trip is what catches an already-typed value.
                value = action.type(value if isinstance(value, str) else str(value))
            except (argparse.ArgumentTypeError, ValueError, TypeError) as exc:
                raise SystemExit(f"{key}: invalid value from config: {exc}")
        if action.choices is not None and action.nargs != 0 and value not in action.choices:
            raise SystemExit(f"{key}: {value!r} is not one of {list(action.choices)}")
        out[key] = value
    return out
```

The `str()` round-trip is the whole fix, and its limit is worth stating: `type=`
is contractually a *string* converter, so round-tripping is lossless for the types
a config file holds. See `argparse_mastery.config.typed_defaults`.

## Precedence

```
code defaults  <  config file  <  environment  <  argv
```

Environment above the file so CI can override a checked-in config without editing
it. argv last so a human can always win.

The trick that makes this free: **install every non-argv layer with
`set_defaults`.** argparse overwrites defaults as it consumes tokens, so argv wins
with no merge code -- and no merge code to get wrong.

```python
merged = {}
if config_path: merged.update(load_config_file(config_path))
merged.update(env_layer)
parser.set_defaults(**typed_defaults(parser, merged))
args = parser.parse_args(argv)          # argv wins, automatically
```

## Two philosophies

**A. argparse-native** (above): ten lines, argv wins for free. Right for a single
config file and a developer tool.

**B. Explicit resolution**: keep the layers separate and merge with declared
precedence, recording *where each value came from*. More code, and the only
approach that supports `--show-config` and secret redaction.

```python
class ConfigResolver:
    def add_defaults(self, data): ...
    def add_file(self, data, source): ...
    def add_env(self, env, prefix="MYAPP_"): ...
    def resolve(self) -> ResolvedConfig:      # returns values + sources + unused
```

`argparse_mastery.config.ConfigResolver` implements B, and `ResolvedConfig.explain()`
renders the `--show-config` view. Two behaviours worth copying:

* **Unknown keys are collected, not dropped.** A config file whose keys match no
  option does nothing, silently, for months. Report them.
* **Secrets are redacted by default**, keyed on substrings like `token`,
  `password`, `secret`, with an explicit `--reveal` to opt out.

## Finding the config file

You need the file before the parser exists, but `--config` is one of the parser's
options. Two honest solutions:

**A throwaway pre-parser** (preferred):

```python
pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
pre.add_argument("--config")
known, _ = pre.parse_known_args(argv)
```

Inherits argparse's handling of `--config=x`, `--config x`, `--` and prefixes. Two
details: `add_help=False` or `-h` is consumed here; `allow_abbrev=False` or the
two parsers disagree about abbreviations.

**Two-pass parsing**:

```python
known, _ = parser.parse_known_args()
parser.set_defaults(**load(known.config))
args = parser.parse_args()                 # strict, and the one users see
```

Also fine, but runs the real validation twice.

Conventional locations, in order:

```
1. --config PATH
2. $MYAPP_CONFIG
3. $XDG_CONFIG_HOME/myapp/config.toml
4. ./myapp.toml
5. nothing
```

## Formats without dependencies

| Suffix | Format | Notes |
|---|---|---|
| `.json` | `json` | stdlib |
| `.toml` | `tomllib` | stdlib since 3.11, read-only |
| `.ini` / `.cfg` | `configparser` | `[section] key` becomes `section_key` |
| `.env` | hand-rolled | `KEY=value`, `#` comments, `export` tolerated |

Chosen by suffix. YAML is deliberately absent: it would add a runtime dependency
to the CLI for the sake of a config format. Refuse unknown suffixes loudly --
silently ignoring a file the user believes is applied is worse.

Two details that matter for INI and `.env`, which are string-only:

* **Coerce scalars.** `"true"`/`"42"` must become `bool`/`int` or every numeric
  option arrives as a string and validators see the wrong type. Be conservative:
  keep `"007"` a string, keep `"1e5"` a string.
* **Use `_`, not `.`, for section prefixes.** `[run] retries = 5` must become
  `run_retries` to match `--run-retries`'s dest. `run.retries` matches nothing.

## Nested tables

```toml
[prefs]
dry_run = true
```

flattens to `prefs_dry_run`, which matches nothing -- a TOML table has no way to
know that `dry_run` is a global option rather than a namespaced one. Document the
flattening, and mention the `[command] option` spelling for subcommand options.

## Environment variables

Three ways, in increasing order of control:

**1. As the default.** Best for one-off tools, and it composes perfectly:

```python
parser.add_argument("--log-level", default=os.environ.get("MYAPP_LOG_LEVEL", "warning"))
```

Note this is the one case where the "defaults bypass `type=`" trap *helps*:
environment values are strings, so argparse converts them.

**2. A valueless `--token` flag.** Keeps secrets out of shell history and `ps`.
Lesson 24, pattern 10.

**3. An explicit env layer with provenance.** For `--show-config`.
`ConfigResolver.add_env`.

### Naming

`MYAPP_LOG_LEVEL=debug` maps to `--log-level` by upper-casing and replacing hyphens
with underscores, with a configurable prefix. Supporting *both* prefixed and
unprefixed names is common; supporting unprefixed names in a tool that might be
installed twice is how `PATH`-adjacent confusion starts. Pick one.

### Always document it

A flag with a hidden environment source is undiscoverable:

```python
help=f"log level (env: {ENV_PREFIX}LOG_LEVEL)"
```

or derive the suffixes: `argparse_mastery.config.explain_env(parser)`.

### Booleans

`bool("false")` is `True`. Users write `1`, `true`, `yes`, `on`, `0`, `no`, `off`
and `False`. Parse explicitly and reject anything else -- a typo'd env var must not
look like a deliberate opt-out.

With `BooleanOptionalAction` and `default=None` the layering is unambiguous:

```
MYAPP_COLOR=0   -> env sets it
--color         -> True          argv wins
--no-color      -> False         argv wins
(nothing)       -> None          "ask isatty()"
```

Only possible because the default is `None`.

### Coercion

`int(os.environ["X"])` raises at import time and stops the tool starting. Convert
inside a `type=` so the user gets a message:

```python
def env_int(name):
    def parse(raw):
        try: return int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"${name}={raw!r} is not an integer")
    return parse
```

## `fromfile_prefix_chars`: not the same thing

`ArgumentParser(fromfile_prefix_chars="@")` splices a file's tokens into argv
**in place of the value**:

```
prog @args.txt --debug      # args.txt contains "--retries 5 --tag a"
```

That is argv splicing, so the file may contain *any* arguments. It is the
mechanism behind `gcc @file` and `curl @file`, and it is **not** a way to read a
config value from a file -- for that, use `set_defaults`.

Three sharp edges:

* `@`-leading *values* break. `--email a@b.com` tries to read the file `b.com`.
* The file size is unbounded.
* Errors name the file, not the argument that referenced it.

Use it for response files only, and consider requiring `@=path`.

Lessons 28, 29, 30; pitfalls 8, 33, 34, 39.