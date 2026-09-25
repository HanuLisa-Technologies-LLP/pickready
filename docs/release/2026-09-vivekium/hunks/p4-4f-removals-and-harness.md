# Phase 4 WP-4F: hunks for the orchestrator

Three groups. Each one also shrinks `PENDING_REMOVAL` in
`backend/tests/test_coding_read_only_evaluation_removed.py`, and that test FAILS
until the matching entries are deleted in the same change (a stale entry is a
standing permission for the name to come back). Apply each group only when the
condition beside it holds.

## 1. The read-only coding evaluator (Phase 5, with the Miti coding sub-stage)

Condition: Phase 5's scorer reads a v2 coding answer through
`coding_assessment.evidence` (`evidence_for_answer` / `for_conversation`) and
`functional_assessment._score_item` no longer sends a CODING answer to
`_evaluate_subjective`. Before that, deleting this branch leaves the scorer with
nothing to grade a coding answer with.

`backend/app/services/assessment_formats/evaluation.py`:

- Delete `CODING_CRITERIA` (lines 58 to 64), `NOT_EXECUTED_NOTE` (66 to 71),
  `HEDGE_MARKERS` (73 to 83) and their three `__all__` entries (40, 42, 43).
- Delete `types.CODING: "assessment_answer_evaluation_coding"` from
  `_PROMPT_FOR_TYPE` (93).
- Delete the CODING branch of `rubric_for` (99 to 100).
- Delete the hedge rule in `_evaluator` (181 to 187).
- Delete the CODING branch of `evaluate` (219 to 230; the `else` body becomes
  the only body) and the `not_executed_note` stamp (273 to 274).
- Make `evaluate` REFUSE `types.CODING` with a `ValueError` naming
  `coding_assessment.review` as the replacement, so a caller that still routes
  a coding answer here fails loudly instead of being graded by reading.
- Rewrite the module docstring's first paragraph (line 12) so it no longer
  describes the not-executed note as current behaviour.

Delete `backend/app/prompts/assessment_answer_evaluation_coding.txt` and remove
it from `tests/fixtures/prompt_snapshots.json` if listed.

`backend/tests/test_assessment_formats_evaluation.py`: delete
`test_a_coding_evaluation_always_states_that_the_code_was_not_run` (128),
`test_an_unhedged_coding_verdict_is_rejected` (192) and
`test_a_coding_answer_is_evaluated_from_the_code_it_submitted` (378); add one
test that `evaluate(types.CODING, ...)` raises. Keep
`test_an_empty_coding_submission_is_unanswered` (400): an empty coding answer
is an evidence gap in `coding_assessment.evidence` too.

`test_coding_read_only_evaluation_removed.PENDING_REMOVAL`: delete the seven
entries under "The evaluator's coding branch".

`backend/tests/test_assessment_formats_live.py` line 796 KEEPS its
`not_executed_note` sentence: it reproduces a stored legacy row, and the sweep
lists it under `PERMANENT` with that reason.

## 2. The old coding question writer (Phase 3 composer, 4B1 hunk 2)

Condition: the Phase 3 composer sends coding slots to
`coding_generation.write_coding_question` / `persist_coding_question`. The exact
edit is 4B1's hunk 2 (see `reports-stage2/p4-4b1.md`). Then delete the three
entries under "The old question writer" from `PENDING_REMOVAL`.

## 3. The editor (Phase 4 WP-4D, on `wip/p4-4d`)

Condition: 4D merges (Monaco replaces CodeMirror). Delete the five entries under
"The editor". If 4D's branch still names `codemirror` anywhere in
`frontend/app`, `frontend/components`, `frontend/lib` or `package.json` after
the merge, the sweep names the file and line.

## 4. The harness's final-answer step, once `respond` takes a v2 coding answer

Condition: the 4B1 types hunk (`hunks/p4-4b1-types-v2-dispatch.patch`) AND the
4C respond hook (`hunks/p4-4c-respond-hook.md`, hunk 1) have both landed, so
`respond` parses a v2 coding answer and calls
`coding_final_answer.accept_structured_answer` itself.

`backend/harness/workload.py`, `_record_the_final_coding_answer`: replace the
body (the two `AssessmentMessage` rows, the `AssessmentAnswer` row, the
completion flag and the direct `accept_structured_answer` call) with ONE request
over the real route, keeping the fact names the scenarios read:

```python
app_client.as_candidate()
with _capturing_coding_logs(ctx):
    observed = app_client.request(
        ctx,
        "record_the_final_coding_answer",
        "POST",
        f"{V2}/assessments/conversations/{ctx.world.id('conversation')}/respond",
        json={
            "answer": "",
            "answer_payload": {"language": "python", "code": CODING_ECHO_PROGRAM},
        },
    )
submission_id = _read_submission_id(ctx)  # one SELECT of coding_submissions.id
                                          # for the world's conversation, in a
                                          # fresh session after the response
ctx.facts["coding_submission_recorded"] = submission_id is not None
ctx.facts["coding_submission_id"] = None if submission_id is None else str(submission_id)
ctx.stage("coding_answer_recorded")
```

Check the exact request body against `respond`'s schema at that point (the
field carrying a structured answer is `answer_payload` on this branch). Both
scenarios then also exercise the respond route's completion path, so re-run
`python -m harness run --tier adversarial` and confirm the `dispatched:` counts:
`respond` completing the conversation dispatches scoring itself, so
`adversarial.a_program_cannot_exfiltrate_the_hidden_tests` may see
`pickready.run_functional_assessment` twice (once from `respond`, once from the
submission's hand-over; scoring's advisory lock makes the second a no-op) and
`adversarial.the_code_runner_is_unavailable` once. Set the counts to what the
integrated product does and say why in the YAML, rather than loosening them.

Also, once the 4B1 types hunk lands, add a `read_the_coding_transcript` step (a
staff GET of `/api/v2/assessments/transcripts/links/{link}`) to the
exfiltration scenario's workload, so the recruiter's view is swept too. It is
not in the scenario today because that route serialises `candidate_view` of the
v2 payload, which answers 500 on this branch.
