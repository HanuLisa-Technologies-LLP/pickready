# CLAUDE.md section draft: Phase 6 WP6-C (messaging and email)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
Migration `0122_candidate_comms` (the orchestrator re-chains it).

## ONE WRITER OF CANDIDATE EMAIL, AND IT DECIDES THE SENDER

`services/email_outbox.queue_candidate_email` is the only code that writes a
candidate-facing `email_log` row. Four places built one by hand (the
recruiter's send, the pipeline transition email, the databank invitation, the
automatic confirmation and reminder) and NONE of them set `sender_id`, so a
corporate sender a client had registered, verified and authorized was never
used for a single email. `api/emails.send_emails` validated the requested
sender and then dropped it. Every writer also dispatched the send BEFORE its
request committed.

- **The sender is resolved on every row** (`resolve_sender`): the one the
  recruiter named, which must be this tenant's and ACTIVE (404 / 409), else the
  tenant's DEFAULT active sender, else None, which is the platform mailbox.
  The worker still re-checks at send time; that is the boundary.
- **The default sender is data.** `client_email_senders.is_default`, at most
  one per tenant by a partial UNIQUE index. `POST/DELETE
  /email-senders/{id}/default` under `authorize_email_senders` (choosing the
  address every automatic email speaks from is the same decision as
  authorizing it), audited, ACTIVE senders only, rows locked so two choices
  serialise. **Every transition away from `active` clears the flag on the same
  row in the same flush**, so the next automatic email falls back to the
  platform mailbox instead of failing at send time.
  `GET /email-senders/active` (under `send_outreach`) is the composer's list:
  a user who sends email may manage no senders.
- **A human-sent email joins the candidate thread.** The recruiter becomes a
  participant, the email is posted in the thread as an `email` channel message
  bound by `email_log_id` (`pending`, moved to `sent`/`failed` by the worker
  with the row), and the worker sets the thread's own reply address as
  Reply-To. Automatic emails are NOT threaded: a reply to a confirmation nobody
  wrote would land in a thread nobody watches.
- **The send is dispatched after the commit** (`dispatch_after_commit`), from
  requests and workers alike. A rolled-back request queues nothing.

## THE SEND IS CLAIMED, AND A CLAIMED ROW IS NEVER RESENT

`_send_lifecycle_email_async` read the row, saw `queued`, and sent, so two
invocations (a redelivery, a double dispatch) both sent. `email_outbox.claim`
is ONE conditional UPDATE, `queued -> processing` stamping `claimed_at`,
COMMITTED before the transport is called. The loser finds no row and does
nothing. A transient failure releases the claim for the retry; every other
outcome is terminal.

`pickready.reconcile_queued_emails` (Route.LAMBDA, every fifteen minutes,
`schedule.py` and all three environments' `module "scheduler"`) repairs a lost
after-commit dispatch by asking the TABLE:

- re-dispatches `queued` rows between ten minutes and a day old (safe, because
  of the claim);
- **reports at ERROR, never sends**, a `queued` row older than a day (a
  confirmation a month late is a mistake, and rows stranded before the sweep
  existed must not go out on deploy day);
- **reports at ERROR, never resends**, a row still `processing` thirty minutes
  after its CLAIM. It may already have left the transport, and a duplicate
  email is worse than a late alarm. Measured from `claimed_at`, never from
  `created_at`: a row re-dispatched an hour after it was written is not stuck
  the instant it is claimed.

## AUTOMATIC EMAILS ARE IDEMPOTENT ON THE STAGE, NOT THE TYPE

The 72 hour assessment reminder was NEVER sent. Idempotence was "any row of
this type for this application", so it always found the 24 hour one, while
`credit_reconciliation` counted it in `reminders_sent` anyway. No test covered
reminders at all.

- **`email_log.dedupe_key` is UNIQUE (partial)** and the outbox insert is
  `ON CONFLICT DO NOTHING`. `application_confirmation:<link>`,
  `assessment_reminder:<link>:<stage hours>`, `message_notification:<message>`.
  Human sends carry no key: writing to somebody twice on purpose is allowed.
  Migration 0122 backfills the key onto the EARLIEST existing row per
  application and type, the row the old check protected.
- **The reconciliation passes the STAGE** (`queue_reminder(link, elapsed,
  stage_hours)`) and dispatches after its commit. Derived from elapsed time
  alone, a conversation found late at 80 hours would label its first reminder
  72 and lose the second to the key. `send_assessment_reminder` takes a
  trailing `reminder_stage`; a payload from the previous release (no stage)
  derives it with `reminder_stage_for`.
- The key is checked BEFORE drafting, so a redelivery does not pay for a model
  call; the database constraint is still what makes it correct.

## A CANDIDATE IS TOLD WHEN A RECRUITER WRITES, AND HAS AN UNREAD COUNT

A candidate is not a `conversation_participants` row (no tenant), so their
read watermark lives on the thread: `conversations.candidate_last_read_at`,
forward only (`mark_candidate_read`). The old `inbound` count on the
candidate's thread list counted every message the company had ever sent and
could never go down; it is REPLACED by `unread`, with `GET
/conversations/me/unread` for the badge and `POST
/conversations/me/{id}/read`. Replying reads the thread.

- **Empty threads are not listed**, on either side for candidate threads.
  Opening a thread is a side effect of clicking "Message"; a list of threads
  that say nothing is a list of clicks. The open route is unchanged.
- **`pickready.notify_candidate_of_message`** is dispatched after a recruiter's
  message (or attachment) commits, in candidate threads only. It writes ONE
  Updates entry (`candidate_updates.MESSAGE_RECEIVED`, link
  `/portal/messages?conversation=<id>`) and ONE `message_notification` email
  through the outbox, fixed copy, no model, the recruiter's words verbatim.
  **One per burst**: `candidate_notified_at` is CLAIMED by one conditional
  UPDATE and re-arms after `candidate_message_notify_debounce_minutes` (default
  10) or as soon as the candidate reads the thread. A message the candidate
  already read is not announced. "Reply to this email" is written ONLY when
  the deployment can receive replies.

## A RETRY CARRIES THE SAME TOKEN AND THE SAME WORDS

`client_token` idempotency is unchanged, and it now also refuses a token
REUSED for different words or by a different author: 409
(`ClientTokenReused`). A composer that kept its token across an edit used to
get the ORIGINAL message back with a 200, silently dropping what was just
written. Attachment tokens are derived from the file AND its caption, so the
same file with a new caption is a new message rather than a refused token.

"Load earlier" pages on the keyset (`created_at`, `id`) when the client passes
`before` and `before_id` (the oldest message it holds). `created_at` alone is a
partial order and skipped messages sharing a boundary timestamp; `before`
alone keeps its old meaning for old clients.

## A CANDIDATE'S EMAILED REPLY IS THE CANDIDATE'S

The inbound router posted EVERY thread reply as `employer_hr`. It now reads
the thread's KIND: a candidate thread's reply is `PARTY_CANDIDATE`, channel
`email`, `author_email` the address it actually came from (shown to the
recruiter, never silently attributed), body through
`conversations.strip_quoted_reply` (cut at the first "On ... wrote:" line,
including the two-line wrapped form, an Outlook "Original Message" divider, or
a trailing `>` block; an empty result keeps the original text). A BGV reply is
unchanged and kept WHOLE, because it is evidence for a verification decision.
The token in the address remains the authorization.

**INERT IN PILOT.** `INBOUND_EMAIL_DOMAIN` is empty there because the pilot
region is not an SES receiving region, so no Reply-To carries a token and
nothing reaches the path. The code ships tested; turning it on is an owner
infrastructure decision (a receipt rule in a receiving region, MX on the
reply subdomain, a staged apply). Do not describe it as verified live.

## RETIRED: THE 40-ASPECT OUTREACH ROUTE AND THE `verification_requests` TABLE

`POST /verification/outreach` (no screen sent it; it mailed a link to the
forty-question form) is deleted with `OutreachIn/Out`. `verification_requests`
is DROPPED by migration 0122 **only when EMPTY**: the upgrade RAISES naming the
count otherwise, because deleting what an employer said about a verification
that really ran is an owner decision, never a migration's (CONTRACT v3 counted
zero in pilot). The downgrade recreates the table exactly, with RLS FORCE, the
guarded policy and the grant. **SUPERSEDES** the 2026-09-18 line "the
`verification_requests` TABLE survives unread" (vivekium C8) and the S4
default for this one table.

## SUPERSESSIONS TO MARK IN PLACE

- 2026-09-12 "Conversations": the candidate thread list's `inbound` count is
  replaced by `unread`; candidate threads with no message are not listed.
- 2026-09-06 "Corporate senders": "Only an ACTIVE sender sends" now also covers
  the default: automatic emails carry the tenant default.
- 2026-09-18 C8: the table no longer "survives unread"; see above.
- General rule 5 ("the authenticated Gmail mailbox is always the From"): the
  Reply-To of a human-sent candidate email is the thread's reply address when
  inbound mail is configured.
