"""The processing lifecycle for one submitted project.

    submitted -> processing -> [evidence built] -> persisted
              -> original deleted (verified) -> processed

Explicit failure states, never silence: `failed_security` (an archive tripped
a guard), `failed_extraction` (nothing usable could be read, or the repository
was rejected), `partially_processed` (deterministic evidence persisted, the AI
interpretation did not arrive). Partial success is a real outcome: one
unreadable file never discards the readable ones.

RETRY-SAFE AND IDEMPOTENT. The project row is the idempotency unit: derived
output lives in columns on that one row, so a rerun overwrites rather than
duplicates; a completed project returns immediately; staging keys are
content-addressed so a retried upload resolves to the same object.

DELETION IS VERIFIED, NEVER ASSUMED (brief section 27). A temporary original
is recorded as deleted only after a HEAD confirms it is gone. Failures are
counted on the row, the keys stay listed, and the hourly sweeper retries; no
fallback archive exists for them to fall into.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.project import (
    STATUS_FAILED_EXTRACTION,
    STATUS_FAILED_SECURITY,
    STATUS_PARTIALLY_PROCESSED,
    STATUS_PERSISTED,
    STATUS_PROCESSED,
    STATUS_PROCESSING,
    CandidateProject,
)
from app.services import object_storage
from app.services.llm_router import LLMUnavailableError
from app.services.projects import ai_reasoning, archive_safety, evidence, repository
from app.services.projects.limits import ProjectLimits, from_settings
from app.services.projects.parsers import ParsedArtifact, parse_file

logger = logging.getLogger(__name__)


class ProjectNotFound(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Input gathering ──────────────────────────────────────────────────────────


async def _gather_files(
    project: CandidateProject, limits: ProjectLimits
) -> tuple[list[tuple[str, bytes]], list[str], int]:
    """Fetch staged originals and expand archives. Returns (files, limitations,
    raw byte count). Raises ArchiveRejected for a security refusal; a missing
    or unreadable staged object is a stated limitation, not a crash."""
    files: list[tuple[str, bytes]] = []
    limitations: list[str] = []
    raw_bytes = 0
    for row in project.intake_objects_json or []:
        key = str(row.get("key") or "")
        filename = str(row.get("filename") or key.rsplit("/", 1)[-1])
        try:
            data = await run_in_threadpool(object_storage.get_bytes, key)
        except object_storage.ObjectStorageError:
            limitations.append(
                f"One staged file could not be retrieved for processing: {filename}"
            )
            continue
        raw_bytes += len(data)
        if filename.lower().endswith(".zip"):
            extracted = archive_safety.extract(data, limits)
            files.extend(
                (f"{filename}/{member.path}", member.data) for member in extracted
            )
            raw_bytes += sum(len(member.data) for member in extracted)
        else:
            files.append((filename, data))
    return files, limitations, raw_bytes


# ── The pipeline ─────────────────────────────────────────────────────────────


async def process_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    limits: ProjectLimits | None = None,
) -> CandidateProject:
    """Run (or rerun) the whole pipeline for one project."""
    limits = limits or from_settings()
    project = await session.get(CandidateProject, project_id)
    if project is None:
        raise ProjectNotFound(f"Project {project_id} not found")
    if project.status == STATUS_PROCESSED:
        return project

    # AI-ONLY COMPLETION. Deterministic evidence is durable and the originals
    # are correctly gone (deletion after persistence is the contract), so the
    # only rerunnable stage is the interpretation. Rebuilding the pack from
    # the stored record also means a mixed submission never loses its file
    # evidence to a repository-only refetch.
    if (
        project.evidence_json is not None
        and project.processed_at is not None
        and not project.intake_objects_json
    ):
        return await _complete_interpretation(session, project, limits)

    started = time.monotonic()
    project.status = STATUS_PROCESSING
    project.failure_code = None
    project.status_detail = "Your project is being analysed."
    project.updated_at = _now()
    await session.flush()

    telemetry: dict[str, Any] = dict(project.telemetry_json or {})
    telemetry["runs"] = int(telemetry.get("runs", 0)) + 1
    limitations: list[str] = []
    repo_metadata: dict[str, Any] | None = None
    files: list[tuple[str, bytes]] = []
    raw_bytes = 0

    # 1. Security + gathering.
    try:
        if project.intake_objects_json:
            files, file_limitations, raw_bytes = await _gather_files(project, limits)
            limitations.extend(file_limitations)
    except archive_safety.ArchiveRejected as exc:
        project.status = STATUS_FAILED_SECURITY
        project.failure_code = "archive_rejected"
        project.status_detail = exc.reason
        project.telemetry_json = {
            **telemetry,
            "processing_ms": int((time.monotonic() - started) * 1000),
        }
        project.updated_at = _now()
        await session.flush()
        return project

    if project.repository_url:
        try:
            ref = repository.validate_repository_url(project.repository_url)
            fetched = await repository.fetch_repository(ref, limits)
            repo_metadata = fetched.metadata
            files.extend(fetched.files)
            raw_bytes += sum(len(data) for _, data in fetched.files)
            limitations.extend(fetched.limitations)
            telemetry["repo_tree_entries"] = fetched.total_tree_entries
            telemetry["repo_ignored_entries"] = fetched.ignored_entries
            telemetry["repo_oversize_skipped"] = fetched.skipped_oversize
        except repository.RepositoryRejected as exc:
            if files:
                limitations.append(f"The repository link was not usable: {exc.reason}")
            else:
                project.status = STATUS_FAILED_EXTRACTION
                project.failure_code = "repository_rejected"
                project.status_detail = exc.reason
                project.telemetry_json = telemetry
                project.updated_at = _now()
                await session.flush()
                return project
        # RepositoryUnavailable is deliberately NOT caught: it is transient,
        # and the Celery retry policy is the right owner of that wait.

    if not files:
        project.status = STATUS_FAILED_EXTRACTION
        project.failure_code = "nothing_extractable"
        project.status_detail = (
            "No readable content was found in this submission."
        )
        project.telemetry_json = telemetry
        project.updated_at = _now()
        await session.flush()
        return project

    # 2. Deterministic parsing.
    artifacts: list[ParsedArtifact] = [
        parse_file(path, data, limits) for path, data in files
    ]

    # 3. Evidence generation and reduction.
    units = evidence.build_units(artifacts, limits)
    record = evidence.build_evidence_record(
        project_name=project.name,
        candidate_description=project.description,
        artifacts=artifacts,
        units=units,
        submission_kind=project.submission_kind,
        repository_metadata=repo_metadata,
        processing_limitations=limitations,
    )
    readme = next(
        (a.text_excerpt for a in artifacts if a.signals.get("is_readme")), ""
    )
    pack = evidence.build_evidence_pack(record, units, limits, readme_excerpt=readme)

    telemetry.update(
        {
            "raw_bytes": raw_bytes,
            "file_count": len(files),
            "supported_count": sum(1 for a in artifacts if a.supported),
            "unsupported_count": sum(1 for a in artifacts if not a.supported),
            "evidence_unit_count": len(units),
            "ai_context_chars": len(pack),
        }
    )

    # 4. AI reasoning. Failure here is the PARTIAL outcome, never a discard:
    # the deterministic evidence below persists either way.
    interpretation: dict[str, Any] | None = None
    try:
        interpretation = await ai_reasoning.interpret(pack)
        telemetry["ai_status"] = "completed"
    except (ai_reasoning.ProjectReasoningError, LLMUnavailableError) as exc:
        telemetry["ai_status"] = f"unavailable:{type(exc).__name__}"
        logger.warning(
            "project_evidence.ai_unavailable project_id=%s error=%s",
            project.id,
            type(exc).__name__,
        )

    # 5. Persist the derived evidence.
    project.evidence_json = record
    project.evidence_units_json = [unit.as_json() for unit in units]
    project.ai_interpretation_json = interpretation
    project.evidence_strength = (
        interpretation["evidence_strength"] if interpretation else None
    )
    project.processed_at = _now()
    project.status = STATUS_PERSISTED if interpretation else STATUS_PARTIALLY_PROCESSED
    if interpretation:
        project.status_detail = "Project evidence is ready."
    else:
        project.status_detail = (
            "The evidence summary is ready; the deeper analysis will be "
            "completed automatically."
        )
    telemetry["processing_ms"] = int((time.monotonic() - started) * 1000)
    project.telemetry_json = telemetry
    project.updated_at = _now()
    await session.flush()

    # 6. Only NOW may the temporary originals go.
    await delete_intake_objects(session, project)
    return project


async def _complete_interpretation(
    session: AsyncSession, project: CandidateProject, limits: ProjectLimits
) -> CandidateProject:
    """Retry the interpretation stage alone, from the persisted record."""
    units = [
        evidence.EvidenceUnit(
            unit_type=str(row.get("unit_type") or ""),
            statement=str(row.get("statement") or ""),
            source_path=str(row.get("source_path") or ""),
            source_detail=row.get("source_detail"),
        )
        for row in project.evidence_units_json or []
    ]
    pack = evidence.build_evidence_pack(project.evidence_json or {}, units, limits)
    telemetry: dict[str, Any] = dict(project.telemetry_json or {})
    telemetry["runs"] = int(telemetry.get("runs", 0)) + 1
    try:
        interpretation = await ai_reasoning.interpret(pack)
        telemetry["ai_status"] = "completed"
    except (ai_reasoning.ProjectReasoningError, LLMUnavailableError) as exc:
        telemetry["ai_status"] = f"unavailable:{type(exc).__name__}"
        project.status = STATUS_PARTIALLY_PROCESSED
        project.status_detail = (
            "The evidence summary is ready; the deeper analysis will be "
            "completed automatically."
        )
        project.telemetry_json = telemetry
        project.updated_at = _now()
        await session.flush()
        return project
    project.ai_interpretation_json = interpretation
    project.evidence_strength = interpretation["evidence_strength"]
    project.status = STATUS_PROCESSED
    project.status_detail = "Project evidence is ready."
    if project.original_deleted_at is None:
        project.original_deleted_at = _now()
    project.telemetry_json = telemetry
    project.updated_at = _now()
    await session.flush()
    return project


async def delete_intake_objects(
    session: AsyncSession, project: CandidateProject
) -> bool:
    """Delete every remaining staged original, verifying each deletion.

    True when nothing temporary remains. Safe to call repeatedly; the sweeper
    calls it for any project whose deletion previously failed.
    """
    remaining = list(project.intake_objects_json or [])
    if not remaining:
        if project.original_deleted_at is None and project.processed_at is not None:
            project.original_deleted_at = _now()
        if project.status == STATUS_PERSISTED:
            project.status = STATUS_PROCESSED
        project.updated_at = _now()
        await session.flush()
        return True
    if project.processed_at is None:
        # Evidence is not durable yet; deleting now would destroy the only
        # copy of the submission. The brief's ordering is deletion LAST.
        return False

    still_present: list[dict[str, Any]] = []
    for row in remaining:
        key = str(row.get("key") or "")
        await run_in_threadpool(object_storage.delete, key)
        try:
            gone = not await run_in_threadpool(object_storage.exists, key)
        except object_storage.ObjectStorageError:
            gone = False
        if not gone:
            still_present.append(row)

    if still_present:
        project.intake_objects_json = still_present
        project.deletion_attempts = int(project.deletion_attempts or 0) + 1
        project.updated_at = _now()
        await session.flush()
        logger.warning(
            "project_evidence.deletion_incomplete project_id=%s remaining=%d attempts=%d",
            project.id,
            len(still_present),
            project.deletion_attempts,
        )
        return False

    project.intake_objects_json = []
    project.original_deleted_at = _now()
    if project.status == STATUS_PERSISTED:
        project.status = STATUS_PROCESSED
        project.status_detail = "Project evidence is ready."
    project.updated_at = _now()
    await session.flush()
    return True


async def discard_project(session: AsyncSession, project: CandidateProject) -> None:
    """Candidate-initiated removal: staged originals AND derived evidence go.

    Staged-object deletion is best effort here (the row is going regardless
    and the lifecycle rule reclaims stragglers); the row deletion is the part
    that must not fail silently, and it raises normally if it does.
    """
    for row in project.intake_objects_json or []:
        await run_in_threadpool(object_storage.delete, str(row.get("key") or ""))
    await session.delete(project)
    await session.flush()
