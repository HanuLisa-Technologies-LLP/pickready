"""Vaada, Miti and Siddhi read retrieved evidence through the TOOL layer only.

CONTRACT v4 item 2 and PLAN-p5 C4. `services/evidence_retrieval` is the one
entry point, and every read it makes is a `tools.execute` call, which is where
an agent's reach, the workflow stage, the object's tenant, validation, bounded
attempts and the tenant-keyed cache are enforced. An agent module importing
`services.rag` directly inherits none of that, silently, so:

  ARCHITECTURE. No module that implements Vaada, Miti or Siddhi may import
  `services.rag`, at module scope or inside a function (a deferred import is
  exactly as coupled and harder to see). The set of modules is the named
  directories and files below PLUS whatever `agents/identity` says implements
  those three agents today, so an agent that grows a new module is covered
  without anybody remembering to add it here. The same holds for
  `projects.context`, the direct read `extract_project_evidence` replaces,
  with ONE pending entry the assessment phase removes; the pending list must
  shrink, and a test fails when an entry no longer needs to be on it.

  BEHAVIOUR. Each read names the right tool, agent, stage, object and scope,
  an execution failure comes back degraded with its class recorded and a
  WARNING logged, and a policy refusal RAISES rather than degrading. Then the
  whole path, unfaked, against a real database: passages come only from this
  application's resume or transcript, and a skill's own answers stay out.
"""
from __future__ import annotations

import ast
import dataclasses
import logging
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from app.services import evidence_retrieval as er
from app.services.tools import executor, permissions, policy, schemas
from app.services.tools.errors import (
    ToolExecutionError,
    ToolPermissionError,
    ToolScopeError,
    ToolTimeout,
)
from tests import rag_world

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
SERVICES = APP / "services"

RAG = "app.services.rag"
PROJECT_CONTEXT = "app.services.projects.context"

#: Directories whose every module belongs to Vaada, Miti or Siddhi, or to the
#: assessment pipeline those agents run inside. A directory that does not exist
#: yet (a later phase creates it) is skipped by `_agent_modules`, never failed.
AGENT_DIRS: tuple[str, ...] = (
    "miti",
    "siddhi",
    "assessment_pipeline",
    "assessment_questions",
)

#: Single modules on the same agents' paths.
AGENT_FILES: tuple[str, ...] = (
    "evidence_retrieval.py",
    "interviewer.py",
    "ppi_interview.py",
    "ppi.py",
    "functional_assessment.py",
    "gap_analysis.py",
)

#: Modules that still read `projects.context` directly, each with the phase
#: that removes the read. Must SHRINK to empty: `evidence_retrieval.
#: project_evidence_for_candidate` is the replacement.
PENDING_PROJECT_CONTEXT_READERS: dict[str, str] = {
    "app/services/assessment_questions/generate.py": (
        "Phase 3 (Vaada question writing): replace the direct "
        "candidate_project_context call with "
        "evidence_retrieval.project_evidence_for_candidate in the dispatched "
        "generation step"
    ),
}


def _identity_modules() -> set[str]:
    from app.services.agents import identity

    modules: set[str] = set()
    for agent_id in (identity.VAADA, identity.MITI, identity.SIDDHI):
        agent = identity.get(agent_id)
        modules.update(agent.implemented_by)
        modules.update(agent.activates_to)
    return modules


def _module_path(module: str) -> pathlib.Path | None:
    relative = pathlib.Path(*module.split("."))
    as_file = BACKEND / relative.with_suffix(".py")
    if as_file.exists():
        return as_file
    as_package = BACKEND / relative / "__init__.py"
    return as_package if as_package.exists() else None


def _agent_modules() -> dict[str, pathlib.Path]:
    found: dict[str, pathlib.Path] = {}
    for name in AGENT_DIRS:
        directory = SERVICES / name
        if directory.is_dir():
            for path in directory.rglob("*.py"):
                found[path.relative_to(BACKEND).as_posix()] = path
    for name in AGENT_FILES:
        path = SERVICES / name
        if path.exists():
            found[path.relative_to(BACKEND).as_posix()] = path
    for module in _identity_modules():
        path = _module_path(module)
        if path is not None:
            found[path.relative_to(BACKEND).as_posix()] = path
    return found


def _imports(path: pathlib.Path) -> set[str]:
    """Every module name this file imports, at ANY depth, symbols included.

    `from app.services import rag` names the package through a symbol, so each
    imported NAME is recorded as `<module>.<name>` as well as the module.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _reaches(imports: set[str], target: str) -> list[str]:
    return sorted(name for name in imports if name == target or name.startswith(f"{target}."))


# ── Architecture ─────────────────────────────────────────────────────────────


def test_the_agent_module_set_is_not_vacuous() -> None:
    """A sweep over nothing passes. The set must include the modules the three
    agents run today, and the identity table must still resolve to files."""
    modules = _agent_modules()
    assert "app/services/evidence_retrieval.py" in modules
    assert any(key.startswith("app/services/miti/") for key in modules)
    assert any(key.startswith("app/services/siddhi/") for key in modules)
    unresolved = sorted(m for m in _identity_modules() if _module_path(m) is None)
    assert not unresolved, f"identity names modules with no file: {unresolved}"


def test_no_vaada_miti_or_siddhi_module_reads_the_retrieval_layer_directly() -> None:
    offenders = {
        relative: _reaches(_imports(path), RAG)
        for relative, path in sorted(_agent_modules().items())
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, (
        "These modules import services.rag directly and so bypass the tool "
        "layer's permission, stage, tenant, validation and timeout checks: "
        f"{offenders}. Read through app.services.evidence_retrieval."
    )


def test_no_agent_module_reads_project_evidence_around_the_tool() -> None:
    offenders = sorted(
        relative
        for relative, path in _agent_modules().items()
        if _reaches(_imports(path), PROJECT_CONTEXT)
        and relative not in PENDING_PROJECT_CONTEXT_READERS
    )
    assert not offenders, (
        f"{offenders} read services.projects.context directly. Use "
        "evidence_retrieval.project_evidence_for_candidate, which reads it "
        "through the extract_project_evidence tool."
    )


def test_the_pending_project_context_readers_still_need_to_be_pending() -> None:
    """The allowlist shrinks: an entry that no longer reads the module directly
    must leave the list in the same change, or it is a standing exemption."""
    stale = sorted(
        relative
        for relative in PENDING_PROJECT_CONTEXT_READERS
        if not (BACKEND / relative).exists()
        or not _reaches(_imports(BACKEND / relative), PROJECT_CONTEXT)
    )
    assert not stale, f"remove from PENDING_PROJECT_CONTEXT_READERS: {stale}"


def test_the_entry_point_itself_goes_through_the_tools_package() -> None:
    imports = _imports(SERVICES / "evidence_retrieval.py")
    assert _reaches(imports, "app.services.tools")
    assert not _reaches(imports, RAG)
    assert not _reaches(imports, PROJECT_CONTEXT)


def test_the_source_type_spellings_match_the_chunker() -> None:
    from app.services.rag import chunking

    assert er.SOURCE_RESUME == chunking.SOURCE_RESUME
    assert er.SOURCE_ASSESSMENT == chunking.SOURCE_ASSESSMENT


def test_nothing_numeric_crosses_the_boundary() -> None:
    """A passage carries where it came from and what it says. No score, rank or
    similarity: retrieval orders what an agent reads, never what it is worth."""
    fields = {field.name for field in dataclasses.fields(er.PassageRef)}
    assert fields == {"chunk_id", "source_type", "source_id", "section_type", "content"}
    assert "score" not in schemas.RetrievedPiece.model_fields


# ── Behaviour, with the executor observed ────────────────────────────────────


_SKILL = SimpleNamespace(name="Kafka", evidence_line="Operated Kafka in production.")


class _Recorder:
    def __init__(self, value=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.value = value
        self.error = error

    async def __call__(self, tool, agent, payload=None, *, session=None, context=None):
        self.calls.append(
            {"tool": tool, "agent": agent, "payload": payload, "context": context}
        )
        if self.error is not None:
            raise self.error
        return executor.ToolResult(tool=tool, value=self.value)


def _retrieved(*retrievers: tuple[str, ...]) -> schemas.RetrievedContext:
    return schemas.RetrievedContext(
        query="q",
        pieces=tuple(
            schemas.RetrievedPiece(
                chunk_id=uuid.uuid4(),
                source_type="resume",
                source_id=uuid.uuid4(),
                section_type="prose",
                content=f"passage {index}",
                retrievers=names,
            )
            for index, names in enumerate(retrievers)
        ),
        text="assembled",
    )


def _ids():
    return uuid.uuid4(), uuid.uuid4()


async def test_a_resume_read_is_vaadas_scoped_to_one_profile_in_assessment(
    monkeypatch,
) -> None:
    tenant, link = _ids()
    profile = uuid.uuid4()
    spy = _Recorder(value=_retrieved(("semantic", "keyword")))
    monkeypatch.setattr(executor, "execute", spy)

    passages = await er.resume_passages_for_skill(
        object(), tenant_id=tenant, link_id=link, profile_id=profile, skill=_SKILL
    )

    [call] = spy.calls
    assert call["tool"] == "retrieve_context"
    assert call["agent"] == permissions.AGENT_INTERVIEWER
    request = call["payload"]
    assert request.source_type == "resume"
    assert request.source_ids == (profile,)
    assert request.top_k == er.TOP_K
    assert request.query == "Kafka. Operated Kafka in production."
    context = call["context"]
    assert context.stage == policy.STAGE_ASSESSMENT
    assert context.tenant_id == tenant
    assert context.objects == (policy.ToolObject("application", link, tenant),)
    assert passages.degraded is False
    assert [piece.content for piece in passages.pieces] == ["passage 0"]
    assert passages.text == "assembled"


async def test_a_transcript_read_is_mitis_and_excludes_the_skills_own_answers(
    monkeypatch,
) -> None:
    tenant, link = _ids()
    own = (uuid.uuid4(), uuid.uuid4())
    spy = _Recorder(value=_retrieved(("semantic",)))
    monkeypatch.setattr(executor, "execute", spy)

    await er.transcript_passages_for_skill(
        object(), tenant_id=tenant, link_id=link, skill=_SKILL,
        exclude_message_ids=own,
    )

    [call] = spy.calls
    assert call["agent"] == permissions.AGENT_SCORING
    assert call["payload"].source_type == "assessment"
    assert call["payload"].source_ids == (link,)
    assert call["payload"].exclude_answer_message_ids == own
    assert call["context"].stage == policy.STAGE_ASSESSMENT


async def test_a_support_read_is_siddhis_in_the_reporting_stage(monkeypatch) -> None:
    tenant, link = _ids()
    spy = _Recorder(value=_retrieved(("semantic",)))
    monkeypatch.setattr(executor, "execute", spy)

    await er.support_passages_for_statement(
        object(), tenant_id=tenant, link_id=link,
        statement="The candidate migrated   consumer offsets ahead of time.",
    )

    [call] = spy.calls
    assert call["agent"] == permissions.AGENT_PPI_REPORT
    assert call["payload"].query == (
        "The candidate migrated consumer offsets ahead of time."
    )
    assert call["context"].stage == policy.STAGE_REPORTING


async def test_project_evidence_goes_through_its_own_tool(monkeypatch) -> None:
    tenant, link = _ids()
    candidate = uuid.uuid4()
    spy = _Recorder(
        value=schemas.ProjectEvidence(candidate_id=candidate, text="Project: X")
    )
    monkeypatch.setattr(executor, "execute", spy)

    evidence = await er.project_evidence_for_candidate(
        object(), tenant_id=tenant, link_id=link, candidate_id=candidate
    )

    [call] = spy.calls
    assert call["tool"] == "extract_project_evidence"
    assert call["agent"] == permissions.AGENT_INTERVIEWER
    assert call["payload"].candidate_id == candidate
    assert evidence.text == "Project: X"
    assert evidence.degraded is False


@pytest.mark.parametrize("error", [ToolExecutionError, ToolTimeout])
async def test_an_execution_failure_is_recorded_as_degraded_never_raised(
    monkeypatch, caplog, error
) -> None:
    tenant, link = _ids()
    monkeypatch.setattr(
        executor, "execute", _Recorder(error=error("retrieve_context", "down"))
    )

    with caplog.at_level(logging.WARNING, logger="app.services.evidence_retrieval"):
        passages = await er.transcript_passages_for_skill(
            object(), tenant_id=tenant, link_id=link, skill=_SKILL
        )

    assert passages.degraded is True
    assert passages.reason == error.__name__
    assert passages.pieces == ()
    [record] = [
        r for r in caplog.records
        if r.getMessage().startswith("evidence_retrieval.degraded")
    ]
    assert f"reason={error.__name__}" in record.getMessage()
    assert "Kafka" not in record.getMessage(), "a log line must never carry the query"


async def test_a_failed_project_read_is_not_the_same_as_no_projects(
    monkeypatch,
) -> None:
    tenant, link = _ids()
    monkeypatch.setattr(
        executor,
        "execute",
        _Recorder(error=ToolTimeout("extract_project_evidence", "slow")),
    )
    evidence = await er.project_evidence_for_candidate(
        object(), tenant_id=tenant, link_id=link, candidate_id=uuid.uuid4()
    )
    assert evidence.text == ""
    assert evidence.degraded is True
    assert evidence.reason == "ToolTimeout"


@pytest.mark.parametrize("error", [ToolPermissionError, ToolScopeError])
async def test_a_policy_refusal_is_a_wiring_defect_and_raises(
    monkeypatch, error
) -> None:
    tenant, link = _ids()
    monkeypatch.setattr(
        executor, "execute", _Recorder(error=error("retrieve_context", "refused"))
    )
    with pytest.raises(error):
        await er.resume_passages_for_skill(
            object(), tenant_id=tenant, link_id=link, profile_id=uuid.uuid4(),
            skill=_SKILL,
        )


async def test_keyword_only_passages_are_returned_and_marked_degraded(
    monkeypatch,
) -> None:
    """The semantic retriever missing from every piece is what an embedding
    outage looks like from here. The passages are still useful; the record says
    they were found by keyword."""
    tenant, link = _ids()
    monkeypatch.setattr(
        executor, "execute", _Recorder(value=_retrieved(("keyword",), ("keyword",)))
    )
    passages = await er.resume_passages_for_skill(
        object(), tenant_id=tenant, link_id=link, profile_id=uuid.uuid4(),
        skill=_SKILL,
    )
    assert len(passages.pieces) == 2
    assert passages.degraded is True
    assert passages.reason == er.REASON_SEMANTIC_UNAVAILABLE


async def test_an_empty_skill_never_reaches_the_tool(monkeypatch) -> None:
    tenant, link = _ids()
    spy = _Recorder(value=_retrieved())
    monkeypatch.setattr(executor, "execute", spy)
    passages = await er.resume_passages_for_skill(
        object(), tenant_id=tenant, link_id=link, profile_id=uuid.uuid4(),
        skill=SimpleNamespace(name="  ", evidence_line=""),
    )
    assert spy.calls == []
    assert passages.degraded is True


def test_exclusions_are_refused_outside_a_transcript() -> None:
    with pytest.raises(ValueError):
        schemas.RetrievalRequest(
            query="Kafka",
            source_type="resume",
            source_ids=(uuid.uuid4(),),
            exclude_answer_message_ids=(uuid.uuid4(),),
        )


# ── The whole path, against a real database ──────────────────────────────────


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


async def _in_scope(factory, fn, **kwargs):
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await fn(s, **kwargs)


async def test_resume_passages_come_from_this_applications_resume_only(indexed) -> None:
    factory, fx = indexed
    passages = await _in_scope(
        factory,
        er.resume_passages_for_skill,
        tenant_id=fx.tenant_id,
        link_id=fx.link_id,
        profile_id=fx.profile_id,
        skill=_SKILL,
    )
    assert passages.pieces, "the resume names Kafka; retrieval found nothing"
    assert {piece.source_id for piece in passages.pieces} == {fx.profile_id}
    assert {piece.source_type for piece in passages.pieces} == {"resume"}
    assert all(piece.locator.startswith("context_chunks:") for piece in passages.pieces)


async def test_a_skills_own_answers_stay_out_of_its_transcript_passages(
    indexed,
) -> None:
    """Exchanges 0 and 2 both talk about Kafka. Excluding exchange 0's ANSWER
    removes exactly that chunk and keeps the other."""
    factory, fx = indexed
    rows = await rag_world.chunk_rows(factory, fx.link_id)
    by_ordinal = {row.ordinal: row.id for row in rows}

    everything = await _in_scope(
        factory,
        er.transcript_passages_for_skill,
        tenant_id=fx.tenant_id,
        link_id=fx.link_id,
        skill=_SKILL,
    )
    assert by_ordinal[0] in {piece.chunk_id for piece in everything.pieces}

    without_own = await _in_scope(
        factory,
        er.transcript_passages_for_skill,
        tenant_id=fx.tenant_id,
        link_id=fx.link_id,
        skill=_SKILL,
        exclude_message_ids=(fx.answer_ids[0],),
    )
    returned = {piece.chunk_id for piece in without_own.pieces}
    assert by_ordinal[0] not in returned
    assert by_ordinal[2] in returned, "the exclusion removed more than it named"
    assert {piece.source_id for piece in without_own.pieces} == {fx.link_id}


async def test_an_agent_without_the_grant_is_refused_by_the_real_executor(
    indexed,
) -> None:
    factory, fx = indexed
    with pytest.raises(ToolPermissionError):
        await _in_scope(
            factory,
            er.resume_passages_for_skill,
            tenant_id=fx.tenant_id,
            link_id=fx.link_id,
            profile_id=fx.profile_id,
            skill=_SKILL,
            agent=permissions.AGENT_EMAIL,
        )
