# Stage 2 integration: deferred orchestrator hunks

Branch `wip/stage2-int`, built from `release/vivekium` (5bc4efb) by merging,
in order, `wip/p2-a`, `wip/p4-4b1`, `wip/p3-w1`, `wip/p3-w4`, `wip/p4-4d`,
`wip/p3-w6a`, `wip/p3-w6b`, then (second round) `wip/p2-b`, `wip/p2-e`,
`wip/p3-w3`, `wip/p3-w2`, `wip/p4-4b2`, `wip/p3-w5`.

Migration chain on this branch: 0121_candidate_comms -> 0122_yukti ->
0123_assessment_conversation -> 0124_coding_execution -> 0125_proctoring_pause
-> 0126_recording_retention (one head; upgrade from empty and a downgrade to
0121 plus upgrade verified).

Every "HUNKS FOR ORCHESTRATOR" item in the merged packages' reports that
targets a package NOT merged here (Phase 2 WP-C, WP-D, WP-F; Phase 4 WP-4C,
WP-4F; Phase 5; Phase 7; the deploy stage) is recorded below VERBATIM from its
source report, with the source named. The hunks that WERE applied are listed
at the end, so nothing is applied twice.

Source reports: `C:\dev\pickready\.claude\vivekium-release\reports-stage2\`.
`wip/p2-b`, `wip/p3-w3`, `wip/p3-w2` and `wip/p4-4b2` have no report in that
directory at integration time; their hunks (if any) are not recorded here, and
their CLAUDE.md drafts (`claude-p2-b.md`, `claude-p3-w3.md`,
`claude-p4-4b2.md`) name none. `wip/p3-w2` has no CLAUDE.md draft on its
branch.

---

## Deferred, from p2-a.md (Phase 2 WP-A, Yukti core)

### p2-a hunk 5 (target: Phase 2 WP-F)

> 5. The `prescreen.anonymise` copy stays until WP-F deletes prescreen, or re-points it to `yukti.anonymise`, so there is one implementation.

---

## Deferred, from p2-e.md (Phase 2 WP-E, the dashboard)

### p2-e hunk 1, the ranking half (target: Phase 2 WP-C)

> 1. Drop commit db5b00c after WP-B and WP-C merge (details at the top).

Applied here for the MIGRATION half only: `p2e_scaffold_yukti_columns.py` is
deleted because `0122_yukti` (WP-B) adds the same columns. The
`app/services/yukti/ranking.py` half of db5b00c STAYS on this branch, because
WP-C is not merged and the dashboard calls it. When WP-C merges, take WP-C's
`ranking.py`. The dashboard's call contract, verbatim from the report:

> My code calls exactly `yukti_ranking.rank_score_sql(link="link", report="rep", tenant="t")`, `yukti_ranking.grade_word(x)` and, in tests only, `rank_score(...)` and `must_have_cap()`. If WP-C's signatures differ, those call sites need adjusting.

### p2-e hunk 2 (target: Phase 2 WP-C)

> 2. When WP-C's `ranking.status_word` / `projection` land, point `services/dashboard.NOT_CHECKED_*`, `NOT_ASSESSED_NOTES`, `LEGACY_NOTE` and `RESUME_CHECK_NOTE` at them so there is one set of wording. `test_dashboard_columns` pins the reasons to `FAILURE_REASONS`, so it will catch a drift.

### p2-e hunk 3 (targets: Phase 2 WP-C and WP-F)

> 3. WP-F's planned sweep bans `ready_pick_score` and `pre_screen_grade` over `backend/tests`. `tests/test_report_number_ban.py` (WP-C) still uses `ready_pick_score` as an example of a forbidden key (lines 394–606), and `tests/test_yukti_live.py:277/543` (WP-F) names the pre-screen grade and dashboard cell. Those owners need to rename or exempt them. None of my files contain either string.

---

## Deferred, from p3-w1.md (Phase 3 WP1, invitations and credits)

### p3-w1 hunk 1, the `assessment_completed` half (target: Phase 5, the report writer)

> - `backend/app/services/hiring_pipeline.py`: the "Move to" dropdown still offers `assessment_in_progress` and `assessment_completed` (seen in the API response for an invited application). A hand move claims a started session or a written report that do not exist. Suggested change: `MANUAL_TRANSITION_EXCLUDED = frozenset({SHORTLISTED, ASSESSMENT_IN_PROGRESS, ASSESSMENT_COMPLETED})`, plus a refusal of those two targets in `api/pipeline.change_status` (my file; can be done at integration). I did not change the owner-level manual set.

Applied here for `assessment_in_progress` only. DEFERRED for
`assessment_completed`, with the reason: nothing in the product writes
`assessment_completed` automatically today (no
`apply_transition(..., target=ASSESSMENT_COMPLETED)` and no SQL writer in
`backend/app`). The FSM's only way on from `assessment_in_progress` is
`assessment_completed`, so withdrawing the hand move now would strand every
assessed candidate before shortlisting. When Phase 5's report insert moves the
application to `assessment_completed` itself, add `ASSESSMENT_COMPLETED` to
`MANUAL_TRANSITION_EXCLUDED` and to `SYSTEM_ONLY_TARGETS` in
`services/hiring_pipeline.py`, widen `SYSTEM_ONLY_REFUSAL` to name both
stages, and extend
`tests/test_invite_batch_credits.py::test_a_hand_move_to_in_progress_is_refused_and_not_offered`.

---

## Deferred, from p3-w4.md (Phase 3 WP4, device pause and audio rules)

### p3-w4 hunk 4 (optional; target: the Phase 3 WP3 engine, a follow-up)

> 4. **Optional:** WP3 could expose `turns.current_question_id(session, conversation)`. `audio._speech_during_non_audio` would then pass `question_id` to `apply_server_event`.

Not applied: it is marked optional, and the engine's current-turn identity is
WP3's to expose. The speech event is stored without `question_id` until then.

---

## Deferred, from p3-w5.md (Phase 3 WP5, segmented recording and D4 retention)

### p3-w5 hunk 1 (target: Phase 7, `app/services/object_storage.py`)

> 1. **Phase 7, `app/services/object_storage.py`** (lines 237 and 259): replace `ServerSideEncryption="AES256"` with `ServerSideEncryption="aws:kms", SSEKMSKeyId=<settings.s3_kms_key_id>`, refusing with `ObjectStorageNotConfigured` when that is empty. `video.storage._sse()` already does exactly this and can be reused. Without it, resume, compliance and project uploads are denied by both policies.

The report's own warning, carried: resume, compliance and project uploads fail
on pilot today because the live bucket policy denies every write whose
encryption header is not `aws:kms`, and this hunk is what fixes it.

### p3-w5 hunk 3 (target: the deploy stage, pilot `terraform.tfvars`, gitignored)

> 3. **Pilot `terraform.tfvars`** (gitignored, deploy stage): set `transcribe_enabled = true`. The bucket name is already set.

---

## Deferred, from p4-4d.md (Phase 4 WP-4D, Monaco coding answer)

### p4-4d hunk 5 (target: Phase 4 WP-4C, the backend coding router)

> 5. **WP-4C backend router:**
>    - Mount it under `/api/v2/assessments`.
>    - `POST .../coding/{qid}/runs` returns 202 `{run_id, status}`.
>    - `GET .../runs/{run_id}` returns `{run_id, status: queued|complete|unavailable|failed, tests: [{key, passed, result_word, stdout, expected_stdout, stderr, compile_output}], message}`.
>    - Refusals are `{"detail": "<sentence>"}`.
>    - `TranscriptAnswerDetailOut` gains optional `coding_outcome`, `compile_error`, `review_reasoning` and `review_citations`.

`wip/p4-4c` points at the same commit as `wip/p4-4b2` (107b90d) at
integration time, so WP-4C has not started: the Run routes the frontend calls
do not exist on this branch.

---

## Found at integration, not a package hunk (for the orchestrator)

1. **`api/dashboard.move_stage` is a second invitation door.**
   `POST /api/v1/dashboard/jobs/{job_id}/candidates/{link_id}/stage` calls
   `hiring_pipeline.apply_transition` directly for ANY FSM-legal target,
   including `assessment_invited` and `assessment_in_progress`. That is the
   defect p3-w1 closed in `api/pipeline.change_status`: it writes the stage
   with no `assessment_conversations` row, asks no credit question and sends
   no invitation. It should route `assessment_invited` through
   `assessment_invitations.invite_batch` (with the `send_outreach` check) and
   refuse `hiring_pipeline.SYSTEM_ONLY_TARGETS`, the way `change_status` now
   does. Not changed here: no merged package owns `api/dashboard.py`, and it
   changes a recruiter surface.
2. **A v2 coding answer is stored but never executed.** `wip/p4-4b2`'s
   `coding_assessment.submissions.accept_final` (the only writer of
   `coding_submissions`) has no caller: `wip/p3-w3`'s `respond` was written
   without it. `test_ai_reachability.ENTRY_POINTS_WITHOUT_CALLERS` records it
   ("Phase 3's respond calls it for a v2 coding answer"), and likewise
   `latest_draft` (the timeout auto-submit) and `scoring_hold` (Phase 5).
   Harmless on pilot while `code_execution_backend=disabled` (the composer
   serves no coding question then), and it must land before the sandbox is
   enabled.
3. **The Yukti ranking stand-in.** See p2-e hunk 1 above.

---

## Applied on this branch (for reference, do not re-apply)

| Source | Hunk | Where |
|---|---|---|
| p2-a | 1, `swot_analysis.build_context` redaction | commit `fix(ctc): redact pay from the SWOT and Sutra prompts; ...` |
| p2-a | 2, `sutra._payload` redaction | same commit |
| p2-a | 3, `resume_parsing` redaction and its CANARY | arrived with `wip/p2-b` |
| p2-a | 4, `yukti_matching_system` on `GATED_PROMPTS` + render values | `fix(ctc)` commit |
| integration | `coding_generation.write_coding_question.execute` registered CANARY with a canary | `fix(ctc)` commit |
| p4-4b1 | 1, the v2 dispatch in `assessment_formats/types.py` | arrived with `wip/p3-w2` |
| p4-4b1 | 2, the v1 coding writer retired from `assessment_formats/generation.py` | arrived with `wip/p3-w2` |
| p4-4b1 | 3, `write_coding_question` / `persist_coding_question` in `REQUIRED_CALLERS` | `test(reachability): the coding question writer ...` |
| p3-w1 | 1, `assessment_in_progress` half | `fix(pipeline): a hand move to assessment_in_progress ...` |
| p3-w4 | 1, one `assessment_pauses` table (WP4's), dropped from 0123 | arrived with `wip/p3-w3` (ed007f8) |
| p3-w4 | 2, `require_answerable` in respond/draft/voice; `enforce` first in start/respond/draft/voice | `fix(assessment): proctoring alone decides ...` |
| p3-w4 | 3, one `assessment_voice_max_seconds` setting | merge commit of `wip/p3-w3` |
| p3-w4 | 5, the pause overlay renders `PauseOut.message`; lockdown kinds map to report lines | present from `wip/p3-w6b`, verified |
| p3-w5 | 2, voice audio in both purge enumerators and the hourly repair (adapted to WP3's `voice_audio`) | `fix(retention): a spoken answer's audio ...` |
| p3-w5 | 4, the WP3 names `api/assessment_recording` imports | verified present |
| p3-w5 | 5, the WP6b recorder contract | per the p3-w6b report (built against WP5's schema); frontend suite green |
| p4-4d | 1, provider + `CodingSubmitButton` + `starterCodeFor` in the player | `fix(assessment-ui): the player hosts the Monaco coding answer; ...` |
| p4-4d | 2, `onBlockedAction(kind)` required (conflict resolved in `contracts.ts`) | merge of `wip/p3-w6a` + `fix(assessment-ui)` |
| p4-4d | 3, one `starterCodeFor` | present |
| p4-4d | 4, `question-renderer.test.tsx` coding section | auto-merged, present |
| p4-4d | 6, `coding-contract.test.ts` reads `CANDIDATE_FIELDS` from `payload.py` | `fix(assessment-ui)` commit |
| p3-w6a | 1, `starterCodeOf` deleted | `fix(assessment-ui)` commit |
| p3-w6a | `proctoring-shell.test.tsx` from 6b; `contracts.ts`/`answers.ts` merge | merge commits |
| p3-w6b | 1, `consumePausedMs` stub deleted | `fix(assessment-ui)` commit |
| p3-w6b | 2, allowlist `assessment-video-section.tsx` in the mode sweep | NOT NEEDED: the sweep's pattern does not match that file |
| p3-w6b | 3, `model-assets.test.ts` green with WP4 merged | vitest |
| p2-e | 1, migration half (scaffold migration deleted) | `chore(migrations): drop the p2-e scaffold migration; ...` |
| integration | `_ensure_conversation_ready` deleted with its last caller; mode ratchet emptied | `refactor(assessment): the readiness helper goes ...` |
| p4-4b1 / p3-w4 / p2-b / p3-w3 / p3-w5 | migrations chained 0121 -> 0122 -> 0123 -> 0124 -> 0125 -> 0126 | `chore(migrations): chain 0125 ...`, the `wip/p3-w3` merge and the `wip/p3-w5` merge |
