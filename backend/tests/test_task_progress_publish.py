"""In-flight progress reaches the polling endpoint, from both sides of a loop.

`TaskContext.publish` is called by a SYNC task body with no loop running, and
by `run_matching` from INSIDE its own `asyncio.run`. The second case used to
call `asyncio.run` again, which raises; the raise was swallowed at DEBUG, the
coroutine was never awaited (the `RuntimeWarning` in every run), and the job
page saw only the terminal payload. Found by the golden journey.

Both cases are judged the way the job page judges them: by reading the
run-status record through `status.read`, against the real Redis, BEFORE the
run has finished.
"""
from __future__ import annotations

import asyncio
import uuid
import warnings

from app.workers import status
from app.workers.runtime import TaskContext


def _context() -> TaskContext:
    return TaskContext(
        run_id=f"progress-test-{uuid.uuid4()}", name="pickready.test", attempt=1, max_attempts=1
    )


def test_progress_published_inside_a_running_loop_is_readable_mid_run() -> None:
    ctx = _context()
    payload = {"stage": "reading_resumes", "done": 2, "of": 5}

    async def _run_body() -> status.RunStatus:
        # The shape of `run_matching`: a sync callback invoked from inside the
        # task's own loop, then more work on that loop before the run ends.
        ctx.publish(payload)
        for _ in range(50):
            seen = await status.read(ctx.run_id)
            if seen.state == status.STATE_PROGRESS:
                return seen
            await asyncio.sleep(0.01)
        return await status.read(ctx.run_id)

    with warnings.catch_warnings():
        # An un-awaited coroutine is exactly the defect: make it fail the test.
        warnings.simplefilter("error", RuntimeWarning)
        seen = asyncio.run(_run_body())

    assert seen.state == status.STATE_PROGRESS
    assert seen.payload == payload


def test_progress_published_with_no_loop_running_is_readable() -> None:
    ctx = _context()
    payload = {"stage": "queued"}

    ctx.publish(payload)

    seen = asyncio.run(status.read(ctx.run_id))
    assert seen.state == status.STATE_PROGRESS
    assert seen.payload == payload
