# Vivekium — PRODUCT.md

<!-- impeccable:product-schema 1 -->

Product context for design and review tooling. This is the "what and for whom"
that `DESIGN.md` is the "how it looks" of.

## Platform

web

**Surface type: PRODUCT.** App UI, dashboards and tools — not a marketing site.
Impeccable's `init` asks this question and the answer changes what its detectors
expect: a marketing site is allowed a hero and a mood, while a product surface
is judged on whether somebody can do their job in it.

---

## What Vivekium is

An AI-native, multi-tenant hiring-intelligence platform. A client posts a role;
six named agents build a fixed evaluation framework for it, run one continuous
conversation with each candidate, score against that framework, and produce a
**PRISM Report** — *Evidence-Based Role Intelligence & Suitability Mapping*.

The claim it is sold on is not "we screen faster". It is **"we can tell you
why"**: every grade traces to evidence, and the report says what the evidence
was. That claim is the reason for most of the design constraints — a surface
that obscures the evidence undercuts the only thing that differentiates the
product.

## Positioning

Sold against traditional executive search and generic ATS/screening tools, to
CHROs, recruitment managers, recruiters and hiring managers who need to be
able to defend a hiring decision, not merely automate one. The mechanism a
neighboring product could not truthfully copy: citation is enforced
structurally at composition (Siddhi's citation chokepoint has no bypass
parameter — an uncited statement cannot render), not by a prompt instruction a
competitor could also write. That is what makes "we can tell you why" a claim
about the architecture rather than a claim about wording. See
`docs/product/PRD.md` §3 for the confirmed product principles this positioning
is drawn from.

## Who uses it

| Portal | Who | What they are doing |
|---|---|---|
| **Customer** (`/org`) | CHRO, Recruitment Manager, Recruiter, Hiring Manager | Posting roles, reviewing candidates, reading reports, making a decision about a person |
| **Candidate** (`/portal`) | Applicants | Applying, and answering one long conversational assessment — **usually on a phone** |
| **Provider** (`/admin`) | Vivekium's own owner | Customers, compliance, billing. Read-only over customer data by design |
| **Business Development** (`/bd`) | Vivekium's sales team | Leads, AI Reach, converting a signed agreement into a tenant |

The two that matter most for design are **Customer** and **Candidate**, and they
pull in opposite directions:

- A **recruiter** works at a desk, scans a dense table, and wants information
  density. Padding is not kindness to them.
- A **candidate** answers on a phone, is nervous, is being assessed, and cannot
  see how many questions remain (deliberately). Their surface is calm, one
  question at a time, and mobile-first.

## Operating Context

The end-to-end journey (`docs/product/PRD.md` §5): company profile → AI-drafted
job → recruiter edits and publishes → the technical question bank and PPI
framework are generated and human-reviewed before any candidate can be invited
→ candidates arrive by public application, third-party sourcing, or recruiter
databank upload → resume parsing and hybrid (lexical + vector) matching →
recruiter selects who gets assessed → one invitation-gated conversational
assessment → the PRISM Report → interview, offer, and pipeline through to
outcome.

Structural facts that shape the surfaces:

- Every job posting runs a fixed 30-day live window plus a 5-day edit-only
  grace period — never configurable, never extendable by a recruiter.
- A company's compliance record is seven fixed slots (GSTIN, PAN, TAN, bank
  details, signed agreement, PO, MSME), always all seven, present or not.
- Billing is a credit-subscription model (Razorpay) read from an append-only
  ledger; three named tenants are demo-exempt from billing refusals, never
  from billing records.
- The Provider workspace is read-only over customer data by design — it
  provisions and inspects, never edits a customer's own contacts, staff, or
  compliance documents.
- The recruiter's environment is a desk, a dense candidate table, and a
  decision to make. The candidate's environment is usually a phone, mid
  application, answering questions they cannot see the end of.

## What is at stake on each screen

This is the sentence that should govern every design decision: **a person's
career is on the other side of it.**

Practically:

- A grade shown wrongly is a hiring decision made wrongly.
- A report that reads as more certain than it is causes a rejection that should
  have been a conversation.
- A flag rendered as an accusation, rather than as "held for review", is the
  platform making a claim about a candidate that it is in no position to make.
- A number shown to a client is a false precision they will quote back.

## Hard product constraints a reviewer should know

These are not style preferences and none of them is negotiable:

1. **No number ever reaches a client.** Not a score, percentage, rank, band
   index, confidence or weight — in the UI, in an API response, or in an email.
   Grades are four words: Highly Matching, Matching, Moderately Matching, Not
   Matching. The one documented exception is the radar chart's radius, which is
   a rendering coordinate and is never displayed as a number.
2. **Any Must-have graded Not Matching caps Overall at Moderately Matching**,
   with no override, and the report says it was capped.
3. **Reports are immutable.** No edit or delete affordance; a retake produces a
   new report beside the old one.
4. **Text is never grey.** Enforced at the CSS token, not per component.
5. **No em dashes in any string**, including seeded and generated content.
6. **No flag ever auto-rejects.** Every flag routes to a human with the evidence
   attached, and a person's decision is recorded.
7. **The Validation section is the candidate's own words, exactly as
   submitted.** Never re-worded, never summarised, never scored.
8. **Never name a storage vendor in user-facing copy.** Candidates are told the
   file limits, not where the bytes land.

## Product Principles

Condensed from the seven confirmed principles in `docs/product/PRD.md` §3:

1. **The job is the anchor.** Candidate ranking, assessment content, reports
   and workflow actions are all scoped to a specific job — never a global
   candidate pool or a cross-job aggregate.
2. **AI assists; deterministic rules protect continuity.** Generation and
   evaluation lean on models; validation, fixed rubrics, immutable records and
   auditable workflow states keep the product usable and honest when a
   provider degrades or fails.
3. **A hiring decision must be defensible, never merely automated.** Numeric
   scores stay internal; what a client sees is always one of four words backed
   by cited evidence, so nobody can point to a raw number as the reason a
   person was rejected.
4. **Records persist through lifecycle change.** Jobs, customers and profiles
   are archived, never destructively removed; a written report is immutable
   and a retake produces a new one alongside it.
5. **Isolation and access are structural, not conventions.** Tenant data is
   isolated at the PostgreSQL row-policy level as well as in application code;
   permissions are data (capability grants resolved user → tenant → template),
   never a role name branched on in a handler.

## Evidence on Hand

- **No production customer data exists anywhere.** The only deployed
  environment (AWS pilot) holds three demo tenants and thirty demo jobs, and
  zero real candidates, profiles, applications, reports, evaluations or
  matrices (`docs/verification/AI_UPGRADE_BASELINE.md`, measured 2026-09-09).
  Do not design or write copy as if real usage volume or real candidate
  content exists to draw on.
- **Live vendor integration is proven, not assumed.** OpenAI (`gpt-5.6-terra`,
  `gpt-5.6-luna`) and Voyage (`voyage-4` embeddings, `rerank-2.5`) have each
  been exercised against a real API call with the result recorded and dated in
  `docs/verification/VERIFICATION_RESULTS.md`. What remains unproven is stated
  plainly, not implied to work, in `docs/verification/VERIFICATION_PENDING.md`
  (for example, the 429 rate-limit path has only ever been tested against a
  fixture, never provoked live).
- **The retrieval golden set is 60 hand-authored cases**, 0% drawn from
  production traffic and 0 human-verified. Retrieval quality is explicitly
  unmeasured against real usage — state that limitation rather than implying a
  benchmarked accuracy number.
- **No press, case study, testimonial, or named customer logo exists.** None
  may be fabricated for any surface, marketing or otherwise; a surface that
  needs one states the absence rather than inventing content.

## Tone

Plain, specific, and never chirpy. This product tells people they did not match
a role. Copy that is upbeat about that is copy that reads as unkind.

- Say what happened and what to do: "Held for review — two sources disagree
  about the dates" beats "Something needs attention!"
- No exclamation marks in an assessment or a report.
- Never congratulate a candidate on a grade, and never commiserate. Both imply
  the platform has an opinion about them beyond what it measured.
- The candidate never learns how many questions remain. Copy must not leak it.

## Where this file is used

`/impeccable critique`, `/impeccable audit` and `/impeccable polish` read this
alongside `DESIGN.md`. It is deliberately about CONSTRAINTS AND STAKES rather
than features: a reviewer who knows a career is on the other side of the screen
catches things a feature list would not surface.
