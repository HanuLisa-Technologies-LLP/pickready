# Stage 2 integration: deferred orchestrator hunks

Branch `wip/stage2-int`, built from `release/vivekium` (5bc4efb) by merging, in
order, `wip/p2-a`, `wip/p4-4b1`, `wip/p3-w1`, `wip/p3-w4`, `wip/p4-4d`,
`wip/p3-w6a`, `wip/p3-w6b`.

Every "HUNKS FOR ORCHESTRATOR" item in those seven package reports that
targets a package NOT merged here (Phase 2 WP-B to WP-F, Phase 3 WP2, WP3 and
WP5, Phase 4 WP-4B2, WP-4C and WP-4F, Phase 5) is recorded below VERBATIM from
its source report, with the source named. The hunks that WERE applied on this
branch are listed at the end for reference, so nothing is applied twice.

Source reports: `C:\dev\pickready\.claude\vivekium-release\reports-stage2\`.

---

## From p2-a.md (Phase 2 WP-A, Yukti core)

### p2-a hunk 3 (target: Phase 2 WP-B, `app/services/resume_parsing.py`)

> 3. `app/services/resume_parsing.py`, WP-B file: import `compensation_guard`; the extraction user message becomes `compensation_guard.redact_text(resume_text)[:24000]`.
>
>    With hunks 1 to 3 applied, the three strict xfails in `test_ctc_never_in_prompt.py` turn into XPASS failures, which is the intended signal. At the same time, delete those three `xfail` marks and change their `PROMPT_BUILDERS` entries from `PENDING` to `CANARY`. With the hunks applied, the resume-parsing, SWOT and Sutra test modules showed no other failures.

The exact diff (from the p2-a scratchpad `p2a/orchestrator_hunks.diff`):

```diff
diff --git a/backend/app/services/resume_parsing.py b/backend/app/services/resume_parsing.py
index ea14109..47ddf95 100644
--- a/backend/app/services/resume_parsing.py
+++ b/backend/app/services/resume_parsing.py
@@ -32,7 +32,7 @@ from typing import Any
 from sqlalchemy.ext.asyncio import AsyncSession
 
 from app.models import Profile
-from app.services import llm_router
+from app.services import compensation_guard, llm_router
 from app.services.embeddings import embed
 from app.services.projects import invisible_text
 from app.services.resume_storage import ResumeStorageError, fetch_resume_bytes, profile_has_resume
@@ -244,7 +244,7 @@ async def extract_structured_fields(
         "extraction",
         [
             {"role": "system", "content": _EXTRACTION_SYSTEM},
-            {"role": "user", "content": resume_text[:24000]},
+            {"role": "user", "content": compensation_guard.redact_text(resume_text)[:24000]},
         ],
         response_format_json=True,
         session=session,
```

State on this branch: hunks 1 (SWOT) and 2 (Sutra) are applied, so only ONE
strict xfail remains in `test_ctc_never_in_prompt.py`
(`test_resume_extraction_carries_no_compensation`). When this hunk lands,
delete that one `xfail` mark and move the
`app/services/resume_parsing.py::extract_structured_fields` entry from
`PENDING` to `CANARY`.

### p2-a hunk 5 (target: Phase 2 WP-F)

> 5. The `prescreen.anonymise` copy stays until WP-F deletes prescreen, or re-points it to `yukti.anonymise`, so there is one implementation.

---

## From p4-4b1.md (Phase 4 WP-4B1, coding answer key and generation)

### p4-4b1 hunk 1 (target: Phase 3 WP2, `app/services/assessment_formats/types.py`)

> 1. `backend/app/services/assessment_formats/types.py` (Phase 3 WP2 owns this file). `parse_payload`, `parse_answer` (CODING) and `candidate_view` (CODING) must dispatch on `coding_assessment.payload.is_v2(payload)`, adding `from app.services.coding_assessment import payload as coding_payload`.
>    - Without it, a v2 row fails the v1 `CodingPayload` model, so the candidate conversation would break on the first v2 coding question.
>    - The exact diff, plus a new test module `backend/tests/test_coding_payload_dispatch.py` (6 tests), is the committed patch `docs/release/2026-09-vivekium/hunks/p4-4b1-types-v2-dispatch.patch`.
>    - Apply with `git show 6dca415:docs/release/2026-09-vivekium/hunks/p4-4b1-types-v2-dispatch.patch | git apply` (this avoids CRLF from a Windows checkout).
>    - It must land with or before the composer that writes v2 rows.

The patch file is present on this branch at
`docs/release/2026-09-vivekium/hunks/p4-4b1-types-v2-dispatch.patch`.
Integration note: the frontend player on this branch already renders a v2
coding payload (p4-4d plus the applied conversation hunk), so nothing may
write a v2 row until this patch lands with the Phase 3 WP2 composer.

### p4-4b1 hunk 2 (target: Phase 3 WP2, `app/services/assessment_formats/generation.py`)

> 2. `backend/app/services/assessment_formats/generation.py` (Phase 3). This should be done only after the composer sends coding slots to `coding_generation.write_coding_question` / `persist_coding_question`:
>    - Remove `types.CODING: "assessment_format_coding"` from the prompt map (line 77).
>    - Remove the CODING rubric branch (394-395) and the `values["languages"]` branch (435-436).
>    - Make `write_structured` refuse CODING.
>    - Delete `app/prompts/assessment_format_coding.txt`.
>    - Remove that prompt from `tests/fixtures/prompt_snapshots.json` if it is listed.
>    - In `test_assessment_formats_generation.py`, replace `test_a_coding_question_keeps_its_expected_approach_off_the_prompt` with a test that CODING is refused.

### p4-4b1 hunk 3 (target: the Phase 3 WP2 composer wiring, `tests/test_ai_reachability.py`)

> 3. `tests/test_ai_reachability.py`: add `coding_generation.write_coding_question` and `persist_coding_question` to `REQUIRED_CALLERS` in the same change that wires the composer.

---

## From p3-w1.md (Phase 3 WP1, invitations and credits)

### p3-w1 hunk 1, the `assessment_completed` half (target: Phase 5, the report writer)

The source hunk, verbatim:

> - `backend/app/services/hiring_pipeline.py`: the "Move to" dropdown still offers `assessment_in_progress` and `assessment_completed` (seen in the API response for an invited application). A hand move claims a started session or a written report that do not exist. Suggested change: `MANUAL_TRANSITION_EXCLUDED = frozenset({SHORTLISTED, ASSESSMENT_IN_PROGRESS, ASSESSMENT_COMPLETED})`, plus a refusal of those two targets in `api/pipeline.change_status` (my file; can be done at integration). I did not change the owner-level manual set.

Applied here for `assessment_in_progress` only (see the end of this file).
DEFERRED for `assessment_completed`, with the reason: nothing in the product
writes `assessment_completed` automatically today (`grep` over `backend/app`
finds no `apply_transition(..., target=ASSESSMENT_COMPLETED)` and no SQL
writer). The FSM's only way from `assessment_in_progress` onward is
`assessment_completed`, so withdrawing the hand move now would strand every
assessed candidate before shortlisting. When Phase 5's report insert moves the
application to `assessment_completed` itself, add `ASSESSMENT_COMPLETED` to
`MANUAL_TRANSITION_EXCLUDED` and to `SYSTEM_ONLY_TARGETS` in
`services/hiring_pipeline.py`, widen `SYSTEM_ONLY_REFUSAL` to name both
stages, and extend
`tests/test_invite_batch_credits.py::test_a_hand_move_to_in_progress_is_refused_and_not_offered`.

---

## From p3-w4.md (Phase 3 WP4, device pause and audio rules)

### p3-w4 hunk 1 (target: Phase 3 WP3)

> 1. **WP3 duplicate table (must resolve before migrations chain).** wip/p3-w3 creates `assessment_pauses` in `0123_assessment_conversation.py`, and also defines `AssessmentPause`/`PAUSE_*` in `models/voice.py` plus its own `pauses.py`. Keep this branch's versions:
>    - Delete the `assessment_pauses` create/drop and its `_tenant_rls` call from 0123.
>    - Delete `AssessmentPause` and `PAUSE_*` from `models/voice.py` and import them from `app.models.assessment_pause`.
>    - Take `services/assessment_conversation/pauses.py` from this branch.
>    - WP3's `turns.py` reads `Pause.end`/`open_at` from `pauses_for_turn`: `pauses.Interval` provides `reason`, `start`, `end` and `open_at` with the same meaning, so `timers.turn_clock` can take the Intervals directly.
>    - Both branches create `assessment_conversation/__init__.py`; keep WP3's.

Migration note for the orchestrator: on this branch `0125_proctoring_pause`
revises `0124_coding_execution`, which revises `0121_candidate_comms`. `0123`
(WP3) must not create `assessment_pauses` because `0125` does.

### p3-w4 hunk 2 (target: Phase 3 WP3, `api/assessment_conversation.py`)

> 2. **`api/assessment_conversation.py` (WP3):**
>    - In `respond`, `save_draft` and `_open_voice_turn`, replace `proctoring_gate.require_active(...)` plus `turns.require_not_device_paused(...)` with `proctoring_gate.require_answerable(session, conversation)`. `turns.require_not_device_paused` can then be deleted.
>    - In start, respond, draft and the voice routes, call `termination = await proctoring_gate.enforce(session, conversation, now=now)` first. If it returns a termination, respond with the terminated state rather than raising, so the ending commits.

Risk carried until it lands (from the p3-w4 report): an answer can still be
accepted while the camera is paused.

### p3-w4 hunk 3 (target: Phase 3 WP3, `app/core/config.py`)

> 3. **Config:** WP3 also adds `assessment_voice_max_seconds` (same default, 180) to `core/config.py`; keep one copy. WP3 additionally has `assessment_voice_failure_pause_seconds`.

### p3-w4 hunk 4 (target: Phase 3 WP3, optional)

> 4. **Optional:** WP3 could expose `turns.current_question_id(session, conversation)`. `audio._speech_during_non_audio` would then pass `question_id` to `apply_server_event`.

Also carried from the p3-w4 notes: `audio.voice_capture_open` queries
`voice_answers` by raw SQL, and that table exists only after WP3's migration.
On this branch a proctoring audio chunk with speech would fail with
UndefinedTable; the `voice_answers` probe test skips until WP3 lands.

---

## From p4-4d.md (Phase 4 WP-4D, Monaco coding answer)

### p4-4d hunk 5 (target: Phase 4 WP-4C, the backend coding router)

> 5. **WP-4C backend router:**
>    - Mount it under `/api/v2/assessments`.
>    - `POST .../coding/{qid}/runs` returns 202 `{run_id, status}`.
>    - `GET .../runs/{run_id}` returns `{run_id, status: queued|complete|unavailable|failed, tests: [{key, passed, result_word, stdout, expected_stdout, stderr, compile_output}], message}`.
>    - Refusals are `{"detail": "<sentence>"}`.
>    - `TranscriptAnswerDetailOut` gains optional `coding_outcome`, `compile_error`, `review_reasoning` and `review_citations`.

---

## From p3-w6b.md (Phase 3 WP6b, frontend proctoring and recording)

### p3-w6b hunk 2 (target: Phase 3 WP5)

> 2. WP5's `test_video_interview_mode_removed.py` must allowlist `frontend/components/assessment-video-section.tsx`. It compares the stored mode against `"video_interview"`, read-only, the same way `models/dual_mode.py` is allowlisted.

Also carried from the p3-w6b open risks (target: WP5): a segment's upload is
opened on the server only when its first part is sent, and WP5 refuses to
open a segment once the proctoring session has ended, so a recording tail
shorter than one part is lost on early termination. The fix belongs to WP5:
allow opening a segment while the recording is still open, even after the
session ends.

---

## Found at integration, not a package hunk (for the orchestrator)

- `api/dashboard.move_stage` (`POST /api/v1/dashboard/jobs/{job_id}/candidates/{link_id}/stage`)
  calls `hiring_pipeline.apply_transition` directly for ANY FSM-legal target,
  including `assessment_invited` and `assessment_in_progress`. That is the
  same second invitation door p3-w1 closed in `api/pipeline.change_status`:
  it writes the stage with no `assessment_conversations` row, asks no credit
  question and sends no invitation. It should route `assessment_invited`
  through `assessment_invitations.invite_batch` (with the `send_outreach`
  check) and refuse `hiring_pipeline.SYSTEM_ONLY_TARGETS`, the way
  `change_status` now does. Not changed here: no stage 2 package owns
  `api/dashboard.py` and it changes a recruiter surface.

---

## Applied on this branch (for reference, do not re-apply)

| Source | Hunk | Commit |
|---|---|---|
| p2-a | 1, `swot_analysis.build_context` redaction | `fix(ctc): redact pay from the SWOT and Sutra prompts; ...` |
| p2-a | 2, `sutra._payload` redaction | same commit |
| p2-a | 4, `yukti_matching_system` on `GATED_PROMPTS` + render values | same commit |
| p2-a | CTC registry: SWOT and Sutra PENDING to CANARY, two xfails removed | same commit |
| integration | `coding_generation.write_coding_question.execute` registered CANARY with a canary | same commit |
| p3-w1 | 1, `assessment_in_progress` half (manual exclusion + `change_status` refusal) | `fix(pipeline): a hand move to assessment_in_progress is refused ...` |
| p4-4d | 1, provider + `CodingSubmitButton` + `starterCodeFor` in the player | `fix(assessment-ui): the player hosts the Monaco coding answer; ...` |
| p4-4d | 2, `onBlockedAction(kind)` required (merge conflict resolved in `contracts.ts`) | merge commit of `wip/p3-w6a` + the same `fix(assessment-ui)` commit |
| p4-4d | 3, one `starterCodeFor` (6a added none on its branch; nothing to delete) | present |
| p4-4d | 4, `question-renderer.test.tsx` coding section | auto-merged, present |
| p4-4d | 6, `coding-contract.test.ts` reads `CANDIDATE_FIELDS` from `payload.py` | `fix(assessment-ui)` commit |
| p3-w6a | 1, `starterCodeOf` deleted | `fix(assessment-ui)` commit |
| p3-w6a | `proctoring-shell.test.tsx` from 6b; `contracts.ts`/`answers.ts` merge | merge commits |
| p3-w6b | 1, `consumePausedMs` stub deleted | `fix(assessment-ui)` commit |
| p3-w6b | 3, `model-assets.test.ts` green with WP4 merged | verified by the vitest run |
| p3-w4 | 5, pause overlay renders `PauseOut.message`; lockdown kinds map to report lines | present from 6b, verified |
| p4-4b1 / p3-w4 | migrations chained 0121 -> 0124 -> 0125 | `chore(migrations): chain 0125_proctoring_pause after 0124_coding_execution` |
