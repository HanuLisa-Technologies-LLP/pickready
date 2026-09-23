"""The Company DNA feature is gone, and this is what keeps it gone.

WHY A TEST FOR AN ABSENCE
---------------------------
A feature removed by deleting its route while its modules survive is a feature
one import away from coming back, and it comes back quietly: somebody adds a
handler onto a module that still compiles, and the product has a questionnaire
again with no decision behind it. This file asserts the MODULES are gone, not
the route, because a retained module is what makes a new route cheap.

WHAT REPLACED IT
------------------
The Company Profile. `services/hiring/company_requirements` still holds Gate 1
and still refuses job creation with a message rather than a boolean; what
changed is which table it asks. The gate itself is tested in
`test_workflow_gates.py`; what is asserted here is that it no longer touches a
Company DNA row.

WHAT DELIBERATELY SURVIVED
----------------------------
`services/hiring/observable` holds the observable-evidence detector and the
protected-attribute detector, which were defined inside the instrument and are
not Company DNA concepts: `swot_quality` and `scorecard` both hold their own
inputs to them. `job_scorecard_bindings` survived under a new name with its
rows intact, because it answers "what was this job built on when I applied"
for every candidate already assessed.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Every module the feature owned. Named individually rather than globbed: a
#: glob would pass the day somebody re-added one under a different name.
GONE = (
    "app.services.hiring.company_dna",
    "app.services.hiring.dna_compilation",
    "app.api.company_dna",
    "app.schemas.company_dna",
    "app.models.company_dna",
)


@pytest.mark.parametrize("module", GONE)
def test_the_module_cannot_be_imported(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__(module)


@pytest.mark.parametrize("module", GONE)
def test_the_file_is_not_on_disk_either(module: str) -> None:
    """An import failing is not the same as a file being gone.

    A module left on disk but unreachable through its package is a file the
    next reader finds, reads, and reasonably assumes is live.
    """
    path = REPO / (module.replace(".", "/") + ".py")
    assert not path.exists(), path


def test_the_router_is_not_mounted() -> None:
    from app.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    offending = [p for p in paths if "dna" in p.lower()]
    assert not offending, offending


def test_no_executable_source_still_names_the_feature() -> None:
    """Swept over the tree rather than checked at a call site.

    A rule enforced at one call site is a rule the next file breaks. The sweep
    skips this file (which has to name the thing it forbids), the migration
    that performs the removal, and the Runbook's own amendment log, which is
    where the record of a withdrawal belongs.
    """
    pattern = re.compile(r"company[ _-]?dna", re.I)
    allowed = {
        REPO / "tests" / "test_company_dna_removed.py",
        REPO / "alembic" / "versions" / "0088_remove_company_dna.py",
    }
    offending: list[str] = []
    for root in ("app", "tests", "scripts"):
        for path in (REPO / root).rglob("*.py"):
            if path in allowed or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            # THE WHITESPACE IS NORMALISED FIRST, AND THAT IS NOT A TIDY-UP.
            #
            # This sweep read one LINE at a time until 2026-09-23, so a
            # mention wrapped across a newline never matched a pattern that
            # contains a space. Exactly one had been sitting in
            # `workers/tasks.py` since the removal, describing a compiled
            # Company DNA as a live precondition of Sutra, and it passed
            # every run of this test. A sweep with a blind spot is worse than
            # no sweep, because the green result is what stops anybody
            # looking. Offsets are mapped back so a hit still names a line
            # somebody can open.
            flat: list[str] = []
            offsets: list[int] = []
            for index, character in enumerate(text):
                if character.isspace():
                    if flat and flat[-1] == " ":
                        continue
                    flat.append(" ")
                else:
                    flat.append(character)
                offsets.append(index)
            for match in pattern.finditer("".join(flat)):
                line = text.count("\n", 0, offsets[match.start()]) + 1
                offending.append(
                    f"{path.relative_to(REPO)}:{line}: {match.group(0)}"
                )
    assert not offending, offending


def test_the_task_type_is_not_routed_to_a_model() -> None:
    """A task type left in the routing table is a budget for work nobody does,
    and it would answer `model_for_task` rather than raising."""
    from app.config import llm_providers

    assert "company_dna_intake" not in llm_providers.MODEL_FOR_TASK
    assert "company_dna_intake" not in llm_providers.TASK_TIMEOUTS


def test_gate_one_reads_the_company_profile() -> None:
    """The gate survived; its subject changed. Asserted over the source so a
    future edit cannot quietly repoint it at a table that no longer exists."""
    import inspect

    from app.services.hiring import company_requirements

    source = inspect.getsource(company_requirements)
    assert "Company" in source
    assert "about_company" in source
    assert not re.search(r"company[ _-]?dna", source, re.I), source
