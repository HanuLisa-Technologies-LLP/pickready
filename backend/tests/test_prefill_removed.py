"""Pre-fill is gone: every item of every assessment is asked.

WHAT WAS REMOVED, AND WHY
-------------------------
Until 2026-09-25 a question could be ANSWERED BEFORE IT WAS ASKED, two ways:
the resume pre-fill (C2, vivekium feature 2) recorded an answer for a
criterion the resume already evidenced, and the portable layer (change request
23) did the same from facts an earlier employer's assessment had gathered. A
ceiling then trimmed whatever was still to be asked. Appendix B of the
Vivekium release reverses all three: the budget is one question per skill,
the mix is served exactly, and a candidate is assessed on what they answer.
So the modules, the ceiling, the conversation's replay of pre-filled answers
and the consent item that authorised cross-employer reuse are deleted, not
switched off.

WHAT SURVIVES, DELIBERATELY
---------------------------
`candidate_questions.prefilled_answer` / `prefill_source` and the table
`portable_evidence_items` stay as columns and a table (S4: nothing that may
hold a customer row is dropped irreversibly). Nothing writes them. The retired
consent key is named in `consent_catalog.RETIRED_KEYS` so a stored consent
act under it still reads as what it was.

THE SWEEP normalises whitespace and maps offsets back to a line, the
technique the 2026-09-23 removal sweep adopted (CLAUDE.md), because a sweep
with a one-line blind spot passes while the thing it forbids sits in a
wrapped comment.
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
THIS_FILE = pathlib.Path(__file__).resolve()

#: The removed feature's identifiers. `portable_evidence` is matched as the
#: MODULE name only: `portable_evidence_items` is the surviving table.
PATTERN = re.compile(
    r"\bresume_prefill\b|\bprefilled_answer\b|\bprefill_source\b"
    r"|\bportable_evidence\b(?!_items)|\bportable_coverage\b|\bportable_evidence_nodes\b"
    r"|\bportable_node\b|\bKIND_PORTABLE\b|\btrim_to_ceiling\b"
    r"|_consume_prefilled_questions|\bcross_employer_reuse_allowed\b"
    r"|\bCROSS_EMPLOYER_EVIDENCE_REUSE\b|\bPREFILL_SOURCE_\w*|\bassessment_question_ceiling\b"
    r"|ASSESSMENT_QUESTION_CEILING"
)

SCOPE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend/app", (".py", ".txt")),
    ("backend/tests", (".py",)),
    ("backend/harness", (".py", ".yaml")),
    ("backend/scripts", (".py", ".sh")),
    ("frontend/app", (".ts", ".tsx")),
    ("frontend/components", (".ts", ".tsx")),
    ("frontend/lib", (".ts", ".tsx")),
)
SINGLE_FILES = (".env.example",)

#: Permanent exemptions, each with its reason. Migrations are history: they
#: created the columns and the table that survive.
EXEMPT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("backend/alembic/versions/", "migrations are history"),
    (
        "backend/tests/test_question_generation_contract.py",
        "reads the two surviving columns from the TABLE to prove nothing writes them",
    ),
)

#: Files another work package still has to clean, and who. MUST SHRINK TO
#: EMPTY at integration; a stale entry fails below, so the ratchet stays tight.
PENDING: dict[str, str] = {
    # EMPTY since the grading split (PLAN-p5 WP5-D): `portable_node` and
    # `KIND_PORTABLE` were deleted from siddhi/evidence.py and siddhi/trail.py.
}

#: Modules that must not come back.
GONE_MODULES = (
    "app.services.resume_prefill",
    "app.services.portable_evidence",
)


def _flatten(text: str) -> tuple[str, list[int]]:
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
    return "".join(flat), offsets


def hits_in(text: str) -> list[tuple[int, str]]:
    flat, offsets = _flatten(text)
    return [
        (text.count("\n", 0, offsets[match.start()]) + 1, match.group(0))
        for match in PATTERN.finditer(flat)
    ]


def _files() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root, suffixes in SCOPE:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix in suffixes
                and "node_modules" not in path.parts
                and "__pycache__" not in path.parts
                and path.resolve() != THIS_FILE
            ):
                found.append(path)
    for single in SINGLE_FILES:
        if (REPO / single).is_file():
            found.append(REPO / single)
    return found


def _sweep() -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for path in _files():
        relative = path.relative_to(REPO).as_posix()
        if any(relative.startswith(prefix) for prefix, _ in EXEMPT_PREFIXES):
            continue
        found = hits_in(path.read_text(encoding="utf-8"))
        if found:
            result[relative] = found
    return result


def test_the_sweep_has_something_to_sweep() -> None:
    """A sweep over an empty file list passes for ever."""
    assert len(_files()) > 500


def test_no_live_source_names_the_prefill_machinery() -> None:
    found = _sweep()
    unexpected = {path: lines for path, lines in found.items() if path not in PENDING}
    assert not unexpected, f"pre-fill is named in live source: {unexpected}"
    stale = sorted(set(PENDING) - set(found))
    assert not stale, (
        "these files no longer name the removed machinery; remove them from "
        f"PENDING so the ratchet stays tight: {stale}"
    )


def test_the_sweep_matches_across_a_line_break_and_spares_the_table() -> None:
    sample = "x = 1\n# the resume_prefill\n    module\ny = portable_evidence_items\n"
    assert hits_in(sample) == [(2, "resume_prefill")]
    assert hits_in("from app.services import portable_evidence\n") == [
        (1, "portable_evidence")
    ]


@pytest.mark.parametrize("module", GONE_MODULES)
def test_the_module_cannot_be_imported_and_is_not_on_disk(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
    path = REPO / "backend" / (module.replace(".", "/") + ".py")
    assert not path.exists(), path


def test_the_ceiling_setting_is_gone() -> None:
    from app.core.config import Settings

    assert "assessment_question_ceiling" not in Settings.model_fields


def test_the_reuse_consent_is_retired_not_offered() -> None:
    """A retired key is never offered again, and it is named as retired so a
    stored act under it reads as what it was rather than as a corrupt row."""
    from app.services import consent_catalog

    key = "cross_employer_evidence_reuse"
    assert key not in consent_catalog.ITEMS_BY_KEY
    assert key not in consent_catalog.STAGE_A_KEYS
    assert key in consent_catalog.RETIRED_KEYS
    assert not hasattr(consent_catalog, "cross_employer_reuse_allowed")


def test_the_generator_writes_no_prefill_and_reads_no_portable_record() -> None:
    """Asserted over the generator's syntax tree: no call it makes passes a
    pre-fill field, and it imports neither removed module nor the retake
    window that sized a portable fact's freshness."""
    import ast

    from app.services.assessment_questions import generate

    tree = ast.parse(inspect.getsource(generate))
    keywords = {
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    }
    assert not keywords & {"prefilled_answer", "prefill_source"}, keywords
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not {"resume_prefill", "portable_evidence", "retake"} & imported, imported
