"""Compilation: deterministic, model-free, numberless, and narrow at the exit.

Four properties, and each one is checked the way it can actually fail.

  DETERMINISM      the same intake compiles to the same bytes, in one process
                   and across a fresh interpreter. A Company DNA artifact
                   constrains every job a client will ever post, so a compiler
                   that drifted would make a rubric disagreement
                   indistinguishable from noise.
  MODEL-FREE       proved by an AST walk over the source, not by a docstring.
                   The same technique `test_miti_pipeline` uses on the
                   aggregator, and for the same reason: the rule has to survive
                   somebody adding one quick call to tidy the phrasing.
  NO NUMBERS       the plain-language restatement carries no weight, multiplier
                   or percentage. It is what a client confirms, and confirming
                   a table of multipliers is confirming that the arithmetic
                   looks plausible.
  A NARROW EXIT    `CompiledDNA`'s field set is asserted EXACTLY, and the raw
                   intake column names appear nowhere in the module.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import re
import subprocess
import sys

import pytest

from app.services.hiring import company_dna
from app.services.hiring import dna_compilation

MODULE = pathlib.Path(dna_compilation.__file__)
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "company_dna"
BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _answers() -> dict:
    raw = json.loads((FIXTURES / "complete_intake.json").read_text(encoding="utf-8"))
    return {key: value for key, value in raw.items() if not key.startswith("_")}


ANSWERS = _answers()


@pytest.fixture(scope="module")
def document() -> dict:
    return dna_compilation.compile_document(ANSWERS, dna_version=1)


# ── The fixture is a real, complete intake ───────────────────────────────────


def test_the_fixture_answers_the_whole_instrument() -> None:
    """A compilation test over a half-filled intake tests the empty branches.

    Every required question has an answer, and every answer passes the same
    validator the API applies, so the golden artifact below is what a real
    completed session actually produces rather than what the compiler does with
    a convenient subset.
    """
    completeness = company_dna.completeness(ANSWERS)
    assert completeness["complete"], completeness["missing"]
    for key, value in ANSWERS.items():
        question = company_dna.question(key)
        assert question is not None, f"{key} is not a question in the instrument"
        dna_compilation.validate_answer(question, value)


def test_the_fixture_exercises_both_halves_of_the_disqualifier_rule(
    document: dict,
) -> None:
    """One lawful requirement accepted, one protected-attribute bar refused.

    Both directions in one fixture on purpose. A false positive is not
    harmless: telling a client their lawful professional requirement is
    discriminatory destroys their trust in every refusal that follows,
    including the ones that are right.
    """
    assert any("CA licence" in item for item in document["disqualifiers"])
    assert any("45" in item for item in document["refused_disqualifiers"])


# ── Determinism ──────────────────────────────────────────────────────────────


def test_the_same_intake_compiles_to_the_same_bytes(document: dict) -> None:
    for _ in range(25):
        again = dna_compilation.compile_document(ANSWERS, dna_version=1)
        assert dna_compilation.canonical_json(again) == dna_compilation.canonical_json(
            document
        )


def test_key_order_in_the_input_does_not_change_the_output(document: dict) -> None:
    """A rehydrated JSONB column does not preserve insertion order.

    So the compiler must not either, or a version stored and read back would
    checksum differently from the one that was just written, and every
    confirmation token would fail on the second read.
    """
    reversed_answers = dict(reversed(list(ANSWERS.items())))
    assert dna_compilation.checksum(
        dna_compilation.compile_document(reversed_answers, dna_version=1)
    ) == dna_compilation.checksum(document)


def test_a_fresh_interpreter_computes_the_same_checksum(document: dict) -> None:
    """Across processes, and therefore across a deploy.

    In-process repetition cannot see a hash-seed dependency, a set iteration or
    a module-level cache warmed by an earlier test. A subprocess can, and this
    is the version of the determinism claim that a rolling deploy actually
    relies on.
    """
    script = (
        "import json, pathlib, sys;"
        "sys.path.insert(0, r'%s');"
        "from app.services.hiring import dna_compilation as d;"
        "raw = json.loads(pathlib.Path(r'%s').read_text(encoding='utf-8'));"
        "answers = {k: v for k, v in raw.items() if not k.startswith('_')};"
        "print(d.checksum(d.compile_document(answers, dna_version=1)))"
    ) % (BACKEND, FIXTURES / "complete_intake.json")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == dna_compilation.checksum(document)


def test_the_version_number_is_part_of_the_fingerprint(document: dict) -> None:
    """Two versions with identical answers are still two versions.

    Otherwise a revision that changed nothing would be indistinguishable from
    the version it replaced, and "which one was this job frozen against" would
    have two answers with one fingerprint.
    """
    second = dna_compilation.compile_document(ANSWERS, dna_version=2)
    assert dna_compilation.checksum(second) != dna_compilation.checksum(document)


# ── Model-free ───────────────────────────────────────────────────────────────


def _executable_names(path: pathlib.Path) -> set[str]:
    """Every name an AST walk can reach: imports, calls, attributes, names.

    READS THE AST, NOT THE PROSE. This module's docstring deliberately explains
    why there is no router import, and a substring scan would report the
    explanation as the violation. That is not hypothetical: it is what the
    first version of the equivalent Miti check did, and a check that flags its
    own documentation is one somebody weakens rather than fixes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


@pytest.mark.parametrize(
    "banned",
    [
        "llm_router",
        "invoke_llm",
        "agent_loop",
        "anthropic",
        "chat_completion",
        "app.services.llm_router",
        "run_loop",
    ],
)
def test_the_compiler_reaches_no_model(banned: str) -> None:
    assert banned not in _executable_names(MODULE), f"{MODULE.name} reaches {banned!r}"


def test_the_compilation_path_has_no_await_in_it() -> None:
    """A model call needs one. Its absence is a second, independent statement.

    The one `async def` in the module is the database read, and it is named
    here so the check cannot be satisfied by there being no async at all when
    somebody adds a second coroutine.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    coroutines = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert coroutines == ["load_compiled"], coroutines


def test_no_model_string_appears_in_the_module() -> None:
    """The three permitted model ids are the only ones in executable source,
    and none of them belongs here."""
    source = MODULE.read_text(encoding="utf-8")
    for model in ("claude-sonnet", "claude-haiku", "voyage-context", "gpt-", "gemini"):
        assert model not in source


# ── The exit is narrow ───────────────────────────────────────────────────────


def test_the_compiled_object_carries_exactly_these_fields() -> None:
    """THE EXACT SET, not the absence of particular names.

    `miti.EvaluatorInput` is asserted the same way and the reason is the same:
    a later field called `context`, `notes` or `extra` would pass a test that
    only looked for `answers` and `transcript`, and it would reopen the whole
    hole. Adding a field here is a deliberate line in a diff that somebody has
    to justify.
    """
    fields = {f.name for f in dataclasses.fields(dna_compilation.CompiledDNA)}
    assert fields == {
        "tenant_id",
        "version",
        "document",
        "checksum",
        "completed_at",
    }


def test_the_compiled_object_is_frozen() -> None:
    """A downstream consumer cannot attach the raw session to it afterwards."""
    assert dataclasses.fields(dna_compilation.CompiledDNA)
    params = getattr(dna_compilation.CompiledDNA, "__dataclass_params__")
    assert params.frozen


@pytest.mark.parametrize(
    "column", ["answers_json", "transcript_json", "pending_prompt"]
)
def test_the_raw_session_columns_are_not_named_anywhere_in_the_module(
    column: str,
) -> None:
    """Not selected, not returned, not mentioned.

    The retrieval interface reads three columns. This is the check that keeps
    it three: a future convenience that "just also fetches the answers for
    debugging" has to delete this test to land.
    """
    assert column not in MODULE.read_text(encoding="utf-8")


def test_the_engine_view_hands_over_configuration_and_nothing_else(
    document: dict,
) -> None:
    """Runbook §15's compilation rule at its narrowest point.

    `recruiter_context` is the bucket §15 says is context for the recruiter and
    never configuration for the engine. It is the one the client's own
    free-text lands in, so it is the one that must not reach a prompt that
    decides what a candidate is graded on.
    """
    import uuid

    compiled = dna_compilation.CompiledDNA(
        tenant_id=uuid.uuid4(),
        version=1,
        document=document,
        checksum=dna_compilation.checksum(document),
        completed_at=None,
    )
    view = compiled.engine_view()
    assert set(view) == set(dna_compilation.SUTRA_KEYS)
    for withheld in (
        "recruiter_context",
        "dossier_preferences",
        "sourcing_hints",
        "constraints",
        "refusals",
        "provenance",
    ):
        assert withheld not in view, f"{withheld} crossed into the engine view"


def test_the_engine_object_is_the_type_the_pipeline_already_takes(
    document: dict,
) -> None:
    """One type for Layer 2's engine input, not a second one.

    `transformation.derive_weight` and `derive_threshold` take
    `company_dna.CompanyDNA`. Returning a parallel type here would mean two
    answers to "what does Sutra read", and the second answer is always the one
    somebody finds later.
    """
    import uuid

    compiled = dna_compilation.CompiledDNA(
        tenant_id=uuid.uuid4(),
        version=1,
        document=document,
        checksum="x",
        completed_at=None,
    )
    engine = dna_compilation.engine_object(compiled)
    assert isinstance(engine, company_dna.CompanyDNA)
    assert engine.weight_modifiers == document["weight_modifiers"]
    assert engine.independence_required == document["independence_required"]
    # The people-facing halves are absent, not merely unused.
    assert engine.dossier_preferences == {}
    assert engine.recruiter_context == {}


# ── The golden artifact ──────────────────────────────────────────────────────


def test_the_compiled_artifact_matches_its_golden_file(document: dict) -> None:
    """spec-doc6 §11.1: structural drift shows up in review as a diff.

    The artifact is the thing every job for a client is built on, so a change
    to its shape is a change to every scorecard that will ever be derived from
    it. Regenerate this file deliberately, in the same commit as the change
    that moved it, or the diff is doing its job.
    """
    golden = json.loads((FIXTURES / "compiled_artifact.json").read_text(encoding="utf-8"))
    assert document == golden


def test_the_artifact_carries_its_schema_and_dna_version(document: dict) -> None:
    assert document["schema_version"] == dna_compilation.ARTIFACT_SCHEMA_VERSION
    assert document["dna_version"] == 1


def test_behavioural_competencies_carry_stable_runbook_identifiers(
    document: dict,
) -> None:
    """Runbook §17.1 numbers them CDNA-01 onward, and a scorecard item cites one.

    Stable across a recompilation, so a trace written under one compilation
    still points at the same statement after the next.
    """
    ids = [item["id"] for item in document["behavioural_competencies"]]
    assert ids == [f"CDNA-{n:02d}" for n in range(1, len(ids) + 1)]
    again = dna_compilation.compile_document(ANSWERS, dna_version=1)
    assert [item["id"] for item in again["behavioural_competencies"]] == ids
    statements = [item["statement"] for item in document["behavioural_competencies"]]
    assert statements == document["observable_signals"]


def test_the_risk_probes_are_not_the_behavioural_competencies(document: dict) -> None:
    """§16.3 is what to look FOR and §16.4 is what to look OUT for.

    They steer questioning in opposite directions, so one bucket loses the
    difference and the failure modes end up read as things the client wants.
    """
    assert document["risk_probes"]
    assert document["behavioural_competencies"]
    statements = {item["statement"] for item in document["behavioural_competencies"]}
    assert statements.isdisjoint(set(document["risk_probes"]))


# ── The plain-language restatement ───────────────────────────────────────────

_NUMBER = re.compile(r"\d")


def test_the_restatement_carries_no_number_at_all(document: dict) -> None:
    """The standing product rule, and the thing that makes the confirmation real.

    A client confirming "we will look harder at what someone has already
    delivered" is confirming what they said. A client confirming a multiplier
    is confirming that the arithmetic looks plausible, which is not a thing
    they can check.

    The client's OWN words are exempt, because refusing them would mean
    refusing "Two strong individual contributors we promoted in 2024" back to
    the person who wrote it.
    """
    recruiter_context = document.get("recruiter_context") or {}
    client_text = {
        str(item).strip()
        for item in list(document.get("observable_signals") or [])
        + list(document.get("risk_probes") or [])
        + list(document.get("disqualifiers") or [])
        + list(document.get("refused_disqualifiers") or [])
        + [value for key, value in recruiter_context.items() if key != "note"]
    }
    for block in dna_compilation.plain_language(document):
        for line in block["lines"]:
            if line.strip() in client_text:
                continue
            assert not _NUMBER.search(line), f"{block['key']}: {line!r}"


def test_the_restatement_carries_no_em_dash(document: dict) -> None:
    # Built from chr(8212) so a repo-wide sweep cannot rewrite the check.
    dash = chr(8212)
    for block in dna_compilation.plain_language(document):
        assert dash not in block["title"]
        for line in block["lines"]:
            assert dash not in line


def test_the_restatement_says_something_about_every_part_of_the_artifact(
    document: dict,
) -> None:
    """Six blocks, each of which a person can disagree with.

    A restatement that omitted the disqualifiers would let a client confirm an
    understanding whose most consequential half they were never shown.
    """
    blocks = {block["key"] for block in dna_compilation.plain_language(document)}
    assert blocks == {
        "emphasis",
        "evidence",
        "good",
        "risks",
        "constraints",
        "reach",
        "reporting",
        "context",
    }
    for block in dna_compilation.plain_language(document):
        assert block["lines"], f"{block['key']} restated nothing"
        assert block["title"].strip()


def test_a_moved_weight_produces_a_different_sentence() -> None:
    """The restatement has to react, or confirming it means nothing.

    Two intakes that differ only in one forced scale must read differently, or
    the client is confirming a fixed paragraph and the confirmation is theatre.
    """
    lowered = dict(ANSWERS)
    lowered["proven_vs_potential"] = company_dna.SCALE_MAX
    raised = dict(ANSWERS)
    raised["proven_vs_potential"] = company_dna.SCALE_MIN

    def emphasis(answers: dict) -> list[str]:
        document = dna_compilation.compile_document(answers, dna_version=1)
        return next(
            block["lines"]
            for block in dna_compilation.plain_language(document)
            if block["key"] == "emphasis"
        )

    assert emphasis(lowered) != emphasis(raised)


def test_an_unconfirmed_prohibited_filter_list_is_said_out_loud() -> None:
    """Appendix A7 item 30 is a signature, and an unsigned one is a fact.

    Reporting nothing when it is unconfirmed would let the absence read as a
    confirmation, which is the direction that costs something.
    """
    without = dict(ANSWERS)
    without["prohibited_filters_confirmed"] = "Not confirmed"
    document = dna_compilation.compile_document(without, dna_version=1)
    lines = next(
        block["lines"]
        for block in dna_compilation.plain_language(document)
        if block["key"] == "constraints"
    )
    assert any("not confirmed" in line.lower() for line in lines)


# ── Forced scales are refused at the boundary ────────────────────────────────


def _scale_questions() -> list[company_dna.Question]:
    return [
        question
        for section in company_dna.SECTIONS
        for question in section.questions
        if question.kind == company_dna.SCALE_QUESTION
    ]


def test_section_two_is_six_forced_scales() -> None:
    """Runbook §16.2 and Appendix A2 items 7 to 12. A count, from the source."""
    assert len(_scale_questions()) == 6


@pytest.mark.parametrize("question", _scale_questions(), ids=lambda q: q.key)
def test_free_text_to_a_forced_scale_is_refused(
    question: company_dna.Question,
) -> None:
    """§16.2: "Each answered on a forced scale, not free text."

    REFUSED AT THE API LAYER, not hidden in the UI. A rule enforced only by the
    control that renders the question is a rule anybody with a terminal is
    exempt from, and the answer that gets through is prose in a column the
    compiler reads as a number.
    """
    for prose in (
        "We value both equally",
        "proven",
        "somewhere in the middle",
        "",
        None,
    ):
        with pytest.raises(dna_compilation.AnswerRejected) as caught:
            dna_compilation.validate_answer(question, prose)
        assert caught.value.question_key == question.key


@pytest.mark.parametrize("question", _scale_questions(), ids=lambda q: q.key)
def test_the_refusal_names_both_poles(question: company_dna.Question) -> None:
    """A refusal that does not say what the scale means teaches nothing."""
    with pytest.raises(dna_compilation.AnswerRejected) as caught:
        dna_compilation.validate_answer(question, "we value both")
    message = caught.value.message
    assert question.poles is not None
    for pole in question.poles:
        assert pole in message


@pytest.mark.parametrize("question", _scale_questions(), ids=lambda q: q.key)
def test_an_out_of_range_position_is_refused(
    question: company_dna.Question,
) -> None:
    """The Runbook's scale is one to five, so nought and six are not positions.

    Refused rather than clamped at this boundary. The compiler clamps, because
    a single malformed stored field must not discard an otherwise complete
    intake, but a LIVE answer arriving out of range is a caller bug and
    accepting it silently would store a position the client never chose.
    """
    for value in (0, -1, 6, 99):
        with pytest.raises(dna_compilation.AnswerRejected):
            dna_compilation.validate_answer(question, value)


@pytest.mark.parametrize("question", _scale_questions(), ids=lambda q: q.key)
def test_every_position_on_the_scale_is_accepted(
    question: company_dna.Question,
) -> None:
    """Including the midpoint, which is a real answer.

    Forcing a client off it would manufacture a preference and then weight a
    matrix by it.
    """
    for value in range(company_dna.SCALE_MIN, company_dna.SCALE_MAX + 1):
        assert dna_compilation.validate_answer(question, value) == value
    assert (
        dna_compilation.validate_answer(question, str(company_dna.SCALE_NEUTRAL))
        == company_dna.SCALE_NEUTRAL
    )


# ── Observable evidence is refused at the boundary too ───────────────────────


def _evidence_list_questions() -> list[company_dna.Question]:
    return [
        question
        for section in company_dna.SECTIONS
        for question in section.questions
        if question.kind == company_dna.EVIDENCE_LIST_QUESTION
    ]


def test_the_instrument_collects_observable_evidence_as_a_list() -> None:
    assert _evidence_list_questions()


def test_one_adjective_in_a_list_of_five_is_refused() -> None:
    """Every line is judged separately.

    A list judged as one blob passes on the strength of its best entry, and the
    weakest entry is the one that becomes a competency nobody can evidence.
    """
    question = _evidence_list_questions()[0]
    good = ANSWERS["observable_behaviours"].splitlines()
    poisoned = "\n".join(good[:-1] + ["Ownership mindset"])
    with pytest.raises(dna_compilation.AnswerRejected) as caught:
        dna_compilation.validate_answer(question, poisoned)
    assert "ownership mindset" in caught.value.message.lower()


def test_too_few_items_is_refused_with_the_number_asked_for() -> None:
    """§16.3 asks for five to eight, and Appendix A3 prints the lines.

    A short list accepted silently is a client who thinks they answered the
    section.
    """
    question = company_dna.question("observable_behaviours")
    assert question is not None
    one = ANSWERS["observable_behaviours"].splitlines()[0]
    with pytest.raises(dna_compilation.AnswerRejected) as caught:
        dna_compilation.validate_answer(question, one)
    assert "five" in caught.value.message


def test_a_choice_outside_its_options_is_refused() -> None:
    question = company_dna.question("decider")
    assert question is not None
    with pytest.raises(dna_compilation.AnswerRejected):
        dna_compilation.validate_answer(question, "Whoever is around")
    assert (
        dna_compilation.validate_answer(question, question.options[0])
        == question.options[0]
    )


# ── Progress ─────────────────────────────────────────────────────────────────


def test_progress_covers_every_section_in_instrument_order() -> None:
    blocks = dna_compilation.progress({})
    assert [block.key for block in blocks] == [
        section.key for section in company_dna.SECTIONS
    ]
    assert len(blocks) == 12, "Runbook §16 is twelve sections"
    assert all(block.intent.strip() for block in blocks), (
        "every section needs a why-we-are-asking line; the screen shows it"
    )
    # Every section that HAS a required question is outstanding on an empty
    # intake. The four that do not are complete from the start, and that is a
    # real product statement rather than an oversight: a client with no
    # absolute requirements has answered Section 5.
    outstanding = {block.key for block in blocks if not block.complete}
    optional = {block.key for block in blocks if block.required_total == 0}
    assert outstanding == {
        block.key for block in blocks if block.required_total
    }
    assert optional == {
        "non_negotiables",
        "offer_reality",
        "sourcing_preferences",
        "historical_calibration",
    }, (
        "the set of sections a client may skip entirely changed; that is a "
        f"product decision and not a refactor: {optional}"
    )


def test_a_completed_intake_reports_every_section_complete() -> None:
    assert all(block.complete for block in dna_compilation.progress(ANSWERS))
