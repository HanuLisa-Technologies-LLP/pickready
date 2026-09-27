# CLAUDE.md section draft, package s4-audit-close (2026-09-28)

For the orchestrator to fold into the Vivekium release section. No older rule
is superseded; two are extended.

## What changed

- **Signing in as the invited user accepts the invitation.**
  `services/staff_invites.accept_pending_invite` is called from both
  session-issuing paths (`api/auth._finalize_single`,
  `login_context.select_context`). It touches only a PENDING row (never an
  accepted, revoked or expired one) and writes `staff_invite_accepted` in one
  insert. The /join handler admits exactly one non-pending state, an
  invitation already accepted by the same signed-in user, because the page
  signs in BEFORE it posts the acceptance; anybody else still gets the 410.
  Ported from PR #5, which as written would have made /join report its own
  link as spent.
- **Saving your own permission row refreshes your own tab**
  (`permission-matrix-modal.tsx`), and the manager is told a colleague sees
  a change within a minute, on their next page (the navigation revalidation
  in `lib/auth-context` is throttled to once a minute).
- **A server-derived audio event names its question.** The conversation
  engine exposes `turns.current_question_id` (the open turn's row; a follow-up
  or re-ask names its parent; None with no open turn); the audio ROUTE
  resolves it and hands it to `proctoring.audio.analyse_chunk`, so the
  proctoring package still reads no question-writing code. A speech run keeps
  the question it started on.
- `docs/operations/BRANCH_PROTECTION.md`: the exact protection `main` needs
  and the command. NOT applied.
- `docs/release/2026-09-vivekium/AUDIT_CLOSEOUT.md`: every audit item closed,
  resolved by deletion, or open with its reason.

## New hard rules, and why

- **Two trackers of one fact need a wire, or they drift for ever.**
  `users.status` and `staff_invites.accepted_at` both answer "did this person
  join?", and only one door wrote the second. When a fact has two columns,
  every writer of one writes the other in the same transaction.
- **A fix that makes an earlier step succeed can make the later step fail.**
  Accepting at sign-in turned the /join page's own acceptance post into a
  410. Follow the flow a user actually walks before calling a port done.
- **A required status check must run on a pull request.** A job with
  `if: github.event_name != 'pull_request'` can never be required on `main`:
  it would block every merge for ever. Renaming a CI job renames the required
  check; change both together (`BRANCH_PROTECTION.md`).
- **"Kept because it is wired" must be checked against the imports.**
  `services/coalescing` was recorded as KEPT because the grading phase wires
  it; only its exception type is imported. Recorded as open in the close-out.

## Extended (not superseded)

- 2026-09-25, "PROCTORING ... Speaking during a question that takes no spoken
  answer is logged": the event now carries `question_id`, and so does the
  second-voice event.
- 2026-09-12 / 2026-07-28, staff invitations: acceptance is also the invitee's
  first proven sign-in, not only the emailed link.
