"""A job's free-text `level` is read and written by nothing. This keeps it so.

`level` left the Create Job form on 2026-07-28, superseded by the grade and
the experience band, and survived as a column every reader kept consulting:
the relevance ranker matched a candidate against it, the employer page and
the job schemas served it, and a job created since read as blank there. The
Vivekium release (PLAN-p1 3.8) removed it from every job schema and every
reader it owns. THE COLUMN STAYS (CONTRACT S4): old rows keep their value and
nothing reads it.

Three checks: an AST walk over `app/` for an attribute read of `.level` on a
job-shaped name; the job schemas' field sets; and a text sweep of the frontend
for `job.level` / `role.level`. Readers owned by packages running beside this
one are PENDING HAND-OFFS, each named with its owner; an entry whose file no
longer reads the field fails `test_every_pending_hand_off_still_reads_level`,
so an exemption cannot outlive its reason.
"""
from __future__ import annotations

import ast
import re

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import employer_pages as employer_schemas
from app.schemas import jobs as job_schemas
from tests.removal_sweep import BACKEND, REPO, sweep

APP = BACKEND / "app"
#: A name a job is bound to in this codebase. The walk is by NAME because a
#: type is not in the AST; `j` is the SQL alias style some loops use.
_JOB_NAMES = re.compile(r"^(?:job|j|_job|job_row|jobs?_?\w*)$")

#: The one file allowed to mention the attribute: the model declares the
#: column, which stays (CONTRACT S4).
_DECLARES = APP / "models" / "job.py"

#: Readers other packages own, each deleted by its owner. Not permanent.
PENDING_BACKEND = {
    # `services/matching.py` (`_jd_text`) left at the stage 2 integration:
    # Phase 2 WP-B moved matching onto Yukti, which reads no level.
    APP / "services" / "functional_assessment.py": "Phase 7 (`infer_grade`)",
}

_FRONTEND_PATTERN = re.compile(r"\b(?:job|role)\??\.level\b")
#: Every frontend reader has landed (PLAN-p1 WP-D and Phase 6 WP6-E): the job
#: list, the job page, the apply page, the employer page, the orphaned jobs
#: list and the candidate portal's New Jobs card no longer read the field.
PENDING_FRONTEND: tuple = ()


def _level_reads(path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "level"
        and isinstance(node.value, ast.Name)
        and _JOB_NAMES.match(node.value.id)
    )


def _backend_reads() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in APP.rglob("*.py"):
        if "__pycache__" in path.parts or path == _DECLARES:
            continue
        lines = _level_reads(path)
        if lines:
            found[str(path.relative_to(BACKEND))] = lines
    return found


def test_no_backend_reader_takes_a_jobs_level() -> None:
    pending = {str(path.relative_to(BACKEND)) for path in PENDING_BACKEND}
    offending = {path: lines for path, lines in _backend_reads().items() if path not in pending}
    assert not offending, offending


def test_every_pending_hand_off_still_reads_level() -> None:
    stale = [
        f"{path.relative_to(BACKEND)} ({owner})"
        for path, owner in PENDING_BACKEND.items()
        if not path.exists() or not _level_reads(path)
    ]
    stale += [
        str(path.relative_to(REPO))
        for path in PENDING_FRONTEND
        if not path.exists() or not sweep(_FRONTEND_PATTERN, roots=(path,))
    ]
    assert not stale, f"these hand-offs have landed; delete their entries: {stale}"


def test_the_walk_is_not_vacuous() -> None:
    """The AST walk must still find the readers it is told are pending, or it
    has quietly stopped seeing the attribute at all."""
    assert _backend_reads(), "the walk found no `.level` read anywhere"


def test_no_job_schema_carries_level() -> None:
    carrying = sorted(
        f"{module.__name__}.{name}"
        for module in (job_schemas, employer_schemas)
        for name, model in vars(module).items()
        if isinstance(model, type)
        and issubclass(model, BaseModel)
        and model.__module__ == module.__name__
        and (name.startswith(("Job", "Public", "Publish", "Employer")))
        and "level" in model.model_fields
    )
    assert not carrying, carrying


def test_the_metadata_patch_refuses_level_loudly() -> None:
    """`extra="forbid"`: an old client sending `level` is a 422, never a field
    silently ignored while the screen believes it saved."""
    with pytest.raises(ValidationError):
        job_schemas.JobPatchIn.model_validate({"level": "Senior"})


def test_no_frontend_source_reads_a_jobs_level() -> None:
    exempt = set(PENDING_FRONTEND)
    hits = sweep(_FRONTEND_PATTERN, exempt=exempt, roots=(
        REPO / "frontend" / "app",
        REPO / "frontend" / "components",
        REPO / "frontend" / "lib",
    ))
    assert not hits, hits
