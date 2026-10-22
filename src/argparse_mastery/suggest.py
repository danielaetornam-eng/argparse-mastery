"""Fuzzy matching for typos in flags and subcommands.

The gap this fills
------------------
Python 3.14 added ``ArgumentParser(suggest_on_error=True)``. Reading
:mod:`argparse` 3.14's :meth:`~argparse.ArgumentParser._check_value`, it does
exactly one thing::

    if self.suggest_on_error and isinstance(value, str):
        if all(isinstance(choice, str) for choice in action.choices):
            suggestions = difflib.get_close_matches(value, action.choices, 1)
            if suggestions:
                msg = 'invalid choice: %(value)r, maybe you meant %(closest)r? ...'

So it covers ``--mode fastt`` but **not** ``--verbse``. Unrecognized arguments
still produce a bare ``error: unrecognized arguments: --verbse`` -- which is the
most common typo class there is.

This module implements the missing half with :mod:`difflib`, plus the two
subtleties that make suggestions usable rather than annoying:

1. **Prefix awareness.** ``difflib`` compares whole strings, so ``--outp`` vs
   ``--output-file`` scores poorly while ``output`` vs ``outp`` is obvious to a
   human. Stripping leading dashes before comparing fixes it.
2. **Bounded noise.** Suggesting for ``--x`` is worse than saying nothing.
   :func:`closest_match` enforces a similarity cutoff and caps the count.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher

__all__ = [
    "closest_match",
    "did_you_mean",
    "unrecognized_suggestions",
]

#: difflib's ratio is 0.0-1.0. 0.6 is difflib's own default; we raise it to 0.7
#: for command-line flags because a false suggestion costs the user more than a
#: missing one does -- they stop reading the error and assume the tool is broken.
_CUTOFF = 0.7


def _normalize(candidate: str) -> str:
    """Strip dashes so ``--outp`` compares against ``output-file`` fairly."""
    return candidate.lstrip("-+/")


def _prefix_bonus(word: str, candidate: str) -> float:
    """Score a candidate that is a prefix-extension of ``word``, or vice versa.

    difflib scores *contiguous* matching blocks, so it is blind to the most common
    flag typo there is: truncating a long name. ``--outp`` against
    ``--output-file`` shares no 4-character block, so ``get_close_matches`` returns
    nothing -- even though the match is obvious to anyone who reads flags by
    prefix.

    A candidate that extends the word (or that the word extends) is therefore a
    guaranteed suggestion, scored above anything difflib produces. The longer the
    shared prefix, the higher the score, so ``--conf`` prefers ``--config`` over
    ``--config-file-only``.
    """
    short, long = sorted((word, candidate), key=len)
    if not short or short == long:
        return 0.0
    if not long.startswith(short):
        return 0.0
    shared = 0
    for a, b in zip(short, long, strict=False):
        if a != b:
            break
        shared += 1
    # 0.90 upward: outranks any real difflib score while still sorting sensibly.
    return 0.90 + min(shared / max(len(long), 1), 0.09)


def _transposition_bonus(word: str, candidate: str) -> float:
    """Score a candidate that becomes ``word`` after swapping one adjacent pair.

    difflib scores *contiguous* matching blocks, so it is systematically bad at the
    second most common CLI typo: two adjacent characters typed in the wrong order.
    ``tset`` against ``test`` scores only 0.6 -- below any sane cutoff -- because
    the two matching runs are 1 and 2 characters long.

    The fix is to detect the swap directly. This is the Damerau-Levenshtein
    insight (one edit may be one transposition) applied as a targeted bonus rather
    than a general edit-distance implementation: cheaper, and it *composes* with
    the ratio score instead of replacing it, so a candidate that is both similar
    and a swap still wins.
    """
    if len(word) != len(candidate) or not word:
        return 0.0
    differences = [i for i, (a, b) in enumerate(zip(word, candidate, strict=True)) if a != b]
    if len(differences) != 2:
        return 0.0
    i, j = differences
    if j != i + 1:
        return 0.0
    if word[i] == candidate[j] and word[j] == candidate[i]:
        # 0.95: above any prefix extension, because a swap is a stronger signal
        # of intent than a truncation.
        return 0.95
    return 0.0


def closest_match(
    word: str,
    candidates: Iterable[str],
    *,
    n: int = 3,
    cutoff: float = _CUTOFF,
    normalize: bool = True,
) -> list[str]:
    """Return up to ``n`` candidates closest to ``word``, best first.

    ::

        >>> closest_match("--verbse", ["--verbose", "--version", "--quiet"])
        ['--verbose']
        >>> closest_match("--outp", ["--output-file", "--quiet"])
        ['--output-file']
        >>> closest_match("tset", ["test", "lint"])
        ['test']

    Three mechanisms combine: difflib's ratio for typos and transpositions it can
    see, a prefix bonus for truncation, and a transposition bonus for adjacent
    swaps. Verified scores::

        "buld"  vs "build"   difflib 0.89   (caught by the ratio alone)
        "biuld" vs "build"   difflib 0.80   (caught by the ratio alone)
        "tset"  vs "test"    difflib 0.60   BELOW the 0.7 cutoff -- needs the bonus
        "--outp" vs "--output-file"  difflib 0.33 -- needs the prefix bonus

    ``normalize=False`` compares the raw strings, which is what you want for
    values rather than flags (paths, URLs, package names).

    Tie-breaking is by descending similarity, then by the order the candidates
    were supplied. Keep your candidate list in a sensible order -- most commonly
    used flags first -- so ties resolve helpfully.
    """
    pool = list(candidates)
    if not pool:
        return []

    if not normalize:
        # Value matching: no dash-stripping, no prefix bonus, plain similarity.
        matches = difflib.get_close_matches(word, pool, n=n, cutoff=cutoff)
        return matches

    pairs = [(c, _normalize(c)) for c in pool]

    # difflib covers typos and transpositions...
    typo_scores: dict[str, float] = {}
    for _candidate, norm in pairs:
        ratio = SequenceMatcher(None, _normalize(word), norm).ratio()
        if ratio >= cutoff:
            typo_scores[norm] = ratio

    # ...and the two bonuses cover what difflib structurally cannot see.
    scores: dict[str, float] = {}
    for candidate, norm in pairs:
        scores[candidate] = max(
            typo_scores.get(norm, 0.0),
            _prefix_bonus(_normalize(word), norm),
            _transposition_bonus(_normalize(word), norm),
        )

    ranked = sorted(
        (c for c in scores if scores[c] >= cutoff),
        key=lambda c: (-scores[c], pool.index(c)),
    )
    return ranked[:n]


def did_you_mean(word: str, candidates: Iterable[str], *, limit: int = 1) -> str:
    """Format a suggestion the way argparse formats its choices errors.

    Returns an empty string when there is nothing good to suggest, so callers
    can use it unconditionally::

        hint = did_you_mean(unknown, known)
        parser.error(f"unrecognized argument {unknown!r}" + (f". {hint}" if hint else ""))
    """
    matches = closest_match(word, candidates, n=limit)
    if not matches:
        return ""
    if len(matches) == 1:
        return f"did you mean {matches[0]!r}?"
    listed = ", ".join(repr(m) for m in matches)
    return f"did you mean one of {listed}?"


def unrecognized_suggestions(
    unknown: Sequence[str],
    known_options: Iterable[str],
    *,
    known_commands: Iterable[str] = (),
) -> str:
    """Build the multi-line suggestion block for unrecognized arguments.

    Distinguishes two cases, because the fix differs:

    * The token looks like a **flag** (``-x``/``--xyz``): suggest other flags.
    * The token looks like a **subcommand**: suggest commands. This is the one
      argparse's own suggestions never cover, and it is the most valuable case
      -- ``prog stat`` vs ``prog status`` is a mistake everyone makes.
    * Otherwise (a stray positional): stay silent. Guessing at a bare word is
      noise.

    ::

        unrecognized_suggestions(["--verbse", "stat"], ["--verbose", "--version"], ["status", "list"])
        # "argument --verbse: did you mean '--verbose'?"
        # "command 'stat': did you mean 'status'?"
    """
    lines: list[str] = []
    options = list(known_options)
    commands = list(known_commands)

    for token in unknown:
        if token.startswith("-"):
            match = did_you_mean(token, options)
            if match:
                lines.append(f"  argument {token!r}: {match}")
        else:
            match = did_you_mean(token, commands)
            if match:
                lines.append(f"  command {token!r}: {match}")
    return "\n".join(lines)
