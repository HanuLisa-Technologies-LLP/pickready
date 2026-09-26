"""The six-month retake is gone, and this is what keeps it gone.

WHAT WAS REMOVED (PLAN-p5 WP5-G, the Vivekium release)
--------------------------------------------------------
`services/retake.py` classified a returning candidate against a six-month
window and decided whether any section of an earlier report could be carried
into a new one. Report reuse was retired on 2026-07-30
(`PORTABLE_CATEGORIES` became an explicit empty frozenset), so for two months
the module only ever produced a sentence: the invitation page's "you took an
assessment recently" explanation (`recent_prior_report`) and the apply-time
`assessment_required` / `assessment_notice` pair. Phase 3 removed the apply
block and the invitation read; this package deleted the module, its tests,
the schema fields and the copy.

Every application is assessed against its own job's locked contract. There is
no waiting period to explain and no reuse to decide, so there is nothing for a
candidate to be told about a retake.

WHY A SWEEP AND NOT ONLY AN IMPORT CHECK
------------------------------------------
A module deleted while a schema field, a docstring or a screen still names it
is a feature one edit away from returning, and the copy is the half a
candidate reads. The sweep normalises whitespace first, for the reason
`test_company_dna_removed.py` records: a mention wrapped across a newline
passed a line-at-a-time sweep for two weeks.
"""
from __future__ import annotations

import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND = REPO / "frontend"

#: What must not be named anywhere in live source. Each is a symbol or a
#: sentence only the retired feature ever had.
PATTERNS = (
    re.compile(r"services[./]retake\b"),
    re.compile(r"\bretake\.decide\b"),
    re.compile(r"\bPORTABLE_CATEGORIES\b"),
    re.compile(r"\brecent_prior_report\b"),
    re.compile(r"\bassessment_notice\b"),
    re.compile(r"retake after six months", re.I),
    re.compile(r"six[- ]month retake", re.I),
)

#: Files allowed to name the thing, each with its reason. Declared rather than
#: globbed so a new exemption is a reviewed test change.
ALLOWED = {
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_retake_removed.py": "the sweep itself",
    # The report eval's own absence check (`no_report_reuse`) asks the import
    # system for the module by name; that is a guard, not a mention.
    BACKEND / "app" / "scripts" / "eval_report.py": "the eval's absence check",
    # Asserts the invitation payload no longer carries the field.
    BACKEND / "tests" / "test_assessment_invitation_link.py": "the field-absence assertion",
}


def _flatten(text: str) -> tuple[str, list[int]]:
    """Whitespace runs collapsed to one space, with each kept character's
    original offset, so a hit wrapped across a newline still matches and still
    names the line it starts on."""
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


def _sources() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root in (BACKEND / "app", BACKEND / "tests"):
        found.extend(
            path for path in root.rglob("*.py") if "__pycache__" not in path.parts
        )
    for root in ("app", "components", "lib"):
        for suffix in ("*.ts", "*.tsx"):
            found.extend((FRONTEND / root).rglob(suffix))
    return found


def test_the_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("app.services.retake")
    assert not (BACKEND / "app" / "services" / "retake.py").exists()


def test_the_invitation_and_portal_payloads_carry_no_retake_field() -> None:
    from app.schemas import assessments as assessment_schemas
    from app.schemas import portal as portal_schemas

    for module in (assessment_schemas, portal_schemas):
        for name in dir(module):
            model = getattr(module, name)
            fields = getattr(model, "model_fields", None)
            if not isinstance(fields, dict):
                continue
            for retired in ("recent_prior_report", "assessment_required", "assessment_notice"):
                assert retired not in fields, f"{module.__name__}.{name}.{retired}"


def test_no_live_source_still_names_the_retake() -> None:
    offending: list[str] = []
    sources = _sources()
    # A sweep over nothing passes; that is the failure it exists to catch.
    assert len(sources) > 500, len(sources)
    for path in sources:
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        flat, offsets = _flatten(text)
        for pattern in PATTERNS:
            for match in pattern.finditer(flat):
                line = text.count("\n", 0, offsets[match.start()]) + 1
                offending.append(f"{path.relative_to(REPO)}:{line}: {match.group(0)}")
    assert not offending, offending
