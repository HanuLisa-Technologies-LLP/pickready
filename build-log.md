# PickReady build log

Newest first. Each entry has a **Product** half (what a user can now do, or can
no longer do) and a **Technical** half (what changed in the code and why that
shape rather than another). Where a decision has a failure mode that a reader
would otherwise re-introduce, the entry says so.

---

## 2026-08-09 — Resume preview: the redirect pointed at a route that does not exist

Follow-up to item 11/12, found in production after the first deploy.

### Product

Resumes open and download. Before this, PDF resumes showed "Preview could not
be loaded" and the Download button did nothing, while Word resumes rendered
fine.

### Technical

**It was neither the format nor the storage.** `resume_file` answers the first,
token-less request with a 307 back to ITSELF carrying a short-lived access
token. That target was written out by hand as
`/api/v2/candidates/profiles/{id}/resume-file`, and the `candidates` router is
mounted at `/api/v1` only. Every resume view and every download 307ed to a path
that does not exist and 404ed, for every profile and every format, from the
moment private storage made this endpoint the only way to read a file.

It PRESENTED as a format bug because of which endpoint each format uses: a DOCX
is rendered by `resume-preview`, which has no redirect and always worked, so
only PDFs visibly failed. It presented as a "fresh candidates only" bug because
the seeded demo corpus is mostly DOCX. Neither reading was right, and both would
have sent someone into the storage layer.

Found from the Cloud Run request log, not from the source tree: two paired
entries per click, a 307 on `/api/v1/...` immediately followed by a 404 on
`/api/v2/...`. That pairing is what named the cause in one look.

The fix takes the redirect target from `request.url.path`, so the mount and the
redirect cannot drift again.

**Note on the earlier fix.** Adding `profile_id` to the candidate row was
necessary and not sufficient: it got the viewer as far as calling the endpoint,
which then 404ed on its own redirect. The first defect hid the second, and the
API-level verification done after that deploy could not have caught it, because
the route existed and answered; it was the redirect TARGET that did not.

### Tests

`backend/tests/test_resume_file_redirect.py` asserts the property that was
actually violated: the redirect target resolves to a route the app serves,
checked against the app's own OpenAPI surface rather than against a hardcoded
prefix, which is the same mistake in a different place. Also pinned: the
redirect follows whatever prefix it was called through, the token and the
download flag survive it, and both resume endpoints stay under one prefix
(that split is the only reason a broken URL looked like a broken format).

Verified to catch the bug rather than pass vacuously: 4 of the 5 fail against
the previous code. The first draft of the suite DID pass vacuously, because
this FastAPI version keeps included routers as wrapper objects with an empty
`path`, so walking `app.routes` reported only the four built-in doc routes.

Full backend suite: 1341 passed. Frontend: 52 passed.

---

## 2026-08-09 — BD Reach, and an AI Dashboard for the Customer Portal

Client change request, items 15 and 16.

### Product

**Personal Reach and Social Reach are one section, BD Reach.** They were always
the same funnel over the same table: same company fields, same contact, same six
progress checkboxes, same agreement decision. The split forced a rep to decide
which screen a company belonged on before they could work it, and made "how many
leads are we working" a question with two answers. Where a lead came from is now
a column on one table and a filter above it, and adding a lead asks for the
source instead of asking which page you are on. The old `/bd/social` URL
redirects rather than 404s.

**The Customer Portal has an AI Dashboard.** The Dashboard beside it answers
"where are my candidates in the pipeline"; this one answers "what has the AI done
for us, and is any of it stuck":

- Job setup: jobs ready for candidates, jobs awaiting your approval, and jobs
  whose framework still needs generating.
- Assessments: invited, started, completed, reports ready.
- How candidates graded: a headcount against each of the four grade words.
- How the reports were produced: how many were scored offline because the AI
  providers were unreachable.

### Technical

**15 is a UI consolidation; `bd_leads.channel` is untouched.** The API has always
treated an omitted `channel` as "both", which is what the merged screen now
sends. Two things had to move:

- `social_source` became a SQL filter (`lead_predicates`). With one table in
  front of the rep, "show me only the LinkedIn ones" is a filter, and narrowing
  a fetched page in the browser would make the result count depend on which page
  happened to be loaded. An unknown source is a 422, not a silent match-all: a
  misspelled filter that quietly returns everything shows a rep the whole
  pipeline while they believe they are looking at one slice.
- The channel is now DERIVED from the source the rep picks
  (`channelForSource`). A Postgres CHECK still requires a social lead to carry a
  source and forbids one on a personal lead, so the two are computed together in
  one place rather than set in two that could disagree. The form distinguishes
  "approached directly" from "unanswered", which a nullable column cannot, and
  refuses to default the unanswered case: defaulting it would file every hurried
  lead as direct. The channel stays immutable after creation, so the source
  select is disabled when editing.
- The export was one file per screen, so the source was implicit in the
  filename. With one screen it is a column, or the sheet loses the distinction.

**16 is new.** `GET /dashboard/ai-insights`, tenant-scoped by RLS, gated on the
same `view_dashboard` capability as the existing dashboard. Three decisions
worth keeping:

- **Framework health asks the TABLE.** "Needs generating" counts jobs with no
  `job_competencies` rows, never jobs missing `framework_generated_at`. That is
  the 2026-08-06 finding restated: 19 of 35 live jobs carried the stamp with
  zero rows, were permanently stuck, and no health check saw it because every
  one of them asked the stamp. `reconcile_job_setup` repairs these; this is
  where a customer can see one that has not been repaired yet.
- **No number reaches the client.** Scores ARE read, to decide which grade a
  candidate falls under, and not one of them reaches the response: what is
  returned is the word and a headcount. Every grade is present even at zero,
  because omitting the empty ones reads as "nobody landed there" rather than
  "nobody has been assessed".
- **The overall grade is read the way the report reads it.** `overall_score` is
  null on reports written before migration 0030, which recompute it from their
  dimensions on read; that recomputation is reproduced here rather than skipped.
  Skipping it would undercount the customer's own history and look like data
  loss, and a divergent rule would count a candidate under one grade here and
  show them another on their report. `deterministic_fallback` is matched
  literally rather than as "not llm_rubric", because `no_transcript` means the
  candidate answered nothing, which is not the AI failing.

The nav item is listed unconditionally, exactly like Dashboard beside it: both
are gated server-side and the Company Admin does not hold `view_dashboard` by
default, so a nav gate would show one of the pair and hide the other. Both pages
render the 403 as "not part of your access" rather than as a fault, because it
does not improve on a reload.

### Tests

- `backend/tests/test_bd_portal.py`: omitted channel lists both, the source
  filter is a WHERE clause, the direct filter does not constrain
  `social_source`, and an unknown source is refused.
- `frontend/components/bd/lead-form-modal.test.tsx`: the channel derived from
  every source, an unanswered source rejected rather than defaulted, and a
  stored personal lead shown as directly approached rather than as unanswered.
- `backend/tests/test_ai_dashboard.py`: no score-shaped field on any schema, a
  grade is a word from the one rating scale, every grade reported even at zero,
  framework health asserted against the parsed ATTRIBUTES so the comment
  explaining the rule may still name the column it forbids, and only a provider
  outage counting as scored offline.

Verified against the live database across five tenants: grades now sum to the
report count per tenant (Sarkar Corp 216, ACRM Corp 177, Specter & Co. 180), and
the first cut of the query returned all zeros because it read a
`report_dimensions` category that does not exist. Full backend suite: 1336
passed. Frontend: 52 passed, `next build` clean including `/org/ai-dashboard`.

**Not verified in a browser.** Both surfaces are behind an authenticated portal
and this environment has no interactive login, so the checks above are the
build, the unit suites and a live-database run of the endpoint. The 16-item
staging pass still needs a human.

---

## 2026-08-09 — Four reported bugs

Client change request, items 11 to 14.

### Product

- **Resumes open and download again in the recruiter portal**, from the job
  page's candidate table. They had been unreadable there since resumes moved to
  private storage.
- **Word resumes preview inside the app** instead of showing "This Word document
  cannot be previewed because its profile reference is missing" and offering
  only a download.
- **The assessment invitation link works.** It had been going out as a
  placeholder that resolved to nothing, so an invited candidate had no way to
  open their assessment and no way to say so.
- **A Provider can delete a Business Development account** from the BD page,
  after retyping the address. Any leads that account owned stay on the BD board
  and become unassigned.

### Technical

**11 and 12 are one defect, and it is not in the storage layer.**
`services/job_candidates` SELECTed `l.profile_id` and then dropped it building
the row payload. Resumes are private objects, so `resume_url` is not fetchable
by a browser and every read goes through
`/candidates/profiles/{id}/resume-file`; with no profile id the viewer had
nothing to ask for, fell through to its "missing its secure profile reference"
panel, and pointed Download at a storage scheme the browser ignores. Fixed in
the payload, the response schema (`RankedCandidateOut.profile_id` -- an
undeclared key is dropped silently by Pydantic, which is how this can look
present in a service and be absent in the browser) and the two call sites.

The second half of 12 is preview ROUTING: a private object name carries no
extension, so a DOCX with no recorded original filename was classified framable
and handed to an iframe, which renders nothing. `kindFromMimeType` now decides
when the filename cannot, and the filename still wins when it has an extension.

**13 is not a URL-construction bug and no test of URL construction would have
found it.** `settings.frontend_url` was read correctly and
`/portal/assessments/{link_id}` is the right route. The URL was handed to the
prompt and the MODEL wrote `link.to.assessment` into the body instead. What was
missing was a check that the link SURVIVED the draft: `link_defects` is now a
deterministic criterion inside the existing `agent_loop`, so a bad link is
rejected and fed back verbatim, and a persistent failure degrades to the
template, which has always carried the real link. `repair_link` is a last line
of defence on both paths, because a candidate who receives a dead link cannot
complete an assessment and has no way to report it except giving up.

The guard has to DISTINGUISH, not just detect: a dotted token needs three
segments to count as a hostname, which separates `link.to.assessment` from
prose like "Node.js" or "readypick.ai". A guard that mangles a good email fails
invisibly, one wasted round of latency at a time, so both directions are
tested.

**14 reverses a standing rule** ("there is no delete route: a BD rep owns
leads"). The reason for that rule is now what the handler protects instead:
`DELETE /admin/bd-users/{id}` requires the email retyped (same guard as the
tenant delete, intent rather than typing precision) and RELEASES the rep's
leads by setting `owner_user_id` to NULL. A rep leaving must never take the
pipeline with them, and that failure would be silent: nobody notices the leads
are gone until they ask after a deal that no longer exists. Customers promoted
from a signed agreement are untouched by construction, since
`promoted_tenant_id` hangs off the lead. Disable survives and is still the right
control for someone who may come back.

### Tests

- `backend/tests/test_resume_access_row.py` pins the profile id on the payload
  AND through the response schema, the null case for a link with no profile
  yet, and that no score crept into a client-facing row.
- `backend/tests/test_assessment_link_integrity.py` pins four shapes of
  invented link, a dropped link, repair in both directions, the template's own
  link, and three pieces of ordinary prose that must NOT be mistaken for one.
- `frontend/components/resume-viewer.test.tsx` pins MIME routing, the
  authenticated preview fetch, and that Download never emits a storage scheme.
- `backend/tests/test_bd_accounts.py` replaces its "never deletes" test with the
  lead-release, confirmation, and still-owner-gated guarantees.

Full backend suite: 1322 passed. Frontend: 44 passed.

---

## 2026-08-09 — Application form content and field changes

Client change request, items 1 to 10.

### Product

**The candidate application form asks less, asks it once, and says so.**

- **Earliest joining date is gone.** It was mandatory, it sat one field below
  Notice period, and a date typed months before an offer exists is not evidence
  of anything. Notice period already answers the question a recruiter is
  actually asking.
- **The mandatory block now states the reuse rule**: "The following information
  is mandatory. You need to fill this information only one time and
  automatically applicable to all other jobs which you apply, otherwise you
  edit." This is now true rather than aspirational: those answers arrive
  prefilled from the candidate's most recent application and are editable in
  place.
- **The document readiness question names its documents.** It previously asked a
  candidate to confirm "All documents ready" while listing none. Nine documents
  are now listed beside the field.
- **Gender is asked once.** It was on the form twice, once in Personal details
  and again as questionnaire item 7.
- **Date of birth is gone**; Age, already collected in Personal details, is the
  surviving field.
- **Certifications and additional qualifications are one question**, worded
  "Additional Qualifications (Professional certifications, Diplomas, Courses,
  Licenses)".
- **The Compensation section is gone from the questionnaire.** Current and
  expected CTC are mandatory fields on the application form itself, so a
  candidate was answering them twice on one page and a recruiter had two answers
  to the same question with no rule for which one counted.
- **Notice period in days and earliest joining date are gone** from the
  questionnaire for the same duplication reason.
- **Both consent questions are mandatory.** They are asked as an explicit Yes or
  No with nothing preselected. Mandatory means the candidate stated a position;
  it does not mean they must consent, and declining is a complete answer.

The questionnaire is 30 questions, numbered 1 to 30 with no gaps.

### Technical

- `services/application_validation.py` drops the `joining_date` field. **No
  column was dropped, because there is none**: the mandatory fields are keys
  inside `job_candidate_links.validation_json`. Reports written before today
  still carry the key in their own snapshot and still render it; `normalise`
  simply stops accepting new values. This was checked before touching anything,
  per the request.
- `SECTION_INTRO` and `REQUIRED_DOCUMENTS` live in that same module and are
  served through `GET /portal/jobs/{id}/apply-context`, for the reason the field
  list already was: the copy states a behaviour, and copy that drifts from the
  behaviour it describes is worse than no copy.
- Reuse is `reusable_defaults(previous)` reading the candidate's most recent
  link, **not** a move of the storage onto the candidate profile. The rule that
  these fields live on the APPLICATION stands: current CTC and notice period are
  true when answered and stale a quarter later, so every application keeps its
  own immutable snapshot. What is reused is the TYPING.
- `lib/aspects.ts`: retired ids are **retired, never reissued, and their
  neighbours are never shifted down**. `id` is the stored key in `aspects_json`
  and in every report written to date, so renumbering 30 surviving questions
  would silently re-point every historical answer at a different question, with
  no migration able to undo it once a report has been read. Retired: 6, 7, 13,
  29 to 33, 37, 38. What the candidate sees is `aspectDisplayNo`, a contiguous
  1..N recomputed from the surviving list, so the form has no gaps and the data
  has no collisions.
- `AspectsReadout` grows a "Previously asked" group. An application submitted
  before today is a record of what the candidate was actually asked; dropping
  those rows because the question left the form would quietly rewrite history in
  the recruiter's view.
- Mandatory consents render as a Yes/No select rather than a Switch. A Switch
  has no unanswered state, which is the only reason those two ever showed
  "(optional)" -- and a default-off toggle read back as an answer would be a
  consent nobody gave.

### Tests

- `frontend/lib/aspects.test.ts` pins the retired-id list, the exact surviving
  id sequence, contiguous display numbering, the merged wording, the absence of
  gender / date of birth / CTC, and that an explicit No on a consent counts as
  answered.
- `frontend/components/application-validation-form.test.tsx` pins the intro
  copy, the document list, and prefill.
- `backend/tests/test_functional_assessment.py` pins that `joining_date` is not
  a mandatory key, is not accepted by `normalise`, and is not reported as a gap;
  that document readiness names its documents; and that `reusable_defaults`
  carries forward while dropping unknown keys.

Full backend suite: 1294 passed. Frontend: 36 passed.
