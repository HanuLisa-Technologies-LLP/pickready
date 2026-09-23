"""The proctoring module stores no media, and the module that does is bounded.

P1 WAS REVERSED ON 2026-09-22, AND THIS FILE IS WHAT REPLACED IT
------------------------------------------------------------------
The principle this file used to encode read:

    P1: "No video is ever stored. No recordings, no frames, no snapshots, no
     images, not in S3, not in the database, not in temp storage, not in
     logs."

The product owner reversed it, verbatim: "Media storage is required. The
assessment video must be compressed and stored securely in S3, linked to the
candidate assessment." The same ruling RE-AFFIRMED P4 ("Proctoring remains
mandatory"), so nothing here softens the gate and there is no enable flag to
test for.

The file keeps its name because the sweep that gave it that name is still
true and still needed. What changed is WHERE media may live, not whether the
proctoring package may write it:

  * `services/proctoring/` still persists NO media. It classifies detections,
    counts warnings and phrases a report. The one media path it touches is
    still the 15-second audio chunk read into memory, posted to the analysis
    service and deleted, and every way of making that buffer outlive the
    request is still refused here.
  * Assessment media is written by ONE pipeline, `services/video/`, onto ONE
    table, `video_recordings`, through ONE transport that encrypts at rest.
    The second half of this file is what keeps that true: a media write
    anywhere else, a bucket name or object key crossing an API boundary, or a
    scorer reaching for the media package, all fail here.

WHY THIS IS A SWEEP AND NOT A BEHAVIOURAL TEST
-----------------------------------------------
A behavioural test proves that today's code path did not write a file. It says
nothing about the path somebody adds next month for a debugging session and
forgets to remove. So this reads the SOURCE, by AST.

The schema half stays for the same reason it was written: a column is a
storage path that needs no code, and a `frame_json` on `proctoring_events`
would be filled by the first client that sent one. The media columns live on
`video_recordings`, where a deletion lifecycle exists for them; a media column
on a proctoring table would have none.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
PACKAGE = APP / "services" / "proctoring"
API = APP / "api" / "proctoring.py"
MODELS = APP / "models" / "proctoring.py"

#: The ONE package that may persist assessment media, and the ONE transport it
#: may persist it through.
MEDIA_PACKAGE = "app.services.video"
MEDIA_TRANSPORT = "app.services.object_storage"


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
        # Added 2026-09-22 with the media pipeline. Proctoring must not reach
        # the media package either: a monitoring rule that could write, read
        # or delete a recording would put the two in one blast radius, and the
        # recording would stop being an assessment artifact and start being a
        # proctoring one.
        "app.services.video",
        "app.services.assessment_media_retention",
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


def _python_files() -> list[pathlib.Path]:
    return [p for p in sorted(APP.rglob("*.py")) if "__pycache__" not in p.parts]


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


# ── Half one: the proctoring module still stores nothing ─────────────────────


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
        f"{path.name} imports a storage library. Assessment media is stored by "
        f"`services/video` and by nothing else: {offenders}"
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
    send a frame would fill it. Media columns belong on `video_recordings`,
    where the retention and deletion lifecycle that governs them lives; one on
    a proctoring table would have no deleter and no retention clock.

    `face_descriptor_baseline` is deliberately NOT caught by this, and is not
    media: it is a 128-float vector that cannot be inverted into a photograph.
    """
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
    """The analysis chunk is still a transient. It goes to a service that
    counts speakers and comes back as a number; it is not the stored
    recording, and nothing may quietly make it one."""
    source = (PACKAGE / "audio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    deletes = [node for node in ast.walk(tree) if isinstance(node, ast.Delete)]
    assert len(deletes) >= 3, "every exit from analyse_chunk must drop the buffer"
    api_source = API.read_text(encoding="utf-8")
    assert "del data" in api_source, "the route must drop the chunk it read"


def test_no_proctoring_module_logs_a_payload() -> None:
    """A log line that interpolated the chunk or an answer would persist
    content in a file nobody thinks of as storage, with no retention clock on
    it and no way to delete it for one candidate."""
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


# ── Half two: media is stored in exactly one place ───────────────────────────

#: The modules that may reach the media transport directly. Every other kind
#: of stored object in this product already has its own storage module on this
#: list; assessment media's is `services/video/storage.py`.
PERMITTED_TRANSPORT_USERS = {
    "services/object_storage.py",
    "services/video/storage.py",
    "services/document_storage.py",
    "services/resume_storage.py",
    "services/assessment_media_retention.py",
    "services/assessment_video_access.py",
    "api/videos.py",
}


def test_assessment_media_is_written_through_one_module() -> None:
    """A `put_object` outside `services/object_storage` is a second way bytes
    reach the bucket, and the second way is the one that forgets the
    encryption argument. This is the same shape as the transport rule the
    resume and document paths already follow, extended to cover the media the
    2026-09-22 ruling introduced."""
    offenders: list[str] = []
    for path in _python_files():
        relative = path.relative_to(APP).as_posix()
        if relative in PERMITTED_TRANSPORT_USERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"put_object", "upload_file", "upload_fileobj"}
            ):
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "assessment media reaches object storage through one module. These "
        f"write to a bucket directly: {offenders}"
    )


def test_every_media_put_is_server_side_encrypted() -> None:
    """Encryption at rest is an ARGUMENT, so it is a thing somebody can leave
    off. Read the two modules that may call `put_object` and assert that every
    such call passes `ServerSideEncryption`."""
    for relative in ("services/object_storage.py", "services/video/storage.py"):
        tree = ast.parse((APP / relative).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "put_object"
        ]
        assert calls, f"{relative} has no put_object; this sweep went vacuous"
        for call in calls:
            names = {kw.arg for kw in call.keywords}
            assert "ServerSideEncryption" in names, (
                f"{relative}:{call.lineno} stores an object without "
                "server-side encryption"
            )


def test_no_response_schema_carries_a_bucket_name_or_an_object_key() -> None:
    """The delivery rule, swept over the schema package rather than checked at
    a route. A presigned URL is the one sanctioned way an object location
    leaves this backend: time limited, single purpose and useless once it
    expires. A raw key in a payload is none of those things."""
    import app.schemas.videos as videos_schemas

    from pydantic import BaseModel

    offenders: list[str] = []
    for name in dir(videos_schemas):
        model = getattr(videos_schemas, name)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        for field in model.model_fields:
            lowered = field.lower()
            if "bucket" in lowered or lowered.endswith("_key") or lowered == "key":
                offenders.append(f"{name}.{field}")
    assert not offenders, (
        "a video response schema exposes an object location: " + ", ".join(offenders)
    )


def test_the_key_detector_would_catch_one() -> None:
    """A guard on the guard above: the field names it is looking for are the
    names the real columns carry."""
    for field in ("s3_bucket", "s3_compressed_key", "s3_raw_key", "key"):
        lowered = field.lower()
        assert "bucket" in lowered or lowered.endswith("_key") or lowered == "key"


def test_the_media_package_is_not_reachable_from_a_scorer() -> None:
    """Storing media must not create an import path into anything that
    grades. `tests/test_dual_mode_assessment.py` owns the allowlist; this
    asserts the two surfaces that matter most, by name, because those are the
    files the rule exists about."""
    for relative in (
        "services/functional_assessment.py",
        "services/job_relevance.py",
    ):
        imports = _imports(APP / relative)
        assert not any(name.startswith(MEDIA_PACKAGE) for name in imports), relative
        assert not any(
            name.startswith("app.services.assessment_media_retention")
            for name in imports
        ), relative
