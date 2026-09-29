"""Leadership Intelligence request and response shapes (spec 31, Leadership).

The SAVE body carries TEXT and nothing that says whose it is: no role, no
author and, for a Functional Head, no department (`extra="forbid"` refuses a
smuggled `department_id` with a 422). The server reads those from the users
row (rule 37.12). Words only cross back: a version is a record label the page
shows ("Version 3"), not an assessment signal.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.leadership.profiles import FIELD_MAX_CHARS


class LeadershipSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_requirements: str = Field(default="", max_length=FIELD_MAX_CHARS)
    department_requirements: str = Field(default="", max_length=FIELD_MAX_CHARS)
    ideal_employee_expectations: str = Field(default="", max_length=FIELD_MAX_CHARS)
    #: CEO and MD only: {department id: expectation}. Checked against the
    #: tenant's own departments; a Functional Head sending any is refused.
    department_expectations: dict[uuid.UUID, str] = Field(default_factory=dict)
    #: The source keys of an AI draft the leader started from, or None when
    #: they wrote it themselves. Provenance only; the saved text is theirs.
    ai_draft_sources: list[str] | None = Field(default=None, max_length=10)


class DepartmentRefOut(BaseModel):
    id: uuid.UUID
    name: str


class ExcludedLineOut(BaseModel):
    text: str
    #: A reason WORD: protected_attribute, culture_fit, not_observable,
    #: too_long, over_limit.
    reason: str


class LeadershipProfileOut(BaseModel):
    id: uuid.UUID
    author_role: str
    author_label: str
    department_id: uuid.UUID | None = None
    version: int
    saved_by: str | None = None
    saved_at: datetime
    company_requirements: str = ""
    department_requirements: str = ""
    ideal_employee_expectations: str = ""
    department_expectations: dict[str, str] = Field(default_factory=dict)
    lines_not_used: list[ExcludedLineOut] = Field(default_factory=list)
    used_ai_draft: bool = False


class LeadershipMeOut(BaseModel):
    can_author: bool
    refusal: str | None = None
    author_role: str | None = None
    author_label: str | None = None
    department: DepartmentRefOut | None = None
    departments: list[DepartmentRefOut] = Field(default_factory=list)
    profile: LeadershipProfileOut | None = None


class LeadershipDraftStartOut(BaseModel):
    task_id: str


class DraftSourceOut(BaseModel):
    key: str
    label: str


class LeadershipDraftOut(BaseModel):
    #: pending | drafted | empty | failed
    status: str
    fields: dict = Field(default_factory=dict)
    sources: list[DraftSourceOut] = Field(default_factory=list)
    message: str | None = None
    generated_by_ai: bool = False


class FunctionalHeadEntryOut(BaseModel):
    department: DepartmentRefOut
    profile: LeadershipProfileOut


class LeadershipCompanyOut(BaseModel):
    ceo: LeadershipProfileOut | None = None
    md: LeadershipProfileOut | None = None
    functional_heads: list[FunctionalHeadEntryOut] = Field(default_factory=list)
    departments: list[DepartmentRefOut] = Field(default_factory=list)
