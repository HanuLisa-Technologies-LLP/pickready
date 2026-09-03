"""Objective scoring, fill-in-the-blank matching, and the transcript line.

WHAT IS PINNED HERE, AND WHY EACH ONE IS WORTH A TEST
-----------------------------------------------------
  * THE SELECT-EVERYTHING STRATEGY SCORES ZERO. That is the whole reason the
    multi-correct rule carries an incorrect-selection penalty (spec 2.3), and
    it is the one edge case a plain "fraction of correct options found" would
    get exactly backwards: selecting all four options finds every correct one.
  * A CANDIDATE IS NEVER MARKED WRONG FOR SPELLING IT ANOTHER WAY. Case,
    whitespace and a synonym all have to pass, and the synonym path only runs
    AFTER the exact match fails, so an accepted answer never costs a model
    call.
  * THE FUZZY FALLBACK FAILS CLOSED. When the equivalence check is
    unavailable the blank is marked incorrect and the degradation is logged.
    Marking it right would be the product deciding an answer was correct on
    nobody's authority.
  * NO NUMBER CROSSES THE BOUNDARY. Correctness is a word, time spent is a
    phrase, and the phrase is asserted to carry no digit at all.
"""
from __future__ import annotations

import pytest

from app.services.assessment_formats import rendering, scoring, types


def _single_payload() -> dict:
    return {
        "options": [
            {"id": "a", "text": "A write-ahead log"},
            {"id": "b", "text": "A read replica"},
            {"id": "c", "text": "A connection pool"},
            {"id": "d", "text": "A materialised view"},
        ],
        "correct_option_id": "a",
    }


def _multi_payload(correct: list[str] | None = None) -> dict:
    return {
        "options": [
            {"id": "a", "text": "Partition the topic"},
            {"id": "b", "text": "Increase the consumer count"},
            {"id": "c", "text": "Raise the batch size"},
            {"id": "d", "text": "Disable acknowledgements"},
        ],
        "correct_option_ids": correct or ["a", "b"],
        "scoring": "partial",
    }


def _blank_payload(case_sensitive: bool = False) -> dict:
    return {
        "template": "The ___ pattern decouples publishers from ___.",
        "blanks": [
            {"index": 0, "accepted": ["observer"], "case_sensitive": case_sensitive},
            {"index": 1, "accepted": ["subscribers", "consumers"], "case_sensitive": False},
        ],
    }


# ── Multiple choice, single correct ──────────────────────────────────────────


def test_the_correct_option_scores_full_credit_and_any_other_scores_none() -> None:
    payload = _single_payload()
    assert scoring.score_mcq_single(payload, {"selected_option_id": "a"}) == 1.0
    for wrong in ("b", "c", "d"):
        assert scoring.score_mcq_single(payload, {"selected_option_id": wrong}) == 0.0


# ── Multiple choice, multi-correct: every edge case in section 2.3 ───────────


def test_all_correct_and_nothing_incorrect_is_full_credit() -> None:
    assert scoring.score_mcq_multi(_multi_payload(), {"selected_option_ids": ["a", "b"]}) == 1.0
    # Order is not part of the answer.
    assert scoring.score_mcq_multi(_multi_payload(), {"selected_option_ids": ["b", "a"]}) == 1.0


def test_selecting_nothing_scores_zero() -> None:
    assert scoring.score_mcq_multi(_multi_payload(), {"selected_option_ids": []}) == 0.0


def test_selecting_only_incorrect_options_scores_zero() -> None:
    assert scoring.score_mcq_multi(_multi_payload(), {"selected_option_ids": ["c", "d"]}) == 0.0


def test_selecting_every_option_scores_zero() -> None:
    """THE STRATEGY THE PENALTY EXISTS FOR (spec 2.3).

    A candidate who ticks every box has selected every correct answer, and a
    scorer that only counted those would award full marks for reading none of
    them.
    """
    payload = _multi_payload()
    assert scoring.score_mcq_multi(payload, {"selected_option_ids": ["a", "b", "c", "d"]}) == 0.0
    # And it holds where the arithmetic alone would not: three correct of
    # four gives (3 - 1) / 3, which is not zero, so the all-selected rule is
    # doing real work rather than restating the formula.
    three = _multi_payload(correct=["a", "b", "c"])
    assert scoring.score_mcq_multi(three, {"selected_option_ids": ["a", "b", "c", "d"]}) == 0.0


def test_a_partial_selection_earns_partial_credit() -> None:
    payload = _multi_payload()
    # One of two correct, nothing incorrect: (1 - 0) / 2.
    assert scoring.score_mcq_multi(payload, {"selected_option_ids": ["a"]}) == 0.5


def test_an_incorrect_selection_cancels_a_correct_one() -> None:
    payload = _multi_payload()
    # (1 - 1) / 2, floored at zero rather than going negative.
    assert scoring.score_mcq_multi(payload, {"selected_option_ids": ["a", "c"]}) == 0.0
    # And the floor holds when the penalty exceeds the credit.
    assert scoring.score_mcq_multi(payload, {"selected_option_ids": ["a", "c", "d"]}) == 0.0


def test_partial_credit_is_never_outside_the_unit_interval() -> None:
    """The database CHECK refuses an `auto_score` outside 0..1, so a formula
    that could exceed it would fail at the INSERT, after the candidate has
    already answered."""
    payload = _multi_payload(correct=["a", "b", "c"])
    for selection in ([], ["a"], ["a", "b"], ["a", "b", "c"], ["d"], ["a", "d"], ["a", "b", "c", "d"]):
        assert 0.0 <= scoring.score_mcq_multi(payload, {"selected_option_ids": selection}) <= 1.0


# ── Fill in the blank ────────────────────────────────────────────────────────


def _blank(index: int = 0, accepted: list[str] | None = None, case_sensitive: bool = False):
    return types.FillBlank(
        index=index, accepted=accepted or ["Observer"], case_sensitive=case_sensitive
    )


def test_an_exact_answer_matches() -> None:
    assert scoring.match_blank("Observer", _blank()) == scoring.RESULT_EXACT


def test_case_is_ignored_unless_the_blank_says_otherwise() -> None:
    assert scoring.match_blank("observer", _blank()) == scoring.RESULT_EXACT
    assert scoring.match_blank("OBSERVER", _blank()) == scoring.RESULT_EXACT
    # A code identifier is the case the flag exists for.
    sensitive = _blank(accepted=["getUserById"], case_sensitive=True)
    assert scoring.match_blank("getUserById", sensitive) == scoring.RESULT_EXACT
    assert scoring.match_blank("getuserbyid", sensitive) == scoring.RESULT_INCORRECT


def test_surrounding_and_inner_whitespace_is_forgiven() -> None:
    assert scoring.match_blank("  observer  ", _blank()) == scoring.RESULT_EXACT
    assert scoring.match_blank("\tobserver\n", _blank()) == scoring.RESULT_EXACT
    spaced = _blank(accepted=["write ahead log"])
    assert scoring.match_blank("write   ahead\tlog", spaced) == scoring.RESULT_EXACT


def test_an_empty_blank_is_unanswered_rather_than_wrong() -> None:
    """A blank nobody filled in and a blank filled in wrongly are different
    facts about the answer, and the recruiter's view shows both."""
    assert scoring.match_blank("", _blank()) == scoring.RESULT_NOT_ANSWERED
    assert scoring.match_blank("   ", _blank()) == scoring.RESULT_NOT_ANSWERED


def test_a_different_term_is_incorrect_before_the_model_is_asked() -> None:
    assert scoring.match_blank("singleton", _blank()) == scoring.RESULT_INCORRECT


@pytest.mark.asyncio
async def test_a_synonym_is_credited_through_the_fuzzy_fallback() -> None:
    """"A candidate who writes 'PostgreSQL' when the key says 'Postgres' must
    not be marked wrong" (spec 2.4)."""
    asked: list[str] = []

    async def _equivalent(session, *, template, blank, value):
        asked.append(value)
        return True

    result = await scoring.score_fill_blank(
        None,
        {"template": "We store it in ___.", "blanks": [{"index": 0, "accepted": ["Postgres"]}]},
        {"values": ["PostgreSQL"]},
        equivalence=_equivalent,
    )
    assert result.score == 1.0
    assert result.blank_results == [scoring.RESULT_EQUIVALENT]
    assert asked == ["PostgreSQL"]


@pytest.mark.asyncio
async def test_an_exact_match_never_spends_a_model_call() -> None:
    """The fuzzy check runs only where the exact match failed. Asking on every
    blank would put a provider on the critical path of a correct answer."""
    calls: list[str] = []

    async def _equivalent(session, *, template, blank, value):
        calls.append(value)
        return True

    result = await scoring.score_fill_blank(
        None, _blank_payload(), {"values": ["observer", "consumers"]}, equivalence=_equivalent
    )
    assert result.score == 1.0
    assert result.blank_results == [scoring.RESULT_EXACT, scoring.RESULT_EXACT]
    assert calls == []


@pytest.mark.asyncio
async def test_a_multi_blank_question_scores_per_blank_then_aggregates() -> None:
    async def _not_equivalent(session, *, template, blank, value):
        return False

    result = await scoring.score_fill_blank(
        None, _blank_payload(), {"values": ["observer", "wrong"]}, equivalence=_not_equivalent
    )
    assert result.blank_results == [scoring.RESULT_EXACT, scoring.RESULT_INCORRECT]
    assert result.score == 0.5


@pytest.mark.asyncio
async def test_an_unavailable_equivalence_check_marks_the_blank_wrong(monkeypatch) -> None:
    """FAILS CLOSED. The alternative is the product crediting an answer no
    model ever judged, which is the silent degradation this codebase has a
    standing rule about."""
    from app.services import llm_router

    async def _boom(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(llm_router, "invoke_llm", _boom)
    equivalent = await scoring.semantically_equivalent(
        None, template="We store it in ___.", blank=_blank(accepted=["Postgres"]), value="PostgreSQL"
    )
    assert equivalent is False


@pytest.mark.asyncio
async def test_a_malformed_equivalence_response_is_not_read_as_yes(monkeypatch) -> None:
    from app.services import llm_router

    async def _shapeless(*args, **kwargs):
        return '{"verdict": "probably"}'

    monkeypatch.setattr(llm_router, "invoke_llm", _shapeless)
    assert await scoring.semantically_equivalent(
        None, template="x ___", blank=_blank(), value="something"
    ) is False


@pytest.mark.asyncio
async def test_the_score_entry_point_dispatches_by_format(monkeypatch) -> None:
    async def _not_equivalent(session, **kwargs):
        return False

    monkeypatch.setattr(scoring, "semantically_equivalent", _not_equivalent)
    single = await scoring.score(None, types.MCQ_SINGLE, _single_payload(), {"selected_option_id": "a"})
    assert single.auto_score == 1.0 and single.blank_results == []
    multi = await scoring.score(None, types.MCQ_MULTI, _multi_payload(), {"selected_option_ids": ["a"]})
    assert multi.auto_score == 0.5
    blank = await scoring.score(None, types.FILL_BLANK, _blank_payload(), {"values": ["observer", "x"]})
    assert blank.auto_score == 0.5 and len(blank.blank_results) == 2


@pytest.mark.asyncio
async def test_a_subjective_format_is_not_scored_here() -> None:
    """Evidence and coding are AI-evaluated (spec 6.2). A deterministic score
    for one would be a second scoring path for one concept."""
    for question_type in (types.EVIDENCE_BASED, types.CODING, types.SHORT_ANSWER):
        with pytest.raises(ValueError):
            await scoring.score(None, question_type, {}, {})


# ── Correctness as a word ────────────────────────────────────────────────────


def test_correctness_is_a_word_and_never_a_score() -> None:
    assert scoring.correctness_word(1.0) == scoring.CORRECT
    assert scoring.correctness_word(0.5) == scoring.PARTIALLY_CORRECT
    assert scoring.correctness_word(0.0) == scoring.INCORRECT
    assert scoring.correctness_word(None) == scoring.NOT_ANSWERED
    for value in (None, 0.0, 0.25, 0.5, 0.75, 1.0):
        word = scoring.correctness_word(value)
        assert word in scoring.CORRECTNESS_WORDS
        assert not any(character.isdigit() for character in word)


# ── The transcript line ──────────────────────────────────────────────────────


def test_an_mcq_answer_renders_as_the_option_text_the_candidate_chose() -> None:
    """The scorers and the recruiter read the transcript, and an option id is
    not something a person can read."""
    line = rendering.transcript_line(types.MCQ_SINGLE, _single_payload(), {"selected_option_id": "a"})
    assert "A write-ahead log" in line
    assert "correct" not in line.casefold()


def test_a_multi_answer_renders_every_choice_and_an_empty_one_says_so() -> None:
    line = rendering.transcript_line(
        types.MCQ_MULTI, _multi_payload(), {"selected_option_ids": ["a", "b"]}
    )
    assert "Partition the topic" in line and "Increase the consumer count" in line
    assert rendering.transcript_line(types.MCQ_MULTI, _multi_payload(), {"selected_option_ids": []}) == (
        "Selected nothing."
    )


def test_a_fill_blank_answer_renders_the_sentence_with_the_entries_in_it() -> None:
    line = rendering.transcript_line(
        types.FILL_BLANK, _blank_payload(), {"values": ["observer", ""]}
    )
    assert line == "The [observer] pattern decouples publishers from [blank]."


def test_a_coding_answer_renders_its_language_and_its_code() -> None:
    line = rendering.transcript_line(
        types.CODING, {"language": "python"}, {"language": "python", "code": "def solve(): pass"}
    )
    assert "python" in line and "def solve(): pass" in line


def test_a_text_answer_renders_as_itself() -> None:
    assert rendering.transcript_line(types.SHORT_ANSWER, {}, {"text": "I owned it."}) == "I owned it."


# ── Time spent, as a phrase ──────────────────────────────────────────────────


def test_time_spent_is_a_phrase_and_carries_no_digit() -> None:
    assert rendering.time_spent_phrase(None) is None
    assert rendering.time_spent_phrase(0) == "under a minute"
    assert rendering.time_spent_phrase(20) == "under a minute"
    assert rendering.time_spent_phrase(60) == "about a minute"
    assert rendering.time_spent_phrase(125) == "about two minutes"
    assert rendering.time_spent_phrase(7 * 60) == "about seven minutes"
    assert rendering.time_spent_phrase(60 * 60) == "about an hour"
    assert rendering.time_spent_phrase(3 * 60 * 60) == "over an hour"
    for seconds in range(0, 4000, 7):
        phrase = rendering.time_spent_phrase(seconds)
        assert not any(character.isdigit() for character in phrase), (seconds, phrase)
