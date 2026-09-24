"""Login by one-time code is gone, and this is what keeps it gone.

WHAT WENT, 2026-09-24 (Phase 7 Wave A)
--------------------------------------
Firebase took over identity on 2026-07-24 (claude.md rule 2). The code-login
flow survived it by two months as code with no route: three handlers in
`api/auth.py` defined without a decorator, `services/otp.py` (challenges,
hashed codes, an SMS fan-out, a limiter with a silent memory fallback), four
schemas, three helpers and a deprecated audience alias in `core/security.py`,
and an unused `otp-input` component. The LIVE half of `services/otp.py`, the
workspace chooser, moved to `services/login_context.py` first; everything else
was deleted.

WHAT IS STILL HERE, AND WHY THAT IS NOT AN EXEMPTION
-----------------------------------------------------
The OTP model and enum and the `otp_challenges` table went in Wave B's
WP-B2 (migration 0128 drops the table only when it is empty). The
`pickready.send_sms` task, `services/sms_service.py`, the MSG91 settings and
the MSG91 secret sit in files shared with every other phase of this release,
so their removal is the rest of Phase 7 Wave B, after those phases merge. `WAVE_B_PENDING` names the exact
files that may still mention them, and it is a RATCHET in both directions: a
mention in any other file fails, and an entry whose file no longer mentions
them fails too, so the list can only shrink.

`tests/test_platform_audit.py` separately keeps one-time-code COPY out of every
portal screen; this file keeps the CODE out of the tree.
"""
from __future__ import annotations

import importlib.util
import re

from tests.removal_sweep import BACKEND, REPO, sweep

#: Gone now. Anywhere outside the exemptions is a failure.
GONE = re.compile(
    r"\brequest_otp\b|\bverify_otp\b|\bregister_candidate\b|\bverify_challenge\b"
    r"|\bpending_channels(_for)?\b|\bAUDIENCE_INTERNAL\b|\bgenerate_otp\b"
    r"|\bhash_otp\b|\bOTPVerifyOut\b|\bOTPVerifyIn\b|\bOTPRequestIn\b"
    r"|\bOTPRequestOut\b|\bCandidateRegister(In|Out)\b|otp-input|\bOtpInput\b"
    r"|services\.otp\b|services import otp\b"
)

#: Removed in Wave B. May appear ONLY in the files below.
WAVE_B = re.compile(r"OTPChallenge|OTPChannel|otp_challenges|send_sms|sms_service|MSG91|msg91")

WAVE_B_PENDING = frozenset(
    {
        "backend/app/main.py",
        "backend/app/services/sms_service.py",
        "backend/app/workers/tasks.py",
        "backend/tests/test_email_delivery.py",
    }
)

THIS_FILE = BACKEND / "tests" / "test_login_otp_removed.py"


#: The two tests that assert the dropped `SessionOut` field and the dropped gate
#: are ABSENT have to name them to do it, the same reason this file is exempt.
GONE_EXEMPT = (
    THIS_FILE,
    BACKEND / "tests" / "test_login_context.py",
    BACKEND / "tests" / "test_auth_session_contract.py",
)


def _files(hits: list[str]) -> set[str]:
    return {hit.split(":", 1)[0].replace("\\", "/") for hit in hits}


def test_the_code_login_module_is_gone() -> None:
    assert importlib.util.find_spec("app.services.otp") is None
    assert not (BACKEND / "app" / "services" / "otp.py").exists()
    assert not (REPO / "frontend" / "components" / "otp-input.tsx").exists()


def test_nothing_names_what_was_removed() -> None:
    hits = sweep(GONE, exempt=GONE_EXEMPT)
    assert not hits, hits


def test_the_wave_b_leftovers_do_not_spread() -> None:
    hits = sweep(WAVE_B, exempt=(THIS_FILE,))
    spread = sorted(_files(hits) - WAVE_B_PENDING)
    assert not spread, (
        "A retired SMS/one-time-code name appeared in a file outside the Wave B "
        f"list. Remove it rather than listing the file: {spread}"
    )


def test_the_wave_b_list_only_shrinks() -> None:
    """An entry whose file no longer mentions the names is an exemption that
    outlived its reason. Delete the entry in the change that removed it."""
    stale = sorted(WAVE_B_PENDING - _files(sweep(WAVE_B, exempt=(THIS_FILE,))))
    assert not stale, stale


def test_the_chooser_survived_the_extraction() -> None:
    """The deletion must not have taken the live half with it."""
    from app.services import login_context

    for name in (
        "eligible_login_users", "resolve_login", "find_users", "build_contexts",
        "make_context_token", "decode_context_token", "select_context",
    ):
        assert callable(getattr(login_context, name)), name
