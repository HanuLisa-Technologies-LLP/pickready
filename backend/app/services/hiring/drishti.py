"""Drishti, the seventh agent: a function's strategic profile (vivekium C3).

WHAT IT IS. Once per FUNCTION per functional head (MD, CEO, CTO, CFO, COO),
never per job and never merged with Bodha: Bodha is per job, per hiring
manager, every posting. Drishti captures the function's strategic purpose,
people philosophy, non-negotiables, culture and leadership expectations,
and the strategic gap being filled, and it feeds every assessment for that
function until the functional head updates it. A leadership change is the
CLIENT's trigger, never auto-detected.

AN ENHANCEMENT LAYER, NOT A DEPENDENCY, by the brief's own feature table
("If absent: the platform still works"), which the reconciliation chose
over the event map's contradicting sentence because the other reading makes
a job uncreatable until a CXO finishes an interview, and Gate 1 was
narrowed for exactly that reason. Absent, every matrix freezes byte-for-
byte as before.

THE TWO PROPERTIES THE 2026-09-09 REMOVAL DEMANDED, held here:

* Sutra and the scorecard read the COMPILED artifact, never the client's
  free text. An unbounded client-authored string in the prompt that decides
  what every candidate is graded on is an injection surface, and "we like
  hungry people" must not become a criterion. Compilation is DETERMINISTIC
  and calls no model, so it is reproducible and diffable between versions.
* Drishti may TUNE and may never SUSPEND: its emphasis reaches weights only
  through `layers.resolve` under `LAYER_COMPANY`, the bounds table the
  removal deliberately kept alive with no supplier. Every clamp and refusal
  is recorded in provenance, the standing rule.

THE STRUCTURED AI CONVERSATION is the critique loop: every sentence of
every section is held to the observable-evidence bar by the SAME detector
Bodha's SWOT quality rules and the model itself are held to
(`hiring/observable`), and each non-observable claim comes back as a probe
asking for what somebody could actually watch happen. Deterministic and
offline, because the guard matters most when the provider is down.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.hiring import observable

#: The compiled artifact's schema version, stored inside the artifact.
COMPILED_VERSION = 1

#: How strongly a competency named in the non-negotiables leans on the
#: weight. Applied through layers.resolve, whose BOUNDS clamp it regardless.
EMPHASIS_MULTIPLIER = 1.10

#: Bound on compiled context lines, so the artifact stays a summary rather
#: than a second copy of the prose.
MAX_CONTEXT_LINES = 12
MAX_LINE_CHARS = 240


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    prompt: str


#: The brief's five capture areas, verbatim in substance.
SECTIONS: tuple[Section, ...] = (
    Section(
        "strategic_purpose",
        "Strategic purpose of the function",
        "What is this function for, in this company, over the next two "
        "years? Describe outcomes somebody could observe, not virtues.",
    ),
    Section(
        "people_philosophy",
        "People philosophy",
        "How does this function grow, evaluate and retain its people? "
        "Describe practices that actually happen.",
    ),
    Section(
        "non_negotiables",
        "Non-negotiables",
        "Name the competencies or standards no hire in this function may "
        "lack. Each one should be something an assessment could observe.",
    ),
    Section(
        "culture_expectations",
        "Culture and leadership expectations",
        "What behaviour do leaders in this function actually reward? "
        "Describe what somebody would see, not what the values page says.",
    ),
    Section(
        "strategic_gap",
        "The strategic gap being filled",
        "What is missing from the function today that hiring is meant to "
        "fix? Name the observable shortfall.",
    ),
)

SECTION_KEYS: tuple[str, ...] = tuple(section.key for section in SECTIONS)


def sections_payload() -> list[dict[str, str]]:
    return [
        {"key": s.key, "title": s.title, "prompt": s.prompt} for s in SECTIONS
    ]


_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")


def critique(section_text: str) -> list[str]:
    """The conversation's probes: one per non-observable claim.

    Reuses the one observable-evidence detector, so Drishti, the SWOT
    quality rules and the model's own bar cannot drift apart. An empty
    section returns no probes; absence is a real state.
    """
    probes: list[str] = []
    for match in _SENTENCES.finditer(section_text or ""):
        sentence = match.group(0).strip()
        if len(sentence) < 8:
            continue
        if not observable.is_observable(sentence):
            probes.append(observable.rejection_message(sentence))
    return probes


def compile_profile(
    *, function_name: str, sections: Mapping[str, str]
) -> dict[str, Any]:
    """The compiled artifact: deterministic, model-free, bounded.

    `context_lines` are the OBSERVABLE sentences only, per section, capped;
    a sentence the detector rejects never reaches a prompt or a weight.
    `emphasis` names each known competency the non-negotiables mention on a
    word boundary (the "hold contains old" lesson), at the one declared
    multiplier; the layers engine clamps it again regardless.
    """
    lines: list[str] = []
    for section in SECTIONS:
        body = str(sections.get(section.key) or "")
        for match in _SENTENCES.finditer(body):
            sentence = match.group(0).strip()
            if len(sentence) >= 8 and observable.is_observable(sentence):
                lines.append(f"{section.title}: {sentence}"[:MAX_LINE_CHARS])
            if len(lines) >= MAX_CONTEXT_LINES:
                break
        if len(lines) >= MAX_CONTEXT_LINES:
            break

    # The non-negotiables text is stored RAW (bounded), unlike the context
    # lines, and the asymmetry is deliberate: context lines can reach a
    # prompt, so they pass the observable bar; this string never does. It is
    # only ever WORD-LOOKED-UP against names the matrix already resolved
    # (`emphasis_map`), so an adjective in it cannot become a criterion; the
    # worst it can do is lean, within layers bounds, on a competency the
    # hiring manager independently declared. Emphasis resolves AT FREEZE, so
    # a profile written before any job exists still reaches every later
    # matrix in its function.
    return {
        "version": COMPILED_VERSION,
        "function": function_name,
        "context_lines": lines,
        "non_negotiables_text": str(sections.get("non_negotiables") or "")[:4000],
    }


async def compiled_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, department: str | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """The compiled artifact and its context lines for a job's function.

    Matched on the DEPARTMENT name, case-insensitively: the job's own
    department field is the function key the client already maintains.
    (None, []) is the enhancement-layer contract: no profile, no change.
    """
    if not department or not str(department).strip():
        return None, []
    row = (
        await session.execute(
            sa_text(
                "SELECT compiled_json FROM drishti_profiles "
                "WHERE tenant_id = :tid AND lower(function_name) = lower(:fn) "
                "LIMIT 1"
            ),
            {"tid": str(tenant_id), "fn": str(department).strip()},
        )
    ).scalar()
    if not isinstance(row, dict):
        return None, []
    lines = row.get("context_lines")
    return row, [str(line) for line in lines] if isinstance(lines, list) else []


def emphasis_map(
    compiled: Mapping[str, Any] | None, names: list[str]
) -> dict[str, float]:
    """Which of THESE competency names the non-negotiables lean on.

    Resolved at freeze against the matrix's own resolved names, word-
    boundary matched (the "hold contains old" lesson), at the one declared
    multiplier; layers.resolve clamps it again regardless.
    """
    if not compiled:
        return {}
    haystack = str(compiled.get("non_negotiables_text") or "").casefold()
    if not haystack:
        return {}
    out: dict[str, float] = {}
    for name in names:
        clean = str(name).strip()
        if not clean:
            continue
        pattern = rf"(?<!\w){re.escape(clean.casefold())}(?!\w)"
        if re.search(pattern, haystack):
            out[clean] = EMPHASIS_MULTIPLIER
    return out
