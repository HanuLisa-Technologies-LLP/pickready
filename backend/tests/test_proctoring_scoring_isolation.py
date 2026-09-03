"""Proctoring touches no score, and nothing that scores touches it (P3).

    P3: "Proctoring never affects any score or ranking. It must not feed into
     the AI Hiring Score, the Ready Pick Score, the Executive Profile
     evaluation, candidate ranking, or any sorting. It is reported separately
     and only."

    Section 14: "Proctoring output does not touch any score or ranking path,
     verified by test."

TWO DIRECTIONS, AND BOTH ARE NEEDED
------------------------------------
Outward: no scorer, no matrix, no ranking query imports
`app.services.proctoring`. If one did, a warning count would be one attribute
access away from a weight, and nothing structural would stop the next person
making that access.

Inward: the proctoring package imports no scorer. That is the half that is
easy to skip and the one that actually holds the line, because the tempting
change is not "let the scorer read the warnings", it is "let the proctoring
report mention the grade", and once the import exists the dependency runs
both ways in review.

The report itself is checked for numbers by `numbers.scan` in
`test_proctoring_report.py`; this file is about the import graph.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
PACKAGE = APP / "services" / "proctoring"

PROCTORING_MODULE = "app.services.proctoring"

#: The ONLY modules outside the package that may import it, each with the
#: reason it is allowed. Anything else is a scoring or ranking surface.
PERMITTED_IMPORTERS: dict[str, str] = {
    "api/proctoring.py": "the routes themselves",
    "api/assessments.py": "the gate on the conversation, and the report join",
    "services/report_pdf.py": "renders the report's final section",
    "workers/tasks.py": "the three proctoring tasks",
    "schemas/proctoring.py": "reads the event vocabulary for its validator",
    "schemas/jobs.py": "reads the warning-policy vocabulary",
    "api/jobs.py": "reads the warning-policy default",
    "main.py": "mounts the router",
}

#: Packages the proctoring code must never reach into. Every one of them
#: decides, weights or orders something about a candidate.
FORBIDDEN_TARGETS = (
    "app.services.functional_assessment",
    "app.services.miti",
    "app.services.siddhi",
    "app.services.matching",
    "app.services.rating",
    "app.services.tiers",
    "app.services.hiring",
    "app.services.dashboard",
    "app.services.job_candidates",
    "app.services.job_relevance",
    "app.services.ppi",
    "app.services.gap_analysis",
)


def _python_files() -> list[pathlib.Path]:
    return [p for p in sorted(APP.rglob("*.py")) if "__pycache__" not in p.parts]


def _imports(path: pathlib.Path) -> set[str]:
    """Every module this file imports, at module scope or inside a function.

    Deliberately includes deferred imports. A scorer that reached for the
    proctoring package inside a function would be exactly as coupled as one
    that imported it at the top, and rather harder to notice.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_sweep_reads_the_whole_app_tree() -> None:
    files = _python_files()
    assert len(files) > 200, len(files)
    assert any(p.name == "functional_assessment.py" for p in files)


def test_no_scoring_or_ranking_module_imports_proctoring() -> None:
    """The outward direction. A new importer fails here by PATH, so the
    failure names the module that would have coupled a warning to a grade."""
    offenders: list[str] = []
    for path in _python_files():
        relative = path.relative_to(APP).as_posix()
        if relative.startswith("services/proctoring/") or relative.startswith("models/"):
            continue
        if relative in PERMITTED_IMPORTERS:
            continue
        if any(name.startswith(PROCTORING_MODULE) for name in _imports(path)):
            offenders.append(relative)
    assert not offenders, (
        "P3: proctoring must not feed any score or ranking. These import it "
        "and are not on the permitted list: " + ", ".join(offenders)
    )


def test_the_permitted_importers_all_still_exist() -> None:
    """A stale allowlist is how a ratchet stops ratcheting: the next real
    offender happens to share a path with something long gone and passes."""
    missing = [name for name in PERMITTED_IMPORTERS if not (APP / name).exists()]
    assert not missing, missing


def test_the_scorer_specifically_does_not_import_proctoring() -> None:
    """Named rather than left to the sweep, because this is THE file the rule
    exists about: it is what turns answers into an item score."""
    imports = _imports(APP / "services" / "functional_assessment.py")
    assert not any(name.startswith(PROCTORING_MODULE) for name in imports)


@pytest.mark.parametrize(
    "module",
    ["services/miti", "services/siddhi", "services/hiring", "services/matching.py",
     "services/rating.py", "services/tiers.py", "services/dashboard.py"],
)
def test_no_grading_surface_reaches_proctoring(module: str) -> None:
    target = APP / module
    paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
    for path in paths:
        if "__pycache__" in path.parts or not path.exists():
            continue
        assert not any(
            name.startswith(PROCTORING_MODULE) for name in _imports(path)
        ), path.relative_to(APP).as_posix()


@pytest.mark.parametrize(
    "path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name
)
def test_proctoring_imports_nothing_that_scores(path: pathlib.Path) -> None:
    """The inward direction. The tempting change is not "let the scorer read
    the warnings", it is "let the report mention the grade"."""
    offenders = sorted(
        name for name in _imports(path)
        if any(name.startswith(target) for target in FORBIDDEN_TARGETS)
    )
    assert not offenders, f"{path.name} imports a scoring module: {offenders}"


def test_the_import_detector_sees_a_deferred_import(tmp_path: pathlib.Path) -> None:
    """A guard on the guard: an import inside a function must count, or the
    sweep would miss the easiest way to couple the two."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def score():\n"
        "    from app.services.proctoring import report\n"
        "    return report\n",
        encoding="utf-8",
    )
    assert any(name.startswith(PROCTORING_MODULE) for name in _imports(sample))


def test_the_assessment_api_uses_proctoring_only_as_a_gate_and_a_report() -> None:
    """`api/assessments.py` is on the permitted list, and the permission is
    narrow: it may ask whether the conversation may proceed and attach the
    finished report. It may not read a warning count, an event or a session's
    behaviour profile into anything it computes."""
    source = (APP / "api" / "assessments.py").read_text(encoding="utf-8")
    for banned in (
        "warnings_used",
        "ProctoringEvent",
        "behaviour_profile_json",
        "proctoring_session.outcome",
    ):
        assert banned not in source, (
            f"api/assessments.py reads {banned!r}. Proctoring state must not "
            "reach the code that decides what a candidate is asked or scored."
        )


def test_no_report_dimension_or_score_field_mentions_proctoring() -> None:
    """The report payload's rated half must have no proctoring-derived field.
    `FunctionalReportOut.proctoring` is the ONE place it appears, and it is a
    words-only section rather than an input to anything."""
    from app.schemas.assessments import DimensionOut, FunctionalReportOut

    assert "proctoring" in FunctionalReportOut.model_fields
    for name in DimensionOut.model_fields:
        assert "proctor" not in name.lower(), name
    for name, field in FunctionalReportOut.model_fields.items():
        if name == "proctoring":
            continue
        assert "proctor" not in name.lower(), name
