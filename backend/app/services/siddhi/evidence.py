"""The citable evidence set a PRISM Report is written against.

`citations.Report` refuses any statement whose refs are not in a known set, and
this module is where that set comes from on the delivered path. It exists so
that the refs a statement carries and the refs the report will accept are minted
by ONE piece of code from ONE input: a generator that minted its own refs
against a set somebody else assembled would either always agree (making the
check decorative) or disagree for reasons nobody could reproduce.

FOUR KINDS OF NODE, AND THE THIRD IS THE ONE WORTH ARGUING FOR
-----------------------------------------------------------------
`answer`    something the candidate said, filed against the criterion it was
            said about. This is what a finding rests on.
`question`  what they were asked. A probe that goes somewhere new with an
            answer cites the exchange, not just the reply.
`searched`  THE RECORD THAT THE CRITERION WAS ASSESSED AT ALL.
`employer`  a previous employer's own confirmation of a declared employment.
            The one kind whose originator is not the candidate, added with the
            Evidence vs Claim Summary; its argument is on the constant below.

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

REFS ARE POSITIONAL; LOCATORS ARE DURABLE (Vivekium release)
--------------------------------------------------------------
A ref (`answer:<slug>:<index>`) is stable within ONE report and means nothing
outside it: it names "the first exchange filed under this item", and a reader
of the stored trail had no way from it back to the message the candidate
actually typed. So a node now also carries `locators`, durable addresses of the
rows it was built from (`assessment_messages:<uuid>`,
`candidate_questions:<uuid>`, `context_chunks:<uuid>`, `employer:<slug>`,
`portable:<slug>`), persisted in the trail beside the ref. A locator is an
address and never text: resolving it to an excerpt happens at READ time,
behind the capability that guards the transcript
(`siddhi.trail.resolve_evidence`), so the stored trail stays a record anyone
can audit without it becoming a copy of the transcript.

`content` is the node's full text, IN MEMORY ONLY, for the support check in
`siddhi.support`. It is never persisted and never part of `as_dict`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "KIND_ANSWER",
    "KIND_QUESTION",
    "KIND_SEARCHED",
    "KIND_EMPLOYER",
    "KIND_PORTABLE",
    "KIND_PASSAGE",
    "LOCATOR_MESSAGE",
    "LOCATOR_QUESTION",
    "LOCATOR_CHUNK",
    "EvidenceNode",
    "EvidenceIndex",
    "employer_item",
    "employer_node",
    "portable_node",
    "passage_node",
]

#: Durable address prefixes. `siddhi.trail` resolves the first three at read
#: time; the employer and portable addresses are their own refs and carry no
#: text to resolve.
LOCATOR_MESSAGE = "assessment_messages"
LOCATOR_QUESTION = "candidate_questions"
LOCATOR_CHUNK = "context_chunks"

KIND_ANSWER = "answer"
KIND_QUESTION = "question"
KIND_SEARCHED = "searched"

#: A FOURTH KIND, AND THE ONLY ONE NOT SPOKEN BY THE CANDIDATE.
#:
#: A previous employer confirmed, or declined to confirm, an employment the
#: candidate declared. The other three kinds are all the candidate's own
#: account or the product's record of asking for it; this is the one node kind
#: whose originator is somebody else, which is exactly why the Evidence vs
#: Claim Summary needs it to exist: a claim corroborated only by the person
#: making it and a claim an employer confirmed are different claims, and with
#: three kinds there was no ref that could tell them apart.
#:
#: WHAT IT CARRIES AND WHAT IT MUST NOT. The employer's NAME and the tenant's
#: own verification decision, as a word. Never the HR contact's address or
#: name: that is a third party's personal contact detail a candidate handed
#: over for one purpose, it reaches the recruiter running the verification and
#: nobody else, and a delivered report is forwarded.
KIND_EMPLOYER = "employer"

#: A FIFTH KIND: THIS CRITERION RESTS ON THE CANDIDATE'S PORTABLE RECORD.
#:
#: Owner ruling 2026-09-22 (change request 23). A criterion the Portable layer
#: already established was recorded rather than asked, so the answer in the
#: transcript is the platform stating what it already held rather than the
#: candidate typing it afresh. Both facts are true and the report must be able
#: to say which it is standing on.
#:
#: A SEPARATE KIND RATHER THAN AN `answer` NODE WITH A NOTE ON IT, because the
#: distinction is exactly the one a person auditing a grade needs: "they told
#: us this, here, in this assessment" and "we already knew this, from their
#: standing record" are different provenances, and a reader with one ref kind
#: could not recover which. The node joins the item's own grounding rather than
#: replacing it, so a criterion with both kinds cites both.
#:
#: WHAT IT DOES NOT CARRY, and this is the constraint the whole feature rests
#: on: no score, no grade, no prior verdict of any kind. There is nothing in
#: `portable_evidence_items` that could supply one, and a ref is a locator in
#: any case. The new job's matrix grades this criterion itself.
KIND_PORTABLE = "portable"

#: A SIXTH KIND: A TRANSCRIPT OR RESUME PASSAGE A GRADING JUDGEMENT READ.
#:
#: Miti's item evaluation may read passages from the candidate's OTHER answers
#: (or their resume) that bear on a skill, retrieved through the typed tool
#: layer. A grade that rested partly on one of those has to be able to cite it,
#: or the remark written from that grade would cite less than it stands on.
#: The node's locator is the `context_chunks` row, and its content (in memory
#: only) is what the support check reads. It joins the item's grounding beside
#: its answers, never instead of them.
KIND_PASSAGE = "passage"

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
    #: Durable addresses of the rows this node was built from. Persisted: an
    #: address is not text. Empty on a node built from an exchange that carried
    #: no ids (every report written before the Vivekium release).
    locators: tuple[str, ...] = ()
    #: INTERNAL. The node's full text for the support check. Never persisted.
    content: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The audit shape. Deliberately without the excerpt or the content.

        The trail is persisted with the report and read far more widely than the
        report is; a trail carrying answer text would make every reader of the
        provenance a reader of the transcript. The locators travel because they
        are addresses, resolved to text only behind the transcript capability.
        """
        return {
            "ref": self.ref,
            "kind": self.kind,
            "item": self.item,
            "locators": list(self.locators),
        }

    @property
    def support_text(self) -> str:
        """The text the support check may read: the full content when held,
        the excerpt otherwise."""
        return self.content or self.excerpt


def employer_item(employer_name: str) -> str:
    """The index key an employer confirmation is filed under.

    Namespaced like the aspect nodes are, so an employer called "Observability"
    can never collide with a competency called "Observability" and silently
    corroborate it.
    """
    return f"employer:{_slug(employer_name)}"


def employer_node(employer_name: str) -> "EvidenceNode":
    """One citable employer confirmation.

    The ref carries the slug of the employer's name and nothing else. A ref is
    persisted with the immutable report and read by people auditing a grade, so
    it is a locator: it must not carry the confirmation's outcome, which is a
    fact that belongs in the cited statement where a reader can see it stated
    rather than encoded in an identifier.
    """
    item = employer_item(employer_name)
    ref = f"{KIND_EMPLOYER}:{_slug(employer_name)}"
    # The ref IS the durable address here: it names the employer, and the
    # confirmation it points at is read from `bgv_verifications` by name.
    return EvidenceNode(ref=ref, kind=KIND_EMPLOYER, item=item, locators=(ref,))


def portable_node(item: str) -> "EvidenceNode":
    """One citable "this came from the portable record" node for a rated item.

    Filed under the ITEM's own name, not under a namespace of its own, because
    it is evidence about that criterion and has to join that criterion's
    grounding. The employer nodes are namespaced instead, and the difference is
    real: an employer confirmation is about an EMPLOYER, which could collide
    with a competency of the same name, while this is about the competency
    already.
    """
    key = str(item)
    ref = f"{KIND_PORTABLE}:{_slug(key)}"
    return EvidenceNode(ref=ref, kind=KIND_PORTABLE, item=key, locators=(ref,))


def passage_node(
    item: str, slug: str, index: int, *, chunk_id: Any, content: str
) -> "EvidenceNode":
    """One citable retrieved passage a grading judgement read for `item`.

    The locator is the `context_chunks` row; the content is held in memory for
    the support check and never persisted. A passage with no chunk id is not
    citable, because a reader of the trail could never find what it pointed at.
    """
    if not chunk_id:
        raise ValueError(
            f"a passage for {item!r} carries no chunk id; an unaddressable "
            f"passage cannot be cited"
        )
    text = str(content or "").strip()
    return EvidenceNode(
        ref=f"{KIND_PASSAGE}:{slug}:{index}",
        kind=KIND_PASSAGE,
        item=str(item),
        excerpt=text[:EXCERPT_CHARS],
        locators=(f"{LOCATOR_CHUNK}:{chunk_id}",),
        content=text,
    )


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

        The item's answers and its portable record when it has either, and its
        `searched` record when it has neither. Never both-or-nothing: a claim
        resting on the search record alone is a weaker claim, and the ref says
        so by its kind.

        THE TWO REAL KINDS ARE RETURNED TOGETHER RATHER THAN ONE WINNING. A
        criterion the Portable layer established still produced a recorded
        exchange in the transcript, so both nodes exist and both are true; a
        reader auditing the grade is entitled to see that the answer was the
        platform restating what it held, which is only visible if the portable
        ref travels beside it. The `searched` fallback stays last for the
        reason it has always been last: it is the record that the criterion was
        assessed at all, and it is what makes a gap statement citable.
        """
        answers = self.refs_for(item, kind=KIND_ANSWER)
        portable = self.refs_for(item, kind=KIND_PORTABLE)
        # A passage a grading judgement read is evidence the grade rests on,
        # so a claim about the grade cites it beside the answers. It never
        # stands in for them: an item with passages and no answer still has
        # real evidence, and the refs say which kind by their prefix.
        passages = self.refs_for(item, kind=KIND_PASSAGE)
        if answers or portable or passages:
            return answers + portable + passages
        return self.refs_for(item, kind=KIND_SEARCHED)

    def node(self, ref: str) -> EvidenceNode | None:
        """The node a ref names, or None for a ref this index does not hold."""
        for node in self.nodes:
            if node.ref == ref:
                return node
        return None

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
        passages: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> "EvidenceIndex":
        """Index every rated item, and every exchange recorded against one.

        `items` is the report's own rated line-up. Passing it separately from
        the exchanges is what guarantees a `searched` node for an item nobody
        answered anything about, which is precisely the item a gap statement
        will be written for.

        An exchange may carry `question_id` and `message_ids` (the
        `candidate_questions` row and the `assessment_messages` rows its answer
        was assembled from); when it does, the nodes carry them as durable
        locators. `passages` maps an item to the retrieved passages a grading
        judgement read for it, each `{"chunk_id", "content"}`.
        """
        seen: dict[str, str] = {}
        nodes: list[EvidenceNode] = []
        recorded = exchanges or {}
        retrieved = passages or {}
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
                question_id = exchange.get("question_id")
                message_ids = [
                    str(value) for value in (exchange.get("message_ids") or []) if value
                ]
                if question:
                    nodes.append(
                        EvidenceNode(
                            ref=f"{KIND_QUESTION}:{slug}:{index}",
                            kind=KIND_QUESTION,
                            item=key,
                            excerpt=question[:EXCERPT_CHARS],
                            locators=(
                                (f"{LOCATOR_QUESTION}:{question_id}",)
                                if question_id
                                else ()
                            ),
                            content=question,
                        )
                    )
                if answer:
                    nodes.append(
                        EvidenceNode(
                            ref=f"{KIND_ANSWER}:{slug}:{index}",
                            kind=KIND_ANSWER,
                            item=key,
                            excerpt=answer[:EXCERPT_CHARS],
                            locators=tuple(
                                f"{LOCATOR_MESSAGE}:{message_id}"
                                for message_id in message_ids
                            ),
                            content=answer,
                        )
                    )
            for index, passage in enumerate(retrieved.get(key, []) or []):
                nodes.append(
                    passage_node(
                        key,
                        slug,
                        index,
                        chunk_id=passage.get("chunk_id"),
                        content=str(passage.get("content") or ""),
                    )
                )
        return cls(nodes=tuple(nodes))
