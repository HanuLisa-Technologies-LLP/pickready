"""The three Runbook controls that cap a band, and the one that closes the hole.

    RPN-PHIL-001 section 12.1  a named Must-have fails its minimum score
    RPN-PHIL-001 section 12.2  a dimension breaches its Layer 1 floor
    RPN-PHIL-001 section 14.1  a Must-have has no evidence above E1

Until 2026-08-29 the product implemented the first and called it "the Must-have
hard cap", citing a section number the Runbook does not have. The rule itself
was a correct synthesis of 12.1, 12.2 and 10.1/10.5/10.8; it was also a correct
SUBSET, and the two it left out are the ones that catch the candidates a
score-based control cannot see.

THE CASE THIS FILE EXISTS FOR is `test_a_fabricated_must_have_...` below.
Section 10.2 puts evidence strength in both the numerator and the denominator
of a competency score, so a competency resting on ONE claim scores exactly its
rubric level whatever the evidence tier -- the Runbook says so itself: "a
dazzling claim with weak evidence and a modest claim with strong evidence can
land in the same place, and that is the intended behaviour." A fabricated
Must-have on one E0 resume bullet therefore grades well and trips no
score-based cap. Section 14.1 reads the TIER instead, which is why it catches
what the score cannot.

The property-based tests state the invariants over generated inputs rather than
examples, because "the band can never exceed the cap" is a claim about every
set of item grades and "delivered <= ceiling" is a claim about a continuous
range of composites and multipliers.
"""
from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services import rating
from app.services.evidence import tiers as evidence_tiers
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)
from app.services.miti import aggregation, caps
from app.services.miti.dimensions import BANDS, DimensionResult

_BAND_NAMES = tuple(name for name, _ in BANDS)


def _result(dimension: str, band: str) -> DimensionResult:
    return DimensionResult(dimension=dimension, band=band, evidence_refs=("e1",))


def _results(band: str = "solid") -> list[DimensionResult]:
    return [
        _result(DIM_VERIFIED_COMPETENCE, band),
        _result(DIM_TRACK_RECORD, band),
        _result(DIM_ROLE_FIT, band),
        _result(DIM_AUTHENTICITY, band),
        _result(DIM_TRAJECTORY, band),
    ]


def _evidence(*tiers: str, groups: int = 2) -> aggregation.MustHaveEvidence:
    return aggregation.MustHaveEvidence(tiers=tuple(tiers), independence_groups=groups)


# -- THE CEILING ------------------------------------------------------------


def test_the_ceiling_is_read_from_the_runbook_and_is_seventy_one() -> None:
    """Q7 recommendation 2: reconcile the number deliberately, not by accident.

    "Consider with reservations" tops out at 71 in section 10.8, and "cannot be
    Ready to Pick" resolves to the same place because Ready to Pick begins at
    72. The product's own `rating.MODERATELY_CEILING` is 74, which is the top
    of "Moderately Matching" on a four-grade scale. THE TWO ARE THE SAME BAND
    AND DIFFERENT NUMBERS, so the assertion is on both: the number the Runbook
    states, and the label it lands in on the scale a client reads.
    """
    ceiling = caps.band_ceiling(caps.BAND_CONSIDER_WITH_RESERVATIONS)
    assert ceiling == 71
    assert caps._ceiling_below(caps.BAND_READY_TO_PICK) == 71
    assert rating.grade_for_percent(ceiling) == rating.GRADE_MODERATELY
    # The product's number is three points higher, and is NOT what the Runbook
    # states. Asserted so that anybody who "fixes" one to match the other has
    # to decide which document they are following.
    assert rating.MODERATELY_CEILING == 74
    assert ceiling < rating.MODERATELY_CEILING


def test_an_unknown_band_or_effect_raises_rather_than_defaulting() -> None:
    """A substituted ceiling is a number nobody chose deciding whether a
    candidate may be delivered as Ready to Pick."""
    with pytest.raises(caps.CapDataError):
        caps.band_ceiling("Extremely Ready to Pick")
    with pytest.raises(caps.CapDataError):
        caps._ceiling_for_effect("Cannot exceed a band nobody wrote down")


# -- THE CASE A SCORE-BASED CAP CANNOT SEE ----------------------------------


def test_a_fabricated_must_have_on_one_e0_bullet_grades_matching_uncapped() -> None:
    """BEFORE. The section 12.1 control, alone, has nothing to bind against.

    This is the AI-generated-resume case. The competency rests on a single
    weakest-tier self-assertion; section 10.2's arithmetic hands back the
    rubric level unchanged; the item grades well; the composite grades
    Matching. Every score-based control passes it, and that is not a bug in
    them -- it is what a score CAN see.
    """
    grades = {"Distributed systems ownership": rating.GRADE_MATCHING}
    assert caps.competency_threshold_caps(grades=grades) == []

    uncapped = aggregation.aggregate(_results("solid"))
    assert uncapped.overall_grade == rating.GRADE_MATCHING
    assert uncapped.applied_caps == []


def test_a_fabricated_must_have_on_one_e0_bullet_is_capped_by_section_14_1() -> None:
    """AFTER. The tier-based control reads what the score cannot.

    Same candidate, same rubric level, same item grade. The only thing that
    changed is that the evaluation now knows the Must-have rests on one E0
    self-assertion, and section 14.1 says such a competency is reported
    Unassessed and the candidate cannot be Ready to Pick.
    """
    name = "Distributed systems ownership"
    capped = aggregation.aggregate(
        _results("solid"),
        competency_categories={name: aggregation.CATEGORY_MUST_HAVE},
        must_have_grades={name: rating.GRADE_MATCHING},
        must_have_evidence={name: _evidence(evidence_tiers.E0, groups=1)},
    )

    assert capped.overall_grade == rating.GRADE_MODERATELY
    assert capped.delivered_score == 71
    assert capped.unassessed_must_haves == [name]
    assert [cap.control for cap in capped.applied_caps] == [
        caps.CONTROL_UNASSESSED_MUST_HAVE
    ]
    assert capped.applied_caps[0].citation == "RPN-PHIL-001 section 14.1"
    # Reported, not hidden. Section 14.1 asks for the competency to be named as
    # Unassessed, and a name is not a number.
    assert capped.client_projection()["unassessed_must_haves"] == [name]


@pytest.mark.parametrize("tier", [evidence_tiers.E0, evidence_tiers.E1])
def test_every_tier_at_or_below_e1_trips_the_abstention_rule(tier: str) -> None:
    """"No evidence above E1" is the trigger, so E1 itself does not save it."""
    assert caps.unassessed_must_haves({"k": (tier,)}, ["k"]) == ["k"]


@pytest.mark.parametrize(
    "tier", [evidence_tiers.E2, evidence_tiers.E3, evidence_tiers.E4, evidence_tiers.E5]
)
def test_any_tier_above_e1_satisfies_the_abstention_rule(tier: str) -> None:
    assert caps.unassessed_must_haves({"k": (tier,)}, ["k"]) == []


def test_a_must_have_with_no_evidence_at_all_is_unassessed_too() -> None:
    """The two cases section 14.1 covers are the same finding: the scorecard
    called this essential and nothing in the record examines it."""
    assert caps.unassessed_must_haves({}, ["k"]) == ["k"]
    assert caps.unassessed_must_haves({"k": ()}, ["k"]) == ["k"]


def test_the_conversation_is_e3_so_a_probed_must_have_is_not_unassessed() -> None:
    """The rule must not fire on the ordinary case, or it is a cap on everyone.

    An answer given in the assessment conversation is a structured response
    under controlled conditions (section 6.1's E3), so a Must-have that was
    actually probed clears the abstention rule.
    """
    assert evidence_tiers.tier_for(source_type="answer", trust="observed") == "E3"
    assert caps.unassessed_must_haves({"k": ("E3",)}, ["k"]) == []


# -- SECTION 12.2, THE DIMENSION FLOORS -------------------------------------


def test_the_dimension_floors_fire_on_d1_d3_and_d4() -> None:
    """Section 12.2 states them as binding and the product implemented none.

    The trigger selects genuinely different candidates from section 12.1's: a
    candidate can be weak on Verified Competence in aggregate while every
    individual Must-have item scrapes a passing grade, and before today that
    candidate was delivered above "Consider with reservations".
    """
    fired = caps.dimension_floor_caps(
        {
            DIM_VERIFIED_COMPETENCE: 30.0,
            DIM_ROLE_FIT: 35.0,
            DIM_AUTHENTICITY: 44.0,
            DIM_TRACK_RECORD: 90.0,
        }
    )
    assert {cap.subject for cap in fired} == {
        DIM_VERIFIED_COMPETENCE,
        DIM_ROLE_FIT,
        DIM_AUTHENTICITY,
    }
    assert {cap.ceiling for cap in fired} == {71}
    assert {cap.citation for cap in fired} == {"RPN-PHIL-001 section 12.2"}


def test_an_unjudged_dimension_is_not_a_floor_breach() -> None:
    """Insufficient evidence is not negative evidence. A dimension excluded
    from the composite has not scored below its floor; it has not scored."""
    assert caps.dimension_floor_caps({DIM_TRACK_RECORD: 90.0}) == []


def test_the_d4_hold_is_a_routing_decision_and_not_a_ceiling() -> None:
    """Section 12.2's D4 floor of 25 is "HOLD, mandatory human review before any
    delivery", which is not a band.

    Modelling it as a ceiling would deliver the candidate anyway with a lower
    number, and modelling it as a fifth grade would put an integrity outcome on
    the scale a client reads.
    """
    assert caps.hold_reason({DIM_AUTHENTICITY: 20.0}) is not None
    assert caps.hold_reason({DIM_AUTHENTICITY: 25.0}) is None

    # THAT DAY ARRIVED ON 2026-09-02, and this assertion is the record of it.
    # The note that stood here said the four internal bands floored D4 at 40, so
    # nothing an evaluator could say reached the HOLD threshold of 25, and that
    # the branch was implemented against "the day a numeric D4 arrives from a
    # richer evaluator". What actually arrived was simpler: the section 9.x
    # rubric the evaluator is already shown has SIX rows, the scale had four
    # words, and the two rows with no word were the 45-59 band and the 0-24 HOLD
    # band. Adding them made the control reachable without moving a number. See
    # RUNBOOK_OPEN_QUESTIONS.md Q24.
    assert min(score for _band, score in BANDS) < 25
    assert caps.hold_reason({DIM_AUTHENTICITY: 40.0}) is None
    # The D4 floor of 45 still fires on "absent", which is the control that was
    # doing the work in the meantime and still does.
    assert [cap.subject for cap in caps.dimension_floor_caps({DIM_AUTHENTICITY: 40.0})] == [
        DIM_AUTHENTICITY
    ]
    assert all(
        cap.subject != DIM_AUTHENTICITY or cap.ceiling == 71
        for cap in caps.dimension_floor_caps({DIM_AUTHENTICITY: 20.0})
    )


# -- COMPOSITION: THE MINIMUM OF WHICHEVER FIRED ----------------------------


def test_the_delivered_band_is_the_minimum_of_the_ceilings_that_fired() -> None:
    """RUNBOOK_OPEN_QUESTIONS.md Q7, option C. The Runbook states three
    controls and never states how they compose; the minimum is the only reading
    under which no stated rule is quietly ignored."""
    assert caps.lowest_ceiling([]) is None
    fired = [
        caps.BandCap("a", "s", "x", "r", 84),
        caps.BandCap("b", "s", "y", "r", 71),
        caps.BandCap("c", "s", "z", "r", 90),
    ]
    assert caps.lowest_ceiling(fired) == 71
    assert caps.apply(100.0, fired) == 71.0


def test_all_three_controls_can_fire_at_once_and_are_all_recorded() -> None:
    """A cap that fired silently is indistinguishable from a candidate who
    simply scored there, so every control that bound is kept, not just the one
    that decided the number."""
    name = "Structural analysis"
    out = aggregation.aggregate(
        [
            _result(DIM_VERIFIED_COMPETENCE, "absent"),
            _result(DIM_TRACK_RECORD, "strong"),
            _result(DIM_ROLE_FIT, "strong"),
            _result(DIM_AUTHENTICITY, "strong"),
            _result(DIM_TRAJECTORY, "strong"),
        ],
        competency_categories={name: aggregation.CATEGORY_MUST_HAVE},
        must_have_grades={name: rating.GRADE_NOT},
        must_have_evidence={name: _evidence(evidence_tiers.E0)},
    )
    assert {cap.control for cap in out.applied_caps} == set(caps.CONTROLS)


def test_a_declared_threshold_beats_the_grade_as_the_minimum() -> None:
    """Section 12.1 says "minimum score on a named competency". Where the
    frozen matrix declares that number, it is the number; where it does not,
    the product's published floor for an essential criterion is."""
    name = "Load path reasoning"
    breached = caps.competency_threshold_caps(
        grades={name: rating.GRADE_MATCHING},
        scores={name: 80.0},
        thresholds={name: 85.0},
    )
    assert [cap.subject for cap in breached] == [name]
    met = caps.competency_threshold_caps(
        grades={name: rating.GRADE_MATCHING},
        scores={name: 86.0},
        thresholds={name: 85.0},
    )
    assert met == []


# -- THE REGRESSION THAT WAS FOUND ONCE ------------------------------------


def test_the_category_comes_from_the_item_and_never_from_the_dimension() -> None:
    """THE PREVIOUSLY-FOUND DEFECT, as an explicit regression case.

    Keying the composite on a dimension-to-category table produced an EMPTY
    Must-have grade for a job whose essentials all sat on one dimension, and
    the cap had nothing to bind against. Here the Must-have sits on Track
    Record, which the fallback table maps to Nice-to-have. The item's own
    category must win, and the cap must still fire.
    """
    name = "Delivered a migration end to end"
    assert aggregation.DIMENSION_TO_CATEGORY[DIM_TRACK_RECORD] == (
        aggregation.CATEGORY_NICE_TO_HAVE
    )
    out = aggregation.aggregate(
        [
            DimensionResult(
                dimension=DIM_TRACK_RECORD,
                band="strong",
                evidence_refs=("e1",),
                per_competency={name: "strong"},
            )
        ],
        competency_categories={name: aggregation.CATEGORY_MUST_HAVE},
        must_have_grades={name: rating.GRADE_NOT},
        must_have_evidence={name: _evidence(evidence_tiers.E3)},
    )
    assert out.category_grades[aggregation.CATEGORY_MUST_HAVE] != ""
    assert out.category_scores[aggregation.CATEGORY_MUST_HAVE] > 0
    assert out.must_have_cap_applied
    assert out.overall_grade == rating.GRADE_MODERATELY


# -- PROPERTIES -------------------------------------------------------------

_CEILINGS = st.integers(min_value=0, max_value=100)
_CAPS = st.lists(
    _CEILINGS.map(lambda c: caps.BandCap("control", "cite", "subject", "why", c)),
    max_size=6,
)


@given(score=st.floats(min_value=0.0, max_value=200.0), fired=_CAPS)
def test_apply_never_exceeds_a_ceiling_and_never_raises_a_score(score, fired) -> None:
    """The whole of what a cap must guarantee, in two lines.

    The second is the subtle one. A cap that SET the score would promote the
    weakest candidates into the band it exists to keep the strong ones out of,
    so `apply` must never return more than it was given.
    """
    out = caps.apply(score, fired)
    assert out <= score + 1e-9
    for cap in fired:
        assert out <= cap.ceiling


@given(
    composite=st.floats(min_value=0.0, max_value=100.0),
    multiplier=st.floats(min_value=0.0, max_value=1.0005),
    ceiling=_CEILINGS,
)
def test_cap_last_holds_the_ceiling_for_every_multiplier(
    composite, multiplier, ceiling
) -> None:
    """Q7's ordering invariant, property-tested rather than argued.

    `cap_last = min(x * m, C)` holds for every multiplier without exception.
    `cap_first = min(x, C) * m` breaches the ceiling the moment m exceeds 1,
    and section 10.5's rounded slope reached 1.0005 at D4 = 75 before Runbook
    v1.2 replaced it with exact fractions. The upper bound of the multiplier
    range here is that historical value, deliberately.
    """
    fired = [caps.BandCap("control", "cite", "subject", "why", ceiling)]
    cap_last = caps.apply(composite * multiplier, fired)
    assert cap_last <= ceiling + 1e-9


@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=75)
@given(
    grades=st.dictionaries(
        st.text(min_size=1, max_size=12), st.sampled_from(rating.GRADES),
        min_size=1, max_size=5,
    ),
    band=st.sampled_from(_BAND_NAMES),
)
def test_a_failing_must_have_can_never_exceed_the_cap(grades, band) -> None:
    """spec-doc6 section 4.4: "for any generated set of item grades, if any
    Must-have item is below its threshold, the composite band can never exceed
    the cap."

    Stated over generated grades because it is a claim about every set, and the
    thing being defended is that no combination of strong dimensions, strong
    other items or a favourable multiplier can lift the delivered band.
    """
    out = aggregation.aggregate(
        _results(band),
        competency_categories={
            name: aggregation.CATEGORY_MUST_HAVE for name in grades
        },
        must_have_grades=grades,
        must_have_evidence={name: _evidence(evidence_tiers.E3) for name in grades},
    )
    if any(grade == rating.GRADE_NOT for grade in grades.values()):
        ceiling = caps.band_ceiling(caps.BAND_CONSIDER_WITH_RESERVATIONS)
        assert out.delivered_score <= ceiling
        assert out.overall_grade in (rating.GRADE_MODERATELY, rating.GRADE_NOT)


# SAME SETTINGS AS THE PROPERTY TEST ABOVE, and for the same measured reason.
# One `aggregate` call over five competencies runs 100 to 580ms, against
# Hypothesis's default 200ms deadline, so this test straddles it: it passes on
# an idle machine and fails under any CPU contention, which on CI reads as a
# section 14.1 violation rather than as a slow example. Observed once here,
# while a coverage run held the cores.
#
# The invariant itself is sound. It was checked exhaustively over 11,154 tier
# assignments and over adversarial competency names, with no counterexample, so
# what is suppressed is a timing artefact and not a failing property. Keep
# `max_examples` at the default 100: the cost of this test is the aggregate
# call, not the example count.
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    tiers=st.dictionaries(
        st.text(min_size=1, max_size=12),
        st.lists(st.sampled_from(evidence_tiers.TIERS), max_size=4).map(tuple),
        min_size=1,
        max_size=5,
    )
)
def test_an_unassessed_must_have_can_never_be_delivered_as_ready_to_pick(
    tiers,
) -> None:
    """Section 14.1's invariant, over generated evidence sets."""
    out = aggregation.aggregate(
        _results("strong"),
        competency_categories={
            name: aggregation.CATEGORY_MUST_HAVE for name in tiers
        },
        must_have_grades={name: rating.GRADE_HIGHLY for name in tiers},
        must_have_evidence={
            name: _evidence(*found) for name, found in tiers.items()
        },
    )
    unassessed = any(
        not any(evidence_tiers.above_e1(tier) for tier in found)
        for found in tiers.values()
    )
    if unassessed:
        assert out.delivered_score <= caps._ceiling_below(caps.BAND_READY_TO_PICK)
        assert out.unassessed_must_haves


# -- DETERMINISM ------------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("none", 1.0), ("minor", 0.9), ("material", 0.7), ("critical", 0.4)],
)
def test_every_detector_severity_has_a_stated_penalty(severity, expected) -> None:
    """Section 10.7's consistency term is "1 - (weighted unresolved
    contradiction severity)" and never says what the weights are; section 6.5's
    table is the only severity weighting the document states, so the detector's
    four levels are aligned onto it top-down.

    `none` returns 1.0 because it is not a contradiction, and any level the
    table does not cover RAISES rather than costing nothing -- a new severity
    added to the detector must stop the build, not quietly stop mattering.
    """
    assert aggregation._consistency_term((severity,), 0) == pytest.approx(expected)
    with pytest.raises(caps.CapDataError):
        aggregation._consistency_term(("catastrophic",), 0)


def test_the_capped_aggregate_is_byte_identical_across_a_hundred_runs() -> None:
    """spec-doc6 section 4.4 asks for it explicitly, and the reason is that two
    runs producing different grades would make a rubric problem indistinguishable
    from noise."""
    name = "Kafka partition rebalancing"
    kwargs = dict(
        competency_categories={name: aggregation.CATEGORY_MUST_HAVE},
        must_have_grades={name: rating.GRADE_NOT},
        must_have_evidence={name: _evidence(evidence_tiers.E0, evidence_tiers.E3)},
        unresolved_contradictions=1,
    )
    first = json.dumps(aggregation.aggregate(_results("solid"), **kwargs).as_dict())
    for _ in range(100):
        assert (
            json.dumps(aggregation.aggregate(_results("solid"), **kwargs).as_dict())
            == first
        )
