"""ZIP inspection BEFORE extraction. Candidate archives are hostile until
proven boring.

Every guard here runs against the archive's DIRECTORY (ZipInfo), not against
extracted bytes, so a decompression bomb is refused before a single byte
inflates. Extraction itself then re-checks actual sizes while reading, because
a crafted central directory can lie about `file_size`.

Refusals raise `ArchiveRejected` with a stated reason; the pipeline records it
as `failed_security`. There is no partial acceptance of a rejected archive --
one traversal entry poisons the whole file, since a crafted archive is an
attack, not a mistake.
"""
from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass

from app.services.projects.formats import is_ignored_path
from app.services.projects.limits import ProjectLimits


class ArchiveRejected(RuntimeError):
    """The archive failed a security check. `reason` is candidate-safe."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExtractedFile:
    """One safely extracted member: its path inside the archive and bytes."""

    path: str
    data: bytes


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # The Unix mode lives in the high 16 bits of external_attr.
    return stat.S_ISLNK(info.external_attr >> 16)


def _is_traversal(name: str) -> bool:
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or normalised.startswith("~"):
        return True
    # A drive letter ("C:") is absolute on the platform that wrote it.
    if len(normalised) > 1 and normalised[1] == ":":
        return True
    return any(segment == ".." for segment in normalised.split("/"))


def inspect(data: bytes, limits: ProjectLimits) -> list[zipfile.ZipInfo]:
    """Validate the archive directory and return the members worth extracting.

    Raises `ArchiveRejected` on any security refusal. Ignored directories
    (node_modules and friends) are dropped here so their declared sizes do not
    spend the extraction budget either.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        members = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveRejected("The archive could not be read as a ZIP file.") from exc

    if len(members) > limits.max_archive_entries:
        raise ArchiveRejected(
            "The archive contains more entries than the processing limit "
            f"({limits.max_archive_entries})."
        )

    kept: list[zipfile.ZipInfo] = []
    declared_total = 0
    for info in members:
        if info.is_dir():
            continue
        if _is_traversal(info.filename):
            raise ArchiveRejected(
                "The archive contains an entry whose path escapes the archive."
            )
        if _is_symlink(info):
            raise ArchiveRejected("The archive contains a symbolic link entry.")
        compressed = max(info.compress_size, 1)
        if info.file_size > limits.max_compression_ratio * compressed and (
            info.file_size > 1024 * 1024
        ):
            raise ArchiveRejected(
                "The archive contains an entry with an implausible "
                "compression ratio."
            )
        if is_ignored_path(info.filename):
            continue
        declared_total += info.file_size
        if declared_total > limits.max_extracted_bytes:
            raise ArchiveRejected(
                "The archive declares more content than the extraction limit."
            )
        kept.append(info)
    return kept


def extract(
    data: bytes, limits: ProjectLimits, *, depth: int = 0
) -> list[ExtractedFile]:
    """Extract inspected members, recursing into nested ZIPs up to the depth
    limit. Actual read sizes are enforced independently of declared sizes."""
    if depth > limits.max_archive_depth:
        raise ArchiveRejected(
            "The archive nests other archives deeper than the processing limit."
        )
    members = inspect(data, limits)
    archive = zipfile.ZipFile(io.BytesIO(data))
    extracted: list[ExtractedFile] = []
    read_total = 0
    for info in members:
        with archive.open(info) as handle:
            # +1 so a member lying about its size is detected, not truncated
            # silently into plausible-looking evidence.
            payload = handle.read(limits.max_file_bytes + 1)
        if len(payload) > limits.max_file_bytes:
            raise ArchiveRejected(
                "The archive contains an entry larger than the per-file limit."
            )
        read_total += len(payload)
        if read_total > limits.max_extracted_bytes:
            raise ArchiveRejected(
                "The archive inflates past the total extraction limit."
            )
        lowered = info.filename.lower()
        if lowered.endswith(".zip"):
            extracted.extend(
                ExtractedFile(path=f"{info.filename}/{inner.path}", data=inner.data)
                for inner in extract(payload, limits, depth=depth + 1)
            )
        else:
            extracted.append(ExtractedFile(path=info.filename, data=payload))
        if len(extracted) > limits.max_archive_entries:
            raise ArchiveRejected(
                "The archive expands to more files than the processing limit."
            )
    return extracted
