"""The five internal dimensions, and the isolation that makes them worth having.

    Verified Competence        Can they actually do it, and how do we know?
    Track Record & Impact      What has happened because of them?
    Role & Context Fit         Will this work HERE, in this situation?
    Authenticity & Consistency  Does the account hold together across sources?
    Trajectory & Potential     Where is this person going?

THESE ARE INTERNAL. The product still shows Must-have / Nice-to-have /
Behavioural, and the Must-have hard cap still applies exactly as before. These
five are how a grade is ARRIVED AT. They are never rendered, never named in a
report, never returned by an API, and `test_miti_pipeline.py` asserts that.

THE ISOLATION IS THE POINT OF THIS FILE
-----------------------------------------
spec-doc5 §A.3: each evaluator "sees ONLY its own competencies, the retrieved
rubric anchors, and evidence mapped to them -- never the other dimensions'
scores, the candidate's name, or the composite. (Isolation prevents halo
effects.)"

A halo effect is not a hypothetical here. Give one evaluator the sentence "this
candidate scored strongly on Verified Competence" and its Trajectory judgment
moves, in a direction nobody chose and nothing records. Give it the candidate's
NAME and a second, worse thing happens: a name carries inferred gender,
ethnicity and nationality, and an evaluator that can see one is an evaluator
whose output can correlate with one. That is a fairness failure with a legal
dimension, not a quality nit.

SO ISOLATION IS ENFORCED BY THE TYPE, NOT BY THE PROMPT
--------------------------------------------------------
`EvaluatorInput` is a frozen dataclass with a closed field list. There is no
`candidate` field, no `other_scores` field, no `composite` field, and no
free-form `context` dict a caller could smuggle any of them through. An
instruction asking a prompt-builder nicely not to include a name is an
instruction somebody eventually forgets; a dataclass that has nowhere to put one
cannot be forgotten.

`render_prompt` then builds the prompt FROM that object and from nothing else,
and `scrub` is a second, independent pass that removes person-name-shaped tokens
from evidence text before it is rendered. Two mechanisms rather than one,
because the evidence excerpts themselves legitimately contain names -- a
candidate saying "Priya and I rewrote the scheduler" is a real answer, and the
answer has to survive while the name does not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)

__all__ = [
    "DIMENSIONS",
    "DIMENSION_LABELS",
    "DIMENSION_QUESTIONS",
    "EvaluatorInput",
    "EvidenceView",
    "DimensionResult",
    "render_prompt",
    "scrub",
    "BANDS",
    "band_for",
]

DIMENSIONS: tuple[str, ...] = (
    DIM_VERIFIED_COMPETENCE,
    DIM_TRACK_RECORD,
    DIM_ROLE_FIT,
    DIM_AUTHENTICITY,
    DIM_TRAJECTORY,
)

DIMENSION_LABELS: dict[str, str] = {
    DIM_VERIFIED_COMPETENCE: "Verified Competence",
    DIM_TRACK_RECORD: "Track Record & Impact",
    DIM_ROLE_FIT: "Role & Context Fit",
    DIM_AUTHENTICITY: "Authenticity & Consistency",
    DIM_TRAJECTORY: "Trajectory & Potential",
}

#: The single question each evaluator answers. Written as ONE question on
#: purpose: an evaluator handed three questions answers the easiest one and
#: reports a blended judgment nobody can decompose afterwards.
DIMENSION_QUESTIONS: dict[str, str] = {
    DIM_VERIFIED_COMPETENCE: (
        "Does the evidence show this person can actually do this, as opposed to "
        "showing they have been near it?"
    ),
    DIM_TRACK_RECORD: (
        "What has measurably happened because of this person, and how much of it "
        "is attributable to them rather than to their team or their timing?"
    ),
    DIM_ROLE_FIT: (
        "Does the evidence suggest this would work in THIS role, in THIS "
        "situation, with the constraints described?"
    ),
    DIM_AUTHENTICITY: (
        "Does the account hold together across the sources, and where it does "
        "not, what is the most ordinary explanation?"
    ),
    DIM_TRAJECTORY: (
        "What is the direction of travel, and what evidence is there that this "
        "person's ceiling is above where they are now?"
    ),
}


# ── Bands ────────────────────────────────────────────────────────────────────
#
# FOUR BANDS, matching `services/rating.GRADES`. Not a fifth internal scale:
# this product has already paid for having two parallel five-label scales kept
# in step by hand, and a five-band internal scale mapping onto a four-grade
# product scale would be that mistake with an extra step.
#
# The values are the representative internal scores the existing scale already
# uses (`rating` cuts at 90 / 75 / 60), so an internal dimension score and a
# product grade are on one number line rather than two.
BANDS: tuple[tuple[str, int], ...] = (
    ("strong", 92),
    ("solid", 80),
    ("partial", 66),
    ("absent", 40),
)

_BAND_SCORES: dict[str, int] = dict(BANDS)


def rubric_anchor_text(dimension: str) -> str:
    """Section 9.x's six scoring anchors for ONE dimension, as prompt text.

    THE ANCHORS ARE PER DIMENSION AND ARE UNIVERSAL. Sections 9.1 to 9.5 each
    carry one six-band table over 0 to 100, stated once and never restated per
    department or per seniority; exactly one department carries anything per
    seniority (section 21.11's emphasis notes for IT and Software) and those are
    not anchors. Section 57.3 names "retrieved rubric anchors from the
    department model" as an evaluator input, which is what led an earlier
    version to build them per department; what the department model actually
    supplies is the COMPETENCY SET the anchors are applied to.

    So each of the five evaluators gets its OWN dimension's table, and none of
    them gets another's. Handing all five the same string, which is what
    happened before, meant four of them were anchored against a rubric written
    for a question they were not asked.

    Raises through `department_models` if the anchors are missing: an evaluator
    with no anchor produces a band nobody can defend, and a generic substitute
    would look exactly like a real one in the prompt.
    """
    from app.services.hiring.department_models import dimension_rubric_anchors
    from app.services.hiring.situations import RUNBOOK_ID_BY_DIMENSION

    runbook_id = RUNBOOK_ID_BY_DIMENSION.get(dimension)
    if runbook_id is None:
        raise ValueError(
            f"Unknown dimension {dimension!r}; it maps to no Runbook D1 to D5 "
            f"identifier, so no section 9.x anchor table can be retrieved."
        )
    return "\n".join(
        f"  {anchor.band}: {anchor.meaning}"
        for anchor in dimension_rubric_anchors(runbook_id)
    )


def band_for(band: str) -> int:
    """The representative score for a band. Raises for an unknown band.

    A model returning a band nobody defined must fail loudly rather than default
    to a middling score: a silent default would convert a malformed response
    into a real grade for a real candidate.
    """
    try:
        return _BAND_SCORES[band]
    except KeyError as exc:
        raise ValueError(
            f"Unknown band {band!r}; expected one of {sorted(_BAND_SCORES)}"
        ) from exc


# ── Name scrubbing ───────────────────────────────────────────────────────────

#: Titles that mark the token after them as a person's name. Handled separately
#: from capitalised-word detection, because "Dr Rao" is unambiguous where a bare
#: capitalised word is not.
_TITLES = re.compile(r"\b(mr|mrs|ms|miss|dr|prof|shri|smt)\.?\s+[A-Z][a-z]+", re.I)

#: The pronoun-and-name shape a candidate's own answer uses: "Priya and I",
#: "with Rahul", "my manager Anita". Deliberately narrow. A broad
#: capitalised-word rule would eat "Kafka", "Spring Boot" and "Bengaluru", and
#: an evaluator handed evidence with the technologies removed would be reading
#: something worse than an anonymised answer -- it would be reading a
#: mutilated one.
_NAMED_PERSON = re.compile(
    r"\b(?:with|alongside|and|from|to|manager|lead|colleague|reported to|"
    r"reporting to|my)\s+([A-Z][a-z]{2,})\b(?=\s+(?:and\s+I\b|said|told|asked|"
    r"agreed|pushed|helped|reviewed|approved))"
)


def scrub(text: str, *, subject_names: Sequence[str] = ()) -> str:
    """Remove person-name-shaped tokens from evidence text.

    `subject_names` is the CANDIDATE's own name parts, which are removed
    unconditionally -- those we know, and they are the ones that matter, because
    it is the subject's name that carries the inferred attributes.

    Everything else is best-effort and deliberately conservative. The second
    mechanism exists because evidence excerpts are a candidate's own prose and
    will contain colleagues' names; missing one is a small residual risk, and
    over-scrubbing would strip the technical nouns the evaluation is actually
    about. The structural guarantee -- that `EvaluatorInput` has no name field --
    is the one that has to hold; this is defence in depth on top of it.
    """
    cleaned = text or ""
    for part in subject_names:
        token = (part or "").strip()
        if len(token) >= 3:
            cleaned = re.sub(rf"\b{re.escape(token)}\b", "[the candidate]", cleaned, flags=re.I)
    cleaned = _TITLES.sub("[a person]", cleaned)
    cleaned = _NAMED_PERSON.sub(lambda m: m.group(0).replace(m.group(1), "[a colleague]"), cleaned)
    return cleaned


# ── The evaluator's whole world ──────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceView:
    """One piece of evidence, as an evaluator is allowed to see it.

    NOT `evidence.ledger.EvidenceItem`. The ledger row carries a `relevance`
    number, a source id and a `text_ref` that resolves to the transcript; an
    evaluator needs the TEXT and the TRUST, and giving it the rest would hand it
    an engineering score to anchor on and a route to the candidate's identity.

    `ref` is the ledger row's id and is carried because Siddhi's citation
    enforcement needs it: a statement in the report must trace to an evidence
    node, and the node is this one.
    """

    ref: str
    text: str
    trust: str
    source_kind: str
    #: Which independence group this belongs to. Two pieces of evidence in the
    #: same group corroborate nothing -- a resume line and the candidate's
    #: restatement of it in the interview are one source saying one thing twice.
    independence_group: str
    #: current | recent | dated, from the ledger's freshness rules.
    freshness: str
    #: Whether the text carried checkable specifics -- numbers, systems, names,
    #: mechanisms. Section 6.1's own words, and the whole of what separates an
    #: E0 assertion from an E1 self-report with specificity. Decided where the
    #: text is, because the ledger stores a locator and never the sentence.
    has_specifics: bool = False

    @property
    def tier(self) -> str:
        """This piece of evidence's Runbook tier, E0 to E5 (section 6.1).

        DERIVED, never stored, from the three fields above. Storing it would
        create a second place where a tier could be set, and the one thing
        section 14.1's control cannot survive is a tier somebody assigned by
        hand: it decides whether a Must-have is Unassessed, which decides
        whether the candidate can be delivered as Ready to Pick at all.
        """
        from app.services.evidence import tiers

        return tiers.tier_for(
            source_type=self.source_kind,
            trust=self.trust,
            has_specifics=self.has_specifics,
        )


@dataclass(frozen=True)
class EvaluatorInput:
    """EVERYTHING one dimension evaluator is allowed to know. Nothing else.

    THE FIELD LIST IS THE SECURITY BOUNDARY. What is deliberately absent:

      * the candidate's name, id, email, or any identifier;
      * the other four dimensions' scores;
      * the composite, the grade, or any prior grade;
      * a free-form `context` dict that any of the above could be smuggled
        through.

    Frozen, so a caller cannot attach an attribute after construction, and
    `test_miti_pipeline.py` asserts the field list by name -- a future field
    called `notes` would pass every existing test and reopen the whole hole,
    so the check is on the SET of fields rather than on the absence of specific
    ones.
    """

    dimension: str
    #: Only the competencies routed to THIS dimension.
    competencies: tuple[str, ...]
    #: The seniority-appropriate anchor from the department model.
    rubric_anchor: str
    #: Only evidence mapped to this dimension's competencies.
    evidence: tuple[EvidenceView, ...]
    #: The role's situation, as a sentence. Included because Role & Context Fit
    #: is unanswerable without it and the others are sharper with it -- and it
    #: says nothing about the candidate.
    role_context: str = ""

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONS:
            raise ValueError(
                f"Unknown dimension {self.dimension!r}. An evaluator with no "
                f"defined question cannot produce a defensible score."
            )

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    @property
    def independence_count(self) -> int:
        """How many INDEPENDENT sources are present.

        The number that matters for sufficiency. Ten pieces of evidence from one
        group is one source repeating itself, and counting it as ten is exactly
        how a confidently-written resume becomes a well-corroborated candidate.
        """
        return len({e.independence_group for e in self.evidence})


@dataclass
class DimensionResult:
    """One evaluator's answer.

    `evidence_refs` is mandatory in practice even though the dataclass cannot
    enforce a non-empty tuple: `aggregation` refuses a result that cites nothing,
    because a score with no citation is a score Siddhi cannot write a sentence
    about, and an uncitable score is one that will be reported without a citation
    or not reported at all.
    """

    dimension: str
    band: str
    #: What the evaluator actually saw, by ledger ref. THE citation trail.
    evidence_refs: tuple[str, ...]
    #: 25-30 words, the product's standing remark length for an item-level note.
    #: Internal: it feeds Siddhi, and Siddhi rewrites for the client.
    rationale: str = ""
    #: True when the evaluator found the evidence insufficient to judge, as
    #: opposed to finding it negative. THE TWO ARE NOT THE SAME and conflating
    #: them is the fairness failure spec-doc5 specifically warns against:
    #: insufficient evidence must reduce CONFIDENCE, never silently reduce the
    #: score. `aggregation` reads this flag and does exactly that.
    insufficient_evidence: bool = False
    #: Per-competency bands within the dimension, when the evaluator gave them.
    per_competency: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> int:
        return band_for(self.band)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "band": self.band,
            "evidence_refs": list(self.evidence_refs),
            "insufficient_evidence": self.insufficient_evidence,
            "per_competency": dict(self.per_competency),
        }


# ── The prompt ───────────────────────────────────────────────────────────────

_SYSTEM = """You are one evaluator on a hiring assessment. You judge ONE dimension and nothing else.

THE DIMENSION YOU ARE JUDGING: {label}
THE QUESTION YOU ARE ANSWERING: {question}

What good looks like at this level:
{anchor}

You will be shown only the evidence relevant to your dimension. You do not know the candidate's name, you do not know how they scored on anything else, and you have no overall picture. That is intentional. Judge what is in front of you.

RULES
1. Distinguish INSUFFICIENT EVIDENCE from NEGATIVE EVIDENCE. If the evidence does not let you answer the question, say so by setting insufficient_evidence true. Do NOT give a low band because you were shown little; a low band means the evidence you have points the wrong way.
2. Corroboration counts by INDEPENDENT SOURCE, not by volume. Several pieces from one source group are one source.
3. Cite. Every band you give must list the evidence refs you used.
4. A claim is not a fact. A statement on a resume is what someone asserts; treat it as such.
5. Never infer or reason about age, gender, religion, caste, marital status, nationality, race, disability or sexual orientation. If a piece of evidence implies one, ignore that aspect entirely.

BANDS: strong, solid, partial, absent.

Return one JSON object:
{{"band": "...", "rationale": "25-30 words", "insufficient_evidence": false, "evidence_refs": ["..."], "per_competency": {{"competency name": "band"}}}}"""


def render_prompt(payload: EvaluatorInput) -> list[dict[str, str]]:
    """Build the evaluator's messages FROM the input object and nothing else.

    Note what this function does not take: no session, no candidate, no report.
    It cannot reach anything `EvaluatorInput` does not carry, which is what makes
    the dataclass's field list a real boundary rather than a convention.
    """
    system = _SYSTEM.format(
        label=DIMENSION_LABELS[payload.dimension],
        question=DIMENSION_QUESTIONS[payload.dimension],
        anchor=payload.rubric_anchor or "(no seniority anchor available)",
    )
    lines: list[str] = []
    if payload.role_context:
        lines.append(f"THE ROLE: {payload.role_context}")
        lines.append("")
    lines.append("COMPETENCIES YOU ARE JUDGING:")
    for name in payload.competencies:
        lines.append(f"  - {name}")
    lines.append("")
    if payload.evidence:
        lines.append("EVIDENCE:")
        for view in payload.evidence:
            lines.append(
                f"  [{view.ref}] ({view.source_kind}, {view.trust}, {view.freshness}, "
                f"source group {view.independence_group})"
            )
            lines.append(f"      {view.text}")
        lines.append("")
        lines.append(
            f"That is {payload.evidence_count} piece(s) of evidence from "
            f"{payload.independence_count} independent source group(s)."
        )
    else:
        lines.append(
            "EVIDENCE: none was mapped to this dimension. That is a reason to "
            "set insufficient_evidence, not a reason to grade low."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
    ]


def route_evidence(
    evidence: Sequence[EvidenceView],
    competency_dimensions: Mapping[str, str],
    evidence_competencies: Mapping[str, Sequence[str]],
    dimension: str,
) -> tuple[EvidenceView, ...]:
    """Only the evidence mapped to competencies belonging to `dimension`.

    The routing half of isolation. `EvaluatorInput` cannot HOLD the wrong
    evidence's neighbours; this is what stops the wrong evidence being put in it.
    """
    wanted = {
        name for name, dim in competency_dimensions.items() if dim == dimension
    }
    return tuple(
        view
        for view in evidence
        if wanted & set(evidence_competencies.get(view.ref, ()))
    )
