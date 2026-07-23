"""Shared enums — string-valued so they serialize cleanly across API/DB."""
import enum


class Role(str, enum.Enum):
    super_admin = "super_admin"
    client = "client"
    hr_manager = "hr_manager"
    recruiter = "recruiter"
    hiring_manager = "hiring_manager"
    candidate = "candidate"


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
    rejected = "rejected"
    shortlisted = "shortlisted"
    hold = "hold"  # requires mandatory remarks (FR-8.2)
    offered = "offered"
    joined = "joined"


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
