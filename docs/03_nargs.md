# `nargs`

One parameter, one table, and one algorithm worth understanding because the
algorithm explains every surprise.

## The table

| Value | Consumes | Result | Result when absent |
|---|---|---|---|
| `None` (default) | exactly 1 | the value | error |
| `0` | nothing | `const` | `default` (flag actions only) |
| `?` | 0 or 1 | the value, or `const` | `default` |
| `*` | 0 or more | list | `default` (often `None`!) |
| `+` | 1 or more | list | `default` |
| `N` (int) | exactly N | **list** of N | error |
| `argparse.REMAINDER` | everything left, flags included | list | `default` |
| `argparse.PARSER` | everything left, subparser claims recorded | list | `default` |
| `argparse.SUPPRESS` | -- | attribute omitted entirely | attribute absent |
| `(m, n)` | -- | **`ValueError: invalid nargs value`** | -- |

**Correction to a common belief:** `nargs=(m, n)` is a click/typer feature.
argparse's supported set is closed. Reading `_get_nargs_pattern`, the integer
branch ends with `'([AO]{%d})' % nargs` and `HelpFormatter._format_args` uses
`range(action.nargs)` -- neither has a tuple case, and both fail loudly.

## The algorithm

argparse does not match positionals one at a time. It builds a **regex** from
every positional's `nargs`, then matches the whole positional pool against it.
`_get_nargs_pattern` is short enough to reproduce:

```python
if nargs is None:       '(A)'      if option else '(-*A-*)'
elif nargs == '?':      '(A?)'     if option else '(-*A?-*)'
elif nargs == '*':      '(A*)'     if option else '(-*[A-]*)'
elif nargs == '+':      '(A+)'     if option else '(-*A[A-]*)'
elif nargs == REMAINDER: '([AO]*)' if option else '(.*)'
elif nargs == PARSER:   '(A[AO]*)' if option else '(-*A[-AO]*)'
else:                   '([AO]{N})' if option else '((?:-*A){N}-*)'
```

Three things fall straight out of those seven lines.

### 1. Option and positional patterns differ

`option` is true when the argument has option strings. The positional variants
have a leading `-*` and, for `*`, a `[A-]*` tail. That extra room is why **`--`
works for positionals and not for options**:

```
positional  nargs='*'  ->  (-*[A-]*)     the '-' slot absorbs '--'
optional    nargs='*'  ->  (A*)          no room for it
```

Verified:

```
prog --items -x          -> error: unrecognized arguments: -x
prog --items -- -x       -> error: unrecognized arguments: -- -x
prog items -x            -> error: unrecognized arguments: -x
prog items -- -x         -> items=['-x']
```

### 2. `[A-]*` is why negative numbers work as values

A positional's `*` pattern accepts a `-` followed by zero or more `A`s, which is
how `-5` is classified as a value rather than an option. An *option's* pattern
has no such allowance, which is why `--offset -5` fails and `--offset=-5` works.

### 3. `REMAINDER` on a positional is `(.*)`, so it takes everything

But only after the earlier positional's pattern has consumed at least one `A`.
Hence:

```
prog -x                -> error: unrecognized arguments: -x
prog echo -x           -> echo=['-x']            <- works
prog echo -- -x        -> echo=['echo', '--', '-x']  <- keeps the literal '--'
```

Every wrapper tool declares a command positional first and `REMAINDER` second,
and this is why.

## `?` and `const`

`nargs='?'` is the only value giving three distinct outcomes:

| Command line | Result |
|---|---|
| absent | `default` |
| `--color` | `const` |
| `--color never` | `"never"` |

Without `const`, the middle row is an error:

```
error: argument --color: expected one argument
```

And `const` must satisfy `choices`, or the bare form fails at run time for a
reason the user cannot see.

## `*` and `+`

`*` accepts zero; `+` does not. Use `+` when "no values" is meaningless (a list
of files to process) and `*` when empty is a meaningful outcome or you want
`--flag` with no arguments to mean "explicitly nothing".

The default you choose changes the *type*:

```
nargs='*', no default    -> None
nargs='*', default=[]    -> []
nargs='*', default=""    -> ""     <- iterates zero times BY ACCIDENT
```

The last one passes your tests and fails in production.

## Exact counts

`nargs=N` always yields a **list**, which is safe to unpack because the length is
guaranteed:

```python
parser.add_argument("--coord", nargs=2, type=int)
x, y = parser.parse_args(["--coord", "3", "4"]).coord    # fine
```

If you want a real tuple, or want to accept `--coord 3,4`, use a `type=` on a
single token (lesson 20).

Two variable-width **positionals** cannot be resolved at all -- nothing marks
where the first ends. Two **options** can, because the flag is the boundary.

## `REMAINDER` and pass-through

The one way to forward a command line verbatim:

```python
parser.add_argument("cmd", nargs="?")
parser.add_argument("rest", nargs=argparse.REMAINDER)

def forwarded(args):
    argv = ([args.cmd] if args.cmd else []) + args.rest
    if argv and argv[0] == "--":       # argparse keeps the literal '--'
        argv = argv[1:]
    return argv
```

`parse_known_args` is the alternative, and it strips the `--` for you -- but it
cannot distinguish a typo from intended pass-through.

## `parse_intermixed_args`

For "options and files in any order" (`grep`, `wc`, `sort`):

```python
args = parser.parse_intermixed_args()     # prog a.txt -v b.txt works
```

Raises `TypeError` if any positional uses `REMAINDER` or `PARSER`, and does not
support subparsers. Use it for file-listing tools, nothing else.

## `SUPPRESS` as a `nargs` value

`nargs=argparse.SUPPRESS` matches nothing and omits the attribute -- the
"was it passed?" mechanism. With `dest=argparse.SUPPRESS` on an *optional* you
get the same effect and can still supply the flag.

Lessons 08, 09, 10, 11; pitfalls 6, 7, 17, 31, 32, 38.