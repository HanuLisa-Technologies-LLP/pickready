"""One SWOT input, through all seven stages, into one grade with its citations.

spec-doc5's final-report requirement, made runnable:

    "a worked example showing one real SWOT input moving one real weight through
     the full seven-stage pipeline into one real grade, with the evidence
     citations that grade would carry in the delivered report."

    python -m app.scripts.worked_example

WHY THIS IS A SCRIPT AND NOT A PARAGRAPH IN A DOCUMENT
--------------------------------------------------------
A paragraph describing what the pipeline does is a paragraph that stops being
true the first time somebody edits a weight and does not reread it. This runs
the actual modules -- `hiring.transformation`, `miti.pipeline`,
`miti.aggregation`, `siddhi.citations` -- so its output cannot drift from the
code without the run changing.

It needs NO DATABASE and NO PROVIDER. The five dimension evaluators are
stubbed with a scripted response, which is the honest arrangement for a
demonstration of MECHANISM: the point is to show a weight moving and a citation
being enforced, and a live model would make the output vary run to run without
demonstrating anything extra. Every arithmetic step it shows is the real
arithmetic.
"""
from __future__ import annotations

import asyncio
import json

from app.services import rating
from app.services.hiring import company_dna, gates, situations, transformation
from app.services.hiring.department_models import department_for
from app.services.miti import aggregation, pipeline
from app.services.miti.dimensions import EvidenceView
from app.services.siddhi import citations

RULE = "=" * 78
THIN = "-" * 78


def _h(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


# ── The scenario ─────────────────────────────────────────────────────────────

JOB_TITLE = "Engineering Manager, Platform"
SENIORITY = "managerial"

#: What the Hiring Manager actually said, in the Weaknesses quadrant. One real
#: sentence, of the kind `swot_quality.review` accepts: it describes an event,
#: not a trait.
SWOT_INPUT = (
    "The last person in this role never owned anything in production - when the "
    "scheduler fell over at 2am it was always somebody else who got paged, and "
    "eventually the team stopped trusting what they shipped."
)

#: The evidence the assessment gathered. Two INDEPENDENT source groups, which is
#: what separates corroboration from one person repeating themselves.
EVIDENCE = [
    EvidenceView(
        ref="ev-101",
        text=(
            "I was on the rotation for the scheduler. The worst one started as a "
            "latency alert and turned out to be a partition rebalance storm; we "
            "had it wrong for about forty minutes because the obvious answer was "
            "a slow query. Afterwards I added the consumer-lag panel that would "
            "have shown it in two minutes."
        ),
        trust="observed",
        source_kind="assessment_answer",
        independence_group="candidate",
        freshness="current",
    ),
    EvidenceView(
        ref="ev-102",
        text="Employer confirmed the role, the dates and the on-call rotation.",
        trust="authoritative",
        source_kind="employer_verification",
        independence_group="employer",
        freshness="recent",
    ),
]


async def _evaluator(task_type, messages, response_format_json=False):
    """A scripted stand-in for the five isolated evaluators.

    Returns a band and its citations. The ISOLATION is real even here: this
    function is handed `messages` built by `dimensions.render_prompt`, which can
    only see what `EvaluatorInput` carries -- and that dataclass has no field for
    a candidate name, another dimension's score, or the composite.
    """
    body = messages[1]["content"]
    if "ev-101" in body or "ev-102" in body:
        return json.dumps(
            {
                "band": "solid",
                "rationale": (
                    "Narrates the incident from before the cause was known and "
                    "names a change that outlived it."
                ),
                "evidence_refs": ["ev-101", "ev-102"],
                "insufficient_evidence": False,
                # Per-competency, so the band lands in the category the hiring
                # manager put this competency in rather than in whichever
                # category the dimension's fallback table names.
                "per_competency": {"Operating what they built": "solid"},
            }
        )
    # No evidence routed to this dimension. INSUFFICIENT, never a low band --
    # that distinction is the fairness rule this pipeline is built around.
    return json.dumps(
        {"band": "partial", "evidence_refs": [], "insufficient_evidence": True}
    )


def main() -> int:
    department = department_for(JOB_TITLE)

    _h("0. THE INPUT")
    print(f"Job          : {JOB_TITLE}  ({SENIORITY})")
    print(f"Department   : {department.label}  (Layer 1 model: {department.key})")
    print(f"\nSWOT weakness, as the Hiring Manager said it:\n  \"{SWOT_INPUT}\"")

    # ── Layer 3a: the situation type ────────────────────────────────────────
    proposals = situations.classify_signals([SWOT_INPUT])
    situation_key = situations.TURNAROUND
    print(f"\nSignals point at : {[k for k, _n, _m in proposals] or '(none)'}")
    print(f"Classified as    : {situations.SITUATIONS[situation_key].label}")
    print("\nWhat Bodha reads back before closing the session:")
    print(
        "  "
        + situations.confirmation_prompt(
            situation_key, evidence=["the team no longer trusting what shipped"]
        )
    )

    # ── Layer 2: the client's compiled philosophy ───────────────────────────
    dna = company_dna.compile_artifact(
        {
            # "Almost always the proven one."
            "proven_vs_potential": -2,
            "corroboration": "No, we want it corroborated somewhere else",
            "overall_bar": "Only people we would seriously consider hiring",
            "stale_experience": "Within about three years",
        }
    )
    _h("1-7. THE SEVEN-STAGE TRANSFORMATION")

    baseline_item = transformation.build_item(
        phrase=SWOT_INPUT,
        category="must_have",
        department=department,
        seniority=SENIORITY,
        swot_origin=SWOT_INPUT,
    )
    item = transformation.build_item(
        phrase=SWOT_INPUT,
        category="must_have",
        department=department,
        seniority=SENIORITY,
        company=dna,
        situation_key=situation_key,
        swot_origin=SWOT_INPUT,
    )

    print(f"Stage 1  COMPETENCY          : {item.name}")
    print(f"         named from Layer 1  : {item.anchor_key}")
    print(f"         internal dimension  : {item.dimension}")
    print(f"\nStage 2  OBSERVABLE EVIDENCE : {item.observable_evidence}")
    print(f"\nStage 3  EVIDENCE SOURCES    : {', '.join(item.evidence_sources)}")
    print(f"Stage 4  ASSESSMENT METHOD   : {item.assessment_method}")
    if item.unreachable_sources:
        print(f"         out of band         : {', '.join(item.unreachable_sources)}")

    terms = item.weight.as_dict()["terms"]
    print("\nStage 5  WEIGHT")
    print(THIN)
    print(f"  Layer 1  department baseline ({item.anchor_key})   x {terms['baseline_layer1']}")
    print(f"  Layer 2  company DNA (hires proven over potential)  x {terms['company_layer2']}")
    print(f"  Layer 3  situation type ({situations.SITUATIONS[situation_key].label})       x {terms['situation_layer3']}")
    print(f"  Layer 3  this SWOT's own emphasis                   x {terms['role_layer3']}")
    print(THIN)
    print(f"  WEIGHT = {item.weight.value:.4f}")
    print(f"  (with no Layer 2 and no Layer 3 it would be {baseline_item.weight.value:.4f})")
    print(
        f"  -> the two layers moved it by "
        f"{item.weight.value - baseline_item.weight.value:+.4f}"
    )

    print(f"\nStage 6  THRESHOLD           : {item.threshold.as_dict()}")
    print(f"Stage 7  DISQUALIFIER        : {item.disqualifier or '(none applicable)'}")
    print(f"\n         stages completed    : {len(transformation.REQUIRED_STAGES)}/6 required")
    print(f"         is_complete()       : {item.is_complete()}")

    # ── Miti ────────────────────────────────────────────────────────────────
    _h("MITI: FIVE ISOLATED EVALUATORS -> DETERMINISTIC AGGREGATION")

    inputs = pipeline.EvaluationInputs(
        matrix={item.name: item.category},
        competency_dimensions={item.name: item.dimension},
        competency_weights={item.dimension: item.weight.value},
        evidence=EVIDENCE,
        evidence_competencies={"ev-101": [item.name], "ev-102": [item.name]},
        rubric_anchor=item.rubric_anchor,
        role_context=(
            f"{situations.SITUATIONS[situation_key].label}: "
            f"{situations.SITUATIONS[situation_key].description}"
        ),
        matrix_items=[item.as_dict()],
        scorecard_approved_at="2026-08-28T09:00:00Z",
        must_have_grades={item.name: rating.GRADE_MATCHING},
    )

    outcome = asyncio.run(pipeline.evaluate(inputs, invoke=_evaluator))

    print("Dimension results (INTERNAL - never rendered, never named to a client):")
    for result in outcome.results:
        status = "insufficient" if result.insufficient_evidence else result.band
        print(f"  {result.dimension:26s} {status:14s} cites {list(result.evidence_refs)}")

    aggregate = outcome.aggregate
    print("\nAggregation (deterministic - zero model calls):")
    print(THIN)
    print(f"  raw composite         : {aggregate.raw_composite:.2f}")
    print(f"  authenticity x{aggregate.authenticity_factor:.2f}    : {aggregate.adjusted_composite:.2f}"
          f"   ({aggregate.authenticity_reason})")
    print(f"  Must-have hard cap    : {'APPLIED' if aggregate.must_have_cap_applied else 'not triggered'}")
    print(f"  confidence            : {aggregate.confidence}")
    print(f"  insufficient          : {aggregate.insufficient_dimensions or '(none)'}")
    print(THIN)
    print(f"  OVERALL GRADE         : {aggregate.overall_grade}")

    print("\nGates:")
    for gate in outcome.gate_results:
        mark = "PASS" if gate.passed else "FAIL"
        blocking = "blocking" if gate.blocking else "non-blocking"
        print(f"  {gate.gate:26s} {mark}  ({blocking})")
        for reason in gate.reasons:
            print(f"      - {reason}")
    print(f"\n  deliverable           : {outcome.deliverable}")
    if not outcome.deliverable:
        print("  blocked by            :")
        for reason in outcome.blocking_reasons:
            print(f"      - {reason}")

    # ── What the client sees ────────────────────────────────────────────────
    _h("WHAT THE CLIENT ACTUALLY SEES")
    projection = outcome.client_projection()
    print(json.dumps(projection, indent=2))
    rendered = json.dumps(projection)
    print(f"\n  contains a digit?         {any(c.isdigit() for c in rendered)}")
    print(f"  names an internal dim?    "
          f"{any(d in rendered for d in ('verified_competence', 'trajectory_potential'))}")

    # ── Citation enforcement ────────────────────────────────────────────────
    _h("SIDDHI: THE CITATIONS THIS GRADE WOULD CARRY")

    report = citations.Report(known_refs=frozenset(aggregate.evidence_refs))
    section = report.section("overall", "Overall Assessment")
    section.add(citations.Statement(citations.KIND_HEADING, "Overall Assessment"))
    section.add(
        citations.Statement(
            citations.KIND_GRADE,
            f"{item.name}: {aggregate.category_grades.get('must_have', 'n/a')}",
            ("ev-101", "ev-102"),
        )
    )
    section.add(
        citations.Statement(
            citations.KIND_FINDING,
            (
                "They narrate a production incident from before the cause was "
                "known, and name a change that outlived it."
            ),
            ("ev-101",),
        )
    )
    gap = report.section("gap", "Gap Analysis & Action Plan")
    gap.add(
        citations.Statement(
            citations.KIND_PROBE,
            (
                "Ask what the consumer-lag panel would have shown at minute two, "
                "and what they would have done differently with it."
            ),
            ("ev-101",),
        )
    )

    for block in report.render():
        print(f"\n  [{block['title']}]")
        for statement in block["statements"]:
            refs = statement["evidence_refs"] or "-"
            print(f"    ({statement['kind']:10s}) cites {refs}")
            print(f"       {statement['text']}")

    print("\n  Now trying to add an UNCITED finding to the same report:")
    gap.add(
        citations.Statement(
            citations.KIND_FINDING,
            "They would probably be a strong cultural fit.",
            (),
        )
    )
    try:
        report.render()
    except citations.UncitedStatement as exc:
        print(f"    BLOCKED: {type(exc).__name__}")
        print(f"    {str(exc)[:150]}")
    else:  # pragma: no cover -- the whole point is that this cannot happen
        print("    NOT BLOCKED -- citation enforcement is broken")
        return 1

    _h("END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
