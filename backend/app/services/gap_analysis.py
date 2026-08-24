"""Gap Analysis & Action Plan (spec §9.6).

WHAT THIS REPLACED
------------------
"Suggested interview questions": eight to ten probes in a flat list, anchored on
whatever graded Moderately Matching or below, generated from the item REMARKS
alone. Draft v4 replaces it entirely. Nothing about gaps or probes appears
anywhere else in the report.

The three things that changed, and why each one matters:

1. **It is grouped by aspect, not flat.** Must-have, Nice-to-have, Behavioural,
   in that order, using the same grouping the rest of the report already uses
   rather than a new taxonomy. Must-have is read first because it is the aspect
   the hard cap actually governs.

2. **A probe is grounded in what the candidate SAID.** The old probes were
   written from a remark, which is itself a summary, so a probe could only ever
   restate the assessment back at the interviewer. This one receives the
   original question and the candidate's own answer, and the locked format makes
   the grounding visible: reference their specific claim, then go deeper on it.

3. **The item's remark is REUSED, never rewritten.** The report states one
   assessment of an item; a second, differently-worded assessment of the same
   item in the same document is not extra information, it is a contradiction
   waiting to happen.

THE CAP IS STATED, NOT IMPLIED
------------------------------
If any Must-have item graded Not Matching, the section says so in words, at the
top of the Must-have group. The alternative is a reader noticing an Overall
Grade of Moderately Matching beside strong individual grades and having to work
out why -- and the whole point of writing the rule down is that nobody should
have to.

EM DASHES
---------
The probe format in the specification is written with an em dash. The product
forbids em dashes in any string in either language, so the structure is kept and
the punctuation is not: "You mentioned X, [follow-up]". The prompt says so, and
`_clean_probe` strips one if a model writes it anyway, because a prompt
instruction is a request rather than a guarantee.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.prompts import registry
from app.services import agent_loop, conversation_guardrails, llm_router, ppi
from app.services.rating import (
    GRADE_MODERATELY,
    GRADE_NOT,
    MODERATE_OR_BELOW,
    grade_for_percent,
)

logger = logging.getLogger(__name__)

__all__ = [
    "gap_order",
    "PROBE_WORDS",
    "build_gap_analysis",
    "gap_items",
    "must_have_cap_applies",
    "probe_count_for",
]

#: A gap is an item graded Moderately Matching or Not Matching. The rule is the
#: same for all three aspects (spec §9.6, "Probe count Dynamically"); what
#: differs between aspects is how many probes a gap earns, not whether it is one.
GAP_GRADES = MODERATE_OR_BELOW

#: The order the three groups are read in. Must-have first, because it is the
#: aspect the hard cap governs.
#:
#: A FUNCTION, not a constant. `ppi` is not a leaf and this module sits on a
#: cycle with it, so reading `ppi.CATEGORIES` while this module is being
#: imported is an `AttributeError` the moment that cycle is entered from the
#: other side. It already was: the order is still `ppi`'s and still defined in
#: exactly one place, it is simply read when asked for.
def gap_order() -> tuple[str, ...]:
    return tuple(ppi.CATEGORIES)

#: A probe is a prompt for the interviewer, not a written assessment, and is
#: capped shorter than an item's 45-50 word remark for exactly that reason.
PROBE_WORDS = (25, 30)

#: How many probes a gap earns. A Not Matching Must-have is the single most
#: consequential thing in the report -- it is what caps the Overall Grade -- and
#: one probe is not enough interview time to resolve it. Everything else gets
#: one, because a section that gives every gap three probes gives an interviewer
#: a list too long to use and is therefore no prioritisation at all.
def probe_counts() -> dict[tuple[str, str], int]:
    """Same reason as `gap_order`: keyed by a `ppi` constant, read on demand."""
    return {(ppi.CATEGORY_MUST_HAVE, GRADE_NOT): 2}
DEFAULT_PROBE_COUNT = 1


def probe_count_for(category: str, grade: str | None) -> int:
    return probe_counts().get((category, str(grade)), DEFAULT_PROBE_COUNT)


def must_have_cap_applies(dimensions: list[dict[str, Any]]) -> bool:
    """Whether any Must-have item graded Not Matching (spec §5.5).

    The single condition behind the hard cap, in one place, so the arithmetic in
    synthesis and the sentence in the report cannot disagree about whether it
    fired.
    """
    return any(
        row.get("category") == ppi.CATEGORY_MUST_HAVE
        and grade_for_percent(row.get("score")) == GRADE_NOT
        for row in dimensions
    )


def gap_items(dimensions: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    """This aspect's gaps, Not Matching before Moderately Matching (spec §9.6).

    Sorted by score within each grade so the worst gap in a band leads it. The
    grade is the primary key rather than the score alone because the two can
    disagree at a band edge, and the spec orders by GRADE.
    """
    rows = [
        row
        for row in dimensions
        if row.get("category") == category
        and grade_for_percent(row.get("score")) in GAP_GRADES
    ]
    order = {GRADE_NOT: 0, GRADE_MODERATELY: 1}
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(grade_for_percent(row.get("score"))), 2),
            row.get("score", 0),
            row.get("ordinal", 0),
        ),
    )


# ── Probe generation ─────────────────────────────────────────────────────────

_EM_DASH = chr(8212)


def _clean_probe(text: str) -> str:
    """One probe, normalised.

    The em dash is replaced rather than the probe rejected: the product's rule
    is that no string contains one, and a probe that is otherwise good does not
    need to be thrown away over punctuation a substitution can fix. Built from
    `chr(8212)` so a repo-wide em dash sweep cannot rewrite the code that strips
    it.
    """
    cleaned = " ".join(str(text or "").split())
    return cleaned.replace(f" {_EM_DASH} ", ", ").replace(_EM_DASH, ", ")


def _words(text: str) -> int:
    return len(re.findall(r"\b[\w&'-]+\b", text))


def _terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9+#./-]{4,}", text.casefold())
        if word
        not in {
            "have", "with", "that", "this", "your", "when", "what", "which",
            "would", "there", "their", "about", "been", "were", "from", "into",
            "describe", "explain", "walk", "through", "give", "tell", "mentioned",
        }
    }


def _repeats_question(probe: str, questions: list[str]) -> bool:
    """Whether a probe is just the original question again.

    The threshold is lower than the one `ppi_interview` uses for repeated
    questions (0.6 against 0.8) and deliberately so. There, several probes of one
    item SHOULD share vocabulary. Here the requirement is the opposite: the
    interviewer is going somewhere NEW with an answer already given, so heavy
    overlap with the question that produced it is the defect itself.
    """
    probe_terms = _terms(probe)
    if not probe_terms:
        return False
    for question in questions:
        question_terms = _terms(question)
        if not question_terms:
            continue
        if len(probe_terms & question_terms) / max(len(probe_terms | question_terms), 1) > 0.6:
            return True
    return False


def _fallback_probes(item: dict[str, Any], evidence: list[dict[str, str]], count: int) -> list[str]:
    """Deterministic probes, used when generation is unavailable.

    Still grounded, and that is what makes them worth having: they quote the
    opening of what the candidate actually said about this item. A probe that
    invented a claim would be worse than no probe, and a purely generic one
    ("ask about X") is what the section was built to stop producing.
    """
    said = next((row["answer"] for row in evidence if row.get("answer")), "")
    snippet = " ".join(said.split())[:90].rstrip(" ,.;:")
    if snippet:
        angles = (
            f"You mentioned {snippet}, so take me through what you personally "
            "decided at that point and what the outcome actually was afterwards.",
            f"You mentioned {snippet}, so tell me what you would do differently "
            "now and what specifically changed your view since then.",
        )
    else:
        # No usable answer to quote. Probing the thinness itself is honest and
        # is what the prompt instructs the model to do in the same situation;
        # inventing a claim to reference would be the one unacceptable option.
        name = item["name"]
        angles = (
            f"You said little about {name}, so walk me through one worked example "
            "in detail, what you personally did, and how you knew it worked.",
            f"You said little about {name}, so tell me where you have come "
            "closest to it and what stopped you going further with it.",
        )
    return [_clean_probe(angle) for angle in angles[:count]]


async def _write_probes(
    session: AsyncSession | None,
    item: dict[str, Any],
    category: str,
    grade: str,
    evidence: list[dict[str, str]],
    count: int,
) -> list[str]:
    """Generate this gap's probes through the bounded loop. Never raises."""
    fallback = _fallback_probes(item, evidence, count)
    asked = [row["question"] for row in evidence if row.get("question")]
    system = registry.render(
        "report_gap_probes",
        item_name=item["name"],
        aspect=ppi.CATEGORY_LABELS.get(category, category),
        grade=grade,
        probe_words=f"{PROBE_WORDS[0]} to {PROBE_WORDS[1]}",
        count=count,
    )
    payload = json.dumps(
        {
            "item_remark": item.get("remark"),
            "what_the_candidate_was_asked_and_answered": evidence,
        },
        ensure_ascii=False,
    )

    async def execute(reflection: str) -> list[str]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.chat_completion(
            "report_synthesis", messages, response_format_json=True, session=session
        )
        probes = [
            _clean_probe(value)
            for value in json.loads(raw).get("probes", [])
            if str(value).strip()
        ]
        if not probes:
            raise ValueError("no probes returned")
        return probes[:count]

    def evaluate(probes: list[str]) -> agent_loop.Critique:
        defects: list[agent_loop.Defect] = []
        if len(probes) != count:
            defects.append(
                agent_loop.Defect(
                    "count",
                    "gap_probes",
                    f"return exactly {count} probe(s); the previous attempt "
                    f"returned {len(probes)}",
                )
            )
        for index, probe in enumerate(probes):
            location = f"gap_probes[{index}]"
            length = _words(probe)
            if not PROBE_WORDS[0] <= length <= PROBE_WORDS[1]:
                defects.append(
                    agent_loop.Defect(
                        "word_count",
                        location,
                        f"write {PROBE_WORDS[0]} to {PROBE_WORDS[1]} words; the "
                        f"previous attempt was {length}",
                    )
                )
            if conversation_guardrails.contains_forbidden_number(probe):
                defects.append(
                    agent_loop.Defect(
                        "numeric_score",
                        location,
                        "remove scores, percentages, ratings out of a total, and percentiles",
                    )
                )
            if _repeats_question(probe, asked):
                defects.append(
                    agent_loop.Defect(
                        "repeats_question",
                        location,
                        "this repeats the wording of a question the candidate was "
                        "already asked; go somewhere new with the answer they gave",
                    )
                )
        return agent_loop.reject_defects(*defects) if defects else agent_loop.ok()

    result = await agent_loop.run_loop(
        name="report_gap_probes",
        execute=execute,
        evaluate=evaluate,
        fallback=fallback,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded:
        logger.info(
            "gap_analysis.probes_degraded item=%s reasons=%s",
            item.get("name"), list(result.reasons),
        )
    return result.value or fallback


# ── The section ──────────────────────────────────────────────────────────────


def _no_gaps_statement(category: str) -> str:
    """Said in words rather than left as blank space (spec §9.6)."""
    label = ppi.CATEGORY_LABELS.get(category, category)
    if category == ppi.CATEGORY_BEHAVIOURAL:
        return "No behavioural gaps identified."
    return f"No {label} gaps identified."


def _cap_statement(names: list[str]) -> str:
    joined = ", ".join(names)
    return (
        "Overall Grade capped at Moderately Matching due to the Must-have "
        f"gap(s) below: {joined}."
    )


def _focus_summary(ordered_gaps: list[tuple[str, dict[str, Any]]]) -> str:
    """One sentence naming the one or two items most worth interview time.

    Drawn from the gap list immediately below it rather than generated, because
    the sentence's only job is to name the top of a list the reader is about to
    see. A generated sentence could name something else, and then the summary
    and the list would disagree in the same screenful.
    """
    if not ordered_gaps:
        return (
            "No gaps were identified against this job's matrix; interview time is "
            "best spent confirming the strongest evidence in the assessment."
        )
    top = ordered_gaps[:2]
    names = [item["name"] for _, item in top]
    aspects = {category for category, _ in top}
    where = (
        ppi.CATEGORY_LABELS[next(iter(aspects))]
        if len(aspects) == 1
        else "the matrix"
    )
    if len(names) == 1:
        return f"Focus the interview on {names[0]}, the clearest gap in {where}."
    return (
        f"Focus the interview on {names[0]} and {names[1]}, the two clearest "
        f"gaps in {where}."
    )


async def build_gap_analysis(
    session: AsyncSession | None,
    dimensions: list[dict[str, Any]],
    evidence_by_item: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """The whole section, ready to render (spec §9.6).

    `evidence_by_item` maps an item's NAME to the questions it was probed with
    and the answers the candidate gave. Keyed on the name rather than the
    competency id because a report row is the permanent record and its name is
    what survives an edit to the job's matrix.

    Never raises. Every probe generation degrades to a deterministic, still
    grounded probe, so a provider outage costs the section its polish and not
    its structure.
    """
    cap_applied = must_have_cap_applies(dimensions)
    groups: list[dict[str, Any]] = []
    ordered_gaps: list[tuple[str, dict[str, Any]]] = []

    for category in gap_order():
        items = gap_items(dimensions, category)
        ordered_gaps.extend((category, item) for item in items)
        entries: list[dict[str, Any]] = []
        for item in items:
            grade = str(grade_for_percent(item.get("score")))
            evidence = evidence_by_item.get(str(item.get("name")), [])
            entries.append(
                {
                    "name": item["name"],
                    "grade": grade,
                    # REUSED, not rewritten (spec §9.6). The report states one
                    # assessment of an item.
                    "remark": item.get("remark"),
                    "probes": await _write_probes(
                        session,
                        item,
                        category,
                        grade,
                        evidence,
                        probe_count_for(category, grade),
                    ),
                }
            )
        groups.append(
            {
                "category": category,
                "label": ppi.CATEGORY_LABELS[category],
                "items": entries,
                "no_gaps_statement": _no_gaps_statement(category) if not entries else None,
                "cap_statement": (
                    _cap_statement([entry["name"] for entry in entries if entry["grade"] == GRADE_NOT])
                    if category == ppi.CATEGORY_MUST_HAVE and cap_applied
                    else None
                ),
            }
        )

    return {
        "focus_summary": _focus_summary(ordered_gaps),
        "must_have_cap_applied": cap_applied,
        "groups": groups,
    }


# The two self-checks that used to run here are now
# `tests/test_functional_assessment.py`. An `assert` at module scope is stripped
# by `python -O`, so it protected nothing in a production image, and it read
# `ppi` at import time, which is the cycle-fatal pattern this file just removed.
