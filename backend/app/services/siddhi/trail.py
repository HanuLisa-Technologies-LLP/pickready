"""The citation trail's READ MODEL: what a PRISM Report's statements rest on.

The trail has been written onto every report since the citation chokepoint went
live (`gap_analysis_json["siddhi"]["citations"]`) and was read by nothing:
`GapAnalysisOut` does not declare the key, so a recruiter could never ask "what
did this sentence rest on" of the one system that recorded the answer. This
module is the one reader. The API serves it (the report citations route); the
report serializer reads the per-row support notes from it.

TWO LAYERS, AND ONLY THE SECOND ONE TOUCHES THE TRANSCRIPT
------------------------------------------------------------
  * `read_trail(gap_analysis_json)` is PURE. It turns the stored trail into
    typed statements and nodes, including a trail written before the Vivekium
    release (version 1: no items, no locators, no support), and reports
    `available=False` for a report with no trail at all rather than an empty
    one that reads as "nothing was cited".
  * `resolve_evidence(session, trail, ...)` turns the durable LOCATORS into
    text at READ time: the question asked, the answer given, the passage read.
    The stored trail holds addresses and never text, so the transcript is read
    only here, behind the capability that guards it, and every lookup is
    scoped to the report's own application: a locator can never resolve to
    another candidate's words, whatever it says.

`view(...)` is the client shape: words only, no ref, no locator, no id, no
position, because every one of those is a number or an identifier and a report
surface carries neither.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.services.siddhi import citations, support
from app.services.siddhi.evidence import (
    EXCERPT_CHARS,
    KIND_ANSWER,
    KIND_EMPLOYER,
    KIND_PASSAGE,
    KIND_PORTABLE,
    KIND_QUESTION,
    KIND_SEARCHED,
    LOCATOR_CHUNK,
    LOCATOR_MESSAGE,
    LOCATOR_QUESTION,
)

__all__ = [
    "EVIDENCE_KIND_WORDS",
    "SUPPORTING_PASSAGE",
    "TrailNode",
    "TrailStatement",
    "CitationTrail",
    "ResolvedEvidence",
    "read_trail",
    "resolve_evidence",
    "view",
    "citation_view",
]

#: The view's kind for a passage the support check found elsewhere in the
#: candidate's answers (`support.REASON_ELSEWHERE`). Not a node kind: the
#: statement does not cite it, and the view says so rather than listing it
#: among the citations.
SUPPORTING_PASSAGE = "supporting_passage"

#: How a reader is told what KIND of evidence a statement rests on. Words.
EVIDENCE_KIND_WORDS: Mapping[str, str] = {
    KIND_ANSWER: "The candidate's answer",
    KIND_QUESTION: "The question asked",
    KIND_PASSAGE: "A passage the evaluation read",
    KIND_SEARCHED: "The record that this area was assessed",
    KIND_EMPLOYER: "A previous employer's confirmation",
    KIND_PORTABLE: "The candidate's standing record",
    SUPPORTING_PASSAGE: "Another of the candidate's answers, which supports this statement",
}


@dataclass(frozen=True)
class TrailNode:
    ref: str
    kind: str
    item: str
    locators: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrailStatement:
    section: str
    kind: str
    item: str
    text: str
    refs: tuple[str, ...]
    support_level: str | None = None
    support_reason: str | None = None
    #: Locators of passages elsewhere in the transcript that support the
    #: statement when its own citation does not (`support.REASON_ELSEWHERE`).
    support_passages: tuple[str, ...] = ()
    #: What the support check's passage lookup did, when one was relevant.
    passage_check: str | None = None

    @property
    def support_note(self) -> str | None:
        """The words-only marker beside this statement, or None.

        The ONE rule, `support.note_for`, so the stored verdict and the words
        a reader sees cannot disagree.
        """
        return support.note_for(self.support_level, self.support_reason)


@dataclass(frozen=True)
class CitationTrail:
    """A report's trail. `available=False` is a report written without one."""

    available: bool
    version: int = 0
    statements: tuple[TrailStatement, ...] = ()
    nodes: Mapping[str, TrailNode] | None = None
    withheld: tuple[Mapping[str, str], ...] = ()

    def statement_for(
        self, section: str, item: str, *, kind: str = citations.KIND_FINDING
    ) -> TrailStatement | None:
        for statement in self.statements:
            if (
                statement.section == section
                and statement.item == item
                and statement.kind == kind
            ):
                return statement
        return None

    def remark_support_note(self, section: str, item: str) -> str | None:
        """The marker beside a rated row's remark (`DimensionOut.support_note`)."""
        statement = self.statement_for(section, item)
        return statement.support_note if statement is not None else None


def read_trail(gap_analysis_json: Mapping[str, Any] | None) -> CitationTrail:
    """The stored trail, typed. Pure; reads version 1 and version 2 trails.

    A report with no Siddhi namespace or no citations is `available=False`.
    A trail that is present and malformed RAISES: it is this system's own
    immutable record, so a bad shape is a defect to fix, not a state to render.
    """
    siddhi = (gap_analysis_json or {}).get("siddhi")
    if not isinstance(siddhi, Mapping):
        return CitationTrail(available=False)
    raw = siddhi.get("citations")
    if not isinstance(raw, Mapping):
        return CitationTrail(available=False)
    nodes = {
        str(node["ref"]): TrailNode(
            ref=str(node["ref"]),
            kind=str(node["kind"]),
            item=str(node.get("item") or ""),
            locators=tuple(str(value) for value in node.get("locators") or ()),
        )
        for node in raw.get("evidence_nodes") or []
    }
    statements = []
    for statement in raw.get("statements") or []:
        verdict = statement.get("support") or {}
        statements.append(
            TrailStatement(
                section=str(statement["section"]),
                kind=str(statement["kind"]),
                item=str(statement.get("item") or ""),
                text=str(statement["text"]),
                refs=tuple(str(ref) for ref in statement.get("evidence_refs") or ()),
                support_level=verdict.get("level"),
                support_reason=verdict.get("reason"),
                support_passages=tuple(
                    str(value) for value in verdict.get("passages") or ()
                ),
                passage_check=verdict.get("passage_check"),
            )
        )
    return CitationTrail(
        available=True,
        version=int(raw.get("version") or 1),
        statements=tuple(statements),
        nodes=nodes,
        withheld=tuple(dict(entry) for entry in raw.get("withheld") or ()),
    )


@dataclass(frozen=True)
class ResolvedEvidence:
    """One node's text, resolved at read time. INTERNAL until `view` shapes it."""

    kind: str
    excerpt: str | None = None
    question: str | None = None
    #: The message's position in the conversation, for ORDERING only. Never
    #: placed in a client payload.
    turn: int | None = None


def _locators(trail: CitationTrail) -> list[str]:
    """Every durable locator the trail holds: the nodes', then the passages
    the support check found for a statement its citation did not support."""
    found = [locator for node in (trail.nodes or {}).values() for locator in node.locators]
    found.extend(
        locator for statement in trail.statements for locator in statement.support_passages
    )
    return found


def _ids(trail: CitationTrail, prefix: str) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    for locator in _locators(trail):
        head, _, tail = locator.partition(":")
        if head != prefix or not tail:
            continue
        try:
            found.add(uuid.UUID(tail))
        except ValueError as exc:
            raise ValueError(
                f"a stored locator under {prefix!r} is not a uuid"
            ) from exc
    return found


def _cap(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(str(text).split())
    return cleaned[:EXCERPT_CHARS]


async def resolve_evidence(
    session: Any,
    trail: CitationTrail,
    *,
    link_id: uuid.UUID,
    chunk_source_ids: Iterable[uuid.UUID] = (),
) -> dict[str, ResolvedEvidence]:
    """{ref: its text}, read now, scoped to THIS application.

    Keys are the trail's refs, plus the LOCATOR of every passage the support
    check found elsewhere for a statement (`TrailStatement.support_passages`),
    which has no ref because the statement does not cite it. The two key
    spaces cannot collide: a ref starts with a node kind, a locator with a
    table name.

    Messages are read only through a conversation belonging to `link_id`,
    questions only when they were issued on `link_id`, and passages only from
    `chunk_source_ids` (the application and the resume profile it was sent
    with). A locator outside that scope resolves to nothing rather than to
    someone else's words. Excerpts are capped at `EXCERPT_CHARS`.

    The session is the caller's tenant session, so RLS is the outer boundary
    and these filters are the inner one.
    """
    from sqlalchemy import select

    from app.models.assessment import (
        AssessmentConversation,
        AssessmentMessage,
        CandidateQuestion,
    )
    from app.models.context import ContextChunk

    resolved: dict[str, ResolvedEvidence] = {}
    if not trail.available:
        return resolved

    message_ids = _ids(trail, LOCATOR_MESSAGE)
    messages: dict[str, Any] = {}
    if message_ids:
        rows = (
            await session.execute(
                select(AssessmentMessage)
                .join(
                    AssessmentConversation,
                    AssessmentConversation.id == AssessmentMessage.conversation_id,
                )
                .where(
                    AssessmentConversation.job_candidate_link_id == link_id,
                    AssessmentMessage.id.in_(message_ids),
                )
            )
        ).scalars().all()
        messages = {str(row.id): row for row in rows}

    question_ids = _ids(trail, LOCATOR_QUESTION)
    questions: dict[str, Any] = {}
    if question_ids:
        rows = (
            await session.execute(
                select(CandidateQuestion).where(
                    CandidateQuestion.job_candidate_link_id == link_id,
                    CandidateQuestion.id.in_(question_ids),
                )
            )
        ).scalars().all()
        questions = {str(row.id): row for row in rows}

    chunk_ids = _ids(trail, LOCATOR_CHUNK)
    sources = set(chunk_source_ids)
    chunks: dict[str, Any] = {}
    if chunk_ids and sources:
        rows = (
            await session.execute(
                select(ContextChunk).where(
                    ContextChunk.id.in_(chunk_ids),
                    ContextChunk.source_id.in_(sources),
                )
            )
        ).scalars().all()
        chunks = {str(row.id): row for row in rows}

    for ref, node in (trail.nodes or {}).items():
        texts: list[str] = []
        turn: int | None = None
        question_text: str | None = None
        for locator in node.locators:
            head, _, tail = locator.partition(":")
            if head == LOCATOR_MESSAGE and tail in messages:
                row = messages[tail]
                texts.append(row.content)
                turn = row.ordinal if turn is None else min(turn, row.ordinal)
            elif head == LOCATOR_QUESTION and tail in questions:
                question_text = questions[tail].prompt
            elif head == LOCATOR_CHUNK and tail in chunks:
                texts.append(chunks[tail].content)
        if node.kind == KIND_QUESTION:
            resolved[ref] = ResolvedEvidence(kind=node.kind, question=_cap(question_text))
        else:
            resolved[ref] = ResolvedEvidence(
                kind=node.kind,
                excerpt=_cap(" ".join(texts)) if texts else None,
                turn=turn,
            )
    for statement in trail.statements:
        for locator in statement.support_passages:
            head, _, tail = locator.partition(":")
            if head != LOCATOR_CHUNK:
                continue
            row = chunks.get(tail)
            resolved[locator] = ResolvedEvidence(
                kind=SUPPORTING_PASSAGE,
                excerpt=_cap(row.content) if row is not None else None,
            )
    return resolved


def view(
    trail: CitationTrail, resolved: Mapping[str, ResolvedEvidence]
) -> dict[str, Any]:
    """The client shape. Words only: no ref, no locator, no id, no position.

    The question an answer was given to is paired onto the answer's entry
    (same exchange position), so a reader sees "asked this, answered that".
    """
    statements = []
    for statement in trail.statements:
        evidence = []
        for ref in statement.refs:
            node = (trail.nodes or {}).get(ref)
            if node is None:
                continue
            found = resolved.get(ref)
            question = None
            if node.kind == KIND_ANSWER:
                suffix = ref.split(":", 1)[1] if ":" in ref else ""
                paired = resolved.get(f"{KIND_QUESTION}:{suffix}") if suffix else None
                question = paired.question if paired is not None else None
            evidence.append(
                (
                    found.turn if found is not None and found.turn is not None else 0,
                    {
                        "kind": EVIDENCE_KIND_WORDS.get(node.kind, node.kind),
                        "question": question,
                        "excerpt": found.excerpt if found is not None else None,
                    },
                )
            )
        evidence.sort(key=lambda pair: pair[0])
        # A passage that supports the statement although it is not cited: after
        # the citations, and named for what it is, so a reader never mistakes
        # it for what the statement cites.
        for locator in statement.support_passages:
            found = resolved.get(locator)
            evidence.append(
                (
                    0,
                    {
                        "kind": EVIDENCE_KIND_WORDS[SUPPORTING_PASSAGE],
                        "question": None,
                        "excerpt": found.excerpt if found is not None else None,
                    },
                )
            )
        statements.append(
            {
                "section": statement.section,
                "item": statement.item,
                "kind": statement.kind,
                "text": statement.text,
                "support": statement.support_note,
                "evidence": [entry for _, entry in evidence],
            }
        )
    return {"trail_available": trail.available, "statements": statements}


async def citation_view(
    session: Any,
    gap_analysis_json: Mapping[str, Any] | None,
    *,
    link_id: uuid.UUID,
    chunk_source_ids: Iterable[uuid.UUID],
) -> dict[str, Any]:
    """THE READ MODEL THE REPORT API SERVES: read, resolve, shape. One call.

    `gap_analysis_json` is the report row's column as stored; `link_id` the
    report's own application; `chunk_source_ids` the sources a cited passage
    may come from, which is the application itself (its transcript chunks)
    and the resume profile it was submitted with. The session is the caller's
    TENANT session: RLS is the outer boundary and the scoping above the inner
    one. A report with no trail answers `trail_available: False` and no
    statements, never an empty list that reads as "nothing was cited".
    """
    read = read_trail(gap_analysis_json)
    resolved = await resolve_evidence(
        session, read, link_id=link_id, chunk_source_ids=chunk_source_ids
    )
    return view(read, resolved)
