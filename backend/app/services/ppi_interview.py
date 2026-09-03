"""Writing one PPI question, at the moment it is asked.

WHAT THIS REPLACED (Draft v4)
-----------------------------
`services/technical_interview`, which did exactly this for a separate technical
track. There is no separate technical track: technical depth is assessed by the
Must-have items of the job's PPI matrix, so one writer serves the whole blended
conversation. The candidate reads one sequence and never sees that different
scoring methods sit behind different parts of it (spec §7).

THE INVARIANT THAT CARRIED OVER, AND WHY IT MATTERS MORE NOW
------------------------------------------------------------
A Must-have or Nice-to-have answer is scored against ITS OWN question's rubric
(spec §8). A generated question is therefore only sound if the rubric is
generated WITH it and stored alongside it -- otherwise an answer is graded
against a rubric written for a question nobody was asked. `write_question`
writes both in ONE model call and persists both before the candidate reads
either.

A Behavioural question carries no rubric, and that is a statement rather than an
omission: there is no single correct answer to weigh a behavioural answer
against, so it is scored by judgement. Asking a model to invent a rubric for it
would manufacture a false precision the scorer would then be bound by.

THE COVERAGE PLAN IS STILL DETERMINISTIC
----------------------------------------
Which items are probed, in what order, and how many questions there are is
decided before the conversation starts, by `ppi.generate_candidate_questions`
against the job's saved matrix. Two candidates on one job are probed on the same
items in the same order. What varies per candidate is how each item is
approached, never which items there are -- and that is what keeps two reports
comparable.

DEGRADATION IS THE PRODUCT'S PREVIOUS BEHAVIOUR
-----------------------------------------------
Every failure path leaves the row exactly as `generate_candidate_questions`
wrote it: a question generated for this candidate from their own resume, and no
rubric. `generated_at` stays NULL, which is the honest record that it happened
and what telemetry counts. A candidate is mid-assessment on a live request; a
provider problem costs the question its adaptivity and nothing else.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import CandidateQuestion, JobCompetency
from app.models.job import Job
from app.prompts import fragments, registry
from app.services import agent_loop, llm_router, ppi
from app.services.assessment_formats import types as question_types
from app.services.hiring import evidence_graph

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RUBRIC",
    "EvidenceBrief",
    "MAX_HOLLOW_TELLS",
    "MAX_QUESTION_CHARS",
    "MAX_RUBRIC_BAND_CHARS",
    "RUBRIC_BANDS",
    "build_evidence_brief",
    "department_hints",
    "is_rubric_scored",
    "load_for_link",
    "write_question",
]

#: The five bands every rubric must carry. A rubric missing a band cannot
#: express the grade that band stands for, so the scorer would quietly compress
#: the scale -- which looks like a harsh or generous marker rather than a
#: malformed rubric.
RUBRIC_BANDS: tuple[str, ...] = ("0_39", "40_59", "60_74", "75_89", "90_100")

DEFAULT_RUBRIC: dict[str, str] = {
    "0_39": "No relevant example or materially incorrect approach.",
    "40_59": "Partial knowledge with limited practical evidence.",
    "60_74": "Sound practical approach with a credible example.",
    "75_89": "Strong depth, trade-offs, and measurable outcomes.",
    "90_100": "Exceptional depth, judgement, outcomes, and transferable insight.",
}

#: A question longer than this is a reading comprehension test, not an interview
#: question. Bounded here rather than truncated, for the standing reason: a
#: truncated question loses its question mark.
MAX_QUESTION_CHARS = 420

#: A rubric band longer than this is an essay the scorer has to read on every
#: single answer.
MAX_RUBRIC_BAND_CHARS = 400


def is_rubric_scored(category: str | None) -> bool:
    """Whether an answer to this item is graded against a stored rubric.

    Must-have and Nice-to-have are; Behavioural is not (spec §8). One function,
    read by the writer and by the scorer, so the two cannot disagree about which
    method applies to an item.
    """
    return str(category) in ppi.RUBRIC_SCORED_CATEGORIES


# ── Question integrity criteria ──────────────────────────────────────────────
# All deterministic, all pure functions of their input, all testable without a
# model or a database.


#: An acronym is UPPER CASE, not merely capitalised, and that distinction is
#: the whole rule. A first pass tested "short and starts with a capital", which
#: swept in "Kafka" -- a proper noun every good question spells out, and exactly
#: the case the mention check exists to police. A single trailing lowercase "s"
#: is allowed so "LLMs" and "APIs" read as the plurals they are.
_ACRONYM = re.compile(r"^[A-Z0-9][A-Z0-9+#./-]{0,7}s?$")


def _looks_like_acronym(name: str) -> bool:
    """Whether `name` is a short initialism a good question may expand.

    THE DEFECT THIS EXISTS FOR, FOUND BY RUNNING A REAL INTERVIEW
    ------------------------------------------------------------
    An end-to-end run against a live "AI / Generative AI Engineer" job produced
    a plan starting `['LLMs', 'RAG', 'LangGraph', ...]`. The model wrote a
    perfectly good opening question -- "can you describe a specific situation
    where you applied Large Language Models..." -- and the mention check
    rejected it, twice, because the string "llms" does not appear in it. Every
    candidate for that job therefore read the deterministic probe as question
    one.

    That is precisely the failure this repo has a standing rule about: with a
    guard on generated text the hard part is the distinction and not the
    detection, and a guard that rejects a real question fails INVISIBLY -- the
    logs record a rejection, the product degrades quietly, and it looks like a
    provider outage.

    Detecting an arbitrary expansion cheaply is not possible, so the tolerant
    direction is the correct one: for an acronym the criterion is skipped and
    the prompt instruction carries it alone.
    """
    return bool(_ACRONYM.match(name.strip()))


def _mentions(text: str, name: str) -> bool:
    """Whether `text` plausibly refers to the item called `name`.

    Compared on WORDS rather than as a substring, because an item label is
    routinely a phrase ("MongoDB schemas and indexes") that a well-written
    question quite properly says in a different order. Requiring every
    significant word would reject good questions; requiring at least one catches
    the failure this criterion exists for, which is a model that ignored the
    item entirely and asked about something else.

    BEHAVIOURAL ITEMS ARE EXEMPT, and that is not a loophole. A good question
    probing "Ownership" asks about a piece of work someone saw through, and the
    word "ownership" appearing in it would TELL the candidate which competency
    is being measured -- which the prompt explicitly forbids. Requiring the
    mention here would make the two rules contradict each other, and the
    deterministic guard would win.
    """
    name = name.strip()
    if _looks_like_acronym(name):
        return True
    significant = [
        word for word in re.findall(r"[A-Za-z0-9+#./-]{3,}", name.casefold())
        if word not in {"and", "the", "for", "with", "using"}
    ]
    if not significant:
        return True  # nothing specific to check for; do not reject on it
    haystack = text.casefold()
    return any(word in haystack for word in significant)


#: A question that stacks several asks. Counting question marks alone is wrong:
#: "How did you handle the outage? Walk me through it." is one question said in
#: two sentences, and rejecting it would reject good interviewer speech. What
#: actually signals a stacked ask is an enumerated or conjoined SECOND demand.
_STACKED = re.compile(
    r"(?:\balso[, ]|\bsecond(?:ly)?\b|\bfinally\b|\bin addition\b|"
    r"\bpart\s*(?:two|2|b)\b|\(\s*[2b]\s*\)|\b2[.)]\s)",
    re.IGNORECASE,
)


def _terms(text: str) -> set[str]:
    """Content words, for the repeat check. Deliberately crude."""
    return {
        word
        for word in re.findall(r"[a-z0-9+#./-]{4,}", text.casefold())
        if word not in {
            "have", "with", "that", "this", "your", "when", "what", "which",
            "would", "there", "their", "about", "been", "were", "from", "into",
            "describe", "explain", "walk", "through", "give", "tell",
        }
    }


def _is_repeat(text: str, asked_before: list[str] | None) -> bool:
    """Whether this question covers ground already covered.

    Compared on content words rather than exact strings, because a model told
    not to repeat itself will happily reword the same question. The threshold is
    high (0.8) on purpose: several questions about one item SHOULD overlap
    heavily in vocabulary, and a low threshold would reject the legitimate
    second and third probes of an item the plan deliberately covers more than
    once.
    """
    terms = _terms(text)
    if not terms:
        return False
    for previous in asked_before or []:
        earlier = _terms(previous)
        if not earlier:
            continue
        if len(terms & earlier) / max(len(terms | earlier), 1) > 0.8:
            return True
    return False


def _normalise(payload: Any, *, rubric_required: bool) -> dict[str, Any] | None:
    """Parsed model output as {question, rubric}, or None if it is not that."""
    if not isinstance(payload, dict):
        return None
    question = " ".join(str(payload.get("question") or "").split())
    if not question:
        return None
    if not rubric_required:
        return {"question": question, "rubric": None}
    rubric_raw = payload.get("rubric")
    if not isinstance(rubric_raw, dict):
        return None
    rubric = {
        band: " ".join(str(rubric_raw.get(band) or "").split())
        for band in RUBRIC_BANDS
    }
    return {"question": question, "rubric": rubric}


#: Share of content words at which a generated question counts as a copy of a
#: Runbook probe. Deliberately below the 0.8 the repeat check uses: these are
#: short questions whose whole substance is four or five words, so a lower bar
#: is the same strictness.
PROBE_REUSE_THRESHOLD = 0.6


def _matching_probe(question: str, probes: Sequence[str]) -> str | None:
    """The Part VI weak probe this question reproduces, or None.

    SECTION 21.6 IS THE ONLY PLACE THE RUNBOOK SAYS WHAT A BAD PROBE IS, and it
    says it by example: "What is horizontal scaling?", "Explain microservices.",
    "How do you debug?" against the participatory questions in the column
    beside them. Its design rule is "probe for specifics that only a participant
    would know, not for knowledge that is publicly retrievable", and a
    retrievable question is exactly what a model reaches for when it has a
    competency name and nothing else.

    Compared on content words rather than as a string, because a model told to
    ask about microservices writes "Can you explain microservices to me?" and
    not the Runbook's four words. The threshold is high for the standing reason:
    a false positive throws away a good question and shows the candidate the
    stored one instead.

    Used TWICE, against two different sets and for two different reasons: a
    21.6 weak probe is refused because it asks for something retrievable, and a
    Part VI validation probe is refused because reusing its words would make
    every candidate for every job in that department read the same question,
    which is the preset bank by another route.
    """
    def clean(text: str) -> set[str]:
        # `_terms` keeps '.' inside a token so "CI/CD" and "3.5" survive, which
        # also swallows the full stop on a sentence-final word. The Runbook
        # prints its weak probes as complete quoted sentences, so without this
        # "microservices." and "microservices" are different terms and the
        # comparison never fires.
        return {word.strip("./-") for word in _terms(text) if word.strip("./-")}

    terms = clean(question)
    if not terms:
        return None
    for probe in probes or ():
        earlier = clean(probe)
        if not earlier:
            continue
        if len(terms & earlier) / max(len(terms | earlier), 1) > PROBE_REUSE_THRESHOLD:
            return probe
    return None


def _evaluate(
    competency: JobCompetency,
    asked_before: list[str],
    *,
    rubric_required: bool,
    weak_probes: Sequence[str] = (),
    field_probes: Sequence[str] = (),
    resume_anchor: str | None = None,
) -> Any:
    """Build the deterministic criteria for one question.

    Returned as a closure so the criteria carry this item and the questions
    already asked, and so they stay pure functions of their input.

    `resume_anchor` is set for an evidence-based question (assessment-spec-doc
    2.1). The rewrite must keep probing the resume item the question was
    anchored to, or the recruiter's view would show an anchor beside a
    question that asks about something else. Checked with `_mentions`, the
    same tolerant word test the item name gets: an anchor is a sentence and
    a good question says part of it, never all of it.
    """

    def evaluate(candidate: dict[str, Any]) -> agent_loop.Critique:
        question = candidate["question"]
        rubric = candidate.get("rubric")
        reasons: list[str] = []

        weak = _matching_probe(question, weak_probes)
        if weak is not None:
            reasons.append(
                "that asks for something a candidate could look up rather than "
                "something only a participant would know; the previous attempt "
                "reads like the weak probe %r. Ask for one specific thing that "
                "happened to them." % weak
            )
        copied = _matching_probe(question, field_probes)
        if copied is not None:
            reasons.append(
                "the example probes were for calibration and must not be "
                "asked; the previous attempt reproduces %r. Write a question "
                "about something this candidate actually did." % copied
            )

        if len(question) > MAX_QUESTION_CHARS:
            reasons.append(
                f"keep the question under {MAX_QUESTION_CHARS} characters; the "
                f"previous attempt was {len(question)}"
            )
        if rubric_required and not _mentions(question, competency.name):
            reasons.append(
                f"the question must actually probe {competency.name!r}; the "
                "previous attempt did not refer to it at all"
            )
        if resume_anchor and not _mentions(question, resume_anchor):
            reasons.append(
                "this is an evidence-based question anchored to a specific item "
                f"on the resume, {resume_anchor!r}; the previous attempt did not "
                "refer to that item at all. Name it and ask about it."
            )
        if _STACKED.search(question):
            reasons.append(
                "ask exactly one thing; the previous attempt stacked a second "
                "question or sub-part onto the first"
            )
        if _is_repeat(question, asked_before):
            reasons.append(
                "this is a question the candidate has already been asked; ask "
                "about a different aspect of the item"
            )

        if rubric_required:
            rubric = rubric or {}
            empty_bands = [band for band in RUBRIC_BANDS if not rubric.get(band)]
            if empty_bands:
                reasons.append(
                    "the rubric must carry all five bands with real text; these "
                    "were missing or empty: " + ", ".join(empty_bands)
                )
            long_bands = [
                band for band in RUBRIC_BANDS
                if len(rubric.get(band) or "") > MAX_RUBRIC_BAND_CHARS
            ]
            if long_bands:
                reasons.append(
                    f"keep every rubric band under {MAX_RUBRIC_BAND_CHARS} "
                    "characters; these were longer: " + ", ".join(long_bands)
                )
            # A rubric whose bands are identical strings cannot separate a
            # strong answer from a weak one, and it is a real failure mode: a
            # model under instruction pressure will pad the shape it was asked
            # for.
            filled = [rubric[band] for band in RUBRIC_BANDS if rubric.get(band)]
            if len(filled) > 1 and len(set(filled)) < len(filled):
                reasons.append(
                    "each rubric band must describe a DIFFERENT standard; the "
                    "previous attempt repeated the same text across bands"
                )

        return agent_loop.reject(*reasons) if reasons else agent_loop.ok()

    return evaluate


# -- The department evidence graph, as this question's brief ------------------
#
# THE LIVE ENTRY POINT. `api/assessments._write_next_question_inner` calls
# `write_question` for every base question a candidate reads, and this is where
# Part VI enters that call. Before this, the question was written from the item
# name, the item description, the JD and the resume: nothing in the prompt knew
# what department the role was in, what evidence would actually establish the
# competency, or what a hollow answer sounds like in that field.
#
# WHY THE CLASS PROSE IS PYTHON AND THE REST IS DATA. Everything Part VI and
# section 38 supply -- what to establish, the specificity rung, the hollow
# tells, the corroboration groups -- is read from `runbook_data/` and is checked
# against the document by `test_runbook_parity.py`. The four sentences below
# are NOT in the Runbook: they are the instruction that turns a class into a
# question, and they live here for the same reason
# `interviewer._CHALLENGE_BY_LABEL` does -- the class is chosen by code, so the
# sentence that expresses it belongs beside the code that chooses it, and
# `app/prompts/ppi_write_question.txt` keeps the surrounding wording.

@lru_cache(maxsize=1)
def _class_instructions() -> dict[str, str]:
    """The four sentences, built on first use rather than at import.

    A FUNCTION, NOT A MODULE-SCOPE DICT, and `tests/test_import_graph.py` is why:
    reading `evidence_graph.CLASS_GAP` while this module is being imported is an
    AttributeError the moment a cycle forms, and `gap_analysis` has already been
    broken by exactly that. Same shape `ppi_report._remark_bounds` uses.
    """
    return {
        evidence_graph.CLASS_CONFIRMATION: (
            "The candidate has already said something about this. Do not ask "
            "them to repeat it. Take one specific part of what they said and "
            "ask for the detail that would only be available to somebody who "
            "was actually there."
        ),
        evidence_graph.CLASS_GAP: (
            "This has been raised and nothing usable has come back yet. Name "
            "the kind of evidence you still need, in plain words, and ask for "
            "one concrete instance of it."
        ),
        evidence_graph.CLASS_CONTRADICTION: (
            "Two things the candidate has told us do not fit together. Put the "
            "difference to them plainly and neutrally and give them the chance "
            "to explain it. Do not accuse, do not imply doubt, and do not state "
            "a conclusion: a candidate who is asked well explains most of these."
        ),
        evidence_graph.CLASS_DISCOVERY: (
            "Nothing in this candidate's profile speaks to this at all. Do NOT "
            "ask them to account for what is missing, which would only "
            "establish that the profile is silent. Ask instead what they have "
            "done that the profile does not show, and leave the question wide "
            "enough that an unusual background can answer it well."
        ),
    }


#: How many of Part VI's own validation probes are shown as calibration. The
#: bound is the same reasoning as MAX_HOLLOW_TELLS: six literal questions in a
#: prompt asking for one question is an invitation to pick one.
MAX_FIELD_PROBES = 3

#: How many of a department's gaming vectors are shown at once. All eight of
#: section 21.7 would be a longer instruction than the question they are meant
#: to shape, and the model reads the last thing best.
MAX_HOLLOW_TELLS = 3


@dataclass(frozen=True)
class EvidenceBrief:
    """What the department evidence graph says about the question being written."""

    #: One of Part VI's fifteen department keys.
    department: str
    #: The Runbook's own title for it, e.g. "CIVIL, STRUCTURAL & CONSTRUCTION".
    department_title: str
    #: The Part VI menu row this matrix item was recognised as, or None when the
    #: item is genuinely role-specific and the menu has no row for it.
    node: evidence_graph.EvidenceNode | None
    #: confirmation | gap | contradiction | discovery.
    question_class: str
    #: The rung of section 38.3's gradient this question aims at.
    specificity: evidence_graph.SpecificityLevel
    #: Independence groups the platform can reach, and the ones it cannot.
    reachable: tuple[str, ...] = ()
    out_of_band: tuple[str, ...] = ()
    #: The sources Sutra's frozen matrix flagged as required for this
    #: competency. Empty when the scorecard could not be read, which is logged
    #: rather than silently treated as "none required".
    required_sources: tuple[str, ...] = ()
    #: Part VI's gaming vectors for this department. Empty for a department the
    #: Runbook prints none for, and empty when `node` is None.
    hollow_tells: tuple[str, ...] = ()
    #: Section 21.6's retrievable probes, which a generated question must not
    #: reproduce.
    weak_probes: tuple[str, ...] = ()
    #: Part VI's own validation probes for this department, where it prints
    #: them. Shown as CALIBRATION and refused as a COPY: see `_specificity_block`
    #: and `_evaluate`.
    field_probes: tuple[str, ...] = ()

    @property
    def establishes(self) -> str:
        return self.node.establishes if self.node is not None else ""


def department_hints(job: Job) -> tuple[str, ...]:
    """What `resolve_department` is given, in order of how specific it is.

    The JD is included and truncated. A title alone is often ambiguous in
    exactly the way Part VI's departments are adjacent -- "Site Reliability
    Engineer" reads as civil site work on its words alone -- and the first part
    of a JD carries the vocabulary that separates them.
    """
    return (
        str(job.department or ""),
        str(job.title or ""),
        str(job.jd_markdown or "")[:1200],
    )


async def _prior_evidence(
    session: AsyncSession | None, row: CandidateQuestion
) -> tuple[int, int]:
    """(answers, substantive answers) already filed under this matrix item.

    Grouped by COMPETENCY, never by question, and that is the same rule
    `api/assessments._coverage_rows` follows: several questions can probe one
    item and a follow-up is filed under its parent's key, so counting questions
    would make a third of the matrix look covered.

    Returns (0, 0) on any failure. This decides which of the four classes the
    next question belongs to; a database hiccup should cost the question its
    class and nothing else, and (0, 0) with no resume claim is Discovery, which
    is the class that assumes least about the candidate.
    """
    if session is None:
        return 0, 0
    try:
        result = (
            await session.execute(
                sql_text(
                    """
                    SELECT COUNT(m.id) AS answers,
                           COUNT(m.id) FILTER (
                               WHERE COALESCE(m.answer_label, 'substantive')
                                     = 'substantive'
                           ) AS substantive
                      FROM candidate_questions q
                      LEFT JOIN assessment_messages m
                             ON m.question_key = CAST(q.id AS text)
                            AND m.speaker = 'candidate'
                     WHERE q.job_candidate_link_id = :link_id
                       AND q.competency_id = :competency_id
                    """
                ),
                {
                    "link_id": str(row.job_candidate_link_id),
                    "competency_id": str(row.competency_id),
                },
            )
        ).first()
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.info(
            "ppi_interview.prior_evidence_unavailable error=%s", type(exc).__name__
        )
        return 0, 0
    if result is None:
        return 0, 0
    return int(result[0] or 0), int(result[1] or 0)


def _claim_present(competency: JobCompetency, resume_excerpt: str) -> bool:
    """Whether the candidate's own profile already says something about this item.

    THE LINE BETWEEN GAP AND DISCOVERY, and the reason the fourth class exists.
    A profile that is silent on a competency has not failed at it: axiom 7 says
    absence of evidence is not evidence of absence, and 6.6's Unknown discipline
    says the same operationally. Asking a gap question of a silent profile
    establishes only that the profile is silent, which is what an ATS already
    concluded, so the silent case gets a Discovery question instead.

    Matched on significant WORDS rather than as a substring, for the reason
    `_mentions` gives: an item label is routinely a phrase a resume says in a
    different order.
    """
    words = [
        word for word in re.findall(r"[a-z0-9+#./-]{4,}", competency.name.casefold())
        if word not in {"and", "the", "for", "with", "using"}
    ]
    if not words:
        return False
    haystack = (resume_excerpt or "").casefold()
    return any(word in haystack for word in words)


async def build_evidence_brief(
    *,
    session: AsyncSession | None,
    job: Job,
    row: CandidateQuestion,
    competency: JobCompetency,
    resume_excerpt: str = "",
    conflicting: bool = False,
) -> EvidenceBrief | None:
    """Read the role's Department Evidence Graph for this one question.

    Returns None when no Part VI department covers the role. NOTHING IS
    SUBSTITUTED: section 36 requires a department model to be authored through
    its own procedure rather than improvised, and the caller falls back to the
    question `ppi.generate_candidate_questions` already wrote for this item from
    this candidate's own resume, which is not a generic bank.

    `conflicting` comes from the evidence ledger and is the caller's to supply;
    it defaults to False so the classification degrades to Confirmation or Gap
    rather than raising a contradiction nobody recorded.
    """
    try:
        department = evidence_graph.resolve_department(*department_hints(job))
    except evidence_graph.DepartmentUnmapped as exc:
        logger.warning(
            "ppi_interview.department_unmapped job_id=%s detail=%s",
            getattr(job, "id", None), exc,
        )
        return None
    graph = evidence_graph.graph_for(department)
    node = evidence_graph.node_for_competency(competency.name, department)

    answers, substantive = await _prior_evidence(session, row)
    question_class = evidence_graph.question_class(
        conflicting=conflicting,
        claim_present=_claim_present(competency, resume_excerpt),
        substantive_answers=substantive,
        answers=answers,
    )
    specificity = evidence_graph.probe_level(
        ordinal=int(row.ordinal or 0), prior_substantive=substantive
    )

    reachable: tuple[str, ...] = ()
    out_of_band: tuple[str, ...] = ()
    if node is not None:
        inside, outside = evidence_graph.corroboration_targets(node)
        reachable, out_of_band = tuple(inside), tuple(outside)

    required: tuple[str, ...] = ()
    try:
        required = await evidence_graph.required_evidence_sources(
            session, getattr(job, "id", None), competency.name
        )
    except evidence_graph.ScorecardUnavailable as exc:
        # NARROW, AND LOGGED. Sutra's frozen matrix is another agent's
        # deliverable; until it lands, the department graph still says what to
        # establish and only the per-competency source routing is missing.
        # Caught by its own class so a genuine data fault is not reported as
        # "Sutra not wired yet" forever.
        logger.info("ppi_interview.scorecard_unavailable detail=%s", exc)

    return EvidenceBrief(
        department=department,
        department_title=graph.title,
        node=node,
        question_class=question_class,
        specificity=specificity,
        reachable=reachable,
        out_of_band=out_of_band,
        required_sources=required,
        # THE MATRIX CORROBORATES THE DEPARTMENT. Part VI's gaming vectors are
        # stated per department, and a department resolved from a job title is a
        # judgement that can be wrong ("Site Reliability Engineer" reads as
        # civil site work on its words alone). A matched menu row is independent
        # evidence that the resolution was right, so the department's tells are
        # only sent when one matched. Without that check a mis-resolved role
        # would be probed for the wrong field's tells and nothing would notice.
        hollow_tells=graph.hollow_tells[:MAX_HOLLOW_TELLS] if node is not None else (),
        weak_probes=tuple(pair.weak_probe for pair in graph.probe_design_pairs),
        field_probes=graph.validation_probes if node is not None else (),
    )


def _establishes_block(brief: EvidenceBrief | None, competency: JobCompetency) -> str:
    if brief is not None and brief.establishes:
        return (
            "%s\n(That is what good looks like in %s work, from the department "
            "evidence model for this role.)"
            % (brief.establishes, brief.department_title.lower())
        )
    return str(competency.description or competency.name)


def _specificity_block(brief: EvidenceBrief | None) -> str:
    if brief is None:
        return (
            "Ask for the specifics of one real instance: what happened, what "
            "the candidate personally decided, and what it cost."
        )
    level = brief.specificity
    block = ["%s\nThat is a question %s." % (level.question, level.answerable_by)]
    if brief.field_probes:
        # SHOWN AS CALIBRATION, REFUSED AS A COPY, and both halves are needed.
        # Part VI prints these as literal probes, which is the closest thing in
        # the Runbook to a statement of what a good question in this field
        # sounds like -- and also exactly the shape of the preset bank this
        # codebase deleted on 2026-08-06, where every candidate read the same
        # words. So the model is calibrated on them and `_evaluate` rejects a
        # question that reproduces one. Teaching by example is the Runbook's own
        # method: 21.6 does it with a weak column beside a strong one.
        block.append(
            "Assessors in this field ask questions of this kind. Use them for "
            "calibration only. DO NOT ask any of them: the question you write "
            "must be about this candidate's own work, in your own words.\n- "
            + "\n- ".join(brief.field_probes[:MAX_FIELD_PROBES])
        )
    return "\n\n".join(block)


def _corroboration_block(brief: EvidenceBrief | None) -> str:
    """Where the answer's corroboration would have to come from.

    THE TRIANGULATION HALF. Section 5.4 counts independence by ORIGINATOR, so a
    candidate restating their own resume is one person saying one thing twice.
    What the assessment can reach is the conversation itself and the factual
    fields the candidate submitted; a reference and a work artefact are out of
    band, and saying so is what stops a well-argued answer from being treated as
    corroborated.
    """
    if brief is None:
        return ""
    lines = [
        "CORROBORATION. Ask for something that could be checked against a "
        "source other than the candidate's own claim."
    ]
    if brief.required_sources:
        lines.append(
            "This job's matrix requires evidence for this item from: %s."
            % ", ".join(brief.required_sources)
        )
    if brief.out_of_band:
        lines.append(
            "The assessment cannot reach these sources, so do not ask for "
            "something only they could settle: %s." % ", ".join(brief.out_of_band)
        )
    return "\n".join(lines)


def _hollow_block(brief: EvidenceBrief | None) -> str:
    if brief is None or not brief.hollow_tells:
        return ""
    return (
        "A HOLLOW ANSWER IN THIS FIELD LOOKS LIKE THIS:\n- "
        + "\n- ".join(brief.hollow_tells)
        + "\nWrite the question so that an answer of that kind would be "
        "visibly thin. Never say any of this to the candidate and never "
        "conclude anything from it: it decides how you ask, not what you think."
    )


_RUBRIC_INSTRUCTION = (
    "ALSO WRITE THE RUBRIC FOR THIS QUESTION. The candidate's answer will be "
    "graded against it and against nothing else, so it must describe what an "
    "answer to THIS question looks like at each of five standards. Return all "
    "five bands, each with real text, and each describing a genuinely different "
    "standard from the others. Bands: "
    + ", ".join(RUBRIC_BANDS)
    + "."
)

_NO_RUBRIC_INSTRUCTION = (
    "Do NOT write a rubric for this question. This item is assessed by "
    "judgement across everything the candidate says about it, because there is "
    "no single correct answer to weigh a behavioural account against."
)

_RUBRIC_SHAPE = '{"question":"...","rubric":{"0_39":"...","40_59":"...","60_74":"...","75_89":"...","90_100":"..."}}'
_PLAIN_SHAPE = '{"question":"..."}'


def _evidence_anchor(row: CandidateQuestion) -> str | None:
    """The quotable resume item an evidence-based row was anchored to, or
    None for every other row. Read with `getattr` because the scorer's unit
    tests hand this module rows that predate the format columns."""
    if getattr(row, "question_type", None) != question_types.EVIDENCE_BASED:
        return None
    anchor = " ".join(str(getattr(row, "resume_anchor", "") or "").split())
    return anchor or None


def _anchor_block(anchor: str | None) -> str:
    """The prompt block that keeps a rewrite on its anchored resume item.

    Empty for a short-answer question, so `ppi_write_question.txt` renders for
    both formats from one template rather than two that would drift.
    """
    if not anchor:
        return ""
    return (
        "THIS IS AN EVIDENCE-BASED QUESTION. It was anchored to this item from "
        f"the candidate's own resume, quoted exactly: \"{anchor}\". The question "
        "you write must keep probing that item: name it, and ask what the "
        "candidate personally did, why, and what happened. Do not move to a "
        "different part of the resume."
    )


def _recent_turns(transcript: list[dict[str, Any]] | None, turns: int = 6) -> list[dict[str, str]]:
    """The last few turns as plain speaker/text pairs.

    Same shape and same bound as `interviewer._recent`: enough to refer back
    without resending a whole interview on every turn, which would blow the
    token ceiling on the later questions of a long assessment.
    """
    rows: list[dict[str, str]] = []
    for message in (transcript or [])[-turns * 2:]:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        speaker = "interviewer" if message.get("speaker") == "agent" else "candidate"
        rows.append({"speaker": speaker, "text": content[:600]})
    return rows


async def write_question(
    *,
    session: AsyncSession | None,
    job: Job,
    row: CandidateQuestion,
    competency: JobCompetency,
    resume_excerpt: str = "",
    transcript: list[dict[str, Any]] | None = None,
    asked_before: list[str] | None = None,
    conflicting: bool = False,
) -> agent_loop.LoopResult[dict[str, Any]]:
    """Write this question (and its rubric, where one applies) onto `row`.

    Persists only on success. A degraded result leaves the row exactly as
    `ppi.generate_candidate_questions` created it, with `generated_at` still
    NULL -- that NULL is the record that this candidate read the pre-generated
    question rather than one written against the live conversation, and it is
    what makes a silent degradation countable.

    Returns the `LoopResult` rather than a bare string so the caller can log the
    degradation and stamp telemetry. Never raises.
    """
    rubric_required = is_rubric_scored(competency.category)
    recent = _recent_turns(transcript)
    anchor = _evidence_anchor(row)
    # THE DEPARTMENT EVIDENCE GRAPH ENTERS HERE, on the live path, for every
    # base question every candidate reads. None means no Part VI department
    # covers this role; the prompt then falls back to the item's own
    # observable-evidence statement, never to a bank.
    brief = await build_evidence_brief(
        session=session,
        job=job,
        row=row,
        competency=competency,
        resume_excerpt=resume_excerpt,
        conflicting=conflicting,
    )
    system = registry.render(
        "ppi_write_question",
        item_name=competency.name,
        aspect=ppi.CATEGORY_LABELS.get(competency.category, competency.category),
        item_measures=competency.description or competency.name,
        one_question=fragments.ONE_QUESTION,
        no_evaluation=fragments.NO_EVALUATION,
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
        evidence_to_establish=_establishes_block(brief, competency),
        question_class=_class_instructions()[
            brief.question_class if brief is not None else evidence_graph.CLASS_GAP
        ],
        specificity=_specificity_block(brief),
        corroboration=_corroboration_block(brief),
        hollow_tells=_hollow_block(brief),
        evidence_anchor=_anchor_block(anchor),
        rubric_instruction=_RUBRIC_INSTRUCTION if rubric_required else _NO_RUBRIC_INSTRUCTION,
        return_shape=_RUBRIC_SHAPE if rubric_required else _PLAIN_SHAPE,
    )

    async def execute(reflection: str) -> dict[str, Any]:
        payload = {
            "job_description": (job.jd_markdown or "")[:2500],
            "candidate_resume": (resume_excerpt or "")[:2500],
            "conversation_so_far": recent,
            "already_asked": list(asked_before or [])[-20:],
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            # The reflection is an ASSISTANT-directed correction, appended as a
            # further user turn so it is the last thing the model reads.
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            messages,
            response_format_json=True,
            session=session,
        )
        parsed = _normalise(json.loads(raw), rubric_required=rubric_required)
        if parsed is None:
            raise ValueError("response was not the expected shape")
        return parsed

    result = await agent_loop.run_loop(
        name="ppi_question",
        execute=execute,
        evaluate=_evaluate(
            competency,
            list(asked_before or []),
            rubric_required=rubric_required,
            weak_probes=brief.weak_probes if brief is not None else (),
            field_probes=brief.field_probes if brief is not None else (),
            resume_anchor=anchor,
        ),
        fallback={
            "question": row.prompt,
            "rubric": dict(row.rubric_json) if row.rubric_json else None,
        },
        max_attempts=agent_loop.INTERACTIVE_ATTEMPTS,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )

    if not result.degraded:
        try:
            row.prompt = result.value["question"]
            if rubric_required:
                row.rubric_json = result.value["rubric"]
            row.generated_at = datetime.now(timezone.utc)
            if session is not None:
                await session.flush()
        except Exception as exc:  # noqa: BLE001
            # PERSISTENCE FAILING MUST NOT COST THE CANDIDATE THEIR TURN.
            #
            # The loop already survives every provider failure; this guard
            # covers the session itself being unusable -- which is a real state,
            # not a hypothetical one. The router loads provider keys through the
            # SAME session as its caller and marks failing keys unhealthy, so a
            # bad enough provider outage can leave the transaction rolled back
            # underneath us. Without this, the one moment every provider is down
            # is the moment `respond` raises a 500 instead of degrading.
            logger.warning(
                "ppi_interview.persist_failed link_id=%s ordinal=%d error=%s",
                row.job_candidate_link_id, row.ordinal, type(exc).__name__,
            )
            return agent_loop.LoopResult(
                value={"question": row.prompt, "rubric": row.rubric_json},
                degraded=True,
                attempts=result.attempts,
                reasons=("the generated question could not be persisted",),
                elapsed_ms=result.elapsed_ms,
                error=type(exc).__name__,
            )
    else:
        logger.info(
            "ppi_interview.degraded link_id=%s ordinal=%d attempts=%d reasons=%s",
            row.job_candidate_link_id, row.ordinal, result.attempts,
            list(result.reasons),
        )
    # LABELS AND KEYS ONLY, never question or answer text, exactly as
    # `interview_telemetry` established for this channel. A department key, a
    # class name and a gradient level are engineering metadata: they say WHICH
    # graph shaped this turn, which is the only way to tell a role probed
    # against Part VI from one that fell through to the item description.
    logger.info(
        "ppi_interview.evidence_graph link_id=%s ordinal=%d department=%s "
        "matched=%s class=%s level=%d routed=%s",
        row.job_candidate_link_id,
        row.ordinal,
        brief.department if brief is not None else "unmapped",
        brief is not None and brief.node is not None,
        brief.question_class if brief is not None else "none",
        brief.specificity.level if brief is not None else 0,
        bool(brief is not None and brief.required_sources),
    )
    return result


async def load_for_link(
    session: AsyncSession, link_id: Any
) -> list[CandidateQuestion]:
    """This candidate's questions in ask order. The scorer's input."""
    rows = (
        await session.execute(
            select(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link_id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).scalars().all()
    return list(rows)


# These two self-checks moved to `tests/test_functional_assessment.py`. A
# module-scope `assert` is stripped by `python -O` so it never protected the
# production image, and reading `ppi` at import time is fatal the moment a cycle
# reaches this module -- which is exactly what happened to `gap_analysis`.
