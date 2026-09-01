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

# ── RBAC_SPECIFICATION.md 24: the capabilities that specification names and
#    this codebase did not have ────────────────────────────────────────────
#
# Before 2026-08-29 the product had ONE job-authoring capability
# (EDIT_JOB_DESCRIPTION) covering both halves of what the specification calls
# out as its most fundamental distinction (25.1): the Recruiter drafts the JD,
# and the Hiring Manager owns the role DEFINITION. One capability cannot
# express "the Recruiter may write this document but may not decide what the
# candidate is graded on", so the six criteria capabilities below exist
# separately and are refused to the Recruiter by 24 and 26.
#
# HIRING_MANAGER_CONTROLLED (below) is the set 9.4, 19, 26, 27 and 39 all
# enumerate. It is written once here and read everywhere, because a list this
# load-bearing copied into a second module drifts the first time somebody adds
# a seventh field.

#: Job visibility. RBAC 24's row is "View all company jobs" and three of its
#: five cells read "Scoped", so the word "all" is precisely what the SCOPED
#: cell takes away: an org-wide role sees every job in the tenant, and a
#: scoped role sees the jobs it is assigned to (9.2, 23). Named for what it
#: grants rather than for the row's widest cell, because a constant called
#: VIEW_COMPANY_JOBS held by a Recruiter would read as a lie.
VIEW_COMPANY_JOBS = "view_company_jobs"

#: 9.3 / 39: the Recruiter hands the draft to the assigned Hiring Manager.
SEND_JD_TO_HIRING_MANAGER = "send_jd_to_hiring_manager"

# The six Hiring-Manager-controlled fields (10.4).
EDIT_MUST_HAVE_SKILLS = "edit_must_have_skills"
EDIT_NICE_TO_HAVE_SKILLS = "edit_nice_to_have_skills"
EDIT_BEHAVIOURAL_COMPETENCIES = "edit_behavioural_competencies"
EDIT_JOB_PHILOSOPHY = "edit_job_philosophy"
EDIT_SWOT = "edit_swot"
EDIT_EVALUATION_RUBRICS = "edit_evaluation_rubrics"

#: 12 / 20: the explicit transition from role definition to publication
#: readiness. Explicit because 20 requires it to record who and when.
FINALIZE_ROLE_DEFINITION = "finalize_role_definition"

#: 24 lists a "Reject JD" row and 11 says the HIRING MANAGER has no such path.
#: Both are true: the capability exists for the Super Admin and the HR Manager,
#: and no amount of tenant configuration can hand it to a Hiring Manager (see
#: NEVER in RBAC_INVARIANTS). The Hiring Manager edits until the definition is
#: right; rejection is not their move to make.
REJECT_JD = "reject_jd"

#: 13.4 / 29. Every remark carries author and timestamp, and nobody edits
#: another person's remark, which is a property of the write path rather than
#: of this grant.
ADD_TEAM_REVIEW_REMARK = "add_team_review_remark"

#: 13.3 / 24. Split from VIEW_REVIEW_SCREEN because an Interview Manager reads
#: the candidate's report and rating WITHOUT reaching the recruiter's review
#: screen, which carries pipeline controls they must not have.
VIEW_CANDIDATE_REPORTS = "view_candidate_reports"
VIEW_CANDIDATE_RATINGS = "view_candidate_ratings"

#: 7.3 "Assign roles". Distinct from MANAGE_STAFF: creating a seat and
#: deciding what authority that seat carries are separable, and 24 lists them
#: as separate rows.
ASSIGN_ROLES = "assign_roles"

#: spec-doc6 C7: an integrity finding is disposed of by the HR Manager by
#: right and by the Super Admin as an audited override, and by nobody else.
#: No flag ever auto-rejects; this capability records that a HUMAN looked.
INTEGRITY_DISPOSITION = "integrity_disposition"

#: The authoritative hiring definition (10.4). Ordered so a rendered list is
#: stable; membership is what matters.
HIRING_MANAGER_CONTROLLED: frozenset[str] = frozenset(
    {
        EDIT_MUST_HAVE_SKILLS,
        EDIT_NICE_TO_HAVE_SKILLS,
        EDIT_BEHAVIOURAL_COMPETENCIES,
        EDIT_JOB_PHILOSOPHY,
        EDIT_SWOT,
        EDIT_EVALUATION_RUBRICS,
        FINALIZE_ROLE_DEFINITION,
    }
)

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
    # RBAC_SPECIFICATION.md 24, appended 2026-08-29. Appended rather than
    # interleaved because resolve_capability_set returns capabilities in THIS
    # order and an existing response's field order should not shuffle.
    VIEW_COMPANY_JOBS, SEND_JD_TO_HIRING_MANAGER,
    EDIT_MUST_HAVE_SKILLS, EDIT_NICE_TO_HAVE_SKILLS,
    EDIT_BEHAVIOURAL_COMPETENCIES, EDIT_JOB_PHILOSOPHY, EDIT_SWOT,
    EDIT_EVALUATION_RUBRICS, FINALIZE_ROLE_DEFINITION, REJECT_JD,
    ADD_TEAM_REVIEW_REMARK, VIEW_CANDIDATE_REPORTS, VIEW_CANDIDATE_RATINGS,
    ASSIGN_ROLES, INTEGRITY_DISPOSITION,
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
# ReadyPick's own sales console and has no tenant. Same two exclusions, and the
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


# ═══════════════════════════════════════════════════════════════════════════
# RBAC_SPECIFICATION.md 24 as data: the CEILING, not the grant
# ═══════════════════════════════════════════════════════════════════════════
#
# WHY THIS IS A SECOND TABLE AND NOT AN EDIT TO THE FIRST ONE
# -----------------------------------------------------------
# `DEFAULT_PERMISSION_MATRIX` above is the TENANT-CONFIGURABLE grant layer: a
# customer's Super Admin may widen or narrow it per tenant, and a per-user
# overlay may pin one person. That is the right shape for an operational
# permission and the wrong shape for an architectural invariant. RBAC 39 calls
# its rules "architectural invariants", and 26 says the Recruiter MUST NOT be
# able to alter finalized criteria "through normal Recruiter permissions" -- a
# rule a tenant row could switch off is not that.
#
# So this table is a CEILING applied on top of the grant engine, never a
# second grant engine. `rbac.decide` computes
#
#     effective = grant_engine_says_yes AND invariant_allows
#
# which can only ever narrow. Same two-layer shape `services/hiring/layers.py`
# already uses: BOUNDS may tune, INVARIANTS may not be suspended.
#
# THE ASTERISKS ARE PRESERVED, NOT FLATTENED
# ------------------------------------------
# RBAC 24 carries three footnotes and each means something different at
# runtime. Collapsing them to YES/NO would throw away exactly the information
# a reader of the audit trail needs.

from enum import Enum as _Enum


class Invariant(str, _Enum):
    """One cell of the RBAC 24 matrix.

    NEVER is stronger than DENY: a DENY cell describes the baseline and a
    tenant may in principle be granted an exception by a future product
    decision, while a NEVER cell is a rule the specification states as a
    non-goal (36) and no configuration path may reach. `reject_jd` for a
    Hiring Manager is the canonical NEVER, and 11 is unusually emphatic.
    """

    #: Unconditional YES for this role.
    ALLOW = "allow"
    #: YES, but only for a job this user is assigned to (9.2, 10.2, 13.1, 23).
    SCOPED = "scoped"
    #: YES*. The matrix marks it deliberately conservative and it stays
    #: allowed, but the action is recorded as an audited exception so the
    #: future product decision has evidence to work from (spec-doc6 C13).
    ALLOW_AUDITED_EXCEPTION = "allow_audited_exception"
    #: YES**. Allowed, but off the canonical flow: the canonical initial JD
    #: comes from the Recruiter. Recorded so the deviation stays visible.
    ALLOW_NON_CANONICAL = "allow_non_canonical"
    #: YES***. Allowed only while the job is still in drafting scope, and
    #: refused from FINALIZED onward (26).
    ALLOW_DRAFT_SCOPE = "allow_draft_scope"
    #: NO. The baseline refuses it.
    DENY = "deny"
    #: NO*. Refused, and the asterisk is carried so a future decision can find
    #: every cell that was conservative by choice rather than by principle.
    DENY_CONSERVATIVE = "deny_conservative"
    #: NO, and unreachable by any grant, tenant row or user overlay.
    NEVER = "never"


#: Cells that permit the action. Everything else refuses.
_PERMITTING: frozenset["Invariant"] = frozenset(
    {
        Invariant.ALLOW,
        Invariant.SCOPED,
        Invariant.ALLOW_AUDITED_EXCEPTION,
        Invariant.ALLOW_NON_CANONICAL,
        Invariant.ALLOW_DRAFT_SCOPE,
    }
)

#: Cells whose use must be recorded as a deviation from the canonical flow.
#: Not a refusal: 7.5 explicitly grants the Super Admin override authority and
#: then requires the override to appear in the audit trail.
EXCEPTIONAL: frozenset["Invariant"] = frozenset(
    {Invariant.ALLOW_AUDITED_EXCEPTION, Invariant.ALLOW_NON_CANONICAL}
)


def permits(cell: "Invariant") -> bool:
    return cell in _PERMITTING


#: The five internal client roles of RBAC 5, in authority order. `client` is
#: this product's identifier for the Client Super Admin; docs/reference/RBAC.md carries
#: the full name mapping and why the identifiers were not renamed.
#:
#: RBAC 5 says "four internal role categories" and then lists five. spec-doc6
#: C4 settles it: five is correct and the count is an editorial defect in the
#: source document. Do not implement four.
CLIENT_ROLES: tuple[Role, ...] = (
    Role.client,             # Client Super Admin
    Role.hr_manager,         # HR Manager
    Role.recruiter,          # Recruiter
    Role.hiring_manager,     # Hiring Manager
    Role.interview_manager,  # Interview Manager
)

_A = Invariant.ALLOW
_S = Invariant.SCOPED
_AX = Invariant.ALLOW_AUDITED_EXCEPTION
_AN = Invariant.ALLOW_NON_CANONICAL
_AD = Invariant.ALLOW_DRAFT_SCOPE
_D = Invariant.DENY
_DC = Invariant.DENY_CONSERVATIVE
_N = Invariant.NEVER

#: RBAC 24, transcribed row by row. Column order is Super Admin, HR Manager,
#: Recruiter, Hiring Manager, Interview Manager, matching CLIENT_ROLES.
RBAC_INVARIANTS: dict[str, dict[Role, Invariant]] = {
    # | Manage staff | YES | NO* | NO | NO | NO |
    MANAGE_STAFF: {
        Role.client: _A, Role.hr_manager: _DC, Role.recruiter: _D,
        Role.hiring_manager: _D, Role.interview_manager: _D,
    },
    # | Assign roles | YES | NO* | NO | NO | NO |
    ASSIGN_ROLES: {
        Role.client: _A, Role.hr_manager: _DC, Role.recruiter: _D,
        Role.hiring_manager: _D, Role.interview_manager: _D,
    },
    # | View all company jobs | YES | YES | Scoped | Scoped | Scoped |
    VIEW_COMPANY_JOBS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _S, Role.interview_manager: _S,
    },
    # | Create initial JD  | YES | YES | YES | YES** | NO |
    # | Generate initial JD| YES | YES | YES | YES** | NO |
    # One capability in this codebase: CREATE_JOB covers both the manual and
    # the generated draft, and the two rows carry identical values.
    CREATE_JOB: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _A,
        Role.hiring_manager: _AN, Role.interview_manager: _D,
    },
    # | Edit JD | YES | YES | YES*** | YES | NO |
    EDIT_JOB_DESCRIPTION: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _AD,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    # | Send JD to Hiring Manager | YES | YES | YES | NO | NO |
    SEND_JD_TO_HIRING_MANAGER: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _D, Role.interview_manager: _D,
    },
    # The six Hiring-Manager-controlled criteria rows, all identical in 24:
    # | ... | YES | YES | NO | YES | NO |
    # NEVER for the Recruiter, not DENY: 26 is a list of things the Recruiter
    # must not be able to do to a finalized definition, and 9.4 says the
    # restriction is enforced at the UI, the API and the database mutation
    # path. A cell a tenant row could switch on would satisfy none of that.
    EDIT_MUST_HAVE_SKILLS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    EDIT_NICE_TO_HAVE_SKILLS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    EDIT_BEHAVIOURAL_COMPETENCIES: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    EDIT_JOB_PHILOSOPHY: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    EDIT_SWOT: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    EDIT_EVALUATION_RUBRICS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    # | Finalize role definition | YES | YES | NO | YES | NO |
    FINALIZE_ROLE_DEFINITION: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _N,
        Role.hiring_manager: _S, Role.interview_manager: _D,
    },
    # | Reject JD | YES | YES | NO | NO | NO |
    # NEVER for the Hiring Manager. 11 and 36 both state it, and 11 gives the
    # reason: the Hiring Manager edits until the definition is right, so a
    # terminal rejection is a workflow that must not exist for them.
    REJECT_JD: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _D,
        Role.hiring_manager: _N, Role.interview_manager: _D,
    },
    # | Publish job | YES | YES* | YES | NO | NO |
    #
    # THE HR MANAGER IS WITHHELD, AND THIS IS THE ONE CELL THAT DEPARTS FROM 24
    # ------------------------------------------------------------------------
    # 9.6 states the rule twice and names exactly one exception: "Recruiter
    # publishes the job. Period. The Super Admin is an administrative exception
    # because the Super Admin has ultimate authority and can override role
    # restrictions." The HR Manager is not in that sentence.
    #
    # 24 nonetheless marks the HR Manager YES*, and its own footnote says the
    # asterisked entries "are intentionally conservative and may require an
    # explicit future product decision". A cell whose footnote says the
    # decision has not been made is not an affirmative grant, so 9.6's prose
    # governs and this is withheld pending an owner decision rather than
    # granted on a disclaimer.
    #
    # spec-doc6 C13 reads this as "HR Manager and Super Admin publish only as
    # an audited exception". That restatement is wider than 9.6, which is the
    # text it cites, so it does not carry.
    #
    # The Super Admin's cell IS an audited exception: 7.5 grants the override
    # and then requires it to be recorded.
    PUBLISH_JOB: {
        Role.client: _AX, Role.hr_manager: _DC, Role.recruiter: _S,
        Role.hiring_manager: _D, Role.interview_manager: _D,
    },
    # | View candidates | YES | YES | YES (scoped) x3 |
    VIEW_REVIEW_SCREEN: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _S, Role.interview_manager: _S,
    },
    # | Shortlist candidates | YES | YES | YES | NO* | NO |
    # | Reject candidates    | YES | YES | YES | NO* | NO |
    # DECIDE_PROFILE is this codebase's one capability for shortlist / reject
    # / hold, so the two rows collapse onto it with identical values.
    DECIDE_PROFILE: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _DC, Role.interview_manager: _D,
    },
    # | Move candidates through stages | YES | YES | YES | NO* | NO |
    UPDATE_PIPELINE_STATUS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _DC, Role.interview_manager: _D,
    },
    # | View candidate reports | YES | YES | YES (scoped) x3 |
    VIEW_CANDIDATE_REPORTS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _S, Role.interview_manager: _S,
    },
    # | View candidate ratings | YES | YES | YES (scoped) x3 |
    VIEW_CANDIDATE_RATINGS: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _S,
        Role.hiring_manager: _S, Role.interview_manager: _S,
    },
    # | Add Team Review remarks | YES | YES | YES* | YES* | YES |
    # The Interview Manager is the PRIMARY participant (13.4, spec-doc6 C6);
    # the two asterisked cells are conservative allowances, kept allowed.
    ADD_TEAM_REVIEW_REMARK: {
        Role.client: _A, Role.hr_manager: _A, Role.recruiter: _AX,
        Role.hiring_manager: _AX, Role.interview_manager: _S,
    },
    # Not a 24 row. spec-doc6 C7: HR Manager by right, Super Admin by audited
    # override, nobody else. Recorded here so the resolution is enforced
    # rather than remembered.
    INTEGRITY_DISPOSITION: {
        Role.client: _AX, Role.hr_manager: _A, Role.recruiter: _D,
        Role.hiring_manager: _D, Role.interview_manager: _D,
    },
}


def invariant_for(role: Role | str, capability: str) -> Invariant:
    """The RBAC 24 cell for one (role, capability), denying by default.

    A capability with no row here is NOT constrained by the matrix and the
    grant engine decides it alone. That is deliberate: this table covers the
    specification's rows, and the product has capabilities (billing,
    compliance, business development) the specification does not speak to.

    A ROLE missing from a row that DOES exist is denied, because the matrix
    enumerates every client role, so an absent role is one the specification
    never authorised for that capability.
    """
    row = RBAC_INVARIANTS.get(capability)
    if row is None:
        return Invariant.ALLOW
    try:
        parsed = role if isinstance(role, Role) else Role(str(role))
    except ValueError:
        return Invariant.DENY
    return row.get(parsed, Invariant.DENY)


# ── The Interview Manager's grant set ────────────────────────────────────────
#
# Read, plus Team Review. Every entry is something 13.3 or 13.4 names, and
# nothing else: 13.5 lists eleven things they must not do, and the cheapest
# way to honour all eleven is to grant none of them.
_INTERVIEW_MANAGER_ACCESS: dict[str, bool] = {
    VIEW_REVIEW_SCREEN: True,
    VIEW_CANDIDATE_REPORTS: True,
    VIEW_CANDIDATE_RATINGS: True,
    ADD_TEAM_REVIEW_REMARK: True,
    VIEW_DASHBOARD: True,
}

# ── The 24 capabilities, distributed onto the grant layer ────────────────────
#
# Written to agree with RBAC_INVARIANTS cell for cell, so the two layers are
# consistent at rest and the ceiling only has work to do once a tenant row or
# a user overlay has been edited. `test_rbac_conformance` asserts the
# agreement rather than trusting this comment.
_SPEC_GRANTS_ORG_WIDE: dict[str, bool] = {
    VIEW_COMPANY_JOBS: True,
    SEND_JD_TO_HIRING_MANAGER: True,
    EDIT_MUST_HAVE_SKILLS: True,
    EDIT_NICE_TO_HAVE_SKILLS: True,
    EDIT_BEHAVIOURAL_COMPETENCIES: True,
    EDIT_JOB_PHILOSOPHY: True,
    EDIT_SWOT: True,
    EDIT_EVALUATION_RUBRICS: True,
    FINALIZE_ROLE_DEFINITION: True,
    REJECT_JD: True,
    ADD_TEAM_REVIEW_REMARK: True,
    VIEW_CANDIDATE_REPORTS: True,
    VIEW_CANDIDATE_RATINGS: True,
    ASSIGN_ROLES: True,
    INTEGRITY_DISPOSITION: True,
}

_SPEC_GRANTS_RECRUITER: dict[str, bool] = {
    # True for a scoped role too: the SCOPED cell in RBAC_INVARIANTS is
    # what narrows it to assigned jobs, and denying the grant outright
    # would leave a Recruiter unable to see the job they own.
    VIEW_COMPANY_JOBS: True,
    SEND_JD_TO_HIRING_MANAGER: True,
    EDIT_MUST_HAVE_SKILLS: False,
    EDIT_NICE_TO_HAVE_SKILLS: False,
    EDIT_BEHAVIOURAL_COMPETENCIES: False,
    EDIT_JOB_PHILOSOPHY: False,
    EDIT_SWOT: False,
    EDIT_EVALUATION_RUBRICS: False,
    FINALIZE_ROLE_DEFINITION: False,
    REJECT_JD: False,
    ADD_TEAM_REVIEW_REMARK: True,
    VIEW_CANDIDATE_REPORTS: True,
    VIEW_CANDIDATE_RATINGS: True,
    ASSIGN_ROLES: False,
    INTEGRITY_DISPOSITION: False,
}

_SPEC_GRANTS_HIRING_MANAGER: dict[str, bool] = {
    VIEW_COMPANY_JOBS: True,
    SEND_JD_TO_HIRING_MANAGER: False,
    EDIT_MUST_HAVE_SKILLS: True,
    EDIT_NICE_TO_HAVE_SKILLS: True,
    EDIT_BEHAVIOURAL_COMPETENCIES: True,
    EDIT_JOB_PHILOSOPHY: True,
    EDIT_SWOT: True,
    EDIT_EVALUATION_RUBRICS: True,
    FINALIZE_ROLE_DEFINITION: True,
    REJECT_JD: False,
    ADD_TEAM_REVIEW_REMARK: True,
    VIEW_CANDIDATE_REPORTS: True,
    VIEW_CANDIDATE_RATINGS: True,
    ASSIGN_ROLES: False,
    INTEGRITY_DISPOSITION: False,
}

_SPEC_GRANTS_INTERVIEW_MANAGER: dict[str, bool] = {
    VIEW_COMPANY_JOBS: True,
    SEND_JD_TO_HIRING_MANAGER: False,
    EDIT_MUST_HAVE_SKILLS: False,
    EDIT_NICE_TO_HAVE_SKILLS: False,
    EDIT_BEHAVIOURAL_COMPETENCIES: False,
    EDIT_JOB_PHILOSOPHY: False,
    EDIT_SWOT: False,
    EDIT_EVALUATION_RUBRICS: False,
    FINALIZE_ROLE_DEFINITION: False,
    REJECT_JD: False,
    ADD_TEAM_REVIEW_REMARK: True,
    VIEW_CANDIDATE_REPORTS: True,
    VIEW_CANDIDATE_RATINGS: True,
    ASSIGN_ROLES: False,
    INTEGRITY_DISPOSITION: False,
}

# Merged in place. The pre-existing entries are untouched: the flat
# operational model is a shipped product decision recorded in CLAUDE.md, and
# narrowing it lives in the ceiling above, where a reader can see WHICH rule
# did the narrowing and why.
DEFAULT_PERMISSION_MATRIX[Role.client].update(_SPEC_GRANTS_ORG_WIDE)
DEFAULT_PERMISSION_MATRIX[Role.hr_manager].update(_SPEC_GRANTS_ORG_WIDE)
DEFAULT_PERMISSION_MATRIX[Role.recruitment_manager].update(_SPEC_GRANTS_ORG_WIDE)
DEFAULT_PERMISSION_MATRIX[Role.recruiter].update(_SPEC_GRANTS_RECRUITER)
DEFAULT_PERMISSION_MATRIX[Role.hiring_manager].update(_SPEC_GRANTS_HIRING_MANAGER)
DEFAULT_PERMISSION_MATRIX[Role.interview_manager] = {
    **_INTERVIEW_MANAGER_ACCESS,
    **_SPEC_GRANTS_INTERVIEW_MANAGER,
}
