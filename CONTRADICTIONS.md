# CONTRADICTION REGISTER

**Status:** PHASE 0 deliverable, per spec-doc6 §0.3 and §15.
**Document ID:** RPN-CONTRA-001
**Date:** 29 August 2026
**Branch:** `feat/specdoc6-activation`

Seeded with the 18 items in spec-doc6 §15, plus everything else found by reading the
documents against **the repository**, not against each other's claims about it
(spec-doc6 §0.1 item 6, §20).

**Precedence table (spec-doc6 §0.2), cited by rank throughout:**

| Rank | Domain | Document |
|---|---|---|
| 1 | Authorization, tenant isolation, role ownership, job lifecycle, audit | RBAC Specification |
| 2 | Evaluation mechanics | `Readypick Hiring Philosophy.md` (Runbook, RPN-PHIL-001) |
| 3 | Anything spec-doc6 states explicitly | spec-doc6 |
| 4 | Candidate list surface | Candidate Dashboard Specification |
| 5 | Agentic architecture, PRISM structure, product contracts | specdoc4, then spec-doc5 |

**Revision 2, 29 August 2026.** The RBAC and Dashboard specifications were supplied by the
product owner mid-session and are now filed, unedited, under `docs/spec/`. Every entry has
been re-resolved against their real text. See C0.

**Totals:** 47 contradictions recorded. 18 seeded from spec-doc6 §15, 29 found here
(C0, C19 to C46). **41 resolved by the precedence table. 6 remain
`RESOLVED-BY-DEFAULT` and need the owner's review: C19, C32, C34, C35, C37, C46.**

Three entries are **known divergences with a stated reason** rather than defects awaiting a
fix: C45 (the public job path), and the two naming instances in C43 that are operational
facts only the owner can settle.

Revision 1 listed nine as resolved-by-default. Seven of those (C0, C4, C5, C6, C7, C11,
C16, C22) collapsed to precedence-table resolutions the moment the real documents were
read. That is the register working as intended: eight defaults were provisional readings
of an absent authority, and none of them survived contact with it.

**Enforcement column:** names the test or code site that enforces the resolution, or
`ENFORCEMENT-PENDING` plus what should enforce it.

---

# C0. PROVENANCE: THE TWO SPECIFICATIONS ARRIVED MID-SESSION

**Severity: provenance note. Not a blocking gap.**

**Corrected 29 August 2026. Revision 1 of this register recorded these two documents as
absent and marked eight entries `RESOLVED-BY-DEFAULT` on that basis. That finding was
true only of the moment it was made and is now wrong. It has been replaced rather than
annotated, because a standing "these documents do not exist" entry at the top of the
register would mislead every later reader.**

### What happened

spec-doc6 §0.1 describes both documents as "provided in this session":

> 4. **`Ready Pick Now: Client RBAC, Authorization & Hiring Workflow Specification`**, provided in this session. Authoritative for all authorization, tenant isolation, role ownership, job lifecycle and audit questions.
> 5. **`Ready Pick Now Candidate Dashboard: Column Framework & Specification v1.0` (28 Aug 2026)**, provided in this session. Authoritative for the candidate list surface only.

At the moment PHASE 0 analysis began, neither was on disk. An exhaustive search (repository
by filename, the user profile to depth 6, Downloads including `Downloads/readypick`,
Documents, Desktop, OneDrive, `C:\dev`, the contents of every `.docx` and `.pptx` via
`unzip -p`, and `git log --all --diff-filter=D`) found neither in any format. **The product
owner supplied both later in the same session**, and the coordinator filed them verbatim
with provenance headers.

### Where they are now

```bash
ls -la /c/dev/pickready/docs/spec/
```

| File | Size | Lines | Precedence |
|---|---|---|---|
| `docs/spec/RBAC_SPECIFICATION.md` | 36 KB | 1,741 | **Rank 1** (spec-doc6 §0.2) |
| `docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md` | 20 KB | 447 | **Rank 4** |
| `docs/spec/ARCHITECTURE_DIRECTION_2026-08-28.md` | 11 KB | 228 | Advisory, **below everything** in §0.2 |

Each carries a provenance header recording that it was filed unedited and that, where
spec-doc6 restates it, spec-doc6's restatement is a summary and the filed file is the
authority.

### The two acceptance criteria are now meetable

Revision 1 called these unmeetable. Both are meetable.

1. **spec-doc6 §17: "Every cell of the §24 matrix is an executable test at the HTTP
   layer."** RBAC §24 (`docs/spec/RBAC_SPECIFICATION.md:1003-1046`) is a **complete
   table**: 24 capability rows by 5 role columns, **120 cells**, with three footnote
   markers whose meanings are defined immediately beneath it. Nothing is missing. The
   conformance suite can be generated from it directly.
2. **spec-doc6 §17: "All eight columns implemented per the Dashboard Specification"** and
   **"the three named workflows are tested as user journeys."** The Dashboard
   Specification carries all eight columns with content, states, styling, exact pixel
   sizes, the visual-hierarchy order, the colour palette, typography, spacing, row states,
   transitions, tab order, screen-reader behaviour, mobile behaviour, the five Ready Pick
   Score bands with their numeric ranges, and all three workflows step by step
   (`CANDIDATE_DASHBOARD_SPECIFICATION.md:320-345`). Phase 8 can be built "as written".

### What this does not change

Everything the specifications do **not** cover remains open, and reading them surfaced
**thirteen new contradictions** (C30 to C42), of which seven are defects in spec-doc6's own
citations of RBAC (C38). The register grew rather than shrank.

**Enforcement:** the specifications are now in the repository and under version control, so
a future session inherits them. `docs/spec/` is the canonical location; cite by file and
section, never from memory.

---

# SEEDED FROM SPEC-DOC6 §15 (C1 to C18)

## C1. Numeric score on the dashboard versus no numbers anywhere

**Source A, spec-doc6 D8:** *"The **Ready Pick Score** (0 to 100 plus band plus confidence)
is a **dashboard triage artifact**. It renders in the candidate list and nowhere else."*

**Source B, CLAUDE.md (2026-07-27 section):** *"**NO NUMBERS REACH A CLIENT. EVER.** Not a
score, percentage, rank, band index, '7.5/10', or 'top 12%', in the UI, in an API response,
or in an email."*

**Resolution (rank 3, spec-doc6 D8 is explicit).** Numbers on the dashboard, never in the
delivered report. Enforce with a serialiser-level rule plus a test asserting no numeric
score field appears in any PRISM payload in any export format.

**This overturns a standing rule and needs the owner's sign-off.** See C19, which is the
same ruling stated against CLAUDE.md rather than against specdoc4/5, and which spec-doc6
does not itself flag. spec-doc6 D8 says: *"Flag this ruling in the final report for the
owner to override if he wants the number gone from the dashboard too."*

**Repo state.** The rule is currently held well. The one place a numeric score could reach
a client is scrubbed: `backend/app/api/matching.py:341` wraps the stored breakdown in
`client_breakdown()` (`backend/app/services/matching.py:771-791`), which strips every
`score` key and every bare numeric value. The single documented exception is the radar
chart's band index (1 to 4), a rendering coordinate, at
`backend/app/schemas/assessments.py:201,203` with the reason in its docstring at `:191-196`.

**Enforcement:** `backend/tests/test_platform_audit.py:295`
(`test_report_ratings_are_words_not_numbers`), `:305`
(`test_matching_labels_are_words_not_numbers`). **`ENFORCEMENT-PENDING`** for the new
serialiser-level PRISM number ban that spec-doc6 §4.5 requires, which does not exist yet.

---

## C2. Dimension breakdown: D1 to D5 numbers versus internal-and-invisible

**Source A, spec-doc6 C2:** the Dashboard's Ready Pick Profile panel lists *"Dimension
breakdown (D1 to D5 scores)"*.

**Source B, CLAUDE.md:** *"Miti's five dimension evaluators are ISOLATED STRUCTURALLY"* and
Miti *"has no surface of its own, and that is the product requirement"*
(`backend/app/services/agents/identity.py:150-153`).

**Resolution (rank 3, spec-doc6 D8).** The Profile panel shows **named per-dimension
ratings**, not raw numbers. Raw D1 to D5 numbers, evaluator outputs and aggregation
internals are exposed only through an authenticated calibration/audit view restricted to
Super Admin and HR Manager, and **always logged when viewed**.

**Repo state.** The five dimensions exist at `backend/app/services/miti/dimensions.py`
(404 lines) and are not on a live path. There is no calibration view and no access log for
one.

**Enforcement:** `ENFORCEMENT-PENDING`. Needs (a) a serialiser test that the Profile
payload carries no D1 to D5 float, and (b) an audit-row assertion on every calibration-view
read.

---

## C3. Product naming

**Source A, spec-doc6 §10.2:** *"The product is currently called 'Ready Pick Now',
'ReadyPick', 'Readypick' and 'readypick.ai' across documents and code. Fix once,
everywhere."* Recommendation stated for the owner to override: **Ready Pick Now** as the
product name, **ReadyPick** as the wordmark and domain-facing brand.

**Source B, CLAUDE.md (memory, `readypick-rebrand.md`):** *"product renamed 2026-08-16;
GCP/Celery/JWT identifiers still say 'pickready' deliberately, do not 'fix' them."*

**Resolution (rank 3).** Apply §10.2. **Two variants spec-doc6 does not list are also
present and matter more than the four it does.** Counts below are per category, so a CI
check does not sweep a deliberate internal identifier.

### Counts (build artifacts, `node_modules`, `.venv`, `.next`, `.next-dev` and `*.map` excluded)

| Area | Ready Pick Now | ReadyPick | Readypick | readypick.ai | PickReady | pickready | picready |
|---|---|---|---|---|---|---|---|
| `frontend/{app,components,lib}` | 0 | 95 | 0 | 0 | 0 | 6 | 0 |
| `backend/app` | 0 | 80 | 4 | 0 | 0 | 152 | 5 |
| `backend/tests` | 0 | 2 | 0 | 0 | 13 | 62 | 10 |
| `docs` | 18 | 12 | 1 | 2 | 0 | 5 | 0 |
| `infra` | 0 | 8 | 0 | 0 | 0 | 25 | 0 |
| `.github` | 0 | 1 | 0 | 0 | 0 | 7 | 0 |
| root `*.md` | 39 | 30 | 5 | 0 | 4 | 7 | 0 |

Repo-wide raw totals including build artifacts, for scale: `pickready` 3262 (of which 2721
are in one Turbopack cache file), `ReadyPick` 355, `PickReady` 50, `Ready Pick Now` 44,
`picready` 18, `Readypick` 9, `readypick.ai` 5.

### Category split, which is what a CI check must respect

**Deliberate internal identifiers, leave alone.** `pickready` in `backend/app` is
overwhelmingly Celery task names and cache/JWT namespaces. Top occurrences:

```
19  pickready.send_email          14  pickready.run_matching
14  pickready.parse_resume        10  pickready.test
 6  pickready:tenant:              6  pickready.generate_ppi_framework
```

Full list: `grep -rho "pickready[a-z:._-]*" backend/app | sort | uniq -c | sort -rn`.
Renaming any of these breaks a rolling deploy (a beat entry, a queued message and a worker
registration cannot change atomically), which CLAUDE.md already records.

Frontend `pickready` is 6 sites, 5 of which are browser storage keys and internal flags:
`frontend/app/(candidate)/portal/(app)/assessments/[link_id]/page.tsx:72`,
`frontend/app/(org)/org/layout.tsx:48,63`, `frontend/components/chunk-recovery.tsx:19`,
`frontend/lib/theme-provider.tsx:20`. Leave them.

**User-facing, must be fixed.** Three findings spec-doc6 §10.2 does not anticipate:

1. **`picready.com`, a misspelling with a missing `k`, is the documented production
   domain.** It appears in five `backend/app` sites and ten test sites, including
   `backend/app/api/jobs.py:98,101,1359`, `backend/app/api/portal.py:467`,
   `backend/app/schemas/jobs.py:145`, and is asserted by
   `backend/tests/test_jobs.py:163,335,713`
   (`assert jobs_api.public_job_url(jid) == f"https://picready.com/apply/{jid}"`). This is
   the string a candidate sees in a job link. It is a seventh variant, and it is a typo.
2. **`hello@pickready.app`**, an eighth variant, is a live `mailto:` in customer-facing UI
   at `frontend/app/(org)/org/billing/page.tsx:442`.
3. `readypick.ai` appears only in `docs` (2 hits) and nowhere in code, so the domain
   spec-doc6 recommends as the brand is not the one the product actually links to.

**Enforcement:** `ENFORCEMENT-PENDING`. The CI check §10.2 asks for must exempt the
identifier categories above by path and pattern, or it will demand a breaking rename of
every Celery task. Add `picready` and `pickready.app` to the disallowed list.

### AMENDMENT (rev 2): RBAC §15 settles the domain, and the repository is wrong

RBAC §15 (`docs/spec/RBAC_SPECIFICATION.md:783-798`) gives the canonical public job URL:

> ```
> https://readypick.ai/jobs/3252463dfbg43t4hfb
> ```

**`readypick.ai` is the canonical domain**, stated by the rank-1 authority. That confirms
spec-doc6 §10.2's recommendation on the domain half without needing the owner to arbitrate,
and it makes both repository domains defects rather than open questions:

| Repository value | Sites | Verdict |
|---|---|---|
| `picready.com` | `backend/app/api/jobs.py:98,101,1359`, `portal.py:467`, `schemas/jobs.py:145`, asserted by `backend/tests/test_jobs.py:163,335,713` | **Wrong.** A misspelling with a missing `k`, and it is the string a candidate sees in a job link |
| `hello@pickready.app` | `frontend/app/(org)/org/billing/page.tsx:442` | **Wrong.** A third domain in customer-facing UI |
| `readypick.ai` | `docs` only, 2 hits | **Correct, and used nowhere in code** |

The URL **path** is also settled: `/jobs/{id}`, matching spec-doc6 §13.2. The repository
serves `/apply/{job_uuid}` on the frontend and `GET /jobs/public/{job_id}` on the API
(`backend/app/api/jobs.py:97-105,1353`). See C42.

The product-name half of §10.2 (Ready Pick Now versus ReadyPick) is **not** settled by RBAC:
the specification's own title uses "Ready Pick Now" while its example URL uses `readypick`,
which is precisely the split spec-doc6 §10.2 recommends. That recommendation now has
rank-1 corroboration and needs only the owner's confirmation.

---

## C4. "Four internal role categories" then five listed: CONFIRMED VERBATIM

**Source A, RBAC §5** (`docs/spec/RBAC_SPECIFICATION.md:207-221`), quoted in full:

> Ready Pick Now has four internal role categories for client organizations:
>
> Client Super Admin
>
> HR Manager
>
> Recruiter
>
> Hiring Manager
>
> Interview Manager
>
> The fifth role, Candidate, is outside this internal client RBAC specification.

The document says "four", lists **five**, and then calls Candidate "the fifth role", which
is how the arithmetic went wrong: the author counted Candidate as the fifth and did not
notice the internal list had already reached five.

**Resolution (rank 1; the defect is internal to the rank-1 document, so precedence resolves
it by reading the enumeration rather than the count).** **Five** internal client roles. The
list is normative; the number is a typo. §6's hierarchy diagram (`:231-255`) independently
shows five, and §24's matrix header (`:1007`) has exactly five role columns. Three independent
statements of five against one statement of four.

**No longer `RESOLVED-BY-DEFAULT`.** Revision 1 marked it so only because the document was
unavailable. It is a clean precedence resolution.

**Repo state.** Eight roles exist (`backend/app/models/enums.py:5-22`). The mapping is not a
rename: see C22 (Super Admin) and C23 (HR Manager). `interview_manager` does not exist at
all: see C16.

**Enforcement:** `ENFORCEMENT-PENDING`. The §24 conformance suite, covering five role
columns, is itself the enforcement.

---

## C5. Dashboard assumes the recruiter moves stages; RBAC §24 says NO for HM and IM

**Source A, the Dashboard Specification, Column 8**
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:238-248`):

> **Move-to dropdown:** lets recruiter advance or hold the candidate
>
> | Normal flow | Enabled; pre-populated with the stage most likely given the Ready Pick Score |

The document is written for one role throughout. `grep -ci "recruiter"` returns **19**;
`grep -ci "interview manager"` returns **0**. It contains no role gating of any kind.

**Source B, RBAC §24** (`docs/spec/RBAC_SPECIFICATION.md:1027-1029`), three rows, columns
Super Admin / HR Manager / Recruiter / Hiring Manager / Interview Manager:

> | Shortlist candidates | YES | YES | YES | NO* | NO |
>
> | Reject candidates | YES | YES | YES | NO* | NO |
>
> | Move candidates through stages | YES | YES | YES | NO* | NO |

**Resolution (rank 1 beats rank 4).** Dashboard stage controls are RBAC-driven: enabled for
Super Admin, HR Manager and Recruiter; **disabled with an explanatory tooltip** for Hiring
Manager and Interview Manager. The Dashboard already establishes that exact treatment for a
different reason in Column 8's integrity row (*"Disabled; tooltip: 'Pending integrity
review, HR Manager only'"*, `:243`), so reuse it rather than hiding the control.

**Read the asterisk.** spec-doc6 §8.2 renders these cells as a flat "NO". The document marks
the Hiring Manager cells **`NO*`** and defines the marker at `:1033-1034`:

> These entries are intentionally conservative and may require an explicit future product decision. Super Admin always retains ultimate authority.

So the Hiring Manager's NO is **provisional and flagged for a future product decision**,
while the Interview Manager's NO is unqualified and independently restated in §13.5
(`:750-752`, *"Modify candidate hiring-stage status"*, *"Shortlist/reject candidates"*).
Implement both as NO; record that one of the two is a placeholder the owner may revisit.
Citation defect logged in C38.

**Scope: RBAC is explicit, and the repository cannot express it.** §9.2 (`:446-456`):

> Each job MUST have exactly one Recruiter.
>
> A Recruiter is associated with a job through an explicit job assignment.
>
> A Recruiter does not automatically have access to every job in the company merely because they hold the Recruiter role.

§23 (`:970-1002`) draws the ownership diagram: Recruiter A acts on Job 101 and *"does not
automatically receive equivalent operational ownership of Job 102."*

```bash
grep -n "created_by\|ForeignKey" backend/app/models/job.py
grep -rn "job_assignment\|assigned_recruiter\|assigned_to" backend/app/models backend/alembic/versions
```

`jobs` carries one user reference, `created_by` (`backend/app/models/job.py:83-84`,
nullable, `ON DELETE SET NULL`), plus `approver_user_id` (`:195`). **There is no job
assignment table.** §9.2 and §10.2 both require one, and §5 makes the cardinality mandatory:
exactly one Recruiter, exactly one Hiring Manager, many Interview Managers.

**No longer `RESOLVED-BY-DEFAULT`.** The scope rule is fully specified. What is missing is
schema, which is a build task rather than an ambiguity.

**Enforcement:** `ENFORCEMENT-PENDING`, blocked on the assignment table. The required test
(five roles by each control by assigned / unassigned / other-tenant) becomes writable the
moment it exists.

---

## C6. Team Review as the recruiter's verdict versus Interview Managers as participants

**Source A, the Dashboard Specification, Column 7**
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:209-232`):

> Single action button: **"Team Review"** (always enabled)
>
> Opens a side panel containing: Recruiter's own structured verdict (independent of the Ready Pick Score) / Checkbox verdicts: Pass / Hold / Reject

**Source B, RBAC §13.4** (`docs/spec/RBAC_SPECIFICATION.md:720-732`):

> Interview Managers can participate in Team Review.
>
> They can: Add remarks / Add comments/observations / Contribute candidate-specific review information
>
> Team Review contributions MUST identify the author and timestamp.

and **§24** (`:1031`): `| Add Team Review remarks | YES | YES | YES* | YES* | YES |`. The
Interview Manager holds the **only unqualified YES** in that row.

**Resolution (rank 1).** Team Review is open to all five roles. The Interview Manager is the
one participant whose access is unconditional; Recruiter and Hiring Manager carry the
"intentionally conservative" asterisk.

**Authorship, RBAC §29** (`:1161-1171`):

> Every remark MUST preserve: Author / Timestamp / Candidate / Job/application context
>
> Interview Managers MUST NOT be able to silently alter another interviewer's remarks.

The no-edit rule is stated **of Interview Managers**, not universally. spec-doc6 §8.2
generalises it to *"Nobody may edit another user's remark"*. That generalisation is stricter
than its source and therefore survives under spec-doc6 §20 (*"implement the one that
restricts more"*), but it is an overreach and is logged in C38.

**A conflict inside the Dashboard document itself, which spec-doc6 does not flag.** Column
7's panel offers **"Checkbox verdicts: Pass / Hold / Reject"** on a button that is *"always
enabled"*. RBAC §13.5 (`:750-752`) forbids Interview Managers from *"Shortlist/reject
candidates"*. If that checkbox is a hiring decision, an Interview Manager exercising it
breaches §13.5; if it is an opinion recorded against the candidate, it does not.

**Resolve in the restrictive direction:** the Team Review verdict is an **opinion field on a
remark**. It never writes a pipeline stage, never shortlists and never rejects. The
Dashboard supports that reading by keeping Stage a separate column (`:255-262`: *"A
candidate's stage in your pipeline is orthogonal to their readiness"*). Enforce
structurally: the Team Review write path must have no access to the stage transition
function.

**No longer `RESOLVED-BY-DEFAULT`.**

**Repo state.** Team Review is the one dashboard column genuinely built:
`CandidateTeamReview`, routes at `backend/app/api/candidates.py:572-580`, UI at
`frontend/components/candidate-team-review-modal.tsx` (277 lines).

**Enforcement:** `ENFORCEMENT-PENDING`. Needed: a 403 test for a user other than
`reviewer_user_id` editing a remark; a matrix case per participating role; and a structural
test that the Team Review handler cannot reach `hiring_pipeline.apply_transition`.

---

## C7. Integrity disposition "HR Manager only" versus Super Admin override on everything

**Source A, the Dashboard Specification.** Column 4's Under Review state
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:143`): *"D4 (Authenticity) < integrity
floor; awaiting HR Manager disposition"*. Column 8 (`:243`): *"Disabled; tooltip: 'Pending
integrity review, HR Manager only'"*. Workflow 2 (`:334`): *"HR Manager closes the flag, row
goes green, Stage dropdown unlocks"*.

**Source B, RBAC §7.5** (`docs/spec/RBAC_SPECIFICATION.md:339-362`):

> If an action is normally restricted to another role, the Super Admin MAY perform that action.
>
> Super Admin may change candidate status. Super Admin may move candidates through hiring stages.
>
> The system MUST still record the Super Admin's action in the audit trail.

**Resolution (rank 1).** HR Manager **by right**; Super Admin **by override, and the
override is audited because §7.5's own final line requires it**. No other role: §24 grants
no disposition capability to Recruiter, Hiring Manager or Interview Manager, and §13.5
forbids the Interview Manager from modifying hiring-stage status.

**No longer `RESOLVED-BY-DEFAULT`.** Both halves are quotable, and the "audited" half turns
out to be stated in the source rather than inferred.

**Two mappings still required, each its own contradiction:** "Super Admin" here is the
**client's**, `Role.client`, not the platform `Role.super_admin` (C22, now settled by RBAC
§5 and §7.1). "HR Manager" maps to **both** `recruitment_manager` and `hr_manager` (C23).

**Repo state.** `review_dispositions` exists (`backend/app/models/hiring.py:247-266`) with
zero readers and zero writers. Its vocabulary is fixed at `:278` as
`cleared | escalated | overridden | rejected`, and CLAUDE.md records `decided_by` as
`ON DELETE RESTRICT`. **Neither the RBAC nor the Dashboard document defines a disposition
vocabulary**, so the repo's four stand. Do not invent a fifth.

**Enforcement:** `ENFORCEMENT-PENDING`. A matrix case per role, plus an assertion that a
Super Admin disposition writes an audit row typed as an override, which is §7.5's own
requirement rather than an addition.

---

## C8. The 39/40 aspect discrepancy: RESOLVED AGAINST THE REPOSITORY

**Source A, spec-doc5 (twice):** line 7, *"the 39-aspect candidate profile form"*; line 81,
*"Candidate completes the 39-aspect profile form → Vaada begins"*.

**Source B, spec-doc6 C8 quoting the Dashboard document:** a *"40-aspect questionnaire"*.

**Source C, the Runbook:** **silent.** `grep -niE "39[- ](aspect|question)|40[- ](aspect|question)|validation aspects" "Readypick Hiring Philosophy.md"` returns **zero hits**. The Runbook, the rank-2 authority, does not mention an aspect count at all.

**Source D, the implemented form,
`backend/app/services/candidate_profile_form.py`:**

- **25 `FormField` instances**, across **7 `FormSection`s**
  (`grep -c "FormField(" backend/app/services/candidate_profile_form.py` returns 25;
  sections named `personal`, `education`, `experience`, `compensation`, `documents`,
  `resume`, `declaration`).
- **20 distinct `display_no` values**: `1`, then `20` through `35`, then `37`, `38`, `39`.
  **There is no 36.** (`grep -oE "display_no=[0-9]+" ... | sort -u`)
- The module's own docstring at `:18-21` states the source questionnaire's numbering:
  *"the source questionnaire numbers its items 1 and 20-39, with the education table
  occupying 2-19 and no item 36."*

### The real number

Arithmetic on the questionnaire's own numbering: `1` (1 item) + `2..19` (18 items, collapsed
into the single `education_table` field) + `20..35` (16 items) + `37..39` (3 items)
= **38 numbered aspects**, implemented as **25 machine fields** in **7 sections**.

**Neither 39 nor 40 is the count of anything.** 39 is the **highest display number**, which
is almost certainly where spec-doc5 got it: someone read the last item's label rather than
counting, and 36 is missing. 40 is a legacy PRD label with no arithmetic behind it.

**Both documents are wrong.** spec-doc6 C8 predicted one of them would be; both are.

### Where the wrong number is repeated in the code

The string "40" is repeated as fact in at least 20 places, including the module's own
opening line:

```
backend/app/services/candidate_profile_form.py:1   "the 40 validation aspects"
backend/app/api/portal.py:108,109,146,182,580      "the 40-aspect questionnaire"
backend/app/api/candidates.py:753                  "completes the 40-aspect questionnaire"
backend/app/api/jobs.py:1279                       "the 40 aspects are a profile form now"
backend/app/models/candidate.py:42,57,103,104      "The 40 validation aspects"
backend/app/schemas/candidates.py:38               "resume + 40 aspects"
backend/app/schemas/portal.py:93                   "The 40 aspects minus..."
backend/app/services/capabilities.py:24            "# 40-aspect + verification"
backend/alembic/versions/0015_candidate_profile_form.py:1
```

**Resolution (rank 6, the repository itself, per spec-doc6 §0.1 item 6 and §8.2's
instruction to "resolve against the Runbook and the actual implemented form").**

- The authoritative count is **38 questionnaire-numbered aspects / 25 implemented fields /
  7 sections**, and it should be stated that way rather than as one number, because "38"
  and "25" answer different questions.
- **spec-doc5's "39" is wrong. The Dashboard document's "40" is wrong.** Correct both.
- **Do not implement a second form.** spec-doc6 §8.2 says so explicitly and it is the right
  call: `backend/app/services/candidate_profile_form.py` is already the single source of
  truth, served to the form so the form and the report cannot drift, and CLAUDE.md pins it
  as a fixed Python constant, never LLM-generated, never client-editable.
- Sweep the 20 stale "40" comments. They are documentation, not behaviour, so this is a
  comment fix, not a migration.

**Enforcement.** A structural invariant already exists at
`backend/app/services/candidate_profile_form.py:426`:
`assert len(ALL_FIELDS) == sum(len(section.fields) for section in FORM_SECTIONS)`.
Tests derive from `len(ALL_FIELDS)` rather than hardcoding a count
(`backend/tests/test_functional_assessment.py:456`,
`backend/tests/test_validation_answers.py:24,42`), which is why the wrong number in the
comments never broke anything. **`ENFORCEMENT-PENDING`:** add a test pinning
`len(ALL_FIELDS) == 25` and `len(FORM_SECTIONS) == 7`, so the next person who "fixes" the
form to match a document's 40 gets a red test instead of a silent second form.

---

## C9. Pre-Screen Grade A/B/C/Hold versus "AI Score"

**Source A, spec-doc6 C9 and §4.4:** the Dashboard's Pre-Screen Grade is A / B / C / Hold.

**Source B, spec-doc5 and the repository:** Yukti's resume-stage output is called the
"AI Score".

**Resolution (rank 3).** *"They are the same artifact; named grade is the product surface,
consistent with named-grades-only."*

**Repo state and a real conflict spec-doc6 does not flag.** The repository's resume-stage
output is **not** A/B/C/Hold. It is the four-grade scale
(`backend/app/services/rating.py:42-45`: Highly Matching, Matching, Moderately Matching,
Not Matching), rendered as `ai_score` at `backend/app/api/assessments.py:764` and pinned by
CLAUDE.md as the product's one rating scale. Introducing A/B/C/Hold makes **two** grade
vocabularies on the same screen: an A/B/C/Hold in column 3 and a "Highly Matching" in the
PRISM Report, for the same candidate.

**Recommendation:** keep the four-grade names for column 3 and drop A/B/C/Hold, unless the
owner specifically wants the letter grades. A letter grade is also a closer cousin of a
number than a word is, which cuts against the same rule D8 is carving an exception out of.
Flag for the owner alongside C1/C19.

**Enforcement:** `ENFORCEMENT-PENDING`. Whichever vocabulary wins, pin it in one module the
way `rating.py` already is, and assert no second scale exists (see C19 for why that
assertion is currently false).

### AMENDMENT (rev 2): the Dashboard's real vocabulary is worse than A/B/C/Hold

Column 3 (`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:91-96`) does specify
**A / B / C / Hold**. But Column 4 (`:118-152`) introduces a **separate five-band
vocabulary with its own numeric cut-points**:

> **Band label** (one of: Ready to Pick, Strong / Ready to Pick / Consider with Reservations / Not Recommended / Under Review)

| Band | Cut-point |
|---|---|
| Ready to Pick, Strong | score >= 85, confidence high |
| Ready to Pick | 72 to 84, confidence high |
| Consider with Reservations | 60 to 71, confidence moderate/low |
| Not Recommended | < 60 |
| Under Review | no number; D4 below the integrity floor |

**The product now has three grade vocabularies and four sets of cut-points:**

| Vocabulary | Values | Cut-points | Where |
|---|---|---|---|
| Four-grade scale | Highly Matching, Matching, Moderately Matching, Not Matching | 90 / 75 / 60 | `backend/app/services/rating.py:42-45,83-88` |
| Tier enum (a defect) | same four words, middle two swapped | **90 / 70 / 50** | `backend/app/services/tiers.py:16-24`, see C19 |
| Pre-Screen Grade | A / B / C / Hold | unstated | Dashboard Column 3 |
| Ready Pick Score band | five labels above | **85 / 72 / 60** | Dashboard Column 4 |

**Resolution (rank 4 for the dashboard surface, rank 5 for the delivered report; they do not
actually collide).** These are different artefacts and may legitimately differ:

- The **PRISM Report** keeps the four words, unchanged (D8, and CLAUDE.md's standing rule).
- The **dashboard** may use its own band labels, because D8 already rules the dashboard a
  separate triage surface.
- **A/B/C/Hold in Column 3 is the one to challenge.** It adds a fourth vocabulary for the
  same candidate on the same screen, and a letter grade is a closer cousin of a number than
  a word is, which cuts against the rule D8 is already carving one exception out of.
  Recommend the four words in Column 3. Owner's call.

**What must not happen** is a fifth set of cut-points appearing in code. Every threshold
here belongs in `runbook_data/` with a source citation, per spec-doc6 §10.1 rule 5, and the
Dashboard's 85/72/60 must be reconciled against the Runbook's band boundaries before it is
implemented, not after.

---

## C10. "Ready Pick Profile" versus "PRISM Report" used loosely

**Source A, spec-doc6 C10 and §8.2:** *"'Ready Pick Profile' is the dashboard's evidence
panel. 'PRISM Report' is the delivered document. They are different artefacts with
different rules. Do not let the codebase use the names interchangeably; pick distinct types
and enforce with the type system."*

**Resolution (rank 3).** Distinct artefacts, distinct types, distinct rules.

**Repo state.** No conflation exists yet, because the Ready Pick Profile does not exist. The
PRISM Report does: `backend/app/api/assessments.py:753` returns `FunctionalReportOut`, and
the header is pinned by `backend/tests/test_prism_report.py`. CLAUDE.md already draws the
`Evaluation` (working) versus `functional_skills_reports` (delivered artifact) line, which
is the same distinction one layer down.

**A related collision that does exist today** and will become this one: two classes named
`CompanyDNA`, at `backend/app/models/hiring.py:88` (ORM row) and
`backend/app/services/hiring/company_dna.py:725` (compiled in-memory artifact). Same
concept, same name, different things. Fix before either goes live.

**Enforcement:** `ENFORCEMENT-PENDING`. A `mypy --strict` pass over both packages plus a
naming test.

---

## C11. Job lifecycle states versus candidate pipeline stages

**Source A, RBAC §17** (`docs/spec/RBAC_SPECIFICATION.md:839-874`), the canonical job
lifecycle, **eight states, not the five spec-doc6 quotes before its ellipsis**:

> DRAFT (Recruiter creates/generates JD) -> SENT_TO_HIRING_MANAGER (Hiring Manager reviews and edits) -> IN_REVIEW (Hiring Manager completes role definition) -> FINALIZED (Recruiter publishes) -> PUBLISHED -> CANDIDATE_APPLICATIONS -> HIRING_PROCESS -> CLOSED / ARCHIVED

and, crucially, the escape clause at `:872-874`:

> The implementation MAY use different internal status names, but the semantic states MUST preserve these distinctions.

**Source B, the Dashboard Specification, Column 8** (`:238`), the candidate pipeline stage,
six values:

> **Current stage label:** Applied / Screening / Shortlisted / Interview / Offer / Closed (read-only display)

**Resolution (rank 1 and rank 4 agree, they are different enums on different entities).**
Distinct type names `JobLifecycleState` and `CandidatePipelineStage`, never a shared table
or enum, plus a test that no code path assigns one to the other.

### Verdict against the repository: not conflated, but three job vocabularies exist and none is §17's

Already separate, which is the good news. What is wrong is that the job side is spread
across three unrelated mechanisms:

| Mechanism | Site | Values |
|---|---|---|
| Approval FSM (`JobStatus`) | `backend/app/models/enums.py:36-43` | `draft`, `requested`, `recommended`, `approved`, `ratified` |
| Assessment setup gate | `backend/app/models/job.py:99`, a bare `String(40)`, **not an enum** | `questions_pending_review`, `ready_for_candidates` |
| Posting window | `backend/app/services/job_posting.py:74`, computed at read time, never stored | `scheduled`, `active`, `grace`, `expired` |

Publication is expressed as `ratified_at IS NOT NULL` (`backend/app/api/jobs.py:1380`), a
timestamp rather than a state.

**§17's escape clause is the resolution.** The implementation may keep different internal
names provided the semantic distinctions survive. Two of §17's eight states have no
semantic counterpart today and must be added: **SENT_TO_HIRING_MANAGER** and **FINALIZED**.
§20 (`:912-930`) makes FINALIZED mandatory and explicit:

> Finalization MUST be an explicit state transition.
>
> Finalization MUST record: User who finalized it / Timestamp / Relevant version / Relevant hiring criteria version

`JobStatus.ratified` can carry PUBLISHED, and `posting_status` distinguishes
CANDIDATE_APPLICATIONS from CLOSED. `IN_REVIEW` maps onto the existing SWOT-intake window.
Write the mapping down once, in one module, and test it against §17's eight names.

**Candidate pipeline, file:line.** `PipelineStatus` at `backend/app/models/enums.py:73-104`,
mirrored at `backend/app/services/hiring_pipeline.py:42-56`. **Eleven** live values plus one
retained legacy synonym: `applied`, `assessment_invited`, `assessment_in_progress`,
`assessment_completed`, `shortlisted`, `rejected`, `interview_scheduled`,
`interview_completed`, `offer_extended`, `joined`, `hold`, and legacy `offered`.

**The eleven-to-six mapping, now derivable and no longer a default.** The Dashboard's six
are a display projection over the eleven, defined once, server-side:

| Dashboard stage | Pipeline values |
|---|---|
| Applied | `applied` |
| Screening | `assessment_invited`, `assessment_in_progress`, `assessment_completed` |
| Shortlisted | `shortlisted` |
| Interview | `interview_scheduled`, `interview_completed` |
| Offer | `offer_extended`, legacy `offered` |
| Closed | `joined`, `rejected` |

**`hold` has no home, and the Dashboard confirms rather than resolves this.** Column 8's
label list has no Hold, while its control description (`:239`) reads *"lets recruiter
advance **or hold** the candidate"*. So the document treats hold as an **action** and not a
**stage**. The safe reading, and the one to implement: render `hold` as its own visible
state rather than folding it into Closed, because folding it hides a live candidate.
Surface it to the owner as a one-line dashboard question.

**Do not collapse the eleven to six.** `assessment_invited` versus `assessment_in_progress`
is exactly what distinguishes an unopened invitation from a stalled assessment, which
CLAUDE.md records as the case a recruiter most wants to see. Six is a view, eleven is the
model.

**No longer `RESOLVED-BY-DEFAULT`.** Both enums are now fully specified and §17 explicitly
authorises the internal-name mapping.

**Enforcement:** `ENFORCEMENT-PENDING`. `JobLifecycleState` and `CandidatePipelineStage` do
not exist yet. Add the no-cross-assignment test now, before the dashboard makes the mistake
possible, and a test that the eight §17 semantic states each resolve.

---

## C12. Seven Terraform modules with no way to route traffic

**Source A, spec-doc5 §D.4:** exactly seven modules (network, ecs, rds, s3, elasticache,
ecr, secrets).

**Source B, spec-doc6 C12 and §13.2:** ECS services cannot receive traffic without a load
balancer, certificate and DNS.

**Resolution (rank 3).** spec-doc6 §13.2 adds `alb/`, `acm/`, `dns/` and an optional,
variable-disabled `waf/`. spec-doc5's list is superseded in scope.

**Repo state.** `ls infra/modules` to confirm the seven; the four new ones do not exist.
This one is straightforwardly correct and needs no owner decision.

**Enforcement:** `ENFORCEMENT-PENDING`. `terraform validate` plus the offline plan profile
in §13.3.

---

## C13. "Recruiter publishes. Period." versus HR Manager Publish YES\*

**Source A, RBAC §9.6** (`docs/spec/RBAC_SPECIFICATION.md:508-550`):

> The Recruiter is the designated operational publisher.
>
> [...] The normal operational rule is: Recruiter publishes the job. Period.
>
> The Super Admin is an administrative exception because the Super Admin has ultimate authority and can override role restrictions.

**Source B, RBAC §24** (`:1024`): `| Publish job | YES | YES* | YES | NO | NO |`.

**Resolution (rank 1, resolved internally).** Recruiter is the operational publisher. Super
Admin publishes by override, per §9.6's own exception clause and §7.5. HR Manager's cell is
`YES*`, and the asterisk (`:1033-1034`) means *"intentionally conservative and may require
an explicit future product decision"*.

**spec-doc6 misstates this, and it is worth correcting precisely.** spec-doc6 C13 resolves
it as *"HR Manager and Super Admin publish only as an audited exception."* **§9.6 names only
the Super Admin as the exception.** The HR Manager appears nowhere in §9.6's exception
clause; its authority comes solely from an asterisked matrix cell that the document itself
flags as provisional. Treating a flagged-for-decision cell as a settled "audited exception"
grants an authority the prose does not.

**Implement the restrictive reading** (spec-doc6 §20): Recruiter publishes; Super Admin
publishes by audited override; **HR Manager's publish capability is withheld pending the
explicit product decision §24's footnote asks for**, and the withholding is recorded rather
than silent. Logged as a spec-doc6 citation defect in C38.

**Repo state.** `publish_job` is a real capability (`backend/app/services/capabilities.py:38`),
seeded broadly by `backend/alembic/versions/0017_seed_new_capabilities.py` and
`0031_seed_full_team_access.py:73`. There is no distinction between "by right" and "by
audited override": a capability is granted or not. Implementing C13 needs an audit-typed
override path, which does not exist and is the same mechanism C7 needs.

**Enforcement:** `ENFORCEMENT-PENDING`. Build the audited-override path once and use it for
C7 and C13 both, not twice.

---

## C14. Real personal names as sample data

**Source A, spec-doc6 C14:** the Dashboard sign-off names "Manju H" and uses a real person
as sample data.

**Source B, spec-doc6 §10.2:** *"Placeholder identities: the Dashboard document's example
candidate ('Manju H') and any other placeholder personal names must not survive into code,
fixtures, seed data or screenshots. Replace with clearly synthetic fixtures."*

**Resolution (rank 3).** Synthetic fixtures everywhere.

**Repo state: "Manju H" does not appear.** `grep -rniE "Manju H\b" backend frontend` returns
nothing. **But the owner's real email address does**, in code and fixtures:

```
backend/app/core/config.py:138          owner_email: str = "manjuchro@gmail.com"   <- a DEFAULT
backend/app/scripts/seed_dev_data.py:6,68,338
backend/app/services/owner.py:4
backend/tests/test_otp.py:32,245  backend/tests/test_owner.py:13,27
backend/tests/test_email_delivery.py:54
```

The `config.py:138` case is different in kind from the rest: it is an operational default,
not sample data, and the owner invariant genuinely needs a value. It should still be
environment-driven with no default, because a default means a fresh deployment silently
makes that address the platform super admin.

**Enforcement:** `ENFORCEMENT-PENDING`. A CI grep for real-looking personal email addresses
in `backend/tests`, `backend/app/scripts` and any fixture directory.

---

## C15. "Prism Report Pending" as a row-level status

**Source A, spec-doc6 C15:** the Dashboard references a "Prism Report Pending" state in its
rationale, implying the PRISM Report is a row-level status.

**Resolution (rank 3).** The row's pending state refers to the **Ready Pick Profile**, not
the delivered PRISM Report. Clarify naming in the dashboard document.

**Repo state.** There is a real distinction to protect here that CLAUDE.md already records:
*"the transcript exists from the first answer and the report does not exist until the
assessment finishes, so hanging it off the report would make the stalled-assessment case,
the one a recruiter most wants, unreachable."* The same argument applies: a row-level
pending state must be keyed on the application link, not on the report's existence, or the
stalled case renders as nothing at all.

**Enforcement:** `ENFORCEMENT-PENDING`.

---

## C16. The Dashboard specification does not mention Interview Managers: CONFIRMED

**Source A, measured.** `grep -ci "interview manager"
docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md` returns **0**. `grep -ci "recruiter"`
returns **19**. spec-doc6's C16 premise is exactly right.

**Source B, RBAC §13**, which defines the role in full and is no longer unavailable:

- **§13.1 Cardinality** (`docs/spec/RBAC_SPECIFICATION.md:689-693`) and §5 (`:229`): *"A job MAY have multiple Interview Managers."*
- **§13.3 Access** (`:705-719`) and §29 (`:1152-1155`): *"Interview Managers can see the candidate information and intelligence required for their participation."*
- **§13.4 Team Review** (`:720-732`): add remarks, comments and observations; contributions must identify author and timestamp.
- **§13.5 Restrictions** (`:734-760`), a closed list: MUST NOT edit the JD, define Must-Have skills, define Nice-to-Have skills, define behavioural competency requirements, edit job-role philosophy, edit SWOT analysis, edit hiring rubrics, publish jobs, modify another user's review, modify candidate hiring-stage status, or shortlist/reject candidates. *"These restrictions apply unless a future explicit permission specification changes them."*
- **§24** (`:1011,1025,1029,1030,1031`): View all company jobs **Scoped**; View candidates, reports and ratings **YES (scoped)**; Add Team Review remarks **YES**; everything else NO.
- **§3** (`:128-131`): *"An Interview Manager may view candidate reports. That does NOT allow the Interview Manager to modify the JD or candidate hiring status unless a future specification explicitly grants that permission."*

**Resolution (rank 1 supplies what rank 4 omits).** Extend the dashboard for the Interview
Manager: **read-only on scoped jobs, plus Team Review.** That is exactly spec-doc6 C16's
resolution, and it is now backed by quotable text rather than inference.

**No longer `RESOLVED-BY-DEFAULT`.** Revision 1 listed seven unknowns; §13 answers five of
them:

| Revision 1 question | RBAC answer |
|---|---|
| How many per job? | Many (§13.1, §5) |
| Which jobs do they see? | Scoped to their job (§24, §23, §3) |
| May they read candidate reports? | Yes, scoped (§24) |
| May they see others' remarks? | Yes; they may not silently alter them (§29) |
| May they move stages or shortlist? | No (§13.5, §24) |
| How are they created and invited? | **Still unspecified.** §13 does not say |
| What happens to their remarks on removal? | **Still unspecified.** §29 requires the author be preserved, which implies retention |

The two remaining unknowns are narrow and do not block the role. Implement retention
(§29 requires author preservation on every remark) and treat invitation as tenant-user
creation until told otherwise.

**Repo state.** `grep -rn "interview_manager\|Interview Manager\|InterviewManager"
backend/app frontend` returns **zero hits**. The role does not exist. It also depends on
the same missing assignment table as C5: §13.1 and §23 both scope an Interview Manager to a
job.

**Enforcement:** `ENFORCEMENT-PENDING`.

---

## C17. Identifier formats: RESOLVED AGAINST THE REPOSITORY

**Source A, spec-doc6 C17:** *"Candidate System ID format (`JSRS-Y4BN-8HGX`) versus the
public job ID format (`3252463dfbg43t4hfb`). Define both formats explicitly, confirm neither
is guessable-to-authorizing, and confirm RBAC §33 (obscurity is not authorization) is
enforced regardless."*

**Resolution (rank 6, the repository).**

### Candidate System ID: implemented, and it matches

`backend/app/services/reference_code.py`. Format `COMPANY-JOB-CANDIDATE`, three groups of
four characters separated by hyphens (`SEGMENT_LENGTH = 4` at `:63`, `SEPARATOR = "-"` at
`:69`). Alphabet is Crockford base32 **excluding I, L, O and U** (`_ALPHABET` at `:58`), so
`JSRS-Y4BN-8HGX` is a well-formed example of exactly this scheme.

Each segment is `HMAC-SHA256(jwt_secret, domain_prefix + id)` truncated to 20 bits
(`_segment` at `:83-101`), with per-position domain separation (`_PREFIX_COMPANY`,
`_PREFIX_JOB`, `_PREFIX_CANDIDATE` at `:71-73`) so a value computed for one position cannot
be replayed into another.

**Is it read back as authorization? No, and the module says so twice.** `:35-37`:
*"It is a LABEL, though, never a permission: nothing anywhere authorises on this value, and
RLS remains the tenant boundary."* `:145`: *"Shape check only. There is no `decode`: the
code is one-way by design."* The only consumer is display
(`backend/app/api/assessments.py:759`). CLAUDE.md restates it: *"It identifies a row and
authorises nothing; nothing may ever read it back as permission."*

### Public job ID: NOT implemented as spec-doc6 describes

There is no `3252463dfbg43t4hfb`-style identifier. The public identifier is the **raw job
UUID**. The URL is `{frontend_url}/apply/{job_uuid}`
(`backend/app/api/jobs.py:97-105`) and the API route is `GET /jobs/public/{job_id}`
(`:1353`), unauthenticated via `Depends(get_public_db)`.

**Is it read back as authorization? No.** `backend/app/api/jobs.py:1380-1391` refuses unless
**all three** hold: `ratified_at IS NOT NULL`, `archived_at IS NULL`, and
`job_posting.public_link_active(posting_start_date, posting_end_date)`. Possessing the UUID
is necessary and not sufficient. 404 is returned for every failure, never 403, and the
docstring at `:1362` states the reason: *"404 for any unpublished or unknown id (never
reveal existence)."*

### RBAC §33 has a documented history here worth quoting

`docs/adr/0001-assessment-invitation-tokens.md` records the exact defect §33 warns about
being found and fixed on this product: *"Nothing bound the link to the person it was sent
to. The id was the whole of the authorization story, and it never expired."* The fix was a
signed JWT under its own audience (`pickready:assessment-invite`) bound to both the
application link and the invited email, with a fixed check order.

**Recommendation:** do **not** introduce an 18-character public job id. It would be shorter,
guessable, and strictly weaker than a UUID plus three window checks.

**Enforcement:** shape is pinned by `is_wellformed` (`reference_code.py:139-151`). The
public-route refusals are enforced at `backend/app/api/jobs.py:1380-1391`.
**`ENFORCEMENT-PENDING`:** a test asserting no route accepts a reference code as an
authorization input, and the ALB listener-rule assertion spec-doc6 §13.2 requires.

### AMENDMENT (rev 2): spec-doc6 misread §33, and the correction strengthens the finding

**The candidate System ID is confirmed by the Dashboard itself.** Column 1
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:53`): *"System ID (e.g. `JSRS-Y4BN-8HGX`),
monospace, 11px, muted color"*. That is exactly the shape
`backend/app/services/reference_code.py` produces: three groups of four Crockford base32
characters. **Already implemented and already correct.**

**`3252463dfbg43t4hfb` is not a format specification.** spec-doc6 C17 calls it *"the public
job ID format"* and asks that both formats be *"defined explicitly"*. It appears twice in
RBAC, both times as an **example of an identifier somebody might already know**, never as a
format:

RBAC §15 (`docs/spec/RBAC_SPECIFICATION.md:787-791`), under the heading "Example":

> ```
> https://readypick.ai/jobs/3252463dfbg43t4hfb
> ```

RBAC §33 (`:1295-1313`), where the whole point is the opposite of a format:

> Knowing:
> ```
> /job/3252463dfbg43t4hfb
> ```
> MUST NOT be sufficient to gain access to the job.
>
> [...] This applies even if public identifiers are difficult to guess.
>
> Obscurity is NOT authorization.

RBAC §21 (`:940-942`) says only that on publication *"A public job identifier is available.
The public URL becomes usable."* **No format is specified anywhere.**

**SUPERSEDED IN PART BY C44.** This entry originally concluded that the repository's raw job
UUID satisfies §15 and is stronger than an 18-character token. That conclusion is right about
**strength** and wrong about **sufficiency**: §15 is not asking for a format, but it *is*
asking for a **separate identifier**, and this codebase does not have one. `grep -rn
"public_job_id" backend/app --include=*.py` returns nothing. Read C44 for what actually
follows from that, and do not read this entry as saying the job identifier merely needs
documenting.

**Correction to revision 1 and to spec-doc6 both:** the 404-not-403 rule is **not in §33**.
§33 requires that the backend *"verify the user's tenant and authorization relationship to
the resource"* and never mentions a status code. spec-doc6 §9.1's *"Cross-tenant reads
return 404, never 403, so existence is not disclosed (RBAC §33)"* attributes a rule its
source does not contain. The rule is still right, and the repository already argues for it
independently (`backend/app/api/jobs.py:1362`: *"never reveal existence"*), but it is
spec-doc6's rule, not RBAC's. Logged in C38.

---

## C18. The under-15% override target as pressure on recruiters

**Source A, spec-doc6 C18:** the Dashboard targets recruiter deviation from the Ready Pick
Score at under 15%.

**Source B, spec-doc6 §8.2:** *"Implement the measurement; do not implement any nudge,
warning, friction or visual discouragement when a recruiter disagrees with the score. A
recruiter's independent judgment is data, and a target that quietly discourages
disagreement destroys the calibration signal it exists to measure."*

**Resolution (rank 3).** Measure, never nudge. State the constraint in `PRODUCT.md`.

**Repo state.** No override-rate measurement exists. `calibration_records`
(`backend/app/models/hiring.py:293-315`) is the intended substrate and has zero readers and
zero writers.

**Enforcement:** `ENFORCEMENT-PENDING`. This is the rare rule best enforced by a **negative**
test: assert the dashboard payload for a divergent decision carries no warning, confirmation
step or styling variant. A rule with only a `PRODUCT.md` sentence behind it will be
reintroduced by whoever builds the calibration view.

---

# FOUND IN THIS PHASE (C19 to C29)

## C19. TWO CONTRADICTING FOUR-GRADE SCALES ARE BOTH LIVE AND BOTH TESTED

**Severity: real defect, not a documentation conflict.**

**Source A, CLAUDE.md (2026-07-30):** *"There is ONE rating scale, it has FOUR grades, and
it lives in `services/rating.py`. … It replaced the product's two parallel five-label
scales … which had to be kept in step by hand in two modules and gave a reader no way to
know that a 'High' and a 'Matching' meant the same thing. `matching.matching_label` and
`functional_assessment.rating_label` are now thin aliases over it and must stay that way.
The cut-points are unchanged (90 / 75 / 60)."*

**Source B, the repository.** A second scale survives, with **different cut-points and the
middle two bands swapped**:

| Scale | Site | Cut-points | Order |
|---|---|---|---|
| `rating.grade_for_percent` | `backend/app/services/rating.py:83-88` | 90 / 75 / 60 | Highly → **Matching** → **Moderately** → Not |
| `tiers.assign_tier` | `backend/app/services/tiers.py:16-24` | **90 / 70 / 50** | Highly → **Moderately** → **Matching** → Not |

Both are live. `assign_tier` is called at `backend/app/services/matching.py:1775`
(`link.tier = assign_tier(link.match_score)`) and the result is serialised to clients as
`tier` at `backend/app/schemas/matching.py:57` and `backend/app/schemas/candidates.py:116`.

Both are pinned by tests, so neither can be quietly deleted:
`backend/tests/test_tiers.py:29` asserts `assign_tier(75.0) == Tier.moderately_matching`,
while `rating.grade_for_percent(75)` returns `"Matching"`.

**Worked consequence.** For one candidate with an internal score of 75, the tier column
says **Moderately Matching** and the report says **Matching**. At 65 it is inverted the
other way: the tier column says **Matching** and the report says **Moderately Matching**.
The two middle bands are literally swapped across the whole 60-to-75 range.

The enum's own comments carry the second set of cut-points too, at
`backend/app/models/enums.py:65-69`.

**Resolution (rank 5, CLAUDE.md and spec-doc5's product contract, and rank 3 via spec-doc6
§10.1 rule 12, "one implementation per concept").** `rating.py` is the one scale.
`tiers.assign_tier` must become a thin alias over it, exactly as `matching_label`
(`backend/app/services/matching.py:688-696`) already is, and
`backend/tests/test_tiers.py` must be rewritten to the 90/75/60 boundaries. The `Tier`
enum's comments must be corrected.

**Note the boundary rule survives either way.** CLAUDE.md rule 8 ("tier boundaries are
inclusive upward, implement top-down") is honoured by both implementations; it is the
thresholds and the ordering that disagree, not the boundary discipline.

**`RESOLVED-BY-DEFAULT (CONTRADICTION-C19)` on one point:** whether existing `tier` column
values must be backfilled. A stored `Tier.moderately_matching` written under the old
thresholds means something different from one written after. Recommend backfilling as part
of the D2 legacy reset (spec-doc6 §6), which is already purging derived machine ratings.

**Enforcement:** `ENFORCEMENT-PENDING`. Add a test asserting `assign_tier` and
`grade_for_percent` agree at every integer 0 to 100. That test would have caught this the
day the second scale was left behind.

---

## C20. spec-doc6 D2 relies on gate G1, which is not on any live path

**Severity: BLOCKING for Phase 6.**

**Source A, spec-doc6 D2:** *"Existing jobs are not unpublished and applications are not
interrupted. Their old scorecard is archived and the job is marked as requiring
re-definition. **No new mechanism is needed to enforce this: gate G1 already blocks
evaluation without an approved scorecard.** Use it. Do not build a second enforcement
path."* Repeated at §4.3 (*"Gate G1 is enforced here"*), §6.2 (*"G1 does the
enforcement"*) and in the §17 acceptance list.

**Source B, the repository.** G1 exists and is unreachable.

```bash
grep -rn "scorecard_gate" backend/app backend/tests --include="*.py" | grep -v __pycache__
```

- Defined: `backend/app/services/hiring/gates.py:121`.
- Called from exactly one place in `app/`: `backend/app/services/miti/pipeline.py:290`.
- `backend/app/services/miti/pipeline.py` has **zero importers** in `backend/app/api/` and
  `backend/app/workers/`. Its only non-test importer anywhere is
  `backend/app/scripts/worked_example.py:34`, a standalone script.

The same holds for G2 (`gates.py:170`, called at `pipeline.py:334`), G3 (`:215`, `:350`) and
G4 (`:273`, `:381`).

**Resolution (rank 6, the repository).** spec-doc6 D2's premise is false. G1 blocks nothing
today. It must be **wired** in Phase 3 before Phase 6's legacy reset can rely on it, or
Phase 6 will archive scorecards and mark jobs as requiring re-definition while evaluation
continues to run against them unimpeded.

**Sequencing consequence:** spec-doc6 §16 already places Phase 3 before Phase 6, so the
order is right. What is wrong is the claim that no work is needed. Record it so nobody skips
the wiring on the strength of D2's sentence.

**Enforcement:** `ENFORCEMENT-PENDING`. spec-doc6 §17 already asks for it:
*"Gates G1 to G4 block on the live path, each with a test that attempts to bypass it and
fails."* Add an import-reachability assertion too, of the kind
`backend/tests/test_llm_task_routing.py` already uses for model strings: assert that at
least one module under `app/api` or `app/workers` transitively imports
`app.services.hiring.gates`.

---

## C21. Two `gates` modules, two `run_gate` functions, different contracts

**Source A, spec-doc6 §10.1 rule 12:** *"One implementation per concept. Two ontology
implementations, two retry helpers, two S3 wrappers, two ways to resolve a tenant: each is a
defect. Find and merge them."*

**Source B, the repository.**

| Module | Symbol | Signature | Returns | Live? |
|---|---|---|---|---|
| `backend/app/services/hiring/gates.py` | `run_gate` at `:317` | `(name, **kwargs)` | `GateResult` | **No** (see C20) |
| `backend/app/services/agents/gates.py` | `run_gate` at `:644` | `(agent_id, payload)` | `verification.Verdict` | **Yes** |

The second is called from four live sites: `backend/app/services/ppi.py:1025` (Sutra),
`backend/app/services/swot_intake.py:626` (Bodha),
`backend/app/services/functional_assessment.py:1590` (Siddhi),
`backend/app/services/matching.py:1511` (Yukti).

These are genuinely different concepts (evaluation gates G1 to G4 versus per-agent output
verifiers), so merging them would be wrong. **Renaming one is right.** A future reader
grepping `run_gate` gets two unrelated answers and no signal which is which, which is
exactly how a caller ends up importing the wrong one.

**Resolution (rank 3).** Rename `hiring.gates.run_gate` to `run_evaluation_gate` (or rename
`agents.gates` to `agents.verifiers`, which reads truer to what it does). Do not merge.

**Enforcement:** `ENFORCEMENT-PENDING`.

---

## C22. RBAC "Super Admin" is `Role.client`, not `Role.super_admin`: SETTLED

**Severity: this was the highest-stakes finding in revision 1, and the real text settles it
unambiguously.**

**Source A, RBAC §5** (`docs/spec/RBAC_SPECIFICATION.md:209-221`, and the role's own name at `:211`). The role's own name carries
the answer:

> Client Super Admin

**Source B, RBAC §7.1** (`docs/spec/RBAC_SPECIFICATION.md:258-266`):

> Each client organization MUST have exactly one active Super Admin.
>
> There MUST NOT be two simultaneously active Super Admins for the same client organization.
>
> The system MUST provide a controlled mechanism for changing/transferring the Super Admin role when necessary.

**Source C, RBAC §7.2** (`:268-277`):

> The Super Admin is the ultimate authority **within the client organization**.
>
> The Super Admin has access to all **company-owned** data and functionality subject to platform-level security and legal constraints.

**Source D, RBAC §23** (`:998-1000`):

> Organization-wide roles such as Super Admin and HR Manager have broader scope as explicitly defined.

Broader **within the organization**. Cardinality is **per client organization**, not per
platform. Every §7.5 override example is a tenant-scoped hiring action (edit a JD, publish a
job, move candidates through stages). The role is unambiguously tenant-scoped.

**Resolution (rank 1, settled, no default required).** Every RBAC "Super Admin" cell maps to
**`Role.client`** (`backend/app/models/enums.py:9`), whose inline comment already says so:
*"The customer's own Super Admin. Named `client` since the product's first release; the
customer portal calls it Super Admin (spec 29)."*

**It does NOT map to `Role.super_admin`** (`backend/app/models/enums.py:6`), the **platform**
owner, sole holder pinned to `settings.owner_email` (`backend/app/core/config.py:138`,
`backend/app/services/owner.py`). Mapping it there would:

1. grant ReadyPick platform staff tenant-scoped write authority over client hiring data,
   which RBAC §4 (`:163-166`) forbids: *"A user belonging to Client A MUST NOT be able to
   access, infer, modify, delete, or retrieve Client B resources"*, and spec-doc6 D3 calls a
   tenant-isolation violation that *"must be tested as such"*; and
2. break the Provider Portal's read-only-by-absence guarantee that CLAUDE.md records, in the
   same change.

**Two corollaries worth stating, because they follow from §7.1 and are easy to miss:**

- The **cardinality invariant is per tenant**: exactly one active `Role.client` per
  `tenant_id`. A global uniqueness constraint would be wrong and would break the second
  customer onboarded.
- §7.1 requires a **controlled transfer mechanism** for the Super Admin role. Nothing in the
  repository implements one.

**No longer `RESOLVED-BY-DEFAULT`.**

**Enforcement:** `ENFORCEMENT-PENDING`. Two tests: no tenant-scoped capability resolves True
for `Role.super_admin` (mirroring `backend/tests/test_owner.py`'s owner-invariant style), and
a partial unique index on `(tenant_id)` where `role = 'client' AND status = 'active'` that
is proven to fire.

---

## C23. RBAC "HR Manager" maps onto two implemented roles

**Source A, spec-doc6 D3:** *"The HR Manager is the primary broad operational hiring
authority with organisation-wide scope (RBAC §8.1, §8.2)."* Single role.

**Source B, `backend/app/models/enums.py:10-14`:** two roles at the same rank,
`recruitment_manager` and `hr_manager`, with the reason recorded inline: *"`hr_manager`
predates it and ranks alongside it: a role a customer already assigned must not silently
change what its holder can do."* CLAUDE.md restates it: *"Legacy `hr_manager` ranks beside
Recruitment Manager until existing accounts are migrated deliberately."*

**Resolution (rank 5, the existing product contract).** Grant every RBAC "HR Manager" cell
to **both** roles, or migrate `hr_manager` into `recruitment_manager` as a deliberate,
separately reviewed change. Granting to only one silently removes authority from existing
accounts, which is the exact failure the inline comment exists to prevent.

**Enforcement:** `ENFORCEMENT-PENDING`. Every RBAC conformance case for HR Manager runs
twice, once per role.

---

## C24. CLAUDE.md states the Runbook does not exist; it does now

**Source A, `CLAUDE.md:19-21`:** *"**`Readypick_Hiring_Philosophy.md` (RPN-PHIL-001) IS NOT
IN THIS REPOSITORY OR ANYWHERE ON THE MACHINE.** … It was searched for exhaustively."*
`GAP_MATRIX.md:12-45` says the same at length.

**Source B:** `Readypick Hiring Philosophy.md` is present at the repository root,
**4094 lines**, and every section number spec-doc6 cites resolves (verified: §3.5 at line
378, §15 at 1475, §16 at 1481, §17 at 1572, §18.3 at 1676, §18.4 at 1686, §18.5 at 1701,
§19 at 1714, §56 at 3379, §57.1 to §57.6 at 3447 to 3471, §58 at 3476, §59 at 3492, §60 at
3530).

**Resolution (rank 6).** `CLAUDE.md`'s §0 block and `GAP_MATRIX.md` §0 are **stale** and must
be corrected in Phase 11's CLAUDE.md update. The instruction *"Grep for that string before
treating any of it as settled"* stays good advice; the premise behind it does not.

**Two secondary corrections while updating:**

1. The filename differs. Every document writes `Readypick_Hiring_Philosophy.md` with
   underscores. The file on disk is `Readypick Hiring Philosophy.md` with spaces. Any
   tooling that opens it by the underscored name (including the §2.2 parity test) will fail.
2. **The file is untracked in git** (`git status` shows `?? "Readypick Hiring Philosophy.md"`).
   The rank-2 authority for the whole build is not under version control. spec-doc6 D4
   requires committing it.

**Enforcement:** the §2.2 parity test, once written, is the enforcement. It must open the
file by its real name.

---

## C25. spec-doc6 says 9 `ASSUMPTION (RUNBOOK-GAP` sites; there are 6

**Source A, spec-doc6 §2.3:** *"`grep -rn "ASSUMPTION (RUNBOOK-GAP"` finds the 9 sites the
previous phase marked."* §17 requires all 9 resolved.

**Source B:**

```bash
grep -rn "RUNBOOK-GAP" --include="*.py" --include="*.md" . | grep -vE "node_modules|\.venv|__pycache__|\.next"
```

**Six code sites**, one each in:

```
backend/app/services/hiring/company_dna.py:48        (§16)
backend/app/services/hiring/department_models.py:41  (no § cited)
backend/app/services/hiring/evidence_graph.py:42     (Part VI)
backend/app/services/hiring/ontology.py:48           (§58)
backend/app/services/hiring/swot_quality.py:69       (§18.3)
backend/app/services/miti/triangulation.py:137       (§57.4)
```

Plus two prose references that are not code sites: `CLAUDE.md:21` and `GAP_MATRIX.md:40`.

**Resolution (rank 6).** Reconcile **six**, not nine. Report the discrepancy rather than
manufacturing three more.

**A seventh site exists in substance without the marker.**
`backend/app/services/hiring/situations.py:70` records: *"The arrows are ORDINAL and the
Runbook attaches no multiplier to them"*, and `:109-128` chooses multipliers the Runbook does
not state. Verified against the Runbook: §18.4 (lines 1686 to 1700) gives a table whose
"Weight consequence" column contains arrows only (`D3 ↑↑, D1 ↑`), with no numbers anywhere.
That is a genuine `STILL_UNSPECIFIED` and should be marked `RUNBOOK-AMBIGUITY (§18.4)` so
the count is honest at seven.

**Enforcement:** spec-doc6 §17's *"Zero `ASSUMPTION (RUNBOOK-GAP` markers remain"* is a
grep, which is adequate.

---

## C26. The audit schema cannot record what spec-doc6 §9.3 requires

**Source A, spec-doc6 §9.3:** thirteen required fields, including *"actor role at time of
action … previous state, new state … job/application/candidate context, request metadata,
and for agent actions both the human principal and the executing agent."*
§4.1 calls the agent-attribution half *"non-negotiable"* (RBAC §34).

**Source B, `backend/app/models/tenant.py:111-128`.** `AuditLog` has seven columns:
`tenant_id`, `actor_user_id`, `action`, `target_type`, `target_id`, `metadata_json`, `at`.

Six of thirteen are present. Five could ride inside `metadata_json` (an unvalidated JSONB),
which means they are present on some rows and absent on others with nothing detecting the
difference. Two have no home at all:
`grep -n "agent_id\|acting_agent\|principal" backend/app/models/tenant.py` returns nothing.

**Resolution (rank 1, RBAC §34 is the highest-precedence domain there is).** Add
`actor_role`, `previous_state`, `new_state`, `agent_id` and `human_principal_user_id` as
real columns, additively, and keep `metadata_json` for request metadata. Do not settle for
a JSONB convention: spec-doc6 §9.3's invariant test asserts *"every expected audit row
exists with correct previous/new state"*, which is not assertable over an optional dict key.

**Note:** `agent_execution_traces` (`backend/app/models/agent.py:19-27`, migration 0055) is
**not** a substitute. CLAUDE.md pins it as identifiers, counts and timings, never content,
with `_SAFE_STAGE_KEYS` as an allowlist, and *"persisting a trace never fails the run it
describes"*, which is the opposite of an audit guarantee.

**Enforcement:** `ENFORCEMENT-PENDING`. spec-doc6 §9.3's scripted end-to-end invariant test
is the enforcement, and it cannot be written until the job lifecycle exists (C11).

---

## C27. `pickready.probe_llm_models` imports a module that was deleted

**Severity: latent runtime error on a registered Celery task.**

**Source A, spec-doc5 / CLAUDE.md Part B:** *"`llm_capacity.py` … and
`scripts/probe_llm_models.py` are deleted."* `git status` confirms:
`D  backend/app/scripts/probe_llm_models.py`.

**Source B:** the task that imports it is still registered.

```
backend/app/workers/tasks.py:1411   @celery_app.task(name="pickready.probe_llm_models")
backend/app/workers/tasks.py:1435       from app.scripts.probe_llm_models import probe
ls backend/app/scripts/probe_llm_models.py   ->   No such file or directory
```

The import is function-local, so the worker still starts and the task still registers. It
raises `ModuleNotFoundError` only when someone or something dispatches it. If it is on a
beat schedule, it fails silently on a timer.

**Resolution (rank 3, spec-doc6 §10.1 rule 4, "no dead code", and rule 1, "fail loudly").**
Delete the task and its beat entry, or restore the script. Check
`backend/app/workers/celery_app.py` for a beat entry before deciding.

**Enforcement:** `ENFORCEMENT-PENDING`. An import-smoke test that imports every registered
Celery task's body would have caught it. A cheaper version: assert every
`from app.scripts.X import` in `workers/` resolves.

---

## C28. Part A exists only in the working tree, uncommitted

**Source A, spec-doc6 D4:** *"Commit anything authored for Readypick"*, in scoped
conventional commits, *"each green on the full suite"*. §0.1 describes Part A as
*"built and tested"*.

**Source B, `git status --short`.** The entire Part A implementation is **untracked**:

```
?? backend/app/services/hiring/          (10 modules, 4,646 lines)
?? backend/app/services/miti/            (7 modules, 2,048 lines)
?? backend/app/services/siddhi/          (2 modules, 301 lines)
?? backend/app/models/hiring.py
?? backend/alembic/versions/0059_hiring_intelligence.py
?? backend/tests/test_hiring_layers.py backend/tests/test_hiring_retrieval.py
?? backend/tests/test_miti_pipeline.py  backend/tests/test_siddhi_citations.py
?? "Readypick Hiring Philosophy.md"
```

alongside 116 changed paths in total, on a branch shared with concurrent agents.
`backend/app/services/agents/` and `backend/app/services/evidence/` are **not** in this
list, which is consistent with those two being the only Part A packages on live paths
(see `GAP_MATRIX_V2.md` §2).

**Resolution (rank 3).** Commit Part A before any activation work begins, so the three
activation commits D1 requires (*"each must be revertable with `git revert` without touching
the other two"*) have a base to revert to. Reverting an activation commit onto an untracked
baseline restores nothing.

**Enforcement:** `ENFORCEMENT-PENDING`, and it is a process control rather than a test.

---

## C29. The `Tier` enum's own comments carry the wrong cut-points

Sub-finding of C19, recorded separately because it is a different file and a different fix.

`backend/app/models/enums.py:65-69`:

```python
class Tier(str, enum.Enum):
    highly_matching = "highly_matching"          # >=90
    moderately_matching = "moderately_matching"  # >=70
    matching = "matching"                        # >=50
    not_matching = "not_matching"                # <50
```

Three of the four comments state thresholds that contradict
`backend/app/services/rating.py:83-88` (90 / 75 / 60), and the **declaration order** encodes
the inverted band ordering. A reader who trusts the enum reads the product's grade scale
backwards in the middle.

**Resolution:** correct with C19, in the same commit.

**Enforcement:** the agreement test proposed in C19 covers it.

---

## C30. The Dashboard's colour palette contradicts the brand system and the never-grey token

**Source A, the Dashboard Specification, Color palette**
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:274-280`):

> - **Ready to Pick, Strong / Ready to Pick:** green (#2FD08A or equivalent)
> - **Consider with Reservations:** amber (#E0B341 or equivalent)
> - **Not Recommended / Under Review:** red (#EF5D6B or equivalent)
> - **Pending / Insufficient data:** gray, muted (#6B7280 or equivalent)
> - **All other text:** --text-dim (70% of full brightness, never pure black)

Reinforced elsewhere: Column 1's System ID is *"muted color"* (`:53`), Column 5's note is
*"text-dim (slightly muted gray, not full black)"* (`:167`), and column headers are
*"muted color"* (`:289`).

**Source B, CLAUDE.md, Part C (2026-08-28) and the 2026-07-28 rule.** Three standing rules
collide with that palette:

> **The brand is navy `#012654` and teal `#00888A`, SAMPLED not chosen.** [...] This replaced an indigo-violet ramp (`#5028E0`) which is precisely the palette Impeccable's `ai-color-palette` detector flags.

> **NAVY IS STRUCTURE, TEAL IS EVIDENCE.** [...] Teal is the one colour in the system with a meaning, and spending it on a button would waste it on the element that needs none.

> **Text is never grey**, enforced at the token. `globals.css` sets `--muted-foreground: var(--ink)` in both themes [...] Do not chase call sites in `components/ui/**`; fix the token if it ever drifts.

**Three distinct conflicts, resolved differently.**

**1. Muted grey text: the standing rule wins outright.** The Dashboard asks for grey text in
at least four places. The repository enforces the opposite **at the token**, in both themes,
by deliberate design, and CLAUDE.md's single documented exception is `::placeholder`. This is
rank 4 versus rank 5, which the precedence table alone would decide the wrong way, so decide
it on the merits and record why: the token rule is an **accessibility floor** with a contrast
script behind it (`frontend/scripts/check-contrast.mjs`), while the Dashboard's "never pure
black" is a stylistic preference with no measurement behind it. **Rendered as: full-ink
text, with hierarchy carried by size and weight rather than by brightness.** The Dashboard's
own typography section already supplies that hierarchy (13.5px bold name, 12 to 13px regular
body, 10.5px uppercase headers), so nothing is lost.

**2. The semantic band colours: reconcile, do not adopt verbatim.** Green / amber / red is a
traffic-light set, and the same family of choice the `ai-color-palette` detector already
flagged four call sites for on this project. Two constraints must hold simultaneously:

- **Teal is reserved.** `#00888A` means "corroborated evidence". A band colour is a verdict,
  not evidence, so the Ready Pick Score pill must **not** be teal, or teal stops meaning
  anything. The Dashboard's own Column 7 note suggesting *"teal vs. primary blue"* for the
  Team Review button (`:227`) must therefore be refused: Team Review is a person's opinion,
  which is the furthest thing from corroborated evidence.
- **The band set needs three or four hues the brand does not own.** The honest answer is that
  the navy/teal system has no verdict palette, and `DESIGN.md` must gain one derived from the
  brand rather than lifted from this document. Anchor the derivation on the brand's own hues
  where possible and keep every value above the AA threshold that
  `frontend/scripts/check-contrast.mjs` already asserts, in both themes.

**3. `#6B7280` for "Pending / Insufficient data".** This is the grey text rule again wearing a
different hat, and it lands on the state that matters most: an insufficient-evidence
candidate is the one CLAUDE.md's fairness rule protects (*"a career-changer gets a
low-confidence report that goes to a human"*). Rendering that state at 70% brightness makes
the row easiest to skip. **Use a full-contrast neutral with an outline treatment**, so the
state is visible rather than faded.

**Also note what the Dashboard gets right and must be kept:** *"Badge colors are never the
sole indicator; text color + label is redundant"* (`:380`), and *"'Under Review' state is
announced as 'Status: Under Review, awaiting integrity disposition' not just a visual red"*
(`:381`). Both are already spec-doc6 §8.3 accessibility gates.

**Enforcement:** `ENFORCEMENT-PENDING`. Three checks, all extensions of scripts that already
exist: `frontend/scripts/check-contrast.mjs` gains the band palette in both themes;
`frontend/scripts/impeccable-gate.mjs` keeps `ai-color-palette` blocking, with any adopted
band hue listed in `.impeccable-exceptions.md` **with a reason**; and a component test that
no dashboard text node resolves to a non-ink foreground token.

---

## C31. Runbook Decision Contract C5 cited the PROHIBITED disqualifier list

**Severity: this is the most dangerous single defect found in any document this phase.**

**Source A, the Runbook's Decision Contract, clause C5** (RPN-PHIL-001 §2), which cited
**§12.4**.

**Source B, the Runbook's own §12.** §12.3 is the list of **legitimate** disqualifiers.
**§12.4 is the list of PROHIBITED ones**: age, caste, gender, employment gaps and the rest.

**Consequence if implemented literally.** A Decision Contract clause authorising automatic
filtering, pointed at the prohibited list, reads as **explicit authorisation to auto-filter
on age, caste, gender and employment gaps**. An implementer following the citation
faithfully would have built the exact discriminatory behaviour the section exists to forbid,
and would have been able to point at the Runbook as their authority. Nothing downstream
would have caught it: the filter would look like a correctly sourced rule.

**Resolution (rank 2, and the fix is editorial, so spec-doc6 §2.1 permits applying it
directly).** §2.1's permitted-edit list includes *"Fix broken internal cross-references (a
§-number that points at the wrong section)"*. **Repaired to §12.3** in Runbook v1.1 by the
Runbook agent, logged in `RUNBOOK_EDITS.md`.

**Why it is recorded here even though it is fixed.** Three reasons. It is a
cross-document issue, because CLAUDE.md's disqualifier rule
(*"A disqualifier is matched on WORD BOUNDARIES and includes numeric age bars"*) and its
recorded history (*"the first version matched substrings and refused 'Must hold a valid CA
licence' because 'hold' contains 'old', while accepting 'No candidates over 45'"*) show this
product has already shipped a defect in exactly this area once. It is the strongest
available argument for the §2.2 parity test, because a parity test that checks every
citation resolves to the cited section would have caught it mechanically. And an
off-by-one section reference is the cheapest possible defect to reintroduce.

**Enforcement:** `tests/test_runbook_parity.py`. Extend it beyond value parity to
**citation-target parity**: every `source: "RPN-PHIL-001 §N"` must resolve to a section whose
content is consistent with the datum it annotates. A citation that resolves to a section
whose heading contains "prohibited" while annotating a permitted-value list must fail loudly.

---

## C32. The Runbook contains no "Must-have hard-cap rule"

**Source A, spec-doc6 §2.2**, which asks for it as extracted data:

> `bands.yaml` , grade band boundaries, the authenticity multiplier, confidence thresholds, and the Must-have hard-cap rule.

Reinforced as an acceptance criterion at §4.4 (*"The Must-have hard-cap applies exactly as
before, with no exceptions and no override. Add property-based tests"*) and §17 (*"the
Must-have hard-cap holds under property-based testing"*).

**Source B, the Runbook.** The phrase does not appear, and neither does the rule as a single
statement. **Three separate band-capping mechanisms exist** (§12.1, §12.2, §14.1), and they
are not obviously the same rule stated three ways.

**Source C, CLAUDE.md**, which states it as settled law with a rationale and existing tests:

> **The Must-have hard cap is applied LAST, on the SCORE, and it is a `min`.** After the authenticity multiplier, because a cap a later multiplication can undo is not a cap. A `min` rather than an assignment, because a candidate who already grades Not Matching must stay there.

and, separately:

> Any Not Matching Must-have caps Overall at Moderately Matching.

**`RESOLVED-BY-DEFAULT (CONTRADICTION-C32)`.** The precedence table cannot resolve this,
because the rank-2 authority is **silent** rather than contradicting, while a rank-3
requirement and a rank-5 product contract both assume a specific rule exists there.

**What to implement.** CLAUDE.md's statement is precise, has a defended rationale, is
already tested, and is a real product contract from spec-doc4. Keep it exactly:
cap at Moderately Matching, applied last, on the score, as a `min`. The 2026-07-30 rule
supplies the cut-point.

**What must be recorded rather than papered over.** `bands.yaml`'s hard-cap entry will carry
a `source` citation to whichever of §12.1 / §12.2 / §14.1 the Runbook agent judges closest,
and that citation is **weaker than every other entry in the file**, because it points at a
mechanism rather than at the stated rule. Mark the entry `RUNBOOK-AMBIGUITY` and list it in
the final report.

**The owner's question, in one line:** are §12.1, §12.2 and §14.1 three views of one cap, or
three different caps? If three, the product implements one of them and the other two are
unimplemented Runbook requirements.

**Enforcement:** the property-based tests spec-doc6 §4.4 requires, plus the parity test's
`RUNBOOK-AMBIGUITY` marker, plus the existing regression case for the category-mapping
defect (a composite keyed on dimension rather than item, which produced an empty Must-have
grade with nothing for the cap to bind against).

---

## C33. Per-seniority rubric anchors exist for one department of fifteen

**Source A, spec-doc6 §2.2:**

> `department_models.yaml` , department competency models, **per-seniority rubric anchors**, baseline weights.

**Source B, the Runbook.** Per-seniority anchors appear in **§21.11 only**, one department
out of the fifteen in Part VI (§21 to §35). Rubric anchors are otherwise **universal**,
stated once per dimension in §9.1 to §9.5 and applied across every department.

**Resolution (rank 2).** The Runbook's structure is the answer and it is a better structure
than the one spec-doc6 assumes: **anchors are per dimension, not per department per
seniority.** `dimensions.yaml` carries the five universal anchor sets from §9.1 to §9.5;
`department_models.yaml` carries competency models and baseline weights;
**§21.11's per-seniority anchors are a single documented departmental refinement**, not the
pattern.

**Why this matters beyond bookkeeping.** Extracting fifteen per-seniority anchor sets where
the Runbook states five universal ones would have manufactured seventy-five values from
nothing, each one looking sourced. The parity test would then have been the only thing
standing between the product and seventy-five invented rubric anchors, and a parity test can
only check what a value claims, not whether the claim was fabricated in good faith.

**Enforcement:** `tests/test_runbook_parity.py` refuses any value with no source citation,
which is exactly the guard that makes this discoverable. §21.11's entries carry their own
citation and are marked as a departmental override.

---

## C34. Situation-type weight consequences have no magnitude, and two types have none anywhere

**Source A, spec-doc6 §2.2:**

> `situation_types.yaml` , the six types (Gap-fill / Turnaround / Scale-up / Greenfield / Steady-state / Succession), each with its dimension weight consequences, **exactly as §18.4 states them**.

**Source B, Runbook §18.4** (`Readypick Hiring Philosophy.md:1686-1700`). The
"Weight consequence" column contains **arrows only**, with no magnitude anywhere:

> | **Gap-fill** | A specific missing capability | D3 up-up, D1 up | Direct prior experience of that exact problem |
>
> | **Turnaround** | Something is broken and must be fixed | D2 up-up, D3 up | Evidence of fixing, not just running |
>
> | **Steady-state** | Maintain and execute | D1 up-up, D5 down | Reliability, depth, consistency |

There is no multiplier, no percentage and no delta in the section. **Numbers exist only in
§11.3, and only for four of the six types. Scale-up and Succession have no magnitude
anywhere in the document.**

**`RESOLVED-BY-DEFAULT (CONTRADICTION-C34)`.** "Exactly as §18.4 states them" is not
satisfiable: §18.4 states ordinal directions, and `situation_types.yaml` needs cardinal
values to move a weight.

**What the repository already says, and it turns out to be the honest answer.**
`backend/app/services/hiring/situations.py:70` records it independently, before the Runbook
was available:

> The arrows are ORDINAL and the Runbook attaches no multiplier to them

and `:115-117` notes that §11.3 supplies an additive bound for four of them but not for
Scale-up or Succession. That module reached the right conclusion from spec-doc5's inline
restatement alone. **Its specific chosen multipliers are still an inference and are still
wrong in detail** (the reconciliation found 4 of 6 rows incorrect, two by inversion), but
its epistemics were right.

**What to implement.** `situation_types.yaml` carries the six arrow patterns from §18.4 as
ordinal data with a §18.4 citation, and the four §11.3 magnitudes with a §11.3 citation.
**Scale-up and Succession carry no magnitude and are marked `RUNBOOK-AMBIGUITY (§18.4)`.**
Do not interpolate them from the other four: an interpolated multiplier would be
indistinguishable in the data file from a sourced one.

**The owner's question:** what are Scale-up's and Succession's weight magnitudes? Until
answered, those two situation types re-weight nothing, which is the safe direction (a
misclassification into an unweighted type costs nothing, while a misclassification into an
invented weighting corrupts the whole vector, which is the error CLAUDE.md calls *"the most
expensive error available at intake"*).

**Enforcement:** the parity test plus a `RUNBOOK-AMBIGUITY` marker on both entries.

---

## C35. "Weakly" is a third value inside an integer count, with no defined arithmetic

**Source A, spec-doc6 §2.2:**

> `evidence_tiers.yaml` , tier definitions, provenance rules, **independence-group rules**, and the specificity / attribution / scale / decay modifiers.

**Source B, the Runbook.** The independence rule is an **integer count of independent
originators**, and **"weakly" appears as a third value inside that count** with no defined
arithmetic. Is a weakly-independent pair 1, 2, or something between? The document does not
say. **Unlisted source types have no default either.**

**Source C, CLAUDE.md**, which states the rule this arithmetic feeds:

> **Independence is counted by ORIGINATOR, never by document.** A resume line and the candidate restating it in the interview could not have disagreed: that is one person saying one thing twice. Platform memory is never independent. **An unknown source type is assumed DEPENDENT, because assuming independence manufactures corroboration.**

**`RESOLVED-BY-DEFAULT (CONTRADICTION-C35)`, and this is the one with the widest blast
radius of the five.** A weakly-independent pair is described in the source material as **the
most common evidence pair in the product**. An undefined arithmetic on the most common case
is not an edge case; it is the modal path.

**Implement the restrictive reading** (spec-doc6 §20). Both halves fall the same way, and
CLAUDE.md already supplies the argument:

- **"Weakly" counts as DEPENDENT**, contributing 0 additional independent originators.
  Counting it as 1 manufactures corroboration, and manufactured corroboration raises a
  confidence score that a human then relies on.
- **An unlisted source type is DEPENDENT.** CLAUDE.md states this verbatim and gives the
  reason.

Record the weakly-dependent choice at the code site as
`RESOLVED-BY-DEFAULT (CONTRADICTION-C35)`, because it is the reading that will most often
be wrong in the candidate's favour and the owner may want a middle value.

**The owner's question:** does a weakly-independent pair count as one originator or two? A
third option exists and may be the right one: count it as one, but record the weak link so
the confidence calculation can penalise it separately from an outright dependent pair.

**Enforcement:** `ENFORCEMENT-PENDING`. A test table over every (source type A, source type
B) pair asserting the independence count, with unlisted types explicitly included, plus the
existing rule that confidence is arithmetic over counts and never a model's opinion.

---

## C36. Three Layer 1 baselines breach the clamp §11.4 calls absolute

**Source A, Runbook §11.4**, which states a weight clamp in absolute terms: ceiling 0.40,
floor 0.05.

**Source B, the Runbook's own Layer 1 baseline tables.** Three departmental baselines are
outside it:

| Department, seniority | Dimension | Baseline | Bound |
|---|---|---|---|
| Mechanical, Fresher | D1 | **0.42** | ceiling 0.40 |
| Skilled Trades, Entry | D1 | **0.44** | ceiling 0.40 |
| Data, Fresher | D2 | **0.04** | floor 0.05 |

**Resolution (rank 2, resolved internally, and the clamp wins).** §11.4 calls the bound
absolute; three data rows sit outside it. An absolute constraint and three exceptions cannot
both be true, and the constraint is the more load-bearing statement: it is the thing every
other layer's tuning is checked against.

**Clamp the three baselines to 0.40, 0.40 and 0.05 respectively, and record every clamp.**
That is exactly the discipline `backend/app/services/hiring/layers.py` already implements,
and CLAUDE.md already states the reason:

> **Every refusal and every clamp is RECORDED**: a clamp that left no trace is indistinguishable from an input that was already in range.

**Why this is not merely arithmetic.** A Layer 1 baseline is the value every Layer 2 and
Layer 3 multiplier is applied to. An out-of-range baseline does not stay 0.02 out of range:
it is multiplied. Three fresher and entry-level rows are also exactly the population where a
weighting error is least visible, because those candidates have the least evidence to
contradict it.

**Do not silently clamp at read time and leave the table wrong.** Fix the data file, cite
§11.4 as the reason in `RUNBOOK_EDITS.md`, and propose the source correction to the owner
rather than applying it to the Runbook prose: changing a baseline weight is on spec-doc6
§2.1's **not permitted** list.

**Enforcement:** a test asserting every value in `department_models.yaml` sits inside
§11.4's bounds, which is a one-line invariant over the whole file and would have caught all
three.

---

## C37. "Insufficient evidence" has two independent definitions

**Source A, Runbook §6.7.** One definition, in the evidence model.

**Source B, Runbook §10.7.** A second, independent definition, in the scoring mathematics.
**The two can disagree in both directions**: evidence sufficient by one and insufficient by
the other, and the reverse.

**Source C, CLAUDE.md**, which makes the consequence of the trigger a hard rule with a
fairness rationale:

> **INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE.** A dimension flagged insufficient is EXCLUDED from the composite and paid for in CONFIDENCE, never scored low. The practical consequence is the point: a career-changer gets a low-confidence report that goes to a human rather than a confidently poor grade that does not.

**`RESOLVED-BY-DEFAULT (CONTRADICTION-C37)`.** A rule with two definitions has an **undefined
trigger**. The consequence is specified precisely and the condition is not, which is the
worse way round: the product knows exactly what to do and not when to do it.

Gate **G2** (evidence sufficiency) depends on this, and spec-doc6 §4.4 requires G2 to
*"block with an actionable reason, not a silent low score"*. A gate cannot block on an
undefined predicate.

**Implement the union, in the direction that flags more.** A dimension is insufficient if
**either** §6.7 or §10.7 says so. That is the restrictive reading in the sense spec-doc6 §20
means it: it routes more candidates to a human and scores fewer of them low. It is also the
direction CLAUDE.md's fairness argument points, since every false "sufficient" produces a
confident grade on thin evidence, and every false "insufficient" produces a low-confidence
report a person reads.

Mark both call sites `RESOLVED-BY-DEFAULT (CONTRADICTION-C37)`.

**The owner's question:** are §6.7 and §10.7 meant to be one rule stated twice, or two
different tests (evidence-model sufficiency versus scoring sufficiency) that both need to
pass? If the latter, the union is correct as implemented and the Runbook should say so.

**Enforcement:** `ENFORCEMENT-PENDING`. A test table of cases where the two definitions
disagree, in both directions, asserting the union, plus the existing test that insufficient
evidence reduces confidence and never the score.

---

## C38. Seven of spec-doc6's RBAC citations do not say what spec-doc6 claims

Produced by reading **all 31 RBAC citations across 22 distinct sections** in spec-doc6
against `docs/spec/RBAC_SPECIFICATION.md`, plus three sections cited without the `RBAC`
prefix (§8.2, §21, §23). **25 sections checked. 18 accurate. 7 inaccurate or overreaching.**

```bash
grep -oE "RBAC §[0-9]+(\.[0-9]+)?" "/c/Users/Saravan Kumar/Downloads/spec-doc6.md" | sort -u
```

| RBAC § | spec-doc6 claims | The section actually says | Verdict |
|---|---|---|---|
| §3 | frontend visibility is not a security boundary | *"Frontend visibility is NOT a security boundary."* | ACCURATE |
| §4 | "AI-generated hiring intelligence" and "hiring data" are tenant-isolated | Both appear verbatim in a 17-item list | ACCURATE |
| §5 | says "four" then lists five | Exactly so, see C4 | ACCURATE |
| §7.5 | Super Admin override, audited | Verbatim, incl. *"MUST still record ... in the audit trail"* | ACCURATE |
| §8.1 / §8.2 | primary broad operational hiring authority, organisation-wide | Verbatim | ACCURATE |
| §9.2 | Recruiter scoped to assigned jobs | Verbatim | ACCURATE |
| **§9.6** | *"HR Manager and Super Admin publish only as an audited exception"* | *"The **Super Admin** is an administrative exception."* **The HR Manager is not named in §9.6 at all**; its authority is an asterisked §24 cell flagged as needing a future product decision | **INACCURATE**, see C13 |
| **§10.3** | the HM review screen shows weight traceability in plain language before finalisation | §10.3 is *"JD Review"*: Review / Edit / Refine / Finalize the JD. **Nothing about traceability, weights, or a review screen** | **NOT IN SOURCE** |
| §11 | HM cannot reject the JD | Verbatim, incl. the workflow diagram | ACCURATE |
| §12 | explicit controlled revision mechanism preserving authorship and auditability | Verbatim | ACCURATE |
| **§13.4** | Interview Managers are the **primary** Team Review participants | *"Interview Managers **can participate** in Team Review."* No primacy is stated. §24 supports the reading (theirs is the only unasterisked YES), but §13.4 does not say it | **OVERREACH** |
| §13.5 | observers/contributors, not owners of definition | A restriction list consistent with that characterisation | ACCURATE as a pattern |
| §15 | public job URL unauthenticated | Verbatim | ACCURATE |
| §17 | `DRAFT ... PUBLISHED ...` | Eight states; spec-doc6's ellipsis hides `CANDIDATE_APPLICATIONS` and `HIRING_PROCESS`, and omits §17's explicit permission to use different internal names | ACCURATE but incomplete, see C11 |
| §20 | audit row with user, timestamp, JD version, criteria version | Verbatim | ACCURATE |
| §21 | Recruiter publishes; blocked if HM components incomplete | Verbatim | ACCURATE |
| §22 | evaluation context references versions in force at application | *"SHOULD preserve the historical version used when each candidate applied"* | ACCURATE (SHOULD, not MUST) |
| **§24** | *"RBAC §24 marks these NO for Hiring Manager and NO for Interview Manager"* | The Hiring Manager cells are **`NO*`**, and the footnote defines the marker as *"intentionally conservative and may require an explicit future product decision"*. spec-doc6 drops the asterisk, converting a provisional cell into a settled prohibition | **INACCURATE**, see C5 |
| **§29** | *"Nobody may edit another user's remark"* | *"**Interview Managers** MUST NOT be able to silently alter another interviewer's remarks."* Stated of one role, generalised to all | **OVERREACH** (safe direction) |
| §31 | Super Admin activity view; audit does not depend on dashboard rendering | *"MUST NOT depend **exclusively** on dashboard rendering."* spec-doc6 drops "exclusively" | ACCURATE, near-verbatim |
| **§33** (a) | *"Cross-tenant reads return 404, never 403"* | **§33 never mentions a status code.** It requires the backend verify tenant and authorization relationship. The 404 rule is spec-doc6's own, and it is a good one | **NOT IN SOURCE** |
| **§33** (b) | `3252463dfbg43t4hfb` is *"the public job ID format"* | It is an **example of an id somebody might know**, in a section whose entire point is that knowing it grants nothing. No format is specified anywhere in RBAC | **MISREAD**, see C17 |
| §34 | agent attribution to both principals; agent constrained to tenant and job scope | Verbatim on both | ACCURATE |
| **§35** | versioning applies to *"... published job definition **and Company DNA**"* | §35 lists **eight** artifacts. **Company DNA is not among them.** It could not be: the concept post-dates this document | **ADDITION** |

**Resolution.** Where spec-doc6 **overreaches in the restrictive direction** (§13.4, §29,
§33a, §22's SHOULD-to-MUST, §35's addition), keep spec-doc6's stricter reading: it
discloses less and restricts more, which spec-doc6 §20 asks for, and rank 3 legitimately
adds requirements rank 1 does not forbid.

Where spec-doc6 **grants an authority its source does not** (§9.6, and §24's dropped
asterisk), the rank-1 source wins: **withhold the HR Manager publish capability** pending
the product decision §24's own footnote requests, and record the Hiring Manager's
`NO*` cells as provisional.

Where spec-doc6 **states a requirement its cited section does not contain** (§10.3, §33b),
the requirement may still be good and must be re-sourced. §4.3's weight-traceability
requirement is defensible on its own terms (§12 makes finalization authoritative, so the
Hiring Manager must be able to see what they are making authoritative) but it is spec-doc6's
requirement, not RBAC §10.3's, and the code comment must cite it as such.

**Why this matters more than a citation-hygiene complaint.** An implementer given
*"RBAC §33 requires 404, never 403"* will write that in a docstring, and the next reader will
believe a rank-1 document mandates it. Three of the seven would have propagated a false
provenance into the codebase, and one (§9.6) would have granted a real capability on it.

**Enforcement:** `ENFORCEMENT-PENDING`. `docs/RBAC.md`, which spec-doc6 §14 already
requires as *"the implemented permission model, mapped cell by cell to the RBAC
specification"*, must cite `docs/spec/RBAC_SPECIFICATION.md` by section and line, never
spec-doc6's summary of it.

---

## C39. spec-doc6 forbids a JD rejection path that RBAC §24 grants to two roles

**Source A, spec-doc6 §4.3:**

> Remember RBAC §11: the Hiring Manager **cannot reject the JD**. Bodha rejecting a SWOT *back to* the Hiring Manager for rework is a different thing and is permitted. **Do not accidentally implement a JD rejection path.**

**Source B, RBAC §24** (`docs/spec/RBAC_SPECIFICATION.md:1023`):

> | Reject JD | YES | YES | NO | NO | NO |

Super Admin **YES**. HR Manager **YES**. Recruiter, Hiring Manager, Interview Manager NO.

**These do not contradict, and the way they fail to contradict is the trap.** §11 is about
the **Hiring Manager**, and §24 agrees with it. But spec-doc6's instruction is written
without a subject: *"Do not accidentally implement a JD rejection path"* reads as "there is
no such path", when RBAC grants one to two roles.

**Resolution (rank 1).** A JD rejection path **exists** and belongs to Super Admin and HR
Manager. What must not exist is a **Hiring Manager** rejection path, because §11 is emphatic
that the Hiring Manager's remedy is to edit rather than reject:

> If the initial JD is poor or incomplete, the Hiring Manager edits it rather than rejecting it.

**Implement:** a `Reject JD` capability granted to `Role.client` and the HR Manager roles
only, refused at the HTTP layer for Recruiter and Hiring Manager, and a matrix test case per
role. Do **not** read spec-doc6 §4.3 as a blanket prohibition.

**Why it is worth catching now.** The safe-by-default reading of spec-doc6 §4.3 is "build
nothing", which produces a **missing capability** rather than an over-broad one. That is the
rare case where spec-doc6 §20's restrictive rule gives the wrong answer, because rank 1
affirmatively grants the capability. Restricting more is only the right default when the
higher authority is silent.

**Enforcement:** `ENFORCEMENT-PENDING`. Two matrix cells (Super Admin YES, HR Manager YES)
and three refusals.

---

## C40. Dashboard Source has two values; the repository has three

**Source A, the Dashboard Specification, Column 2**
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:73-90`):

> - `Databank` (sourced by Ready Pick Now's research team)
> - `Applied` (candidate applied directly via job posting)

Two values, and the column is *"Sortable / filterable at top of dashboard"*.

**Source B, the repository and CLAUDE.md (2026-07-28):**

> **Every candidate link carries `source_type`: `applied | sourced | databank`.** Applied means they came through ReadyPick, sourced means a third-party link, databank means the recruitment team bulk-uploaded them. This is provenance for DISPLAY and filtering ONLY. Nothing may branch on it.

Three values, on `MatchOut.source_type` (`backend/app/schemas/matching.py:56`), with
`sourced` (a third-party link) having no Dashboard counterpart.

There is also an older, unrelated two-value marker on the same rows: `LinkSource` =
`databank | fresh` (`backend/app/models/enums.py:60-62`), which answers a different question
and is returned alongside as `source` (`backend/app/schemas/matching.py:52`). **Two fields
named `source` and `source_type` on one row is itself a readability defect.**

**Resolution (rank 5 for the data model, rank 4 for the surface; they are compatible).**
Keep the **three** stored values, because `sourced` is real provenance the product already
records and collapsing it would lose information that cannot be reconstructed. Render
Column 2 over all three, adding a `Sourced` pill. A two-value filter over a three-value
column would silently hide every third-party-sourced candidate from a recruiter filtering by
source, which is the failure mode a provenance column exists to prevent.

**Note the standing constraint that survives:** *"Nothing may branch on it: all three are
parsed, embedded, matched and assessed identically."* The dashboard filter is display, not a
branch.

**Enforcement:** `ENFORCEMENT-PENDING`. A test that the Column 2 filter's option set is
derived from `source_type`'s enum rather than hardcoded, so a fourth value cannot appear in
the data and be invisible in the UI.

---

## C41. RBAC §35's versioning list does not include Company DNA

**Source A, spec-doc6 §5:**

> **Versioning** applies to JD, Must-have, Nice-to-have, behavioural competencies, job philosophy, SWOT, evaluation rubrics, published job definition and **Company DNA** (RBAC §35).

**Source B, RBAC §35** (`docs/spec/RBAC_SPECIFICATION.md:1361-1387`), the complete list:

> JD / Must-Have skills / Nice-to-Have skills / Behavioural competencies / Job philosophy / SWOT / Evaluation rubrics / Published job definition

**Eight items. Company DNA is absent**, and could not be present: Company DNA is a Runbook
Layer 2 concept and this document is scoped to *"internal client/company-side authorization
and hiring workflow"*.

§35 also says **SHOULD** throughout (*"The platform SHOULD version important hiring
artifacts"*, *"A candidate's evaluation context SHOULD reference the relevant version"*),
while spec-doc6 §5 states it as a requirement with a mandatory scenario test.

**Resolution (rank 3 legitimately extends rank 1).** spec-doc6's addition is **correct and
should be implemented**. RBAC does not forbid versioning Company DNA; it simply predates the
concept. And the case for versioning it is stronger than for anything on §35's list:
CLAUDE.md records that *"a Company DNA artifact constrains every job that client will ever
post, so it must be reproducible, diffable between versions, and explainable without a
provider"*, and spec-doc6 §4.2 requires *"immutable versions, new version on any change,
never in-place mutation"* with every `Role` referencing the exact version in force when its
scorecard was frozen.

Likewise, spec-doc6's promotion of SHOULD to MUST restricts more and is kept.

**Recorded because the citation is wrong, not the requirement.** A code comment reading
*"versioned per RBAC §35"* on the Company DNA model would be false provenance. Cite
spec-doc6 §4.2 and §5 there, and cite RBAC §35 only for the eight artifacts it actually
lists. Part of the C38 pattern.

**Enforcement:** the golden-file test spec-doc6 §11.1 requires for the compiled Company DNA
artifact, plus the §5 scenario test (candidate applies, criteria are revised, evaluation
still resolves against the original version), which RBAC §22 independently supports.

---

## C42. The public job URL path differs in three places

**Source A, RBAC §15** (`docs/spec/RBAC_SPECIFICATION.md:787-791`):

> ```
> https://readypick.ai/jobs/3252463dfbg43t4hfb
> ```

**Source B, spec-doc6 §13.2:**

> The public job URL path (`/jobs/{public_job_id}`, unauthenticated per RBAC §15) is routable without authentication while every other route is not: assert this at the ALB/listener-rule level and in application tests.

**Source C, the repository.** Two paths, neither matching:

| Layer | Path | Site |
|---|---|---|
| Frontend, what a candidate clicks | `{frontend_url}/apply/{job_uuid}` | `backend/app/api/jobs.py:97-105` |
| API | `GET /jobs/public/{job_id}` | `backend/app/api/jobs.py:1353` |
| RBAC §15 and spec-doc6 §13.2 | `/jobs/{id}` | |

The frontend comment at `:101-104` records the deliberate choice: *"The frontend serves the
public application page at /apply/{job_uuid} (a bare root catch-all was deliberately
avoided)"*.

**Resolution (rank 1 on the domain, deliberate deviation on the path, and both must be
written down).** §15's requirement is **substantive**: the URL must be reachable without
authentication. It is not a routing specification, and it gives the example under the
heading "Example". The repository's `/apply/{uuid}` satisfies §15's actual requirement.

**But spec-doc6 §13.2 asks for an ALB listener-rule assertion**, and a listener rule is
literal. If the rule is written for `/jobs/*` while the application serves `/apply/*`, the
public link 404s **in production and nowhere else**, because no local or staging test
exercises the load balancer's path matching. That is the failure this requirement exists to
prevent, and it is the kind that a green pipeline reports as success.

**RESOLVED BY C45**, which settles the choice and states the reason. In short: keep
`/apply/{...}`, record the divergence from §15 rather than treating it as a defect, derive
the listener rule and the application route from one constant, and add the test spec-doc6
§13.2 requires at both layers.

**Fix the domain regardless:** `picready.com` is a typo (see C3).

**Enforcement:** `ENFORCEMENT-PENDING`. `backend/tests/test_jobs.py:163` currently asserts
the wrong domain, so it will need changing with the fix; the listener-rule assertion does
not exist because the `alb` module does not exist (C12).

---

## C43. Two runtime instances of `pickready.app`, reported rather than changed

Sub-finding of C3, recorded separately because these two are **not documentation** and cannot
be fixed by a sweep: whether the mailboxes exist is an operational fact only the owner knows.

**Source A, RBAC §15** (`docs/spec/RBAC_SPECIFICATION.md:787-791`) makes `readypick.ai`
canonical. C3's amendment records `pickready.app` as an eighth naming variant.

**Source B, the repository.** Two live instances, and the first is the higher risk:

| Site | Value | Kind | Risk |
|---|---|---|---|
| `backend/app/core/config.py:93` | `smtp_from_email: str = "noreply@pickready.app"` | **Runtime default** | A deployment that does not set `SMTP_FROM_EMAIL` sends **every outbound email in the product** from a domain that may not exist. Bounces, SPF/DKIM failures and spam classification all follow, and none of them raises |
| `frontend/app/(org)/org/billing/page.tsx:442` | `mailto:hello@pickready.app?subject=Enterprise%20plan` | Customer-facing UI | A paying customer clicking "Talk to us about Enterprise" mails an address that may not be monitored. Silent lost revenue, no error anywhere |

Note the adjacent line `backend/app/core/config.py:94` already reads
`smtp_from_name: str = "ReadyPick"`, so the display name and the domain in the same struct
disagree about the brand.

**Resolution (rank 1 on the domain; the mailbox question is the owner's).** Both should point
at `readypick.ai`, **but only after the owner confirms `noreply@readypick.ai` and a monitored
sales address exist**. Changing a From address to a mailbox that does not exist trades one
silent failure for a louder one, and CLAUDE.md's Gmail-SMTP rule makes the authenticated
mailbox the From address in any case, so the default may be dead code that only fires on a
misconfigured deployment.

**Recommendation, independent of the mailbox answer:** `smtp_from_email` should have **no
default at all**. A required, unset environment variable fails at startup; a wrong default
fails silently at the first email. That is the same argument C14 makes about
`owner_email`'s default on the line above it.

**Enforcement:** `ENFORCEMENT-PENDING`. Two things: the C3 naming CI check must cover
`pickready.app`, and a config test asserting `smtp_from_email` has no default once it is
removed.

---

## C44. RBAC §15 requires a separate public job identifier; this codebase has none

**Severity: a design decision, not a formatting one. Supersedes C17's job-ID half.**

**Source A, RBAC §15** (`docs/spec/RBAC_SPECIFICATION.md:785-791`):

> Each published job receives a **unique public identifier**.
>
> Example:
> ```
> https://readypick.ai/jobs/3252463dfbg43t4hfb
> ```

and **§21** (`:940-942`), at publication: *"A public job identifier is available. The public
URL becomes usable."*

**Source B, the repository.**

```bash
grep -rn "public_job_id" backend/app --include=*.py     # returns nothing
```

`public_job_url` (`backend/app/api/jobs.py:97-105`) returns `{base}/apply/{job_id}`, where
`job_id` is `jobs.id`: the **raw internal primary key**, a UUID via `UUIDPKMixin`
(`backend/app/models/base.py:13`, `backend/app/models/job.py:40`).

**§15's example is not a UUID.** Eighteen characters of mixed alphanumerics against a UUID's
36 with hyphens. §15 is asking for an identifier that is *derived at publication and distinct
from the primary key*, not for the primary key rendered into a URL.

**Resolution (rank 1).** Add a `jobs.public_job_id`: a column, a backfill for published rows,
a lookup path, and stamping at publication alongside `posting_start_date`. **This is not a
rename.** Three consequences are worth stating explicitly, because two of them are easy to
argue away and should not be.

**1. The public URL currently leaks the internal primary key, and that is not by itself a
hole.** RBAC §33 is explicit that *"Obscurity is NOT authorization"*, and §33 compliance must
be verified independently either way. The repository already satisfies §33 on this route:
`backend/app/api/jobs.py:1380-1391` refuses unless `ratified_at IS NOT NULL` **and**
`archived_at IS NULL` **and** `job_posting.public_link_active(...)`, returning 404 for every
failure. **What the leak actually costs is coupling.** Any endpoint, email, log line or
third-party job board that carries a public link now also carries an internal id, and **the
two can never be rotated apart**. A separate public id makes the public handle revocable and
re-issuable without touching a single foreign key.

**2. §15's example is short enough to quote over the phone. `jobs.id` is not.** That looks
like a cosmetic observation and is not: the same usability property is why
`backend/app/services/reference_code.py` exists at all, and that module argues it at length
(`:8-11`): *"the underlying identifiers are UUIDs nobody can hold in their head or read down
a phone line."* The specification appears to be selecting for the same property deliberately,
and the product has already built the machinery once.

**3. The obvious implementation is already in the repository.** `reference_code._segment`
(`:83-101`) produces HMAC-derived, one-way, Crockford-base32 segments with per-position domain
separation. A public job id of the same construction would be non-reversible, non-enumerable,
quotable, and stable for the life of the row. **It must remain a label, never a permission**,
exactly as `reference_code.py:35-37` insists of the candidate code: *"nothing anywhere
authorises on this value, and RLS remains the tenant boundary."* §33's checks stay exactly as
they are.

**Do not read this as urgent.** Nothing is exposed that §33 does not already gate. It is a
schema change that gets more expensive the longer public links accumulate, which is an
argument for doing it before Phase 8 rather than after.

**Enforcement:** `ENFORCEMENT-PENDING`. A migration adding the column with a unique
constraint; a test that publication stamps it; a test that the public route resolves by
`public_job_id` and **not** by `jobs.id`; and the existing §33 refusal tests kept unchanged,
because the new id changes nothing about authorization.

---

## C45. The public job PATH diverges from §15, deliberately, and stays

**Supersedes C42's open "pick one path" question.**

**Source A, RBAC §15** (`docs/spec/RBAC_SPECIFICATION.md:787-791`): the path is
`/jobs/{public_job_id}`. spec-doc6 §13.2 restates it as `/jobs/{public_job_id}`.

**Source B, the repository.** `backend/app/api/jobs.py:97-105` builds `/apply/{job_id}`, and
the docstring records the reason: *"The frontend serves the public application page at
/apply/{job_uuid} (a bare root catch-all was deliberately avoided)."*

**Resolution: a KNOWN DIVERGENCE with a stated reason, not a defect awaiting a fix.**

§15's requirement is substantive, not a routing specification: *"The public job URL MUST be
accessible without authentication. [...] Authentication MUST NOT be required merely to view
the public job posting."* `/apply/{...}` satisfies that. The path itself appears under the
heading "Example".

**Why it is not changed.** Published job links are already in candidates' inboxes, in job
boards, in sent email bodies recorded in `email_log`, and in traces a rolling deploy is still
writing. A route change needs a redirect story before it needs a rename. **This is the same
argument CLAUDE.md already makes, and accepts, for the `ppi` module names:**

> A route is quoted in report links already in people's inboxes and in traces a rolling deploy is still writing [...] so a symbol rename would cost a reader access to an existing report and buy nothing anybody sees.

**Two obligations follow, and neither is optional.**

**The divergence must be written down where the load balancer is configured.** spec-doc6
§13.2 requires an assertion *"at the ALB/listener-rule level and in application tests"*, and a
listener rule is **literal**. A rule written for `/jobs/*` against an application serving
`/apply/*` produces a public link that 404s **in production and nowhere else**, because no
local or staging run exercises the load balancer's path matching. That is precisely the class
of failure a green pipeline reports as success, and this project has a documented history of
it (CLAUDE.md: *"'The pipeline passed' is not evidence that anything works"*).

**Derive both from one constant.** The listener rule and `public_job_url` must read the same
value, or they will drift the first time either is edited.

**Already corrected and worth recording as such:** the docstrings at those sites now describe
the code they sit on (`readypick.ai/apply/{job_uuid}`), so the code is self-consistent and
honestly documented. Only the divergence from §15's example remains, and it is now recorded
rather than latent.

**Note the interaction with C44.** If `public_job_id` is added, the path becomes
`/apply/{public_job_id}`. The path stays; the identifier in it changes. The two decisions are
independent and should not be bundled.

**Enforcement:** `ENFORCEMENT-PENDING`. `docs/DEPLOY_AWS.md` states the path; the `alb` module
derives its listener rule from the same constant as `public_job_url`; a test asserts the
constant is used at both sites. The `alb` module does not exist yet (C12).

---

## C46. `impeccable` gates CI and is the one design tool nothing pins

**Source A, spec-doc6 D4:**

> **Do not commit vendored third-party skill source** [...] Gitignore those paths and instead commit a pinned manifest, `tools/design-tools.manifest.json`, recording each tool's repository URL, the exact commit SHA installed, the skill name, and the install command, plus `tools/install-design-tools.sh` that reproduces the environment from the manifest.

**Source B, the repository.** The gitignore rules, the manifest and the installer all exist
(`.gitignore:52-67`, `tools/design-tools.manifest.json`, `tools/install-design-tools.sh`), and
the manifest is **honest about what it cannot pin**: it records `null` with a stated
`sha_status` rather than inventing a SHA. That is the right call, and it is what makes this
finding legible instead of hidden.

What the manifest exposes:

| | Pinned by | Files |
|---|---|---|
| `design-taste-frontend`, `high-end-visual-design`, `redesign-existing-projects` | `skills-lock.json`, **content hash**, not a commit SHA | 3 |
| **`impeccable`** | **nothing** | **148 per copy, and it is installed twice** |

```bash
grep -c "sourceType" skills-lock.json     # 3 entries
grep -c "impeccable" skills-lock.json     # 0
find .claude/skills/impeccable -type f | wc -l    # 148
find .github/skills/impeccable -type f | wc -l    # 148, a byte-duplicate
```

**And CI depends on it.** `.github/workflows/deploy.yml:204-211` runs
`node scripts/impeccable-gate.mjs`, which per CLAUDE.md *"exits non-zero on any finding not
listed in `.impeccable-exceptions.md` WITH A REASON, because a detector that only prints
warnings is one everybody scrolls past."*

**`RESOLVED-BY-DEFAULT (CONTRADICTION-C46)`, and the default is to keep shipping while saying
so.** The precedence table has nothing to say about tool provenance; spec-doc6 D4 asks for a
SHA that is not recoverable from what the installer wrote.

**The version discrepancy is the part that needs the owner.** The installed
`SKILL.md` frontmatter declares **`version: 4.1.1`**; `npm view impeccable version` returns
**3.6.0**. The installed skill is therefore **not the public npm `impeccable` package at its
current version**, or it came from a different channel. Until that is resolved, the manifest's
`install_command` (`npx impeccable init`) is **inferred from the skill's own Setup section and
not confirmed against an install transcript**, so `tools/install-design-tools.sh` cannot be
said to reproduce the environment. It reproduces three of four tools.

**Why this matters more than a lockfile complaint.** A CI gate whose implementation cannot be
reproduced is a gate that can change what it enforces without any commit in this repository.
If `impeccable` is re-installed at a different version, `frontend/scripts/impeccable-gate.mjs`
may start failing on findings that were previously clean, or stop failing on findings that
were previously caught, and **`.impeccable-exceptions.md` would still read as current**. The
exception registry's whole value is that every entry states a reason against a known detector
set.

**Two things to do that do not need the owner.** Delete one of the two byte-identical copies:
296 of the 302 vendored files are one tool installed twice, and the duplicate doubles the
untracked footprint for nothing. And record the installed tree's own content hash in the
manifest, which is a weaker guarantee than a SHA but detects drift, exactly as
`skills-lock.json` already does for the other three.

**The owner's question, in one line:** where was `impeccable` 4.1.1 installed from, given npm
publishes 3.6.0 under that name?

**Enforcement:** `tools/design-tools.manifest.json` records the gap in its own `sha_status`
and `npm_registry_discrepancy` fields, which is the honest minimum. **`ENFORCEMENT-PENDING`**
for a CI check that the installed `impeccable` version matches the manifest, so a silent
re-install fails the build instead of quietly changing what the design gate enforces.

---

## Reproduction

Every claim above is reproducible from the repository root:

```bash
cd /c/dev/pickready

# C0  the search
find . -type f \( -iname "*rbac*" -o -iname "*column*framework*" \) | grep -v node_modules

# C3  naming, per category
grep -rho "pickready[a-z:._-]*" backend/app | sort | uniq -c | sort -rn
grep -rn "picready" backend/app frontend/app

# C8  the real form size
grep -c "FormField(" backend/app/services/candidate_profile_form.py            # 25
grep -oE "display_no=[0-9]+" backend/app/services/candidate_profile_form.py | sort -u | wc -l   # 20
grep -niE "39[- ]aspect|40[- ]aspect" "Readypick Hiring Philosophy.md"          # 0

# C11 the enums
sed -n '36,44p;73,105p' backend/app/models/enums.py
sed -n '42,70p' backend/app/services/hiring_pipeline.py

# C17 the id formats
sed -n '55,75p;104,151p' backend/app/services/reference_code.py
sed -n '97,105p;1353,1392p' backend/app/api/jobs.py

# C19 the two scales
sed -n '83,89p' backend/app/services/rating.py
sed -n '9,24p'  backend/app/services/tiers.py
grep -n "assign_tier" backend/app/services/matching.py

# C20 G1 reachability
grep -rn "scorecard_gate" backend/app --include="*.py" | grep -v __pycache__
grep -rn "services.miti" backend/app/api backend/app/workers                    # no output

# C44 the absent public job identifier
grep -rn "public_job_id" backend/app --include=*.py      # no output
sed -n '97,105p' backend/app/api/jobs.py                 # /apply/{jobs.id}
sed -n '785,798p' docs/spec/RBAC_SPECIFICATION.md        # 15: a unique public identifier

# C43 the two runtime pickready.app instances
sed -n '93,94p' backend/app/core/config.py
grep -n "pickready.app" "frontend/app/(org)/org/billing/page.tsx"

# C46 what pins the design tools
grep -c "sourceType" skills-lock.json ; grep -c "impeccable" skills-lock.json
find .claude/skills/impeccable .github/skills/impeccable -type f | wc -l
grep -n "impeccable-gate" .github/workflows/deploy.yml

# C26 audit fields
sed -n '111,128p' backend/app/models/tenant.py

# C27 the deleted import
sed -n '1411,1436p' backend/app/workers/tasks.py; ls backend/app/scripts/probe_llm_models.py
```
