"""Yukti's fixed structure: six parts, their weights, and every limit. DATA.

Owner decision D2 (Vivekium release): Yukti scores a resume on a FIXED six-part
structure that lives only in backend configuration. Recruiters cannot see, edit
or re-prioritise it: there are no High/Medium/Low toggles and no editable
categories, and nothing in `app/api` or `app/schemas` may import this module
(`tests/test_yukti_config.py` walks their imports to keep it that way). The
recruiter sees a word grade, evidence tags and a provenance line in words.

THE SIX PARTS
-------------
1. must_have_evidenced     the job's Must-have skills, judged from the resume
2. nice_to_have_evidenced  the job's Nice-to-have skills, judged likewise
3. experience_level        the resume against the job's grade and band
4. role_fit                the resume against what the role actually does
5. company_need_fit        the resume against a NAMED need from the saved SWOT
6. validation_fit          the application answers, deterministic code only

Parts one to five come from ONE model reading per batch (`judge.py`), every
claim of evidence grounded deterministically in the resume text
(`grounding.py`). Part six never reaches a model. Behavioural skills are not a
part at all, because a resume cannot prove behaviour; the assessment tests
them. Education is not a part either: a JD that requires a qualification has it
carried by Sutra as a Must-have skill.

WHAT A MISSING PART MEANS
-------------------------
A part with nothing to judge (a job with no Nice-to-have skills, a sourced
candidate who never answered the application questions, a job whose SWOT names
no need) is EXCLUDED and the remaining weights renormalise. It is never scored
zero, because a zero is arithmetically identical to negative evidence, and "we
did not ask" is not "the answer was bad" (the rule `prescreen` and Miti already
follow). Every exclusion is recorded in words in the provenance.

The weights and value tables are ASSUMPTIONS the owner has not ruled on
(PLAN-p2 owner questions Q1 to Q3); they are data here, so a ruling is an edit
to this file and nothing else.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from app.services import application_validation, recruiter_columns

# ── The six parts ────────────────────────────────────────────────────────────

COMPONENT_MUST_HAVE = "must_have_evidenced"
COMPONENT_NICE_TO_HAVE = "nice_to_have_evidenced"
COMPONENT_EXPERIENCE = "experience_level"
COMPONENT_ROLE_FIT = "role_fit"
COMPONENT_COMPANY_NEED = "company_need_fit"
COMPONENT_VALIDATION = "validation_fit"

#: In this order, everywhere: the prompt, the provenance and the tests.
COMPONENTS: tuple[str, ...] = (
    COMPONENT_MUST_HAVE,
    COMPONENT_NICE_TO_HAVE,
    COMPONENT_EXPERIENCE,
    COMPONENT_ROLE_FIT,
    COMPONENT_COMPANY_NEED,
    COMPONENT_VALIDATION,
)

#: The parts a model judges. `validation_fit` is deterministic code only.
MODEL_COMPONENTS: tuple[str, ...] = COMPONENTS[:5]

#: ASSUMPTION (PLAN-p2 Q1, owner question): the relative weight of each part,
#: summing to 100. Must-have evidence dominates because a Must-have is the one
#: thing the hiring team said the role cannot be done without; company-need
#: fit is small because a SWOT names at most a handful of needs and most
#: resumes speak to none of them, which must not read as a weakness.
COMPONENT_WEIGHTS: Mapping[str, int] = MappingProxyType(
    {
        COMPONENT_MUST_HAVE: 40,
        COMPONENT_NICE_TO_HAVE: 15,
        COMPONENT_EXPERIENCE: 15,
        COMPONENT_ROLE_FIT: 15,
        COMPONENT_COMPANY_NEED: 5,
        COMPONENT_VALIDATION: 10,
    }
)

# ── The model's three-word vocabulary ────────────────────────────────────────

VERDICT_STRONG = "strong"
VERDICT_SOME = "some"
VERDICT_NONE = "none"
VERDICTS: tuple[str, ...] = (VERDICT_STRONG, VERDICT_SOME, VERDICT_NONE)

#: What a verdict is worth inside a part, 0 to 1. The model never sees or
#: returns a number: it returns one of three words and this table converts it,
#: server side, the way the four-grade scale converts a score for display.
VERDICT_VALUES: Mapping[str, float] = MappingProxyType(
    {VERDICT_STRONG: 1.0, VERDICT_SOME: 0.5, VERDICT_NONE: 0.0}
)

# ── Validation fit (part six) ────────────────────────────────────────────────

VALIDATION_CTC = "ctc"
VALIDATION_NOTICE = "notice"
VALIDATION_DOCUMENTS = "documents"
VALIDATION_PARTS: tuple[str, ...] = (
    VALIDATION_CTC,
    VALIDATION_NOTICE,
    VALIDATION_DOCUMENTS,
)

#: ASSUMPTION (PLAN-p2 Q2): the weight of each answer inside part six.
VALIDATION_SUBWEIGHTS: Mapping[str, int] = MappingProxyType(
    {VALIDATION_CTC: 50, VALIDATION_NOTICE: 30, VALIDATION_DOCUMENTS: 20}
)

#: ASSUMPTION (PLAN-p2 Q2): what each answer is worth, keyed by the EXACT words
#: the deterministic columns produce, imported rather than retyped, so the
#: table and the recruiter's CTC and Notice columns cannot drift apart
#: (`tests/test_yukti_config.py` rebuilds both key sets from the live option
#: lists). Below range is worth as much as within range: a candidate asking
#: for less than the budget is not a worse fit for the budget.
_CTC_VALUES: dict[str, float] = {
    recruiter_columns.CTC_WITHIN: 1.0,
    recruiter_columns.CTC_BELOW: 1.0,
    recruiter_columns.CTC_ABOVE: 0.0,
}

#: Keyed by `recruiter_columns.notice_period_bucket` output. "Above 90 days" is
#: reachable only from a free-text answer submitted before the select existed.
_NOTICE_VALUES: dict[str, float] = {
    "Immediate": 1.0,
    "Within 30 days": 1.0,
    "Serving notice": 0.8,
    "30 to 60 days": 0.6,
    "60 to 90 days": 0.3,
    "Above 90 days": 0.0,
}

#: Keyed by `application_validation.DOCUMENT_READINESS_OPTIONS`, in form order.
_DOCUMENT_VALUES: dict[str, float] = dict(
    zip(application_validation.DOCUMENT_READINESS_OPTIONS, (1.0, 0.75, 0.4, 0.1))
)

VALIDATION_VALUES: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        VALIDATION_CTC: MappingProxyType(_CTC_VALUES),
        VALIDATION_NOTICE: MappingProxyType(_NOTICE_VALUES),
        VALIDATION_DOCUMENTS: MappingProxyType(_DOCUMENT_VALUES),
    }
)

# ── The model call ───────────────────────────────────────────────────────────

#: The router task type: Terra, because Yukti JUDGES evidence (Phase 2 moved it
#: off the Luna `rerank` hint, which existed to be fast and orders a list it
#: does not grade). Registered in `config/llm_providers`.
TASK_TYPE = "yukti_matching"

#: The system prompt, `app/prompts/yukti_matching_system.txt`.
PROMPT_NAME = "yukti_matching_system"

#: Candidates per model call. Every candidate in a batch is judged against the
#: same skills and needs, so the prompt's fixed part is paid once per batch.
BATCH_SIZE = 5

#: The resume a model reads, in characters, cut on a LINE boundary and never
#: mid-line, so a quote the model copies is always a whole line or part of one.
RESUME_CHARS = 6000

#: The job description a model reads, cut the same way.
JD_CHARS = 6000

#: Needs read from the saved SWOT: Weaknesses first, then Opportunities, then
#: Threats. A sentence longer than the cap is dropped WHOLE rather than cut,
#: because a model handed half a sentence completes it from its own priors.
MAX_NEEDS = 12
MAX_NEED_CHARS = 320
NEED_SOURCES: tuple[str, ...] = ("weakness", "opportunity", "threat")

# ── Grounding and tags ───────────────────────────────────────────────────────

#: A quote shorter than this (in normalised words) grounds nothing: two words
#: are found in almost any resume, which would make grounding a formality.
MIN_QUOTE_WORDS = 3
#: A quote longer than this is refused as a quote. The model is asked for the
#: shortest passage that shows the evidence, not for the resume back.
MAX_QUOTE_CHARS = 300

#: A model-written tag (experience, role fit, a company need): at most this
#: many words and characters. Skill tags are never model-written: they store
#: the skill id and the name is resolved from the live row at read time.
MAX_TAG_WORDS = 5
MAX_TAG_CHARS = 40

#: How many tags a ranked-table row shows before "Details". Display data, read
#: by the projection (Phase 2 WP-C), declared here with the other tag limits.
MAX_TAGS_SHOWN_IN_ROW = 4

POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"

TAG_KIND_SKILL = "skill"
TAG_KIND_EXPERIENCE = "experience"
TAG_KIND_ROLE_FIT = "role_fit"
TAG_KIND_COMPANY_NEED = "company_need"

# ── Status and failure vocabulary (stored on the link) ───────────────────────

STATUS_PENDING = "pending"
STATUS_SCORED = "scored"
STATUS_NOT_ASSESSED = "not_assessed"
STATUS_LEGACY = "legacy"
STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_SCORED,
    STATUS_NOT_ASSESSED,
    STATUS_LEGACY,
)

#: Why a link is `not_assessed`. Stored in `yukti_failure_reason` (40 chars).
FAILURE_NO_RESUME = "no_resume"
FAILURE_NO_RESUME_TEXT = "no_resume_text"
FAILURE_MODEL_UNAVAILABLE = "model_unavailable"
FAILURE_MODEL_OUTPUT_INVALID = "model_output_invalid"
FAILURE_NO_GROUNDED_EVIDENCE = "no_grounded_evidence"
FAILURE_REASONS: tuple[str, ...] = (
    FAILURE_NO_RESUME,
    FAILURE_NO_RESUME_TEXT,
    FAILURE_MODEL_UNAVAILABLE,
    FAILURE_MODEL_OUTPUT_INVALID,
    FAILURE_NO_GROUNDED_EVIDENCE,
)

#: The two failures that say nothing about the candidate: the model could not
#: be reached, or twice returned something unusable. A prior `scored` result
#: for the same resume and the same contract survives one of these rather than
#: being overwritten by it (`scoring.keep_prior`).
TRANSIENT_FAILURES: frozenset[str] = frozenset(
    {FAILURE_MODEL_UNAVAILABLE, FAILURE_MODEL_OUTPUT_INVALID}
)

#: Why a part was excluded, recorded in provenance.
EXCLUDED_NO_SKILLS = "no_skills_in_bucket"
EXCLUDED_UNGROUNDED = "no_grounded_evidence"
EXCLUDED_NO_NEEDS = "no_named_needs"
EXCLUDED_NOT_ANSWERED = "not_answered"


# ── Invariants, checked at import ────────────────────────────────────────────


def _check() -> None:
    """Refuse to import a structure that does not add up.

    A module-level check rather than only a test, because a weight table that
    sums to 99 would still rank candidates, silently, in an order nobody
    designed; importing it must fail on the first request instead.
    """
    if set(COMPONENT_WEIGHTS) != set(COMPONENTS):
        raise RuntimeError("Yukti weights must name exactly the six parts")
    if sum(COMPONENT_WEIGHTS.values()) != 100:
        raise RuntimeError("Yukti part weights must sum to 100")
    if any(weight <= 0 for weight in COMPONENT_WEIGHTS.values()):
        raise RuntimeError("every Yukti part must carry a positive weight")
    if set(VALIDATION_SUBWEIGHTS) != set(VALIDATION_PARTS):
        raise RuntimeError("validation sub-weights must name exactly its parts")
    if sum(VALIDATION_SUBWEIGHTS.values()) != 100:
        raise RuntimeError("validation sub-weights must sum to 100")
    for part, table in VALIDATION_VALUES.items():
        if part not in VALIDATION_PARTS:
            raise RuntimeError(f"unknown validation part {part!r}")
        if any(not 0.0 <= value <= 1.0 for value in table.values()):
            raise RuntimeError(f"validation values for {part!r} must lie in 0..1")
    if len(_DOCUMENT_VALUES) != len(application_validation.DOCUMENT_READINESS_OPTIONS):
        raise RuntimeError("every document readiness option needs a value")
    if set(VERDICT_VALUES) != set(VERDICTS):
        raise RuntimeError("every verdict needs a value")


_check()
