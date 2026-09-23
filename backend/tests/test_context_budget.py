"""Context budgets by purpose, and the rule that nothing is cut mid sentence.

RPN-AI-UP-001 W4.4 and W4.5. The acceptance criterion this file executes is the
one the specification states: "a prompt exceeding a slot budget is refused or
compressed, never silently truncated mid sentence. Cutting the assembled string
hands a model half a sentence, and a model handed half a sentence completes it
from its own priors, into text a grade is written from."

Both halves of that are asserted, and the negative direction matters more than
the positive one: it is easy to write a fitter that keeps the prompt under the
budget, and the way it usually does it is by slicing the string.
"""
from __future__ import annotations

import pytest

from app.services import context_budget as cb


# ── The slots are the ones the specification names ───────────────────────────


def test_the_six_slots_carry_the_budgets_the_spec_states() -> None:
    """Transcribed independently of the module, so a change has to be made
    twice. A budget table that only agrees with itself proves nothing."""
    assert cb.SLOT_TOKEN_BUDGETS == {
        "system_policy": 1024,
        "application_state": 2048,
        "retrieved_evidence": 4096,
        "memory": 2048,
        "tool_output": 3072,
        "working_scratch": 2048,
    }
    assert cb.TOTAL_CONTEXT_BUDGET_TOKENS == 14336


def test_an_undeclared_slot_raises_rather_than_defaulting() -> None:
    """A slot nobody budgeted would otherwise inherit whichever default was
    written, and the two plausible defaults are "unlimited" and "1024"."""
    with pytest.raises(ValueError):
        cb.budget_for("everything_else")


def test_evidence_and_policy_are_not_compressible_and_say_so() -> None:
    """The asymmetry the module exists to hold. A summary of an answer is not
    evidence of what someone said."""
    assert cb.SLOT_RETRIEVED_EVIDENCE not in cb.COMPRESSIBLE_SLOTS
    assert cb.SLOT_SYSTEM_POLICY not in cb.COMPRESSIBLE_SLOTS
    with pytest.raises(ValueError):
        cb.fit_summary("anything at all.", slot=cb.SLOT_RETRIEVED_EVIDENCE)


# ── Never mid sentence ───────────────────────────────────────────────────────


def _sentence(index: int, *, words: int) -> str:
    return " ".join([f"w{index}"] * words) + "."


def test_an_oversized_evidence_slot_drops_whole_chunks_and_records_each() -> None:
    """Whole units, and a record. A drop that left no trace is
    indistinguishable from a retriever that returned less."""
    units = [_sentence(index, words=400) for index in range(6)]
    fitted = cb.fit_evidence(units)

    assert fitted.tokens <= cb.budget_for(cb.SLOT_RETRIEVED_EVIDENCE)
    assert fitted.was_reduced
    assert len(fitted.dropped) == fitted.dropped_units
    # Every surviving unit is one of the inputs, byte for byte. This is the
    # assertion that catches a slice: a truncated chunk would be a prefix of an
    # input rather than an input.
    for kept in fitted.text.split("\n\n"):
        assert kept in units


def test_evidence_is_capped_by_unit_count_as_well_as_by_tokens() -> None:
    """W4.5: four highly relevant chunks beat twenty adequate ones. Twenty
    short chunks fit inside the token budget easily, so the token ceiling alone
    would let the distractor effect straight through."""
    units = [f"Relevant fact number {index}." for index in range(20)]
    fitted = cb.fit_evidence(units)
    assert fitted.kept_units == cb.EVIDENCE_PREFERRED_UNITS
    assert fitted.dropped_units == 20 - cb.EVIDENCE_PREFERRED_UNITS
    assert fitted.tokens < cb.budget_for(cb.SLOT_RETRIEVED_EVIDENCE)


def test_a_single_oversized_evidence_unit_is_refused_rather_than_cut() -> None:
    """The case where the two easy answers are both wrong.

    Dropping it leaves a prompt whose evidence slot is empty while the caller
    believes it supplied some; shortening it compresses evidence, which is the
    one thing this module will not do. So it raises, naming the slot.
    """
    with pytest.raises(cb.ContextOverBudget) as excinfo:
        cb.fit_evidence([_sentence(0, words=20_000)])
    assert excinfo.value.slot == cb.SLOT_RETRIEVED_EVIDENCE
    assert excinfo.value.tokens > excinfo.value.budget_tokens


def test_a_state_summary_is_compressed_at_sentence_boundaries() -> None:
    """The compressible half of the asymmetry, and the marker that says so."""
    text = " ".join(_sentence(index, words=60) for index in range(200))
    fitted = cb.fit_summary(text, slot=cb.SLOT_APPLICATION_STATE)

    assert fitted.tokens <= cb.budget_for(cb.SLOT_APPLICATION_STATE)
    assert fitted.was_reduced
    assert cb.OMISSION_MARKER in fitted.text
    # Every surviving sentence is a whole sentence from the input.
    original = set(cb.sentences(text))
    for piece in cb.sentences(fitted.text):
        assert piece == cb.OMISSION_MARKER or piece in original


def test_the_most_recent_sentences_are_the_ones_kept() -> None:
    """A state summary's recent sentences say where the workflow actually is."""
    text = " ".join(_sentence(index, words=60) for index in range(200))
    fitted = cb.fit_summary(text, slot=cb.SLOT_APPLICATION_STATE)
    assert fitted.text.rstrip().endswith(_sentence(199, words=60))


def test_an_oversized_system_policy_is_refused_and_never_shortened() -> None:
    """Compressing a policy removes a RULE, and the failure mode is a rule
    nobody can see was dropped."""
    with pytest.raises(cb.ContextOverBudget) as excinfo:
        cb.assemble({cb.SLOT_SYSTEM_POLICY: [_sentence(0, words=3000)]})
    assert excinfo.value.slot == cb.SLOT_SYSTEM_POLICY


def test_assemble_fits_every_slot_and_reports_what_it_dropped() -> None:
    assembled = cb.assemble(
        {
            cb.SLOT_SYSTEM_POLICY: ["Grade against the rubric only."],
            cb.SLOT_APPLICATION_STATE: [
                cb.authoritative_state(tenant_id="t-1", job_id="j-1")
            ],
            cb.SLOT_RETRIEVED_EVIDENCE: [
                _sentence(index, words=400) for index in range(6)
            ],
        }
    )
    assert assembled.tokens > 0
    assert assembled.dropped, "the oversized evidence slot must report its drops"
    for fitted in assembled.slots:
        assert fitted.tokens <= cb.budget_for(fitted.slot)


# ── Do not ask the model what the application already knows ──────────────────


def test_authoritative_state_injects_the_facts_rather_than_asking_for_them() -> None:
    block = cb.authoritative_state(
        tenant_id="t-1",
        job_id="j-1",
        candidate_id="c-1",
        role="recruiter",
        scorecard_version=4,
        permissions=["view_review_screen"],
        workflow_stage="assessment_completed",
    )
    for value in ("t-1", "j-1", "c-1", "recruiter", "4", "assessment_completed"):
        assert value in block
    assert "never infer them" in block


def test_a_none_fact_reads_as_unknown_rather_than_vanishing() -> None:
    """An absent line reads as a fact nobody had, and a model filling in an
    absent line from a transcript is what this block exists to prevent."""
    block = cb.authoritative_state(job_id=None)
    assert "job_id: unknown" in block


def test_the_authoritative_keys_are_closed() -> None:
    """A caller that could pass any key could pass "grade", and an
    authoritative-looking block asserting a grade is exactly what must never sit
    in front of a model that is about to write one."""
    with pytest.raises(ValueError):
        cb.authoritative_state(grade="Highly Matching")


# ── Compression for the router's overflow retry ──────────────────────────────


def test_a_multi_turn_request_loses_whole_turns_and_keeps_every_system_message() -> None:
    messages = [{"role": "system", "content": "policy"}] + [
        {"role": "user", "content": _sentence(index, words=50)} for index in range(8)
    ]
    result = cb.compress_messages(messages)

    assert result.compressed
    assert result.messages[0] == {"role": "system", "content": "policy"}
    kept = [m["content"] for m in result.messages if m["role"] != "system"]
    assert cb.OMISSION_MARKER in kept
    for content in kept:
        assert content == cb.OMISSION_MARKER or content in {
            m["content"] for m in messages
        }
    assert len(kept) < len(messages)


def test_a_single_turn_keeps_its_head_and_its_tail() -> None:
    """The instruction is at the head and the question is at the tail of every
    single-turn prompt this codebase builds, so trimming from either end would
    remove the part that says what to do."""
    units = [_sentence(index, words=30) for index in range(10)]
    messages = [{"role": "user", "content": " ".join(units)}]
    result = cb.compress_messages(messages)

    assert result.compressed
    body = result.messages[-1]["content"]
    assert body.startswith(units[0])
    assert body.rstrip().endswith(units[-1])
    assert cb.OMISSION_MARKER in body
    for piece in cb.sentences(body):
        assert piece == cb.OMISSION_MARKER or piece in units


def test_nothing_removable_reports_that_rather_than_cutting() -> None:
    """The honest answer when no whole unit can go. The router reads
    `compressed` and stops, because an identical retry reproduces the same 400
    and a shortened one would be a fabrication."""
    result = cb.compress_messages([{"role": "user", "content": "One indivisible ask."}])
    assert not result.compressed
    assert result.messages == [{"role": "user", "content": "One indivisible ask."}]
    assert "sentence" in result.note


def test_compression_never_produces_a_fragment_of_a_sentence() -> None:
    """The rule stated as a sweep rather than as three separate assertions.

    Whatever survives a compression is a whole unit of what went in. Anything
    else is a slice, and a slice is what hands a model half a sentence.
    """
    units = [_sentence(index, words=40) for index in range(12)]
    for messages in (
        [{"role": "user", "content": " ".join(units)}],
        [{"role": "system", "content": "policy"}]
        + [{"role": "user", "content": unit} for unit in units],
    ):
        result = cb.compress_messages(messages)
        assert result.compressed
        for message in result.messages:
            for piece in cb.sentences(str(message["content"])):
                assert (
                    piece == cb.OMISSION_MARKER
                    or piece in units
                    or piece == "policy"
                ), piece
