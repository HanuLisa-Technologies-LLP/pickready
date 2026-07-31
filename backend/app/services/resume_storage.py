"""Cloudinary-backed resume storage.

This module is the only place that accepts, stores, retrieves, or deletes a
resume binary.  Resume bytes never persist on the application filesystem.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

ALLOWED_RESUME_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_RESUME_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # Chromium and some mobile browsers do not identify DOCX correctly.
    "application/octet-stream",
}
MAX_RESUME_BYTES = 10 * 1024 * 1024
CLOUDINARY_FOLDER = "pickready/resumes"


@dataclass(frozen=True)
class ResumeAsset:
    public_id: str
    secure_url: str
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    sha256: str
    metadata: dict[str, Any]


def _error(code: int, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"message": message, "retryable": retryable},
    )


def _normalise_filename(filename: str | None) -> str:
    name = os.path.basename((filename or "").strip())
    if not name or len(name) > 255:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a resume file with a valid filename.")
    return name


def _assert_document_signature(data: bytes, extension: str) -> None:
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The selected file is not a valid PDF.")
    if extension == ".docx" and data[:2] != b"PK":
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The selected file is not a valid DOCX document.")


async def read_validated_resume(file: UploadFile) -> tuple[bytes, str, str]:
    """Read one upload into memory after strict format and size validation."""
    filename = _normalise_filename(file.filename)
    extension = os.path.splitext(filename.lower())[1]
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if extension not in ALLOWED_RESUME_EXTENSIONS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Resume files must be PDF or DOCX.")
    if content_type and content_type not in ALLOWED_RESUME_CONTENT_TYPES:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The selected file type does not match a PDF or DOCX resume.")

    data = await file.read(MAX_RESUME_BYTES + 1)
    if not data:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The selected resume is empty.")
    if len(data) > MAX_RESUME_BYTES:
        raise _error(status.HTTP_413_CONTENT_TOO_LARGE, "Resume files must be 10 MB or smaller.")
    _assert_document_signature(data, extension)
    mime_type = (
        "application/pdf"
        if extension == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return data, filename, mime_type


def _configure_cloudinary() -> None:
    settings = get_settings()
    if not settings.cloudinary_url:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Resume storage is not configured. Please try again shortly or contact support.",
            retryable=True,
        )
    import cloudinary

    cloudinary.config(cloudinary_url=settings.cloudinary_url, secure=True)


def _cloudinary_resource(public_id: str) -> dict[str, Any] | None:
    import cloudinary.api

    try:
        return cloudinary.api.resource(public_id, resource_type="raw")
    except Exception:  # A missing asset and an unavailable admin API both fall through to upload/error.
        return None


def _upload_or_get_existing(data: bytes, sha256: str, filename: str, mime_type: str) -> dict[str, Any]:
    """Use a content-addressed id so retries cannot create another asset."""
    _configure_cloudinary()
    public_id = f"{CLOUDINARY_FOLDER}/{sha256}"
    existing = _cloudinary_resource(public_id)
    if existing:
        return existing

    import cloudinary.uploader

    try:
        return cloudinary.uploader.upload(
            data,
            resource_type="raw",
            public_id=public_id,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            filename_override=filename,
            context={"original_filename": filename, "mime_type": mime_type, "sha256": sha256},
        )
    except Exception as exc:
        # A response can be lost after Cloudinary successfully receives the asset.
        # Resolve the deterministic id before declaring the request failed.
        existing = _cloudinary_resource(public_id)
        if existing:
            return existing
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We could not securely store your resume. Nothing was submitted; please retry.",
            retryable=True,
        ) from exc


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def store_resume(file: UploadFile) -> ResumeAsset:
    """Validate and persist a resume in Cloudinary, returning DB-ready metadata."""
    data, filename, mime_type = await read_validated_resume(file)
    sha256 = hashlib.sha256(data).hexdigest()
    result = await run_in_threadpool(_upload_or_get_existing, data, sha256, filename, mime_type)
    secure_url = result.get("secure_url")
    public_id = result.get("public_id")
    if not secure_url or not public_id:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Resume storage returned an incomplete response. Please retry.",
            retryable=True,
        )
    metadata = {
        "asset_id": result.get("asset_id"),
        "version": result.get("version"),
        "resource_type": result.get("resource_type", "raw"),
        "format": result.get("format"),
        "folder": CLOUDINARY_FOLDER,
    }
    return ResumeAsset(
        public_id=str(public_id),
        secure_url=str(secure_url),
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=int(result.get("bytes") or len(data)),
        uploaded_at=_as_utc(result.get("created_at")),
        sha256=sha256,
        metadata=metadata,
    )


def apply_resume_asset(profile: Any, asset: ResumeAsset) -> None:
    """Copy all durable Cloudinary metadata to a Profile before it is flushed."""
    profile.resume_url = asset.secure_url
    profile.resume_public_id = asset.public_id
    profile.resume_original_filename = asset.original_filename
    profile.resume_mime_type = asset.mime_type
    profile.resume_size_bytes = asset.size_bytes
    profile.resume_uploaded_at = asset.uploaded_at
    profile.resume_sha256 = asset.sha256
    profile.resume_metadata_json = asset.metadata


def copy_resume_metadata(source: Any, target: Any) -> None:
    """Reuse a Cloudinary asset by copying its immutable persisted metadata."""
    if not profile_has_resume(source):
        raise ResumeStorageError("The prior resume is missing Cloudinary metadata.")
    for field in (
        "resume_url", "resume_public_id", "resume_original_filename",
        "resume_mime_type", "resume_size_bytes", "resume_uploaded_at",
        "resume_sha256", "resume_metadata_json",
    ):
        setattr(target, field, getattr(source, field))


def profile_has_resume(profile: Any) -> bool:
    return bool(profile.resume_public_id and profile.resume_url and profile.resume_original_filename)


async def fetch_resume_bytes(profile: Any) -> bytes:
    """Download a resume through Cloudinary's signed API endpoint.

    Free Cloudinary environments block direct PDF/ZIP delivery by default,
    which makes an otherwise valid ``secure_url`` return 401. The signed
    download endpoint authenticates the backend without making candidate
    resumes public and works for raw PDF and DOCX assets.
    """
    if not profile_has_resume(profile):
        raise ResumeStorageError("The resume file is missing its Cloudinary metadata.")
    _configure_cloudinary()
    import cloudinary.utils

    metadata = profile.resume_metadata_json or {}
    resource_type = metadata.get("resource_type") or "raw"
    delivery_type = metadata.get("delivery_type") or "upload"
    download_url = cloudinary.utils.private_download_url(
        profile.resume_public_id,
        "",
        resource_type=resource_type,
        type=delivery_type,
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as client:
            response = await client.get(download_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ResumeStorageError("Cloudinary could not be reached while retrieving the resume.") from exc
    if not response.content:
        raise ResumeStorageError("Cloudinary returned an empty resume file.")
    return response.content


async def delete_resume_asset(asset: ResumeAsset) -> None:
    """Best-effort compensation for a database write that fails after upload."""
    try:
        _configure_cloudinary()
        import cloudinary.uploader

        await run_in_threadpool(cloudinary.uploader.destroy, asset.public_id, resource_type="raw", invalidate=True)
    except Exception:
        # The asset is content-addressed and harmless without a DB reference;
        # retaining it is safer than obscuring the database failure.
        return


class ResumeStorageError(RuntimeError):
    pass
