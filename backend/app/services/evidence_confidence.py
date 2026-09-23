"""Evidence Confidence: how well corroborated one rated line actually is.

WHAT THIS ANSWERS, AND WHAT IT MUST NEVER ANSWER
--------------------------------------------------
Beside every grade on a PRISM Report there is now a second word: how strong the
evidence base under that grade is. It answers "how sure are we of the record",
never "how good is the candidate". Those are different questions and the whole
value of printing the second one is that it cannot move the first.

CONFIDENCE NEVER MOVES A GRADE, AND THE ENFORCEMENT IS THAT NOTHING HERE CAN
-----------------------------------------------------------------------------
This module imports no scorer, computes no score, and returns no number. It is
called AFTER every item has been graded, with the grade already decided, and
its output is written to its own columns. `tests/test_evidence_confidence.py`
sweeps a range of inputs and asserts the grade is byte-identical whatever this
function returns, which is the same technique the proctoring isolation tests
use for the same reason: a rule stated in a docstring is a rule the next caller
does not see.

It is also the same rule `miti.aggregation.confidence_score` already follows one
layer up, and it is stated in the Runbook's own words (section 14.3):
confidence is reported as the strength of the evidence base, "never as a
probability of success -- we have not earned that claim".

DETERMINISTIC ARITHMETIC OVER ORIGINATORS, NEVER A MODEL'S SELF-ESTIMATE
-------------------------------------------------------------------------
An LLM asked how confident it is makes the criterion unfalsifiable and fails
exactly when the provider is already failing. So the derivation is three
branches over a set, and the set is keyed by ORIGINATOR rather than by document.

That distinction is the one that matters. A resume line and the candidate
restating it in the interview could not have disagreed: they are one person
saying one thing twice, and counting them as two is how a confidently written
resume becomes a well corroborated candidate. The grouping is
`miti.tiering.independence_group_for`'s, and `test_evidence_confidence.py`
asserts that every ledger source type in the registry below carries the group
that function assigns it.

THIS MODULE IS A LEAF, AND IT HAS TO BE
-----------------------------------------
It imports nothing from `app.services`. Not the ledger whose source keys it
names, not `miti.tiering` whose grouping it follows, and not the aggregator
whose four words it repeats. Every one of those reaches
`app.services.evidence`, whose package import walks through `verification` into
`functional_assessment` and back out through Siddhi, so an import here closes a
cycle and the failure is an AttributeError on a half built module rather than
anything a reader would recognise as an import problem.

So the three vocabularies are RESTATED as literals and
`tests/test_evidence_confidence.py` asserts each one against its owner, in both
directions. The copy costs an assertion; the import costs the report renderer a
dependency on the scoring pipeline, which is the thing this module must not
have for a second and more important reason: it is what makes "confidence
cannot move a grade" checkable by reading the import list.

THE THREE WORDS, AND WHY THE MIDDLE ONE IS NOT A ROUNDING
-----------------------------------------------------------
    High         two or more distinct originators corroborate it.
    Moderate     one originator, but the product ASKED and was answered: the
                 claim was put to the candidate under assessment conditions
                 rather than only asserted by them.
    Low          the candidate's own unprompted account and nothing else.
    Insufficient nothing in the record bears on this line at all.

Moderate is not "somewhere between the other two". It is a real, separable
state: "we asked about this and they answered" is a materially stronger record
than "it is on their resume and nothing ever tested it", and collapsing the two
into Low would hide the difference on every report the product writes today.

INSUFFICIENT IS REPORTED, NEVER ROUNDED AWAY
----------------------------------------------
A line with no evidence behind it reports Insufficient. It does not report Low,
which would read as a weak finding about a person, and it does not report High,
which would be a fabrication. Insufficient evidence is not negative evidence,
here as everywhere else in this codebase.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MODERATE",
    "CONFIDENCE_LOW",
    "CONFIDENCE_INSUFFICIENT",
    "CONFIDENCE_WORDS",
    "CONFIDENCE_DISPLAY",
    "SOURCE_BGV",
    "SOURCE_RESUME",
    "SOURCE_ANSWER",
    "SOURCE_VALIDATION",
    "SOURCE_MEMORY",
    "SOURCE_SWOT",
    "SOURCE_JD",
    "SOURCE_REGISTRY",
    "EvidenceSource",
    "DimensionConfidence",
    "confidence_word",
    "describe",
    "display_word",
    "source_labels",
]

# ── The vocabulary ───────────────────────────────────────────────────────────
#
# The same four words `miti.aggregation` already emits, restated as codes
# rather than imported so this module stays a leaf: `miti.aggregation` reaches
# the Runbook data loader at call time, and a report renderer should not have
# to. `test_evidence_confidence.py` asserts the two vocabularies are equal, so
# they cannot drift, which is the guarantee an import would have bought.

CONFIDENCE_HIGH = "high"
CONFIDENCE_MODERATE = "moderate"
CONFIDENCE_LOW = "low"
CONFIDENCE_INSUFFICIENT = "insufficient"

#: Best to worst. Nothing may be inserted into the middle of it.
CONFIDENCE_WORDS: tuple[str, ...] = (
    CONFIDENCE_HIGH,
    CONFIDENCE_MODERATE,
    CONFIDENCE_LOW,
    CONFIDENCE_INSUFFICIENT,
)

#: The code stored in the column, and the word a client reads. Two vocabularies
#: on purpose, and the same split the four grades already make: the code is
#: read by logic and by a CHECK constraint, the word is copy. "Insufficient
#: evidence" spells out what it means rather than printing a single adjective a
#: reader would take as a fourth rung on the same ladder.
CONFIDENCE_DISPLAY: dict[str, str] = {
    CONFIDENCE_HIGH: "High",
    CONFIDENCE_MODERATE: "Moderate",
    CONFIDENCE_LOW: "Low",
    CONFIDENCE_INSUFFICIENT: "Insufficient evidence",
}

# ── The sources ──────────────────────────────────────────────────────────────

#: EMPLOYER CONFIRMATION. Not a `ledger.SOURCE_TYPES` member, deliberately.
#:
#: A confirmation lives in `bgv_verifications`, which is a per-tenant decision a
#: person made about a `candidate_employments` row, and it is never written to
#: `evidence_items`. Adding it to the ledger's closed source list would mean
#: adding a CHECK value nothing writes, which is a vocabulary entry with no
#: producer. It is named HERE because this is where a source becomes a word a
#: client reads, and the confirmation genuinely is one.
SOURCE_BGV = "bgv"

#: The ledger's own six source types, and the two originator groups
#: `miti.tiering` assigns them, restated as literals. See the module docstring
#: for why they are not imported; `test_evidence_confidence.py` pins every one
#: of them against the module that owns it.
#: PUBLIC, because the report side needs one vocabulary for a source key and
#: this leaf is the only module every renderer can reach without dragging the
#: scoring pipeline in behind it.
SOURCE_RESUME = "resume"
SOURCE_ANSWER = "answer"
SOURCE_VALIDATION = "validation"
SOURCE_MEMORY = "memory"
SOURCE_SWOT = "swot"
SOURCE_JD = "jd"

_GROUP_CANDIDATE = "candidate"
_GROUP_EMPLOYER = "employer"


@dataclass(frozen=True)
class EvidenceSource:
    """One kind of thing that can stand behind a rated line.

    `evidences_candidate` is the field that stops a role description from
    corroborating a person. The JD and the Job SWOT Analysis are the hiring
    side's account of the ROLE: they contribute the bar, not the proof, and a
    derivation that counted them as originators would raise confidence every
    time a hiring manager wrote a requirement down.

    `elicited` separates "the product asked and was answered" from "the
    candidate volunteered it". Both are the candidate speaking, so neither adds
    an originator, but only the first was put to them under assessment
    conditions and that difference is what Moderate reports.
    """

    key: str
    #: The word a client reads. Never the key.
    label: str
    #: Who is speaking. `miti.tiering`'s group vocabulary, not a second one.
    originator: str
    evidences_candidate: bool
    elicited: bool = False


#: Every source a rated line may rest on, in the order a reader should meet
#: them: what the candidate asserted, what they were asked, what an outside
#: party confirmed, and last what the role itself required.
SOURCE_REGISTRY: tuple[EvidenceSource, ...] = (
    EvidenceSource(
        key=SOURCE_RESUME,
        label="Resume",
        originator=_GROUP_CANDIDATE,
        evidences_candidate=True,
    ),
    EvidenceSource(
        key=SOURCE_VALIDATION,
        label="Application details",
        originator=_GROUP_CANDIDATE,
        evidences_candidate=True,
    ),
    EvidenceSource(
        # Platform memory: the product's own earlier reading of the same
        # person. Grouped with the candidate because it is derived from things
        # already counted, so it can never form a second originator and can
        # never corroborate the account it was derived from.
        key=SOURCE_MEMORY,
        label="Earlier assessment on record",
        originator=_GROUP_CANDIDATE,
        evidences_candidate=True,
    ),
    EvidenceSource(
        key=SOURCE_ANSWER,
        label="Assessment responses",
        originator=_GROUP_CANDIDATE,
        evidences_candidate=True,
        elicited=True,
    ),
    EvidenceSource(
        key=SOURCE_BGV,
        # WHO SPOKE, not what they said. "Employer Confirmed" is the product's
        # display language for a CONFIRMED background check, and naming the
        # source with it would print the word "Confirmed" beside an employment
        # the employer had just declined to confirm. The outcome is a sentence
        # in the entry, where a reader can see it stated.
        label="Previous employer",
        originator=_GROUP_EMPLOYER,
        evidences_candidate=True,
    ),
    EvidenceSource(
        key=SOURCE_SWOT,
        label="Hiring manager defined requirement",
        originator=_GROUP_EMPLOYER,
        evidences_candidate=False,
    ),
    EvidenceSource(
        key=SOURCE_JD,
        label="Role description",
        originator=_GROUP_EMPLOYER,
        evidences_candidate=False,
    ),
)

_BY_KEY: dict[str, EvidenceSource] = {source.key: source for source in SOURCE_REGISTRY}


@dataclass(frozen=True)
class DimensionConfidence:
    """One rated line's confidence, and the sources it was derived from.

    Carries the source KEYS rather than the labels. The keys are what is stored
    on the immutable row; the labels are copy, and copy that was frozen into a
    permanent record could never be corrected.
    """

    confidence: str
    sources: tuple[str, ...] = ()

    @property
    def display(self) -> str | None:
        """The client-facing word, or None for a code nothing recognises.

        `str | None` rather than a default, for the reason `display_word`
        gives: a code that reached a reader unconverted is a leak of an
        internal vocabulary, and a substituted word would be a claim about an
        evidence base nobody assembled.
        """
        return display_word(self.confidence)

    @property
    def labels(self) -> tuple[str, ...]:
        return source_labels(self.sources)


def _known(kinds: Iterable[str]) -> list[EvidenceSource]:
    """The registry entries for these kinds, deduplicated, in registry order.

    An UNRECOGNISED kind is dropped rather than counted as a new originator.
    Counting it would manufacture the corroboration this whole grouping exists
    to prevent, which is the same argument `independence_group_for` makes for
    defaulting an unknown source to the candidate.
    """
    present = {str(kind) for kind in kinds}
    return [source for source in SOURCE_REGISTRY if source.key in present]


def confidence_word(kinds: Iterable[str]) -> str:
    """The confidence code for a line resting on these source kinds.

    Three branches over a set of originators, and nothing else. No score, no
    threshold, no model, and no arithmetic that could be tuned by feel.
    """
    contributing = [source for source in _known(kinds) if source.evidences_candidate]
    if not contributing:
        return CONFIDENCE_INSUFFICIENT
    originators = {source.originator for source in contributing}
    if len(originators) >= 2:
        return CONFIDENCE_HIGH
    if any(source.elicited for source in contributing):
        return CONFIDENCE_MODERATE
    return CONFIDENCE_LOW


def describe(kinds: Iterable[str]) -> DimensionConfidence:
    """The confidence and the named sources, from one pass over the kinds.

    One function rather than two so the word and the list a reader sees beside
    it are computed from the same set. Two entry points would let a report say
    High above a source list that cannot support it.
    """
    known = _known(kinds)
    return DimensionConfidence(
        confidence=confidence_word(source.key for source in known),
        sources=tuple(source.key for source in known),
    )


def display_word(confidence: str | None) -> str | None:
    """The client-facing word for a stored code.

    None in, None out: a row written before this release carries no confidence
    at all, and inventing one for it would be a claim about evidence nobody
    assembled. An UNRECOGNISED code is also None rather than echoed, because a
    code that reached a reader unconverted is a leak of an internal vocabulary.
    """
    if not confidence:
        return None
    return CONFIDENCE_DISPLAY.get(str(confidence))


def source_labels(keys: Sequence[str] | None) -> tuple[str, ...]:
    """The words for stored source keys, in registry order.

    Order comes from the registry rather than from the stored list so two
    reports never name the same sources in a different sequence, which is the
    kind of difference a reader comparing two candidates reads as meaning
    something.
    """
    if not keys:
        return ()
    present = {str(key) for key in keys}
    return tuple(
        source.label for source in SOURCE_REGISTRY if source.key in present
    )
