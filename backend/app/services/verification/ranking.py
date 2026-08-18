"""Critic for the AI Score: the four matching parameters and their remarks.

WHAT IT DELIBERATELY DOES NOT CHECK
-----------------------------------
The specification this package implements asks the ranking critic to assert
that "weights sum to 1.0". There are no weights. The 0.35 / 0.30 / 0.20 / 0.15
table was deleted on 2026-07-30 and `tests/test_scoring.py` asserts that
`matching` has no `WEIGHTS` symbol at all, for two reasons that have not
changed: the weighting was rendered to the client as a percentage beside each
remark, which is a number reaching a client, and a fixed weighting claims that
skills matter 2.3x more than education for every role in the product. Adding
the check back would require adding the concept back.

What replaces it is the check the weighting was standing in for -- that the
ranking DISCRIMINATES. A ranked list whose top five remarks are paraphrases of
one another has not ranked anything, and unlike a weight sum, that is a
property you can read off the output.

WHERE THE THRESHOLDS COME FROM
------------------------------
`matching.COMMENT_MIN_WORDS` / `COMMENT_MAX_WORDS` and
`matching_categories.LEGACY_KEYS`, imported rather than restated. A verifier
carrying its own copy of a contract is a verifier that will one day pass an
output the writer already considers invalid.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services import agent_loop, conversation_guardrails, matching, matching_categories
from app.services.verification import base, generic_language

#: An internal parameter score is an integer 1-10 before it ever becomes a word.
_SCORE_MIN = 1
_SCORE_MAX = 10

#: Above this, two remarks in one ranked list are saying the same thing. Same
#: mechanism `agent_loop.similarity_gate` applies inside the generators; the
#: threshold is looser here because this compares remarks about DIFFERENT people
#: on the same job, which legitimately share vocabulary.
_MAX_PAIRWISE_SIMILARITY = 0.9

#: How far down a ranked list the diversity check reaches. Five, because five is
#: what a recruiter actually reads, and a list that discriminates at the top is
#: doing its job even if positions 40 and 41 are hard to tell apart.
_DIVERSITY_DEPTH = 5


def verify_entry(entry: dict[str, Any], *, location: str = "entry") -> base.Verdict:
    """Verify one candidate's four-parameter score and its remarks."""
    findings: list[base.Finding] = []

    for key in matching_categories.LEGACY_KEYS:
        value = entry.get(key)
        if not isinstance(value, dict):
            findings.append(
                base.high(
                    "missing_parameter",
                    f"{location}.{key}",
                    f"{key} is absent or is not an object",
                    f"return an object for {key} carrying a score and a comment",
                )
            )
            continue
        findings.extend(_score_findings(value.get("score"), f"{location}.{key}"))
        findings.extend(
            _comment_findings(value.get("comment"), f"{location}.{key}.comment")
        )

    findings.extend(_comment_findings(entry.get("overall_comment"), f"{location}.overall_comment"))
    return base.verdict("ranking_entry", findings)


def _score_findings(score: Any, location: str) -> list[base.Finding]:
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return [
            base.high(
                "missing_score",
                location,
                "score is absent or not a number",
                f"return an integer between {_SCORE_MIN} and {_SCORE_MAX} for score",
            )
        ]
    if not _SCORE_MIN <= score <= _SCORE_MAX:
        return [
            base.high(
                "score_out_of_range",
                location,
                f"score {score} is outside {_SCORE_MIN}-{_SCORE_MAX}",
                f"return a score between {_SCORE_MIN} and {_SCORE_MAX}",
            )
        ]
    return []


def _comment_findings(comment: Any, location: str) -> list[base.Finding]:
    text = str(comment or "").strip()
    if not text:
        return [
            base.high(
                "missing_comment",
                location,
                "the comment is empty",
                (
                    f"write a comment of {matching.COMMENT_MIN_WORDS}-"
                    f"{matching.COMMENT_MAX_WORDS} words"
                ),
            )
        ]

    findings: list[base.Finding] = []
    count = base.words_in(text)
    if not matching.COMMENT_MIN_WORDS <= count <= matching.COMMENT_MAX_WORDS:
        findings.append(
            base.medium(
                "comment_word_count",
                location,
                f"the comment is {count} words",
                (
                    "rewrite it as complete sentences of "
                    f"{matching.COMMENT_MIN_WORDS}-{matching.COMMENT_MAX_WORDS} "
                    "words; do not truncate one to fit"
                ),
            )
        )

    # A client reads this remark. The oldest standing rule in the product is
    # that no number reaches them, and the guard is the same one the
    # conversation path uses so a score cannot leak through a second door.
    if conversation_guardrails.contains_forbidden_number(text):
        findings.append(
            base.high(
                "number_leaked",
                location,
                "the comment states a score, percentage or rank",
                "state the assessment in words only; remove every score, "
                "percentage, rank and band from the comment",
            )
        )

    findings.extend(generic_language.findings(text, location=location))
    return findings


def verify_ranked_list(
    entries: Sequence[dict[str, Any]], *, depth: int = _DIVERSITY_DEPTH
) -> base.Verdict:
    """Verify a whole ranked list: every entry, plus whether it discriminates.

    The list-level check is the one that cannot be made per entry. Each of five
    remarks can be individually well-formed while all five say the same thing,
    and that list has told the recruiter nothing about which of the five to
    interview first.
    """
    findings: list[base.Finding] = []
    for index, entry in enumerate(entries):
        findings.extend(verify_entry(entry, location=f"entries[{index}]").findings)

    top = [str(entry.get("overall_comment") or "") for entry in entries[:depth]]
    populated = [text for text in top if text.strip()]
    if len(populated) > 1:
        critique = agent_loop.similarity_gate(
            populated, maximum=_MAX_PAIRWISE_SIMILARITY, location="top_comments"
        )
        findings.extend(
            base.medium(
                "ranking_lacks_diversity",
                defect.location,
                defect.detail,
                (
                    "rewrite each remark around what distinguishes THAT "
                    "candidate: the systems they named, the scale they worked "
                    "at, the gap the resume leaves open"
                ),
            )
            for defect in critique.defects
        )

    return base.verdict("ranking", findings)
