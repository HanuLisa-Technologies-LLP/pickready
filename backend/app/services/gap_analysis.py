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
from typing import Any, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.prompts import registry
from app.services import (
    agent_loop,
    generation_sufficiency,
    llm_router,
    ppi,
)
from app.services.rating import (
    GRADE_MODERATELY,
    GRADE_NOT,
    GRADES,
    MODERATE_OR_BELOW,
    grade_for_percent,
)
from app.services.siddhi import numbers as siddhi_numbers
from app.services.siddhi import remarks as siddhi_remarks
from app.services.siddhi import report as siddhi_report
from app.services.siddhi import support as siddhi_support
from app.services.siddhi import synthesis as siddhi_synthesis
from app.services.siddhi.provenance import ProvenanceSink

logger = logging.getLogger(__name__)

__all__ = [
    "gap_order",
    "PROBE_WORDS",
    "PROBES_MODEL",
    "PROBES_TEMPLATE",
    "PROBES_EMPTY_STATE",
    "build_gap_groups",
    "build_gap_analysis",
    "gap_items",
    "must_have_cap_applies",
    "probe_count_for",
    "row_grade",
]

#: How an entry's probes were written, stored on the entry as `probes_source`.
#: `template` is the deterministic fallback after the model failed or never
#: satisfied the critic, and flags the report for review; `empty_state` is the
#: fixed pair the sufficiency gate uses when there is nothing to ground a probe
#: on, which is a deliberate catalogue answer rather than a fallback.
PROBES_MODEL = "model"
PROBES_TEMPLATE = "template"
PROBES_EMPTY_STATE = "empty_state"

#: The registry name of the probe prompt, recorded with a model call.
PROBE_PROMPT = "report_gap_probes"

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
#: capped shorter than an item's 45-50 word remark for exactly that reason. The
#: contract lives with the other report word contracts in `siddhi.remarks`.
PROBE_WORDS = siddhi_remarks.PROBE_REMARK_WORDS

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


#: The `report_dimensions.assessment_status` value for a skill whose
#: evaluation could not be completed (PLAN-p5 P5-D3). Such a row has no grade.
STATUS_NOT_ASSESSED = "not_assessed"


def row_grade(row: Mapping[str, Any]) -> str | None:
    """The grade WORD a rated row carries, as the grading authority decided it.

    Miti is the sole grading authority, so a row that carries its grade word is
    read, never re-derived: re-deriving it from the score here would be a second
    arithmetic that has to agree with Miti's, and the gap section would state
    "Name: Grade" differently from the rated section the day they disagreed
    (the quality gate's `grade_restated_differently`). A row with no word
    (every row written by the scoring path before the grading phase) falls
    back to the one scale, `rating.grade_for_percent`. A skill whose evaluation
    could not be completed has NO grade, whatever its score field says: it is
    neither a gap nor a failed Must-have, because "not assessed" is not "poor".
    """
    if row.get("assessment_status") == STATUS_NOT_ASSESSED:
        return None
    stated = row.get("grade")
    if stated in GRADES:
        return str(stated)
    return grade_for_percent(row.get("score"))


def must_have_cap_applies(dimensions: list[dict[str, Any]]) -> bool:
    """Whether any Must-have item graded Not Matching (spec §5.5).

    The single condition behind the hard cap, in one place, so the arithmetic in
    synthesis and the sentence in the report cannot disagree about whether it
    fired. Reads `row_grade`, so a Not assessed Must-have does not fire it.
    """
    return any(
        row.get("category") == ppi.CATEGORY_MUST_HAVE
        and row_grade(row) == GRADE_NOT
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
        if row.get("category") == category and row_grade(row) in GAP_GRADES
    ]
    order = {GRADE_NOT: 0, GRADE_MODERATELY: 1}
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(row_grade(row)), 2),
            row.get("score") if row.get("score") is not None else 0,
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
        # No usable answer to quote. This is now the GATED path rather than a
        # fallback the model shares: `generation_sufficiency.gap_probe_state`
        # refuses to run the prompt at all in this state, so these two
        # sentences are the fixed empty-state content for the item and no model
        # is asked to write about an absence.
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
    provenance: ProvenanceSink | None,
) -> tuple[list[str], str, str | None]:
    """Generate this gap's probes through the bounded loop. Never raises.

    Returns the probes, how they were written (`PROBES_MODEL`,
    `PROBES_TEMPLATE` or `PROBES_EMPTY_STATE`) and, when the sufficiency gate
    refused, the fixed empty-state key that says why. The probes are never
    empty: an item in the gap band with nothing recorded still gets the
    deterministic grounded pair, which is fixed catalogue text.

    A model call is recorded to `provenance` only when an attempt was
    ACCEPTED; a fallback is recorded as a template. Until 2026-09-24 the two
    were indistinguishable once they left this function, so a report whose
    every probe was the fallback read exactly like one a model had written.
    """
    fallback = _fallback_probes(item, evidence, count)
    # THE GATE, BEFORE THE PROMPT. Asking a model to write a probe for an item
    # the candidate never answered produces a probe about the assessment ("the
    # answer does not establish whether they led the recovery"), which goes into
    # a report a hiring team reads as if it were a finding about the person.
    state = generation_sufficiency.gap_probe_state(evidence)
    if not state.sufficient:
        logger.info(
            "gap_analysis.probes_gated item=%s reason=%s",
            item.get("name"), state.reason,
        )
        return fallback, PROBES_EMPTY_STATE, str(state.empty_state_key)
    asked = [row["question"] for row in evidence if row.get("question")]
    system = registry.render(
        PROBE_PROMPT,
        item_name=item["name"],
        aspect=ppi.CATEGORY_LABELS.get(category, category),
        grade=grade,
        probe_words=f"{PROBE_WORDS[0]} to {PROBE_WORDS[1]}",
        count=count,
    )
    payload = json.dumps(
        {
            "item_remark": item.get("remark"),
            # The question and the answer, and never the row ids an exchange
            # also carries for the citation trail: an address is for the
            # audit, and a prompt handed identifiers would have them to quote.
            "what_the_candidate_was_asked_and_answered": [
                {"question": row.get("question"), "answer": row.get("answer")}
                for row in evidence
            ],
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
            # THE DELIVERED-DOCUMENT RULE, not the conversation's. A probe is
            # printed in the PRISM Report, whose serialiser and PDF both RAISE
            # on a bare percentage the interviewer guard deliberately permits,
            # so a probe reading "you cut cost by 30%" used to pass here and
            # then make the PDF download fail after the report was written.
            if siddhi_numbers.scan_text(probe, path=location):
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
            # GENERIC ADVICE IS THE FAILURE MODE THIS SECTION EXISTS TO STOP.
            # spec-doc6 §4.5 names it directly. A probe that could have been
            # written before the interview was not grounded in anything the
            # candidate said, whatever it cites, and the corpus is checked
            # through the product's one banned-phrase implementation so a close
            # variant is caught without a second matcher to keep in step.
            defects.extend(
                agent_loop.banned_phrase_gate(
                    probe,
                    siddhi_synthesis.GENERIC_ADVICE_PHRASES,
                    location=location,
                ).defects
            )
            # A probe that describes the evidence rather than the work. Same
            # shape as the rule above it and the same reason: the prompt asks,
            # and a check is what makes it hold when the pack is thinnest.
            defects.extend(
                generation_sufficiency.meta_commentary_defects(
                    probe, location=location
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
        logger.warning(
            "gap_analysis.probes_degraded item=%s reasons=%s",
            item.get("name"), list(result.reasons),
        )
        if provenance is not None:
            provenance.template(f"probes:{item.get('name')}")
        return fallback, PROBES_TEMPLATE, None
    if provenance is not None:
        provenance.model_call("report_synthesis", PROBE_PROMPT)
    return result.value, PROBES_MODEL, None


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


async def build_gap_groups(
    session: AsyncSession | None,
    dimensions: list[dict[str, Any]],
    evidence_by_item: dict[str, list[dict[str, Any]]],
    *,
    provenance: ProvenanceSink | None,
) -> dict[str, Any]:
    """The Gap Analysis & Action Plan section itself (spec 9.6), NOT composed.

    Groups by aspect, orders each aspect's gaps, writes each gap's probes, and
    states the cap and the empty aspects in words. It does not render anything
    and does not call Siddhi's composer: turning these groups into delivered,
    cited statements is `siddhi.report.compose_prism`'s job, and the section
    is one of its inputs.

    `evidence_by_item` maps an item's NAME to the questions it was probed with
    and the answers the candidate gave. Keyed on the name rather than the
    competency id because a report row is the permanent record and its name is
    what survives an edit to the job's skills.

    Every entry carries `probes_source` (`PROBES_MODEL`, `PROBES_TEMPLATE`,
    `PROBES_EMPTY_STATE`), so how its probes were written survives onto the
    stored report whatever the caller does with `provenance`. `provenance` is
    the run's recorder; None means the caller reads the entries instead.

    PROBE GENERATION NEVER RAISES. A provider outage degrades a probe to a
    deterministic, still grounded one, recorded as a template.
    """
    cap_applied = must_have_cap_applies(dimensions)
    groups: list[dict[str, Any]] = []
    ordered_gaps: list[tuple[str, dict[str, Any]]] = []

    for category in gap_order():
        items = gap_items(dimensions, category)
        ordered_gaps.extend((category, item) for item in items)
        entries: list[dict[str, Any]] = []
        for item in items:
            grade = str(row_grade(item))
            evidence = evidence_by_item.get(str(item.get("name")), [])
            probes, probes_source, empty_state_key = await _write_probes(
                session,
                item,
                category,
                grade,
                evidence,
                probe_count_for(category, grade),
                provenance,
            )
            entries.append(
                {
                    "name": item["name"],
                    "grade": grade,
                    # REUSED, not rewritten (spec 9.6). The report states one
                    # assessment of an item.
                    "remark": item.get("remark"),
                    "probes": probes,
                    "probes_source": probes_source,
                    # A KEY, present only when the sufficiency gate refused, so
                    # a reader of the stored section can tell "the model wrote
                    # these" from "nothing was recorded and these are the fixed
                    # ones". Never a sentence: `EMPTY_STATE_COPY` owns the
                    # wording, in one place, for every surface.
                    "probes_empty_state": empty_state_key,
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


async def build_gap_analysis(
    session: AsyncSession | None,
    dimensions: list[dict[str, Any]],
    evidence_by_item: dict[str, list[dict[str, Any]]],
    *,
    overall_summary: str | None = None,
    overall_grade: str | None = None,
    overall_remark_source: str | None = None,
    validation: dict[str, Any] | None = None,
    validation_points: dict[str, Any] | None = None,
    claim_evidence: dict[str, Any] | None = None,
    extra_nodes: Sequence[Any] = (),
    passages: dict[str, list[dict[str, Any]]] | None = None,
    provenance: ProvenanceSink | None = None,
    passage_source: siddhi_support.PassageSource | None = None,
) -> dict[str, Any]:
    """The section, COMPOSED through Siddhi, as the report row stores it.

    `build_gap_groups`, then `siddhi.report.compose_prism` over the whole
    report, with the result kept under Siddhi's own namespace on the row. This
    is the call the current scoring orchestrator makes; the grading phase's
    orchestrator calls the two halves itself, and when it does this wrapper has
    no caller and is deleted with the old orchestrator.

    WHAT CHANGED (Vivekium release). The composer used to RAISE on an uncited
    statement, so the whole scoring task failed and the candidate got no
    report. It now WITHHOLDS the statement and routes the report to review:
    `result["siddhi"]["review"]` carries `needs_human_review` and the findings
    (no prose), and the orchestrator ORs them into the report row. Nothing
    uncited is rendered as cited, which is the rule the raise protected.
    """
    section = await build_gap_groups(
        session, dimensions, evidence_by_item, provenance=provenance
    )
    composed = await siddhi_report.compose_prism(
        dimensions=dimensions,
        evidence_by_item=evidence_by_item,
        gap_groups=section["groups"],
        focus_summary=section["focus_summary"],
        overall_summary=overall_summary,
        overall_grade=overall_grade,
        overall_remark_source=overall_remark_source,
        validation=validation,
        validation_points=validation_points,
        claim_evidence=claim_evidence,
        extra_nodes=tuple(extra_nodes),
        passages=passages,
        embed=siddhi_support.semantic_embedder(),
        passage_source=passage_source,
    )
    # SIDDHI'S OWN NAMESPACE ON THE IMMUTABLE ROW. The citation trail (with
    # durable locators and support verdicts), the dashboard's Vivekium Note and
    # the review verdict are properties of the report and survive with it.
    # `GapAnalysisOut` does not declare the key, so it never crosses the API
    # boundary as part of the section; `siddhi.trail` is the read model that
    # serves the trail, behind the transcript capability.
    #
    # THE NOTE HERE IS NOT A SECOND SOURCE FOR THE DASHBOARD CELL. There is one
    # producer, `siddhi.synthesis.ready_pick_note`: the dashboard renders the
    # SENTENCE from `evaluations.aggregate_json`, and this row keeps the
    # sentence WITH ITS CITATIONS.
    return {**section, "siddhi": composed.siddhi_namespace()}


# The two self-checks that used to run here are now
# `tests/test_functional_assessment.py`. An `assert` at module scope is stripped
# by `python -O`, so it protected nothing in a production image, and it read
# `ppi` at import time, which is the cycle-fatal pattern this file just removed.
