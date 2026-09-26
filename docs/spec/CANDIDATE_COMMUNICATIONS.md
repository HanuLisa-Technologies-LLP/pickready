# Candidate identity and communications

**Status:** normative for the surfaces it names. Precedence rank 4, beside
[HIRING_WORKFLOW.md](HIRING_WORKFLOW.md): below the RBAC Specification and the
Runbook, above the PRD and the ESD. Where this document and the hiring
workflow touch (the sourced stage, the Updates feed), the workflow governs the
SEQUENCE and this document governs who the candidate is and how words reach
them.

**Written:** 2026-09-24, for the Vivekium release, Phase 6 (audit Part 1
section 3 items 3, 5, 6, 7, 21, 24 and 25; Part 2 "candidate email replies
not threaded"; Part 3 "Gmail wording").

**Read beside it:** the Phase 6 section of [../../claude.md](../../claude.md)
and its drafts under `docs/release/2026-09-vivekium/` (`claude-p6.md`,
`claude-p6-a.md`), which carry the reasons and the tests that pin each rule.

---

## 0. What "email" means in this product

When a screen, a document or a person says a candidate or a recruiter was
"emailed", it means exactly two things and nothing else:

1. **Outbound email**, sent by the backend through the one deployment
   transport (`email_transport`, SMTP or SES), recorded row by row in
   `email_log` with the copy actually sent.
2. **In-portal threads** (`conversations`, `conversation_messages`), which a
   candidate reads under Messages and a recruiter reads on the candidate's
   case panel.

**There is no inbox synchronisation.** Vivekium never reads a Gmail, Outlook
or corporate mailbox, never imports a sent folder and never mirrors a
recruiter's personal correspondence. A reply reaches the product only through
the reply address of section 5, and only where inbound mail is enabled. Copy
that says "Gmail" to mean "email" is wrong on its face: Gmail is at most the
SMTP relay a deployment happens to use.

---

## 1. Identity: one person, one candidate record

A signed-in candidate is a `users` row with `role = candidate`. Their
candidate record is the `candidates` row whose `user_id` is theirs.

| Question | Answer | Where |
|---|---|---|
| Which record is this request's? | The row with `candidates.user_id = users.id`. Nothing else, ever. | `services/candidate_identity.resolve_candidate_id`, `require_candidate` |
| When is an email address matched? | At SIGN-IN only, and only when Firebase reports the address verified. | `candidate_identity.link_on_sign_in` |
| Which record does a verified sign-in link? | The OLDEST unlinked record with that address, compared case-insensitively. Audited `candidate_linked`. | same |
| What does an unverified sign-in get? | A record of its own. It links nothing, because it proved nothing about the address. | same |
| How many records may one user own? | One. `uq_candidates_user_id` (unique where set), migration 0120. | database |
| Which record does an upload attach to? | The signed-in record first, then the oldest, case-insensitively. | `candidate_identity.find_canonical_by_email` |

**There is no request-time email fallback.** A resolver that matches
`email = users.email` at request time hands a password sign-up that typed
somebody else's address the record a recruiter sourced for that somebody.

**Records are never merged.** When a verified applicant applies to a job on
which an UNLINKED record with the same address holds a `sourced` link, that
one link is re-pointed to the applicant (`rehome_sourced_link`, audited
`candidate_link_rehomed`) and then moved `sourced -> applied` through
`hiring_pipeline.apply_transition`. The old record, its profile and its feed
rows stay as history. Twenty-odd tables reference `candidates.id`, so a bulk
merge on live data is erasure-grade risk for a problem one link solves.

**Sign-in is Google or email and password, for every role.** Phone sign-in is
removed (owner ruling); `users.phone` survives as contact data. Password
recovery is Firebase's own reset email ("Forgot password" on the sign-in
screens); Vivekium stores no password and runs no recovery flow of its own,
and the screen answers the same sentence whether or not an account exists for
the address, so it cannot be used to discover who has one.

---

## 2. Sessions: the idle deadline moves for a person, never for a timer

- A session is the Redis record in `services/auth_sessions`; every auth cookie
  is a browser-session cookie (no Max-Age, no Expires). The access JWT expires
  after fifteen minutes and is refreshed; the SESSION ends after thirty
  minutes without real activity, or when the browser closes.
- **Only a request carrying `X-User-Activity: 1` renews the thirty minutes.**
  The browser sends it only within five seconds of a real pointer, key or
  touch event (`frontend/lib/user-activity.ts`). A polling tab, a background
  refetch and a socket never renew it.
- A socket checks the session (a `sid` is required, the store is asked, a
  store outage refuses) and never renews it.
- Signing in is activity. Redis unavailable is 503, never a fail-open check.

The consequence is deliberate: somebody who reads a page for thirty minutes
without touching it is signed out. Typing and clicking in an assessment count.

---

## 3. Applying

There is ONE application form, used by the in-portal New Jobs dialog and by
the public `/apply/{job}` page.

- **Fields:** a resume (the main resume reused, or a new upload) and the six
  mandatory validation fields of `services/application_validation.py`.
  Nothing else. The 40-aspect questionnaire, the mandatory Age and Gender, and
  the full-name and city fields are gone, and nothing wrote anything anybody
  read (`candidates.age` and `gender` had no reader).
- **Applying never touches consent.** The databank consent is changed only on
  the consent surfaces; an application that rewrote it could revoke a consent
  given elsewhere without the candidate noticing.
- **Applying never starts an assessment.** The confirmation is "Application
  submitted" with a link to the application. An assessment exists only when a
  recruiter invites (`select-candidates`), because the
  `assessment_conversations` row IS the invitation.
- **`application_source` is `direct` or `external_link`** (`sourced` survives
  only on historical rows). A person who applied through a shared link is an
  APPLICANT: `source_type` is `applied`, never `sourced`.
- **Already applied is said before any field is shown**, on the New Jobs board
  and in the dialog. A recruiter's databank entry (`status = sourced`) is not
  an application and is never listed under Applied Jobs.
- Retired with the form: `GET/POST /portal/outreach/{token}`,
  `POST /verification/outreach` and the outreach token helpers.

---

## 4. Messages

- **The database is the record; the socket is a notification.** Unchanged
  from 2026-09-12.
- **Unread is per candidate**, a forward-only watermark
  (`conversations.candidate_last_read_at`). The candidate nav badges Messages
  with the unread count; opening a thread marks it read.
- **"Load earlier" pages backwards** with `?before=`; nothing caps a thread at
  its last fifty messages.
- **A retry is the same message.** The client token belongs to the DRAFT: kept
  across failed attempts, rotated after a confirmed send or an edit to the
  text (`frontend/lib/composer-token.ts` on the recruiter side). Minting a
  token per click turned a send whose response was lost into two messages.
- **A candidate is told when a recruiter writes**, by an Updates feed row and a
  `message_notification` email (`pickready.notify_candidate_of_message`,
  dispatched after commit). Fixed deterministic copy, no model. Debounced: a
  burst of messages inside `candidate_message_notify_debounce_minutes` is one
  email, and a message the candidate has already read sends none.
- **A thread with no message is not shown** to either side.
- **A BGV thread stays refused** to chat sends and invisible to the candidate
  it concerns (unchanged from 2026-09-12).

---

## 5. Reply threading

Every recruiter-sent candidate email and every message notification carries
`Reply-To: conversations+<thread token>@<inbound domain>`. The inbound webhook
reads the token from the ADDRESS, never from a subject line (unchanged rule),
and posts the reply into the thread:

- a `candidate` thread records the reply as the CANDIDATE, `channel = email`,
  with the sending address shown to the recruiter, so a reply from a different
  address is visible rather than silent;
- the quoted history below the reply (`On ... wrote:`, `-----Original
  Message-----`, a trailing `>` block) is cut, and an empty result keeps the
  original text rather than posting nothing;
- a `bgv` thread is unchanged: the employer's reply, and no inferred verdict.

**Automatic emails (application confirmation, reminders) keep today's
Reply-To**, because a reply to an email no human sent would land in a thread
nobody is watching. ASSUMPTION, owner visible.

**Inert on pilot.** The pilot region is not an SES receiving region, so
`INBOUND_EMAIL_DOMAIN` is empty there, no reply address is minted and a reply
reaches the sending mailbox instead. The code ships and is tested; making it
live is an owner infrastructure decision (a receipt rule in a receiving region
and MX on the reply subdomain). **Do not describe candidate reply threading as
working in production until that is done and observed.**

---

## 6. Outbound email: one writer, a sender, no duplicates

- **`services/email_outbox.queue_candidate_email` is the one writer** of a
  candidate-facing `email_log` row, for the recruiter's composer, the
  lifecycle transition emails, the databank invitation and the automatic
  emails alike. It dispatches the send after commit.
- **Every row carries a sender.** The one a person chose (an ACTIVE sender of
  this tenant), else the tenant's default sender, else NULL, meaning the
  Vivekium mailbox. The sender is re-validated at send time, so a sender
  revoked between queueing and sending is not used.
- **The default sender** is chosen on the Email senders card by a holder of
  `authorize_email_senders`. Only an ACTIVE sender may hold it, one per
  tenant, and any transition away from `active` clears it in the same
  statement. The composer preselects it; somebody who may not list senders
  posts none and gets the same default.
- **A send is claimed atomically** (`queued -> processing`), so two
  deliveries of one dispatch send one email. A row stuck in `processing` is
  logged and counted, NEVER resent: the send may have happened, and a
  duplicate email is worse than a late alarm. Rows left `queued` by a lost
  dispatch are re-dispatched by `pickready.reconcile_queued_emails`.
- **Reminders are keyed by STAGE**: `assessment_reminder:<link>:<hours>`
  under a unique `email_log.dedupe_key`. The 24 hour and the 72 hour
  reminder both send; a redelivered 24 hour reminder is a no-op.

---

## 7. Leaving: Delete My Profile and the Firebase identity

- Delete My Profile erases the candidate's rows (`cascade_erasure`) and, in
  the SAME transaction, deletes their Firebase identity. If the identity
  cannot be deleted the request answers 503 and NOTHING is deleted; a person
  asked to leave and must not be left half gone.
- **A shared identity is kept**: when the same address is also a staff login,
  deleting the Firebase account would lock that staff member out, so the
  identity stays and the response says so.
- The browser signs out of Firebase before it leaves the page.
- **The inactivity erasure run by a worker keeps the Firebase identity, by
  design**: workers hold no Firebase key, and a person who never asked to
  leave keeps the door back.

---

## 8. The recruiter's side

- **One place per candidate for follow-up work**: the candidate case panel
  (`components/candidate-case-panel.tsx`) carries Messages, Background
  verification and Projects. It is the only mount of the BGV verification
  panel, which the offer gate depends on.
- **Deleted, not hidden**: the unlinked `/org/review` screen, the
  `/org/templates` builder, their components, and the two routes that moved a
  pipeline status without `apply_transition`
  (`POST /candidates/links/{id}/decision` and `/status`). Every status change
  goes through the one chokepoint and its offer gate, feed row and email.
- **A closed job's assessment records** are explained on the job page by the
  retention panel (`components/assessment-retention-panel.tsx`): the server's
  own sentence, the deletion date, and for a holder of
  `retrieve_disputed_assessment` the dispute path (open with a stated reason,
  close). Dates only; no count and no candidate detail. The dispute never
  extends the thirty days.

---

## 9. What is NOT true yet

- Candidate reply threading does not work on pilot (section 5).
- `orchestration/versioning.resolve_for_application` still has no production
  caller (claude.md, 2026-09-23); nothing in this document depends on it.
- The `/outreach` bulk email routes (`api/outreach.py`) and
  `/companies/me/email-templates` lost their last screen with the review
  page and the templates builder; they are recorded for the Phase 7 route
  sweep rather than silently kept.
