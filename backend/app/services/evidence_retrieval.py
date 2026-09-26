"""Evidence RAG: the ONE way Vaada, Miti and Siddhi read retrieved evidence.

WHY THIS MODULE EXISTS
----------------------
Retrieval was write-only. `context_chunks` filled up from every parsed resume,
every published JD and every completed assessment, and the only reader of
`rag.retrieval` was a tool (`retrieve_context`) that nothing on the live path
called. The master prompt asks for resume and project passages per skill when
Vaada writes a question, transcript passages per skill when Miti judges one,
and support passages when Siddhi checks a citation, and it asks for them
through the TYPED TOOL LAYER.

The tool layer is the point, not a detour. `services/tools.execute` is where an
agent's reach is enforced (`permissions.AGENT_TOOLS`), where the workflow stage
and the object's tenant are checked BEFORE any row is read (`tools.policy`),
where the payload and the result are validated, where every attempt is bounded
by a predicting deadline, where compensation is stripped from the SHAPE, and
where a cached result is keyed on the tenant. An agent that imported
`services.rag` directly would inherit none of that, silently, and
`tests/test_evidence_retrieval_through_tools.py` fails the build on the first
Vaada, Miti or Siddhi module that does.

RETRIEVAL IS A RANKING PRIOR, AND NOTHING NUMERIC CROSSES THIS LINE
-------------------------------------------------------------------
A `PassageRef` carries a chunk id, where it came from and its verbatim text.
No score, no rank value, no similarity. Passages decide what an agent READS
first; they never decide what an answer is worth, and a scorer handed a
relevance number is one careless line from grading on it
(`tests/test_retrieval_scoring_isolation.py`).

A FAILED READ IS RECORDED, NEVER SILENT, AND NEVER FATAL
--------------------------------------------------------
Tools RAISE and loops DEGRADE (the 2026-08-18 rule). This module is where a
retrieval failure becomes a degradation: an execution failure, a timeout, a
malformed result or an input the tool refused comes back as
`degraded=True` with the exception CLASS as the reason, logged at WARNING as
`evidence_retrieval.degraded`, and the conversation or scoring run continues
without the passages. Question writing and grading both worked before
retrieval existed; losing it costs context, never the candidate's assessment.

A POLICY REFUSAL IS DIFFERENT AND IT RAISES. `ToolPolicyError` (an agent that
does not hold the tool, a stage the tool is not available in, an object owned
by another tenant) and `ToolNotFound` are WIRING defects, deterministic and
identical on every retry. Recording one as "degraded" would ship a caller that
never retrieved anything with a log line nobody reads, which is the shape the
2026-09-09 audit found `index_document` in. They propagate.

Also recorded: a result whose pieces all came from the keyword half. The
semantic retriever missing from every piece is the visible form of an
embedding outage (`RetrievedPiece.retrievers`), and "we found this by keyword"
is a weaker statement than "we found this by meaning" that a caller may want
to say out loud.

WHO CALLS WHAT (the wiring the assessment and grading phases add)
-----------------------------------------------------------------
Nothing on the live path calls this module yet, and
`tests/test_ai_reachability.py` records it as NOT_LIVE until something does:

  * Vaada, in the DISPATCHED question-generation step
    (`assessment_questions/generate.py`, never per interactive turn):
    `resume_passages_for_skill` for each contract skill, and
    `project_evidence_for_candidate` once, replacing the direct
    `projects.context.candidate_project_context` import.
  * Miti, in `items.evaluate_skill`, BEFORE the model call, for Behavioural and
    Must-have skills: `transcript_passages_for_skill` with the skill's own
    answer message ids in `exclude_message_ids`.
  * Siddhi, when checking whether a statement is supported:
    `support_passages_for_statement`.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.tools import executor, permissions, policy, schemas
from app.services.tools.errors import ToolError, ToolNotFound, ToolPolicyError

logger = logging.getLogger(__name__)

#: The `RetrievalRequest.source_type` values this module asks for. Restated
#: rather than imported from `rag.chunking`, because importing ANY part of
#: `services.rag` from here is the bypass this module exists to close;
#: `tests/test_evidence_retrieval_through_tools.py` asserts they equal
#: `chunking.SOURCE_RESUME` and `chunking.SOURCE_ASSESSMENT`, so the two
#: spellings cannot drift.
SOURCE_RESUME = "resume"
SOURCE_ASSESSMENT = "assessment"

#: How many passages one skill read returns, and how many tokens the assembled
#: block may use. Small on purpose: these ride inside a prompt that already
#: carries the contract, the question and the answer, and three well-chosen
#: passages beat eight loosely related ones.
TOP_K = 3
MAX_TOKENS = 600

#: `RetrievalRequest.query` refuses anything longer. A skill name plus its
#: evidence line is far shorter; the cap is stated here so an unusually long
#: evidence line is trimmed rather than refused.
_QUERY_MAX_CHARS = 2000

#: The retriever name `rag.retrieval` stamps on a piece the semantic half found.
_SEMANTIC = "semantic"

#: Reasons, as the log line and `Passages.reason` carry them.
REASON_SEMANTIC_UNAVAILABLE = "semantic_unavailable"

#: The object kind the policy engine checks the tenant of.
_APPLICATION = "application"


class EvidenceSkill(Protocol):
    """What a skill must offer to be retrieved for.

    `assessment_contract.ContractSkill` satisfies it; so does anything else
    carrying the two fields, which keeps this module from importing the
    contract (a scorer-adjacent module) just to name a type.
    """

    name: str
    evidence_line: str


@dataclass(frozen=True)
class PassageRef:
    """One retrieved passage, verbatim, with where it came from. No score."""

    chunk_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    section_type: str
    content: str

    @property
    def locator(self) -> str:
        """The citation locator Siddhi records for this passage."""
        return f"context_chunks:{self.chunk_id}"


@dataclass(frozen=True)
class Passages:
    """What a retrieval returned, and whether it could be trusted to be whole."""

    pieces: tuple[PassageRef, ...] = ()
    #: The assembled, budgeted, labelled block, ready for a prompt.
    text: str = ""
    degraded: bool = False
    #: The exception class, or `REASON_SEMANTIC_UNAVAILABLE`. None when whole.
    reason: str | None = None


@dataclass(frozen=True)
class ProjectEvidenceText:
    """The derived project evidence block, and whether the read succeeded.

    A `str` alone could not tell "this candidate submitted no projects" (a
    normal state, never a penalty) from "the read failed", and a caller that
    cannot tell them apart would write the second as the first.
    """

    text: str = ""
    degraded: bool = False
    reason: str | None = None


def skill_query(skill: EvidenceSkill) -> str:
    """The retrieval query for one skill: its name, then what evidence looks like.

    The evidence line is the hidden "what good evidence looks like" sentence
    from the contract, and it is what makes the query about the SKILL rather
    than about its label: "Python" alone retrieves every paragraph that names
    the language; the evidence line retrieves the ones that show it used.
    """
    name = " ".join(str(skill.name or "").split())
    line = " ".join(str(skill.evidence_line or "").split())
    query = f"{name}. {line}" if line else name
    return query[:_QUERY_MAX_CHARS]


def _context(
    *, tenant_id: uuid.UUID, link_id: uuid.UUID, stage: str
) -> policy.ToolContext:
    return policy.ToolContext(
        tenant_id=tenant_id,
        stage=stage,
        objects=(policy.ToolObject(_APPLICATION, link_id, tenant_id),),
    )


def _degraded_log(*, agent: str, tool: str, link_id: uuid.UUID, reason: str) -> None:
    # Identifiers and the class name only. Never the query, never a passage:
    # both carry a candidate's words, and this line is read far more widely
    # than the database it describes.
    logger.warning(
        "evidence_retrieval.degraded agent=%s tool=%s link_id=%s reason=%s",
        agent,
        tool,
        link_id,
        reason,
    )


async def _retrieve(
    session: AsyncSession,
    *,
    agent: str,
    stage: str,
    tenant_id: uuid.UUID,
    link_id: uuid.UUID,
    query: str,
    source_type: str,
    source_id: uuid.UUID,
    exclude_message_ids: tuple[uuid.UUID, ...] = (),
) -> Passages:
    tool = "retrieve_context"
    if not query.strip():
        # A skill with no name is a contract defect upstream; asking the tool
        # would be refused as invalid input anyway. Recorded the same way.
        _degraded_log(agent=agent, tool=tool, link_id=link_id, reason="empty_query")
        return Passages(degraded=True, reason="empty_query")
    try:
        result = await executor.execute(
            tool,
            agent,
            schemas.RetrievalRequest(
                query=query,
                source_type=source_type,
                source_ids=(source_id,),
                top_k=TOP_K,
                max_tokens=MAX_TOKENS,
                exclude_answer_message_ids=tuple(exclude_message_ids),
            ),
            session=session,
            context=_context(tenant_id=tenant_id, link_id=link_id, stage=stage),
        )
    except (ToolPolicyError, ToolNotFound):
        raise
    except ToolError as exc:
        reason = type(exc).__name__
        _degraded_log(agent=agent, tool=tool, link_id=link_id, reason=reason)
        return Passages(degraded=True, reason=reason)

    context = result.value
    assert isinstance(context, schemas.RetrievedContext)
    pieces = tuple(
        PassageRef(
            chunk_id=piece.chunk_id,
            source_type=piece.source_type,
            source_id=piece.source_id,
            section_type=piece.section_type,
            content=piece.content,
        )
        for piece in context.pieces
    )
    if pieces and not any(_SEMANTIC in piece.retrievers for piece in context.pieces):
        _degraded_log(
            agent=agent, tool=tool, link_id=link_id, reason=REASON_SEMANTIC_UNAVAILABLE
        )
        return Passages(
            pieces=pieces,
            text=context.text,
            degraded=True,
            reason=REASON_SEMANTIC_UNAVAILABLE,
        )
    return Passages(pieces=pieces, text=context.text)


async def resume_passages_for_skill(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    link_id: uuid.UUID,
    profile_id: uuid.UUID,
    skill: EvidenceSkill,
    agent: str = permissions.AGENT_INTERVIEWER,
) -> Passages:
    """The parts of THIS application's resume that bear on one skill.

    Vaada, while writing questions in the dispatched generation step. Scoped
    to the one profile the application was submitted with: `source_ids` is
    what keeps retrieval to the person being assessed, and RLS alone would
    only keep it inside the tenant.
    """
    return await _retrieve(
        session,
        agent=agent,
        stage=policy.STAGE_ASSESSMENT,
        tenant_id=tenant_id,
        link_id=link_id,
        query=skill_query(skill),
        source_type=SOURCE_RESUME,
        source_id=profile_id,
    )


async def transcript_passages_for_skill(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    link_id: uuid.UUID,
    skill: EvidenceSkill,
    exclude_message_ids: tuple[uuid.UUID, ...] = (),
    agent: str = permissions.AGENT_SCORING,
) -> Passages:
    """Exchanges from the candidate's OTHER answers that bear on one skill.

    Miti, before judging the skill. `exclude_message_ids` names the skill's
    own ANSWER messages: those are what is being graded, and retrieving them
    again would count one answer as its own corroboration.
    """
    return await _retrieve(
        session,
        agent=agent,
        stage=policy.STAGE_ASSESSMENT,
        tenant_id=tenant_id,
        link_id=link_id,
        query=skill_query(skill),
        source_type=SOURCE_ASSESSMENT,
        source_id=link_id,
        exclude_message_ids=exclude_message_ids,
    )


async def support_passages_for_statement(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    link_id: uuid.UUID,
    statement: str,
    agent: str = permissions.AGENT_PPI_REPORT,
) -> Passages:
    """Transcript passages that bear on one report statement.

    Siddhi, checking that a statement is SUPPORTED rather than merely that a
    pointer exists. The statement is the query; the transcript of this one
    application is the scope. What counts as support is Siddhi's decision,
    made on the verbatim passages returned here.
    """
    query = " ".join(str(statement or "").split())[:_QUERY_MAX_CHARS]
    return await _retrieve(
        session,
        agent=agent,
        stage=policy.STAGE_REPORTING,
        tenant_id=tenant_id,
        link_id=link_id,
        query=query,
        source_type=SOURCE_ASSESSMENT,
        source_id=link_id,
    )


async def project_evidence_for_candidate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    link_id: uuid.UUID,
    candidate_id: uuid.UUID,
    agent: str = permissions.AGENT_INTERVIEWER,
) -> ProjectEvidenceText:
    """The candidate's derived project evidence, for question writing.

    Through `extract_project_evidence`, which only Vaada holds and only in the
    assessment stage: project evidence informs what is ASKED, and a scorer
    that could read it would grade the projects rather than the answers.
    """
    tool = "extract_project_evidence"
    try:
        result = await executor.execute(
            tool,
            agent,
            schemas.ProjectEvidenceRequest(candidate_id=candidate_id),
            session=session,
            context=_context(
                tenant_id=tenant_id, link_id=link_id, stage=policy.STAGE_ASSESSMENT
            ),
        )
    except (ToolPolicyError, ToolNotFound):
        raise
    except ToolError as exc:
        reason = type(exc).__name__
        _degraded_log(agent=agent, tool=tool, link_id=link_id, reason=reason)
        return ProjectEvidenceText(degraded=True, reason=reason)
    evidence = result.value
    assert isinstance(evidence, schemas.ProjectEvidence)
    return ProjectEvidenceText(text=evidence.text)


__all__ = [
    "EvidenceSkill",
    "MAX_TOKENS",
    "PassageRef",
    "Passages",
    "ProjectEvidenceText",
    "REASON_SEMANTIC_UNAVAILABLE",
    "TOP_K",
    "project_evidence_for_candidate",
    "resume_passages_for_skill",
    "skill_query",
    "support_passages_for_statement",
    "transcript_passages_for_skill",
]
