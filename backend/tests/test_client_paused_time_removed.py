"""No client-reported pause and no answer edit: the server keeps the time, and a past answer is final.

Before 2026-09-24 the browser sent `paused_ms` with each answer and the server
subtracted it from the time a recruiter reads, so that number was one the
client chose; and `PATCH .../answers/{id}` rewrote a past answer. Appendix B
section 3 ends both: paused time is a row the server wrote
(`assessment_pauses`), and past answers are read-only.

THE BACKEND IS SWEPT NOW. The frontend's sender (`consumePausedMs`, the
warning modal's `usePausedTime`, the answer edit) is removed by the frontend
work packages that run beside this one; each file still carrying it is a
PENDING hand-off named with its owner, and an entry whose file no longer
carries it FAILS, so an exemption cannot outlive its reason.
"""
from __future__ import annotations

import re

from app.schemas.assessment_conversation import ConversationMessageIn, DraftIn
from tests.removal_sweep import BACKEND, REPO, sweep

SELF = BACKEND / "tests" / "test_client_paused_time_removed.py"

#: The field, the helpers that fed it, and the edit route's handler.
REMOVED = re.compile(r"\b(?:paused_ms|consumePausedMs|usePausedTime|edit_latest_answer)\b")

EXEMPT = {
    SELF: "this sweep",
    # Sends the field on purpose, to prove the server refuses it with a 422.
    BACKEND / "tests" / "test_turn_timer_api.py": "asserts the refusal",
    # PLAN-p3 WP4's module; its docstring records what the pause rows replace.
    BACKEND / "app" / "services" / "assessment_conversation" / "pauses.py": "WP4 docstring",
}

FRONTEND = REPO / "frontend"
#: Frontend files that still send or build the client pause, and their owner.
#: EMPTY since the stage 2 integration merged WP6a and WP6b: every hand-off
#: landed, and the two comments that still named the removed field were
#: reworded, so the sweep now covers the whole frontend.
PENDING_FRONTEND: dict = {}


def test_nothing_sends_or_reads_a_client_pause_or_edits_an_answer() -> None:
    hits = sweep(REMOVED, exempt=[*EXEMPT, *PENDING_FRONTEND])
    assert not hits, "a client-reported pause or the answer edit survives:\n" + "\n".join(hits)


def test_every_pending_frontend_hand_off_still_carries_it() -> None:
    """Remove the entry in the change that cleans the file."""
    stale = [
        f"{path.relative_to(REPO)} ({owner})"
        for path, owner in PENDING_FRONTEND.items()
        if not path.exists() or not REMOVED.search(path.read_text(encoding="utf-8"))
    ]
    assert not stale, "these pending entries no longer need their exemption:\n" + "\n".join(stale)


def test_the_answer_and_draft_bodies_refuse_the_field() -> None:
    for model in (ConversationMessageIn, DraftIn):
        assert "paused_ms" not in model.model_fields
        assert model.model_config.get("extra") == "forbid", model.__name__
