"""Canonical capability names + the default permission matrix (PRD §6).

This is the seed data for the RBAC engine — Super Admin can vary it per
tenant via `role_permissions`. Never branch on role in business logic;
use `require_capability(...)` (claude.md rule 3).
"""
from app.models.enums import Role

# Capability constants — use these everywhere, never string literals inline.
CREATE_COMPANY_PAGE = "create_company_page"
CREATE_HIRING_MANAGERS = "create_hiring_managers"
CONFIGURE_APPROVAL_LEVELS = "configure_approval_levels"
EDIT_JOB_DESCRIPTION = "edit_job_description"          # HR, post-ratification
CREATE_JOB = "create_job"                              # Hiring Manager JD creation (FR-3.1)
APPROVE_JOB = "approve_job"                            # at assigned level only
ADD_COMPENSATION = "add_compensation"
VIEW_DATABANK = "view_databank"
UPLOAD_RESUMES = "upload_resumes"
TRIGGER_MATCHING = "trigger_matching"
SEND_OUTREACH = "send_outreach"                        # 40-aspect + verification
VIEW_REVIEW_SCREEN = "view_review_screen"
DECIDE_PROFILE = "decide_profile"                      # Shortlist / Reject / Hold
SCHEDULE_INTERVIEWS = "schedule_interviews"
UPDATE_PIPELINE_STATUS = "update_pipeline_status"
VIEW_DASHBOARD = "view_dashboard"
EDIT_ROLE_PERMISSIONS = "edit_role_permissions"        # Super Admin only
MANAGE_EMAIL_TEMPLATES = "manage_email_templates"

ALL_CAPABILITIES = [
    CREATE_COMPANY_PAGE, CREATE_HIRING_MANAGERS, CONFIGURE_APPROVAL_LEVELS,
    EDIT_JOB_DESCRIPTION, CREATE_JOB, APPROVE_JOB, ADD_COMPENSATION,
    VIEW_DATABANK, UPLOAD_RESUMES, TRIGGER_MATCHING, SEND_OUTREACH,
    VIEW_REVIEW_SCREEN, DECIDE_PROFILE, SCHEDULE_INTERVIEWS,
    UPDATE_PIPELINE_STATUS, VIEW_DASHBOARD, EDIT_ROLE_PERMISSIONS,
    MANAGE_EMAIL_TEMPLATES,
]

# Default template (PRD §6). {role: {capability: allowed}} — capabilities not
# listed default to False for that role.
DEFAULT_PERMISSION_MATRIX: dict[Role, dict[str, bool]] = {
    Role.recruiter: {
        VIEW_DATABANK: True,
        UPLOAD_RESUMES: True,
        TRIGGER_MATCHING: True,
        SCHEDULE_INTERVIEWS: True,
        UPDATE_PIPELINE_STATUS: True,
        VIEW_DASHBOARD: True,
    },
    Role.hr_manager: {
        EDIT_JOB_DESCRIPTION: True,
        ADD_COMPENSATION: True,
        VIEW_DATABANK: True,
        TRIGGER_MATCHING: True,
        SEND_OUTREACH: True,
        VIEW_REVIEW_SCREEN: True,
        UPDATE_PIPELINE_STATUS: True,
        VIEW_DASHBOARD: True,
        MANAGE_EMAIL_TEMPLATES: True,
    },
    Role.hiring_manager: {
        CREATE_JOB: True,
        APPROVE_JOB: True,          # only if assigned a level — FSM re-checks assignment
        VIEW_REVIEW_SCREEN: True,   # read-only, granted profiles only
        DECIDE_PROFILE: True,
    },
    Role.client: {
        CREATE_COMPANY_PAGE: True,
        CREATE_HIRING_MANAGERS: True,
        CONFIGURE_APPROVAL_LEVELS: True,
        APPROVE_JOB: True,          # if assigned a level
        MANAGE_EMAIL_TEMPLATES: True,
    },
    # super_admin bypasses require_capability via its dedicated audit-logged
    # path; candidates use the portal endpoints (separate JWT audience).
}
