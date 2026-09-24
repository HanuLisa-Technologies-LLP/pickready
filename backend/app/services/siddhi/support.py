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

A THIRD LOOK, AND WHY IT CAN ONLY EVER LIFT `unsupported` TO `weak`
-------------------------------------------------------------------
A statement whose citation does not support it may still be TRUE: the model
wrote from the whole record and pinned the sentence to the wrong answer. So a
statement that ends `unsupported` is looked up once more, in the candidate's
OTHER answers, through `evidence_retrieval.support_passages_for_statement`
(the typed tool layer; Siddhi never reads the retrieval layer directly). The
seam is `PassageSource`, a callable the caller supplies
(`statement_passage_source` builds the production one), because the retrieval
needs the scoring session and this module holds none.

A passage that supports the statement (the same two tiers, applied per
passage) turns the verdict into `weak` with `REASON_ELSEWHERE`, and the
passage's durable locator is recorded on the verdict so a reader can open it.
NEVER `supported`: the statement's own citation still does not support it, and
"the candidate said this somewhere" is a weaker claim than "the candidate said
this here". Nothing else is ever looked up, and a lookup cannot make any
verdict worse. A retrieval that degraded, or a semantic tier that was
unavailable for the passages, leaves the verdict exactly as it was and records
WHY on it (`passage_check`), because "we did not look" and "we looked and found
nothing" are different facts.

WHAT THE VERDICT DOES
-----------------------
`unsupported` routes the report to human review and puts a words-only marker
beside the statement (`note_for`). A statement supported only elsewhere in the
transcript carries its own marker and does not route to review: the claim is
the candidate's, only its citation is wrong. Every other `weak` is recorded in
the trail, changes nothing else and has no marker: a marker on every statement
a provider outage left unchecked would teach a reader to skip the one that
matters. Nothing here moves a grade: the check reads prose that was written
FROM a grade, so the most it can conclude is that the prose should not be
relied on.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Hashable, Mapping, Protocol, Sequence

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
    "REASON_ELSEWHERE",
    "PASSAGE_FOUND",
    "PASSAGE_NOT_FOUND",
    "PASSAGE_UNAVAILABLE",
    "PASSAGE_NOT_ATTEMPTED",
    "PASSAGE_CHECKS",
    "PASSAGE_LOOKUP_LIMIT",
    "SUPPORT_NOTES",
    "note_for",
    "Embedder",
    "RetrievedPassage",
    "RetrievedPassages",
    "PassageSource",
    "SupportVerdict",
    "SupportRequest",
    "deterministic",
    "assess",
    "assess_many",
    "semantic_embedder",
    "statement_passage_source",
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
#: The citation does not support the statement; another of the candidate's
#: answers, found through the passage lookup, does. Always level `weak`.
REASON_ELSEWHERE = "supported_by_uncited_passage"

#: What the passage lookup did for one statement. Recorded on the verdict only
#: when a lookup was relevant: the statement ended `unsupported` on its own
#: citation and the caller supplied a passage source.
PASSAGE_FOUND = "found"
PASSAGE_NOT_FOUND = "not_found"
PASSAGE_UNAVAILABLE = "unavailable"
PASSAGE_NOT_ATTEMPTED = "not_attempted"
PASSAGE_CHECKS: tuple[str, ...] = (
    PASSAGE_FOUND,
    PASSAGE_NOT_FOUND,
    PASSAGE_UNAVAILABLE,
    PASSAGE_NOT_ATTEMPTED,
)

#: How many statements one report may look up. Each lookup is one bounded tool
#: call on the scoring run's session, made one after another (a session is not
#: safe to share across concurrent awaits), so the ceiling bounds the run's wall
#: clock. A statement past it records `PASSAGE_NOT_ATTEMPTED`: never silently
#: skipped, never silently cleared.
PASSAGE_LOOKUP_LIMIT = 16

#: The only sentences a reader is ever told beside a statement. WORDS ONLY and
#: no em dash: they travel to the report screen and the PDF, under the number
#: ban. Keyed by what they describe; `note_for` decides which one applies.
SUPPORT_NOTES: Mapping[str, str] = {
    LEVEL_UNSUPPORTED: (
        "The cited answers do not support this statement; a person should "
        "read the transcript before relying on it."
    ),
    REASON_ELSEWHERE: (
        "The answer this statement cites does not support it, but another of "
        "the candidate's answers does; read that answer before relying on it."
    ),
}


def note_for(level: str | None, reason: str | None) -> str | None:
    """The marker beside a statement with this verdict, or None for no marker.

    `supported` and every other `weak` say nothing: a marker on every sound
    sentence, or on every sentence an outage left unchecked, is noise that
    teaches a reader to skip the one that matters.
    """
    if level == LEVEL_UNSUPPORTED:
        return SUPPORT_NOTES[LEVEL_UNSUPPORTED]
    if level == LEVEL_WEAK and reason == REASON_ELSEWHERE:
        return SUPPORT_NOTES[REASON_ELSEWHERE]
    return None

#: `embed(texts) -> vectors`, order preserved. The production value is
#: `embeddings.embed`; a test passes a stub.
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


class RetrievedPassage(Protocol):
    """One passage a lookup returned: its verbatim text and its durable address.

    `evidence_retrieval.PassageRef` satisfies it (`content`, and `locator` as
    `context_chunks:<id>`). Stated structurally so this module never imports
    the retrieval entry point: the pipeline's stages reach it only through the
    seam their orchestrator wires.
    """

    content: str

    @property
    def locator(self) -> str: ...


class RetrievedPassages(Protocol):
    """What one lookup returned, and whether it could be trusted to be whole.

    `evidence_retrieval.Passages` satisfies it.
    """

    pieces: Sequence[RetrievedPassage]
    degraded: bool
    reason: str | None


#: `source(statement) -> passages`: the transcript passages that bear on one
#: statement, from THIS application only. `statement_passage_source` builds
#: the production value; a test passes a stub.
PassageSource = Callable[[str], Awaitable[RetrievedPassages]]

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
    #: Durable locators (`context_chunks:<id>`) of the passages that supported
    #: the statement when its own citation did not. Addresses, never text.
    passages: tuple[str, ...] = ()
    #: What the passage lookup did, when one was relevant (`PASSAGE_CHECKS`).
    passage_check: str | None = None

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"Unknown support level {self.level!r}")
        if self.passage_check is not None and self.passage_check not in PASSAGE_CHECKS:
            raise ValueError(f"Unknown passage check {self.passage_check!r}")
        if (self.reason == REASON_ELSEWHERE) != bool(self.passages) or (
            self.reason == REASON_ELSEWHERE and self.level != LEVEL_WEAK
        ):
            # The one way a lookup changes a verdict, pinned in the type: it
            # lifts to `weak`, never to `supported`, it names what it found,
            # and no other verdict carries a passage.
            raise ValueError(
                "a statement supported elsewhere is `weak` and carries the "
                "locators of the passages that support it; no other verdict "
                "carries a passage"
            )

    @property
    def note(self) -> str | None:
        return note_for(self.level, self.reason)

    def as_dict(self) -> dict[str, Any]:
        """The trail shape. The lookup fields appear only when they say something."""
        shaped: dict[str, Any] = {"level": self.level, "reason": self.reason}
        if self.passages:
            shaped["passages"] = list(self.passages)
        if self.passage_check is not None:
            shaped["passage_check"] = self.passage_check
        return shaped


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


async def _embed_all(
    texts: list[str], embed: Embedder, *, statements: int
) -> list[list[float]] | None:
    """One embedding call, or None when the semantic tier could not run.

    A failure is logged with the exception's class name and never its message:
    a message can quote the text that was being embedded.
    """
    from app.services.embeddings import EmbeddingError, EmbeddingUnavailable
    from app.services.reliability.vendor_contract import VendorContractViolation

    try:
        vectors = await embed(texts)
    except (EmbeddingError, EmbeddingUnavailable, VendorContractViolation) as exc:
        logger.warning(
            "siddhi.support.semantic_unavailable statements=%d error=%s",
            statements, type(exc).__name__,
        )
        return None
    if len(vectors) != len(texts):
        logger.warning(
            "siddhi.support.semantic_unavailable statements=%d error=%s",
            statements, "EmbeddingCountMismatch",
        )
        return None
    return vectors


class _Slots:
    """Texts to embed, each once, in first-seen order."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self._at: dict[str, int] = {}

    def slot(self, text: str) -> int:
        if text not in self._at:
            self._at[text] = len(self.texts)
            self.texts.append(text)
        return self._at[text]


async def _cited_tiers(
    requests: Sequence[SupportRequest],
    *,
    embed: Embedder | None,
    floor: float,
) -> dict[Hashable, SupportVerdict]:
    """Tiers one and two, against each statement's OWN citation."""
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

    slots = _Slots()
    plan = [
        (
            request,
            slots.slot(request.statement),
            [slots.slot(text) for text in request.excerpts if str(text or "").strip()],
        )
        for request in undecided
    ]
    vectors = await _embed_all(slots.texts, embed, statements=len(undecided))
    if vectors is None:
        for request in undecided:
            verdicts[request.key] = unavailable
        return verdicts

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


async def _passage_tier(
    verdicts: dict[Hashable, SupportVerdict],
    requests: Sequence[SupportRequest],
    *,
    passage_source: PassageSource,
    embed: Embedder | None,
    floor: float,
) -> None:
    """The third look, for `unsupported` verdicts only. Mutates `verdicts`.

    Lookups are SEQUENTIAL on purpose: the production source runs a tool call
    on the scoring run's own session, and one session must not serve two
    awaits at once. Every lookup's outcome is recorded on the verdict.
    """
    targets = [
        request
        for request in requests
        if verdicts[request.key].level == LEVEL_UNSUPPORTED
    ]
    if not targets:
        return

    # (request, the passages it found, the per-passage verdicts decided so far,
    #  whether the lookup itself reported a degradation)
    looked: list[
        tuple[SupportRequest, list[RetrievedPassage], list[bool | None], bool]
    ] = []
    for position, request in enumerate(targets):
        current = verdicts[request.key]
        if position >= PASSAGE_LOOKUP_LIMIT:
            verdicts[request.key] = SupportVerdict(
                current.level, current.reason, passage_check=PASSAGE_NOT_ATTEMPTED
            )
            continue
        found = await passage_source(request.statement)
        if found.degraded:
            # Recorded, never silent. A degraded lookup may still carry real
            # passages (a keyword-only result is the candidate's own words all
            # the same), so they are judged; what a degradation changes is the
            # meaning of finding NOTHING, which is "could not look properly".
            logger.warning(
                "siddhi.support.passages_degraded reason=%s", found.reason
            )
        pieces = [piece for piece in found.pieces if str(piece.content or "").strip()]
        if not pieces:
            verdicts[request.key] = SupportVerdict(
                current.level,
                current.reason,
                passage_check=(
                    PASSAGE_UNAVAILABLE if found.degraded else PASSAGE_NOT_FOUND
                ),
            )
            continue
        decided: list[bool | None] = []
        for piece in pieces:
            per_piece = deterministic(
                request.statement, [piece.content], subject_terms=request.subject_terms
            )
            decided.append(
                None if per_piece is None else per_piece.level == LEVEL_SUPPORTED
            )
        looked.append((request, pieces, decided, bool(found.degraded)))

    semantic: dict[tuple[int, int], bool] = {}
    pending = [
        (entry, index)
        for entry, (_, _, decided, _) in enumerate(looked)
        for index, value in enumerate(decided)
        if value is None
    ]
    semantic_ran = False
    if pending and embed is not None:
        slots = _Slots()
        plan = [
            (
                entry,
                index,
                slots.slot(looked[entry][0].statement),
                slots.slot(looked[entry][1][index].content),
            )
            for entry, index in pending
        ]
        vectors = await _embed_all(slots.texts, embed, statements=len(looked))
        if vectors is not None:
            semantic_ran = True
            for entry, index, statement_slot, piece_slot in plan:
                semantic[(entry, index)] = (
                    _cosine(vectors[statement_slot], vectors[piece_slot]) >= floor
                )

    for entry, (request, pieces, decided, degraded) in enumerate(looked):
        current = verdicts[request.key]
        supporting = tuple(
            dict.fromkeys(
                piece.locator
                for index, piece in enumerate(pieces)
                if decided[index] is True or semantic.get((entry, index)) is True
            )
        )
        if supporting:
            verdicts[request.key] = SupportVerdict(
                LEVEL_WEAK,
                REASON_ELSEWHERE,
                passages=supporting,
                passage_check=PASSAGE_FOUND,
            )
            continue
        # Nothing supported it. When the lookup degraded, or a passage could
        # only have been judged by the semantic tier and that tier did not
        # run, the honest record is "could not check", not "checked and found
        # nothing".
        unjudged = any(value is None for value in decided) and not semantic_ran
        verdicts[request.key] = SupportVerdict(
            current.level,
            current.reason,
            passage_check=(
                PASSAGE_UNAVAILABLE if (unjudged or degraded) else PASSAGE_NOT_FOUND
            ),
        )


async def assess_many(
    requests: Sequence[SupportRequest],
    *,
    embed: Embedder | None,
    threshold: float | None = None,
    passage_source: PassageSource | None = None,
) -> dict[Hashable, SupportVerdict]:
    """Every request's verdict, with ONE embedding call per tier.

    `embed=None` means no semantic tier is available (see
    `semantic_embedder`), and every undecided statement is `weak` with the
    reason saying so. An embedding failure is the same state.

    `passage_source`, when given, is the third look (module docstring): only
    a statement that ended `unsupported` is looked up, and a lookup can only
    lift it to `weak` with `REASON_ELSEWHERE`. None means no lookup is made and
    nothing about the lookup is recorded.
    """
    floor = _threshold(threshold)
    verdicts = await _cited_tiers(requests, embed=embed, floor=floor)
    if passage_source is not None:
        await _passage_tier(
            verdicts,
            requests,
            passage_source=passage_source,
            embed=embed,
            floor=floor,
        )
    return verdicts


async def assess(
    statement: str,
    excerpts: Sequence[str],
    *,
    subject_terms: Sequence[str] = (),
    embed: Embedder | None,
    threshold: float | None = None,
    passage_source: PassageSource | None = None,
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
        passage_source=passage_source,
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


def statement_passage_source(
    fetch: Callable[..., Awaitable[RetrievedPassages]],
    session: Any,
    *,
    tenant_id: Any,
    link_id: Any,
) -> PassageSource:
    """The production `PassageSource`, bound to one application.

    `fetch` is `evidence_retrieval.support_passages_for_statement`, whose
    signature is `(session, *, tenant_id, link_id, statement, agent=...)`:
    the orchestrator passes it in, so this package never imports the
    retrieval entry point and the scope (this tenant, this application) is
    fixed once, here, rather than restated at every call. A policy refusal
    from the tool layer RAISES through this seam: it is a wiring defect,
    identical on every retry, and recording it as "unavailable" would ship a
    lookup that never ran.
    """

    async def _source(statement: str) -> RetrievedPassages:
        return await fetch(
            session, tenant_id=tenant_id, link_id=link_id, statement=statement
        )

    return _source
