"""A fresher's own verification documents: validate, store, enumerate, erase.

WHY THIS EXISTS AT ALL
------------------------
The brief, verbatim: "Freshers: no employer BGV. Academic certificates and
address proof only." The product had a radio button on the profile form that
stored no file, so a fresher's half of background verification was a question
nobody could answer. This is the store.

WHAT IT DELIBERATELY DOES NOT DO
----------------------------------
It gates NOTHING. `bgv_workflow.derive_status` answers `not_required` for a
fresher and that path is untouched: a missing certificate must never block an
offer, because the brief gives a fresher no verification step that could
complete and inventing one would end a candidacy over paperwork the product
never asked for at the right time.

There is also no recruiter route. A candidate's degree certificate is a
document they uploaded once for a platform, not a file every customer who ever
looks at their profile may download, and the product has no consent record for
sharing one (`bgv_share_consents` covers the inquiry results and says nothing
about this). Building the download before the consent is how a document
obtained for one purpose becomes a file in twelve companies' folders. The
absence is the decision; the consent model is an owner call.

HOSTILE INPUT, AND THE THREE THINGS THAT FOLLOW
-------------------------------------------------
A candidate-supplied file is hostile input. Nothing here executes, unpacks or
parses a byte of it:

* the extension, the declared content type AND the magic bytes must all agree
  (`document_storage.assert_signature`, shared rather than copied, so a format
  cannot be accepted here and refused there);
* every ceiling is a `bgv_document_*` setting, never a literal in this module;
* the object key is CONTENT-ADDRESSED and NEVER crosses an API boundary. It
  exists so erasure can reach the bytes, and every response names the row id
  and the filename the candidate typed.

ERASURE IS THE WHOLE REASON `candidate_document_keys` IS SEPARATE
--------------------------------------------------------------------
Deleting the ROW deletes nothing in the object store: no foreign key reaches
an object, which is the same trap `context_chunks` and the video recordings
already taught this product. `candidate_document_keys` must be called BEFORE
the rows are erased, because after the cascade there is nothing left that
names the objects and an enumeration run afterwards returns an honest empty
tuple that a caller would read as a complete deletion of nothing.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.models.bgv_documents import (
    BGV_DOCUMENT_TYPES,
    DOCUMENT_TYPE_LABELS,
)
from app.services import document_storage, object_storage

#: The folder every one of these objects lives under. Separate from
#: `compliance/` and from resumes: a candidate's certificate and a customer's
#: GSTIN scan have different owners and different erasure paths, and one
#: prefix would make a bulk operation on either reach the other.
OBJECT_PREFIX = "bgv-documents"

#: What erasure records this object WAS. One kind, because a certificate and an
#: address proof are deleted identically and a distinction that changes no
#: behaviour is a distinction an operator has to decode for nothing.
KIND_BGV_DOCUMENT = "bgv_document"

#: Scans a candidate actually produces. The same three as a compliance record,
#: and DOCX is absent for the same reason: a certificate is an issued artefact,
#: never an editable draft.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".jpg", ".jpeg", ".png"})
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        # Some browsers send this for a file picked from a file manager.
        "application/octet-stream",
    }
)


def upload_limits_hint() -> str:
    """The limits a candidate reads, built from the settings that enforce them.

    Rendered rather than typed, so the sentence on the screen and the number in
    the check cannot drift: a hint that says 10 MB beside a ceiling of 5 is a
    refusal the candidate cannot understand.
    """
    megabytes = get_settings().bgv_document_max_bytes // (1024 * 1024)
    return f"PDF, JPG or PNG, up to {megabytes} MB."


@dataclass(frozen=True)
class StoredBGVDocument:
    """One stored document, as the row is about to record it."""

    id: uuid.UUID
    document_type: str
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


async def read_validated_upload(file: UploadFile) -> tuple[bytes, str, str]:
    """Read one upload into memory after strict format and size validation.

    Returns (bytes, filename, mime type). Raises an HTTPException carrying a
    sentence the candidate can act on; never a stack trace and never a
    storage-vendor name (claude.md, 2026-07-26).
    """
    settings = get_settings()
    filename = os.path.basename((file.filename or "").strip())
    if not filename or len(filename) > 255:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose a file with a valid name.",
        )
    extension = os.path.splitext(filename.lower())[1]
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Documents must be {upload_limits_hint()}",
        )
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The selected file type does not match a PDF, JPG or PNG document.",
        )
    ceiling = settings.bgv_document_max_bytes
    # Read ONE byte past the ceiling, so an oversized file is refused without
    # this process ever holding the whole of it.
    data = await file.read(ceiling + 1)
    if not data:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The selected file is empty."
        )
    if len(data) > ceiling:
        raise _error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Documents must be {ceiling // (1024 * 1024)} MB or smaller.",
        )
    # Shared with the compliance store rather than copied: the extension is
    # what a person typed and the magic bytes are what the file is.
    #
    # The CHECK is shared; its RESPONSE SHAPE is not. `document_storage` answers
    # with `{"message": ..., "retryable": ...}`, which is what the Provider's
    # compliance screen reads; every refusal on this router is a plain sentence.
    # One route family answering with two shapes is a client that has to guess.
    try:
        document_storage.assert_signature(data, extension)
    except HTTPException as exc:
        detail = exc.detail
        message = (
            detail.get("message") if isinstance(detail, dict) else str(detail)
        )
        raise _error(exc.status_code, str(message)) from exc
    return data, filename, document_storage.MIME_BY_EXTENSION[extension]


def _put(data: bytes, sha256: str, filename: str, mime_type: str) -> str:
    key = f"{OBJECT_PREFIX}/{sha256}"
    try:
        stored = object_storage.put_if_absent(
            key=key,
            data=data,
            content_type=mime_type,
            metadata={
                "original_filename": filename,
                "mime_type": mime_type,
                "sha256": sha256,
            },
        )
    except object_storage.ObjectStorageNotConfigured as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Document storage is not available right now. Nothing was saved; "
            "please try again shortly.",
        ) from exc
    except object_storage.ObjectStorageError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We could not securely store this document. Nothing was saved; "
            "please retry.",
        ) from exc
    return stored.key


async def store_document(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    document_type: str,
    file: UploadFile,
) -> StoredBGVDocument:
    """Validate, store and record one of a candidate's own documents.

    IDEMPOTENT ON CONTENT. The key is the sha256, so a double click or a retry
    after a lost response resolves to the object already stored, and
    `uq_candidate_bgv_document` resolves the ROW the same way: the existing row
    is returned rather than a second one filed. A candidate uploading the same
    certificate twice has uploaded it once.
    """
    if document_type not in BGV_DOCUMENT_TYPES:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose an academic certificate or an address proof.",
        )
    settings = get_settings()
    existing = (
        await session.execute(
            text(
                "SELECT count(*) FROM candidate_bgv_documents "
                "WHERE candidate_id = :cid AND document_type = :kind"
            ),
            {"cid": str(candidate_id), "kind": document_type},
        )
    ).scalar_one()
    if existing >= settings.bgv_documents_max_per_type:
        label = DOCUMENT_TYPE_LABELS[document_type].lower()
        raise _error(
            status.HTTP_409_CONFLICT,
            f"You have already added {settings.bgv_documents_max_per_type} "
            f"files under {label}. Remove one before adding another.",
        )

    data, filename, mime_type = await read_validated_upload(file)
    sha256 = hashlib.sha256(data).hexdigest()
    already = (
        (
            await session.execute(
                text(
                    "SELECT id, original_filename, mime_type, size_bytes, "
                    " uploaded_at FROM candidate_bgv_documents "
                    "WHERE candidate_id = :cid AND document_type = :kind "
                    "  AND sha256 = :sha"
                ),
                {
                    "cid": str(candidate_id),
                    "kind": document_type,
                    "sha": sha256,
                },
            )
        )
        .mappings()
        .first()
    )
    if already is not None:
        return StoredBGVDocument(
            id=uuid.UUID(str(already["id"])),
            document_type=document_type,
            original_filename=already["original_filename"],
            mime_type=already["mime_type"],
            size_bytes=already["size_bytes"],
            uploaded_at=already["uploaded_at"],
        )

    key = await run_in_threadpool(_put, data, sha256, filename, mime_type)
    now = datetime.now(timezone.utc)
    document_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO candidate_bgv_documents (id, candidate_id, "
            " document_type, object_key, original_filename, mime_type, "
            " size_bytes, sha256, uploaded_at, created_at) "
            "VALUES (:id, :cid, :kind, :key, :name, :mime, :size, :sha, "
            " :at, :at)"
        ),
        {
            "id": str(document_id),
            "cid": str(candidate_id),
            "kind": document_type,
            "key": key,
            "name": filename,
            "mime": mime_type,
            "size": len(data),
            "sha": sha256,
            "at": now,
        },
    )
    return StoredBGVDocument(
        id=document_id,
        document_type=document_type,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        uploaded_at=now,
    )


async def list_documents(
    session: AsyncSession, *, candidate_id: uuid.UUID
) -> list[StoredBGVDocument]:
    """This candidate's documents. NO object key crosses this boundary."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, document_type, original_filename, mime_type, "
                    " size_bytes, uploaded_at FROM candidate_bgv_documents "
                    "WHERE candidate_id = :cid "
                    "ORDER BY document_type, uploaded_at DESC, id"
                ),
                {"cid": str(candidate_id)},
            )
        )
        .mappings()
        .all()
    )
    return [
        StoredBGVDocument(
            id=uuid.UUID(str(row["id"])),
            document_type=row["document_type"],
            original_filename=row["original_filename"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            uploaded_at=row["uploaded_at"],
        )
        for row in rows
    ]


async def delete_document(
    session: AsyncSession, *, candidate_id: uuid.UUID, document_id: uuid.UUID
) -> bool:
    """Remove one document the candidate uploaded, bytes included.

    Scoped by candidate id IN THE STATEMENT, not checked beforehand: a delete
    that reads then writes is a delete somebody can race, and this runs in the
    bypass scope where a candidate id in the WHERE clause is the only boundary
    there is.

    THE ROW GOES FIRST AND THE BYTES SECOND, deliberately. The object is shared
    by content address, so another row may still reference it; the delete is
    attempted only when no row does.
    """
    key = (
        await session.execute(
            text(
                "DELETE FROM candidate_bgv_documents "
                "WHERE id = :id AND candidate_id = :cid RETURNING object_key"
            ),
            {"id": str(document_id), "cid": str(candidate_id)},
        )
    ).scalar()
    if key is None:
        return False
    await _drop_object_if_unreferenced(session, str(key))
    return True


class BGVDocumentNotDeleted(RuntimeError):
    """The row is gone and the bytes are still readable.

    `object_storage.delete` is best-effort and never raises, which is right on
    a rollback path and wrong here: a candidate's certificate reported as
    removed and still sitting in the store is the one outcome this module must
    never produce quietly. So the delete is followed by a HEAD, the same
    contract the project-intake pipeline holds itself to, and this is what an
    unconfirmed deletion looks like from the outside.
    """


async def _drop_object_if_unreferenced(session: AsyncSession, key: str) -> None:
    """Remove one object once no row names it, and CONFIRM it is gone.

    The key is a content address, so two candidates who uploaded byte-identical
    files share one object: erasing one person must not remove the other's
    document, and the reference check is what keeps that true.
    """
    still_referenced = (
        await session.execute(
            text(
                "SELECT 1 FROM candidate_bgv_documents WHERE object_key = :key "
                "LIMIT 1"
            ),
            {"key": key},
        )
    ).scalar()
    if still_referenced is not None:
        return
    await run_in_threadpool(object_storage.delete, key)
    # "The delete call returned" is the same class of non-evidence as "the
    # pipeline was green" (Project Evidence brief section 27). Ask.
    if await run_in_threadpool(object_storage.exists, key):
        raise BGVDocumentNotDeleted(
            f"object {key} is still readable after a delete was issued"
        )


# ── The erasure hook ─────────────────────────────────────────────────────────


async def candidate_document_keys(
    session: AsyncSession, candidate_id: uuid.UUID | str
) -> tuple[dict[str, str], ...]:
    """Every object key this candidate's BGV documents own.

    Shaped exactly like `erasure.candidate_object_keys` entries,
    `{"key": ..., "kind": ...}`, so the two concatenate without a translation
    step that could drop a field.

    CALL THIS BEFORE THE ROWS ARE ERASED. Nothing else in the database names
    these objects, so an enumeration run after the cascade returns an empty
    tuple and a caller acting on it reports a complete deletion of nothing.

    THE SESSION MUST BE IN A BYPASS SCOPE, for the reason the resume
    enumeration gives: these rows are tenant-free and a tenant-scoped read
    would answer with whatever subset it could see.
    """
    identifier = uuid.UUID(str(candidate_id))
    rows = (
        await session.execute(
            text(
                "SELECT object_key FROM candidate_bgv_documents "
                "WHERE candidate_id = :cid "
                "AND object_key IS NOT NULL AND btrim(object_key) <> ''"
            ),
            {"cid": str(identifier)},
        )
    ).all()
    return tuple(
        {"key": str(key), "kind": KIND_BGV_DOCUMENT} for (key,) in rows
    )


async def delete_candidate_documents(
    session: AsyncSession, candidate_id: uuid.UUID | str
) -> int:
    """Delete this candidate's BGV documents, rows and bytes. Returns the count.

    The rows are deleted first and the objects second, and an object still
    referenced by ANOTHER candidate's row is left alone: the key is a content
    address, so two people who uploaded byte-identical files share one object
    and erasing one person must not remove the other's document.

    An UNCONFIRMED deletion raises `BGVDocumentNotDeleted`. An erasure that
    reported success while the bytes stayed readable is exactly the failure
    `erasure.LegacyObjectNotDeletable` exists to make visible, and swallowing
    it here would reintroduce it one table over.
    """
    identifier = uuid.UUID(str(candidate_id))
    keys = [entry["key"] for entry in await candidate_document_keys(session, identifier)]
    if not keys:
        return 0
    await session.execute(
        text("DELETE FROM candidate_bgv_documents WHERE candidate_id = :cid"),
        {"cid": str(identifier)},
    )
    for key in dict.fromkeys(keys):
        await _drop_object_if_unreferenced(session, key)
    return len(keys)
