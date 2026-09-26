"""The ranked table's one key: the SQL expression and its Python twin agree.

`yukti.ranking.rank_score_sql` orders every page and `rank_score` is its pure
twin, used wherever a key is needed outside a query. Two implementations of
one rule are safe only while a test holds them together, so this evaluates the
REAL expression in Postgres over a grid of every input it reads (pre score,
Yukti status, report presence, overall, Must-have failure, tenant ratio) and
compares every cell with the twin.

Also pinned, because each is the property the design exists for:
* the Must-have cap is applied AFTER the blend: a failed Must-have never grades
  above Moderately Matching, whatever the resume said;
* the cap never manufactures a key: Postgres' `LEAST` ignores a NULL, and a
  candidate with no reading and no overall must stay keyless;
* the words: the grade word at inclusive-upward boundaries, the ratio as a
  word, and the header sentences, none of which carries a digit.
"""
from __future__ import annotations

import itertools

import pytest
import sqlalchemy as sa

from app.services import rating
from app.services.yukti import config, ranking
from tests.ranking_world import require_database, run, sessions

STATUSES = ("pending", "scored", "not_assessed", "legacy")
PRES = [None, *range(0, 101, 7), 82.3, 100]
OVERALLS = [None, *range(0, 101, 9), 100]
WEIGHTS = (0, 30, 70, 100)

#: (report present, overall, must_have_failed). A report can exist with no
#: overall (Miti's overall Not assessed), and a report that failed a Must-have
#: with no overall and no reading is the case the NULL guard exists for.
REPORTS = [(False, None, None)] + [
    (True, overall, failed) for overall in OVERALLS for failed in (False, True)
]


def _grid_sql() -> str:
    return f"""
        SELECT l.yukti_pre_score AS pre, l.yukti_status AS status,
               rep.id IS NOT NULL AS has_report, rep.overall_score AS overall,
               rep.must_have_failed AS failed,
               t.yukti_assessment_weight_pct AS weight,
               {ranking.rank_score_sql()} AS key
          FROM (
                SELECT CAST(p AS real) AS yukti_pre_score, s AS yukti_status
                  FROM unnest(CAST(:pres AS double precision[])) AS p
                 CROSS JOIN unnest(CAST(:statuses AS text[])) AS s
               ) l
         CROSS JOIN (
                SELECT CASE WHEN present THEN gen_random_uuid() END AS id,
                       CAST(o AS integer) AS overall_score, f AS must_have_failed
                  FROM unnest(CAST(:present AS boolean[]),
                              CAST(:overalls AS integer[]),
                              CAST(:failed AS boolean[])) AS r(present, o, f)
               ) rep
         CROSS JOIN (
                SELECT CAST(w AS smallint) AS yukti_assessment_weight_pct
                  FROM unnest(CAST(:weights AS integer[])) AS w
               ) t
    """


def _evaluate() -> list[dict]:
    async def _read() -> list[dict]:
        async with sessions()() as session:
            async with session.begin():
                result = await session.execute(
                    sa.text(_grid_sql()),
                    {
                        "pres": PRES,
                        "statuses": list(STATUSES),
                        "present": [r[0] for r in REPORTS],
                        "overalls": [r[1] for r in REPORTS],
                        "failed": [r[2] for r in REPORTS],
                        "weights": list(WEIGHTS),
                    },
                )
                return [dict(row) for row in result.mappings().all()]

    return run(_read())


@pytest.fixture(scope="module")
def grid() -> list[dict]:
    require_database()
    return _evaluate()


def test_the_grid_covers_every_combination(grid) -> None:
    assert len(grid) == len(PRES) * len(STATUSES) * len(REPORTS) * len(WEIGHTS)


def test_the_sql_and_the_twin_agree_on_every_cell(grid) -> None:
    mismatches = []
    for cell in grid:
        twin = ranking.rank_score(
            cell["pre"],
            cell["status"],
            cell["overall"] if cell["has_report"] else None,
            cell["failed"],
            cell["weight"],
        )
        key = cell["key"]
        if (twin is None) != (key is None) or (
            twin is not None and abs(twin - key) > 1e-9
        ):
            mismatches.append((cell, twin))
    assert not mismatches, mismatches[:5]


def test_a_failed_must_have_never_grades_above_moderately_matching(grid) -> None:
    graded = [
        ranking.grade_word(cell["key"])
        for cell in grid
        if cell["has_report"] and cell["failed"] and cell["key"] is not None
    ]
    assert graded, "the grid produced no capped cell, so this sweep proves nothing"
    assert set(graded) <= {rating.GRADE_MODERATELY, rating.GRADE_NOT}


def test_the_cap_never_gives_a_key_to_a_candidate_who_has_none(grid) -> None:
    keyless = [
        cell for cell in grid
        if cell["status"] not in ranking.RANKED_STATUSES
        and (not cell["has_report"] or cell["overall"] is None)
    ]
    assert keyless
    assert all(cell["key"] is None for cell in keyless)


def test_the_cap_is_applied_after_the_blend() -> None:
    """Overall 74 with a failed Must-have, a perfect resume and the default
    70/30 ratio: the blend is 81.8, and the cap brings it back to the ceiling,
    which grades Moderately Matching. Applied inside the blend (to the overall
    alone) it would have come out Matching."""
    key = ranking.rank_score(100.0, "scored", 74, True, 70)
    assert key == ranking.must_have_ceiling()
    assert ranking.grade_word(key) == rating.GRADE_MODERATELY
    uncapped = ranking.rank_score(100.0, "scored", 74, False, 70)
    assert uncapped == pytest.approx(81.8)
    assert ranking.grade_word(uncapped) == rating.GRADE_MATCHING


def test_a_candidate_below_the_ceiling_stays_below_it() -> None:
    """A `min`, never an assignment: the cap must not promote the weakest."""
    assert ranking.rank_score(20.0, "scored", 30, True, 70) == pytest.approx(27.0)


def test_the_ceiling_is_the_one_runbook_number() -> None:
    from app.services.miti import caps

    assert ranking.must_have_ceiling() == caps.band_ceiling(
        caps.BAND_CONSIDER_WITH_RESERVATIONS
    )
    assert rating.grade_for_percent(ranking.must_have_ceiling()) == rating.GRADE_MODERATELY


def test_the_ranked_statuses_are_the_configs_words() -> None:
    assert set(ranking.RANKED_STATUSES) == {config.STATUS_SCORED, config.STATUS_LEGACY}
    assert set(STATUSES) == set(config.STATUSES)


def test_the_expression_refuses_an_alias_that_is_not_an_identifier() -> None:
    with pytest.raises(ValueError):
        ranking.rank_score_sql(link="l; DROP TABLE jobs")


def test_the_total_order_ends_in_arrival_then_id() -> None:
    assert ranking.order_by_sql().endswith(
        "DESC NULLS LAST, l.created_at ASC, l.id ASC"
    )


@pytest.mark.parametrize(
    "score, word",
    [
        (90.0, rating.GRADE_HIGHLY),
        (89.99999999999999, rating.GRADE_HIGHLY),
        (75.0, rating.GRADE_MATCHING),
        (74.9, rating.GRADE_MODERATELY),
        (60.0, rating.GRADE_MODERATELY),
        (59.9, rating.GRADE_NOT),
        (None, None),
    ],
)
def test_the_grade_word_is_inclusive_upward(score, word) -> None:
    assert ranking.grade_word(score) == word


@pytest.mark.parametrize(
    "pct, word",
    [(100, "entirely"), (99, "mostly"), (70, "mostly"), (60, "mostly"),
     (59, "about half"), (41, "about half"), (40, "partly"), (1, "partly"),
     (0, "not at all")],
)
def test_the_ratio_reaches_a_recruiter_only_as_a_word(pct, word) -> None:
    assert ranking.weight_word(pct) == word


def test_the_header_sentences_carry_no_digit_and_no_em_dash() -> None:
    sentences = {
        ranking.header_sentence(has_assessed=has, weight_pct=pct)
        for has, pct in itertools.product((False, True), (0, 30, 50, 70, 100))
    }
    assert ranking.HEADER_RESUME_ONLY in sentences
    for sentence in sentences:
        assert not any(char.isdigit() for char in sentence), sentence
        assert chr(8212) not in sentence
