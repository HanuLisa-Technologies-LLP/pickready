"""D3 on the Candidate Dashboard: no number, and no letter, reaches a client.

SUPERSEDES the D8 version of this file. spec-doc6 D8 licensed ONE number here,
the Vivekium Score in column 4, and this file used to defend it as loudly as it
defended the rule. The Vivekium brief (D3, CONTRACT C8) removed that exception
with no replacement, and spec v4 had already forbidden letter grades, so the
file now defends the rule from both directions it can be broken:

  * by TYPE: no response model in `schemas/dashboard.py` carries a numeric
    field that is not a count, except the calibration view's two schemas,
    whose fate is Phase 7's (they are named, and the allow-list is asserted to
    shrink, never grow);
  * by NAME: the row carries no field that reads as a score, a percent or a
    band, and no field holding the old letter grade.

The HTTP half (the JSON a browser actually receives) is walked in
`test_dashboard_workflows.py`, over real rows with real scores behind them.
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
        "comparable",
        "diverged",
        "rate",
        "scorecard_version",
        "databank_matched",
        "fresh_sourced",
        "shortlisted",
        "offered",
        "joined",
        "total_jobs_worked",
    }
)

#: The only schemas still allowed a raw number: the audited calibration view
#: (Super Admin / HR Manager, every read audited). Phase 7 decides whether it
#: survives; when it goes, this set empties and the parametrised existence
#: test below fails until the entry is removed. `DashboardRowOut` is NOT here
#: and must never be again.
NUMERIC_SCHEMAS = {
    "CalibrationDimensionOut": "the audited Super Admin / HR Manager view (Phase 7)",
    "CalibrationInternalsOut": "the audited Super Admin / HR Manager view (Phase 7)",
}

#: Substrings a row field name may not carry. A word column needs none of
#: them, and each one is how a number, or the letter scale, comes back.
#: "rank" is deliberately absent: `ranking_label` is the WORD for the rank,
#: and the numeric-type walk above is what keeps a rank number out.
FORBIDDEN_NAME_PARTS = ("score", "percent", "band", "pre_screen", "range")


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


def test_no_dashboard_schema_carries_an_assessment_number():
    """Walks every response model in the module rather than naming the ones to
    check: a schema added next month is covered without anybody remembering
    to add it here."""
    offenders = {
        name: fields
        for name, model in _models()
        if (fields := _numeric_fields(model)) and name not in NUMERIC_SCHEMAS
    }
    assert not offenders, f"an assessment number reached {offenders}"


def test_the_row_has_no_numeric_field_at_all():
    """The row is the surface a recruiter scans. D8's one licence lived here;
    D3 took it, and the assertion is absolute rather than via the allow-list."""
    assert not _numeric_fields(schemas.DashboardRowOut)
    assert "DashboardRowOut" not in NUMERIC_SCHEMAS


@pytest.mark.parametrize("model", [schemas.DashboardRowOut, schemas.DashboardPageOut])
def test_no_row_or_page_field_is_named_like_a_score(model: type[BaseModel]):
    offenders = sorted(
        name
        for name in model.model_fields
        if any(part in name for part in FORBIDDEN_NAME_PARTS)
    )
    assert not offenders, f"{model.__name__} carries {offenders}"


def test_the_grade_columns_are_words_from_the_one_scale():
    """The two grade columns' filter domain is the four `rating` words, served
    to the browser; nothing else is offered to filter by."""
    served = schemas.DashboardPageOut.model_fields["ai_match_grades"]
    assert served.default_factory() == list(service.AI_MATCH_GRADES)
    for grade in served.default_factory():
        assert not any(character.isdigit() for character in grade)


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


def test_the_two_artefacts_are_distinguishable_in_the_payload_itself():
    """spec-doc6 C10, enforced with the type system.

    Each artefact declares its own literal `artifact` discriminator, so a
    consumer switching on it cannot be handed the other one, and a reader
    looking at a captured response can tell which they are holding.
    """
    profile = schemas.ReadyPickProfileOut.model_fields["artifact"]
    calibration = schemas.CalibrationInternalsOut.model_fields["artifact"]
    assert typing.get_args(profile.annotation) == ("ready_pick_profile",)
    assert typing.get_args(calibration.annotation) == ("calibration_internals",)
    assert profile.annotation != calibration.annotation


def test_neither_artefact_reference_can_hold_a_score():
    """The construction C10 leans on: a serialiser building either reference
    has nothing numeric to leak. The profile reference lost its `score` with
    D3; asserted by field set so a renamed field cannot slip through."""
    assert set(service.PrismReportRef.__dataclass_fields__) == {"report_id"}
    assert set(service.ReadyPickProfileRef.__dataclass_fields__) == {"evaluation_id"}


@pytest.mark.parametrize("name", sorted(NUMERIC_SCHEMAS))
def test_every_licensed_numeric_schema_still_exists(name: str):
    """The allow-list is not allowed to accumulate names for schemas that were
    deleted, because a stale entry is a hole nobody notices reopening."""
    assert hasattr(schemas, name), f"{name} is allow-listed and no longer exists"
