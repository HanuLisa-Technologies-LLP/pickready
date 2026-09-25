# Hunks for the orchestrator from Phase 4 WP-4C

## 1. `backend/app/api/assessment_conversation.py` (Phase 3 WP3 owns `respond`)

Import, beside the other service imports:

```python
from app.services.coding_assessment import final_answer as coding_final_answer
```

In `respond`'s STRUCTURED branch, immediately after the
`await _write_answer_record(...)` call for `question_row` (before
`_record_behaviour`), one call, unconditional (a no-op for every format but a
v2 coding question):

```python
        await coding_final_answer.accept_structured_answer(
            session, conversation=conversation, question=question_row,
        )
```

When WP3 moves the body into `services/assessment_conversation/turns.submit_turn`,
the same call goes after the answer row is written, and the EXPIRY path passes
`auto_submitted=True`; for an expired coding turn with no client submission,
WP3 first writes the answer from `submissions.latest_draft(...)` (empty when it
returns None) and then makes this call.

Then in `backend/tests/test_ai_reachability.py`, move
`("app/services/coding_assessment/final_answer.py", "accept_structured_answer")`
from `ENTRY_POINTS_WITHOUT_CALLERS` to `REQUIRED_CALLERS` with the reason
"Phase 3's respond structured branch".

## 2. `backend/app/api/assessment_coding.py::_require_open` (after WP3 lands)

Add the server-side timer and the pause to the open-question check, once
`timers.turn_clock` and `pauses.pauses_for_turn` exist (names per PLAN-p3 3.4):

```python
    clock = timers.turn_clock(
        conversation.prompt_shown_at,
        conversation.turn_allocation_seconds,
        await pauses.pauses_for_turn(session, conversation),
        datetime.now(timezone.utc),
    )
    if clock.paused:
        raise HTTPException(status_code=409, detail=PAUSED_DETAIL)
    if clock.expired:
        raise HTTPException(status_code=409, detail=QUESTION_NOT_OPEN_DETAIL)
```

If WP3 makes `_candidate_link` / `_conversation_prompts` public (PLAN-p4 7.1 g),
rename the two imports at the top of `assessment_coding.py`.

## 3. Prerequisite: `hunks/p4-4b1-types-v2-dispatch.patch`

`types.candidate_view` on this branch still reads every coding payload as v1
and RAISES on a v2 row. The recruiter transcript serialises the candidate view
of every structured answer, so a transcript holding an executed coding answer
500s until 4B1's hunk is applied. `test_hidden_tests_never_leave_server.py`
installs that exact dispatch with `monkeypatch` (`_v2_candidate_view`); delete
the helper once the hunk lands.
