"""What Vaada is given to write one question: the contract, never a live row.

THE SAME CONTEXT MITI GRADES AGAINST
-----------------------------------
Appendix A: Vaada "receives JD + skills + hidden assessment context at
conversation start", and Miti "receives the SAME context Vaada used". Both read
it through `assessment_contract.load_contract_for_conversation`, which answers
from the snapshot the start bound to THIS conversation, so a skills edit after
the start (refused anyway, D5) or a later snapshot version can never make the
question and the grade disagree about what was being assessed. The start logs
the digest as `stage=vaada`; grading logs it as `stage=miti`.

WHAT IS HIDDEN STAYS HIDDEN. `skill.evidence_line` (what good evidence looks
like) and `role_summary` are written by Sutra for the model and never shown to
the recruiter or the candidate. They enter a PROMPT and nothing else.

`passages` is the retrieved resume evidence for the skill. It stays EMPTY here:
Evidence RAG reads go through the typed tool layer (Phase 5,
`evidence_retrieval`), and filling this tuple is that phase's one edit to this
module. An empty tuple means "no passages were retrieved", never "the resume is
silent".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.services import assessment_contract


class ContractSkillMissing(LookupError):
    """The question names a skill the conversation's contract does not carry.

    Impossible after the start's digest check (the questions are regenerated
    whenever their contract differs from the locked one), so it is raised
    rather than answered from a live row: a question written against a skill
    nobody is graded on would be graded against nothing.
    """


@dataclass(frozen=True)
class VaadaContext:
    """Everything the question writer knows about the job, for one question."""

    contract: assessment_contract.AssessmentContract
    skill: assessment_contract.ContractSkill
    role_summary: str
    passages: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return self.contract.digest


async def build(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    question: CandidateQuestion,
) -> VaadaContext:
    """The context for `question`, from the contract bound to `conversation`.

    Raises `ContractSkillMissing` when the question's skill is not in the
    contract, and whatever `load_contract_for_conversation` raises for a
    conversation whose binding is broken. The caller degrades to the stored
    question and logs; it never substitutes live rows.
    """
    contract = await assessment_contract.load_contract_for_conversation(
        session, conversation.id
    )
    skill_id: uuid.UUID = question.competency_id
    skill = next((item for item in contract.skills if item.id == skill_id), None)
    if skill is None:
        raise ContractSkillMissing(
            f"question {question.id} probes skill {skill_id}, which contract "
            f"version {contract.version} of job {contract.job_id} does not carry"
        )
    return VaadaContext(
        contract=contract,
        skill=skill,
        role_summary=contract.role_summary,
        passages=(),
    )
