"""The tenant-owned employer-verification system is RETIRED (vivekium C8).

Owner-ruled 2026-09-18: the brief describes the candidate-owned system
(candidate_employments, bgv_verifications, the seven-item checkbox form),
so the older tenant-owned one goes the way the 2026-09-09 and
2026-09-10 removals went,
with a sweep rather than a memory. The TABLE stays: rows already written
are history, and dropping them would delete the answer to "what did this
employer actually say" for verifications that really ran.

The sweep covers live source only. Migrations, docs and history keep the
name, because they record what was true when they were written.
"""
from __future__ import annotations

import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
FRONTEND = BACKEND.parent / "frontend"

#: The retired system's names. `verification_requests` itself is allowed in
#: exactly one live place, the models package's comment trail, and is
#: otherwise the table's name in MIGRATIONS, which are provenance.
RETIRED_NAMES = (
    "VerificationRequest",
    "send_verification_requests",
    "parse_verification_reply",
    "verification_parsing",
    "EMPLOYER_FORM_FIELDS",
    "employer_seq",
)

#: This test, and the API contract doc trail, may speak the names.
_ALLOWED = {"test_verification_requests_retired.py"}


def _live_python():
    for path in APP.rglob("*.py"):
        if path.name in _ALLOWED:
            continue
        yield path


def test_the_retired_system_is_gone_from_live_backend_source() -> None:
    offenders: list[str] = []
    for path in _live_python():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in RETIRED_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(BACKEND)}: {name}")
    assert not offenders, (
        "the retired employer-verification system is still named in live "
        f"source: {offenders}. It was retired under C8; the surviving system "
        "is candidate_employments + bgv_verifications + /bgv/form/{token}."
    )


def test_the_retired_routes_are_not_registered() -> None:
    from app.main import app

    paths = {route.path for route in app.router.routes}
    for gone in (
        "/api/v1/verification/form/{token}",
        "/api/v1/verification/profile/{profile_id}",
        "/api/v1/verification/requests/{request_id}/override",
    ):
        assert gone not in paths, f"{gone} is still registered"
    # The survivors, so this test cannot pass by the router being empty.
    assert "/api/v1/verification/outreach" in paths
    assert "/api/v1/verification/inbound-email" in paths
    assert "/api/v1/bgv/form/{token}" in paths


def test_the_retired_frontend_intake_is_gone() -> None:
    offenders: list[str] = []
    for path in FRONTEND.rglob("*.tsx"):
        if "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "employer_emails" in text or "VerificationFormInfo" in text:
            offenders.append(str(path.relative_to(FRONTEND)))
    assert not offenders, f"retired intake still in the frontend: {offenders}"
