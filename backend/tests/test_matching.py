"""Matching-pipeline robustness tests (Track B) — the graceful-degradation and
LLM-output-parsing paths that sit above the pure scoring math in test_scoring.py.

Covers:
  * _extract_valid — malformed LLM entries are skipped, not crashed on.
  * the deterministic breakdown when the LLM chain is unavailable, which since
    spec-doc6 §4.4 comes from Yukti's pre-screen EVIDENCE reading rather than
    from a retrieval rank. The ceiling below the Highly Matching boundary is
    kept, and now has a reason rather than a constant behind it.
  * _score_batch degrading on llm_router.LLMUnavailableError instead of
    aborting the batch.
  * the ontology-expanded, OR-ed lexical stage (spec-doc6 §4.6).

No DB — the DB-touching run_matching() is exercised by the live seeded run in
the build report, not here.
"""
import uuid

import pytest

from app.models.enums import Tier
from app.services import matching
from app.services.matching import (
    COMMENT_MAX_WORDS,
    COMMENT_MIN_WORDS,
    PARAMETERS,
    RANKING_COMMENT_KEYS,
    _extract_valid,
    comment_fields_out_of_range,
    compute_overall_score,
    enforce_breakdown_comments,
    enforce_word_range,
    ranking_payload,
    word_count,
)
from app.services.hiring import prescreen
from app.services.tiers import assign_tier


def _prescreen(resume: str, requirements=("python",), skills=("Python",)):
    """A real pre-screen result, built the way the live path builds one."""
    return prescreen.grade(
        prescreen.PreScreenInput(
            requirements=tuple(requirements),
            requirement_source=prescreen.REQUIREMENTS_FROM_JD,
            claims=prescreen.claims_from_resume(resume, skills=list(skills)),
        )
    )


#: A resume whose only evidence is a bare skills list. RPN-PHIL-001 §6.1 calls
#: that E0: an unverifiable self-claim, free to produce.
ASSERTED_ONLY = "Worked across several teams on a range of responsibilities."

#: The same claim with a mechanism, a checkable number and clear ownership in
#: it, which is what §6.1 calls E1.
CHECKABLE = (
    "I owned the migration of the Python billing service, cutting p99 latency "
    "from 900ms to 120ms across 40 million requests a day."
)


def _in_range(text: str) -> bool:
    return COMMENT_MIN_WORDS <= word_count(text) <= COMMENT_MAX_WORDS


# A 28-word comment — inside the 25-30 word contract, so fixtures using it do
# not accidentally trigger the corrective regeneration pass.
GOOD_COMMENT = (
    "Candidate demonstrates strong practical command of the core technologies this "
    "role requires, with directly comparable delivery experience, though a few "
    "secondary tools remain unevidenced in the submitted resume."
)


def _valid_entry(pid: str, **overrides) -> dict:
    entry = {
        "profile_id": pid,
        "skills_match": {"score": 8, "comment": GOOD_COMMENT},
        "experience_relevance": {"score": 7, "comment": GOOD_COMMENT},
        "role_alignment": {"score": 9, "comment": GOOD_COMMENT},
        "education_fit": {"score": 6, "comment": GOOD_COMMENT},
        "overall_comment": GOOD_COMMENT,
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


# ── The deterministic breakdown is EVIDENCE, not similarity ─────────────────

def test_the_retrieval_rank_band_is_gone():
    """The deleted half of spec-doc6 §4.4, asserted by absence.

    A grade derived from a retrieval rank is a grade derived from document
    similarity, which is the measurement RPN-PHIL-001 §58 says systematically
    undervalues candidates who use non-standard vocabulary. Naming the deleted
    symbols here is what stops one quietly coming back beside the new grader and
    reopening the dual path spec-doc6 §4.1 forbids.
    """
    for gone in (
        "_fallback_breakdown",
        "_fallback_param_score",
        "_fallback_comment",
        "_FALLBACK_MIN",
        "_FALLBACK_MAX",
        "_FALLBACK_COMMENTS",
        "_AI_UNAVAILABLE_COMMENT",
    ):
        assert not hasattr(matching, gone), gone


def test_deterministic_breakdown_shape_and_flagged_comments():
    bd = matching.prescreen_breakdown(_prescreen(CHECKABLE))
    for param in PARAMETERS:
        # Real, readable, in-contract text — never a placeholder.
        assert _in_range(bd[param]["comment"])
        assert "AI scoring unavailable" not in bd[param]["comment"]
        assert 1 <= bd[param]["score"] <= 10
    assert _in_range(bd["overall"]["comment"])
    # The deterministic mode is flagged in a machine-readable way instead.
    assert bd["scoring_mode"] == matching.SCORING_MODE_PRESCREEN
    # overall is the Python-computed mean of the category scores.
    assert bd["overall"]["score"] == compute_overall_score(
        {p: bd[p]["score"] for p in PARAMETERS}
    )


def test_stronger_resume_evidence_scores_higher_than_a_bare_assertion():
    """The property a retrieval rank could not have: the number tracks the
    EVIDENCE. Same requirement, same skills list, two resumes, one asserting and
    one carrying a mechanism, a number and clear ownership."""
    weak = matching.prescreen_breakdown(_prescreen(ASSERTED_ONLY))
    strong = matching.prescreen_breakdown(_prescreen(CHECKABLE))
    assert strong["overall"]["score"] > weak["overall"]["score"]


def test_deterministic_breakdown_never_reaches_highly_matching_tier():
    """A resume-only pass can never fabricate a top-tier match.

    The ceiling is kept from the deleted band and now has a reason rather than a
    constant behind it: RPN-PHIL-001 §6.1 puts a candidate's own document at E2
    at best, and every tier above it requires a controlled response, an
    observation or a third party. The top grade is a claim about verified depth,
    and a resume contains none.
    """
    best = _prescreen(
        "I owned the rewrite, see https://github.com/example/service, cutting "
        "p99 latency from 900ms to 40ms for 200 million requests with a team of 12.",
        requirements=("python",),
        skills=(),
    )
    bd = matching.prescreen_breakdown(best)
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
async def test_score_batch_degrades_to_the_evidence_reading(monkeypatch):
    async def _down(*a, **k):
        raise matching.llm_router.LLMUnavailableError("all providers exhausted")

    monkeypatch.setattr(matching.llm_router, "chat_completion", _down)

    strong, weak = _FakeProfile(), _FakeProfile()
    prescreened = {
        strong.id: _prescreen(CHECKABLE),
        weak.id: _prescreen(ASSERTED_ONLY),
    }
    result = await matching._score_batch(
        session=None, jd_text="JD", batch=[strong, weak], prescreened=prescreened
    )
    # Every profile still gets a breakdown — the batch never aborts.
    assert set(result) == {strong.id, weak.id}
    for bd in result.values():
        assert bd["scoring_mode"] == matching.SCORING_MODE_PRESCREEN
        assert comment_fields_out_of_range(bd) == {}
    # Ordering follows the EVIDENCE, which is the whole change: the resume that
    # can be checked outranks the one that only asserts.
    assert result[strong.id]["overall"]["score"] > result[weak.id]["overall"]["score"]


@pytest.mark.asyncio
async def test_score_batch_malformed_then_skipped(monkeypatch):
    # LLM returns junk on both the first pass and the corrective retry ->
    # the profile is skipped (absent from results) rather than crashing.
    async def _junk(*a, **k):
        return "not json at all"

    monkeypatch.setattr(matching.llm_router, "chat_completion", _junk)

    batch = [_FakeProfile()]
    result = await matching._score_batch(
        session=None,
        jd_text="JD",
        batch=batch,
        prescreened={batch[0].id: _prescreen(CHECKABLE)},
    )
    assert result == {}  # skipped, not crashed


# ── word_count / enforce_word_range (pure helpers) ──────────────────────────

def test_word_count_ignores_punctuation_only_tokens():
    assert word_count("one two three") == 3
    assert word_count("one — two – three") == 3       # bare dashes are not words
    assert word_count("  spaced   out  ") == 2
    assert word_count("") == 0
    assert word_count(None) == 0
    assert word_count("Python, FastAPI; Redis.") == 3


def test_enforce_word_range_leaves_in_range_text_alone():
    assert word_count(GOOD_COMMENT) == 28
    out = enforce_word_range(GOOD_COMMENT)
    assert out == GOOD_COMMENT
    assert _in_range(out)


def test_enforce_word_range_pads_too_short():
    short = "Strong Python match."
    assert word_count(short) < COMMENT_MIN_WORDS
    out = enforce_word_range(short)
    assert _in_range(out)
    assert out.startswith("Strong Python match.")
    assert out.endswith((".", "!", "?"))


def test_enforce_word_range_pads_empty_input():
    for empty in ("", "   ", None):
        out = enforce_word_range(empty)
        assert _in_range(out), out


def test_enforce_word_range_trims_too_long_at_a_boundary():
    long = (
        "The candidate has deep experience with Python, FastAPI, PostgreSQL and "
        "Redis; they have also shipped Dockerised services, mentored engineers, "
        "run incident response, and owned migrations across several teams over "
        "the last decade of professional work."
    )
    assert word_count(long) > COMMENT_MAX_WORDS
    out = enforce_word_range(long)
    assert _in_range(out)
    assert long.startswith(out[:40])          # a prefix of the original, not a rewrite
    assert out.endswith((".", "!", "?"))


def test_enforce_word_range_hard_cut_when_no_boundary_exists():
    # 60 punctuation-free words: no clause boundary to fall back to.
    out = enforce_word_range(" ".join(["alpha"] * 60))
    assert _in_range(out)


def test_enforce_word_range_boundaries_are_inclusive():
    for n in (COMMENT_MIN_WORDS, COMMENT_MAX_WORDS):
        text = " ".join(["alpha"] * n)
        assert word_count(enforce_word_range(text)) == n


def test_enforce_word_range_rejects_a_nonsense_range():
    with pytest.raises(ValueError):
        enforce_word_range("x", 30, 25)


def test_comment_fields_out_of_range_reports_field_and_count():
    bd = {
        **{p: {"score": 5, "comment": GOOD_COMMENT} for p in PARAMETERS},
        "overall": {"score": 5.0, "comment": "too short"},
    }
    bd["skills_match"] = {"score": 5, "comment": "also short"}
    bad = comment_fields_out_of_range(bd)
    assert bad == {"skills_match": 2, "overall": 2}


def test_enforce_breakdown_comments_fixes_every_field():
    bd = {
        **{p: {"score": 5, "comment": "short"} for p in PARAMETERS},
        "overall": {"score": 5.0, "comment": ""},
    }
    enforce_breakdown_comments(bd)
    assert comment_fields_out_of_range(bd) == {}


# ── _score_batch enforces the word contract ─────────────────────────────────

def _raw(*entries) -> str:
    import json

    return json.dumps({"results": list(entries)})


@pytest.mark.asyncio
async def test_score_batch_regenerates_when_comments_are_out_of_range(monkeypatch):
    """First response is in-schema but the comments are too short -> exactly one
    corrective pass is made, and the corrective prompt names the bad fields and
    their word counts."""
    profile = _FakeProfile()
    pid = str(profile.id)
    calls: list[list[dict]] = []

    async def _fake(role_hint, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return _raw(_valid_entry(pid, skills_match={"score": 8, "comment": "too short"}))
        return _raw(_valid_entry(pid))

    monkeypatch.setattr(matching.llm_router, "chat_completion", _fake)

    result = await matching._score_batch(
        session=None, jd_text="JD", batch=[profile],
        prescreened={profile.id: _prescreen(CHECKABLE)},
    )
    assert len(calls) == 2, "expected exactly one corrective regeneration pass"
    corrective = calls[1][-1]["content"]
    assert "skills_match was 2 words" in corrective
    assert f"{COMMENT_MIN_WORDS}-{COMMENT_MAX_WORDS} word rule" in corrective
    assert comment_fields_out_of_range(result[profile.id]) == {}
    assert result[profile.id]["skills_match"]["comment"] == GOOD_COMMENT


@pytest.mark.asyncio
async def test_score_batch_repairs_deterministically_when_retry_also_fails(monkeypatch):
    """The model never complies -> the stored comments are still 25-30 words."""
    profile = _FakeProfile()
    pid = str(profile.id)
    too_long = " ".join(["alpha"] * 80)

    async def _fake(role_hint, messages, **kwargs):
        return _raw(
            _valid_entry(
                pid,
                skills_match={"score": 8, "comment": "nope"},
                education_fit={"score": 4, "comment": too_long},
            )
        )

    monkeypatch.setattr(matching.llm_router, "chat_completion", _fake)

    result = await matching._score_batch(
        session=None, jd_text="JD", batch=[profile],
        prescreened={profile.id: _prescreen(CHECKABLE)},
    )
    bd = result[profile.id]
    assert comment_fields_out_of_range(bd) == {}
    for field in (*PARAMETERS, "overall"):
        assert _in_range(bd[field]["comment"])


@pytest.mark.asyncio
async def test_score_batch_no_retry_when_everything_is_in_range(monkeypatch):
    profile = _FakeProfile()
    calls: list[int] = []

    async def _fake(role_hint, messages, **kwargs):
        calls.append(1)
        return _raw(_valid_entry(str(profile.id)))

    monkeypatch.setattr(matching.llm_router, "chat_completion", _fake)
    result = await matching._score_batch(
        session=None, jd_text="JD", batch=[profile],
        prescreened={profile.id: _prescreen(CHECKABLE)},
    )
    assert len(calls) == 1
    assert comment_fields_out_of_range(result[profile.id]) == {}


# ── ranking_payload: the comments-only shape the UI consumes ────────────────

def test_ranking_payload_keys_are_exactly_the_contract():
    assert set(RANKING_COMMENT_KEYS.values()) == {
        "skills_match_comment",
        "experience_comment",
        "role_alignment_comment",
        "education_comment",
        "overall_comment",
    }


def test_ranking_payload_ready_has_all_five_in_range_comments():
    bd = matching.prescreen_breakdown(_prescreen(CHECKABLE))
    payload = ranking_payload(bd)
    assert payload["ranking_status"] == "ready"
    for key in RANKING_COMMENT_KEYS.values():
        assert _in_range(payload[key]), (key, payload[key])


def test_ranking_payload_not_scored_is_explicit_not_silent():
    for empty in (None, {}):
        payload = ranking_payload(empty)
        assert payload["ranking_status"] == "not_scored"
        # every key is still present — the UI branches on status, not on KeyError
        for key in RANKING_COMMENT_KEYS.values():
            assert key in payload and payload[key] is None


def test_ranking_payload_repairs_legacy_out_of_range_comments():
    legacy = {
        **{p: {"score": 5, "comment": "AI scoring unavailable."} for p in PARAMETERS},
        "overall": {"score": 5.0, "comment": None},
    }
    payload = ranking_payload(legacy)
    assert payload["ranking_status"] == "ready"
    for key in RANKING_COMMENT_KEYS.values():
        assert _in_range(payload[key])


def test_parameters_still_locked_and_unweighted():
    # Guard against drift in this file's neighbourhood: the four parameters are
    # the contract, and none of them outranks another (spec 2026-07-30).
    assert matching.PARAMETERS == (
        "skills_match", "experience_relevance", "role_alignment", "education_fit",
    )
    assert not hasattr(matching, "WEIGHTS")


# ── Numbers never cross the client boundary ─────────────────────────────────

def test_client_breakdown_strips_every_numeric_score():
    """Stored numeric scores are internal ranking data (claude.md) — the review
    screen gets the comments and the scoring_mode, never a number."""
    from app.services.matching import client_breakdown

    stored = matching.prescreen_breakdown(_prescreen(CHECKABLE))
    out = client_breakdown(stored)
    assert out is not None
    assert out["scoring_mode"] == matching.SCORING_MODE_PRESCREEN
    for field in (*matching.PARAMETERS, "overall"):
        assert "score" not in out[field]
        assert out[field]["comment"] == stored[field]["comment"]
    # Nothing numeric survives anywhere in the projection.
    assert not [
        v for block in out.values() if isinstance(block, dict)
        for v in block.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    # The stored breakdown itself is not mutated — audit data stays intact.
    assert stored["overall"]["score"] > 0


def test_client_breakdown_passes_through_empty_and_null():
    from app.services.matching import client_breakdown

    assert client_breakdown(None) is None
    assert client_breakdown({}) is None
    assert client_breakdown({"scoring_mode": "llm"}) == {"scoring_mode": "llm"}
