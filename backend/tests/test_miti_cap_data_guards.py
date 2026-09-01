"""What the cap module does when its Runbook data cannot answer.

`test_band_caps` proves the ARITHMETIC over generated inputs: the delivered
score never exceeds a ceiling that fired and a candidate already below one is
never lifted to it. Those are claims about every score. This file covers the
other half, which no amount of generated scores reaches: what happens when
`runbook_data/bands.yaml` cannot answer the question being asked.

Every guard here fails LOUDLY on purpose, and the module says why: "a floor
whose consequence cannot be resolved must stop the build, not be skipped".
A Runbook edit that adds a fourth consequence, renames a band or drops an
upper bound would otherwise sit unnoticed until the first candidate it applied
to, and would then surface as a runtime error rather than a configuration one.
Silently skipping the row is worse still: the cap simply would not fire, and a
candidate would be delivered at a band the Runbook forbids with nothing in the
record saying so.

The data is patched at `bands_data`, the single reader, so these exercise the
real guards rather than a reimplementation of them.
"""
from __future__ import annotations

import pytest

from app.services.miti import caps


# ── The section 10.8 score-band table ────────────────────────────────────────


@pytest.mark.parametrize(
    "table",
    [
        {},                                   # no score_bands key at all
        {"score_bands": None},                # present and empty
        {"score_bands": {"bands": []}},       # a table with no rows
        {"score_bands": {"bands": "nope"}},   # a table that is not a list
    ],
)
def test_a_missing_score_band_table_stops_everything(monkeypatch, table) -> None:
    """Every ceiling in the module is read from this table, so an absent one
    cannot be defaulted around."""
    monkeypatch.setattr(caps, "bands_data", lambda: table)
    with pytest.raises(caps.CapDataError):
        caps.band_ceiling("Matching")


def test_an_unknown_band_is_refused_rather_than_given_a_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {"score_bands": {"bands": [{"band": "Matching", "low": 60, "high": 74}]}},
    )
    with pytest.raises(caps.CapDataError) as excinfo:
        caps.band_ceiling("Invented Band")
    assert "Invented Band" in str(excinfo.value)


def test_a_band_with_no_upper_bound_is_not_treated_as_a_ceiling(monkeypatch) -> None:
    """The HOLD row deliberately carries no numeric range. Reading it as a
    ceiling would deliver a candidate the Runbook wants held."""
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {"score_bands": {"bands": [{"band": "Hold", "low": None, "high": None}]}},
    )
    with pytest.raises(caps.CapDataError):
        caps.band_ceiling("Hold")


def test_a_real_band_resolves_to_its_upper_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {
            "score_bands": {
                "bands": [
                    {"band": "Not Matching", "low": 0, "high": 59},
                    {"band": "Matching", "low": 60, "high": 74},
                ]
            }
        },
    )
    assert caps.band_ceiling("Matching") == 74


def test_a_band_with_nothing_beneath_it_cannot_resolve_a_demotion(monkeypatch) -> None:
    """"Cannot be delivered as X" means "the ceiling of the band below X".
    With no band below, there is no honest answer, and inventing zero would
    quietly become the harshest possible cap."""
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {"score_bands": {"bands": [{"band": "Bottom", "low": 0, "high": 59}]}},
    )
    with pytest.raises(caps.CapDataError):
        caps._ceiling_below("Bottom")


def test_a_demotion_resolves_to_the_ceiling_of_the_band_below(monkeypatch) -> None:
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {
            "score_bands": {
                "bands": [
                    {"band": "Not Matching", "low": 0, "high": 59},
                    {"band": "Moderately Matching", "low": 60, "high": 74},
                    {"band": "Matching", "low": 75, "high": 89},
                ]
            }
        },
    )
    assert caps._ceiling_below("Matching") == 74


# ── The section 12.2 dimension-floor table ───────────────────────────────────


@pytest.mark.parametrize(
    "table",
    [
        {},
        {"dimension_floors": None},
        {"dimension_floors": {"floors": []}},
    ],
)
def test_a_missing_dimension_floor_table_stops_everything(monkeypatch, table) -> None:
    monkeypatch.setattr(caps, "bands_data", lambda: table)
    with pytest.raises(caps.CapDataError):
        caps._floor_rows()


def test_a_consequence_this_module_cannot_apply_stops_the_build(monkeypatch) -> None:
    """The case the source argues for explicitly: a fourth consequence added to
    the Runbook must fail on the first evaluation after the edit, not be
    skipped and discovered by the candidate it silently did not apply to."""
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {
            "dimension_floors": {
                "floors": [
                    {
                        "dimension": "D4",
                        "floor": 25,
                        "effect_if_breached": "escalate to the ethics board",
                    }
                ]
            }
        },
    )
    with pytest.raises(caps.CapDataError) as excinfo:
        caps._floor_rows()
    assert "ethics board" in str(excinfo.value)


# ── HOLD is a routing outcome, not a band ────────────────────────────────────


def test_an_authenticity_score_below_the_floor_is_held(monkeypatch) -> None:
    from app.services.hiring.department_models import DIM_AUTHENTICITY

    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {
            "dimension_floors": {
                "floors": [
                    {"dimension": "D4", "floor": 25, "effect_if_breached": caps._HOLD_EFFECT}
                ]
            }
        },
    )
    reason = caps.hold_reason({DIM_AUTHENTICITY: 10.0})
    assert reason and "human disposition" in reason


def test_an_authenticity_score_above_the_floor_is_not_held(monkeypatch) -> None:
    from app.services.hiring.department_models import DIM_AUTHENTICITY

    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {
            "dimension_floors": {
                "floors": [
                    {"dimension": "D4", "floor": 25, "effect_if_breached": caps._HOLD_EFFECT}
                ]
            }
        },
    )
    assert caps.hold_reason({DIM_AUTHENTICITY: 80.0}) is None


def test_an_unscored_authenticity_dimension_is_not_held() -> None:
    """Insufficient evidence is not negative evidence. A dimension nobody
    scored must not be read as a floor breach."""
    assert caps.hold_reason({}) is None


# ── Composition ──────────────────────────────────────────────────────────────


def test_no_caps_at_all_means_no_ceiling() -> None:
    assert caps.lowest_ceiling([]) is None
    assert caps.apply(88.0, []) == 88.0


def test_the_binding_ceiling_is_the_lowest_of_those_that_fired() -> None:
    """Section 12.1: the delivered band is the MINIMUM of every ceiling that
    fires. Any other composition quietly ignores a stated control."""
    fired = [
        caps.BandCap(control="a", citation="12.1", subject="s", reason="r", ceiling=74),
        caps.BandCap(control="b", citation="12.2", subject="s", reason="r", ceiling=59),
    ]
    assert caps.lowest_ceiling(fired) == 59
    assert caps.apply(95.0, fired) == 59.0


def test_a_candidate_already_below_the_ceiling_is_never_lifted_to_it() -> None:
    """`min`, never an assignment: setting the score would promote the weakest
    candidates into the band the cap exists to keep the strong ones out of."""
    fired = [
        caps.BandCap(control="a", citation="12.1", subject="s", reason="r", ceiling=74)
    ]
    assert caps.apply(30.0, fired) == 30.0
