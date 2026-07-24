"""Matching-pipeline robustness tests (Track B) — the graceful-degradation and
LLM-output-parsing paths that sit above the pure scoring math in test_scoring.py.

Covers:
  * _extract_valid — malformed LLM entries are skipped, not crashed on.
  * deterministic fallback breakdowns when the LLM chain is unavailable
    (ordering preserved, ceiling below the Highly-Matching boundary, comments
    flagged "AI scoring unavailable").
  * _score_batch falling back on llm_router.LLMUnavailableError instead of
    aborting the batch.

No DB — the DB-touching run_matching() is exercised by the live seeded run in
the build report, not here.
"""
import uuid

import pytest

from app.models.enums import Tier
from app.services import matching
from app.services.matching import (
    _AI_UNAVAILABLE_COMMENT,
    PARAMETERS,
    WEIGHTS,
    _extract_valid,
    _fallback_breakdown,
    _fallback_param_score,
    compute_overall_score,
)
from app.services.tiers import assign_tier


def _valid_entry(pid: str, **overrides) -> dict:
    entry = {
        "profile_id": pid,
        "skills_match": {"score": 8, "comment": "strong"},
        "experience_relevance": {"score": 7, "comment": "relevant"},
        "role_alignment": {"score": 9, "comment": "aligned"},
        "education_fit": {"score": 6, "comment": "adjacent"},
        "overall_comment": "Solid overall fit.",
    }
    entry.update(overrides)
    return entry


# ── _extract_valid: malformed skipped, valid kept ───────────────────────────

def test_extract_valid_keeps_good_skips_bad():
    good = uuid.uuid4()
    bad = uuid.uuid4()
    import json

    raw = json.dumps(
        {
            "results": [
                _valid_entry(str(good)),
                # malformed: score out of range -> must be reported missing
                _valid_entry(str(bad), skills_match={"score": 0, "comment": "x"}),
            ]
        }
    )
    got, missing = _extract_valid(raw, {good, bad})
    assert set(got) == {good}
    assert missing == {bad}
    assert got[good]["overall"]["score"] == compute_overall_score(
        {"skills_match": 8, "experience_relevance": 7, "role_alignment": 9, "education_fit": 6}
    )


def test_extract_valid_non_json_reports_all_missing():
    wanted = {uuid.uuid4(), uuid.uuid4()}
    got, missing = _extract_valid("this is not json", set(wanted))
    assert got == {}
    assert missing == wanted


def test_extract_valid_ignores_unwanted_and_duplicate_ids():
    wanted_id = uuid.uuid4()
    stray_id = uuid.uuid4()
    import json

    raw = json.dumps(
        {
            "results": [
                _valid_entry(str(wanted_id)),
                _valid_entry(str(stray_id)),          # not requested -> ignored
                _valid_entry(str(wanted_id)),          # duplicate -> ignored
                {"profile_id": "not-a-uuid"},          # unparseable id -> skipped
            ]
        }
    )
    got, missing = _extract_valid(raw, {wanted_id})
    assert set(got) == {wanted_id}
    assert missing == set()


# ── Deterministic fallback (LLM unavailable) ────────────────────────────────

def test_fallback_param_score_band_and_monotonicity():
    # single candidate -> top of band
    assert _fallback_param_score(0, 1) == matching._FALLBACK_MAX
    # best and worst hit the band edges
    assert _fallback_param_score(0, 5) == matching._FALLBACK_MAX
    assert _fallback_param_score(4, 5) == matching._FALLBACK_MIN
    # monotonic non-increasing with rank
    scores = [_fallback_param_score(i, 10) for i in range(10)]
    assert scores == sorted(scores, reverse=True)
    assert min(scores) >= matching._FALLBACK_MIN
    assert max(scores) <= matching._FALLBACK_MAX


def test_fallback_breakdown_shape_and_flagged_comments():
    bd = _fallback_breakdown(0, 1)
    for param in PARAMETERS:
        assert bd[param]["comment"] == _AI_UNAVAILABLE_COMMENT
        assert 1 <= bd[param]["score"] <= 10
    assert bd["overall"]["comment"] == _AI_UNAVAILABLE_COMMENT
    # overall is the Python-computed weighted average of the fallback scores
    assert bd["overall"]["score"] == compute_overall_score(
        {p: bd[p]["score"] for p in PARAMETERS}
    )


def test_fallback_never_reaches_highly_matching_tier():
    # Even the single best fallback candidate stays below the 90 boundary, so a
    # fallback score never fabricates a "Highly Matching" result.
    bd = _fallback_breakdown(0, 1)
    match_score = round(bd["overall"]["score"] * 10, 1)
    assert match_score <= 80.0
    assert assign_tier(match_score) != Tier.highly_matching


# ── _score_batch degrades on LLMUnavailableError ────────────────────────────

class _FakeProfile:
    def __init__(self):
        self.id = uuid.uuid4()
        self.candidate_id = uuid.uuid4()
        self.parsed_fields_json = {"skills": ["Python"]}
        self.aspects_json = {}
        self.resume_text = "resume text"


@pytest.mark.asyncio
async def test_score_batch_falls_back_when_llm_unavailable(monkeypatch):
    async def _down(*a, **k):
        raise matching.llm_router.LLMUnavailableError("all providers exhausted")

    monkeypatch.setattr(matching.llm_router, "chat_completion", _down)

    batch = [_FakeProfile(), _FakeProfile()]
    rank_by_id = {p.id: i for i, p in enumerate(batch)}
    result = await matching._score_batch(
        session=None, jd_text="JD", batch=batch, rank_by_id=rank_by_id, total=len(batch)
    )
    # Every profile still gets a (fallback) breakdown — the batch never aborts.
    assert set(result) == {p.id for p in batch}
    for pid, bd in result.items():
        assert bd["overall"]["comment"] == _AI_UNAVAILABLE_COMMENT
        for param in PARAMETERS:
            assert bd[param]["comment"] == _AI_UNAVAILABLE_COMMENT
    # Rank ordering preserved: earlier profile scores >= later profile.
    assert (
        result[batch[0].id]["overall"]["score"]
        >= result[batch[1].id]["overall"]["score"]
    )


@pytest.mark.asyncio
async def test_score_batch_malformed_then_skipped(monkeypatch):
    # LLM returns junk on both the first pass and the corrective retry ->
    # the profile is skipped (absent from results) rather than crashing.
    async def _junk(*a, **k):
        return "not json at all"

    monkeypatch.setattr(matching.llm_router, "chat_completion", _junk)

    batch = [_FakeProfile()]
    rank_by_id = {batch[0].id: 0}
    result = await matching._score_batch(
        session=None, jd_text="JD", batch=batch, rank_by_id=rank_by_id, total=1
    )
    assert result == {}  # skipped, not crashed


def test_weights_still_locked():
    # Guard against accidental weight drift in this file's neighbourhood.
    assert WEIGHTS == {
        "skills_match": 0.35,
        "experience_relevance": 0.30,
        "role_alignment": 0.20,
        "education_fit": 0.15,
    }
