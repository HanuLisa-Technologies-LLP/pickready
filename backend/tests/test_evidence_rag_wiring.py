"""The Evidence RAG reaches all three agents, each through one named call (WP5-D).

CONTRACT v2 P5 / PLAN-p5 C4: `services/evidence_retrieval` is the ONE entry
point for Vaada, Miti and Siddhi, and it goes through the typed tool layer.
`tests/test_ai_reachability.py` proves the package is reachable; it cannot pin
these call sites, because they live beside the entry point under
`app/services` and so count as "inside the package" to its caller check. This
file pins each one by AST, and names what fails silently without it:

  * Vaada (question writing) never sees the resume passage a skill is about,
    and project evidence reaches the prompt without the tool layer's checks.
  * Miti's item judge is never shown the candidate's other answers.
  * Siddhi's support check never looks past the cited answer.

And none of the three agents' own packages imports the retrieval layer: the
reader is INJECTED, so Miti and Siddhi cannot bypass the tool layer
(CONTRACT v4 item 2).
"""
from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def _tree(relative: str) -> ast.AST:
    return ast.parse((APP / relative).read_text(encoding="utf-8"))


def _attribute_uses(tree: ast.AST, owner: str, name: str) -> list[ast.Attribute]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == owner
    ]


def test_miti_is_handed_the_transcript_reader_by_the_grading_stage() -> None:
    tree = _tree("services/assessment_pipeline/grading.py")
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate_application"
    ]
    assert len(calls) == 1
    [passages] = [kw.value for kw in calls[0].keywords if kw.arg == "passages"]
    assert isinstance(passages, ast.Attribute)
    assert passages.attr == "transcript_passages_for_skill"
    assert isinstance(passages.value, ast.Name) and passages.value.id == "evidence_retrieval"


def test_siddhi_is_handed_the_support_reader_by_the_composition_stage() -> None:
    tree = _tree("services/assessment_pipeline/composition.py")
    assert _attribute_uses(tree, "evidence_retrieval", "support_passages_for_statement")


def test_vaada_reads_the_resume_and_projects_through_the_entry_point() -> None:
    tree = _tree("services/assessment_questions/generate.py")
    assert _attribute_uses(tree, "evidence_retrieval", "resume_passages_for_skill")
    assert _attribute_uses(tree, "evidence_retrieval", "project_evidence_for_candidate")
    source = (APP / "services/assessment_questions/generate.py").read_text(encoding="utf-8")
    assert "candidate_project_context" not in source, (
        "project evidence is read through the tool layer, never the module directly"
    )


def _imports(path: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_no_agent_package_reads_the_retrieval_layer_directly() -> None:
    """Miti and Siddhi are handed their readers; they import neither the
    entry point nor `services.rag`, so a read cannot skip the capability,
    stage and tenant checks the tool layer runs before any row is read."""
    offenders: list[str] = []
    for package in ("miti", "siddhi"):
        for path in (APP / "services" / package).rglob("*.py"):
            for module in _imports(path):
                if module.startswith(("app.services.rag", "app.services.evidence_retrieval")):
                    offenders.append(f"{path.relative_to(BACKEND)}: {module}")
    assert not offenders, offenders
