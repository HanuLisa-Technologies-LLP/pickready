"""Audio never touches a filesystem (proctoring spec sections 2, 3.4 and 10).

Two independent proofs, because each alone has a hole:

  DYNAMIC   Every path that could put bytes on disk is made to raise for the
            duration of a real /diarize request: `open` in any write mode,
            `os.open` with a write or create flag, every `tempfile`
            constructor, `Path.write_bytes` and `Path.write_text`, and the
            `shutil` copy helpers. The request must still answer 200. A test
            that only asserted "the decoder got a BytesIO" would pass a
            decoder that spooled the buffer to disk on its way through.

  STATIC    The service source is read and none of those names may appear in
            a request path at all, so a write that was added under a branch
            the dynamic test did not take is still refused. Reading a config
            file at model-load time is allowed and is the only `open` there is.
"""
from __future__ import annotations

import builtins
import io
import os
import pathlib
import re
import shutil
import tempfile

import pytest

from tests.conftest import FakeDecoder, FakePipeline, make_client, make_components, upload

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
_WRITE_MODES = re.compile(r"[wax+]")


def _refuse(name: str):
    def refuse(*args, **kwargs):
        raise AssertionError(f"{name} was called during a /diarize request; audio must never reach a filesystem")

    return refuse


@pytest.fixture
def no_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = builtins.open
    real_os_open = os.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if _WRITE_MODES.search(mode):
            raise AssertionError(f"open({file!r}, {mode!r}) during a /diarize request")
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            raise AssertionError(f"os.open({path!r}) with a write flag during a /diarize request")
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in (
        "TemporaryFile",
        "NamedTemporaryFile",
        "SpooledTemporaryFile",
        "TemporaryDirectory",
        "mkstemp",
        "mkdtemp",
        "mktemp",
    ):
        monkeypatch.setattr(tempfile, name, _refuse(f"tempfile.{name}"))
    monkeypatch.setattr(pathlib.Path, "write_bytes", _refuse("Path.write_bytes"))
    monkeypatch.setattr(pathlib.Path, "write_text", _refuse("Path.write_text"))
    for name in ("copyfileobj", "copyfile", "copy", "copy2", "move"):
        monkeypatch.setattr(shutil, name, _refuse(f"shutil.{name}"))


def test_a_diarize_request_completes_with_every_write_path_disabled(no_disk) -> None:
    pipeline = FakePipeline(speakers=["SPEAKER_00", "SPEAKER_01"], speech_seconds=12.0)
    decoder = FakeDecoder()
    payload = os.urandom(4096)

    with make_client(make_components(pipeline=pipeline), decoder=decoder) as client:
        response = client.post("/diarize", files=upload(payload))

    assert response.status_code == 200, response.text
    assert response.json() == {"speaker_count": 2, "speech_seconds": 12.0}
    assert decoder.received == [payload], "the decoder saw exactly the uploaded bytes, from memory"


def test_the_buffer_is_closed_before_the_response_is_returned() -> None:
    decoder = FakeDecoder()
    with make_client(make_components(pipeline=FakePipeline()), decoder=decoder) as client:
        assert client.post("/diarize", files=upload(b"chunk")).status_code == 200
    buffer = decoder.buffers[0]
    assert isinstance(buffer, io.BytesIO)
    assert buffer.closed, "diarize() must close and delete the BytesIO after decoding"
    with pytest.raises(ValueError):
        buffer.getvalue()


def test_the_buffer_is_closed_even_when_decoding_fails() -> None:
    decoder = FakeDecoder(fail=True)
    with make_client(make_components(pipeline=FakePipeline()), decoder=decoder) as client:
        assert client.post("/diarize", files=upload(b"chunk")).status_code == 422
    assert decoder.buffers[0].closed


def test_the_service_source_names_no_write_path() -> None:
    forbidden = (
        r"\btempfile\b",
        r"NamedTemporaryFile",
        r"SpooledTemporaryFile",
        r"\bmkstemp\b",
        r"\bmkdtemp\b",
        r"write_bytes",
        r"write_text",
        r"\bshutil\b",
        r"os\.open\(",
        r"open\([^)]*,\s*['\"][wax]",
        r"\.save\(",
        r"to_file",
    )
    offenders: list[str] = []
    for path in sorted(APP_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            for match in re.finditer(pattern, source):
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.name}:{line} {match.group(0)!r}")
    assert not offenders, "a write path is named in the service source:\n  " + "\n  ".join(offenders)


def test_the_only_open_in_the_service_reads_the_pipeline_config() -> None:
    """One `open`, read-only, at load time, on the pyannote config. Not on audio."""
    opens: list[str] = []
    for path in sorted(APP_DIR.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"\bopen\(", line):
                opens.append(f"{path.name}: {line.strip()}")
    assert opens == ['diarization.py: with open(config_path, encoding="utf-8") as handle:'], opens
