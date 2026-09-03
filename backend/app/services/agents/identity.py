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

THE DEFECT THIS TABLE ONCE CARRIED, AND THE TEST THAT NOW STOPS IT
-------------------------------------------------------------------
`implemented_by` used to read `services/ppi`, `services/matching`,
`services/functional_assessment` and so on: the OLD modules, in a path-like
spelling nothing could resolve. Meanwhile the three-layer framework in
`hiring/`, `miti/` and `siddhi/` was imported by no route and no worker, so
grepping `app/api` and `app/workers` for any of those package names returned
nothing at all.

The consequence was not cosmetic. Every log line and every A2A artifact showed
Bodha, Sutra, Yukti, Vaada, Miti and Siddhi running and succeeding, and anyone
reading a trace would have concluded Part A was live. It was not. A naming table
that points at unreachable code is worse than no naming table, because it is
evidence of the wrong thing.

Two fields and one test replace that. `implemented_by` names what executes
TODAY, in dotted module names a resolver can check, and the test asserts every
entry is transitively importable from `app/api/` or `app/workers/`.
`activates_to` names the Part A modules the agent must run once its stage is
activated, and the same test asserts that any of them which HAS become reachable
also appears in `implemented_by`. Between them the table cannot claim code that
nothing reaches, and cannot lag behind an activation that has already happened.

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

from dataclasses import dataclass

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
    #: The modules that implement it TODAY, as importable dotted names.
    #:
    #: NOT a documentation field. `test_agent_identity` asserts every entry is
    #: transitively importable from `app/api/` or `app/workers/`, which is the
    #: check that would have caught the defect described at the top of this
    #: module: a name pointing at code nothing reaches.
    implemented_by: tuple[str, ...] = ()
    #: The Part A modules this agent must run once its stage is activated.
    #:
    #: Separate from `implemented_by` because the two are not the same claim.
    #: One says "this is what executes when you call this agent today"; the
    #: other says "this is what should". While a stage is mid-activation they
    #: differ, and a single field would have to lie about one of them --  which
    #: is exactly how every agent name came to point at the old modules while
    #: the framework ran nowhere.
    #:
    #: `test_agent_identity` holds the ratchet: the moment a module named here
    #: becomes reachable from a route or a worker, it must also appear in
    #: `implemented_by`. So the table cannot silently lag behind activation.
    activates_to: tuple[str, ...] = ()
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
        implemented_by=(
            # Bodha has TWO mandates and they are at different stages of
            # activation, which is why this list is mixed. The Company DNA half
            # is live: `app/api/company_dna.py` imports both modules. The SWOT
            # half still runs the pre-Part-A intake.
            "app.services.swot_intake",
            "app.services.hiring.company_dna",
            "app.services.hiring.dna_compilation",
            "app.services.hiring.swot_quality",
            "app.services.hiring.situations",
        ),
        activates_to=(
            "app.services.hiring.company_dna",
            "app.services.hiring.dna_compilation",
            "app.services.hiring.swot_quality",
            "app.services.hiring.situations",
        ),
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
        implemented_by=(
            "app.services.ppi",
            "app.services.hiring.scorecard",
            "app.services.hiring.transformation",
            "app.services.hiring.layers",
            "app.services.hiring.department_models",
        ),
        activates_to=(
            "app.services.hiring.transformation",
            "app.services.hiring.layers",
            "app.services.hiring.department_models",
            "app.services.hiring.scorecard",
        ),
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
        implemented_by=(
            "app.services.matching",
            "app.services.matching_categories",
            "app.services.hiring.prescreen",
            "app.services.hiring.ontology",
        ),
        activates_to=(
            "app.services.hiring.prescreen",
            "app.services.hiring.ontology",
        ),
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
        implemented_by=(
            "app.services.interviewer",
            "app.services.ppi_interview",
            "app.services.hiring.evidence_graph",
        ),
        activates_to=("app.services.hiring.evidence_graph",),
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
        implemented_by=(
            "app.services.functional_assessment",
            "app.services.evidence.ledger",
            "app.services.miti.pipeline",
            "app.services.miti.dimensions",
            "app.services.miti.aggregation",
            "app.services.miti.triangulation",
            "app.services.miti.tiering",
            "app.services.miti.claims",
        ),
        activates_to=(
            "app.services.miti.pipeline",
            "app.services.miti.dimensions",
            "app.services.miti.aggregation",
            "app.services.miti.triangulation",
            "app.services.miti.tiering",
            "app.services.miti.claims",
        ),
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
        implemented_by=(
            "app.services.functional_assessment",
            "app.services.gap_analysis",
            "app.services.siddhi.synthesis",
            "app.services.siddhi.citations",
        ),
        activates_to=(
            "app.services.siddhi.synthesis",
            "app.services.siddhi.citations",
        ),
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


def activation_status(reachable: frozenset[str]) -> dict[str, dict[str, object]]:
    """Per agent: what runs, what should, and what has not landed yet.

    `reachable` is supplied by the caller rather than computed here on purpose.
    Working it out means walking the import graph of the whole `app` package,
    which is an AST pass this module has no business owning and which would make
    a naming table depend on a static analyser. `orchestration_checks` owns it,
    and both the test and `eval_agents.py` read the same answer.
    """
    report: dict[str, dict[str, object]] = {}
    for agent_id, agent in AGENTS.items():
        landed = tuple(m for m in agent.activates_to if m in reachable)
        mapped = tuple(m for m in agent.activates_to if m in agent.implemented_by)
        report[agent_id] = {
            "name": agent.name,
            "implemented_by": list(agent.implemented_by),
            "activates_to": list(agent.activates_to),
            "activated": sorted(landed),
            # THE RATCHET, and its granularity is deliberate. It fires when the
            # agent's Part A implementation has become reachable and the table
            # still names none of it -- once per AGENT, not once per file. A
            # per-file rule would go red every time a collaborator lands one
            # more module of a stage that is already correctly mapped, and a
            # guard that goes red for a change that is fine is a guard people
            # start editing rather than reading.
            "activated_but_unmapped": sorted(landed) if landed and not mapped else [],
            "not_yet_reachable": sorted(
                m for m in agent.activates_to if m not in reachable
            ),
            "live_but_unreachable": sorted(
                m for m in agent.implemented_by if m not in reachable
            ),
        }
    return report


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
        if not agent.implemented_by:
            problems.append(f"{agent_id} names no module that implements it")
        for module in agent.implemented_by + agent.activates_to:
            # Dotted, because a path-like `services/ppi` cannot be resolved,
            # imported or checked for reachability, and the previous spelling
            # was exactly that. A shape check here is what stops the next
            # entry being written in the unresolvable form.
            if not module.startswith("app.") or "/" in module:
                problems.append(
                    f"{agent_id} names {module!r}, which is not an importable "
                    "dotted module path"
                )
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
