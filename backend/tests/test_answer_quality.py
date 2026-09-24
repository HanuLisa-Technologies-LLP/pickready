"""The gibberish-scores-as-Matching bug, pinned.

The reported failure: typing `ewidjverip` produced a report claiming the
candidate was "good at some skills". These tests assert the two halves of why
that happened and that neither can happen again.

Half one was the hash fallback: on an outage, an answer was graded by a
digest of its own bytes, and about seven in ten hashed answers passed. It is
DELETED (WP5-B): a failed evaluation is now "not assessed" with no score at all,
and `tests/test_hash_fallback_removed.py` keeps it gone.

Half two is that a non-empty non-answer reached a scorer at all. That guard is
unchanged and is what this file still pins: a non-answer never reaches a
scoring prompt, and it grades Not Matching as unanswered.
"""
from __future__ import annotations

import pytest

from app.services import answer_quality
from app.services.functional_assessment import (
    UNANSWERED_SCORE,
    rating_label,
)


# ── The reported input, and its neighbours ──────────────────────────────────

@pytest.mark.parametrize(
    "answer",
    [
        "ewidjverip",              # the exact reported input
        "",                        # nothing at all
        "   \n\t  ",               # whitespace only
        "...",                     # punctuation only
        "???",
        "ok",                      # an acknowledgement, not an answer
        "yes",
        "n/a",
        "asdfghjkl",               # keyboard mash, one token
        "qwrtplk zxcvbn hjklmn",   # three tokens, no vowels in any of them
        "aaaaaa aaaaaa aaaaaa",    # vowel ratio above the ceiling
    ],
)
def test_non_answers_are_refused(answer: str) -> None:
    verdict = answer_quality.assess(answer)
    assert not verdict.substantive, (
        f"{answer!r} was accepted as gradeable (reason={verdict.reason}); "
        "it would reach a scoring prompt and be judged as though it answered"
    )


# ── The far more dangerous direction: refusing a REAL answer ────────────────

@pytest.mark.parametrize(
    "answer",
    [
        # A negative answer is a real answer. It must be graded low by the
        # rubric, never discarded here -- discarding it and grading it Not
        # Matching would reach the same score by a dishonest route.
        "I have not worked with Kafka before.",
        "No, I have never used that tool in production.",
        "I built the payments service using Python and Postgres, and I owned "
        "the migration off the legacy queue.",
        "We ran a team of six. I set the roadmap and handled stakeholder "
        "reviews every fortnight.",
        # Short but real.
        "I led the rewrite.",
        # Heavy on jargon and numbers, light on prose.
        "Scaled to 40k rps across 12 nodes with p99 under 80ms.",
        # Words with long consonant runs and unusual vowel ratios.
        "My strengths are rhythm and depth in system design work.",
    ],
)
def test_real_answers_are_accepted(answer: str) -> None:
    verdict = answer_quality.assess(answer)
    assert verdict.substantive, (
        f"{answer!r} was refused as gibberish (reason={verdict.reason}); "
        "a false positive here silently grades a real candidate Not Matching"
    )


# ── Why the guard must run BEFORE scoring, not after ────────────────────────

def test_the_hash_fallback_no_longer_exists() -> None:
    """The fallback that graded an answer by a hash of its bytes is gone
    (WP5-B). An outage now makes a skill "not assessed", with no score."""
    from app.services import functional_assessment

    assert not hasattr(functional_assessment, "_stable_score")


def test_unanswered_score_is_the_honest_destination() -> None:
    """Refused answers route to UNANSWERED_SCORE, which must grade Not
    Matching. If someone raises it above the 60 cut-point, gibberish silently
    starts passing again through a different door."""
    assert rating_label(UNANSWERED_SCORE) == "Not Matching"


def test_reason_is_reported_for_the_log() -> None:
    """The reason names the heuristic that fired. It is logged, never shown to
    a candidate or a client."""
    assert answer_quality.assess("ewidjverip").reason.startswith("too_few_words")
    assert answer_quality.assess("").reason == "empty"
    assert answer_quality.assess("qwrtplk zxcvbn hjklmn").reason.startswith("not_wordlike")
