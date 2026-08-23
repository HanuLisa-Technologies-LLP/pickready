"""The inline reasoning shown while AI matching runs.

The property that matters most is the one a screenshot cannot check: the stages
must describe work the pipeline ACTUALLY does. A progress display is trusted by
whoever is reading it, so a convincing one that is wrong is worse than none --
and the two ways it goes wrong are a stage the pipeline never emits (a row that
spins forever) and a stage the pipeline emits that nothing declared (free-text
narration creeping back in).
"""
import inspect
import re

import pytest

from app.services import matching, matching_progress as mp


# ── The vocabulary and the pipeline agree ───────────────────────────────────


def _emitted_stage_keys() -> set[str]:
    """Every stage key `matching.run_matching` names in its own source."""
    source = inspect.getsource(matching.run_matching)
    return set(
        re.findall(r'reporter\.(?:start|finish|skip|fail)\(\s*"([a-z_]+)"', source)
    )


def test_every_declared_stage_is_emitted_by_the_pipeline():
    """A declared-but-never-emitted stage draws a row that stays pending for the
    whole run, which reads as "this step is stuck"."""
    missing = set(mp.STAGE_KEYS) - _emitted_stage_keys()
    assert not missing, f"declared but never emitted: {sorted(missing)}"


def test_every_emitted_stage_is_declared():
    """The other direction. An undeclared key is refused at runtime, so this
    catches it at build time instead of mid-run on a recruiter's screen."""
    unknown = _emitted_stage_keys() - set(mp.STAGE_KEYS)
    assert not unknown, f"emitted but not declared: {sorted(unknown)}"


def test_an_unknown_stage_is_refused_rather_than_displayed():
    with pytest.raises(mp.UnknownStage):
        mp.Progress().start("thinking_really_hard")


# ── The payload ─────────────────────────────────────────────────────────────


def test_the_full_plan_is_present_before_anything_runs():
    """The panel draws every step at once and fills them in. A list that grows
    a row at a time looks like the system is inventing its plan as it goes."""
    payload = mp.empty_payload()
    assert [row["key"] for row in payload["stages"]] == list(mp.STAGE_KEYS)
    assert {row["status"] for row in payload["stages"]} == {mp.STATUS_PENDING}


def test_starting_a_later_stage_closes_the_earlier_ones():
    """Otherwise a stage nobody explicitly finished spins forever ABOVE a stage
    that has already moved on, and the display contradicts itself."""
    progress = mp.Progress()
    progress.start("understanding")
    progress.start("scoring")
    status = {row["key"]: row["status"] for row in progress.payload()["stages"]}
    assert status["understanding"] == mp.STATUS_DONE
    assert status["planning"] == mp.STATUS_DONE
    assert status["scoring"] == mp.STATUS_ACTIVE
    assert status["saving"] == mp.STATUS_PENDING


def test_a_skipped_stage_says_why():
    """The load-bearing case. A run whose embedding service was down really did
    rank on keywords alone, and showing that stage as complete would present a
    degraded run as a full one."""
    progress = mp.Progress()
    progress.skip("semantic_retrieval", "The embedding service was unavailable.")
    row = next(
        r for r in progress.payload()["stages"] if r["key"] == "semantic_retrieval"
    )
    assert row["status"] == mp.STATUS_SKIPPED
    assert "unavailable" in row["detail"]


def test_every_transition_publishes():
    seen: list[dict] = []
    progress = mp.Progress(publish=seen.append)
    progress.start("understanding")
    progress.finish("understanding")
    progress.scored(2, 5)
    assert len(seen) == 3
    assert seen[-1]["scored_count"] == 2
    assert seen[-1]["candidate_count"] == 5


def test_a_failing_publisher_never_reaches_the_pipeline():
    """A progress display that can fail the work it describes is a strictly
    worse trade than one that goes blank."""

    def _explode(_payload):
        raise RuntimeError("redis is down")

    progress = mp.Progress(publish=_explode)
    progress.start("understanding")  # must not raise
    progress.scored(1, 1)
    assert progress.payload()["scored_count"] == 1


def test_counts_are_clamped_rather_than_trusted():
    progress = mp.Progress()
    progress.scored(-4, -9)
    assert progress.payload()["scored_count"] == 0
    assert progress.payload()["candidate_count"] == 0


# ── The no-numbers rule, and the no-narration rule ──────────────────────────


def test_no_stage_text_carries_a_score_or_percentage():
    """Counts of candidates are rows being processed. A percentage or a rating
    would be an assessment number reaching a client, which nothing may do."""
    for stage in mp.STAGES:
        text = f"{stage.label} {stage.detail}"
        assert "%" not in text, stage.key
        assert not re.search(r"\b\d+\s*(?:/|out of)\s*\d+\b", text), stage.key


def test_the_stage_text_is_static_and_cannot_come_from_a_model():
    """The whole reason this is a table. The prompts behind a matching run hold
    a real candidate's resume and a real client's JD; a model narrating its own
    reasoning quotes its prompt. Nothing in this module calls a provider."""
    source = inspect.getsource(mp)
    for forbidden in ("invoke_llm", "llm_router", "chat_completion", "agent_loop"):
        assert forbidden not in source, forbidden


def test_stage_text_is_written_for_a_recruiter():
    """Not a module name and not a status code: it says what is being done to
    their data. Cheap shape check -- a sentence, ending in a full stop."""
    for stage in mp.STAGES:
        assert stage.label and stage.label[0].isupper(), stage.key
        assert stage.detail.endswith("."), stage.key
        assert "_" not in stage.detail, stage.key


def test_no_em_dash_in_any_displayed_string():
    """Repo-wide rule: no em dash in a string, in either language."""
    dash = chr(8212)
    for stage in mp.STAGES:
        assert dash not in stage.label, stage.key
        assert dash not in stage.detail, stage.key
