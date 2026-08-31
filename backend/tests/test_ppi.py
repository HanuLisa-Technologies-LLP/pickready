"""PPI framework generation, per-candidate questions, and the four-grade scale."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import ppi, rating
from app.services import application_validation as av
from app.services.hiring import layers, scorecard, swot_quality, transformation


# ── The one rating scale (spec §10.2) ────────────────────────────────────────

def test_exactly_four_grades_best_to_worst() -> None:
    assert rating.GRADES == (
        "Highly Matching",
        "Matching",
        "Moderately Matching",
        "Not Matching",
    )


def test_bands_are_inclusive_upward() -> None:
    """CLAUDE.md rule 8: a score landing exactly on a boundary takes the
    HIGHER band. The cut-points are unchanged from the retired five-label
    scale, so a report written before this release regrades identically."""
    assert rating.grade_for_percent(90) == "Highly Matching"
    assert rating.grade_for_percent(89.9) == "Matching"
    assert rating.grade_for_percent(75) == "Matching"
    assert rating.grade_for_percent(74.9) == "Moderately Matching"
    assert rating.grade_for_percent(60) == "Moderately Matching"
    assert rating.grade_for_percent(59.9) == "Not Matching"
    assert rating.grade_for_percent(0) == "Not Matching"


def test_none_in_none_out_and_a_bool_is_not_a_score() -> None:
    assert rating.grade_for_percent(None) is None
    assert rating.grade_for_percent(True) is None
    assert rating.grade_for_percent("high") is None
    assert rating.grade_for_ten(None) is None
    assert rating.grade_for_ten(False) is None


def test_the_ten_point_scale_agrees_with_the_hundred_point_scale() -> None:
    for tenth in range(0, 101):
        assert rating.grade_for_ten(tenth / 10.0) == rating.grade_for_percent(tenth)


def test_band_index_is_a_radius_not_a_score() -> None:
    assert rating.band_index_for("Highly Matching") == 4
    assert rating.band_index_for("Not Matching") == 1
    # A report from an older build still draws.
    assert rating.band_index_for("Very High") == 1
    assert rating.band_index_for(None) == 1


# ── Culture is refused (spec §5) ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    ["Culture", "Culture fit", "CULTURAL ALIGNMENT", "Company culture", "cultural add"],
)
def test_culture_is_refused_in_any_casing(name) -> None:
    assert ppi.is_forbidden_competency(name)


@pytest.mark.parametrize("name", ["Agricultural domain knowledge", "Ownership", ""])
def test_a_legitimate_competency_is_not_caught(name) -> None:
    assert not ppi.is_forbidden_competency(name)


# ── The three aspects (spec §5) ──────────────────────────────────────────────

def test_the_matrix_has_three_aspects_in_report_order() -> None:
    assert ppi.CATEGORIES == ("must_have", "nice_to_have", "behavioural")
    assert ppi.CATEGORY_LABELS[ppi.CATEGORY_MUST_HAVE] == "Must-have"
    assert ppi.CATEGORY_LABELS[ppi.CATEGORY_NICE_TO_HAVE] == "Nice-to-have"


def test_the_retired_aspect_names_are_gone() -> None:
    """Must-have and Nice-to-have are RENAMES, not new aspects alongside the old
    ones. Two vocabularies would mean every read path had to accept either."""
    assert not hasattr(ppi, "CATEGORY_PRIMARY")
    assert not hasattr(ppi, "CATEGORY_SECONDARY")


# ── Matrix generation: Sutra's seven stages ──────────────────────────────────
#
# The single-pass generator these tests used to exercise is DELETED (spec-doc6
# D1, "delete on activation"), along with the deterministic JD-derived fallback
# that stood in for it during an outage. `hiring/scorecard.py` replaced both.
#
# The end-to-end path -- a real SWOT session, a real Company DNA artifact and a
# real matrix, through the HTTP API -- is `tests/test_job_setup_live.py`, which
# needs a database because the layers it composes are stored ones. What is
# tested HERE is the arithmetic and the refusals, which need neither.


def test_the_single_pass_generator_and_its_fallback_are_gone() -> None:
    """spec-doc6 D1: the old implementation is removed, not flagged off.

    Named symbols rather than a grep, because the failure this prevents is a
    partial revert: a `generate_framework` that came back would be a second way
    to produce criteria, and the two would disagree about provenance without
    anything failing.
    """
    for symbol in (
        "generate_framework",
        "_fallback_framework",
        "_ensure_every_aspect",
        "_normalise",
        "_maximum_total",
        "_framework_system_prompt",
        "load_swot",
    ):
        assert not hasattr(ppi, symbol), f"ppi.{symbol} came back"
    from app.prompts import registry

    assert "ppi_framework_system" not in registry.names()


def test_the_quadrant_mapping_is_section_18_1s() -> None:
    """§18.1's "What it produces" column, and there is only one copy of it.

    Bodha reads it to run §18.5's everything-is-must-have rule and Sutra reads
    it to categorise what it builds. Two copies would drift, and the drift would
    be silent: one module refusing an intake for a share the other module's own
    mapping does not produce.
    """
    assert swot_quality.QUADRANT_CATEGORY == {
        "weaknesses": ppi.CATEGORY_MUST_HAVE,
        "strengths": ppi.CATEGORY_NICE_TO_HAVE,
        "opportunities": ppi.CATEGORY_NICE_TO_HAVE,
        "threats": ppi.CATEGORY_BEHAVIOURAL,
    }
    assert scorecard.QUADRANT_CATEGORY is swot_quality.QUADRANT_CATEGORY


def test_a_team_strength_deprioritises_and_a_weakness_promotes() -> None:
    """§19.2's counterintuitive move, and §18.1's "highest-weighted items".

    A team strength REDUCES the weight of that competency because the hire does
    not need to supply it. A system that weighted everything the JD mentions
    "gets this exactly backwards and consistently selects candidates who
    duplicate existing strengths while leaving the real gap unfilled".

    The magnitudes are read off `layers.BOUNDS` rather than written down, so
    this asserts the DIRECTION and the fact that neither endpoint escapes the
    declared bound.
    """
    emphasis = scorecard._quadrant_emphasis()
    bound = layers.BOUNDS["competency_weight"]
    assert emphasis["weaknesses"] > 1.0 > emphasis["strengths"]
    assert emphasis["weaknesses"] == bound.high
    assert emphasis["strengths"] == bound.low
    for value in emphasis.values():
        assert bound.contains(value)


def _job(grade: str = "non_managerial") -> SimpleNamespace:
    """Just enough of a job for the pure helpers: a grade and an id."""
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Backend Engineer",
        department=None, assessment_grade=grade, jd_markdown="",
        role_classification=None,
        jd_json={"skills": ["Python", "PostgreSQL", "Kafka"]},
        framework_generated_at=None, framework_approved_at=None,
        question_target=None, swot_completed_at=None, correlation_id="job-test",
    )


def _item(name: str, category: str, weight: float) -> transformation.Item:
    return transformation.build_item(
        phrase=name,
        category=category,
        department="generic",
        seniority="non_managerial",
        observable_evidence=(
            "Has shipped a change to a live system and can reconstruct what "
            "they decided and why."
        ),
        role_emphasis={name: weight},
    )


def test_the_force_ranking_is_total_and_has_no_ties() -> None:
    """§20.3: "Rank the required competencies 1..n (max 6). No ties."

    Two competencies whose derived weights are identical must still get
    different ranks, because a rank two items share is not a ranking. The tie is
    broken deterministically by name, so the same matrix ranks the same way
    twice.
    """
    built = [
        _item("Alpha", ppi.CATEGORY_MUST_HAVE, 1.0),
        _item("Bravo", ppi.CATEGORY_MUST_HAVE, 1.0),
        _item("Charlie", ppi.CATEGORY_NICE_TO_HAVE, 1.0),
    ]
    ranking = scorecard._rank_and_normalise(built)
    ranks = [ranking[index][0] for index in range(len(built))]
    assert sorted(ranks) == [1, 2, 3]
    assert scorecard._rank_and_normalise(built) == ranking


def test_the_scored_weights_are_shares_that_sum_to_one() -> None:
    """§20.1's own scorecard sums to 1.00 (0.35+0.25+0.20+0.12+0.08).

    Normalising is what keeps the stored weight a SHARE rather than a raw
    product, and it is also what makes a Layer 2 or Layer 3 change observable:
    raising one competency's multiplier raises its share and lowers everyone
    else's.
    """
    built = [
        _item("Alpha", ppi.CATEGORY_MUST_HAVE, 2.0),
        _item("Bravo", ppi.CATEGORY_MUST_HAVE, 1.0),
        _item("Charlie", ppi.CATEGORY_BEHAVIOURAL, 1.0),
    ]
    ranking = scorecard._rank_and_normalise(built)
    scored = [
        ranking[index][1]
        for index, item in enumerate(built)
        if item.category in scorecard.SCORED_CATEGORIES
    ]
    assert abs(sum(scored) - 1.0) < 1e-9
    # Alpha carries twice Bravo's emphasis, so it must carry the larger share.
    assert ranking[0][1] > ranking[1][1]
    # Behavioural is normalised among itself: §20.1's scorecard has no
    # behavioural row, so it is not part of the scored ranking.
    assert ranking[2][0] is None
    assert abs(ranking[2][1] - 1.0) < 1e-9


def test_the_six_competency_ceiling_drops_the_lowest_and_says_so() -> None:
    """§20.2: "Maximum six. No exceptions."

    And the removal is LOUD. A matrix quietly shorter than the session it came
    from is how a criterion somebody cared about disappears with nobody
    noticing, so each drop is a rejection carrying the competency's name.
    """
    built = [
        (_item(f"Skill {index}", ppi.CATEGORY_MUST_HAVE, 2.0 - index * 0.1), "weaknesses")
        for index in range(9)
    ]
    rejections: list = []
    kept = scorecard._apply_ceilings(built, _job(), rejections)
    scored = [item for item, _q in kept if item.category in scorecard.SCORED_CATEGORIES]
    assert len(scored) == swot_quality.MAX_SCORECARD_COMPETENCIES
    assert len(rejections) == 3
    assert all("20.2" in row["reason"] for row in rejections)
    # The ones kept are the highest-weighted, not the first six seen.
    assert {item.name for item in scored} == {f"Skill {index}" for index in range(6)}


def test_a_row_that_never_ran_the_stages_is_not_a_matrix_item() -> None:
    """A row written by the retired generator has no dimension and no weight.

    None rather than a filled-in default: substituting values would present
    criteria nobody derived as though the pipeline had derived them, and G1
    would then pass on a job that has never been through setup.
    """
    row = SimpleNamespace(
        id=uuid.uuid4(), name="Python", category=ppi.CATEGORY_MUST_HAVE,
        description="", required_level=95, ordinal=1,
        dimension=None, observable_evidence=None, evidence_sources=None,
        assessment_method=None, weight=None, threshold_json=None,
        disqualifier=None, provenance_json=None, swot_origin=None,
        anchor_key=None, force_rank=None,
    )
    assert scorecard.item_from_row(row) is None


def test_the_provenance_a_hiring_manager_reads_carries_no_number() -> None:
    """spec-doc6 §4.3 asks for the traceability "in plain language".

    A hiring manager confirming "1.4850" is confirming that the arithmetic looks
    plausible; a hiring manager confirming "you said the last person never owned
    anything in production" is confirming the thing they actually said. The
    standing no-numbers rule and the usability requirement point the same way
    here.
    """
    item = scorecard.MatrixItem(
        competency_id=uuid.uuid4(),
        competency="Production incident ownership",
        category=ppi.CATEGORY_MUST_HAVE,
        dimension="verified_competence",
        observable_evidence="Has carried production on-call and can narrate an incident.",
        evidence_sources=("assessment_answer",),
        assessment_method="conversation",
        weight=0.35,
        threshold={"independence_required": 2},
        disqualifier=None,
        provenance={
            "terms": {
                "baseline_layer1": 1.2,
                "company_layer2": 1.1,
                "situation_layer3": 1.25,
                "role_layer3": 1.35,
            },
            "situation_key": "turnaround",
            "unreachable_sources": ["reference"],
        },
        swot_origin="The last person never owned anything in production.",
        anchor_key="delivery_ownership",
        force_rank=1,
        required_level="Highly Matching",
        ordinal=1,
    )
    lines = scorecard.plain_provenance(item)
    assert lines, "a derived item must be able to say where its weight came from"
    blob = " ".join(lines)
    assert not any(character.isdigit() for character in blob), blob
    # Every layer that moved the weight is accounted for by a sentence.
    assert "philosophy" in blob
    assert "Turnaround" in blob
    assert "never owned anything in production" in blob


def test_required_levels_never_offer_not_matching() -> None:
    """A job that requires nothing of an item would not list it."""
    assert "Not Matching" not in ppi.REQUIRED_LEVEL_SCORES
    assert ppi.required_level_score("Highly Matching") == 95
    assert ppi.required_level_score("nonsense") == ppi.DEFAULT_REQUIRED_LEVEL


# ── The save gate (spec §5.3) ────────────────────────────────────────────────

def _competency(category: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(category=category, name=name, is_active=True)


def _small_matrix() -> list[SimpleNamespace]:
    return [_competency(category, f"{category}-1") for category in ppi.CATEGORIES]


def test_a_three_item_matrix_can_be_saved() -> None:
    """One item per aspect is enough. Draft v4 removed the floor of five, and
    this is the assertion that the removal is real rather than aspirational."""
    ok, reason = ppi.matrix_is_complete(_small_matrix(), "non_managerial")
    assert ok and reason is None


def test_an_empty_aspect_blocks_the_save_and_says_which() -> None:
    rows = [row for row in _small_matrix() if row.category != ppi.CATEGORY_NICE_TO_HAVE]
    ok, reason = ppi.matrix_is_complete(rows, "non_managerial")
    assert not ok
    assert "Nice-to-have" in reason


def test_a_matrix_above_the_grade_ceiling_blocks_the_save_and_says_how_many() -> None:
    """Every item is probed at least once, so a matrix bigger than the grade
    allows questions would grade a candidate on criteria nobody asked them
    about. The refusal names the number to remove rather than truncating."""
    ceiling = ppi.max_questions("cxo")
    rows = _small_matrix() + [
        _competency(ppi.CATEGORY_MUST_HAVE, f"Extra {index}")
        for index in range(ceiling)
    ]
    ok, reason = ppi.matrix_is_complete(rows, "cxo")
    assert not ok
    assert str(len(rows) - ceiling) in reason
    # The same matrix is perfectly saveable at a grade that asks more questions.
    assert ppi.matrix_is_complete(rows, "non_managerial")[0] is (
        len(rows) <= ppi.max_questions("non_managerial")
    )


def test_a_hand_typed_culture_competency_blocks_the_save() -> None:
    """The Hiring Manager's Edit control can type anything, so the refusal is
    enforced at save as well as at generation."""
    rows = _small_matrix() + [_competency(ppi.CATEGORY_BEHAVIOURAL, "Culture fit")]
    ok, reason = ppi.matrix_is_complete(rows, "non_managerial")
    assert not ok
    assert "Culture" in reason


# ── Per-candidate questions (spec §5.6) ──────────────────────────────────────

def _matrix(per_aspect: int = 5) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id=uuid.uuid4(), category=category,
                        name=f"{category}-{index}", ordinal=index + 1)
        for category in ppi.CATEGORIES
        for index in range(per_aspect)
    ]


def test_allocation_probes_every_item_at_least_once() -> None:
    competencies = _matrix()
    plan = ppi._allocate(competencies, 20, "non_managerial")
    assert len(plan) == 20
    assert {row.name for row in plan} == {row.name for row in competencies}


def test_allocation_spends_the_remainder_on_the_most_weighted_aspect() -> None:
    """The typical split is illustrative and nothing enforces it, but it is
    what decides where a SPARE question goes: whichever aspect the client's
    table asks the most of."""
    competencies = _matrix()
    plan = ppi._allocate(competencies, 20, "non_managerial")  # 15 items, 5 spare
    extras = plan[15:]
    split = ppi.typical_split("non_managerial")
    heaviest = max(ppi.CATEGORIES, key=lambda category: split[category][1])
    assert all(row.category == heaviest for row in extras)


def test_seniority_and_stem_both_raise_the_question_count() -> None:
    # Master Directive Part 3 section 6 inverted the old direction: seniority
    # now ADDS questions, and a STEM job probes deeper than a non-STEM one at
    # every grade.
    assert ppi.max_questions("cxo") >= ppi.max_questions("non_managerial")
    assert ppi.min_questions("cxo") >= ppi.min_questions("non_managerial")
    for grade in ("non_managerial", "managerial", "leadership", "cxo"):
        assert ppi.min_questions(grade, "STEM") > ppi.min_questions(grade)
        assert ppi.max_questions(grade, "STEM") > ppi.max_questions(grade)


# ── Mandatory application fields (spec §7) ───────────────────────────────────

def _complete() -> dict:
    return {
        "current_ctc": "18 LPA",
        "expected_ctc": "26 LPA",
        "notice_period": "60 days",
        "joining_date": "2026-09-01",
        "document_readiness": "All documents ready",
        "role_interest": "I want to work on larger distributed systems at scale.",
    }


def test_a_complete_submission_has_nothing_missing() -> None:
    assert av.missing_fields(_complete()) == []


@pytest.mark.parametrize("key", av.MANDATORY_KEYS)
def test_every_field_is_mandatory(key) -> None:
    payload = _complete()
    payload.pop(key)
    assert av.missing_fields(payload)


def test_a_one_word_answer_on_interest_is_not_enough() -> None:
    payload = {**_complete(), "role_interest": "money"}
    assert av.missing_fields(payload)


def test_unknown_keys_are_dropped_rather_than_stored() -> None:
    """This blob renders straight into the report, so it accepts exactly the
    fields the form defines and nothing a caller invents."""
    stored = av.normalise({**_complete(), "internal_note": "<script>", "score": 9})
    assert set(stored) == set(av.MANDATORY_KEYS)


def test_the_open_text_field_reaches_the_report_verbatim() -> None:
    words = "I have followed this team's work on streaming for two years."
    stored = av.normalise({**_complete(), "role_interest": words})
    assert stored["role_interest"] == words
