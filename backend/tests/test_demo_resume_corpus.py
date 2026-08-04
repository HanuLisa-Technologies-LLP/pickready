"""The demo resume corpus must ship INSIDE the image, or production has no candidates.

THE BUG THIS PREVENTS
---------------------
The corpus lived at `<repo-root>/resumes`. The backend image is built with
`backend` as the Docker context, so those files were never copied in, and
`seed_resumes.resumes_dir()` returned None on Cloud Run. The seed's response to
that was to log "resume corpus dir not found" and carry on succeeding.

The result was invisible for a long time: every deploy was green, every local
run had the full corpus, and production had TWO candidates against the thirty
every demonstration assumes. Nothing failed, so nothing was investigated.

These tests assert the property that was actually broken -- the corpus is inside
the build context -- rather than "the seed runs", which was true throughout.
"""
from __future__ import annotations

import pathlib

from app.scripts.seed_resumes import resumes_dir

#: Thirty demonstration candidates, one per file. A permanent fixture.
EXPECTED_RESUME_COUNT = 30

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_corpus_lives_inside_the_docker_build_context() -> None:
    """`docker build ... backend` can only copy what is under `backend/`.

    A corpus outside it is invisible to the image however correct every other
    part of the seed is, and the failure is silent.
    """
    shipped = _BACKEND_ROOT / "demo_resumes"
    assert shipped.is_dir(), (
        f"{shipped} is missing. If the corpus moved back outside backend/, it "
        "will not reach the image and production will silently have no demo "
        "candidates again."
    )


def test_the_corpus_has_every_demo_candidate() -> None:
    shipped = _BACKEND_ROOT / "demo_resumes"
    files = sorted(shipped.glob("*.docx"))
    assert len(files) == EXPECTED_RESUME_COUNT, (
        f"expected {EXPECTED_RESUME_COUNT} resumes, found {len(files)}; the "
        "demo dataset is a permanent fixture and files should not be removed"
    )


def test_the_generator_scripts_do_not_ship() -> None:
    """They author the corpus and have no business in a runtime image."""
    shipped = _BACKEND_ROOT / "demo_resumes"
    assert not list(shipped.glob("*.py")), (
        "generator scripts belong at the repo root, not in the image"
    )


def test_resumes_dir_resolves_without_any_environment_help() -> None:
    """The production path. On Cloud Run there is no SEED_RESUMES_DIR and no
    /resumes mount, so resolution has to succeed from the shipped copy alone."""
    resolved = resumes_dir()
    assert resolved is not None, (
        "resumes_dir() found nothing; on Cloud Run the seed would log that it "
        "found no files and exit successfully, seeding nobody"
    )
    assert len(list(resolved.glob("*.docx"))) == EXPECTED_RESUME_COUNT
