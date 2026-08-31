"""Vaada draws on the Department Evidence Graph, on the live path.

WHAT THIS FILE IS FOR
---------------------
spec-doc6 4.4 asks for three things from Vaada, and this file is where each of
them is pinned:

  1. "Question generation draws on the relevant Department Evidence Graph for
     the role's department, not a generic bank. Delete the generic bank in the
     same commit."
  2. "Triangulation posture: each claim is something to corroborate, routed
     toward the evidence sources Sutra's matrix flagged as required for that
     competency."
  3. "Ends when Sutra's question-count range AND evidence sufficiency are both
     satisfied. The candidate never sees a remaining count."

THE GENERIC BANK IS PROVED GONE BY PROVING THE SOURCE
------------------------------------------------------
`test_every_node_is_the_runbooks_own_sentence` reads
`Readypick Hiring Philosophy.md` and asserts that every `establishes` line in
every one of the fifteen graphs appears in Part VI at the section that node
cites. A hand-written node cannot pass that, so the test is the deletion: there
is no way to reintroduce a bank here without failing it.

THE THREE INVARIANTS THIS MUST NOT BREAK
-----------------------------------------
`tests/test_conversation_flow.py` drives `respond` end to end and pins them
against the real endpoint. This file pins them at Vaada's own layer, by name,
because the extension added here is the first thing since 2026-08-05 that can
lengthen a conversation:

  * an extension probe is answered under an EXISTING matrix item's question key,
    so `answers_by_key` files it with that item's other answers;
  * nothing Vaada writes advances `next_question_index`, so `charge_completed`
    fires after exactly the same set of base questions as before;
  * an outstanding probe holds completion open.
"""
from __future__ import annotations

import inspect
import json
import re
import uuid
from pathlib import Path

import pytest

from app.prompts import registry
from app.services import agent_loop, interviewer, llm_router, ppi_interview
from app.services.hiring import evidence_graph

RUNBOOK_PATH = Path(__file__).resolve().parents[2] / "Readypick Hiring Philosophy.md"

#: U+2014, built from its code point so a repo-wide em dash sweep cannot rewrite
#: the code that normalises it. The Runbook uses it and the data files may not.
EM_DASH = chr(8212)


@pytest.fixture(scope="module")
def runbook() -> str:
    if not RUNBOOK_PATH.exists():
        raise AssertionError(
            f"RPN-PHIL-001 is not at {RUNBOOK_PATH}. Every node in the evidence "
            f"graph is a sentence out of Part VI and cannot be checked without "
            f"the document."
        )
    return RUNBOOK_PATH.read_text(encoding="utf-8")


def _normalise(text: str) -> str:
    text = text.replace(EM_DASH, "-").replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", text).strip()


# -- 1. The fifteen graphs, and the bank that is gone -------------------------


def test_part_vi_supplies_fifteen_department_graphs() -> None:
    keys = evidence_graph.department_keys()
    assert len(keys) == 15, keys
    for key in keys:
        assert evidence_graph.nodes_for(key), key


def test_every_node_is_the_runbooks_own_sentence(runbook: str) -> None:
    """THE DELETION OF THE GENERIC BANK, ASSERTED RATHER THAN ANNOUNCED.

    Five hand-written node sets used to live in `evidence_graph.py` and none of
    their sentences came from RPN-PHIL-001. Every node now carries Part VI's
    observable-evidence column verbatim, so a hand-written one fails here.
    """
    document = _normalise(runbook)
    checked = 0
    for key in evidence_graph.department_keys():
        for node in evidence_graph.nodes_for(key):
            assert node.establishes.strip(), (key, node.competency_id)
            assert _normalise(node.establishes) in document, (
                key, node.competency_id, node.establishes[:80]
            )
            checked += 1
    assert checked >= 150, checked


def test_a_node_establishes_a_fact_rather_than_asking_a_question() -> None:
    """A question is what Vaada writes fresh, per candidate. Putting questions
    here would rebuild the preset bank this codebase deleted on 2026-08-06."""
    for key in evidence_graph.department_keys():
        for node in evidence_graph.nodes_for(key):
            assert not node.establishes.strip().endswith("?"), node.competency_id


def test_there_is_no_generic_graph_and_no_generic_fallback() -> None:
    """The old `GRAPHS` dict returned the GENERIC set for anything it did not
    recognise, so a civil engineer, a designer and a tradesperson were all
    probed against one seven-node menu."""
    assert not hasattr(evidence_graph, "GRAPHS")
    assert "generic" not in evidence_graph.department_keys()
    with pytest.raises(evidence_graph.DepartmentUnmapped):
        evidence_graph.graph_for("generic")


def test_an_unknown_department_names_what_the_runbook_does_carry() -> None:
    with pytest.raises(evidence_graph.DepartmentUnmapped) as caught:
        evidence_graph.nodes_for("veterinary_surgery")
    message = str(caught.value)
    assert "veterinary_surgery" in message
    assert "it_software_engineering" in message
    # Section 36 is the procedure, and the message has to point at it rather
    # than leaving the reader to pick a near-enough department.
    assert "36" in message


def test_a_department_the_runbook_gives_no_gaming_vectors_for_has_none() -> None:
    """Part VI's coverage is uneven and section 67.8 says so. Section 35 prints
    neither gaming vectors nor red flags, and borrowing another department's
    would read as department knowledge while being none."""
    graph = evidence_graph.graph_for("non_technical_support_administrative")
    assert graph.nodes
    assert graph.hollow_tells == ()
    assert graph.red_flags == ()


def test_the_departments_that_do_have_tells_have_the_runbooks_own(
    runbook: str,
) -> None:
    document = _normalise(runbook)
    with_tells = 0
    for key in evidence_graph.department_keys():
        graph = evidence_graph.graph_for(key)
        for tell in graph.hollow_tells + graph.red_flags:
            assert _normalise(tell) in document, (key, tell[:80])
        if graph.hollow_tells:
            with_tells += 1
    assert with_tells >= 9, with_tells


def test_red_flags_route_to_review_and_carry_no_verdict() -> None:
    """Section 21.8: "route to review, never auto-reject". The enforcement is
    the absence of the capability: a graph has flags and no field that could
    carry a decision, a status or a score."""
    graph = evidence_graph.graph_for("it_software_engineering")
    fields = set(graph.__dataclass_fields__)
    assert not fields & {"reject", "status", "decision", "score", "auto_reject"}
    node_fields = set(evidence_graph.EvidenceNode.__dataclass_fields__)
    assert not node_fields & {"reject", "status", "decision", "score"}


# -- 2. Placing a role in one of the fifteen ---------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Backend Engineer", "it_software_engineering"),
        ("Data Scientist", "data_analytics_data_science_ai_ml"),
        ("Mechanical Design Engineer", "mechanical_engineering_manufacturing"),
        ("PLC Automation Engineer", "electrical_electronics_engineering"),
        ("Quantity Surveyor", "civil_structural_construction"),
        ("Project Architect", "architecture_built_environment"),
        ("Financial Analyst", "finance_accounting"),
        ("Talent Acquisition Specialist", "human_resources"),
        ("Inside Sales Representative", "sales_marketing_business_development"),
        ("Supply Chain Planner", "operations_supply_chain_logistics"),
        ("CNC Machine Operator", "skilled_trades_blue_collar_frontline"),
        ("Customer Support Associate", "non_technical_support_administrative"),
    ],
)
def test_a_role_lands_in_the_department_the_runbook_names(
    title: str, expected: str
) -> None:
    assert evidence_graph.resolve_department(title) == expected


def test_resolution_is_deterministic() -> None:
    """The department decides which graph a job is probed against, so two
    candidates on one job must get the same one. Same rule the coverage plan
    follows."""
    first = evidence_graph.resolve_department("Structural Design Engineer")
    for _ in range(5):
        assert evidence_graph.resolve_department("Structural Design Engineer") == first


@pytest.mark.parametrize("title", ["Staff Nurse", "Legal Counsel", "Sous Chef"])
def test_a_role_outside_part_vi_is_unmapped_rather_than_guessed(title: str) -> None:
    """Section 36 lists legal, healthcare clinical, education and hospitality as
    departments Ready Pick Now will encounter and does not yet cover, and says a
    new model is "added only through the following procedure, and never
    improvised mid-engagement"."""
    with pytest.raises(evidence_graph.DepartmentUnmapped):
        evidence_graph.resolve_department(title)


def test_a_seniority_word_never_decides_a_department() -> None:
    """In Indian job titles "Executive" is routinely a junior grade, and it is
    also a word in one Part VI department title. Section 11.1 uses it as a
    SENIORITY BAND label, so it cannot discriminate a function."""
    assert "executive" in evidence_graph._seniority_words()
    # Asserted on the VOCABULARY rather than on one title, because that is where
    # the property lives: no department can score a point for the word at all.
    for key in evidence_graph.department_keys():
        token_weights, _phrases = evidence_graph._vocabulary()[key]
        assert "executive" not in token_weights, key
        assert "manager" not in token_weights, key
    # "Warehouse Executive" is a junior operations role in Indian job titles.
    # Before the filter it resolved to LEADERSHIP on the second word alone.
    try:
        placed = evidence_graph.resolve_department("Warehouse Executive")
    except evidence_graph.DepartmentUnmapped:
        placed = None
    assert placed != "leadership_general_management_executive"


def test_nothing_resolves_from_an_empty_role() -> None:
    with pytest.raises(evidence_graph.DepartmentUnmapped):
        evidence_graph.resolve_department("", None)


# -- 3. Triangulation: 38.1 groups, 38.3's gradient --------------------------


def test_a_claim_never_corroborates_itself() -> None:
    """Section 5.4 counts independence by ORIGINATOR. A resume line and the
    candidate restating it are one person saying one thing twice."""
    node = evidence_graph.node_for_competency("SW-02", "it_software_engineering")
    assert node is not None
    assert evidence_graph.SELF_WRITTEN_GROUP not in node.corroborated_by
    assert set(node.corroborated_by) < set(evidence_graph.independence_groups())


def test_out_of_band_corroboration_is_reported_rather_than_dropped() -> None:
    """A competency the assessment can probe and cannot confirm is one Miti
    should hold confidence down on. Saying so is more honest than treating a
    well-argued answer as corroborated."""
    node = evidence_graph.node_for_competency("SW-04", "it_software_engineering")
    assert node is not None
    reachable, out_of_band = evidence_graph.corroboration_targets(node)
    assert "assessment" in reachable
    assert "third_party" in out_of_band
    assert "artefact" in out_of_band


def test_the_gradient_is_the_runbooks_five_levels() -> None:
    levels = evidence_graph.specificity_levels()
    assert [level.level for level in levels] == [1, 2, 3, 4, 5]
    assert evidence_graph.discriminator_levels() == (4, 5)


def test_no_probe_opens_on_the_rung_anyone_can_answer() -> None:
    """Section 38.3 says level 1 is answerable by anyone and the resume already
    answered it."""
    for ordinal in range(30):
        assert evidence_graph.probe_level(ordinal=ordinal).level >= 3


def test_the_probe_plan_meets_the_runbooks_own_design_rule() -> None:
    """"at least 40% of probe items must sit at Level 4 or 5" (38.3), for all
    validation instruments across all departments."""
    fraction = evidence_graph.minimum_discriminator_fraction()
    for total in (10, 16, 20, 22, 28):
        levels = [evidence_graph.probe_level(ordinal=i) for i in range(total)]
        discriminating = sum(1 for level in levels if level.discriminating)
        assert discriminating / total >= fraction, (total, discriminating)


def test_a_second_probe_of_one_item_climbs_the_gradient() -> None:
    """"A claim is probed at increasing specificity until either the candidate
    demonstrates participatory knowledge or the probe exhausts"."""
    climb = [
        evidence_graph.probe_level(ordinal=2, prior_substantive=n).level
        for n in range(4)
    ]
    assert climb == sorted(climb)
    assert climb[0] == 4 and climb[-1] == 5
    assert evidence_graph.next_specificity_level(5) is None


def test_the_probe_plan_is_deterministic() -> None:
    """A model-chosen or random level would make two reports on one job
    incomparable, which is what the fixed coverage plan exists to prevent."""
    for ordinal in range(20):
        assert (
            evidence_graph.probe_level(ordinal=ordinal).level
            == evidence_graph.probe_level(ordinal=ordinal).level
        )


# -- 4. The four classes of question -----------------------------------------


def test_the_four_classes_are_the_four_the_architecture_note_names() -> None:
    assert set(evidence_graph.QUESTION_CLASSES) == {
        "confirmation", "gap", "contradiction", "discovery",
    }


def test_a_silent_profile_gets_discovery_and_never_a_gap_question() -> None:
    """THE CLASS THAT PROTECTS THE UNCONVENTIONAL CANDIDATE. Asking a gap
    question of a silent profile establishes only that the profile is silent,
    which is what an ATS already concluded. Axiom 7: absence of evidence is not
    evidence of absence."""
    assert evidence_graph.question_class() == evidence_graph.CLASS_DISCOVERY
    assert (
        evidence_graph.question_class(claim_present=True)
        == evidence_graph.CLASS_GAP
    )


def test_a_contradiction_outranks_everything() -> None:
    """Same precedence `ledger.support_state` uses: an item with evidence on
    both sides is the most interesting one in the conversation and the easiest
    to lose behind a rule that lets support outweigh disagreement."""
    assert (
        evidence_graph.question_class(
            conflicting=True, claim_present=True, substantive_answers=3
        )
        == evidence_graph.CLASS_CONTRADICTION
    )


def test_a_substantive_answer_turns_the_next_question_into_confirmation() -> None:
    assert (
        evidence_graph.question_class(claim_present=True, substantive_answers=1)
        == evidence_graph.CLASS_CONFIRMATION
    )


def test_every_class_carries_an_instruction() -> None:
    """A class with no instruction would render an empty block and the question
    would be written with no reason behind it."""
    for name in evidence_graph.QUESTION_CLASSES:
        assert ppi_interview._class_instructions()[name].strip()


# -- 5. Sutra's routing, absent and present ----------------------------------


@pytest.mark.asyncio
async def test_sutras_matrix_is_named_when_it_cannot_be_read() -> None:
    """`hiring/scorecard.py` is another agent's deliverable. Returning an empty
    tuple on ImportError would report "no sources required" for every competency
    in the product, which is a silent fallback wearing a successful return."""
    with pytest.raises(evidence_graph.ScorecardUnavailable) as caught:
        await evidence_graph.required_evidence_sources(None, uuid.uuid4(), "Kafka")
    assert "matrix" in str(caught.value)


def test_the_scorecard_is_never_imported_at_module_scope() -> None:
    """A module-scope import would make the whole conversation unimportable
    until Sutra's deliverable lands."""
    source = inspect.getsource(evidence_graph)
    header = source[: source.index("class DepartmentUnmapped")]
    assert "scorecard" not in header


# -- 6. The live path: what the question writer is briefed with --------------


class _Job:
    """Enough of `models.Job` for the writer. A real row needs a session."""

    def __init__(self, title: str, department: str = "", jd: str = "") -> None:
        self.id = uuid.uuid4()
        self.title = title
        self.department = department
        self.jd_markdown = jd


class _Competency:
    def __init__(self, name: str, category: str = "must_have") -> None:
        self.id = uuid.uuid4()
        self.name = name
        self.category = category
        self.description = "what the hiring manager wrote"


class _Row:
    def __init__(self, ordinal: int = 0) -> None:
        self.id = uuid.uuid4()
        self.ordinal = ordinal
        self.prompt = "the stored question written from this candidate's resume"
        self.rubric_json = None
        self.generated_at = None
        self.job_candidate_link_id = uuid.uuid4()
        self.competency_id = uuid.uuid4()


def _capture(monkeypatch, question: str) -> list[list[dict]]:
    """Record what actually reaches the model, and answer with a valid shape.

    The reply has to name the item, because `_mentions` refuses a question that
    ignored the criterion entirely; a refusal would degrade the loop and hide
    the thing this file is testing.
    """
    seen: list[list[dict]] = []

    async def _invoke(task_type, messages, **kwargs):
        seen.append(messages)
        return json.dumps(
            {
                "question": question,
                "rubric": {
                    band: "band %s" % band for band in ppi_interview.RUBRIC_BANDS
                },
            }
        )

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    return seen


@pytest.mark.asyncio
async def test_the_written_question_is_briefed_from_the_department_graph(
    monkeypatch,
) -> None:
    """THE LIVE ENTRY POINT. `api/assessments._write_next_question_inner` calls
    `write_question` for every base question a candidate reads."""
    seen = _capture(
        monkeypatch,
        "On the system design you described, what did that architecture make "
        "harder six months later, and what did it cost?",
    )
    job = _Job(
        "Senior Backend Engineer",
        department="Engineering",
        jd="production services, incidents, rollbacks and on-call",
    )
    competency = _Competency("System design & architecture")
    result = await ppi_interview.write_question(
        session=None, job=job, row=_Row(), competency=competency
    )
    assert not result.degraded
    system = seen[0][0]["content"]

    node = evidence_graph.node_for_competency(
        "System design & architecture", "it_software_engineering"
    )
    assert node is not None
    # Part VI's own observable evidence for SW-02 is in the prompt.
    assert node.establishes in system
    # And a Part VI gaming vector for this department.
    tells = evidence_graph.graph_for("it_software_engineering").hollow_tells
    assert any(tell in system for tell in tells)
    # And the rung of 38.3's gradient this probe aims at.
    assert evidence_graph.probe_level(ordinal=0).question in system


@pytest.mark.asyncio
async def test_the_hollow_tells_are_withheld_when_the_matrix_does_not_match(
    monkeypatch,
) -> None:
    """THE MATRIX CORROBORATES THE DEPARTMENT. A department resolved from a job
    title is a judgement that can be wrong; a matched menu row is independent
    evidence that it was right. Without this check a mis-resolved role would be
    probed for another field's tells and nothing would notice."""
    seen = _capture(
        monkeypatch,
        "Tell me about a time you had to hold a working conversation in "
        "Japanese under pressure, and what went wrong in it.",
    )
    job = _Job("Senior Backend Engineer", department="Engineering")
    competency = _Competency("Conversational Japanese")
    await ppi_interview.write_question(
        session=None, job=job, row=_Row(), competency=competency
    )
    system = seen[0][0]["content"]
    for tell in evidence_graph.graph_for("it_software_engineering").hollow_tells:
        assert tell not in system
    assert "A HOLLOW ANSWER IN THIS FIELD" not in system


@pytest.mark.asyncio
async def test_an_unmapped_department_falls_back_to_the_item_and_never_a_bank(
    monkeypatch,
) -> None:
    """Nothing is substituted. The prompt states the item's own
    observable-evidence line, which is what Sutra's stage 2 guaranteed exists,
    and the question stays this candidate's own."""
    seen = _capture(
        monkeypatch,
        "Walk me through one ward handover that went wrong: what was missed, "
        "and what did the discipline you kept afterwards cost the shift?",
    )
    job = _Job("Staff Nurse")
    competency = _Competency("Ward handover discipline")
    result = await ppi_interview.write_question(
        session=None, job=job, row=_Row(), competency=competency
    )
    assert not result.degraded
    system = seen[0][0]["content"]
    assert competency.description in system
    assert "department evidence model" not in system


@pytest.mark.asyncio
async def test_a_silent_profile_is_asked_what_it_has_and_not_what_it_lacks(
    monkeypatch,
) -> None:
    seen = _capture(
        monkeypatch,
        "What production ownership have you personally carried that this "
        "profile would not show me, and what went wrong with it?",
    )
    job = _Job("Senior Backend Engineer", department="Engineering")
    await ppi_interview.write_question(
        session=None,
        job=job,
        row=_Row(),
        competency=_Competency("Production ownership"),
        resume_excerpt="",
    )
    assert (
        ppi_interview._class_instructions()[evidence_graph.CLASS_DISCOVERY]
        in seen[0][0]["content"]
    )


@pytest.mark.asyncio
async def test_a_claim_on_the_resume_is_asked_for_the_missing_evidence(
    monkeypatch,
) -> None:
    seen = _capture(
        monkeypatch,
        "On the payments service whose production ownership you held, what was "
        "the worst incident and what did it cost?",
    )
    job = _Job("Senior Backend Engineer", department="Engineering")
    await ppi_interview.write_question(
        session=None,
        job=job,
        row=_Row(),
        competency=_Competency("Production ownership"),
        resume_excerpt="Owned production for the payments service, on-call rota.",
    )
    assert (
        ppi_interview._class_instructions()[evidence_graph.CLASS_GAP]
        in seen[0][0]["content"]
    )


def test_a_retrievable_probe_is_refused() -> None:
    """Section 21.6's design rule: "probe for specifics that only a participant
    would know, not for knowledge that is publicly retrievable". Its weak column
    is the only place the Runbook says what a bad probe looks like."""
    weak = tuple(
        pair.weak_probe
        for pair in evidence_graph.graph_for("it_software_engineering").probe_design_pairs
    )
    assert weak
    assert ppi_interview._matching_probe("Explain microservices to me.", weak)
    assert ppi_interview._matching_probe("So, how do you debug?", weak)
    # And the participatory question beside it survives.
    strong = next(
        pair.strong_probe
        for pair in evidence_graph.graph_for("it_software_engineering").probe_design_pairs
        if pair.claim_type == "Debugging"
    )
    assert ppi_interview._matching_probe(strong, weak) is None


@pytest.mark.asyncio
async def test_part_vis_own_probes_are_calibration_and_never_the_question(
    monkeypatch,
) -> None:
    """SHOWN AS CALIBRATION, REFUSED AS A COPY.

    Part VI prints literal validation probes for six departments, which is the
    closest the Runbook comes to saying what a good question in that field
    sounds like. It is also exactly the shape of the preset bank this codebase
    deleted on 2026-08-06, where every candidate read the same words. So the
    model is calibrated on them and the question that comes back is refused if
    it reproduces one.
    """
    probes = evidence_graph.graph_for(
        "mechanical_engineering_manufacturing"
    ).validation_probes
    assert probes

    attempts: list[str] = []

    async def _invoke(task_type, messages, **kwargs):
        attempts.append(messages[0]["content"])
        # First attempt copies the Runbook probe; the loop's reflection should
        # get a real question on the second.
        if len(attempts) == 1:
            return json.dumps({"question": probes[0], "rubric": {}})
        return json.dumps(
            {
                "question": (
                    "On the bracket you designed, what tolerance did the shop "
                    "floor push back on, and what did the change cost?"
                ),
                "rubric": {
                    band: "band %s" % band for band in ppi_interview.RUBRIC_BANDS
                },
            }
        )

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    result = await ppi_interview.write_question(
        session=None,
        job=_Job("Mechanical Design Engineer", department="Manufacturing"),
        row=_Row(),
        competency=_Competency("GD&T and tolerancing"),
    )
    # The probes reached the prompt as calibration...
    assert any(probe in attempts[0] for probe in probes)
    assert "DO NOT ask any of them" in attempts[0]
    # ...and the copy was rejected rather than shown to the candidate.
    assert len(attempts) > 1
    assert result.value["question"] not in probes
    assert ppi_interview._matching_probe(probes[0], probes) == probes[0]


def test_the_prompt_blocks_carry_no_number_a_candidate_could_read() -> None:
    """The standing rule, applied to the four blocks added on 2026-08-29. A
    gradient RUNG is engineering metadata one turn away from text a candidate
    reads, so the level number is never rendered and never sent."""
    for ordinal in range(6):
        level = evidence_graph.probe_level(ordinal=ordinal)
        rendered = "%s %s" % (level.question, level.answerable_by)
        assert not re.search(r"\b(level|rung)\s*\d", rendered.lower())
    payload = interviewer._probe_specificity(0)
    assert payload is not None
    assert "level" not in payload


# -- 7. Ending the conversation, and the three invariants --------------------


def _state(**kwargs) -> interviewer.ConversationState:
    defaults = dict(asked=20, total_written=20, floor=20, probe_outstanding=False)
    defaults.update(kwargs)
    return interviewer.conversation_state(**defaults)


def _item(name: str, **kwargs) -> interviewer.DimensionEvidence:
    return interviewer.DimensionEvidence(dimension=name, **kwargs)


def test_sufficiency_is_a_stop_condition_of_its_own() -> None:
    """spec-doc6 4.4: "Ends when Sutra's question-count range AND evidence
    sufficiency are both satisfied"."""
    covered = [
        _item("A", answers=1, substantive=1, must_have=True, question_key="k1"),
        _item("B", answers=1, substantive=1, must_have=True, question_key="k2"),
    ]
    assert interviewer.STOP_EVIDENCE_SUFFICIENT in _state(
        dimensions=covered
    ).stop_conditions


def test_an_unevidenced_must_have_withholds_sufficiency() -> None:
    dimensions = [
        _item("A", answers=1, substantive=1, must_have=True, question_key="k1"),
        _item("B", answers=0, must_have=True, question_key="k2"),
        _item("C", answers=1, substantive=1),
    ]
    state = _state(dimensions=dimensions)
    assert interviewer.STOP_EVIDENCE_SUFFICIENT not in state.stop_conditions
    assert [item.dimension for item in state.unevidenced] == ["B"]


def test_an_evasive_answer_does_not_establish_a_must_have() -> None:
    """Treating a non-answer as coverage would let a candidate shorten their own
    assessment by not answering."""
    state = _state(
        dimensions=[_item("A", answers=3, substantive=0, must_have=True, question_key="k")]
    )
    assert interviewer.STOP_EVIDENCE_SUFFICIENT not in state.stop_conditions


def test_with_no_must_have_marked_every_dimension_is_critical() -> None:
    """"Restrict more when unsure" applies exactly here: the alternative reading
    would let an unwired caller declare every conversation sufficient."""
    state = _state(dimensions=[_item("A", answers=0), _item("B", answers=1, substantive=1)])
    assert not state.evidence_sufficient
    assert {item.dimension for item in state.critical} == {"A", "B"}


def test_the_floor_is_never_lowered_by_sufficiency() -> None:
    """The per-grade minimum is what keeps two candidates on one job
    comparable. Sufficiency can only ever be an ADDITIONAL condition."""
    covered = [_item("A", answers=1, substantive=1, must_have=True, question_key="k")]
    early = _state(dimensions=covered, asked=3, floor=20, total_written=20)
    assert interviewer.STOP_EVIDENCE_SUFFICIENT in early.stop_conditions
    assert interviewer.STOP_FLOOR_REACHED not in early.stop_conditions


def test_the_extension_ceiling_comes_from_the_runbook_and_not_from_a_literal(
    monkeypatch,
) -> None:
    """A number restated in a module is a number nobody can trace. Moving the
    data must move the ceiling, in both directions."""
    assert evidence_graph.extension_ceiling() == len(
        evidence_graph.specificity_levels()
    )
    levels = evidence_graph.specificity_levels()[:3]
    monkeypatch.setattr(evidence_graph, "specificity_levels", lambda: levels)
    assert evidence_graph.extension_ceiling() == 3
    assert interviewer.extension_ceiling() == 3


def test_the_extension_is_bounded_by_the_gradient() -> None:
    """Section 38.3 bounds how far one claim can be probed: five rungs, then
    "the probe exhausts". A conversation that still lacks evidence after
    climbing an entire gradient has established that the evidence is not there,
    and reporting that is never a rejection."""
    ceiling = interviewer.extension_ceiling()
    dimensions = [
        _item("D%d" % n, answers=0, must_have=True, question_key="k%d" % n)
        for n in range(ceiling + 4)
    ]
    state = _state(dimensions=dimensions)
    assert len(state.extension_targets(ceiling)) == ceiling
    spent = _state(dimensions=dimensions, extensions_used=ceiling)
    assert spent.extension_targets(ceiling) == ()


# THE THREE INVARIANTS, BY NAME. `tests/test_conversation_flow.py` drives them
# through `respond`; these pin the same three at Vaada's own layer, because the
# bounded extension is the first thing since 2026-08-05 that can lengthen a
# conversation.


def test_invariant_an_extension_is_answered_under_an_existing_question_key() -> None:
    """A follow-up is filed under the SAME `question_key`, so `answers_by_key`
    hands the scorer one richer answer rather than an unknown key every scorer
    silently DROPS. An item with no existing row cannot be extended at all."""
    ceiling = interviewer.extension_ceiling()
    dimensions = [
        _item("has a row", answers=0, must_have=True, question_key="row-1"),
        _item("has no row", answers=0, must_have=True, question_key=""),
    ]
    targets = _state(dimensions=dimensions).extension_targets(ceiling)
    assert [item.dimension for item in targets] == ["has a row"]
    assert all(item.question_key for item in targets)


def test_invariant_nothing_vaada_writes_advances_the_question_index() -> None:
    """`charge_completed` fires on `next_question_index >= len(prompts)`, so a
    write to that counter from here would move billing."""
    for module in (interviewer, ppi_interview, evidence_graph):
        source = inspect.getsource(module)
        # An ASSIGNMENT, not a mention. `interviewer`'s own docstring explains
        # why the counter must not move, and banning the words would ban the
        # explanation.
        assert not re.search(r"next_question_index\s*\+?=[^=]", source), module.__name__
        assert not re.search(r"charge_completed\s*\(", source), module.__name__


def test_invariant_an_outstanding_probe_holds_completion_open() -> None:
    """A probe outstanding on the LAST base question holds completion open, or
    the customer is charged and scoring dispatched while the candidate is still
    typing."""
    covered = [_item("A", answers=1, substantive=1, must_have=True, question_key="k")]
    quiet = _state(dimensions=covered, probe_outstanding=False)
    busy = _state(dimensions=covered, probe_outstanding=True)
    assert interviewer.STOP_NO_PROBE_OUTSTANDING in quiet.stop_conditions
    assert interviewer.STOP_NO_PROBE_OUTSTANDING not in busy.stop_conditions


def test_the_candidate_never_sees_a_remaining_count() -> None:
    """spec-doc6 4.4, and the standing no-numbers rule. The state is operator
    data: it is a dict of counts and it must not be reachable from a prompt."""
    state = _state(dimensions=[_item("A", answers=1, substantive=1)])
    log = state.as_log()
    assert set(log) >= {"unevidenced", "extensions_used", "stop_conditions"}
    for prompt in ("ppi_write_question", "interview_follow_up_decision"):
        text = registry.load(prompt).text.lower()
        assert "how many" not in text
        assert "remaining" not in text
        assert "questions left" not in text


# -- 8. The probe carries the triangulation posture --------------------------


@pytest.mark.asyncio
async def test_the_probe_is_told_which_rung_to_aim_at(monkeypatch) -> None:
    """A follow-up is where the discrimination happens: 38.3's levels 4 and 5
    are the rungs a generative model answers generically and a participant
    answers specifically."""
    seen: list[dict] = []

    async def _invoke(task_type, messages, **kwargs):
        seen.append(json.loads(messages[-1]["content"]))
        return json.dumps({"follow_up": "What did that outage cost you?"})

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    out = await interviewer.next_follow_up(
        session=None,
        question="Tell me about the migration.",
        answer="We moved the monolith to services over two quarters and it went well.",
        transcript=[],
        follow_ups_used=0,
        already_followed_up=False,
        budget=3,
    )
    assert out
    aim = seen[0]["probe_at_specificity"]
    discriminating = [
        level for level in evidence_graph.specificity_levels() if level.discriminating
    ]
    assert aim["question"] == discriminating[0].question


@pytest.mark.asyncio
async def test_an_unreadable_gradient_costs_the_posture_and_not_the_turn(
    monkeypatch,
) -> None:
    """Every failure path returns the product's previous behaviour. A candidate
    is mid-assessment; a data package problem must not end their assessment."""
    def _raise() -> tuple:
        raise RuntimeError("data package unreadable")

    monkeypatch.setattr(evidence_graph, "discriminator_levels", _raise)
    seen: list[dict] = []

    async def _invoke(task_type, messages, **kwargs):
        seen.append(json.loads(messages[-1]["content"]))
        return json.dumps({"follow_up": "What did you change first?"})

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    out = await interviewer.next_follow_up(
        session=None,
        question="Tell me about the migration.",
        answer="We moved the monolith to services over two quarters.",
        transcript=[],
        follow_ups_used=0,
        already_followed_up=False,
        budget=3,
    )
    assert out
    assert "probe_at_specificity" not in seen[0]


def test_an_unreadable_ceiling_falls_back_to_the_modules_own_floor(
    monkeypatch,
) -> None:
    """Refusing the turn over a data file would END a candidate's assessment
    rather than shorten it, so the ceiling degrades to the floor this module
    already uses for a caller that passes no budget."""

    def _raise() -> int:
        raise RuntimeError("data package unreadable")

    monkeypatch.setattr(evidence_graph, "extension_ceiling", _raise)
    assert interviewer.extension_ceiling() == interviewer.MAX_FOLLOW_UPS


@pytest.mark.asyncio
async def test_a_provider_failure_still_means_ask_the_next_scripted_question(
    monkeypatch,
) -> None:
    async def _down(*args, **kwargs):
        raise RuntimeError("every provider is down")

    monkeypatch.setattr(llm_router, "invoke_llm", _down)
    assert (
        await interviewer.next_follow_up(
            session=None,
            question="q",
            answer="a real and complete answer about a migration I ran",
            transcript=[],
            follow_ups_used=0,
            already_followed_up=False,
            budget=3,
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_degraded_writer_leaves_the_candidates_own_question(
    monkeypatch,
) -> None:
    """`generated_at` stays NULL, which is the honest record that it happened."""
    async def _down(*args, **kwargs):
        raise RuntimeError("every provider is down")

    monkeypatch.setattr(llm_router, "invoke_llm", _down)
    row = _Row()
    result = await ppi_interview.write_question(
        session=None,
        job=_Job("Senior Backend Engineer", department="Engineering"),
        row=row,
        competency=_Competency("System design & architecture"),
    )
    assert result.degraded
    assert result.value["question"] == row.prompt
    assert row.generated_at is None
    assert isinstance(result, agent_loop.LoopResult)
