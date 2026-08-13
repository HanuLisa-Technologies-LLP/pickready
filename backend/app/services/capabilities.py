"""Canonical capability names + the default permission matrix (PRD §6).

This is the seed data for the RBAC engine — Super Admin can vary it per
tenant via `role_permissions`. Never branch on role in business logic;
use `require_capability(...)` (claude.md rule 3).
"""
from app.models.enums import Role

# Capability constants — use these everywhere, never string literals inline.
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
# Company Portal -> Profile: the About / Work Life / Benefits sections every
# new job snapshots (spec §3.2/§7.1).
EDIT_COMPANY_PROFILE = "edit_company_profile"
# Publishing is bundled into CREATE_JOB on the flat model (jobs publish
# directly on create), but the spec's permission matrix lists it separately so
# an HR Head can grant JD authorship without publishing rights.
PUBLISH_JOB = "publish_job"
# Customer Portal -> Compliance: uploading the customer's own tax and
# commercial records (Provider Portal spec §3.3). Granted to the Company Admin
# (HR Head) alone by default — the three staff roles are otherwise identical,
# so this is the one place the flat model deliberately does NOT flatten:
# a GSTIN certificate and a signed agreement are the company's legal
# instruments, not recruitment data. Still a grant, not a role branch, so an
# HR Head can delegate it to a specific person via the per-user overlay.
MANAGE_COMPLIANCE_DOCUMENTS = "manage_compliance_documents"

# Customer Portal -> Billing: choosing a plan, opening Razorpay Checkout, and
# changing or cancelling the subscription. Granted to the Company Admin (HR
# Head) alone by default, for the same reason as the compliance documents above:
# committing the company to a recurring charge is a financial act, not
# recruitment work. Reading the credit balance is separate (VIEW_BILLING) so a
# recruiter can see why invitations are paused without being able to spend.
MANAGE_BILLING = "manage_billing"
VIEW_BILLING = "view_billing"

# Business Development Portal (the fourth portal, /bd). Three grants, one per
# area of the console, so a BD lead can be given the customer database and the
# AI Reach search without the ability to edit anyone's pipeline.
MANAGE_BD_LEADS = "manage_bd_leads"        # Personal Reach + Social Reach
VIEW_BD_CUSTOMERS = "view_bd_customers"    # Customers page + CSV export
USE_AI_REACH = "use_ai_reach"              # AI Reach search

ALL_CAPABILITIES = [
    MANAGE_STAFF, CONFIGURE_APPROVAL_LEVELS,
    EDIT_JOB_DESCRIPTION, CREATE_JOB, APPROVE_JOB, ADD_COMPENSATION,
    VIEW_DATABANK, UPLOAD_RESUMES, TRIGGER_MATCHING, SEND_OUTREACH,
    VIEW_REVIEW_SCREEN, DECIDE_PROFILE, SCHEDULE_INTERVIEWS,
    UPDATE_PIPELINE_STATUS, VIEW_DASHBOARD, EDIT_ROLE_PERMISSIONS,
    MANAGE_EMAIL_TEMPLATES, EDIT_COMPANY_PROFILE, PUBLISH_JOB,
    MANAGE_COMPLIANCE_DOCUMENTS,
    MANAGE_BD_LEADS, VIEW_BD_CUSTOMERS, USE_AI_REACH,
    MANAGE_BILLING, VIEW_BILLING,
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
    PUBLISH_JOB: True,
    EDIT_COMPANY_PROFILE: True,
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
    # Read-only. A recruiter whose invitations stop sending must be able to see
    # that the credit pool is in deficit; they still cannot change the plan.
    VIEW_BILLING: True,
}

# The customer-side grant set, shared by all four customer roles.
#
# Migration 0031 seeds exactly this list globally, for exactly these four roles,
# and this dict must agree with it: `_seed_permissions` (api/admin.py) copies
# THIS dict into TENANT-SCOPED rows whenever the Owner console creates a
# customer, and a tenant row BEATS the global template in
# rbac.resolve_permission. When the two disagree, every console-created customer
# silently gets the smaller of the two sets.
#
# The product decision behind it (PRD v1.0 §4): recruiters, hiring managers, HR
# managers and the client company owner all run the customer's hiring and are
# FUNCTIONALLY IDENTICAL, including billing. What separates them is not a
# capability list; it is who the account belongs to.
_CUSTOMER_FULL_ACCESS: dict[str, bool] = {
    **_STAFF_OPERATIONAL,
    MANAGE_STAFF: True,
    CONFIGURE_APPROVAL_LEVELS: True,   # dormant (FSM bypassed) but kept grantable
    APPROVE_JOB: True,                 # dormant for the same reason
    MANAGE_COMPLIANCE_DOCUMENTS: True,
    MANAGE_BILLING: True,
}

# Default template. {role: {capability: allowed}} — capabilities not listed
# default to False for that role.
#
# EDIT_ROLE_PERMISSIONS is never here: it rewrites the matrix itself, so
# granting it to a customer role removes the boundary rather than widening it.
# The three MANAGE_BD_* / USE_AI_REACH grants stay with the `bd` role, which is
# PickReady's own sales console and has no tenant. Same two exclusions, and the
# same reasoning, as migration 0031.
DEFAULT_PERMISSION_MATRIX: dict[Role, dict[str, bool]] = {
    Role.recruitment_manager: dict(_CUSTOMER_FULL_ACCESS),
    Role.hr_manager: dict(_CUSTOMER_FULL_ACCESS),
    Role.recruiter: dict(_CUSTOMER_FULL_ACCESS),
    # Bottom of the hierarchy: there is no subordinate role to manage.
    Role.hiring_manager: {**_CUSTOMER_FULL_ACCESS, MANAGE_STAFF: False},
    # Company Admin: the same functional access, on the account they own.
    Role.client: dict(_CUSTOMER_FULL_ACCESS),
    # Business Development. Deliberately NOT given any recruitment capability:
    # a BD rep sells the platform, they do not run a customer's hiring. The set
    # here must match migration 0023's seeded rows exactly, or the engine (which
    # reads the rows, not this dict) and this template will disagree.
    Role.bd: {
        MANAGE_BD_LEADS: True,
        VIEW_BD_CUSTOMERS: True,
        USE_AI_REACH: True,
    },
    # EDIT_ROLE_PERMISSIONS stays Owner-only (granted to no role here).
    # super_admin bypasses require_capability via its dedicated audit-logged
    # path; candidates use the portal endpoints (separate JWT audience).
}
