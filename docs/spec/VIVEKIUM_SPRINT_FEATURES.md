# Vivekium Sprint Feature Brief, reconciled against this codebase

**Source**: `vivekium.pdf`, "Developer Handoff Document, UPDATED", dated
September 2026. Platform *Vivekium*, entity *Varpitech LLP*, domain
*vivekium.ai*, 8 features, estimated build 13 to 14 days.

**Status of this document**: it is the RECONCILIATION, not the brief. The brief
is an owner document and is reproduced faithfully in section 1. Section 2 is
what this repository already has. Section 3 is the conflict register: seven
places where the brief and a standing hard rule in `claude.md` cannot both be
true, each with the decision it needs. Nothing in section 3 has been built,
because building it would break a rule this repository enforces with a test.

Precedence: this document sits at rank 3, beside spec-doc6. The RBAC
Specification and the Runbook outrank it. Where it is silent, the standing
rules apply unchanged.

---

## 0. The identity question, which is the first thing to settle

The brief names a DIFFERENT product (Vivekium), a DIFFERENT legal entity
(Varpitech LLP) and a DIFFERENT domain (vivekium.ai) from the one this
repository builds, deploys and serves (ReadyPick, Hanulisa Technologies LLP,
readypick.ai). It also names Bodha, Sutra, the Tatva Assessment Matrix and the
PRISM Report, which exist in this codebase and nowhere else.

So the brief is written against THIS platform under another name. Two readings
are possible and they are not the same project:

- **A rebrand.** Every user-visible string, the sender addresses, the metadata
  and the deployed domain move. That is a release, not a feature, and it lands
  on a live site currently serving readypick.ai.
- **A second go-to-market identity** over the same platform, in which case the
  brand is DEPLOYMENT DATA (a settings value and a DNS record), never a literal
  in the tree, and every one of these features is built brand-neutral.

**Nothing in this repository has been renamed, and the second reading is what
the features below are built against.** `bgv@vivekium.ai` and
`support@vivekium.ai` are treated as configured sender addresses, the same way
`INBOUND_EMAIL_DOMAIN` already is. A rename is an owner decision with a
migration, a Firebase authorized-domain change, a certificate and a redirect
plan attached, and it is not inferable from a feature brief.

---

## 1. The eight features, as written

### 1. Drishti, the seventh agent

Standalone, built from day one, never merged with Bodha.

| | |
|---|---|
| Meaning | Drishti = strategic vision / perspective |
| Audience | MD, CEO, Functional Heads (CTO, CFO, COO). NOT the Hiring Manager |
| Frequency | Once per function per functional head. A functional-head change is the CLIENT's trigger, never auto-detected |
| Captures | Strategic purpose of the function; people philosophy; non-negotiables; culture and leadership expectations; the strategic gap being filled |
| Feeds | Every assessment for that function, permanently. Sutra reads Drishti alongside the JD and Bodha's HM input when building the Tatva matrix |
| Updatable | Yes, by the functional head, at any time |
| If absent | The platform still works. Drishti is an ENHANCEMENT LAYER, not a dependency |
| Duration | 20 to 30 minutes, structured AI conversation, any device |

Key distinction, verbatim: "Bodha = per job, per Hiring Manager, every job
posting. Drishti = per function, per functional head, once and permanent. Do
not merge these agents under any circumstances."

### 2. Resume-aware dynamic questionnaire

The agent parses the resume BEFORE the conversation opens and asks only what it
cannot already determine. Employment tenure, titles, compensation history,
education, listed skills and career-progression pattern are pre-filled. Only
gaps, ambiguities and behavioural dimensions are asked. Ceiling of 40 questions,
explicitly "a ceiling, not a fixed script"; a detailed resume may produce 8 to
12 questions. A full re-assessment runs for each new job (each job has its own
Tatva matrix), but parsing and pre-fill carry forward, so the candidate never
re-enters a fact the platform already holds.

### 3. Recruiter home page, seven columns

Minimum visible information; everything else lives inside the Executive Profile.

| # | Column | Notes |
|---|---|---|
| 1 | Candidate Name | Opens Executive Profile on click |
| 2 | Executive Profile Match Score | "% match for this specific job" |
| 3 | CTC Match | Within range / Above range / Below range |
| 4 | Notice Period Match | Immediate / Within 30 / 30 to 60 / 60 to 90 / Above 90 days |
| 5 | Education Match **(NEW)** | Match / Partial / No match against job requirement |
| 6 | Resume Link **(NEW)** | Direct tap, opens resume in a new tab |
| 7 | BGV Status **(NEW)** | Done / Pending / Not Started, detail inside the Executive Profile only |

PRISM Report, Tatva scores, transcript, BGV detail and consent items are visible
inside the Executive Profile only.

### 4. BGV automation

Shortlisted candidates only, in parallel with the assessment, never blocking it.

- Candidate supplies official HR department email addresses. **Maximum 2
  employers, minimum 1.**
- **PAN / PF / ESI: consent tick only. No numbers collected. No numbers
  stored.** One checkbox, recorded as ticked and timestamped.
- Freshers: no employer BGV. Academic certificates and address proof only.
- Link expiry: **3 days** from send.
- **Bounce detection**: notify the candidate immediately to correct the
  address; do not wait out the 3-day window.
- Data isolation: an employer sees assessment data only for candidates who
  applied to their own job. Enforced at the data layer, not as a UI rule.

Flow: shortlisted, email to employer HR (unique link per employer-candidate
pair, not reusable), bounce means an immediate alert to the candidate, HR opens
the link and completes a checkbox form (no typing, under two minutes), the
response auto-links to the candidate BGV record and the status becomes Done,
then Email 2 to the candidate on completion or Email 3 on day-3 non-response.

HR checkbox items: employment period accurate; job title accurate; compensation
details accurate; third-party BGV was conducted during employment; education
certificates verified at onboarding; address verified at onboarding; eligible
for rehire (optional).

### 5. Two data lifecycles

| Assessment data, TEMPORARY | BGV data, PERMANENT and PORTABLE |
|---|---|
| Tied to one job at one employer | Candidate-level, tied to no job |
| PRISM Report, Tatva scores, transcript | Travels with the candidate across future applications |
| Auto-deleted when the employer closes that job posting | Always the last 2 employers only |
| No manual action from anyone | A new employer auto-drops the oldest |
| Disclosed to the candidate in consent | A new BGV email fires automatically; status Pending until the employer responds |

"BGV auto-maintenance must run automatically when the candidate updates
employment history. No manual trigger. No admin action required."

### 6. Consent framework, two stages, six items

Each item timestamped individually. Stage A at registration, Stage B during the
assessment interaction.

Forbidden wording, verbatim: do not use "direct employer" or "manpower agency"
anywhere on the platform, in consent text, emails or any system copy. Use
"employer clients registered on the platform".

**Stage A, at registration**
1. Profile and verified information retained, and may be considered by employer
   clients registered on the platform who post openings.
2. Photos, assessment videos and BGV records stored, and may be accessed by
   employer clients for current and future hiring decisions.

**Stage B, during the assessment**
3. The assessment report for this job is tied to this job only; when the
   employer closes the position it is automatically and permanently removed.
   (Covers PRISM Report, Tatva scores, transcript.)
4. BGV records are retained independently of any job. Only the last two
   employers are kept; updating employment history automatically replaces the
   oldest.
5. All information provided is accurate and complete.
6. Consent for the platform to contact previous employers' HR teams for
   employment verification under India's Digital Personal Data Protection Act,
   2023.

### 7. Delete My Profile

DPDP compliance, in candidate profile settings, clearly visible.

Deletes all stored data: profile, BGV records, photos, assessment videos,
assessment reports, consent records, all job application history. Two-step
confirmation: a smart warning screen, then the candidate types DELETE.
Immediate permanent removal, a confirmation email, cannot be undone. A returning
candidate starts from scratch.

The warning screen states, in order: the complete profile including BGV and
assessment data is permanently deleted; immediate removal from all active job
matching; employer clients considering the profile will no longer see it; any
ongoing assessment or shortlisting is cancelled immediately; a completed BGV
record is permanently lost and must be re-obtained; a returning candidate
repeats profile, questionnaire, assessment and BGV from the beginning; the
action cannot be reversed under any circumstances.

### 8. Consent renewal and inactivity

- Consent renewal cycle: every 6 months from registration or last renewal.
- Reminder email at 6 months. Grace period 15 days.
- At 15 days with no response, a second email states the profile will be
  permanently auto-deleted, and must explain the loss of BGV records, removal
  from job matching, and the effort required to rebuild.
- After the grace period with no renewal, the profile is permanently
  auto-deleted. No manual admin action.
- 24-month inactivity rule: genuinely inactive (no matches, no applications, no
  interactions) triggers deletion regardless of renewal status.
- A job-matching email the candidate RECEIVES counts as engagement and resets
  the inactivity clock.
- Leadership change is not auto-detected; the client's HR Head triggers a new
  Drishti session.

### System email templates

Four, reproduced in the brief in full:

1. **To previous employer HR**, on shortlisting. From `bgv@vivekium.ai`,
   display name "Vivekium BGV Team". Subject "3-Day Action Required,
   Employment Verification for [Candidate Full Name]". Names the DPDP Act,
   states the 3-day window, states that non-response may disqualify the
   candidate, lists the seven checkbox items, states the link is unique and
   cannot be reused.
2. **To candidate**, on BGV Done. From `support@vivekium.ai`. No BGV detail is
   shared with the candidate.
3. **To candidate**, on day-3 non-response. Asks the candidate to contact the
   employer's HR directly. The HR address is **partially masked**.
4. **To candidate**, on bounce. Asks the candidate to log in and correct the HR
   address. The HR address is **partially masked**.

### System event map, the eight wirings

1. Drishti output to Sutra: the matrix reads JD + Bodha HM SWOT + Drishti,
   all three required before the matrix is generated.
2. Shortlisting fires two parallel triggers: assessment start AND the BGV
   email. Neither waits for the other.
3. Resume parser to the conversation: parsing completes first; pre-filled
   answers are passed as context and never asked.
4. Consent to three destinations in ONE operation: candidate record, BGV
   record, and the candidate page in the Executive Profile.
5. Employment-history update to BGV auto-maintenance, automatically.
6. Job closure to assessment-data deletion, permanent, recruiter view updates.
7. HR response to the recruiter home page, live.
8. Bounce to the immediate candidate alert.

---

## 2. What this repository already has

| Brief | Here today | Gap |
|---|---|---|
| 1 Drishti | Nothing. Sutra reads the JD and Bodha's SWOT. The Company DNA layer that used to sit beside them was REMOVED on 2026-09-09 by owner decision | The whole agent, its table, its portal surface, and a third input to `scorecard.freeze` |
| 2 Resume-aware questionnaire | Questions are already per-candidate and already read the resume (`ppi.generate_framework`, `technical_interview.write_question`). Counts are FIXED BY GRADE: 25/20/15/10 behavioural plus 20/17/15/12 technical | Pre-fill and the skip logic do not exist. The 40 ceiling contradicts the fixed counts, see C2 |
| 3 Seven columns | `services/job_candidates.order_by_clause` drives the inline candidate table. Name, grade, PRISM link, resume, rated comments, decision | Education Match, a dedicated Resume Link column and BGV Status are absent. Column 2 is refused, see C1 |
| 4 BGV automation | Substantially built, 2026-09-12. `candidate_employments` (immutable by trigger), `bgv_verifications` (per tenant), `bgv_inquiries`, `bgv_agent` drafts from a frozen `FactBlock`, SES inbound routes the reply by reply-address token, the offer gate lives in `apply_transition` | No 2-employer cap, no 3-day expiry, no bounce detection, no PAN/PF/ESI consent tick, no HR checkbox form. Reply handling is free-text plus a human verdict, not a checkbox form, see C4 |
| 5 Two lifecycles | `services/erasure.cascade_erasure` exists and reaches rows, vectors and caches. Reports are immutable. `POST /jobs/{id}/close` exists (Gate 8) | Nothing deletes assessment data on job closure, see C5. The last-2-employers rule and the auto-drop do not exist |
| 6 Consent, six items | `assessment_consents` (per session, versioned, gates both modes), `retention_consents` (`video_download_allowed`, `assessment_download_allowed`, NULL means never-asked and refuses) | The six-item catalogue, per-item timestamps, the Stage A/Stage B split, and the three-destination write |
| 7 Delete My Profile | **The machinery exists and has no door.** `pickready.cascade_erasure` is a registered task; no route anywhere calls it | The route, the warning screen, the typed confirmation, the confirmation email |
| 8 Renewal and inactivity | Nothing. No consent expiry, no inactivity clock, no sweep | The columns, the sweep, its EventBridge rule, and its `test_schedule_parity` entry |

---

## 3. Conflict register

Seven items. Each is a place where the brief and a rule this repository
enforces with a test cannot both hold. None is built.

**RULED 2026-09-18, owner, verbatim: "whatever is given in vivekium is
ultimate final source of truth."** That sentence resolves every decision this
register was waiting on, in the brief's favour. Where the brief contradicts
ITSELF (C3's enhancement-layer sentence versus the event map's "all three
required"), the feature table's own explicit statement wins over the wiring
summary. The resolutions:

- **C1**: column 2 IS the percentage. Rule 1 is amended in `claude.md` in the
  same commit that builds it, with `tests/test_platform_audit.py` changed
  beside it, exactly as this register required. The amendment is NARROW: the
  Executive Profile Match Score on recruiter surfaces is the one sanctioned
  number; grades everywhere else stay words.
- **C2**: the 40-question ceiling and resume-driven count stand. The owner has
  traded fixed-count comparability for speed; a criterion the resume already
  evidences is pre-filled and skipped rather than asked.
- **C3**: Drishti is built as the ENHANCEMENT LAYER the feature table states
  ("If absent: the platform still works"), supplying `LAYER_COMPANY` through a
  compiled artifact, never free text.
- **C5**: job closure deletes the candidate-identifying assessment artifacts
  immediately, as the brief says ("No manual action from anyone"); the billing
  fact, which names no candidate content, is retained because the brief is
  silent about it and the credit ledger is append-only.
- **C8**: the brief describes the candidate-owned system (candidate-level,
  portable, two employers, checkbox form), so `bgv_inquiries` wins and
  `verification_requests` is retired the way Company DNA and Intercom were,
  with a sweep test.

### C1. "Executive Profile Match Score, % match for this specific job"

**Conflicts with hard rule 1**, the oldest and most-enforced rule here: *no
number ever reaches a client*, and the conversion to one of four words happens
server-side at the serializer. `tests/test_platform_audit.py` sweeps for it and
`contains_forbidden_number` strips it from generated copy.

A percentage on the recruiter's home page is the exact thing that rule exists
to prevent. The recruiter IS the client.

**The nearest thing that is buildable today**: column 2 renders the four-grade
word the report already carries (Highly Matching / Matching / Moderately
Matching / Not Matching), sorted by the internal score in SQL as it already is.
The ORDER carries the comparison the percentage was there to give.

**Decision needed**: either column 2 is the word, or rule 1 is amended by the
owner in `claude.md` with its test changed in the same commit. It must not be
worked around at one call site.

### C2. A 40-question ceiling versus counts fixed by grade

**Conflicts with the 2026-08-05 rule**: *the coverage plan stays deterministic,
which criterion, in what order, how many* , and that is what makes two
candidates on one job comparable. The brief's "8 to 12 questions for a detailed
resume" makes the question count a function of the individual's resume, so two
candidates for one job are assessed over different criteria.

The brief's own goal (do not re-ask what the resume states) is achievable
WITHOUT that cost: pre-fill the FACTUAL answers and keep the criteria fixed. A
criterion the resume already evidences becomes a shorter confirmation rather
than a dropped question, so the matrix still has a value for every dimension.

**Decision needed**: whether comparability is being traded for speed. If it is,
`INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE` has to be re-examined at the
same time, because a skipped dimension is an unscored dimension.

### C3. Drishti versus the Company DNA removal

Drishti is a client-authored strategic-context document that feeds every
assessment for a function, permanently. That is structurally the thing the
Company DNA instrument was, and it was **deleted by owner decision on
2026-09-09**, with `tests/test_company_dna_removed.py` sweeping the tree so a
client cannot tell it ever existed.

This is not a reason to refuse Drishti. It IS a reason to build it with the two
properties the removal was about, and to say so in the same commit that
reintroduces the shape:

- **Sutra reads a COMPILED artifact, never the client's free text.** An
  unbounded client-authored string in the prompt that decides what every
  candidate is graded on is an injection surface and a way for "we like hungry
  people" to become a criterion. `services/hiring/observable.py` survived the
  removal for exactly this and is the detector to reuse.
- **A layer may TUNE within declared bounds and may never SUSPEND.**
  `hiring/layers.py` still carries `LAYER_COMPANY` with its bounds table and no
  live supplier. Drishti is that supplier, and every clamp and refusal is
  recorded, per the existing rule.

The brief's own "Drishti is an enhancement layer, not a dependency" and the
event map's "all three required before the matrix is generated" contradict each
other. **The enhancement-layer reading is the one built**, because the other
makes a job uncreatable until a CXO has finished a 30-minute interview, which
is Gate 1 all over again and Gate 1 was narrowed for that reason.

### C4. "Response auto-links, BGV status updates to Done automatically"

Reads at first like a conflict with the 2026-09-12 rule *nothing infers a
verdict from the reply; a person reads the employer's answer and presses
Verified or Not verified*. **It is not one, and the distinction is worth
writing down**: that rule forbids a MODEL inferring a verdict from free text.
An HR person ticking seven boxes IS the human decision, made by a better-placed
human than the recruiter. A structured form submitted by the employer is
evidence of the same kind as a recruiter's click.

**So this is buildable**, with the boundary kept explicit: the checkbox
submission sets the status; the free-text reply path keeps its human verdict;
`bgv.parse_reply` never writes a status. `tests/test_inbound_conversation_reply.py`
stays exactly as it is.

### C5. "Assessment data auto-deleted when the employer closes the job"

**Conflicts with two rules.** Reports are immutable and are the client-facing
permanent record; and `credit_ledger` bills per completed assessment, so
deleting the artifact a customer was charged for removes the answer to a
billing dispute.

The consent item (Stage B item 3) is what makes the deletion legitimate, so the
feature is right in principle. What it needs and does not have is the
separation the platform already uses elsewhere: delete the CANDIDATE-IDENTIFYING
artifact, retain the billing fact. `services/erasure.py` already models exactly
this split for candidate erasure and is the module to extend, not to duplicate.

**Decision needed**: the retention period for the billing record, and whether
job closure deletes immediately (the brief) or after a stated window. Note that
`POST /jobs/{id}/close` is terminal with no reopen, so an immediate delete is
unrecoverable by design and a mis-click costs every report on the job.

### C6. Two calendars that both end in permanent deletion

The brief has consent expiring at 6 months + 15 days, and a 24-month inactivity
deletion, and says the second fires "regardless of consent renewal status".
Since 6 months is far shorter than 24, the inactivity rule can only ever fire
for a candidate who HAS been renewing, so the sentence is about the interaction
rather than a race. Worth stating because the obvious implementation, one sweep
with an OR, deletes an active candidate who renewed last week if a clock was
never reset.

**Built as**: two independent sweeps, each with its own reason recorded on the
erasure receipt, and the inactivity clock reset by the engagement events the
brief names, including a matching email the candidate merely RECEIVES.

### C7. The forbidden terms, and the em dash

"Direct employer" and "manpower agency" are forbidden by the brief. This
repository already sweeps generated and seeded copy in
`tests/test_platform_audit.py`, which is where the sweep belongs, in both
languages, over source AND the database, the same shape the em-dash sweep
already has.

**Note that the brief itself is full of em dashes**, which this platform forbids
everywhere including in seeded content. The email templates in section 1 are
therefore reproduced for their SUBSTANCE; they are not copy-ready and must be
re-set without the character before any of them is sent.

---

## 4. Build order

Ordered by what is a pure addition, what needs a decision, and what would break
a rule if built as written.

**Buildable now, no rule in tension, no decision needed**

1. ~~**Delete My Profile** (feature 7).~~ **BUILT 2026-09-18.** The machinery
   existed and had no door: `services/erasure` reaches rows, vectors and
   caches, `pickready.cascade_erasure` has been registered since the AI runtime
   upgrade, and NOTHING called either, on any portal. `DELETE /portal/me` with
   a server-checked typed phrase, `GET /portal/me/deletion-notice` serving the
   warning verbatim, and `components/delete-profile-card.tsx` rendering it and
   authoring none of its own copy. The erasure also takes the sign-in `users`
   row, without which the person keeps a working identity against a profile
   that no longer exists and every route answers 404 for ever.
2. **BGV** (feature 4, per C4). Partly done.
   - ~~the 3-day link expiry~~ **BUILT 2026-09-18**, and it turned out to be a
     security finding rather than a feature: the employer form link was
     single-use and had NO expiry, so one nobody ever used stayed valid for
     ever in a third party's mailbox. SEC-20.
   - The 2-employer cap already exists as
     `bgv.MAX_INQUIRIES_PER_CANDIDATE = 2`.
   - **Still open: bounce detection, the PAN/PF/ESI consent tick, and the HR
     checkbox form.** The last of these needs the decision in C8 below first.
3. **Recruiter columns 5, 6 and 7** (feature 3), with column 2 as the word.
4. **The six consent items with per-item timestamps** (feature 6).
5. ~~**Consent renewal and the inactivity sweep** (feature 8, per C6).~~
   **BUILT 2026-09-18.** `services/consent_lifecycle` derives the stage from
   four nullable stamps (migration 0100) and stores no status;
   `pickready.sweep_consent_lifecycle` runs daily with its rule in all three
   environments. THE ERASURE IS GATED on `consent_auto_deletion_enabled`,
   which defaults to OFF and is an owner decision, because until
   `last_engagement_at` has been recording for longer than the inactivity
   window every dormancy answer is computed from registration and an active
   candidate reads as dormant. Unarmed, the sweep still sends the letters and
   logs what it WOULD erase. Mutation-checked: removing the gate erases a
   candidate and the test says so.

**Needs an owner decision first**

6. Column 2 as a percentage (C1).
7. The question ceiling versus fixed counts (C2).
8. Job-closure deletion and the billing-record retention window (C5).
9. Drishti as a gate versus an enhancement layer (C3). The enhancement-layer
   build does not need the decision; making it a hard requirement does.

**C8, added 2026-09-18: there are TWO background-verification systems**

Building feature 4 further means choosing between them, and that is an owner
decision rather than an implementation detail, because rule 5 says one
implementation per concept and this is the clearest live violation of it.

- `bgv_inquiries` (migration 0085, 2026-09-05). CANDIDATE-owned, a departmental
  HR mailbox, capped at two employers, an emailed inquiry whose FREE-TEXT reply
  is parsed by a model, and an employer tenant reads a result only through a
  `bgv_share_consents` row. This is the one the brief's feature 4 describes.
- `verification_requests` (the original outreach flow). TENANT-owned, up to
  three employers by `employer_seq`, a tokenised WEB FORM with ten structured
  fields, and an HR override as the documented way past a silent employer. This
  is the one that already has the checkbox-style form the brief asks for.

The brief wants the first one's ownership model and the second one's form. They
overlap enough that a recruiter could reasonably ask why a candidate is chased
twice, and neither knows about the other. Whichever wins, the other should be
retired the way Company DNA and Intercom were retired, with a sweep test, not
left in place as a second path.

The 3-day expiry landed on `verification_requests` because that is where the
link lives; it is correct under either outcome and goes with that system if it
is retired.

**Needs a release plan, not a feature commit**

10. The Vivekium/Varpitech/vivekium.ai identity (section 0).
