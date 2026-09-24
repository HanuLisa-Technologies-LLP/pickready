"""The unreachable agent subsystems are gone, and this keeps them gone.

Vivekium release, PLAN-p7 WP-B5. Six packages were written, tested and reached
by no route and no worker: the reasoning planner and runner, the experience
memory, the orchestration coordinator, router, enforcement door and versioning
resolver, the agent action ledger, and the three-level degradation layer. Every
unit test of them was green while none of them could run in production, which
is the exact state Part A was in for a whole phase. They were deleted rather
than kept "for later", because a subsystem nothing runs is a subsystem whose
tests describe behaviour the product does not have.

WHAT REPLACED WHAT
------------------
* `orchestration/versioning.resolve_for_application` answered "what was this
  candidate assessed against" and had no production caller. The skills
  snapshot answers it now: `assessment_contract.lock_contract` writes an
  immutable `job_skill_snapshots` row at the first start and binds the
  conversation to it.
* `app/orchestration_checks.py` shrank to `app/import_graph.py`: the
  reachability walk the isolation tests are built on, the agent-identity
  activation check, and the tool-layer invariants, including the one that
  made the action ledger deletable (every registered tool is a bounded read).
* `pickready.revoke_learnings_from_source` was the only task that touched the
  experience memory. It was never dispatched and never scheduled, so there was
  no schedule entry and no Terraform rule to remove. The `agent_learnings` and
  `agent_actions` TABLES stay, as history.
* `services/coalescing` and the tool layer are KEPT: the grading phase wires
  them through `tools.executor`.

The sweep is whitespace-normalised through `tests/removal_sweep.py`, the shape
`test_company_dna_removed.py` established, so a mention wrapped across a line
still matches.
"""
from __future__ import annotations

import importlib.util
import re

from tests.removal_sweep import BACKEND, sweep

APP = BACKEND / "app"

#: The deleted packages and modules, by dotted name.
DELETED_MODULES: tuple[str, ...] = (
    "app.services.reasoning",
    "app.services.memory",
    "app.services.orchestration",
    "app.services.agent_actions",
    "app.services.reliability.degradation",
    "app.orchestration_checks",
    "app.scripts.eval_trajectory",
    "app.services.tools.manifest",
)

#: Every live-code spelling of the deleted subsystems. Path and dotted forms
#: both, because a comment says `services/memory` and an import says
#: `services.memory`, and either one is a live reference.
PATTERN = re.compile(
    r"services[./](?:reasoning|memory|orchestration|agent_actions)\b"
    r"|reliability[./]degradation\b"
    r"|resolve_for_application"
    r"|revoke_learnings_from_source"
    r"|orchestration_checks"
    r"|eval_trajectory"
    r"|REVOKE_AGENT_LEARNINGS"
    r"|\bAgentLearning\b"
    r"|\bAgentAction\b"
)

#: Files this sweep may not flag, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_unreachable_subsystems_removed.py",
)

#: PENDING HAND-OFFS, not permanent exemptions. Each file below is owned by a
#: package running in parallel with this one, so this package may not edit it.
#: The owner (or the orchestrator at merge) removes the reference and deletes
#: the entry in the same commit; `test_every_pending_hand_off_is_still_pending`
#: fails the moment an entry outlives the reference it excuses.
PENDING_HAND_OFFS: dict[str, str] = {
    "app/models/__init__.py": "WP-B2 deletes the AgentLearning and AgentAction exports",
    "app/models/agent.py": "WP-B2 deletes AgentLearning; the table stays",
    "app/models/agent_action.py": "WP-B2 deletes the module; the table stays",
    "app/services/capabilities.py": "WP-B2 deletes REVOKE_AGENT_LEARNINGS with its migration",
    "app/models/assessment.py": "a comment naming the deleted degradation layer",
    "app/services/functional_assessment.py": (
        "a comment naming the deleted degradation layer (Phase 5 owns the file)"
    ),
    "app/models/job_skill_snapshot.py": (
        "a docstring naming the deleted versioning resolver (Phase 1 owns the file)"
    ),
}


def test_the_deleted_modules_do_not_import() -> None:
    importable = [name for name in DELETED_MODULES if importlib.util.find_spec(name)]
    assert not importable, importable


def test_the_deleted_directories_are_gone() -> None:
    for name in ("reasoning", "memory", "orchestration", "agent_actions"):
        assert not (APP / "services" / name).exists(), name
    assert not (APP / "orchestration_checks.py").exists()
    assert not (APP / "services" / "reliability" / "degradation.py").exists()
    assert not (APP / "services" / "tools" / "tool_manifest.json").exists()


def test_no_source_names_a_deleted_subsystem() -> None:
    pending = tuple(APP.parent / rel for rel in PENDING_HAND_OFFS)
    hits = sweep(PATTERN, exempt=EXEMPT + pending)
    assert not hits, hits


def test_every_pending_hand_off_is_still_pending() -> None:
    """A landed hand-off leaves the list, so the list cannot rot into a blanket
    exemption for a file that has since gained a NEW reference."""
    landed = []
    for rel, reason in PENDING_HAND_OFFS.items():
        path = APP.parent / rel
        if not path.exists() or not sweep(PATTERN, roots=(path,)):
            landed.append(f"{rel} ({reason})")
    assert not landed, (
        "These hand-offs have landed; delete their PENDING_HAND_OFFS entries: "
        + "; ".join(landed)
    )


def test_the_tool_manifest_moved_under_tests_and_still_pins() -> None:
    """Moved, not dropped: the pinning test still has a manifest to read."""
    assert (BACKEND / "tests" / "fixtures" / "tool_manifest.json").is_file()
    assert importlib.util.find_spec("tests.support.tool_manifest") is not None


def test_no_production_module_imports_the_test_support_package() -> None:
    hits = sweep(re.compile(r"\btests\.support\b|\bfrom tests\b"), roots=(APP,))
    assert not hits, hits


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the import graph."""
    hits = sweep(re.compile(r"def reachable_modules"), roots=(APP,))
    assert any("import_graph.py" in hit for hit in hits), hits
