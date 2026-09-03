"""No frame, image or audio buffer is ever persisted (P1, sections 5 and 10).

    P1: "No video is ever stored. No recordings, no frames, no snapshots, no
     images, not in S3, not in the database, not in temp storage, not in logs."

    Section 10: "add an automated test that fails if any image/video write
     path exists in the proctoring module".

WHY THIS IS A SWEEP AND NOT A BEHAVIOURAL TEST
-----------------------------------------------
A behavioural test proves that today's code path did not write a file. It
says nothing about the path somebody adds next month for a debugging session
and forgets to remove, which is exactly how a no-storage promise is broken.
So this reads the SOURCE: every module under `services/proctoring/` and
`api/proctoring.py`, by AST, for any call that could put bytes anywhere they
would survive the request.

The second half checks the schema, because a column is a storage path that
needs no code: a `frame_json` on `proctoring_events` would be filled by the
first client that sent one.

WHAT IS DELIBERATELY ALLOWED. `upload.read()` into memory and an httpx POST of
those bytes to the analysis service. That is the one media path the
specification permits, and it is bounded: read, post, delete. What is banned
is every way of making it OUTLIVE the request.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "app" / "services" / "proctoring"
API = BACKEND / "app" / "api" / "proctoring.py"
MODELS = BACKEND / "app" / "models" / "proctoring.py"


def _sources() -> list[pathlib.Path]:
    paths = sorted(PACKAGE.glob("*.py")) + [API]
    return [p for p in paths if p.exists()]


#: Module names that persist bytes. An import of any of these inside the
#: proctoring package is a storage path, whatever it is called at the call
#: site, so the import itself is what is refused.
FORBIDDEN_IMPORTS = frozenset(
    {
        "tempfile",
        "shutil",
        "boto3",
        "app.services.object_storage",
        "app.services.document_storage",
        "app.services.resume_storage",
        "app.services.cloudinary_storage",
    }
)

#: Attribute calls that write. Matched on the ATTRIBUTE name, so any object
#: exposing one is caught regardless of what it was assigned to.
FORBIDDEN_CALLS = frozenset(
    {
        "write_bytes",
        "write_text",
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
        "mkdtemp",
        "put_object",
        "upload_file",
        "upload_fileobj",
        "copyfileobj",
        "imwrite",
        "imsave",
    }
)

#: Column names that would hold media. Checked as substrings of a model's
#: mapped attribute names.
MEDIA_COLUMN_WORDS = (
    "image", "frame", "photo", "picture", "snapshot", "thumbnail",
    "audio", "video", "recording", "clip", "media", "blob", "screenshot",
)


def test_the_sweep_actually_reads_the_proctoring_source() -> None:
    """A sweep over an empty list passes forever, and this repository has
    shipped that: six secret-hygiene assertions reported SKIPPED for a whole
    phase after the file they read was deleted."""
    sources = _sources()
    assert len(sources) >= 9, [p.name for p in sources]
    assert API in sources
    assert sum(len(p.read_text(encoding="utf-8")) for p in sources) > 40_000


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_proctoring_module_imports_a_storage_library(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [
        name for name in imported
        if name in FORBIDDEN_IMPORTS or name.split(".")[0] in FORBIDDEN_IMPORTS
    ]
    assert not offenders, (
        f"{path.name} imports a storage library. No frame, image or audio "
        f"buffer may outlive the request that carried it: {offenders}"
    )


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_proctoring_module_calls_a_write_path(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name in FORBIDDEN_CALLS:
            offenders.append(f"{path.name}:{node.lineno} {name}()")
    assert not offenders, offenders


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_proctoring_module_opens_a_file_for_writing(path: pathlib.Path) -> None:
    """`open(..., "wb")` is the shortest route from a chunk to a file on disk,
    and it needs no import to notice."""
    offenders: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "open":
            continue
        modes = [
            argument.value
            for argument in list(node.args[1:]) + [kw.value for kw in node.keywords]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        ]
        if any(letter in mode for mode in modes for letter in ("w", "a", "x", "+")):
            offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def test_the_detector_would_catch_a_real_write_path() -> None:
    """A guard on the guard. A detector that matched nothing would make every
    assertion above pass on a module that saved every frame to disk."""
    tree = ast.parse(
        'import tempfile\n'
        'def save(chunk):\n'
        '    with open("/tmp/x.webm", "wb") as handle:\n'
        '        handle.write(chunk)\n'
    )
    imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
    assert set(imports) & FORBIDDEN_IMPORTS
    writes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
        and any(
            isinstance(a, ast.Constant) and "w" in str(a.value) for a in node.args[1:]
        )
    ]
    assert writes


def test_no_proctoring_column_is_shaped_like_media() -> None:
    """A column needs no code to become a storage path: the first client to
    send a frame would fill it. `face_descriptor_baseline` is deliberately
    NOT caught by this, and is not media: it is a 128-float vector that
    cannot be inverted into a photograph."""
    from app.models import proctoring as models

    offenders: list[str] = []
    for name in dir(models):
        model = getattr(models, name)
        if not (isinstance(model, type) and hasattr(model, "__tablename__")):
            continue
        for column in model.__table__.columns:
            lowered = column.name.lower()
            for word in MEDIA_COLUMN_WORDS:
                if word in lowered:
                    offenders.append(f"{model.__tablename__}.{column.name}")
    assert not offenders, (
        "a proctoring column is shaped like media storage: " + ", ".join(offenders)
    )


def test_the_column_detector_would_catch_one() -> None:
    assert any(word in "frame_jpeg" for word in MEDIA_COLUMN_WORDS)
    assert any(word in "audio_chunk_url" for word in MEDIA_COLUMN_WORDS)
    assert not any(word in "face_descriptor_baseline" for word in MEDIA_COLUMN_WORDS)


def test_the_descriptor_is_documented_as_not_an_image() -> None:
    """Section 10 requires the non-reversibility to be documented, because
    "we store a face vector" reads as "we store a face" to everybody who has
    not been told otherwise."""
    text = MODELS.read_text(encoding="utf-8")
    assert "NOT an image" in text or "not an image" in text
    assert "128" in text


def test_the_audio_path_reads_into_memory_and_deletes_the_buffer() -> None:
    """The one permitted media path, checked for its two halves: the bytes are
    never named by a write call (above), and every branch drops the reference
    rather than leaving it live in the response frame."""
    source = (PACKAGE / "audio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    deletes = [node for node in ast.walk(tree) if isinstance(node, ast.Delete)]
    assert len(deletes) >= 3, "every exit from analyse_chunk must drop the buffer"
    api_source = API.read_text(encoding="utf-8")
    assert "del data" in api_source, "the route must drop the chunk it read"


def test_no_proctoring_module_logs_a_payload() -> None:
    """P1 ends "not in logs". A log line that interpolated the chunk or an
    answer would persist media in a file nobody thinks of as storage."""
    offenders: list[str] = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith(("logger.", "log.")):
                continue
            for banned in ("chunk", "data", "answer_text", "content", "descriptor"):
                if f"%s" in stripped and banned in stripped.split("%s")[-1]:
                    offenders.append(f"{path.name}:{number}")
    assert not offenders, offenders
