"""All SQLAlchemy models, mirroring ESD §4. Import from here so Base.metadata
sees every table (Alembic autogenerate, tests)."""
from app.models.base import Base
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    CandidateTechnicalQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
    ReportSkillEvidence,
    TechnicalQuestion,
)
from app.models.bd import (
    CHANNELS,
    PROGRESS_FLAGS,
    PROGRESS_LABELS,
    SOCIAL_SOURCES,
    TENANT_PROSPECT,
    BDLead,
)
from app.models.billing import (
    CONSUMPTION_SUBUNITS,
    EVENT_COMPLETED,
    EVENT_GRANT,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    LEDGER_EVENT_TYPES,
    SUBSCRIPTION_STATUSES,
    SUBUNITS_PER_CREDIT,
    BillingTransaction,
    CreditLedgerEntry,
    CreditLot,
    CreditLotDraw,
    CreditPurchase,
    OldProfileReview,
    PricingPlan,
    WebhookEvent,
)
from app.models.agent import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    AgentExecutionTrace,
    AgentLearning,
    AgentToolApprovalRule,
)
from app.models.agent_action import AgentAction
from app.models.context import ContextChunk
from app.models.evidence import (
    EvidenceClaim,
    EvidenceClaimLink,
    EvidenceItemRow,
    PortableEvidenceItem,
)
from app.models.candidate_update import CandidateUpdate
from app.models.deletion import CandidateDeletionRequest
from app.models.support import SupportMessage, SupportThread
from app.models.bgv import BGVInquiry, BGVShareConsent
from app.models.bgv_documents import BGVContactCorrection, CandidateBGVDocument
from app.models.bgv_verification import BGVVerification
from app.models.conversation import (
    Conversation,
    ConversationAttachment,
    ConversationMessage,
    ConversationParticipant,
)
from app.models.employment import CandidateEmployment
from app.models.dual_mode import AssessmentConsent, VideoRecording
from app.models.project import CandidateProject
from app.models.drishti import DrishtiProfile
from app.models.candidate import (
    Candidate,
    CandidateTeamReview,
    Interview,
    JobCandidateLink,
    PipelineStatusEntry,
    Profile,
)
from app.models.company import Company, EmailTemplate, HiringManager
from app.models.compliance import (
    COMMERCIAL_DOCUMENT_TYPES,
    DOCUMENT_GROUPS,
    DOCUMENT_LABELS,
    DOCUMENT_TYPES,
    TAX_DOCUMENT_TYPES,
    ComplianceDocument,
)
from app.models.email_log import EMAIL_TYPES, EmailLog
from app.models.email_sender import ClientEmailSender
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
    Tier,
    UserStatus,
)
from app.models.job import JDDraft, Job, JobApproval
from app.models.job_setup import (
    SWOT_ANALYSIS_EDITED,
    SWOT_ANALYSIS_FAILED,
    SWOT_ANALYSIS_GENERATED,
    SWOT_ANALYSIS_NOT_GENERATED,
    SWOT_ANALYSIS_SECTIONS,
    SWOT_AREAS,
    SWOT_STATUS_ACTIVE,
    SWOT_STATUS_COMPLETE,
    JobMatchingCategory,
    JobSwotAnalysis,
    JobSwotIntake,
)
from app.models.proctoring import (
    ProctoringEvent,
    ProctoringReport,
    ProctoringSession,
)
from app.models.cost import (
    COST_BASIS_ESTIMATED,
    COST_BASIS_FINALIZED,
    COST_BASIS_VALUES,
    AssessmentCostRecord,
)
from app.models.telemetry import TelemetryEvent
from app.models.tenant import AuditLog, LLMProviderKey, RolePermission, Tenant
from app.models.hiring import (
    CalibrationRecord,
    Evaluation,
    ReviewDisposition,
)
# The append-only record of which scorecard version a job was frozen against,
# and when. Read by `orchestration/versioning` to answer what a candidate
# applied under.
from app.models.job_scorecard_binding import JobScorecardBinding
from app.models.user import OTPChallenge, User

__all__ = [
    "DrishtiProfile",
    "Base",
    "APPROVAL_CHAIN",
    "ApprovalDecision",
    "BDLead",
    "BGVContactCorrection",
    "BGVInquiry",
    "BGVShareConsent",
    "CHANNELS",
    "AssessmentAnswer",
    "AssessmentConsent",
    "AssessmentConversation",
    "AssessmentMessage",
    "VideoRecording",
    "AuditLog",
    "BillingTransaction",
    "Candidate",
    "CandidateProject",
    "CandidateUpdate",
    "CandidateDeletionRequest",
    "SupportMessage",
    "SupportThread",
    "CandidateQuestion",
    "CandidateTeamReview",
    "CandidateTechnicalQuestion",
    "CONSUMPTION_SUBUNITS",
    "CreditLedgerEntry",
    "CreditLot",
    "CreditLotDraw",
    "CreditPurchase",
    "EVENT_COMPLETED",
    "EVENT_GRANT",
    "EVENT_INCOMPLETE",
    "EVENT_NO_SHOW",
    "EVENT_OLD_PROFILE_REVIEW",
    "LEDGER_EVENT_TYPES",
    "OldProfileReview",
    "PricingPlan",
    "SUBSCRIPTION_STATUSES",
    "SUBUNITS_PER_CREDIT",
    "WebhookEvent",
    "COMMERCIAL_DOCUMENT_TYPES",
    "Company",
    "ComplianceDocument",
    "DOCUMENT_GROUPS",
    "DOCUMENT_LABELS",
    "DOCUMENT_TYPES",
    "EMAIL_TYPES",
    "ClientEmailSender",
    "EmailLog",
    "EmailTemplate",
    "HiringManager",
    "FunctionalSkillsReport",
    "Interview",
    "Job",
    "JobApproval",
    "JobMatchingCategory",
    "JobSwotAnalysis",
    "JobSwotIntake",
    "SWOT_ANALYSIS_EDITED",
    "SWOT_ANALYSIS_FAILED",
    "SWOT_ANALYSIS_GENERATED",
    "SWOT_ANALYSIS_NOT_GENERATED",
    "SWOT_ANALYSIS_SECTIONS",
    "SWOT_AREAS",
    "SWOT_STATUS_ACTIVE",
    "SWOT_STATUS_COMPLETE",
    "JobCandidateLink",
    "JobCompetency",
    "JobStatus",
    "LinkSource",
    "LLMProvider",
    "LLMProviderKey",
    "LLMRoleHint",
    "OTPChallenge",
    "OTPChannel",
    "PipelineStatus",
    "PipelineStatusEntry",
    "PROGRESS_FLAGS",
    "PROGRESS_LABELS",
    "Profile",
    "Role",
    "SOCIAL_SOURCES",
    "TENANT_PROSPECT",
    "ReportDimension",
    "ReportSkillEvidence",
    "RolePermission",
    "TAX_DOCUMENT_TYPES",
    "AgentAction",
    "AgentExecutionTrace",
    "CalibrationRecord",
    "Evaluation",
    "ReviewDisposition",
    "JobScorecardBinding",
    "AgentLearning",
    "AgentToolApprovalRule",
    "ContextChunk",
    "EvidenceClaim",
    "EvidenceClaimLink",
    "EvidenceItemRow",
    "PortableEvidenceItem",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_SUCCESS",
    "ProctoringEvent",
    "ProctoringReport",
    "ProctoringSession",
    "AssessmentCostRecord",
    "COST_BASIS_ESTIMATED",
    "COST_BASIS_FINALIZED",
    "COST_BASIS_VALUES",
    "TelemetryEvent",
    "Tenant",
    "TechnicalQuestion",
    "Tier",
    "User",
    "UserStatus",
    "CandidateEmployment",
    "BGVVerification",
    "CandidateBGVDocument",
    "Conversation",
    "ConversationParticipant",
    "ConversationMessage",
    "ConversationAttachment",
]
