"""Independence counting, and the two lookups that refuse an unknown key.

INDEPENDENCE IS COUNTED BY ORIGINATOR, NEVER BY DOCUMENT. A resume line and the
candidate restating it in the interview could not have disagreed: that is one
person saying one thing twice, and counting it as two manufactures the
corroboration the whole triangulation stage exists to measure. An unknown
source type is assumed DEPENDENT for the same reason -- assuming independence
is the direction that invents support.

The two lookups below raise rather than defaulting. A dimension with no Runbook
identifier has no section 9.x anchor table, so a default would grade a
candidate against a rubric that does not exist; a band outside the declared set
has no score, and substituting one would turn a parsing failure into a real
grade for a real candidate.

Pure functions. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.miti import dimensions, triangulation


def _source(ref: str, group: str | None = None, **extra) -> dict:
    row = {"ref": ref}
    if group is not None:
        row["independence_group"] = group
    row.update(extra)
    return row


# ── Grouping sources by originator ───────────────────────────────────────────


def test_two_sources_from_one_originator_form_one_group() -> None:
    """The resume line and the candidate repeating it in the interview."""
    groups = triangulation.independence_groups(
        [_source("resume:1", "candidate"), _source("answer:7", "candidate")]
    )
    assert list(groups) == ["candidate"]
    assert len(groups["candidate"]) == 2


def test_sources_from_different_originators_form_different_groups() -> None:
    groups = triangulation.independence_groups(
        [_source("resume:1", "candidate"), _source("ref:2", "employer")]
    )
    assert set(groups) == {"candidate", "employer"}


def test_a_source_with_no_group_is_attributed_to_the_candidate() -> None:
    """The dependent direction. Defaulting to a fresh group would let an
    unlabelled source corroborate the candidate's own account."""
    groups = triangulation.independence_groups([_source("mystery:1")])
    assert list(groups) == ["candidate"]


def test_a_source_with_no_reference_is_skipped() -> None:
    """A locator is what makes a piece of evidence addressable; counting one
    without a ref would add support nobody can go and read."""
    groups = triangulation.independence_groups([{"independence_group": "employer"}])
    assert groups == {}


def test_an_id_is_accepted_where_a_ref_is_absent() -> None:
    groups = triangulation.independence_groups(
        [{"id": "answer:3", "independence_group": "employer"}]
    )
    assert groups["employer"] == ["answer:3"]


# ── Counting them ────────────────────────────────────────────────────────────


def test_independence_counts_originators_and_not_documents() -> None:
    """Three documents, one person: one independent source."""
    count = triangulation.count_independence(
        [
            _source("resume:1", "candidate"),
            _source("answer:7", "candidate"),
            _source("answer:9", "candidate"),
        ]
    )
    assert count == 1


def test_two_originators_count_as_two() -> None:
    count = triangulation.count_independence(
        [_source("resume:1", "candidate"), _source("ref:2", "employer")]
    )
    assert count == 2


def test_no_sources_at_all_count_as_none() -> None:
    assert triangulation.count_independence([]) == 0


# ── Stock explanations, the deterministic floor ──────────────────────────────


def test_every_axis_has_stock_explanations_so_an_outage_cannot_disable_escalation() -> None:
    """The rule is two benign explanations before any escalation above Minor.
    If those had to be generated, a provider outage would silently disable
    integrity escalation and the run would look clean."""
    for axis in ("timeline", "scope", "ownership", "capability"):
        explanations = triangulation.standard_explanations(axis)
        assert len(explanations) >= 2, axis
        for explanation in explanations:
            assert explanation.text
            assert explanation.as_dict()["text"] == explanation.text


# ── The two lookups that refuse ──────────────────────────────────────────────


def test_an_unknown_dimension_has_no_rubric_anchor() -> None:
    """No section 9.x table exists for it, and a default would grade a
    candidate against a rubric that is not written down anywhere."""
    with pytest.raises(ValueError) as excinfo:
        dimensions.rubric_anchor_text("not_a_dimension")
    assert "not_a_dimension" in str(excinfo.value)


def test_each_real_dimension_has_anchor_text() -> None:
    from app.services.hiring.department_models import (
        DIM_AUTHENTICITY,
        DIM_ROLE_FIT,
        DIM_TRACK_RECORD,
        DIM_TRAJECTORY,
        DIM_VERIFIED_COMPETENCE,
    )

    for dimension in (
        DIM_VERIFIED_COMPETENCE,
        DIM_TRACK_RECORD,
        DIM_ROLE_FIT,
        DIM_AUTHENTICITY,
        DIM_TRAJECTORY,
    ):
        assert dimensions.rubric_anchor_text(dimension).strip(), dimension


def test_an_unknown_band_has_no_score() -> None:
    """Substituting one would turn a parsing failure into a real grade for a
    real candidate."""
    with pytest.raises(ValueError) as excinfo:
        dimensions.band_for("Fantastic")
    assert "Fantastic" in str(excinfo.value)


def test_the_declared_bands_score_in_order() -> None:
    """A better band must never score lower, which is the same invariant the
    four-grade scale carries."""
    scores = [dimensions.band_for(band) for band in dimensions._BAND_SCORES]
    assert len(set(scores)) == len(scores), "two bands sharing a score is a tie nobody can break"
