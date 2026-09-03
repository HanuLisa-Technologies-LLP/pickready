# Proctoring

Status: implemented 2026-09-02, per `proctoring-spec-doc.md` v1.0 (owner: Manju,
Hanulisa Technologies LLP). This file records how the specification landed in
this codebase and where each rule is enforced. The specification's seven
product principles are locked; this document does not soften any of them.

## The seven principles, and where each one is enforced

| # | Principle | Enforced by |
|---|---|---|
| P1 | No video is ever stored | The browser infers over frames in a Web Worker and discards them; the worker posts detections only. `backend/tests/test_proctoring_no_media.py` fails the build if a write path for a frame, an image or an audio buffer appears in the proctoring module. The analysis service decodes audio from bytes in memory and deletes the buffer; its own test makes `tempfile` and file writes raise during a request. |
| P2 | No human review during a live session | There is no live proctor route, no review queue and no mid-session write path for staff. Decisions are made by `services/proctoring/ingestion.py` in the request that carries the event. |
| P3 | Proctoring never affects any score or ranking | Nothing under `services/proctoring/` is imported by any scorer, by the Tatva matrix, by Miti, by Siddhi, by the dashboard or by any ranking query. `backend/tests/test_proctoring_scoring_isolation.py` asserts the import graph. The report is appended to the PRISM Report as its final, informational section and read nowhere else. |
| P4 | Proctoring is mandatory | `services/proctoring/gate.require_active` runs first in both `start_conversation` and `respond`. There is no enable flag and no role bypass. The earlier optional screen-capture consent component was removed because it contradicted this principle and P1. |
| P5 | The candidate is always informed | The consent screen (spec 8.1 content, verbatim) requires an explicit "I understand and agree" and the timestamp lands on `proctoring_sessions.consented_at`, which is NOT NULL. |
| P6 | Recruiter-facing language has zero internal terminology | `services/proctoring/phrasing.py` is a lookup table; `tests/test_proctoring_report.py` sweeps every generated sentence for the forbidden words. |
| P7 | Never state or imply certainty of cheating | The phrasing library describes what was detected, never what it means, and adds context sentences where a finding could be misread. |

## Architecture as built

```
browser (all inference here)
  webcam -> Web Worker: COCO-SSD (objects), MediaPipe Face Landmarker (faces,
           count, landmarks kept in a forward-compatible shape for a future
           gaze module), face-api.js (128-d identity descriptor)
  mic    -> energy VAD -> 15-second chunks, uploaded only when speech was
           present and only when the deployment has an analysis service
  lockdown (fullscreen, blocked keys and events, focus tracking)
  behavioural capture (keystroke and pointer TIMINGS, never characters)
  -> POST /api/v2/proctoring/sessions/{id}/events   (batched, JSON, no media)
  -> POST /api/v2/proctoring/sessions/{id}/heartbeat
  -> POST /api/v2/proctoring/sessions/{id}/audio     (chunk, in memory only)

backend
  services/proctoring/ingestion.py  classifies, debounces, counts warnings in
                                    Redis (authoritative), terminates
  services/proctoring/behaviour.py  evaluates typing against the candidate's
                                    own baseline at submission
  services/proctoring/audio.py      hands the chunk to the analysis service
                                    and destroys it
  services/proctoring/report.py     generates the report once, after the
                                    session ends; joins it onto the PRISM
                                    payload and the PDF as the last section

analysis-service (separate ECS service, CPU)
  POST /diarize   pyannote/speaker-diarization-3.1, speaker count only
  POST /ai-text   roberta-base-openai-detector, behind a flag, informational
```

## Every threshold lives in one place

`backend/app/core/config.py` holds every `proctoring_*` setting;
`services/proctoring/config.py` reads them once into a frozen object and
serves the browser-side subset (`CLIENT_FIELDS`) on the session response, so
the client and the server work from the same figures. No module in the
pipeline carries a literal. The specification's defaults are the settings'
defaults.

## Consequence paths (spec section 4)

`services/proctoring/catalog.py` is the vocabulary. Every event type carries
its path (A immediate termination, B the shared three-warning counter, C
logged only), its report group, whether a client may emit it, and its cooldown
or once-per-session rule. The counter is SHARED across every Path B type,
deliberately. Four identifiers were added to the specification's catalog for
rules it states in prose without naming an event: `MONITORING_INTERRUPTED`,
`INTEGRITY_CHECK_FAILED`, `IDENTITY_CHECK_MISMATCH` and `CAMERA_STREAM_FAILED`.

The server decides. The browser reports a mismatch; the server counts the
second consecutive one. The browser reports a focus loss; the server ignores
one under two seconds. Cooldowns are held in Redis so a phone on the desk
cannot burn three warnings in six seconds. Redis being unreachable answers
503 rather than silently issuing no warning.

## The recruiter's one setting (spec section 6)

`jobs.proctoring_warning_policy`, `terminate` or `continue_and_note`, default
`continue_and_note`. Set on the job page's setup review (the "Assessment
monitoring" card, with the specification's exact label, help text and two
options) and applied to every candidate on the job. It moves no score.

## The report (spec section 7)

Generated once by `pickready.generate_proctoring_report` after the PRISM
report is written (completion or termination) or by the hourly
`pickready.reconcile_proctoring_sessions` sweep for abandoned sessions.
Stored on `proctoring_reports.report_content` as WORDS: counts are spelled
out, durations are approximate ("about half a minute"), and the only digits
are clock times in the date line and the activity log. That is what lets the
report travel inside `FunctionalReportOut` under the serialiser-level number
ban unchanged. The section order of the PRISM Report is now eight entries,
with `proctoring` last, written once per renderer and pinned by
`tests/test_prism_report.py` as before.

## Retention

The platform has no time-based candidate-data purge; personal data is deleted
by cascade with the candidate or the application, and the proctoring tables
cascade the same way. `proctoring_event_retention_days` is 0 by default,
meaning "the platform's policy"; a positive value enables the hourly
`pickready.purge_proctoring_events`. Choosing a number is an owner decision
and this implementation does not invent one.

## What is honest about the limits

Detection runs on the candidate's machine. The heartbeat, the integrity
self-check, server-side counting and a production-minified bundle raise the
effort for a determined technical candidate; they do not stop one, and the
code comments say so. A monitoring gap is reported as a gap, never as a clean
period. Audio analysis that is not configured is reported as unavailable.

## Explicitly not built (spec section 13)

Gaze tracking (landmark data is captured in a shape a future module can read),
AI-overlay detection, screen-mirroring detection, remote-desktop detection,
ID-document verification, session replay, a human review queue.
