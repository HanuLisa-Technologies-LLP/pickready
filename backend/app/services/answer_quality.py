"""Deterministic substance check, run BEFORE an answer reaches a scoring prompt.

WHY THIS EXISTS
---------------
Typing `ewidjverip` into the assessment produced a report claiming the candidate
was "good at some skills" with "great projects". That was not the model
hallucinating. It was arithmetic:

* `ewidjverip` is not empty, so it never reached the unanswered path. It went to
  `_llm_score`.
* `_llm_score` returns None on ANY exception -- an outage, a bad key, a timeout.
  The caller then falls back to `_stable_score`, which hashes its seed into
  45..94. The grade therefore comes from a digest of the answer's own bytes and
  not from anything the answer says. Measured over 20,000 seeds against the
  90/75/60 cut-points: 69.6% grade Moderately Matching or better and 10.1%
  grade Highly Matching. A failing grade IS reachable (45..59, about three in
  ten) -- the defect is not that gibberish cannot fail, it is that whether it
  fails is decided by a hash.
* The remark then fell to `_fallback_remark_25` / `_fallback_remark_45`, which
  assert "Available evidence demonstrates dependable capability in <name>"
  unconditionally, with no reference to what the candidate actually wrote.

So with the LLM chain down, EVERY candidate scored 45..94 and was praised in
prose no model ever saw. The failure is fully deterministic and reproducible
offline, which is why it survived prompt tuning: no prompt runs on that path.

WHAT THIS MODULE DOES
---------------------
Answers the one question the scoring path never asked: is there anything here to
grade? An answer that fails is routed to the SAME unanswered path the product
already has -- UNANSWERED_SCORE, which is Not Matching, and `_unanswered_remark`,
which says plainly that no usable evidence was produced. Nothing new is invented
for this case; the honest output already existed and was simply unreachable.

WHY DETERMINISTIC, AND NOT A MODEL CALL
---------------------------------------
The bug is *triggered by* the model being unavailable. A guardrail that itself
needs the model would be absent in exactly the situation it exists to cover.
This runs in-process, always, and its verdict is a pure function of the text.

DELIBERATELY CONSERVATIVE
-------------------------
A false positive here silently grades a real candidate Not Matching, which is
far worse than letting a weak-but-real answer through to the rubric. Every
threshold is therefore set to catch obvious non-answers -- keyboard mash, a
single word, an empty gesture -- and to let anything resembling a sentence
proceed to be judged on its merits. "I have not worked with Kafka" is a REAL
answer, substantive by this module's definition, and is scored low by the rubric
rather than discarded here. This module decides only whether there is text worth
scoring, never whether the answer is correct or relevant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Verdict", "assess", "is_substantive"]

#: Below this many characters of real content, there is nothing to grade.
#: "Yes", "ok", "n/a" and a stray keysmash all land here.
MIN_CHARS = 12

#: A single token is never an answer to an interview question, however long.
#: This is what catches `ewidjverip`: ten characters, one token, no spaces.
MIN_WORDS = 3

#: Share of tokens that must look like language for the whole answer to count.
MIN_WORDLIKE_RATIO = 0.5

#: A run of this many consecutive consonants does not occur in English words
#: outside a handful of borrowings, but is the signature of a keyboard mash.
#: Six, not four: "strengths" has five ("ngths") and is an ordinary word that
#: appears constantly in behavioural answers.
MAX_CONSONANT_RUN = 6

_VOWELS = set("aeiouy")
_TOKEN = re.compile(r"[a-z0-9']+")
_CONSONANT_RUN = re.compile(r"[bcdfghjklmnpqrstvwxz]{%d,}" % (MAX_CONSONANT_RUN + 1))

#: Short function words are word-like by definition. Listing them stops the
#: vowel-ratio test from rejecting ordinary English: "the", "a", "in", "of".
_STOPWORDS = frozenset(
    "a an and are as at be but by do for from had has have i if in is it its me my "
    "no not of on or our so that the their them then there they this to us was we "
    "were what when which who will with would you your".split()
)


@dataclass(frozen=True)
class Verdict:
    """Why an answer was accepted or refused. `reason` is for the log, never
    for the candidate or the client: it names the heuristic that fired."""

    substantive: bool
    reason: str


def _is_wordlike(token: str) -> bool:
    """Could this token plausibly be language rather than keyboard noise?"""
    if token in _STOPWORDS:
        return True
    if token.isdigit():
        # A bare number is legitimate content in a technical answer (a version,
        # a throughput, a team size) and must not drag the ratio down.
        return True
    if len(token) < 2:
        return False
    letters = [character for character in token if character.isalpha()]
    if not letters:
        return False
    vowels = sum(1 for character in letters if character in _VOWELS)
    if vowels == 0:
        # No vowel at all: "ewidjverip" passes this, "qwrtplk" does not.
        return False
    ratio = vowels / len(letters)
    # Real words sit well inside these bounds. "rhythm" is saved by the 'y' in
    # _VOWELS; "aaaaaa" is caught by the upper bound.
    if not 0.15 <= ratio <= 0.75:
        return False
    if _CONSONANT_RUN.search(token):
        return False
    return True


def assess(answer: str | None) -> Verdict:
    """Is there content here worth sending to a rubric?

    Order matters: the cheap structural tests run first, so an empty or
    one-word answer never reaches the per-token analysis.
    """
    text = (answer or "").strip()
    if not text:
        return Verdict(False, "empty")

    tokens = _TOKEN.findall(text.casefold())
    if not tokens:
        # Punctuation or symbols only: "...", "???", "-".
        return Verdict(False, "no_tokens")

    if len(tokens) < MIN_WORDS:
        return Verdict(False, f"too_few_words:{len(tokens)}")

    # Measured on tokens, not the raw string, so whitespace padding cannot buy
    # an answer its way past the length floor.
    if sum(len(token) for token in tokens) < MIN_CHARS:
        return Verdict(False, "too_short")

    wordlike = sum(1 for token in tokens if _is_wordlike(token))
    ratio = wordlike / len(tokens)
    if ratio < MIN_WORDLIKE_RATIO:
        return Verdict(False, f"not_wordlike:{wordlike}/{len(tokens)}")

    return Verdict(True, "substantive")


def is_substantive(answer: str | None) -> bool:
    """Convenience wrapper for call sites that do not log the reason."""
    return assess(answer).substantive
