"""Hiring data failures must be explicit, and matching must keep its boundaries."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.hiring import department_models, layers, pipeline_halt, runbook_data


def test_unknown_seniority_uses_the_documented_default() -> None:
    department = department_models.DEPARTMENTS["engineering"]
    default = department_models.SENIORITIES[0]
    assert department_models.baseline_for(department, "unrecognised") == department_models.baseline_for(
        department, default
    )
    assert department_models.rubric_anchors(department, "unrecognised") == department_models.rubric_anchors(
        department, default
    )


def test_blank_phrase_has_no_baseline_and_a_name_matches_directly() -> None:
    assert department_models.match_competency("  ", "engineering") is None
    competency = department_models.baseline_for("engineering", "managerial")[0]
    assert department_models.match_competency(competency.name, "engineering", "managerial") == competency


def test_longest_matching_alias_wins_without_relabeling_the_phrase(monkeypatch) -> None:
    short = SimpleNamespace(key="short", name="Unrelated first", aliases=("on call",))
    long = SimpleNamespace(key="long", name="Unrelated second", aliases=("incident command",))
    shorter = SimpleNamespace(key="shorter", name="Unrelated third", aliases=("incident",))
    monkeypatch.setattr(department_models, "baseline_for", lambda *_args: (short, long, shorter))
    found = department_models.match_competency("on call incident command", "engineering")
    assert found is long


@pytest.mark.parametrize("missing", [None, {}])
def test_missing_department_table_is_refused(monkeypatch, missing) -> None:
    monkeypatch.setattr(layers, "runbook_value", lambda *_args: missing)
    with pytest.raises(layers.RunbookDataUnavailable, match="departments"):
        department_models._department_data()


def test_missing_weight_table_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(layers, "runbook_value", lambda *_args: None)
    with pytest.raises(layers.RunbookDataUnavailable, match="weight table"):
        department_models.baseline_dimension_weights("unknown", "Fresher")


def test_unknown_department_has_no_seniority_emphasis() -> None:
    with pytest.raises(KeyError, match="No Part VI department model"):
        department_models.seniority_emphasis("unknown")


@pytest.mark.parametrize("content", ["broken: [", "[]"])
def test_corrupt_runbook_data_is_refused(tmp_path, monkeypatch, content: str) -> None:
    monkeypatch.setattr(runbook_data, "_DIR", tmp_path)
    (tmp_path / "bands.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(runbook_data.RunbookDataError):
        runbook_data.load("bands")


def test_missing_runbook_data_file_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runbook_data, "_DIR", tmp_path)
    with pytest.raises(runbook_data.RunbookDataError, match="cannot read"):
        runbook_data.load("bands")


def test_runbook_metadata_and_nested_list_citations_are_checked() -> None:
    with pytest.raises(runbook_data.RunbookDataError, match="meta"):
        runbook_data._check_meta("bands", {})
    assert runbook_data._has_source([{"source": "RPN-PHIL-001 section 11"}])
    assert not runbook_data._has_source([{"value": 1}])


def test_pipeline_halt_reads_current_configuration_and_rejects_typos(monkeypatch) -> None:
    stage = pipeline_halt.STAGES[0]
    monkeypatch.delenv(pipeline_halt.ENV_VAR, raising=False)
    assert not pipeline_halt.is_halted(stage)
    monkeypatch.setenv(pipeline_halt.ENV_VAR, f" {stage.upper()} , ")
    assert pipeline_halt.is_halted(stage)
    with pytest.raises(pipeline_halt.PipelineHalted) as caught:
        pipeline_halt.check(stage)
    assert stage in pipeline_halt.http_detail(caught.value)
    assert pipeline_halt.as_dict(caught.value)["stage"] == stage
    monkeypatch.setenv(pipeline_halt.ENV_VAR, pipeline_halt.HALT_ALL)
    assert pipeline_halt.halted_stages() == frozenset(pipeline_halt.STAGES)
    monkeypatch.setenv(pipeline_halt.ENV_VAR, "misspelled_stage")
    with pytest.raises(pipeline_halt.UnknownHaltStage, match="misspelled_stage"):
        pipeline_halt.halted_stages()
    with pytest.raises(pipeline_halt.UnknownHaltStage, match="not a declared"):
        pipeline_halt.check("misspelled_stage")


def test_pipeline_halt_enforcement_records_a_refusal_before_raising(monkeypatch) -> None:
    stage = pipeline_halt.STAGES[0]
    recorded = []

    async def record(halt, **kwargs):
        recorded.append((halt.stage, kwargs["agent"]))

    monkeypatch.setattr(pipeline_halt, "_record", record)
    monkeypatch.delenv(pipeline_halt.ENV_VAR, raising=False)
    asyncio.run(pipeline_halt.enforce(stage, agent="sutra"))
    assert recorded == []
    monkeypatch.setenv(pipeline_halt.ENV_VAR, stage)
    with pytest.raises(pipeline_halt.PipelineHalted):
        asyncio.run(pipeline_halt.enforce(stage, agent="sutra"))
    assert recorded == [(stage, "sutra")]
