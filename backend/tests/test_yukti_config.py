"""Yukti's fixed six-part structure: exact, summing to 100, and backend only.

Owner decision D2: the six parts and their weights live only in backend
configuration; recruiters cannot see, edit or re-prioritise them. These tests
pin the structure, pin the value tables to the live option lists they are
keyed by, and walk the import graph of the recruiter-facing packages.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.services import application_validation, recruiter_columns
from app.services.yukti import config

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_the_six_parts_are_exactly_the_owner_structure() -> None:
    assert config.COMPONENTS == (
        "must_have_evidenced",
        "nice_to_have_evidenced",
        "experience_level",
        "role_fit",
        "company_need_fit",
        "validation_fit",
    )
    assert config.MODEL_COMPONENTS == config.COMPONENTS[:5]
    assert config.COMPONENT_VALIDATION not in config.MODEL_COMPONENTS


def test_the_weights_name_every_part_once_and_sum_to_one_hundred() -> None:
    assert set(config.COMPONENT_WEIGHTS) == set(config.COMPONENTS)
    assert sum(config.COMPONENT_WEIGHTS.values()) == 100
    assert all(weight > 0 for weight in config.COMPONENT_WEIGHTS.values())
    # Must-have evidence dominates: it is the one thing the team said the role
    # cannot be done without.
    assert config.COMPONENT_WEIGHTS[config.COMPONENT_MUST_HAVE] == max(
        config.COMPONENT_WEIGHTS.values()
    )


def test_the_tables_cannot_be_edited_at_runtime() -> None:
    with pytest.raises(TypeError):
        config.COMPONENT_WEIGHTS[config.COMPONENT_ROLE_FIT] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        config.VALIDATION_VALUES[config.VALIDATION_CTC]["Above range"] = 1.0  # type: ignore[index]


def test_the_import_check_refuses_a_structure_that_does_not_add_up(monkeypatch) -> None:
    """The module-level check is the enforcement; prove it bites."""
    monkeypatch.setattr(
        config,
        "COMPONENT_WEIGHTS",
        {**config.COMPONENT_WEIGHTS, config.COMPONENT_ROLE_FIT: 16},
    )
    with pytest.raises(RuntimeError, match="sum to 100"):
        config._check()
    monkeypatch.setattr(
        config,
        "COMPONENT_WEIGHTS",
        {**dict(config.COMPONENT_WEIGHTS), "behavioural_signal": 0},
    )
    with pytest.raises(RuntimeError, match="exactly the six parts"):
        config._check()


def test_verdicts_are_three_words_worth_one_half_and_nothing() -> None:
    assert config.VERDICTS == ("strong", "some", "none")
    assert dict(config.VERDICT_VALUES) == {"strong": 1.0, "some": 0.5, "none": 0.0}


def test_validation_sub_weights_sum_to_one_hundred() -> None:
    assert set(config.VALIDATION_SUBWEIGHTS) == set(config.VALIDATION_PARTS)
    assert sum(config.VALIDATION_SUBWEIGHTS.values()) == 100


def test_the_ctc_values_are_keyed_by_the_recruiter_columns_own_words() -> None:
    assert set(config.VALIDATION_VALUES[config.VALIDATION_CTC]) == {
        recruiter_columns.CTC_WITHIN,
        recruiter_columns.CTC_BELOW,
        recruiter_columns.CTC_ABOVE,
    }


def test_every_reachable_notice_bucket_has_a_value_and_nothing_else_does() -> None:
    """Rebuilt from the LIVE form options, plus the free-text answers submitted
    before the select existed, so a bucket added to `recruiter_columns` without
    a value here fails this test before `validation_fit` could ever see it."""
    reachable = {
        recruiter_columns.notice_period_bucket(option)
        for option in application_validation.NOTICE_PERIOD_OPTIONS
    }
    reachable |= {
        recruiter_columns.notice_period_bucket(f"{days} days")
        for days in (0, 1, 30, 31, 60, 61, 90, 91, 365)
    }
    reachable.discard(None)
    assert set(config.VALIDATION_VALUES[config.VALIDATION_NOTICE]) == reachable


def test_every_document_readiness_option_has_a_value_in_form_order() -> None:
    table = config.VALIDATION_VALUES[config.VALIDATION_DOCUMENTS]
    assert tuple(table) == application_validation.DOCUMENT_READINESS_OPTIONS
    values = list(table.values())
    assert values == sorted(values, reverse=True), "readier must never be worth less"


def test_limits_are_the_documented_ones() -> None:
    assert config.TASK_TYPE == "yukti_matching"
    assert config.PROMPT_NAME == "yukti_matching_system"
    assert config.MAX_TAG_WORDS == 5
    assert config.MIN_QUOTE_WORDS >= 3
    assert config.BATCH_SIZE >= 1
    assert config.STATUSES == ("pending", "scored", "not_assessed", "legacy")


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_no_recruiter_facing_module_imports_the_structure() -> None:
    """D2: the parts and weights never cross an API boundary. A route or a
    schema that imported them is the first step towards serialising them."""
    roots = [APP / "api", APP / "schemas"]
    for root in roots:
        assert root.is_dir(), f"{root} is missing, so the sweep would pass vacuously"
    offenders = [
        str(path.relative_to(APP))
        for root in roots
        for path in root.rglob("*.py")
        if any(
            name == "app.services.yukti.config"
            or name.startswith("app.services.yukti.config.")
            for name in _imports(path)
        )
    ]
    assert not offenders, offenders
