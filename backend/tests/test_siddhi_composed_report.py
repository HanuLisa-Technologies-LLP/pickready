"""What leaves the generator: the audit trail, and the ban at its boundary.

`ComposedReport` is the object that survived the citation chokepoint, and two
things about it are contracts rather than conveniences.

THE TRAIL CARRIES SENTENCES AND LOCATORS, NEVER EXCERPTS. It exists so a reader
can ask "what did this sentence rest on", and answering that needs the sentence
and the locator, never the transcript the locator points at. A trail that
inlined excerpts would quietly become a second copy of the candidate's answers,
living in the audit record where nobody is looking for candidate data.

THE NUMBER BAN IS RE-EXPORTED AT THIS BOUNDARY on purpose: a caller reaching for
the generator should not have to know which module the ban lives in, and one
import site is one fewer place a future export format can forget.

No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.siddhi import citations, numbers, synthesis
from app.services.siddhi.evidence import EvidenceIndex


def _report_with(sections: list[dict], index: EvidenceIndex) -> synthesis.ComposedReport:
    return synthesis.ComposedReport(sections=sections, index=index)


# ── The refs the report can cite ─────────────────────────────────────────────


def test_evidence_refs_are_sorted_and_come_from_the_index() -> None:
    """Sorted so two runs over the same evaluation produce the same audit row;
    an unordered set would make a diff of two trails unreadable."""
    index = EvidenceIndex.build(
        items=["Kafka", "Postgres"],
        exchanges={"Kafka": [{"question": "q", "answer": "a"}]},
    )
    report = _report_with([], index)
    assert report.evidence_refs == tuple(sorted(index.refs))
    assert list(report.evidence_refs) == sorted(report.evidence_refs)


def test_a_report_with_no_evidence_reports_no_refs() -> None:
    assert synthesis.ComposedReport().evidence_refs == ()


# ── The audit trail ──────────────────────────────────────────────────────────


def _section(key: str, statements: list[dict]) -> dict:
    return {"key": key, "title": key.title(), "statements": statements}


def test_the_trail_pairs_each_cited_sentence_with_its_locators() -> None:
    index = EvidenceIndex.build(
        items=["Kafka"], exchanges={"Kafka": [{"question": "q", "answer": "a"}]}
    )
    refs = list(index.grounding("Kafka"))
    sections = [
        _section(
            "overall",
            [
                {
                    "kind": next(iter(citations.REQUIRES_CITATION)),
                    "text": "Matching on the evidence recorded.",
                    "evidence_refs": refs,
                }
            ],
        )
    ]
    trail = _report_with(sections, index).trail()

    assert [node["ref"] for node in trail["evidence_nodes"]] == [
        node.ref for node in index.nodes
    ]
    assert len(trail["statements"]) == 1
    entry = trail["statements"][0]
    assert entry["section"] == "overall"
    assert entry["text"] == "Matching on the evidence recorded."
    assert entry["evidence_refs"] == refs


def test_the_trail_carries_no_evidence_excerpt() -> None:
    """The property the docstring states. An excerpt here would put the
    candidate's own words into the audit record, which is a different data
    class from a locator and is not what the trail is for."""
    index = EvidenceIndex.build(
        items=["Kafka"],
        exchanges={"Kafka": [{"question": "q", "answer": "rebalanced the partitions"}]},
    )
    trail = _report_with([], index).trail()
    rendered = repr(trail)
    assert "rebalanced the partitions" not in rendered


def test_a_statement_that_needs_no_citation_stays_out_of_the_trail() -> None:
    """The trail answers "what did this sentence rest on". A heading rests on
    nothing, and listing it with an empty ref list would read as a citation
    failure rather than as a sentence that never needed one."""
    uncited_kind = next(
        kind
        for kind in ("heading", "label", "caption", "title")
        if kind not in citations.REQUIRES_CITATION
    )
    sections = [
        _section(
            "overall",
            [{"kind": uncited_kind, "text": "Overall", "evidence_refs": []}],
        )
    ]
    assert _report_with(sections, EvidenceIndex()).trail()["statements"] == []


# ── The ban, at the generator's own boundary ─────────────────────────────────


def test_a_clean_payload_passes_the_boundary_check() -> None:
    synthesis.assert_deliverable(
        {"remark": "Matching, with clear examples of owned delivery."},
        where="test",
    )


def test_a_score_shaped_payload_is_refused_at_the_boundary() -> None:
    """The same refusal `numbers.assert_clean` raises, reachable without the
    caller knowing which module the ban lives in."""
    with pytest.raises(numbers.NumberInDeliveredReport):
        synthesis.assert_deliverable(
            {"remark": "You scored 82/100 overall."}, where="test"
        )


def test_the_refusal_names_where_it_happened() -> None:
    """A ban that fires without saying which surface tripped it leaves somebody
    grepping every renderer."""
    with pytest.raises(numbers.NumberInDeliveredReport) as excinfo:
        synthesis.assert_deliverable(
            {"remark": "You are in the top 12% of applicants."},
            where="the_pdf_renderer",
        )
    assert "the_pdf_renderer" in str(excinfo.value)
