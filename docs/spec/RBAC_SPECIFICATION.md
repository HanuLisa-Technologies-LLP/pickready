# Ready Pick Now — Client RBAC, Authorization & Hiring Workflow Specification

**Document Status:** Canonical Specification
**Scope:** Internal client/company-side authorization and hiring workflow
**Platform:** Ready Pick Now
**Primary Audience:** Product, Backend, Frontend, AI/Agent, Security, DevOps and QA teams

> Provenance note (added when filed into the repository, 2026-08-29): this is the
> document spec-doc6 (RPN-SPEC-006) §0.1 item 4 names as **precedence rank 1** for
> authorization, tenant isolation, role ownership, job lifecycle and audit. It was
> supplied by the product owner in the implementing session and filed here verbatim.
> Nothing in it has been edited. Where spec-doc6 restates it, spec-doc6's restatement
> is a summary and this file is the authority.

## 1. Purpose

This specification defines the authoritative role-based access control (RBAC), tenant
isolation, resource ownership, permissions, job-definition workflow, candidate workflow,
interview-manager access, publication workflow, and audit requirements for the client
side of Ready Pick Now.

This document is intended to remove ambiguity from implementation.

Unless a future architectural decision explicitly supersedes this document, the rules in
this specification are authoritative.

## 2. Scope

This specification covers users belonging to a client/company organization.

It covers:

Client organizations/tenants

Internal client users

Super Admin

HR Manager

Recruiter

Hiring Manager

Interview Manager

Job/JD lifecycle

Hiring criteria

Candidate access and hiring-stage operations

Public job URLs

Candidate application entry point

Tenant isolation

Authorization enforcement

Auditability and activity dashboards

Candidate accounts are intentionally outside the internal client RBAC hierarchy defined
here.

This specification does not define the detailed candidate-side RBAC model, billing
permissions, subscription plans, or the complete candidate application UX.

## 3. Core Authorization Principles

Ready Pick Now MUST enforce authorization on the backend.

Frontend visibility is NOT a security boundary.

A user being unable to see a button does not constitute permission enforcement.

Every protected operation MUST be authorized using the user's:

Authenticated identity

Client organization/tenant

Role

Permission

Resource relationship/scope

Resource state, where applicable

The conceptual authorization model is:

```
Authenticated User
        |
        v
Client/Tenant
        |
        v
Role
        |
        v
Permission
        |
        v
Resource Scope / Ownership
        |
        v
Resource State
        |
        v
ALLOW / DENY
```

A valid role alone is not sufficient.

For example:

A Recruiter may be allowed to publish jobs.

That does NOT allow the Recruiter to publish a job belonging to another client.

A Hiring Manager may edit hiring criteria.

That does NOT allow the Hiring Manager to edit criteria for a job assigned to another
Hiring Manager.

An Interview Manager may view candidate reports.

That does NOT allow the Interview Manager to modify the JD or candidate hiring status
unless a future specification explicitly grants that permission.

## 4. Multi-Tenant Isolation

Ready Pick Now is a multi-tenant platform.

Each client/company is a separate tenant.

Example:

```
Ready Pick Now
|
+-- Client A
|   +-- Users
|   +-- Jobs
|   +-- Candidates
|   +-- Applications
|   +-- Reports
|   +-- Interview Reviews
|   +-- Hiring Data
|
+-- Client B
|   +-- Users
|   +-- Jobs
|   +-- Candidates
|   +-- Applications
|   +-- Reports
|   +-- Interview Reviews
|   +-- Hiring Data
```

A user belonging to Client A MUST NOT be able to access, infer, modify, delete, or
retrieve Client B resources.

This restriction MUST be enforced at the backend/data-access layer.

Tenant isolation MUST apply to, at minimum:

Users

Jobs

JDs

Hiring criteria

Candidates

Candidate profiles

Candidate documents

Applications

Candidate reports

Candidate ratings

Interview reviews

Team Review remarks

Hiring-stage data

AI-generated candidate intelligence

AI-generated hiring intelligence

Audit records

Any future client-owned resource

Client identifiers MUST be derived from the authenticated session/token/server-side
identity wherever possible rather than blindly trusting a client-supplied tenant ID.

## 5. Internal Client Roles

Ready Pick Now has four internal role categories for client organizations:

Client Super Admin

HR Manager

Recruiter

Hiring Manager

Interview Manager

The fifth role, Candidate, is outside this internal client RBAC specification.

A client organization MUST have exactly one active Super Admin.

A job MUST have exactly one Recruiter.

A job MUST have exactly one Hiring Manager.

A job MAY have multiple Interview Managers.

## 6. Role Hierarchy

The conceptual authority hierarchy is:

```
Client Super Admin
        |
        +-- HR Manager
        |
        +-- Recruiter
        |
        +-- Hiring Manager
        |
        +-- Interview Manager
```

This is an authority hierarchy, not necessarily an inheritance implementation.

The system MAY implement permissions as explicit permission sets rather than role
inheritance.

The important rule is:

Super Admin has ultimate authority over the client organization.

## 7. Client Super Admin

### 7.1 Cardinality

Each client organization MUST have exactly one active Super Admin.

There MUST NOT be two simultaneously active Super Admins for the same client
organization.

The system MUST provide a controlled mechanism for changing/transferring the Super Admin
role when necessary.

### 7.2 Authority

The Super Admin is the ultimate authority within the client organization.

The Super Admin can perform any action available to any other client-side role.

The Super Admin can override normal operational role restrictions.

The Super Admin has access to all company-owned data and functionality subject to
platform-level security and legal constraints.

### 7.3 Staff Management

The Super Admin can:

Add staff

Remove staff

Activate/deactivate staff

Assign roles

Change staff roles

Manage staff access

View staff

Manage job assignments where the relevant functionality exists

Staff roles include:

HR Manager

Recruiter

Hiring Manager

Interview Manager

The Super Admin role itself MUST remain singular per company.

### 7.4 Data Access

The Super Admin can access:

All jobs

All JDs

All hiring criteria

All candidates belonging to the company

All applications

All candidate reports

All ratings

All hiring stages

All interview reviews

All team-review remarks

All company hiring activity

Audit/activity information

### 7.5 Override Authority

If an action is normally restricted to another role, the Super Admin MAY perform that
action.

Examples:

Super Admin may edit a JD.

Super Admin may edit hiring criteria.

Super Admin may publish a job.

Super Admin may manage candidates.

Super Admin may change candidate status.

Super Admin may move candidates through hiring stages.

Super Admin may access candidate reports.

Super Admin may perform administrative staff operations.

The system MUST still record the Super Admin's action in the audit trail.

## 8. HR Manager

### 8.1 Purpose

The HR Manager is the primary broad operational hiring authority.

### 8.2 Data Access

The HR Manager has organization-wide access to hiring data belonging to the client.

The HR Manager can access:

All company candidates

All company jobs

All applications

Candidate profiles

Candidate reports

Hiring-stage information

Hiring process information

Relevant hiring intelligence

The HR Manager MUST NOT access another client's data.

### 8.3 CRUD Authority

The HR Manager has full CRUD authority over candidate and hiring-process data within the
client's tenant, subject to system-level immutable/audit restrictions.

CRUD means:

Create

Read

Update

Delete

The HR Manager can manage the hiring process across the organization.

This includes candidate and hiring records unless a separate future policy explicitly
makes a particular record immutable.

### 8.4 Relationship to Job Definition

The HR Manager is not the designated Hiring Manager for a job merely by virtue of being
an HR Manager.

The job still has one explicitly assigned Hiring Manager.

The HR Manager's organization-wide operational authority is separate from the job's
role-definition ownership.

## 9. Recruiter

### 9.1 Purpose

The Recruiter owns the operational flow of:

```
Initial JD Generation
        |
        v
Send to Hiring Manager
        |
        v
Receive Finalized JD
        |
        v
Publish Job
        |
        v
Candidate Hiring Operations
```

### 9.2 Job Assignment

Each job MUST have exactly one Recruiter.

A Recruiter is associated with a job through an explicit job assignment.

A Recruiter does not automatically have access to every job in the company merely because
they hold the Recruiter role.

Access MUST be scoped according to the applicable job assignment and organization-level
permissions.

### 9.3 JD Creation

The Recruiter can:

Create an initial JD

Generate the initial JD

Edit the portions of the JD that are within Recruiter-authorized drafting scope

Send the JD to the assigned Hiring Manager

The initial JD is a draft and is NOT considered final.

### 9.4 Hiring Criteria Restrictions

The Recruiter MUST NOT be able to authoritatively modify the Hiring Manager-controlled
hiring criteria.

The Recruiter cannot edit:

Must-Have skills

Nice-to-Have skills

Behavioural competency skills

Job-role philosophy

Job-role SWOT analysis

Hiring/evaluation rubrics

The Recruiter MAY view these fields once they are available to the Recruiter.

The Recruiter MUST NOT be able to alter finalized values through normal Recruiter
permissions.

UI controls, API endpoints, and database mutation paths MUST enforce this restriction.

### 9.5 Receiving the Finalized JD

After the Hiring Manager completes and finalizes the JD and associated hiring criteria,
the finalized version is sent back to the Recruiter.

The Recruiter can view the finalized content.

The Recruiter does not have authority to change the Hiring Manager-controlled finalized
criteria.

### 9.6 Publishing

The Recruiter is the designated operational publisher.

Normal workflow:

```
Hiring Manager finalizes
        |
        v
Finalized job returned to Recruiter
        |
        v
Recruiter publishes
```

The Recruiter MUST NOT publish an unfinished job.

Before publication, the system MUST ensure all required Hiring Manager-controlled
components have been finalized.

These include, where applicable:

Final JD

Must-Have skills

Nice-to-Have skills

Behavioural competencies

Job-role philosophy

SWOT analysis

Hiring/evaluation rubrics

The normal operational rule is:

Recruiter publishes the job. Period.

The Super Admin is an administrative exception because the Super Admin has ultimate
authority and can override role restrictions.

### 9.7 Candidate Operations

For jobs assigned to the Recruiter, the Recruiter can:

View candidates

View candidate application information

Shortlist candidates

Reject candidates

Move candidates through hiring stages

Candidate-stage actions MUST still be recorded in the audit trail.

## 10. Hiring Manager

### 10.1 Purpose

The Hiring Manager is the authoritative owner of the role definition and hiring criteria
for a specific job.

### 10.2 Job Assignment

Each job MUST have exactly one Hiring Manager.

The Hiring Manager assigned to a job is responsible for finalizing that job's definition.

A Hiring Manager does not automatically become the Hiring Manager for all jobs in the
company.

### 10.3 JD Review

The Hiring Manager receives the initial JD generated by the Recruiter.

The Hiring Manager can:

Review the JD

Edit the JD

Refine the JD

Finalize the JD

### 10.4 Hiring Criteria Authority

The Hiring Manager has authoritative control over:

Must-Have Skills

The Hiring Manager can create, edit, remove, and finalize Must-Have skills.

Nice-to-Have Skills

The Hiring Manager can create, edit, remove, and finalize Nice-to-Have skills.

Behavioural Competencies

The Hiring Manager can create, edit, remove, and finalize behavioural competency
requirements.

Job-Role Philosophy

The Hiring Manager defines the philosophy/intent of the role and the qualities the
organization is seeking.

SWOT Analysis

The Hiring Manager completes the job-role SWOT analysis.

Evaluation Rubrics

The Hiring Manager defines the evaluation/hiring rubrics used to assess candidates
against the role.

These Hiring Manager-controlled fields constitute the authoritative hiring definition for
the job.

## 11. Hiring Manager Cannot Reject the JD

The Hiring Manager does NOT have a "Reject JD" workflow.

The Hiring Manager MUST NOT reject the JD as a terminal action.

Instead, the Hiring Manager edits/refines the JD until it is suitable.

The workflow is:

```
Recruiter creates JD
        |
        v
Hiring Manager reviews
        |
        v
Hiring Manager edits/refines
        |
        v
Hiring Manager completes criteria
        |
        v
Hiring Manager finalizes
        |
        v
Recruiter receives finalized version
```

If the initial JD is poor or incomplete, the Hiring Manager edits it rather than
rejecting it.

## 12. Hiring Manager Finalization

The Hiring Manager's finalization is the authoritative transition from role-definition
work to publication readiness.

Once finalized:

The JD is considered the approved role definition.

Hiring criteria are considered authoritative.

Recruiter receives the finalized job definition.

Recruiter may publish the job.

Recruiter cannot modify Hiring Manager-controlled criteria.

If a change is required after finalization, the system MUST provide an explicit
controlled re-edit/revision mechanism rather than allowing silent mutation.

The exact post-finalization revision workflow may be specified separately, but it MUST
preserve authorship and auditability.

## 13. Interview Manager

### 13.1 Cardinality

Each job MAY have multiple Interview Managers.

There is no requirement that a job have exactly one Interview Manager.

### 13.2 Purpose

Interview Managers are participants in candidate evaluation and team review.

They are not owners of the JD.

They are not owners of hiring criteria.

They are not the designated publishers.

### 13.3 Access

Interview Managers can view candidate information and candidate evaluation information
made available to them, including:

Candidate reports

Candidate ratings

Candidate intelligence

Relevant candidate information

Team Review information

### 13.4 Team Review

Interview Managers can participate in Team Review.

They can:

Add remarks

Add comments/observations

Contribute candidate-specific review information

Team Review contributions MUST identify the author and timestamp.

### 13.5 Restrictions

Interview Managers MUST NOT, by default:

Edit the JD

Define Must-Have skills

Define Nice-to-Have skills

Define behavioural competency requirements

Edit job-role philosophy

Edit SWOT analysis

Edit hiring rubrics

Publish jobs

Modify another user's review

Modify candidate hiring-stage status

Shortlist/reject candidates

These restrictions apply unless a future explicit permission specification changes them.

## 14. Candidate Role

Candidate accounts are outside the client-side RBAC hierarchy.

A candidate is not:

Super Admin

HR Manager

Recruiter

Hiring Manager

Interview Manager

Candidate permissions are defined separately.

A candidate MUST NOT gain access to internal client functionality merely by
authenticating.

## 15. Public Job URLs

Each published job receives a unique public identifier.

Example:

```
https://readypick.ai/jobs/3252463dfbg43t4hfb
```

The public job URL MUST be accessible without authentication.

The public page can expose the job information intended for public candidates.

Authentication MUST NOT be required merely to view the public job posting.

## 16. Candidate Application Flow

The standard flow is:

```
Public Job URL
      |
      v
Public Job Page
      |
      v
Candidate clicks APPLY
      |
      v
Candidate Login / Registration
      |
      v
Candidate authenticated
      |
      v
Application Flow
      |
      v
Application submitted
```

The public job page does not require candidate authentication.

The Apply action requires candidate authentication/registration.

The system MUST associate the submitted application with:

The candidate account

The target job

The target client tenant

The application record

## 17. Job Lifecycle

The canonical job lifecycle is:

```
DRAFT
  |
  | Recruiter creates/generates JD
  v
SENT_TO_HIRING_MANAGER
  |
  | Hiring Manager reviews and edits
  v
IN_REVIEW
  |
  | Hiring Manager completes role definition
  v
FINALIZED
  |
  | Recruiter publishes
  v
PUBLISHED
  |
  v
CANDIDATE_APPLICATIONS
  |
  v
HIRING_PROCESS
  |
  v
CLOSED / ARCHIVED
```

The implementation MAY use different internal status names, but the semantic states MUST
preserve these distinctions.

## 18. Job Draft State

In Draft:

Recruiter can create/generate the initial JD.

The JD is not public.

Candidates cannot apply.

Recruiter can send the JD to the Hiring Manager.

The job MUST NOT be published as an unfinished draft.

## 19. Hiring Manager Review State

During Hiring Manager review:

The Hiring Manager can modify:

JD

Must-Have skills

Nice-to-Have skills

Behavioural competencies

Job-role philosophy

SWOT analysis

Evaluation rubrics

The Recruiter can see the applicable job information but cannot modify Hiring
Manager-controlled criteria.

## 20. Finalized State

A job becomes Finalized when the assigned Hiring Manager explicitly completes the role
definition.

Finalization MUST be an explicit state transition.

Finalization MUST record:

User who finalized it

Timestamp

Relevant version

Relevant hiring criteria version

The finalized definition becomes the authoritative basis for publication.

## 21. Publication State

The Recruiter publishes the finalized job.

Publication MUST NOT be possible if required Hiring Manager-controlled components are
incomplete.

When published:

The job becomes publicly accessible.

A public job identifier is available.

The public URL becomes usable.

Candidates can reach the application flow.

Publication MUST record:

Publishing user

Timestamp

Published version

Job identifier

## 22. Hiring Criteria Immutability After Publication

Once a job is published, the system MUST NOT silently alter the criteria that were used
to define the published job.

Any post-publication modification MUST use an explicit revision/versioning mechanism.

The system SHOULD preserve the historical version used when each candidate applied.

This is important because candidate evaluation and AI intelligence may depend on the
exact hiring criteria active at the time of application.

## 23. Resource Ownership Model

RBAC and ownership are separate concepts.

Example:

```
Client A
|
+-- Job 101
|   +-- Recruiter A
|   +-- Hiring Manager A
|   +-- Interview Manager A
|
+-- Job 102
    +-- Recruiter B
    +-- Hiring Manager B
    +-- Interview Manager B
```

Recruiter A can perform Recruiter-authorized actions for Job 101.

Recruiter A does not automatically receive equivalent operational ownership of Job 102.

Similarly:

Hiring Manager A controls the role definition for Job 101.

Hiring Manager A does not automatically control the role definition for Job 102.

Organization-wide roles such as Super Admin and HR Manager have broader scope as
explicitly defined.

## 24. Permission Matrix

The following matrix defines the baseline permissions.

| Capability | Super Admin | HR Manager | Recruiter | Hiring Manager | Interview Manager |
|---|---|---|---|---|---|
| Manage staff | YES | NO* | NO | NO | NO |
| Assign roles | YES | NO* | NO | NO | NO |
| View all company jobs | YES | YES | Scoped | Scoped | Scoped |
| Create initial JD | YES | YES | YES | YES** | NO |
| Generate initial JD | YES | YES | YES | YES** | NO |
| Edit JD | YES | YES | YES*** | YES | NO |
| Send JD to Hiring Manager | YES | YES | YES | NO | NO |
| Edit Must-Have skills | YES | YES | NO | YES | NO |
| Edit Nice-to-Have skills | YES | YES | NO | YES | NO |
| Edit behavioural competencies | YES | YES | NO | YES | NO |
| Edit job philosophy | YES | YES | NO | YES | NO |
| Edit SWOT | YES | YES | NO | YES | NO |
| Edit evaluation rubrics | YES | YES | NO | YES | NO |
| Finalize role definition | YES | YES | NO | YES | NO |
| Reject JD | YES | YES | NO | NO | NO |
| Publish job | YES | YES* | YES | NO | NO |
| View candidates | YES | YES | YES (scoped) | YES (scoped) | YES (scoped) |
| Shortlist candidates | YES | YES | YES | NO* | NO |
| Reject candidates | YES | YES | YES | NO* | NO |
| Move candidates through stages | YES | YES | YES | NO* | NO |
| View candidate reports | YES | YES | YES (scoped) | YES (scoped) | YES (scoped) |
| View candidate ratings | YES | YES | YES (scoped) | YES (scoped) | YES (scoped) |
| Add Team Review remarks | YES | YES | YES* | YES* | YES |

\* These entries are intentionally conservative and may require an explicit future
product decision. Super Admin always retains ultimate authority.

\*\* If the Hiring Manager creates content directly, it must still follow the same
role-definition authority and workflow. The canonical initial flow remains Recruiter
generates the initial JD.

\*\*\* Recruiter may edit the initial/draft JD within permitted drafting scope, but MUST
NOT edit Hiring Manager-controlled criteria or finalized role-definition fields.

The final implementation MUST convert this conceptual matrix into explicit backend
permissions.

## 25. Important Permission Distinctions

### 25.1 JD vs Hiring Criteria

"JD" and "Hiring Criteria" are not one undifferentiated permission.

The system MUST distinguish between:

Recruiter-controlled drafting

The Recruiter creates/generates the initial JD.

Hiring Manager-controlled role definition

The Hiring Manager controls:

Must-Have

Nice-to-Have

Behavioural competencies

Job philosophy

SWOT

Evaluation rubrics

Final role definition

This distinction is fundamental to Ready Pick Now.

## 26. Recruiter Cannot Override Hiring Manager

After Hiring Manager finalization, the Recruiter MUST NOT be able to:

Change Must-Have skills

Change Nice-to-Have skills

Change behavioural competencies

Change job philosophy

Change SWOT

Change evaluation rubrics

Alter the finalized role definition without an authorized revision workflow

The Recruiter can publish the finalized job.

## 27. Hiring Manager Owns Role Definition

The Hiring Manager is the authoritative functional owner of:

```
What is this job?
What skills are mandatory?
What skills are desirable?
What behaviours matter?
What does success in this role mean?
What is the role philosophy?
What are the strengths/weaknesses/opportunities/threats?
How should candidates be evaluated?
```

The Recruiter is responsible for operationalizing that definition into publication.

## 28. Candidate Hiring Operations

The Recruiter is authorized to operate the candidate pipeline for their assigned jobs.

This includes:

```
Candidate
   |
   +-- View
   |
   +-- Shortlist
   |
   +-- Reject
   |
   +-- Move to next hiring stage
```

The HR Manager and Super Admin have broader organization-level authority.

Interview Managers are observers/contributors rather than pipeline controllers under the
baseline model.

## 29. Interview Manager Review Model

Interview Managers are evaluation participants.

For a job:

```
Job
 |
 +-- Hiring Manager
 |
 +-- Recruiter
 |
 +-- Interview Manager 1
 +-- Interview Manager 2
 +-- Interview Manager 3
```

Interview Managers can see the candidate information and intelligence required for their
participation.

They can add individual remarks to Team Review.

Every remark MUST preserve:

Author

Timestamp

Candidate

Job/application context

Interview Managers MUST NOT be able to silently alter another interviewer's remarks.

## 30. Audit Trail

Ready Pick Now MUST maintain an audit trail for meaningful authorization-sensitive
actions.

At minimum, the audit system SHOULD record:

Actor

Actor role at time of action

Tenant/client

Action

Resource type

Resource ID

Previous value/state where appropriate

New value/state where appropriate

Timestamp

Relevant job/application/candidate context

Source/request metadata where appropriate

Examples:

```
Recruiter X
created JD
Job 101
2026-08-28 10:30

Hiring Manager Y
edited Must-Have skills
Job 101
2026-08-28 11:15

Hiring Manager Y
finalized job
Job 101
2026-08-28 11:42

Recruiter X
published job
Job 101
2026-08-28 12:01

Recruiter X
shortlisted candidate
Candidate 884
Job 101
2026-08-29 09:15
```

## 31. Super Admin Activity Dashboard

The Super Admin portal MUST provide simple visibility into important company activity.

The Super Admin should be able to answer:

Who changed this?

What did they change?

When did they change it?

Which job was affected?

Which candidate was affected?

What was the previous state?

What is the current state?

The dashboard is an operational visibility layer over the underlying audit trail.

The audit trail MUST NOT depend exclusively on dashboard rendering.

## 32. API Authorization Requirements

Every protected API endpoint MUST enforce authorization.

Example:

```
POST /jobs
POST /jobs/{id}/send-to-hiring-manager
PATCH /jobs/{id}
POST /jobs/{id}/finalize
POST /jobs/{id}/publish

GET /jobs/{id}/candidates
PATCH /candidates/{id}
POST /candidates/{id}/shortlist
POST /candidates/{id}/reject
POST /candidates/{id}/move-stage

POST /jobs/{id}/team-review
```

The API MUST verify:

Authentication

Tenant membership

Role

Permission

Job/resource relationship

Current resource state

The API MUST reject unauthorized operations even if the request is manually constructed
outside the frontend.

## 33. Direct Object Reference Protection

Ready Pick Now MUST protect against ID-based authorization bypass.

Knowing:

```
/job/3252463dfbg43t4hfb
```

MUST NOT be sufficient to gain access to the job.

Similarly, knowing a candidate ID MUST NOT grant access to the candidate.

The backend MUST verify the user's tenant and authorization relationship to the resource.

This applies even if public identifiers are difficult to guess.

Obscurity is NOT authorization.

## 34. AI and Agent Authorization

Any Ready Pick Now AI agent that performs actions on behalf of a client user MUST operate
under the same authorization model.

An AI agent MUST NOT receive unrestricted access merely because it is an internal
service.

For example:

A Recruiter-authorized AI agent may assist with JD generation.

It MUST NOT use that authority to modify Hiring Manager-controlled criteria.

A Hiring Manager-authorized AI agent may assist with:

Role philosophy

SWOT

Skills

Behavioural competencies

Evaluation rubrics

But it MUST remain constrained to the Hiring Manager's tenant and assigned job scope.

AI agents MUST NOT bypass:

Tenant isolation

RBAC

Resource ownership

Workflow state

Audit requirements

Every AI-initiated mutation MUST be attributable to both:

The human principal/user on whose behalf the action was authorized

The AI agent/service that executed the action

## 35. Versioning

The platform SHOULD version important hiring artifacts.

At minimum, versioning should apply to:

JD

Must-Have skills

Nice-to-Have skills

Behavioural competencies

Job philosophy

SWOT

Evaluation rubrics

Published job definition

A candidate's evaluation context SHOULD reference the relevant version of the job
criteria used at the time of evaluation/application.

This prevents historical candidate assessments from becoming ambiguous after future job
revisions.

## 36. Explicit Non-Goals

The following are NOT implied by this specification:

HR Manager is automatically the Hiring Manager for every job.

Recruiter can edit Hiring Manager criteria.

Interview Manager can reject candidates.

Interview Manager can publish jobs.

Hiring Manager can reject a JD.

Multiple Recruiters can own one job.

Multiple Hiring Managers can own one job.

Candidate is part of the internal client RBAC hierarchy.

Public job URLs require authentication.

A public job ID grants access to internal job data.

Frontend hiding is sufficient authorization.

AI agents can bypass human role permissions.

## 37. Canonical End-to-End Example

Consider:

```
Company: Acme Technologies

Super Admin:
    CEO

HR Manager:
    HR Head

Recruiter:
    Recruiter A

Hiring Manager:
    Engineering Manager A

Interview Managers:
    Senior Engineer A
    Senior Engineer B
```

The workflow is:

Step 1, Recruiter creates job

Recruiter A generates:

Senior Backend Engineer

The JD is initially a draft.

Step 2, Recruiter sends JD

Recruiter A sends the JD to Engineering Manager A.

Step 3, Hiring Manager refines role

Engineering Manager A:

Edits JD

Defines Must-Have skills

Defines Nice-to-Have skills

Defines behavioural competencies

Defines role philosophy

Completes SWOT

Defines evaluation rubrics

Step 4, Hiring Manager finalizes

Engineering Manager A finalizes the job.

The system records:

```
Finalized by: Engineering Manager A
Timestamp: ...
Version: ...
```

Step 5, Recruiter receives finalized job

Recruiter A can view the final definition.

Recruiter A cannot modify Hiring Manager-controlled criteria.

Step 6, Recruiter publishes

Recruiter A publishes the job.

Ready Pick Now generates/uses:

```
readypick.ai/jobs/{public_job_id}
```

Step 7, Candidate discovers job

A candidate visits the public URL.

No authentication is required to view the job.

Step 8, Candidate applies

Candidate clicks Apply.

Candidate logs in/registers.

Candidate submits the application.

Step 9, Recruiter manages candidate

Recruiter A can:

View candidate

Shortlist candidate

Reject candidate

Move candidate through hiring stages

Step 10, Interview Managers participate

Senior Engineer A and Senior Engineer B can:

View permitted candidate reports

View ratings

Review candidate intelligence

Add Team Review remarks

They cannot modify the JD or Hiring Manager criteria.

Step 11, Super Admin oversight

The Super Admin can view company activity and see who performed important actions.

## 38. Final Authority Model

The entire system can be summarized as:

```
SUPER ADMIN
    |
    |-- Ultimate company authority
    |-- Staff management
    |-- Full company access
    |-- Can perform any role's action
    |
    +------------------------------------------------
                                                     |
HR MANAGER                                           |
    |                                                |
    |-- Organization-wide hiring operations          |
    |-- Candidate CRUD                               |
    |-- Hiring-process authority                     |
    |                                                |
    +------------------------------------------------
                                                     |
JOB-SPECIFIC WORKFLOW                                |
                                                     |
RECRUITER ------------------> HIRING MANAGER         |
    |                              |                 |
    | Generate JD                  | Review JD       |
    |                              | Edit JD         |
    | Send JD -------------------> | Skills          |
    |                              | Behaviours      |
    |                              | Philosophy      |
    |                              | SWOT            |
    |                              | Rubrics         |
    |                              | Finalize        |
    | <----------------------------|                 |
    |                                                |
    | Publish                                        |
    |                                                |
    v                                                |
PUBLIC JOB                                           |
    |                                                |
    v                                                |
CANDIDATE                                            |
    |                                                |
    v                                                |
APPLICATION                                          |
    |                                                |
    v                                                |
HIRING PIPELINE                                      |
    |                                                |
    +---- Recruiter: operate pipeline                |
    |                                                |
    +---- Hiring Manager: role-definition authority  |
    |                                                |
    +---- Interview Managers: review + remarks       |
                                                     |
    +---- Super Admin / HR: broad authority ---------+
```

## 39. Non-Negotiable Rules

The following rules MUST be treated as architectural invariants:

Exactly one active Super Admin per client company.

Strict tenant isolation is mandatory.

Super Admin has ultimate authority within the company.

HR Manager has broad organization-wide hiring/candidate CRUD authority.

Each job has exactly one Recruiter.

Each job has exactly one Hiring Manager.

A job may have multiple Interview Managers.

Recruiter creates/generates the initial JD.

Recruiter sends the JD to the Hiring Manager.

Hiring Manager reviews and edits the JD.

Hiring Manager controls Must-Have skills.

Hiring Manager controls Nice-to-Have skills.

Hiring Manager controls behavioural competencies.

Hiring Manager controls job-role philosophy.

Hiring Manager controls SWOT analysis.

Hiring Manager controls evaluation rubrics.

Recruiter cannot modify Hiring Manager-controlled criteria.

Hiring Manager does not reject the JD.

Hiring Manager edits/refines until the role definition is finalized.

Hiring Manager finalizes everything before publication.

Recruiter publishes the finalized job.

Recruiter can view candidates for their assigned jobs.

Recruiter can shortlist candidates.

Recruiter can reject candidates.

Recruiter can move candidates through hiring stages.

Interview Managers can view permitted candidate reports and ratings.

Interview Managers can add Team Review remarks.

Interview Managers do not control JD or hiring criteria.

Public job URLs are accessible without authentication.

Candidate authentication is required to apply.

Candidate accounts are outside the internal client RBAC model.

A user from one client can never access another client's data.

Authorization must be enforced server-side.

Public IDs are not authorization mechanisms.

Important mutations must be auditable.

Super Admin dashboards must expose who changed what.

AI agents must obey the same tenant/RBAC/workflow boundaries.

Published hiring definitions must be versioned or otherwise historically preserved.

Post-finalization/post-publication changes must use controlled revision semantics.

No role may bypass tenant isolation through direct API calls, database access paths
exposed through the application, or AI-agent execution.

## 40. Canonical Product Philosophy

Ready Pick Now deliberately separates role-definition authority from hiring-process
execution.

The principle is:

```
Recruiter
    =
Build the initial JD
+
Coordinate the JD
+
Publish
+
Operate the candidate pipeline

Hiring Manager
    =
Define what the company actually wants
+
Own the hiring criteria
+
Finalize the role

Interview Managers
    =
Evaluate and contribute observations

HR Manager
    =
Organization-wide hiring operations

Super Admin
    =
Ultimate organization authority
```

This separation is intentional.

The Recruiter operationalizes the role.

The Hiring Manager defines the role.

The Interview Managers contribute evaluation.

The HR Manager governs the broader hiring operation.

The Super Admin governs the organization.

That separation MUST remain visible in the product's UX, API authorization, database
relationships, audit model, and AI-agent permissions.
