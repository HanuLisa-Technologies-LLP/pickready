"""The Tatva matrix editor, its compiler and the Matching Categories editor are
gone. This keeps them gone.

Owner decision D1 (Vivekium release): the recruiter no longer sees a matrix.
Sutra drafts skills (`services/hiring/sutra`), the team edits and saves them
(`services/skills`), and the saved set is the assessment contract
(`services/assessment_contract`). Deleted with the editor, all of it:

* the `/framework` routes (add, bulk, edit, delete, finalize, reopen, reorder)
  and the job-setup route that repaired a missing matrix by dispatching;
* the Matching Categories routes, their generator, its prompt and its task;
* the matrix compiler and freeze (`scorecard`'s write half),
  `hiring/transformation.py`, `hiring/swot_quality.py`, Drishti's weighting, the
  naming prompt;
* the tasks `compile_tatva_matrix`, `generate_matching_categories`, the two
  alias names that forwarded to it, and the technical-questions reminder with
  its setting.

A deleted feature is deleted everywhere, so this is a whitespace-normalised
sweep of the whole live tree (`tests/removal_sweep`), plus the route table.
"""
from __future__ import annotations

import importlib.util
import re

from tests.removal_sweep import BACKEND, REPO, sweep

#: Case-SENSITIVE on purpose for the one prose pattern: "Tatva Assessment
#: matrix" was the editor's heading, while a report catalogue entry titled
#: "Tatva Assessment Matrix Document" is a separate owner's surface.
PATTERN = re.compile(
    "|".join(
        (
            r"/framework/(?:finalize|reopen|reorder|bulk)",
            r"/categories/finalize",
            r"compile_tatva_matrix",
            r"generate_ppi_framework",
            r"generate_technical_questions",
            r"generate_matching_categories",
            r"remind_unapproved_technical_questions",
            r"technical_review_reminder_hours",
            r"compile_matrix\(",
            r"_enrich_reviewed_rows",
            r"scorecard\.freeze",
            r"sutra_competency_naming",
            r"matching_categories_system",
            r"generate_categories\(",
            r"emphasis_map",
            r"swot_quality",
            r"hiring\.transformation",
            r"MatchingCategoriesCard",
            r"JobSetupReview",
            r"Tatva Assessment matrix",
        )
    )
)

#: Every exemption, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_tatva_matrix_editor_removed.py",
    # These two assert, by name, that the deleted symbols are ABSENT
    # (`hasattr(...)` is False, the prompt is not registered). A test of an
    # absence has to spell the absence.
    BACKEND / "tests" / "test_ppi.py",
    BACKEND / "tests" / "test_drishti.py",
)

#: Every hand-off has landed: the frontend package (PLAN-p1 WP-D) deleted the
#: matrix editor and the categories card and replaced the job page's setup
#: section, and WP-E moved the harness onto the Skills step. The check below
#: stays so a future hand-off entry cannot outlive its reason.
PENDING_HAND_OFFS = EXEMPT[3:]


def test_no_live_source_names_the_retired_editor_or_its_machinery() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT)
    assert not hits, hits


def test_every_pending_hand_off_still_has_something_to_hand_off() -> None:
    """A pending exemption whose file no longer mentions anything is stale."""
    stale = [
        str(path.relative_to(REPO))
        for path in PENDING_HAND_OFFS
        if not path.exists() or not sweep(PATTERN, roots=(path,))
    ]
    assert not stale, (
        f"these hand-offs have landed; delete their EXEMPT entries: {stale}"
    )


def test_the_deleted_modules_cannot_be_imported() -> None:
    for module in (
        "app.services.hiring.transformation",
        "app.services.hiring.swot_quality",
        "app.scripts.worked_example",
        "app.scripts.validate_ppi",
    ):
        assert importlib.util.find_spec(module) is None, module
    for prompt in ("sutra_competency_naming", "matching_categories_system"):
        assert not (BACKEND / "app" / "prompts" / f"{prompt}.txt").exists(), prompt


def test_the_deleted_tasks_are_not_registered() -> None:
    from app.workers import registry

    registry.resolve("pickready.draft_job_skills")  # imports the task modules
    for name in (
        "pickready.compile_tatva_matrix",
        "pickready.generate_matching_categories",
        "pickready.generate_ppi_framework",
        "pickready.generate_technical_questions",
        "pickready.remind_unapproved_technical_questions",
    ):
        assert name not in registry._REGISTRY, name


def test_no_registered_route_edits_a_matrix_or_a_category_list() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])
    offending = sorted(
        path for path in paths if "/framework" in path or "/categories" in path
    )
    assert not offending, offending


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the live tree."""
    hits = sweep(re.compile(r"draft_job_skills"), roots=(BACKEND / "app",))
    assert any("tasks.py" in hit for hit in hits), hits
