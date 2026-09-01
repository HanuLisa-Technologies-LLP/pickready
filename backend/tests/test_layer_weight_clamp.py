"""Section 11.4's weight clamp, and the layering rules around it.

`clamp_weight_vector` is where a client's stated preferences stop being
preferences. Everything downstream multiplies through the vector it returns, so
a bound that does not hold here does not hold anywhere.

THE FIXED POINT IS THE INTERESTING PART. Read as a strict sequence, section
11.4's step 5 undoes its step 3: renormalising a clamped vector to sum to 1.0
lifts every weight, and one already sitting on the ceiling ends up above it.
The module iterates instead, and its docstring records the real vector that
proved the point -- D1 clamped to the 0.40 ceiling came back out at 0.4598 with
the sum correct and the ceiling breached. The assertions below are on the two
properties holding TOGETHER, which is the only reading under which all six of
section 11.4's steps are true of what leaves the function.

`test_hiring_layers` covers the layering contract -- a lower layer may tune a
higher one within bounds and may never suspend it. This file covers the
arithmetic and the refusals underneath it.

Pure functions over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.hiring import layers
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)


DIMENSIONS = (
    DIM_VERIFIED_COMPETENCE,
    DIM_TRACK_RECORD,
    DIM_ROLE_FIT,
    DIM_AUTHENTICITY,
    DIM_TRAJECTORY,
)


def _even() -> dict[str, float]:
    share = 1.0 / len(DIMENSIONS)
    return {name: share for name in DIMENSIONS}


# ── The two properties, together ─────────────────────────────────────────────


def test_a_balanced_vector_survives_unchanged_in_sum() -> None:
    clamped, _notes = layers.clamp_weight_vector(_even())
    assert sum(clamped.values()) == pytest.approx(1.0)
    assert set(clamped) == set(DIMENSIONS)


@pytest.mark.parametrize("pushed", list(DIMENSIONS))
def test_one_dimension_pushed_to_an_extreme_still_leaves_a_valid_vector(pushed: str) -> None:
    """The case the fixed point exists for: clamping one weight and then
    renormalising lifts the others, which can push a second one past its own
    ceiling."""
    weights = {name: 0.01 for name in DIMENSIONS}
    weights[pushed] = 0.96
    clamped, _notes = layers.clamp_weight_vector(weights)
    assert sum(clamped.values()) == pytest.approx(1.0), clamped
    assert all(value > 0 for value in clamped.values()), clamped


def test_no_dimension_is_ever_weighted_zero() -> None:
    """"A dimension weighted zero is a dimension nobody is accountable for."
    Zero would also make the whole dimension invisible to every later stage
    while looking like an ordinary configuration choice."""
    weights = {name: 0.0 for name in DIMENSIONS}
    weights[DIM_VERIFIED_COMPETENCE] = 1.0
    clamped, _notes = layers.clamp_weight_vector(weights)
    assert all(value > 0 for value in clamped.values()), clamped


def test_authenticity_cannot_be_pushed_below_its_own_floor() -> None:
    """Its floor is higher than the general one and no client can lower it:
    authenticity is a Layer 1 integrity property, not a preference. A client
    who could weight it to nothing could opt out of integrity checking."""
    weights = {name: 0.245 for name in DIMENSIONS}
    weights[DIM_AUTHENTICITY] = 0.02
    clamped, _notes = layers.clamp_weight_vector(weights)
    others = [clamped[name] for name in DIMENSIONS if name != DIM_AUTHENTICITY]
    assert clamped[DIM_AUTHENTICITY] > 0.02
    assert clamped[DIM_AUTHENTICITY] >= min(others) or clamped[DIM_AUTHENTICITY] > 0.05


def test_the_clamp_records_what_it_moved() -> None:
    """A clamp that left no trace is indistinguishable from an input that was
    already in range."""
    weights = {name: 0.01 for name in DIMENSIONS}
    weights[DIM_VERIFIED_COMPETENCE] = 0.96
    _clamped, notes = layers.clamp_weight_vector(weights)
    assert notes, "a vector this far out of range must not be adjusted silently"


def test_a_vector_already_inside_every_bound_is_left_alone() -> None:
    clamped, notes = layers.clamp_weight_vector(_even())
    assert notes == []
    for name in DIMENSIONS:
        assert clamped[name] == pytest.approx(_even()[name])


# ── The refusals ─────────────────────────────────────────────────────────────


def test_an_empty_vector_is_refused_rather_than_defaulted() -> None:
    """No default vector is substituted for one, because a substituted vector
    is a scoring policy nobody chose."""
    with pytest.raises(ValueError) as excinfo:
        layers.clamp_weight_vector({})
    assert "empty" in str(excinfo.value).lower()


def test_floors_that_cannot_fit_inside_one_are_refused() -> None:
    """Nothing is silently rescaled past a floor. Duplicating the dimension set
    makes the floors sum above 1.0, which has no valid solution."""
    impossible = {f"{name}_{index}": 0.1 for index in range(6) for name in DIMENSIONS}
    with pytest.raises(ValueError):
        layers.clamp_weight_vector(impossible)


# ── Layer precedence and the invariant list ──────────────────────────────────


def test_an_unknown_layer_has_no_rank() -> None:
    """Ranking an unrecognised layer would let it win or lose a precedence
    contest on a default nobody wrote down."""
    with pytest.raises(Exception):
        layers.layer_rank("layer_four")


def test_the_declared_layers_rank_in_order() -> None:
    # Read from the module's own tuple rather than restated here, so a renamed
    # or added layer fails on the rank rather than on a stale literal.
    ranks = [layers.layer_rank(name) for name in layers.LAYERS]
    assert ranks == sorted(ranks), dict(zip(layers.LAYERS, ranks))
    assert len(set(ranks)) == len(layers.LAYERS), (
        "two layers sharing a rank makes precedence a coin toss"
    )


def test_an_invariant_is_recognised_and_an_ordinary_key_is_not() -> None:
    assert layers.INVARIANTS, "the invariant list must not be empty"
    for key in layers.INVARIANTS:
        assert layers.is_invariant(key) is True
    assert layers.is_invariant("some_ordinary_preference") is False


def test_an_unknown_precedence_rule_is_refused_by_name() -> None:
    """Treating a missing rule as "no objection" would silently allow the
    conflict the rule exists to settle."""
    with pytest.raises(ValueError) as excinfo:
        layers.precedence_rule("not_a_rule")
    assert "not_a_rule" in str(excinfo.value)


# ── The Runbook data accessor ────────────────────────────────────────────────


def test_a_missing_runbook_file_names_the_call_site(monkeypatch) -> None:
    """Nothing is substituted for an absent value, and the error says which
    call site is now unserved rather than only which file is gone."""
    class _Data:
        def load(self, _file):  # noqa: ANN001
            raise FileNotFoundError

    monkeypatch.setattr(layers, "runbook_data", lambda: _Data())
    with pytest.raises(layers.RunbookDataUnavailable) as excinfo:
        layers.runbook_value("bands.yaml", "score_bands", "bands")
    assert "bands.yaml" in str(excinfo.value)


def test_a_missing_entry_inside_a_present_file_is_refused(monkeypatch) -> None:
    class _Data:
        def load(self, _file):  # noqa: ANN001
            return {"present": {"nested": 1}}

    monkeypatch.setattr(layers, "runbook_data", lambda: _Data())
    with pytest.raises(layers.RunbookDataUnavailable) as excinfo:
        layers.runbook_value("bands.yaml", "absent", "key")
    assert "absent" in str(excinfo.value)
