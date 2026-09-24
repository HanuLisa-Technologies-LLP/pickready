# CLAUDE.md section draft: Phase 6 WP6-E (candidate frontend)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly. No
migration and no backend change. It builds against the API shapes of WP6-B
(apply, board, renewal, deletion), WP6-C (candidate messages) and WP6-D
(`correction_needed`, the HR address correction), and depends on their merge.

## ONE APPLY FORM, AND SUBMITTING ENDS ON THE APPLICATION

`components/apply-form.tsx` is the only application form. The New Jobs dialog
renders it with `source="direct"` and the public `/apply/{job}` page with
`source="external_link"`; the server's one apply path is the reason there is
one form. It asks for a resume (the main one or a fresh upload) and the six
validation fields the server serves, and nothing else.

- **Already applied is decided BEFORE any field renders**, from
  `apply-context`, and the board says it on the card (`already_applied`,
  `application_id`) so nobody opens a form that can only answer 409. A 409 on
  submit asks the server which refusal it was instead of matching the words.
- **Success points at `/portal/applications?application=<id>`, never at an
  assessment.** The old "Submit and start assessment" sent every applicant to
  a questions page that refused them: inviting is the recruiter's act.
- **The public page lost its personal fields, the mandatory Age and Gender,
  the forty-item questionnaire and the organisation-route fallback.** The job
  is read from `/jobs/public/{id}`, then the candidate's `/portal/jobs/{id}`,
  and never a staff route. `aspects-form.tsx`, `lib/aspects.ts` and the
  outreach page are deleted; `test_aspect_form_removed.py` sweeps for them.

## A FAILED READ IS NEVER RENDERED AS AN EMPTY ANSWER

Rule 6 applied to screens. Four places turned a failed request into a
statement about the candidate's world, and each was a lie a person acted on:

- New Jobs rendered a failed board load as "No matching roles yet".
- The public apply page rendered a 5xx as "This job is not available". Only
  401, 403 and 404 mean a role this visitor cannot read; anything else is a
  failure with a retry.
- Applied Jobs opened the job dialog on "This posting has closed" for the
  length of every request. Loading is its own state, and only a 404 is a
  closed posting.
- The employment history card rendered NOTHING on any error. A 404 (no
  candidate record yet) still hides it; everything else is said.

The one-click keep-profile page and the append-employer form showed
`ApiError.message`, which is the transport's "API error 404".
`apiErrorMessage` reads the server's `detail`; use it for anything a person
reads.

## MESSAGES: THE TOKEN AND THE DRAFT BELONG TO THE THREAD

- **`client_token` is minted per DRAFT, not per attempt** (`ComposerToken`). A
  retry after a lost response reuses it, so the server collapses it into the
  message it already stored; a confirmed send or an edit rotates it, because
  one token carrying two texts is a 409 now.
- **Drafts and tokens are keyed by thread.** One shared box carried words
  written to one company into the next thread selected, one Enter away from
  replying to the wrong employer.
- **"Load earlier" pages on the pair (`created_at`, `id`)**; a timestamp alone
  drops whichever message sits on the page boundary. A page shorter than the
  limit is the start of the thread.
- **Opening a thread marks it read and tells the nav badge** through one
  window event (`announceUnreadChanged`). The badge reads
  `GET /conversations/me/unread`; a failed count renders no badge rather than
  a zero. `?conversation=` opens the thread an Updates entry names.

## MY PROFILE SHOWS THE CANDIDATE'S OWN RECORDS, IN THE SERVER'S WORDS

Four capabilities had routes and no screen. Each card renders server-authored
sentences and computes no rule of its own.

- **Verification documents** (`bgv-documents-card.tsx`): EVERY accepted type,
  empty ones included (the compliance-slot rule), the server's limit hint and
  refusals verbatim. It claims nothing about employers: there is no recruiter
  route to these files.
- **The HR address correction** lives on the employment history card and
  appears ONLY on rows the server marks `correction_needed`, which is the same
  condition the correction route accepts. One field; the claim itself never
  becomes editable.
- **Keep my profile** (`consent-renewal-card.tsx`): stage, dates and message
  from `GET /portal/me/consent/renewal`. Reading renews nothing; the button
  posts and then RE-READS rather than computing the next date.
- **My consents** (`consent-history-card.tsx`): the whole catalogue and the
  verbatim history. No version identifier and no digest on screen: those are
  provenance for an auditor. A history act with no recorded wording says so.

## LEAVING IS COMPLETE, AND RECOVERY IS FIREBASE'S

- **Delete My Profile signs the Firebase client out before the hard
  navigation.** When the server KEPT the sign-in because the address is also a
  staff login, its `sign_in_identity_note` is shown and nothing leaves until
  the person presses Continue.
- **Forgot password is `sendPasswordResetEmail` and nothing else**
  (`components/forgot-password.tsx`, on the login page and the apply page's
  sign-in). This AMENDS the wording of general rule 2 ("do NOT build a custom
  password store or forgot password flow") without changing its substance:
  there is still no custom flow, no token and no route of ours; the control
  asks Firebase to send its own reset email. **A missing account reads exactly
  like a sent email**, so the sign-in screen cannot enumerate registered
  addresses; pinned by `forgot-password.test.tsx`.

## SMALLER RULES

- `/portal/assessments` is a redirect to `/portal/applications`: old feed rows
  and emails link to the bare path (probe PR-7), and a 404 for a link the
  product sent reads as the assessment being gone.
- The candidate pages read no `job.level`; `PortalJobOut` no longer carries it.
- Real activity is `lib/user-activity.ts` (WP6-A): `X-User-Activity: 1` only
  within five seconds of a pointer, key or touch event. A focus refetch or a
  poll renews nothing, which is why Messages refetches on focus freely.
