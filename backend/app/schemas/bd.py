"""Business Development Portal schemas (Pydantic v2).

Vocabulary, kept strict the way the other three portals are:

    Provider Portal   the ReadyPick owner's console over its customers
    Customer Portal   a client company's own dashboard
    Candidate Portal  the public candidate surface
    BD Portal         the sales pipeline that turns a LEAD into a customer

A LEAD is a company ReadyPick wants; a CUSTOMER is a company it has (a
`tenants` row). The BD Customers page reads the leads that crossed that line,
which is why `BDCustomerOut` carries both the lead id and the tenant id.

NO EM DASHES in any string a user can see, including validation messages
(design brief rule 1). NO NUMBERS in rated output: AI Reach confidence is a
word label, never a score or a percentage (CLAUDE.md).
"""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.bd import CHANNELS, PROGRESS_FLAGS, PROGRESS_LABELS, SOCIAL_SOURCES

from app.schemas.pagination import PageMeta

__all__ = [
    "AIReachIn",
    "AIReachOut",
    "BDCustomerListOut",
    "BDCustomerOut",
    "BDProfileOut",
    "BDProfileUpdateIn",
    "Channel",
    "ConfidenceLabel",
    "JobCardOut",
    "LeadAgreementIn",
    "LeadCreateIn",
    "LeadListOut",
    "LeadOut",
    "LeadProgressIn",
    "LeadUpdateIn",
    "ProgressStepOut",
    "SegmentOut",
    "SocialSource",
]

Channel = Literal["personal", "social"]
SocialSource = Literal["linkedin", "google", "facebook", "instagram", "x"]

#: The approved four word-only ratings. Similarity stays internal.
ConfidenceLabel = Literal[
    "Highly Matching", "Matching", "Moderately Matching", "Not Matching"
]

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_NOTES = 5_000

# Keep the Literal aliases and the model constants from drifting apart.
assert set(CHANNELS) == {"personal", "social"}
assert set(SOCIAL_SOURCES) == {"linkedin", "google", "facebook", "instagram", "x"}


def _clean(value: str | None) -> str | None:
    """Trim, and collapse an all-whitespace string to None so a blank input is
    an absent value rather than an empty one."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _bare_host(value: str | None) -> str | None:
    """Accept what a human pastes, store the bare host. Same rule the Provider
    Portal applies to `website_domain`, so the two consoles agree."""
    cleaned = _clean(value)
    if cleaned is None:
        return None
    for prefix in ("https://", "http://"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
    return cleaned.strip("/") or None


# ── Leads ────────────────────────────────────────────────────────────────────

class ProgressStepOut(BaseModel):
    """One of the six checkboxes, as the table renders it."""

    key: str
    label: str
    done: bool = False
    #: When the box was FIRST ticked. Survives an untick, so the history of the
    #: relationship is not lost to a mis-click.
    at: datetime | None = None


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: Channel
    company_name: str
    website: str | None = None
    industry: str | None = None
    location: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    social_source: SocialSource | None = None
    progress: list[ProgressStepOut] = Field(default_factory=list)
    #: NULL not decided, true signed, false declined.
    agreement: bool | None = None
    agreement_at: datetime | None = None
    #: Set once the lead has been promoted to a customer.
    tenant_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = None
    notes: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class LeadListOut(PageMeta):
    """`total` counts every lead matching the filter, not just this page, so
    the UI can render "showing 25 of 108" honestly."""

    leads: list[LeadOut]
    total: int
    page: int
    page_size: int


class _LeadFields(BaseModel):
    """Fields shared by create and update, with their cleaning rules."""

    company_name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        return _bare_host(value)

    @field_validator(
        "company_name", "industry", "location", "contact_name",
        "contact_email", "contact_phone", "notes",
    )
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _clean(value)


class LeadCreateIn(_LeadFields):
    """POST /bd/leads.

    `channel` is required and immutable afterwards: moving a lead between
    Personal Reach and Social Reach would silently strip or invent its source.
    """

    channel: Channel
    company_name: str = Field(min_length=1, max_length=255)
    social_source: SocialSource | None = None

    @model_validator(mode="after")
    def _source_matches_channel(self) -> "LeadCreateIn":
        # The database CHECK is the real boundary; this is the friendly error.
        if self.channel == "social" and self.social_source is None:
            raise ValueError("A social lead needs a source: LinkedIn, Google, "
                             "Facebook, Instagram or X.")
        if self.channel == "personal" and self.social_source is not None:
            raise ValueError("A personal lead has no social source.")
        return self


class LeadUpdateIn(_LeadFields):
    """PATCH /bd/leads/{id}. Partial: an absent key leaves the field alone, an
    explicit "" clears it.

    `channel` is deliberately absent (see LeadCreateIn). `social_source` may be
    changed only within the social channel, which the handler enforces against
    the stored row because a PATCH body does not carry the channel.
    """

    social_source: SocialSource | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "LeadUpdateIn":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class LeadProgressIn(BaseModel):
    """PATCH /bd/leads/{id}/progress.

    A SPARSE {flag: bool} map, so the UI sends only the box that was clicked
    and two reps ticking different boxes never overwrite each other.
    """

    progress: dict[str, bool] = Field(min_length=1)

    @field_validator("progress")
    @classmethod
    def _known_flags(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(set(value) - set(PROGRESS_FLAGS))
        if unknown:
            raise ValueError(
                "Unknown progress steps: " + ", ".join(unknown) + ". "
                "Valid steps are " + ", ".join(PROGRESS_FLAGS) + "."
            )
        return value


class LeadAgreementIn(BaseModel):
    """PATCH /bd/leads/{id}/agreement.

    Three-valued on purpose: `null` puts the lead back to undecided, `false` is
    an explicit no. The field must be SENT (it has no default) so an empty body
    cannot silently read as "declined".
    """

    agreement: bool | None


# ── Customers (the BD Customers page) ────────────────────────────────────────

class BDCustomerOut(BaseModel):
    """One row of the BD Customers table, and one row of the CSV export.

    Sourced from the lead that closed, joined to the tenant it created. The
    lead is the record of the relationship the BD team owns; the tenant is the
    customer identity the rest of the platform uses.
    """

    lead_id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    company_name: str
    location: str | None = None
    industry: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    website: str | None = None
    channel: Channel
    social_source: SocialSource | None = None
    agreement_at: datetime | None = None


class BDCustomerListOut(PageMeta):
    customers: list[BDCustomerOut]
    total: int
    page: int
    page_size: int


#: CSV column order, and the header row. One tuple so the streaming export and
#: the JSON list can never disagree about what a "customer row" is.
CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("company_name", "Company"),
    ("location", "Location"),
    ("industry", "Industry"),
    ("contact_name", "Contact Name"),
    ("contact_email", "Contact Email"),
    ("contact_phone", "Contact Phone"),
    ("website", "Website"),
    ("channel", "Channel"),
    ("social_source", "Source"),
    ("agreement_at", "Agreement Date"),
)


# ── Settings (BD team member's own details) ──────────────────────────────────

class BDProfileOut(BaseModel):
    """GET /bd/me. There is no password field anywhere in this schema: Firebase
    owns credentials and recovery (CLAUDE.md rule 2), and the existing
    `frontend/components/change-password.tsx` talks to the Firebase client SDK
    directly rather than to any ReadyPick endpoint."""

    user_id: uuid.UUID
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str
    capabilities: list[str] = Field(default_factory=list)


class BDProfileUpdateIn(BaseModel):
    """PATCH /bd/me. Name, email and phone only."""

    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)

    @field_validator("name", "email", "phone")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _clean(value)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "BDProfileUpdateIn":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


# ── AI Reach ─────────────────────────────────────────────────────────────────

class AIReachIn(BaseModel):
    """POST /bd/ai-reach/search. `company` is the one optional input."""

    job_role: str = Field(min_length=2, max_length=160)
    city: str = Field(min_length=2, max_length=120)
    industry: str = Field(min_length=2, max_length=120)
    company: str | None = Field(default=None, max_length=160)

    @field_validator("job_role", "city", "industry", "company")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _clean(value)


class JobCardOut(BaseModel):
    """One clickable card. `company_url` is REQUIRED because the card's whole
    job is to open the company website; `job_url` is optional because a
    specific posting URL is often not confidently known, and a guessed link is
    worse than no link."""

    job_title: str
    company: str
    city: str | None = None
    industry: str | None = None
    company_url: str
    job_url: str | None = None
    source_domain: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_source_url: str | None = None
    #: Approved word-only rating. A similarity number never crosses the API.
    confidence_label: ConfidenceLabel = "Moderately Matching"


class SegmentOut(BaseModel):
    """One of the two segments AI Reach returns.

    `status` is what lets the internet segment fail politely: `ok`,
    `unconfigured` (no web search key on this deployment), `timeout`,
    `breaker_open`, `quota_exhausted`, or `unavailable`. `message` is plain
    English for the empty state.
    """

    status: Literal[
        "ok", "unconfigured", "timeout", "breaker_open", "quota_exhausted",
        "unavailable",
    ] = "ok"
    message: str | None = None
    jobs: list[JobCardOut] = Field(default_factory=list)


class AIReachOut(BaseModel):
    """The two segments, always both present and always clearly separated.

    `similar_to_customers` needs no external network call and must always work,
    so a web search outage degrades the page rather than breaking it.
    """

    query: AIReachIn
    similar_to_customers: SegmentOut
    from_internet: SegmentOut


def progress_steps(source: object) -> list[ProgressStepOut]:
    """Build the six steps, in fixed order, from a lead row.

    All six are ALWAYS returned, ticked or not, for the same reason the
    Provider Portal returns all seven compliance slots: a checkbox that is
    simply absent from the payload is a checkbox the UI has to invent.
    """
    return [
        ProgressStepOut(
            key=flag,
            label=PROGRESS_LABELS[flag],
            done=bool(getattr(source, flag, False)),
            at=getattr(source, f"{flag}_at", None),
        )
        for flag in PROGRESS_FLAGS
    ]
