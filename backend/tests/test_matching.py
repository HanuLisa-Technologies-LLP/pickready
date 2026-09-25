"""The matching run's pure parts, and the legacy read side it still carries.

The run itself (retrieval by candidate, the resume the link carries, sourced
databank links, the skills gate, degradation) is exercised over a real
database in `tests/test_yukti_run_matching_db.py`. Here:

* the lexical stage's query builder: ontology-expanded and OR-ed
  (spec-doc6 §4.6), and sanitised before it reaches `to_tsquery`;
* the pool is keyed by PERSON: `Found` carries a candidate, and the run's
  constants a recruiter reads are server words with no number;
* the LEGACY read side of the frozen `match_breakdown_json` is GONE (Phase 2
  WP-F): nothing projects the breakdown any more, and the names are pinned
  absent so a helpful re-add fails here.
"""
from types import SimpleNamespace


from app.services import matching

EM_DASH = chr(8212)


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


def test_the_legacy_read_side_is_gone():
    """The projections of the frozen breakdown, the 25-30 word padding and the
    four-parameter mean had their last readers moved onto the Yukti columns
    (WP-C, WP-E) and were deleted (WP-F). `WEIGHTS` is pinned absent here too,
    moved from the deleted `test_scoring.py`: a reintroduced weighting table is
    how "35% role-fit weighting" reached a client."""
    for name in (
        "ranking_payload",
        "client_breakdown",
        "matching_label",
        "MATCHING_LABELS",
        "enforce_word_range",
        "enforce_breakdown_comments",
        "word_count",
        "compute_overall_score",
        "PARAMETERS",
        "RANKING_COMMENT_KEYS",
        "RANKING_LABEL_KEYS",
        "COMMENT_MIN_WORDS",
        "COMMENT_MAX_WORDS",
        "_PAD_CLAUSES",
        "WEIGHTS",
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
