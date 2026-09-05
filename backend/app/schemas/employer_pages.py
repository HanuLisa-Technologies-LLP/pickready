"""Response schemas for the PUBLIC employer pages.

Provenance: the 2026-09-05 add-features spec ("Employer Page & Content").

These models are the boundary an anonymous visitor reads through, so they are
strict allowlists: no internal notes, no contact emails, no credit or billing
state, no subscription linkage, no applicant counts, no numbers about
candidates. `tests/test_employer_pages.py` asserts the field sets stay closed.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class EmployerCardOut(BaseModel):
    """One search result on the public employer directory."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    industry: str | None = None
    website_domain: str | None = None


class EmployerSearchOut(BaseModel):
    """A page of the public employer directory. `total` counts COMPANIES
    matching the search -- an operational listing figure, never a candidate
    number."""

    employers: list[EmployerCardOut]
    total: int
    page: int
    page_size: int


class EmployerOpenRoleOut(BaseModel):
    """One live job on an employer's careers section. Strictly the fields the
    public apply page already shows for the same job: no compensation, no
    pipeline state, no applicant information."""

    id: uuid.UUID
    title: str
    department: str | None = None
    level: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    #: Same-origin path to the existing public application page.
    apply_path: str
    #: Absolute form of the same link, from the one builder
    #: (api/jobs.public_job_url), for sharing off-site.
    apply_url: str


class EmployerPageOut(BaseModel):
    """The public employer page: identity, narrative sections, careers."""

    slug: str
    name: str
    industry: str | None = None
    website_domain: str | None = None
    about_company: str | None = None
    work_life: str | None = None
    benefits: str | None = None
    open_roles: list[EmployerOpenRoleOut]
