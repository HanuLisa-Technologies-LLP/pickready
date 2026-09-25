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
The shares and the per-grade durations this file pinned until 2026-09-25
are gone with the rules that read them: the mix is a count now
(`assessment_questions.budget`, swept for literals here too).

The float sweep is the cheap structural half: every tuning knob in this
package is a share or a weight, so a float literal anywhere but the unit
bounds is a share somebody wrote into a module.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import uuid

import pytest

from app.core.config import get_settings
from app.services.assessment_contract import ContractSkill
from app.services.assessment_formats import composition
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types
from app.services.assessment_questions import budget

PACKAGE = pathlib.Path(format_config.__file__).resolve().parent

#: The family clock (Appendix B section 3): which setting times each format.
TIME_SETTING = {
    types.EVIDENCE_BASED: "assessment_time_prose_seconds",
    types.SHORT_ANSWER: "assessment_time_prose_seconds",
    types.MCQ_SINGLE: "assessment_time_objective_seconds",
    types.MCQ_MULTI: "assessment_time_objective_seconds",
    types.FILL_BLANK: "assessment_time_objective_seconds",
    types.CODING: "assessment_time_coding_seconds",
}
WEIGHT_SUFFIX = {
    types.EVIDENCE_BASED: "evidence",
    types.SHORT_ANSWER: "short_answer",
    types.MCQ_SINGLE: "mcq_single",
    types.MCQ_MULTI: "mcq_multi",
    types.FILL_BLANK: "fill_blank",
    types.CODING: "coding",
}


def test_every_scalar_field_reads_its_own_setting() -> None:
    settings = get_settings()
    config = format_config.get_config()
    for field in ("composition_attempts", "evaluation_min_reasoning_words",
                  "anchor_min_chars", "misconception_min_words"):
        assert getattr(config, field) == getattr(settings, f"assessment_{field}"), field


@pytest.mark.parametrize("question_type", types.QUESTION_TYPES)
def test_every_format_has_a_family_time_and_a_weight_from_settings(question_type) -> None:
    settings = get_settings()
    config = format_config.get_config()
    assert config.time_seconds_by_type[question_type] == getattr(settings, TIME_SETTING[question_type])
    assert config.weight_by_type[question_type] == getattr(
        settings, f"assessment_weight_{WEIGHT_SUFFIX[question_type]}"
    )


def test_the_family_clock_is_the_owners_table() -> None:
    """Appendix B section 3: prose three minutes, objective one, coding twenty."""
    settings = get_settings()
    assert settings.assessment_time_prose_seconds == 180
    assert settings.assessment_time_objective_seconds == 60
    assert settings.assessment_time_coding_seconds == 1200


def test_the_retired_bounds_are_gone() -> None:
    """The evidence-majority share, the supporting-share bounds and the
    per-grade durations went with the rules that read them."""
    from app.core.config import Settings

    fields = set(Settings.model_fields)
    for retired in (
        "assessment_evidence_min_share",
        "assessment_supporting_max_share",
        "assessment_supporting_max_share_senior",
        "assessment_duration_minutes_non_managerial",
        "assessment_time_evidence_seconds",
        "assessment_time_short_answer_seconds",
        "assessment_time_mcq_single_seconds",
        "assessment_time_mcq_multi_seconds",
        "assessment_time_fill_blank_seconds",
    ):
        assert retired not in fields, retired
    assert not hasattr(composition, "fit_duration")


def test_the_config_is_one_frozen_object_per_process() -> None:
    """Frozen so a caller cannot tune the product at runtime, and cached so
    every module in one request reads the same numbers."""
    assert format_config.get_config() is format_config.get_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        format_config.get_config().anchor_min_chars = 1


def test_no_module_in_this_package_carries_a_tuning_constant() -> None:
    """Every knob here is a share or a weight, so a stray float is one of them
    written into a module. The unit bounds are exempt: they are the ends of
    the 0..1 scale, not a value anyone would tune. The budget module is swept
    too, because the mix shares are the knobs most likely to be typed in."""
    offenders: list[str] = []
    for path in sorted([*PACKAGE.glob("*.py"), pathlib.Path(budget.__file__)]):
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


def _composed(grade: str = "non_managerial") -> list[composition.Slot]:
    skills = [
        ContractSkill(id=uuid.uuid4(), name=f"{bucket} {n}", bucket=bucket, priority=n, evidence_line="e")
        for bucket in ("must_have", "nice_to_have", "behavioural")
        for n in range(1, 4)
    ]
    total = budget.question_budget(grade, len(skills))
    mix = budget.mix(total, coding=True)
    return composition.compose(composition.allocate(skills, total, stem=True), mix=mix, grade=grade)


def test_the_family_clock_decides_the_time_allocations(monkeypatch) -> None:
    times = dict(_BASE.time_seconds_by_type)
    times[types.CODING] = 900
    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(time_seconds_by_type=times))
    coding = [slot for slot in _composed() if slot.question_type == types.CODING]
    assert coding and {slot.time_allocation_seconds for slot in coding} == {900}


def test_the_anchor_floor_decides_what_counts_as_an_anchor(monkeypatch) -> None:
    slots = _composed()
    for slot in slots:
        if slot.question_type == types.EVIDENCE_BASED:
            slot.resume_anchor = f"led the work stream numbered {slot.index} at Northwind Payments"
    mix = budget.mix(len(slots), coding=True)

    def unanchored() -> list[str]:
        return [reason for reason in composition.validate(slots, mix=mix, skills=[])
                if "not anchored" in reason]

    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(anchor_min_chars=12))
    assert not unanchored()
    # Raise the floor above the anchors that were accepted a moment ago, and
    # the same assessment stops validating.
    monkeypatch.setattr(format_config, "get_config", lambda: _replaced(anchor_min_chars=500))
    assert unanchored()
