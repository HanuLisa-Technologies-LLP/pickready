"""All SQLAlchemy models, mirroring ESD §4. Import from here so Base.metadata
sees every table (Alembic autogenerate, tests)."""
from app.models.base import Base
from app.models.candidate import (
    Candidate,
    Interview,
    JobCandidateLink,
    PipelineStatusEntry,
    Profile,
    VerificationRequest,
)
from app.models.company import Company, EmailTemplate, HiringManager
from app.models.enums import (
    APPROVAL_CHAIN,
    ApprovalDecision,
    JobStatus,
    LinkSource,
    LLMProvider,
    LLMRoleHint,
    OTPChannel,
    PipelineStatus,
    Role,
    SubmittedVia,
    Tier,
    UserStatus,
    VerificationStatus,
)
from app.models.job import Job, JobApproval
from app.models.tenant import AuditLog, LLMProviderKey, RolePermission, Tenant
from app.models.user import OTPChallenge, User

__all__ = [
    "Base",
    "APPROVAL_CHAIN",
    "ApprovalDecision",
    "AuditLog",
    "Candidate",
    "Company",
    "EmailTemplate",
    "HiringManager",
    "Interview",
    "Job",
    "JobApproval",
    "JobCandidateLink",
    "JobStatus",
    "LinkSource",
    "LLMProvider",
    "LLMProviderKey",
    "LLMRoleHint",
    "OTPChallenge",
    "OTPChannel",
    "PipelineStatus",
    "PipelineStatusEntry",
    "Profile",
    "Role",
    "RolePermission",
    "SubmittedVia",
    "Tenant",
    "Tier",
    "User",
    "UserStatus",
    "VerificationRequest",
]
