"""D8, from both directions: the number belongs here and nowhere else.

spec-doc6 D8 rules two things at once, and a test that checks only one of them
is checking the easy half:

  * the Vivekium Score renders NUMERICALLY on the dashboard, in column 4 and
    its hover. The product's oldest rule is "no numbers reach a client", so
    this is a deliberate, bounded exception and it needs a test saying the
    number IS there, or a well-meaning sweep deletes it;
  * "it must be technically impossible for it to enter a delivered report".

The second is enforced by CONSTRUCTION rather than by filtering: the two
artefacts are different types over different tables (spec-doc6 C10), and the
one that reaches a delivered document has no numeric field to lose. This file
asserts that the construction is still what it claims to be.
"""
from __future__ import annotations

import inspect
import typing

import pytest
from pydantic import BaseModel

from app.schemas import dashboard as schemas
from app.services import dashboard as service

#: Field names whose value is a COUNT of things rather than a score, a
#: percentage or a rank. The product's no-numbers rule has always been about
#: assessment figures: "how many people are in this stage" is not one, and the
#: existing dashboard summary has returned counts since the first release.
COUNT_FIELDS = frozenset(
    {
        "total",
        "page",
        "page_size",
        "team_review_count",
        "scorecard_version",
        "databank_matched",
        "fresh_sourced",
        "shortlisted",
        "offered",
        "joined",
        "total_jobs_worked",
    }
)

#: The schema D8 licenses to carry an assessment number, and what it is.
#: Anything else with a numeric field is a leak. The audited calibration view
#: (`CalibrationInternalsOut`, `CalibrationDimensionOut`) was deleted with its
#: route in the Vivekium release (PLAN-p7 WP-B6).
NUMERIC_SCHEMAS = {
    "DashboardRowOut": "column 4, the Vivekium Score (D8)",
}


def _models():
    for name, obj in vars(schemas).items():
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel:
            yield name, obj


def _numeric_fields(model: type[BaseModel]) -> set[str]:
    found = set()
    for name, field in model.model_fields.items():
        if name in COUNT_FIELDS:
            continue
        annotation = field.annotation
        args = typing.get_args(annotation) or (annotation,)
        if any(arg in (int, float) for arg in args):
            found.add(name)
        # A dict of numbers is still numbers.
        if any(
            typing.get_origin(arg) is dict
            and any(inner in (int, float) for inner in typing.get_args(arg))
            for arg in args
        ):
            found.add(name)
    return found


def test_only_the_licensed_schemas_carry_an_assessment_number():
    """One field, in one place, for one documented reason.

    Walks every response model in the module rather than naming the ones to
    check: a schema added next month is covered without anybody remembering
    to add it here.
    """
    offenders = {
        name: fields
        for name, model in _models()
        if (fields := _numeric_fields(model)) and name not in NUMERIC_SCHEMAS
    }
    assert not offenders, f"an assessment number reached {offenders}"


def test_the_ready_pick_score_is_actually_there():
    """The other half of D8, and the half a well-meaning sweep would delete.

    "No numbers reach a client" was the rule for a year before D8 carved out
    this one exception, so the exception needs a test defending it as loudly as
    the rule has tests defending it.
    """
    assert "ready_pick_score" in schemas.DashboardRowOut.model_fields
    annotation = schemas.DashboardRowOut.model_fields["ready_pick_score"].annotation
    assert int in typing.get_args(annotation)


def test_the_evidence_panel_carries_no_number():
    """spec-doc6 C2: NAMED per-dimension ratings, not raw D1-D5 numbers."""
    assert not _numeric_fields(schemas.ReadyPickProfileOut)
    assert not _numeric_fields(schemas.ProfileDimensionOut)
    # And the dimension entry has no score field at all, by name as well as by
    # type: `rating` is the word the evaluator produced.
    assert "score" not in schemas.ProfileDimensionOut.model_fields
    assert "rating" in schemas.ProfileDimensionOut.model_fields


def test_the_team_review_panel_carries_no_number():
    """A colleague's verdict is a decision, not a rating. A number beside it
    would make a human opinion read as a machine grade."""
    assert not _numeric_fields(schemas.TeamReviewPanelOut)
    assert not _numeric_fields(schemas.TeamReviewEntryOut)


def test_the_profile_declares_its_artefact_in_the_payload_itself():
    """spec-doc6 C10, enforced with the type system: a reader looking at a
    captured response can tell which artefact they are holding."""
    profile = schemas.ReadyPickProfileOut.model_fields["artifact"]
    assert typing.get_args(profile.annotation) == ("ready_pick_profile",)


def test_no_schema_carries_the_calibration_internals_any_more():
    """The raw D1-D5 view is deleted, not hidden (PLAN-p7 WP-B6)."""
    assert not hasattr(schemas, "CalibrationInternalsOut")
    assert not hasattr(schemas, "CalibrationDimensionOut")


def test_the_dashboard_service_type_for_a_delivered_report_cannot_hold_a_score():
    """The construction D8 leans on: `PrismReportRef` has no score field, so a
    serialiser building a delivered payload from it has nothing to leak."""
    assert set(service.PrismReportRef.__dataclass_fields__) == {"report_id"}
    assert "score" in service.ReadyPickProfileRef.__dataclass_fields__


@pytest.mark.parametrize("name", sorted(NUMERIC_SCHEMAS))
def test_every_licensed_numeric_schema_still_exists(name: str):
    """The allow-list is not allowed to accumulate names for schemas that were
    deleted, because a stale entry is a hole nobody notices reopening."""
    assert hasattr(schemas, name), f"{name} is allow-listed and no longer exists"
