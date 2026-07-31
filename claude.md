# claude.md, PickReady Build Conventions

## Current hard rules, PPI + the four-grade scale (2026-07-30)

- **There is ONE rating scale, it has FOUR grades, and it lives in
  `services/rating.py`.** Highly Matching, Matching, Moderately Matching, Not
  Matching. It replaced the product's two parallel five-label scales, the
  assessment's *Very High / High / Medium / Low / Developing* and matching's
  *Highly Matching / Matching / Moderate / Low / No Matching*, which had to be
  kept in step by hand in two modules and gave a reader no way to know that a
  "High" and a "Matching" meant the same thing. `matching.matching_label` and
  `functional_assessment.rating_label` are now thin aliases over it and must
  stay that way. The cut-points are unchanged (90 / 75 / 60), so a report
  written before this release regrades identically, with the old Low and
  Developing collapsing into Not Matching. Boundaries stay inclusive upward
  (rule 8).
- **PPI replaced PFI, and the difference is per-job versus per-product.** The
  PickReady Functional Index was ONE fixed dimension set per grade, reused
  across every job. PickReady Profile Intelligence generates a FRESH framework
  for every job from that job's own JD: at least 5 Primary Skills, 5 Secondary
  Skills and 5 Behavioural Competencies, more when complexity warrants it.
  `services/pfi_bank.py` and `services/validation_bank.py` are DELETED, and
  `tests/test_functional_assessment.py` asserts they cannot be imported. PPI is
  proprietary PickReady work and is never associated with DISC, MBTI, Hogan,
  CliftonStrengths or any other licensed instrument.
- **The framework is per JOB, the questions are per CANDIDATE, and confusing
  the two breaks the product's only comparability guarantee.** A saved
  framework is the fixed evaluation criteria for every candidate on that job.
  The questions probing it are generated from the JD, the framework, AND that
  candidate's resume, so two candidates get different questions against
  identical criteria. Counts are fixed by the CANDIDATE's grade, never by the
  job: 25 / 20 / 15 / 10 for non-managerial / managerial / leadership / CXO.
  Note the direction, MORE questions for a junior candidate.
- **"Culture" is refused as a Behavioural Competency, at three layers.** The
  generator prompt forbids it, `ppi.framework_is_complete` rejects it at save,
  and a Postgres CHECK on `job_competencies` refuses the row. A prompt
  instruction is a request, not a guarantee, and the Hiring Manager's Edit
  control can type anything. Cultural fit cannot be assessed accurately from a
  single assessment and PPI does not claim otherwise.
- **The manual review gate is BACK, it covers BOTH halves, and approving one
  does not open the job.** `jobs.assessment_status` starts at
  `questions_pending_review` and reaches `ready_for_candidates` only when
  `questions_approved_at` AND `framework_approved_at` are both stamped
  (`api/assessments._refresh_setup_status`). Until then the conversation 409s
  and `select-candidates` 409s, so nobody is mailed an assessment they cannot
  open. This deliberately REVERSES the 2026-07-25 decision that removed the
  gate. `pickready.remind_unapproved_technical_questions` is live again and is
  what stops the one manual step going silent.
- **Publishing and assessment readiness are independent.** A published job
  takes applications and ranks them immediately; it just cannot invite anyone
  yet. Making publish wait on the review would hold the 30-day posting window
  closed over a step that only affects what happens after someone applies.
- **A saved framework is frozen, and reopening is refused once anyone has been
  assessed.** A report is immutable and states a grade against those exact
  criteria; letting the criteria change underneath it would make two reports on
  the same job incomparable, which is the one property the framework exists to
  guarantee.
- **Validation is six MANDATORY FIELDS on the application form, and nothing
  scores it.** Current CTC, expected CTC, notice period, joining date, document
  readiness, and "Why does this role interest you?" in the candidate's own
  words. `services/application_validation.py` is the single source of the field
  list, served to the form so the form and the report's Validation section
  cannot drift. It lands on `job_candidate_links.validation_json`, NOT the
  candidate profile: current CTC and notice period are answered per opportunity
  and change over time. Capturing it before the conversation is what lets a
  recruiter drop a candidate outside the budget before a credit is spent. The
  RECRUITER, not any agent, decides whether stated interest is genuine.
- **There are TWO scoring agents, not three.** Technical (per-question rubric)
  and PPI (against the saved framework), fanning out in parallel and joining at
  synthesis. `validation_capture` is a graph node but NOT a scorer: it copies
  the application's fields into the report shape and touches no model.
- **Report order is fixed: AI Score, then the PPI Assessment.** AI Score (four
  matching parameters, 25-30 word remarks) is the pre-assessment resume
  snapshot; Overall + Primary Skills + Secondary Skills + Behavioural
  Competencies (45-50 word remarks) is the post-conversation assessment. They
  are deliberately NEVER merged: a close match confirms the resume was
  accurate, and a gap is itself signal. Then Validation, then 8-10 suggested
  interview questions anchored on whatever graded Moderately Matching or Not
  Matching. Technical items are scored and anchor those questions but are not a
  rendered section.
- **FOUR radar charts, each plotting TWO shapes.** Overall, Primary, Secondary,
  Behavioural, each overlaying the job's required level and the candidate's
  assessed level on the same axes. Built from the SAME dimension rows the
  sections render, so a chart can never disagree with the text beside it. No
  number anywhere: not an axis tick, not a data label, not a tooltip. The
  legend names the two shapes by word. The Overall chart plots the three PPI
  category aggregates and EXCLUDES technical, which carries no job-requirement
  level and would force the requirement shape to invent a value for that spoke.
- **`report_dimensions.required_level` is COPIED onto the report, never joined
  to the live framework.** A written report is a permanent record of the
  criteria it was written against, and the job's framework may be edited later.
- **The four matching parameters carry NO mathematical weightage.** The
  0.35 / 0.30 / 0.20 / 0.15 table is gone and `services/matching.py` has no
  `WEIGHTS` symbol; `tests/test_scoring.py` asserts its absence. Two things
  were wrong with it: the weights were shown to the client as "35% role-fit
  weighting" beside each remark, which is a number reaching a client, and a
  fixed weighting asserts that skills matter 2.3x more than education for every
  role in the product, an arithmetic the comments do not perform. The internal
  overall is now their plain mean and orders a list; it is never displayed.
- **Report REUSE is retired.** `retake.PORTABLE_CATEGORIES` is an explicit
  EMPTY frozenset and `copy_report` never copies. Under PPI both the framework
  and the technical bank come from each job's own JD, so every section is
  job-scoped and carrying one across would state a grade against criteria the
  candidate was never assessed on, the identical error that always kept the
  matching section from travelling. The six-month classification still runs so
  the candidate is told why they are answering questions again.

## Current hard rules, subscriptions + the credit ledger (2026-07-28, later)

- **A customer's SUBSCRIPTION hangs off `tenants`, not `companies`.** The spec
  writes `ALTER TABLE companies ADD COLUMN razorpay_...`; in this schema a
  customer IS a `tenants` row, and `companies` is the client-authored page that
  does not exist until they first sign in. Billing on `companies` would be
  unreachable for exactly the customer who has just paid on the landing page.
  Same substitution for `credit_ledger.related_application_id`, which maps to
  `job_candidate_links`.
- **One credit is 60 integer SUB-UNITS, and nothing in the money path is a
  float.** Consumption is 1, 1/3, 1/15, 1/20 of a credit; LCM(1,3,15,20) = 60,
  so completed = 60, incomplete = 20, no-show = 4, old-profile review = 3.
  Division happens ONCE, at display, through `Decimal`.
- **The balance is `SUM(subunits_delta)`, never a stored counter.** A customer
  disputing usage gets a statement, not a number. `tenants.credit_deficit` is
  the one derived cache, and it exists only so the invitation gate does not
  re-aggregate the ledger on every send.
- **Every credit write carries a UNIQUE `idempotency_key`.** Razorpay delivers
  webhooks at least once and Celery redelivers tasks, so a double grant is the
  DEFAULT behaviour unless something prevents it. Checkout-verify and the
  webhook derive the SAME key from the payment id, which is why both can run for
  one payment and the customer is granted one month.
- **A completed assessment is charged even into the negative; the NEXT
  invitation is what gets blocked.** The work is already done and cannot be
  undone, so refusing the charge would only lose the revenue.
  `POST /pipeline/jobs/{id}/select-candidates` answers 402 with both ways out
  named.
- **Razorpay Subscriptions, never Orders.** An Order is a one-time charge and
  would silently turn a monthly plan into a single payment. The Checkout
  signature for a subscription is `payment_id|subscription_id`, the REVERSE of
  the Orders flow; getting it backwards fails 100% of real payments.
- **The Key Secret is server-side only and never reaches the frontend.** The
  browser gets the Key ID from `GET /billing/config` at runtime, not from a
  build-time `NEXT_PUBLIC_` variable, so the frontend container never needs the
  `.env` at all. `api-keys.txt` is gitignored and was never committed.
- **`checkout_ready` is about the SERVER's credentials, not the plan row.**
  Razorpay Plans are minted lazily on first subscribe, so keying it off
  `razorpay_plan_id` disables every Subscribe button on a fresh install and the
  only thing that could populate that column is the button it just disabled.
- **A job renewal restamps `posting_start_date`, and that is the ONLY thing
  that distinguishes an Old Profile.** `profile_age` is DERIVED from
  `link.created_at < job.posting_start_date`, never stored. Renewal is refused
  while a posting is still live, for the same reason publish refuses a second
  stamp. An Old Profile is ranked, listed, opened and assessed identically; the
  label is provenance and billing, never access.
- **Interactive LLM calls are capped at 15s per attempt and 30s in total;
  background ones are not.** The latency brief asks for a flat 10 to 15s cap on
  every call. Applied to `report_synthesis` that does not make the product
  faster, it makes every report fail and then retry. The split is by whether a
  request handler is blocked, and BOTH bounds are needed: four attempts at 15s
  is a 60-second request with a 15-second timeout on it.
- **No em dash in a STRING, in either language.** The 2026-07-28 sweep covered
  `frontend/` and the database; it did not cover backend Python, where 123
  em dashes sat in `detail=` messages, stage labels, profile-form options and
  seeded content. The same sweep covers every file added since. `tests/test_platform_audit.py` now asserts this, along with
  the DISC/MBTI/Hogan sweep, the no-OTP-in-any-portal rule, Gmail-SMTP-only, and
  no-numbers-to-a-client. A character class that MATCHES a dash is data, not
  prose: build it from `chr(8212)` so a repo-wide sweep cannot rewrite the code
  that strips it.

## Current hard rules, BD Portal, unified JD, procurement types (2026-07-28)

- **There are now FOUR portals, and the fourth is the Business Development
  Portal** (`/bd` in the UI and in the API). It is where PickReady's own sales
  team works leads and closes customers. The other three are unchanged:
  Provider Portal (`/admin`, `/provider`), Customer Portal (`/org`,
  `/companies`), Candidate Portal (`/portal`). A `bd` user is PLATFORM staff:
  `tenant_id` is NULL, the token carries the OWNER audience, and `bd` must
  never be added to `_ORG_ROLES` (that path demands a tenant they do not have).
- **A signed agreement CREATES a tenant, in a third `prospect` status.**
  `PATCH /bd/leads/{id}/agreement` with `true` mints a `tenants` row and links
  it, because a customer IS a tenant. Setting it back to false or null CLEARS
  the link and ARCHIVES the tenant, never deletes it, and
  `bd_leads.promoted_tenant_id` is permanent so a re-signed lead reuses its
  original company instead of minting a duplicate. The Provider Portal's
  customer list still accepts only `active | archived | all`, so a prospect
  cannot appear there as though it were live.
- **AI Reach returns two segments and the first one never touches the network.**
  `similar_to_customers` is computed from PickReady's own tenants and jobs and
  is computed FIRST; `from_internet` is a LangGraph agent over Tavily. With no
  `TAVILY_API_KEY` the internet segment returns `status: "unconfigured"` with a
  plain message and the page still works. Retrieved web content is DATA, never
  instructions, and the evaluate node says so explicitly.
- **`confidence_label` on an AI Reach card is a word, never a number.** High,
  Medium, Low. The no-numbers rule covers discovered jobs exactly as it covers
  candidate ratings.
- **A job description is ONE markdown document.** `jobs.jd_markdown` is
  canonical; the per-section columns are DERIVED from it and kept populated so
  nothing downstream breaks. The seven separate text boxes are gone from the
  Create Job form. The sequence is draft, then edit, then publish: publishing
  with an empty `jd_markdown` is refused.
- **`level` is superseded by an experience band.** `experience_min_years` and
  `experience_max_years`, with a Postgres CHECK that min never exceeds max.
  `level` survives only for jobs created before 2026-07-28 and is not collected
  on the form. `reportees` and the JD generator's `company_context` were
  DROPPED, not deprecated.
- **Every candidate link carries `source_type`: `applied | sourced |
  databank`.** Applied means they came through PickReady, sourced means a
  third-party link, databank means the recruitment team bulk-uploaded them.
  This is provenance for DISPLAY and filtering ONLY. Nothing may branch on it:
  all three are parsed, embedded, matched and assessed identically. Bulk upload
  is `POST /jobs/{id}/candidates/databank`, at most 25 files, partial success
  allowed so one unreadable PDF cannot discard the other 24, and parsing is a
  Celery task as always.
- **`shortlisted` stays in the FSM but is no longer OFFERED as a manual move.**
  It is the only route into `interview_scheduled` and `offer_extended`, it is
  written by `api/candidates.decide_profile`, and historic applications sit in
  it, so deleting it would strand them. Only its offer is withdrawn, via
  `hiring_pipeline.MANUAL_TRANSITION_EXCLUDED`. The UI renders
  `allowed_transition_options` from the server and hardcodes no stage list.
- **A BD account is reserved, never credentialed.** `POST /admin/bd-users`
  writes a `users` row with `role = 'bd'`, `tenant_id = NULL`, status
  `invited` and no `firebase_uid`; the first proven Firebase sign-in on that
  email binds the uid and flips it to `active`
  (`api/auth._finalize_single`). PickReady stores no password and sends no
  invite token for BD, so a Firebase identity must exist for the address
  before the first login. Disable is the reversible switch and there is no
  delete route: a BD rep owns leads (`bd_leads.owner_user_id`).
- **NO EM DASHES anywhere in the UI, INCLUDING IN DATA.** Not in labels, helper
  text, empty states, toasts, emails, page titles or generated JD text, and not
  in seeded or generated CONTENT either. Sweeping `frontend/` for U+2014 only
  covers what the code writes; `jobs.about_company`, `work_life`, `benefits`
  and `jd_json` render straight onto the public application page and broke the
  rule just as visibly (fixed in `0025_strip_em_dashes`). Check both the source
  tree AND the database.
- **Text is never grey, enforced at the TOKEN.** `globals.css` sets
  `--muted-foreground: var(--ink)` in both themes, so the shadcn primitives'
  built-in `text-muted-foreground` resolves to pure ink. Do not chase call
  sites in `components/ui/**`; fix the token if it ever drifts.
- **The brand is the Readypick.ai logo lockup** at
  `frontend/public/brand/readypick-logo.png`, with the indigo-violet primary
  `#5028E0` on ink `#080820` sampled from it. Tokens and rules live in
  `docs/spec/DESIGN_BRIEF.md`, which UI work reads BEFORE inventing its own.
  The logo already contains a wordmark, so it is never rendered beside a text
  "PickReady". ASSUMPTION, still open with the client: the logo says
  "Readypick.ai" while the product is named PickReady.
- **Page metadata must not repeat the site name.** `app/layout.tsx` sets a
  `%s | PickReady` template, so a page title is just "Sign in".
- **The frontend dev container does not see file changes over the Windows bind
  mount.** Restart the `frontend` service after editing, or you will verify
  against stale output and believe a change did not work.

## Current hard rules — Provider Portal (2026-07-27)

- **Three portals, three names, never interchanged.** *Provider Portal* is the
  PickReady owner's console (`/admin` in the UI, `/provider` in the API).
  *Customer Portal* is a client company's own dashboard (`/org`,
  `/companies`). *Candidate Portal* is `/portal`. A **customer** is one
  onboarded client company.
- **A customer IS a `tenants` row, not a `companies` row.** `tenants` carries
  the customer identity (name, industry, profile) and exists from onboarding;
  `companies` is the client-AUTHORED candidate-facing page and does not exist
  until the client signs in. Compliance documents, the archive lifecycle and
  the Provider-editable metadata therefore hang off `tenants`.
- **The Provider is READ-ONLY over the customer's own data, enforced by
  ABSENCE.** `api/provider.py` exposes no route that writes a contact detail, a
  team member, or a compliance document — not a handler that checks a flag, no
  route at all. The Provider may edit exactly `industry`, `website_domain`,
  `notes` and the archive flag; `CustomerUpdateIn` has no other fields.
- **Archive is a reversible hide; delete is not on this screen.** Archiving
  sets `tenants.status` and stamps `archived_at`, touching no job, application,
  report or user; unarchiving CLEARS `archived_at`. The irreversible
  `DELETE /admin/tenants/{id}` still exists and still requires retyping the
  company name — it is never one click away from Edit.
- **All seven compliance slots are always returned, present or not.** Four tax
  records (GSTIN, PAN, TAN, bank details) then three commercial ones (signed
  agreement, PO, MSME), in that fixed order. An absent document is a slot with
  `document: null` rendering "Not Available Yet" — never a short list a missing
  PAN card can hide in. UNIQUE on (tenant, type): re-uploading REPLACES in
  place, keeping the document id stable.
- **`jobs_closed` and `jobs_ongoing` OVERLAP and are not a partition of
  `jobs_posted`.** Closed is `now > posting_end_date`; ongoing is
  `now <= grace_period_end_date`; a job in its 5-day grace tail is both. They
  are two independent questions — never render them as parts of a whole, and
  never "fix" them to sum. Boundaries stay inclusive at the end of each window
  (rule 8), matching `services/job_posting`.
- **Customer search, the archived filter and pagination run in SQL**
  (`api/provider.list_customers`), before pagination. Filtering a fetched page
  in the browser makes the match count depend on which page was loaded.
- **The Provider Portal nav is Customers + Business Development + Billing +
  Settings, nothing else** (amended twice on 2026-07-28). Team Management,
  Permissions and the Audit Log still have no page. Billing is READ-ONLY like
  every other Provider view of customer data, and read-only by ABSENCE: there
  is no route in `api/billing` that lets the Provider write a subscription, a
  plan or a credit. Business Development is not a fourth
  cross-tenant admin surface: it is the ONLY place a `bd` account can be
  created, because every invite path in the product is tenant-scoped and a BD
  user has no tenant. The audit trail is
  still written for every Provider request (`get_superadmin_db`); it simply has
  no UI. Settings stays because the theme toggle lives there (rule 10).
- **`manage_compliance_documents` is the one capability the flat staff model
  does NOT flatten.** Granted to `client` (Company Admin) alone by default: a
  GSTIN certificate and a signed agreement are the company's legal instruments,
  not recruitment data. Still a capability, never a role branch — an HR Head
  can delegate it via `users.permissions_json`.

## Current hard rules — Job posting lifecycle + hiring pipeline (2026-07-27)

- **Every job is live for EXACTLY 30 days, then 5 days of grace.** The window
  is not configurable and a recruiter can never move it: `jobs.posting_end_date`
  and `grace_period_end_date` are Postgres GENERATED columns, so an UPDATE
  against them is rejected by the database itself. `posting_start_date` is
  stamped at publish and is the only writable date.
- **`posting_status` and `is_within_grace_period` are READ-TIME values, never
  stored.** The spec asks for them as generated columns; Postgres refuses,
  because their definitions call `now()` (and one subqueries another table) and
  a generated column must be IMMUTABLE. They live in
  `services/job_posting.py`, mirrored by the `job_posting_state` SQL view. The
  two must agree — change them together.
- **Visibility rules are in `services/job_posting.py` and are pure functions.**
  A wrong boundary there silently grants or removes a person's access, so every
  boundary is asserted directly in `tests/test_job_posting.py`. Boundaries are
  INCLUSIVE at the end of each window: an instant exactly on `posting_end_date`
  is still active, ties go to the candidate (consistent with rule 8).
- **A candidate who registered after `posting_end_date` never sees that job** —
  not in the board, not in search, not by direct URL. The window filter is
  applied BEFORE relevance ranking and before `?search=`, because search
  deliberately bypasses relevance and would otherwise bypass this too.
- **The grace period is for EDITING an existing application, never for creating
  one.** It grants nothing to a non-applicant and nothing to an anonymous
  visitor: the public/external job link 404s the moment the 30 days end.
- **Not every applicant is assessed.** All applicants are ranked on resume +
  profile form, but only candidates a recruiter selects
  (`POST /pipeline/jobs/{id}/select-candidates`) get an assessment — and
  therefore a PFI report. The `assessment_conversations` row IS the invitation;
  `POST /assessments/conversations/links/{id}/start` refuses without one, so an
  uninvited candidate cannot reach the questions by guessing a URL.
- **Application status is a validated 10-stage pipeline**
  (`services/hiring_pipeline.py`). Illegal moves are refused — an application
  cannot jump from `applied` to `offer_extended`, because each stage carries a
  promise (`assessment_completed` means a report exists) and the transition
  emails reference it. `rejected` and `hold` are reachable from any live stage.
  `pipeline_status` stays the append-only history; `job_candidate_links.status`
  is a denormalised mirror, and only `apply_transition` writes either.

## Current hard rules — Job detail page + LangGraph router (2026-07-27)

- **NO NUMBERS REACH A CLIENT. EVER.** Not a score, percentage, rank, band
  index, "7.5/10", or "top 12%" — in the UI, in an API response, or in an
  email. SUPERSEDED IN PART, 2026-07-30: rated output is now the FOUR grades of
  `services/rating.py`, not two parallel five-label scales. The conversion from
  the internal score still happens SERVER-SIDE so a number cannot leak by
  omission. The single, documented exception is the radar chart's band index
  (now 1–4), which is a rendering coordinate — a radar has no geometry without
  a radius — and is never displayed as a number anywhere.
- **Every LLM call routes through `services/llm_router.invoke_llm(task_type,
  …)`.** Task types are `jd_generation | technical_questions |
  behavioral_assessment | report_synthesis | email_composition`, plus the
  legacy `rerank | extraction` hints. Routing policy is DATA in
  `config/llm_providers.py` (provider order, timeout, retry budget per task) —
  never inline in a service. The key roster is 7 slots per provider (21 total),
  every slot optional; the router round-robins within a provider tier and
  walks the tier order on failure. A LangGraph `StateGraph` drives the retry
  loop; the circuit breaker, half-open recovery, and never-log-a-key rules are
  unchanged.
- **Candidates are listed INLINE on the job detail page.** There is no separate
  Review Screen, no Email Templates builder, and no separate JD-edits card.
  Columns are Name | Level | PPI Report | Resume | the rated comments (and
  Decision, when the caller holds `decide_profile`). The job page also carries
  the assessment-setup review (`components/job-setup-review.tsx`), which is the
  one manual step in the pipeline.
- **The candidate table is sorted in SQL, never in JavaScript.** Order is
  grade-driven (`services/job_candidates.order_by_clause`): non-managerial is
  skills → experience → behavioural; managerial and above is skills →
  behavioural → experience. It must stay a TOTAL order (trailing
  `created_at, id`) or paginated rows will duplicate or vanish. 25 per page.
- **About Company / Work Life / Benefits live in two layers.** The company
  profile (Company Portal → Profile) is the default; a job SNAPSHOTS it at
  creation and may override it per job. Editing the company profile reaches
  FUTURE jobs only — never a job candidates are already applying to. A NULL
  section on a job reads through to the live company profile.
- **Reports are immutable.** No edit or delete affordance in the UI, and
  PATCH/PUT/DELETE on the report route return 403 explicitly (a registered
  handler, not an accidental 405). A retake generates a NEW report alongside
  the old one. This is also why a saved PPI framework cannot be reopened once
  anyone has been assessed against it.
- ~~**Six-month retake rule**~~ REUSE RETIRED 2026-07-30 (`services/retake`):
  every application runs its own assessment, because under PPI the framework and
  the technical bank both come from the job's own JD and nothing in a report is
  portable any more. `PORTABLE_CATEGORIES` is an explicit empty frozenset. The
  183-day classification still runs so the candidate is told why they are
  answering questions again.
- **All six lifecycle emails are AI-drafted and editable before sending.**
  Prompts are `.txt` files in `app/prompts/`; every send is recorded in
  `email_log` with the copy actually sent and whether a human edited it.
  Delivery is a Celery task over Gmail SMTP. An email never contains a score.
- **Permissions gain a per-user layer.** Resolution is user overlay → tenant
  row → global template → deny. `users.permissions_json` is a SPARSE
  {capability: bool} object: a capability the HR Head never pinned keeps
  tracking its role default. Still `require_capability(...)`, never a role
  branch.

## Hard rules — Unified candidate profile release (2026-07-27)

- ~~**The 40 validation aspects are a FORM on the candidate profile.**~~
  SUPERSEDED 2026-07-30: validation is six mandatory fields on the APPLICATION
  form (see the top of this file). `candidate_profile_form.py` survives as the
  candidate's own reusable profile, but it is no longer where the report's
  Validation section reads from. Original rule, for context: A candidate's answers are identical for every job, so
  they are collected once under My Profile and snapshotted onto each
  application's `profiles.aspects_json`. `services/candidate_profile_form.py` is
  the single source of truth for that form — a fixed Python constant, never
  LLM-generated and never client-editable, exactly like `pfi_bank.py`. The
  report's Validation section reads the snapshot; `validation_bank.py` survives
  only to keep pre-2026-07-27 transcripts readable.
- **A per-job assessment is technical (by grade) + PPI (by grade), and nothing
  else.** SUPERSEDED 2026-07-30: the behavioural half is PPI and its count now
  varies by grade, so non-managerial is 45 questions rather than 40.
- **The candidate has a MAIN resume** (`candidates.main_profile_id`), managed on
  My Profile and offered on every application beside "upload a new resume".
  Replacing it never rewrites a submitted application — each application remains
  an immutable snapshot of the resume it was actually sent with.
- **The candidate's New Jobs board shows RELEVANT roles only**, ranked by
  `services/job_relevance.py` against their main resume, its parsed skills, and
  their profile form. `?search=` deliberately bypasses relevance entirely. This
  is candidate-side presentation ONLY — it must never decide who gets scored.
- **Text is never grey.** Every text token resolves to pure black in the light
  theme and pure white in the dark theme; grey survives only on borders, input
  outlines and muted backgrounds. The single exception is `::placeholder`, dimmed
  so an empty field cannot be mistaken for a filled one.
- The candidate portal's nav is **New Jobs → Applied Jobs → My Profile**. There
  is no "Settings" page for candidates, and their role is never displayed.

## Hard rules — Grade-driven assessment release (2026-07-26)

- Evolve the system additively. Extend tables and routes; do not replace
  established contracts without a migration and versioned compatibility path.
- The PickReady Functional Index is proprietary PickReady work derived from
  first-principles job analysis. Never associate its name, prompts, code,
  comments, UI, or documentation with a third-party licensed assessment
  instrument.
- Client-facing rated output uses only these labels: Very High, High, Medium,
  Low, Developing. Stored numeric scores are internal ranking data and must
  never be returned by report APIs or rendered in the client UI.
- Rated remarks are 25–30 words and overall summaries are 45–50 words. AMENDED
  2026-07-30: the 25–30 rule now covers only the AI Score's four matching
  parameters and technical items; every Primary Skill, Secondary Skill,
  Behavioural Competency and the Overall Remark is 45–50 words. Validate
  and regenerate complete prose; never truncate a sentence to hit a limit.
- A candidate experiences one unified conversation. Technical, Behavioral, and
  Validation scoring fan out in parallel; synthesis is an explicit join.
  (SUPERSEDED IN PART, 2026-07-27: validation is no longer *asked* in the
  conversation — the three scorers still fan out in parallel, but
  `validation_capture` now reads the candidate's profile form.)
- Gmail SMTP is the only outbound email path. Authentication is email/password
  or Google OAuth; no OTP UI or copy is permitted.

### Grade drives the assessment (2026-07-26)

- **Every job carries a grade**: `non_managerial | managerial | leadership |
  cxo`. It is a REQUIRED dropdown on the Create Job form, stored in the existing
  `jobs.assessment_grade` column and exposed on every job read as `grade`. It is
  never null — legacy rows read `non_managerial`. Grade is chosen by the
  recruiter, not inferred; LLM inference survives only as a fallback for rows
  created before this release.
- **Question counts are fixed by grade.** Technical: non-managerial 20,
  managerial 17, leadership 15, CXO 12 — unchanged. ~~Behavioural: always 20 (5
  grade-specific PFI dimensions × 4 fixed questions).~~ SUPERSEDED 2026-07-30:
  the behavioural half is now PPI and its count varies by grade — 25 / 20 / 15 /
  10 — so a non-managerial candidate answers 45 questions and a CXO 22.
  ~~Validation: always all 40 aspects.~~ Validation left the conversation on
  2026-07-27 for the profile form, and left the profile form on 2026-07-30 for
  six mandatory fields on the application form.
- ~~**There is no manual question-bank approval step, and no question-bank
  UI.**~~ REVERSED 2026-07-30, client decision. The gate is back and now covers
  the PPI framework as well as the technical bank; see the 2026-07-30 section
  at the top of this file. Recruiters review, edit and finalise both, and no
  candidate can be invited until they have.
- ~~**Behavioural questions and the profile form are fixed Python
  constants**~~ SUPERSEDED 2026-07-30 for the behavioural half:
  `services/pfi_bank.py` and `services/validation_bank.py` are deleted, and the
  behavioural competencies are now part of the per-job PPI framework generated
  from the JD. `services/candidate_profile_form.py` survives unchanged and is
  still a fixed constant, never LLM-generated and never client-editable.
- **Scoring reads the candidate's actual answers.** Each technical answer is
  scored against that question's own rubric; each PFI dimension is scored from
  its four answers. A deterministic hash is permitted ONLY as a flagged
  LLM-outage fallback and must set `scoring_mode`.
- **A technical report dimension is named after a skill, never a JD sentence.**
  `report_dimensions` is UNIQUE on (report_id, category, name), so the report
  carries one entry per distinct skill probed — not one per question.
- **A candidate linked to a job is always scored.** Retrieval (pgvector, ts_rank)
  is a ranking prior only; it must never decide who gets scored. Every
  non-archived link on the job enters the scoring pool.
- ~~**Report section order is fixed**: overall summary → radar chart → Profile
  Matching → Behavioural (PFI) → Technical → Validation → Suggested Interview
  Probes.~~ SUPERSEDED 2026-07-30, see the report order at the top of this
  file: AI Score → Overall → Primary Skills → Secondary Skills → Behavioural
  Competencies → Validation → Suggested interview questions.
- **Never name a storage vendor in user-facing copy.** Candidates are told the
  file limits, not where the bytes land.

This file is the standing context for any Claude Code session working on this repo. Read `PRD.md` for functional requirements and `ESD.md` for the architecture — this file is *how* to build it, not *what* to build.

---

## 1. Project One-Liner

PickReady is a multi-tenant recruitment/ATS platform for Hanulisa Technologies LLP. Next.js + FastAPI, Firebase auth for every role, Postgres+pgvector for data and matching, a grade-driven AI assessment producing the Functional Skills Report, Celery for all async work, fully Dockerized.

---

## 2. Repository Layout

```
/frontend                Next.js 14 (App Router), TypeScript, shadcn/ui
  /app                   routes, grouped by role: (super-admin) (client) (hr) (recruiter) (hiring-manager) (candidate)
  /components            shared UI, shadcn primitives in /components/ui
  /lib                    api client, auth helpers, theme provider
/backend
  /app
    /api                 FastAPI routers, one module per PRD section (auth, jobs, candidates, matching, verification, dashboard, admin)
    /models              SQLAlchemy models, mirroring ESD §4 tables
    /schemas             Pydantic request/response models
    /services            business logic — approval FSM, RBAC engine, LLM router, matching pipeline
    /workers             Celery tasks (send_email, send_sms, run_matching, poll_verification, refresh_dashboard_views)
    /core                config, security (OTP hashing, JWT), db session with RLS tenant-var setter
  /alembic                migrations
  Dockerfile
/infra
  docker-compose.yml      local dev: postgres+pgvector, redis, backend, worker, beat, frontend
  railway.json / render.yaml   production service definitions
/docs
  PRD.md
  ESD.md
  claude.md
```

---

## 3. Non-Negotiable Rules

These are architectural decisions already made in ESD.md — do not silently deviate from them or re-litigate them in code review:

1. **Every tenant-scoped query goes through the RLS-aware session.** Never hand-write a `WHERE tenant_id = ...` filter as the *only* protection — the Postgres RLS policy is the real boundary; app-level filtering is defense in depth, not a substitute.
2. **Authentication is Firebase (as of 2026-07-24).** All roles sign in via Firebase Auth — Google, email/password, and phone. The backend verifies the Firebase ID token (`services/firebase_auth.py`) and issues the app's own portal-scoped JWT cookies; database roles/permissions remain authoritative (Firebase is identity only, never authorization). **Exception to the original "no passwords" rule:** candidate email/password is explicitly allowed (user decision, 2026-07-24). Do NOT build a custom password store or "forgot password" flow — Firebase owns credentials and recovery. The legacy MSG91 OTP send-path is retained as a working SMS feature but is no longer the login mechanism.
3. **Permissions are data, not code — but the staff model is now FLAT (as of 2026-07-24, PRD v1.0 §4).** HR Manager, Recruiter, and Hiring Manager are **equal**: all three create jobs, share one candidate pool, and hold the same operational capabilities. Keep using the `require_capability("...")` dependency backed by `role_permissions` (don't hardcode `if role == ...`), but the seeded matrix grants all three staff roles the full operational set. The RBAC engine and multi-level approval FSM remain in the codebase but are **bypassed** (jobs publish directly on creation) — do not surface approval levels or per-staff-role gating in the UI. Owner and Client Company Admin sit above the flat staff roles.
4. **All async/slow work is a Celery task**, never inline in a request handler: matching/re-ranking, email/SMS sending, resume parsing, verification-reply parsing, dashboard aggregation.
5. **All outbound email goes through Gmail SMTP from the backend.** Configure `smtp.gmail.com:587` with STARTTLS, the Gmail address, and a Google App Password via `SMTP_*`. The authenticated Gmail mailbox is always the From address. Sending remains a Celery task with database audit records and permanent-vs-transient failure handling.
6. **Candidate resumes ARE persisted on the candidate profile and reused across applications (as of 2026-07-24, PRD v1.0 FR-6.2).** Store the uploaded resume on the candidate's profile; on a new application, offer to reuse the last resume or upload a fresh one. (This reverses the earlier fresh-upload-only rule.)
7. **Databank candidates never re-enter the verification/40-aspect flow** — their existing Profile is reused as-is. Only freshly sourced candidates go through Section 5's data-collection + verification steps.
8. **Tier boundaries are inclusive upward**: a score of exactly 90 is Highly Matching, not Moderately Matching. Implement tier assignment top-down (check ≥90 first).
9. **LLM keys are routed with fallback, never hardcoded to a single provider.** Use the `llm_provider_keys` table and the router service (ESD §8.4); mark a key unhealthy on repeated failure rather than crashing the calling task.
10. **The theme toggle lives only in Settings/Profile** — never in the main navbar or a persistent floating control.

---

## 4. Coding Conventions

- **Backend**: Python 3.12, FastAPI, async everywhere (`async def` route handlers, `asyncpg`/`SQLAlchemy` async engine). Pydantic v2 for all request/response schemas — no bare dicts crossing the API boundary.
- **Frontend**: TypeScript strict mode on. Server Components by default; `"use client"` only where interactivity requires it. shadcn/ui components live under `/components/ui` and are not hand-edited beyond the CLI-generated output — wrap/compose instead of modifying generated files.
- **Styling**: Tailwind, monochrome palette (CSS variables for the black/white theme pair so the toggle is a variable swap, not a component-level branch).
- **Migrations**: every schema change is an Alembic migration, checked in — no manual production schema edits.
- **Tests**: Pytest for backend (unit tests on the approval FSM, RBAC engine, tier-boundary logic are mandatory given how much of the product depends on getting these exactly right); Playwright or React Testing Library for frontend critical flows (OTP login, job approval chain, HR review screen).
- **Commits**: Conventional Commits style (`feat:`, `fix:`, `chore:`, `refactor:`) to keep the history usable for a changelog later.

---

## 5. Environment Variables

**`/.env.example` is the single source of truth — read it, do not trust a copy.**
A duplicated list here drifts: this section previously still advertised
`RESEND_API_KEY` (email moved to Gmail SMTP) and a 9-key LLM roster (now 21).

Notes that are not obvious from the file itself:

- **LLM keys**: 7 slots per provider (`GROQ_API_KEY_1..7`, `GEMINI_API_KEY_1..7`,
  `OPENROUTER_API_KEY_1..7`). Every slot is OPTIONAL — the router enumerates
  only populated ones, so three keys and twenty-one behave identically. The
  `llm_provider_keys` table takes precedence over env when it has rows.
- **Email is Gmail SMTP only** (`SMTP_*`): `smtp.gmail.com:587` with STARTTLS
  and a Google App Password. The authenticated mailbox is always the From
  address. There is no Resend/Mailtrap path.
- **OTP settings remain** for the retained SMS feature; they are no longer the
  login mechanism (Firebase owns authentication).

---

## 6. Local Dev Quick Start

```bash
git clone <repo>
cd pickready
cp .env.example .env          # fill in real keys before first run
docker compose -f infra/docker-compose.yml up --build
# frontend: http://localhost:3000
# backend:  http://localhost:8000/docs (FastAPI auto-docs)
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.scripts.seed_dev_data
```

---

## 7. Build Order (matches PRD §9 phasing — build and ship in this order, don't jump ahead)

1. Tenant model + RLS policies + Super Admin console + RBAC engine
2. OTP auth for every role + Candidate Portal auth scope
3. Company onboarding + Hiring Manager account creation (max-5 enforced)
4. Job creation + configurable approval FSM
5. Resume upload + BGE-M3 embeddings + pgvector + Databank
6. Hybrid ranking pipeline (semantic → keyword → LLM re-rank → tiers)
7. Candidate outreach + 40-aspect flow + employer verification (form + fallback parsing)
8. HR Review Screen + Hiring Manager shortlist actions
9. Interview scheduling (client-domain email, .ics) + mandatory status tracking
10. HR/Recruiter dashboard (materialized views)
11. Observability, audit log UI, load/security hardening

---

## 8. When Unsure

If a requirement in PRD.md is ambiguous and the ESD doesn't resolve it, don't guess silently — implement the most defensible interpretation, leave a clear `# ASSUMPTION:` comment at the point of implementation, and surface it back to the user rather than letting it drift into an undocumented behavior.
