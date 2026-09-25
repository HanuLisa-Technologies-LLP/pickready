"""A spoken answer: recorded, transcribed with the clock paused, and final.

Appendix B section 3. A prose turn may be answered by speaking, up to three
minutes, in English. The recording is stored through the sanctioned media
package, the turn's clock is PAUSED while Amazon Transcribe runs, and the
transcript is the answer: the candidate cannot edit it, and a typed body sent
beside it is ignored. The audio is deleted, HEAD-confirmed, once transcribed.
A failure is a STATE, never a fake transcript: the candidate reads that it
failed and types instead, with the clock held for a short, capped window.

Transcribe and object storage are doubled at the module attributes the route
and the task read, so no AWS call is made. Every state assertion reads a
second connection.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services.assessment_conversation import turns, voice_audio
from app.services.video import transcribe, voice
from app.workers import dispatch as dispatch_mod
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

TASK = "pickready.transcribe_voice_answer"
SPOKEN = "I reconciled the card captures every night and found a double settlement."
AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 2048


@pytest.fixture
async def candidate():
    if not await cw.reachable():
        pytest.skip("no database reachable")
    factory = cw.sessions()
    person = await create_candidate_session(factory)
    try:
        yield person
    finally:
        await close_candidate_session(factory, person)


class _FakeMedia:
    """The media package's two S3 verbs, in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def store(self, *, key: str, data: bytes, content_type: str) -> int:
        self.objects[key] = data
        return len(data)

    def delete_verified(self, key: str) -> bool:
        self.objects.pop(key, None)
        self.deleted.append(key)
        return True


@pytest.fixture
def media(monkeypatch) -> _FakeMedia:
    fake = _FakeMedia()
    monkeypatch.setattr(voice, "store_audio", fake.store)
    monkeypatch.setattr(voice, "delete_verified", fake.delete_verified)
    monkeypatch.setattr(transcribe, "is_available", lambda: True)
    return fake


def _transcribes_to(monkeypatch, text: str) -> None:
    def _run(**kwargs):
        return {"results": {"transcripts": [{"transcript": text}]}}

    monkeypatch.setattr(transcribe, "run_transcription", _run)


def _fails(monkeypatch) -> None:
    def _run(**kwargs):
        raise transcribe.TranscriptionError("Amazon Transcribe reported the job failed: test")

    monkeypatch.setattr(transcribe, "run_transcription", _run)


async def _run_task(voice_id: str) -> None:
    """The task body is synchronous and runs its own event loop, as it does in
    the worker; a thread keeps it off the test's loop."""
    from app.workers import tasks_voice

    await asyncio.to_thread(tasks_voice.transcribe_voice_answer, voice_id)


def _record(http, world) -> dict:
    begun = http.post(
        f"{cw.BASE}/conversations/{world.conversation}/voice/begin", json={"turn_seq": 1}
    )
    assert begun.status_code == 200, begun.text
    assert begun.json()["status"] == "recording"
    uploaded = http.post(
        f"{cw.BASE}/conversations/{world.conversation}/voice/{begun.json()['id']}/audio",
        files={"file": ("answer.webm", AUDIO, "audio/webm;codecs=opus")},
    )
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()


async def _pauses(factory, world) -> list:
    return await cw.committed(
        factory,
        "SELECT reason, ref_id, started_at, ended_at, expires_at FROM assessment_pauses "
        "WHERE conversation_id = :c ORDER BY started_at, created_at",
        c=str(world.conversation),
    )


async def test_a_spoken_answer_is_transcribed_with_the_clock_paused_and_is_final(
    candidate, monkeypatch, media
) -> None:
    cw.quiet_models(monkeypatch)
    _transcribes_to(monkeypatch, SPOKEN)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            shown = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            assert shown.json()["voice_input_available"] is True
            recorded = _record(http, world)
            voice_id = recorded["id"]
            assert recorded["status"] == "uploaded"
            assert recorded["transcript"] is None

            waiting = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            assert waiting.json()["turn"]["paused"] is True
            assert waiting.json()["turn"]["pause_reason"] == "transcription"
            early = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 1, "answer": "typed while it transcribes"},
            )
            assert early.status_code == 409
            assert early.json()["detail"] == turns.VOICE_NOT_READY_DETAIL

        assert dispatch_mod.recorded_names().count(TASK) == 1
        [key] = list(media.objects)
        assert key == f"voice-answers/{world.conversation}/{voice_id}.webm"
        open_pauses = await _pauses(factory, world)
        assert [(p.reason, str(p.ref_id), p.ended_at) for p in open_pauses] == [
            ("transcription", voice_id, None)
        ]
        assert open_pauses[0].expires_at is not None, "a transcription pause must be capped"

        await _run_task(voice_id)

        rows = await cw.committed(
            factory,
            "SELECT status, transcript_text, audio_deleted_at, s3_key FROM voice_answers "
            "WHERE id = :v",
            v=voice_id,
        )
        assert rows[0].status == "transcribed"
        assert rows[0].transcript_text == SPOKEN
        assert rows[0].audio_deleted_at is not None
        assert key in media.deleted and key not in media.objects
        assert all(p.ended_at is not None for p in await _pauses(factory, world))

        with cw.client(candidate) as http:
            status = http.get(f"{cw.BASE}/conversations/{world.conversation}/voice/{voice_id}")
            assert status.json()["transcript"] == SPOKEN
            answered = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={
                    "turn_seq": 1,
                    "answer": "An edited version the candidate typed over the transcript.",
                    "voice_answer_id": voice_id,
                },
            )
            assert answered.status_code == 200, answered.text
            replayed = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 2, "answer": "", "voice_answer_id": voice_id},
            )
        assert replayed.status_code == 409, "one recording answered two questions"

        filed = [row.content for row in await cw.messages(factory, world) if row.speaker == "candidate"]
        assert filed == [SPOKEN], "the transcript is final; a typed body replaced it"
        records = await cw.committed(
            factory,
            "SELECT answer_json FROM assessment_answers WHERE conversation_id = :c",
            c=str(world.conversation),
        )
        assert records[0].answer_json["input"] == "voice"
        assert records[0].answer_json["voice_answer_id"] == voice_id
        consumed = await cw.committed(
            factory, "SELECT status, consumed_message_id FROM voice_answers WHERE id = :v", v=voice_id
        )
        assert consumed[0].status == "consumed"
        assert consumed[0].consumed_message_id is not None
    finally:
        await cw.cleanup(factory, world)


async def test_a_failed_transcription_holds_the_clock_briefly_then_the_answer_is_typed(
    candidate, monkeypatch, media
) -> None:
    cw.quiet_models(monkeypatch)
    _fails(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            voice_id = _record(http, world)["id"]
        await _run_task(voice_id)

        rows = await cw.committed(
            factory,
            "SELECT status, transcript_text, failure_reason, audio_deleted_at FROM voice_answers "
            "WHERE id = :v",
            v=voice_id,
        )
        assert rows[0].status == "failed"
        assert rows[0].transcript_text is None, "a failure wrote a transcript"
        assert "Transcribe" in rows[0].failure_reason
        assert rows[0].audio_deleted_at is not None, "the audio outlived a failed transcription"
        held = [p for p in await _pauses(factory, world) if p.ended_at is None]
        assert len(held) == 1 and held[0].expires_at is not None, (
            "the failure window is not a capped pause"
        )

        with cw.client(candidate) as http:
            status = http.get(f"{cw.BASE}/conversations/{world.conversation}/voice/{voice_id}")
            assert status.json()["status"] == "failed"
            assert status.json()["message"] == turns.VOICE_FAILED_DETAIL
            again = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/voice/begin", json={"turn_seq": 1}
            )
            assert again.status_code == 409
            acknowledged = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/voice/{voice_id}/acknowledge"
            )
            assert acknowledged.status_code == 200, acknowledged.text
            typed = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 1, "answer": cw.TEXT_ANSWER},
            )
            assert typed.status_code == 200, typed.text

        assert all(p.ended_at is not None for p in await _pauses(factory, world))
        filed = [row.content for row in await cw.messages(factory, world) if row.speaker == "candidate"]
        assert filed == [cw.TEXT_ANSWER]
    finally:
        await cw.cleanup(factory, world)


async def test_voice_is_offered_only_where_it_can_work(candidate, monkeypatch, media) -> None:
    """Not on a structured question, and not at all when Transcribe is off:
    the microphone is simply not offered, and the answer is typed."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        plan=(("mcq_single", cw.MCQ_SINGLE_PAYLOAD, "must_have"),) + tuple(cw.PROSE_ONLY[1:]),
    )
    try:
        with cw.client(candidate) as http:
            shown = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            on_mcq = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/voice/begin", json={"turn_seq": 1}
            )
        assert shown.json()["voice_input_available"] is False
        assert on_mcq.status_code == 409

        monkeypatch.setattr(transcribe, "is_available", lambda: False)
        await cw.set_conversation(factory, world, next_question_index=1, turn_seq=2)
        with cw.client(candidate) as http:
            shown = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            refused = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/voice/begin", json={"turn_seq": 2}
            )
        assert shown.json()["voice_input_available"] is False
        assert refused.status_code == 409
        rows = await cw.committed(
            factory, "SELECT id FROM voice_answers WHERE conversation_id = :c",
            c=str(world.conversation),
        )
        assert rows == []
    finally:
        await cw.cleanup(factory, world)


async def test_an_oversized_or_unreadable_recording_is_refused_not_truncated(
    candidate, monkeypatch, media
) -> None:
    cw.quiet_models(monkeypatch)
    monkeypatch.setattr(cw.get_settings(), "assessment_voice_max_bytes", 1024)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            begun = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/voice/begin", json={"turn_seq": 1}
            ).json()
            url = f"{cw.BASE}/conversations/{world.conversation}/voice/{begun['id']}/audio"
            too_long = http.post(url, files={"file": ("a.webm", AUDIO, "audio/webm")})
            wrong_kind = http.post(url, files={"file": ("a.flac", b"fLaC", "audio/flac")})
        assert too_long.status_code == 422
        assert too_long.json()["detail"] == voice.TOO_LARGE_DETAIL
        assert wrong_kind.status_code == 422
        assert media.objects == {}
        rows = await cw.committed(
            factory, "SELECT status, s3_key FROM voice_answers WHERE id = :v", v=begun["id"]
        )
        assert rows[0].status == "recording" and rows[0].s3_key is None
    finally:
        await cw.cleanup(factory, world)


async def test_a_transcription_that_never_reports_back_is_failed_on_read(
    candidate, monkeypatch, media
) -> None:
    """A lost task must not leave a candidate waiting for ever. The pause is
    capped by construction, and the row is failed on read past the budget so
    the candidate is told to type."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            voice_id = _record(http, world)["id"]
        stale = datetime.now(timezone.utc).timestamp() - turns.transcription_budget_seconds() - 5
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(
                        text("UPDATE voice_answers SET uploaded_at = to_timestamp(:t) WHERE id = :v"),
                        {"t": stale, "v": voice_id},
                    )
        with cw.client(candidate) as http:
            status = http.get(f"{cw.BASE}/conversations/{world.conversation}/voice/{voice_id}")
        assert status.json()["status"] == "failed"
        rows = await cw.committed(
            factory, "SELECT status, failure_reason FROM voice_answers WHERE id = :v", v=voice_id
        )
        assert rows[0].status == "failed"
        assert "time budget" in rows[0].failure_reason
    finally:
        await cw.cleanup(factory, world)


# ── The audio goes, and a lost task is repaired ──────────────────────────────


async def _repair(factory) -> voice_audio.RepairResult:
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await voice_audio.repair_pending_audio(
                    s, now=datetime.now(timezone.utc)
                )


async def test_an_unconfirmed_audio_delete_is_counted_and_retried_until_it_is_gone(
    candidate, monkeypatch, media
) -> None:
    """Store transcript text only. A delete the HEAD could not confirm is
    COUNTED, the keys stay findable for a purge that must delete objects before
    rows, and the hourly repair pass deletes them; nothing gives up."""
    cw.quiet_models(monkeypatch)
    _transcribes_to(monkeypatch, SPOKEN)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            voice_id = _record(http, world)["id"]
        monkeypatch.setattr(voice, "delete_verified", lambda key: False)
        await _run_task(voice_id)

        rows = await cw.committed(
            factory,
            "SELECT status, audio_deleted_at, audio_delete_failures FROM voice_answers "
            "WHERE id = :v",
            v=voice_id,
        )
        assert rows[0].status == "transcribed", "a cleanup failure cost the transcript"
        assert rows[0].audio_deleted_at is None
        assert rows[0].audio_delete_failures == 1

        async with factory() as s:
            async with superadmin_scope(s):
                keys = await voice_audio.undeleted_audio_keys(s, [world.conversation])
        assert sorted(keys) == sorted(
            [
                f"voice-answers/{world.conversation}/{voice_id}.webm",
                f"voice-answers/{world.conversation}/{voice_id}.transcript.json",
            ]
        )

        monkeypatch.setattr(voice, "delete_verified", media.delete_verified)
        result = await _repair(factory)
        assert result.deleted >= 1
        rows = await cw.committed(
            factory,
            "SELECT audio_deleted_at, audio_delete_failures FROM voice_answers WHERE id = :v",
            v=voice_id,
        )
        assert rows[0].audio_deleted_at is not None
        assert f"voice-answers/{world.conversation}/{voice_id}.webm" not in media.objects
        async with factory() as s:
            async with superadmin_scope(s):
                assert await voice_audio.undeleted_audio_keys(s, [world.conversation]) == []
    finally:
        await cw.cleanup(factory, world)


async def test_a_lost_transcription_is_failed_and_its_audio_deleted_by_the_repair_pass(
    candidate, monkeypatch, media
) -> None:
    """Nobody may be reading the row again: the candidate's turn has moved on.
    The repair pass fails it by the same rule the routes apply on read, and
    deletes the audio; a transcription still inside its budget is left alone,
    because the task may be running this minute."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            voice_id = _record(http, world)["id"]

        await _repair(factory)
        fresh = await cw.committed(
            factory, "SELECT status, audio_deleted_at FROM voice_answers WHERE id = :v", v=voice_id
        )
        assert fresh[0].status == "uploaded", "a transcription in its budget was failed"
        assert fresh[0].audio_deleted_at is None

        stale = datetime.now(timezone.utc).timestamp() - turns.transcription_budget_seconds() - 5
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(
                        text("UPDATE voice_answers SET uploaded_at = to_timestamp(:t) WHERE id = :v"),
                        {"t": stale, "v": voice_id},
                    )
        result = await _repair(factory)
        assert result.failed_stale >= 1
        rows = await cw.committed(
            factory,
            "SELECT status, failure_reason, audio_deleted_at FROM voice_answers WHERE id = :v",
            v=voice_id,
        )
        assert rows[0].status == "failed"
        assert "time budget" in rows[0].failure_reason
        assert rows[0].audio_deleted_at is not None
    finally:
        await cw.cleanup(factory, world)


async def test_a_transcript_arriving_after_the_row_was_failed_does_not_replace_it(
    candidate, monkeypatch, media
) -> None:
    """The candidate was told to type once the row failed on read. A Transcribe
    job that finishes after that must not turn the row back into an answer they
    were told not to rely on; its audio still goes."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            voice_id = _record(http, world)["id"]

        def _slow_run(**kwargs):
            async def _fail_meanwhile() -> None:
                meanwhile = cw.sessions()
                async with meanwhile() as s:
                    async with s.begin():
                        async with superadmin_scope(s):
                            await s.execute(
                                text(
                                    "UPDATE voice_answers SET status = 'failed', "
                                    "failure_reason = 'failed on read', failed_at = now() "
                                    "WHERE id = :v"
                                ),
                                {"v": voice_id},
                            )

            asyncio.run(_fail_meanwhile())
            return {"results": {"transcripts": [{"transcript": SPOKEN}]}}

        monkeypatch.setattr(transcribe, "run_transcription", _slow_run)
        await _run_task(voice_id)

        rows = await cw.committed(
            factory,
            "SELECT status, transcript_text, audio_deleted_at FROM voice_answers WHERE id = :v",
            v=voice_id,
        )
        assert rows[0].status == "failed"
        assert rows[0].transcript_text is None
        assert rows[0].audio_deleted_at is not None
    finally:
        await cw.cleanup(factory, world)
