"""Seed the sample resume corpus into Cloudinary + the Databank (dev only).

Called from `seed_dev_data.py`. For every file in the resume corpus directory
this:

1. derives a realistic, distinct candidate identity from the filename
   (`Resume_01_Akash_Rao.docx` -> "Akash Rao",
   `akash.rao01@candidates.pickready.test`, phone `9100000001`);
2. uploads the raw file to Cloudinary under a DETERMINISTIC public_id
   (`pickready/resumes/resume_01_akash_rao`) so re-runs never duplicate the
   asset;
3. creates a shared-Databank Candidate + Profile row carrying the resume_url;
4. enqueues `pickready.parse_resume` so the AI pipeline (text extraction +
   embedding + LLM parse — owned by another module) has data to work on.

IDEMPOTENT: a candidate whose email already exists is skipped entirely (no
re-upload, no duplicate row). Cloudinary uploads additionally pass
`overwrite=False`, so even a forced re-upload against the same public_id
returns the existing asset instead of creating a copy.

FAILS SOFT: if Cloudinary is unconfigured or unreachable, the candidate/profile
rows are still created with `resume_url=None` (the parse task tolerates a
missing URL) and a clear warning is logged — the seed never crashes.

The resume files are NOT part of the backend image. Point the seed at them
with the `SEED_RESUMES_DIR` env var, or copy them into the container first
(`docker compose ... cp ./resumes backend:/resumes`). Absent both, the corpus
step logs that it found no files and the rest of the seed proceeds unchanged.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Candidate, Profile
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)

# Non-deliverable, reserved test domain (NOT example.com — Resend/Mailtrap and
# most providers reject example.com with a 422). Every seeded candidate lives
# here so the corpus can never accidentally email a real person.
CANDIDATE_EMAIL_DOMAIN = "candidates.pickready.test"
CLOUDINARY_FOLDER = "pickready/resumes"
RESUME_EXTENSIONS = {".pdf", ".doc", ".docx"}


def resumes_dir() -> Path | None:
    """Resolve the resume corpus directory, or None if it can't be found.

    Search order: explicit `SEED_RESUMES_DIR`, the conventional container copy
    target `/resumes`, then `<repo-root>/resumes` for a source checkout."""
    candidates: list[Path] = []
    env = os.getenv("SEED_RESUMES_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/resumes"))
    # scripts -> app -> /app (backend root) -> repo root, when run from source.
    candidates.append(Path(__file__).resolve().parents[3] / "resumes")
    for path in candidates:
        try:
            if path.is_dir():
                return path
        except OSError:  # pragma: no cover — unreadable path
            continue
    return None


def _slug(value: str) -> str:
    """Lowercase, non-alphanumeric -> single underscore (deterministic id)."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def derive_identity(filename: str) -> dict[str, str]:
    """Map `Resume_07_Vikramaditya_Verma.docx` to a stable identity.

    Returns name, email, phone and the deterministic Cloudinary public_id. The
    sequence number keeps emails/phones/public_ids unique even if two files
    ever share a name."""
    stem = Path(filename).stem
    parts = stem.split("_")
    # Expected shape: ["Resume", "07", "Vikramaditya", "Verma"]. Be tolerant:
    # find the first numeric token as the sequence, the rest are name words.
    seq = "00"
    name_words: list[str] = []
    for token in parts:
        if token.lower() == "resume":
            continue
        if token.isdigit() and seq == "00":
            seq = token.zfill(2)
            continue
        name_words.append(token)
    if not name_words:  # pathological filename — fall back to the stem
        name_words = [stem]
    full_name = " ".join(w.capitalize() for w in name_words)
    local = ".".join(w.lower() for w in name_words)
    email = f"{local}{seq}@{CANDIDATE_EMAIL_DOMAIN}"
    phone = f"91{seq.zfill(2)}000000"[:10]  # e.g. 9107000000 — distinct, non-real
    public_id = f"resume_{seq}_{_slug(' '.join(name_words))}"
    return {
        "seq": seq,
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "public_id": public_id,
    }


def _upload_to_cloudinary(data: bytes, public_id: str) -> str | None:
    """Upload raw bytes under a deterministic public_id. Returns the secure_url
    or None (unconfigured or any failure) — never raises."""
    if not get_settings().cloudinary_url:
        return None
    try:
        import cloudinary.uploader  # lazy: keep import cost out of the hot path

        result = cloudinary.uploader.upload(
            data,
            resource_type="raw",
            folder=CLOUDINARY_FOLDER,
            public_id=public_id,
            overwrite=False,  # deterministic id + no-overwrite => no duplicates
            unique_filename=False,
            use_filename=False,
        )
        return result.get("secure_url")
    except Exception as exc:  # noqa: BLE001 — storage failure must not crash seed
        log.warning("seed_resumes: Cloudinary upload failed for %s: %s", public_id, exc)
        return None


async def seed_resume_corpus(session: AsyncSession, source_tenant_id: uuid.UUID) -> int:
    """Seed the resume corpus. Returns the number of NEW candidates created.

    Only runs in non-production environments — the corpus is dev/demo data."""
    if get_settings().is_production:
        log.info("seed_resumes: skipped (production environment)")
        return 0

    directory = resumes_dir()
    if directory is None:
        print(
            "  ! resume corpus dir not found — set SEED_RESUMES_DIR or copy the "
            "files into the container (docker compose cp ./resumes backend:/resumes); "
            "skipping resume seed"
        )
        return 0

    files = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in RESUME_EXTENSIONS
    )
    if not files:
        print(f"  ! no resume files in {directory} — skipping resume seed")
        return 0

    cloud_ok = bool(get_settings().cloudinary_url)
    if not cloud_ok:
        print("  ! CLOUDINARY_URL unset — seeding candidates with resume_url=None")

    created = 0
    uploaded = 0
    for path in files:
        ident = derive_identity(path.name)
        existing = (
            await session.execute(
                select(Candidate).where(Candidate.email == ident["email"])
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue  # idempotent: already seeded, do not re-upload

        try:
            data = path.read_bytes()
        except OSError as exc:  # pragma: no cover — unreadable file
            log.warning("seed_resumes: cannot read %s: %s", path.name, exc)
            continue

        resume_url = _upload_to_cloudinary(data, ident["public_id"])
        if resume_url:
            uploaded += 1

        candidate = Candidate(
            tenant_id=None,  # shared Databank row (spans tenants)
            full_name=ident["full_name"],
            email=ident["email"],
            phone=ident["phone"],
            city="Bengaluru",
            consent_databank=True,  # corpus is the matchable Databank
        )
        session.add(candidate)
        await session.flush()

        profile = Profile(
            candidate_id=candidate.id,
            source_tenant_id=source_tenant_id,
            resume_url=resume_url,
            # resume_text / embedding / parsed_fields are filled by
            # pickready.parse_resume (owned elsewhere) — enqueued below.
        )
        session.add(profile)
        await session.flush()

        # Heavy work is always the Celery task (claude.md rule 4).
        celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
        created += 1
        print(f"  + resume candidate {ident['full_name']} <{ident['email']}> "
              f"(url={'yes' if resume_url else 'none'})")

    print(f"  = resume corpus: {created} new candidate(s), "
          f"{uploaded} uploaded to Cloudinary, {len(files)} file(s) scanned")
    return created
