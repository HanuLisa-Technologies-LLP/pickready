# Stage 2 integration: deferred orchestrator hunks

Branch `wip/stage2-int`, built from `release/vivekium` (5bc4efb) by merging,
in order, `wip/p2-a`, `wip/p4-4b1`, `wip/p3-w1`, `wip/p3-w4`, `wip/p4-4d`,
`wip/p3-w6a`, `wip/p3-w6b`, then (second round) `wip/p2-b`, `wip/p2-e`,
`wip/p3-w3`, `wip/p3-w2`, `wip/p4-4b2`, `wip/p3-w5`, then (third round)
`wip/p2-f` (which carries WP-C, WP-D, WP-E and WP-F on the Phase 2 integration
line) and `wip/p4-4f` (which carries WP-4B2, WP-4C and WP-4F). Every Stage 2
package is merged.

Migration chain on this branch: 0121_candidate_comms -> 0122_yukti ->
0123_assessment_conversation -> 0124_coding_execution -> 0125_proctoring_pause
-> 0126_recording_retention (one head; upgrade from empty and a downgrade to
0121 plus upgrade verified).

Every "HUNKS FOR ORCHESTRATOR" item in the merged packages' reports that
targets work NOT on this branch (Phase 5, Phase 7, the deploy stage, and the
one Phase 3 follow-up no package took) is recorded below VERBATIM from its
source report, with the source named. The hunks that WERE applied are listed
at the end, so nothing is applied twice.

Source reports: `C:\dev\pickready\.claude\vivekium-release\reports-stage2\`.
`wip/p2-b`, `wip/p3-w3`, `wip/p3-w2` and `wip/p4-4b2` have no report in that
directory at integration time; their hunks (if any) are not recorded here, and
their CLAUDE.md drafts (`claude-p2-b.md`, `claude-p3-w3.md`,
`claude-p4-4b2.md`) name none. `wip/p3-w2` has no CLAUDE.md draft on its
branch.

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

## Deferred, from p2-d.md and p2-f.md (Phase 2 WP-D / WP-F)

### p2-d hunk 2 = p2-f hunk 3 (target: a Phase 3 follow-up, `schemas/ranking.py`)

> 2. Phase 3: when `assessment_mode*` / `video_status` go from `schemas/ranking.py`, remove the "Assessment" column from `candidate-ranking-table.tsx` (the header, the `TableCell` reading `assessment_mode_label` / `video_status`, and `columnCount` 13 to 12), the matching fields in `lib/types.ts`, and the fixture keys in `candidate-ranking-table.test.tsx`.

Still deferred: `schemas/ranking.py` still carries `assessment_mode`,
`assessment_mode_label` and `video_status`, and `job_candidates._row_payload`
still fills them. No merged Phase 3 package removed them.

### p2-f hunk 1 (target: Phase 5)

> 1. **Phase 5:** swap `_matching_dimensions` to `yukti.projection.ai_score_summary`, then delete `services/matching_categories.py` and its `PENDING_CATEGORY_READERS` entry. The hand-off test forces the second step once the first lands.

---

## Deferred, from p4-4f.md (Phase 4 WP-4F)

### p4-4f hunk 1 (target: Phase 5, with the Miti coding sub-stage)

> 1. **Phase 5, once Miti reads `coding_assessment.evidence` and stops sending coding answers to `_evaluate_subjective`.**
>    - In `assessment_formats/evaluation.py`, delete `CODING_CRITERIA`, `NOT_EXECUTED_NOTE`, `HEDGE_MARKERS` and their `__all__` entries (lines 40-43, 58-83), the CODING prompt entry (93), the CODING branch of `rubric_for` (99-100), the hedge rule (181-187), the CODING branch of `evaluate` (219-230) and the note stamp (273-274).
>    - Make `evaluate(CODING)` raise.
>    - Delete `prompts/assessment_answer_evaluation_coding.txt`.
>    - In `test_assessment_formats_evaluation.py`, delete the tests at lines 128, 192 and 378, add a test that `evaluate(CODING)` raises, and keep the empty-submission test.
>    - Remove 7 entries from `PENDING_REMOVAL`.

The full text is `docs/release/2026-09-vivekium/hunks/p4-4f-removals-and-harness.md` section 1.

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
2. **Scoring does not wait for an owed coding answer.** Answering the last
   base question through `respond` completes the conversation and dispatches
   scoring at once, while a v2 coding submission may still be executing.
   `coding_assessment.submissions.scoring_hold` exists for exactly this and
   has no caller (`test_ai_reachability.ENTRY_POINTS_WITHOUT_CALLERS`, owner
   Phase 5). Harmless on pilot while `code_execution_backend=disabled` (no
   coding question is composed), and it must land with Phase 5 before the
   sandbox is enabled. The harness scenario
   `adversarial.the_code_runner_is_unavailable` v2 records the early dispatch
   in its expected counts with this reason beside it.

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
| p2-a | 5, one anonymiser (`hiring/prescreen.py` deleted) | arrived with `wip/p2-f` |
| p2-e | 1 (ranking half), 2, 3 | arrived with `wip/p2-f` (WP-C's `ranking.py`, the dashboard reads `yukti.projection`) |
| p2-c | 1 to 4 (pending-schema fixture deleted, frontend polling URL, harness dashboard read, legacy read side deleted) | arrived with `wip/p2-d` / `wip/p2-f` |
| p2-d | 1 (a finished run keeps its stages), 3 | arrived with `wip/p2-f` |
| p2-f | 2 (SWOT and Sutra redaction) | already applied in round one |
| p4-4c | 1, the respond hook in `turns.submit_turn` (and the expired-turn latest-Run submit) | `feat(assessment): a final coding answer given through the turn engine ...` |
| p4-4c | 2, the Run route reads the server's turn clock | `fix(coding): a Run follows the server's turn clock; ...` |
| p4-4c | 3, the `_v2_candidate_view` stand-in deleted | same commit |
| p4-4d | 5, the WP-4C router | arrived with `wip/p4-4f` |
| p4-4f | 2 and 3, the stale `PENDING_REMOVAL` entries deleted | `test(coding): the read-only evaluation ledger loses ...` |
| p4-4f | 4, the harness answers through `respond` and sweeps the transcript | `test(harness): the coding scenarios answer through the real respond ...` |
| p4-4b1 / p3-w4 / p2-b / p3-w3 / p3-w5 | migrations chained 0121 -> 0122 -> 0123 -> 0124 -> 0125 -> 0126 | `chore(migrations): chain 0125 ...`, the `wip/p3-w3` merge and the `wip/p3-w5` merge |
