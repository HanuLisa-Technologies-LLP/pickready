"""Yukti part six: the application answers, deterministic, and never zero for
"not asked".

The mutation this file exists to catch: returning 0 for a missing answer. A
sourced candidate never answered the application questions, and scoring that
silence as zero would rank every non-applicant below every applicant on
paperwork alone.
"""
from __future__ import annotations

import pytest

from app.services import application_validation, recruiter_columns
from app.services.yukti import config, validation_fit

RANGE = {"ctc_min": 1_200_000, "ctc_max": 1_800_000}


@pytest.mark.parametrize(
    ("expected", "word", "value"),
    [
        ("15,00,000", recruiter_columns.CTC_WITHIN, 1.0),
        ("18 LPA", recruiter_columns.CTC_WITHIN, 1.0),  # the inclusive upper edge
        ("10 LPA", recruiter_columns.CTC_BELOW, 1.0),
        ("25,00,000", recruiter_columns.CTC_ABOVE, 0.0),
    ],
)
def test_each_ctc_word_scores_its_value(expected: str, word: str, value: float) -> None:
    fit = validation_fit.score({"expected_ctc": expected}, RANGE)
    assert fit.parts == {config.VALIDATION_CTC: word}
    assert fit.score == pytest.approx(value * 100)


@pytest.mark.parametrize("option", application_validation.NOTICE_PERIOD_OPTIONS)
def test_every_notice_option_scores_through_its_bucket(option: str) -> None:
    fit = validation_fit.score({"notice_period": option}, None)
    bucket = recruiter_columns.notice_period_bucket(option)
    assert fit.parts == {config.VALIDATION_NOTICE: bucket}
    assert fit.score == pytest.approx(
        config.VALIDATION_VALUES[config.VALIDATION_NOTICE][bucket] * 100
    )


@pytest.mark.parametrize("option", application_validation.DOCUMENT_READINESS_OPTIONS)
def test_every_document_option_scores(option: str) -> None:
    fit = validation_fit.score({"document_readiness": f"  {option.upper()} "}, None)
    assert fit.parts == {config.VALIDATION_DOCUMENTS: option}
    assert fit.score == pytest.approx(
        config.VALIDATION_VALUES[config.VALIDATION_DOCUMENTS][option] * 100
    )


@pytest.mark.parametrize("validation", [None, {}, "not a dict", []])
def test_no_answers_is_excluded_never_zero(validation) -> None:
    fit = validation_fit.score(validation, RANGE)
    assert fit.score is None
    assert dict(fit.parts) == {}


def test_unparseable_answers_are_excluded_never_zero() -> None:
    fit = validation_fit.score(
        {"expected_ctc": "negotiable", "notice_period": "", "document_readiness": "soon"},
        RANGE,
    )
    assert fit.score is None


def test_ctc_without_a_job_range_is_not_compared() -> None:
    fit = validation_fit.score({"expected_ctc": "25,00,000", "notice_period": "Immediate"}, None)
    assert config.VALIDATION_CTC not in fit.parts
    assert fit.score == pytest.approx(100.0)


def test_partial_answers_renormalise_over_what_was_answered() -> None:
    # CTC above range (0.0, weight 50) and notice 60 days (bucket "30 to 60
    # days", 0.6, weight 30); documents unanswered and excluded.
    fit = validation_fit.score(
        {"expected_ctc": "30 LPA", "notice_period": "60 days"}, RANGE
    )
    assert set(fit.parts) == {config.VALIDATION_CTC, config.VALIDATION_NOTICE}
    expected = (50 * 0.0 + 30 * 0.6) / (50 + 30) * 100
    assert fit.score == pytest.approx(round(expected, 1))


def test_all_three_answers() -> None:
    fit = validation_fit.score(
        {
            "expected_ctc": "15 LPA",
            "notice_period": "Serving notice period",
            "document_readiness": "Most documents ready",
        },
        RANGE,
    )
    expected = (50 * 1.0 + 30 * 0.8 + 20 * 0.75) / 100 * 100
    assert fit.score == pytest.approx(round(expected, 1))


def test_the_parts_carry_words_and_never_the_amount() -> None:
    fit = validation_fit.score({"expected_ctc": "15,00,000"}, RANGE)
    for word in fit.parts.values():
        assert not any(ch.isdigit() for ch in word)


def test_a_word_with_no_value_raises_rather_than_scoring_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        recruiter_columns, "notice_period_bucket", lambda raw: "Next quarter"
    )
    with pytest.raises(KeyError, match="Next quarter"):
        validation_fit.score({"notice_period": "whenever"}, None)
