"""Fuzzy matching for typos in flags, commands and task names.

Python 3.14 added ``ArgumentParser(suggest_on_error=True)``, but reading its
:meth:`~argparse.ArgumentParser._check_value` shows it does exactly one thing: it
improves ``choices`` violations, and only when *every* choice is a ``str``. So
``--mode fastt`` gets "maybe you meant 'fast'?" while ``--verbse`` gets a bare
``unrecognized arguments``.

This module covers the missing half, with two adjustments that make the
suggestions actually useful:

* **Prefix awareness.** ``difflib`` compares whole strings, so ``--outp`` scores
   poorly against ``--output-file`` while the match is obvious to a human. Strip
   leading dashes before comparing.
* **A bounded cutoff.** Suggesting for ``--x`` is worse than saying nothing, so the
   similarity floor is 0.7 rather than difflib's default 0.6. A false suggestion
   costs the user more than a missing one: they stop reading the error and assume
   the tool is broken.
"""

from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher

__all__ = ["closest_match", "did_you_mean", "suggest_for_tokens"]

_CUTOFF = 0.7


def _normalise(candidate: str) -> str:
    return candidate.lstrip("-+/")


def _prefix_bonus(word: str, candidate: str) -> float:
    """Boost a candidate that is a prefix-extension of ``word``, or vice versa.

    difflib scores *contiguous* matching blocks, so it is blind to the single most
    common flag typo: truncating a long name. ``--outp`` against ``--output-file``
    shares no 4-character block, so plain ``get_close_matches`` returns nothing --
    even though the match is obvious to a human, who reads flags by prefix.

    So: a candidate that starts with the word (or that the word starts with) is a
    guaranteed suggestion, scored above anything difflib would produce.
    """
    short, long = sorted((word, candidate), key=len)
    if not short:
        return 0.0
    if long.startswith(short) and short != long:
        # Longer shared prefix => better. 0.90 upwards, so it outranks any real
        # difflib score while still sorting sensibly among itself.
        shared = 0
        for a, b in zip(short, long, strict=False):
            if a != b:
                break
            shared += 1
        return 0.90 + min(shared / max(len(long), 1), 0.09)
    return 0.0


def _transposition_bonus(word: str, candidate: str) -> float:
    """Score a candidate that becomes ``word`` after swapping one adjacent pair.

    difflib scores *contiguous* matching blocks, so it is systematically bad at the
    second most common CLI typo: two adjacent characters typed in the wrong order.
    ``bliud`` against ``build`` scores only 0.6 -- below any sane cutoff -- because
    the two matching runs are 1 and 2 characters long.

    The fix is to detect the transposition directly. This is the Damerau-Levenshtein
    insight (one edit means one swap) applied as a targeted bonus rather than a
    general edit-distance implementation: cheaper, and it composes with the ratio
    score rather than replacing it.
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
        # 0.95: above any prefix extension, because swapping is a stronger signal
        # of intent than truncating.
        return 0.95
    return 0.0


def closest_match(
    word: str,
    candidates: Iterable[str],
    *,
    n: int = 3,
    cutoff: float = _CUTOFF,
) -> list[str]:
    """Return up to ``n`` candidates closest to ``word``, best first.

    ::

        >>> closest_match("--verbse", ["--verbose", "--version", "--quiet"])
        ['--verbose']
        >>> closest_match("--outp", ["--output-file", "--quiet"])
        ['--output-file']

    Ordering matters for ties: difflib keeps the supplied order among equally
    similar candidates, so list the most common options first.
    """
    pool = list(candidates)
    if not pool:
        return []

    normalised = [(c, _normalise(c)) for c in pool]

    # difflib handles typos and transpositions...
    typo_scores: dict[str, float] = {}
    for _candidate, norm in normalised:
        ratio = SequenceMatcher(None, _normalise(word), norm).ratio()
        if ratio >= cutoff:
            typo_scores[norm] = ratio

    # ...and the two bonuses cover what difflib structurally cannot see.
    scores: dict[str, float] = {}
    for candidate, norm in normalised:
        scores[candidate] = max(
            typo_scores.get(norm, 0.0),
            _prefix_bonus(_normalise(word), norm),
            _transposition_bonus(_normalise(word), norm),
        )

    ranked = sorted(
        (c for c in scores if scores[c] >= cutoff),
        key=lambda c: (-scores[c], pool.index(c)),
    )
    return ranked[:n]


def did_you_mean(word: str, candidates: Iterable[str], *, limit: int = 1) -> str:
    """Format a suggestion, or an empty string when nothing is close enough.

    Empty-on-no-match lets callers append it unconditionally::

        parser.error(f"unknown task {name!r}.{did_you_mean(name, TASKS)}")
    """
    matches = closest_match(word, candidates, n=limit)
    if not matches:
        return ""
    if len(matches) == 1:
        return f" Did you mean {matches[0]!r}?"
    return " Did you mean one of " + ", ".join(repr(m) for m in matches) + "?"


def suggest_for_tokens(
    tokens: Iterable[str], options: Iterable[str], commands: Iterable[str]
) -> str:
    """Build suggestion lines for a batch of unrecognised tokens.

    Distinguishes flags from bare words, because the fix differs: a flag needs
    another flag, and a bare word needs a command.
    """
    option_list = list(options)
    command_list = list(commands)
    lines: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            hint = did_you_mean(token, option_list)
            if hint:
                lines.append(f"  argument {token!r}:{hint}")
        else:
            hint = did_you_mean(token, command_list)
            if hint:
                lines.append(f"  command {token!r}:{hint}")
    return "\n".join(lines)
