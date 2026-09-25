## Current hard rules, the device pause and the audio rules (2026-09-25)

Draft for the orchestrator to assemble into CLAUDE.md. PLAN-p3 WP4
(proctoring). Migration `0125_proctoring_pause` (re-chained at integration).
Master prompt, Phase 3, Proctoring and Paste.

### A LOST CAMERA OR MICROPHONE PAUSES THE ASSESSMENT. IT NO LONGER ENDS IT

**SUPERSEDES the 2026-09-02 catalog rule that `CAMERA_PERMISSION_LOST`,
`MIC_PERMISSION_LOST` and `CAMERA_STREAM_FAILED` were Path A (immediate
termination), and `proctoring_camera_recovery_seconds`, which turned a camera
that stayed down into one.** A fourth consequence path, `P`, sits beside A, B
and C in `services/proctoring/catalog.py`, and those three plus the new
`MIC_STREAM_FAILED` are on it.

- **The rule the candidate is read before starting.** A loss pauses the
  assessment and its clock; the candidate has `proctoring_device_grace_seconds`
  (120) to restore the device; at most `proctoring_device_max_pauses` (2)
  pauses per session. The next loss after that
  (`DEVICE_PAUSE_LIMIT_EXCEEDED`), or a pause not recovered inside its grace
  (`DEVICE_RECOVERY_TIMED_OUT`), ends the session. Both are server-derived
  Path A reasons and both are ALWAYS `technical_failure`: a camera that died
  and a camera switched off end the same way, and the outcome sentence refuses
  to vouch for which it was.
- **A loss shorter than `proctoring_device_glitch_seconds` (5) pauses
  nothing.** It is stored as `CAMERA_STREAM_INTERRUPTED` or
  `MIC_STREAM_INTERRUPTED` on Path C. Downgrade only, never upgrade: a browser
  cannot earn a graver path by sending a longer number.
- **THE GRACE BOUNDARY IS INCLUSIVE.** A recovery received exactly at the end
  of the grace resumes; the session ends only once the grace is EXCEEDED.
  Ties go to the candidate, the same direction as rule 8.
- **THE STATE IS THE PAUSE ROW, NOT A FLAG.** PLAN-p3 3.10 sketched
  `proctoring_sessions.device_pauses_used` and `device_paused_at`. They were
  deliberately NOT added: "paused" is a `device_loss` row in
  `assessment_pauses` with no `ended_at`, and the pauses used are the COUNT of
  such rows. A flag beside the row is two records of one fact, and the day
  they disagree a candidate is locked out of a running assessment or answering
  a paused one. Concurrency is a ROW LOCK on the proctoring session
  (`device_pause.lock`), so a batch carrying a loss and one carrying a
  recovery are decided one after the other.
- **Times are the server's.** A pause opens when the server receives the loss
  and closes when it receives the recovery. A loss the browser reports only
  after it ended is dated back by its measured duration, never by more than
  the grace, and judged at the server's `now`.
- **An expired pause is settled on the NEXT thing that arrives**: every event
  batch, every heartbeat (`ingestion.enforce_pause`), and the hourly
  `reconcile_proctoring_sessions` for a tab that never came back. It RETURNS
  the termination rather than raising it, so the ending commits; a raise would
  roll it back and the next request would end it again.
- **A heartbeat GAP is recorded, never a termination.** PLAN-p3 3.0 item 7
  proposed ending a session whose heartbeats stopped for longer than the
  grace. Not done: from the server a candidate's dead network, this platform's
  own outage and a deploy look identical, and ending every in-flight
  assessment because the API was unreachable for three minutes is exactly the
  unfair termination the plan's own risk list forbids. The gap is stated in
  the report; a device the browser SAW stop is paused and timed out on its own
  evidence.
- **While paused, no answer is taken.** `gate.require_answerable` refuses with
  `PAUSED_DETAIL` (409) whenever a `device_loss` pause is unclosed, including
  one past its grace (an answer taken then is an answer given with the camera
  off). The START route shows the pause (`SessionOut.pause`) rather than an
  error, so a reload mid-pause lands on the pause screen.
- **The grace and the pause allowance are server STATE, not client config.**
  The browser holds a loss back for `device_glitch_seconds` and decides
  nothing else; every ingest and heartbeat response carries
  `pause: {paused, message, grace_deadline_at, pauses_used, max_pauses}`,
  the deadline on the server's clock and the message written by
  `phrasing.pause_message`. Two counts are operational figures like the
  warning counter, never an assessment number.

### ONE PAUSE RECORD FOR EVERY REASON THE CLOCK STOPS

`assessment_pauses` (migration 0125) and
`services/assessment_conversation/pauses.py` are the ONE implementation (rule
5). Three reasons, three owners: `device_loss` (proctoring), `warning`
(proctoring, a blocking warning on screen), `transcription` (the voice answer
path). The turn timer reads the union of all three; an overlap counts once.

- **`expires_at` is a CAP, not a deadline somebody must enforce.** A warning
  nobody acknowledged stops the clock for at most
  `assessment_warning_pause_max_seconds` (30), a device loss for at most the
  grace, whether or not a request ever comes back to close it. Every reader
  applies the cap through one helper.
- **A close may be SCHEDULED.** `close_pause` with a future `at` stamps an end
  that has not happened yet (a failed transcription holds the clock for a
  short window); a later close at an earlier instant brings it FORWARD and
  never pushes an end later.
- **Idempotent per reason and reference.** A retried open returns the same
  row; an open by a DIFFERENT reference supersedes the open row at `at` (the
  clock loses nothing, the new pause has its own cap). The partial UNIQUE
  index `uq_assessment_pauses_open` (one unclosed row per reason) is the
  guarantee, not the service's check.
- **The module imports nothing from proctoring**, pinned by an AST test. The
  conversation engine times turns with it, and a module that times turns must
  not acquire the proctoring package (`test_proctoring_scoring_isolation.py`).
- **The warning pause.** A Path B warning that is not the end of the session
  opens a `warning` pause; `POST /proctoring/sessions/{id}/warnings/ack`
  closes it. This replaces the client-reported `paused_ms`, a duration the
  client chose.

### A SECOND VOICE IS FLAGGED ONLY WHEN IT IS STRONG

The analysis service's `/diarize` answers `speaker_seconds` beside
`speaker_count` and `speech_seconds`: each speaker's own total
(`label_duration`, so a voice that only spoke over the candidate still counts
in full), longest first, one entry per speaker. A chunk counts toward
`SECOND_VOICE_DETECTED` only when the SECOND entry is at least
`proctoring_second_voice_min_seconds` (3.0). The longest speaker is taken to be
the candidate and is never measured; one speaker is never a second voice. The
two-consecutive-chunks rule is unchanged. **An answer without the per-speaker
list is a BAD answer, not "one speaker"** (`audio.parse_analysis` raises and
the gap is noted once), because guessing would either flag the candidate's own
voice or hear nobody.

### SPEAKING DURING A QUESTION THAT TAKES NO SPOKEN ANSWER IS LOGGED, NEVER PUNISHED

Speech of at least `proctoring_speech_min_seconds` (2.0) in a chunk while no
spoken answer is being captured (`audio.voice_capture_open` reads
`voice_answers`: a capture `recording` inside its maximum length, or uploaded
within the last two chunk lengths) is `SPEECH_DURING_NON_AUDIO_QUESTION`, Path
C. ONE event per RUN of consecutive speaking chunks, whose duration grows with
the run, so a minute of reading aloud is one occurrence of about a minute. It
is never a warning and never an ending, under any job policy. The report lists
it under Audio Monitoring every time and lifts it into the summary from
`proctoring_speech_highlight_threshold` (3) occurrences; the sentence itself
says it never ends an assessment.

### EVERY BLOCKED PASTE IS IN THE REPORT, ON ITS OWN LINE

`BLOCKED_ACTION_ATTEMPTED` carries the refused action in `metadata.action`.
Every attempt is its own Path C row and activity-log entry. After the one
sentence that says what is blocked, the report itemises paste, copy or cut,
drag and drop, and everything else, in that fixed order, so a paste is never
hidden inside a total. An unknown or missing action is counted under "another
blocked action", never dropped.

### THE PROCTORING REPORT HONOURS JOB CLOSURE

`GET /proctoring/links/{id}/report` calls `job_assessment_retention.
require_readable` after the tenant check and BEFORE the report is loaded, so a
closed job answers 410 exactly as the PRISM Report, the transcript and the
recording do (audit P1 3.22). Pinned by `tests/test_proctoring_report_closure.py`
over the real handler, mutation-checked.

### THE CANDIDATE IS READ THE RULES THE SERVER ENFORCES

`GET /proctoring/config` serves `candidate_rules`, composed by
`phrasing.candidate_rules(config)` from the SAME numbers the pipeline applies:
the grace, the pause allowance, the warning count, the audio rules (only when
audio analysis is available), paste blocking, fixed order and server-timed
questions. Change a setting and the sentence changes with it; the consent
screen words nothing itself.

### Smaller rules that came with it

- **The candidate is resolved through `services/candidate_identity`** in
  `api/proctoring.py`; the local `Candidate.user_id` lookup is gone and the
  `LEGACY_RESOLVERS` entry with it.
- **A termination orders the PRISM Report AFTER COMMIT**
  (`ingestion.enqueue_after_commit`, `dispatch_after_commit`); the
  `enqueue_assessment` entry left the dispatch sweep's allowlist.
- **Boot refuses a meaningless rule**: a grace not longer than the glitch
  window, a negative pause cap, a non-positive audio threshold or warning cap.
- **The migration downgrade REFUSES** while any event sits on the pause path:
  rewriting a stored event's path would change what the report says happened.
