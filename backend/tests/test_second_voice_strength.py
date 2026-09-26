"""A second voice is flagged only when it is STRONG (master prompt, Phase 3).

    "Flag a strong SECOND voice only. The candidate's own voice while the mic
     is active is expected and not flagged."

The analysis service now reports how long each separated speaker spoke. A
chunk counts toward a second voice only when the SECOND-longest speaker spoke
for at least `second_voice_min_seconds`; the two-consecutive-chunks rule on
top of that is unchanged. These tests hold both halves: the pure rule, and the
pipeline refusing to warn on a weak second label however many chunks carry it.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app.models.proctoring import POLICY_CONTINUE_AND_NOTE
from app.services.proctoring import audio as proctoring_audio
from app.services.proctoring.audio import ChunkAnalysis
from app.services.proctoring.config import get_config

from tests.test_proctoring_pipeline import _Fx, _cleanup, _events, _factory_or_skip, _load, _seed

CONFIG = get_config()
STRONG = CONFIG.second_voice_min_seconds
WEAK = CONFIG.second_voice_min_seconds - 0.5


def _heard(*speaker_seconds: float) -> ChunkAnalysis:
    ordered = tuple(sorted(speaker_seconds, reverse=True))
    return ChunkAnalysis(
        speaker_count=len(ordered), speaker_seconds=ordered, speech_seconds=sum(ordered)
    )


# ── The pure rule ────────────────────────────────────────────────────────────


def test_one_speaker_is_never_a_second_voice_however_long_they_spoke() -> None:
    assert not proctoring_audio.strong_second_voice(_heard(15.0), CONFIG)
    assert not proctoring_audio.strong_second_voice(_heard(), CONFIG)


def test_a_second_speaker_at_the_minimum_is_strong_and_just_under_is_not() -> None:
    """Inclusive at the boundary, like every rule here. Mutation-checked:
    reading `speaker_seconds[0]` (the candidate) instead of `[1]` makes the
    weak case strong, and this fails."""
    assert proctoring_audio.strong_second_voice(_heard(10.0, STRONG), CONFIG)
    assert not proctoring_audio.strong_second_voice(_heard(10.0, WEAK), CONFIG)


def test_only_the_second_longest_speaker_is_measured() -> None:
    """Three labels where only a third is long is still one strong second
    voice; three where the second is short is none, because the third is
    shorter still."""
    assert proctoring_audio.strong_second_voice(_heard(9.0, STRONG + 1, 0.2), CONFIG)
    assert not proctoring_audio.strong_second_voice(_heard(9.0, WEAK, 0.2), CONFIG)


# ── The analysis service's answer ────────────────────────────────────────────


def test_the_answer_is_parsed_and_ordered_longest_first() -> None:
    parsed = proctoring_audio.parse_analysis(
        {"speaker_count": 2, "speaker_seconds": [2.0, 9.5], "speech_seconds": 11.0}
    )
    assert parsed == ChunkAnalysis(2, (9.5, 2.0), 11.0)


@pytest.mark.parametrize(
    "body",
    [
        # An analysis service older than this backend: no per-speaker figures.
        # That is a bad answer, never "one speaker".
        {"speaker_count": 2, "speech_seconds": 4.0},
        {"speaker_count": 2, "speaker_seconds": [4.0], "speech_seconds": 4.0},
        {"speaker_count": 1, "speaker_seconds": [-1.0], "speech_seconds": 4.0},
        {"speaker_count": 1, "speaker_seconds": [True], "speech_seconds": 4.0},
        {"speaker_count": "1", "speaker_seconds": [1.0], "speech_seconds": 4.0},
        {"speaker_count": 1, "speaker_seconds": [1.0]},
        [1, 2],
    ],
)
def test_an_answer_that_cannot_support_the_rule_is_refused(body) -> None:
    with pytest.raises(ValueError):
        proctoring_audio.parse_analysis(body)


# ── Through the pipeline ─────────────────────────────────────────────────────


@pytest.fixture
def analysis_configured(monkeypatch):
    configured = dataclasses.replace(CONFIG, analysis_service_url="http://analysis.invalid:8100")
    monkeypatch.setattr(proctoring_audio, "get_config", lambda: configured)
    return configured


def _poster(analysis: ChunkAnalysis):
    async def _post(chunk, content_type, config):  # noqa: ANN001
        return analysis

    return _post


async def _answering_by_voice(session, conversation_id, now, config):  # noqa: ANN001
    return True


@pytest.mark.asyncio
async def test_a_weak_second_label_never_warns_however_long_it_persists(
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
                    for index in range(CONFIG.second_voice_consecutive_chunks * 3):
                        out = await proctoring_audio.analyse_chunk(
                            s, ps, POLICY_CONTINUE_AND_NOTE, b"x", "audio/webm",
                            now=now + timedelta(seconds=15 * index),
                            post=_poster(_heard(12.0, WEAK)),
                            probe=_answering_by_voice, enqueue=fx.enqueue,
                        )
                        assert out.warning is None
                    assert "SECOND_VOICE_DETECTED" not in [e.event_type for e in await _events(s, fx)]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_strong_second_voice_in_consecutive_chunks_warns(analysis_configured) -> None:
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
                            s, ps, POLICY_CONTINUE_AND_NOTE, b"x", "audio/webm",
                            now=now + timedelta(seconds=15 * index),
                            post=_poster(_heard(10.0, STRONG)),
                            probe=_answering_by_voice, enqueue=fx.enqueue,
                        )
                    assert out.warning is not None
                    assert out.warning.event_type == "SECOND_VOICE_DETECTED"
                    voice = [e for e in await _events(s, fx) if e.event_type == "SECOND_VOICE_DETECTED"]
                    assert voice[0].metadata_json["second_speaker_seconds"] == round(STRONG, 1)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_weak_chunk_between_two_strong_ones_breaks_the_run(analysis_configured) -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        sequence = [_heard(10.0, STRONG), _heard(10.0, WEAK), _heard(10.0, STRONG)]
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    for index, heard in enumerate(sequence):
                        out = await proctoring_audio.analyse_chunk(
                            s, ps, POLICY_CONTINUE_AND_NOTE, b"x", "audio/webm",
                            now=now + timedelta(seconds=15 * index), post=_poster(heard),
                            probe=_answering_by_voice, enqueue=fx.enqueue,
                        )
                    assert out.warning is None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
