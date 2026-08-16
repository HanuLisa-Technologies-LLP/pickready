"""Business Development Portal models (migration 0023).

THE FOURTH PORTAL
-----------------
    Provider Portal   the ReadyPick owner's console            /admin  -> /provider
    Customer Portal   a client company's own dashboard         /org    -> /companies
    Candidate Portal  the public candidate surface             /portal -> /portal
    BD Portal         the sales pipeline that FINDS customers  /bd     -> /bd

The BD team works leads. A lead is a company ReadyPick wants as a customer but
does not have yet, so `bd_leads` is deliberately NOT a tenant-scoped table: a
lead has no tenant until it converts, and ReadyPick's own sales pipeline is not
any customer's data. It is a global table in the same family as `tenants` and
`llm_provider_keys`, reachable only through the RLS bypass scope the BD and
Owner consoles run in (migration 0023 gives it a policy that requires
`app.bypass_rls = 'on'`, so an org session cannot read the pipeline).

ONE TABLE, TWO CHANNELS
-----------------------
Personal Reach and Social Reach are the same funnel entered from two places:
same company fields, same primary contact, same six progress checkboxes, same
agreement decision. The only difference is that a social lead carries the
platform it came from. Two tables would mean two of every query, two of every
serializer and a `UNION` on the Customers page, so `channel` discriminates one
table and a CHECK constraint keeps `social_source` honest in BOTH directions:
required for social, forbidden for personal.

PROGRESS IS A FLAG PLUS A TIMESTAMP
-----------------------------------
Each of the six checkboxes has an `_at` companion stamped the FIRST time the
box is ticked and never cleared by unticking. Unticking is a correction to the
current state, not an erasure of history: the UI can still say "first contacted
on 3 August" after a rep clears a box they ticked by mistake.

AGREEMENT IS THREE-VALUED
-------------------------
NULL = not decided, TRUE = signed, FALSE = declined. A nullable boolean rather
than two booleans, because "not decided" and "declined" are genuinely different
states and a single `agreement = false` would collapse them.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

#: `bd_leads.channel` values. Mirrors the CHECK constraint in migration 0023.
CHANNEL_PERSONAL = "personal"
CHANNEL_SOCIAL = "social"
CHANNELS: tuple[str, ...] = (CHANNEL_PERSONAL, CHANNEL_SOCIAL)

#: `bd_leads.social_source` values. Required when the channel is social,
#: forbidden when it is personal.
SOCIAL_SOURCES: tuple[str, ...] = (
    "linkedin",
    "google",
    "facebook",
    "instagram",
    "x",
)

#: The six progress checkboxes, in the order the BD table renders them. The
#: order is data so the API, the CSV and the UI cannot drift apart.
PROGRESS_FLAGS: tuple[str, ...] = (
    "interaction_1",
    "interaction_2",
    "interaction_3",
    "meeting_demo_1",
    "meeting_demo_2",
    "meeting_demo_3",
)

#: Human labels for the six flags. No em dashes (design brief rule 1).
PROGRESS_LABELS: dict[str, str] = {
    "interaction_1": "Interaction 1",
    "interaction_2": "Interaction 2",
    "interaction_3": "Interaction 3",
    "meeting_demo_1": "Meeting / Demo 1",
    "meeting_demo_2": "Meeting / Demo 2",
    "meeting_demo_3": "Meeting / Demo 3",
}


def progress_timestamp_column(flag: str) -> str:
    """`interaction_1` -> `interaction_1_at`. One helper so the naming rule is
    stated once rather than spelled out at every call site."""
    return f"{flag}_at"


#: A THIRD `tenants.status`, added by migration 0023's widened CHECK.
#:
#: ASSUMPTION (2026-07-28): a lead that signs an agreement becomes a real
#: `tenants` row immediately (a customer IS a tenant, CLAUDE.md hard rule), but
#: it has not been onboarded yet: nobody has been invited, no company profile
#: exists, no job has been posted. Landing it as `active` would put it in the
#: Provider Portal's default customer list looking exactly like a live customer.
#: `prospect` keeps it out of that list (which defaults to `status=active` and
#: only accepts active | archived | all) while keeping the row real, so the
#: Owner's onboarding flow has something to onboard rather than a second
#: parallel notion of "customer" to reconcile later.
TENANT_PROSPECT = "prospect"


class BDLead(Base, UUIDPKMixin, CreatedAtMixin):
    """One company the BD team is working, on either reach channel."""

    __tablename__ = "bd_leads"
    __table_args__ = (
        CheckConstraint(
            "channel IN (" + ", ".join(f"'{c}'" for c in CHANNELS) + ")",
            name="ck_bd_leads_channel",
        ),
        CheckConstraint(
            "social_source IS NULL OR social_source IN ("
            + ", ".join(f"'{s}'" for s in SOCIAL_SOURCES)
            + ")",
            name="ck_bd_leads_social_source_value",
        ),
        # The rule that makes one table safe for two channels. Enforced in the
        # DATABASE, not only in pydantic: a seed script, a migration backfill or
        # a psql session must not be able to create a personal lead that claims
        # to have come from LinkedIn.
        CheckConstraint(
            "(channel = 'social' AND social_source IS NOT NULL) "
            "OR (channel = 'personal' AND social_source IS NULL)",
            name="ck_bd_leads_social_source_matches_channel",
        ),
        Index("ix_bd_leads_channel", "channel"),
        Index("ix_bd_leads_agreement", "agreement"),
        Index("ix_bd_leads_archived_at", "archived_at"),
        Index("ix_bd_leads_owner", "owner_user_id"),
    )

    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    # ── The company ──────────────────────────────────────────────────────────
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(255))

    # ── The primary contact ──────────────────────────────────────────────────
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(50))

    # NULL for a personal lead, required for a social one (CHECK above).
    social_source: Mapped[str | None] = mapped_column(String(20))

    # ── The six checkboxes, each with its first-ticked timestamp ─────────────
    interaction_1: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    interaction_1_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interaction_2: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    interaction_2_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interaction_3: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    interaction_3_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meeting_demo_1: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    meeting_demo_1_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meeting_demo_2: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    meeting_demo_2_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meeting_demo_3: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    meeting_demo_3_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── The final stage ──────────────────────────────────────────────────────
    #: NULL not decided | True signed | False declined.
    agreement: Mapped[bool | None] = mapped_column(Boolean)
    agreement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The LIVE link to the customer this lead became. Cleared when agreement
    #: stops being true, which is what "unlink" means.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL")
    )
    #: The PERMANENT record of which tenant this lead once created. Never
    #: cleared, and deliberately carries no foreign key (the same reasoning as
    #: `audit_log.tenant_id`: the history must survive a tenant deletion).
    #:
    #: ASSUMPTION (2026-07-28): the brief says un-setting agreement "unlinks and
    #: archives". Keeping only `tenant_id` would make a re-signed lead mint a
    #: SECOND tenant for the same company, so the two columns are split: the
    #: link is cleared as instructed, and re-promotion reuses and unarchives the
    #: tenant recorded here instead of duplicating the customer.
    promoted_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # ── Ownership, notes, lifecycle ──────────────────────────────────────────
    #: The BD rep working this lead. SET NULL, not CASCADE: the pipeline
    #: outlives the person, exactly like a compliance document (migration 0020).
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)

    #: Soft delete. Same shape as `jobs.archived_at` and `tenants.archived_at`:
    #: DELETE /bd/leads/{id} stamps this and nothing is destroyed.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Server-clock timestamps. `onupdate=func.now()` is evaluated by Postgres,
    #: which means SQLAlchemy EXPIRES this attribute after every UPDATE flush
    #: and has to read it back. Under the async engine that read cannot happen
    #: lazily: attribute access is synchronous, so SQLAlchemy cannot await, and
    #: the first read of `lead.updated_at` after a flush raised
    #: `MissingGreenlet` and 500'd every mutating /bd/leads route.
    #: `eager_defaults` (below) makes the UPDATE use RETURNING so the new value
    #: comes back with the write itself: no lazy load, no extra SELECT, and the
    #: timestamp still comes from the database clock rather than the app's.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __mapper_args__ = {"eager_defaults": True}
