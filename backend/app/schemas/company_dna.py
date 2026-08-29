"""Request and response models for the Company DNA intake (spec-doc6 §4.2).

TWO SHAPES, AND THE SPLIT IS THE AUTHORIZATION
------------------------------------------------
`CompanyDNASessionOut` carries the raw intake: the questions, the client's own
answers, the transcript position. `CompanyDNACompiledOut` carries the compiled
artifact in plain language and nothing else.

spec-doc6 D3 gives the Recruiter and the Hiring Manager the second and never
the first. That is expressed here as two models rather than as one model with a
field somebody has to remember to blank, because a field that is blanked at the
call site is a field the next call site forgets. `CompanyDNAOverviewOut.session`
is `None` for a caller without authorship, and
`tests/test_company_dna_authorization.py` reads a recruiter's response body and
asserts no answer text is in it.

NO NUMBERS
----------
Nothing in the client-facing models carries a weight, a multiplier, a
percentage or a score. The compiled configuration IS numeric, and spec-doc6 D8
allows exactly one route to it: an audited view restricted to the Super Admin
and the HR Manager. That is `CompanyDNAVersionDetailOut.configuration`, opt-in
per request and logged on every read.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CompanyDNACompiledOut",
    "CompanyDNACompleteIn",
    "CompanyDNACreateIn",
    "CompanyDNAOverviewOut",
    "CompanyDNAPermissionsOut",
    "CompanyDNASessionOut",
    "CompanyDNAStatusOut",
    "CompanyDNAVersionDetailOut",
    "CompanyDNAVersionListOut",
    "CompanyDNAVersionOut",
    "EvidenceExampleOut",
    "IntakeAnswerIn",
    "QuestionOut",
    "ScorecardBlockOut",
    "SectionOut",
    "UnderstandingBlockOut",
]

STATUS_DRAFT = "draft"
STATUS_COMPLETE = "complete"
STATUS_SUPERSEDED = "superseded"


class QuestionOut(BaseModel):
    """One question, with everything the control needs to render itself.

    `kind` decides the control, and the API refuses an answer of the wrong
    kind, so a client that renders a text box for a scale question gets a 422
    rather than a silently accepted sentence.
    """

    model_config = ConfigDict(from_attributes=True)

    key: str
    kind: str
    prompt: str
    help_text: str = ""
    required: bool = True
    #: SCALE only. Two real alternatives, never "how important is X".
    poles: list[str] | None = None
    #: SCALE only, so the control does not hardcode the range.
    scale_min: int | None = None
    scale_max: int | None = None
    #: CHOICE only.
    options: list[str] = Field(default_factory=list)


class EvidenceExampleOut(BaseModel):
    """A Runbook §16.3 accepted and rejected pair, shown beside the section.

    The verbatim Runbook wording, carried on the instrument itself so the
    question and the example beside it can never come from two different
    readings of §16. Shown rather than paraphrased because the pair IS the
    quality bar: a client who reads one rejected answer next to its accepted
    rewrite converts the next one themselves.
    """

    rejected: str
    accepted: str


class SectionOut(BaseModel):
    key: str
    title: str
    #: Why we are asking, in the terms a CHRO cares about.
    intent: str
    questions: list[QuestionOut] = Field(default_factory=list)
    #: Every question in the section, and how many carry an answer.
    answered: int = 0
    total: int = 0
    #: The subset the session cannot close without. Four of the twelve sections
    #: carry none, and a screen reads that as "optional" rather than drawing a
    #: tick the client never earned.
    required_answered: int = 0
    required_total: int = 0
    complete: bool = False
    #: The accepted and rejected pairs the Runbook prints for this section.
    #: Empty for the sections that do not carry one.
    examples: list[EvidenceExampleOut] = Field(default_factory=list)
    #: How many items §16 asks for, where it states a number, and the shape it
    #: asks each one to be written in. Carried so the control can say what it
    #: wants before the API has to refuse it.
    min_items: int | None = None
    max_items: int | None = None
    item_format: str = ""


class UnderstandingBlockOut(BaseModel):
    """One paragraph of the compiled artifact restated in plain language.

    Carries no number. This is what the client confirms before the session
    closes, and confirming a multiplier table would be confirming that the
    arithmetic looks plausible rather than that the meaning is right.
    """

    key: str
    title: str
    lines: list[str] = Field(default_factory=list)


class CompanyDNASessionOut(BaseModel):
    """The RAW intake session. Authorship roles only, never a Recruiter."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: str
    created_at: datetime
    authored_by: str | None = None
    sections: list[SectionOut] = Field(default_factory=list)
    #: {question_key: answer}. The client's own words, back to them.
    answers: dict[str, Any] = Field(default_factory=dict)
    next_question: QuestionOut | None = None
    pending_prompt: str | None = None
    answered: int = 0
    required: int = 0
    ready_to_complete: bool = False
    #: Present once every required question is answered. What Bodha reads back.
    understanding: list[UnderstandingBlockOut] | None = None
    #: The fingerprint of the understanding above. `POST .../complete` refuses
    #: a token that does not match what the answers now compile to, so a
    #: confirmation cannot be carried across an answer changed after it.
    understanding_token: str | None = None


class CompanyDNACompiledOut(BaseModel):
    """The COMPILED artifact, in plain language. Safe for every reader."""

    version: int
    status: str
    completed_at: datetime | None = None
    authored_by: str | None = None
    understanding: list[UnderstandingBlockOut] = Field(default_factory=list)


class CompanyDNAPermissionsOut(BaseModel):
    """What this caller may do, resolved server-side.

    The screens read this rather than the session's capability list, because
    the two Company DNA capabilities are grants in `role_permissions` that
    `services/capabilities.ALL_CAPABILITIES` does not yet name, so they do not
    appear in the login response. Resolving here is also the more honest
    arrangement: the server is the only thing that decides.
    """

    can_author: bool = False
    can_view_compiled: bool = False
    can_view_session: bool = False


class ScorecardBlockOut(BaseModel):
    """The Layer 2 requirement, stated on the screen that can act on it.

    A client with no Company DNA can create jobs and draft descriptions. What
    they cannot do is lock a scorecard, because Sutra has no Layer 2 artifact
    to compile against. Saying so here is the difference between an actionable
    requirement and a mysterious failure three screens later.

    `blocked` reports the ABSENCE OF THE ARTIFACT. It is not a report that an
    evaluation was refused: gate G1 lives in `services/hiring/gates` and is
    reached only through `services/miti/pipeline`, which nothing in the API or
    the workers imports yet. When spec-doc6 phases 3 to 5 put Part A on the
    live path, G1 refuses against exactly this condition and this field will
    already be describing it correctly.
    """

    blocked: bool = False
    message: str = ""


class CompanyDNAOverviewOut(BaseModel):
    client_id: uuid.UUID
    has_artifact: bool = False
    compiled: CompanyDNACompiledOut | None = None
    #: Whether a draft is open, stated rather than inferred from `session`.
    #:
    #: `session` is null in two different situations that a screen has to tell
    #: apart: nobody has started one, and the caller is not allowed to see it.
    #: A reader forced to infer which would eventually get it wrong, and the
    #: wrong guess is an onboarding prompt shown to a Recruiter who cannot act
    #: on it.
    draft_open: bool = False
    #: None for any caller without authorship. See the module docstring.
    session: CompanyDNASessionOut | None = None
    permissions: CompanyDNAPermissionsOut
    scorecard: ScorecardBlockOut


class CompanyDNAStatusOut(BaseModel):
    """Completion status and version. The ONLY Company DNA shape internal
    Business Development staff may ever see: no answers, no artifact, no
    plain-language restatement, no author."""

    client_id: uuid.UUID
    status: str
    version: int | None = None
    completed_at: datetime | None = None
    draft_open: bool = False


class CompanyDNAVersionOut(BaseModel):
    version: int
    status: str
    is_current: bool
    authored_by: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    #: The artifact fingerprint, so two versions can be told apart at a glance
    #: and a diff can be keyed without re-reading both documents.
    checksum: str | None = None


class CompanyDNAVersionListOut(BaseModel):
    items: list[CompanyDNAVersionOut] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25


class CompanyDNAVersionDetailOut(CompanyDNAVersionOut):
    understanding: list[UnderstandingBlockOut] = Field(default_factory=list)
    #: The numeric engine configuration. spec-doc6 D8 restricts raw internals
    #: to an audited view for the Super Admin and the HR Manager; it is opt-in
    #: per request and every read writes an audit row. Absent by default.
    configuration: dict[str, Any] | None = None


class CompanyDNACreateIn(BaseModel):
    """Open a new draft version.

    `copy_from_version` seeds the draft from an existing version's answers,
    which is what "create a new version" means in practice: a client revising
    their philosophy is editing six answers out of thirty, not retyping the
    session. The source version is never modified.
    """

    copy_from_version: int | None = Field(default=None, ge=1)


class IntakeAnswerIn(BaseModel):
    """One turn of the session.

    `answer` is deliberately untyped here and validated against the QUESTION's
    kind by `dna_compilation.validate_answer`. A Pydantic union would have to
    accept a string for every kind to be usable, which is precisely the
    free-text-into-a-forced-scale hole the Runbook closes.
    """

    question_key: str = Field(min_length=1, max_length=100)
    answer: Any = None


class CompanyDNACompleteIn(BaseModel):
    """Close the session and freeze the version.

    The token is the fingerprint of the understanding the client was shown. It
    is required, not optional: a completion without one would be a completion
    nobody confirmed, and the whole point of reading the compiled understanding
    back is that somebody said yes to THAT understanding.
    """

    understanding_token: str = Field(min_length=16, max_length=128)
