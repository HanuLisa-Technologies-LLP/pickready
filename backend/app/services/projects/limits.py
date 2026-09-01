"""The processing ceilings, read once from Settings.

Every limit the pipeline enforces comes through here, so a test can construct
a small `ProjectLimits` directly and the production path reads configuration.
No module in this package reads `get_settings()` for a limit anywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class ProjectLimits:
    max_projects_per_candidate: int
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_archive_depth: int
    max_archive_entries: int
    max_extracted_bytes: int
    max_compression_ratio: int
    max_text_chars_per_file: int
    max_evidence_units: int
    max_ai_context_chars: int
    repo_max_files: int
    repo_max_file_bytes: int
    description_max_words: int = 100


def from_settings() -> ProjectLimits:
    settings = get_settings()
    return ProjectLimits(
        max_projects_per_candidate=settings.project_max_projects_per_candidate,
        max_files=settings.project_max_files,
        max_file_bytes=settings.project_max_file_bytes,
        max_total_bytes=settings.project_max_total_bytes,
        max_archive_depth=settings.project_max_archive_depth,
        max_archive_entries=settings.project_max_archive_entries,
        max_extracted_bytes=settings.project_max_extracted_bytes,
        max_compression_ratio=settings.project_max_compression_ratio,
        max_text_chars_per_file=settings.project_max_text_chars_per_file,
        max_evidence_units=settings.project_max_evidence_units,
        max_ai_context_chars=settings.project_max_ai_context_chars,
        repo_max_files=settings.project_repo_max_files,
        repo_max_file_bytes=settings.project_repo_max_file_bytes,
    )
