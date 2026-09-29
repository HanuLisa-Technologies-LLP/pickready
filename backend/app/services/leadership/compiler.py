"""Compile a leader's words into the bounded artifact the pipeline may read.

Owner spec 2026-09-29, section 19. "Raw leadership text must not be inserted
directly into high-impact prompts without control. Compile it into a bounded
structured artifact."

DETERMINISTIC AND MODEL-FREE, the two properties Drishti's compiler held and
the 2026-09-09 removal demanded: the artifact that shapes every job in a
department must be reproducible, diffable between versions and explainable
during a provider outage. The same words always compile to the same artifact.

WHAT IS KEPT, AND WHAT IS REFUSED (spec 19.1)
---------------------------------------------
Every sentence is judged on its own, by the product's ONE implementation of
each rule (`services/hiring/observable`, `ppi.is_forbidden_competency`), so a
leader, a SWOT and Sutra's own output are held to one bar that cannot drift:

* `protected_attribute`: it rests on age, gender, religion, caste, family
  status, disability or another protected or sensitive attribute
  (`observable.prohibited_in`, word boundaries, numeric age bars included).
  Refused FIRST: a protected criterion stays refused however observable it is.
* `culture_fit`: it names culture fit, which a single assessment cannot
  judge (the same refusal the Skills step and the database CHECK make).
* `not_observable`: it describes a person rather than something somebody
  could watch happen ("We need aggressive people"), or is too short to be an
  account of anything (`observable.is_observable`).
* `too_long`: longer than one line may be. Refused WHOLE rather than cut,
  because half a sentence is a sentence nobody wrote.
* `over_limit`: past the per-field or per-profile cap, so a long input cannot
  crowd out everything else.

A refused sentence is never deleted from the leader's input and never reaches a
prompt: it is listed in `excluded_or_unsafe_claims` with its reason word and
shown back to the leader after Save, so they can rewrite it as something an
assessment could observe. The compiler does not rewrite it for them: turning
"aggressive" into a criterion is a decision about what candidates are graded
on, and that decision is a person's (spec 19.1, the last paragraph).

WHERE A KEPT SENTENCE GOES
--------------------------
Into exactly ONE list. A sentence that states a must, a gap, an outcome or a
working behaviour goes to that list by its wording; any other goes to the
list its FIELD names (company-wide requirements to `company_priorities`,
department requirements and expectations to `department_priorities`, the
ideal employee to `observable_competencies`). The lists are
implementation intelligence (spec 16.3), never a form the leader fills.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Mapping

from app.services import ppi
from app.services.hiring import observable

__all__ = [
    "COMPANY_PRIORITIES",
    "CULTURE_EXPECTATIONS",
    "DEPARTMENT_PRIORITIES",
    "FIELD_COMPANY",
    "FIELD_DEPARTMENT",
    "FIELD_DEPARTMENT_EXPECTATION",
    "FIELD_IDEAL",
    "LISTS",
    "MAX_FIELD_LINES",
    "MAX_LINE_CHARS",
    "MAX_PROFILE_LINES",
    "NON_NEGOTIABLES",
    "OBSERVABLE_COMPETENCIES",
    "REASON_CULTURE_FIT",
    "REASON_NOT_OBSERVABLE",
    "REASON_OVER_LIMIT",
    "REASON_PROTECTED",
    "REASON_TOO_LONG",
    "SCHEMA_VERSION",
    "STRATEGIC_GAPS",
    "SUCCESS_OUTCOMES",
    "compile_expectation",
    "compile_profile",
    "kept_lines",
    "judge_sentence",
    "sentences",
]

#: The artifact's own shape version, stored inside it.
SCHEMA_VERSION = 1

#: Bounds. A leader's input is a summary of intent, not a second JD.
MAX_LINE_CHARS = 240
MAX_FIELD_LINES = 8
MAX_PROFILE_LINES = 24

COMPANY_PRIORITIES = "company_priorities"
DEPARTMENT_PRIORITIES = "department_priorities"
OBSERVABLE_COMPETENCIES = "observable_competencies"
NON_NEGOTIABLES = "non_negotiables"
SUCCESS_OUTCOMES = "success_outcomes"
CULTURE_EXPECTATIONS = "culture_expectations"
STRATEGIC_GAPS = "strategic_gaps"
#: Spec 19's lists, in the order the artifact carries them.
LISTS: tuple[str, ...] = (
    COMPANY_PRIORITIES,
    DEPARTMENT_PRIORITIES,
    OBSERVABLE_COMPETENCIES,
    NON_NEGOTIABLES,
    SUCCESS_OUTCOMES,
    CULTURE_EXPECTATIONS,
    STRATEGIC_GAPS,
)

#: The input fields, and the list a plainly worded sentence of each lands in.
FIELD_COMPANY = "company_requirements"
FIELD_DEPARTMENT = "department_requirements"
FIELD_IDEAL = "ideal_employee_expectations"
FIELD_DEPARTMENT_EXPECTATION = "department_expectation"
_FIELD_LIST: dict[str, str] = {
    FIELD_COMPANY: COMPANY_PRIORITIES,
    FIELD_DEPARTMENT: DEPARTMENT_PRIORITIES,
    FIELD_IDEAL: OBSERVABLE_COMPETENCIES,
    FIELD_DEPARTMENT_EXPECTATION: DEPARTMENT_PRIORITIES,
}

REASON_PROTECTED = "protected_attribute"
REASON_CULTURE_FIT = "culture_fit"
REASON_NOT_OBSERVABLE = "not_observable"
REASON_TOO_LONG = "too_long"
REASON_OVER_LIMIT = "over_limit"

#: Wording cues, checked in this order; the first that matches decides.
_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        NON_NEGOTIABLES,
        re.compile(r"\b(must|never|non[- ]negotiable|mandatory|always|required to)\b"),
    ),
    (
        STRATEGIC_GAPS,
        re.compile(r"\b(gap|gaps|missing|lack|lacks|shortfall|nobody|no one|do not have|does not have)\b"),
    ),
    (
        SUCCESS_OUTCOMES,
        re.compile(r"\b(achieve|achieved|deliver|delivers|delivered|within|by the end|outcome|outcomes|result|results|target)\b"),
    ),
    (
        CULTURE_EXPECTATIONS,
        re.compile(r"\b(collaborat\w*|feedback|mentor\w*|coach\w*|review\w*|share\w*|teach\w*)\b"),
    ),
)

_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")
#: Below this a fragment is not a claim anybody could judge ("Yes.").
_MIN_SENTENCE_CHARS = 8


def sentences(text: str | None) -> list[str]:
    """The claims in one field, split the ONE way the compiler and the screen
    agree on (a bullet marker is not part of a claim)."""
    out: list[str] = []
    for match in _SENTENCES.finditer(text or ""):
        fragment = " ".join(match.group(0).split()).lstrip("-*• ").strip()
        if len(fragment) >= _MIN_SENTENCE_CHARS:
            out.append(fragment)
    return out


def judge_sentence(sentence: str) -> str | None:
    """None when the sentence may be kept, else the reason WORD it may not."""
    if observable.prohibited_in(sentence):
        return REASON_PROTECTED
    if ppi.is_forbidden_competency(sentence):
        return REASON_CULTURE_FIT
    if len(sentence) > MAX_LINE_CHARS:
        return REASON_TOO_LONG
    if not observable.is_observable(sentence):
        return REASON_NOT_OBSERVABLE
    return None


def _list_for(sentence: str, field: str) -> str:
    lowered = sentence.casefold()
    for list_name, pattern in _CUES:
        if pattern.search(lowered):
            return list_name
    return _FIELD_LIST[field]


def _empty(role: str, department_id: uuid.UUID | None, profile_id: uuid.UUID, version: int) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "source": {
            "role": role,
            "profile_id": str(profile_id),
            "department_id": str(department_id) if department_id else None,
        },
    }
    for list_name in LISTS:
        artifact[list_name] = []
    artifact["excluded_or_unsafe_claims"] = []
    artifact["provenance"] = []
    return artifact


def _compile_into(
    artifact: dict[str, Any],
    fields: Mapping[str, str | None],
    *,
    role: str,
    profile_id: uuid.UUID,
    version: int,
    department_id: uuid.UUID | None,
    budget: int,
) -> int:
    kept_total = 0
    for field, text in fields.items():
        kept_here = 0
        for sentence in sentences(text):
            reason = judge_sentence(sentence)
            if reason is None and (kept_here >= MAX_FIELD_LINES or kept_total >= budget):
                reason = REASON_OVER_LIMIT
            if reason is not None:
                artifact["excluded_or_unsafe_claims"].append(
                    {"text": sentence[:MAX_LINE_CHARS], "reason": reason, "field": field}
                )
                continue
            list_name = _list_for(sentence, field)
            artifact[list_name].append(sentence)
            artifact["provenance"].append(
                {
                    "text": sentence,
                    "list": list_name,
                    "field": field,
                    "role": role,
                    "profile_id": str(profile_id),
                    "version": version,
                    "department_id": str(department_id) if department_id else None,
                }
            )
            kept_here += 1
            kept_total += 1
    return kept_total


def compile_profile(
    *,
    role: str,
    profile_id: uuid.UUID,
    version: int,
    department_id: uuid.UUID | None,
    company_requirements: str | None = None,
    department_requirements: str | None = None,
    ideal_employee_expectations: str | None = None,
) -> dict[str, Any]:
    """The artifact for one leader's own fields. Pure; the same input, the
    same artifact. A CEO's or MD's per-department expectations compile
    separately (`compile_expectation`), one artifact per department row."""
    artifact = _empty(role, department_id, profile_id, version)
    _compile_into(
        artifact,
        {
            FIELD_COMPANY: company_requirements,
            FIELD_DEPARTMENT: department_requirements,
            FIELD_IDEAL: ideal_employee_expectations,
        },
        role=role,
        profile_id=profile_id,
        version=version,
        department_id=department_id,
        budget=MAX_PROFILE_LINES,
    )
    return artifact


def compile_expectation(
    *,
    role: str,
    profile_id: uuid.UUID,
    version: int,
    department_id: uuid.UUID,
    requirements: str,
) -> dict[str, Any]:
    """The artifact for a CEO's or MD's expectation of ONE department."""
    artifact = _empty(role, department_id, profile_id, version)
    _compile_into(
        artifact,
        {FIELD_DEPARTMENT_EXPECTATION: requirements},
        role=role,
        profile_id=profile_id,
        version=version,
        department_id=department_id,
        budget=MAX_FIELD_LINES,
    )
    return artifact


def kept_lines(compiled: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """(list name, sentence) for every kept line, in provenance order.

    RE-JUDGED against today's bar, not the bar the day it was compiled: a
    stored artifact outlives the compiler that wrote it, and a rule that only
    ran at write time is a rule an old row walks past. A Drishti row carried
    across by migration 0135 is read the same way.
    """
    if not isinstance(compiled, Mapping):
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    provenance = compiled.get("provenance")
    entries: list[tuple[str, str]] = []
    if isinstance(provenance, list) and provenance:
        for entry in provenance:
            if isinstance(entry, Mapping):
                entries.append((str(entry.get("list") or ""), str(entry.get("text") or "")))
    else:
        for list_name in LISTS:
            for text in compiled.get(list_name) or []:
                entries.append((list_name, str(text)))
    for list_name, raw in entries:
        text = " ".join(raw.split())
        # A Drishti line carried "<Section title>: <sentence>"; judge the words
        # the leader wrote, not the label a compiler added.
        judged = text.partition(": ")[2] if ": " in text[:60] else text
        if list_name not in LISTS or not text or judge_sentence(judged or text) is not None:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append((list_name, text))
    return out
