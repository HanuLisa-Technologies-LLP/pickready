"""Does the cited evidence SUPPORT the statement, not merely exist?

THE GAP THIS CLOSES (PLAN-p5 P5-D9)
-------------------------------------
`citations` guarantees a delivered statement carries a ref and that the ref is
in the evaluation's evidence set. That is an EXISTENCE check: a remark saying
"led the Kafka migration" citing an answer about a spreadsheet passes it, and a
reader clicking through to the citation finds something that does not say what
the sentence says. A citation that exists and does not support is a fabricated
citation with better manners, and it reads as provenance.

TWO TIERS, DETERMINISTIC FIRST
--------------------------------
1. DETERMINISTIC. The statement is checked against the text of the nodes it
   cites (the answers, the question they answered, any retrieved passage):
     * a proper noun it names that appears nowhere in that text is an
       INVENTED TERM, and the verdict is `unsupported`. Same rule, same
       function (`remarks.invented_terms`) the writer is held to, so the writer
       and the checker cannot disagree about what "invented" means;
     * a shared CONTENT TERM (five letters or more, not the skill's own name,
       not the report's own vocabulary) is an anchor, and the verdict is
       `supported`.
2. SEMANTIC, ONLY WHEN THE ANCHOR FAILS. A faithful paraphrase shares no word
   with what it paraphrases, so the statement and the cited texts are embedded
   (voyage-4) and the best cosine is compared with
   `settings.siddhi_support_similarity_min`. At or above: `supported`. Below:
   `unsupported`.

Deterministic first because it is free, reproducible and works during a
provider outage; semantic second because it is the only thing that can tell a
paraphrase from an unrelated sentence.

A DEGRADATION IS RECORDED, NEVER SILENT
------------------------------------------
With no real embedding model configured (`embeddings.is_semantic()` is False,
where `embed` would return pseudo-random vectors) or with the endpoint failing,
the semantic tier did not run. The verdict is then `weak` with
`reason="semantic_check_unavailable"`: never `supported`, because "we could not
check" is not "it checked out", and never `unsupported`, because an outage is
not evidence against the candidate. A statement citing only the record that an
area was SEARCHED is `weak` by definition: there is no text to support it with,
and saying so is the honest verdict.

WHAT THE VERDICT DOES
-----------------------
`unsupported` routes the report to human review and puts a words-only marker
beside the statement (`SUPPORT_NOTES`). `weak` is recorded in the trail and
changes nothing else. Nothing here moves a grade: the check reads prose that
was written FROM a grade, so the most it can conclude is that the prose should
not be relied on.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Hashable, Mapping, Sequence

from app.services.siddhi import remarks

logger = logging.getLogger(__name__)

__all__ = [
    "LEVEL_SUPPORTED",
    "LEVEL_WEAK",
    "LEVEL_UNSUPPORTED",
    "LEVELS",
    "REASON_ANCHORED",
    "REASON_INVENTED",
    "REASON_SEMANTIC_MATCH",
    "REASON_SEMANTIC_BELOW",
    "REASON_SEMANTIC_UNAVAILABLE",
    "REASON_SEARCHED_ONLY",
    "SUPPORT_NOTES",
    "Embedder",
    "SupportVerdict",
    "SupportRequest",
    "deterministic",
    "assess",
    "assess_many",
    "semantic_embedder",
]

LEVEL_SUPPORTED = "supported"
LEVEL_WEAK = "weak"
LEVEL_UNSUPPORTED = "unsupported"
LEVELS: tuple[str, ...] = (LEVEL_SUPPORTED, LEVEL_WEAK, LEVEL_UNSUPPORTED)

REASON_ANCHORED = "content_term_anchor"
REASON_INVENTED = "invented_term"
REASON_SEMANTIC_MATCH = "semantic_match"
REASON_SEMANTIC_BELOW = "semantic_below_threshold"
REASON_SEMANTIC_UNAVAILABLE = "semantic_check_unavailable"
REASON_SEARCHED_ONLY = "searched_only"

#: What a reader is told beside a statement, by verdict. WORDS ONLY and no em
#: dash: this travels to the report screen and the PDF, under the number ban.
#: `supported` says nothing, because a marker on every sound sentence is noise
#: that teaches a reader to skip the one that matters.
SUPPORT_NOTES: Mapping[str, str | None] = {
    LEVEL_SUPPORTED: None,
    LEVEL_WEAK: (
        "The cited record for this statement is thin; read the candidate's "
        "answers before relying on it."
    ),
    LEVEL_UNSUPPORTED: (
        "The cited answers do not support this statement; a person should "
        "read the transcript before relying on it."
    ),
}

#: `embed(texts) -> vectors`, order preserved. The production value is
#: `embeddings.embed`; a test passes a stub.
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]

#: Report vocabulary that appears in a remark whatever the candidate said. A
#: shared word from this list is not an anchor: "the candidate described an
#: outcome" shares "outcome" with almost any answer and proves nothing.
_REPORT_VOCABULARY: frozenset[str] = frozenset(
    {
        "about", "account", "action", "actions", "across", "against",
        "answer", "answered", "answers", "approach", "assessed", "assessment",
        "because", "before", "being", "candidate", "candidates", "capability",
        "clear", "clearly", "confirm", "confirmed", "conversation", "could",
        "decision", "decisions", "demonstrate", "demonstrated",
        "demonstrates", "depth", "describe", "described", "describes",
        "detail", "detailed", "details", "directly", "during", "evidence",
        "example", "examples", "experience", "explain", "explained",
        "further", "given", "having", "interview", "interviewer",
        "interviewers", "matching", "moderately", "other", "outcome",
        "outcomes", "personal", "personally", "practical", "probe", "probes",
        "question", "questions", "relevant", "result", "results", "shows",
        "situation", "situations", "should", "specific", "stated", "strong",
        "their", "there", "these", "those", "through", "verification",
        "verify", "where", "which", "while", "within", "without", "would",
    }
)

_TERM = re.compile(r"[a-z][a-z0-9+#]*")


def _terms(text: str) -> set[str]:
    return {token for token in _TERM.findall((text or "").casefold()) if len(token) >= 5}


def _stem(token: str) -> str:
    return token[:-1] if token.endswith("s") else token


@dataclass(frozen=True)
class SupportVerdict:
    """One statement's verdict: a level and the reason code that produced it.

    No number. The similarity that decided a semantic verdict is compared in
    memory and never stored: the trail is persisted with an immutable report
    and read far more widely than the report, and a float in it is a number
    outside the one conversion point `services/rating` exists to be.
    """

    level: str
    reason: str

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"Unknown support level {self.level!r}")

    @property
    def note(self) -> str | None:
        return SUPPORT_NOTES[self.level]

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "reason": self.reason}


@dataclass(frozen=True)
class SupportRequest:
    """One statement to check, against the texts of the nodes it cites."""

    key: Hashable
    statement: str
    excerpts: tuple[str, ...]
    #: The names the statement is ABOUT (skill names). Naming the skill being
    #: assessed is never an anchor and never an invention.
    subject_terms: tuple[str, ...] = ()


def deterministic(
    statement: str, excerpts: Sequence[str], *, subject_terms: Sequence[str] = ()
) -> SupportVerdict | None:
    """The deterministic tier. None means "undecided: ask the semantic tier"."""
    texts = [text for text in excerpts if str(text or "").strip()]
    if not texts:
        return SupportVerdict(LEVEL_WEAK, REASON_SEARCHED_ONLY)
    haystack = " ".join(texts)
    subject = " ".join(subject_terms)
    if remarks.invented_terms(statement, evidence=haystack, name=subject):
        return SupportVerdict(LEVEL_UNSUPPORTED, REASON_INVENTED)
    excluded = _REPORT_VOCABULARY | _terms(subject)
    wanted = {_stem(term) for term in _terms(statement) if term not in excluded}
    offered = {_stem(term) for term in _terms(haystack)}
    if wanted & offered:
        return SupportVerdict(LEVEL_SUPPORTED, REASON_ANCHORED)
    return None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _threshold(value: float | None) -> float:
    if value is not None:
        return float(value)
    from app.core.config import get_settings

    return float(get_settings().siddhi_support_similarity_min)


async def assess_many(
    requests: Sequence[SupportRequest],
    *,
    embed: Embedder | None,
    threshold: float | None = None,
) -> dict[Hashable, SupportVerdict]:
    """Every request's verdict, with ONE embedding call for all the undecided.

    `embed=None` means no semantic tier is available (see
    `semantic_embedder`), and every undecided statement is `weak` with the
    reason saying so. An embedding failure is the same state, logged with the
    exception's class name and never its message (a message can quote the
    text that was being embedded).
    """
    from app.services.embeddings import EmbeddingError, EmbeddingUnavailable
    from app.services.reliability.vendor_contract import VendorContractViolation

    verdicts: dict[Hashable, SupportVerdict] = {}
    undecided: list[SupportRequest] = []
    for request in requests:
        verdict = deterministic(
            request.statement, request.excerpts, subject_terms=request.subject_terms
        )
        if verdict is None:
            undecided.append(request)
        else:
            verdicts[request.key] = verdict
    if not undecided:
        return verdicts

    unavailable = SupportVerdict(LEVEL_WEAK, REASON_SEMANTIC_UNAVAILABLE)
    if embed is None:
        for request in undecided:
            verdicts[request.key] = unavailable
        return verdicts

    texts: list[str] = []
    slots: dict[str, int] = {}

    def _slot(text: str) -> int:
        if text not in slots:
            slots[text] = len(texts)
            texts.append(text)
        return slots[text]

    plan = [
        (
            request,
            _slot(request.statement),
            [_slot(text) for text in request.excerpts if str(text or "").strip()],
        )
        for request in undecided
    ]
    try:
        vectors = await embed(texts)
    except (EmbeddingError, EmbeddingUnavailable, VendorContractViolation) as exc:
        logger.warning(
            "siddhi.support.semantic_unavailable statements=%d error=%s",
            len(undecided), type(exc).__name__,
        )
        for request in undecided:
            verdicts[request.key] = unavailable
        return verdicts
    if len(vectors) != len(texts):
        logger.warning(
            "siddhi.support.semantic_unavailable statements=%d error=%s",
            len(undecided), "EmbeddingCountMismatch",
        )
        for request in undecided:
            verdicts[request.key] = unavailable
        return verdicts

    floor = _threshold(threshold)
    for request, statement_slot, excerpt_slots in plan:
        best = max(
            (_cosine(vectors[statement_slot], vectors[slot]) for slot in excerpt_slots),
            default=0.0,
        )
        verdicts[request.key] = (
            SupportVerdict(LEVEL_SUPPORTED, REASON_SEMANTIC_MATCH)
            if best >= floor
            else SupportVerdict(LEVEL_UNSUPPORTED, REASON_SEMANTIC_BELOW)
        )
    return verdicts


async def assess(
    statement: str,
    excerpts: Sequence[str],
    *,
    subject_terms: Sequence[str] = (),
    embed: Embedder | None,
    threshold: float | None = None,
) -> SupportVerdict:
    """One statement. `assess_many` is what a report uses; this is the same rule."""
    verdicts = await assess_many(
        [
            SupportRequest(
                key=0,
                statement=statement,
                excerpts=tuple(excerpts),
                subject_terms=tuple(subject_terms),
            )
        ],
        embed=embed,
        threshold=threshold,
    )
    return verdicts[0]


def semantic_embedder() -> Embedder | None:
    """The production embedder, or None when no real model is configured.

    None rather than `embeddings.embed` in the unconfigured state, because
    that function returns deterministic pseudo-random vectors outside
    production, and a support verdict decided by a random cosine would be a
    judgement that looks exactly like a real one.
    """
    from app.services import embeddings

    if not embeddings.is_semantic():
        return None
    return embeddings.embed
