"""The invitation has ONE writer, and applying is not it (PLAN-p3 WP1).

An `assessment_conversations` row IS the invitation: it is what lets a
candidate open the assessment, what the skills reopen guard counts as an
issued contract, and what the credit reconciliation settles. Applying used to
write one (and dispatch question generation) for every applicant to a ready
job, so each applicant held a half-made invitation the assessment page then
refused, and the reopen guard locked the skills before anybody was invited.
Phase 6 deleted that write; `services/assessment_invitations.invite_batch`
is the one writer now, reached from `select-candidates` and from the hand
move to `assessment_invited`.

THE BEHAVIOUR is pinned from a SECOND connection by
`tests/test_portal.py::test_applying_to_a_ready_job_creates_no_assessment`
and `tests/test_public_apply_unified.py` (both apply paths), and by the
harness scenario `regression.applying_never_creates_an_assessment`. THIS
module pins the SHAPE: no module under `app/` other than the one writer
constructs the row, inserts it through the ORM, or carries SQL that inserts
it. A second writer is how the apply-time row came to exist in the first
place, and it would pass every behavioural test written for the first one.

The SQL sweep normalises whitespace, so an INSERT wrapped across lines is
found (the 2026-09-23 lesson of `test_company_dna_removed.py`: a sweep with a
blind spot is worse than no sweep, because the green result is what stops
anybody looking).

Mutation-checked: an `INSERT INTO assessment_conversations` literal added to
`api/portal.py` fails `test_only_the_invitation_service_writes_the_row`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

#: The one writer.
WRITER = "services/assessment_invitations.py"

#: Writers that are not product paths, each with the reason it may stay.
ALLOWED_ELSEWHERE: dict[str, str] = {
    "scripts/seed_mock_data.py": (
        "the dev database repair seed: it fills in a conversation for a demo "
        "application it has already advanced past `applied`, and no route, "
        "worker or schedule reaches it"
    ),
}

_INSERT_SQL = re.compile(r"insert\s+into\s+assessment_conversations\b", re.IGNORECASE)
_MODEL = "AssessmentConversation"


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _writes(path: Path) -> list[str]:
    """Every way this file could create a conversation row, as readable hits."""
    source = path.read_text(encoding="utf-8")
    hits: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _INSERT_SQL.search(" ".join(node.value.split())):
                hits.append(f"line {node.lineno}: SQL INSERT INTO assessment_conversations")
        elif isinstance(node, ast.Call):
            called = _name(node.func)
            if called == _MODEL:
                hits.append(f"line {node.lineno}: {_MODEL}(...) constructed")
            elif called in {"insert", "pg_insert"} and node.args:
                if _name(node.args[0]) == _MODEL:
                    hits.append(f"line {node.lineno}: {called}({_MODEL})")
    return hits


def _all_writers() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        hits = _writes(path)
        if hits:
            found[path.relative_to(APP).as_posix()] = hits
    return found


def test_only_the_invitation_service_writes_the_row() -> None:
    writers = _all_writers()
    assert WRITER in writers, (
        "the invitation service no longer writes the row; the sweep has gone "
        "vacuous or the writer moved without this test"
    )
    unexpected = {
        path: hits
        for path, hits in writers.items()
        if path != WRITER and path not in ALLOWED_ELSEWHERE
    }
    assert not unexpected, (
        "assessment_conversations rows are the invitation and are written by "
        f"{WRITER} alone; found another writer: {unexpected}"
    )


def test_every_allowed_writer_still_writes() -> None:
    """An allowlist entry for a file that no longer writes the row is a hole
    kept open for nothing: the next writer added there would pass."""
    writers = _all_writers()
    stale = sorted(set(ALLOWED_ELSEWHERE) - set(writers))
    assert not stale, f"allowlisted writers that no longer write the row: {stale}"


def test_the_sweep_sees_a_wrapped_insert() -> None:
    """The whitespace normalisation is what the sweep's claim rests on."""
    wrapped = 'q = """INSERT\n    INTO\n  assessment_conversations (id) VALUES (1)"""\n'
    tree = ast.parse(wrapped)
    literal = next(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert _INSERT_SQL.search(" ".join(literal.split()))
