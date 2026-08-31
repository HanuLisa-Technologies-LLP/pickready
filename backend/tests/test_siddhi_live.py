"""Siddhi on the LIVE path: citation enforcement, grounded gaps, and gate G4.

    spec-doc6 §4.5: "Citation enforcement is architectural. The generator
    physically cannot emit a statement without a citation to an evidence node.
    The existing test that tries to emit an uncited statement and confirms it is
    blocked must now run against the live generation path."

`tests/test_siddhi_citations.py` proves the chokepoint works when a caller uses
it directly. That is a property of the module. THIS file proves the generator
the product actually runs goes through it, which is a property of the wiring,
and the wiring is the half that was missing: before this phase the only
non-test importer of the whole Siddhi package was a worked example.

THE LIVE PATH, NAMED
----------------------
    functional_assessment.synthesis_node
      -> gap_analysis.build_gap_analysis          (the assessment graph calls this)
        -> siddhi.synthesis.compose
          -> citations.Report.render              THE CHOKEPOINT

Every test below enters at `build_gap_analysis`. None of them constructs a
`citations.Report` by hand, because a test that did would be testing the module
again rather than the path.
"""
from __future__ import annotations

import json
import re

import pytest

from app.schemas.assessments import GapAnalysisOut
from app.services import gap_analysis, ppi
from app.services.siddhi import citations, delivery, evidence, synthesis


def _boom(monkeypatch) -> None:
    """No provider. Every test here is about structure, not about prose."""

    async def _fail(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _fail)


def _dimensions() -> list[dict]:
    return [
        {
            "category": ppi.CATEGORY_MUST_HAVE,
            "name": "Distributed Systems",
            "score": 88,
            "required_level": 90,
            "ordinal": 1,
            "remark": "Owned the migration end to end and named the rollback.",
        },
        {
            "category": ppi.CATEGORY_MUST_HAVE,
            "name": "Kafka",
            "score": 30,
            "required_level": 90,
            "ordinal": 2,
            "remark": "No owned example of partition rebalancing was offered.",
        },
        {
            "category": ppi.CATEGORY_NICE_TO_HAVE,
            "name": "Observability",
            "score": 65,
            "required_level": 70,
            "ordinal": 1,
            "remark": "Named the dashboards but not what they changed because of one.",
        },
        {
            "category": ppi.CATEGORY_BEHAVIOURAL,
            "name": "Judgement under pressure",
            "score": 92,
            "required_level": 80,
            "ordinal": 1,
            "remark": "Described the call they made first during the outage.",
        },
    ]


def _exchanges() -> dict[str, list[dict[str, str]]]:
    return {
        "Distributed Systems": [
            {
                "question": "Walk me through a migration you owned.",
                "answer": "I moved the orders service onto the new cluster over two sprints.",
            }
        ],
        "Observability": [
            {
                "question": "How do you know a deploy is healthy?",
                "answer": "I watch the error rate dashboard for the first ten minutes.",
            }
        ],
    }


# ── THE ACCEPTANCE CRITERION, ON THE LIVE PATH ───────────────────────────────


@pytest.mark.asyncio
async def test_the_live_generator_renders_through_the_citation_chokepoint(
    monkeypatch,
) -> None:
    """The wiring itself, asserted before anything about its behaviour.

    `compose` is the only thing that turns a rated row into delivered text, and
    `build_gap_analysis` is what the assessment graph calls. If this stops being
    true, every other test in this file is testing a path the product no longer
    runs.
    """
    _boom(monkeypatch)
    seen: dict[str, object] = {}
    real = synthesis.compose

    def _spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(gap_analysis.siddhi_synthesis, "compose", _spy)
    await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    assert seen["dimensions"] == _dimensions()
    assert seen["gap_groups"]


@pytest.mark.asyncio
async def test_producing_an_uncited_statement_is_blocked_on_the_live_path(
    monkeypatch,
) -> None:
    """THE ACCEPTANCE CRITERION. Try to produce an uncited statement through the
    real generator, and confirm the report is not written.

    The uncitable condition is simulated the way it actually arises: an
    evaluation whose evidence set came back empty. That is what a degraded
    dimension evaluator produces, and it is precisely the run where an uncited
    claim is most likely to be wrong. The report is refused rather than shipped
    with a marker, because a PRISM Report whose claims are not traced is not a
    worse report, it is a different product.
    """
    _boom(monkeypatch)
    monkeypatch.setattr(
        synthesis.EvidenceIndex, "build", classmethod(lambda cls, **kw: cls())
    )
    with pytest.raises(citations.UncitedStatement) as caught:
        await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    assert "evidence citation" in str(caught.value)


@pytest.mark.asyncio
async def test_a_fabricated_citation_is_blocked_on_the_live_path_and_named_apart(
    monkeypatch,
) -> None:
    """An empty citation list is a generator that forgot; an unknown ref is a
    generator that INVENTED one. The second is worse because it reads as
    provenance, so it raises a different error class, on the live path too."""
    _boom(monkeypatch)

    class _Fabricating(evidence.EvidenceIndex):
        def grounding(self, item: str) -> tuple[str, ...]:
            return ("answer:not-in-this-evaluation:0",)

    monkeypatch.setattr(
        synthesis,
        "EvidenceIndex",
        type(
            "Patched",
            (_Fabricating,),
            {
                "build": classmethod(
                    lambda cls, **kw: cls(
                        nodes=evidence.EvidenceIndex.build(**kw).nodes
                    )
                )
            },
        ),
    )
    with pytest.raises(citations.UnknownEvidence):
        await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())


@pytest.mark.asyncio
async def test_the_live_generator_has_no_bypass_to_reach_for(monkeypatch) -> None:
    """`compose` takes no `force`, no `strict`, no `allow_uncited`. A bypass
    parameter is a bypass that will be used, in a hotfix, at the end of a
    release, and the way it gets added is somebody hitting this failure once."""
    import inspect

    parameters = set(inspect.signature(synthesis.compose).parameters)
    for forbidden in ("force", "strict", "allow_uncited", "skip_checks", "degraded"):
        assert forbidden not in parameters


@pytest.mark.asyncio
async def test_the_live_generator_does_not_catch_the_chokepoints_errors() -> None:
    """Asserted against the SOURCE, because a `try` around `render` would make
    every other test in this file pass while the product shipped uncited
    reports."""
    import pathlib

    for module in (synthesis, gap_analysis):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "except citations." not in source
        assert "UncitedStatement" not in source.replace(
            "raises `UncitedStatement`", ""
        ).replace("`UncitedStatement`", "")


# ── EVERY STATEMENT CARRIES ITS EVIDENCE ─────────────────────────────────────


@pytest.mark.asyncio
async def test_every_delivered_statement_traces_to_an_evidence_node(
    monkeypatch,
) -> None:
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(
        None,
        _dimensions(),
        _exchanges(),
        overall_summary="Consistent ownership across the platform surface.",
        overall_grade="Matching",
    )
    trail = section["siddhi"]["citations"]
    known = {node["ref"] for node in trail["evidence_nodes"]}
    assert trail["statements"]
    for statement in trail["statements"]:
        assert statement["evidence_refs"], statement
        # The trail's node list is the WHOLE citable set, aspect records
        # included. A ref that is citable but absent from the persisted nodes
        # would be provenance nobody could resolve later.
        assert set(statement["evidence_refs"]) <= known, statement


@pytest.mark.asyncio
async def test_a_gap_statement_carries_the_evidence_that_was_searched(
    monkeypatch,
) -> None:
    """THE ENTRY WORTH DEFENDING.

    Kafka has no exchange recorded against it, so there is no answer to point
    at. The gap is still stated and still cited, to the record that the
    criterion was assessed. That is the whole difference between "we asked about
    this and none of what they said addressed it" and "we never asked", and the
    second is a gap in the assessment being reported as a gap in the candidate.
    """
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    statements = section["siddhi"]["citations"]["statements"]
    kafka = [s for s in statements if "Kafka" in s["text"] or "partition" in s["text"]]
    assert kafka, statements
    for statement in kafka:
        assert statement["evidence_refs"]
        assert any(
            ref.startswith(f"{evidence.KIND_SEARCHED}:")
            for ref in statement["evidence_refs"]
        ), statement


@pytest.mark.asyncio
async def test_a_gap_on_an_answered_item_cites_the_answer_not_the_search_record(
    monkeypatch,
) -> None:
    """A weaker citation must not be used where a stronger one exists, or the
    two kinds of node stop meaning different things."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    statements = section["siddhi"]["citations"]["statements"]
    observability = [s for s in statements if "dashboards" in s["text"]]
    assert observability
    assert all(
        any(ref.startswith(f"{evidence.KIND_ANSWER}:") for ref in s["evidence_refs"])
        for s in observability
    )


@pytest.mark.asyncio
async def test_an_empty_aspect_states_its_absence_of_gaps_and_cites_the_aspect(
    monkeypatch,
) -> None:
    """"No Must-have gaps identified" is a claim about a candidate, not a
    layout decision, and it is a claim that stays true only while somebody
    actually looked at that aspect."""
    _boom(monkeypatch)
    clean = [
        {"category": category, "name": f"Item {index}", "score": 95,
         "required_level": 90, "ordinal": 1, "remark": "Strong evidence throughout."}
        for index, category in enumerate(ppi.CATEGORIES, 1)
    ]
    section = await gap_analysis.build_gap_analysis(None, clean, {})
    statements = section["siddhi"]["citations"]["statements"]
    no_gaps = [s for s in statements if s["text"].startswith("No ")]
    assert no_gaps
    for statement in no_gaps:
        assert statement["evidence_refs"]
        assert all(
            ref.startswith("searched:aspect:") for ref in statement["evidence_refs"]
        )


@pytest.mark.asyncio
async def test_the_verbatim_validation_section_needs_no_citation(monkeypatch) -> None:
    """The candidate's own unrated submission is not a claim about the candidate
    derived from evidence. Requiring a citation would produce a fake one."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(
        None,
        _dimensions(),
        _exchanges(),
        validation={
            "fields": [{"label": "Notice period", "value": "Sixty days"}],
        },
    )
    texts = [s["text"] for s in section["siddhi"]["citations"]["statements"]]
    assert not any("Notice period" in text for text in texts)


# ── THE CITATION TRAIL IS AUDIT, NOT PRODUCT ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_citation_trail_never_crosses_the_api_boundary(
    monkeypatch,
) -> None:
    """A ref identifies a row and authorises nothing, and it is stored so a
    grade can be traced later. It is not something a browser needs."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    assert "siddhi" in section
    delivered = GapAnalysisOut.model_validate(section).model_dump()
    assert "siddhi" not in delivered
    assert set(delivered) == {"focus_summary", "must_have_cap_applied", "groups"}


@pytest.mark.asyncio
async def test_the_trail_records_the_locator_and_never_the_transcript(
    monkeypatch,
) -> None:
    """The trail is read from far more places than the report is. A trail
    carrying answer text would make every reader of the provenance a reader of
    the candidate's conversation."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    serialised = json.dumps(section["siddhi"]["citations"]["evidence_nodes"])
    assert "orders service onto the new cluster" not in serialised
    assert "excerpt" not in serialised


def test_the_same_refs_are_available_to_a_caller_outside_a_section_render() -> None:
    """Siddhi's quality gate reads `claims` as records carrying `evidence_refs`,
    and it is not inside a render. The refs it needs come from the same index
    the generator uses, so a claim can never be grounded in the report and
    ungrounded in the gate that checks the report."""
    refs = synthesis.evidence_refs_for(_dimensions(), _exchanges())
    assert set(refs) == {row["name"] for row in _dimensions()}
    for name, item_refs in refs.items():
        assert item_refs, name
    assert any(
        ref.startswith(f"{evidence.KIND_ANSWER}:")
        for ref in refs["Distributed Systems"]
    )
    # Nothing was answered about Kafka, so its claim rests on the search record.
    assert all(
        ref.startswith(f"{evidence.KIND_SEARCHED}:") for ref in refs["Kafka"]
    )


def test_two_criteria_that_slug_alike_do_not_share_an_evidence_set() -> None:
    """A locator collision would silently file one competency's answers under
    another, and the report would cite provenance belonging to a different
    criterion while every citation check passed."""
    index = evidence.EvidenceIndex.build(
        items=["Kafka / Streaming", "Kafka Streaming"],
        exchanges={
            "Kafka / Streaming": [{"question": "q1", "answer": "a1"}],
            "Kafka Streaming": [{"question": "q2", "answer": "a2"}],
        },
    )
    first = set(index.refs_for("Kafka / Streaming"))
    second = set(index.refs_for("Kafka Streaming"))
    assert first and second
    assert not (first & second)


# ── GENERIC ADVICE, THE BANNED-PHRASE CORPUS ─────────────────────────────────


@pytest.mark.asyncio
async def test_no_generic_advice_reaches_the_gap_section(monkeypatch) -> None:
    """spec-doc6 §4.5. Every probe is grounded in the candidate's own words, and
    the negative form of that rule is that a probe which could have been written
    before the interview never appears.

    Run on the DEGRADED path deliberately: the deterministic fallback is where
    generic advice would come from if it came from anywhere, because a model is
    at least looking at the transcript.
    """
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    probes = [
        probe
        for group in section["groups"]
        for item in group["items"]
        for probe in item["probes"]
    ]
    assert probes
    haystack = " ".join(probes).casefold()
    for phrase in synthesis.GENERIC_ADVICE_PHRASES:
        assert phrase not in haystack, phrase


@pytest.mark.asyncio
async def test_the_whole_rendered_section_is_free_of_the_banned_corpus(
    monkeypatch,
) -> None:
    """Not only the probes. A focus summary, a cap statement or a reused remark
    could carry the same emptiness."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    delivered = json.dumps(GapAnalysisOut.model_validate(section).model_dump()).casefold()
    for phrase in synthesis.GENERIC_ADVICE_PHRASES:
        assert phrase not in delivered, phrase


@pytest.mark.asyncio
async def test_a_generated_probe_that_is_generic_advice_is_rejected_and_rewritten(
    monkeypatch,
) -> None:
    """The corpus is not only a test assertion; it is a rejection reason fed
    back to the generator verbatim, so the model is told and writes it again.
    A corpus checked only in CI would let the defect ship and fail afterwards.
    """
    attempts: list[list[dict]] = []
    grounded = (
        "You mentioned watching the error rate dashboard, so tell me about the "
        "one deploy where that signal was misleading and what you did next after."
    )

    async def _chat(task_type, messages, **kwargs):
        attempts.append(messages)
        payload = (
            {"probes": [
                "This candidate should consider taking a course and work on "
                "your confidence before the next interview round begins in "
                "earnest, then improve your communication skills."
            ]}
            if len(attempts) == 1
            else {"probes": [grounded]}
        )
        return json.dumps(payload)

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _chat)
    section = await gap_analysis.build_gap_analysis(
        None,
        [{"category": ppi.CATEGORY_NICE_TO_HAVE, "name": "Observability",
          "score": 65, "ordinal": 1, "remark": "Named the dashboards only."}],
        _exchanges(),
    )
    assert section["groups"][1]["items"][0]["probes"] == [grounded]
    assert len(attempts) == 2
    correction = attempts[1][-1]["content"].casefold()
    assert "banned" in correction or "phrase" in correction


def test_the_banned_corpus_is_a_corpus_and_not_a_token_list() -> None:
    """Every entry is a PHRASE. A single word is never evidence that generic
    advice is present; it is evidence that English was used, and a one-word
    entry would reject almost every real probe."""
    assert len(synthesis.GENERIC_ADVICE_PHRASES) >= 20
    for phrase in synthesis.GENERIC_ADVICE_PHRASES:
        assert len(phrase.split()) >= 3, phrase
        assert phrase == phrase.casefold()


# ── THE DASHBOARD'S READY PICK NOTE ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_ready_pick_note_is_one_line_derived_from_the_report(
    monkeypatch,
) -> None:
    """spec-doc6 §4.5: one line, plain language, client-facing, derived from the
    "why this candidate" material. Deriving it is what stops the dashboard and
    the report saying different things about the same person."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    note = section["siddhi"]["ready_pick_note"]
    sentence = note["sentence"]
    assert sentence.count("\n") == 0
    # Judgement under pressure is the strongest assessed item in the fixture.
    assert "Judgement under pressure" in sentence
    assert chr(8212) not in sentence
    assert not re.search(r"\d", sentence)


@pytest.mark.asyncio
async def test_the_ready_pick_note_carries_its_citations_internally(
    monkeypatch,
) -> None:
    """The dashboard renders the sentence alone. The refs travel anyway, because
    a note whose provenance was dropped at the border between the report and the
    candidate list would be a claim nobody could trace, on the one surface a
    recruiter triages from."""
    _boom(monkeypatch)
    section = await gap_analysis.build_gap_analysis(None, _dimensions(), _exchanges())
    note = section["siddhi"]["ready_pick_note"]
    assert note["evidence_refs"]
    known = {
        node["ref"] for node in section["siddhi"]["citations"]["evidence_nodes"]
    }
    assert set(note["evidence_refs"]) <= known


def test_the_note_is_deterministic_across_runs() -> None:
    """A triage line that changed wording between two loads of the same list
    would make a recruiter distrust the list. It calls no model, so it cannot."""
    index = evidence.EvidenceIndex.build(
        items=[row["name"] for row in _dimensions()], exchanges=_exchanges()
    )
    first = synthesis.ready_pick_note(_dimensions(), index)
    for _ in range(5):
        assert synthesis.ready_pick_note(_dimensions(), index) == first


def test_siddhi_and_the_dashboard_agree_on_the_key_the_note_is_read_from() -> None:
    """Both halves are read out of their own source rather than restated here.

    The dashboard reads column 5 from `evaluations.aggregate_json` under its
    own constant, and Siddhi is the agent that writes it. A key that drifted on
    one side would not fail: the dashboard would simply render its pending text
    forever, on every candidate, which reads exactly like an assessment that has
    not finished yet.
    """
    from app.services import dashboard

    assert synthesis.READY_PICK_NOTE_KEY == dashboard.READY_PICK_NOTE_KEY


def test_the_note_prefers_the_top_weighted_item_when_weights_are_supplied() -> None:
    """Runbook §43.1 asks for the top-WEIGHTED competency. A delivered report
    row carries no weight, so a caller holding the frozen matrix passes them and
    a caller that does not gets the best-evidenced item instead. Nothing is
    invented to stand in for a weight."""
    index = evidence.EvidenceIndex.build(
        items=[row["name"] for row in _dimensions()], exchanges=_exchanges()
    )
    weighted = synthesis.ready_pick_note(
        _dimensions(), index, priority={"Distributed Systems": 2.0}
    )
    assert "Distributed Systems" in weighted.sentence
    unweighted = synthesis.ready_pick_note(_dimensions(), index)
    assert "Judgement under pressure" in unweighted.sentence


def test_the_note_says_so_rather_than_inventing_one_when_nothing_is_assessed() -> None:
    note = synthesis.ready_pick_note([], evidence.EvidenceIndex())
    assert "nothing to summarise" in note.sentence
    assert note.evidence_refs == ()


# ── GATE G4, BEFORE DELIVERY ─────────────────────────────────────────────────


class _Row:
    def __init__(self, disposition: str, decided_by: object | None) -> None:
        self.disposition = disposition
        self.decided_by = decided_by


class _Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _Session:
    """The narrowest session a gate needs: it answers one query."""

    def __init__(self, row: object | None = None) -> None:
        self.row = row

    async def execute(self, statement):  # noqa: ANN001 - a stub, not an engine
        return _Result(self.row)


class _Report:
    def __init__(self, needs_human_review: bool) -> None:
        self.id = "report-1"
        self.job_candidate_link_id = "link-1"
        self.needs_human_review = needs_human_review


@pytest.mark.asyncio
async def test_a_report_needing_review_is_not_delivered_without_a_disposition() -> None:
    """G4 blocks. Unlike G2 and G3 it may: it withholds a document from a
    client until a human has decided, and decides nothing about the candidate
    itself."""
    with pytest.raises(delivery.DeliveryBlocked) as caught:
        await delivery.gate_delivery(_Session(None), _Report(True))
    assert "no disposition" in str(caught.value)
    assert "auto-resolve" in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disposition", sorted(["cleared", "escalated", "overridden", "rejected"])
)
async def test_g4_asks_whether_a_human_decided_not_whether_they_approved(
    disposition: str,
) -> None:
    """All four dispositions pass, `rejected` included. A gate requiring
    approval is one the pipeline can satisfy by nagging until somebody clicks
    yes; a gate requiring a recorded decision is satisfied only by someone
    having looked."""
    clearance = await delivery.gate_delivery(
        _Session(_Row(disposition, "user-1")), _Report(True)
    )
    assert clearance.disposition == disposition
    assert clearance.decided_by == "user-1"


@pytest.mark.asyncio
async def test_a_disposition_with_nobody_attached_does_not_satisfy_g4() -> None:
    """A row saying a human decided while unable to say who is
    indistinguishable from the pipeline having written it itself."""
    with pytest.raises(delivery.DeliveryBlocked):
        await delivery.gate_delivery(_Session(_Row("cleared", None)), _Report(True))


@pytest.mark.asyncio
async def test_there_is_no_auto_cleared_disposition_to_satisfy_g4_with() -> None:
    """An automatic disposition would satisfy G4 without a human, which is the
    entire thing G4 exists to prevent."""
    from app.services.hiring import gates

    assert "auto_cleared" not in gates.DISPOSITIONS
    assert not any("auto" in value for value in gates.DISPOSITIONS)
    with pytest.raises(delivery.DeliveryBlocked):
        await delivery.gate_delivery(
            _Session(_Row("auto_cleared", "user-1")), _Report(True)
        )


@pytest.mark.asyncio
async def test_a_report_not_flagged_for_review_delivers_without_a_disposition() -> None:
    """G4 is not a review requirement on every report. It is a requirement that
    a FLAG is not resolved by the pipeline."""
    clearance = await delivery.gate_delivery(_Session(None), _Report(False))
    assert clearance.needed_review is False
    assert clearance.disposition is None


@pytest.mark.asyncio
async def test_the_gate_reads_the_disposition_from_the_database_not_the_caller() -> None:
    """A gate whose input its own caller supplies is a gate the caller can
    satisfy, and G4's whole purpose is to be unsatisfiable by the pipeline."""
    import inspect

    parameters = set(inspect.signature(delivery.gate_delivery).parameters)
    assert parameters == {"session", "report"}


@pytest.mark.asyncio
async def test_nothing_is_rendered_in_any_format_while_g4_blocks() -> None:
    """Gating after serialisation would mean the bytes already exist, and bytes
    that exist get sent."""
    with pytest.raises(delivery.DeliveryBlocked):
        await delivery.deliver(
            _Session(None),
            _Report(True),
            {"reference_code": "K7QP-2M4X-9TB1"},
            candidate_name="Fixture Candidate",
            job_title="Platform Engineer",
            tenant_name="Fixture Tenant",
        )


# ── THE FROZEN MATRIX A REPORT IS WRITTEN AGAINST ────────────────────────────


@pytest.mark.asyncio
async def test_a_missing_scorecard_module_refuses_rather_than_falling_back(
    monkeypatch,
) -> None:
    """`hiring.scorecard` is built by the job-setup phase and may not be present
    in a given checkout. Its absence must be a loud, specific refusal: a report
    written without the frozen matrix would state grades against criteria nobody
    finalised, which is what gate G1 exists to refuse."""
    import builtins

    real_import = builtins.__import__

    def _no_scorecard(name, *args, **kwargs):
        if name == "app.services.hiring" and args and "scorecard" in (args[2] or ()):
            raise ImportError("no scorecard module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_scorecard)
    with pytest.raises(synthesis.ScorecardUnavailable) as caught:
        await synthesis.require_frozen_matrix(None, "job-1")
    assert "no report is generated" in str(caught.value) or "not present" in str(
        caught.value
    )


@pytest.mark.asyncio
async def test_a_scorecard_module_without_the_function_also_refuses(
    monkeypatch,
) -> None:
    """Half a dependency is not a dependency. A module that exists but exposes
    no `require_frozen_matrix` would otherwise fail as an AttributeError deep in
    a report task, which reads as a bug rather than as a missing phase."""
    import sys
    import types

    package = sys.modules.get("app.services.hiring")
    stub = types.ModuleType("app.services.hiring.scorecard")
    monkeypatch.setitem(sys.modules, "app.services.hiring.scorecard", stub)
    monkeypatch.setattr(package, "scorecard", stub, raising=False)
    with pytest.raises(synthesis.ScorecardUnavailable) as caught:
        await synthesis.require_frozen_matrix(None, "job-1")
    assert "require_frozen_matrix" in str(caught.value)


def test_the_scorecard_dependency_is_imported_lazily() -> None:
    """A module-level import would make Siddhi unimportable in a checkout
    without the job-setup phase, which hides the dependency rather than making
    it loud."""
    import pathlib

    source = pathlib.Path(synthesis.__file__).read_text(encoding="utf-8")
    header = source.split("def require_frozen_matrix")[0]
    assert "from app.services.hiring import scorecard" not in header
