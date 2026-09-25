"""The read-only coding evaluation is being removed, and this is what keeps it removed.

WHAT IS GOING, AND WHAT REPLACES IT
-----------------------------------
Until Phase 4 a coding answer was READ by a model and judged "not executed":
the evaluator stored `NOT_EXECUTED_NOTE` on every coding evaluation, refused a
verdict without one of `HEDGE_MARKERS`, and ran the prompt
`assessment_answer_evaluation_coding`; the question itself was written by
`assessment_format_coding`, whose prompt told the model the code would never
run; and the editor was CodeMirror. Phase 4 replaces all of it: the code runs
against hidden tests in a sandbox (`services/code_execution`), a separate
review judges how it is written (`coding_assessment/review.py`,
`coding_quality_review`), questions are written with a validated reference
solution (`assessment_formats/coding_generation.py`,
`coding_question_generation`), and the editor is Monaco.

WHY A TEST FOR AN ABSENCE
-------------------------
The removal lands in several phases (the evaluator's coding branch with the
Miti coding sub-stage, the old writer with the Phase 3 composer, the editor
with the Phase 4 frontend), and a removal done in pieces is the easiest kind to
leave half done: a constant nothing reads, a prompt nothing loads, a
dependency nothing imports. Each is one import away from coming back with no
decision behind it.

THE PENDING LEDGER SHRINKS AND CANNOT GROW
------------------------------------------
Every site still carrying a forbidden name is listed in `PENDING_REMOVAL`
with the package that owns its deletion. The ledger is enforced in BOTH
directions: a hit that is not listed fails (the removal regressed, or a new
use appeared), and a listed site that no longer carries its name fails too
(the deletion landed; delete the entry in the same change). So the ledger can
only get shorter, and the day it is empty this is an ordinary removal sweep.
The shape is the one `test_dispatch_after_commit_sweep.py` uses for its legacy
call sites.

`PERMANENT` is different: a site that must keep naming the removed thing for
a stated reason, and it is not expected to shrink.

WHITESPACE IS NORMALISED FIRST
------------------------------
For the reason CLAUDE.md records on 2026-09-23 ("the removal sweep had a
blind spot"): a
sweep that reads one line at a time never matches a phrase wrapped across a
newline, and a sweep with a blind spot is worse than none, because the green
result is what stops anybody looking. Offsets are mapped back, so a hit still
names a line somebody can open.
"""
from __future__ import annotations

import pathlib
import re
from typing import Iterator

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

#: The roots the plan names (PLAN-p4 section 4). `frontend/package.json` is a
#: single file: it is where a dependency comes back.
ROOTS: tuple[pathlib.Path, ...] = (
    BACKEND / "app",
    BACKEND / "tests",
    BACKEND / "harness",
    REPO / "frontend" / "app",
    REPO / "frontend" / "components",
    REPO / "frontend" / "lib",
    REPO / "frontend" / "package.json",
)

#: Text files only. A lockfile is deliberately out of scope: it is generated
#: from `package.json`, which is swept, and it lists transitive packages no
#: source in this repository chose.
SUFFIXES = frozenset(
    {".py", ".txt", ".json", ".yaml", ".yml", ".md", ".ts", ".tsx", ".js", ".mjs", ".css"}
)

THIS_FILE = pathlib.Path(__file__).resolve()

#: The names that must not come back. Case sensitive, because the
#: identifiers are, and because `not_executed_note` (lower case) is a stored
#: FIELD every legacy coding evaluation still carries and the transcript still
#: renders; it is not the constant that wrote it.
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NOT_EXECUTED_NOTE", re.compile(r"\bNOT_EXECUTED_NOTE\b")),
    ("HEDGE_MARKERS", re.compile(r"\bHEDGE_MARKERS\b")),
    ("read and judged, not executed", re.compile(r"was read and judged, not executed")),
    ("assessment_answer_evaluation_coding", re.compile(r"assessment_answer_evaluation_coding")),
    ("assessment_format_coding", re.compile(r"assessment_format_coding")),
    ("codemirror", re.compile(r"codemirror")),
    ("@lezer/", re.compile(r"@lezer/")),
)

#: Forbidden in PROMPTS only. Elsewhere "never executed" is ordinary prose
#: (an uploaded document is validated and never executed; so is a candidate
#: project), and those sentences are true. In a prompt it told a model that a
#: coding answer would not run, which is no longer the product.
PROMPTS = BACKEND / "app" / "prompts"
PROMPT_ONLY: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("never executed", re.compile(r"never executed", re.I)),
)

#: Sites that keep naming a removed thing, for good. (path, name) -> reason.
PERMANENT: dict[tuple[str, str], str] = {
    ("backend/tests/test_assessment_formats_live.py", "read and judged, not executed"): (
        "A LEGACY STORED ROW. The transcript view must keep rendering a coding "
        "evaluation written before Phase 4, whose `not_executed_note` holds "
        "exactly this sentence, and the fixture reproduces that row verbatim."
    ),
}

#: Sites still carrying a forbidden name because their deletion belongs to a
#: package that has not landed on this branch. (path, name) -> owner. Each
#: entry is removed in the change that deletes the site; see the module
#: docstring for why a stale entry fails.
PENDING_REMOVAL: dict[tuple[str, str], str] = {
    # The evaluator's coding branch: Phase 4 deletes it together with the Miti
    # coding sub-stage (Phase 5), because until that sub-stage reads
    # `coding_assessment.evidence` the branch is the only thing grading a v2
    # answer's code on this branch.
    ("backend/app/services/assessment_formats/evaluation.py", "NOT_EXECUTED_NOTE"): "Phase 4 hunk, with Phase 5",
    ("backend/app/services/assessment_formats/evaluation.py", "HEDGE_MARKERS"): "Phase 4 hunk, with Phase 5",
    ("backend/app/services/assessment_formats/evaluation.py", "read and judged, not executed"): "Phase 4 hunk, with Phase 5",
    ("backend/app/services/assessment_formats/evaluation.py", "assessment_answer_evaluation_coding"): "Phase 4 hunk, with Phase 5",
    ("backend/app/prompts/assessment_answer_evaluation_coding.txt", "assessment_answer_evaluation_coding"): "Phase 4 hunk, with Phase 5",
    ("backend/app/prompts/assessment_answer_evaluation_coding.txt", "never executed"): "Phase 4 hunk, with Phase 5",
    ("backend/tests/test_assessment_formats_evaluation.py", "NOT_EXECUTED_NOTE"): "Phase 4 hunk, with Phase 5",
    # The old question writer (4B1 hunk 2, via wip/p3-w2) and the CodeMirror
    # editor (WP-4D) both left at the stage 2 integration; their entries went
    # with them.
}


def _files() -> Iterator[pathlib.Path]:
    for root in ROOTS:
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and path.suffix in SUFFIXES
                and path.resolve() != THIS_FILE
                and not {"node_modules", "__pycache__", ".next"} & set(path.parts)
            ):
                yield path


def _flatten(text: str) -> tuple[str, list[int]]:
    """Collapse every run of whitespace to one space, keeping each kept
    character's offset in the original so a hit maps back to its line."""
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


def _hits() -> dict[tuple[str, str], list[int]]:
    """(repo-relative path, forbidden name) -> the lines it occurs on.

    A FILE NAME counts as an occurrence on line zero: a prompt nothing loads
    still ships in the image, and deleting its last reference while leaving
    the file is the half-done removal this sweep exists to catch.
    """
    found: dict[tuple[str, str], list[int]] = {}
    for path in _files():
        relative = path.relative_to(REPO).as_posix()
        patterns = FORBIDDEN + (PROMPT_ONLY if PROMPTS in path.parents else ())
        for name, pattern in patterns:
            if pattern.search(path.name):
                found.setdefault((relative, name), []).append(0)
        text = path.read_text(encoding="utf-8", errors="replace")
        flat, offsets = _flatten(text)
        for name, pattern in patterns:
            for match in pattern.finditer(flat):
                line = text.count("\n", 0, offsets[match.start()]) + 1
                found.setdefault((relative, name), []).append(line)
    return found


HITS = _hits()


def test_the_sweep_reads_the_roots_it_names() -> None:
    """A root that moved would make every assertion below vacuous."""
    backend_files = [path for path in _files() if BACKEND in path.parents]
    assert len(backend_files) > 500, len(backend_files)
    assert (REPO / "frontend" / "package.json").is_file()


def test_nothing_names_the_read_only_coding_evaluation_outside_the_ledger() -> None:
    unexpected = sorted(
        f"{path}:{','.join(str(line) for line in lines)}: {name}"
        for (path, name), lines in HITS.items()
        if (path, name) not in PERMANENT and (path, name) not in PENDING_REMOVAL
    )
    assert not unexpected, (
        "the read-only coding evaluation is named where it must not be:\n"
        + "\n".join(unexpected)
        + "\nIts replacement is code_execution plus coding_assessment (review, "
        "evidence) and coding_generation; see this module's docstring."
    )


def test_every_pending_removal_is_still_pending() -> None:
    """A deletion landed, so its entry goes in the same change.

    Left behind, a stale entry is a standing permission for the name to come
    back at that site with nothing failing.
    """
    stale = sorted(
        f"{path}: {name} ({owner})"
        for (path, name), owner in PENDING_REMOVAL.items()
        if (path, name) not in HITS
    )
    assert not stale, (
        "these sites no longer carry the name, so their ledger entries must be "
        "deleted:\n" + "\n".join(stale)
    )


def test_every_permanent_exception_is_still_needed() -> None:
    stale = sorted(
        f"{path}: {name}" for (path, name) in PERMANENT if (path, name) not in HITS
    )
    assert not stale, stale


def test_the_ledger_names_only_what_the_sweep_forbids() -> None:
    known = {name for name, _ in FORBIDDEN + PROMPT_ONLY}
    listed = {name for _, name in (*PERMANENT, *PENDING_REMOVAL)}
    assert listed <= known, listed - known


def test_a_wrapped_mention_is_still_found() -> None:
    """The whitespace blind spot, pinned with the phrase wrapped inside a
    docstring, which is how prose is wrapped in this codebase."""
    text = "x = 1\n'''This code was read and judged,\n    not executed.'''\n"
    flat, offsets = _flatten(text)
    pattern = dict(FORBIDDEN)["read and judged, not executed"]
    match = pattern.search(flat)
    assert match is not None, flat
    assert text.count("\n", 0, offsets[match.start()]) + 1 == 2
