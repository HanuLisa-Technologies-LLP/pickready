"""Shared enums — string-valued so they serialize cleanly across API/DB."""
import enum


class Role(str, enum.Enum):
    super_admin = "super_admin"
    client = "client"
    hr_manager = "hr_manager"
    recruiter = "recruiter"
    hiring_manager = "hiring_manager"
    candidate = "candidate"
    # Business Development. PickReady's own staff, not a customer's: a bd user
    # has tenant_id NULL and works the sales pipeline in `bd_leads`. Sits in
    # the OWNER token audience (see core/security.audience_for_role) because
    # the BD console is a platform console, not a tenant one.
    bd = "bd"


class UserStatus(str, enum.Enum):
    invited = "invited"
    active = "active"
    disabled = "disabled"


class OTPChannel(str, enum.Enum):
    email = "email"
    sms = "sms"


class JobStatus(str, enum.Enum):
    """Approval FSM states (ESD §7). `draft` precedes the chain; `ratified` is
    terminal — only then does HR gain access (FR-3.4)."""
    draft = "draft"
    requested = "requested"
    recommended = "recommended"
    approved = "approved"
    ratified = "ratified"


# Ordered approval chain — the FSM walks this list, skipping inactive levels.
APPROVAL_CHAIN: list[JobStatus] = [
    JobStatus.requested,
    JobStatus.recommended,
    JobStatus.approved,
    JobStatus.ratified,
]


class ApprovalDecision(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"
    skipped = "skipped"  # inactive level — logged explicitly, never silent


class LinkSource(str, enum.Enum):
    databank = "databank"
    fresh = "fresh"


class Tier(str, enum.Enum):
    highly_matching = "highly_matching"        # ≥90
    moderately_matching = "moderately_matching"  # ≥70
    matching = "matching"                      # ≥50
    not_matching = "not_matching"              # <50


class PipelineStatus(str, enum.Enum):
    """The 10-stage hiring pipeline (spec §3.3), plus two retained legacy values.

    This enum MUST stay in step with `services/hiring_pipeline`'s constants and
    with migration 0018's `PIPELINE_STATUSES`, which is what the `varchar(30)`
    columns actually accept. It did not: 0018 widened the vocabulary from five
    values to ten, but this enum kept only the original five. Reading a row
    written by the newer code — the first time a recruiter invited anyone to an
    assessment — then raised

        LookupError: 'assessment_invited' is not among the defined enum values

    inside SQLAlchemy's row processor, which 500'd GET /dashboard/summary for
    that whole tenant, permanently, from the first invitation onward.

    `offered` and `offer_extended` are deliberately BOTH here: 0018 kept the old
    name valid rather than rewriting history, so both can appear in the table.
    """
    applied = "applied"
    assessment_invited = "assessment_invited"
    assessment_in_progress = "assessment_in_progress"
    assessment_completed = "assessment_completed"
    shortlisted = "shortlisted"
    rejected = "rejected"
    interview_scheduled = "interview_scheduled"
    interview_completed = "interview_completed"
    offer_extended = "offer_extended"
    joined = "joined"
    hold = "hold"  # requires mandatory remarks (FR-8.2)
    #: Legacy synonym of `offer_extended`, retained so historic rows still read.
    offered = "offered"


class VerificationStatus(str, enum.Enum):
    pending = "pending"
    submitted = "submitted"
    overridden = "overridden"  # explicit HR override with logged reason (ESD §10)


class SubmittedVia(str, enum.Enum):
    form = "form"
    email_reply = "email_reply"


class LLMProvider(str, enum.Enum):
    groq = "groq"
    gemini = "gemini"
    openrouter = "openrouter"


class LLMRoleHint(str, enum.Enum):
    rerank = "rerank"          # Groq-first chain (latency-sensitive)
    extraction = "extraction"  # Gemini-first chain (long context)
