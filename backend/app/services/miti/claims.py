"""Stage 2: NORMALISE & EXTRACT -- claim extraction, materiality, competency mapping.

WHAT A CLAIM IS
---------------
A CLAIM IS NOT A FACT. That distinction is the whole evidence model (Runbook
Part II) and it is the thing this stage exists to preserve. "Led the migration
to Kafka" on a resume is an assertion by an interested party. It may be true. It
is not evidence that it is true, and a pipeline that files it as a fact has
already lost the ability to ask whether it holds up.

So this stage produces `Claim` objects -- subject, predicate, the competency
they bear on, how material they are -- and never verdicts. The evidence
supporting or contradicting each claim is stage 3's job, and whether it holds is
stage 4 and 5's.

THIS STAGE MUST NOT EVALUATE (Runbook §57.1, and it is why it runs on Haiku)
------------------------------------------------------------------------------
spec-doc5 §B.3 assigns claim extraction to Haiku 4.5 with the note "narrow,
mechanical, must-not-evaluate". The model tier is a consequence of that rule,
not the reason for it.

The reason is ordering. If extraction is allowed to form an opinion -- "this
claim seems inflated" -- that opinion enters the pipeline BEFORE the dimension
evaluators, which are the only components allowed to hold one, and it enters
without a rubric, without the isolation those evaluators run under, and without
a citation. Downstream it is indistinguishable from a finding. `Claim` therefore
has no score field, no confidence field and no assessment field, and
`test_miti_pipeline.py` asserts the field list -- the same structural technique
`EvaluatorInput` uses, for the same reason.

MATERIALITY IS NOT IMPORTANCE
-------------------------------
`materiality` answers "how much does this claim matter to the DECISION", which
is a different question from "how impressive is it". A claim about a Must-have
competency is material because getting it wrong caps the report; a claim about a
technology mentioned once in passing is not, however senior it sounds. It is
derived from the matrix, deterministically, and never from the claim's own
wording -- otherwise a confidently written resume would rate itself material.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "MATERIALITY_CRITICAL",
    "MATERIALITY_HIGH",
    "MATERIALITY_MODERATE",
    "MATERIALITY_LOW",
    "MATERIALITIES",
    "Claim",
    "materiality_for",
    "extraction_prompt",
    "parse_claims",
    "SUBJECT_SELF",
    "SUBJECT_TEAM",
    "SUBJECT_AMBIGUOUS",
]

# ── Materiality ──────────────────────────────────────────────────────────────

MATERIALITY_CRITICAL = "critical"   # a Must-have; getting it wrong caps the report
MATERIALITY_HIGH = "high"
MATERIALITY_MODERATE = "moderate"
MATERIALITY_LOW = "low"

MATERIALITIES: tuple[str, ...] = (
    MATERIALITY_CRITICAL,
    MATERIALITY_HIGH,
    MATERIALITY_MODERATE,
    MATERIALITY_LOW,
)

_BY_CATEGORY: dict[str, str] = {
    "must_have": MATERIALITY_CRITICAL,
    "nice_to_have": MATERIALITY_MODERATE,
    "behavioural": MATERIALITY_HIGH,
}


def materiality_for(
    competencies: Sequence[str], matrix: Mapping[str, str]
) -> str:
    """Materiality from the MATRIX, never from the claim's own wording.

    `matrix` is {competency name: category}. A claim bearing on several
    competencies takes the highest materiality among them, because the cost of
    being wrong is set by the most consequential thing it touches.

    A claim mapped to NOTHING is LOW rather than dropped. Dropping it would lose
    a piece of the candidate's account for the crime of not matching a matrix
    item, and Miti's triangulation stage legitimately reads claims the matrix
    does not grade -- an inconsistency in an ungraded claim is still an
    inconsistency.
    """
    best = MATERIALITY_LOW
    order = {name: index for index, name in enumerate(MATERIALITIES)}
    for name in competencies:
        category = matrix.get(name)
        if category is None:
            continue
        candidate = _BY_CATEGORY.get(category, MATERIALITY_LOW)
        if order[candidate] < order[best]:
            best = candidate
    return best


# ── Attribution ──────────────────────────────────────────────────────────────
#
# WHO DID THE THING. Recorded because it is the single most common way a true
# statement is misleading: "we migrated to Kafka" and "I migrated us to Kafka"
# are both true of the same person and mean very different things about them.
# `tiering` reads this as the attribution modifier.

SUBJECT_SELF = "self"           # "I built", "I decided"
SUBJECT_TEAM = "team"           # "we shipped", "the team delivered"
SUBJECT_AMBIGUOUS = "ambiguous"  # "the migration was completed"

_SELF = re.compile(r"\b(i|my|me|myself)\b", re.I)
_TEAM = re.compile(r"\b(we|our|us|the team|my team)\b", re.I)


def infer_subject(text: str) -> str:
    """Deterministic, and deliberately so.

    A grammatical property of a sentence needs no model, and having a model do
    it would let attribution -- a judgment with direct scoring consequences --
    vary between runs.
    """
    body = text or ""
    has_self = bool(_SELF.search(body))
    has_team = bool(_TEAM.search(body))
    if has_self and not has_team:
        return SUBJECT_SELF
    if has_team and not has_self:
        return SUBJECT_TEAM
    if has_self and has_team:
        # "We migrated and I owned the rollout." Both present: the self-claim is
        # the specific one and is what a scorer should read, but the ambiguity
        # is real and is recorded rather than resolved.
        return SUBJECT_AMBIGUOUS
    return SUBJECT_AMBIGUOUS


# ── The claim ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Claim:
    """One assertion, mapped to what it bears on.

    NOTE THE FIELD LIST. There is no `score`, no `confidence`, no `assessment`,
    no `plausibility`. Extraction must not evaluate, and a field it could write
    an opinion into is a field an opinion eventually appears in.
    """

    #: A stable id, so evidence and dimension results can cite it.
    ref: str
    #: The assertion, in the candidate's own terms. NOT rewritten: a summary of
    #: a claim is not the claim, and a scorer reading a paraphrase is scoring
    #: the paraphraser.
    text: str
    #: Where it came from: resume | answer | validation | jd | swot.
    source_kind: str
    #: The ledger row this claim was extracted from.
    source_ref: str
    #: Matrix competencies this claim bears on. May be empty.
    competencies: tuple[str, ...] = ()
    materiality: str = MATERIALITY_LOW
    subject: str = SUBJECT_AMBIGUOUS
    #: Whether the claim carries a checkable specific -- a number, a named
    #: system, a date. Not a quality judgment: a specific claim is EASIER TO
    #: CHECK, which is what makes it worth more as evidence, and that is
    #: `tiering`'s specificity modifier.
    has_specifics: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "competencies": list(self.competencies),
            "materiality": self.materiality,
            "subject": self.subject,
            "has_specifics": self.has_specifics,
            # `text` is deliberately ABSENT from the dict projection. This shape
            # travels into traces, and a trace never carries content -- the same
            # rule `agent_execution_traces` follows by dropping a defect's
            # detail. The text is reachable through `source_ref`, under the
            # capability that guards the transcript.
        }


#: A number, a date, a version, a named product. What makes a claim checkable.
_SPECIFIC = re.compile(
    r"(\b\d[\d,.]*\s*(%|percent|ms|s\b|k\b|m\b|x\b|hours?|days?|weeks?|months?|years?)"
    r"|\b(19|20)\d{2}\b"
    r"|\bv?\d+\.\d+\b)",
    re.I,
)


def has_specifics(text: str) -> bool:
    return bool(_SPECIFIC.search(text or ""))


# ── The extraction call ──────────────────────────────────────────────────────

_SYSTEM = """You extract claims from hiring material. You do not evaluate them.

A CLAIM is something the candidate asserts about what they have done, known, or been responsible for. Extract it as close to their own words as you can while making it a standalone sentence.

YOU ARE NOT JUDGING ANYTHING. Do not say whether a claim is impressive, plausible, inflated, vague or strong. Do not score. Do not rank. If you find yourself forming an opinion about the candidate, you have exceeded this job: something later in the pipeline does that, with a rubric and with the evidence in front of it, and your opinion would reach it uncited and unchecked.

Also extract:
  - subject: "self" if they say they did it, "team" if they say the team did, "ambiguous" if it cannot be told.
  - competencies: which of the listed competencies, if any, this claim bears on. Empty list is a correct answer.

Do NOT extract: opinions about themselves ("I'm a fast learner"), preferences, or statements about what they want. Those are not claims about what happened.

Return one JSON object: {"claims": [{"text": "...", "subject": "self|team|ambiguous", "competencies": ["..."]}]}"""


def extraction_prompt(
    *, text: str, source_kind: str, competencies: Sequence[str]
) -> list[dict[str, str]]:
    listed = "\n".join(f"  - {name}" for name in competencies) or "  (none)"
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"COMPETENCIES THIS ROLE IS ASSESSED ON:\n{listed}\n\n"
                f"SOURCE: {source_kind}\n\n"
                f"TEXT:\n{text}"
            ),
        },
    ]


def parse_claims(
    payload: Mapping[str, Any],
    *,
    source_kind: str,
    source_ref: str,
    matrix: Mapping[str, str],
    prefix: str = "c",
) -> list[Claim]:
    """Turn the model's JSON into `Claim` objects, deriving what it must not.

    THREE FIELDS ARE DERIVED HERE AND NEVER TAKEN FROM THE MODEL:

      * `materiality`, from the matrix -- a model asked how material its own
        extraction is would answer from the claim's tone;
      * `has_specifics`, by regex -- a checkable property of the text; and
      * `subject`, cross-checked against `infer_subject`, with the DETERMINISTIC
        answer winning on disagreement. Attribution has direct scoring
        consequences and is a grammatical property, so it should not vary
        between runs.

    A malformed row is skipped rather than raising: an extraction that returned
    nine good claims and one broken one should yield nine, not zero.
    """
    claims: list[Claim] = []
    rows = payload.get("claims")
    if not isinstance(rows, list):
        return claims
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        names = tuple(
            str(name).strip()
            for name in (row.get("competencies") or [])
            if str(name or "").strip() in matrix
        )
        derived_subject = infer_subject(text)
        claimed_subject = str(row.get("subject") or "").strip().lower()
        subject = (
            derived_subject
            if derived_subject != SUBJECT_AMBIGUOUS
            else (
                claimed_subject
                if claimed_subject in {SUBJECT_SELF, SUBJECT_TEAM, SUBJECT_AMBIGUOUS}
                else SUBJECT_AMBIGUOUS
            )
        )
        claims.append(
            Claim(
                ref=f"{prefix}{index}",
                text=text,
                source_kind=source_kind,
                source_ref=source_ref,
                competencies=names,
                materiality=materiality_for(names, matrix),
                subject=subject,
                has_specifics=has_specifics(text),
            )
        )
    return claims
