"""Explicit disclaimers: the one shape of negative evidence detected in code.

RPN-AI-UP-001 W6.6, and the boundary it lives inside first. The evidence
ledger has carried `STANCE_CONTRADICTS` and `CLAIM_CONTRADICTED` since it was
built, the contradiction module grades a contradicted claim MATERIAL, and the
scoring pass already routes MATERIAL into `needs_human_review`. What the whole
chain lacked was a WRITER: the single ledger write site recorded every
substantive answer as `supports`, so a candidate saying plainly "I have not
used Kafka" was filed as evidence FOR the claim that they demonstrated Kafka.
The read side of negative evidence existed end to end; none had ever been
written.

NEGATIVE EVIDENCE IS NOT ABSENT EVIDENCE, AND THE TWO NEVER BLUR
------------------------------------------------------------------
The standing rule (Miti, spec-doc5): insufficient evidence is EXCLUDED and
paid for in confidence, never scored low. This module does not touch that. A
non-answer, an evasion, or silence on a skill stays exactly what it was. What
is detected here is the opposite thing: a SUBSTANTIVE answer in which the
candidate explicitly denies the experience the claim asserts. "I have not
used Kafka" is a complete, honest answer (`answer_classification` has said so
since 2026-08-05) and it is also counter-evidence, and recording it as
support was simply wrong.

NO FLAG AUTO-REJECTS, STRUCTURALLY
------------------------------------
A `contradicts` stance reaches exactly one consumer: the contradiction report
that sets `needs_human_review` on the report row. The scorers read the
transcript, not the ledger; the aggregator is deterministic arithmetic that
imports no ledger; and `TriangulationResult` still has no reject field. A
false positive here therefore costs a human one look at a report, never a
candidate a grade, which is why the detector may exist at all.

DETERMINISTIC AND CONSERVATIVE, WITH THE MISS DIRECTION CHOSEN
----------------------------------------------------------------
No model call, for the standing reason: the moment a guard matters is the
moment the provider is failing. The patterns require a first-person negation
phrase with the competency's own term in the same sentence, close by. A mixed
answer ("I have not used Kafka Streams, but I ran plain Kafka consumers")
does match, and that is the accepted direction: it routes to a person, who is
exactly who should read a mixed answer. The direction NOT accepted is
inventing contradictions from hedges: "not much", "not only", "not just" and
a bare "no" match nothing.
"""
from __future__ import annotations

import re

__all__ = ["disclaimed_terms", "disclaims"]

#: First-person negation phrases, each anchored so the candidate is speaking
#: about themselves. Deliberately a short list of unambiguous shapes rather
#: than a long list of clever ones: every entry widens the false-positive
#: surface, and the cost of a MISS is only that an answer is filed as support,
#: which is where every answer was filed before this module existed.
_NEGATION_PHRASES = (
    r"i\s+have\s+not\s+(?:used|worked\s+with|done|led|managed|built)",
    r"i\s+have\s+never\s+(?:used|worked\s+with|done|led|managed|built)",
    r"i\s+haven't\s+(?:used|worked\s+with|done|led|managed|built)",
    r"i've\s+never\s+(?:used|worked\s+with|done|led|managed|built)",
    r"i\s+never\s+(?:used|worked\s+with|led|managed|built)",
    r"i\s+have\s+no\s+experience\s+(?:with|in|of)",
    r"i\s+do\s+not\s+have\s+(?:any\s+)?experience\s+(?:with|in|of)",
    r"i\s+don't\s+have\s+(?:any\s+)?experience\s+(?:with|in|of)",
    r"i\s+am\s+not\s+familiar\s+with",
    r"i'm\s+not\s+familiar\s+with",
    r"i\s+do\s+not\s+know",
    r"i\s+don't\s+know",
)

_NEGATION = re.compile("|".join(f"(?:{p})" for p in _NEGATION_PHRASES))

#: How far after the negation phrase the term may sit, in characters, and the
#: window also STOPS at the first clause boundary. Both halves matter: "I have
#: not used Kafka" binds, while "I have not used the staging cluster much,
#: though Kafka is where I spend most days" must not, because the denial there
#: is about the cluster and the comma is where its reach ends. The test file
#: carries that exact sentence, and the boundary rule exists because the
#: character window alone let it through.
_WINDOW = 40

#: Where a negation's reach ends inside a sentence. A denial does not cross a
#: contrast conjunction and keep its force.
_CLAUSE_BOUNDARY = re.compile(r",|;|\s+(?:but|though|although|however|while)\s+")

#: Competency-name words too generic to anchor a disclaimer on. "I do not
#: know management" is not a sentence anybody writes about the Management
#: competency, and matching words like these would let one hedged clause
#: contradict half a framework.
_GENERIC = frozenset(
    {
        "skills", "skill", "experience", "knowledge", "ability", "abilities",
        "management", "communication", "leadership", "team", "teams", "work",
        "working", "technical", "development", "general", "core", "advanced",
        "tools", "tooling", "systems", "system", "processes", "process",
    }
)

_WORD = re.compile(r"[a-z0-9+#.]+")


def _terms(name: str) -> list[str]:
    """The distinctive words of a competency name, generic filler removed."""
    return [
        token
        for token in _WORD.findall(str(name or "").casefold())
        if len(token) >= 4 and token not in _GENERIC
    ]


def _sentences(text: str) -> list[str]:
    return re.split(r"[.!?\n]+", str(text or "").casefold())


def disclaimed_terms(answer: str, competency_name: str) -> list[str]:
    """The competency terms this answer explicitly disclaims. Usually empty.

    A term counts only when a first-person negation phrase and the term sit in
    the SAME sentence, with the term inside a short window after the phrase,
    so a negation in one sentence cannot reach a term two sentences later.
    """
    terms = _terms(competency_name)
    if not terms:
        return []
    found: list[str] = []
    for sentence in _sentences(answer):
        for match in _NEGATION.finditer(sentence):
            window = sentence[match.end() : match.end() + _WINDOW]
            boundary = _CLAUSE_BOUNDARY.search(window)
            if boundary:
                window = window[: boundary.start()]
            for term in terms:
                if term in found:
                    continue
                if re.search(rf"(?<![a-z0-9]){re.escape(term)}", window):
                    found.append(term)
    return found


def disclaims(answer: str, competency_name: str) -> bool:
    """Whether this substantive answer explicitly denies the competency."""
    return bool(disclaimed_terms(answer, competency_name))
