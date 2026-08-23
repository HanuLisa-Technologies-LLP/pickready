"""The six named agents that execute the Tatva Assessment.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
----------------------------------------------------
This is an IDENTITY table, not a seventh execution framework. Every one of
these six agents already existed and already ran inside
`agent_loop.run_loop`, already reached data through `tools.execute`, and
already had its reach pinned in `tools.permissions.AGENT_TOOLS`. What was
missing was that none of it had a NAME, so the product spoke about "Bodha" and
"Sutra" and the code spoke about `job_setup`, and there was no single place
where the two met.

Adding a parallel dispatcher keyed on the new names would have produced two
routers that must agree and eventually will not. So the runtime id stays what it
was, and this table carries the name, the meaning, the trigger, the portal the
output lands in, and the A2A skill list -- each row pointing AT the existing
runtime id rather than replacing it. `validate_identities` asserts that pointer
resolves, and it runs as a test rather than at import: a startup crash over a
naming table is worse than a red test, because it takes production down for a
change that affects nothing operational.

WHY THE ACTIVATION ORDER IS DATA
--------------------------------
Because two of the six run in PARALLEL and that is a product decision, not an
implementation detail. Sutra and Yukti both consume the finalised JD and neither
consumes the other's output, so serialising them would add a whole matrix
generation to the wall clock before the first AI Score appears. `sequence()`
returns the order with the parallel pair grouped, so anything that renders or
schedules the pipeline reads the same shape rather than restating it.

MITI IS NOT MERELY A RENAME
---------------------------
Five of these six map onto an existing runtime id. Miti did not: scoring lived
inside `functional_assessment` and had no separate identity, no separate tool
grant, and therefore no way to say "the scorer may read the transcript and the
matrix, and may not touch the JD". It gets its own id here and its own row in
the permission table, which is what makes the security boundary in the
specification enforceable rather than aspirational.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.tools import permissions

# ── Portals an artifact can land in ─────────────────────────────────────────
PORTAL_CUSTOMER = "customer"
PORTAL_CANDIDATE = "candidate"
#: Not a portal at all: the agent produces no user-visible surface of its own.
PORTAL_INTERNAL = "internal"


@dataclass(frozen=True)
class Agent:
    #: The product name. What the specification, the UI and a conversation with
    #: the client all use.
    name: str
    #: What the Sanskrit word means. Carried because it is the reason the name
    #: was chosen, and a reader three years from now has no other way to know.
    meaning: str
    #: The role, in the product's own words.
    role: str
    #: The runtime id in `tools.permissions.AGENT_TOOLS` and
    #: `orchestration.router.ROUTES`. NOT renamed to the Sanskrit name: a beat
    #: entry, a queued message and a worker registration cannot all be changed
    #: atomically during a rolling deploy, and this id appears in persisted
    #: traces that must stay readable.
    runtime_id: str
    #: What starts it.
    trigger: str
    #: Where its output is delivered.
    portal: str
    #: The A2A skills it publishes (spec 16.3). A capability list, not a
    #: dispatch table: nothing here calls a function by name.
    skills: tuple[str, ...]
    #: The modules that implement it, for a reader trying to find the code.
    implemented_by: tuple[str, ...] = ()
    #: Artifact types it produces and consumes. The consumer side is what
    #: `artifacts.verify_for_consumer` checks against.
    produces: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()


BODHA = "bodha"
SUTRA = "sutra"
YUKTI = "yukti"
VAADA = "vaada"
MITI = "miti"
SIDDHI = "siddhi"


AGENTS: dict[str, Agent] = {
    BODHA: Agent(
        name="Bodha",
        meaning="Understanding / insight",
        role="Hiring Manager SWOT Intake Agent",
        runtime_id=permissions.AGENT_JOB_SETUP,
        trigger="Immediately after the recruiter saves the finalised job description.",
        portal=PORTAL_CUSTOMER,
        skills=("collect_swot", "summarize_role_context", "validate_swot_completeness"),
        implemented_by=("services/swot_intake",),
        produces=("swot_evidence",),
        consumes=("job_description",),
    ),
    SUTRA: Agent(
        name="Sutra",
        meaning="Thread / framework",
        role="Tatva Matrix Agent",
        runtime_id=permissions.AGENT_JOB_SETUP,
        trigger=(
            "After Bodha completes the SWOT intake. Runs in parallel with Yukti."
        ),
        portal=PORTAL_CUSTOMER,
        skills=("build_tatva_matrix", "validate_matrix_coverage", "publish_locked_matrix"),
        implemented_by=("services/ppi",),
        produces=("tatva_matrix",),
        consumes=("job_description", "swot_evidence"),
    ),
    YUKTI: Agent(
        name="Yukti",
        meaning="Logical reasoning / right fit",
        role="Matching Agent",
        runtime_id=permissions.AGENT_RANKING,
        trigger=(
            "In parallel with Sutra during job setup, and again the moment a "
            "resume is uploaded against a live job."
        ),
        portal=PORTAL_CUSTOMER,
        skills=("build_matching_categories", "score_resume_fit", "retrieve_matching_evidence"),
        implemented_by=("services/matching", "services/matching_categories"),
        produces=("ai_score",),
        consumes=("job_description", "resume"),
    ),
    VAADA: Agent(
        name="Vaada",
        meaning="Dialogue / exchange",
        role="Candidate Conversational Agent",
        runtime_id=permissions.AGENT_INTERVIEWER,
        trigger="Immediately after the candidate completes the profile form.",
        portal=PORTAL_CANDIDATE,
        skills=("plan_conversation", "generate_probe", "request_followup", "close_assessment"),
        implemented_by=("services/interviewer", "services/ppi_interview"),
        produces=("answer_event",),
        consumes=("tatva_matrix", "job_description", "evidence_gap"),
    ),
    MITI: Agent(
        name="Miti",
        meaning="Precise measurement",
        role="Tatva Scoring Agent",
        runtime_id=permissions.AGENT_SCORING,
        trigger=(
            "Real time, throughout Vaada's conversation. Invisible to the candidate."
        ),
        # It has no surface of its own, and that is the product requirement:
        # the candidate never sees a score, a grade or a signal, during or
        # after. An agent with no portal cannot leak one.
        portal=PORTAL_INTERNAL,
        skills=(
            "score_answer",
            "update_evidence_state",
            "detect_contradiction",
            "validate_assessment_completion",
        ),
        implemented_by=("services/functional_assessment", "services/evidence"),
        produces=("scoring_state", "evidence_gap"),
        consumes=("tatva_matrix", "answer_event"),
    ),
    SIDDHI: Agent(
        name="Siddhi",
        meaning="Accomplishment / conclusive result",
        role="PRISM Report Synthesis Agent",
        runtime_id=permissions.AGENT_PPI_REPORT,
        trigger="The moment Vaada's conversation ends and Miti has finished scoring.",
        # Customer portal ONLY. The candidate never sees the report or any
        # component of it.
        portal=PORTAL_CUSTOMER,
        skills=("build_prism_report", "generate_gap_analysis", "verify_report_consistency"),
        implemented_by=("services/functional_assessment", "services/gap_analysis"),
        produces=("prism_report",),
        consumes=("tatva_matrix", "scoring_state", "ai_score"),
    ),
}

#: Activation order. A tuple of tuples: an inner tuple with more than one entry
#: runs CONCURRENTLY, which is a product decision (Sutra and Yukti both consume
#: the finalised JD and neither consumes the other) rather than a scheduling
#: convenience.
ACTIVATION: tuple[tuple[str, ...], ...] = (
    (BODHA,),
    (SUTRA, YUKTI),
    (VAADA, MITI),
    (SIDDHI,),
)


class UnknownAgent(KeyError):
    """Never guessed at. A name that is not one of the six is a programming
    error, and defaulting to some agent would give it that agent's tool grant."""


def get(agent_id: str) -> Agent:
    try:
        return AGENTS[agent_id]
    except KeyError as exc:
        raise UnknownAgent(agent_id) from exc


def runtime_id(agent_id: str) -> str:
    """The permission/routing id this named agent executes as."""
    return get(agent_id).runtime_id


def granted_tools(agent_id: str) -> frozenset[str]:
    """Reach, resolved through the SAME table every other caller reads.

    Deliberately not a second copy of the grants. A named agent's reach IS its
    runtime agent's reach; two tables would drift, and the drift would be a
    silently widened permission.
    """
    return permissions.granted_tools(runtime_id(agent_id))


def sequence() -> tuple[tuple[Agent, ...], ...]:
    return tuple(tuple(get(name) for name in step) for step in ACTIVATION)


def agent_card(agent_id: str) -> dict:
    """The A2A Agent Card (spec 16.2).

    Capability and service metadata only. It deliberately carries no endpoint
    credentials, no prompt, no memory handle and no tool implementation detail:
    the point of publishing a card is that another agent can discover what this
    one DOES without reaching into how it does it.
    """
    agent = get(agent_id)
    return {
        "id": agent_id,
        "name": agent.name,
        "meaning": agent.meaning,
        "role": agent.role,
        "trigger": agent.trigger,
        "portal": agent.portal,
        "skills": list(agent.skills),
        "produces": list(agent.produces),
        "consumes": list(agent.consumes),
        # The reach, as data. Published because a consumer verifying an artifact
        # should be able to see that its producer could not have read something
        # it claims to have read.
        "granted_tools": sorted(granted_tools(agent_id)),
        "implemented_by": list(agent.implemented_by),
    }


def agent_cards() -> list[dict]:
    return [agent_card(agent_id) for agent_id in AGENTS]


def validate_identities() -> list[str]:
    """Problems with the table, as strings. Empty when it is sound.

    Run as a test rather than at import. Checks the three things that would be
    silently wrong: a runtime id nothing routes to, an agent that consumes an
    artifact type nobody produces, and a duplicate produced type (two producers
    for one artifact means a consumer cannot know whose version it holds).
    """
    problems: list[str] = []
    produced: dict[str, str] = {}

    for agent_id, agent in AGENTS.items():
        if agent.runtime_id not in permissions.AGENTS:
            problems.append(
                f"{agent_id} executes as {agent.runtime_id!r}, which is not in "
                "tools.permissions.AGENTS"
            )
        if not agent.skills:
            problems.append(f"{agent_id} publishes no A2A skills")
        for artifact_type in agent.produces:
            if artifact_type in produced:
                problems.append(
                    f"{artifact_type!r} is produced by both {produced[artifact_type]} "
                    f"and {agent_id}; a consumer cannot tell whose version it holds"
                )
            produced[artifact_type] = agent_id

    # Inputs that arrive from outside the agent pipeline rather than from
    # another agent. Named explicitly so an unresolvable consumer entry is a
    # real problem rather than noise.
    external = {"job_description", "resume"}
    for agent_id, agent in AGENTS.items():
        for artifact_type in agent.consumes:
            if artifact_type not in produced and artifact_type not in external:
                problems.append(
                    f"{agent_id} consumes {artifact_type!r}, which no agent produces"
                )

    ordered = [name for step in ACTIVATION for name in step]
    if sorted(ordered) != sorted(AGENTS):
        problems.append("ACTIVATION does not cover exactly the six agents")

    return problems
