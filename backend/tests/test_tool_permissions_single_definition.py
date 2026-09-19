"""Each agent constant is defined exactly once in the module that enforces reach.

RPN-AI-UP-001 section 2.3 found `AGENT_BODHA` through `NAMED_AGENTS` declared
TWICE in `services/tools/permissions.py`, at roughly lines 161 to 173 and again
at 206 to 218. The duplication was real and is removed; this file is what stops
it coming back.

WHY A DUPLICATED CONSTANT BLOCK IS WORTH A TEST OF ITS OWN
----------------------------------------------------------
It has no symptom. Python takes the last assignment, both copies held the same
VALUES, and every importer therefore behaved identically -- which is precisely
why it survived a release. What differed was the documentation: `AGENT_VAADA`
read "evidence graphs" in the first copy and "the candidate conversational
agent" in the second, so the module that decides what each agent may reach
carried two answers to what one of them is, and a reader who scrolled to the
first was wrong.

The failure it is actually insurance against is the next edit, not the last
one. Someone renames an agent, finds the declaration, changes it, and the copy
forty lines away silently overrides the change -- at which point a tool grant
is keyed on a name nothing else uses and the agent's reach quietly becomes
whatever the empty default is. That is a permission decision made by scroll
position.

This is "one implementation per concept" applied inside the enforcement module,
and it is checked by AST rather than by grep so a constant assigned inside a
class body or an `if TYPE_CHECKING:` block is still counted once at module
level.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PERMISSIONS = BACKEND / "app" / "services" / "tools" / "permissions.py"

#: Every module-level name in this file that must be assigned exactly once.
#: The six named agents plus the tuple over them, and the tool-grant table
#: itself -- a second `AGENT_TOOLS` would be the same defect with teeth, since
#: the losing copy's grants would simply vanish.
SINGLY_DEFINED: tuple[str, ...] = (
    "AGENT_BODHA",
    "AGENT_SUTRA",
    "AGENT_YUKTI",
    "AGENT_VAADA",
    "AGENT_MITI",
    "AGENT_SIDDHI",
    "NAMED_AGENTS",
    "AGENT_TOOLS",
)


def _module_level_assignments() -> dict[str, list[int]]:
    """name -> the line numbers it is assigned on, at module level only."""
    tree = ast.parse(PERMISSIONS.read_text(encoding="utf-8"), filename=str(PERMISSIONS))
    seen: dict[str, list[int]] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                seen.setdefault(target.id, []).append(node.lineno)
    return seen


@pytest.mark.parametrize("name", SINGLY_DEFINED)
def test_the_constant_is_assigned_exactly_once(name: str) -> None:
    assignments = _module_level_assignments().get(name, [])
    assert assignments, (
        f"{name} is not assigned at module level in permissions.py. If it was "
        "renamed, rename it here too; if it was deleted, delete it here."
    )
    assert len(assignments) == 1, (
        f"{name} is assigned {len(assignments)} times in permissions.py, at "
        f"lines {assignments}. Python keeps the last one, so the earlier "
        "declaration is documentation that lies about the value in force. "
        "Delete the redundant block rather than keeping both in step by hand."
    )


def test_named_agents_lists_every_agent_constant_and_nothing_else() -> None:
    """The tuple and the constants cannot drift apart.

    Deleting one duplicated block is exactly the moment `NAMED_AGENTS` could
    silently lose a member, because the tuple was duplicated alongside the
    constants it is built from."""
    from app.services.tools import permissions

    expected = {
        permissions.AGENT_BODHA,
        permissions.AGENT_SUTRA,
        permissions.AGENT_YUKTI,
        permissions.AGENT_VAADA,
        permissions.AGENT_MITI,
        permissions.AGENT_SIDDHI,
    }
    assert set(permissions.NAMED_AGENTS) == expected
    assert len(permissions.NAMED_AGENTS) == len(expected), (
        "NAMED_AGENTS carries a duplicate member; a set comparison alone would "
        "not have noticed."
    )
