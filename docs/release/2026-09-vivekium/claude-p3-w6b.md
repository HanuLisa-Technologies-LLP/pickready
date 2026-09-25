# CLAUDE.md section draft: Phase 3 WP6b (frontend proctoring and recording)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
Frontend only, no migration. Built against the WP4 proctoring schemas
(`PauseOut`, `candidate_rules`, `warnings/ack`) and the WP5 recording routes
(`session-media/start`, `recording/segments`, parts, `complete`, `finalize`)
as they stood on their branches on 2026-09-25.

## A LOST CAMERA OR MICROPHONE PAUSES THE ASSESSMENT, AND THE BROWSER DECIDES NONE OF IT

**SUPERSEDES the 2026-09-02 proctoring rule that camera and microphone loss is
Path A (immediate termination).** A loss is now Path P: a pause, an explicit
screen, a two-minute grace, at most two pauses per session, and a third loss
or an expired grace ends the session as a technical failure. Every one of those
numbers is the SERVER's.

- **`lib/proctoring/device-watch.ts` is the only place a loss becomes an
  event.** A revoked permission is reported at once
  (`CAMERA_PERMISSION_LOST`, `MIC_PERMISSION_LOST`). A failed stream gets
  `device_glitch_seconds` to come back on its own: back in time it is logged as
  `CAMERA_STREAM_INTERRUPTED` / `MIC_STREAM_INTERRUPTED` and costs no pause;
  still gone, it is reported as `CAMERA_STREAM_FAILED` / `MIC_STREAM_FAILED`
  with its duration. `DEVICE_RECOVERED` is sent ONCE, when every device lost in
  the episode is live again, never for a camera that is back while the
  microphone is still gone.
- **The monitors stay alive through an outage.** They retry a failed stream
  every `DEVICE_RETRY_MS` and wait for the candidate on a revoked permission
  (a timer that re-prompted every second would put a browser dialog in front
  of them every second); "Try again" on the pause screen reacquires inside the
  candidate's click.
- **The pause screen renders the server's `pause` state and nothing else.**
  `IngestOut.pause`, `HeartbeatOut.pause` and `SessionOut.pause`: `paused`,
  `grace_deadline_at`, `pauses_used`, `max_pauses` and `message`. The
  explanation is the server's sentence (`phrasing.pause_message`), verbatim;
  the screen words no allowance of its own. The countdown is the server's
  deadline placed on this device's clock with the offset the heartbeat
  measures (midpoint of request and answer), so a wrong device clock still
  shows the true time left. The browser adds only what it alone can see: which
  device it saw go, in the moment before the server has answered, and that the
  devices are back before the server has lifted the pause.
- **A reload into an open pause lifts it at once.** `SessionOut.pause` is the
  first statement the new page gets, with the devices live (the system check
  just passed), so the runtime sends one `DEVICE_RECOVERED` rather than
  leaving the candidate on a pause nobody can lift until the grace ends it.
- **The integrity episode does not count a device the pause owns.** Its
  sixty-second rule would otherwise raise `INTEGRITY_CHECK_FAILED`, a Path A
  termination, one minute into a two-minute grace. The HEARTBEAT still reports
  the device down, because it is down.
- **The detectors forget the outage** (`DetectionRules.resetPresence`): time
  without a camera is not time without a face, and a phone-detection run must
  not straddle two minutes of darkness.

## THE COUNTDOWN IS A CLOCK, SO IT HAS DIGITS

**SUPERSEDES the `time-guidance` rationale that "no digit reads as a clock"**
(PLAN-p3 decision 9). The pause countdown reads `m:ss`, rounded up so it never
shows `0:00` with time left, and reaching zero asks the server once
(`checkNow`) rather than concluding anything. Rule 1 bans numbers ABOUT the
candidate; every count on the monitoring screens (warnings used) stays spelled
out, and the pause overlay test asserts no digit anywhere but the clock.

## THE RULES ARE SERVED, NOT WRITTEN

`ConsentScreen` renders `GET /proctoring/config`'s `candidate_rules` verbatim
and in order; the seven hard-coded `CONSENT_POINTS` are deleted. While the
rules load the agreement is not offered, and when they cannot be loaded the
screen says so and offers a retry: agreeing to rules nobody was shown is not
agreement, and there is no fallback text. The shell's screen is the ONE place
the rules are shown (WP6a's consent screen carries the consent terms only).

## THE WARNING'S HOLD ON THE CLOCK IS THE SERVER'S TO MEASURE

**SUPERSEDES the 2026-09-02 `usePausedTime` stopwatch and `paused_ms`.**
Acknowledging a warning posts `POST /proctoring/sessions/{id}/warnings/ack`;
the server holds the turn clock from issuance to that call, capped on its side.
A pause length the client reports is a number the client chose, and it lived
in a React ref that a reload lost. The bridge's `paused` flag tells the player
to freeze its display and refuse input while a warning or the device pause is
up; it is never sent anywhere.

## THE RECORDING LEAVES THE TAB AS IT IS MADE

**SUPERSEDES the whole-session blob posted once at the end** (PLAN-p3 N2: two
hours of video in browser memory, and a gibibyte read into one API request).

- **One segment is one server-side multipart upload.** `MediaRecorder` hands a
  blob every `TIMESLICE_MS`; blobs collect to the server's `part_min_bytes`
  and each full part is PUT at once, never above `part_max_bytes`. The tab
  holds about one part. Calls are strictly serial, so the server sees parts and
  segments in recorded order.
- **The last part of a segment is sent `?final=true`, and only it.** It is the
  one part allowed under the object store's 5 MiB floor; the server refuses a
  short unmarked part up front because S3 would otherwise refuse the whole
  segment at completion, after the session is over. A segment whose tail
  already left as a full part sends no marked part at all.
- **A new segment after every device recovery**, on the new tracks, once BOTH
  devices are back. A recorder is bound to the tracks it was built on.
- **The encoder is bounded by the server's numbers** (`video_bits_per_second`,
  `audio_bits_per_second`, `max_width`, `max_height` from
  `session-media/start`); the size cap applies to a CLONE of the camera track
  so the detectors keep the resolution the system check measured. A response
  missing any of them is refused rather than defaulted.
- **A recording failure never ends an assessment.** Transport failures retry
  with backoff for about two minutes; a refusal (409, any other 4xx), exhausted
  retries or a backlog past `MAX_PENDING_PARTS` stops the RECORDING, reports
  why, and leaves the candidate answering. Completed segments are still
  finalized; WP5's hourly sweep completes a segment a closed tab left open.

## PASTE IS NAMED AS PASTE

A clipboard READ through the Clipboard API reports `action: "paste"` and a
write reports `copy`, both with `via: "clipboard_api"`, so the report's
itemised paste line counts every route to the act. The lockdown's document
listeners run in the capture phase ahead of every field (a textarea, a
contenteditable and a code editor's hidden input alike), and an attempt the
document caught is ALSO counted against the answer on screen
(`BehaviourCapture.recordBlocked`), which the answer's behaviour record read as
zero before. One attempt is one event and one count whichever layer caught it.

## THE AUDIO CHUNK CARRIES THE AUDIO AND NOTHING ELSE

Speech during a question that takes no spoken answer is decided by the server
against its own voice-answer stamps over a two-chunk window. The browser sends
no capture window: a time this device measured must not decide it.

## OLD VIDEO-INTERVIEW RECORDINGS SAY WHAT THEY ARE

The recruiter's video section no longer shows an "Assessment mode" row (every
assessment is the same proctored session). A recording whose stored mode is
`video_interview` carries one sentence saying it was taken in an earlier
format, because a recording laid out unlike every other one with nothing
explaining it reads as broken. `assessment-video-section.tsx` compares that
stored value read-only; the video-interview removal sweep must allowlist it the
way it allowlists `models/dual_mode.py`.
