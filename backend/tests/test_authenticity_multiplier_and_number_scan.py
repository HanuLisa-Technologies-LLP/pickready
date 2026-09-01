"""Section 10.5's authenticity multiplier, and the shapes the number scan reads.

TWO GUARDS, ONE ARGUMENT. Both modules refuse rather than default when they
cannot answer, and in both cases the quiet alternative is the dangerous one:

  The multiplier is read from a piecewise table. A branch whose slope cannot be
  derived, or a D4 score no branch covers, means the composite would be
  suppressed by an amount nobody can state. Returning the neutral 1.0 there
  would deliver the candidate at full strength on evidence that does not hold
  together, and nothing in the record would say the multiplier never ran.

  `numbers.scan` is the last thing between an internal score and a client. It
  has to reach into whatever shape the payload happens to be -- a Pydantic
  model, a dataclass, a namespace, a plain dict -- because a number hidden in a
  shape it cannot open is a number that reaches the client.

`None` from the multiplier means HOLD, not zero, and the difference matters:
zero would deliver the candidate at the bottom of the scale, while HOLD routes
them to a person.
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from app.services.miti import aggregation, caps
from app.services.siddhi import numbers


# ── The piecewise table behind the multiplier ────────────────────────────────


@pytest.mark.parametrize(
    "table",
    [
        {},
        {"authenticity_multiplier": None},
        {"authenticity_multiplier": {"piecewise": []}},
        {"authenticity_multiplier": {"piecewise": "not-a-list"}},
    ],
)
def test_a_missing_piecewise_table_stops_the_multiplier(monkeypatch, table) -> None:
    monkeypatch.setattr(caps, "bands_data", lambda: table)
    with pytest.raises(caps.CapDataError):
        aggregation.authenticity_multiplier_for_score(50.0)


def test_a_branch_with_no_stated_range_cannot_have_its_slope_derived() -> None:
    """The slope is what turns a D4 score into a suppression. Guessing one
    would suppress a composite by an amount the Runbook never stated."""
    with pytest.raises(caps.CapDataError) as excinfo:
        aggregation._exact_slope(
            {
                "condition": "mid",
                "d4_low": 25,
                "d4_high_exclusive": 60,
                "intercept": 0.8,
                "stated_range": "0.8",  # one endpoint, not two
            }
        )
    assert "endpoint range" in str(excinfo.value)


def test_a_branch_covering_an_empty_interval_is_refused() -> None:
    """Tested on the slope helper directly, because a row whose interval is
    empty can never satisfy `low <= d4 < high` and so is unreachable through
    the score lookup. The guard still has to hold: dividing by a zero span
    would raise ZeroDivisionError deep inside the arithmetic instead of naming
    the branch that is misconfigured."""
    with pytest.raises(caps.CapDataError) as excinfo:
        aggregation._exact_slope(
            {
                "condition": "empty",
                "d4_low": 60,
                "d4_high_exclusive": 60,
                "intercept": 0.8,
                "stated_range": "0.8 -> 1.0",
            }
        )
    assert "empty interval" in str(excinfo.value)


def test_a_score_no_branch_covers_is_refused_rather_than_defaulted(monkeypatch) -> None:
    """The table is meant to be total over the D4 range. A hole in it is a
    configuration error, and treating it as "no suppression" would be the
    single most generous possible reading of missing data."""
    monkeypatch.setattr(
        caps,
        "bands_data",
        lambda: {
            "authenticity_multiplier": {
                "piecewise": [
                    {
                        "condition": "high",
                        "d4_low": 80,
                        "d4_high_exclusive": None,
                        "intercept": 1.0,
                        "stated_range": "1.0 -> 1.0",
                    }
                ]
            }
        },
    )
    with pytest.raises(caps.CapDataError):
        aggregation.authenticity_multiplier_for_score(10.0)


def test_the_real_table_is_total_across_the_whole_d4_range() -> None:
    """No patching. The shipped table has to answer for every score a
    dimension can return, or some candidate hits the hole in production."""
    for d4 in (0.0, 1.0, 24.9, 25.0, 40.0, 59.9, 60.0, 75.0, 99.9, 100.0):
        value, reason = aggregation.authenticity_multiplier_for_score(d4)
        assert reason, d4
        # None is HOLD, which is a routing outcome rather than a multiplier.
        assert value is None or 0.0 < value <= 1.0, (d4, value)


def test_a_high_authenticity_score_is_not_suppressed() -> None:
    value, _reason = aggregation.authenticity_multiplier_for_score(95.0)
    assert value == pytest.approx(1.0)


def test_suppression_is_monotonic_in_the_authenticity_score() -> None:
    """Less internally consistent must never be worth more."""
    scored = [
        (d4, aggregation.authenticity_multiplier_for_score(d4)[0])
        for d4 in (30.0, 50.0, 70.0, 90.0)
    ]
    values = [value for _d4, value in scored if value is not None]
    assert values == sorted(values), scored


# ── The shapes the number scan has to open ───────────────────────────────────


class _Model:
    """Stands in for a Pydantic model: it exposes `model_dump`."""

    def __init__(self, **fields) -> None:
        self._fields = fields

    def model_dump(self) -> dict:
        return dict(self._fields)


@dataclasses.dataclass
class _Row:
    remark: str


def test_a_model_dump_payload_is_opened_and_scanned() -> None:
    found = numbers.scan(_Model(remark="You scored 82/100 overall."))
    assert found, "a number inside a model payload must still be caught"


def test_a_dataclass_payload_is_opened_and_scanned() -> None:
    found = numbers.scan(_Row(remark="You scored 82/100 overall."))
    assert found


def test_a_namespace_payload_is_opened_and_scanned() -> None:
    found = numbers.scan(SimpleNamespace(remark="You scored 82/100 overall."))
    assert found


def test_a_clean_payload_of_each_shape_reports_nothing() -> None:
    clean = "Matching, with clear examples of owned delivery."
    assert numbers.scan(_Model(remark=clean)) == []
    assert numbers.scan(_Row(remark=clean)) == []
    assert numbers.scan(SimpleNamespace(remark=clean)) == []


def test_empty_text_is_not_a_violation() -> None:
    """An absent remark is not a leaked number, and reporting one would train
    people to ignore the check."""
    assert numbers.scan_text("") == []
    assert numbers.scan_text(None) == []  # type: ignore[arg-type]


def test_a_score_shaped_sentence_is_caught_in_a_bare_string() -> None:
    """An email body is a bare string, which is why `scan_text` is public."""
    found = numbers.scan_text("You are in the top 12% of applicants.")
    assert found
    assert found[0].rule == numbers.RULE_SCORE_PROSE
