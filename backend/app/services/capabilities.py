"""Canonical capability names + the default permission matrix (PRD §6).

This is the seed data for the RBAC engine — Super Admin can vary it per
tenant via `role_permissions`. Never branch on role in business logic;
use `require_capability(...)` (claude.md rule 3).
"""
from app.models.enums import Role

# Capability constants — use these everywhere, never string literals inline.
CREATE_COMPANY_PAGE = "create_company_page"
# Client-org staff management (HR Manager / Recruiter / Hiring Manager
# accounts). Owned by the Client role; grantable to HR Managers per tenant via
# the dynamic permission engine. NOT an Owner/Super Admin function — the
# corrected role model (Pickready.docx §2) puts the whole staff hierarchy
# inside the client organization.
MANAGE_STAFF = "manage_staff"
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
    CREATE_COMPANY_PAGE, MANAGE_STAFF, CONFIGURE_APPROVAL_LEVELS,
    EDIT_JOB_DESCRIPTION, CREATE_JOB, APPROVE_JOB, ADD_COMPENSATION,
    VIEW_DATABANK, UPLOAD_RESUMES, TRIGGER_MATCHING, SEND_OUTREACH,
    VIEW_REVIEW_SCREEN, DECIDE_PROFILE, SCHEDULE_INTERVIEWS,
    UPDATE_PIPELINE_STATUS, VIEW_DASHBOARD, EDIT_ROLE_PERMISSIONS,
    MANAGE_EMAIL_TEMPLATES,
]

# Flattened staff model (PRD v1.0 §4, FINAL — 2026-07-24). HR Manager,
# Recruiter, and Hiring Manager are EQUAL: all three create+publish jobs and
# share one candidate pool. There is no multi-level approval surfaced to them
# (the approval FSM code remains in place but bypassed — jobs publish directly,
# see api/jobs.py and approval_fsm.plan_direct_publish). The three roles must
# end up FUNCTIONALLY IDENTICAL, so they get the same operational grant set.
# This stays data (require_capability), never a role branch (claude.md rule 3).
_STAFF_OPERATIONAL: dict[str, bool] = {
    CREATE_JOB: True,               # create → published directly (no approval chain)
    EDIT_JOB_DESCRIPTION: True,
    ADD_COMPENSATION: True,
    VIEW_DATABANK: True,
    UPLOAD_RESUMES: True,
    TRIGGER_MATCHING: True,
    SEND_OUTREACH: True,
    VIEW_REVIEW_SCREEN: True,
    DECIDE_PROFILE: True,
    SCHEDULE_INTERVIEWS: True,
    UPDATE_PIPELINE_STATUS: True,
    VIEW_DASHBOARD: True,
    MANAGE_EMAIL_TEMPLATES: True,
}

# Default template. {role: {capability: allowed}} — capabilities not listed
# default to False for that role. APPROVE_JOB / CONFIGURE_APPROVAL_LEVELS are
# intentionally NOT granted to the staff roles: the approval chain is dormant.
DEFAULT_PERMISSION_MATRIX: dict[Role, dict[str, bool]] = {
    Role.hr_manager: dict(_STAFF_OPERATIONAL),
    Role.recruiter: dict(_STAFF_OPERATIONAL),
    Role.hiring_manager: dict(_STAFF_OPERATIONAL),
    Role.client: {
        # Company Admin: company page + staff management. Also allowed to
        # create jobs (a Company Admin is a legitimate job poster).
        CREATE_COMPANY_PAGE: True,
        MANAGE_STAFF: True,
        CONFIGURE_APPROVAL_LEVELS: True,   # dormant (FSM bypassed) but kept grantable
        CREATE_JOB: True,
        MANAGE_EMAIL_TEMPLATES: True,
    },
    # EDIT_ROLE_PERMISSIONS stays Owner-only (granted to no role here).
    # super_admin bypasses require_capability via its dedicated audit-logged
    # path; candidates use the portal endpoints (separate JWT audience).
}
