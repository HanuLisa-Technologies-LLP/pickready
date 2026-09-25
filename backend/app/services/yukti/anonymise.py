"""The name-blind first pass over a resume, before any model reads it.

MOVED from `services/hiring/prescreen.py` (Vivekium release, Phase 2), with TWO
deliberate changes, argued in `anonymise`: a name is now removed on word
boundaries, as an employer already was, and the identity shapes (email, phone,
profile link) are removed BEFORE the names rather than after, because a name
scrubbed out of "priya@example.com" first left the domain behind. The pre-screen
grade this served is retired; the
guarantee it carried is not, and before this move Yukti's own prompt received
the RAW resume excerpt while only the deterministic pre-screen was name-blind
(PLAN-p2 NF-3). Now the one model that reads a resume to rank it reads it
anonymised, and this module is the one implementation of the scrub.

Until the pre-screen module is deleted it still carries its original copy;
the orchestrator's hunk re-points it here, so there is one implementation
again the moment both land (recorded in the Phase 2 report).

WHAT IS REMOVED
---------------
RPN-PHIL-001 section 52.2's anonymised first pass: the candidate's own name and
its parts, every employer and institution name the parser found, and the
shapes that carry identity whatever the name is (email addresses, phone
numbers, personal social profile links). Institutional pedigree therefore
contributes NOTHING to the ranking, which is section 8.9's ceiling set to zero
by the absence of the input rather than by a branch somebody could flip.

WHAT IS NEVER REMOVED
---------------------
A term the job is asking about (`protected_terms`, the job's skill names and
their ontology equivalents). An employer called Oracle, Docker or Elastic
shares its name with a technology, and scrubbing it would cost the candidate
exactly the evidence they are being ranked on.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Iterable, Mapping

from app.services.hiring import ontology

__all__ = [
    "anonymise",
    "identities",
    "name_tokens",
    "organisations_from",
    "terms",
]

#: Identity shapes an anonymised pass must not carry (section 52.2).
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
#: A long run of digits and separators. Written as a LENGTH rule rather than as
#: a set of national formats, because a format list is a list of the countries
#: whoever wrote it thought of. It also catches bare year ranges, which is not
#: collateral damage: RPN-PHIL-001 section 52.2 puts age indicators on the same
#: list as the name, and a graduation year is one.
_PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\b\d[\d\s().-]{6,}\d\b")
_URL_HOST = re.compile(r"\b(?:linkedin\.com|twitter\.com|x\.com|facebook\.com)/\S+", re.IGNORECASE)

_WORD = re.compile(r"[a-z0-9+#&/.\-]{2,}")

#: Words that carry no competency signal. Kept short on purpose: an aggressive
#: stop list is a second, invisible vocabulary filter, and the whole point of
#: the ontology is that this product does not decide which words count.
_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have in is it its of on or that the to
    with will you your our we they this these those been being do does not was
    were role job work working team teams company year years experience
    responsible responsibilities including etc various multiple across using use
    used new strong good excellent ability able skills skill knowledge
    """.split()
)


def terms(value: str | None) -> set[str]:
    """The significant lowercase words of `value`, stop words removed."""
    if not value:
        return set()
    return {
        w.strip(".-/")
        for w in _WORD.findall(str(value).casefold())
        if w.strip(".-/") and w.strip(".-/") not in _STOPWORDS
    }


#: Words that name a corporate wrapper rather than a brand. Scrubbing these
#: would remove ordinary nouns from technical prose while removing no pedigree
#: at all: "Systems" out of "distributed systems" costs a candidate a real term
#: and hides nobody's employer.
_ORG_SUFFIXES = frozenset(
    """
    inc llc llp ltd limited plc gmbh bv nv ag sa pvt pte co corp
    corporation company companies group holdings holding partners partnership
    associates ventures capital technologies technology tech systems system
    solutions services service consultancy consulting labs lab laboratories
    software digital global international worldwide industries enterprises
    enterprise studios studio works media networks network data analytics
    """.split()
)

#: The shortest org token worth scrubbing. Two-letter and three-letter tokens
#: are initialisms that collide with everything ("IT", "AI", "SAP", "ACE").
_MIN_ORG_TOKEN_CHARS = 4


def _org_patterns(
    organisations: Iterable[str], protected: frozenset[str]
) -> list[str]:
    """The org names safe to remove, longest first.

    LONGEST FIRST so "Tata Consultancy Services" is removed before "Tata" would
    be, and the shorter pattern is not left matching the tail of the longer one.
    Same ordering rule `identities` already follows for a person's name.
    """
    patterns: list[str] = []
    for raw in organisations:
        name = " ".join(str(raw or "").split())
        if len(name) < _MIN_ORG_TOKEN_CHARS:
            continue
        if name.casefold() in protected:
            continue
        # The whole name first. A multi-word brand is the thing that carries
        # pedigree, and it is also the safest thing to remove: "Goldman Sachs"
        # collides with nothing.
        patterns.append(name)
        words = [w for w in re.split(r"[^\w&]+", name) if w]
        if len(words) < 2:
            continue
        for word in words:
            key = word.casefold()
            if len(word) < _MIN_ORG_TOKEN_CHARS:
                continue
            if key in _ORG_SUFFIXES or key in _STOPWORDS or key in protected:
                continue
            patterns.append(word)
    return sorted(set(patterns), key=len, reverse=True)


def anonymise(
    text: str | None,
    *,
    identities: Iterable[str] = (),
    organisations: Iterable[str] = (),
    protected_terms: Iterable[str] = (),
) -> str:
    """Section 52.2's anonymised first pass, applied to resume text.

    Removes the named identities (the candidate's own name and its parts), the
    employer and institution names the parser found, and the shapes that carry
    identity whatever the name is: email addresses, phone numbers and personal
    social profile links. It is deliberately NOT a general PII scrubber. What it
    guarantees is the property the fairness test asserts, that the same document
    under a different name and a different letterhead reads identically to the
    model that ranks it.

    BOTH NAMES AND ORGANISATIONS ARE REMOVED ON WORD BOUNDARIES. Unbounded, an
    employer called "Ace" removes "namespace" and "tracer", and one called
    "Data" removes "Database", mangling technical prose in a way that costs a
    candidate real terms and that nothing downstream could detect. That is the
    failure this codebase's standing rule names: a guard that mangles a real
    answer fails invisibly.

    THE NAME HALF IS THE ONE CHANGE MADE IN THE MOVE. The pre-screen copy
    removed a person's name as a bare substring, on the reasoning that a name
    is a handful of tokens we supply. The tokens are short, which is the
    problem: a candidate called Ram lost "program" to "prog", and one called
    Anu lost "manual" and "manufacturing". The cost of the scrub then depended
    on the candidate's NAME, which is the one thing a name-blind pass exists to
    make irrelevant.

    `protected_terms` IS THE SECOND HALF OF THAT GUARD AND IT IS THE LOAD
    BEARING ONE. It carries the skills this resume is about to be judged
    against, so a word the JOB IS ASKING ABOUT is never removed. An employer
    called Oracle, Docker or Elastic shares its name with a technology, and
    those are not in the ontology's equivalence table; the skill list can
    protect them, which makes scrubbing UNABLE to remove a term that could have
    earned evidence (`tests/test_yukti_anonymise.py` asserts it directly).
    """
    out = str(text or "")
    # The identity SHAPES go first. Run after the name scrub, an address such
    # as "priya@example.com" has already lost "priya" on a word boundary, and
    # what is left ("@example.com") no longer matches the address pattern, so
    # the domain reaches the model. A personal domain is an identity.
    out = _EMAIL.sub(" ", out)
    out = _URL_HOST.sub(" ", out)
    out = _PHONE.sub(" ", out)
    protected = frozenset(
        term
        for raw in protected_terms
        for term in (
            {" ".join(str(raw or "").split()).casefold()}
            | {w for w in terms(raw)}
            | {s for w in terms(raw) for s in ontology.equivalent(w)}
        )
        if term
    )
    for identity in identities:
        token = " ".join(str(identity or "").split())
        if len(token) < 2:
            continue
        # A single name word that is also a term the job asks about ("Swift",
        # "Rust") is left in place: it then reads as the technology, which is
        # exactly what the model is told to look for, and removing it would
        # delete the candidate's evidence because of their name.
        if " " not in token and token.casefold() in protected:
            continue
        out = re.sub(
            r"(?<!\w)%s(?!\w)" % re.escape(token), " ", out, flags=re.IGNORECASE
        )
    for pattern in _org_patterns(organisations, protected):
        out = re.sub(
            r"(?<![\w&])%s(?![\w&])" % re.escape(pattern),
            " ",
            out,
            flags=re.IGNORECASE,
        )
    return out


def organisations_from(parsed: Mapping[str, Any] | Any) -> tuple[str, ...]:
    """Every employer and institution name the parser found, for the scrub.

    READ HERE AND USED ONLY TO DELETE TEXT, exactly like `identities`. Neither
    name reaches a prompt as a field: the structured fields are never sent to
    Yukti's model at all, and this is what removes the same names from the
    PROSE, which is where a candidate writes "Rebuilt the ingestion pipeline at
    <employer>" and would otherwise carry the pedigree into the reading.
    """
    names: list[str] = []
    if not isinstance(parsed, dict):
        return ()
    for key, field in (("employment_history", "company"), ("education", "institution")):
        entries = parsed.get(key)
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = " ".join(str(entry.get(field) or "").split())
            if name:
                names.append(name)
    return tuple(dict.fromkeys(names))


def name_tokens(full_name: str | None) -> tuple[str, ...]:
    """The name tokens `anonymise` must strip, longest first.

    Longest first, so "Priya Raghunathan" is removed before "Priya" is, and the
    shorter pattern is not left matching the tail of the longer one.
    """
    full = str(full_name or "").strip()
    if not full:
        return ()
    parts = [p for p in re.split(r"\s+", full) if len(p) > 2]
    return tuple(sorted({full, *parts}, key=len, reverse=True))


async def identities(session: Any, candidate_id: uuid.UUID | None) -> tuple[str, ...]:
    """The candidate's name tokens, read from their row, for the scrub.

    Read here and used only to DELETE text. The name never reaches a prompt.
    """
    from app.models.candidate import Candidate  # noqa: PLC0415

    if candidate_id is None:
        return ()
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        return ()
    return name_tokens(getattr(candidate, "full_name", None))
