"""Tool inputs and outputs as Pydantic v2 models. No bare dicts cross this line.

WHY THE OUTPUT MODELS ARE NARROW
--------------------------------
Each one names exactly the fields an agent is allowed to reason over, and
nothing else. That is doing real work in two places:

  Compensation.  `JobFacts` has no compensation field and no free-form escape
  hatch that could carry one. ESD 16 says the re-rank chain never receives
  compensation data; today that is enforced by `matching._strip_compensation`
  at one call site. Making it a property of the SHAPE means the next agent that
  reads a JD inherits the guarantee instead of having to remember it.

  Numbers.  `ReportableGrade` is the four words of `services.rating` and cannot
  hold a score. A tool that returned `overall_score: 87` would put a number one
  careless f-string away from a client, which is the product's oldest standing
  rule.

WHY `extra="forbid"`
--------------------
A handler that grows a field nobody declared is a handler whose output nothing
downstream was written against. Rejecting it turns a silent shape drift into a
`ToolOutputError` on the first call, which is the cheapest moment to find it.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services import rating

#: The only rated words that may leave a tool. Mirrors `services.rating` rather
#: than restating it, so the four-grade scale has exactly one definition.
ReportableGrade = Literal[
    rating.GRADE_HIGHLY,
    rating.GRADE_MATCHING,
    rating.GRADE_MODERATELY,
    rating.GRADE_NOT,
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── extract_jd ───────────────────────────────────────────────────────────────


class JobRef(_Strict):
    job_id: uuid.UUID


class JobFacts(_Strict):
    """A job as an agent may see it. Deliberately without compensation."""

    job_id: uuid.UUID
    title: str
    department: str | None = None
    #: non_managerial | managerial | leadership | cxo. Drives question counts.
    grade: str
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    #: Short skill labels mined from the JD, in JD order.
    skills: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    education: tuple[str, ...] = ()
    #: The canonical JD document. May be absent on a job still being drafted.
    jd_markdown: str | None = None
    #: Whether a candidate could be invited today. Cheaper for an agent to read
    #: than to re-derive from three timestamps, and it is the question every
    #: caller of this tool actually has.
    assessment_status: str
    framework_approved: bool = False


# ── extract_resume ───────────────────────────────────────────────────────────


class ProfileRef(_Strict):
    profile_id: uuid.UUID
    #: Resume text is the largest thing a tool returns and most callers want a
    #: snippet for a prompt, not the document. 0 means omit it entirely.
    resume_chars: int = Field(default=1500, ge=0, le=20000)


class ResumeFacts(_Strict):
    """A parsed resume, compensation-stripped, bounded in size."""

    profile_id: uuid.UUID
    candidate_id: uuid.UUID
    skills: tuple[str, ...] = ()
    education: tuple[str, ...] = ()
    employment_history: tuple[str, ...] = ()
    total_experience_years: float | None = None
    #: Truncated to `resume_chars`. Empty when the resume never parsed, which
    #: is a real and common state and must not look like an empty resume.
    resume_excerpt: str = ""
    resume_parsed: bool = False


# ── extract_assessment ───────────────────────────────────────────────────────


class LinkRef(_Strict):
    link_id: uuid.UUID
    #: A non-managerial interview runs to 120 messages. A caller that wants the
    #: whole transcript asks for it; the default is what fits a prompt.
    max_exchanges: int = Field(default=40, ge=1, le=200)


class Exchange(_Strict):
    """One question and the answer filed under it.

    Paired SERVER-SIDE, and the follow-up rule is why: a probe reuses its
    parent's `question_key`, which is exactly how the scorers file it. Pairing
    per caller would reimplement that rule and let it drift.
    """

    question_key: str | None = None
    domain: str
    question: str
    answer: str
    #: substantive | empty | gibberish | off_topic | evasive, when classified.
    answer_label: str | None = None


class AssessmentFacts(_Strict):
    link_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    status: str
    grade: str
    exchanges: tuple[Exchange, ...] = ()
    #: True when more exchanges exist than `max_exchanges` returned. A caller
    #: that silently reasons over a truncated transcript believing it complete
    #: is the failure this field exists to prevent.
    truncated: bool = False


# ── extract_framework ────────────────────────────────────────────────────────


class Competency(_Strict):
    competency_id: uuid.UUID
    #: must_have | nice_to_have | behavioural
    category: str
    name: str
    description: str | None = None
    #: The job's requirement as a WORD. The stored column is an integer; it is
    #: converted here so no number leaves the tool layer.
    required_level: ReportableGrade


class FrameworkFacts(_Strict):
    job_id: uuid.UUID
    competencies: tuple[Competency, ...] = ()
    approved: bool = False
    #: True when the job carries a generation stamp and no rows. A timestamp is
    #: not evidence that work happened, and 19 live jobs once proved it.
    framework_pending: bool = False


# ── validate_output ──────────────────────────────────────────────────────────


class ValidationRequest(_Strict):
    """Ask the tool layer whether a generated payload is shaped correctly.

    A tool rather than a helper function because it is the one piece of
    verification an agent may invoke on ITSELF mid-task, and routing it through
    the executor means that self-check is counted and bounded like every other
    call rather than being invisible work inside a prompt loop.
    """

    model_config = ConfigDict(extra="forbid")

    #: Registered schema name. Never an arbitrary class path -- a caller that
    #: could name any importable model could name anything importable.
    schema_name: str
    payload: dict | list


class ValidationVerdict(_Strict):
    schema_name: str
    valid: bool
    errors: tuple[str, ...] = ()

# ── retrieve_context ─────────────────────────────────────────────────────────


class RetrievalRequest(_Strict):
    """Ask the context engine for the pieces of a document bearing on a query.

    `source_ids` is required rather than optional, and that is the important
    field. An unscoped semantic search over the chunk table would happily return
    another candidate's resume paragraph: RLS keeps it inside the tenant, and
    nothing but this scoping keeps it to the person being assessed.
    """

    query: str = Field(min_length=1, max_length=2000)
    #: jd | resume | assessment
    source_type: str
    source_ids: tuple[uuid.UUID, ...] = Field(min_length=1)
    section_types: tuple[str, ...] = ()
    top_k: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=2000, ge=100, le=8000)


class RetrievedPiece(_Strict):
    chunk_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    section_type: str
    content: str
    #: Which retrievers surfaced it. Semantic missing across every piece is the
    #: visible form of an embedding outage, and a caller that logs it can tell
    #: "no good evidence" apart from "the GPU service is down".
    retrievers: tuple[str, ...] = ()


class RetrievedContext(_Strict):
    query: str
    pieces: tuple[RetrievedPiece, ...] = ()
    #: The assembled, budgeted, labelled block an agent puts in its prompt.
    text: str = ""
    tokens: int = 0
    dropped: int = 0
    compressed: bool = False
