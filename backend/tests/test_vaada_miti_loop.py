"""The closed loop between the conversation and the evidence ledger.

WHAT THE CONVERSATION KNEW ABOUT ITSELF BEFORE THIS. One tuple, `(covered,
total)`, computed for the stopping rule and then discarded. That answers "may I
stop?" and nothing else: an item probed once and answered evasively, an item
nobody has reached yet, and an item the ledger already holds two contradicting
readings of all looked identical -- and all three looked identical to an item
that was answered well.

Two properties are asserted here, and a third is asserted by NOT changing.

  1. the state is explicit, deterministic, and internal;
  2. two semantically equivalent questions are not both asked, decided without
     a model call, because the guard matters most when the provider is down;
  3. `ppi.conversation_may_close` is still the only thing that ENDS a
     conversation early, floor included. Everything added here can make it
     stricter and can never make it looser.

The four invariants around a follow-up -- same `question_key`, no index
advance, completion held open, early stopping governed by
`conversation_may_close` -- live in `tests/test_conversation_flow.py` and are
deliberately not re-asserted here.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.services import interviewer

_ASKED = "How did you tune Kafka consumer lag when the p99 spiked?"


# ── Repetition detection ─────────────────────────────────────────────────────


def test_the_repetition_check_calls_no_model() -> None:
    """The guard matters most in the moment a provider is already failing: that
    is when the writer is falling back to thinner prompts and most likely to
    re-ask. A model asked "is this a repeat?" is absent exactly then, and its
    "no" would be indistinguishable from a real "no"."""
    assert not inspect.iscoroutinefunction(interviewer.is_semantic_repeat)
    source = inspect.getsource(interviewer.is_semantic_repeat)
    for smell in ("llm_router", "invoke_llm", "await ", "chat_completion"):
        assert smell not in source, f"the repetition check reached a model: {smell}"


def test_the_same_question_asked_twice_is_a_repeat() -> None:
    assert interviewer.is_semantic_repeat(_ASKED, [_ASKED])


def test_a_reworded_question_is_still_a_repeat() -> None:
    """A model told not to repeat itself will cheerfully reword the same
    question and return it, which is why this compares substance rather than
    the exact string."""
    assert interviewer.is_semantic_repeat(
        "Could you tell me how you tuned Kafka consumer lag when the p99 spiked?",
        [_ASKED],
    )


def test_a_behavioural_repeat_with_no_specific_term_is_caught() -> None:
    """The shingles half. A behavioural question names no technology, so a
    check built only on specific terms would never see this one."""
    assert interviewer.is_semantic_repeat(
        "Tell me about a disagreement with a peer and how you resolved it",
        ["Describe a disagreement with a peer and how you resolved it"],
    )


def test_a_different_question_is_not_a_repeat() -> None:
    """The direction that matters. A false positive silently throws away a
    well-written adaptive question and shows the candidate the stored one
    instead, which is the scripted feel the whole module exists to remove."""
    assert not interviewer.is_semantic_repeat(
        "What did you personally change in the deployment pipeline?", [_ASKED]
    )


def test_two_different_questions_about_one_topic_are_not_a_repeat() -> None:
    """The hardest case for a shingle check, and the reason the threshold is
    where it is: an interview probes one competency several times on purpose."""
    assert not interviewer.is_semantic_repeat(
        "Which partition assignment strategy did you settle on for Kafka?",
        [_ASKED],
    )


def test_nothing_asked_yet_is_never_a_repeat() -> None:
    assert not interviewer.is_semantic_repeat(_ASKED, [])
    assert not interviewer.is_semantic_repeat(_ASKED, None)


def test_a_short_question_is_not_matched_on_shingles_alone() -> None:
    """Below a handful of shingles a comparison is noise, and two short
    questions in the same domain would collide constantly."""
    assert not interviewer.is_semantic_repeat(
        "Why that database?", ["Why that queue?"]
    )


@pytest.mark.asyncio
async def test_a_repeated_probe_is_dropped_rather_than_asked() -> None:
    """Dropping it costs nothing: the conversation simply moves to the next
    scripted question, which is this module's standard degradation. Asking it
    spends a scarce follow-up to demonstrate that nobody was listening."""
    import json

    dropped = await interviewer._decide_validate(
        {
            "raw": json.dumps({"follow_up": _ASKED}),
            "asked_before": [_ASKED],
        }
    )
    assert dropped["follow_up"] is None

    kept = await interviewer._decide_validate(
        {
            "raw": json.dumps({"follow_up": "What broke first when you cut the batch?"}),
            "asked_before": [_ASKED],
        }
    )
    assert kept["follow_up"] == "What broke first when you cut the batch?"


def test_a_caller_that_passes_nothing_keeps_the_previous_behaviour() -> None:
    """An older caller keeps what it had instead of silently losing a guard it
    never knew about, which is the same rule `budget` follows."""
    signature = inspect.signature(interviewer.next_follow_up)
    assert signature.parameters["asked_before"].default is None


# ── The explicit conversation state ──────────────────────────────────────────


def _state(*dimensions, asked=10, total_written=20, floor=8, probe=False):
    return interviewer.conversation_state(
        dimensions=dimensions,
        asked=asked,
        total_written=total_written,
        floor=floor,
        probe_outstanding=probe,
    )


def test_every_dimension_lands_in_exactly_one_bucket() -> None:
    state = _state(
        interviewer.DimensionEvidence("Covered", answers=2, substantive=2),
        interviewer.DimensionEvidence("Partial", answers=3, substantive=1, gaps=1),
        interviewer.DimensionEvidence("Weak", answers=2, substantive=0),
        interviewer.DimensionEvidence("Unprobed"),
        interviewer.DimensionEvidence(
            "Conflicting", answers=2, substantive=2, conflicting=True
        ),
    )
    assert state.covered == ("Covered",)
    assert state.partially_covered == ("Partial",)
    assert state.weak_evidence == ("Weak",)
    assert state.unprobed == ("Unprobed",)
    assert state.conflicting_evidence == ("Conflicting",)
    # One list of what is still owed, rather than four a caller would eventually
    # union three of.
    assert state.remaining == ("Partial", "Weak", "Unprobed", "Conflicting")


def test_a_probed_but_unusable_dimension_is_not_the_same_as_an_unprobed_one() -> None:
    """The distinction the whole state exists for, and the same one the ledger
    draws between `inferred_only` and `unsupported`: there is something to ask
    about, and nobody has asked yet."""
    weak = _state(interviewer.DimensionEvidence("Item", answers=2, substantive=0))
    unprobed = _state(interviewer.DimensionEvidence("Item"))
    assert weak.weak_evidence and not weak.unprobed
    assert unprobed.unprobed and not unprobed.weak_evidence


def test_a_contradiction_outranks_everything_else() -> None:
    """`ledger.support_state` checks contradiction FIRST for the same reason: a
    dimension with strong evidence on both sides is the most interesting one in
    the conversation and the easiest to lose behind a rule that lets support
    outweigh contradiction."""
    state = _state(
        interviewer.DimensionEvidence(
            "Item", answers=4, substantive=4, gaps=1, conflicting=True
        )
    )
    assert state.conflicting_evidence == ("Item",)
    assert not state.covered and not state.partially_covered


def test_confidence_is_a_word_and_is_arithmetic_over_the_states() -> None:
    """Never a model's opinion of its own work: a self-assessed confidence is
    unfalsifiable and fails exactly when the provider is already failing.
    `verification.Verdict.confidence` follows the same rule."""
    covered = interviewer.DimensionEvidence("A", answers=1, substantive=1)
    assert _state(covered).confidence == interviewer.CONFIDENCE_HIGH
    assert (
        _state(covered, interviewer.DimensionEvidence("B", answers=2, substantive=1, gaps=1)).confidence
        == interviewer.CONFIDENCE_MEDIUM
    )
    assert (
        _state(covered, interviewer.DimensionEvidence("B")).confidence
        == interviewer.CONFIDENCE_MEDIUM
    )
    assert (
        _state(covered, interviewer.DimensionEvidence("B"), interviewer.DimensionEvidence("C")).confidence
        == interviewer.CONFIDENCE_LOW
    )
    assert (
        _state(covered, interviewer.DimensionEvidence("B", answers=1, substantive=1, conflicting=True)).confidence
        == interviewer.CONFIDENCE_LOW
    )
    assert _state().confidence == interviewer.CONFIDENCE_LOW

    source = inspect.getsource(type(_state()).confidence.fget)
    for smell in ("llm_router", "invoke_llm", "await "):
        assert smell not in source


def test_the_floor_is_a_stop_condition_and_is_reported_honestly() -> None:
    """Without it a fluent candidate is assessed on fewer criteria than a
    hesitant one, and two reports on the same job stop being comparable."""
    covered = interviewer.DimensionEvidence("A", answers=1, substantive=1)
    below = _state(covered, asked=3, floor=8)
    assert interviewer.STOP_FLOOR_REACHED not in below.stop_conditions
    at_floor = _state(covered, asked=8, floor=8)
    assert interviewer.STOP_FLOOR_REACHED in at_floor.stop_conditions


def test_an_outstanding_contradiction_withholds_its_stop_condition() -> None:
    """A MATERIAL contradiction obliges `ask_follow_up` while a conversation is
    still running. Stopping with one outstanding throws away the only chance
    anybody will get to ask the candidate about it."""
    state = _state(
        interviewer.DimensionEvidence("A", answers=2, substantive=2, conflicting=True),
        asked=20,
        floor=8,
    )
    assert interviewer.STOP_NO_CONFLICT_OUTSTANDING not in state.stop_conditions
    assert interviewer.STOP_EVERY_DIMENSION_COVERED not in state.stop_conditions


def test_an_outstanding_probe_withholds_its_stop_condition() -> None:
    """The candidate is mid-sentence. Charging the customer and dispatching
    scoring there would score an assessment that is still being written."""
    covered = interviewer.DimensionEvidence("A", answers=1, substantive=1)
    assert (
        interviewer.STOP_NO_PROBE_OUTSTANDING
        not in _state(covered, probe=True).stop_conditions
    )
    assert (
        interviewer.STOP_NO_PROBE_OUTSTANDING
        in _state(covered, probe=False).stop_conditions
    )


def test_an_empty_matrix_never_reports_full_coverage() -> None:
    """`conversation_may_close` refuses on `total_dimensions <= 0` for the same
    reason: nothing covered is not everything covered."""
    assert (
        interviewer.STOP_EVERY_DIMENSION_COVERED not in _state().stop_conditions
    )


def test_the_state_reports_conditions_and_decides_nothing() -> None:
    """Two functions that could both end an assessment is one more than anybody
    would keep in step, and the one that got forgotten would be the one that let
    a candidate be graded on half a matrix."""
    # Parsed, not grepped. The class docstring legitimately NAMES
    # `conversation_may_close` while explaining that it deliberately does not
    # call it, so a substring search matches the very comment that documents the
    # rule. What matters is whether the code CALLS it, which only the AST can
    # answer.
    tree = ast.parse(textwrap.dedent(inspect.getsource(interviewer.ConversationState)))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "conversation_may_close" not in called, (
        "ConversationState calls the stopping rule. It must REPORT conditions "
        "and decide nothing, or two functions can end an assessment."
    )
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (defined & {"may_close", "should_stop"}), sorted(defined)


def test_the_state_log_carries_counts_and_words_only() -> None:
    """An ordinary log is far more widely readable than a trace, and this one is
    written on every turn of a live candidate conversation."""
    state = _state(
        interviewer.DimensionEvidence("Stream Processing", answers=2, substantive=2),
        interviewer.DimensionEvidence("Stakeholder management"),
    )
    payload = state.as_log()
    rendered = repr(payload)
    assert "Stream Processing" not in rendered
    assert "Stakeholder management" not in rendered
    assert payload["confidence"] in {
        interviewer.CONFIDENCE_HIGH,
        interviewer.CONFIDENCE_MEDIUM,
        interviewer.CONFIDENCE_LOW,
    }
    assert payload["covered"] == 1 and payload["unprobed"] == 1


# ── The loop, and what governs stopping ──────────────────────────────────────


def test_conversation_may_close_still_governs_early_stopping() -> None:
    """The addition is an `and`, never a replacement, and the source is where
    that has to be checked: a passing end state cannot tell the two apart."""
    from app.api import assessments

    source = inspect.getsource(assessments.respond)
    assert "ppi.conversation_may_close(" in source
    close_at = source.index("evidence_complete = ppi.conversation_may_close(")
    tail = source[close_at : close_at + 1200]
    assert ") and (" in tail, tail[:400]
    assert "interviewer.STOP_NO_CONFLICT_OUTSTANDING in coverage.stop_conditions" in tail
    # Completion by exhausting the written questions is untouched, so nothing
    # added here can strand a candidate in an endless interview.
    assert "conversation.next_question_index >= len(prompts) or evidence_complete" in source


def test_the_conflicting_dimensions_are_read_from_the_ledger() -> None:
    """THE MITI SIDE OF THE LOOP. Whether two readings disagree is a question
    about the evidence ledger, not about the transcript, so it is read from the
    ledger rather than guessed at from what the candidate typed."""
    from app.api import assessments

    source = inspect.getsource(assessments._ledger_dimension_flags)
    assert "load_claims" in source
    assert "CLAIM_CONTRADICTED" in source
    assert "CLAIM_INFERRED_ONLY" in source
    # Imported inside the function. `app.services.evidence` sits on an import
    # cycle, and a module-level import here is an AttributeError the moment
    # another one forms.
    assert "    from app.services.evidence import ledger" in source


def test_an_unavailable_ledger_never_stalls_a_conversation() -> None:
    """The dangerous direction. A ledger outage that made every conversation
    refuse to close would strand every candidate in the product
    mid-assessment."""
    from app.api import assessments

    source = inspect.getsource(assessments._ledger_dimension_flags)
    assert "except Exception" in source
    assert "return set(), set()" in source


def test_the_coverage_read_counts_matrix_items_and_not_questions() -> None:
    """Several questions can probe one matrix item, and a follow-up is filed
    under its parent's key, so counting questions would let a third of the
    matrix look like full coverage."""
    from app.api import assessments

    source = inspect.getsource(assessments._coverage_rows)
    assert "JOIN job_competencies c ON c.id = q.competency_id" in source
    assert "GROUP BY c.id" in source
    assert "m.question_key = CAST(q.id AS text)" in source
    # And only a substantive answer counts, or a candidate could shorten their
    # own assessment by not answering.
    assert "COALESCE(m.answer_label, 'substantive') = 'substantive'" in source


def test_the_state_never_reaches_a_response_schema() -> None:
    """Internal engineering metadata, exactly like the ledger's `relevance`. The
    counts here order work and explain a decision to an operator; the standing
    no-numbers rule covers them as it covers a score."""
    from app.api import assessments

    source = inspect.getsource(assessments.respond)
    returned = source[source.index("return ConversationOut(") :]
    for leak in ("coverage", "confidence", "stop_conditions", "remaining"):
        assert leak not in returned, f"conversation state leaked to the client: {leak}"
