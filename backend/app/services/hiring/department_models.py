"""LAYER 1: the platform's own department competency models (spec-doc5 §A.1).

WHAT LAYER 1 IS, AND WHY IT IS PYTHON RATHER THAN A TABLE
-----------------------------------------------------------
Layer 1 is the ReadyPick Hiring Philosophy compiled into something executable:
per-department baseline competencies, their baseline weights, the rubric anchors
a dimension evaluator is given, and the evidence sources that would actually
show a competency is real. spec-doc5 calls it "captured once, ever -- not
something a client or recruiter fills in".

That last clause is why this is a Python constant and not a database table, and
the argument is one this codebase has already made twice: `pfi_bank.py` and
`candidate_profile_form.py` are fixed constants for the same reason. A table has
an UPDATE statement, an UPDATE statement eventually gets an admin screen, and an
admin screen makes Layer 1 something a client can edit -- at which point the
layering is decorative. A constant can only be changed by a reviewed commit,
which is exactly the ceremony a universal, rarely-changing artifact should cost.

WHAT A DEPARTMENT MODEL IS FOR
-------------------------------
Two things, and they are different:

  1. It NAMES competencies. Sutra's transformation pipeline stage 1 turns a SWOT
     input into a competency "named from the department model", so that two
     hiring managers who describe the same need in different words land on the
     same competency and their jobs stay comparable. Without it, one job has
     "ownership of delivery" and another has "sees things through", and nothing
     in the platform knows those are the same axis.
  2. It supplies the BASELINE WEIGHT that Layers 2 and 3 then tune. A weight
     with no baseline is a weight a model invented, and spec-doc5's acceptance
     criterion asks for a weight "traceable to a specific Layer 1/2/3 source
     instead of being an opaque model output".

WHY THE WEIGHTS HERE ARE NOT SHOWN TO ANYONE
----------------------------------------------
They are internal ranking data, exactly like `matching`'s scores. The standing
rule is absolute: no number reaches a client. A weight orders the matrix and
feeds the deterministic aggregator; it is never rendered, never returned by a
report route, and never converted to a percentage in an email.

PROVENANCE, AND WHAT PART VI ACTUALLY SUPPLIES
------------------------------------------------
RPN-PHIL-001 Part VI (§21 to §35) carries FIFTEEN department models, each with a
competency MENU of eight to twelve entries carrying a stable id (SW-01, DS-04,
FIN-02), a competency name and an observable-evidence sentence. §11.1 carries the
Layer 1 baseline weight matrix, ten department families by seniority band, over
the five dimensions. Both are now reachable through `runbook_departments()` and
`baseline_dimension_weights()`, which read the extracted data rather than
restating it.

WHAT THIS MODULE HELD BEFORE was five departments (generic, engineering, sales,
finance, operations) with six to eight competencies each, written to the shape
spec-doc5 described. Not one competency name, id or observable-evidence sentence
came from the Runbook, because the Runbook was not present. Ten of the fifteen
departments had no model at all, so a civil engineer, a designer, an architect,
an HR generalist and a tradesperson were all being named against the generic
model, which is exactly the vocabulary collapse `match_competency` exists to
prevent.

TWO THINGS PART VI DOES NOT SUPPLY, AND BOTH ARE MARKED BELOW
--------------------------------------------------------------
1. A PER-COMPETENCY BASELINE WEIGHT. §11.1 weights DIMENSIONS, not competencies,
   and §20.3 is explicit that competency importance comes from the hiring
   manager's force-ranking at Layer 3: "Rank the required competencies 1..n
   (max 6). No ties." Part VI reinforces it -- "the competency list in each
   model is the menu ... the scorecard for a given role selects at most six from
   it, weighted by SWOT force-ranking. No role uses the whole menu." So
   `BaselineCompetency.baseline_weight` has no Runbook source, and the honest
   reading is that Layer 1 should supply a menu and a dimension vector while
   Layer 3 supplies the ranking. Settled in RPN-PHIL-001 v1.3's Part VI preamble rather
   than silently re-derived, because changing it moves every weight in the
   product.

2. A COMPETENCY-TO-DIMENSION MAPPING. §9 defines the five dimensions and §21.3
   lists the competencies, and nothing in between says which competency
   evidences which dimension. Miti's routing needs one. Marked
   settled in v1.3.

THE SENIORITY VOCABULARIES DO NOT MATCH, AND THAT IS A GENUINE CONFLICT
-------------------------------------------------------------------------
§11.1 bands seniority per department family and the bands differ between them:
IT & Software runs Fresher / 2-5 yrs / 5-10 yrs / 10+ / Eng leadership, Finance
adds a CFO row, Leadership runs First-line manager / Senior manager / Director
or VP / CXO, and trades run Entry / Experienced / Supervisory. This codebase has
one four-grade vocabulary (`non_managerial | managerial | leadership | cxo`) that
the whole product uses, and CLAUDE.md is explicit that a fifth vocabulary for
"how senior is this role" is how the two parallel five-label rating scales
happened. The two cannot both be canonical. `SENIORITIES` therefore stays as it
is and `baseline_dimension_weights` takes the Runbook's own band label, so the
mapping between them is done once, at the call site, visibly, rather than being
assumed anywhere. Recorded in RUNBOOK_OPEN_QUESTIONS_PHASE0B.md.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping

from app.services.hiring import layers

__all__ = [
    "SENIORITIES",
    "EvidenceSource",
    "EVIDENCE_SOURCES",
    "BaselineCompetency",
    "DepartmentModel",
    "DEPARTMENTS",
    "DEFAULT_DEPARTMENT",
    "department_for",
    "baseline_for",
    "rubric_anchors",
    "competency_names",
    "match_competency",
    "RunbookCompetency",
    "RunbookDepartment",
    "runbook_departments",
    "runbook_competency_menu",
    "baseline_dimension_weights",
    "RubricBand",
    "dimension_rubric_anchors",
    "seniority_emphasis",
]

# ── Seniority ────────────────────────────────────────────────────────────────
#
# The same four grades the rest of the product uses. Reused rather than
# redefined: a fifth vocabulary for "how senior is this role" is exactly how the
# two parallel five-label rating scales happened.
SENIORITIES: tuple[str, ...] = (
    "non_managerial",
    "managerial",
    "leadership",
    "cxo",
)


# ── Evidence sources ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceSource:
    """Where a competency could actually be SEEN, and how much that is worth.

    `tier` maps onto `evidence/ledger.py`'s trust lattice, which is already
    ordered authoritative > validated > observed > inferred and already refuses
    to treat a claim standing only on `inferred` evidence as supported. Reusing
    that lattice rather than inventing a parallel one is the point: two ordered
    scales for "how much do we believe this" would have to be kept in step by
    hand, which is the failure the four-grade rating scale was created to end.
    """

    key: str
    label: str
    tier: str
    #: Whether this source is reachable inside a ReadyPick assessment today.
    #: A source that is not reachable is still worth naming -- it tells Sutra's
    #: stage 3 what would be needed, and it tells a recruiter what the platform
    #: cannot see -- but it must never be treated as a satisfied requirement.
    available_in_assessment: bool


EVIDENCE_SOURCES: dict[str, EvidenceSource] = {
    "resume_claim": EvidenceSource(
        "resume_claim", "A statement on the resume", "inferred", True
    ),
    "assessment_answer": EvidenceSource(
        "assessment_answer", "What the candidate said in the assessment", "observed", True
    ),
    "worked_example": EvidenceSource(
        "worked_example",
        "A specific incident the candidate can narrate end to end",
        "observed",
        True,
    ),
    "validation_field": EvidenceSource(
        "validation_field", "A factual field the candidate submitted", "validated", True
    ),
    "work_artefact": EvidenceSource(
        "work_artefact", "Something the candidate produced", "authoritative", False
    ),
    "reference": EvidenceSource(
        "reference", "A named former colleague or manager", "authoritative", False
    ),
    "employer_verification": EvidenceSource(
        "employer_verification", "The employer confirmed it", "authoritative", True
    ),
}


# ── Competencies ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BaselineCompetency:
    """One competency a department cares about, before any client sees it."""

    key: str
    name: str
    #: What we would SEE if this were true. Stage 2 of Sutra's pipeline turns a
    #: SWOT phrase into one of these, and this is the quality bar it is held to:
    #: a behaviour somebody could have watched happen, never an adjective.
    observable_evidence: str
    #: SOURCE: RPN-PHIL-001 Part VI preamble (v1.3): Baseline importance, before
    #: Layer 2 and Layer 3 tuning. THE RUNBOOK SUPPLIES NO SUCH NUMBER. §11.1
    #: weights the five DIMENSIONS and §20.3 puts competency importance in the
    #: hiring manager's force-ranking at Layer 3 ("Rank the required
    #: competencies 1..n (max 6). No ties."), which Part VI restates as "the
    #: competency list in each model is the menu ... weighted by SWOT
    #: force-ranking". Kept, because `match_competency` and the matrix have
    #: nothing else to order by and the Runbook offers no replacement, and
    #: escalated rather than re-derived: changing it moves every weight in the
    #: product. Relative within a department only -- comparing a weight across
    #: departments is meaningless and nothing does it.
    baseline_weight: float
    #: SOURCE: RPN-PHIL-001 Part VI preamble (v1.3): which of the five internal dimensions this
    #: competency mostly speaks to. §9 defines the dimensions and §21.3 lists
    #: the competencies; nothing between them maps one onto the other, and
    #: Miti's routing needs a mapping. A hint for that routing, not a partition:
    #: a competency can produce evidence for more than one dimension, and the
    #: evaluators see whatever is mapped to them.
    primary_dimension: str
    #: Ordered by how much they would settle the question.
    evidence_sources: tuple[str, ...]
    #: Seniorities where this competency is material at all. A competency that
    #: does not apply is ABSENT rather than weighted to zero -- a zero-weight row
    #: in a matrix is a thing a recruiter has to read and dismiss.
    seniorities: tuple[str, ...] = SENIORITIES
    #: Words a hiring manager might use for the same thing. Used by
    #: `match_competency` so two managers describing one need in different words
    #: land on one competency and their jobs stay comparable.
    aliases: tuple[str, ...] = ()


# The five internal dimensions, named here so a competency can point at one
# without importing the Miti package (which imports this one).
DIM_VERIFIED_COMPETENCE = "verified_competence"
DIM_TRACK_RECORD = "track_record_impact"
DIM_ROLE_FIT = "role_context_fit"
DIM_AUTHENTICITY = "authenticity_consistency"
DIM_TRAJECTORY = "trajectory_potential"


@dataclass(frozen=True)
class DepartmentModel:
    key: str
    label: str
    competencies: tuple[BaselineCompetency, ...]
    #: SOURCE: RPN-PHIL-001 §9.1 to §9.5 with §57.3 (v1.3): rubric anchor wording per
    #: seniority. THE RUNBOOK PUTS RUBRIC ANCHORS SOMEWHERE ELSE. §9.1 to §9.5
    #: each carry one six-band scoring-anchor table over 0 to 100, and those
    #: tables are universal: stated once per DIMENSION, never restated per
    #: department or per seniority. Exactly one department carries anything per
    #: seniority, §21.11's "Seniority notes" for IT & Software, and those are an
    #: emphasis shift rather than an anchor.
    #:
    #: §57.3 names "retrieved rubric anchors from the department model" as an
    #: evaluator input, which is what led here; the anchors that exist are the
    #: dimension ones, and the department model supplies the COMPETENCY SET they
    #: are applied to. Use `dimension_rubric_anchors` for the real thing and
    #: `seniority_emphasis` for §21.11. These stay because Sutra and Vaada read
    #: them today and the Runbook offers no per-seniority replacement for
    #: fourteen departments, and they are marked rather than trusted.
    #:
    #: These are ANCHORS, not thresholds: they describe what the top of the
    #: range looks like at that seniority, and the evaluator places evidence
    #: against them. A numeric threshold here would be a number a model invented
    #: dressed up as a standard.
    anchors: dict[str, str] = field(default_factory=dict)

    def for_seniority(self, seniority: str) -> tuple[BaselineCompetency, ...]:
        return tuple(c for c in self.competencies if seniority in c.seniorities)


# ── The models ───────────────────────────────────────────────────────────────
#
# Deliberately SMALL. Five departments and a generic fallback, each with six to
# eight competencies. A larger catalogue authored here without the Runbook would
# be a larger quantity of guesswork, and the shape is what the rest of the
# system is built against.

_GENERIC = DepartmentModel(
    key="generic",
    label="General",
    anchors={
        "non_managerial": (
            "Has personally done the work, can narrate a specific instance end "
            "to end including what went wrong, and can say why they made the "
            "choices they made rather than which tool they used."
        ),
        "managerial": (
            "Has delivered through other people, can name a decision they got "
            "wrong and what changed afterwards, and describes their team's "
            "outcomes in terms a person outside the team would recognise."
        ),
        "leadership": (
            "Has set direction under real ambiguity, can describe a bet that "
            "did not pay off and what it cost, and distinguishes what they "
            "decided from what the organisation decided around them."
        ),
        "cxo": (
            "Has owned an outcome the whole organisation was measured on, can "
            "describe a trade-off between two things they cared about, and "
            "talks about the constraint they were actually operating under."
        ),
    },
    competencies=(
        BaselineCompetency(
            key="core_craft",
            name="Core craft depth",
            observable_evidence=(
                "Can take one piece of their own recent work and explain the "
                "decisions inside it, including the option they rejected"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "assessment_answer", "work_artefact"),
            aliases=("technical depth", "hands-on skill", "domain expertise"),
        ),
        BaselineCompetency(
            key="delivery_ownership",
            name="Delivery ownership",
            observable_evidence=(
                "Has taken a project from an unclear brief to a shipped outcome "
                "and can name what they had to resolve to get there"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "assessment_answer", "reference"),
            aliases=("ownership", "accountability", "sees things through", "drive"),
        ),
        BaselineCompetency(
            key="problem_framing",
            name="Problem framing",
            observable_evidence=(
                "Restates an ambiguous problem in terms that make the next step "
                "obvious, and says what they chose not to solve"
            ),
            baseline_weight=0.9,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("assessment_answer", "worked_example"),
            aliases=("analytical thinking", "structured thinking", "judgement"),
        ),
        BaselineCompetency(
            key="collaboration",
            name="Working across boundaries",
            observable_evidence=(
                "Describes a disagreement with another function and what they "
                "changed as a result of it"
            ),
            baseline_weight=0.8,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("assessment_answer", "reference"),
            aliases=("collaboration", "teamwork", "stakeholder management"),
        ),
        BaselineCompetency(
            key="learning_velocity",
            name="Learning velocity",
            observable_evidence=(
                "Names something they did not know six months ago, how they "
                "closed the gap, and what it changed about their work"
            ),
            baseline_weight=0.7,
            primary_dimension=DIM_TRAJECTORY,
            evidence_sources=("assessment_answer", "worked_example"),
            aliases=("learning agility", "curiosity", "growth mindset"),
        ),
        BaselineCompetency(
            key="communication",
            name="Explaining work to people who did not do it",
            observable_evidence=(
                "Explains a technical decision to a non-specialist without "
                "either jargon or condescension"
            ),
            baseline_weight=0.7,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("assessment_answer",),
            aliases=("communication", "articulation", "clarity"),
        ),
        BaselineCompetency(
            key="people_leadership",
            name="Developing other people",
            observable_evidence=(
                "Can name someone who grew under them and say specifically what "
                "they did that caused it"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "reference"),
            seniorities=("managerial", "leadership", "cxo"),
            aliases=("people management", "coaching", "mentoring", "team building"),
        ),
    ),
)

_ENGINEERING = DepartmentModel(
    key="engineering",
    label="Engineering",
    anchors=dict(_GENERIC.anchors),
    competencies=(
        BaselineCompetency(
            key="systems_design",
            name="Systems design under constraint",
            observable_evidence=(
                "Describes a design they chose, the constraint that forced it, "
                "and what it made harder later"
            ),
            baseline_weight=1.2,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "assessment_answer", "work_artefact"),
            aliases=(
                "architecture", "system design", "technical design",
                "design under constraint", "designed the system",
            ),
        ),
        BaselineCompetency(
            key="production_ownership",
            name="Operating what they built",
            observable_evidence=(
                "Can narrate a production incident they were on, what the "
                "symptom looked like before they knew the cause, and what "
                "changed afterwards"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=(
                "on-call", "on call", "paged", "pager", "reliability",
                "operations", "sre", "in production", "production incident",
                "owned anything in production", "own it in production",
                "runs what they build", "you build it you run it",
            ),
        ),
        BaselineCompetency(
            key="code_quality",
            name="Code somebody else can change",
            observable_evidence=(
                "Describes a refactor they did and what it was costing before "
                "they did it"
            ),
            baseline_weight=0.9,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("work_artefact", "assessment_answer"),
            aliases=("code quality", "maintainability", "engineering rigour"),
        ),
        BaselineCompetency(
            key="debugging_depth",
            name="Debugging a problem nobody had seen",
            observable_evidence=(
                "Narrates a bug where the obvious explanation was wrong, and "
                "what made them abandon it"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=(
                "debugging", "troubleshooting", "root cause",
                "root cause analysis", "diagnose", "hard bug",
            ),
        ),
        BaselineCompetency(
            key="delivery_ownership",
            name="Delivery ownership",
            observable_evidence=(
                "Has taken a project from an unclear brief to a shipped outcome "
                "and can name what they had to resolve to get there"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "assessment_answer", "reference"),
            aliases=("ownership", "accountability", "drive"),
        ),
        BaselineCompetency(
            key="collaboration",
            name="Working across boundaries",
            observable_evidence=(
                "Describes a disagreement with product or design and what they "
                "changed as a result"
            ),
            baseline_weight=0.8,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("assessment_answer", "reference"),
            aliases=("collaboration", "teamwork", "cross-functional"),
        ),
        BaselineCompetency(
            key="learning_velocity",
            name="Learning velocity",
            observable_evidence=(
                "Names a technology they picked up under delivery pressure and "
                "what they got wrong first"
            ),
            baseline_weight=0.8,
            primary_dimension=DIM_TRAJECTORY,
            evidence_sources=("assessment_answer", "worked_example"),
            aliases=("learning agility", "curiosity", "adaptability"),
        ),
        BaselineCompetency(
            key="technical_leadership",
            name="Raising the bar around them",
            observable_evidence=(
                "Names a practice the team adopted because of them and how they "
                "got the team to agree to it"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "reference"),
            seniorities=("managerial", "leadership", "cxo"),
            aliases=("technical leadership", "mentoring", "engineering culture"),
        ),
    ),
)

_SALES = DepartmentModel(
    key="sales",
    label="Sales",
    anchors=dict(_GENERIC.anchors),
    competencies=(
        BaselineCompetency(
            key="pipeline_ownership",
            name="Building a pipeline from nothing",
            observable_evidence=(
                "Describes where a specific closed deal actually came from, "
                "including the part that was not inbound"
            ),
            baseline_weight=1.2,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "assessment_answer", "reference"),
            aliases=("prospecting", "pipeline generation", "hunting"),
        ),
        BaselineCompetency(
            key="discovery_depth",
            name="Finding the problem behind the request",
            observable_evidence=(
                "Names a deal where what the buyer first asked for was not what "
                "they needed, and how that surfaced"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=("discovery", "consultative selling", "needs analysis"),
        ),
        BaselineCompetency(
            key="deal_navigation",
            name="Getting a decision out of an organisation",
            observable_evidence=(
                "Can name every person who had to say yes on a deal they closed "
                "and what each of them cared about"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=("stakeholder mapping", "closing", "negotiation"),
        ),
        BaselineCompetency(
            key="resilience",
            name="Carrying on after a loss",
            observable_evidence=(
                "Describes a deal they lost, what they concluded from it, and "
                "what they did differently next"
            ),
            baseline_weight=0.9,
            primary_dimension=DIM_TRAJECTORY,
            evidence_sources=("assessment_answer",),
            aliases=("resilience", "persistence", "grit"),
        ),
        BaselineCompetency(
            key="forecast_honesty",
            name="Forecasting against their own interest",
            observable_evidence=(
                "Describes a deal they pulled from a forecast before they had "
                "to, and what told them to"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_AUTHENTICITY,
            evidence_sources=("worked_example", "reference"),
            aliases=("forecasting", "pipeline hygiene", "sales discipline"),
        ),
        BaselineCompetency(
            key="collaboration",
            name="Working across boundaries",
            observable_evidence=(
                "Describes something they needed from product or delivery and "
                "how they got it without escalating"
            ),
            baseline_weight=0.8,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("assessment_answer", "reference"),
            aliases=("collaboration", "internal stakeholder management"),
        ),
        BaselineCompetency(
            key="people_leadership",
            name="Developing other sellers",
            observable_evidence=(
                "Names a rep who improved under them and what specifically they "
                "changed about how that person worked"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "reference"),
            seniorities=("managerial", "leadership", "cxo"),
            aliases=("sales management", "coaching", "team building"),
        ),
    ),
)

_FINANCE = DepartmentModel(
    key="finance",
    label="Finance",
    anchors=dict(_GENERIC.anchors),
    competencies=(
        BaselineCompetency(
            key="control_rigour",
            name="Controls that survive a bad month",
            observable_evidence=(
                "Describes a control they put in and the specific failure it "
                "was a response to"
            ),
            baseline_weight=1.2,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=("internal controls", "compliance", "governance", "audit"),
        ),
        BaselineCompetency(
            key="analytical_depth",
            name="Explaining a number somebody disagreed with",
            observable_evidence=(
                "Narrates a variance they investigated and what the real cause "
                "turned out to be"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "work_artefact"),
            aliases=("financial analysis", "fp&a", "variance analysis"),
        ),
        BaselineCompetency(
            key="business_partnering",
            name="Being useful to people who are not in finance",
            observable_evidence=(
                "Names a decision another function made differently because of "
                "something they showed them"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("worked_example", "reference"),
            aliases=("business partnering", "commercial finance", "influence"),
        ),
        BaselineCompetency(
            key="integrity_under_pressure",
            name="Holding a position that was unpopular",
            observable_evidence=(
                "Describes a time they refused to sign something and what "
                "happened next"
            ),
            baseline_weight=1.2,
            primary_dimension=DIM_AUTHENTICITY,
            evidence_sources=("worked_example", "reference"),
            aliases=("integrity", "ethics", "professional scepticism"),
        ),
        BaselineCompetency(
            key="close_discipline",
            name="Closing on time, repeatedly",
            observable_evidence=(
                "Describes what they changed to move a close earlier and what "
                "it cost elsewhere"
            ),
            baseline_weight=0.9,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=("month end close", "reporting", "process improvement"),
        ),
        BaselineCompetency(
            key="learning_velocity",
            name="Learning velocity",
            observable_evidence=(
                "Names a regulation or standard they had to learn quickly and "
                "how they checked they had it right"
            ),
            baseline_weight=0.7,
            primary_dimension=DIM_TRAJECTORY,
            evidence_sources=("assessment_answer",),
            aliases=("learning agility", "technical accounting"),
        ),
    ),
)

_OPERATIONS = DepartmentModel(
    key="operations",
    label="Operations",
    anchors=dict(_GENERIC.anchors),
    competencies=(
        BaselineCompetency(
            key="process_design",
            name="Designing a process people actually follow",
            observable_evidence=(
                "Describes a process they changed, and how they knew the old one "
                "was being worked around"
            ),
            baseline_weight=1.2,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "work_artefact"),
            aliases=("process improvement", "lean", "six sigma", "operational excellence"),
        ),
        BaselineCompetency(
            key="throughput_ownership",
            name="Owning a number that moves daily",
            observable_evidence=(
                "Names an operational metric they were accountable for and the "
                "specific intervention that moved it"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "assessment_answer"),
            aliases=("throughput", "sla", "productivity", "capacity planning"),
        ),
        BaselineCompetency(
            key="escalation_judgement",
            name="Knowing what to escalate and when",
            observable_evidence=(
                "Describes something they escalated early and something they "
                "deliberately did not, and the difference between them"
            ),
            baseline_weight=1.0,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("worked_example", "reference"),
            aliases=("escalation", "judgement", "incident management"),
        ),
        BaselineCompetency(
            key="vendor_management",
            name="Getting performance out of a third party",
            observable_evidence=(
                "Names a supplier problem they fixed without simply switching "
                "supplier"
            ),
            baseline_weight=0.9,
            primary_dimension=DIM_VERIFIED_COMPETENCE,
            evidence_sources=("worked_example", "reference"),
            aliases=("vendor management", "supplier management", "procurement"),
        ),
        BaselineCompetency(
            key="people_leadership",
            name="Running a team through a bad week",
            observable_evidence=(
                "Describes a period of sustained pressure and what specifically "
                "they did to keep the team functioning"
            ),
            baseline_weight=1.1,
            primary_dimension=DIM_TRACK_RECORD,
            evidence_sources=("worked_example", "reference"),
            seniorities=("managerial", "leadership", "cxo"),
            aliases=("people management", "shift management", "team leadership"),
        ),
        BaselineCompetency(
            key="collaboration",
            name="Working across boundaries",
            observable_evidence=(
                "Describes a handoff between two teams that was failing and what "
                "they changed about it"
            ),
            baseline_weight=0.8,
            primary_dimension=DIM_ROLE_FIT,
            evidence_sources=("assessment_answer", "reference"),
            aliases=("collaboration", "cross-functional", "coordination"),
        ),
    ),
)

DEPARTMENTS: dict[str, DepartmentModel] = {
    model.key: model
    for model in (_GENERIC, _ENGINEERING, _SALES, _FINANCE, _OPERATIONS)
}

DEFAULT_DEPARTMENT = "generic"


# ── Lookup ───────────────────────────────────────────────────────────────────

#: Words in a job title or a department field that indicate a department.
#: Ordered longest-first at match time, so "sales engineer" resolves on the more
#: specific token rather than on whichever appears first in the dict.
_DEPARTMENT_HINTS: dict[str, tuple[str, ...]] = {
    "engineering": (
        "engineer", "engineering", "developer", "software", "backend", "frontend",
        "full stack", "fullstack", "devops", "sre", "platform", "data engineer",
        "architect", "qa", "test automation", "mobile", "android", "ios",
    ),
    "sales": (
        "sales", "account executive", "business development", "revenue",
        "account manager", "pre-sales", "presales", "inside sales", "sdr", "bdr",
    ),
    "finance": (
        "finance", "accounting", "accountant", "controller", "treasury", "audit",
        "fp&a", "financial", "tax", "cfo",
    ),
    "operations": (
        "operations", "ops", "supply chain", "logistics", "warehouse",
        "procurement", "service delivery", "fulfilment", "fulfillment",
    ),
}


def department_for(*hints: str | None) -> DepartmentModel:
    """Resolve a department from a job title, a department field, a JD.

    Falls back to `generic` rather than raising, and that is deliberate: a job
    the hint table does not recognise must still get a Layer 1 baseline, because
    the alternative is a matrix built with no baseline at all -- which is exactly
    the "opaque model output" spec-doc5 is replacing. The generic model is a
    weaker baseline, not an absent one.
    """
    haystack = " ".join(h.lower() for h in hints if h)
    if not haystack:
        return DEPARTMENTS[DEFAULT_DEPARTMENT]
    best: tuple[int, str] | None = None
    for key, tokens in _DEPARTMENT_HINTS.items():
        for token in tokens:
            if token in haystack and (best is None or len(token) > best[0]):
                best = (len(token), key)
    return DEPARTMENTS[best[1]] if best else DEPARTMENTS[DEFAULT_DEPARTMENT]


def baseline_for(department: str | DepartmentModel, seniority: str) -> tuple[BaselineCompetency, ...]:
    model = (
        department
        if isinstance(department, DepartmentModel)
        else DEPARTMENTS.get(department, DEPARTMENTS[DEFAULT_DEPARTMENT])
    )
    if seniority not in SENIORITIES:
        seniority = SENIORITIES[0]
    return model.for_seniority(seniority)


def rubric_anchors(department: str | DepartmentModel, seniority: str) -> str:
    """The anchor wording a dimension evaluator is given for this seniority.

    What "strong" means for a graduate and for a CXO are different sentences,
    and an evaluator handed the wrong one grades the wrong job -- which shows up
    as a senior candidate reading as merely competent and a junior one reading
    as exceptional, both wrong, neither obviously so.
    """
    model = (
        department
        if isinstance(department, DepartmentModel)
        else DEPARTMENTS.get(department, DEPARTMENTS[DEFAULT_DEPARTMENT])
    )
    if seniority not in model.anchors:
        seniority = SENIORITIES[0]
    return model.anchors.get(seniority, "")


def competency_names(department: str | DepartmentModel, seniority: str) -> tuple[str, ...]:
    return tuple(c.name for c in baseline_for(department, seniority))


#: Light plural folding, so "systems design" matches the alias "system design".
#:
#: NOT A STEMMER. A real stemmer would fold "operations" to "oper" and start
#: matching things nobody meant; this only removes a trailing "s" from words of
#: four letters or more, which fixes the singular/plural mismatch that actually
#: occurs in job descriptions and does nothing else. The four-letter floor keeps
#: "ops" and "sre" intact.
def _fold(text: str) -> str:
    words = re.findall(r"[a-z&/+.#-]+", (text or "").lower())
    return " ".join(
        word[:-1] if len(word) >= 5 and word.endswith("s") and not word.endswith("ss")
        else word
        for word in words
    )


def match_competency(
    phrase: str,
    department: str | DepartmentModel,
    seniority: str = "non_managerial",
) -> BaselineCompetency | None:
    """Map a hiring manager's words onto a baseline competency, or None.

    RETURNS NONE RATHER THAN A BEST GUESS. A phrase that matches nothing is a
    genuinely role-specific requirement, and forcing it onto the nearest
    baseline competency would quietly relabel it as something the department
    model already knew about -- which is worse than having no baseline, because
    it looks like traceability and is not. Sutra's stage 1 handles the None case
    by naming the competency from the JD and recording that it had no Layer 1
    anchor, which is an honest provenance rather than a fabricated one.

    Matching is longest-alias-wins so a phrase containing both "design" and
    "system design" resolves on the more specific one, and it is done over a
    plural-folded form so "systems design" and "system design" agree -- a
    mismatch that costs a competency its Layer 1 baseline for a grammatical
    reason nobody intended.
    """
    text = _fold(phrase)
    if not text:
        return None
    candidates = baseline_for(department, seniority)
    for competency in candidates:
        folded_name = _fold(competency.name)
        if folded_name and (folded_name in text or text in folded_name):
            return competency
    best: tuple[int, BaselineCompetency] | None = None
    for competency in candidates:
        for alias in competency.aliases:
            folded_alias = _fold(alias)
            if folded_alias and folded_alias in text:
                if best is None or len(folded_alias) > best[0]:
                    best = (len(folded_alias), competency)
    return best[1] if best else None


def all_competency_keys() -> frozenset[str]:
    return frozenset(
        competency.key
        for model in DEPARTMENTS.values()
        for competency in model.competencies
    )


def evidence_sources_for(keys: Iterable[str]) -> tuple[EvidenceSource, ...]:
    """Resolve source keys, dropping unknown ones rather than raising.

    A competency naming a source that does not exist is a data error in this
    file, and it should not take down a matrix generation at request time. The
    absence is caught by `test_department_models.py` instead, which is where a
    data error in a constant belongs.
    """
    return tuple(
        EVIDENCE_SOURCES[key] for key in keys if key in EVIDENCE_SOURCES
    )


# ── RPN-PHIL-001 Part VI and §11.1, read from the extracted data ─────────────
#
# The Runbook's own content, reachable without being restated here. Sutra's
# stage 1 names a competency from the department model, and "the department
# model" means Part VI's menu -- twelve entries for IT & Software, eleven for
# Data, and so on across fifteen departments -- not the five-department anchor
# set this module carried before the Runbook was present.


@dataclass(frozen=True)
class RunbookCompetency:
    """One row of a Part VI competency menu, as the Runbook prints it."""

    #: The Runbook's own stable id, e.g. "SW-02". Quoted in a scorecard so a
    #: reviewer can find the row it came from.
    id: str
    name: str
    observable_evidence: str
    source: str


@dataclass(frozen=True)
class RunbookDepartment:
    """One Part VI department model."""

    key: str
    #: The Runbook section number, e.g. 21.
    number: int
    title: str
    role_families: tuple[str, ...]
    menu: tuple[RunbookCompetency, ...]
    #: Which §11.1 baseline weight family this department's weights come from.
    baseline_weight_family: str
    #: §21.11's seniority notes, {level: emphasis shift}. EMPTY for fourteen of
    #: the fifteen departments, because only IT & Software has them.
    seniority_notes: dict[str, str]
    source: str


def _department_data() -> Mapping[str, Any]:
    raw = layers.runbook_value("department_models", "departments")
    if not isinstance(raw, Mapping) or not raw:
        raise layers.RunbookDataUnavailable(
            "runbook_data/department_models.yaml has no 'departments' mapping. "
            "Part VI's fifteen department models are not restated in code."
        )
    return raw


@lru_cache(maxsize=1)
def runbook_departments() -> dict[str, RunbookDepartment]:
    """Part VI's fifteen department models. Raises if the data is unreachable.

    Cached because it is read on every matrix generation and the underlying
    file does not change at runtime. `cache_clear` exists on this function for
    a test that needs to swap the data.
    """
    built: dict[str, RunbookDepartment] = {}
    for key, entry in _department_data().items():
        menu_source = str(entry.get("competency_menu_source") or entry.get("source") or "")
        menu = tuple(
            RunbookCompetency(
                id=str(row["id"]),
                name=str(row["competency"]),
                observable_evidence=str(row.get("observable_evidence") or ""),
                source=menu_source,
            )
            for row in entry.get("competency_menu") or ()
        )
        built[str(key)] = RunbookDepartment(
            key=str(key),
            number=int(entry["number"]),
            title=str(entry["title"]),
            role_families=tuple(str(f) for f in entry.get("role_families") or ()),
            menu=menu,
            baseline_weight_family=str(entry.get("baseline_weight_family") or ""),
            seniority_notes={
                str(row["level"]): str(row["emphasis_shift"])
                for row in entry.get("seniority_notes") or ()
            },
            source=str(entry.get("source") or ""),
        )
    return built


def runbook_competency_menu(department_key: str) -> tuple[RunbookCompetency, ...]:
    """One department's Part VI menu, or a raise for an unknown department.

    NO GENERIC FALLBACK. Naming a civil engineer's competencies from IT &
    Software's menu, or from a generic one, is the vocabulary collapse the
    department models exist to prevent, and it would look like a successful
    lookup at every call site.
    """
    departments = runbook_departments()
    try:
        return departments[department_key].menu
    except KeyError as exc:
        raise KeyError(
            f"No Part VI department model named {department_key!r}; the Runbook "
            f"carries {sorted(departments)}"
        ) from exc


def baseline_dimension_weights(family: str, band: str) -> dict[str, float]:
    """§11.1's Layer 1 baseline vector for a department family and seniority band.

    `band` is the RUNBOOK's own label ("5-10 yrs", "Eng leadership", "CXO"), not
    this codebase's four-grade vocabulary. The two do not correspond and the
    mapping is deliberately left at the call site rather than being buried here,
    so that a reader can see which Runbook row a job was weighted against
    instead of trusting a translation nobody reviewed. See the module docstring.

    Returns D1..D5 keys, because that is how §11.1 prints them; use
    `situations.DIMENSION_BY_RUNBOOK_ID` to name them.
    """
    weights = layers.runbook_value("department_models", "baseline_weight_families", family, "weights")
    if not isinstance(weights, Mapping):
        raise layers.RunbookDataUnavailable(
            f"runbook_data/department_models.yaml has no §11.1 weight table for "
            f"family {family!r}."
        )
    if band not in weights:
        raise KeyError(
            f"§11.1's {family!r} table has no seniority band {band!r}; it "
            f"carries {sorted(weights)}. The bands differ between families and "
            f"are not interchangeable."
        )
    return {str(k): float(v) for k, v in weights[band].items()}


# ── Rubric anchors: §9.1 to §9.5, per DIMENSION ─────────────────────────────
#
# WHERE THE RUNBOOK ACTUALLY PUTS RUBRIC ANCHORS, which is not where this
# module put them. §9.1 to §9.5 each carry one six-band scoring-anchor table
# over 0 to 100, and those tables are UNIVERSAL: they are stated once per
# dimension and never restated per department or per seniority. Section 57.3
# names "retrieved rubric anchors from the department model" as an evaluator
# input, which is what led the pre-Runbook implementation to build them per
# department; the anchors that exist are the dimension ones, and the department
# model supplies the COMPETENCY SET the evaluator applies them to.
#
# Exactly one department carries anything per seniority: §21.11's "Seniority
# notes" table for IT & Software, and it is an EMPHASIS SHIFT ("5-10: system
# design; production ownership; influence"), not a rubric anchor. Fourteen
# departments have no per-seniority material at all.


@dataclass(frozen=True)
class RubricBand:
    """One row of a §9.x scoring-anchor table."""

    #: The band as the Runbook prints it, e.g. "75-89". Kept as a string
    #: alongside the numbers so a citation can quote the document verbatim.
    band: str
    low: int
    high: int
    meaning: str


def dimension_rubric_anchors(runbook_dimension_id: str) -> tuple[RubricBand, ...]:
    """§9.x's six scoring anchors for one dimension. Raises if absent.

    `runbook_dimension_id` is D1..D5, the Runbook's own naming; use
    `situations.RUNBOOK_ID_BY_DIMENSION` to convert from this codebase's names.

    These are the anchors an evaluator is actually given. They carry NUMBERS,
    which is correct and stays internal: §9.x's bands are 0 to 100 and
    `services/rating.py` converts to the four words a client reads. No caller
    may render one.
    """
    anchors = layers.runbook_value(
        "dimensions", "dimensions", runbook_dimension_id, "rubric_anchors"
    )
    if not isinstance(anchors, list) or not anchors:
        raise layers.RunbookDataUnavailable(
            f"runbook_data/dimensions.yaml has no rubric_anchors for "
            f"{runbook_dimension_id!r}. §9.1 to §9.5 state them once per "
            f"dimension and they are not restated in code."
        )
    return tuple(
        RubricBand(
            band=str(row["band"]),
            low=int(row["low"]),
            high=int(row["high"]),
            meaning=str(row["meaning"]),
        )
        for row in anchors
    )


def seniority_emphasis(department_key: str) -> dict[str, str]:
    """§21.11's seniority notes, for the one department that has them.

    Returns an EMPTY MAPPING for the fourteen departments that carry none,
    because that is the true answer and inventing a shift per seniority is what
    this module did before. An empty result is a caller's cue to fall back to
    the dimension anchors, which are universal and always present.
    """
    department = runbook_departments().get(department_key)
    if department is None:
        raise KeyError(
            f"No Part VI department model named {department_key!r}; the Runbook "
            f"carries {sorted(runbook_departments())}"
        )
    return dict(department.seniority_notes)
