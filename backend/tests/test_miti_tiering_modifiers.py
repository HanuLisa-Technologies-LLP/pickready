"""The four things that make one piece of evidence weigh more than another.

EVERY ONE OF THEM DISCOUNTS RATHER THAN DISCARDS, and that is the shape of the
whole module. Team attribution, a mismatched scale, an old claim: each is real
evidence of something, weaker evidence of what is being asked. Zeroing any of
them would let one property of a sentence delete a candidate's account of their
own work.

THE UNKNOWNS ALL FALL THE CONSERVATIVE WAY, WHICH IS NOT THE SAME AS THE HARSH
WAY.

  * An unknown SOURCE TYPE is the candidate's own group. Assuming a new source
    is independent would manufacture corroboration; assuming it is not costs a
    little confidence and cannot invent any.
  * An unknown SUBJECT is discounted like an ambiguous one, because a sentence
    nobody can attribute is not a sentence attributable to the candidate.
  * An unknown DATE does not decay at all. Penalising evidence for missing a
    timestamp would penalise a candidate for the platform's own gap in
    provenance, and "we do not know when this was" is not "this was long ago".

Two of those three make the evidence weigh less and one makes it weigh the
same, which is what "conservative" means here: never in the direction that
invents support, never in the direction that punishes an absence.

Pure arithmetic. No database, no network, no model.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.evidence import ledger
from app.services.miti import tiering
from app.services.miti.claims import SUBJECT_AMBIGUOUS, SUBJECT_SELF, SUBJECT_TEAM


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ── Which group a source belongs to ──────────────────────────────────────────


def test_a_resume_is_the_candidate_speaking() -> None:
    assert tiering.independence_group_for(ledger.SOURCE_RESUME) == tiering.GROUP_CANDIDATE


def test_an_unknown_source_type_is_assumed_to_be_the_candidate() -> None:
    """The conservative default. Assuming a new source type is independent
    would manufacture corroboration, which is the failure this grouping exists
    to prevent."""
    assert tiering.independence_group_for("a_source_added_next_year") == (
        tiering.GROUP_CANDIDATE
    )
    assert tiering.independence_group_for("") == tiering.GROUP_CANDIDATE


# ── Specificity ──────────────────────────────────────────────────────────────


def test_an_answer_too_short_to_check_is_discounted_hardest() -> None:
    """Word count matters independently of specifics: a three-word answer has
    nothing to check even if it contains a number."""
    assert tiering.specificity_modifier(has_specifics=True, word_count=3) == 0.6
    assert tiering.specificity_modifier(has_specifics=False, word_count=3) == 0.6


def test_a_long_specific_answer_weighs_most() -> None:
    assert tiering.specificity_modifier(has_specifics=True, word_count=30) > 1.0


def test_a_short_specific_answer_still_gains() -> None:
    assert 1.0 < tiering.specificity_modifier(has_specifics=True, word_count=15) < (
        tiering.specificity_modifier(has_specifics=True, word_count=30)
    )


def test_long_unspecific_prose_is_discounted_rather_than_neutral() -> None:
    """An answer with plenty of room for a specific and none in it is weaker
    than a short answer that never had room."""
    assert tiering.specificity_modifier(has_specifics=False, word_count=50) == 0.9


def test_a_middling_unspecific_answer_is_neutral() -> None:
    assert tiering.specificity_modifier(has_specifics=False, word_count=20) == 1.0


def test_specificity_never_zeroes_anything() -> None:
    for specifics in (True, False):
        for words in (0, 1, 7, 8, 24, 25, 39, 40, 200):
            assert tiering.specificity_modifier(
                has_specifics=specifics, word_count=words
            ) > 0.0


# ── Attribution ──────────────────────────────────────────────────────────────


def test_a_first_person_claim_weighs_most() -> None:
    """"We migrated to Kafka" and "I migrated us to Kafka" are both true of the
    same person and say very different things about them."""
    assert tiering.attribution_modifier(SUBJECT_SELF) > tiering.attribution_modifier(
        SUBJECT_AMBIGUOUS
    )


def test_team_attribution_is_discounted_and_not_discarded() -> None:
    """Being on the team that did it is real evidence of proximity, exposure
    and probably contribution. It is weaker evidence of personal capability,
    which is what the discount says."""
    team = tiering.attribution_modifier(SUBJECT_TEAM)
    assert 0.0 < team < tiering.attribution_modifier(SUBJECT_AMBIGUOUS)


def test_an_unknown_subject_is_treated_like_an_ambiguous_one() -> None:
    """A sentence nobody can attribute is not a sentence attributable to the
    candidate, and it is not nothing either."""
    assert tiering.attribution_modifier("something_else") == (
        tiering.attribution_modifier(SUBJECT_AMBIGUOUS)
    )


# ── Scale ────────────────────────────────────────────────────────────────────


def test_evidence_at_the_role_s_own_scale_gains_a_little() -> None:
    assert tiering.scale_modifier(
        role_seniority="non_managerial", evidence_scale="individual"
    ) > 1.0


def test_evidence_from_a_bigger_stage_is_neutral_and_not_a_bonus() -> None:
    """Someone who ran a five-hundred-person function applying for a fifty
    person one has real evidence and a different question hanging over them.
    That question is Role and Context Fit, which is a dimension with a rubric,
    not a multiplier."""
    assert tiering.scale_modifier(
        role_seniority="non_managerial", evidence_scale="organisation"
    ) == 1.0


def test_evidence_from_a_smaller_stage_is_discounted_by_how_far() -> None:
    one_short = tiering.scale_modifier(
        role_seniority="managerial", evidence_scale="individual"
    )
    two_short = tiering.scale_modifier(
        role_seniority="leadership", evidence_scale="individual"
    )
    assert two_short < one_short < 1.0


def test_no_stated_scale_is_neutral() -> None:
    """Most evidence carries no scale at all. Discounting it would penalise
    every ordinary claim for a field the parser did not fill."""
    assert tiering.scale_modifier(
        role_seniority="cxo", evidence_scale=None
    ) == 1.0
    assert tiering.scale_modifier(role_seniority="cxo", evidence_scale="") == 1.0


def test_an_unrecognised_scale_is_read_as_matching_rather_than_short() -> None:
    """A scale word this table does not know must not be read as "smaller than
    the role", which would silently discount every claim carrying a new word."""
    assert tiering.scale_modifier(
        role_seniority="managerial", evidence_scale="squad"
    ) == tiering.scale_modifier(
        role_seniority="managerial", evidence_scale="team"
    )


def test_an_unrecognised_seniority_is_read_as_the_bottom_band() -> None:
    assert tiering.scale_modifier(
        role_seniority="principal_staff_fellow", evidence_scale="individual"
    ) == tiering.scale_modifier(
        role_seniority="non_managerial", evidence_scale="individual"
    )


# ── Decay ────────────────────────────────────────────────────────────────────


def test_an_undated_claim_does_not_decay() -> None:
    """"We do not know when this was" is not "this was long ago", and the gap
    is the platform's rather than the candidate's."""
    assert tiering.decay_modifier(None, now=NOW) == 1.0


def test_recent_evidence_is_undiscounted() -> None:
    assert tiering.decay_modifier(NOW - timedelta(days=30), now=NOW) == 1.0


def test_decay_starts_only_after_the_grace_period() -> None:
    assert tiering.decay_modifier(
        NOW - timedelta(days=tiering._DECAY_START_DAYS), now=NOW
    ) == 1.0
    assert tiering.decay_modifier(
        NOW - timedelta(days=tiering._DECAY_START_DAYS + 1), now=NOW
    ) < 1.0


def test_decay_bottoms_out_rather_than_reaching_zero() -> None:
    """It still happened. Zeroing old evidence would erase a career."""
    for years in (6, 10, 30):
        assert tiering.decay_modifier(
            NOW - timedelta(days=365 * years), now=NOW
        ) == tiering._DECAY_FLOOR


def test_decay_is_monotone_between_the_two_marks() -> None:
    ages = range(tiering._DECAY_START_DAYS, tiering._DECAY_FLOOR_DAYS + 1, 60)
    values = [
        tiering.decay_modifier(NOW - timedelta(days=age), now=NOW) for age in ages
    ]
    assert values == sorted(values, reverse=True)


def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing() -> None:
    """Stored timestamps arrive both ways. Raising here would take down the
    tiering of a whole resume over a column's timezone setting."""
    naive = datetime(2026, 8, 1)
    assert tiering.decay_modifier(naive, now=NOW) == 1.0


def test_a_future_timestamp_does_not_become_a_bonus() -> None:
    """Clock skew and a candidate's typo both produce one. Reading it as
    negative age would let a wrong date weigh more than a right one."""
    assert tiering.decay_modifier(NOW + timedelta(days=90), now=NOW) == 1.0


def test_a_client_horizon_replaces_the_platform_curve() -> None:
    """A client who said "it has to be current" is answering exactly this
    question, and their answer binds."""
    age = NOW - timedelta(days=400)
    assert tiering.decay_modifier(age, now=NOW, max_age_days=365) == (
        tiering._DECAY_FLOOR
    )
    assert tiering.decay_modifier(age, now=NOW, max_age_days=500) == 1.0


def test_the_client_horizon_also_floors_rather_than_zeroes() -> None:
    assert tiering.decay_modifier(
        NOW - timedelta(days=3650), now=NOW, max_age_days=365
    ) == tiering._DECAY_FLOOR


# ── The whole weighing, with every term kept ─────────────────────────────────


def _tiered(**overrides) -> tiering.TieredEvidence:
    fields = dict(
        ref="ref:1",
        trust=ledger.TRUST_INFERRED,
        source_type=ledger.SOURCE_RESUME,
        subject=SUBJECT_SELF,
        text="Rebuilt the ingestion pipeline and cut the nightly batch runtime.",
        has_specifics=True,
        as_of=NOW - timedelta(days=30),
        role_seniority="non_managerial",
        evidence_scale="individual",
        now=NOW,
    )
    fields.update(overrides)
    return tiering.tier_evidence(**fields)


def test_the_weight_is_the_product_of_every_term() -> None:
    """Kept separately so "why does this weigh what it weighs" is answered by
    reading the row rather than by rerunning the pipeline."""
    weighed = _tiered()
    assert weighed.weight == pytest.approx(
        weighed.tier_base
        * weighed.specificity
        * weighed.attribution
        * weighed.scale
        * weighed.decay
    )


def test_an_unknown_trust_level_takes_the_declared_default() -> None:
    """Not zero, and not the top. A trust word this table does not know is a
    contract drift, and weighing it at the inferred baseline is the reading
    that neither invents support nor deletes the evidence."""
    assert _tiered(trust="something_new").tier_base == tiering.DEFAULT_TIER_BASE


def test_the_serialised_form_keeps_every_modifier_beside_the_weight() -> None:
    payload = _tiered().as_dict()
    assert set(payload["modifiers"]) == {
        "tier_base",
        "specificity",
        "attribution",
        "scale",
        "decay",
    }
    assert payload["weight"] > 0


# ── The bounded refinement hook ──────────────────────────────────────────────


def test_no_proposal_leaves_the_deterministic_answer_standing() -> None:
    """An outage costs the refinement and nothing else."""
    original = _tiered()
    assert tiering.refine_specificity(original, None) is original


@pytest.mark.parametrize("junk", ["not a number", {}, [], object()])
def test_an_unusable_proposal_leaves_it_standing_too(junk) -> None:
    original = _tiered()
    assert tiering.refine_specificity(original, junk) is original


def test_a_proposal_inside_the_bounds_is_taken() -> None:
    refined = tiering.refine_specificity(_tiered(), 1.0)
    assert refined.specificity == pytest.approx(1.0)


def test_a_proposal_outside_the_bounds_is_clamped_rather_than_refused() -> None:
    """The model may adjust WITHIN bounds. Letting it past them would make the
    deterministic tier advisory, and refusing outright would throw away a
    proposal that was merely enthusiastic."""
    assert tiering.refine_specificity(_tiered(), 99.0).specificity == (
        tiering._SPECIFICITY_MAX
    )
    assert tiering.refine_specificity(_tiered(), -5.0).specificity == (
        tiering._SPECIFICITY_MIN
    )


def test_refining_changes_nothing_but_specificity() -> None:
    """A hook that could move attribution or decay would be a second, unbounded
    weighting path."""
    original = _tiered()
    refined = tiering.refine_specificity(original, 1.0)
    for term in ("ref", "trust", "independence_group", "tier_base", "attribution", "scale", "decay"):
        assert getattr(refined, term) == getattr(original, term), term
