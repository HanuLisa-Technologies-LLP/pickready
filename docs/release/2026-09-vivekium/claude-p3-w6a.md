# CLAUDE.md section draft: Phase 3 WP6a (the candidate's assessment screens)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
Frontend only, no migration. Built against the PLAN-p3 section 3.4 to 3.9
shapes as the conversation package (WP3) serves them.

## ONE MODE, ONE CONSENT SCREEN, THEN THE RULES, THEN THE CHECK

The page is `consent -> ProctoringShell(rules -> system check -> active) ->
AssessmentConversation`. `mode-selection.tsx`, `video-interview.tsx` and
`lib/assessment/time-guidance.ts` are DELETED, with the `AssessmentMode`,
`AssessmentModeState`, `VideoInterviewQuestion` and `VideoInterviewStart`
types and the invitation page's `recent_prior_report` branch.
`StoredAssessmentMode` (a plain string) survives only on the recruiter-side
payloads that describe an old recording.

- **The consent screen carries the consent and nothing else.** One server text
  for everyone (`GET /conversations/links/{id}/consent`), the per-item ticks,
  and a line saying the rules come next. **The rules are shown ONCE**, by the
  proctoring shell's consent screen, from the server's `candidate_rules`,
  before the system check. Rendering them on both screens put the same
  sentences in front of a candidate twice, one click apart, with two
  agreements to them.
- **`components/assessment/single-mode-surface.test.ts` sweeps the candidate's
  assessment surface** (whitespace-normalised, offsets mapped back to lines)
  for every retired name and asserts the three deleted files stay deleted.

## THE CLOCK IS THE SERVER'S, AND THE SCREEN IS ONLY ITS FACE

- **SUPERSEDES the "no digit reads as a clock" rule of `time-guidance.ts`.**
  The countdown is `m:ss`. It is a deadline, not an assessment number, and it
  is enforced now, so it must be readable at a glance. A digit that scores or
  ranks a candidate is still banned.
- **The face reads the server's two instants.** `remainingMs(deadline_at,
  server_now, receivedAt, at)`: the browser's clock contributes only the time
  since the response arrived, so a wrong laptop clock counts down correctly.
- **A pause freezes the face where it stood** (`readingInstant`), never at the
  time left on receipt. Reading `deadline_at - server_now` during a pause that
  began mid-countdown handed the candidate back time the server never gave.
- **Zero submits nothing by itself.** `expire` re-reads the server first; a
  deadline a pause moved simply takes over. Only a turn the server agrees is
  out of time is submitted, `timed_out: true`, empty if empty. An expiry that
  cannot reach the server retries a bounded number of times and then SAYS so
  (`EXPIRY_UNREACHABLE`), promising only what the server does: it submits the
  last draft it received.
- **Nothing the browser measures is sent as time.** `paused_ms`,
  `consumePausedMs` and `usePausedTime` are gone from the contract; the server
  refuses the field with a 422.

## FIXED ORDER: THE SERVER NAMES THE TURN

- **Every respond, draft and voice call carries `turn_seq`.** A 409 means the
  turn is not current (answered, expired, paused): nothing was written, the
  toast shows the server's own sentence, a typed answer goes back into the
  field, and the screen re-reads the server rather than retrying into the next
  question.
- **Drafts and behaviour captures are keyed by `conversation:turn:seq`**
  (`turnKeyFor`), so a re-ask is a fresh draft and a fresh capture.
- **Past answers are the server's `history`, read-only.** There is no edit
  control because there is no edit route (`EditableAnswerBubble` and the
  `PATCH /answers` call are deleted).
- **The draft goes to the server** (`PUT /conversations/{id}/draft`, at most
  every five seconds, always the latest value) because the server submits it
  on expiry; the local copy only puts words back after a reload. "Draft saved"
  means the SERVER holds it.

## SPOKEN ANSWERS: THE TRANSCRIPT IS FINAL

- The microphone control renders only when `voice_input_available` is true.
  The capture is opened BEFORE the server row, so a refused microphone creates
  nothing to clean up. The recorder stops itself at the server's
  `max_seconds` and records at 64 kbit/s so the longest answer fits the upload
  ceiling in every container.
- The transcript is shown read-only and submitted by its id; nothing edits it.
- **Two failures, and only one holds the clock.** A server-side transcription
  failure keeps the server's pause open until the candidate acknowledges it
  (the server's `message` is shown); a failure that never reached the server
  (no microphone, refused upload) opened no pause and goes straight to typing.

## PASTE IS REFUSED IN EVERY ANSWER FIELD, AND NAMED

`fieldEventProps` refuses copy, cut, paste and drop and calls
`onBlockedAction(kind)`; the proctoring runtime turns that into one
`BLOCKED_ACTION_ATTEMPTED` event carrying the kind, so the report can say
"paste". `kind` is optional on the contract only for the legacy code editor
Phase 4 replaces.
