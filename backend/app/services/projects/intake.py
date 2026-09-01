"""Project intake: validation and TEMPORARY staging of untrusted uploads.

The staging prefix is `project-intake/`, deliberately separate from the
durable `resumes/` and compliance prefixes: everything under it is transient
by contract and is deleted by the pipeline once derived evidence persists (an
object-store lifecycle rule on the prefix is the backstop for anything a crash
orphans). Nothing durable may ever reference a `project-intake/` key except
`candidate_projects.intake_objects_json`, whose whole job is to track what
still awaits deletion.

Validation raises HTTPException with candidate-safe copy, the same contract
`resume_storage` follows, and never names a storage vendor.
"""
from __future__ import annotations

import hashlib
import os
import re

from fastapi import HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.services import object_storage
from app.services.projects.formats import classify
from app.services.projects.limits import ProjectLimits

INTAKE_PREFIX = "project-intake"

_WORD_PATTERN = re.compile(r"\b[\w'-]+\b")

#: Extensions whose magic bytes are known and therefore CHECKED. A mismatch on
#: an archive is refused (we extract archives); a mismatch elsewhere degrades
#: in the parser, which is already total.
_SIGNATURES: dict[str, bytes] = {
    ".zip": b"PK",
    ".docx": b"PK",
    ".xlsx": b"PK",
    ".pdf": b"%PDF",
}


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def count_words(text: str) -> int:
    return len(_WORD_PATTERN.findall(text or ""))


def validate_description(description: str, limits: ProjectLimits) -> str:
    cleaned = (description or "").strip()
    if not cleaned:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Describe the project in a few sentences.",
        )
    words = count_words(cleaned)
    if words > limits.description_max_words:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The project description must be {limits.description_max_words} "
            f"words or fewer. Yours is {words} words.",
        )
    return cleaned


def validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Give the project a name."
        )
    return cleaned[:160]


def _normalise_filename(filename: str | None) -> str:
    name = os.path.basename((filename or "").strip())
    if not name or len(name) > 255:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "One of the selected files has an invalid filename.",
        )
    return name


async def read_validated_files(
    files: list[UploadFile], limits: ProjectLimits
) -> list[tuple[str, str, bytes]]:
    """Read every upload within the ceilings. Returns (filename, content_type,
    bytes) triples. Unsupported FORMATS are accepted and recorded downstream;
    only size, count and signature violations are refused here."""
    if len(files) > limits.max_files:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A project can include at most {limits.max_files} files.",
        )
    total = 0
    out: list[tuple[str, str, bytes]] = []
    for upload in files:
        filename = _normalise_filename(upload.filename)
        data = await upload.read(limits.max_file_bytes + 1)
        if not data:
            raise _error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"The file {filename} is empty.",
            )
        if len(data) > limits.max_file_bytes:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"The file {filename} is larger than the per-file limit of "
                f"{limits.max_file_bytes // (1024 * 1024)} MB.",
            )
        total += len(data)
        if total > limits.max_total_bytes:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "The project upload exceeds the total size limit of "
                f"{limits.max_total_bytes // (1024 * 1024)} MB.",
            )
        extension = os.path.splitext(filename.lower())[1]
        signature = _SIGNATURES.get(extension)
        if signature and not data.startswith(signature):
            raise _error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"The file {filename} does not match its declared format.",
            )
        content_type = (upload.content_type or "application/octet-stream").split(
            ";", 1
        )[0].strip()
        out.append((filename, content_type, data))
    return out


def file_metadata(
    validated: list[tuple[str, str, bytes]]
) -> list[dict[str, object]]:
    """The metadata persisted on the project row: never content, never a key."""
    rows: list[dict[str, object]] = []
    for filename, content_type, data in validated:
        cls = classify(filename)
        rows.append(
            {
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(data),
                "family": cls.family,
                "label": cls.label,
                "supported": cls.supported,
            }
        )
    return rows


def _stage_one(key: str, data: bytes, content_type: str, filename: str) -> str:
    stored = object_storage.put_if_absent(
        key=key,
        data=data,
        content_type=content_type,
        metadata={"original_filename": filename, "lifecycle": "temporary"},
    )
    return stored.key


async def stage_intake(
    project_id: str, validated: list[tuple[str, str, bytes]]
) -> list[dict[str, str]]:
    """Store the originals TEMPORARILY and return [{key, filename}] for the
    project row's deletion ledger. Keys are content-addressed inside the
    project's own prefix, so a retried upload resolves to the same object."""
    staged: list[dict[str, str]] = []
    try:
        for index, (filename, content_type, data) in enumerate(validated):
            digest = hashlib.sha256(data).hexdigest()[:32]
            key = f"{INTAKE_PREFIX}/{project_id}/{index:03d}-{digest}"
            await run_in_threadpool(_stage_one, key, data, content_type, filename)
            staged.append({"key": key, "filename": filename})
    except object_storage.ObjectStorageNotConfigured as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Project storage is not configured. Please try again shortly.",
        ) from exc
    except object_storage.ObjectStorageError as exc:
        # Compensate what was already staged: the request failed, nothing may
        # linger. Best effort by design; the lifecycle rule is the backstop.
        for row in staged:
            await run_in_threadpool(object_storage.delete, row["key"])
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We could not securely stage your project files. Nothing was "
            "submitted; please retry.",
        ) from exc
    return staged
