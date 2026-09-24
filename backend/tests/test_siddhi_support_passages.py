"""The support check's third look: passages elsewhere in the candidate's answers.

A statement its own citation does not support may still be TRUE: the model
wrote from the whole record and pinned the sentence to the wrong answer. So an
`unsupported` statement is looked up once more through a `PassageSource`
(production: `evidence_retrieval.support_passages_for_statement`, through the
typed tool layer). These tests pin the one way that lookup may change a
verdict, and every way it must not:

  * it lifts `unsupported` to `weak` with `REASON_ELSEWHERE` and the passage's
    locator, and NEVER to `supported`;
  * it never runs for a statement that is not `unsupported`;
  * a degraded lookup, an empty one and a semantic tier that could not judge
    the passages each leave the verdict as it was, with the reason recorded;
  * lookups are sequential (one scoring session) and bounded per report.

The passage source is a stub throughout, shaped like `evidence_retrieval`'s
`Passages` / `PassageRef`, which lives on another branch. The signature the
production seam binds is pinned against the real function by
`test_siddhi_support_wiring.py`, which lands with the wiring once both
branches are merged (it would only be a skip before then, and a skip is not a
check).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest

from app.services.siddhi import report as siddhi_report
from app.services.siddhi import support, trail

ANSWER = (
    "I moved the orders service onto the new cluster over two sprints and "
    "wrote the rollback runbook myself."
)
ELSEWHERE = (
    "Later I ran the Kubernetes upgrade for payments and handled the on-call "
    "rota during the cutover."
)
KUBERNETES_STATEMENT = "They led the Kubernetes upgrade for payments."


@dataclass(frozen=True)
class _Piece:
    """Shaped like `evidence_retrieval.PassageRef`: content plus a locator."""

    chunk_id: uuid.UUID
    content: str

    @property
    def locator(self) -> str:
        return f"context_chunks:{self.chunk_id}"


@dataclass(frozen=True)
class _Passages:
    """Shaped like `evidence_retrieval.Passages`."""

    pieces: tuple[_Piece, ...] = ()
    degraded: bool = False
    reason: str | None = None


@dataclass
class _Source:
    """A passage source that records every statement it was asked about."""

    answers: dict[str, _Passages] = field(default_factory=dict)
    default: _Passages = field(default_factory=_Passages)
    asked: list[str] = field(default_factory=list)
    in_flight: int = 0
    max_in_flight: int = 0

    async def __call__(self, statement: str) -> _Passages:
        self.asked.append(statement)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)
        self.in_flight -= 1
        return self.answers.get(statement, self.default)


def _piece(text: str) -> _Piece:
    return _Piece(chunk_id=uuid.uuid4(), content=text)


@pytest.mark.asyncio
async def test_a_statement_supported_elsewhere_is_weak_never_supported() -> None:
    """THE RULE. "Kubernetes" is nowhere in the cited answer (invented, so
    unsupported), and another answer says it: the verdict becomes `weak` with
    the passage's locator, and the citation is still not called supportive."""
    found = _piece(ELSEWHERE)
    source = _Source(answers={KUBERNETES_STATEMENT: _Passages(pieces=(found,))})
    verdict = await support.assess(
        KUBERNETES_STATEMENT, [ANSWER], embed=None, passage_source=source
    )
    assert verdict.level == support.LEVEL_WEAK
    assert verdict.reason == support.REASON_ELSEWHERE
    assert verdict.passages == (found.locator,)
    assert verdict.passage_check == support.PASSAGE_FOUND
    assert verdict.note == support.SUPPORT_NOTES[support.REASON_ELSEWHERE]
    assert source.asked == [KUBERNETES_STATEMENT]


@pytest.mark.asyncio
async def test_only_an_unsupported_statement_is_looked_up() -> None:
    """A supported statement and a weak one are left alone: the lookup exists
    to rescue a misattributed claim, never to add weight to a sound one."""
    source = _Source(default=_Passages(pieces=(_piece(ELSEWHERE),)))
    verdicts = await support.assess_many(
        [
            support.SupportRequest(
                key="anchored",
                statement="They owned the rollback runbook for the orders move.",
                excerpts=(ANSWER,),
            ),
            support.SupportRequest(key="searched", statement="No answer here.", excerpts=()),
            support.SupportRequest(
                key="paraphrase",
                statement="They shifted a live system to fresh infrastructure.",
                excerpts=(ANSWER,),
            ),
        ],
        embed=None,
        passage_source=source,
    )
    assert source.asked == []
    assert verdicts["anchored"].level == support.LEVEL_SUPPORTED
    assert verdicts["searched"].reason == support.REASON_SEARCHED_ONLY
    assert verdicts["paraphrase"].reason == support.REASON_SEMANTIC_UNAVAILABLE
    for verdict in verdicts.values():
        assert verdict.passage_check is None
        assert "passages" not in verdict.as_dict()


@pytest.mark.asyncio
async def test_a_lookup_that_finds_nothing_leaves_the_verdict_and_says_it_looked() -> None:
    source = _Source(default=_Passages(pieces=(_piece("We use spreadsheets for budgets."),)))
    verdict = await support.assess(
        KUBERNETES_STATEMENT, [ANSWER], embed=None, passage_source=source
    )
    assert verdict.level == support.LEVEL_UNSUPPORTED
    assert verdict.reason == support.REASON_INVENTED
    assert verdict.passage_check == support.PASSAGE_NOT_FOUND
    assert verdict.passages == ()


@pytest.mark.asyncio
async def test_an_empty_lookup_is_not_found_and_a_degraded_empty_one_is_unavailable() -> None:
    empty = await support.assess(
        KUBERNETES_STATEMENT, [ANSWER], embed=None, passage_source=_Source()
    )
    assert empty.level == support.LEVEL_UNSUPPORTED
    assert empty.passage_check == support.PASSAGE_NOT_FOUND

    down = await support.assess(
        KUBERNETES_STATEMENT,
        [ANSWER],
        embed=None,
        passage_source=_Source(default=_Passages(degraded=True, reason="ToolTimeout")),
    )
    assert down.level == support.LEVEL_UNSUPPORTED
    assert down.passage_check == support.PASSAGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_degraded_lookup_still_judges_the_passages_it_returned(caplog) -> None:
    """A keyword-only result is still the candidate's own words. What the
    degradation changes is the meaning of finding NOTHING."""
    found = _piece(ELSEWHERE)
    source = _Source(
        default=_Passages(pieces=(found,), degraded=True, reason="semantic_unavailable")
    )
    with caplog.at_level("WARNING"):
        rescued = await support.assess(
            KUBERNETES_STATEMENT, [ANSWER], embed=None, passage_source=source
        )
    assert rescued.reason == support.REASON_ELSEWHERE
    assert rescued.passages == (found.locator,)
    assert any("siddhi.support.passages_degraded" in r.getMessage() for r in caplog.records)
    # The log line names the reason and never the statement.
    assert all("Kubernetes" not in r.getMessage() for r in caplog.records)

    unrelated = _Source(
        default=_Passages(
            pieces=(_piece("We use spreadsheets for budgets."),),
            degraded=True,
            reason="semantic_unavailable",
        )
    )
    held = await support.assess(
        KUBERNETES_STATEMENT, [ANSWER], embed=None, passage_source=unrelated
    )
    assert held.level == support.LEVEL_UNSUPPORTED
    assert held.passage_check == support.PASSAGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_paraphrase_elsewhere_needs_the_semantic_tier() -> None:
    """A passage sharing no content term can only be judged semantically. With
    the tier up it can rescue; with the tier down the record says the passage
    could not be judged, never that nothing was found."""
    statement = "They negotiated a vendor contract under budget pressure."
    paraphrase = _piece("I bargained hard with our supplier when money was tight.")
    table = {
        statement: [1.0, 0.0, 0.0],
        ANSWER: [0.0, 1.0, 0.0],
        paraphrase.content: [0.95, 0.05, 0.0],
    }

    async def _embed(texts: list[str]) -> list[list[float]]:
        return [table.get(text, [0.0, 0.0, 1.0]) for text in texts]

    source = _Source(default=_Passages(pieces=(paraphrase,)))
    rescued = await support.assess(
        statement, [ANSWER], embed=_embed, threshold=0.55, passage_source=source
    )
    assert rescued.level == support.LEVEL_WEAK
    assert rescued.reason == support.REASON_ELSEWHERE
    assert rescued.passages == (paraphrase.locator,)

    from app.services.embeddings import EmbeddingError

    calls: list[int] = []

    async def _first_only(texts: list[str]) -> list[list[float]]:
        calls.append(1)
        if len(calls) == 1:
            return await _embed(texts)
        raise EmbeddingError("Voyage endpoint failure: ConnectError")

    unjudged = await support.assess(
        statement, [ANSWER], embed=_first_only, threshold=0.55,
        passage_source=_Source(default=_Passages(pieces=(paraphrase,))),
    )
    assert unjudged.level == support.LEVEL_UNSUPPORTED
    assert unjudged.reason == support.REASON_SEMANTIC_BELOW
    assert unjudged.passage_check == support.PASSAGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_lookups_are_sequential_and_bounded_per_report() -> None:
    """One scoring session serves every lookup, so they never overlap; and a
    report past the ceiling records `not_attempted`, never a silent skip."""
    source = _Source()
    count = support.PASSAGE_LOOKUP_LIMIT + 3
    requests = [
        support.SupportRequest(
            key=index,
            statement=f"They led the Kubernetes rollout number {chr(65 + index)}.",
            excerpts=(ANSWER,),
        )
        for index in range(count)
    ]
    verdicts = await support.assess_many(requests, embed=None, passage_source=source)
    assert source.max_in_flight == 1
    assert len(source.asked) == support.PASSAGE_LOOKUP_LIMIT
    checks = [verdicts[index].passage_check for index in range(count)]
    assert checks[: support.PASSAGE_LOOKUP_LIMIT] == [support.PASSAGE_NOT_FOUND] * (
        support.PASSAGE_LOOKUP_LIMIT
    )
    assert checks[support.PASSAGE_LOOKUP_LIMIT :] == [support.PASSAGE_NOT_ATTEMPTED] * 3
    assert all(verdicts[index].level == support.LEVEL_UNSUPPORTED for index in range(count))


def test_the_lookup_can_only_ever_lift_to_weak_by_construction() -> None:
    """Pinned in the type, not only in the code path: a verdict naming the
    elsewhere reason is `weak` and names its passages, and nothing else
    carries a passage."""
    with pytest.raises(ValueError):
        support.SupportVerdict(
            support.LEVEL_SUPPORTED, support.REASON_ELSEWHERE, passages=("context_chunks:x",)
        )
    with pytest.raises(ValueError):
        support.SupportVerdict(support.LEVEL_WEAK, support.REASON_ELSEWHERE)
    with pytest.raises(ValueError):
        support.SupportVerdict(
            support.LEVEL_UNSUPPORTED,
            support.REASON_INVENTED,
            passages=("context_chunks:x",),
        )
    with pytest.raises(ValueError):
        support.SupportVerdict(
            support.LEVEL_UNSUPPORTED, support.REASON_INVENTED, passage_check="maybe"
        )


@pytest.mark.asyncio
async def test_the_production_seam_binds_this_application_and_passes_the_statement() -> None:
    """`statement_passage_source` is what the orchestrator wires around
    `evidence_retrieval.support_passages_for_statement(session, *, tenant_id,
    link_id, statement, agent=...)`. The scope is fixed once, here."""
    seen: list[tuple] = []

    async def _fetch(session, *, tenant_id, link_id, statement):
        seen.append((session, tenant_id, link_id, statement))
        return _Passages()

    session, tenant_id, link_id = object(), uuid.uuid4(), uuid.uuid4()
    source = support.statement_passage_source(
        _fetch, session, tenant_id=tenant_id, link_id=link_id
    )
    await source("They ran the payments cutover.")
    assert seen == [(session, tenant_id, link_id, "They ran the payments cutover.")]


@pytest.mark.asyncio
async def test_a_policy_refusal_from_the_tool_layer_propagates() -> None:
    """A wiring defect is identical on every retry: recording it as
    `unavailable` would ship a lookup that never ran."""
    from app.services.tools.errors import ToolPolicyError

    async def _refused(session, *, tenant_id, link_id, statement):
        raise ToolPolicyError("retrieve_context", "the report agent does not hold this tool")

    source = support.statement_passage_source(
        _refused, object(), tenant_id=uuid.uuid4(), link_id=uuid.uuid4()
    )
    with pytest.raises(ToolPolicyError):
        await support.assess(KUBERNETES_STATEMENT, [ANSWER], embed=None, passage_source=source)


# ── Through the composer and the read model ──────────────────────────────────


def _rows() -> list[dict]:
    return [
        {
            "category": "must_have",
            "name": "Distributed Systems",
            "grade": "Matching",
            "remark": KUBERNETES_STATEMENT,
        }
    ]


def _exchanges() -> dict:
    return {
        "Distributed Systems": [
            {
                "question": "A migration you owned?",
                "answer": ANSWER,
                "question_id": str(uuid.uuid4()),
                "message_ids": [str(uuid.uuid4())],
            }
        ]
    }


@pytest.mark.asyncio
async def test_a_misattributed_remark_is_marked_not_sent_to_review_and_its_passage_is_readable() -> None:
    found = _piece(ELSEWHERE)
    composed = await siddhi_report.compose_prism(
        dimensions=_rows(),
        evidence_by_item=_exchanges(),
        embed=None,
        passage_source=_Source(answers={KUBERNETES_STATEMENT: _Passages(pieces=(found,))}),
    )
    # Not a review flag: the claim is the candidate's, only its citation is wrong.
    assert composed.unsupported() == []
    assert composed.needs_human_review is False
    stored = {"groups": [], "siddhi": composed.siddhi_namespace()}
    read = trail.read_trail(stored)
    statement = read.statement_for("must_have", "Distributed Systems")
    assert statement.support_passages == (found.locator,)
    assert statement.passage_check == support.PASSAGE_FOUND
    assert read.remark_support_note("must_have", "Distributed Systems") == (
        support.SUPPORT_NOTES[support.REASON_ELSEWHERE]
    )
    shaped = trail.view(
        read,
        {found.locator: trail.ResolvedEvidence(kind=trail.SUPPORTING_PASSAGE, excerpt=ELSEWHERE)},
    )
    [remark] = [
        entry
        for entry in shaped["statements"]
        if entry["section"] == "must_have" and entry["text"] == KUBERNETES_STATEMENT
    ]
    assert remark["evidence"][-1] == {
        "kind": trail.EVIDENCE_KIND_WORDS[trail.SUPPORTING_PASSAGE],
        "question": None,
        "excerpt": ELSEWHERE,
    }
    assert str(found.chunk_id) not in repr(shaped)


@pytest.mark.asyncio
async def test_with_no_passage_source_the_composer_looks_nothing_up() -> None:
    composed = await siddhi_report.compose_prism(
        dimensions=_rows(), evidence_by_item=_exchanges(), embed=None
    )
    [(_, statement)] = composed.unsupported()
    assert statement["support"] == {
        "level": support.LEVEL_UNSUPPORTED,
        "reason": support.REASON_INVENTED,
    }
    assert composed.needs_human_review is True
