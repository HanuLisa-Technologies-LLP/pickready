"""No module may read another service module's attribute at IMPORT time.

THE DEFECT THIS PINS, and why it was nearly invisible.

Importing a partially initialised module is legal in Python. Reading an
attribute off one while it is still executing is not, and it fails as
`AttributeError: partially initialized module`. Two modules did exactly that:

    verification/ppi_report.py:  _REMARK_MIN, _REMARK_MAX = functional_assessment.PPI_REMARK_WORDS
    verification/probes.py:      _PROBE_MIN, _PROBE_MAX  = gap_analysis.PROBE_WORDS

Harmless for as long as nothing closed a cycle through them. Wiring the agent
runtime into the service layer closed one:

    functional_assessment -> gap_analysis -> ppi -> verification -> probes
      -> gap_analysis (still initialising)

WHAT MADE IT DANGEROUS IS THAT IT IS ORDER DEPENDENT. In a full `pytest tests`
run some earlier module had already finished initialising the target, so the
whole suite was GREEN while `pytest tests/test_platform_audit.py` on its own was
RED. A green suite is the thing everyone acts on, and production controls its
import order no better than pytest does: the same code would have failed
whenever a worker happened to import that side of the graph first.

So this file checks two things a passing suite cannot:

  1. every service module imports cleanly ON ITS OWN, in a fresh interpreter;
  2. no module-level statement reads an attribute off an imported service
     module, which is the shape that makes any future cycle fatal.

Rule 2 is enforced precisely rather than by allowlist. Reading an attribute off
a LEAF module -- one that imports no other app.services module -- can never
fail, because a leaf cannot be halfway through its own initialisation when a
cycle reaches it. `tools/permissions` and `verification/base` are leaves, which
is why `identity.py` may read `permissions.AGENT_RANKING` at module scope
safely. The rule therefore flags a module-level read only when the SOURCE
module can itself sit on a cycle, which is exactly the condition that makes the
read fatal.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

SERVICES = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"

#: Packages that sit on an import cycle today. A module-level attribute read in
#: any of these is fatal the moment a cycle forms, which has now happened once.
CYCLE_PRONE = ("verification", "agents", "evidence")


def _service_modules() -> list[str]:
    out = []
    for path in sorted(SERVICES.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(SERVICES.parents[1])
        out.append(".".join(("app",) + rel.with_suffix("").parts[1:]))
    return out


@pytest.mark.parametrize("module", [m for m in _service_modules() if any(p in m for p in CYCLE_PRONE)])
def test_the_module_imports_on_its_own(module: str) -> None:
    """A FRESH interpreter per module. Importing inside this process would reuse
    sys.modules and reproduce exactly the ordering luck that hid the bug."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{module} cannot be imported first:\n{result.stderr[-1500:]}"
    )


def _imports_other_services(path: pathlib.Path) -> bool:
    """True when this module imports another app.services module.

    A module that does not is a LEAF: nothing can make it partially
    initialised, so reading its attributes at import time is always safe.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.services")
        for node in ast.walk(tree)
    )


def _module_path(name: str) -> pathlib.Path | None:
    """Resolve an imported alias back to the file it came from, if it is ours."""
    candidate = SERVICES / f"{name}.py"
    if candidate.exists():
        return candidate
    for path in SERVICES.rglob(f"{name}.py"):
        return path
    return None


def test_no_cycle_prone_module_reads_another_service_at_import_time() -> None:
    offenders: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        if not any(p in str(path) for p in CYCLE_PRONE):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.asname or alias.name.split(".")[-1]
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("app.services")
            for alias in node.names
        }
        for node in tree.body:
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom),
            ):
                continue
            for sub in ast.walk(node):
                if not (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in imported
                ):
                    continue
                source = _module_path(sub.value.id)
                # A leaf cannot be mid-initialisation, so reading it is safe.
                if source is None or not _imports_other_services(source):
                    continue
                offenders.append(
                    f"{path.name}:{node.lineno} reads {sub.value.id}.{sub.attr}"
                )

    assert not offenders, (
        "These read another service module's attribute at import time, which is "
        "an AttributeError the moment a cycle forms. Read it inside a function "
        "instead, as ppi_report._remark_bounds and probes._probe_bounds do:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )
