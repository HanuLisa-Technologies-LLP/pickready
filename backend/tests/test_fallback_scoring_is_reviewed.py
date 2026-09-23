"""A hash-scored report is always flagged for a human. It was not.

`claude.md` has carried this rule since the agent framework landed: "A stub is
always flagged for human review ... what makes that honest rather than
misleading is `needs_human_review`, never a stub that reads like a result."

The deterministic fallback IS that stub. `_stable_score` hashes into 45..94,
a range that cannot express Not Matching at all, and measured over 20,000 seeds
it graded 69.6% of inputs Moderately Matching or better. `scoring_mode` was
computed, logged and STORED on the row, and none of that reached the flag. A
provider outage therefore produced a report that looked scored, carried a
plausible grade, and reached a client with nothing asking anyone to look.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.services import functional_assessment as fa

SOURCE = Path(inspect.getfile(fa)).read_text(encoding="utf-8")


def test_the_known_good_mode_is_a_named_constant() -> None:
    """Compared against a NAME, never a repeated string literal.

    `needs_human_review` tests equality against this value. A typo in a literal
    at that site would silently stop flagging every fallback report, and would
    look correct in a diff.
    """
    assert fa.MODE_LLM_RUBRIC == "llm_rubric"


def test_needs_human_review_reacts_to_the_scoring_mode() -> None:
    """The assertion that would have caught the gap.

    Read from SOURCE rather than by running the graph: reaching this field for
    real needs a job, a frozen matrix, a candidate, a transcript and a
    provider, and a test that heavy would have been written later, which is
    exactly why it was never written at all.
    """
    tree = ast.parse(SOURCE)
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "needs_human_review":
                    found = value
    assert found is not None, "the needs_human_review field is gone or renamed"

    expression = ast.unparse(found)
    assert "scoring_mode" in expression, (
        "needs_human_review does not consider scoring_mode, so a report scored "
        "by a hash during a provider outage reaches a client unflagged"
    )
    assert "MODE_LLM_RUBRIC" in expression, (
        "the comparison must be against the one KNOWN-GOOD mode, not against a "
        "list of bad ones: a third mode added later is unreviewed by default "
        "under `!= fallback` and reviewed by default under `!= llm_rubric`"
    )


def test_the_comparison_is_against_the_good_mode_not_the_bad_one() -> None:
    """Direction matters, and it is the whole reason the constant is the good one.

    `!= MODE_LLM_RUBRIC` means every mode that is not a real rubric run is
    reviewed, including one nobody has invented yet. `== "deterministic_fallback"`
    would review only the failure somebody remembered to enumerate.
    """
    assert "scoring_mode != MODE_LLM_RUBRIC" in SOURCE
    assert 'scoring_mode == "deterministic_fallback"' not in SOURCE


def test_the_hash_grades_most_inputs_as_a_pass() -> None:
    """The measured reason a fallback report must never stand unreviewed.

    A COMMENT IN THE SOURCE CLAIMED THE 45..94 RANGE "cannot express Not
    Matching", and writing this test is what showed that to be false: on the
    current four-grade scale (90 / 75 / 60) roughly 30% of hashed inputs DO
    grade Not Matching. The claim was true of an earlier scale and survived the
    2026-07-30 consolidation unread. Both the comment and this test now state
    the measured distribution instead.

    The defect is unchanged and the numbers say it better than the wrong claim
    did: most hashed inputs pass. Keyboard mash reaching this path is far more
    likely to be graded acceptable than to be caught, and the real fault is not
    that a hash cannot fail somebody, it is that a HASH decides.
    """
    from app.services.rating import grade_for_percent

    scores = [fa._stable_score(f"seed-{i}") for i in range(20000)]
    assert min(scores) >= 45
    assert max(scores) <= 94

    grades = [grade_for_percent(s) for s in scores]
    passing = sum(1 for g in grades if g != "Not Matching") / len(grades)
    assert passing > 0.6, (
        "the hash no longer grades most inputs as a pass. That is a real "
        "change to the fallback's behaviour and the review rule's "
        "justification should be re-read rather than assumed."
    )
    # And it reaches the top band, which is the most damaging single outcome:
    # a provider outage handing an unassessed candidate the best grade.
    assert "Highly Matching" in set(grades)
