"""Interview telemetry: the log line's shape, the arithmetic, and the promise
that none of it can ever break the request it observes.

The privacy tests are the ones that matter most here. A telemetry module is
edited casually -- "just add the answer so we can see what they said" is a
one-line change that nobody reviews hard -- so the rule that answer and question
TEXT never reaches an ordinary log is pinned rather than merely documented.
"""
from __future__ import annotations

import logging

import pytest

from app.services import interview_telemetry as it
from app.services.interview_telemetry import (
    TurnEvent,
    conversation_summary,
    emit_summary,
    record_turn,
)


def _event(**overrides) -> TurnEvent:
    base = dict(
        conversation_id="conv-1",
        turn_index=0,
        question_key="tech_kafka_1",
        domain="technical",
        answer_label="substantive",
        action="advanced",
        generated=True,
        degraded=False,
        latency_ms=100,
    )
    base.update(overrides)
    return TurnEvent(**base)


# ── The log line ─────────────────────────────────────────────────────────────

def test_record_turn_emits_one_info_line_in_key_value_shape(caplog):
    with caplog.at_level(logging.INFO, logger=it.logger.name):
        record_turn(_event(turn_index=3, action="followed_up", latency_ms=812))

    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert caplog.records[0].levelno == logging.INFO
    assert line.startswith("interview_telemetry.turn ")
    for fragment in (
        "conversation_id=conv-1",
        "turn_index=3",
        "question_key=tech_kafka_1",
        "domain=technical",
        "answer_label=substantive",
        "action=followed_up",
        "generated=True",
        "degraded=False",
        "latency_ms=812",
    ):
        assert fragment in line


def test_log_line_has_no_spaces_inside_a_value(caplog):
    """A value with a space in it silently breaks every `key=value` query
    written against this line -- the next field reads as part of this one."""
    with caplog.at_level(logging.INFO, logger=it.logger.name):
        record_turn(_event(question_key="two words here", domain="pp i"))

    line = caplog.records[0].getMessage()
    fields = line.split()[1:]  # drop the event name
    assert all("=" in field for field in fields)
    assert "question_key=two_words_here" in line


# ── Privacy ──────────────────────────────────────────────────────────────────

def test_turn_line_never_carries_answer_or_question_text(caplog):
    """TurnEvent has no field for text, and the line must not acquire one by
    some other route (a repr of the event, a stray %s of the whole object)."""
    with caplog.at_level(logging.INFO, logger=it.logger.name):
        record_turn(_event())

    line = caplog.records[0].getMessage()
    assert "TurnEvent" not in line
    for forbidden in ("answer=", "text=", "question_text=", "name=", "email=", "@"):
        assert forbidden not in line


def test_a_label_carrying_smuggled_answer_text_is_truncated(caplog):
    """Defence in depth against the caller mistake this module cannot prevent:
    passing an answer where a label belongs. It must cost 64 characters of log,
    not a paragraph of a real candidate's answer."""
    smuggled = "I led the migration off Kafka at " + ("x" * 500)
    with caplog.at_level(logging.INFO, logger=it.logger.name):
        record_turn(_event(answer_label=smuggled))

    line = caplog.records[0].getMessage()
    assert len(line) < 400
    assert "x" * 100 not in line


def test_summary_line_never_carries_text(caplog):
    with caplog.at_level(logging.INFO, logger=it.logger.name):
        emit_summary("conv-9", [_event(), _event(action="followed_up")])

    line = caplog.records[-1].getMessage()
    assert line.startswith("interview_telemetry.conversation ")
    assert "conversation_id=conv-9" in line
    assert "total_turns=2" in line
    assert "TurnEvent" not in line


def test_emit_summary_logs_exactly_one_line(caplog):
    """One event, one line. Splitting it per label would force a reader to
    reassemble a conversation out of interleaved lines."""
    with caplog.at_level(logging.INFO, logger=it.logger.name):
        emit_summary("conv-9", [_event() for _ in range(5)])

    assert len(caplog.records) == 1
    assert "answer_labels=substantive:5" in caplog.records[0].getMessage()


# ── Summary arithmetic ───────────────────────────────────────────────────────

def test_empty_conversation_does_not_divide_by_zero():
    summary = conversation_summary([])
    assert summary["total_turns"] == 0
    assert summary["answer_labels"] == {}
    assert summary["actions"] == {}
    # None, not 0.0: "no turn adapted" is a claim, and a conversation with no
    # turns has not made it.
    assert summary["adaptivity_rate"] is None
    assert summary["generation_rate"] is None
    assert summary["degradation_rate"] is None
    assert summary["latency_p50_ms"] is None
    assert summary["latency_p95_ms"] is None


def test_counts_by_label_and_action():
    events = [
        _event(answer_label="substantive", action="advanced"),
        _event(answer_label="substantive", action="followed_up"),
        _event(answer_label="gibberish", action="rechallenged"),
        _event(answer_label="empty", action="advanced"),
    ]
    summary = conversation_summary(events)
    assert summary["total_turns"] == 4
    assert summary["answer_labels"] == {"substantive": 2, "gibberish": 1, "empty": 1}
    assert summary["actions"] == {"advanced": 2, "followed_up": 1, "rechallenged": 1}


def test_adaptivity_rate_counts_follow_ups_and_rechallenges():
    events = [
        _event(action="advanced"),
        _event(action="advanced"),
        _event(action="followed_up"),
        _event(action="rechallenged"),
    ]
    assert conversation_summary(events)["adaptivity_rate"] == 0.5


def test_adaptivity_rate_is_zero_for_a_fully_scripted_conversation():
    """The distinguishing case: zero here is a real measurement (nothing
    adapted), where the empty conversation returns None (nothing happened)."""
    events = [_event(action="advanced") for _ in range(3)]
    assert conversation_summary(events)["adaptivity_rate"] == 0.0


def test_generation_and_degradation_rates():
    events = [
        _event(generated=True, degraded=False),
        _event(generated=True, degraded=False),
        _event(generated=False, degraded=True),
        _event(generated=False, degraded=True),
        _event(generated=True, degraded=False),
    ]
    summary = conversation_summary(events)
    assert summary["generation_rate"] == 0.6
    assert summary["degradation_rate"] == 0.4


def test_percentiles_are_observed_values_not_interpolations():
    events = [_event(latency_ms=ms) for ms in range(100, 1100, 100)]  # 10 turns
    summary = conversation_summary(events)
    # Nearest rank: p50 is the 5th of 10, p95 is the 10th.
    assert summary["latency_p50_ms"] == 500
    assert summary["latency_p95_ms"] == 1000
    assert summary["latency_p95_ms"] in {e.latency_ms for e in events}


def test_percentiles_on_a_single_turn():
    summary = conversation_summary([_event(latency_ms=42)])
    assert summary["latency_p50_ms"] == 42
    assert summary["latency_p95_ms"] == 42


def test_percentiles_ignore_input_order():
    shuffled = [_event(latency_ms=ms) for ms in (900, 100, 500, 300, 700)]
    summary = conversation_summary(shuffled)
    assert summary["latency_p50_ms"] == 500
    assert summary["latency_p95_ms"] == 900


def test_summary_returns_a_plain_dict():
    assert type(conversation_summary([_event()])) is dict


# ── Never raises ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "malformed",
    [
        None,
        "not a list",
        7,
        [None],
        [object()],
        [{"answer_label": "substantive"}],
        [_event(), None, "junk"],
    ],
)
def test_conversation_summary_never_raises(malformed):
    """Observability that can break the request it observes is worse than no
    observability -- a candidate is mid-assessment on a live request."""
    result = conversation_summary(malformed)
    assert isinstance(result, dict)
    assert "total_turns" in result


def test_unusable_entries_are_skipped_not_fatal():
    """One bad entry must not discard the counters for the turns that are fine.
    Someone reading this is usually here because something already went wrong."""
    summary = conversation_summary([_event(), None, _event(action="followed_up")])
    assert summary["total_turns"] == 2
    assert summary["adaptivity_rate"] == 0.5


def test_unreadable_latency_still_counts_as_a_turn():
    events = [_event(latency_ms="soon"), _event(latency_ms=200)]
    summary = conversation_summary(events)
    assert summary["total_turns"] == 2
    assert summary["latency_p50_ms"] == 200


@pytest.mark.parametrize("bad", [None, object(), "junk", 3])
def test_record_turn_never_raises(bad):
    record_turn(bad)


@pytest.mark.parametrize(
    "conversation_id,events",
    [("conv-1", None), (None, [_event()]), (object(), "junk")],
)
def test_emit_summary_never_raises(conversation_id, events):
    emit_summary(conversation_id, events)


def test_record_turn_survives_a_broken_logging_handler(monkeypatch):
    """The failure mode nobody anticipates: logging itself throws. A formatter
    misconfiguration must not take down a live assessment turn."""
    def explode(*args, **kwargs):
        raise RuntimeError("handler is broken")

    monkeypatch.setattr(it.logger, "info", explode)
    record_turn(_event())
    emit_summary("conv-1", [_event()])


# ── The rule this dict must not break ────────────────────────────────────────

def test_summary_is_operator_data_and_says_so():
    """The dict is full of rates and is exactly the thing someone would put on
    a page. The warning is load-bearing documentation, so it is pinned."""
    assert "NEVER" in (it.__doc__ or "")
    assert "client" in (it.__doc__ or "").lower()
    assert "OPERATOR DATA ONLY" in (conversation_summary.__doc__ or "")


def test_module_has_no_em_dash():
    """tests/test_platform_audit.py enforces this repo-wide; asserted here too
    so a local edit fails fast rather than in an unrelated audit run."""
    import inspect

    source = inspect.getsource(it)
    assert chr(8212) not in source
