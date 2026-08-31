"""The citable evidence set a PRISM Report is written against.

`citations.Report` refuses any statement whose refs are not in a known set, and
this module is where that set comes from on the delivered path. It exists so
that the refs a statement carries and the refs the report will accept are minted
by ONE piece of code from ONE input: a generator that minted its own refs
against a set somebody else assembled would either always agree (making the
check decorative) or disagree for reasons nobody could reproduce.

THREE KINDS OF NODE, AND THE THIRD IS THE ONE WORTH ARGUING FOR
-----------------------------------------------------------------
`answer`    something the candidate said, filed against the criterion it was
            said about. This is what a finding rests on.
`question`  what they were asked. A probe that goes somewhere new with an
            answer cites the exchange, not just the reply.
`searched`  THE RECORD THAT THE CRITERION WAS ASSESSED AT ALL.

The third is the entry the citation rule stands or falls on. "There is no
evidence of on-call ownership" feels uncitable, because there is nothing to
point at. It is citable, and it must be: the citation is the evidence that was
SEARCHED. That is the whole difference between

    "we asked about this competency, and none of what they said addressed it"

and

    "we never asked".

The first is a finding about a candidate. The second is a gap in the assessment
being reported as a gap in the candidate, and it is the specific injustice the
gap-needs-a-citation rule prevents. Without a `searched` node a generator facing
an unevidenced criterion has exactly two options, both bad: emit the gap uncited
(which the chokepoint refuses, taking the section with it) or drop the gap
silently (which hides the criterion the candidate did worst on). With one, the
honest statement is expressible and carries its provenance.

A `searched` node exists for EVERY rated item, including one with no answers,
because the item being on the frozen matrix and having been carried into the
report IS the record that it was assessed. It is not a placeholder and not a
default: it is a different, weaker, true fact than an answer node, and it is
named differently so nobody can mistake one for the other.

REFS ARE DETERMINISTIC AND CONTAIN NO CANDIDATE TEXT
------------------------------------------------------
A ref is a locator. It is persisted with the immutable report and read by people
auditing a grade, and a locator that quoted the sentence it points at would put
a candidate's own words into every table that stores a citation. The excerpt is
held in the node, in memory, for the generator to ground a probe on, and is
never part of the ref.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "KIND_ANSWER",
    "KIND_QUESTION",
    "KIND_SEARCHED",
    "EvidenceNode",
    "EvidenceIndex",
]

KIND_ANSWER = "answer"
KIND_QUESTION = "question"
KIND_SEARCHED = "searched"

#: How many characters of an answer the generator may quote back when grounding
#: a probe. Long enough to be recognisably the candidate's own claim, short
#: enough that it is a reference rather than a reproduction.
EXCERPT_CHARS = 240

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG.sub("-", str(value or "").casefold()).strip("-") or "item"


@dataclass(frozen=True)
class EvidenceNode:
    """One thing a statement may cite."""

    ref: str
    kind: str
    item: str
    #: INTERNAL. What the node points at, for grounding a probe. Never rendered
    #: and never part of the ref.
    excerpt: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The audit shape. Deliberately without the excerpt.

        The trail is persisted with the report and read far more widely than the
        report is; a trail carrying answer text would make every reader of the
        provenance a reader of the transcript.
        """
        return {"ref": self.ref, "kind": self.kind, "item": self.item}


@dataclass
class EvidenceIndex:
    """The evaluation's complete citable set, keyed for the generator's use."""

    nodes: tuple[EvidenceNode, ...] = ()
    _by_item: dict[str, list[EvidenceNode]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for node in self.nodes:
            self._by_item.setdefault(node.item, []).append(node)

    @property
    def refs(self) -> frozenset[str]:
        """What `citations.Report` is constructed from."""
        return frozenset(node.ref for node in self.nodes)

    def for_item(self, item: str, *, kind: str | None = None) -> tuple[EvidenceNode, ...]:
        found = self._by_item.get(str(item), [])
        if kind is None:
            return tuple(found)
        return tuple(node for node in found if node.kind == kind)

    def refs_for(self, item: str, *, kind: str | None = None) -> tuple[str, ...]:
        return tuple(node.ref for node in self.for_item(item, kind=kind))

    def grounding(self, item: str) -> tuple[str, ...]:
        """The refs a claim ABOUT this item rests on.

        The item's answers when it has any, and its `searched` record when it
        does not. Never both-or-nothing: a claim resting on the search record
        alone is a weaker claim, and the ref says so by its kind.
        """
        answers = self.refs_for(item, kind=KIND_ANSWER)
        if answers:
            return answers
        return self.refs_for(item, kind=KIND_SEARCHED)

    def searched(self, item: str) -> tuple[str, ...]:
        return self.refs_for(item, kind=KIND_SEARCHED)

    def excerpt(self, item: str) -> str:
        for node in self.for_item(item, kind=KIND_ANSWER):
            if node.excerpt:
                return node.excerpt
        return ""

    def exchanges(self, item: str) -> tuple[tuple[str, str, str], ...]:
        """(question_ref, answer_ref, answer_excerpt) for this item, in order."""
        questions = self.for_item(item, kind=KIND_QUESTION)
        answers = self.for_item(item, kind=KIND_ANSWER)
        return tuple(
            (question.ref, answer.ref, answer.excerpt)
            for question, answer in zip(questions, answers)
        )

    @classmethod
    def build(
        cls,
        *,
        items: Sequence[str],
        exchanges: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> "EvidenceIndex":
        """Index every rated item, and every exchange recorded against one.

        `items` is the report's own rated line-up. Passing it separately from
        the exchanges is what guarantees a `searched` node for an item nobody
        answered anything about, which is precisely the item a gap statement
        will be written for.
        """
        seen: dict[str, str] = {}
        nodes: list[EvidenceNode] = []
        recorded = exchanges or {}
        for name in items:
            key = str(name)
            if key in seen:
                continue
            # A slug collision between two differently-punctuated names would
            # silently merge two criteria's evidence. Disambiguated by position,
            # which is stable for one report and is all a locator needs.
            base = _slug(key)
            slug = base if base not in set(seen.values()) else f"{base}-{len(seen)}"
            seen[key] = slug
            nodes.append(
                EvidenceNode(
                    ref=f"{KIND_SEARCHED}:{slug}",
                    kind=KIND_SEARCHED,
                    item=key,
                )
            )
            for index, exchange in enumerate(recorded.get(key, []) or []):
                question = str(exchange.get("question") or "").strip()
                answer = str(exchange.get("answer") or "").strip()
                if question:
                    nodes.append(
                        EvidenceNode(
                            ref=f"{KIND_QUESTION}:{slug}:{index}",
                            kind=KIND_QUESTION,
                            item=key,
                            excerpt=question[:EXCERPT_CHARS],
                        )
                    )
                if answer:
                    nodes.append(
                        EvidenceNode(
                            ref=f"{KIND_ANSWER}:{slug}:{index}",
                            kind=KIND_ANSWER,
                            item=key,
                            excerpt=answer[:EXCERPT_CHARS],
                        )
                    )
        return cls(nodes=tuple(nodes))
