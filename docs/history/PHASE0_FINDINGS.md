# PHASE 0 FINDINGS

**What a human needs to decide before implementation proceeds.**
Blocking gaps first, per spec-doc6 §18 rule 1.

**Document ID:** RPN-P0F-001
**Revision:** 2, 29 August 2026
**Branch:** `feat/specdoc6-activation`
**Companions:** `CONTRADICTIONS.md`, `GAP_MATRIX_V2.md`, `RUNBOOK_RECONCILIATION.md`,
`RUNBOOK_EDITS.md`, `RUNBOOK_OPEN_QUESTIONS.md`

---

## WHAT CHANGED SINCE REVISION 1

Revision 1 led with a blocking gap that no longer exists: it recorded the RBAC and Dashboard
specifications as absent after an exhaustive search. **The product owner supplied both later
in the same session**, and they are now filed unedited with provenance headers under
`docs/spec/`. Revision 1's `RBAC_DASHBOARD_DERIVED_REQUIREMENTS.md`, a paraphrase built to
work around the absence, **has been deleted**: keeping a derived restatement beside the
originals is exactly the duplicate-source drift spec-doc6 §10.1 rule 12 forbids. Everything
in it that had no counterpart in the real documents moved into `CONTRADICTIONS.md`.

The Runbook also reached **v1.1**, all nine assumption sites are reconciled, and
`runbook_data/` plus its parity test are built and green. **Phase 0's gate is met.**

Net effect on the register: it **grew**. Reading the real documents resolved eight
`RESOLVED-BY-DEFAULT` entries down to precedence-table resolutions and surfaced **thirteen
new contradictions**, seven of which are defects in spec-doc6's own citations of RBAC.

---

## BLOCKING

### B1. Part A is not on a live path, and it is worse than "not wired"

Unchanged from revision 1 and re-verified. spec-doc6 §0.1 already quotes the previous
phase's finding. Confirmed, with the call graph:

```bash
grep -rn "hiring\.\|miti\.\|siddhi\." backend/app/api backend/app/workers --include="*.py"
# no output
```

`app/services/hiring/`, `app/services/miti/` and `app/services/siddhi/` (**6,995 lines, 173
passing tests**) have exactly one non-test importer between them:
`app/scripts/worked_example.py`, a standalone `python -m` script.

**All six agents are live, and none of them runs Part A.**
`app/services/agents/identity.py:104,124,138,152,176` points every agent name at the **old**
implementations. Logs, traces and A2A artifacts show "Bodha", "Sutra", "Miti" and "Siddhi"
executing successfully today while the three-layer framework runs nowhere.

**Three modules have zero importers anywhere in `app/`**, and all three are named acceptance
criteria: `hiring/evidence_graph.py` (Department Evidence Graphs, 537 lines),
`hiring/ontology.py` (the skills ontology spec-doc6 §4.4 calls **"a fairness requirement"**,
228 lines), `hiring/swot_quality.py` (§18.5 rejection rules, 398 lines). Seven Part A tables
have zero readers and zero writers.

**Sharpened by the reconciliation.** Four of the nine assumption sites were not just
unverified guesses but materially wrong: `department_models.py` covered **5 departments where
the Runbook has 15**, so civil engineers, designers, architects, HR and skilled trades were
all scored against a generic model; `triangulation.py` contained **none of §13.2's seven
named benign explanations**; `situations.py` had **4 of 6 rows wrong, two by inversion**;
`company_dna.py` was **missing 5 of 12 sections and had invented 4**. None of it reached a
user, and the only reason is that none of these modules is reachable. That is not a
mitigation to rely on twice.

### B2. spec-doc6 D2's premise is false: gate G1 blocks nothing

D2 says, of existing jobs: *"**No new mechanism is needed to enforce this: gate G1 already
blocks evaluation without an approved scorecard.** Use it. Do not build a second enforcement
path."* Repeated at §4.3, §6.2 and in the §17 acceptance list.

```bash
grep -rn "scorecard_gate" backend/app --include="*.py" | grep -v __pycache__
```

G1 is defined at `backend/app/services/hiring/gates.py:121` and called from exactly one
place, `backend/app/services/miti/pipeline.py:290`, which has zero importers in
`backend/app/api/` or `backend/app/workers/`. Same for G2, G3 and G4.

**Consequence.** If Phase 6 runs on D2's wording it will archive scorecards and mark jobs as
requiring re-definition while evaluation continues against them unimpeded. G1 must be wired
in Phase 3 first. §16's ordering already puts Phase 3 before Phase 6; what is wrong is the
claim that no work is needed. `CONTRADICTIONS.md` C20.

### B3. RBAC requires a per-job assignment table; the repository has none

Now backed by quotable text rather than inference. RBAC §9.2
(`docs/spec/RBAC_SPECIFICATION.md:446-456`):

> Each job MUST have exactly one Recruiter.
>
> A Recruiter is associated with a job through an explicit job assignment.
>
> A Recruiter does not automatically have access to every job in the company merely because they hold the Recruiter role.

RBAC §5 (`:223-229`) makes three cardinality invariants mandatory: exactly one Recruiter per
job, exactly one Hiring Manager per job, many Interview Managers. §23 (`:970-1002`) draws the
ownership diagram.

```bash
grep -rn "job_assignment\|assigned_recruiter\|assigned_to" backend/app/models backend/alembic/versions
# no output
```

`jobs` carries one user reference, `created_by` (`backend/app/models/job.py:83-84`, nullable,
`ON DELETE SET NULL`), plus `approver_user_id` (`:195`).

**Four deliverables block on this:** the dashboard's RBAC-driven controls, the required role
by control by (assigned / unassigned / other-tenant) table test, three of four cardinality
invariants, and the Interview Manager role itself (which is scoped to a job by §13.1 and
§23). Build it early; it unblocks more than anything else on the list.
`CONTRADICTIONS.md` C5.

---

## DECISIONS THE OWNER MUST MAKE

Five `RESOLVED-BY-DEFAULT` entries remain. Each has a safe implementation already chosen and
marked at the code site; each needs confirming or overruling.

### The five open defaults

| ID | Question | Implemented by default | Why it matters |
|---|---|---|---|
| **C32** | The Runbook contains **no "Must-have hard-cap rule"**. Three separate band-capping mechanisms exist (§12.1, §12.2, §14.1). Are they three views of one cap, or three different caps? | CLAUDE.md's version: cap at Moderately Matching, applied last, on the score, as a `min` | spec-doc6 §2.2 asks for it in `bands.yaml` and §4.4/§17 make property-based tests of it an acceptance criterion. Its `source` citation will be the weakest in the whole data file |
| **C34** | §18.4's situation weight consequences are **arrows with no magnitude**. §11.3 supplies numbers for four of six types. **Scale-up and Succession have none anywhere.** | Those two re-weight nothing, marked `RUNBOOK-AMBIGUITY (§18.4)` | Not interpolating is the safe direction: a misclassification into an unweighted type costs nothing; an invented weighting corrupts the whole vector invisibly |
| **C35** | **"Weakly" appears as a third value inside an integer count** of independent originators, with no defined arithmetic. Unlisted source types have no default. | Weakly counts as **dependent** (0 additional originators); unlisted types are **dependent** | This is described as the **most common evidence pair in the product**, so it is the modal path, not an edge case. Counting it as independent manufactures corroboration, which raises a confidence figure a human then relies on |
| **C37** | **"Insufficient evidence" has two independent definitions** (§6.7 and §10.7) that can disagree in both directions | The **union**: insufficient if either says so | Gate G2 blocks on this predicate, and spec-doc6 §4.4 requires it to block "with an actionable reason". A gate cannot block on an undefined trigger |
| **C19** | Two contradicting four-grade scales are both live and both tested. Do stored `tier` values need backfilling? | Backfill as part of the D2 legacy reset | See "Defects found" below |
| **C46** | **Where was `impeccable` 4.1.1 installed from?** The installed `SKILL.md` declares 4.1.1; `npm view impeccable version` returns **3.6.0** | Ship as-is, with the gap recorded in `tools/design-tools.manifest.json` rather than papered over | `.github/workflows/deploy.yml:204-211` **gates CI** on `impeccable-gate.mjs`. A CI gate whose implementation cannot be reproduced can change what it enforces with no commit in this repository, and `.impeccable-exceptions.md` would still read as current |

### D8's numeric score contradicts a standing rule

spec-doc6 D8 rules that the **Ready Pick Score (0 to 100, plus band, plus confidence)**
renders in dashboard column 4. CLAUDE.md says:

> **NO NUMBERS REACH A CLIENT. EVER.** Not a score, percentage, rank, band index, "7.5/10", or "top 12%", in the UI, in an API response, or in an email.

spec-doc6 D8 asks for exactly this flag: *"Flag this ruling in the final report for the owner
to override if he wants the number gone from the dashboard too."*

**The rule is currently held well, and deliberately.** The one place a number could leak is
scrubbed at `backend/app/api/matching.py:341` via `client_breakdown()`
(`backend/app/services/matching.py:771-791`), which strips every `score` key and every bare
numeric value. The single documented exception is the radar chart's 1-to-4 band index
(`backend/app/schemas/assessments.py:191-196`). Reversing the rule for one column is cheap to
implement and expensive to contain: containment is a serialiser-level ban plus a
payload-traversal test in every export format, and none of it exists yet.

**Reading the real Dashboard document makes this worse, not better.** Column 4
(`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:118-152`) does not just add a number. It
adds a **five-band vocabulary with its own cut-points**: Ready to Pick Strong (>= 85), Ready
to Pick (72 to 84), Consider with Reservations (60 to 71), Not Recommended (< 60), Under
Review. With Column 3's A/B/C/Hold, that is **three grade vocabularies and four sets of
cut-points** for one candidate:

| Vocabulary | Cut-points | Where |
|---|---|---|
| Four-grade scale | 90 / 75 / 60 | `backend/app/services/rating.py:83-88` |
| Tier enum (**a defect**) | 90 / 70 / 50, middle two swapped | `backend/app/services/tiers.py:16-24` |
| Pre-Screen Grade A/B/C/Hold | unstated | Dashboard Column 3 |
| Ready Pick Score band | 85 / 72 / 60 | Dashboard Column 4 |

**Recommendation:** allow the number and the five bands in Column 4 (D8's ruling), and drop
**A/B/C/Hold** in favour of the four words in Column 3. A letter grade is a closer cousin of
a number than a word is, and it adds a fourth vocabulary to the same screen for no gain.
`CONTRADICTIONS.md` C1, C9, C19.

### The naming decision, and the domain half is now settled

**RBAC §15 (`docs/spec/RBAC_SPECIFICATION.md:787-791`) settles the domain** by giving the
canonical public job URL:

> ```
> https://readypick.ai/jobs/3252463dfbg43t4hfb
> ```

`readypick.ai` is canonical, stated by the rank-1 authority. That makes both repository
domains defects rather than open questions:

| Repository value | Status |
|---|---|
| `picready.com` (a typo missing the `k`) | **FIXED 2026-08-29.** `grep -rn "picready" backend frontend` now returns one hit, a historical comment at `backend/tests/test_staff.py:266` recording the correction. Every site and every assertion was updated |
| `readypick.ai` | **Now used in code**: `backend/app/api/jobs.py:98,101,1359`, `portal.py:467`, `schemas/jobs.py:145` |
| `noreply@pickready.app` | **OPEN, and the higher risk of the two.** `backend/app/core/config.py:93`, a **runtime default** for `smtp_from_email` |
| `hello@pickready.app` | **OPEN.** `frontend/app/(org)/org/billing/page.tsx:442`, a live `mailto:` in customer-facing billing UI |

**The two open ones need the owner, not a sweep.** Whether `noreply@readypick.ai` and a
monitored sales mailbox exist is an operational fact. Changing a From address to a mailbox
that does not exist trades one silent failure for a louder one. The runtime default is the
worse of the pair: a deployment that does not set `SMTP_FROM_EMAIL` sends **every outbound
email in the product** from a domain that may not exist, and bounces, SPF/DKIM failures and
spam classification all follow without raising. Note `config.py:94` already reads
`smtp_from_name: str = "ReadyPick"`, so the display name and the domain in the same struct
disagree about the brand.

**Independent of the mailbox answer:** `smtp_from_email` should have **no default**. A
required unset variable fails at startup; a wrong default fails silently at the first email.
Same argument C14 makes about `owner_email`'s default on the line above it.
`CONTRADICTIONS.md` C43.

**The product-name half still needs the owner.** spec-doc6 §10.2 recommends **Ready Pick
Now** as the product name and **ReadyPick** as the wordmark. Source-only counts:

| | `frontend/{app,components,lib}` | `backend/app` | `docs` |
|---|---|---|---|
| ReadyPick | 95 | 80 | 12 |
| Ready Pick Now | 0 | 0 | 18 |
| pickready | 6 | 152 | 5 |

`Ready Pick Now` appears **nowhere in code**. `ReadyPick` is what 187 code sites already say.
Most of `backend/app`'s 152 `pickready` are Celery task names (`pickready.send_email` x19,
`pickready.run_matching` x14) and JWT/cache namespaces, which CLAUDE.md records as deliberate
and unrenameable during a rolling deploy. **The RBAC specification's own title uses "Ready
Pick Now" while its example URL uses `readypick`**, which is precisely the split §10.2
recommends, so the recommendation now has rank-1 corroboration and needs only confirmation.

### RBAC §15 requires a public job identifier this codebase does not have

Not a formatting question, and revision 2 of this document got it wrong by treating it as
one. `grep -rn "public_job_id" backend/app --include=*.py` **returns nothing.**

RBAC §15 (`docs/spec/RBAC_SPECIFICATION.md:785-791`): *"Each published job receives a
**unique public identifier**"*, with the example
`https://readypick.ai/jobs/3252463dfbg43t4hfb`. **That example is not a UUID.**
`public_job_url` (`backend/app/api/jobs.py:97-105`) returns `{base}/apply/{job_id}`, where
`job_id` is `jobs.id`, the raw internal primary key.

**Adding it is a column, a backfill and a lookup path. It is not a rename.** Three things
follow, and two of them are easy to argue away and should not be:

1. **The public URL currently leaks the internal primary key, and that is not by itself a
   hole.** §33 is explicit that obscurity is not authorization, and the repository already
   satisfies §33 here: `backend/app/api/jobs.py:1380-1391` refuses unless `ratified_at` is
   set, `archived_at` is null **and** the posting window is open, returning 404 for every
   failure. **What the leak costs is coupling:** every email, log line and third-party job
   board carrying a public link also carries an internal id, and **the two can never be
   rotated apart.**
2. **§15's example is short enough to quote over the phone; `jobs.id` is not.** That is the
   same usability property `backend/app/services/reference_code.py:8-11` argues at length
   (*"UUIDs nobody can hold in their head or read down a phone line"*). The specification
   appears to select for it deliberately, and the product has already built the machinery.
3. **The implementation already exists.** `reference_code._segment` (`:83-101`) produces
   HMAC-derived, one-way, Crockford-base32 segments with per-position domain separation. It
   must stay a **label, never a permission** (`:35-37`), and §33's checks stay unchanged.

**Not urgent.** Nothing is exposed that §33 does not already gate. But it is a schema change
that gets more expensive the longer public links accumulate, which argues for doing it before
Phase 8. `CONTRADICTIONS.md` C44.

**The path is a separate, settled decision.** §15 says `/jobs/{...}`; the code serves
`/apply/{...}`, deliberately, and it stays: published links are already in candidates'
inboxes, in `email_log` bodies and in traces a rolling deploy is still writing, so a route
change needs a redirect story first. This is the argument CLAUDE.md already accepts for the
`ppi` module names. Recorded as a **known divergence with a stated reason**, not a defect
awaiting a fix. The one obligation that is not optional: spec-doc6 §13.2's ALB listener rule
is **literal**, so a rule written for `/jobs/*` against an app serving `/apply/*` 404s the
public link **in production and nowhere else**. Derive the listener rule and `public_job_url`
from one constant. `CONTRADICTIONS.md` C45.

### Two role-mapping questions, one settled and one open

**C22 is SETTLED and needs no decision, only awareness.** RBAC §5 (`:211`) names the role
**"Client Super Admin"**, and §7.1 (`:258-266`) scopes it: *"Each client **organization**
MUST have exactly one active Super Admin."* §7.2: *"ultimate authority **within the client
organization**"*. It is tenant-scoped.

Every RBAC "Super Admin" cell maps to **`Role.client`** (`backend/app/models/enums.py:9`),
whose inline comment already says *"The customer's own Super Admin."* It does **not** map to
`Role.super_admin` (`:6`), the platform owner. Mapping it there would grant ReadyPick
platform staff tenant-scoped write authority over client hiring data, which RBAC §4
(`:163-166`) forbids outright, and would break the Provider Portal's read-only-by-absence
guarantee in the same change. **Two corollaries:** the uniqueness constraint is **per
tenant**, not global (a global one breaks the second customer onboarded); and §7.1 requires a
**controlled Super Admin transfer mechanism** that nothing in the repository implements.

**C23 is open.** RBAC has one "HR Manager"; the repository has **two** roles at the same
rank, `recruitment_manager` and `hr_manager`, kept side by side deliberately
(`backend/app/models/enums.py:10-14`: *"a role a customer already assigned must not silently
change what its holder can do"*). Grant every HR Manager cell to **both**, or migrate
deliberately. Granting to one silently removes authority from existing accounts.

---

## SPEC-DOC6 CITATION DEFECTS: 7 OF 25 SECTIONS CHECKED

All 31 RBAC citations across 22 distinct sections in spec-doc6 were read against
`docs/spec/RBAC_SPECIFICATION.md`, plus three sections cited without the `RBAC` prefix.
**25 sections checked. 18 accurate. 7 inaccurate or overreaching.** Full table with both
quotes per row in `CONTRADICTIONS.md` C38.

**Three that grant or state something the source does not, and matter most:**

1. **§9.6.** spec-doc6 C13 says *"HR Manager and Super Admin publish only as an audited
   exception."* §9.6 names **only the Super Admin**. The HR Manager's publish authority is an
   asterisked §24 cell whose footnote says *"intentionally conservative and may require an
   explicit future product decision."* **Withhold the HR Manager publish capability** pending
   that decision, rather than granting it on a misquote. (C13)
2. **§33.** spec-doc6 §9.1 says *"Cross-tenant reads return 404, never 403, so existence is
   not disclosed (RBAC §33)."* **§33 never mentions a status code.** The rule is right and
   the repository already argues for it independently
   (`backend/app/api/jobs.py:1362`, *"never reveal existence"*), but it is spec-doc6's rule,
   not RBAC's, and a docstring citing §33 for it would carry a false provenance. (C17)
3. **§10.3.** spec-doc6 §4.3 attributes the Hiring Manager's weight-traceability review
   screen to §10.3. §10.3 is *"JD Review"*: Review / Edit / Refine / Finalize. **Nothing
   about traceability, weights or a review screen.** The requirement is defensible on its own
   terms (§12 makes finalization authoritative, so the Hiring Manager must see what they are
   making authoritative) but it must be cited to spec-doc6. (C38)

**One that reverses the safe default.** spec-doc6 §4.3 says *"Do not accidentally implement a
JD rejection path."* RBAC §24 (`:1023`) grants **Reject JD: Super Admin YES, HR Manager
YES**. §11 forbids it to the **Hiring Manager** only. Reading §4.3 as a blanket prohibition
produces a **missing** capability. This is the rare case where spec-doc6 §20's
restrict-more rule gives the wrong answer, because rank 1 affirmatively grants it:
restricting more is only right when the higher authority is silent. (C39)

**Three overreaches in the restrictive direction, all kept:** §13.4 ("primary" Team Review
participants, where RBAC says "can participate"), §29 ("nobody may edit another's remark",
where RBAC states it of Interview Managers), and §35 (Company DNA added to a versioning list
that has eight items and does not include it, correctly, since Company DNA post-dates that
document). Each is stricter than its source and survives under §20; each needs its citation
corrected so the provenance is honest.

---

## THE 39/40 QUESTION, ANSWERED

Unchanged from revision 1. **Neither.** spec-doc5 says 39 (twice). The Dashboard document
says 40 (`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md:351`). **The Runbook, the rank-2
authority, says nothing at all:**
`grep -niE "39[- ]aspect|40[- ]aspect|validation aspects" "Readypick Hiring Philosophy.md"`
returns zero hits.

The implemented form, `backend/app/services/candidate_profile_form.py`:

- **25 `FormField` instances** across **7 `FormSection`s**.
- **20 distinct `display_no` values**: `1`, `20` to `35`, `37`, `38`, `39`. **There is no 36.**
- Its own docstring at `:18-21` gives the source numbering: items 1 and 20 to 39, with the
  education table occupying 2 to 19, and no item 36.

Arithmetic: `1` + `2..19` (18, collapsed into one `education_table` field) + `20..35` (16) +
`37..39` (3) = **38 numbered aspects**, implemented as **25 machine fields** in **7
sections**.

**39 is the highest display number, not a count.** Someone read the last item's label and
missed that 36 is absent. **40 is a legacy PRD label** with no arithmetic behind it, repeated
in 20 code comments including the module's own opening line.

Nothing broke, because tests derive from `len(ALL_FIELDS)`
(`backend/tests/test_functional_assessment.py:456`,
`backend/tests/test_validation_answers.py:24,42`) and a structural invariant holds at
`candidate_profile_form.py:426`. **Do not implement a second form**, correct both documents,
sweep the 20 comments, and add a test pinning `len(ALL_FIELDS) == 25` so the next person who
"fixes" the form to match a document gets a red test. `CONTRADICTIONS.md` C8.

---

## DEFECTS FOUND, WORTH FIXING BEFORE ANYTHING ELSE

### The Runbook authorised filtering on age, caste, gender and employment gaps

**The most dangerous single defect found in any document this phase.** The Runbook's Decision
Contract clause **C5** cited **§12.4**, the list of **PROHIBITED** disqualifiers (age, caste,
gender, employment gaps), where it meant **§12.3**, the legitimate one.

**Read literally, C5 authorised automatic filtering on those attributes**, and an implementer
following the citation faithfully could have pointed at the Runbook as their authority.
Nothing downstream would have caught it: the filter would have looked like a correctly
sourced rule.

**Repaired to §12.3 in Runbook v1.1**, logged in `RUNBOOK_EDITS.md`. Recorded here anyway,
because this product has already shipped a defect in exactly this area once (CLAUDE.md: the
first disqualifier matcher *"refused 'Must hold a valid CA licence' because 'hold' contains
'old', while accepting 'No candidates over 45'"*), and because it is the strongest available
argument for extending `test_runbook_parity.py` from **value** parity to **citation-target**
parity: a citation annotating a permitted-value list must fail loudly if it resolves to a
section whose heading says "prohibited". `CONTRADICTIONS.md` C31.

### Three Layer 1 baselines breach a clamp §11.4 calls absolute

| Department, seniority | Dimension | Baseline | Bound |
|---|---|---|---|
| Mechanical, Fresher | D1 | **0.42** | ceiling 0.40 |
| Skilled Trades, Entry | D1 | **0.44** | ceiling 0.40 |
| Data, Fresher | D2 | **0.04** | floor 0.05 |

A Layer 1 baseline is the value every Layer 2 and Layer 3 multiplier is applied to, so an
out-of-range baseline does not stay 0.02 out of range: it is multiplied. Fresher and
entry-level rows are also exactly the population where a weighting error is least visible,
because those candidates have the least evidence to contradict it.

Clamp to 0.40 / 0.40 / 0.05, **record every clamp** (CLAUDE.md: *"a clamp that left no trace
is indistinguishable from an input that was already in range"*), and **propose** the source
correction rather than applying it: changing a baseline weight is on spec-doc6 §2.1's **not
permitted** list. `CONTRADICTIONS.md` C36.

### Two contradicting four-grade scales, both live, both tested

CLAUDE.md: *"There is ONE rating scale, it has FOUR grades, and it lives in
`services/rating.py`. The cut-points are unchanged (90 / 75 / 60)."*

| | Site | Cut-points | Order |
|---|---|---|---|
| `rating.grade_for_percent` | `backend/app/services/rating.py:83-88` | 90 / 75 / 60 | Highly, **Matching**, **Moderately**, Not |
| `tiers.assign_tier` | `backend/app/services/tiers.py:16-24` | **90 / 70 / 50** | Highly, **Moderately**, **Matching**, Not |

`assign_tier` is live: `backend/app/services/matching.py:1775` writes `link.tier` from it, and
`tier` is serialised to clients at `backend/app/schemas/matching.py:57` and
`backend/app/schemas/candidates.py:116`. Both are pinned by tests:
`backend/tests/test_tiers.py:29` asserts `assign_tier(75.0) == Tier.moderately_matching`
while `rating.grade_for_percent(75)` returns `"Matching"`.

**For one candidate at 75, the tier column says "Moderately Matching" and the report says
"Matching". At 65 it is inverted the other way.** The whole 60-to-75 range disagrees with
itself, and `backend/app/models/enums.py:65-69` carries the wrong thresholds in its comments
and the inverted order in its declaration.

Fix: make `assign_tier` a thin alias over `rating.py`, as `matching_label`
(`backend/app/services/matching.py:688-696`) already is; rewrite `test_tiers.py` to 90/75/60;
correct the enum comments; add a test asserting the two agree at every integer 0 to 100.
`CONTRADICTIONS.md` C19, C29.

### A registered Celery task imports a deleted module

`backend/app/workers/tasks.py:1411` registers `pickready.probe_llm_models`; its body at
`:1435` does `from app.scripts.probe_llm_models import probe`;
`ls backend/app/scripts/probe_llm_models.py` returns "No such file or directory". The import
is function-local, so the worker starts and the task registers. It raises `ModuleNotFoundError`
only when dispatched. Check `celery_app.py` for a beat entry: if there is one, it is failing
on a timer right now. `CONTRADICTIONS.md` C27.

### The vendored design skills are handled, with one residual gap

Closed since revision 2. 302 files were untracked and unignored; `.gitignore:52-67` now covers
them, and `tools/design-tools.manifest.json` plus `tools/install-design-tools.sh` exist per
spec-doc6 D4. **The manifest records `null` with a stated reason where no SHA was recoverable
rather than inventing a pin**, which is what makes the residual gap legible instead of hidden.

**The residual gap is `impeccable`, and it is the one that gates CI.** `skills-lock.json` pins
**3** entries by content hash, none of them `impeccable`
(`grep -c "impeccable" skills-lock.json` returns 0), while `impeccable` is **148 files per
copy and is installed twice** (`.claude/skills/impeccable/` and `.github/skills/impeccable/`
are byte-duplicates, 296 of the 302 files). `.github/workflows/deploy.yml:204-211` runs
`impeccable-gate.mjs`, which per CLAUDE.md exits non-zero on any finding not listed in
`.impeccable-exceptions.md` with a reason.

**Two fixes need nobody's input:** delete one of the two identical copies, and record the
installed tree's content hash in the manifest, which detects drift exactly as
`skills-lock.json` does for the other three. **One needs the owner:** the installed skill
declares `version: 4.1.1` while npm publishes **3.6.0** under that name, so
`install-design-tools.sh` reproduces three of four tools. `CONTRADICTIONS.md` C46.

### Part A is untracked in git

`git status --short` shows `??` on `backend/app/services/{hiring,miti,siddhi}/`,
`backend/app/models/hiring.py`, migration `0059`, four test files, **and the Runbook itself**.
D1 requires three activation commits *"each revertable with `git revert` without touching the
other two"*. Reverting an activation commit onto an untracked baseline restores nothing.
**Commit Part A before activation begins.** `CONTRADICTIONS.md` C28.

---

## SMALLER CORRECTIONS TO SPEC-DOC6'S OWN CLAIMS

| Claim | Reality |
|---|---|
| §2.2: extract *"the Must-have hard-cap rule"*, *"per-seniority rubric anchors"*, situation weights *"exactly as §18.4 states them"*, and *"independence-group rules"* | **All four are unsatisfiable as written.** The hard-cap phrase and rule do not exist (three separate mechanisms do, C32); per-seniority anchors exist for **1 department of 15** and anchors are otherwise universal per dimension (C33); §18.4 gives arrows with no magnitude and two of six types have none anywhere (C34); "weakly" is an undefined third value inside an integer count (C35) |
| §2.3: *"finds the 9 sites the previous phase marked"* | **6** code sites carried the marker. All nine assumption sites are now reconciled and **zero markers remain**, with **0 CONFIRMED, 8 CORRECTED, 1 CORRECTED-in-part** |
| §3.2: *"commit it as `docker-compose.test.yml`"* | **Already exists** at the repository root: `pgvector/pgvector:pg16` (`:42`), `redis:7.2-alpine` (`:75`), MinIO (`:99`). Verify pg16 against `infra/modules/rds`; §3.2 says a mismatch is a defect |
| §6, D2: purge `Evaluation` rows | The `evaluations` table has **never been written to** and is empty by construction. The real purge targets are `functional_skills_reports`, `report_dimensions` and `job_candidate_links.{match_score, match_breakdown_json, tier}` |
| §13.2: *"The public job URL path (`/jobs/{public_job_id}`)"* | Three paths disagree: RBAC §15's example is `/jobs/{id}`; the repo serves `/apply/{job_uuid}` on the frontend and `GET /jobs/public/{job_id}` on the API. §15's requirement is substantive (unauthenticated), not a routing spec, so `/apply/` satisfies it. **But the ALB listener rule §13.2 asks for is literal**: if it says `/jobs/*` while the app serves `/apply/*`, the public link 404s in production and nowhere else. Derive both from one constant (C42) |
| C17: the public job ID format `3252463dfbg43t4hfb` | **Not a format specification.** It appears in RBAC §15 under "Example" and in §33, whose entire point is that knowing it grants nothing. **No format is specified anywhere in RBAC.** The repo's raw job UUID satisfies it and is stronger. The candidate System ID **is** implemented and matches its example (`JSRS-Y4BN-8HGX`) exactly (`backend/app/services/reference_code.py`), is HMAC-derived and one-way, and authorises nothing |

---

## STALE STANDING CONTEXT TO CORRECT IN PHASE 11

`CLAUDE.md:19-21` states in capitals that the Runbook *"IS NOT IN THIS REPOSITORY OR ANYWHERE
ON THE MACHINE"*, and `GAP_MATRIX.md:12-45` says the same at length. **The Runbook is present
and is now v1.1.** So are both specifications, under `docs/spec/`.

Two secondary corrections while updating:

1. **The Runbook's filename on disk uses spaces**: `Readypick Hiring Philosophy.md`. Every
   specification writes `Readypick_Hiring_Philosophy.md` with underscores. Tooling that opens
   it by the underscored name will fail.
2. **§16's subsections are now numbered §16.1 to §16.12** in v1.1. Anything citing "Section N"
   under §16 must cite §16.N. Appendices D and E were renumbered so they stop colliding with
   the dimension names D1 to D5 and the tier names E0 to E5.

`CLAUDE.md` must also gain, per spec-doc6 §14: the precedence order (§0.2) **with
`docs/spec/` named as the canonical location for the two specifications**, the §1 decisions,
the anti-slop rules, the no-flag/one-path rule, the verification honesty rule, and the
location of `runbook_data/` and its parity test. `CONTRADICTIONS.md` C24.

---

## CONTRADICTION REGISTER SUMMARY

**47 recorded.** 18 seeded from spec-doc6 §15; 29 found here (C0, C19 to C46).
**41 resolved by the precedence table. 6 remain `RESOLVED-BY-DEFAULT`: C19, C32, C34, C35,
C37, C46**, each listed with its default and its owner question above.

Three further entries are **known divergences with a stated reason** rather than open
questions: C45 (the public job path) and the two naming instances in C43, which are
operational facts only the owner can settle.

Revision 1 listed nine as resolved-by-default. Seven of those (C0, C4, C5, C6, C7, C11, C16,
C22) collapsed to precedence-table resolutions the moment the real documents were read. That
is the register working as intended: eight defaults were provisional readings of an absent
authority, and none survived contact with it.

Full register with both sources quoted, the precedence rank applied, and the enforcing test
or `ENFORCEMENT-PENDING` named for each: `CONTRADICTIONS.md`.
