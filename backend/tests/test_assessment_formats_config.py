"""Every bound this package uses comes from Settings, and behaviour follows it.

`services/assessment_formats/config.py` states the rule this file enforces:
"Every number the composer, the validator and the scorer use comes from here,
and every field here comes from an `assessment_*` setting in `core/config.py`;
no module in this package carries a literal."

Two halves, and the second is the one with teeth. Checking that the config
object reads the settings proves only that the table agrees with itself; what
matters is that the CODE reads the table, so each rule is also exercised by
changing the config and watching the composer and the validator change with
it. A threshold nothing reads is a threshold that was quietly hardcoded
somewhere else.

The float sweep is the cheap structural half: every tuning knob in this
package is a share or a weight, so a float literal anywhere but the unit
bounds is a share somebody wrote into a module.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from app.core.config import get_settings
from app.services import ppi
from app.services.assessment_formats import composition
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types

PACKAGE = pathlib.Path(format_config.__file__).resolve().parent

GRADES = ("non_managerial", "managerial", "leadership", "cxo")


def test_every_scalar_field_reads_its_own_setting() -> None:
    settings = get_settings()
    config = format_config.get_config()
    for field in ("evidence_min_share", "supporting_max_share", "supporting_max_share_senior",
                  "composition_attempts", "evaluation_min_reasoning_words",
                  "anchor_min_chars", "misconception_min_words"):
        assert getattr(config, field) == getattr(settings, f"assessment_{field}"), field


@pytest.mark.parametrize("grade", GRADES)
def test_every_duration_reads_its_own_setting(grade) -> None:
    settings = get_settings()
    minutes = getattr(settings, f"assessment_duration_minutes_{grade}")
    assert format_config.get_config().duration_for(grade) == minutes * 60


@pytest.mark.parametrize("question_type", types.QUESTION_TYPES)
def test_every_format_has_a_time_and_a_weight_from_settings(question_type) -> None:
    settings = get_settings()
    config = format_config.get_config()
    suffix = {
        types.EVIDENCE_BASED: "evidence",
        types.SHORT_ANSWER: "short_answer",
        types.MCQ_SINGLE: "mcq_single",
        types.MCQ_MULTI: "mcq_multi",
        types.FILL_BLANK: "fill_blank",
        types.CODING: "coding",
    }[question_type]
    assert config.time_seconds_by_type[question_type] == getattr(
        settings, f"assessment_time_{suffix}_seconds"
    )
    assert config.weight_by_type[question_type] == getattr(
        settings, f"assessment_weight_{suffix}"
    )


def test_the_config_is_one_frozen_object_per_process() -> None:
    """Frozen so a caller cannot tune the product at runtime, and cached so
    every module in one request reads the same numbers."""
    assert format_config.get_config() is format_config.get_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        format_config.get_config().evidence_min_share = 0.9


def test_a_senior_role_is_held_to_the_tighter_share() -> None:
    config = format_config.get_config()
    for grade in GRADES:
        expected = (
            config.supporting_max_share_senior
            if grade in format_config.SENIOR_GRADES
            else config.supporting_max_share
        )
        assert config.supporting_share_for(grade) == expected
    assert config.supporting_max_share_senior < config.supporting_max_share
    assert format_config.SENIOR_GRADES == {"leadership", "cxo"}


def test_the_evidence_share_is_a_majority_by_definition() -> None:
    """"Majority" is not a preference. A share at or below one half would let a
    valid assessment be half supporting formats, which is the thing section 1
    forbids."""
    assert format_config.get_config().evidence_min_share > 0.5


def test_no_module_in_this_package_carries_a_tuning_constant() -> None:
    """Every knob here is a share or a weight, so a stray float is one of them
    written into a module. The unit bounds are exempt: they are the ends of
    the 0..1 scale, not a value anyone would tune."""
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value not in (0.0, 1.0):
                    offenders.append(f"{path.name}:{node.lineno} {node.value}")
    assert not offenders, (
        "these look like tuning constants written into a module rather than "
        f"read from Settings: {offenders}"
    )


# ── The code actually reads the table ────────────────────────────────────────


#: The real config, captured ONCE at import. `_replaced` must not read
#: `get_config` at call time: the tests below monkeypatch that name, and a
#: replacement built by calling it would call itself.
_BASE = format_config.get_config()


def _replaced(**changes) -> format_config.FormatConfig:
    return dataclasses.replace(_BASE, **changes)


def _allocation(size: int = 20):
    from types import SimpleNamespace
    import uuid as _uuid

    matrix = [
        SimpleNamespace(
            id=_uuid.uuid4(), category=category, name=f"{category}-{index}",
            description="", ordinal=index + 1,
        )
        for category in ppi.CATEGORIES
        for index in range(5)
    ]
    return ppi._allocate(matrix, size, "non_managerial")


def _anchored(slots):
    """A DIFFERENT quotable item per evidence slot: two questions probing one
    resume item is its own rule, and it would mask the one under test."""
    for slot in slots:
        if slot.question_type == types.EVIDENCE_BASED:
            slot.resume_anchor = f"led the work stream numbered {slot.index} at Northwind Payments"
    return slots


def test_the_supporting_share_decides_how_many_slots_are_structured(monkeypatch) -> None:
    allocation = _allocation()
    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(supporting_max_share=0.0))
    none_at_all = composition.compose(allocation, grade="non_managerial", role_classification="STEM")
    assert not [slot for slot in none_at_all if slot.question_type in types.SUPPORTING_TYPES]

    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(supporting_max_share=0.25))
    some = composition.compose(allocation, grade="non_managerial", role_classification="STEM")
    assert [slot for slot in some if slot.question_type in types.SUPPORTING_TYPES]


def test_the_duration_decides_the_time_allocations(monkeypatch) -> None:
    allocation = _allocation()
    tight = dict(_BASE.duration_seconds_by_grade)
    tight["non_managerial"] = 600
    monkeypatch.setattr(
        format_config, "get_config", lambda: _replaced(duration_seconds_by_grade=tight)
    )
    slots = composition.compose(allocation, grade="non_managerial", role_classification="STEM")
    assert sum(slot.time_allocation_seconds for slot in slots) <= 600
    assert composition.validate(slots, "non_managerial", "STEM") == [] or all(
        "duration" not in reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
    )


def test_the_anchor_floor_decides_what_counts_as_an_anchor(monkeypatch) -> None:
    allocation = _allocation()
    slots = _anchored(
        composition.compose(allocation, grade="non_managerial", role_classification="STEM")
    )

    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(anchor_min_chars=12))
    assert not [
        reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
        if "not anchored" in reason
    ]
    # Raise the floor above the anchors that were accepted a moment ago, and
    # the same assessment stops validating.
    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(anchor_min_chars=500))
    assert [
        reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
        if "not anchored" in reason
    ]


def test_the_evidence_share_decides_whether_a_mix_is_rejected(monkeypatch) -> None:
    allocation = _allocation()
    slots = _anchored(
        composition.compose(allocation, grade="non_managerial", role_classification="STEM")
    )
    assert composition.validate(slots, "non_managerial", "STEM") == []
    # A share nothing could satisfy rejects the very mix the composer built.
    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(evidence_min_share=0.999))
    assert [
        reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
        if "majority" in reason
    ]
