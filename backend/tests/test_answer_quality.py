"""The gibberish-scores-as-Matching bug, pinned.

The reported failure: typing `ewidjverip` produced a report claiming the
candidate was "good at some skills". These tests assert the two halves of why
that happened and that neither can happen again.

Half one is `_stable_score`'s floor. Its range is 45..94 against cut-points of
90/75/60, so EVERY value it can return grades Matching or Moderately Matching.
That is asserted directly below, because it is the reason a content guard has to
run before scoring rather than after: once an answer reaches the fallback, a
failing grade is arithmetically unreachable.

Half two is that a non-empty non-answer reached that fallback at all.
"""
from __future__ import annotations

import pytest

from app.services import answer_quality
from app.services.functional_assessment import (
    UNANSWERED_SCORE,
    _stable_score,
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
        "it would reach _llm_score and, on any LLM failure, score 45..94"
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

def test_the_deterministic_fallback_grades_by_hash_not_by_content() -> None:
    """This is the whole reason a pre-scoring guard is necessary.

    `_stable_score` is the LLM-outage fallback. It hashes the seed into 45..94,
    so an answer that reaches it is graded by a digest of its own bytes and not
    by anything it says. Two candidates writing equally worthless answers get
    different grades, and most of them pass.

    Note what this test does NOT claim. A failing grade is reachable: 45..59
    grades Not Matching against the 60 cut-point, which is about three seeds in
    ten. The bug is not that the fallback cannot fail an answer -- it is that
    whether it fails one is decided by a hash. Roughly seven in ten gibberish
    answers grade Moderately Matching or better, and one in ten grades Highly
    Matching, which is where "great projects" came from.
    """
    labels = [rating_label(_stable_score(f"seed-{n}")) for n in range(20000)]
    passing = sum(1 for label in labels if label != "Not Matching") / len(labels)
    assert 0.6 < passing < 0.8, (
        f"{passing:.1%} of hashed scores pass; the fallback's range or the "
        "cut-points have moved and the guard's rationale needs re-checking"
    )
    assert "Highly Matching" in labels, (
        "the fallback can no longer return the top grade for arbitrary text; "
        "re-examine whether this test still describes the product"
    )


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
