# RBAC as implemented

**Status:** the implemented model, mapped cell by cell to
`docs/spec/RBAC_SPECIFICATION.md` (precedence rank 1 for authorization, tenant
isolation, role ownership, job lifecycle and audit).
**Written:** 2026-08-29, spec-doc6 Phase 9.
**Enforced by:** `backend/tests/test_rbac_conformance.py`,
`test_agent_authorization.py`, `test_audit_invariants.py`,
`test_stage_enum_separation.py`, `test_rbac_cardinality.py` (the last needs a
live Postgres, because a constraint you have not seen refuse an INSERT is a
constraint you have not tested).

Read this file with the specification open. Where they disagree, the
specification wins and this file is wrong.

---

## 1. Role mapping

The specification names five internal client roles. This codebase predates it
and uses different identifiers. Nothing was renamed: a role identifier is
stored in `users.role`, quoted in `role_permissions`, and carried in every JWT
already issued, so a rename would cost every signed-in user their session and
buy nothing anybody sees. This table is the translation.

| RBAC spec role | Repo identifier | Defined at | Notes |
|---|---|---|---|
| Client Super Admin | `Role.client` | `backend/app/models/enums.py:9` | **Not** `Role.super_admin`. See below. |
| HR Manager | `Role.hr_manager` | `backend/app/models/enums.py:14` | |
| Recruiter | `Role.recruiter` | `backend/app/models/enums.py:15` | |
| Hiring Manager | `Role.hiring_manager` | `backend/app/models/enums.py:16` | |
| Interview Manager | `Role.interview_manager` | `backend/app/models/enums.py:17` | **Added 2026-08-29.** Did not exist. |
| (not in the spec) | `Role.recruitment_manager` | `backend/app/models/enums.py:13` | This product's own tier, from the 2026-08-14 hierarchy release. Ranks beside HR Manager. |
| (not a client role) | `Role.super_admin` | `backend/app/models/enums.py:6` | ReadyPick's **platform** owner. `tenant_id` IS NULL. |
| (not a client role) | `Role.bd` | `backend/app/models/enums.py:22` | ReadyPick's own sales staff. `tenant_id` IS NULL. |
| Candidate | `Role.candidate` | `backend/app/models/enums.py:18` | Outside this hierarchy (RBAC 14). |

### `Role.super_admin` is NOT the specification's Super Admin

This is the highest-stakes mapping in the phase and it is settled explicitly.

RBAC 5 titles the role **"Client Super Admin"**. RBAC 7.1 says "Each client
organization MUST have exactly one active Super Admin", which only means
something for a tenant-scoped role. RBAC 7.4 lists their data access as "All
jobs / All candidates belonging to the company". RBAC 39 says "A user from one
client can never access another client's data", with no exemption. RBAC 4's
isolation list ends with "Any future client-owned resource" and no carve-out
for any role.

`Role.super_admin` in this codebase is ReadyPick's platform owner: `tenant_id`
is NULL, the token carries the OWNER audience, and every request goes through
`get_superadmin_db`, which opens the RLS-bypass scope and writes an audit row.
Mapping the specification's role onto it would have given every customer's
Super Admin cross-tenant reach, which is a privilege escalation that reads as
correct in a diff.

**So `Role.client` is the Client Super Admin, and it is tenant-scoped like
every other client role.** `rbac.decide` returns `NOT_FOUND` for a
cross-tenant resource for `Role.client` exactly as for a Recruiter, and
`test_super_admin_of_another_tenant_reaches_nothing` asserts it.

The platform owner is a separate, audited surface and is out of scope for this
document, which is what RBAC 2 means by "users belonging to a client/company
organization".

### Five roles, not four

RBAC 5 says "four internal role categories" and then lists five. spec-doc6 C4
records this as an editorial defect in the source document. Five is correct.
`capabilities.CLIENT_ROLES` holds five and
`test_five_roles_not_four` asserts it.

---

## 2. Two layers: the grant, and the ceiling

Authorization resolves through two tables, and the split matters.

**`DEFAULT_PERMISSION_MATRIX`** (`services/capabilities.py`) is the
tenant-configurable grant layer. Resolution is unchanged: user overlay
(`users.permissions_json`) -> tenant row (`role_permissions`) -> global
template -> deny.

**`RBAC_INVARIANTS`** (`services/capabilities.py`) is RBAC 24 transcribed cell
by cell, and it is a **ceiling**. `rbac.decide` computes

```
effective = grant_engine_says_yes AND invariant_allows
```

so it can only ever narrow. RBAC 39 calls its rules "architectural
invariants", and RBAC 26 says the Recruiter must not be able to alter
finalized criteria "through normal Recruiter permissions" -- a rule a tenant
row could switch off is not that. This is the same two-layer shape
`services/hiring/layers.py` already uses: BOUNDS may tune, INVARIANTS may not
be suspended.

`rbac.apply_invariant_ceiling` also trims the capability list `/auth/me`
returns, so the product does not render a control the API will refuse.

### The asterisks are encoded, not flattened

RBAC 24 carries three footnotes and each means something different at runtime.

| Cell | `Invariant` member | Runtime meaning |
|---|---|---|
| YES | `ALLOW` | Unconditional. |
| Scoped | `SCOPED` | Only for a job this user is assigned to (9.2, 10.2, 13.1, 23). |
| YES\* | `ALLOW_AUDITED_EXCEPTION` | Allowed, recorded as a deviation (7.5, spec-doc6 C13). |
| YES\*\* | `ALLOW_NON_CANONICAL` | Allowed, off the canonical Recruiter-generates flow. |
| YES\*\*\* | `ALLOW_DRAFT_SCOPE` | Allowed in `DRAFT`/`SENT_TO_HIRING_MANAGER`/`IN_REVIEW`, refused from `FINALIZED` (26). |
| NO | `DENY` | Refused. |
| NO\* | `DENY_CONSERVATIVE` | Refused, and the asterisk is preserved so a future product decision can find every cell that was conservative by choice. |
| (not in 24) | `NEVER` | Refused, and unreachable by any grant, tenant row or user overlay. |

`NEVER` is used for two things RBAC states as non-goals (36): a Recruiter
touching Hiring-Manager-controlled criteria, and a Hiring Manager rejecting a
JD.

---

## 3. The permission matrix, cell by cell

Column order matches RBAC 24. `SA` = Client Super Admin (`Role.client`).

| RBAC 24 capability | Repo capability | SA | HR | Rec | HM | IM |
|---|---|---|---|---|---|---|
| Manage staff | `manage_staff` | ALLOW | DENY\* | DENY | DENY | DENY |
| Assign roles | `assign_roles` | ALLOW | DENY\* | DENY | DENY | DENY |
| View all company jobs | `view_company_jobs` | ALLOW | ALLOW | SCOPED | SCOPED | SCOPED |
| Create initial JD | `create_job` | ALLOW | ALLOW | ALLOW | ALLOW\*\* | DENY |
| Generate initial JD | `create_job` | ALLOW | ALLOW | ALLOW | ALLOW\*\* | DENY |
| Edit JD | `edit_job_description` | ALLOW | ALLOW | ALLOW\*\*\* | SCOPED | DENY |
| Send JD to Hiring Manager | `send_jd_to_hiring_manager` | ALLOW | ALLOW | SCOPED | DENY | DENY |
| Edit Must-Have skills | `edit_must_have_skills` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Edit Nice-to-Have skills | `edit_nice_to_have_skills` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Edit behavioural competencies | `edit_behavioural_competencies` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Edit job philosophy | `edit_job_philosophy` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Edit SWOT | `edit_swot` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Edit evaluation rubrics | `edit_evaluation_rubrics` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Finalize role definition | `finalize_role_definition` | ALLOW | ALLOW | **NEVER** | SCOPED | DENY |
| Reject JD | `reject_jd` | ALLOW | ALLOW | DENY | **NEVER** | DENY |
| Publish job | `publish_job` | ALLOW\* | **DENY\*** | SCOPED | DENY | DENY |
| View candidates | `view_review_screen` | ALLOW | ALLOW | SCOPED | SCOPED | SCOPED |
| Shortlist candidates | `decide_profile` | ALLOW | ALLOW | SCOPED | DENY\* | DENY |
| Reject candidates | `decide_profile` | ALLOW | ALLOW | SCOPED | DENY\* | DENY |
| Move candidates through stages | `update_pipeline_status` | ALLOW | ALLOW | SCOPED | DENY\* | DENY |
| View candidate reports | `view_candidate_reports` | ALLOW | ALLOW | SCOPED | SCOPED | SCOPED |
| View candidate ratings | `view_candidate_ratings` | ALLOW | ALLOW | SCOPED | SCOPED | SCOPED |
| Add Team Review remarks | `add_team_review_remark` | ALLOW | ALLOW | ALLOW\* | ALLOW\* | SCOPED |
| (spec-doc6 C7) | `integrity_disposition` | ALLOW\* | ALLOW | DENY | DENY | DENY |

Where two RBAC rows map onto one repo capability ("Create"/"Generate initial
JD", "Shortlist"/"Reject candidates"), the two rows carry identical values in
RBAC 24, so nothing is lost by collapsing them.

### The one cell that departs from RBAC 24: HR Manager publish

RBAC 24 marks the HR Manager's Publish job cell YES\*. It is **withheld**.

RBAC 9.6 states the rule and names exactly one exception: "Recruiter publishes
the job. Period. The Super Admin is an administrative exception because the
Super Admin has ultimate authority and can override role restrictions." The HR
Manager is not in that sentence. RBAC 24's own footnote says the asterisked
entries "are intentionally conservative and may require an explicit future
product decision", so the cell is not an affirmative grant, it is a placeholder
for a decision nobody has taken.

spec-doc6 C13 restates this as "HR Manager and Super Admin publish only as an
audited exception". That restatement is wider than 9.6, which is the text it
cites, so it does not carry.

**This is overrulable and awaits an owner decision.** It is recorded here, in
`RBAC_INVARIANTS`, and in `KNOWN_GRANTS_ABOVE_THE_CEILING` rather than settled
quietly. The distinction that governed it is worth keeping: spec-doc6 20's
"restrict more when unsure" applies where the higher authority is SILENT. It
never licenses overriding an affirmative grant, which is why `reject_jd` is
NOT deleted even though 11 forbids it to the Hiring Manager: 24 affirmatively
grants it to the Super Admin and the HR Manager.

`Role.recruitment_manager` is not in RBAC 24. It holds the HR Manager's cells,
which is what its rank in `role_hierarchy.HIERARCHY` already meant.

### Grants that exceed the ceiling

Six grants predate the specification and sit above its ceiling. All six come
from the flat staff model CLAUDE.md records as a client decision. All six are
refused at runtime and removed from the advertised capability list, and all six
are enumerated in `KNOWN_GRANTS_ABOVE_THE_CEILING`
(`tests/test_rbac_conformance.py`) with a per-row test proving the refusal.

| Role | Capability | RBAC 24 says |
|---|---|---|
| HR Manager | `manage_staff` | NO\* |
| HR Manager | `assign_roles` | NO\* |
| HR Manager | `publish_job` | YES\*, withheld against 9.6 |
| Recruiter | `manage_staff` | NO |
| Hiring Manager | `publish_job` | NO |
| Hiring Manager | `decide_profile` | NO\* |
| Hiring Manager | `update_pipeline_status` | NO\* |

Narrowing the underlying `role_permissions` rows is a live-data change with a
support consequence and is left for a product decision. A seventh divergence
fails a test.

---

## 4. Ownership: `job_assignments`

RBAC 23 separates RBAC from ownership. Before 2026-08-29 this codebase had no
per-job assignment of any kind: `jobs.created_by` records who typed, not who
owns, and it is `ON DELETE SET NULL`, so an invariant built on it would
evaporate when a user is deleted.

Migration `0061_rbac_cardinality_and_audit` adds `job_assignments`:

| Column | Notes |
|---|---|
| `tenant_id` | RLS policy, like every tenant-scoped table. |
| `job_id` | `ON DELETE CASCADE`. |
| `user_id` | `ON DELETE RESTRICT`. An assignment whose person was erased asserts that somebody owns this job while being unable to say who. |
| `assignment_role` | CHECK: `recruiter` / `hiring_manager` / `interview_manager`. |
| `active`, `revoked_at` | CHECK: exactly one of them is set, so a revoked row cannot look live. |

`rbac.decide` requires an active assignment for every `SCOPED` cell.
Assignment roles are **never** inferred from `users.role`: holding the
Recruiter role is not being the Recruiter for a job (9.2), and 10.2 says the
same again for the Hiring Manager.

**The table is created empty and nothing is backfilled.** Deriving an assignee
from `created_by` would invent an ownership record that grants access, which
is the opposite of the restrictive direction. Until a job is assigned, every
scoped role is refused it and the org-wide roles are unaffected.

---

## 5. Cardinality (RBAC 5, 39)

| Invariant | Enforced by | Where |
|---|---|---|
| Exactly one active Super Admin per client | **Partial unique index**, unique on `(tenant_id)` so each tenant holds one | `uq_users_one_active_super_admin_per_tenant` |
| Exactly one Recruiter per job | **Partial unique index** | `uq_job_assignments_one_active_recruiter` |
| Exactly one Hiring Manager per job | **Partial unique index** | `uq_job_assignments_one_active_hiring_manager` |
| A job may have many Interview Managers | **Absence of a constraint**, plus `uq_job_assignments_no_duplicate_holder` so one person cannot hold the same assignment twice | migration 0061 |

Partial indexes are the right instrument for "exactly one ACTIVE": historical
rows stay, and a replacement can be inserted beside a revoked one. An
application-level check is not equivalent, because two concurrent requests both
read zero and both insert.

### The Super Admin survey

A unique index cannot be added to a table that already violates it, so
migration 0061 surveys first and raises with the offending tenant ids named.
It does not delete, deactivate or pick a winner: which of two Super Admins is
real is the customer's decision.

Run before deploying:

```sql
SELECT tenant_id, count(*), array_agg(id)
FROM users
WHERE role = 'client' AND status <> 'disabled' AND tenant_id IS NOT NULL
GROUP BY tenant_id HAVING count(*) > 1;
```

The index is `UNIQUE ON users (tenant_id) WHERE role = 'client' AND status
<> 'disabled' AND tenant_id IS NOT NULL`. Uniqueness is on the tenant_id
VALUE, so each distinct tenant admits exactly one row. This is the difference
between a working constraint and one that rejects the second customer ever
onboarded, and `test_two_tenants_may_each_hold_their_own_super_admin` asserts
the expression rather than trusting it.

### The transfer mechanism (RBAC 7.1)

7.1 has a second sentence: "The system MUST provide a controlled mechanism for
changing/transferring the Super Admin role when necessary." Shipping the
uniqueness index without it would be the worse half of the requirement on its
own: a client whose Super Admin leaves the company could not appoint another,
because the index refuses the second row and nothing existed to deactivate the
first. The constraint is exactly what would make that unrecoverable.

`rbac.transfer_super_admin` demotes the outgoing holder and promotes the
incoming one in the caller's transaction, in that order (the index is checked
per statement, so promoting first is refused while the outgoing holder is
still active). The outgoing holder is DEMOTED, never deleted or disabled, and
defaults to HR Manager, the nearest organisation-wide role in 6's hierarchy.

`POST /admin/tenants/{tenant_id}/super-admin` exposes it on the Provider
console, which is where the seat is minted at onboarding and the only place it
can live for the case that needs it: a tenant whose Super Admin has left has
nobody inside it with the authority to appoint a replacement. Recorded with
`exceptional=True`, because an administrative act on somebody else's
organization should read as exceptional in the trail.

### Survey result

Run 2026-08-29 against the containerised test database (22 users, 35 jobs,
2 tenants):

```
SURVEY multi-super-admin tenants: []
client rows per tenant:           1, 1
```

**Zero violations.** Migration 0061 applied cleanly, and `alembic downgrade`
then `upgrade` round-trips. All four indexes and all twelve audit columns were
verified present afterwards.

This is NOT the production database. No production Postgres was reachable from
the implementing session, so the survey above is evidence that the migration
runs and that the seeded shape is clean, not that production is. Run the query
above before deploying; the migration will refuse with the offending tenant ids
named if it is not.

---

## 6. Job lifecycle (RBAC 17) and the two "stage" concepts

`JobLifecycleState` (`services/hiring_pipeline.py`) is RBAC 17's eight states,
on `jobs.lifecycle_state`. `CandidatePipelineStage` is the Dashboard
Specification's six coarse stages, and it is a **presentation view** derived
from the stored ten-value pipeline, not a replacement for it.

Three vocabularies, and how they relate:

| | Entity | Column | Values |
|---|---|---|---|
| RBAC 17 job lifecycle | `jobs` | `lifecycle_state` | 8 |
| Stored candidate pipeline | `job_candidate_links`, `pipeline_status` | `status` | 10 + legacy `offered` |
| Dashboard candidate stage | none (derived) | none | 6 |

`DASHBOARD_STAGE` maps every stored status onto a coarse stage except one.
`shortlisted` is untouched: historic applications sit in it and it is still
the only route into `interview_scheduled`.

**`hold` has no coarse stage, and forcing one was the first attempt.** The
Dashboard Specification treats hold as an ACTION taken on a candidate rather
than as a stage they occupy, and the stored FSM agrees: `hold` returns to
whatever stage it paused rather than carrying outward edges of its own.
Screening would claim the candidate had moved backwards; Closed would say the
process had ended. `dashboard_stage` returns `None` and `NO_DASHBOARD_STAGE`
names the exemption so the absence reads as a decision.

`test_stage_enum_separation.py` asserts the two vocabularies share no value,
that no code path assigns one to the other (an AST walk over the whole `app`
package), and that they never share a table.

State rules the lifecycle drives:

- Publishing requires `FINALIZED` or later (21).
- The Recruiter's JD edit is refused from `FINALIZED` (24\*\*\*, 26).
- Hiring-Manager-controlled criteria are frozen at `FINALIZED` for everyone
  including the Hiring Manager, because 12 and 22 require an explicit revision
  workflow and none exists yet.

---

## 7. Cross-tenant: 404, never 403

RBAC 33 states the PRINCIPLE and never names a status code: knowing an id
must not be sufficient, and "Obscurity is NOT authorization". The status code
comes from spec-doc6 9.1, which instructs it explicitly: "Cross-tenant reads
return 404, never 403, so existence is not disclosed."

The reason both are pointing at is RBAC 4, which forbids a user of one client
to "access, **infer**, modify, delete or retrieve" another client's resources.
A 403 on a foreign id answers "that exists" to anybody who can enumerate
uuids, which is inference.

- Cross-tenant resource -> `Decision.NOT_FOUND` -> 404.
- Nonexistent id -> 404, byte-identical.
- Unparseable id -> 404, so the shape of an id is not itself an oracle.
- **In-tenant but unassigned -> 403.** The resource belongs to the caller's own
  company, so the distinction is not a leak, and spec-doc6 8.2 expects the
  dashboard to render a disabled control rather than nothing.

This holds for every client role including the Client Super Admin. It also
holds for agents: `authorize_agent_action` checks tenant before anything about
the agent, so an agent refusal cannot be told apart from a foreign resource.

---

## 8. Agent authorization (RBAC 34)

Three layers, and each answers a different question.

| Layer | Question | Where |
|---|---|---|
| `AGENT_TOOLS` | What may this agent READ? | `services/tools/permissions.py` |
| `AGENT_CAPABILITIES` | What may this agent CAUSE? | `services/rbac.py` |
| `decide` | On whose authority, in which tenant, on which job, in which state? | `services/rbac.py` |

`authorize_agent_action` resolves through `decide`, the identical function
`require_authorized` uses for HTTP, so an agent inherits tenant isolation, the
24 ceiling, per-job scope and the state rules without a line of its own. That
is spec-doc6 9.2's "the same authorization layer, not a parallel one".

`AGENT_FORBIDDEN_CAPABILITIES` is refused to **every** agent whatever its
principal holds: finalize, publish, reject JD, decide profile, move stage,
manage staff, assign roles, integrity disposition. Finalization is a human act
(20 requires it to record who), publication is the Recruiter's operational act
(9.6), and a candidate decision is the sensitive action a human must take at
any confidence.

RBAC 34's worked example runs as
`test_the_specifications_own_worked_example`: a Recruiter-authorized agent is
refused every Hiring-Manager-controlled field, and the same agent under a
Hiring Manager is allowed them.

`Principal.__post_init__` raises if `agent` is set and `user_id` is None. There
is no way to represent an agent acting on nobody's authority.

### What is NOT claimed

`services/agents/identity.py` maps all six named agents onto the OLD runtime
surfaces (`AGENT_JOB_SETUP`, `AGENT_RANKING`, `AGENT_INTERVIEWER`,
`AGENT_SCORING`, `AGENT_PPI_REPORT`), and no live path calls
`services/hiring`, `services/miti` or `services/siddhi`. The gate is proven;
Part A running is not, and nothing here should be read as claiming otherwise.

---

## 9. Audit (RBAC 30, 31, 34)

`audit_log` carried 6 of RBAC 30's 11 fields and no agent attribution at all.
Migration 0061 adds twelve columns, all nullable, so a rolling deploy has an
old writer and a new reader coexisting.

| RBAC 30 field | Column | Before 0061 |
|---|---|---|
| Actor | `actor_user_id` | present |
| Actor role at time of action | `actor_role` | **added** |
| Tenant/client | `tenant_id` | present |
| Action | `action` | present |
| Resource type | `target_type` | present |
| Resource ID | `target_id` | present |
| Previous value/state | `previous_state` | **added** |
| New value/state | `new_state` | **added** |
| Timestamp | `at` | present |
| Job / application / candidate context | `job_id`, `application_id`, `candidate_id` | **added** |
| Source/request metadata | `request_method`, `request_path`, `request_ip` | **added** |
| RBAC 34 human principal | `actor_user_id` | present |
| RBAC 34 executing agent | `agent_name` | **added** |
| spec-doc6 4.1 correlation id | `correlation_id` | **added** |
| RBAC 24 asterisked cell used | `exceptional` | **added** |

`audit.record_action` names every field as a keyword argument, and raises
`AgentPrincipalError` for an agent row with no human. `audit_log` also carries
a CHECK enforcing the same shape, because the service is not the only writer a
database ever gets.

`actor_role` is COPIED, never joined: a person's role changes, and what
authority a past action was taken under does not.

`audit.activity` is the reader behind RBAC 31's dashboard. It is a plain
paginated query, and `test_the_trail_exists_with_nothing_rendered` runs the
whole scripted scenario with no route or serialiser in the process, which is
RBAC 31's closing sentence as a test.

### No rejection without a recorded human disposition

- `TriangulationResult` has no reject field, no status and no decision.
- `review_dispositions.decided_by` stays `ON DELETE RESTRICT`.
- `decide_profile` is in `AGENT_FORBIDDEN_CAPABILITIES`, so no agent can author
  a rejection even in principle.
- A candidate row under open integrity review does not MOVE, and reading is
  untouched: a finding must not remove the candidate from anybody's screen, or
  nobody can review it.

---

## 10. Not yet wired

Honest scope. These exist as a decision layer and a conformance suite; they
are not attached to the production handlers, which are owned by other work in
this phase.

| Piece | State |
|---|---|
| `rbac.require_authorized` on `api/jobs.py`, `api/pipeline.py`, `api/candidates.py` | **Not wired.** Those routers still gate on `require_capability` alone, so tenant is enforced by RLS and scope/state are not enforced at all. `test_rbac_conformance.py` mounts RBAC 32's route surface with the real dependency and is what the wiring must satisfy. |
| `jobs.lifecycle_state` writers | Column added and backfilled; no handler transitions it yet. |
| `job_assignments` writers | Table added; no route assigns anybody yet. |
| Client Super Admin activity view | The reader (`audit.activity`) and the Provider-side route exist. The client-facing route belongs in `api/companies.py`, which this work did not own. |
| RBAC 35 per-application criteria version | `jobs.criteria_version` exists as a counter. Referencing it from each application's evaluation context is not built. |
| RBAC 12/22 post-finalization revision workflow | Does not exist, so post-finalization criteria edits are REFUSED rather than allowed-and-versioned. That is the restrictive reading and it is deliberate. |

---

## 11. Two defects this work introduced and then caught

Recorded because both were caught by a test rather than by review, and both
would have been invisible in a diff.

**The `job_assignments` RLS policy omitted `app.bypass_rls`.** Every other
tenant-scoped table in this schema carries `OR current_setting('app.bypass_rls',
true) = 'on'` in both USING and WITH CHECK. The first draft of 0061 did not, so
the Celery workers and the audited Super Admin path could reach every
tenant-scoped table except this one. The symptom would have been a 500 in a
background task, which is the kind that surfaces days later as work that
silently did not happen. Caught by `test_rbac_cardinality.py` failing with
`new row violates row-level security policy`; fixed by copying migration
0001's policy verbatim.

**`jobs.lifecycle_state` shipped without a server default.** Rows written after
the migration landed with NULL, and `rbac.decide` read an unknown state as "no
state rule applies", which would have made publishing an unfinalised job
possible for exactly those rows. Five appeared in the test database within one
suite run. Two independent fixes, because the default only protects rows the
default reaches: the column now defaults to `DRAFT` (the state that grants
least), and `decide` refuses every state-gated capability outright when the
state is unknown, with reason `lifecycle_state_unknown`. Reads are still
allowed, because refusing them would hide the job from the people who have to
fix its state.

---

## 12. A pre-existing defect found while mapping the roles

`core.security._ORG_ROLES` and `api.auth.PORTAL_ROLES` were both
hand-maintained lists of the tenant roles, and **`recruitment_manager` was
missing from both** and had been since migration 0050 added the role.
`audience_for_role` raised `ValueError: no audience defined for role
'recruitment_manager'`, so no token was ever minted: the login failed before
any capability was consulted. There is no permission model in which that reads
as a refusal a user could act on.

`interview_manager` would have arrived with the identical defect. Both lists
are now complete, and three tests assert them against `Role` itself rather than
against another hand-maintained list, so the next role added fails a test
rather than a person's sign-in.

---

## 13. Enforcement summary

| Rule | Enforced by |
|---|---|
| Permissions are data | `role_permissions` + `users.permissions_json`, `require_capability` |
| RBAC 24 ceiling | `capabilities.RBAC_INVARIANTS`, applied in `rbac.decide` |
| Tenant isolation | Postgres RLS (real boundary) + `rbac.decide` cross-tenant branch (defence in depth) |
| Per-job scope | `job_assignments` + `rbac.decide` |
| Cardinality | Partial unique indexes (migration 0061), proven to fire in `test_rbac_cardinality.py` |
| Hiring Manager cannot reject a JD | `Invariant.NEVER` + grant `False` + no route |
| Agent cannot exceed its principal | `rbac.authorize_agent_action` -> `rbac.decide` |
| Agent dual attribution | `Principal.__post_init__`, `audit.record_action`, DB CHECK |
| Two stage concepts stay separate | `test_stage_enum_separation.py` AST walk |
