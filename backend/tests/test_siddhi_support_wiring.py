"""Siddhi's support check reaches the candidate's other answers through the tool layer.

Lands with the wiring, once WP5-C (Siddhi) and WP5-E (`evidence_retrieval`)
are both merged: before that the production seam had nothing real to bind, and
a test that could only skip is not a check.

  * the seam's keyword parameters exist on the real retrieval entry point, so
    the wiring cannot drift from the function it names;
  * the scoring orchestrator hands Siddhi exactly that function, bound to the
    run's own session, tenant and application;
  * END TO END against a real database, through the REAL executor, policy and
    retriever: a remark pinned to the wrong answer is lifted to `weak` by the
    answer that does support it, the stored verdict names that answer's chunk,
    and the read model resolves it to the candidate's words;
  * an agent without the grant is refused by the real executor, and that
    refusal propagates through the seam rather than reading as "unavailable".
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from app.services import evidence_retrieval
from app.services.siddhi import report as siddhi_report
from app.services.siddhi import support, trail

from tests import rag_world

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_the_seam_matches_the_retrieval_entry_points_signature() -> None:
    parameters = inspect.signature(
        evidence_retrieval.support_passages_for_statement
    ).parameters
    assert list(parameters)[0] == "session"
    for name in ("tenant_id", "link_id", "statement"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    # The seam binds nothing else, so every other parameter must default.
    for name, parameter in parameters.items():
        if name not in {"session", "tenant_id", "link_id", "statement"}:
            assert parameter.default is not inspect.Parameter.empty, name


def test_the_orchestrator_hands_siddhi_the_retrieval_entry_point() -> None:
    """Read from the source, because running `synthesis_node` needs a scored
    application; what this pins is the ONE call and what it is bound to."""
    source = (BACKEND / "app/services/functional_assessment.py").read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_gap_analysis"
    ]
    assert len(calls) == 1
    [seam] = [kw.value for kw in calls[0].keywords if kw.arg == "passage_source"]
    assert isinstance(seam, ast.Call)
    assert isinstance(seam.func, ast.Attribute)
    assert seam.func.attr == "statement_passage_source"
    fetch = seam.args[0]
    assert isinstance(fetch, ast.Attribute)
    assert fetch.attr == "support_passages_for_statement"
    assert isinstance(fetch.value, ast.Name) and fetch.value.id == "evidence_retrieval"
    assert {kw.arg for kw in seam.keywords} == {"tenant_id", "link_id"}


# ── End to end, against a real database ──────────────────────────────────────

SKILL = "Streaming platform"

#: Pinned to exchange 1 (a disagreement with an architect), which names no
#: Kafka and no consumer offsets. Exchange 2 says exactly this.
MISATTRIBUTED = "They migrated the consumer offsets ahead of the Kafka cutover."


@pytest.fixture
async def indexed(monkeypatch):
    rag_world.disable_prefixes(monkeypatch)
    engine, factory = await rag_world.factory_or_skip()
    fx = rag_world.World()
    await rag_world.seed(factory, fx)
    await rag_world.index_all(factory, fx)
    try:
        yield factory, fx
    finally:
        await rag_world.cleanup(factory, fx)
        await engine.dispose()


def _rows() -> list[dict]:
    return [
        {
            "category": "must_have",
            "name": SKILL,
            "grade": "Matching",
            "remark": MISATTRIBUTED,
        }
    ]


def _exchanges(fx) -> dict:
    question, answer = rag_world.EXCHANGES[1]
    return {
        SKILL: [
            {
                "question": question,
                "answer": answer,
                "question_id": "",
                "message_ids": [str(fx.answer_ids[1])],
            }
        ]
    }


async def _compose(session, fx):
    return await siddhi_report.compose_prism(
        dimensions=_rows(),
        evidence_by_item=_exchanges(fx),
        embed=None,
        passage_source=support.statement_passage_source(
            evidence_retrieval.support_passages_for_statement,
            session,
            tenant_id=fx.tenant_id,
            link_id=fx.link_id,
        ),
    )


async def test_a_misattributed_remark_is_lifted_by_the_answer_that_supports_it(
    indexed,
) -> None:
    from app.core.db import superadmin_scope

    factory, fx = indexed
    chunks = {row.ordinal: row.id for row in await rag_world.chunk_rows(factory, fx.link_id)}

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                composed = await _compose(s, fx)

    [(section, statement)] = [
        pair for pair in composed.statements() if pair[1]["kind"] == "finding"
    ]
    verdict = statement["support"]
    assert verdict["level"] == support.LEVEL_WEAK
    assert verdict["reason"] == support.REASON_ELSEWHERE
    assert verdict["passage_check"] == support.PASSAGE_FOUND
    # The chunk that SAYS it (exchange 2), and not the one the remark cites.
    assert f"context_chunks:{chunks[2]}" in verdict["passages"]
    assert f"context_chunks:{chunks[1]}" not in verdict["passages"]
    # Misattributed, not unsupported: marked, and not sent to review for it.
    assert not composed.unsupported()
    assert statement["text"] == MISATTRIBUTED

    stored = {"groups": [], "siddhi": composed.siddhi_namespace()}
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                view = await trail.citation_view(
                    s, stored, link_id=fx.link_id,
                    chunk_source_ids=(fx.link_id, fx.profile_id),
                )
    [shown] = [entry for entry in view["statements"] if entry["kind"] == "finding"]
    assert shown["support"] == support.SUPPORT_NOTES[support.REASON_ELSEWHERE]
    supporting = [
        entry for entry in shown["evidence"]
        if entry["kind"] == trail.EVIDENCE_KIND_WORDS[trail.SUPPORTING_PASSAGE]
    ]
    assert supporting and "consumer offsets" in (supporting[0]["excerpt"] or "")


async def test_an_agent_without_the_grant_is_refused_through_the_seam(indexed) -> None:
    """A policy refusal is a wiring defect identical on every retry, so it
    RAISES through the seam; recording it as `unavailable` would ship a lookup
    that never ran."""
    from app.core.db import superadmin_scope
    from app.services.tools import permissions
    from app.services.tools.errors import ToolPermissionError

    factory, fx = indexed

    async def _as_email_agent(session, **kwargs):
        return await evidence_retrieval.support_passages_for_statement(
            session, agent=permissions.AGENT_EMAIL, **kwargs
        )

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                with pytest.raises(ToolPermissionError):
                    await siddhi_report.compose_prism(
                        dimensions=_rows(),
                        evidence_by_item=_exchanges(fx),
                        embed=None,
                        passage_source=support.statement_passage_source(
                            _as_email_agent, s,
                            tenant_id=fx.tenant_id, link_id=fx.link_id,
                        ),
                    )
