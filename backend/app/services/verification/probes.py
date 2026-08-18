"""Critic for Gap Analysis probes: are they about this candidate's actual gaps.

WHAT A PROBE IS FOR
-------------------
A probe is a prompt handed to a human interviewer about a criterion this
candidate did NOT clear. Its whole value is that the interviewer walks in
already knowing where to push. Three ways that value is destroyed, and this
critic checks all three:

  Asking about the wrong thing. A probe attached to a criterion the candidate
  cleared is interview time spent confirming what the report already said.
  Gaps are Moderately Matching or Not Matching, and `gap_analysis` decides that;
  this critic checks the probe was written for one.

  Asking it ungrounded. "Tell me about your experience with distributed
  systems" could be generated without reading a single word the candidate
  wrote. A probe that shares no vocabulary with the answer it is probing was
  not written from the answer, whatever the prompt asked for.

  Asking it again. A probe that repeats a question the assessment already put
  to the candidate will get the same answer it already got.

THE COUNTS ARE NOT A PREFERENCE
-------------------------------
`gap_analysis.probe_count_for` gives a Not Matching Must-have two probes and
everything else one, because a Not Matching Must-have is what caps the Overall
grade and one probe is not enough interview time to resolve it. This critic
imports that function rather than restating the numbers.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services import conversation_guardrails, gap_analysis, rating
from app.services.verification import base, generic_language

_PROBE_MIN, _PROBE_MAX = gap_analysis.PROBE_WORDS

EM_DASH = chr(8212)

#: How much vocabulary a probe must share with the answer it probes. Low on
#: purpose: a probe legitimately introduces words the candidate never used --
#: that is what makes it a probe rather than an echo -- so this is a floor
#: against a probe that could have been written without reading anything, not a
#: demand that it paraphrase.
_MIN_GROUNDING_TERMS = 2

#: Words too common to count as evidence that a probe read anything.
_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has
    have how i if in into is it its me my no not of on or our so than that the
    their them then there these they this to was we were what when where which
    who why will with would you your""".split()
)


def _terms(text: str) -> set[str]:
    return {
        word
        for word in "".join(
            char if char.isalnum() else " " for char in str(text or "").casefold()
        ).split()
        if len(word) > 2 and word not in _STOPWORDS
    }


def verify_probe(
    probe: str,
    *,
    location: str,
    answer: str | None = None,
    asked_questions: Sequence[str] = (),
) -> base.Verdict:
    """Verify one probe against the answer it is supposed to be probing."""
    text = str(probe or "").strip()
    if not text:
        return base.verdict(
            "probe",
            [
                base.high(
                    "missing_probe",
                    location,
                    "the probe is empty",
                    f"write a {_PROBE_MIN}-{_PROBE_MAX} word question",
                )
            ],
        )

    findings: list[base.Finding] = []

    count = base.words_in(text)
    if not _PROBE_MIN <= count <= _PROBE_MAX:
        findings.append(
            base.medium(
                "probe_word_count",
                location,
                f"the probe is {count} words",
                f"rewrite it as a complete question of {_PROBE_MIN}-{_PROBE_MAX} words",
            )
        )

    if not text.rstrip().endswith("?"):
        findings.append(
            base.medium(
                "probe_not_a_question",
                location,
                "the probe does not read as a question an interviewer can ask",
                "phrase the probe as a direct question ending in a question mark",
            )
        )

    if EM_DASH in text:
        findings.append(
            base.high(
                "em_dash",
                location,
                "the probe contains an em dash",
                "replace the em dash with a comma, a colon or a full stop",
            )
        )

    if conversation_guardrails.contains_forbidden_number(text):
        findings.append(
            base.high(
                "number_leaked",
                location,
                "the probe states a score, percentage, rank or band",
                "remove the number; a probe never tells the interviewer how the "
                "candidate scored",
            )
        )

    if answer:
        shared = _terms(text) & _terms(answer)
        if len(shared) < _MIN_GROUNDING_TERMS:
            findings.append(
                base.high(
                    "probe_not_grounded",
                    location,
                    (
                        "the probe shares almost no vocabulary with the answer "
                        "it is probing, so it was not written from that answer"
                    ),
                    (
                        "quote or name something the candidate actually said, "
                        "then ask what it leaves unresolved"
                    ),
                )
            )

    for asked in asked_questions:
        if _terms(asked) and _terms(text) >= _terms(asked):
            findings.append(
                base.medium(
                    "probe_repeats_question",
                    location,
                    "the probe restates a question the assessment already asked",
                    (
                        "ask something the assessment did not: the decision "
                        "behind the answer, its trade-off, or what they would "
                        "change"
                    ),
                )
            )
            break

    findings.extend(generic_language.findings(text, location=location))
    return base.verdict("probe", findings)


def verify_group(
    *,
    category: str,
    item: dict[str, Any],
    probes: Sequence[str],
    answer: str | None = None,
    asked_questions: Sequence[str] = (),
) -> base.Verdict:
    """Verify every probe written for one gap item, and how many there are."""
    grade = rating.grade_for_percent(item.get("score"))
    findings: list[base.Finding] = []

    if grade not in gap_analysis.GAP_GRADES:
        findings.append(
            base.high(
                "probe_on_a_non_gap",
                f"{category}.{item.get('name') or 'item'}",
                f"{item.get('name')!r} graded {grade}, which is not a gap",
                "write probes only for criteria graded Moderately Matching or "
                "Not Matching",
            )
        )

    expected = gap_analysis.probe_count_for(category, grade)
    if len(probes) != expected:
        findings.append(
            base.medium(
                "probe_count",
                f"{category}.{item.get('name') or 'item'}",
                f"{len(probes)} probes were written where {expected} is required",
                f"write exactly {expected} probe(s) for this item",
            )
        )

    for index, probe in enumerate(probes):
        findings.extend(
            verify_probe(
                probe,
                location=f"{category}.{item.get('name') or 'item'}.probes[{index}]",
                answer=answer,
                asked_questions=asked_questions,
            ).findings
        )

    return base.verdict("probe_group", findings)


def verify_ordering(items: Sequence[dict[str, Any]]) -> base.Verdict:
    """Not Matching must be read before Moderately Matching, within a group.

    The section is a priority list for somebody with a fixed hour of interview
    time. Ordered wrongly it is still accurate and no longer a priority list,
    which is a failure that looks exactly like success.
    """
    order = {rating.GRADE_NOT: 0, rating.GRADE_MODERATELY: 1}
    ranks = [
        order.get(str(rating.grade_for_percent(item.get("score"))), 2)
        for item in items
    ]
    if ranks == sorted(ranks):
        return base.verdict("probe_ordering", [])
    return base.verdict(
        "probe_ordering",
        [
            base.medium(
                "gap_order",
                "gap_items",
                "Moderately Matching items are listed before Not Matching ones",
                f"list every {rating.GRADE_NOT} item before any "
                f"{rating.GRADE_MODERATELY} item",
            )
        ],
    )
