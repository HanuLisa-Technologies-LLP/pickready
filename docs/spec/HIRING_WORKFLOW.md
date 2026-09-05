# End-to-end hiring workflow

**Status:** normative for the surfaces it names. Precedence rank 4, beside the
Candidate Dashboard Specification: below the RBAC Specification and the Runbook,
above the PRD and the ESD.

**Written:** 2026-09-04.

This document specifies the journey a candidate and a client each take through
ReadyPick, and the eight gates that hold that journey together. Most of it was
already built; what is written here is the whole of it, including the parts that
were not, so the sequence can be read in one place rather than reconstructed
from nine phase sections.

Where this document describes something that already had a name in the
codebase, it uses the existing name. Three of those substitutions matter enough
to state up front:

| The workflow calls it | The product calls it | Why they are the same thing |
|---|---|---|
| Company Hiring Requirements | **Company DNA** (`services/hiring/company_dna.py`) | A company-level statement of what this organisation considers a strong hire, gathered behind guiding questions rather than an empty textbox, editable afterwards, and used as an input to matching and ranking. That is the Company DNA instrument, in twelve sections. |
| Executive Profile | **PRISM Report** (`services/siddhi/`) | The consolidated, decision-oriented view of one candidate, produced once their assessment completes. It already exists, is immutable, and has a fixed section order. |
| Pre-Assessment Report | **Pre-Screen Grade / AI Score** (`services/hiring/prescreen.py`) | Yukti's resume-stage evaluation against the job, before any assessment. spec-doc6 C9 already settled that these two names are one artifact. |

**No fourth artifact was created for any of them.** One implementation per
concept: a second free-text "hiring requirements" box beside Company DNA would
immediately disagree with it about which one Sutra reads, and a second
consolidated candidate document beside the PRISM Report would force a choice
about which one a recruiter is looking at.

---

## 1. The two journeys

```
                         READY PICK
                             |
              +--------------+--------------+
              |                             |
       DIRECT CANDIDATE               DATABANK CANDIDATE
              |                             |
       Creates account                 Resume uploaded by recruiter
              |                             |
       Completes profile              Contact details parsed
              |                             |
       Uploads project evidence       Invitation sent
       if applicable                       |
              |                       Candidate signs in
              |                             |
              +-------------+---------------+
                            |
                     Candidate APPLIES
                            |
                            v
                    Matching + pre-screen
                            |
                            v
                     AI pre-ranking
                            |
                            v
              Hiring team selects for assessment
                            |
                            v
                       Assessment
                            |
                            v
                      PRISM Report
                            |
                            v
                     Final ranking
                            |
                            v
                      Team Review
                            |
                            v
                    Further stages
```

The two paths converge at **applies**, and that convergence is Gate 5. It is
the single most important line in this document: a databank candidate is not an
applicant until they complete the flow themselves.

---

## 2. The eight gates

Each gate is a real check with tests, not documentation. Where it lives is
named, because a gate whose enforcement nobody can find is a paragraph.

### Gate 1 — Company Hiring Requirements before a job can be created

A client cannot create a job until their Company DNA session is complete.

- **Enforced by** `services/hiring/company_requirements.creation_blocked`,
  called at the top of `POST /jobs`. 409 with a message that names the artifact
  and where to complete it.
- **Asked of the TABLE**: the current row with `status = complete`. Not a
  timestamp on the tenant. An open draft does not satisfy it, because the
  compiled artifact does not exist until the session closes.
- **At CREATE, not at publish.** The requirements are what the JD generator,
  the SWOT session and the scorecard derivation all read, so a job drafted
  without them was drafted against nothing. Refusing at publish would let a
  recruiter write a whole JD first.
- **Not retroactive.** A job created before the client completed theirs stays
  created. The gate is on the act; retro-invalidating a live job would close a
  posting candidates are already applying to.
- **Tests:** `tests/test_workflow_gates.py`.

### Gate 2 — the experience band spans at most five years

- **Enforced by** `ExperienceBandMixin._span_within_ceiling` in
  `schemas/jobs.py`, so create, patch and JD generation all inherit it. A
  recruiter cannot create a legal band and then widen it with a PATCH.
- **The ceiling is on the SPAN, never on the values.** 15-to-20 is as valid as
  0-to-5. The rule must not quietly become "no senior roles".
- **Refused, never clamped.** Clamping would have to choose which end of the
  recruiter's band to discard, and either choice publishes a JD advertising a
  range nobody agreed to.
- **Why five:** a 0-to-12-year posting is not one role. It collects freshers
  and architects into a single pool that no scorecard can rank coherently,
  because the rubric level a Must-have needs at one year and at twelve is not
  the same level.
- **Ordering matters.** An inverted pair is reported as a data-entry mistake
  rather than as a span of minus seven years.
- **Tests:** `tests/test_workflow_gates.py`. Mirrored in the Create Job form so
  the recruiter is told before they submit; the server is the gate.

### Gate 3 — the Hiring Manager's SWOT before publication

Already built. `api/jobs._publication_blocked` refuses publication while
`swot_completed_at` is null or the Tatva matrix is not frozen, and it asks the
table rather than a stamp.

### Gate 4 — the recruiter posts, and only with everything in place

Already built, as RBAC 17's lifecycle: `DRAFT -> SENT_TO_HIRING_MANAGER ->
IN_REVIEW -> FINALIZED -> PUBLISHED`. Publication additionally requires Gate 3.

### Gate 5 — a databank candidate must onboard before counting as an applicant

- **Enforced by a MISSING EDGE.** `hiring_pipeline.SOURCED` is the first
  pipeline stage and its only forward edge is `applied`. A sourced candidate
  cannot be invited to an assessment or shortlisted, because the transition
  does not exist. It is not a flag a future caller has to remember.
- **What was wrong before:** `POST /jobs/{id}/candidates/databank` wrote every
  uploaded resume as `applied`. That row says a person read this job, wanted
  it, and submitted an application with their notice period in it. None of that
  happened. Every count, funnel and "applicants" figure inherited the claim.
- **The invitation** (`POST /jobs/{id}/candidates/databank/invite`) sends an
  email asking them to sign in and apply. It moves nobody: being emailed is not
  applying. Every skip is reported, never swallowed; an unidentifiable resume
  (no email on it) is skipped with a named reason rather than mailed into the
  void at `@placeholder.invalid`.
- **The candidate converts the row themselves.** Applying through the portal
  finds the existing sourced link and transitions it, so the recruiter keeps
  one candidate on the job and the databank provenance survives. The apply
  screen does not tell somebody acting on our own invitation that they have
  already applied.
- **The Dashboard funnel excludes `sourced`.** Mapping it to Applied would
  report a recruiter's own filing cabinet as inbound applications, which is the
  confusion the stage exists to end.
- **Tests:** `tests/test_databank_onboarding.py`.

### Gate 6 — only candidates the hiring team selects are assessed

Already built. `POST /pipeline/jobs/{id}/select-candidates`. The
`assessment_conversations` row IS the invitation, so an uninvited candidate
cannot reach the questions by guessing a URL.

### Gate 7 — the final ranking incorporates the assessment

- **Enforced by** `job_candidates.order_by_clause`. Two stages: the assessed
  candidates ranked by their assessment, then everyone else ranked by their
  resume.
- **What was wrong before:** the top sort key was a resume-derived skills
  score, so a candidate who ranked first on their resume ranked first for ever
  and the assessment moved nobody. This document requires that "a candidate who
  ranked highly before assessment can move down", and a ranking whose top key
  is a resume score cannot do that at all.
- **Stage first, rather than one blended number.** An assessment score and a
  resume similarity score are not on the same scale, so any fixed weighting
  between them is a number nobody can justify — the same argument
  `services/rag/fusion` makes for reading order rather than mixing a cosine
  distance with a `ts_rank`.
- **The resume keys survive underneath.** They are the whole order for the
  unassessed pool, and among the assessed they break a tie between two
  identical assessment scores, which is common on a four-band scale.
- **The stage is read from the REPORT** (`rep.synthesized_at`), never from
  `l.status`. A status is a denormalised mirror; the report is the artifact.
- **Tests:** `tests/test_final_ranking.py`.

### Gate 8 — the client closes the job when the requirement is met

- **Enforced by** `POST /jobs/{id}/close` and `jobs.closed_at`. The 30 days are
  the LONGEST a posting runs, never the shortest.
- **`closed` is a fifth posting state and it dominates the four date-derived
  ones**, checked first in `job_posting.posting_status`. Checked after the
  window, a job closed on day 18 would keep reading as active for twelve more
  days, which is exactly the twelve days the client closed it to avoid.
- **A column, not a back-dated `posting_start_date`.** Moving the start would
  rewrite which applications count as in-window and which profiles count as
  Old, and both are read as history.
- **Terminal for candidates, invisible to the team.** No new applications, no
  public link, the job leaves every board. The ranked list, the reports, the
  pipeline stages and anybody mid-assessment continue untouched —
  `can_edit_application` deliberately takes no `closed_at`, so somebody already
  invited can finish work the client has already been charged for.
- **No reopen.** RBAC 22 asks for a controlled revision mechanism, and
  reopening would restart a window candidates have been told is over.
- **Everyone still waiting is told**, through the Updates feed, in one
  statement. Sourced, rejected and joined rows are excluded: a closure notice
  after a rejection reads as a second rejection.
- **Tests:** `tests/test_job_closure.py`.

---

## 3. The candidate's Updates feed

Sections 14 and 15 of the workflow. A dedicated page inside the Candidate
Portal recording everything that has happened on their applications.

**Why it exists.** Email was the product's only channel to a candidate, and
email silently fails: a spam filter, a full inbox, or a typo in an address a
recruiter uploaded, and somebody misses an assessment invitation with neither
side ever finding out. Missing an email must not mean missing an opportunity.

**Rules:**

- **The copy is a fixed catalogue in `services/candidate_updates.py`, not a
  prompt.** No model is called. This is the surface that exists because the
  EMAIL might not arrive, and the email is the thing that already depends on a
  provider; a feed that failed the same way at the same time would protect
  nobody.
- **No score, no grade, no rank, no number.** A stage change tells the
  candidate what happened, never what it means about their chances. Swept over
  the whole catalogue by `tests/test_candidate_updates.py`, because a rule
  enforced at one call site is a rule the next entry breaks.
- **This is not `email_log`.** That table is an outbound delivery record, and
  it carries rows for internal recipients too. Not every update has an email
  and not every email has an update. One table would force every future event
  to invent an email it does not send.
- **The write happens at the chokepoint.** `hiring_pipeline.apply_transition`
  writes the feed row beside the two status writes, so none of its six callers
  can forget. It still does not send the email, and that distinction is why the
  third write is acceptable: an email needs drafting, a provider, review and a
  dispatch, and a feed row needs none of them.
- **`sourced` produces no update.** A resume landing in a recruiter's databank
  is not an event the candidate caused. The invitation, if one is sent, is what
  they hear about, and it has its own kind.
- **A stored `link_path` must be relative**, enforced by a database CHECK. The
  feed renders it as an href, and a row carrying an absolute URL would turn the
  candidate's own Updates page into somebody else's redirector.
- **Marking read is automatic on opening the page**, and the unread styling
  survives that render: somebody who came to see what is new should still be
  able to see which rows were new.

---

## 4. New Candidates

Section 32. Candidates who apply after a selection round are surfaced in their
own section so they are not invisible.

The invisibility is real and it is a consequence of ranking: a recruiter works
down a ranked page, selects a batch and moves on, and somebody who applies the
next morning lands wherever their score puts them, which for most people is a
page nobody opens again.

- **New means: arrived after the most recent time this job invited anyone to an
  assessment.** That instant is when the team last acted on the ranking.
- **Derived, never stored**, exactly like `profile_age`. A stored flag needs
  back-filling on every round and is wrong for every row written between the
  round and the backfill.
- **Before the first round, nobody is new.** The comparison is against a MAX
  over an empty set, which is NULL, and `created_at > NULL` is NULL. A job
  whose first batch has not gone out has one pool, not a pool plus a
  supplement.
- **The count is over the whole job, never narrowed by the page filters.** It
  answers "how many people are waiting outside the list you are looking at",
  which a count narrowed by the same filter could not.
- **Presentation only.** It changes no score, no ranking and no access.
- **Tests:** `tests/test_new_candidates.py`, against a real database, because
  the two properties most worth pinning are three-valued-logic behaviours that
  read as correct in Python and are wrong in SQL.

---

## 5. What this document deliberately does not add

- **No "Executive Profile" table.** The PRISM Report is that artifact and it is
  already immutable with a fixed section order. A second one would force a
  choice about which document a recruiter is reading.
- **No second Company Hiring Requirements field.** Company DNA is it. Sutra
  reads the COMPILED artifact by design, because an unbounded client-authored
  string in a prompt that decides what every candidate is graded on is an
  injection surface.
- **No "invited" pipeline stage between sourced and applied.** `email_log`
  already records who was written to and when. A stage meaning "we emailed
  them" is a stage nothing can leave except by the same edge, and it would let
  a recruiter mistake having sent an email for having had a reply.
- **No blending of assessment and resume scores into one number.** See Gate 7.
