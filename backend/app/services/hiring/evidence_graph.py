"""Department Evidence Graphs (spec-doc5 §A.3, Runbook Part VI).

    "question generation should now draw on the relevant Department Evidence
     Graph for this role's department, not a generic question bank, and should
     behave like the triangulation model ... treat each candidate claim as
     something to corroborate across sources rather than take at face value,
     and route toward the specific evidence sources Sutra's matrix flagged as
     required for each competency."

WHAT A GRAPH NODE ACTUALLY IS
------------------------------
Not a question. A question is what Vaada writes fresh, per candidate, in their
own conversational context -- that has not changed and must not, because the
per-candidate question is what makes the product adaptive.

A node is the thing ONE question can establish, plus what would corroborate it,
plus what a hollow answer to it looks like. Vaada uses it to decide WHAT to
establish next and WHERE the corroboration should come from; the words are still
Vaada's.

THE DIFFERENCE FROM A QUESTION BANK, AND WHY IT MATTERS
---------------------------------------------------------
A question bank asks the same words of everybody. This codebase deleted its
preset technical bank on 2026-08-06 for exactly that reason, and nothing here
reintroduces one: `test_ppi.py` and `test_conversation_flow.py` pin that a
question is generated per candidate.

What the graph adds is COVERAGE STRUCTURE. Without it, a conversation can spend
five questions establishing the same thing five ways and never ask what would
actually corroborate it -- which is the failure mode of a fluent interviewer with
no plan. `next_target` is the plan: given what has been established, what is the
highest-value unestablished node, and what would confirm it.

THE HOLLOW-ANSWER TELL IS THE MOST USEFUL FIELD
-------------------------------------------------
`hollow_tell` is what an answer looks like when somebody has read about the work
rather than done it. It is not a lie detector and it is not scored -- it is
routed to `follow_up`, which is the honest response: a candidate who gives a
textbook answer may be nervous, may be summarising, or may not have done it, and
the way to find out is to ask a more specific question rather than to conclude.

PROVENANCE, AND WHAT PART VI SUPPLIES FOR THESE NODES
------------------------------------------------------
RPN-PHIL-001 Part VI is the source for what a node ESTABLISHES: each of its
fifteen department models carries a competency menu whose observable-evidence
column is exactly "what a good answer would establish", and
`department_models.runbook_competency_menu` reads it. It also supplies, per
department, the evidence tiers, the gaming vectors and the red flags. Its own
stated reading order is "what good means -> competencies -> evidence tiers ->
assessment design -> validation probes -> authenticity vectors -> red flags",
which is the same traversal `next_target` performs.

CONFIRMED against Part VI:

  * a node is not a question. §21.5 is explicit that the reasoning walkthrough
    "cannot be prepared for in general -- only by actually having done the
    work", which is an argument for probing structure and against a bank of
    words, and this codebase deleted its preset technical bank for that reason.
  * `hollow_tell` is Part VI's gaming-vector column, and Part VI is equally
    explicit that its red flags "route to review, never auto-reject" (§21.8),
    which is what `hollow_tell` feeding `follow_up` rather than a grade already
    does.
  * `corroborated_by` is §5.4's independence rule applied per node.

RUNBOOK-AMBIGUITY (Part VI): the GRAPH itself. Part VI gives menus, tiers,
probes and flags per department; it does not give EDGES -- which competency
becomes worth probing once another is established -- and `unlocks` is the field
that makes this a graph rather than a checklist. §67.8 separately concedes that
"department coverage is uneven", so the absence is not an oversight to be read
around. The nodes below therefore remain this implementation's, keyed to
`department_models.py` so the two stay in step, and the edges are recorded for
review in RUNBOOK_OPEN_QUESTIONS_PHASE0B.md.

THE ONE THING THAT MUST NOT DRIFT is that a node's `establishes` should be
Part VI's observable-evidence sentence for that competency wherever a
competency has one. `runbook_node_for` resolves that directly from the extracted
data, so a department with a Runbook menu never needs a hand-written
establishes line.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.services.hiring import department_models
from app.services.hiring.department_models import (
    DEFAULT_DEPARTMENT,
    DEPARTMENTS,
    DepartmentModel,
    department_for,
)

__all__ = [
    "EvidenceNode",
    "GRAPHS",
    "nodes_for",
    "node_for_competency",
    "next_target",
    "corroboration_targets",
    "runbook_node_for",
]


@dataclass(frozen=True)
class EvidenceNode:
    """What one line of questioning can establish, and what would confirm it."""

    #: The `department_models.BaselineCompetency.key` this serves.
    competency_key: str
    #: What a good answer would ESTABLISH. Written as a fact about the
    #: candidate, not as a question -- Vaada writes the question.
    establishes: str
    #: What would corroborate it from a DIFFERENT source. This is the
    #: triangulation half: an answer that is only ever confirmed by the same
    #: person who gave it has been repeated, not corroborated.
    corroborated_by: tuple[str, ...]
    #: What a hollow answer sounds like. Routed to a follow-up, never to a
    #: grade.
    hollow_tell: str
    #: Other node keys that become worth asking once this is established. The
    #: EDGE, and what makes this a graph rather than a checklist.
    unlocks: tuple[str, ...] = ()
    #: How much this node is worth establishing, relative to its siblings.
    #: Used to order, never to grade.
    value: float = 1.0

    @property
    def key(self) -> str:
        return self.competency_key


_GENERIC_NODES: tuple[EvidenceNode, ...] = (
    EvidenceNode(
        competency_key="core_craft",
        establishes=(
            "They have personally done work of this kind recently enough to "
            "remember the decisions inside it"
        ),
        corroborated_by=(
            "a work artefact they can point at",
            "a specific outcome the employer could confirm",
        ),
        hollow_tell=(
            "Describes the process in the abstract and cannot name a decision "
            "they made or an option they rejected"
        ),
        unlocks=("problem_framing",),
        value=1.2,
    ),
    EvidenceNode(
        competency_key="delivery_ownership",
        establishes=(
            "They have carried something from an unclear brief to a shipped "
            "outcome, including the parts nobody assigned them"
        ),
        corroborated_by=(
            "a named colleague who would remember it",
            "a date or a release the employer could confirm",
        ),
        hollow_tell=(
            "Describes the project's outcome but not what they had to resolve "
            "to get there; every obstacle is external"
        ),
        unlocks=("collaboration",),
        value=1.2,
    ),
    EvidenceNode(
        competency_key="problem_framing",
        establishes=(
            "They can restate an ambiguous problem in a way that makes the next "
            "step obvious, and say what they chose not to solve"
        ),
        corroborated_by=("consistency with how they described the same work earlier",),
        hollow_tell="Reframes the problem in the same words it arrived in",
        value=1.0,
    ),
    EvidenceNode(
        competency_key="collaboration",
        establishes=(
            "They have had a real disagreement with another function and "
            "changed something as a result"
        ),
        corroborated_by=("a reference from outside their own team",),
        hollow_tell=(
            "Every disagreement in their account ends with the other party "
            "coming round"
        ),
        value=0.9,
    ),
    EvidenceNode(
        competency_key="learning_velocity",
        establishes=(
            "They closed a real knowledge gap under pressure and can say what "
            "they got wrong first"
        ),
        corroborated_by=("a technology on the resume that postdates their training",),
        hollow_tell="Names what they learned but not how, or what was hard about it",
        value=0.8,
    ),
    EvidenceNode(
        competency_key="communication",
        establishes=(
            "They can explain a technical decision to somebody who did not make "
            "it, without jargon and without condescension"
        ),
        corroborated_by=("the clarity of their own answers across the conversation",),
        hollow_tell="Explains by simplifying away the part that was actually hard",
        value=0.8,
    ),
    EvidenceNode(
        competency_key="people_leadership",
        establishes=(
            "Someone grew under them and they can say specifically what they did "
            "that caused it"
        ),
        corroborated_by=("a reference from someone who reported to them",),
        hollow_tell=(
            "Describes their management philosophy rather than a person and what "
            "changed for them"
        ),
        value=1.2,
    ),
)

_ENGINEERING_NODES: tuple[EvidenceNode, ...] = (
    EvidenceNode(
        competency_key="systems_design",
        establishes=(
            "They chose a design under a real constraint and know what it made "
            "harder later"
        ),
        corroborated_by=(
            "a system they can describe operating in production",
            "a later problem that traces back to the choice",
        ),
        hollow_tell=(
            "Describes a textbook architecture with no constraint that shaped it "
            "and no cost that followed"
        ),
        unlocks=("production_ownership",),
        value=1.3,
    ),
    EvidenceNode(
        competency_key="production_ownership",
        establishes=(
            "They have been on the sharp end of an incident and can narrate it "
            "from before the cause was known"
        ),
        corroborated_by=(
            "a specific metric or symptom they can name",
            "a change that outlived the incident",
        ),
        hollow_tell=(
            "The story starts at the root cause. A real incident is remembered "
            "from the confusion, not from the postmortem"
        ),
        unlocks=("debugging_depth",),
        value=1.3,
    ),
    EvidenceNode(
        competency_key="debugging_depth",
        establishes=(
            "They have chased a problem where the obvious explanation was wrong, "
            "and can say what made them abandon it"
        ),
        corroborated_by=("consistency with the incident they described earlier",),
        hollow_tell=(
            "Describes a debugging method rather than a bug. Anyone can describe "
            "bisecting; the tell is whether they remember being wrong"
        ),
        value=1.1,
    ),
    EvidenceNode(
        competency_key="code_quality",
        establishes=(
            "They have paid down real technical debt and knew what it was "
            "costing before they did"
        ),
        corroborated_by=("a repository or a review they could point at",),
        hollow_tell="Talks about clean code as a value rather than a trade-off",
        value=0.9,
    ),
    EvidenceNode(
        competency_key="technical_leadership",
        establishes=(
            "A practice changed on their team because of them, and they got the "
            "team to agree rather than mandating it"
        ),
        corroborated_by=("a peer who would remember the change",),
        hollow_tell="The practice was adopted because they were senior",
        value=1.1,
    ),
    *(node for node in _GENERIC_NODES if node.competency_key in {
        "delivery_ownership", "collaboration", "learning_velocity",
    }),
)

_SALES_NODES: tuple[EvidenceNode, ...] = (
    EvidenceNode(
        competency_key="pipeline_ownership",
        establishes=(
            "A closed deal originated from something they did rather than from "
            "inbound or from a colleague"
        ),
        corroborated_by=("a named account and a timeline that hangs together",),
        hollow_tell=(
            "Every named deal arrived somehow. The tell is vagueness about "
            "origin combined with precision about size"
        ),
        unlocks=("deal_navigation",),
        value=1.3,
    ),
    EvidenceNode(
        competency_key="discovery_depth",
        establishes=(
            "They found a problem the buyer had not stated, and can say how it "
            "surfaced"
        ),
        corroborated_by=("consistency with how they described the same account",),
        hollow_tell="Describes a discovery framework rather than a discovery",
        value=1.2,
    ),
    EvidenceNode(
        competency_key="deal_navigation",
        establishes=(
            "They can name everyone who had to say yes on a deal and what each "
            "of them cared about"
        ),
        corroborated_by=("the deal they described under pipeline ownership",),
        hollow_tell="Names one champion and one signer and nobody in between",
        value=1.2,
    ),
    EvidenceNode(
        competency_key="forecast_honesty",
        establishes=(
            "They removed a deal from a forecast before they had to, against "
            "their own short-term interest"
        ),
        corroborated_by=("a manager who would remember it",),
        hollow_tell="Talks about forecast accuracy as a discipline, not an instance",
        value=1.1,
    ),
    EvidenceNode(
        competency_key="resilience",
        establishes=(
            "They lost something that mattered and changed their approach "
            "because of it"
        ),
        corroborated_by=("consistency with the accounts they have already described",),
        hollow_tell="The loss is always attributed to price or timing",
        value=0.9,
    ),
    *(node for node in _GENERIC_NODES if node.competency_key in {
        "collaboration", "people_leadership",
    }),
)

_FINANCE_NODES: tuple[EvidenceNode, ...] = (
    EvidenceNode(
        competency_key="control_rigour",
        establishes=(
            "They put in a control that was a response to a specific failure, "
            "and can name the failure"
        ),
        corroborated_by=("an audit or a policy document they could point at",),
        hollow_tell="Describes a control framework rather than a control and its cause",
        unlocks=("integrity_under_pressure",),
        value=1.3,
    ),
    EvidenceNode(
        competency_key="analytical_depth",
        establishes=(
            "They investigated a variance and the real cause was not the "
            "expected one"
        ),
        corroborated_by=("a figure or a period they can name",),
        hollow_tell="Describes the analysis method and not what it found",
        value=1.2,
    ),
    EvidenceNode(
        competency_key="integrity_under_pressure",
        establishes=(
            "They declined to sign or approve something, and can say what "
            "happened next"
        ),
        corroborated_by=("someone senior who would remember the disagreement",),
        hollow_tell=(
            "Answers with a principle. Everybody has the principle; the question "
            "is whether they have the incident"
        ),
        value=1.3,
    ),
    EvidenceNode(
        competency_key="business_partnering",
        establishes=(
            "Another function made a different decision because of something "
            "they showed them"
        ),
        corroborated_by=("a colleague outside finance",),
        hollow_tell="Describes producing the analysis, not anyone acting on it",
        value=1.0,
    ),
    EvidenceNode(
        competency_key="close_discipline",
        establishes=(
            "They moved a close earlier and know what it cost somewhere else"
        ),
        corroborated_by=("a reporting calendar their employer could confirm",),
        hollow_tell=(
            "The close got faster with no trade-off named. Something always "
            "gives"
        ),
        value=0.9,
    ),
    # Finance's cross-boundary competency is `business_partnering`, which has
    # its own node above. It does NOT borrow the generic `collaboration` node --
    # the finance department model has no such competency, and a graph node
    # pointing at a competency its own model lacks is a node nothing can ever
    # reach. `test_hiring_retrieval.py` pins that, and it caught this exact
    # dangling reference.
    *(node for node in _GENERIC_NODES if node.competency_key in {
        "learning_velocity",
    }),
)

_OPERATIONS_NODES: tuple[EvidenceNode, ...] = (
    EvidenceNode(
        competency_key="process_design",
        establishes=(
            "They changed a process and knew, before changing it, how the old "
            "one was being worked around"
        ),
        corroborated_by=("a metric that moved", "someone who worked the process"),
        hollow_tell=(
            "Describes the new process. The tell is whether they know what "
            "people were actually doing before"
        ),
        unlocks=("throughput_ownership",),
        value=1.3,
    ),
    EvidenceNode(
        competency_key="throughput_ownership",
        establishes=(
            "They owned an operational number and can name the specific "
            "intervention that moved it"
        ),
        corroborated_by=("a reporting line or a review their employer would confirm",),
        hollow_tell="The number improved during their tenure, causally unattached",
        value=1.2,
    ),
    EvidenceNode(
        competency_key="escalation_judgement",
        establishes=(
            "They can name something they escalated early and something they "
            "deliberately did not, and the difference"
        ),
        corroborated_by=("consistency with the incident they described earlier",),
        hollow_tell="Has an escalation matrix but no instance of judgement inside it",
        value=1.1,
    ),
    EvidenceNode(
        competency_key="vendor_management",
        establishes=(
            "They fixed a supplier problem without simply changing supplier"
        ),
        corroborated_by=("a contract or an SLA change",),
        hollow_tell="The resolution was always escalation or replacement",
        value=0.9,
    ),
    *(node for node in _GENERIC_NODES if node.competency_key in {
        "people_leadership", "collaboration",
    }),
)

GRAPHS: dict[str, tuple[EvidenceNode, ...]] = {
    "generic": _GENERIC_NODES,
    "engineering": _ENGINEERING_NODES,
    "sales": _SALES_NODES,
    "finance": _FINANCE_NODES,
    "operations": _OPERATIONS_NODES,
}


def nodes_for(department: str | DepartmentModel) -> tuple[EvidenceNode, ...]:
    key = department.key if isinstance(department, DepartmentModel) else str(department)
    return GRAPHS.get(key, GRAPHS[DEFAULT_DEPARTMENT])


def node_for_competency(
    competency_key: str, department: str | DepartmentModel
) -> EvidenceNode | None:
    """The node serving one competency, or None.

    None is a real answer, not a failure: a genuinely role-specific competency
    that the department model had no anchor for will have no graph node either,
    and Vaada falls back to generating from the matrix item's own
    observable-evidence statement -- which stage 2 guaranteed exists.
    """
    for node in nodes_for(department):
        if node.competency_key == competency_key:
            return node
    return None


def next_target(
    *,
    department: str | DepartmentModel,
    matrix_keys: Sequence[str],
    established: Iterable[str] = (),
    weights: Mapping[str, float] | None = None,
) -> EvidenceNode | None:
    """The highest-value node still worth establishing.

    THE ORDER IS DETERMINISTIC, AND THAT IS THE POINT. `interviewer` already
    keeps its COVERAGE PLAN deterministic while letting the WORDS vary per
    candidate, because a fixed plan is what keeps two candidates on one job
    comparable. This is the same rule applied to the evidence graph: which node
    is targeted next is arithmetic over Sutra's weights and the graph's own
    values; how it is asked is Vaada's.

    An unlocked node is preferred over a locked one at equal value, so the
    conversation follows the graph's edges rather than jumping between
    unconnected topics -- which is the difference between an interview and a
    questionnaire.
    """
    done = set(established)
    available = [
        node
        for node in nodes_for(department)
        if node.competency_key in set(matrix_keys) and node.competency_key not in done
    ]
    if not available:
        return None
    unlocked = {
        key
        for node in nodes_for(department)
        if node.competency_key in done
        for key in node.unlocks
    }

    def score(node: EvidenceNode) -> tuple[float, float, str]:
        weight = (weights or {}).get(node.competency_key, 1.0)
        # Negated for a descending sort while keeping the key ascending, and the
        # competency key breaks ties so the order is total -- two nodes of equal
        # value must not depend on dict ordering.
        return (
            -(node.value * weight + (0.25 if node.competency_key in unlocked else 0.0)),
            -node.value,
            node.competency_key,
        )

    return sorted(available, key=score)[0]


def corroboration_targets(
    node: EvidenceNode, *, available_sources: Iterable[str] = ()
) -> tuple[list[str], list[str]]:
    """(reachable, out_of_band).

    The second list is what the platform CANNOT get. Returned rather than
    dropped, because it is what Miti reads as a reason to hold confidence down:
    a competency whose only real corroboration is a reference is a competency
    the assessment can probe and cannot confirm, and saying so is more honest
    than treating a well-argued answer as corroborated.
    """
    reachable = set(available_sources)
    if not reachable:
        return [], list(node.corroborated_by)
    inside = [c for c in node.corroborated_by if any(s in c for s in reachable)]
    outside = [c for c in node.corroborated_by if c not in inside]
    return inside, outside


# ── Part VI's own observable evidence, read from the extracted data ──────────


def runbook_node_for(
    department_key: str, competency_id: str
) -> EvidenceNode | None:
    """A node built from Part VI's competency menu, or None if it has no row.

    `establishes` comes from the Runbook's observable-evidence column verbatim,
    which is what that column is: "what we would see if this were true". A node
    resolved this way needs no hand-written establishes line and cannot drift
    from the document.

    Returns None rather than raising for an unknown competency id, because the
    caller's next move is to fall through to its own node set, and a department
    the Runbook covers can still be asked about a competency the menu does not
    list. An unknown DEPARTMENT does raise, in `runbook_competency_menu`, for
    the opposite reason: naming a civil engineer's competencies from a generic
    menu is the vocabulary collapse the department models exist to prevent.
    """
    wanted = (competency_id or "").strip().upper()
    for competency in department_models.runbook_competency_menu(department_key):
        if competency.id.upper() != wanted:
            continue
        return EvidenceNode(
            competency_key=competency.id.lower().replace("-", "_"),
            establishes=competency.observable_evidence,
            # §5.4: independence is counted by ORIGINATOR. A node's
            # corroboration has to come from somebody who was not the
            # candidate, or it is one person saying one thing twice.
            corroborated_by=(
                "a source with a different originator from the candidate",
                "an artefact whose provenance can be checked independently",
            ),
            hollow_tell=(
                "Describes the practice in the abstract and cannot reconstruct "
                "one instance of it end to end"
            ),
        )
    return None
