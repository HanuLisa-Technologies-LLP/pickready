# CLAUDE.md section draft: Phase 6 (candidate portal and communications)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.

**How to read this draft.** Part 1 is WP6-F, the recruiter frontend, and every
sentence in it describes code on `wip/p6-f` with the test that pins it. Part 2
is WP6-A in one paragraph, because `claude-p6-a.md` carries it in full. Part 3
is the rest of the phase (WP6-B, C, D, E) as DESIGNED in `PLAN-p6.md` and
`CONTRACT.md` v2/v3; those packages were built in parallel and their own
drafts and code are authoritative. **Before folding Part 3 in, check each
sentence against the merged tree**: a rule written here and not true in the
code is exactly the "true in prose only" defect this file keeps recording.
Part 4 lists the supersessions to mark in place.

The normative document for the whole phase is
`docs/spec/CANDIDATE_COMMUNICATIONS.md` (new, indexed in `docs/README.md`).

Suggested jump-table row:

| Candidate identity and communications (2026-09-24) | One candidate record per person, the idle rule, one application form, messages and notifications, the one email writer and its sender, the recruiter's case panel and the retention panel |

---

## Part 1. WP6-F, the recruiter frontend

### DELETING AN ORPHANED PAGE CAN ORPHAN A LIVE FEATURE, SO FOLLOW THE MOUNTS

The brief asked for `/org/review` and `/org/templates` to be deleted with the
components only they used. Both pages were unlinked from every navigation
entry, which read as "nothing reaches them". **Following the imports said
otherwise.** `/org/review` was the ONLY mount of `profile-review.tsx`, and
that was the ONLY mount of four live recruiter surfaces: the BGV verification
panel, the BGV results panel, the recruiter's thread with a candidate
(`candidate-conversation-card` over `conversation-panel`) and the project
evidence panel. The BGV verification panel is the only screen on which a
person can mark a previous employer verified, and the offer gate in
`hiring_pipeline.apply_transition` refuses an offer until somebody has. So
deleting the page alone would have made every candidate who declared an
employer permanently un-offerable, with nothing failing anywhere.

- **The four panels moved into `components/candidate-case-panel.tsx`**: one
  tabbed block (Messages, Background verification, Projects) that owns no data
  and takes a candidate id, so any candidate surface can embed it. Each panel
  still loads, authorizes and refuses for itself. Tabs mount lazily and a new
  candidate starts on the requested tab, never on the previous person's.
  `candidate-case-panel.test.tsx`.
- **Its mount is requested, not made**: the Candidate Dashboard's profile sheet
  (`candidate-dashboard/panels.tsx`, Phase 2's file) is the natural home. Until
  that hunk lands, the panels have no route, which is the state they were
  effectively already in.
- **Deleted**: both pages, `candidate-decision-actions.tsx`,
  `hm-decision-actions.tsx`, `candidate-selection.tsx`, `profile-review.tsx`
  (it also rendered the retired 40-aspect answers and "PPI" copy),
  `send-outreach-modal.tsx` with `lib/outreach-payload.ts` and its test (a
  SECOND bulk-email path beside the composition modal, which rule 5 forbids and
  which bypassed the sender entirely), and the `EmailTemplate` type.
  `jobs-list.tsx` was Phase 1's to delete.
- **The rule, general form: an unlinked page is not a dead page until you have
  followed what it mounts.** "No nav entry" answers who can FIND a screen, not
  what the screen is the only home of.

### A RETRY IS THE SAME MESSAGE, SO THE TOKEN BELONGS TO THE DRAFT

`conversation-panel.tsx` called `newClientToken()` inside the send handler, so
every click minted a fresh idempotency token. The server collapsed duplicates
correctly; the client was telling it that a retry after a lost response was a
new message. `lib/composer-token.ts` (`createComposerToken`,
`useComposerToken`) makes the token a property of the DRAFT: minted when a
draft starts, kept across failed attempts, rotated after a CONFIRMED send and
on any edit to the text (different words are a different message even if the
earlier attempt secretly landed). `conversation-panel.test.tsx`,
mutation-checked: a per-attempt token fails "retries a failed send with the
SAME client token".

The 2026-09-12 rule "Idempotency is a client token the CLIENT mints" was
right and incomplete: it never said WHEN the client mints it, and minting per
attempt satisfies the sentence while defeating its purpose.

### THE DEFAULT SENDER, AND THE SENDER THE COMPOSER POSTS

`email_log.sender_id` was never written by anyone and no screen ever posted
one, so an approved corporate mailbox was never used (audit P1-3.6; the audit
said the screen "accepts a sender" and no screen did).

- **`email-senders-card.tsx`** labels the tenant's default sender and offers
  Make default on an ACTIVE sender and Stop using as default on the current
  one, against `POST`/`DELETE /email-senders/{id}/default` (WP6-C), to a
  holder of `authorize_email_senders` only. A non-active sender is never
  offered it. `email-senders-card.test.tsx`, mutation-checked on the active
  guard.
- **The card's toast sentences are an action TABLE** (`SENDER_ACTIONS`), each
  written whole. Building "Could not " + label.toLowerCase() is how the card
  told people it "could not approved this sender".
- **The Add dialog no longer promises a six-digit mailbox code.** The code was
  withdrawn on 2026-09-08 and the dialog kept saying it would be sent for
  sixteen days. A promise printed on a control is a claim about the code.
- **`email-composition-modal.tsx` posts `sender_id`**: a person who may list
  senders (`manage_email_senders`) picks an ACTIVE sender, preselected to the
  default; a sender that cannot send yet is shown and disabled. Everybody else
  is shown no picker, posts no sender and gets the default the server
  resolves, and is told so in one line. **A "Vivekium mailbox" choice is
  offered only when no default exists**, because omitting the sender MEANS
  "the default" on the server: a choice the request cannot express is a
  promise the send would break. A senders list that fails to load is said out
  loud and the send still works. `email-composition-modal.test.tsx`,
  mutation-checked: dropping `sender_id` from the body fails.

### THE DISPUTE PATH HAS A SCREEN NOW

The Close dialog has promised since 2026-09-22 that a closed job's records
"can be retrieved only through the assessment dispute process", and the three
routes behind that promise had no UI.
`components/assessment-retention-panel.tsx` (mounted by the orchestrator on
the job page when `closed_at` is set):

- renders the server's `message` VERBATIM (`job_assessment_retention` is its
  one author, the same sentence the 410 gate answers with) and the deletion
  DATE;
- **shows no count**: it renders the date, never `days_remaining`, so no
  number stands next to the word "assessment";
- offers a holder of `retrieve_disputed_assessment` (new constant
  `CAP.retrieveDisputedAssessment` in `lib/permissions.ts`) Open a dispute,
  with a required reason the server records, and Close the dispute; says to
  everybody else, through `ReadOnlyNotice` and nothing else, that they may
  view it and not change it; offers nothing at all once the records are
  purged;
- shows a refusal (409, 410, 422) verbatim.

`assessment-retention-panel.test.tsx`, eight cases. **Mutation found a gap in
the first version of the tests**: dropping the capability guard on the open
form passed every case, because the no-capability case only covered an
already open dispute. A case now pins that the form is never offered without
the capability.

---

## Part 2. WP6-A, identity and sessions (see `claude-p6-a.md`)

One resolver (`services/candidate_identity`), `candidates.user_id` only at
request time, email matching only at sign-in and only on a Firebase-verified
address, one record per user by a unique index (migration 0121), conversion by
re-pointing one sourced link and never by merging records; phone sign-in
removed; the idle deadline renewed only by a request carrying
`X-User-Activity: 1`, which the browser sends within five seconds of a real
interaction; the socket checks the session and never renews it;
`firebase_auth.delete_identity`; `tests/candidate_session.py`, a real
candidate session for tests with no dependency overrides.

---

## Part 3. The rest of the phase, as designed (verify before folding in)

### ONE APPLICATION FORM (WP6-B, WP6-E)

- The in-portal dialog and the public `/apply/{job}` page use ONE component
  (`components/apply-form.tsx`): a resume and the six validation fields.
  The 40-aspect form, mandatory Age and Gender, and the name and city fields
  are deleted with `GET/POST /portal/outreach/{token}`,
  `POST /verification/outreach` and the outreach token helpers. `age` and
  `gender` had no reader.
- **Applying never touches consent** (it used to rewrite
  `consent_databank` from the aspects form on every apply) **and never starts
  an assessment** (no `assessment_conversations` row, no question dispatch;
  the row IS the invitation, and only a recruiter invites). Parse and
  confirmation are dispatched after commit.
- `application_source` in {`direct`, `external_link`}; a link applicant is an
  APPLICANT, never `sourced`. Already-applied is said before any field;
  `sourced` links never appear under Applied Jobs.

### MESSAGES AND NOTIFICATIONS (WP6-C, WP6-E)

- A per-candidate forward-only read watermark, an unread badge on Messages,
  "Load earlier" with `?before=`, threads with no message hidden.
- A recruiter's message dispatches `pickready.notify_candidate_of_message`
  after commit: an Updates feed row and a `message_notification` email, fixed
  copy, no model, debounced by `candidate_message_notify_debounce_minutes`,
  and nothing when the candidate has already read it.

### ONE CANDIDATE EMAIL WRITER (WP6-C)

- `services/email_outbox.queue_candidate_email` is the only writer of a
  candidate-facing `email_log` row. Sender: the chosen ACTIVE one, else the
  tenant default, else the platform mailbox, re-validated at send time.
- **The send is CLAIMED** (`queued -> processing` in one UPDATE); a row stuck
  in `processing` is logged and counted and NEVER resent, because the send
  may have happened. `pickready.reconcile_queued_emails` re-dispatches rows a
  lost after-commit dispatch left `queued`.
- **Reminders are keyed by stage** (`assessment_reminder:<link>:<hours>`,
  unique `dedupe_key`): the 72 hour reminder was skipped as "already sent"
  while reconciliation counted it as sent.

### REPLY THREADING SHIPS INERT ON PILOT (WP6-C)

A reply to a recruiter-sent email or a message notification is posted into
the candidate thread as the CANDIDATE, quoted history cut, sending address
shown. Automatic emails keep today's Reply-To. **Pilot has
`INBOUND_EMAIL_DOMAIN=""` because its region is not an SES receiving region,
so none of this is observable there.** Never describe it as working in
production until an owner enables inbound and it is observed.

### LEAVING (WP6-B, WP6-E)

Delete My Profile deletes the Firebase identity in the same transaction as the
rows; a failure is 503 and deletes nothing; an identity shared with a staff
login is kept and the response says so; the browser signs out of Firebase.
The worker's inactivity erasure keeps the identity, by design (no Firebase key
in the worker, and a person who never asked to leave keeps the door back).

### RECRUITER ROUTES (WP6-D)

`POST /candidates/links/{id}/decision` and `/status` are DELETED. Both wrote a
`pipeline_status` row and an audit row and never touched `link.status`, so
they bypassed `apply_transition` and with it the offer gate, the Updates feed
and the transition email. BGV `/bgv/me*` routes take the CANDIDATE principal
(they took a staff principal and a candidate session at once, which no token
satisfies; every test overrode both).

---

## Part 4. Supersessions to mark in place

- **2026-07-27 "Job detail + router"**: "There is no separate Review Screen,
  no Email Templates builder". **This was prose only until this release**:
  both pages existed, reachable by URL, for two months. True now.
- **2026-07-28 "BD Portal"**: "`shortlisted` ... is written by
  `api/candidates.decide_profile`". That route is deleted, and it never wrote
  `link.status` in the first place. `shortlisted` is reached through
  `apply_transition`, for example from the Candidate Dashboard's stage
  control; `MANUAL_TRANSITION_EXCLUDED` is unchanged.
- **2026-09-06 "Corporate senders"**: add that every candidate email now
  carries the resolved sender and that the tenant has one default sender,
  cleared whenever it leaves `active`.
- **2026-09-12 "Idempotency is a client token the CLIENT mints"**: add "per
  DRAFT, never per attempt" (Part 1).
- **2026-09-12 "The reply address is the routing"**: candidate threads now use
  it too, and it is inert on pilot (Part 3).
- **2026-09-20 "The 30 minutes are a Redis TTL touched only by REAL USER
  ACTIVITY"**: prose only until WP6-A (see `claude-p6-a.md`).
- **General rule 2 (section 3)**: "Google, email/password, and phone"
  becomes "Google or email/password" (WP6-A).
- **General rule 7 (section 3)** names "the verification/40-aspect flow": the
  40-aspect form is deleted (WP6-B, WP6-E); the rule's point (a databank
  profile is reused as it is) stands.
- **2026-07-27 "Unified candidate profile"**, the candidate nav: amended again
  by the unread badge on Messages (no new entry).

## Open, for the owner or a later phase

- **`api/outreach.py`** (`/outreach/preview`, `/send`, `/status`,
  `/send-email`) and **`/companies/me/email-templates`** lost their last
  screen with this package's deletions. Recommended for the Phase 7 route
  sweep, with `POST /candidates/links/{id}/grant-access` and the recruiter
  `GET /conversations/unread`.
- The candidate case panel needs its mount (Part 1).
