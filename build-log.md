# PickReady build log

Newest first. Each entry has a **Product** half (what a user can now do, or can
no longer do) and a **Technical** half (what changed in the code and why that
shape rather than another). Where a decision has a failure mode that a reader
would otherwise re-introduce, the entry says so.

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
