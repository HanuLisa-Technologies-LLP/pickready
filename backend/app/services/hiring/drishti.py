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

THE STRUCTURED AI CONVERSATION lives in `hiring/drishti_conversation` and
is a CAPTURE MECHANISM for this module and nothing more: it produces the
same five strings the form produces, and hands them to the same save route,
which calls the same `compile_profile`. There is no second compiler and no
second artifact, which is what keeps the paragraph above structural rather
than a matter of care. Both doors share this module's critique loop: every
sentence of every section is held to the observable-evidence bar by the
SAME detector Bodha's SWOT quality rules and the model itself are held to
(`hiring/observable`), and each non-observable claim comes back as a probe
asking for what somebody could actually watch happen. Deterministic and
offline, because the guard matters most when the provider is down.

WHAT REACHES A MODEL PROMPT is `prompt_context` and only `prompt_context`:
derived from the compiled artifact, re-checked against the detector, and
capped in lines and characters. Sutra's naming call is its one reader.
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

#: The SECOND, tighter bound: what may reach a model prompt. Deliberately
#: smaller than the artifact's own bound, because the two are protecting
#: different things. `MAX_CONTEXT_LINES` keeps the stored artifact a summary;
#: these keep the share of Sutra's naming prompt that is client-authored small
#: enough that it cannot dominate the instruction above it. A budget in
#: CHARACTERS as well as in lines, because twelve lines of 240 characters is a
#: different prompt from twelve lines of thirty.
PROMPT_CONTEXT_LINES = 8
PROMPT_CONTEXT_CHARS = 1200


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

#: Below this, a fragment is not a claim anybody could probe. "Yes." and a
#: stray initial are not statements the detector should have an opinion about.
MIN_SENTENCE_CHARS = 8


def sentences(section_text: str) -> list[str]:
    """The claims in one section, split the ONE way.

    `critique`, `compile_profile` and the capture conversation all have to
    agree about where one claim ends and the next begins, or a section reads
    as three probes in one place and one long sentence in another. Splitting
    it here once is the same argument `observable` makes for having one
    detector rather than two copies.
    """
    return [
        fragment
        for match in _SENTENCES.finditer(section_text or "")
        if len(fragment := match.group(0).strip()) >= MIN_SENTENCE_CHARS
    ]


def critique(section_text: str) -> list[str]:
    """The conversation's probes: one per non-observable claim.

    Reuses the one observable-evidence detector, so Drishti, the SWOT
    quality rules and the model's own bar cannot drift apart. An empty
    section returns no probes; absence is a real state.
    """
    return [
        observable.rejection_message(sentence)
        for sentence in sentences(section_text)
        if not observable.is_observable(sentence)
    ]


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
        for sentence in sentences(str(sections.get(section.key) or "")):
            if observable.is_observable(sentence):
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


def prompt_context(compiled: Mapping[str, Any] | None) -> list[str]:
    """The ONLY Drishti text a model prompt is ever given.

    THE C3 GUARANTEE, AT THE ONE PLACE IT COULD BE LOST. Sutra's naming call
    decides what every candidate in this function is graded against, so what
    reaches it has to be bounded three ways at once, and this function is
    where all three are applied together:

    * DERIVED, never raw. Its input is `compile_profile`'s own
      `context_lines`, which are the observable sentences and nothing else.
      `non_negotiables_text` is deliberately NOT reachable from here: it is
      stored raw, and the whole reason that is safe is that it is only ever
      word-looked-up against names the matrix already resolved
      (`emphasis_map`). Putting it in a prompt would remove the property
      that makes storing it raw defensible at all.
    * RE-CHECKED against today's bar, not the bar the day it was compiled.
      A stored artifact outlives the compiler that wrote it, and a detector
      that only ever ran at write time is a detector an old row walks past.
      The section title prefix is stripped before the check so the words
      being judged are the client's, not ours.
    * CAPPED, in lines and in characters, so a long profile cannot crowd out
      the instruction above it.

    An absent profile, and a profile whose every sentence fails the bar,
    both return `[]`, which is what keeps the enhancement-layer contract
    true for the PROMPT as well as for the weights: no lines, no key in the
    payload, byte-identical request.
    """
    if not compiled:
        return []
    raw = compiled.get("context_lines")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    spent = 0
    for entry in raw:
        line = str(entry).strip()[:MAX_LINE_CHARS]
        if not line:
            continue
        # `compile_profile` writes "<Section title>: <sentence>". Judge the
        # sentence the client wrote, not the label this module added to it.
        _, _, sentence = line.partition(": ")
        if not observable.is_observable(sentence or line):
            continue
        if spent + len(line) > PROMPT_CONTEXT_CHARS:
            break
        out.append(line)
        spent += len(line)
        if len(out) >= PROMPT_CONTEXT_LINES:
            break
    return out


# ── The functional-head binding (migration 0115) ────────────────────────────

#: Claim the profile for the caller: a new function, a profile nobody holds,
#: or a change of head the client has explicitly confirmed. Stamps the
#: binding and is audited under its own action.
BIND = "bind"
#: The caller is already the head of record. Write, change nothing else.
KEEP = "keep"
#: Somebody else's function, and no confirmation. 409, naming them.
REFUSE = "refuse"


def resolve_head_binding(
    *,
    current_head_id: uuid.UUID | None,
    caller_id: uuid.UUID,
    exists: bool,
    change_confirmed: bool,
) -> str:
    """Who owns this function's profile after this write, as a pure function.

    PULLED OUT OF THE HANDLER ON PURPOSE. The brief's rule is one sentence
    ("a functional-head change is the CLIENT's trigger, never auto-detected")
    and getting it wrong has exactly one visible symptom, which is nothing at
    all: the profile saves, the head silently becomes whoever opened the
    form, and the function's strategic direction is now attributed to
    somebody who never set it. A rule whose failure is invisible has to be
    testable without a database, or the only thing that ever exercises it is
    production.

    The three answers and why each is the answer:

    * A profile that does not exist yet is CLAIMED. There is nobody to take
      it from.
    * A profile whose head is NULL is claimed too, with no confirmation
      asked. That is a row from before this binding existed whose author has
      since been deleted, and a refusal naming nobody is a dead end.
    * A profile held by somebody else is REFUSED unless the caller confirmed
      the change. Confirmation is the client's trigger; its absence is not a
      permission problem, which is why the refusal is a 409 and not a 403.
    """
    if not exists or current_head_id is None:
        return BIND
    if current_head_id == caller_id:
        return KEEP
    return BIND if change_confirmed else REFUSE
