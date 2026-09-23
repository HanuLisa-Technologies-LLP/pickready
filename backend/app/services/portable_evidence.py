"""Portable Intelligence: what the product knows about a PERSON, across jobs.

THE OWNER RULING, 2026-09-22
------------------------------
The owner chose the full Portable plus Job Specific split, knowingly reversing
the 2026-07-30 retirement of cross-job reuse. `services/retake.py` carries the
reversal in place, with the reason the old rule was right and the reason it no
longer binds. This module is the Portable half.

TWO LAYERS, AND THE LINE BETWEEN THEM IS THE WHOLE DESIGN
-----------------------------------------------------------
PORTABLE INTELLIGENCE is a fact about a person that did not happen inside a
job: their career history, their education, an employment a previous employer
confirmed, factual profile information, and the core skills their resume
claims. None of those change because a different company is now reading them.

JOB SPECIFIC INTELLIGENCE is everything that only exists relative to one
opening: role specific capability as THIS matrix defines it, every behavioural
dimension, leadership requirements, scenario responses, and alignment with this
employer's own strategy. None of those are portable, and the first three of
them are refused here structurally rather than by anybody remembering.

WHAT TRAVELS IS EVIDENCE. A VERDICT NEVER TRAVELS
---------------------------------------------------
This is the constraint the feature stands or falls on, and it is the one the
2026-07-30 retirement was written to protect:

    a prior job's SCORE or GRADE is never reused as evidence, for anything.

Job A's grade was reached against a matrix generated from job A's JD, by a
rubric written for job A's questions. Reusing it would state a verdict about
criteria the candidate was never assessed on. What this module carries is the
raw fact underneath ("their resume claims Kafka, and here is where it says
so"), which the NEW job's matrix then judges for itself, with its own required
level, its own rubric and its own grade.

Four mechanisms enforce it, and only the first one matters:

1.  `portable_evidence_items` HAS NO VERDICT COLUMN. Not score, grade, band,
    percent, rating, level, tier, composite or verdict. There is nowhere for
    one to be written. `tests/test_portable_evidence.py` reads
    `information_schema.columns` and fails on one appearing.
2.  This module imports no report model, no scorer and no aggregator, and a
    test asserts that over the import graph. It cannot read a grade even if it
    wanted one.
3.  `record` sweeps the `facts` payload for verdict-shaped KEYS and the
    `statement` for the four grade WORDS, and raises rather than storing.
4.  `retake.PORTABLE_CATEGORIES` stays an empty frozenset and `copy_report`
    still copies nothing. No report SECTION travels. That has not changed and
    this ruling does not change it.

CONSENT
---------
Reuse is gated on `candidates.retain_assessment_consent` through
`retention_consent.reuse_across_jobs_allowed`, which is True only for an
explicit stored True. A never-asked NULL reads as no, which is the safe
direction and the one the consent module already takes everywhere else.
`docs`-level findings about how far that consent actually reaches are in the
change report; nothing here widens it.

TENANT ISOLATION
------------------
There is no tenant column, for the reason `candidate_employments` has none: a
person's education did not happen inside a tenant. The RLS policy is BYPASS
ONLY, which is deliberately stricter than `bgv_inquiries`, whose policy admits
a tenant session and leans on an application level consent check. Portable
evidence has exactly two readers, and both already run in an audited bypass
scope: the candidate's own portal session (`get_candidate_db`) and the
assessment worker. No recruiter route reads this table; what a recruiter sees
is the PRISM report, which is tenant scoped already. So a tenant equality arm
in the policy would be a door nobody walks through, and a door nobody walks
through is a door nobody notices being used.

Every read here takes a candidate id as a REQUIRED argument. There is no
"load everything" function, and `load_for_link` additionally requires the
employer to hold a `job_candidate_links` row for that candidate, which is the
same legitimacy test `candidates.get_bgv_results` already makes.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rating import GRADES

logger = logging.getLogger(__name__)

__all__ = [
    "PortableEvidenceError",
    "KIND_CAREER_HISTORY",
    "KIND_EDUCATION",
    "KIND_EMPLOYMENT_CONFIRMED",
    "KIND_PROFILE_FACT",
    "KIND_CORE_SKILL",
    "PORTABLE_KINDS",
    "COVERING_KINDS",
    "NEVER_COVERED_CATEGORIES",
    "JOB_SPECIFIC_MARKERS",
    "SOURCE_RESUME",
    "SOURCE_VALIDATION",
    "SOURCE_BGV",
    "SOURCE_PROFILE",
    "SOURCE_KINDS",
    "STATUS_ACTIVE",
    "STATUS_SUPERSEDED",
    "STATUS_REVOKED",
    "PortableFact",
    "Covered",
    "Coverage",
    "criterion_is_job_specific",
    "coverage",
    "coverage_sentence",
    "prefill_text",
    "record",
    "load_for_candidate",
    "load_for_link",
    "harvest",
]


class PortableEvidenceError(ValueError):
    """A write that would have put a verdict, or an unknown kind, on the record.

    Raised rather than logged. A portable row is read by every employer the
    candidate ever applies to; a bad one that shipped with a warning beside it
    would outlive the warning by years.
    """


# ── The portable vocabulary ──────────────────────────────────────────────────

KIND_CAREER_HISTORY = "career_history"
KIND_EDUCATION = "education"
KIND_EMPLOYMENT_CONFIRMED = "employment_confirmed"
KIND_PROFILE_FACT = "profile_fact"
KIND_CORE_SKILL = "core_skill"

#: The closed list. A kind outside it is refused at `record` and by a database
#: CHECK, because "what may be reused across employers" is a product decision
#: and should cost a reviewed line rather than defaulting into permission.
PORTABLE_KINDS: frozenset[str] = frozenset(
    {
        KIND_CAREER_HISTORY,
        KIND_EDUCATION,
        KIND_EMPLOYMENT_CONFIRMED,
        KIND_PROFILE_FACT,
        KIND_CORE_SKILL,
    }
)

#: Which kinds may ESTABLISH a matrix criterion, which is a narrower question
#: than which kinds may be STORED. A profile fact ("notice period: 60 days") is
#: portable and is worth carrying; it evidences no capability, so it can never
#: stand in for asking about one.
COVERING_KINDS: frozenset[str] = frozenset(
    {
        KIND_CORE_SKILL,
        KIND_CAREER_HISTORY,
        KIND_EDUCATION,
        KIND_EMPLOYMENT_CONFIRMED,
    }
)

#: THE CATEGORY THAT IS NEVER COVERED, WHATEVER THE EVIDENCE SAYS.
#:
#: Every behavioural dimension is freshly assessed on every application. Not
#: because the evidence would be weak, but because a behavioural competency is
#: graded on the account a person gives of a situation, under this job's
#: framing, and an account given for another role is an answer to another
#: question. It is also the dimension a candidate can most easily have changed
#: on since.
#:
#: Restated as a LITERAL rather than imported from `ppi`: `ppi` imports this
#: module's sibling `resume_prefill` and reaches the whole assessment stack,
#: and this module has to stay importable from the candidate portal.
#: `tests/test_portable_evidence.py` pins it against `ppi.CATEGORY_BEHAVIOURAL`
#: in both directions, which is the `evidence_confidence` bargain: the copy
#: costs an assertion, the import would cost a cycle.
NEVER_COVERED_CATEGORIES: frozenset[str] = frozenset({"behavioural"})

#: The rest of the Job Specific layer, as three VOCABULARIES rather than a list
#: of criterion names. A criterion nobody has seen before resolves from the
#: words it is made of, which is the lesson `stem_classification` wrote down:
#: do not "fix" a misclassification by adding the name.
#:
#: THE BIAS IS DELIBERATE AND IT IS TOWARDS ASKING. "Configuration management"
#: matches `management` and is read as job specific, so the candidate is asked
#: about it. That costs one question. The opposite error, reading a leadership
#: requirement as covered by a resume line, costs a grade decided on evidence
#: nobody put to the person. Only one of those two is recoverable, so the
#: vocabulary errs wide.
JOB_SPECIFIC_MARKERS: frozenset[str] = frozenset(
    {
        # Leadership requirements
        "lead", "leads", "leading", "leadership", "manage", "manages",
        "managing", "management", "mentor", "mentoring", "coaching",
        "delegation", "stakeholder", "stakeholders", "influence", "hiring",
        "headcount", "succession",
        # Scenario responses
        "scenario", "scenarios", "situational", "judgement", "judgment",
        "tradeoff", "tradeoffs", "prioritisation", "prioritization",
        "escalation", "incident", "conflict", "negotiation", "crisis",
        "ambiguity", "decision", "decisions",
        # Employer or job specific strategic alignment
        "strategy", "strategic", "roadmap", "vision", "alignment",
        "transformation", "organisational", "organizational", "stewardship",
    }
)

#: WHO originated the fact. The same distinction `evidence_confidence` counts:
#: a resume line and the candidate repeating it are one originator saying one
#: thing twice, and only `bgv` is somebody other than the candidate.
SOURCE_RESUME = "resume"
SOURCE_VALIDATION = "validation"
SOURCE_BGV = "bgv"
SOURCE_PROFILE = "profile"
SOURCE_KINDS: frozenset[str] = frozenset(
    {SOURCE_RESUME, SOURCE_VALIDATION, SOURCE_BGV, SOURCE_PROFILE}
)

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REVOKED = "revoked"

#: The ledger's four trust levels, restated for the reason the categories are.
#: Pinned against `evidence.ledger` by the test module, in both directions.
TRUST_AUTHORITATIVE = "authoritative"
TRUST_VALIDATED = "validated"
TRUST_OBSERVED = "observed"
TRUST_INFERRED = "inferred"
TRUST_LEVELS: frozenset[str] = frozenset(
    {TRUST_AUTHORITATIVE, TRUST_VALIDATED, TRUST_OBSERVED, TRUST_INFERRED}
)

#: Key names that would let a verdict travel inside the free-form `facts`
#: payload. Swept recursively, and a hit RAISES: the column ban is worthless if
#: the same number can ride along in a JSONB blob one level down.
_VERDICT_KEYS: frozenset[str] = frozenset(
    {
        "score", "scores", "grade", "grades", "rating", "ratings", "rated",
        "band", "bands", "tier", "tiers", "percent", "percentage",
        "match_score", "match_percent", "verdict", "overall", "composite",
        "required_level", "level", "confidence", "assessment", "evaluation",
        "report", "report_id", "result", "rank", "ranking", "weight",
        "threshold", "delivered_score", "adjusted_composite",
    }
)

#: A statement is the product's own wording of a fact, so none of the four
#: client-facing grade words belongs in one. Matched on word boundaries over
#: the whole phrase, so "Not Matching" is caught and "matching criteria" is not.
_GRADE_PHRASES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"(?<!\w){re.escape(grade.casefold())}(?!\w)") for grade in GRADES
)

_TOKEN = re.compile(r"[a-z0-9]+")


# ── The pure layer: mapping portable evidence onto a NEW matrix ──────────────


@dataclass(frozen=True)
class PortableFact:
    """One portable row, as everything downstream needs it.

    A frozen dataclass rather than the ORM row, for the reason
    `miti.EvaluatorInput` is one: the field set is the contract, and a field
    set with no score in it cannot grow one by accident when somebody adds a
    column for a different purpose.
    """

    kind: str
    subject: str
    statement: str
    source_kind: str
    source_ref: str
    trust: str = TRUST_OBSERVED
    observed_on: date | None = None
    facts: Mapping[str, Any] | None = None
    id: uuid.UUID | None = None


class _CriterionLike(Protocol):
    name: str
    category: str


@dataclass(frozen=True)
class Covered:
    """One matrix criterion the portable record already establishes."""

    name: str
    category: str
    fact: PortableFact

    @property
    def provenance(self) -> dict[str, Any]:
        """What the report and the transcript say about where this came from.

        A locator, an originator and a date. No score, because there is none,
        and no candidate prose, because `source_ref` points at it rather than
        quoting it.
        """
        return {
            "layer": "portable",
            "kind": self.fact.kind,
            "subject": self.fact.subject,
            "source_kind": self.fact.source_kind,
            "source_ref": self.fact.source_ref,
            "observed_on": (
                self.fact.observed_on.isoformat() if self.fact.observed_on else None
            ),
        }


@dataclass(frozen=True)
class Coverage:
    """The split of one job's matrix against one candidate's portable record.

    `established` is never allowed to swallow a criterion: every name in the
    matrix is in exactly one of the three tuples, and `assert_total` is what
    says so. A criterion that fell out of all three would be a criterion
    silently dropped from the assessment, which is the failure the standing
    "insufficient evidence is not negative evidence" rule forbids in its other
    direction.
    """

    established: tuple[Covered, ...] = ()
    to_assess: tuple[str, ...] = ()
    behavioural: tuple[str, ...] = ()

    @property
    def established_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.established)

    def by_name(self) -> dict[str, Covered]:
        return {item.name: item for item in self.established}

    def assert_total(self, criteria: Sequence[_CriterionLike]) -> None:
        """Every criterion is accounted for, or this raises.

        Called by `coverage` on its own result. A total function checked
        against its own input is worth more than a docstring promising one:
        the whole risk of this feature is a criterion quietly disappearing
        between the matrix and the conversation.
        """
        seen = set(self.established_names) | set(self.to_assess) | set(self.behavioural)
        missing = [c.name for c in criteria if c.name not in seen]
        if missing:
            raise PortableEvidenceError(
                f"Coverage dropped {missing!r} from the matrix. Every criterion "
                f"is either established by the portable record, queued to be "
                f"assessed, or behavioural; a fourth outcome is a criterion "
                f"nobody grades."
            )


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(str(value or "").casefold()))


def criterion_is_job_specific(name: str) -> bool:
    """Does this criterion's own wording put it in the Job Specific layer?

    Word-boundary tokens, never substrings. The disqualifier detector learned
    this the expensive way: matching substrings refused "Must hold a valid CA
    licence" because "hold" contains "old".
    """
    return bool(_tokens(name) & JOB_SPECIFIC_MARKERS)


def _matches(criterion_name: str, subject: str) -> bool:
    """Does a portable subject speak to this criterion?

    Either direction, on whole tokens: a criterion "Kafka" is established by a
    skill "Kafka", and a criterion "Kafka operations" is established by the
    same skill. A subject that merely SHARES a common word with the criterion
    is not a match, so the overlap has to be the whole of one side.
    """
    left = _tokens(criterion_name)
    right = _tokens(subject)
    if not left or not right:
        return False
    return left <= right or right <= left


def _is_fresh(fact: PortableFact, *, now: datetime, max_age_days: int) -> bool:
    """Is this fact recent enough to stand in for asking?

    A fact with NO `observed_on` is never fresh enough. That is the same rule
    the derived recruiter columns follow: an absent or unparseable input
    renders no comparison, because a fabricated answer beside a hiring decision
    is worse than an honest blank. `recorded_at` is deliberately not used as a
    substitute: when we wrote a fact down is not when it was true, and
    conflating the two is how a 2019 achievement described in a resume uploaded
    yesterday reads as current.
    """
    if fact.observed_on is None:
        return False
    age = (now.date() - fact.observed_on).days
    if age < 0:
        # A clock-skewed future date is not evidence of recency. Treated as
        # today rather than trusted, the same direction `retake.classify_age`
        # takes for the same reason.
        age = 0
    return age < max_age_days


def coverage(
    criteria: Sequence[_CriterionLike],
    facts: Iterable[PortableFact],
    *,
    max_age_days: int,
    now: datetime | None = None,
) -> Coverage:
    """Map a candidate's portable record onto THIS job's frozen matrix.

    Pure, deterministic and model-free. Two runs over the same matrix and the
    same record produce the same split, because a candidate told on Monday
    that their Kafka evidence is established and on Tuesday that it is not has
    been told the product is guessing.

    THE ORDER OF THE TESTS IS THE ENFORCEMENT, and behavioural is first:

        behavioural                 -> never established, whatever exists
        job-specific wording        -> never established, whatever exists
        a fresh covering fact       -> established, with its provenance
        anything else               -> assessed

    Note that nothing here reads a prior score, because `PortableFact` has no
    field that could hold one.
    """
    now = now or datetime.now(timezone.utc)
    usable = [
        fact
        for fact in facts
        if fact.kind in COVERING_KINDS and _is_fresh(fact, now=now, max_age_days=max_age_days)
    ]
    established: list[Covered] = []
    to_assess: list[str] = []
    behavioural: list[str] = []

    for criterion in criteria:
        name = str(criterion.name)
        if str(criterion.category) in NEVER_COVERED_CATEGORIES:
            behavioural.append(name)
            continue
        if criterion_is_job_specific(name):
            to_assess.append(name)
            continue
        match = next((fact for fact in usable if _matches(name, fact.subject)), None)
        if match is None:
            to_assess.append(name)
            continue
        established.append(
            Covered(name=name, category=str(criterion.category), fact=match)
        )

    result = Coverage(
        established=tuple(established),
        to_assess=tuple(to_assess),
        behavioural=tuple(behavioural),
    )
    result.assert_total(criteria)
    return result


def _join(names: Sequence[str]) -> str:
    """"a", "a and b", "a, b and c". No Oxford comma and no dash of any kind."""
    items = list(names)
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


#: How many established criteria a candidate is told about by name. Beyond this
#: the sentence stops being readable; it names the first few and says there are
#: others, WITHOUT a count, because a count is a number reaching a client.
_NAMED_LIMIT = 4


def coverage_sentence(result: Coverage) -> str | None:
    """What the candidate reads before they start, or None when nothing to say.

    NAMES, NEVER COUNTS. "We already have your Python and SQL evidence" is a
    fact the candidate can check. "3 of 12 criteria are covered" is a number
    reaching a client, which rule 1 forbids, and it is also the less useful of
    the two: a name tells them what we think we know, and a fraction tells them
    nothing they can correct.

    The behavioural half is stated OUT LOUD rather than folded into the
    remainder. A candidate who has just been told the platform already holds
    their skills evidence will read a behavioural question as the platform
    having forgotten; saying that those are always asked fresh is the
    difference between a system that looks inconsistent and one that looks
    deliberate.
    """
    if not result.established:
        return None
    names = list(result.established_names)
    shown = _join(names[:_NAMED_LIMIT])
    more = " and some other areas" if len(names) > _NAMED_LIMIT else ""
    established = (
        f"Your profile already evidences {shown}{more}, so this assessment will "
        "not ask you to repeat that."
    )
    if result.to_assess:
        established += (
            f" You will be asked about {_join(list(result.to_assess)[:_NAMED_LIMIT])}"
            f"{' and the rest of this role' if len(result.to_assess) > _NAMED_LIMIT else ''}."
        )
    if result.behavioural:
        established += (
            " The behavioural questions are always asked fresh for every role, "
            "so you will answer those again."
        )
    return established


def prefill_text(covered: Covered) -> str:
    """The recorded answer for an established criterion.

    It says what was established and WHERE it came from, in the same voice
    `resume_prefill.prefill_text` uses, because both end up in the same
    transcript and a reader should not have to learn two conventions to tell
    where an answer came from.
    """
    return (
        "Established from this candidate's portable record, which already "
        f"evidences this: {covered.fact.statement.strip()}"
    )


# ── The write path ───────────────────────────────────────────────────────────


def _sweep_for_verdict(payload: Any, *, path: str = "facts") -> None:
    """Refuse a verdict hiding in the free-form payload.

    Recursive, because a nested dict is exactly where a prior report's shape
    would arrive if somebody handed this function a report row. The column ban
    is the real guarantee; this is what stops the same number riding along one
    level down inside JSONB.
    """
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).casefold() in _VERDICT_KEYS:
                raise PortableEvidenceError(
                    f"{path}.{key} is a verdict-shaped key. Portable evidence "
                    f"carries the FACT a new matrix judges for itself, never a "
                    f"grade another matrix reached. Store the underlying fact."
                )
            _sweep_for_verdict(value, path=f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            _sweep_for_verdict(value, path=f"{path}[{index}]")


def _sweep_statement(statement: str) -> None:
    """Refuse a client-facing grade word inside the product's own wording."""
    folded = str(statement).casefold()
    for pattern in _GRADE_PHRASES:
        if pattern.search(folded):
            raise PortableEvidenceError(
                "A portable statement carries one of the four client-facing "
                "grade words. A grade is a verdict reached against one job's "
                "matrix and it is never portable; record what was observed."
            )


async def record(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    kind: str,
    subject: str,
    statement: str,
    source_kind: str,
    source_ref: str,
    trust: str = TRUST_OBSERVED,
    observed_on: date | None = None,
    facts: Mapping[str, Any] | None = None,
    source_job_id: uuid.UUID | None = None,
    source_tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """UPSERT one portable fact, and return its id.

    AN UPSERT RATHER THAN AN INSERT, AND THAT IS NOT A CONVENIENCE.
    `uq_portable_evidence_subject` is UNIQUE with no predicate and `status` is
    a soft retirement, which is precisely the pairing that turned the matrix
    editor's paste box into a 500 in pilot on 2026-09-20. It is safe here only
    because this is the ONLY writer and it REVIVES on conflict: a retired row
    coming back is the same fact about the same person, so re-observing it
    restores it rather than colliding with it.

    Written in one statement for the reason `audit()` is: a flush followed by
    an attribute assignment is a second statement, and the second statement is
    the one that vanishes.
    """
    if kind not in PORTABLE_KINDS:
        raise PortableEvidenceError(
            f"{kind!r} is not a portable kind. What may follow a person between "
            f"employers is a product decision; add it to PORTABLE_KINDS and to "
            f"the database CHECK deliberately."
        )
    if source_kind not in SOURCE_KINDS:
        raise PortableEvidenceError(f"{source_kind!r} is not a known source kind.")
    if trust not in TRUST_LEVELS:
        raise PortableEvidenceError(f"{trust!r} is not a known trust level.")
    subject_text = " ".join(str(subject).split())
    statement_text = " ".join(str(statement).split())
    if not subject_text or not statement_text:
        raise PortableEvidenceError(
            "A portable fact needs both a subject and a statement: the subject "
            "is what a matrix criterion is matched against, and the statement "
            "is what a report would have to cite."
        )
    payload = dict(facts or {})
    _sweep_for_verdict(payload)
    _sweep_statement(statement_text)

    row_id = uuid.uuid4()
    result = await session.execute(
        text(
            """
            INSERT INTO portable_evidence_items (
                id, candidate_id, kind, subject, statement, facts, trust,
                source_kind, source_ref, source_job_id, source_tenant_id,
                observed_on, recorded_at, status, created_at
            ) VALUES (
                :id, :candidate_id, :kind, :subject, :statement,
                CAST(:facts AS jsonb), :trust, :source_kind, :source_ref,
                :source_job_id, :source_tenant_id, :observed_on, now(),
                :active, now()
            )
            ON CONFLICT (candidate_id, kind, subject) DO UPDATE SET
                statement = EXCLUDED.statement,
                facts = EXCLUDED.facts,
                trust = EXCLUDED.trust,
                source_kind = EXCLUDED.source_kind,
                source_ref = EXCLUDED.source_ref,
                source_job_id = EXCLUDED.source_job_id,
                source_tenant_id = EXCLUDED.source_tenant_id,
                observed_on = EXCLUDED.observed_on,
                recorded_at = now(),
                status = :active
            RETURNING id
            """
        ),
        {
            "id": str(row_id),
            "candidate_id": str(candidate_id),
            "kind": kind,
            "subject": subject_text,
            "statement": statement_text,
            "facts": _json(payload),
            "trust": trust,
            "source_kind": source_kind,
            "source_ref": str(source_ref),
            "source_job_id": str(source_job_id) if source_job_id else None,
            "source_tenant_id": str(source_tenant_id) if source_tenant_id else None,
            "observed_on": observed_on,
            "active": STATUS_ACTIVE,
        },
    )
    return uuid.UUID(str(result.scalar_one()))


def _json(payload: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(payload), default=str)


# ── The read path ────────────────────────────────────────────────────────────


def _fact_from_row(row: Any) -> PortableFact:
    return PortableFact(
        id=row.id,
        kind=row.kind,
        subject=row.subject,
        statement=row.statement,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        trust=row.trust,
        observed_on=row.observed_on,
        facts=dict(row.facts or {}),
    )


async def load_for_candidate(
    session: AsyncSession, candidate_id: uuid.UUID
) -> list[PortableFact]:
    """Every ACTIVE portable fact for ONE candidate.

    The candidate id is a required positional argument and there is no variant
    without it. That is the whole of the application-level isolation on this
    path: the RLS policy is bypass only, both callers run in bypass scope, and
    what keeps one candidate's record out of another's assessment is that no
    query in this module can be written without naming whose record it wants.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, kind, subject, statement, facts, trust, source_kind,
                       source_ref, observed_on
                  FROM portable_evidence_items
                 WHERE candidate_id = :candidate_id
                   AND status = :active
                 ORDER BY observed_on DESC NULLS LAST, subject
                """
            ),
            {"candidate_id": str(candidate_id), "active": STATUS_ACTIVE},
        )
    ).all()
    return [_fact_from_row(row) for row in rows]


async def load_for_link(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> list[PortableFact]:
    """The same record, for an EMPLOYER, and only with a link to stand on.

    The employer must already hold a `job_candidate_links` row for this
    candidate. That is the legitimacy test `candidates.get_bgv_results` makes
    before it will show a background check, and it is the right one here for
    the same reason: a portable record follows a person, so "which employers
    may read it" cannot be answered by a tenant column that does not exist. It
    is answered by whether this person has entered that employer's pipeline.

    Returns an EMPTY list rather than raising when there is no link. A caller
    on the assessment path would otherwise have to distinguish "no portable
    record" from "not entitled to it", and both mean the same thing here: every
    criterion is assessed fresh, which is the product's behaviour before this
    feature existed and is never wrong, only slower.
    """
    linked = (
        await session.execute(
            text(
                """
                SELECT 1 FROM job_candidate_links
                 WHERE candidate_id = :candidate_id AND tenant_id = :tenant_id
                 LIMIT 1
                """
            ),
            {"candidate_id": str(candidate_id), "tenant_id": str(tenant_id)},
        )
    ).first()
    if linked is None:
        logger.info(
            "portable_evidence.no_link candidate_id=%s tenant_id=%s, "
            "every criterion will be assessed fresh",
            candidate_id, tenant_id,
        )
        return []
    return await load_for_candidate(session, candidate_id)


# ── Harvest: turning what we already hold into portable facts ────────────────


def _skill_subjects(parsed_fields: Any) -> list[str]:
    if not isinstance(parsed_fields, Mapping):
        return []
    skills = parsed_fields.get("skills")
    if not isinstance(skills, (list, tuple)):
        return []
    seen: dict[str, str] = {}
    for raw in skills:
        name = " ".join(str(raw).split())
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return list(seen.values())


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


async def harvest(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    job_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> int:
    """Record everything portable the product already holds. Returns the count.

    DETERMINISTIC EXTRACTION, NO MODEL CALL. Every fact here is read from a row
    somebody already filled in: the parsed resume, the candidate's employment
    history, a previous employer's own confirmation. A model in this path would
    make a person's standing record depend on which provider was up the day
    they applied.

    IT READS NO REPORT. There is no join to `functional_skills_reports` or
    `report_dimensions` anywhere below, and `tests/test_portable_evidence.py`
    asserts that over this module's import graph and its SQL. A harvest that
    could reach a grade is one refactor away from carrying one.

    Called from `pickready.generate_candidate_questions`, which is already a
    dispatched task, so nothing here runs inside a request handler.
    """
    recorded = 0

    profile = (
        await session.execute(
            text(
                """
                SELECT p.id, p.parsed_fields_json, p.created_at
                  FROM profiles p
                 WHERE p.candidate_id = :candidate_id
                 ORDER BY p.created_at DESC
                 LIMIT 1
                """
            ),
            {"candidate_id": str(candidate_id)},
        )
    ).first()
    if profile is not None:
        observed = _as_date(profile.created_at)
        for subject in _skill_subjects(profile.parsed_fields_json):
            await record(
                session,
                candidate_id=candidate_id,
                kind=KIND_CORE_SKILL,
                subject=subject,
                statement=(
                    f"The candidate's resume claims {subject} among their "
                    "core skills."
                ),
                # They said it, unprompted, in their own words: the ledger's
                # definition of `observed`, and never higher. A resume is an
                # interested party's account of itself.
                trust=TRUST_OBSERVED,
                source_kind=SOURCE_RESUME,
                source_ref=f"profiles:{profile.id}#skills",
                observed_on=observed,
                source_job_id=job_id,
                source_tenant_id=tenant_id,
            )
            recorded += 1

    employments = (
        await session.execute(
            text(
                """
                SELECT e.id, e.employer_name, e.designation, e.started_on,
                       e.ended_on
                  FROM candidate_employments e
                 WHERE e.candidate_id = :candidate_id
                 ORDER BY e.started_on
                """
            ),
            {"candidate_id": str(candidate_id)},
        )
    ).all()
    for row in employments:
        employer = " ".join(str(row.employer_name or "").split())
        designation = " ".join(str(row.designation or "").split())
        if not employer:
            continue
        await record(
            session,
            candidate_id=candidate_id,
            kind=KIND_CAREER_HISTORY,
            subject=employer,
            statement=(
                f"The candidate declared the role of {designation} at "
                f"{employer}."
                if designation
                else f"The candidate declared employment at {employer}."
            ),
            # The candidate confirmed it when asked, on a form they finalised
            # and cannot edit afterwards. That is `validated`, and it is a
            # rung above a resume line for exactly that reason.
            trust=TRUST_VALIDATED,
            source_kind=SOURCE_VALIDATION,
            source_ref=f"candidate_employments:{row.id}",
            observed_on=_as_date(row.ended_on),
            facts={"designation": designation} if designation else {},
            source_job_id=job_id,
            source_tenant_id=tenant_id,
        )
        recorded += 1

    # THE ONE KIND WHOSE ORIGINATOR IS NOT THE CANDIDATE. Read WITHOUT a tenant
    # filter on purpose: the confirmation is a fact about the employment, it is
    # already candidate-owned, and this table is candidate-owned too. What it
    # does NOT carry anywhere is the HR contact's name or address, which is a
    # third party's detail handed over for one purpose; there is no column here
    # that could hold one.
    confirmed = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (e.id)
                       v.id AS verification_id, e.employer_name, e.ended_on
                  FROM bgv_verifications v
                  JOIN candidate_employments e
                    ON e.id = v.candidate_employment_id
                 WHERE v.candidate_id = :candidate_id
                   AND v.status = 'verified'
                 ORDER BY e.id, v.decided_at DESC NULLS LAST
                """
            ),
            {"candidate_id": str(candidate_id)},
        )
    ).all()
    for row in confirmed:
        employer = " ".join(str(row.employer_name or "").split())
        if not employer:
            continue
        await record(
            session,
            candidate_id=candidate_id,
            kind=KIND_EMPLOYMENT_CONFIRMED,
            subject=employer,
            statement=(
                f"A previous employer confirmed the candidate's employment at "
                f"{employer}."
            ),
            trust=TRUST_AUTHORITATIVE,
            source_kind=SOURCE_BGV,
            source_ref=f"bgv_verifications:{row.verification_id}",
            observed_on=_as_date(row.ended_on),
            source_job_id=job_id,
            source_tenant_id=tenant_id,
        )
        recorded += 1

    if recorded:
        logger.info(
            "portable_evidence.harvested candidate_id=%s facts=%d",
            candidate_id, recorded,
        )
    return recorded
