"""The PRISM Report's one composer: assemble, render what is cited, check support.

    compose_prism
      -> synthesis.assemble          every statement, unrendered, with its refs
      -> citations.render_collect    render the cited, WITHHOLD the rest
      -> support.assess_many         does the cited text support the statement?
      -> ComposedPrism               sections, trail, review findings

WHAT CHANGED, AND WHAT DID NOT (Vivekium release, P5-D8 and P5-D9)
--------------------------------------------------------------------
Before this module the composer rendered in the RAISING mode, so a single
uncited statement failed the scoring task and nothing was written: a candidate
who had finished the assessment got no report and nobody was told. Now:

  * an uncited or fabricated-citation statement is WITHHELD: not rendered, not
    in the trail, reported as a finding with no prose, the report flagged for
    human review, and `prism.statement_withheld_for_review` logged at ERROR for
    the CloudWatch metric filter and alarm to count;
  * every statement Siddhi's writer produced about the candidate's evidence
    (a rated remark, the Overall remark, a gap probe) gets a SUPPORT verdict,
    and `unsupported` flags the report and puts a words-only marker beside it;
  * text a fixed template wrote in place of a model (a remark or a probe) is
    a `template_output` finding, and flags the report too.

What did NOT change is the rule: nothing uncited is ever rendered as cited.

WHICH STATEMENTS ARE SUPPORT-CHECKED, AND WHY NOT ALL OF THEM
---------------------------------------------------------------
`SUPPORT_CHECKED` is the closed list, and it is the prose a model writes FROM
evidence. It is not the grade statements: "Kafka: Matching" is a verdict, not a
paraphrase, and its support is the quality gate's comparison with Miti's grade.
It is not the deterministic sections either (the claim summary quotes the
ledger's claim verbatim and its evidence sentences come from a catalogue;
the validation points are a catalogue): checking catalogue text for semantic
support would flag every report for a sentence no model wrote, and a check that
fires on every report is a blanket verdict, which is the failure the quality
gate's own history records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services.siddhi import citations, support, synthesis
from app.services.siddhi.evidence import (
    KIND_ANSWER,
    KIND_PASSAGE,
    KIND_QUESTION,
    EvidenceIndex,
    EvidenceNode,
)
from app.services.siddhi.remarks import SOURCE_TEMPLATE

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORT_CHECKED",
    "TRAIL_VERSION",
    "WITHHELD_LOG_EVENT",
    "ComposedPrism",
    "compose_prism",
]

#: (section key, statement kind) pairs whose statements get a support verdict.
SUPPORT_CHECKED: frozenset[tuple[str, str]] = frozenset(
    {(section, citations.KIND_FINDING) for section in synthesis.RATED_SECTIONS}
    | {("overall", citations.KIND_FINDING), ("gap_analysis", citations.KIND_PROBE)}
)

#: The trail's shape version. 1 (implicit, absent) is every report written
#: before the Vivekium release: no locators, no support, no items. The read
#: model in `siddhi.trail` reads both.
TRAIL_VERSION = 2

#: The ERROR log event a withheld statement produces. A CloudWatch metric
#: filter matches this exact token (infra/modules/observability), so it is a
#: constant and a test pins the log line to it.
WITHHELD_LOG_EVENT = "prism.statement_withheld_for_review"

#: The node kinds whose text a statement's support is judged against.
_TEXT_KINDS = frozenset({KIND_ANSWER, KIND_PASSAGE})

_SEVERITY_HIGH = "high"
_SEVERITY_MEDIUM = "medium"


@dataclass
class ComposedPrism:
    """What the composer produced: the rendered report and what it held back."""

    #: `render_collect` output, each checked statement annotated with
    #: `support`. Every statement in it is cited.
    sections: list[dict[str, Any]] = field(default_factory=list)
    note: synthesis.ReadyPickNote = field(
        default_factory=lambda: synthesis.ReadyPickNote("")
    )
    index: EvidenceIndex = field(default_factory=EvidenceIndex)
    withheld: tuple[citations.Withheld, ...] = ()
    #: Where a fixed template stood in for a model: `remark:<item>`,
    #: `remark:overall`, `probes:<item>`.
    templates: tuple[str, ...] = ()

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self.index.refs))

    def statements(self) -> list[tuple[str, dict[str, Any]]]:
        """(section key, rendered statement) for every rendered statement."""
        return [
            (section["key"], statement)
            for section in self.sections
            for statement in section["statements"]
        ]

    def unsupported(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (key, statement)
            for key, statement in self.statements()
            if (statement.get("support") or {}).get("level")
            == support.LEVEL_UNSUPPORTED
        ]

    @property
    def needs_human_review(self) -> bool:
        """Anything withheld, unsupported, or written by a template."""
        return bool(self.withheld or self.unsupported() or self.templates)

    def review_findings(self) -> list[dict[str, str]]:
        """The report row's findings: issue, location, severity, recommendation.

        NO PROSE, ever. `review_findings_json` is read from far more places
        than the report itself; a finding naming the sentence it is about
        would deliver a withheld sentence by another door.
        """
        findings: list[dict[str, str]] = []
        for held in self.withheld:
            fabricated = held.problem == citations.PROBLEM_UNKNOWN_EVIDENCE
            findings.append(
                {
                    "severity": _SEVERITY_HIGH,
                    "issue": "fabricated_citation" if fabricated else "uncited_statement",
                    "location": _location(held.section, held.item or held.kind),
                    "recommendation": (
                        "A statement about this area could not be traced to "
                        "the candidate's evidence and was withheld from the "
                        "report. Read the candidate's answers for this area "
                        "before relying on the report."
                    ),
                }
            )
        for key, statement in self.unsupported():
            findings.append(
                {
                    "severity": _SEVERITY_MEDIUM,
                    "issue": "citation_unsupported",
                    "location": _location(key, str(statement.get("item") or "")),
                    "recommendation": (
                        "The answers this statement cites do not support it. "
                        "Read the transcript for this area before relying on "
                        "the statement."
                    ),
                }
            )
        for where in self.templates:
            findings.append(
                {
                    "severity": _SEVERITY_MEDIUM,
                    "issue": "template_output",
                    "location": f"report.{where}",
                    "recommendation": (
                        "This text was written from a fixed template because "
                        "the writing model was unavailable. Read the "
                        "candidate's answers before relying on it."
                    ),
                }
            )
        return findings

    def trail(self) -> dict[str, Any]:
        """The audit shape persisted alongside the immutable report.

        Statement text is included and evidence EXCERPTS are not: the trail
        answers "what did this sentence rest on" with the sentence, the refs
        and the durable locators, and never with the transcript the locators
        point at. Resolving a locator to text happens at read time, behind the
        transcript capability (`siddhi.trail.resolve_evidence`).
        """
        return {
            "version": TRAIL_VERSION,
            "evidence_nodes": [node.as_dict() for node in self.index.nodes],
            "statements": [
                {
                    "section": key,
                    "kind": statement["kind"],
                    "item": statement.get("item", ""),
                    "text": statement["text"],
                    "evidence_refs": statement["evidence_refs"],
                    "support": statement.get("support"),
                }
                for key, statement in self.statements()
                if statement["kind"] in citations.REQUIRES_CITATION
            ],
            "withheld": [held.as_dict() for held in self.withheld],
        }

    def siddhi_namespace(self) -> dict[str, Any]:
        """What the report row keeps under `gap_analysis_json["siddhi"]`."""
        return {
            "citations": self.trail(),
            "ready_pick_note": self.note.as_dict(),
            "review": {
                "needs_human_review": self.needs_human_review,
                "findings": self.review_findings(),
            },
        }


def _location(section: str, item: str) -> str:
    return f"report.{section}.{item}" if item else f"report.{section}"


def _templates(
    dimensions: Sequence[Mapping[str, Any]],
    gap_groups: Sequence[Mapping[str, Any]],
    overall_remark_source: str | None,
) -> tuple[str, ...]:
    found: list[str] = []
    for row in dimensions:
        if row.get("remark_provenance") == SOURCE_TEMPLATE and row.get("name"):
            found.append(f"remark:{row['name']}")
    if overall_remark_source == SOURCE_TEMPLATE:
        found.append("remark:overall")
    for group in gap_groups:
        for entry in group.get("items") or []:
            if entry.get("probes_source") == SOURCE_TEMPLATE and entry.get("name"):
                found.append(f"probes:{entry['name']}")
    return tuple(dict.fromkeys(found))


def _support_text(index: EvidenceIndex, refs: Iterable[str]) -> tuple[str, ...]:
    """The texts a statement's support is judged against.

    The cited answers and passages, and the QUESTION each cited answer was
    given to: a remark may legitimately restate what was asked ("asked about
    rollback, they described..."), and holding it to the answer alone would
    call the question's own words invented.
    """
    texts: list[str] = []
    for ref in refs:
        node = index.node(ref)
        if node is None or node.kind not in _TEXT_KINDS:
            continue
        texts.append(node.support_text)
        if node.kind == KIND_ANSWER:
            question = _question_for(index, node)
            if question is not None:
                texts.append(question.support_text)
    return tuple(text for text in texts if text)


def _question_for(index: EvidenceIndex, answer: EvidenceNode) -> EvidenceNode | None:
    """The question node of the same exchange: same item, same position."""
    suffix = answer.ref.split(":", 1)[1] if ":" in answer.ref else ""
    return index.node(f"{KIND_QUESTION}:{suffix}") if suffix else None


def _subjects(
    section: str, item: str, rated: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """The names a statement may use without that being an invention or an anchor."""
    if section == "overall":
        return tuple(str(row["name"]) for row in rated)
    return (item,) if item else ()


async def compose_prism(
    *,
    dimensions: Sequence[Mapping[str, Any]],
    evidence_by_item: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    gap_groups: Sequence[Mapping[str, Any]] = (),
    focus_summary: str = "",
    overall_summary: str | None = None,
    overall_grade: str | None = None,
    overall_remark_source: str | None = None,
    validation: Mapping[str, Any] | None = None,
    validation_points: Mapping[str, Any] | None = None,
    claim_evidence: Mapping[str, Any] | None = None,
    extra_nodes: Sequence[EvidenceNode] = (),
    passages: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    embed: support.Embedder | None,
    threshold: float | None = None,
) -> ComposedPrism:
    """Compose the PRISM Report. Never raises over a statement it cannot cite.

    `embed` is REQUIRED (keyword, may be None): the caller states whether a
    semantic tier exists, normally `support.semantic_embedder()`. None is a
    real state and is recorded on every statement it leaves undecided.

    Raises only for a programming error (an unknown statement kind, a malformed
    passage): those are defects in the code, not outcomes of a run.
    """
    assembled = synthesis.assemble(
        dimensions=dimensions,
        evidence_by_item=evidence_by_item,
        gap_groups=gap_groups,
        focus_summary=focus_summary,
        overall_summary=overall_summary,
        overall_grade=overall_grade,
        validation=validation,
        validation_points=validation_points,
        claim_evidence=claim_evidence,
        extra_nodes=extra_nodes,
        passages=passages,
    )
    sections, withheld = assembled.report.render_collect()
    for held in withheld:
        logger.error(
            "%s section=%s kind=%s item=%s problem=%s",
            WITHHELD_LOG_EVENT, held.section, held.kind, held.item, held.problem,
        )

    requests: list[support.SupportRequest] = []
    for section_index, section in enumerate(sections):
        for statement_index, statement in enumerate(section["statements"]):
            if (section["key"], statement["kind"]) not in SUPPORT_CHECKED:
                continue
            requests.append(
                support.SupportRequest(
                    key=(section_index, statement_index),
                    statement=statement["text"],
                    excerpts=_support_text(
                        assembled.index, statement["evidence_refs"]
                    ),
                    subject_terms=_subjects(
                        section["key"], str(statement.get("item") or ""), assembled.rated
                    ),
                )
            )
    verdicts = await support.assess_many(requests, embed=embed, threshold=threshold)
    for (section_index, statement_index), verdict in verdicts.items():
        sections[section_index]["statements"][statement_index]["support"] = (
            verdict.as_dict()
        )
        if verdict.level == support.LEVEL_UNSUPPORTED:
            logger.warning(
                "prism.statement_unsupported section=%s item=%s reason=%s",
                sections[section_index]["key"],
                sections[section_index]["statements"][statement_index].get("item"),
                verdict.reason,
            )

    return ComposedPrism(
        sections=sections,
        note=assembled.note,
        index=assembled.index,
        withheld=tuple(withheld),
        templates=_templates(dimensions, gap_groups, overall_remark_source),
    )
