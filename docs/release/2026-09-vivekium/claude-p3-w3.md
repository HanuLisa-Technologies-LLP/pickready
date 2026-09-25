## Current hard rules, the conversation engine, the turn clock and spoken answers (2026-09-25)

Draft for the orchestrator to assemble into CLAUDE.md. PLAN-p3 WP3.
Migration `0123_assessment_conversation` (re-chained at integration). Master
prompt Phase 3 and Appendix B sections 1 to 4.

The candidate's side of the assessment is no longer a six hundred line route
handler. `api/assessment_conversation.py` resolves who is asking and which turn
they name; `services/assessment_conversation/turns.py` is the ENGINE the route
and the expiry path share; `timers.py` is the clock as pure arithmetic.

### THE SERVER KEEPS THE TIME, AND EVERY ANSWER NAMES ITS TURN

**SUPERSEDES the 2026-09-02 rule "per-question time is measured by the server
... less the bounded time a blocking warning held the screen"**, which in
practice subtracted `paused_ms`, a number the CLIENT sent. There is no client
pause any more: `ConversationMessageIn` is `extra="forbid"`, so an old client
still sending `paused_ms` gets a 422 rather than a number quietly ignored.

- **A TURN is every prompt the candidate is shown**: a base question, a
  follow-up or a re-ask. Opening one increments `turn_seq`, stamps
  `prompt_shown_at` ONCE (a reload returns the same turn and the same clock; it
  used to re-stamp and hand out a fresh question clock) and SNAPSHOTS the
  allocation (Appendix B: prose 180 s, multiple choice and fill-in-the-blank
  60 s, a follow-up or re-ask 100 s, coding 1200 s). A settings change never
  moves the deadline of a question somebody is answering.
- **The deadline is computed from rows the server wrote**: `prompt_shown_at`,
  the snapshot, and `assessment_pauses` (device loss, transcription, warning).
  Overlapping pauses count once. While any pause is open the turn cannot
  expire. The grace (`assessment_submit_grace_seconds`, 5) is INCLUSIVE: an
  answer arriving exactly at deadline plus grace is the candidate's own.
- **Every answer names `turn_seq`. Any other number is a 409 with nothing
  written.** A double click, a replay and a retry after a lost response can
  never again be filed as the answer to the NEXT question.
- **An answer that arrives after deadline plus grace is not the candidate's.**
  The server submits what it holds: a transcribed spoken answer, else the
  draft saved in time (`PUT /conversations/{id}/draft`, refused once the turn
  expired), else nothing. Nothing is an EVIDENCE GAP: the transcript line is
  the product's own sentence, `answer_label = timed_out`, `evidence_gap =
  true`, never re-asked and never probed. The same submission runs lazily when
  the candidate comes back to a turn that expired while they were away.
- **FIXED ORDER, NO SKIPPING, NO EDITING.** Only the current turn is
  answerable. `PATCH /conversations/{id}/answers/{message_id}` is DELETED;
  every response carries `history`, the past exchanges, read-only.
- **A clock is not a score.** The response carries `allocation_seconds`,
  `deadline_at` and `server_now` so the countdown is the server's. That is an
  operational time, not an assessment number; it supersedes the "no digit
  reads as a clock" rationale of the retired `time-guidance.ts`.

### EVERY ITEM IS ASKED, SO THERE IS ONE WAY TO FINISH

**SUPERSEDES the 2026-08-23 early close** (`conversation_may_close`, "ends when
the question-count range AND evidence sufficiency are both satisfied") and
the bounded EXTENSION past the plan. A conversation ends when its written
questions are exhausted, and a new row's `end_reason` is always
`prompts_exhausted`. The other five words stay in the CHECK and in
`interviewer.STOP_CONDITIONS` because rows written before today carry them.
`interviewer.conversation_state` survives as the operator's log line and
decides nothing.

### THE START: CREDIT, CONTRACT, DIGEST, IN ONE TRANSACTION

- **The FIRST start re-checks credit** (`credits.can_start_assessment`, one
  report). An invitation is not a reservation. The candidate reads a sentence
  that names no billing term; the employer sees the 402 on their own screens.
  A running conversation always finishes.
- **The contract is locked in the transaction that stamps `started_at`**
  (`assessment_contract.lock_contract`, under the skills advisory lock, so
  the comparison and the lock cannot be split by an edit), and Vaada's half of
  the digest pair is logged (`stage=vaada`).
- **Questions written against another contract are rewritten BEFORE the first
  answer.** `assessment_conversations.questions_contract_digest` is stamped by
  the generator; a mismatch (NULL included) deletes the unasked questions and
  returns `preparing`. Only a STAMPED mismatch re-dispatches at once (the
  skills really changed); an UNSTAMPED set waits out
  `assessment_question_redispatch_seconds`, because a generator that stopped
  stamping would otherwise start a Fargate task on every poll.
- **Missing questions are `preparing`, dispatched AFTER the commit.** The old
  start dispatched and then RAISED, so the transaction rolled back after the
  task was sent.

### VAADA WRITES FROM THE BOUND CONTRACT, AND PERSISTS ONLY WHAT IS SHOWN

- **`services/vaada_context.build`** reads the snapshot bound to THIS
  conversation (`load_contract_for_conversation`): the skill, its hidden
  evidence line and the hidden role summary. Never a live row. `passages` is
  Phase 5's to fill through the tool layer.
- **The repeat check and the outbound guard are criteria of
  `ppi_interview.write_question`'s own loop** (audit P1 3.10). Until today the
  caller rejected a repeat AFTER the writer had persisted the new prompt and
  rubric, then showed the old text: the candidate read one question and was
  graded against another's rubric. A result the loop cannot make acceptable is
  DEGRADED and writes nothing, so the text on screen, the stored prompt and the
  stored rubric always belong to one question.
- **`conflicting` finally reaches the writer**, and the ledger it reads is
  written PER ANSWER while the conversation runs
  (`assessment_pipeline.evidence.record_answer_evidence`, the one writer). The
  Miti to Vaada loop was dead twice over: the ledger was written only at
  scoring, and the caller never passed the flag.
- **DELETED, not flagged off** (SUPERSEDES the 2026-08-05 `MODE_GENERATE` /
  `MODE_REWORD` rule and its `_substance_preserved` check):
  `compose_next_question`, both modes, the deliver graph, both extension
  ceilings, `interview_write_question.txt` and `interview_deliver_question.txt`.
  `challenge_prompt` and `interview_challenge.txt` STAY: they are live through
  `challenge_non_answer`. `tests/test_dead_interviewer_modes_removed.py`
  sweeps the tree, whitespace-normalised. `app/scripts/eval_interview.py`
  measures the live writer now.

### A SPOKEN ANSWER: TRANSCRIBED WITH THE CLOCK PAUSED, FINAL, AND ONLY TEXT KEPT

- **Prose turns only**, one capture in flight per turn (a partial UNIQUE
  index), offered only where `transcribe_enabled` is true. Disabled means the
  microphone is not rendered; it is never a fake transcript.
- **The audio is stored through the sanctioned media package**
  (`services/video/voice.py`, under `voice-answers/`), read with a ceiling one
  byte above `assessment_voice_max_bytes` and REFUSED above it, never
  truncated: a truncated recording is an answer the candidate did not finish.
- **Transcription is `pickready.transcribe_voice_answer` (Route.LAMBDA),
  dispatched after the commit.** The clock is paused by a transcription pause
  CAPPED at the Transcribe budget, so a lost task cannot stop a candidate's
  clock for ever; a row past the budget is failed on read and the candidate is
  told to type.
- **A failure is a STATE.** The clock stays stopped for
  `assessment_voice_failure_pause_seconds` (or until the candidate
  acknowledges), then they type. A transcript that arrives AFTER the row was
  failed does not replace the failure: the task re-reads its row locked.
- **The transcript is FINAL.** A transcribed recording for the turn IS the
  answer, whatever else the request carries; there is no edit route.
- **"Store transcript text only."** `assessment_conversation/voice_audio` is
  the one HEAD-confirmed deletion of the recording and Transcribe's output. An
  unconfirmed delete is COUNTED and retried by `repair_pending_audio` (alarm
  past `ATTEMPTS_BEFORE_ALARM`); `undeleted_audio_keys` is what a purge asks
  BEFORE the conversation cascade takes the rows that name the objects.

### ONE MODE, ONE CONSENT

**SUPERSEDES the 2026-09-06 dual-mode consent** ("the mode is chosen BEFORE
consent and FROZEN"). `GET/POST .../mode` are deleted; `GET/POST
.../consent` serve and record ONE text, `assessment_consent_text`, version
`2026-09-24`, stating the recording, its D4 deletion, the transcription with
only text kept, and the paste rule. `assessment_conversations.mode` stays
readable; new rows are `conversational`, migration 0123 moved every UNSTARTED
video interview row, and a started one is refused with a sentence rather than
continued in the wrong mode.

### RULES THIS ADDS

- **Never compute an answer's time from the client.** Every paused second is a
  row the server wrote.
- **Never write a question's prompt or rubric without the other**, and never
  persist a question the candidate will not read.
- **Candidate identity is `candidate_identity.resolve_candidate_id`** on every
  route here; nothing matches an email at request time.
- **Completion dispatches scoring and indexing AFTER the commit**
  (`dispatch_after_commit`); a rolled-back completion dispatches nothing.
