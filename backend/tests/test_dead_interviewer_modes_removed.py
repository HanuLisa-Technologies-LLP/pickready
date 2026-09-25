"""The interviewer's dead question-delivery graph is gone, and its live half stays.

`services/interviewer` carried a second graph that rewrote the next scripted
question (a GENERATE mode and a REWORD mode, a substance check on the rewrite,
two prompts). Nothing on the live path called it: the base question is written
with its rubric by `ppi_interview.write_question`, and only the offline eval
reached the rest. It was DELETED on 2026-09-24 (PLAN-p3 3.5), and so was
`hiring/evidence_graph.extension_ceiling`, whose only reader it was.

THE HALF THAT IS LIVE MUST SURVIVE THIS SWEEP. `challenge_prompt` and
`interview_challenge.txt` word the re-ask after a non-answer, through
`challenge_non_answer`, on every conversation; the master prompt's instruction
to delete them was wrong (CONTRACT v2, P3). The second test pins that.

Whitespace-normalised through `tests/removal_sweep.py`, so a mention wrapped
across a line cannot hide.
"""
from __future__ import annotations

import re

from tests.removal_sweep import BACKEND, sweep

SELF = BACKEND / "tests" / "test_dead_interviewer_modes_removed.py"

REMOVED = re.compile(
    r"\b(?:compose_next_question|MODE_GENERATE|MODE_REWORD|_substance_preserved"
    r"|extension_ceiling|_DELIVER_GRAPH|_deliver_(?:plan|compose|validate|route)"
    r"|_build_deliver_graph|DELIVERY_GROWTH_FACTOR|DELIVERY_MIN_CEILING"
    r"|interview_write_question|interview_deliver_question)\b"
)


def test_nothing_names_the_deleted_delivery_graph() -> None:
    hits = sweep(REMOVED, exempt=[SELF])
    assert not hits, "the deleted interviewer modes are still named:\n" + "\n".join(hits)


def test_the_prompt_files_are_gone_and_the_live_challenge_stays() -> None:
    prompts = BACKEND / "app" / "prompts"
    assert not (prompts / "interview_write_question.txt").exists()
    assert not (prompts / "interview_deliver_question.txt").exists()
    assert (prompts / "interview_challenge.txt").exists()

    from app.services import interviewer

    assert callable(interviewer.challenge_prompt)
    assert callable(interviewer.challenge_non_answer)
    assert callable(interviewer.next_follow_up)
    assert callable(interviewer.is_semantic_repeat)
    for gone in ("compose_next_question", "MODE_GENERATE", "MODE_REWORD"):
        assert not hasattr(interviewer, gone), gone

    from app.services.hiring import evidence_graph

    assert not hasattr(evidence_graph, "extension_ceiling")
