"""A delivered report records which model and prompts produced it (0094).

WHY THIS EXISTS
-----------------
A report is immutable and client-facing, and before 0094 it could not be
replayed or audited against the configuration that wrote it: "which delivered
reports did prompt version N reach" was answerable only from deploy
timestamps, and a timestamp is not evidence that work happened.

WHAT IS PINNED, AND THE DIRECTION THAT MATTERS MOST
-----------------------------------------------------
The FALLBACK direction. A deterministic-fallback report must carry NULL for
both columns: no model and no versioned prompt produced it, and a provenance
field naming one would be manufactured provenance, the same class of failure
as backfilling old rows with a plausible id.
"""
from __future__ import annotations

import re

from app.services.assessment_pipeline import persistence
from app.config import llm_providers
from app.prompts import registry


def test_the_prompt_versions_are_real_registry_labels() -> None:
    """Each label must resolve through the registry, in name@declared+digest
    form, so the stored value describes the prompt files in the running image
    rather than a string somebody typed once."""
    from app.services.assessment_pipeline.types import ProvenanceRecorder

    recorder = ProvenanceRecorder()
    recorder.model_call("report_synthesis", "report_gap_probes")
    recorder.model_call("report_synthesis", "report_gap_probes")
    value = recorder.prompt_version()
    parts = value.split("; ")
    assert len(parts) == 1, "a repeated call is recorded once"
    for part in parts:
        name, _, version = part.partition("@")
        assert version == registry.version(name), part
        assert re.fullmatch(r"\d+\+[0-9a-f]+", version), part


def test_a_run_with_no_accepted_model_call_claims_no_model() -> None:
    """THE FALLBACK DIRECTION. Templates are recorded as templates and name no
    model and no prompt, so a report no model wrote stores NULL for both."""
    from app.services.assessment_pipeline.types import ProvenanceRecorder

    recorder = ProvenanceRecorder()
    recorder.template("remark:Distributed Systems")
    recorder.template("probes:Kafka")
    assert recorder.model_id() is None
    assert recorder.prompt_version() is None
    assert recorder.as_json() == {
        "models": [],
        "prompts": [],
        "templates": ["remark:Distributed Systems", "probes:Kafka"],
    }


def test_the_report_row_takes_its_provenance_from_the_recorder() -> None:
    """Asserted over the source, the way this suite pins write-site rules. The
    columns used to be stamped from the scoring mode, so a run whose every
    remark fell back to a template still named the writing model."""
    import inspect

    source = inspect.getsource(persistence.write_report)
    assert '"model_id": provenance.model_id()' in source
    assert '"prompt_version": provenance.prompt_version()' in source
    assert '"generation_provenance_json": provenance.as_json()' in source


def test_the_model_id_comes_from_the_closed_mapping() -> None:
    """The value written is resolved through `model_for`, so a report can only
    ever name a model the closed mapping actually routes to. The mapping is
    the authority; this test just proves the task key is a real one."""
    assert llm_providers.model_for("report_synthesis") in (
        set(llm_providers.MODEL_FOR_TASK.values())
    )


def test_the_columns_exist_and_are_nullable() -> None:
    """Nullable is load-bearing: every pre-0094 row and every fallback run is
    an honest NULL, and a NOT NULL constraint would force a lie into both."""
    from app.models.assessment import FunctionalSkillsReport

    table = FunctionalSkillsReport.__table__
    for name in ("model_id", "prompt_version", "generation_provenance_json"):
        assert name in table.columns, name
        assert table.columns[name].nullable, name
