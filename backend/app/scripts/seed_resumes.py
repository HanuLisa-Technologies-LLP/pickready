"""Seed the sample resume corpus into Cloudinary + the Databank (dev only).

Called from `seed_dev_data.py`. For every file in the resume corpus directory
this:

1. derives a realistic, distinct candidate identity from the filename
   (`Resume_01_Akash_Rao.docx` -> "Akash Rao",
   `akash.rao01@candidates.pickready.test`, phone `9100000001`);
2. uploads the raw file through the shared content-addressed Cloudinary
   pipeline, so re-runs never duplicate the asset;
3. creates a shared-Databank Candidate + Profile row carrying all Cloudinary
   metadata;
4. enqueues `pickready.parse_resume` so the AI pipeline (text extraction +
   embedding + LLM parse  -  owned by another module) has data to work on.

IDEMPOTENT: a candidate whose email already exists is skipped entirely (no
re-upload, no duplicate row). Cloudinary uploads additionally pass
`overwrite=False`, so even a forced re-upload against the same public_id
returns the existing asset instead of creating a copy.

Cloudinary is mandatory: a file that cannot be stored is not seeded. This
prevents a demo candidate with a broken or missing resume reference.

The resume files DO ship in the backend image, at `/app/demo_resumes`. They used
to live at `<repo-root>/resumes`, outside the Docker build context, so they never
reached the image: `resumes_dir()` returned None on the deployed container, this step logged
that it found no files, and the seed carried on succeeding. That is why
production ran with two candidates instead of thirty while every deploy was
green. `SEED_RESUMES_DIR` and `/resumes` are still honoured and still take
precedence, so a local override behaves as it always did.

To seed them deliberately, including in production, use
`python -m app.scripts.seed_demo_candidates` -- this module refuses production
by default (see `seed_resume_corpus`).
"""
from __future__ import annotations

import logging
import os
import re
import uuid
import io
from pathlib import Path

from starlette.datastructures import Headers, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Candidate, Profile
from app.services.resume_storage import apply_resume_asset, profile_has_resume, store_resume
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)

# Non-deliverable, reserved test domain (NOT example.com  -  Resend/legacy email provider and
# most providers reject example.com with a 422). Every seeded candidate lives
# here so the corpus can never accidentally email a real person.
CANDIDATE_EMAIL_DOMAIN = "candidates.pickready.test"
RESUME_EXTENSIONS = {".pdf", ".docx"}


def resumes_dir() -> Path | None:
    """Resolve the resume corpus directory, or None if it can't be found.

    Search order: explicit `SEED_RESUMES_DIR`, the corpus SHIPPED IN THE IMAGE,
    the conventional container copy target `/resumes`, then `<repo-root>/resumes`
    for a source checkout.

    The shipped copy is why the demo candidates can exist in production at all.
    The corpus used to live at `<repo-root>/resumes`, which is OUTSIDE the
    backend Docker build context, so it never reached the image and this
    function returned None on the deployed container. The seed then logged that it found no
    files and moved on, which is why production had two candidates against the
    thirty the demo assumes. Moving the .docx files under `backend/` puts them
    inside the context, so the existing `COPY . .` carries them to
    `/app/demo_resumes` with no Dockerfile change. The generator scripts stay
    at the repo root: they author the corpus and have no business in a runtime
    image."""
    candidates: list[Path] = []
    env = os.getenv("SEED_RESUMES_DIR")
    if env:
        candidates.append(Path(env))
    # scripts -> app -> backend root. Present in the image and in a checkout.
    candidates.append(Path(__file__).resolve().parents[2] / "demo_resumes")
    candidates.append(Path("/resumes"))
    # scripts -> app -> /app (backend root) -> repo root, when run from source.
    candidates.append(Path(__file__).resolve().parents[3] / "resumes")
    for path in candidates:
        try:
            if path.is_dir():
                return path
        except OSError:  # pragma: no cover  -  unreadable path
            continue
    return None


def _slug(value: str) -> str:
    """Lowercase, non-alphanumeric -> single underscore (deterministic id)."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def derive_identity(filename: str) -> dict[str, str]:
    """Map `Resume_07_Vikramaditya_Verma.docx` to a stable identity.

    Returns a stable name, email, phone and seed reference. The sequence keeps
    generated identities unique even if two files ever share a name."""
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
    if not name_words:  # pathological filename  -  fall back to the stem
        name_words = [stem]
    full_name = " ".join(w.capitalize() for w in name_words)
    local = ".".join(w.lower() for w in name_words)
    email = f"{local}{seq}@{CANDIDATE_EMAIL_DOMAIN}"
    phone = f"91{seq.zfill(2)}000000"[:10]  # e.g. 9107000000  -  distinct, non-real
    public_id = f"resume_{seq}_{_slug(' '.join(name_words))}"
    return {
        "seq": seq,
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "public_id": public_id,
    }


async def seed_resume_corpus(
    session: AsyncSession,
    source_tenant_id: uuid.UUID,
    *,
    allow_production: bool = False,
) -> int:
    """Seed the resume corpus. Returns the number of NEW candidates created.

    Refuses to run in production UNLESS the caller says otherwise, and the
    distinction is the whole point of the flag.

    The guard exists for `seed_dev_data`, which seeds an entire development
    world -- demo tenants, staff logins, jobs -- and must never be pointed at a
    real database by accident. That caller keeps the default and keeps the
    protection.

    The thirty resume candidates are a different matter: they are PERMANENT
    demonstration fixtures that are supposed to exist in production, and a
    blanket refusal is why production had two candidates while every deploy
    reported success. `seed_demo_candidates` therefore opts in explicitly.
    Opt-in rather than removal, because the risk the guard was written for --
    somebody running the full dev seed against production -- has not gone away.
    """
    if get_settings().is_production and not allow_production:
        log.info("seed_resumes: skipped (production environment)")
        return 0

    directory = resumes_dir()
    if directory is None:
        print(
            "  ! resume corpus dir not found, set SEED_RESUMES_DIR or copy the "
            "files into the container (docker compose cp ./resumes backend:/resumes); "
            "skipping resume seed"
        )
        return 0

    files = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in RESUME_EXTENSIONS
    )
    if not files:
        print(f"  ! no resume files in {directory}  -  skipping resume seed")
        return 0

    created = 0
    repaired = 0
    uploaded = 0
    for path in files:
        ident = derive_identity(path.name)
        existing = (
            await session.execute(
                select(Candidate).where(Candidate.email == ident["email"])
            )
        ).scalar_one_or_none()
        profile = None
        if existing is not None:
            profile = (
                await session.execute(
                    select(Profile)
                    .where(Profile.candidate_id == existing.id)
                    .order_by(Profile.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if profile is not None and profile_has_resume(profile):
                continue  # idempotent: complete seed row, do not re-upload

        try:
            data = path.read_bytes()
        except OSError as exc:  # pragma: no cover  -  unreadable file
            log.warning("seed_resumes: cannot read %s: %s", path.name, exc)
            continue

        mime_type = (
            "application/pdf" if path.suffix.lower() == ".pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        upload = UploadFile(file=io.BytesIO(data), filename=path.name,
                            headers=Headers({"content-type": mime_type}))
        try:
            asset = await store_resume(upload)
        except Exception as exc:
            log.error("seed_resumes: private storage upload failed for %s: %s", path.name, exc)
            continue
        uploaded += 1

        if existing is None:
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
            )
            session.add(profile)
            created += 1
        elif profile is None:
            profile = Profile(
                candidate_id=existing.id,
                source_tenant_id=source_tenant_id,
            )
            session.add(profile)
            repaired += 1
        else:
            repaired += 1

        apply_resume_asset(profile, asset)
        await session.flush()

        # Heavy work is always the Celery task (claude.md rule 4).
        celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
        action = "repaired" if existing is not None else "added"
        print(f"  + {action} resume candidate {ident['full_name']} <{ident['email']}> "
              f"(gcs_object={asset.public_id})")

    print(f"  = resume corpus: {created} new candidate(s), "
          f"{repaired} repaired profile(s), {uploaded} uploaded to private GCS, "
          f"{len(files)} file(s) scanned")
    return created
