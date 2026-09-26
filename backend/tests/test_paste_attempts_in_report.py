"""Every paste attempt is blocked, logged and shown in the proctoring report.

Master prompt, Phase 3: "Block paste, copy and drop everywhere (text boxes and
Monaco). Log every attempt as a proctoring event and show it in the proctoring
report." The browser refuses the action and reports it as
`BLOCKED_ACTION_ATTEMPTED` with the action it refused in `metadata.action`
(`frontend/lib/proctoring/lockdown`). The server:

- stores EVERY attempt as its own row (Path C: never a warning, never an
  ending), so the activity log lists each one;
- counts them in the report on their own lines, one per kind, in a fixed
  order (paste, copy or cut, drag and drop, anything else), after the one
  sentence that says what is blocked, so a paste is never hidden inside a
  total;
- counts an action it does not recognise, or an attempt that named none,
  under "another blocked action": still counted, never dropped.

Counts are spelled out; no digit reaches the report.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models.proctoring import POLICY_TERMINATE
from app.schemas.proctoring import EventBatchIn, EventIn
from app.services.proctoring import catalog, ingestion, phrasing
from app.services.proctoring import report as proctoring_report
from app.services.proctoring.report import EventView

from tests.test_proctoring_pipeline import _Fx, _cleanup, _events, _factory_or_skip, _load, _seed

T0 = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


class _Ps:
    """The slice of a session row the composer reads."""

    def __init__(self) -> None:
        self.started_at = T0
        self.consented_at = T0
        self.ended_at = T0 + timedelta(minutes=40)
        self.outcome = "completed"
        self.warnings_used = 0
        self.termination_reason = None
        self.session_quality = "good"


def _blocked(minute: int, action: str | None) -> EventView:
    return EventView(
        event_type="BLOCKED_ACTION_ATTEMPTED",
        occurred_at=T0 + timedelta(minutes=minute),
        duration_ms=None,
        path=catalog.PATH_C,
        warning_issued=False,
        warning_number=None,
        metadata={} if action is None else {"action": action, "via": "field"},
    )


def _compose(actions: list[str | None]) -> dict:
    return proctoring_report.compose(
        candidate_name="A Candidate",
        assessment_name="Tatva Assessment for a role",
        ps=_Ps(),
        events=[_blocked(minute, action) for minute, action in enumerate(actions)],
        audio_available=True,
    )


def test_blocking_is_path_c_and_the_browser_may_report_it() -> None:
    spec = catalog.spec_for("BLOCKED_ACTION_ATTEMPTED")
    assert spec.path == catalog.PATH_C
    assert spec.client_emittable
    assert not spec.warns and not spec.terminates


def test_each_kind_is_itemised_in_a_fixed_order_after_the_blocked_sentence() -> None:
    content = _compose(["copy", "paste", "drop", "paste", "cut", "context_menu", "paste"])
    screen = content["findings"]["screen_browser"]
    paste = phrasing.blocked_item_sentence(phrasing.BLOCKED_PASTE, times=3)
    copy = phrasing.blocked_item_sentence(phrasing.BLOCKED_COPY, times=2)
    drop = phrasing.blocked_item_sentence(phrasing.BLOCKED_DROP, times=1)
    other = phrasing.blocked_item_sentence(phrasing.BLOCKED_OTHER, times=1)
    for line in (paste, copy, drop, other):
        assert line in screen, f"missing itemised line: {line!r}"
    assert screen.index(paste) < screen.index(copy) < screen.index(drop) < screen.index(other)
    assert "three times" in paste
    # The summary sentence that says what is blocked comes first.
    assert screen.index(paste) > 0


def test_a_paste_alone_is_named_and_is_never_folded_into_a_total() -> None:
    content = _compose(["paste"])
    screen = content["findings"]["screen_browser"]
    assert phrasing.blocked_item_sentence(phrasing.BLOCKED_PASTE, times=1) in screen
    assert not any("copy or cut" in line for line in screen)


def test_every_attempt_has_its_own_row_in_the_activity_log() -> None:
    content = _compose(["paste", "paste", "copy", None])
    described = [row["what_happened"] for row in content["activity_log"]]
    assert described.count("Tried to paste into an answer") == 2
    assert described.count("Tried to copy or cut text") == 1
    assert described.count("A blocked action was attempted") == 1
    assert all(row["what_the_system_did"] == "Noted it" for row in content["activity_log"])


@pytest.mark.parametrize(
    ("metadata", "kind"),
    [
        ({"action": "paste"}, phrasing.BLOCKED_PASTE),
        ({"action": " PASTE "}, phrasing.BLOCKED_PASTE),
        ({"action": "copy"}, phrasing.BLOCKED_COPY),
        ({"action": "cut"}, phrasing.BLOCKED_COPY),
        ({"action": "drop"}, phrasing.BLOCKED_DROP),
        ({"action": "developer_tools"}, phrasing.BLOCKED_OTHER),
        ({"action": 7}, phrasing.BLOCKED_OTHER),
        ({}, phrasing.BLOCKED_OTHER),
        (None, phrasing.BLOCKED_OTHER),
    ],
)
def test_an_unknown_or_missing_action_is_counted_as_another_blocked_action(
    metadata, kind
) -> None:
    assert phrasing.blocked_kind(metadata) == kind


def test_no_blocked_line_says_a_digit() -> None:
    content = _compose(["paste"] * 12 + ["copy"] * 4)
    for line in content["findings"]["screen_browser"]:
        assert not re.search(r"\d", line), line


def test_no_attempt_means_no_blocked_lines() -> None:
    content = _compose([])
    assert not any("Tried to" in line for line in content["findings"]["screen_browser"])


@pytest.mark.asyncio
async def test_every_attempt_is_stored_and_none_warns_even_under_terminate() -> None:
    """Through the real ingestion path and the real table: five attempts in
    one batch are five rows on Path C, no warning is issued, the session
    carries on, and the generated report itemises them."""
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, policy=POLICY_TERMINATE)
        now = datetime.now(timezone.utc)
        batch = EventBatchIn(
            events=[
                EventIn(
                    event_type="BLOCKED_ACTION_ATTEMPTED",
                    occurred_at=now,
                    metadata={"action": action, "via": "field"},
                )
                for action in ("paste", "paste", "copy", "drop", "print")
            ]
        )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    out = await ingestion.ingest(
                        s, ps, POLICY_TERMINATE, batch, now=now, enqueue=fx.enqueue
                    )
                    assert out.accepted == 5
                    assert out.warning is None and out.termination is None
                    assert out.warnings_used == 0
                    rows = await _events(s, fx)
                    assert [row.event_type for row in rows] == ["BLOCKED_ACTION_ATTEMPTED"] * 5
                    assert {row.path for row in rows} == {catalog.PATH_C}
                    assert sorted(row.metadata_json["action"] for row in rows) == [
                        "copy", "drop", "paste", "paste", "print",
                    ]
                    ps.outcome = "completed"
                    ps.ended_at = now
                    report = await proctoring_report.generate(s, ps)
                    screen = report.report_content["findings"]["screen_browser"]
                    assert phrasing.blocked_item_sentence(
                        phrasing.BLOCKED_PASTE, times=2
                    ) in screen
                    assert fx.enqueued == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
