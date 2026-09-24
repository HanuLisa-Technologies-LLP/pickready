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

import app.services.functional_assessment as fa
from app.config import llm_providers
from app.prompts import registry


def test_the_prompt_versions_are_real_registry_labels() -> None:
    """Each label must resolve through the registry, in name@declared+digest
    form, so the stored value describes the prompt files in the running image
    rather than a string somebody typed once."""
    value = fa._report_prompt_versions()
    parts = value.split("; ")
    assert len(parts) == 2, value
    for part in parts:
        name, _, version = part.partition("@")
        assert version == registry.version(name), part
        assert re.fullmatch(r"\d+\+[0-9a-f]+", version), part


def test_the_model_id_comes_from_the_closed_mapping() -> None:
    """The value written is resolved through `model_for`, so a report can only
    ever name a model the closed mapping actually routes to. The mapping is
    the authority; this test just proves the task key is a real one."""
    assert llm_providers.model_for("report_synthesis") in (
        set(llm_providers.MODEL_FOR_TASK.values())
    )


def test_provenance_is_gated_on_the_model_backed_mode_in_source() -> None:
    """Asserted over the source, the way this suite pins write-site rules.

    The condition that guards `model_id` must be the same comparison that
    guards `needs_human_review`'s fallback arm: equality with the ONE
    known-good mode, never inequality with a list of bad ones, so a future
    third scoring mode defaults to honest NULL provenance rather than to a
    claimed model.
    """
    import inspect

    source = inspect.getsource(fa)
    assert re.search(
        r'"model_id":\s*\(\s*\n?\s*llm_providers\.model_for\("report_synthesis"\)'
        r"\s*\n?\s*if scoring_mode == MODE_MITI",
        source,
    ), "model_id must be written only for a model-backed run"
    assert re.search(
        r'"prompt_version":\s*\(\s*\n?\s*_report_prompt_versions\(\)'
        r"\s*if scoring_mode == MODE_MITI",
        source,
    ), "prompt_version must be written only for a model-backed run"


def test_the_columns_exist_and_are_nullable() -> None:
    """Nullable is load-bearing: every pre-0094 row and every fallback run is
    an honest NULL, and a NOT NULL constraint would force a lie into both."""
    from app.models.assessment import FunctionalSkillsReport

    table = FunctionalSkillsReport.__table__
    for name in ("model_id", "prompt_version"):
        assert name in table.columns, name
        assert table.columns[name].nullable, name
