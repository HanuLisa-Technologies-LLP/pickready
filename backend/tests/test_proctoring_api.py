"""The routes, the audio hand-off and the behaviour recorder (sections 8, 3.4).

Session creation is the one route with real rules in it (consent, the system
check, the invitation, idempotency), so it is driven against the database
here. The audio path is driven with an injected poster rather than a live
analysis service: what is being tested is the second-voice rule and the
in-memory contract, not somebody else's HTTP server.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models.proctoring import (
    OUTCOME_ACTIVE,
    OUTCOME_COMPLETED,
    POLICY_CONTINUE_AND_NOTE,
    QUALITY_DEGRADED,
    QUALITY_GOOD,
    QUALITY_POOR,
)
from app.schemas.assessments import AnswerBehaviourIn
from app.schemas.proctoring import DeviceContextIn, SessionCreateIn, SystemCheckIn
from app.services.proctoring import audio as proctoring_audio
from app.services.proctoring import behaviour, state
from app.services.proctoring.config import get_config

from tests.test_proctoring_pipeline import (  # reuse one harness, not two
    _Fx,
    _cleanup,
    _events,
    _factory_or_skip,
    _load,
    _seed,
)

CONFIG = get_config()
DESCRIPTOR = [0.1] * 128


def _create_body(**overrides) -> SessionCreateIn:
    checks = {
        "camera": True,
        "microphone": True,
        "browser_supported": True,
        "fullscreen_supported": True,
        "face_detected": True,
        "inference_adequate": True,
        "measured_fps": float(CONFIG.sampling_fps_normal),
    }
    checks.update(overrides.pop("system_check", {}))
    fields = {
        "consent": True,
        "device_context": DeviceContextIn(user_agent="Chrome", platform="Win32"),
        "face_descriptor": list(DESCRIPTOR),
    }
    fields.update(overrides)
    return SessionCreateIn(system_check=SystemCheckIn(**checks), **fields)


@pytest.fixture
def analysis_configured(monkeypatch):
    """A configuration WITH an analysis service.

    The test deployment has none, which is the honest default (there is no
    Hugging Face token in the test stack), so the second-voice rule would
    otherwise never be reached by any test. Patching the config the audio
    module reads exercises the rule without pretending a service is running:
    the poster is injected separately.
    """
    import dataclasses

    configured = dataclasses.replace(
        CONFIG, analysis_service_url="http://analysis.invalid:8100"
    )
    monkeypatch.setattr(proctoring_audio, "get_config", lambda: configured)
    return configured


def _user(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(
        user_id=fx.user_id, tenant_id=None, role=Role.candidate, audience=AUDIENCE_CANDIDATE
    )


# ══════════════════════════════════════════════════════════════════════════
# SESSION CREATION (section 8.1, 8.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_consent_must_be_given_explicitly() -> None:
    """Section 8.1 requires an explicit "I understand and agree". A default,
    an omission or a false is not consent, and the schema refuses each."""
    from pydantic import ValidationError

    for value in (False, None):
        with pytest.raises(ValidationError):
            _create_body(consent=value)


@pytest.mark.asyncio
async def test_a_session_records_consent_with_its_timestamp() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope
        from app.models.proctoring import ProctoringSession

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    # The fixture already seeded one; this route must find it.
                    existing = await s.get(ProctoringSession, fx.ps_id)
                    assert existing.consented_at is not None
                    out = await api.create_session(
                        fx.link_id, _create_body(), user=_user(fx), session=s
                    )
                    assert out.session_id == fx.ps_id
                    assert out.status == OUTCOME_ACTIVE
                    assert out.consented_at is not None
                    assert out.warning_policy == POLICY_CONTINUE_AND_NOTE
                    assert out.max_warnings == CONFIG.max_warnings
                    assert out.config["max_warnings"] == CONFIG.max_warnings
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_creating_a_session_twice_returns_the_same_one() -> None:
    """A reload of the consent page must not open a second session or reset
    the warning count. Idempotency here is what makes the counter meaningful."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, warnings_used=2)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    first = await api.create_session(
                        fx.link_id, _create_body(), user=_user(fx), session=s
                    )
                    second = await api.create_session(
                        fx.link_id, _create_body(), user=_user(fx), session=s
                    )
                    assert first.session_id == second.session_id
                    assert second.warnings_used == 2
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_reopening_a_session_never_overwrites_the_face_baseline() -> None:
    """The baseline is the identity the session is anchored to.

    Overwriting it on a reopen would let a different person install their own
    face and pass every later identity check, which defeats section 3.3
    completely and would leave no trace anywhere.
    """
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        other_face = [0.9] * 128
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    original = list(ps.face_descriptor_baseline)
                    await api.create_session(
                        fx.link_id,
                        _create_body(face_descriptor=other_face),
                        user=_user(fx), session=s,
                    )
                    await s.refresh(ps)
                    assert list(ps.face_descriptor_baseline) == original
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_reopening_with_a_different_face_is_recorded_as_a_mismatch() -> None:
    """It feeds the ordinary consecutive-mismatch rule rather than a second
    rule of its own, so a reopen by someone else counts exactly as a
    mid-assessment check by someone else does."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope
        from app.services.proctoring import catalog
        from app.services.proctoring.identity import MISMATCH_DISTANCE_KEY

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await api.create_session(
                        fx.link_id,
                        _create_body(face_descriptor=[0.9] * 128),
                        user=_user(fx), session=s,
                    )
                    rows = await _events(s, fx)
                    assert [r.event_type for r in rows] == ["IDENTITY_CHECK_MISMATCH"]
                    assert rows[0].path == catalog.PATH_C
                    assert rows[0].metadata_json["source"] == "session_reopened"
                    assert rows[0].metadata_json[MISMATCH_DISTANCE_KEY] > (
                        CONFIG.face_distance_threshold
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_reopening_with_the_same_face_records_nothing() -> None:
    """The false-positive direction. A candidate who reloaded the page is the
    ordinary case and must leave no mark on their own report."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await api.create_session(
                        fx.link_id, _create_body(), user=_user(fx), session=s
                    )
                    assert await _events(s, fx) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_failed_system_check_refuses_to_open_a_session() -> None:
    """Section 8.2: "Do not let the assessment begin until all pass."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as caught:
                        await api.create_session(
                            fx.link_id,
                            _create_body(system_check={"camera": False}),
                            user=_user(fx), session=s,
                        )
                    assert caught.value.status_code == 409
                    assert "system check" in caught.value.detail.lower()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_another_candidates_application_is_not_reachable() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        stranger = _Fx()
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as caught:
                        await api.create_session(
                            fx.link_id, _create_body(), user=_user(stranger), session=s
                        )
                    assert caught.value.status_code == 404
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_slow_device_opens_a_session_marked_degraded() -> None:
    """Section 3.6: a slow laptop is recorded, never refused."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        slow = float(CONFIG.sampling_fps_degraded)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await api.create_session(
                        fx.link_id,
                        _create_body(system_check={"measured_fps": slow}),
                        user=_user(fx), session=s,
                    )
                    assert out.status == OUTCOME_ACTIVE
                    ps = await _load(s, fx)
                    assert ps.session_quality == QUALITY_DEGRADED
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_session_on_an_ended_one_is_refused_rather_than_reopened() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.api import proctoring as api
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    ps.outcome = OUTCOME_COMPLETED
                    await s.flush()
                    with pytest.raises(HTTPException) as caught:
                        await api.create_session(
                            fx.link_id, _create_body(), user=_user(fx), session=s
                        )
                    assert caught.value.status_code == 409
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_config_route_serves_the_same_numbers_the_server_enforces() -> None:
    from app.api import proctoring as api

    out = await api.get_client_config(_user(_Fx()))
    assert out.max_warnings == CONFIG.max_warnings
    assert out.config["face_distance_threshold"] == CONFIG.face_distance_threshold
    assert out.audio_analysis_available == CONFIG.audio_analysis_available


@pytest.mark.asyncio
async def test_a_descriptor_of_the_wrong_width_is_refused_at_the_schema() -> None:
    """The database CHECK refuses it too, and this is the earlier of the two:
    a vector from another network is not a face-api.js descriptor."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _create_body(face_descriptor=[0.1] * 64)


# ══════════════════════════════════════════════════════════════════════════
# AUDIO (section 3.4)
# ══════════════════════════════════════════════════════════════════════════


async def _never_called(chunk, content_type, config):  # noqa: ANN001
    raise AssertionError("the analysis service must not be called")


def _speakers(count: int):
    async def _post(chunk, content_type, config):  # noqa: ANN001
        assert isinstance(chunk, bytes)
        return count

    return _post


@pytest.mark.asyncio
async def test_with_no_analysis_service_the_chunk_is_never_sent_and_the_gap_is_noted() -> None:
    """A deployment without diarization must report "not available" rather
    than a clean audio section it never checked."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        if CONFIG.audio_analysis_available:
            pytest.skip("this deployment has an analysis service configured")
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    now = datetime.now(timezone.utc)
                    out = await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"webm-bytes", "audio/webm",
                        now=now, post=_never_called, enqueue=fx.enqueue,
                    )
                    assert out.analysed is False
                    assert out.status == proctoring_audio.STATUS_UNAVAILABLE
                    rows = await _events(s, fx)
                    assert [r.event_type for r in rows] == ["SESSION_QUALITY_DEGRADED"]
                    from app.services.proctoring import phrasing

                    assert rows[0].metadata_json["note"] == phrasing.AUDIO_UNAVAILABLE_NOTE

                    # ...and only once per session, however many chunks arrive.
                    await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"more", "audio/webm",
                        now=now + timedelta(seconds=15), post=_never_called,
                        enqueue=fx.enqueue,
                    )
                    assert len(await _events(s, fx)) == 1
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_chunk_with_a_second_voice_is_not_enough(analysis_configured) -> None:
    """Section 4.2: two consecutive chunks, which is thirty seconds of
    evidence. One chunk is a cough in the corridor."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    out = await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"bytes", "audio/webm",
                        now=datetime.now(timezone.utc), post=_speakers(2),
                        enqueue=fx.enqueue,
                    )
                    assert out.analysed is True
                    assert out.warning is None
                    assert out.warnings_used == 0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_consecutive_chunks_with_a_second_voice_take_a_warning(
    analysis_configured,
) -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    out = None
                    for index in range(CONFIG.second_voice_consecutive_chunks):
                        out = await proctoring_audio.analyse_chunk(
                            s, ps, POLICY_CONTINUE_AND_NOTE, b"bytes", "audio/webm",
                            now=now + timedelta(seconds=15 * index),
                            post=_speakers(2), enqueue=fx.enqueue,
                        )
                    assert out.warning is not None
                    assert out.warning.event_type == "SECOND_VOICE_DETECTED"
                    assert out.warnings_used == 1
                    kinds = [r.event_type for r in await _events(s, fx)]
                    assert "SECOND_VOICE_DETECTED" in kinds
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_single_speaker_chunk_resets_the_run(analysis_configured) -> None:
    """Consecutive means consecutive here too: one voice in between means the
    second voice was not sustained."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"a", "audio/webm",
                        now=now, post=_speakers(2), enqueue=fx.enqueue,
                    )
                    await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"b", "audio/webm",
                        now=now + timedelta(seconds=15), post=_speakers(1),
                        enqueue=fx.enqueue,
                    )
                    out = await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"c", "audio/webm",
                        now=now + timedelta(seconds=30), post=_speakers(2),
                        enqueue=fx.enqueue,
                    )
                    assert out.warning is None
                    assert out.warnings_used == 0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_analysis_failure_is_recorded_rather_than_swallowed(
    analysis_configured,
) -> None:
    """A service that timed out has not established that nothing was heard."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope
        from app.services.proctoring import phrasing

        async def _broken(chunk, content_type, config):  # noqa: ANN001
            raise ValueError("no speaker_count in the answer")

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    out = await proctoring_audio.analyse_chunk(
                        s, ps, POLICY_CONTINUE_AND_NOTE, b"bytes", "audio/webm",
                        now=datetime.now(timezone.utc), post=_broken, enqueue=fx.enqueue,
                    )
                    assert out.status == proctoring_audio.STATUS_FAILED
                    rows = await _events(s, fx)
                    assert rows[0].metadata_json["note"] == phrasing.AUDIO_FAILED_NOTE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
# BEHAVIOUR RECORDING (section 4.5)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recording_behaviour_builds_the_baseline_and_never_raises() -> None:
    """The signature the assessment API calls. A missing baseline is the
    ordinary state of the first answers, not an error, and this must not
    raise for it: the candidate is mid-assessment on a live request."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation

        offsets = [i * 200 for i in range(120)]
        answer = AnswerBehaviourIn(keydown_offsets_ms=offsets, focus_ms=offsets[-1])
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    for _ in range(CONFIG.baseline_answers):
                        written = await behaviour.record_answer_behaviour(
                            s, ps, conversation, None, answer, len(offsets)
                        )
                        assert written == []
                    assert behaviour.baseline_established(ps.behaviour_profile_json, CONFIG)
                    assert ps.behaviour_profile_json["answers"] == CONFIG.baseline_answers
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_notable_answer_writes_a_path_c_event_against_its_question() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation
        from app.services.proctoring import catalog

        slow = [i * 400 for i in range(200)]
        baseline = AnswerBehaviourIn(keydown_offsets_ms=slow, focus_ms=slow[-1])
        keys = (CONFIG.fast_entry_sustained_seconds * 1000 // 10) + 200
        fast_offsets = [i * 10 for i in range(keys)]
        fast = AnswerBehaviourIn(
            keydown_offsets_ms=fast_offsets, focus_ms=fast_offsets[-1]
        )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    for _ in range(CONFIG.baseline_answers):
                        await behaviour.record_answer_behaviour(
                            s, ps, conversation, None, baseline, len(slow)
                        )
                    written = await behaviour.record_answer_behaviour(
                        s, ps, conversation, None, fast, keys
                    )
                    assert "FAST_TEXT_ENTRY" in written
                    rows = await _events(s, fx)
                    assert rows
                    for row in rows:
                        assert row.path == catalog.PATH_C
                        assert row.warning_issued is False
                        assert row.warning_number is None
                        # Aggregates only: no list of offsets survives.
                        for value in row.metadata_json.values():
                            assert not isinstance(value, (list, dict))
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_behaviour_never_moves_the_warning_counter() -> None:
    """Section 4.3: everything about how somebody typed is logged only."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation

        length = CONFIG.low_ratio_min_length * 4
        pasted = AnswerBehaviourIn(
            keydown_offsets_ms=[i * 150 for i in range(20)], focus_ms=3_000
        )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    written = await behaviour.record_answer_behaviour(
                        s, ps, conversation, None, pasted, length
                    )
                    assert "LOW_TYPED_RATIO" in written
                    assert ps.warnings_used == 0
                    assert await state.warnings_used(fx.ps_id) == 0
                    assert ps.outcome == OUTCOME_ACTIVE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
