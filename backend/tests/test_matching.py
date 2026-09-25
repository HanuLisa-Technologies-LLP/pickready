"""The matching run's pure parts, and the legacy read side it still carries.

The run itself (retrieval by candidate, the resume the link carries, sourced
databank links, the skills gate, degradation) is exercised over a real
database in `tests/test_yukti_run_matching_db.py`. Here:

* the lexical stage's query builder: ontology-expanded and OR-ed
  (spec-doc6 §4.6), and sanitised before it reaches `to_tsquery`;
* the pool is keyed by PERSON: `Found` carries a candidate, and the run's
  constants a recruiter reads are server words with no number;
* the LEGACY projections of the frozen `match_breakdown_json`, which still
  have readers until Phase 2 WP-C and WP-E move them and WP-F deletes the
  section. They are tested while they are live, and deleted with it.
"""
from types import SimpleNamespace

import pytest

from app.services import matching
from app.services.matching import (
    COMMENT_MAX_WORDS,
    COMMENT_MIN_WORDS,
    PARAMETERS,
    RANKING_COMMENT_KEYS,
    enforce_breakdown_comments,
    enforce_word_range,
    ranking_payload,
    word_count,
)

EM_DASH = chr(8212)
EN_DASH = chr(8211)


def _in_range(text: str) -> bool:
    return COMMENT_MIN_WORDS <= word_count(text) <= COMMENT_MAX_WORDS


# A 28-word comment, inside the 25-30 word contract.
GOOD_COMMENT = (
    "Candidate demonstrates strong practical command of the core technologies this "
    "role requires, with directly comparable delivery experience, though a few "
    "secondary tools remain unevidenced in the submitted resume."
)


def _stored_breakdown() -> dict:
    """A breakdown shaped exactly as the retired matcher stored one."""
    return {
        **{key: {"score": 7, "comment": GOOD_COMMENT} for key in PARAMETERS},
        "overall": {"score": 7.0, "comment": GOOD_COMMENT},
        "scoring_mode": "llm",
    }


# ── The lexical stage's query ────────────────────────────────────────────────


def test_the_keyword_query_ors_its_terms_so_one_hit_is_enough():
    job = SimpleNamespace(title="Data Engineer", jd_json={"skills": ["Kafka", "Airflow"]})
    query = matching._tsquery(matching._keyword_query_terms(job))
    assert " | " in query and " & " not in query
    for word in ("kafka", "airflow", "data", "engineer"):
        assert word in query.split(" | ")


def test_the_keyword_query_is_sanitised_before_it_reaches_postgres():
    assert matching._tsquery(["ci/cd", "gd&t", "!!!"]) == "ci | cd | gd"
    assert matching._tsquery([]) == ""


def test_the_jd_embedding_text_is_yuktis_and_never_reads_level_or_reportees():
    """`matching._jd_text` read `jobs.level` and `jd_json.reportees`; the run
    embeds `yukti.inputs.jd_text` now, which reads neither."""
    assert not hasattr(matching, "_jd_text")
    assert not hasattr(matching, "_strip_compensation")


def test_the_scoring_half_of_the_retired_matcher_is_gone():
    for name in (
        "_llm_score",
        "_score_batch",
        "_extract_valid",
        "prescreen_breakdown",
        "_customer_success_patterns",
        "publish_ai_scores",
        "_profile_summary",
        "_RERANK_BATCH_SIZE",
        "SCORING_MODE_PRESCREEN",
    ):
        assert not hasattr(matching, name), name


def test_the_sentences_a_recruiter_reads_carry_no_number_and_no_em_dash():
    sentences = (
        matching.SKILLS_NOT_SAVED,
        matching.JOB_NOT_OPEN,
        matching.ALREADY_RUNNING,
        matching.EMBEDDING_DEGRADED,
        matching.DATABANK_REMARK,
    )
    for sentence in sentences:
        assert not any(ch.isdigit() for ch in sentence), sentence
        assert EM_DASH not in sentence, sentence


# ── LEGACY: word_count / enforce_word_range ─────────────────────────────────

# ── word_count / enforce_word_range (pure helpers) ──────────────────────────

def test_word_count_ignores_punctuation_only_tokens():
    assert word_count("one two three") == 3
    assert word_count(f"one {EM_DASH} two {EN_DASH} three") == 3  # bare dashes are not words
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


def test_enforce_breakdown_comments_fixes_every_field():
    bd = {
        **{p: {"score": 5, "comment": "short"} for p in PARAMETERS},
        "overall": {"score": 5.0, "comment": ""},
    }
    enforce_breakdown_comments(bd)
    for block in bd.values():
        assert _in_range(block["comment"])



# ── LEGACY: ranking_payload, the comments-only shape the UI consumes ────────

def test_ranking_payload_keys_are_exactly_the_contract():
    assert set(RANKING_COMMENT_KEYS.values()) == {
        "skills_match_comment",
        "experience_comment",
        "role_alignment_comment",
        "education_comment",
        "overall_comment",
    }


def test_ranking_payload_ready_has_all_five_in_range_comments():
    bd = _stored_breakdown()
    payload = ranking_payload(bd)
    assert payload["ranking_status"] == "ready"
    for key in RANKING_COMMENT_KEYS.values():
        assert _in_range(payload[key]), (key, payload[key])


def test_ranking_payload_not_scored_is_explicit_not_silent():
    for empty in (None, {}):
        payload = ranking_payload(empty)
        assert payload["ranking_status"] == "not_scored"
        # every key is still present: the UI branches on status, not on KeyError
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
    """Stored numeric scores are internal ranking data (claude.md): the review
    screen gets the comments and the scoring_mode, never a number."""
    from app.services.matching import client_breakdown

    stored = _stored_breakdown()
    out = client_breakdown(stored)
    assert out is not None
    assert out["scoring_mode"] == "llm"
    for field in (*matching.PARAMETERS, "overall"):
        assert "score" not in out[field]
        assert out[field]["comment"] == stored[field]["comment"]
    # Nothing numeric survives anywhere in the projection.
    assert not [
        v for block in out.values() if isinstance(block, dict)
        for v in block.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    # The stored breakdown itself is not mutated; audit data stays intact.
    assert stored["overall"]["score"] > 0


def test_client_breakdown_passes_through_empty_and_null():
    from app.services.matching import client_breakdown

    assert client_breakdown(None) is None
    assert client_breakdown({}) is None
    assert client_breakdown({"scoring_mode": "llm"}) == {"scoring_mode": "llm"}
