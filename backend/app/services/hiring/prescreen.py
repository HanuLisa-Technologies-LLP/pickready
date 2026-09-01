"""Yukti's pre-screen grade: A / B / C / Hold, at resume upload (spec-doc6 4.4).

WHAT THIS REPLACES, AND WHY IT IS NOT THE SAME THING
------------------------------------------------------
Until this module existed, the resume-stage grade came from one of two places:
a four-parameter model reading of the resume, or, when the provider chain was
exhausted, a linear map from RETRIEVAL RANK into a 4..8 band. The second one is
the interesting failure. It turned "this document is lexically similar to that
document" into a grade a recruiter reads, and document similarity is exactly the
measurement the Runbook says systematically undervalues candidates who describe
their work in non-standard vocabulary (RPN-PHIL-001 section 58). The
retrieval-rank band is deleted in the same change that added this module;
`matching.py` now asks this module for the deterministic breakdown, so there is
ONE resume-stage grader rather than a real one and a similarity-shaped stand-in
for it.

THE ONE ARTIFACT, UNDER TWO NAMES
-----------------------------------
The Candidate Dashboard calls column 3 the "Pre-Screen Grade" and spec-doc5
calls Yukti's resume-stage output the "AI Score". spec-doc6 C9 settles it: they
are the SAME artifact, and the named grade is the product surface. This module
produces it once.

A RESUME LINE IS A CLAIM, NOT A FACT
--------------------------------------
Everything here is built on sections 5 and 6: a candidate's own document is E0
or E1 evidence, an artefact they link to is E2, and nothing on a resume can be
higher than that, because nothing on a resume has been observed or verified. So
the grade is a statement about EVIDENCE STRENGTH, per section 6.5's arithmetic,
and never about how closely two documents read alike. A beautifully written
bullet is E0 (section 6.2); the same bullet naming the system, the mechanism and
the number is E1; the same bullet with a repository behind it is E2.

That is also why an A is deliberately hard to reach from a resume alone. The
Dashboard Specification requires column 3 to render muted precisely so an early
signal cannot read as a final verdict, and a grader that handed out As on prose
quality would reintroduce the bug the styling rule exists to prevent.

THE SCORE IS COMPUTED OVER WHAT THE RESUME SPEAKS TO, NEVER OVER WHAT IT OMITS
--------------------------------------------------------------------------------
Section 6.6 is explicit, and it is the fairness rule this module exists to
honour: a requirement the candidate was never asked about is UNKNOWN. It is
EXCLUDED from the weighted average, the remaining weights renormalise, and
CONFIDENCE falls. It is never scored zero, because a zero is arithmetically
identical to negative evidence, and a resume that is silent about Kubernetes is
not a resume that says the candidate cannot do Kubernetes.

The practical consequence is the point. A career changer, a fresher and a
candidate out of the formal sector all produce thin resumes against a standard
requirement set. Under this module they get whatever their actual claims earn,
with a low confidence that sends a human to look. Under a coverage-as-score
design they would get a confident bad grade that does not.

NAME-BLINDNESS IS STRUCTURAL, NOT A CONVENTION
------------------------------------------------
Section 52.2 requires an anonymised first pass. `PreScreenInput` has no name
field, no email field, no institution field and no employer-brand field, and
there is no free-form context dict a name could travel in. `claims_from_resume`
strips identity out of the resume text before a claim is built, and the profile
adapter drops `company` from employment history and `institution` from education
on the way in -- both by never reading the field AND by scrubbing the same names
out of the prose, which is where a candidate writes "Rebuilt the pipeline at
<employer>" and would otherwise carry the brand into a claim term.
This is the same shape as Miti's `EvaluatorInput`, and for the same reason: a
test that asserts the ABSENCE of specific field names passes happily once
somebody adds a field called `notes`.

THE PEDIGREE CAP IS SET TO ZERO HERE
--------------------------------------
Section 8.9 allows institutional pedigree to contribute at most 5% of the score,
only through Trajectory, and lets a client lower that ceiling to zero freely.
This module lowers it to zero, by never reading an employer brand or an
institution name at all. Enforcement is the absence of the input, so there is no
branch that could be flipped and no configuration that could raise it by
accident.

WHAT THIS MODULE MUST NOT DO
------------------------------
It must never reject anybody. The grade vocabulary has no rejecting value:
`Hold` is "a person needs to look at this", which is what the Runbook's own HOLD
row means (section 10.8, "not ranked pending human disposition"). A resume that
cannot be graded routes to a human; it never routes to a decision.

It must never call a model. The grade is arithmetic over cited constants, so two
runs over one resume produce one grade, a provider outage cannot change anybody's
triage position, and a disagreement is a line somebody can point at.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.services.hiring import ontology, runbook_data

logger = logging.getLogger(__name__)

__all__ = [
    "GRADE_A",
    "GRADE_B",
    "GRADE_C",
    "GRADE_HOLD",
    "GRADES",
    "STATE_FRESHER",
    "STATE_INFORMAL_SECTOR",
    "STATE_CAREER_CHANGER",
    "STATE_RETURNER",
    "STATE_STANDARD",
    "REQUIREMENTS_FROM_COMPETENCIES",
    "REQUIREMENTS_FROM_JD",
    "Claim",
    "EmploymentSpan",
    "PreScreenInput",
    "PreScreenGrade",
    "PreScreenScore",
    "PreScreenResult",
    "PreScreenUnavailable",
    "anonymise",
    "candidate_states",
    "claims_from_resume",
    "clamp_tier_strength",
    "decay_multiplier",
    "domain_clock",
    "effective_strength",
    "grade",
    "requirement_terms",
    "tier_strength",
]


class PreScreenUnavailable(RuntimeError):
    """The Runbook data this grader is built from could not be read.

    Raised rather than substituted for. Every constant below is a Runbook value
    with a citation, and a default quietly filled in for a failed load would be
    a magic number wearing a citation it did not earn.
    """


# The named grades, which are the product surface.
#
# Four values, exactly the Dashboard Specification's column 3. There is no
# fifth, and in particular there is no rejecting value: the enforcement of "no
# flag auto-rejects" is the absence of the capability, not a check somewhere.
GRADE_A = "A"
GRADE_B = "B"
GRADE_C = "C"
GRADE_HOLD = "Hold"
GRADES: tuple[str, ...] = (GRADE_A, GRADE_B, GRADE_C, GRADE_HOLD)

# Candidate states (section 39).
#
# Recorded, reported, and deliberately WITHOUT arithmetic consequence at this
# stage. Section 39 gives each state a "core adjustment" phrased in terms of the
# five dimensions, which do not exist yet at resume upload. What each state does
# here is guarantee the candidate is not penalised for evidence that could not
# exist: section 8.5 for the fresher, 8.6 for the break, 40.3 for the changer,
# 40.5 for the undocumented history. That guarantee is already structural,
# because an absent requirement is UNKNOWN and never a zero, so the states carry
# information forward rather than moving a number here.
STATE_FRESHER = "fresher"
STATE_INFORMAL_SECTOR = "informal_sector"
STATE_CAREER_CHANGER = "career_changer"
STATE_RETURNER = "career_break_returner"
STATE_STANDARD = "standard_experienced"

#: Precedence when a candidate is in more than one state. A fresher with no
#: documented employment is a fresher, not an informal-sector candidate, so
#: experience length is read first.
_STATE_PRECEDENCE: tuple[str, ...] = (
    STATE_FRESHER,
    STATE_INFORMAL_SECTOR,
    STATE_CAREER_CHANGER,
    STATE_RETURNER,
    STATE_STANDARD,
)

#: Where the requirement set came from. Pre-screen grading runs at resume
#: upload, which is before any scorecard can be frozen, so it must be able to
#: grade against the job description alone. It records WHICH, because a grade
#: written against a JD and a grade written against an approved competency list
#: are answers to different questions and a consumer must not have to guess.
REQUIREMENTS_FROM_COMPETENCIES = "job_competencies"
REQUIREMENTS_FROM_JD = "job_description"

#: The resume-stage evidence tiers. Higher tiers exist and none of them can be
#: reached by a document the candidate wrote (section 6.1): E3 needs a
#: controlled response, E4 needs observation, E5 needs a third party.
TIER_ASSERTED = "E0"
TIER_SPECIFIC = "E1"
TIER_ARTEFACT = "E2"
RESUME_STAGE_TIERS: tuple[str, ...] = (TIER_ASSERTED, TIER_SPECIFIC, TIER_ARTEFACT)

#: Section 6.3's two domain clocks, by their Runbook names.
CLOCK_FAST = "fast_moving"
CLOCK_STABLE = "stable"

#: Section 39's trigger for the career-break state, in months.
RETURNER_GAP_MONTHS = 6
#: Section 39's trigger for the fresher state, in years of professional
#: experience.
FRESHER_MAX_YEARS = 1.0

#: Section 8.9's ceiling on what institutional pedigree may contribute to the
#: score, as a fraction. This module contributes ZERO, which section 8.9
#: permits explicitly ("a client may lower it to zero freely"); the constant is
#: kept so the fairness test can assert the measured contribution against the
#: Runbook's ceiling rather than against a hand-typed 0.
PEDIGREE_CONTRIBUTION_CEILING = 0.05


def _tiers() -> Mapping[str, Any]:
    try:
        return runbook_data.evidence_tiers()
    except Exception as exc:  # noqa: BLE001 - re-raised, never swallowed
        raise PreScreenUnavailable(
            "evidence_tiers.yaml could not be loaded, so no tier strength, "
            "quality modifier or decay multiplier is available"
        ) from exc


def _band_data() -> Mapping[str, Any]:
    try:
        return runbook_data.bands()
    except Exception as exc:  # noqa: BLE001 - re-raised, never swallowed
        raise PreScreenUnavailable(
            "bands.yaml could not be loaded, so no confidence coefficient or "
            "label threshold is available"
        ) from exc


def clamp_tier_strength(value: float) -> float:
    """Section 6.1's [0.05, 1.00] clamp, as its own function so it is testable.

    Added to the Runbook in v1.2. A strength above 1.00 would mean one piece of
    evidence counted for more than certainty. The floor matters more here: E0
    minus its departmental allowance is zero, and a zero-strength claim is
    deleted from the ledger by arithmetic rather than recorded as weak. A weak
    claim that is visible can be probed; a claim arithmetic removed cannot.
    """
    return min(1.00, max(0.05, float(value)))


def tier_strength(tier: str) -> float:
    """Section 6.1's default strength for a tier, through the 6.1 clamp."""
    entry = _tiers()["tiers"].get(tier)
    if entry is None:
        raise PreScreenUnavailable(f"evidence_tiers.yaml has no tier {tier!r}")
    return clamp_tier_strength(float(entry["default_strength"]))


def _modifier(name: str, bound: str) -> float:
    """One end of a section 6.4 quality-modifier range."""
    entry = _tiers()["quality_modifiers"].get(name)
    if entry is None:
        raise PreScreenUnavailable(f"evidence_tiers.yaml has no modifier {name!r}")
    return float(entry[bound])


#: A modifier that does not apply is 1.0. That is not a Runbook value and does
#: not pretend to be one: it is the multiplicative identity, which is what "this
#: modifier was not applied" has to mean inside a product of factors.
_NEUTRAL = 1.0

_AGE_LOW = re.compile(r"^\s*(\d+)")


def decay_multiplier(age_years: float | None, clock: str) -> float:
    """Section 6.3's decay multiplier for an event of this age on this clock.

    `None` means the resume did not date the claim, and an undated claim is NOT
    decayed. Section 6.3's own anti-pattern warning is that decay must not
    become an age proxy; guessing a date for an undated line and then decaying
    it is precisely how that happens.
    """
    if age_years is None:
        return _NEUTRAL
    chosen: Mapping[str, Any] | None = None
    for row in _tiers()["decay"]["multipliers"]:
        match = _AGE_LOW.match(str(row["age_of_underlying_event"]))
        if match is None:
            continue
        if float(age_years) >= float(match.group(1)):
            chosen = row
    if chosen is None:
        return _NEUTRAL
    key = CLOCK_FAST if clock == CLOCK_FAST else CLOCK_STABLE
    return float(chosen[key])


def domain_clock(*words: str | None) -> str:
    """Which section 6.3 clock this role runs on.

    Unknown resolves to the STABLE clock, which decays less. That direction is
    chosen deliberately: the fast clock takes 55% off a ten-year-old claim, and
    applying it to a domain nobody classified would cost older candidates score
    on a guess, which is the age proxy section 6.3 warns against.
    """
    haystack = " ".join(w for w in words if w).casefold()
    if not haystack:
        return CLOCK_STABLE
    fast = str(_tiers()["decay"]["fast_moving_domains"])
    for term in (t.strip().casefold() for t in fast.split(",")):
        if term and term in haystack:
            return CLOCK_FAST
    return CLOCK_STABLE


# Reading one resume line: tier, and the three per-evidence modifiers.
#
# Every detector below is deterministic and lexical. That is not a shortcut
# taken for speed: a model asked "does this line contain checkable specifics"
# would answer differently for two candidates who wrote the same thing on two
# different days, and the whole value of a triage grade is that it is stable
# enough to sort a list by.

#: A URL to something the candidate is offering as an artefact. Section 6.1's
#: E2 is "portfolio, sample, document, repository link supplied by the
#: candidate", so the link itself is the tier signal.
_ARTEFACT_URL = re.compile(
    r"\b(?:https?://|www\.)\S+|\b(?:github|gitlab|bitbucket|behance|dribbble|"
    r"kaggle|figma|medium|notion|drive\.google)\.[a-z]{2,}\S*",
    re.IGNORECASE,
)

#: A number that makes a claim checkable. Section 6.1's E1 is "self-report
#: containing checkable specifics (numbers, systems, names, mechanisms)".
#:
#: The last alternative, a number followed by a plural noun, is what keeps this
#: from being a units list somebody has to keep extending. A units list is a
#: vocabulary filter wearing different clothes: whoever writes it enumerates the
#: measurements they have seen, and a candidate measuring their work in tonnes,
#: invoices or panchayats scores as though they gave no number at all.
#:
#: Deliberately NOT a bare year. "2019 to 2022" is a date range on every resume
#: ever written and says nothing about the work, so a number reaching this
#: detector has to be attached to something.
_QUANTITY = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent)"
    r"|(?:rs\.?|inr|usd|eur|\u20b9|\$)\s?\d"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:k|m|mn|bn|cr|crore|lakh|lakhs|x)\b"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:ms|sec|secs|second|seconds|min|mins|minute|minutes|"
    r"hour|hours|hr|hrs|day|days|week|weeks|month|months|year|years|"
    r"kb|mb|gb|tb|pb|qps|rps|tps)\b"
    r"|\b\d[\d,]*\s+[a-z][a-z\-]{2,}s\b",
    re.IGNORECASE,
)

#: A mechanism verb. Section 8.1's impact test asks what this person actually
#: did; a line built on a mechanism verb answers it, a line built on "responsible
#: for" does not.
_MECHANISM = re.compile(
    r"\b(?:migrat\w+|refactor\w+|instrument\w+|automat\w+|benchmark\w+|profil\w+|"
    r"partition\w+|shard\w+|index\w+|cach\w+|negotiat\w+|renegotiat\w+|"
    r"consolidat\w+|reconcil\w+|forecast\w+|calibrat\w+|standardis\w+|"
    r"standardiz\w+|decompos\w+|rearchitect\w+|re-architect\w+|rewrote|rebuilt|"
    r"redesign\w+|deprecat\w+|rollback|roll back|rolled back|root[- ]caus\w+)\b",
    re.IGNORECASE,
)

#: The stems of the verbs that carry ownership of a piece of work. Written as
#: stems with open endings because the tense a resume uses is a property of
#: where the writer learnt English and not of what they did: "I am leading",
#: "I have led" and "I led" are the same claim, and the first two are ordinary
#: Indian business English. A pattern that only recognised the simple past was
#: reading fluency and reporting it as attribution, which is the failure section
#: 52.4's proxy audit names by name.
_OWNERSHIP_STEM = (
    r"(?:own\w*|led|lead\w*|spearhead\w*|architect\w*|built|build\w*|design\w*|"
    r"drove|driv\w*|deliver\w*|ship\w*|found\w*|creat\w*|implement\w*|"
    r"introduc\w*|ran|run\w*|set\s+up|establish\w*)"
)

#: First-person ownership. Section 6.4's attribution modifier: "'We built' vs
#: 'I owned'; ambiguous ownership reduces". Section 6.4 calls this "one of the
#: highest-value discriminations in resume evaluation and almost never made by
#: similarity-based systems", which is a fair summary of why it is here.
#:
#: Four ways a resume claims ownership, and all four are equally valid:
#: a first-person clause with any auxiliary, the implied-subject bullet style
#: that starts a line with the verb, the agent-passive an Indian-English writer
#: often prefers ("was owned by me"), and the explicit sole-ownership phrases.
#: "We" is deliberately absent: it belongs to `_COLLECTIVE`, which is the whole
#: distinction section 6.4 is drawing.
_OWNED = re.compile(
    r"\bi\s+(?:(?:have|had|has|am|was|were|was\s+the|been)\s+)?" + _OWNERSHIP_STEM + r"\b"
    r"|^(?:own\w*|led|spearhead\w*|architect\w*|built|design\w*|deliver\w*|"
    r"establish\w*|introduc\w*)\b"
    r"|\b" + _OWNERSHIP_STEM + r"\s+by\s+(?:me|myself)\b"
    r"|\bsingle[- ]handedly\b"
    r"|\bsole\s+(?:owner|author|engineer|designer|contributor)\b"
    r"|\bmy\s+(?:responsibility|ownership|remit)\b",
    re.IGNORECASE,
)

#: Collective or peripheral attribution.
_COLLECTIVE = re.compile(
    r"\b(?:we\s+\w+|our\s+team|the\s+team|as\s+part\s+of|part\s+of\s+a?\s?team|"
    r"assisted|supported\s+the|involved\s+in|participated|contributed\s+to|"
    r"worked\s+(?:with|under)|helped\s+the)\b",
    re.IGNORECASE,
)

#: Scope evidence, per section 8.3: people managed, budget owned, systems owned,
#: blast radius. Section 8.3's rule is that title is context and scope is the
#: score input, so this is the modifier that reads scope.
_SCALE = re.compile(
    r"\b(?:\d[\d,\.]*\s?(?:m|mn|bn|k|lakh|crore|cr)\b|team\s+of\s+\d+|"
    r"\d+\s+(?:direct\s+reports?|reports?|engineers?|people|members?|stores?|"
    r"branches|sites?|plants?|clients?|accounts?)|p&l|budget\s+of|"
    r"revenue\s+of|headcount|\d+\s?(?:tb|pb|gb)\b|\d+\s?(?:qps|rps|tps)\b|"
    r"\d+\s?(?:million|billion)\s+(?:users?|requests?|transactions?|records?))\b",
    re.IGNORECASE,
)

#: Identity shapes an anonymised pass must not carry (section 52.2).
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
#: A long run of digits and separators. Written as a LENGTH rule rather than as
#: a set of national formats, because a format list is a list of the countries
#: whoever wrote it thought of. It also catches bare year ranges, which is not
#: collateral damage: RPN-PHIL-001 §52.2 puts age indicators on the same list as
#: the name, and a graduation year is one.
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


def _terms(value: str | None) -> set[str]:
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
    Same ordering rule `_identities` already follows for a person's name.
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
    under a different name and a different letterhead grades identically.

    ORGANISATIONS ARE REMOVED ON WORD BOUNDARIES AND IDENTITIES ARE NOT, AND THE
    DIFFERENCE IS DELIBERATE. A person's name is supplied by us from the
    candidate row and is a handful of tokens; a company name is whatever a
    parser pulled off a letterhead, and this scrub is a case-insensitive
    replace. Unbounded, an employer called "Ace" removes "namespace" and
    "tracer", and one called "Data" removes "Database" -- mangling technical
    prose in a way that costs a candidate real terms and that nothing
    downstream could detect. That is the failure this codebase's standing rule
    names: a guard that mangles a real answer fails invisibly.

    `protected_terms` IS THE SECOND HALF OF THAT GUARD AND IT IS THE LOAD
    BEARING ONE. It carries the requirements this resume is about to be graded
    against, so a word the JOB IS ASKING ABOUT is never removed. An employer
    called Oracle, Docker or Elastic shares its name with a technology, and
    those are not in the ontology's equivalence table -- it is a curated set of
    synonyms, not a registry of every product name, so it cannot be the guard
    here. The requirement list can: it makes scrubbing UNABLE to remove a term
    that could have earned a match -- a property
    `test_scrubbing_an_employer_never_lowers_a_grade` asserts directly, rather
    than a promise resting on how good a word list is.

    So the two directions are both closed. Adding a brand to a resume moves the
    grade by nothing (section 8.9's pedigree ceiling, set to zero here, pinned
    by `test_yukti_live`), and removing one cannot move it either.
    """
    out = str(text or "")
    for identity in identities:
        token = str(identity or "").strip()
        if len(token) < 2:
            continue
        out = re.sub(re.escape(token), " ", out, flags=re.IGNORECASE)
    protected = frozenset(
        term
        for raw in protected_terms
        for term in (
            {" ".join(str(raw or "").split()).casefold()}
            | {w for w in _terms(raw)}
            | {s for w in _terms(raw) for s in ontology.equivalent(w)}
        )
        if term
    )
    for pattern in _org_patterns(organisations, protected):
        out = re.sub(
            r"(?<![\w&])%s(?![\w&])" % re.escape(pattern),
            " ",
            out,
            flags=re.IGNORECASE,
        )
    out = _EMAIL.sub(" ", out)
    out = _URL_HOST.sub(" ", out)
    out = _PHONE.sub(" ", out)
    return out


@dataclass(frozen=True)
class Claim:
    """One resume line, read as a claim rather than as a fact.

    `terms` is what the ontology compares; `text` is kept only so a ledger entry
    can point at the line it came from. Neither carries a name: `claims_from_resume`
    anonymises before it splits.
    """

    text: str
    terms: frozenset[str]
    tier: str
    specificity: float
    attribution: float
    scale_relevance: float
    #: Age of the underlying event in years, or None when the line is undated.
    #: None is not zero: an undated claim is not decayed at all (section 6.3).
    event_age_years: float | None = None
    #: Which part of the resume the line came from. Recorded for the ledger and
    #: for the fresher rule, never used to weight a claim up or down.
    origin: str = "resume_text"

    def strength(self, clock: str) -> float:
        """Section 6.5's per-evidence strength, for this claim on this clock."""
        return (
            tier_strength(self.tier)
            * self.specificity
            * self.attribution
            * self.scale_relevance
            * decay_multiplier(self.event_age_years, clock)
        )


def read_claim(
    text: str,
    *,
    origin: str = "resume_text",
    event_age_years: float | None = None,
) -> Claim:
    """Tier and modify one line, per sections 6.1, 6.2 and 6.4.

    The tier ladder is the fabrication-cost ladder of section 6.2 and nothing
    else. An artefact link is E2 because somebody has to have made the thing; a
    line naming a system, a mechanism or a checkable number is E1 because it can
    be probed; everything else is E0 because it is free to write. Prose quality
    moves nothing, which is the whole posture: we do not detect fabrication, we
    move weight onto evidence that is expensive to fake.
    """
    line = str(text or "").strip()
    artefact = bool(_ARTEFACT_URL.search(line))

    # KINDS of specific, not a COUNT of hits, and the difference is a fairness
    # property rather than a tidiness one. Section 6.1 enumerates the kinds it
    # means ("numbers, systems, names, mechanisms"), and counting hits instead
    # lets the WORD FOR THE WORK tip the threshold: a candidate who wrote "test
    # automation" picks up a mechanism hit from their own vocabulary that a
    # candidate who wrote "SDET" does not, and the two would then be graded
    # differently for describing the same job. Measured on the vocabulary corpus
    # before this was changed: three pairs diverged for exactly that reason.
    kinds = sum(
        (
            bool(_QUANTITY.search(line)),
            bool(_MECHANISM.search(line)),
        )
    )

    if artefact:
        tier = TIER_ARTEFACT
    elif kinds:
        tier = TIER_SPECIFIC
    else:
        tier = TIER_ASSERTED

    if kinds >= 2:
        specificity = _modifier("specificity", "maximum")
    elif kinds == 1:
        specificity = _NEUTRAL
    else:
        specificity = _modifier("specificity", "minimum")

    if _OWNED.search(line):
        attribution = _modifier("attribution_clarity", "maximum")
    elif _COLLECTIVE.search(line):
        attribution = _modifier("attribution_clarity", "minimum")
    else:
        attribution = _NEUTRAL

    # Section 6.4's scale-relevance range runs 0.8 to 1.15, and only its upper
    # half is reachable here. The lower end means "evidence at a scale unlike
    # the role's context", and a resume-stage pass has no role context to
    # compare against; applying a penalty on a comparison nobody made would be
    # inventing the judgement rather than deferring it.
    scale = _modifier("scale_relevance", "maximum") if _SCALE.search(line) else _NEUTRAL

    return Claim(
        text=line,
        terms=frozenset(_terms(line)),
        tier=tier,
        specificity=specificity,
        attribution=attribution,
        scale_relevance=scale,
        event_age_years=event_age_years,
        origin=origin,
    )


_LINE_SPLIT = re.compile(r"[\r\n•●·]+|(?<=[.;])\s{1,}")
#: A line shorter than this is a heading, a date or a fragment, not a claim.
_MIN_CLAIM_CHARS = 20


def claims_from_resume(
    resume_text: str | None,
    *,
    skills: Sequence[str] = (),
    identities: Iterable[str] = (),
    role_lines: Sequence[tuple[str, float | None]] = (),
    organisations: Iterable[str] = (),
    protected_terms: Iterable[str] = (),
) -> tuple[Claim, ...]:
    """Every claim this resume makes, anonymised first.

    Three sources, and the distinction between them is a tier distinction rather
    than a trust one:

      * `skills`, the parsed skills list. A word on a list with nothing behind
        it is section 6.1's E0 exactly: an unverifiable self-claim that costs
        nothing to produce. It is still admitted, because section 6.1's floor
        exists so a weak claim stays visible and probeable.
      * `role_lines`, employment bullets carrying the end date of the role they
        belong to, so section 6.3's decay has an event date to work from.
      * the rest of the resume text, undated and therefore undecayed.
    """
    organisations = tuple(organisations)
    protected_terms = tuple(protected_terms)
    cleaned = anonymise(
        resume_text,
        identities=identities,
        organisations=organisations,
        protected_terms=protected_terms,
    )
    claims: list[Claim] = []
    seen: set[str] = set()

    for skill in skills:
        term = str(skill or "").strip()
        if not term:
            continue
        key = f"skill:{term.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        claims.append(
            Claim(
                text=term,
                terms=frozenset(_terms(term)),
                # A skills-list entry is a bare assertion whatever it names, so
                # it is tiered directly rather than through `read_claim`: the
                # specificity detector would otherwise read "99.9% uptime" as a
                # skill with checkable specifics when it is a word on a list.
                tier=TIER_ASSERTED,
                # NEUTRAL, not the minimum. Section 6.4's specificity modifier
                # reduces for "generic language" and increases for "checkable
                # mechanism/number/system names"; a skills-list entry IS a
                # system name, so nothing reduces it. What makes it weak is its
                # TIER: E0, an unverifiable self-claim, free to produce. The two
                # are different statements and stacking them would charge a bare
                # assertion twice for being a bare assertion.
                specificity=_NEUTRAL,
                attribution=_NEUTRAL,
                scale_relevance=_NEUTRAL,
                event_age_years=None,
                origin="skills_list",
            )
        )

    for line, age in role_lines:
        # ANONYMISED LIKE THE RESUME TEXT, and for the same reason. This loop
        # read the bullet verbatim until 2026-09-01, so a role line beginning
        # with the candidate's name kept it: the name landed in `Claim.text`
        # and, worse, `read_claim` turned it into one of the `terms` the
        # ontology COMPARES. Two identical histories under two names then
        # produced two different term sets, which is exactly the property
        # section 52.2 exists to guarantee against, and this module's own
        # docstrings already promised ("anonymised first"; "neither carries a
        # name"). Cleaned BEFORE the length check and the dedup key so both
        # measure the text that is actually kept.
        body = anonymise(
            line,
            identities=identities,
            organisations=organisations,
            protected_terms=protected_terms,
        ).strip()
        if len(body) < _MIN_CLAIM_CHARS:
            continue
        key = f"role:{body.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        claims.append(read_claim(body, origin="employment_history", event_age_years=age))

    for raw in _LINE_SPLIT.split(cleaned):
        body = str(raw or "").strip()
        if len(body) < _MIN_CLAIM_CHARS:
            continue
        key = f"text:{body.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        claims.append(read_claim(body, origin="resume_text"))

    return tuple(claims)


@dataclass(frozen=True)
class EmploymentSpan:
    """One documented role, by DATES ONLY.

    No company field, and that absence is the pedigree cap (section 8.9) and the
    anonymised pass (section 52.2) at once. The title is kept because section
    8.3 says title is context, and it is read only to tell a career changer from
    a domain switcher, never to move a number.
    """

    title: str | None
    start: date | None
    end: date | None


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def employment_gaps(spans: Sequence[EmploymentSpan]) -> tuple[int, ...]:
    """Gaps in months between consecutive documented roles.

    RECORDED, NEVER SCORED (sections 8.6 and 40.2). This function exists so the
    dossier can say a break happened and a later stage can ask one neutral
    question about it. Nothing in `_score` reads its output, and
    `test_yukti_live` asserts that by holding the claim set fixed and varying
    only the spans.
    """
    dated = sorted(
        (s for s in spans if s.start is not None and s.end is not None),
        key=lambda s: (s.start, s.end),
    )
    gaps: list[int] = []
    for previous, current in zip(dated, dated[1:]):
        months = _months_between(previous.end, current.start)  # type: ignore[arg-type]
        if months > 0:
            gaps.append(months)
    return tuple(gaps)


def candidate_states(
    *,
    total_experience_years: float | None,
    spans: Sequence[EmploymentSpan],
    job_terms: frozenset[str],
    has_academic_claims: bool,
    today: date | None = None,
) -> tuple[frozenset[str], str]:
    """Section 39's state model, as a set plus a deterministic primary.

    A set rather than one value, because a returner can also be a career
    changer and section 39's table does not say which wins. The primary is
    fixed by `_STATE_PRECEDENCE` so a dashboard cell is deterministic, and the
    full set travels so nothing downstream has to re-derive it.

    NOTHING HERE MOVES THE SCORE. Every state is a statement about what evidence
    could not exist yet, and this module already refuses to score absent
    evidence at all.
    """
    now = today or datetime.now(timezone.utc).date()
    found: set[str] = set()

    years = total_experience_years
    if years is not None and float(years) < FRESHER_MAX_YEARS:
        found.add(STATE_FRESHER)
    elif not spans and has_academic_claims and years is None:
        # No documented employment and no stated total: an academic record with
        # nothing else is a fresher, not an undocumented work history.
        found.add(STATE_FRESHER)

    if not spans and (years is not None and float(years) >= FRESHER_MAX_YEARS):
        # Section 40.5. Work is claimed and none of it is documented. The
        # protocol is that this stays gradeable and that documentation absence
        # is never treated as a negative, which is what excluding it from the
        # score rather than zeroing it achieves.
        found.add(STATE_INFORMAL_SECTOR)

    gaps = employment_gaps(spans)
    trailing = 0
    dated_ends = [s.end for s in spans if s.end is not None]
    if dated_ends:
        trailing = _months_between(max(dated_ends), now)
    if any(g >= RETURNER_GAP_MONTHS for g in gaps) or trailing >= RETURNER_GAP_MONTHS:
        found.add(STATE_RETURNER)

    if spans and job_terms:
        titles = frozenset().union(*(_terms(s.title) for s in spans)) if spans else frozenset()
        if titles and not ontology.overlap(titles, job_terms):
            # Section 40.3. No overlap at all between what this person has been
            # called and what the role is called, ONCE VOCABULARY IS SET ASIDE.
            # Running it through the ontology matters: without it, a "Deputy
            # Manager, Business Finance" applying to an "FP&A Lead" role reads
            # as a career changer and is handled as one.
            found.add(STATE_CAREER_CHANGER)

    if not found:
        found.add(STATE_STANDARD)
    primary = next(state for state in _STATE_PRECEDENCE if state in found)
    return frozenset(found), primary


@dataclass(frozen=True)
class PreScreenInput:
    """Everything the pre-screen is allowed to see.

    THE FIELD SET IS THE ISOLATION. There is no name, no email, no photograph
    reference, no institution, no employer brand, no age, no gender, no address
    and no free-form context dict. Section 52.2's anonymised first pass is this
    field list, and it is asserted as a whole in `test_yukti_live` rather than
    by naming forbidden fields one at a time, because a future field called
    `notes` would pass the narrower test and reopen the hole.
    """

    requirements: tuple[str, ...]
    requirement_source: str
    claims: tuple[Claim, ...]
    clock: str = CLOCK_STABLE
    states: frozenset[str] = frozenset({STATE_STANDARD})
    primary_state: str = STATE_STANDARD
    gap_months: tuple[int, ...] = ()
    #: Present so the ledger can say the resume was unreadable rather than
    #: empty. A false here forces `Hold`, which routes to a person.
    resume_parsed: bool = True


@dataclass(frozen=True)
class PreScreenGrade:
    """THE PRODUCT SURFACE. Named values only.

    This type physically cannot carry a number, which is the enforcement of D8
    for the resume stage: the delivered document is named grades only, so the
    object the report path is handed has no numeric field for one to leak
    through. `test_yukti_live` walks `dataclasses.fields` and fails if a numeric
    annotation ever appears here.
    """

    grade: str
    confidence_label: str
    requirement_source: str
    primary_state: str
    evidence_note: str


@dataclass(frozen=True)
class PreScreenScore:
    """THE DASHBOARD TRIAGE ARTIFACT. Numbers live here and only here.

    D8 puts the Ready Pick Score on the dashboard and forbids it in the
    delivered report. Keeping the number in a separate type from the grade is
    what makes "the number cannot reach a report" a property of the code rather
    than a rule somebody has to remember: the report path is handed a
    `PreScreenGrade`, and there is nothing on it to serialise.
    """

    value: float
    confidence: float
    coverage: float
    assessed_requirements: int
    total_requirements: int
    best_tier: str | None


@dataclass(frozen=True)
class PreScreenResult:
    named: PreScreenGrade
    internal: PreScreenScore
    ledger: tuple[dict[str, Any], ...]

    @property
    def grade(self) -> str:
        return self.named.grade

    def report_payload(self) -> dict[str, str]:
        """What a delivered document may be handed. No numbers, by construction."""
        return {
            "grade": self.named.grade,
            "confidence": self.named.confidence_label,
            "requirement_source": self.named.requirement_source,
            "candidate_state": self.named.primary_state,
            "evidence_note": self.named.evidence_note,
        }

    def dashboard_cell(self) -> dict[str, Any]:
        """What the Candidate Dashboard renders (column 3 plus its tooltip).

        The number rides along for column 4's triage use, per D8, and is a
        separate key from the grade so a consumer picking one cannot get the
        other by accident.
        """
        return {
            "prescreen_grade": self.named.grade,
            "prescreen_score": self.internal.value,
            "confidence": self.named.confidence_label,
            "confidence_score": self.internal.confidence,
            "coverage": self.internal.coverage,
            "assessed_requirements": self.internal.assessed_requirements,
            "total_requirements": self.internal.total_requirements,
            "best_evidence_tier": self.internal.best_tier,
            "candidate_state": self.named.primary_state,
            "requirement_source": self.named.requirement_source,
        }


def effective_strength(claims: Sequence[Claim], clock: str) -> float:
    """Section 6.5's combined strength for one requirement.

    Section 6.5 combines with diminishing returns ACROSS INDEPENDENT GROUPS:
    S(c) = 1 - product over groups of (1 - max strength in the group). At the
    resume stage there is exactly ONE group, because section 5.4 says a resume
    and a cover letter are the same authorship in the same preparation session,
    and everything here came out of one document. So the product collapses to
    the strongest single claim, and the corroboration multiplier is the
    independence-count-of-1 entry, which is 1.00.

    That collapse is not a simplification, it is the honest answer. Restating
    the same claim three times on one resume is one person saying one thing
    three times, and a design that added them up would manufacture corroboration
    out of repetition, which is exactly what an AI-written resume is good at.
    """
    if not claims:
        return 0.0
    corroboration = float(
        _tiers()["effective_strength"]["corroboration_multiplier"]["by_independence_count"][1]
    )
    strongest = max(claim.strength(clock) for claim in claims)
    combined = 1.0 - (1.0 - strongest)
    return min(1.0, combined * corroboration)


def _confidence(
    *,
    coverage: float,
    depth: float,
    independence: float,
    consistency: float,
) -> float:
    """Section 10.7's four-term confidence, with its own coefficients.

    ONE READING IS THIS MODULE'S AND IS STATED HERE. Section 10.7 defines the
    coverage term as must-haves carrying evidence ABOVE E1, which at a stage
    where E2 is the ceiling would make almost every candidate Insufficient and
    would make the term measure "did this person link a repository" rather than
    "how much of the role does this resume speak to". The resume-stage reading
    is coverage of requirements with ANY claim. The other three terms are used
    as written. The consequence is reported and never hidden: the confidence a
    pre-screen produces is a confidence in a resume, and the four terms are the
    Runbook's own.

    Confidence NEVER moves the score. That separation is section 6.6's central
    fairness rule and it is why the two are computed here in different
    functions with no shared state.
    """
    terms = _band_data()["confidence"]["terms"]
    return round(
        float(terms["evidence_coverage"]["coefficient"]) * coverage
        + float(terms["evidence_depth"]["coefficient"]) * depth
        + float(terms["independence"]["coefficient"]) * independence
        + float(terms["consistency"]["coefficient"]) * consistency,
        4,
    )


def confidence_label(score: float) -> str:
    """Section 10.7's four confidence labels, at its own thresholds."""
    conf = _band_data()["confidence"]
    if score >= float(conf["high_threshold"]):
        return "High"
    if score >= float(conf["moderate_threshold"]):
        return "Moderate"
    if score >= float(conf["low_threshold"]):
        return "Low"
    return "Insufficient"


def _grade_for(score: float) -> str:
    """The A / B / C boundaries, and where they come from.

    THE TWO CUT POINTS ARE SECTION 6.1'S OWN TIER STRENGTHS, times 100. That is
    the only place in the Runbook where a resume-stage quantity has named
    levels: section 10.8's RPS bands describe a completed evaluation and are
    unreachable from a document nobody has verified, and section 6.7's
    sufficiency levels are defined in terms of E4 evidence, which a resume
    cannot contain. Reading the grade off the tier ladder instead gives each
    letter a meaning somebody can state:

        A  the claims behind this grade stand at ARTEFACT strength (E2, 0.40)
        B  they stand at CHECKABLE strength (E1, 0.25)
        C  they are ASSERTED and nothing checkable sits behind them

    An A therefore says something specific and hard: this candidate did not just
    describe the work, they attached something. Section 8.8 is the reason that
    is a ceiling and not a penalty. Absence of a repository is never negative
    evidence here, it simply adds none, and a candidate whose employer forbids
    public code tops out at B with nothing subtracted and a live probe route
    open in front of them.

    C IS A RESIDUAL AND HAS NO LOWER CUT POINT, which is deliberate. `Hold` is
    reserved for the one case that is genuinely not a grade, decided by the
    caller: there was nothing to grade. If `Hold` also meant "graded and weak",
    a recruiter scanning column 3 could not tell a resume nobody could read from
    a resume that read poorly, and only one of those is the candidate's.
    """
    if score >= tier_strength(TIER_ARTEFACT) * 100:
        return GRADE_A
    if score >= tier_strength(TIER_SPECIFIC) * 100:
        return GRADE_B
    return GRADE_C


def requirement_terms(
    *,
    competencies: Sequence[str] = (),
    jd_skills: Sequence[str] = (),
    job_title: str | None = None,
) -> tuple[tuple[str, ...], str]:
    """The requirement set, and an honest record of where it came from.

    Pre-screen grading runs at resume upload, which is before any scorecard can
    be frozen, so gate G1 is NOT called here and is not the right question:
    grading a resume is not evaluating a candidate against a scorecard. When an
    approved competency list exists it is used, because it is what a human
    actually agreed the job needs; otherwise the job description is, and the
    result says so. Degrading silently between the two would make two grades on
    one dashboard incomparable with nothing on the row to show it.
    """
    named = tuple(dict.fromkeys(str(c).strip() for c in competencies if str(c).strip()))
    if named:
        return named, REQUIREMENTS_FROM_COMPETENCIES
    from_jd = [str(s).strip() for s in jd_skills if str(s).strip()]
    if job_title and str(job_title).strip():
        from_jd.append(str(job_title).strip())
    return tuple(dict.fromkeys(from_jd)), REQUIREMENTS_FROM_JD


def grade(data: PreScreenInput) -> PreScreenResult:
    """The pre-screen grade for one resume against one job. No model call.

    The shape of the calculation, in one place:

      1. Every requirement is matched against every claim THROUGH THE ONTOLOGY
         (section 58), so a candidate who wrote "semantic technologies" against
         a requirement for "graph database" is matched, not missed.
      2. A requirement with at least one matching claim is ASSESSED and gets
         section 6.5's effective strength.
      3. A requirement with no matching claim is UNKNOWN (section 6.6). It is
         excluded from the mean, the remaining weights renormalise by virtue of
         being a mean over the assessed set, and it costs CONFIDENCE. It is
         never scored zero.
      4. The score is 100 times the mean assessed strength. The grade is that
         score against section 6.1's tier ladder.
      5. Nothing anywhere reads a name, an institution, an employer brand, an
         employment gap, or the length of a career.
    """
    requirements = tuple(data.requirements)
    ledger: list[dict[str, Any]] = []

    if not data.resume_parsed or not data.claims or not requirements:
        # Nothing to grade is not a bad grade. Section 10.8's HOLD row is "not
        # ranked pending human disposition", and that is exactly this case: a
        # scanned image, an empty file, or a job with no stated requirements.
        note = (
            "The resume produced no readable claims, so it was not graded and is "
            "waiting on a person."
            if not data.resume_parsed or not data.claims
            else "This job states no requirements yet, so there is nothing to grade "
            "a resume against."
        )
        return PreScreenResult(
            named=PreScreenGrade(
                grade=GRADE_HOLD,
                confidence_label=confidence_label(0.0),
                requirement_source=data.requirement_source,
                primary_state=data.primary_state,
                evidence_note=note,
            ),
            internal=PreScreenScore(
                value=0.0,
                confidence=0.0,
                coverage=0.0,
                assessed_requirements=0,
                total_requirements=len(requirements),
                best_tier=None,
            ),
            ledger=(),
        )

    strengths: list[float] = []
    tiers_hit: list[str] = []
    for requirement in requirements:
        # `ontology.matches` and not a set intersection. A requirement is a
        # PHRASE ("graph database", "stakeholder management") and a set of
        # single words cannot contain one, so the intersection form misses the
        # multi-word case which is most of the interesting ones, and misses it
        # silently.
        matched = [
            claim for claim in data.claims if ontology.matches(requirement, claim.text)
        ]
        if not matched:
            ledger.append(
                {
                    "requirement": requirement,
                    "status": "UNKNOWN",
                    "tier": None,
                    "strength": None,
                    # Said in full because the sentence is the fairness rule.
                    "note": (
                        "The resume does not speak to this requirement. It is "
                        "excluded from the score and reduces confidence, per "
                        "RPN-PHIL-001 section 6.6, and is not read as evidence "
                        "against the candidate."
                    ),
                }
            )
            continue
        strength = effective_strength(matched, data.clock)
        best = max(matched, key=lambda c: c.strength(data.clock))
        strengths.append(strength)
        tiers_hit.append(best.tier)
        ledger.append(
            {
                "requirement": requirement,
                "status": "ASSESSED",
                "tier": best.tier,
                "strength": round(strength, 4),
                "claims": len(matched),
                "origin": best.origin,
            }
        )

    total = len(requirements)
    assessed = len(strengths)
    coverage = assessed / total if total else 0.0

    if not strengths:
        return PreScreenResult(
            named=PreScreenGrade(
                grade=GRADE_HOLD,
                confidence_label=confidence_label(0.0),
                requirement_source=data.requirement_source,
                primary_state=data.primary_state,
                evidence_note=(
                    "The resume does not speak to any of this job's stated "
                    "requirements, so it is waiting on a person rather than "
                    "carrying a grade."
                ),
            ),
            internal=PreScreenScore(
                value=0.0,
                confidence=_confidence(
                    coverage=0.0, depth=0.0, independence=0.0, consistency=1.0
                ),
                coverage=0.0,
                assessed_requirements=0,
                total_requirements=total,
                best_tier=None,
            ),
            ledger=tuple(ledger),
        )

    value = round(100.0 * (sum(strengths) / assessed), 1)

    # Section 10.7's depth term is the mean best tier strength over the assessed
    # requirements; its independence term is min(1, mean group count / 3), and
    # at the resume stage the group count is 1 for every claim, for section
    # 5.4's reason. Consistency is 1.0 because a resume-stage pass resolves no
    # contradictions: it neither finds them nor clears them, and scoring an
    # unrun check as a failure would penalise every candidate for work this
    # stage does not do.
    depth = sum(tier_strength(t) for t in tiers_hit) / len(tiers_hit)
    independence = min(1.0, 1.0 / 3.0)
    confidence = _confidence(
        coverage=coverage, depth=depth, independence=independence, consistency=1.0
    )

    best_tier = max(tiers_hit, key=RESUME_STAGE_TIERS.index)
    named_grade = _grade_for(value)
    return PreScreenResult(
        named=PreScreenGrade(
            grade=named_grade,
            confidence_label=confidence_label(confidence),
            requirement_source=data.requirement_source,
            primary_state=data.primary_state,
            evidence_note=_evidence_note(named_grade, best_tier, assessed, total),
        ),
        internal=PreScreenScore(
            value=value,
            confidence=confidence,
            coverage=round(coverage, 4),
            assessed_requirements=assessed,
            total_requirements=total,
            best_tier=best_tier,
        ),
        ledger=tuple(ledger),
    )


_TIER_PROSE = {
    TIER_ARTEFACT: "an artefact the candidate supplied",
    TIER_SPECIFIC: "self-description carrying checkable specifics",
    TIER_ASSERTED: "self-description with nothing checkable behind it",
}


def _evidence_note(named_grade: str, best_tier: str, assessed: int, total: int) -> str:
    """One sentence, plain language, no numeric score. Column 3's tooltip.

    Says what the grade RESTS ON rather than what it is, because the failure
    this column exists to prevent is an early signal being read as a verdict,
    and "this rests on self-description" does that work in a way a letter cannot.
    Counts of requirements are not scores and are permitted here; the score
    itself is not, and lives on `PreScreenScore`.
    """
    covered = (
        f"{assessed} of {total} stated requirements"
        if total != 1
        else f"{assessed} of the 1 stated requirement"
    )
    if named_grade == GRADE_HOLD:
        return (
            f"The resume speaks to {covered}, and what it says is too weak to "
            "grade on, so it is waiting on a person."
        )
    return (
        f"The resume speaks to {covered}, and the strongest evidence behind that "
        f"is {_TIER_PROSE[best_tier]}."
    )


# The adapter from this product's rows to the anonymised input above.
#
# It lives here rather than in `matching.py` so there is one place that decides
# what the pre-screen may see, and one place a reviewer has to read to check
# that a name never crosses.


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%m/%Y", "%Y-%m", "%b %Y", "%B %Y", "%Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def _organisations_from(parsed: Mapping[str, Any] | Any) -> tuple[str, ...]:
    """Every employer and institution name the parser found, for the scrub.

    READ HERE AND USED ONLY TO DELETE TEXT, exactly like `_identities`. Neither
    name reaches `PreScreenInput`, which has no field either could sit in: the
    structured fields are dropped by not being read (`_spans_from_history` never
    looks at `company`), and this is what removes the same names from the PROSE,
    which is where a candidate writes "Rebuilt the ingestion pipeline at
    <employer>" and carries the pedigree into a claim.
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


def _spans_from_history(history: Any) -> tuple[EmploymentSpan, ...]:
    """Employment history as dates and titles. The company name is DROPPED."""
    if not isinstance(history, (list, tuple)):
        return ()
    spans: list[EmploymentSpan] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        spans.append(
            EmploymentSpan(
                title=str(entry.get("title") or "").strip() or None,
                start=_as_date(entry.get("start")),
                end=_as_date(entry.get("end")),
            )
        )
    return tuple(spans)


_ACADEMIC = re.compile(
    r"\b(?:intern(?:ship)?|academic|thesis|dissertation|capstone|coursework|"
    r"final[- ]year|college\s+project|university\s+project|hackathon)\b",
    re.IGNORECASE,
)


def input_from_profile(
    profile: Any,
    *,
    requirements: Sequence[str],
    requirement_source: str,
    clock: str,
    identities: Iterable[str] = (),
    job_terms: Iterable[str] = (),
    today: date | None = None,
) -> PreScreenInput:
    """Build the anonymised input from a `Profile` row.

    The three fields that would carry pedigree are dropped here and nowhere
    else: `company` off every employment entry, `institution` off every
    education entry, and the candidate's own name out of the resume text. There
    is no flag that re-admits them.

    DROPPING THE FIELD IS ONLY HALF OF IT, and the other half was open until
    2026-09-01. `_spans_from_history` never reads `company`, so no structured
    employer could reach a claim -- but the resume PROSE names employers too,
    and "Rebuilt the ingestion pipeline at Goldman Sachs" put "goldman" and
    "sachs" into `Claim.terms`, which is the set the ontology compares. So the
    names collected here are also passed to `anonymise`, bounded on word
    boundaries and against this job's own requirements. See `anonymise` for why
    both bounds are needed.
    """
    parsed = profile.parsed_fields_json if isinstance(profile.parsed_fields_json, dict) else {}
    skills = [s for s in (parsed.get("skills") or []) if isinstance(s, str)]
    history = parsed.get("employment_history")
    spans = _spans_from_history(history)

    now = today or datetime.now(timezone.utc).date()
    role_lines: list[tuple[str, float | None]] = []
    if isinstance(history, (list, tuple)):
        for entry in history:
            if not isinstance(entry, dict):
                continue
            end = _as_date(entry.get("end"))
            age = None if end is None else max(0.0, (now - end).days / 365.25)
            for key in ("summary", "description", "responsibilities", "achievements", "title"):
                body = entry.get(key)
                if isinstance(body, str) and body.strip():
                    role_lines.append((body.strip(), age))
                elif isinstance(body, (list, tuple)):
                    for item in body:
                        if isinstance(item, str) and item.strip():
                            role_lines.append((item.strip(), age))

    resume_text = getattr(profile, "resume_text", None)
    claims = claims_from_resume(
        resume_text,
        skills=skills,
        identities=identities,
        role_lines=role_lines,
        organisations=_organisations_from(parsed),
        # THE REQUIREMENTS ARE WHAT MAKES THE SCRUB SAFE. A word the job is
        # asking about is never removed, so an employer called Oracle or Docker
        # cannot cost the candidate the technology of the same name. See
        # `anonymise`.
        protected_terms=tuple(requirements),
    )
    has_academic = any(_ACADEMIC.search(claim.text) for claim in claims)
    years = parsed.get("total_experience_years")
    try:
        years_value = None if years is None else float(years)
    except (TypeError, ValueError):
        years_value = None

    states, primary = candidate_states(
        total_experience_years=years_value,
        spans=spans,
        job_terms=frozenset(_terms(" ".join(str(t) for t in job_terms))),
        has_academic_claims=has_academic,
        today=now,
    )
    return PreScreenInput(
        requirements=tuple(requirements),
        requirement_source=requirement_source,
        claims=claims,
        clock=clock,
        states=states,
        primary_state=primary,
        gap_months=employment_gaps(spans),
        resume_parsed=bool(claims),
    )


def field_names(cls: type) -> tuple[str, ...]:
    """The declared field names of a dataclass, for the isolation tests."""
    return tuple(f.name for f in fields(cls))


# Persistence.
#
# The three columns are written by raw SQL and are deliberately NOT on the
# SQLAlchemy model, exactly as `match_breakdown_json` and `jobs.embedding` are.
# That keeps the write path in one file that one agent owns and keeps the model
# module free of a column six other people are editing around.

_UPSERT = (
    "UPDATE job_candidate_links "
    "SET prescreen_grade = :grade, "
    "    prescreen_score = :score, "
    "    prescreen_json = CAST(:payload AS jsonb) "
    "WHERE id = :link_id"
)


async def store(session: Any, link_id: uuid.UUID | str, result: PreScreenResult) -> None:
    """Write one link's pre-screen grade, score and ledger.

    Grade and score go to SEPARATE COLUMNS, which is D8 in the schema: a report
    serialiser reading `prescreen_grade` cannot pick up a number by accident,
    and a query that wants the number has to ask for it by name.
    """
    import json  # noqa: PLC0415 - json is only needed on the write path

    from sqlalchemy import text as sql_text  # noqa: PLC0415

    payload = {
        "grade": result.named.grade,
        "confidence": result.named.confidence_label,
        "confidence_score": result.internal.confidence,
        "coverage": result.internal.coverage,
        "assessed_requirements": result.internal.assessed_requirements,
        "total_requirements": result.internal.total_requirements,
        "best_evidence_tier": result.internal.best_tier,
        "candidate_state": result.named.primary_state,
        "requirement_source": result.named.requirement_source,
        "evidence_note": result.named.evidence_note,
        "ledger": list(result.ledger),
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }
    await session.execute(
        sql_text(_UPSERT),
        {
            "grade": result.named.grade,
            "score": result.internal.value,
            "payload": json.dumps(payload, default=str),
            "link_id": str(link_id),
        },
    )


# The live path.
#
# `parse_resume` calls `grade_profile` the moment a resume is readable, which is
# the earliest point at which a grade can honestly exist, and is what the
# Dashboard Specification's column 3 means by "populated immediately when a
# candidate's resume is ingested".


async def _requirements_for_job(session: Any, job: Any) -> tuple[tuple[str, ...], str]:
    """This job's requirement set: the approved competency list, or the JD.

    Gate G1 is NOT consulted and must not be. G1 asks whether a candidate may be
    EVALUATED against a frozen scorecard; a pre-screen grade is a reading of a
    document against whatever the job has said about itself so far, and a job
    that has not been through Sutra yet still receives applicants who still need
    triaging. What the grade owes the reader is not a refusal, it is an honest
    record of which of the two it used, and `requirement_source` is that record.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.assessment import JobCompetency  # noqa: PLC0415

    names: list[str] = []
    if getattr(job, "framework_approved_at", None) is not None:
        rows = (
            await session.execute(
                select(JobCompetency.name)
                .where(
                    JobCompetency.job_id == job.id,
                    JobCompetency.is_active.is_(True),
                )
                .order_by(JobCompetency.category, JobCompetency.ordinal)
            )
        ).all()
        names = [str(r[0]) for r in rows if r[0]]
    jd = job.jd_json if isinstance(job.jd_json, dict) else {}
    skills = [s for s in (jd.get("skills") or []) if isinstance(s, str)]
    return requirement_terms(
        competencies=names, jd_skills=skills, job_title=getattr(job, "title", None)
    )


async def grade_link(session: Any, job: Any, profile: Any, link: Any) -> PreScreenResult:
    """Grade one candidate against one job, and store the result."""
    requirements, source = await _requirements_for_job(session, job)
    clock = domain_clock(
        getattr(job, "department", None),
        getattr(job, "title", None),
        " ".join(str(s) for s in ((job.jd_json or {}).get("skills") or []))
        if isinstance(getattr(job, "jd_json", None), dict)
        else None,
    )
    result = grade(
        input_from_profile(
            profile,
            requirements=requirements,
            requirement_source=source,
            clock=clock,
            identities=await _identities(session, profile),
            job_terms=(getattr(job, "title", None) or "", getattr(job, "department", None) or ""),
        )
    )
    await store(session, link.id, result)
    logger.info(
        "prescreen.graded job_id=%s profile_id=%s grade=%s confidence=%s "
        "requirement_source=%s state=%s",
        getattr(job, "id", None),
        getattr(profile, "id", None),
        result.named.grade,
        result.named.confidence_label,
        result.named.requirement_source,
        result.named.primary_state,
    )
    return result


async def _identities(session: Any, profile: Any) -> tuple[str, ...]:
    """The name tokens `anonymise` must strip out of this resume.

    Read here and used only to DELETE text. The name never reaches
    `PreScreenInput`, which has no field it could sit in.
    """
    from app.models.candidate import Candidate  # noqa: PLC0415

    candidate = await session.get(Candidate, profile.candidate_id)
    if candidate is None:
        return ()
    full = str(getattr(candidate, "full_name", None) or "").strip()
    if not full:
        return ()
    parts = [p for p in re.split(r"\s+", full) if len(p) > 2]
    # Longest first, so "Priya Raghunathan" is removed before "Priya" is, and
    # the shorter pattern is not left matching the tail of the longer one.
    return tuple(sorted({full, *parts}, key=len, reverse=True))


async def grade_profile(session: Any, profile: Any) -> int:
    """Grade this profile against every job it is linked to. Returns the count.

    THIS IS THE LIVE ENTRY POINT. It is called from `resume_parsing.parse_resume`
    the moment a resume has been read, so every route that accepts a resume
    reaches it: a candidate applying, a recruiter bulk-uploading a databank, a
    candidate replacing their main resume, and the provider-side upload. None of
    those routes had to learn about grading, which is the point of putting it on
    the parse rather than on each of them.

    It raises nothing it can help raising and swallows nothing it should not:
    a failure to grade is logged with the link it happened on and the remaining
    links are still graded, exactly as a databank bulk upload allows partial
    success so one unreadable file cannot discard the other twenty-four.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import Job, JobCandidateLink  # noqa: PLC0415

    links = (
        (
            await session.execute(
                select(JobCandidateLink).where(
                    JobCandidateLink.candidate_id == profile.candidate_id
                )
            )
        )
        .scalars()
        .all()
    )
    graded = 0
    for link in links:
        if link.profile_id is not None and link.profile_id != profile.id:
            # This application was submitted with a different resume, and an
            # application is an immutable snapshot of the document it was
            # actually sent with. Re-grading it against a newer upload would
            # rewrite a record of what a recruiter saw.
            continue
        job = await session.get(Job, link.job_id)
        if job is None:
            continue
        try:
            await grade_link(session, job, profile, link)
        except PreScreenUnavailable:
            # The Runbook data is missing, which is a deployment fault and not a
            # property of this candidate. Raised out: every grade in the run
            # would be equally wrong, and a partial sweep that silently skipped
            # them all would look like a clean run.
            raise
        except Exception:  # noqa: BLE001 - one bad row must not cost the rest
            logger.warning(
                "prescreen.grade_failed job_id=%s profile_id=%s link_id=%s",
                link.job_id,
                profile.id,
                link.id,
                exc_info=True,
            )
            continue
        graded += 1
    return graded
