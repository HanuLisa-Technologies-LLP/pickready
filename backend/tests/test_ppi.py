"""PPI framework generation, per-candidate questions, and the four-grade scale."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import ppi, rating
from app.services import application_validation as av
from app.services import skills
from app.services.hiring import scorecard


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


# ── The retired matrix generators ───────────────────────────────────────────
#
# Two generations of matrix builder are DELETED: the single-pass generator
# (spec-doc6 D1) and the seven-stage compiler that replaced it (Vivekium
# release, D1). Skills are drafted by `hiring/sutra` and saved by
# `services/skills`; their tests are `test_job_skills_*.py`. What stays tested
# HERE is the read half the scoring path still uses and the vocabulary.


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
        # The Vivekium release: the matrix save check and the A2A matrix
        # artifact went with the Tatva matrix editor.
        "matrix_is_complete",
        "framework_is_complete",
        "publish_tatva_matrix",
        "published_matrix",
        "verify_matrix_for_consumer",
    ):
        assert not hasattr(ppi, symbol), f"ppi.{symbol} came back"
    for symbol in ("compile_matrix", "freeze", "_enrich_reviewed_rows", "_name_unanchored"):
        assert not hasattr(scorecard, symbol), f"scorecard.{symbol} came back"
    from app.prompts import registry

    assert "ppi_framework_system" not in registry.names()
    assert "sutra_competency_naming" not in registry.names()


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
    assert "Turnaround" in blob
    assert "never owned anything in production" in blob


def test_required_levels_never_offer_not_matching() -> None:
    """A job that requires nothing of an item would not list it."""
    assert "Not Matching" not in ppi.REQUIRED_LEVEL_SCORES
    assert ppi.required_level_score("Highly Matching") == 95
    assert ppi.required_level_score("nonsense") == ppi.DEFAULT_REQUIRED_LEVEL


# ── The save gate: the Skills step's rule (D1) ──────────────────────────────

def _competency(category: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(category=category, name=name, is_active=True)


def _small_matrix() -> list[SimpleNamespace]:
    return [_competency(category, f"{category}-1") for category in ppi.CATEGORIES]


def test_one_skill_per_bucket_can_be_saved() -> None:
    """One per bucket is enough; there is no floor of five."""
    assert skills.validate_for_save(_small_matrix()) == []


def test_nice_to_have_may_be_empty_but_must_have_and_behavioural_may_not() -> None:
    no_nice = [row for row in _small_matrix() if row.category != ppi.CATEGORY_NICE_TO_HAVE]
    assert skills.validate_for_save(no_nice) == []
    problems = skills.validate_for_save([_competency(ppi.CATEGORY_NICE_TO_HAVE, "x")])
    assert any("Must-have" in problem for problem in problems)
    assert any("Behavioural" in problem for problem in problems)


def test_a_sixth_skill_in_a_bucket_blocks_the_save_and_says_how_many() -> None:
    rows = _small_matrix() + [
        _competency(ppi.CATEGORY_MUST_HAVE, f"Extra {index}") for index in range(5)
    ]
    problems = skills.validate_for_save(rows)
    assert len(problems) == 1
    assert "Must-have holds six skills" in problems[0]
    assert "Remove one" in problems[0]
    assert not any(character.isdigit() for character in problems[0]), problems[0]


def test_a_hand_typed_culture_competency_blocks_the_save() -> None:
    """The team can type anything, so the refusal is enforced at save."""
    rows = _small_matrix() + [_competency(ppi.CATEGORY_BEHAVIOURAL, "Culture fit")]
    problems = skills.validate_for_save(rows)
    assert problems and "Culture" in problems[0]


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
