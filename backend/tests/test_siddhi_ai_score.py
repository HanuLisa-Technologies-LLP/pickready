"""The AI Score section: Yukti's snapshot, checked, frozen, words only.

Yukti lands from Phase 2, so its value is stubbed here in exactly the shape
the seam reads (`status`, `grade`, `tags` with `text` and `polarity`,
`header`). The stub also carries a `score`, because the one thing that must
never happen is that a number Yukti holds reaches the frozen section.
"""
from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace

import pytest

from app.services.siddhi import ai_score, numbers


def _yukti(**overrides):
    base = dict(
        status="scored",
        grade="Matching",
        score=82,
        header="Strong ownership of production services, lighter on data modelling.",
        tags=(
            SimpleNamespace(text="Owned the orders service migration", polarity="positive"),
            SimpleNamespace(text="No evidence of schema design", polarity="negative"),
        ),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_the_snapshot_is_words_and_never_the_score() -> None:
    frozen = ai_score.snapshot_from(_yukti())
    stored = frozen.as_json()
    assert stored["grade"] == "Matching"
    assert [tag["polarity"] for tag in stored["tags"]] == ["positive", "negative"]
    assert "score" not in stored
    blob = json.dumps({key: value for key, value in stored.items() if key != "shape"})
    assert "82" not in blob
    assert not re.search(r"\d", blob)
    # The delivered document's own structural rule finds nothing, the stored
    # shape's name included: it is a string, not a version number.
    assert numbers.scan(stored, path="ai_score") == []
    assert frozen.needs_human_review is False
    assert frozen.review_findings() == []


def test_a_tag_or_header_the_delivered_rule_refuses_is_withheld_and_flags_review(caplog) -> None:
    source = _yukti(
        header="Matching 82 against this role.",
        tags=(
            SimpleNamespace(text="Cut incidents by 30%", polarity="positive"),
            SimpleNamespace(text="Led the payments cutover " + chr(8212) + " twice", polarity="positive"),
            SimpleNamespace(text="Owned the orders service migration", polarity="positive"),
        ),
    )
    with caplog.at_level("WARNING"):
        frozen = ai_score.snapshot_from(source)
    assert frozen.header == ""
    assert [tag.text for tag in frozen.tags] == ["Owned the orders service migration"]
    assert frozen.withheld == (
        ai_score.WITHHELD_NUMBER,
        ai_score.WITHHELD_NUMBER,
        ai_score.WITHHELD_EM_DASH,
    )
    assert frozen.needs_human_review is True
    [finding] = frozen.review_findings()
    assert finding["issue"] == "ai_score_text_withheld"
    blob = json.dumps(frozen.as_json()) + json.dumps(finding)
    for text in ("30%", "82", "cutover"):
        assert text not in blob
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "30%" not in logged and "cutover" not in logged


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "legacy"},
        {"status": "done"},
        {"grade": "Very High"},
        {"status": "not_assessed", "grade": "Matching"},
        {"status": "scored", "grade": None},
        {"tags": (SimpleNamespace(text="Owned it", polarity="neutral"),)},
    ],
)
def test_a_value_outside_the_vocabulary_raises(overrides) -> None:
    with pytest.raises(ValueError):
        ai_score.snapshot_from(_yukti(**overrides))


def test_a_not_assessed_snapshot_states_no_grade() -> None:
    frozen = ai_score.snapshot_from(
        _yukti(status="not_assessed", grade=None, tags=(), header="The resume could not be read.")
    )
    assert frozen.grade is None
    assert frozen.as_json()["grade"] is None


def test_the_stored_snapshot_reads_back_identically() -> None:
    frozen = ai_score.snapshot_from(_yukti(tags=(SimpleNamespace(text="Cut incidents by 30%", polarity="positive"),)))
    stored = json.loads(json.dumps(frozen.as_json()))
    assert ai_score.read_snapshot(stored) == frozen
    assert ai_score.read_snapshot(None) is None
    assert ai_score.read_snapshot({}) is None
    with pytest.raises(KeyError):
        ai_score.read_snapshot({"grade": "Matching"})


@pytest.mark.asyncio
async def test_the_seam_reads_through_the_source_and_invents_nothing_when_it_is_empty() -> None:
    link_id = uuid.uuid4()
    asked: list = []

    async def _empty(session, link):
        asked.append((session, link))
        return None

    assert await ai_score.snapshot_for_report("session", link_id, source=_empty) is None
    assert asked == [("session", link_id)]

    async def _found(session, link):
        return _yukti()

    frozen = await ai_score.snapshot_for_report("session", link_id, source=_found)
    assert frozen.grade == "Matching"
