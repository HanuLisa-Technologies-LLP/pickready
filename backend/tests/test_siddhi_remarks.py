"""A remark says how it was written, and provenance never claims a call that did not happen.

`bounded_remark` used to return a bare string, so a remark a model wrote and
the fixed template the loop fell back to during an outage were
indistinguishable, and the report's `model_id` was stamped from the scoring
mode rather than from any call that actually succeeded. These tests pin the
replacement: `Remark(text, source)`, and a recorder told `model_call` only
after the critic ACCEPTED an attempt, `template` otherwise. Probes follow the
same rule and carry `probes_source` on the stored entry.
"""
from __future__ import annotations

import json
import re

import pytest

from app.services import gap_analysis, ppi
from app.services.siddhi import provenance, remarks


class _Recorder:
    """The two calls Siddhi makes, remembered."""

    def __init__(self) -> None:
        self.models: list[tuple[str, str | None]] = []
        self.templates: list[str] = []

    def model_call(self, task_type: str, prompt_name: str | None) -> None:
        self.models.append((task_type, prompt_name))

    def template(self, where: str) -> None:
        self.templates.append(where)


EVIDENCE = (
    "the candidate's own answers probing this competency: - I moved the orders "
    "service onto the new cluster and wrote the rollback runbook myself."
)

GOOD_REMARK = (
    "The candidate moved the orders service onto the new cluster and wrote the "
    "rollback runbook personally, which shows owned delivery of a risky change. "
    "Interviewers should probe how the rollback was rehearsed, which signals "
    "triggered it, and how the runbook was kept current after the cluster "
    "migration finished."
)


def test_the_recorder_used_here_satisfies_the_protocol() -> None:
    assert isinstance(_Recorder(), provenance.ProvenanceSink)


@pytest.mark.asyncio
async def test_an_accepted_model_remark_records_the_call_and_says_model(monkeypatch) -> None:
    assert 45 <= remarks.word_count(GOOD_REMARK) <= 50

    async def _chat(task_type, messages, **kwargs):
        return GOOD_REMARK

    monkeypatch.setattr(remarks.llm_router, "chat_completion", _chat)
    recorder = _Recorder()
    remark = await remarks.bounded_remark(
        None, "Distributed Systems", EVIDENCE, rating="Matching", provenance=recorder
    )
    assert remark.source == remarks.SOURCE_MODEL
    assert remark.text == GOOD_REMARK
    assert recorder.models == [(remarks.REMARK_TASK_TYPE, None)]
    assert recorder.templates == []
    assert remark.row_fields() == {"remark": GOOD_REMARK, "remark_provenance": "model"}


@pytest.mark.asyncio
async def test_an_outage_returns_the_template_says_so_and_records_no_model(monkeypatch) -> None:
    async def _down(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(remarks.llm_router, "chat_completion", _down)
    recorder = _Recorder()
    remark = await remarks.bounded_remark(
        None, "Distributed Systems", EVIDENCE, rating="Matching", provenance=recorder
    )
    assert remark.source == remarks.SOURCE_TEMPLATE
    assert remark.is_template
    assert recorder.models == [], "no model call succeeded, so none is recorded"
    assert recorder.templates == ["remark:Distributed Systems"]
    assert 45 <= remarks.word_count(remark.text) <= 50


@pytest.mark.asyncio
async def test_a_remark_the_critic_never_accepts_is_a_template_too(monkeypatch) -> None:
    """A model call that returned text the critic refused is not a model call
    that produced the remark. Recording it would claim authorship of text the
    model did not write."""

    async def _numbers(task_type, messages, **kwargs):
        return "The candidate scored 82 out of 100 on this competency."

    monkeypatch.setattr(remarks.llm_router, "chat_completion", _numbers)
    recorder = _Recorder()
    remark = await remarks.bounded_remark(
        None, "Distributed Systems", EVIDENCE, rating="Matching", provenance=recorder
    )
    assert remark.source == remarks.SOURCE_TEMPLATE
    assert recorder.models == []


def test_catalogue_remarks_are_named_as_catalogue_and_fit_the_contract() -> None:
    for remark in (remarks.unanswered_remark("Distributed Systems"), remarks.not_assessed_remark()):
        assert remark.source == remarks.SOURCE_CATALOGUE
        assert 45 <= remarks.word_count(remark.text) <= 50, remark.text
        assert not re.search(r"\d", remark.text)
        assert chr(8212) not in remark.text


def test_the_not_assessed_sentence_says_what_happened_and_grades_nothing() -> None:
    text = remarks.NOT_ASSESSED_REMARK
    assert "could not be evaluated" in text
    for grade in ("Highly Matching", "Moderately Matching", "Not Matching", "Matching"):
        assert grade not in text


def test_an_unknown_source_is_refused() -> None:
    with pytest.raises(ValueError):
        remarks.Remark(text="x", source="guessed")


def test_every_rating_template_fits_the_contract_for_a_long_anchor() -> None:
    long_evidence = "Stakeholder alignment across procurement legal finance engineering " * 3
    for rating in ("Highly Matching", "Matching", "Moderately Matching", "Not Matching", None):
        text = remarks.rating_differentiated_template(long_evidence, rating)
        assert 45 <= remarks.word_count(text) <= 50, (rating, text)


def test_the_template_note_is_words_only() -> None:
    assert not re.search(r"\d", remarks.TEMPLATE_REMARK_NOTE)
    assert chr(8212) not in remarks.TEMPLATE_REMARK_NOTE


# ── Probes follow the same rule ──────────────────────────────────────────────


def _gap_rows() -> list[dict]:
    return [
        {
            "category": ppi.CATEGORY_NICE_TO_HAVE,
            "name": "Observability",
            "score": 65,
            "ordinal": 1,
            "remark": "Named the dashboards only.",
        }
    ]


def _gap_exchanges() -> dict:
    return {
        "Observability": [
            {
                "question": "How do you know a deploy is healthy?",
                "answer": "I watch the error rate dashboard for the first ten minutes.",
                "question_id": "3f1c3c9e-9d1f-4c1b-8f55-3f6f7a1b2c3d",
                "message_ids": ["8a0d8f8e-6f2b-4a52-9f0c-2a4e1b7c9d10"],
            }
        ]
    }


@pytest.mark.asyncio
async def test_fallback_probes_are_marked_template_and_recorded(monkeypatch) -> None:
    async def _down(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _down)
    recorder = _Recorder()
    section = await gap_analysis.build_gap_groups(
        None, _gap_rows(), _gap_exchanges(), provenance=recorder
    )
    entry = section["groups"][1]["items"][0]
    assert entry["probes_source"] == gap_analysis.PROBES_TEMPLATE
    assert recorder.templates == ["probes:Observability"]
    assert recorder.models == []


@pytest.mark.asyncio
async def test_model_probes_are_marked_model_and_record_the_prompt(monkeypatch) -> None:
    grounded = (
        "You mentioned watching the error rate dashboard, so tell me about the "
        "one deploy where that signal was misleading and what you did next after."
    )
    seen: list[str] = []

    async def _chat(task_type, messages, **kwargs):
        seen.append(messages[1]["content"])
        return json.dumps({"probes": [grounded]})

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _chat)
    recorder = _Recorder()
    section = await gap_analysis.build_gap_groups(
        None, _gap_rows(), _gap_exchanges(), provenance=recorder
    )
    entry = section["groups"][1]["items"][0]
    assert entry["probes"] == [grounded]
    assert entry["probes_source"] == gap_analysis.PROBES_MODEL
    assert recorder.models == [("report_synthesis", gap_analysis.PROBE_PROMPT)]
    # The row ids the exchange carries for the trail never reach the prompt.
    assert "8a0d8f8e" not in seen[0]
    assert "3f1c3c9e" not in seen[0]


@pytest.mark.asyncio
async def test_a_probe_quoting_a_percentage_is_rejected_by_the_delivered_rule(monkeypatch) -> None:
    """The interviewer guard permits a bare percentage; the delivered report
    does not, and a probe is printed in the delivered report."""
    attempts: list[int] = []
    grounded = (
        "You mentioned watching the error rate dashboard, so tell me about the "
        "one deploy where that signal was misleading and what you did next after."
    )

    async def _chat(task_type, messages, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return json.dumps({"probes": [
                "You mentioned the dashboard cut incidents by 30%, so walk me through "
                "exactly how you measured that change and who else confirmed it back then."
            ]})
        return json.dumps({"probes": [grounded]})

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _chat)
    section = await gap_analysis.build_gap_groups(
        None, _gap_rows(), _gap_exchanges(), provenance=_Recorder()
    )
    assert section["groups"][1]["items"][0]["probes"] == [grounded]
    assert len(attempts) == 2
