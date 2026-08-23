"""Which agent may call which tool. Data, checked before the handler runs.

THE SHAPE IS THE SAME ONE THE PRODUCT ALREADY USES
--------------------------------------------------
`require_capability(...)` is how every operational route decides what a person
may do, and the standing rule is that it is never replaced by a role branch
inside the handler. This table is that rule applied to agents: an agent's reach
is a set of names in one readable place, and the executor refuses anything
outside it before the handler is called.

WHY IT MATTERS MORE FOR AN AGENT THAN FOR A PERSON
--------------------------------------------------
A person's request arrives as one HTTP call a router already authorised. An
agent decides at runtime which tool to reach for, partly from text a candidate
wrote. Candidate text is DATA, never instructions (`conversation_guardrails`) --
but the belt-and-braces version of that rule is that even a successful
injection can only name a tool the agent was already granted.

GRANTS ARE THE MINIMUM, NOT THE CONVENIENT SET
-----------------------------------------------
The email agent gets no resume tool. Not because it would misuse one, but
because a lifecycle email has no legitimate reason to hold a parsed resume, and
the narrowest grant that still works is the one that stays honest when somebody
later adds a field to that prompt.
"""
from __future__ import annotations

# ── Agents ──────────────────────────────────────────────────────────────────
# One name per generative surface that calls tools. These are the agents the
# spec names, mapped onto what this product actually has: PPI rather than PFI,
# and gap probes rather than the removed "suggested interview questions".
AGENT_RANKING = "ranking"          # services/matching -- the AI Score
AGENT_PPI_REPORT = "ppi_report"    # services/functional_assessment + ppi
AGENT_EMAIL = "email"              # services/lifecycle_email
AGENT_PROBE = "probe"              # services/gap_analysis
AGENT_INTERVIEWER = "interviewer"  # services/interviewer + ppi_interview
AGENT_JOB_SETUP = "job_setup"      # services/ppi.generate_framework, swot_intake
# Miti, the Tatva Scoring Agent. Split out on 2026-08-23 and NOT merely renamed:
# scoring previously ran inside the report agent's grant, which meant the scorer
# held `extract_jd`. The specification's security boundary says Miti "cannot
# alter the locked assessment framework" and reads answers, matrix and evidence
# -- and a boundary is only real if it is the absence of a tool. Giving scoring
# its own row is what made that enforceable instead of aspirational.
AGENT_SCORING = "scoring"          # services/functional_assessment, per-answer

AGENTS: tuple[str, ...] = (
    AGENT_RANKING,
    AGENT_PPI_REPORT,
    AGENT_EMAIL,
    AGENT_PROBE,
    AGENT_INTERVIEWER,
    AGENT_JOB_SETUP,
    AGENT_SCORING,
)

AGENT_TOOLS: dict[str, frozenset[str]] = {
    AGENT_RANKING: frozenset(
        {"extract_jd", "extract_resume", "retrieve_context", "validate_output"}
    ),
    AGENT_PPI_REPORT: frozenset(
        {
            "extract_jd",
            "extract_resume",
            "extract_assessment",
            "extract_framework",
            "retrieve_context",
            "validate_output",
        }
    ),
    # No resume, no assessment. An email states a decision that was already
    # made; it does not re-read the evidence behind it.
    AGENT_EMAIL: frozenset({"extract_jd", "validate_output"}),
    AGENT_PROBE: frozenset(
        {
            "extract_jd",
            "extract_assessment",
            "extract_framework",
            "retrieve_context",
            "validate_output",
        }
    ),
    AGENT_INTERVIEWER: frozenset(
        {
            "extract_jd",
            "extract_resume",
            "extract_framework",
            "retrieve_context",
            "validate_output",
        }
    ),
    AGENT_JOB_SETUP: frozenset({"extract_jd", "retrieve_context", "validate_output"}),
    # NO `extract_jd`. Miti scores an answer against the LOCKED matrix and its
    # rubric; the JD is what Sutra used to BUILD that matrix, and a scorer that
    # can re-read it can quietly grade against the source rather than against
    # the criteria the candidate was actually assessed on -- which is the one
    # property making two reports on a job comparable.
    AGENT_SCORING: frozenset(
        {
            "extract_assessment",
            "extract_framework",
            "extract_resume",
            "retrieve_context",
            "validate_output",
        }
    ),
}


def granted_tools(agent: str) -> frozenset[str]:
    """Deny by default: an unknown agent holds nothing."""
    return AGENT_TOOLS.get(agent, frozenset())


def is_granted(agent: str, tool: str) -> bool:
    return tool in granted_tools(agent)


def agents_holding(tool: str) -> frozenset[str]:
    return frozenset(agent for agent, held in AGENT_TOOLS.items() if tool in held)
