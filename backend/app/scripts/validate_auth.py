"""End-to-end auth validation harness (run against the RUNNING stack).

    docker compose -f infra/docker-compose.yml exec -T backend \
        python -m app.scripts.validate_auth

Exercises the real HTTP auth surface (never imports the app in-process for the
flows — it hits the API the way a browser would) and prints a PASS/FAIL line
per check plus a summary table. Exits non-zero if ANY check FAILED, so it is
usable as a CI / smoke gate.

Design notes
------------
* Seed values are DISCOVERED from the database, never hardcoded, so the script
  survives seed changes: the Owner comes from settings.owner_email, one
  representative per role is read from `users`, and the multi-context
  identifier is found by looking for an email/phone attached to 2+ eligible
  users.
* No dependency on real email/SMS delivery: in development the OTP request
  endpoint returns the code in `debug_code`, and that is what we verify with
  (the Resend key only delivers to the Owner; MSG91 SMS works but we don't
  need it here).
* Resilient: every check is isolated — one failure never aborts the run.
* Rate-limit friendly: the OTP resend/lock/request counters (Redis) for an
  identifier are cleared right before a positive-path login so back-to-back
  logins in one run don't trip the 30s resend throttle or the hourly cap. The
  attempt-limit check deliberately does NOT clear between attempts.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass, field

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

API_PREFIX = "/api/v1"
TIMEOUT = 20.0

# Org roles that must resolve to a SCOPED (non-"*") capability list.
ORG_ROLES = ["hr_manager", "recruiter", "hiring_manager", "client"]


# ── Result plumbing ──────────────────────────────────────────────────────────

@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
        self.results.append(Result(name, passed, detail))

    def check(self, name: str, fn) -> None:
        """Run `fn()` which returns (passed, detail); a raised exception is a
        FAIL, never an abort."""
        try:
            passed, detail = fn()
        except Exception as exc:  # noqa: BLE001 — a check crash is just a FAIL
            passed, detail = False, f"exception: {exc!r}"
            traceback.print_exc()
        self.record(name, passed, detail)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def summary(self) -> None:
        width = max((len(r.name) for r in self.results), default=10)
        print("\n" + "=" * (width + 14))
        print("VALIDATION SUMMARY")
        print("=" * (width + 14))
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  {status:4}  {r.name.ljust(width)}")
        total = len(self.results)
        passed = total - self.failed
        print("=" * (width + 14))
        print(f"  {passed}/{total} passed, {self.failed} failed")
        print("=" * (width + 14))


# ── Discovery (DB) ───────────────────────────────────────────────────────────

@dataclass
class Fixtures:
    owner_email: str
    role_reps: dict[str, dict]  # role -> {"email", "phone"}
    candidate_email: str | None
    multi_identifier: str | None
    multi_phone: str | None


async def _discover() -> Fixtures:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))

            # Multi-context identifier: an email OR phone attached to 2+
            # non-disabled users (the unified-login fixture).
            multi_email = (
                await s.execute(
                    text(
                        "SELECT email FROM users WHERE status <> 'disabled' "
                        "GROUP BY email HAVING count(*) > 1 ORDER BY email LIMIT 1"
                    )
                )
            ).scalar_one_or_none()
            multi_phone = (
                await s.execute(
                    text(
                        "SELECT phone FROM users WHERE phone IS NOT NULL "
                        "AND status <> 'disabled' GROUP BY phone "
                        "HAVING count(*) > 1 ORDER BY phone LIMIT 1"
                    )
                )
            ).scalar_one_or_none()

            role_reps: dict[str, dict] = {}
            for role in ORG_ROLES:
                row = (
                    await s.execute(
                        text(
                            "SELECT email, phone FROM users "
                            "WHERE role = :role AND tenant_id IS NOT NULL "
                            "AND status <> 'disabled' "
                            "AND (CAST(:multi_email AS text) IS NULL OR email <> :multi_email) "
                            "AND (CAST(:multi_phone AS text) IS NULL OR phone IS NULL OR phone <> :multi_phone) "
                            "ORDER BY (status = 'active') DESC, "
                            "(phone IS NOT NULL) DESC, created_at LIMIT 1"
                        ),
                        {
                            "role": role,
                            "multi_email": multi_email,
                            "multi_phone": multi_phone,
                        },
                    )
                ).first()
                if row is not None:
                    role_reps[role] = {"email": row[0], "phone": row[1]}

            candidate_email = (
                await s.execute(
                    text(
                        "SELECT email FROM users WHERE role = 'candidate' "
                        "AND status <> 'disabled' "
                        "AND (CAST(:multi_email AS text) IS NULL OR email <> :multi_email) "
                        "ORDER BY (status = 'active') DESC, created_at LIMIT 1"
                    ),
                    {"multi_email": multi_email},
                )
            ).scalar_one_or_none()

            return Fixtures(
                owner_email=settings.owner_email,
                role_reps=role_reps,
                candidate_email=candidate_email,
                multi_identifier=multi_email or multi_phone,
                multi_phone=multi_phone,
            )
    finally:
        await engine.dispose()


# ── Infra helpers ────────────────────────────────────────────────────────────

def _pick_base_url() -> str:
    for base in ("http://localhost:8000", "http://backend:8000"):
        try:
            r = httpx.get(base + "/health", timeout=5)
            if r.status_code == 200:
                return base
        except Exception:  # noqa: BLE001
            continue
    # Default; individual checks will surface the connection error clearly.
    return "http://localhost:8000"


def _clear_rate_limits(identifier: str) -> None:
    """Delete the OTP resend/lock/request counters for an identifier so a
    positive-path login in this run isn't throttled by a previous one."""
    if not identifier:
        return
    try:
        import redis  # sync client

        c = redis.Redis.from_url(get_settings().redis_url)
        for key in (
            f"otp:resend:{identifier}",
            f"otp:lock:{identifier}",
            f"otp:req:{identifier}",
        ):
            c.delete(key)
        c.close()
    except Exception:  # noqa: BLE001 — best effort; redis may be unreachable
        pass


def _request_otp(client: httpx.Client, identifier: str, channel: str) -> dict:
    r = client.post(
        f"{API_PREFIX}/auth/otp/request",
        json={"identifier": identifier, "channel": channel},
    )
    r.raise_for_status()
    return r.json()


def _has_cookie(client: httpx.Client, name: str = "pr_access") -> bool:
    return client.cookies.get(name) is not None


@dataclass
class Login:
    status: int
    body: dict
    client: httpx.Client
    access_token: str | None


def _login(base: str, identifier: str, phone: str | None = None) -> Login:
    """Full positive login: email OTP -> verify. Handles the client first-login
    dual-OTP (FR-1.2) by completing the phone channel when the verify response
    still lists pending channels."""
    client = httpx.Client(base_url=base, timeout=TIMEOUT)
    _clear_rate_limits(identifier)
    req = _request_otp(client, identifier, "email")
    vr = client.post(
        f"{API_PREFIX}/auth/otp/verify",
        json={"challenge_id": req["challenge_id"], "code": req["debug_code"]},
    )
    body = vr.json() if vr.content else {}
    status = vr.status_code

    # Client dual-OTP: second (SMS) channel still pending -> finish it via phone.
    if status == 200 and body.get("pending_channels") and phone:
        _clear_rate_limits(phone)
        req2 = _request_otp(client, phone, "sms")
        vr2 = client.post(
            f"{API_PREFIX}/auth/otp/verify",
            json={"challenge_id": req2["challenge_id"], "code": req2["debug_code"]},
        )
        body = vr2.json() if vr2.content else {}
        status = vr2.status_code

    return Login(status, body, client, client.cookies.get("pr_access"))


# ── Checks ───────────────────────────────────────────────────────────────────

def main() -> int:
    settings = get_settings()
    print("PickReady auth validation harness")
    print(f"  environment = {settings.environment}")

    if settings.environment != "development":
        print(
            "FATAL: this harness requires ENVIRONMENT=development so the OTP "
            "request endpoint returns debug_code. Aborting."
        )
        return 2

    base = _pick_base_url()
    print(f"  base_url    = {base}")

    fx = asyncio.run(_discover())
    print(f"  owner       = {fx.owner_email}")
    print(f"  role reps   = { {r: v['email'] for r, v in fx.role_reps.items()} }")
    print(f"  candidate   = {fx.candidate_email}")
    print(f"  multi-ctx   = {fx.multi_identifier}")
    print()

    report = Report()
    # Tokens captured for the cross-portal isolation checks.
    state: dict[str, str | None] = {"owner_token": None, "candidate_token": None}

    # 1) Owner login -> super_admin, caps == ["*"], routes to owner portal.
    def owner_check():
        lg = _login(base, fx.owner_email)
        if lg.status != 200:
            return False, f"verify status {lg.status}: {lg.body}"
        user = lg.body.get("user") or {}
        caps = lg.body.get("capabilities")
        state["owner_token"] = lg.access_token
        if user.get("role") != "super_admin":
            return False, f"role={user.get('role')} (expected super_admin)"
        if caps != ["*"]:
            return False, f"capabilities={caps} (expected ['*'])"
        # Routes to the owner portal: the owner-only admin console is reachable.
        adm = lg.client.get(f"{API_PREFIX}/admin/tenants")
        if adm.status_code != 200:
            return False, f"owner-portal /admin/tenants returned {adm.status_code}"
        return True, "role=super_admin, caps=['*'], /admin/tenants=200"

    report.check("owner_login_super_admin_star_and_portal", owner_check)

    # 2) Each client-org role -> correct role + scoped (non-"*") capabilities.
    for role in ORG_ROLES:
        def role_check(role=role):
            rep = fx.role_reps.get(role)
            if not rep:
                return False, "no representative user found in DB"
            lg = _login(base, rep["email"], phone=rep.get("phone"))
            if lg.status != 200:
                return False, f"verify status {lg.status}: {lg.body}"
            user = lg.body.get("user") or {}
            caps = lg.body.get("capabilities")
            if user.get("role") != role:
                return False, f"role={user.get('role')} (expected {role})"
            if caps is None:
                return False, f"no capabilities in response (pending={lg.body.get('pending_channels')})"
            if "*" in caps:
                return False, f"capabilities include '*' (should be scoped): {caps}"
            if not caps:
                return False, "capability list is empty"
            return True, f"role={role}, {len(caps)} scoped caps"

        report.check(f"role_login_{role}", role_check)

    # 3) Candidate -> role=candidate.
    def candidate_check():
        if not fx.candidate_email:
            return False, "no candidate user found in DB"
        lg = _login(base, fx.candidate_email)
        if lg.status != 200:
            return False, f"verify status {lg.status}: {lg.body}"
        user = lg.body.get("user") or {}
        state["candidate_token"] = lg.access_token
        if user.get("role") != "candidate":
            return False, f"role={user.get('role')} (expected candidate)"
        return True, "role=candidate"

    report.check("candidate_login", candidate_check)

    # 4) Multi-context identifier -> contexts + context_token, NO cookies;
    #    select-context then issues cookies.
    def multi_check():
        if not fx.multi_identifier:
            return False, "no multi-context identifier discovered in DB"
        client = httpx.Client(base_url=base, timeout=TIMEOUT)
        _clear_rate_limits(fx.multi_identifier)
        req = _request_otp(client, fx.multi_identifier, "email")
        vr = client.post(
            f"{API_PREFIX}/auth/otp/verify",
            json={"challenge_id": req["challenge_id"], "code": req["debug_code"]},
        )
        if vr.status_code != 200:
            return False, f"verify status {vr.status_code}: {vr.text}"
        body = vr.json()
        contexts = body.get("contexts") or []
        token = body.get("context_token")
        if len(contexts) < 2 or not token:
            return False, f"expected >=2 contexts + token, got {len(contexts)} contexts token={bool(token)}"
        if _has_cookie(client):
            return False, "cookies were set on multi-context verify (must NOT be)"
        # Finalize: select the first workspace -> cookies must now be issued.
        sel = client.post(
            f"{API_PREFIX}/auth/select-context",
            json={"context_token": token, "user_id": contexts[0]["user_id"]},
        )
        if sel.status_code != 200:
            return False, f"select-context status {sel.status_code}: {sel.text}"
        if not _has_cookie(client):
            return False, "select-context did not set cookies"
        sel_body = sel.json()
        if not sel_body.get("user"):
            return False, "select-context returned no user"
        return True, f"{len(contexts)} contexts, no cookies pre-select, cookies after select"

    report.check("multi_context_login", multi_check)

    # 5) Wrong OTP rejected + repeated wrong OTPs trigger the attempt limit.
    def wrong_and_lock_check():
        # Dedicated identifier so the resulting lockout doesn't affect others.
        rep = fx.role_reps.get("recruiter") or next(iter(fx.role_reps.values()), None)
        if not rep:
            return False, "no identifier available for lockout test"
        identifier = rep["email"]
        client = httpx.Client(base_url=base, timeout=TIMEOUT)
        _clear_rate_limits(identifier)
        req = _request_otp(client, identifier, "email")
        real = req["debug_code"]
        wrong = "000000" if real != "000000" else "111111"

        first_status = None
        locked = False
        max_attempts = settings.otp_max_attempts
        for i in range(max_attempts + 3):
            vr = client.post(
                f"{API_PREFIX}/auth/otp/verify",
                json={"challenge_id": req["challenge_id"], "code": wrong},
            )
            if i == 0:
                first_status = vr.status_code
            if vr.status_code == 429:  # OTPLocked -> attempt limit reached
                locked = True
                break
        wrong_rejected = first_status == 401
        if not wrong_rejected:
            return False, f"first wrong OTP returned {first_status} (expected 401)"
        if not locked:
            return False, "attempt limit never triggered a 429 lockout"
        return True, "first wrong=401, lockout=429 after repeated failures"

    report.check("wrong_otp_and_attempt_limit", wrong_and_lock_check)

    # 6) Cross-portal token reuse blocked (owner<->candidate audience split).
    def cross_owner_on_candidate():
        tok = state.get("owner_token")
        if not tok:
            return False, "owner token unavailable (owner login failed?)"
        r = httpx.get(
            f"{base}{API_PREFIX}/portal/applications",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=TIMEOUT,
        )
        if r.status_code in (401, 403):
            return True, f"owner token rejected on candidate portal ({r.status_code})"
        return False, f"ISOLATION BROKEN: candidate portal accepted owner token ({r.status_code})"

    report.check("cross_portal_owner_token_on_candidate_endpoint", cross_owner_on_candidate)

    def cross_candidate_on_internal():
        tok = state.get("candidate_token")
        if not tok:
            return False, "candidate token unavailable (candidate login failed?)"
        r = httpx.get(
            f"{base}{API_PREFIX}/admin/tenants",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=TIMEOUT,
        )
        if r.status_code in (401, 403):
            return True, f"candidate token rejected on internal endpoint ({r.status_code})"
        return False, f"ISOLATION BROKEN: internal endpoint accepted candidate token ({r.status_code})"

    report.check("cross_portal_candidate_token_on_internal_endpoint", cross_candidate_on_internal)

    # 7) Protected endpoint rejects an unauthenticated request.
    def unauth_check():
        r = httpx.get(f"{base}{API_PREFIX}/auth/me", timeout=TIMEOUT)
        if r.status_code == 401:
            return True, "/auth/me -> 401 without credentials"
        return False, f"/auth/me returned {r.status_code} (expected 401)"

    report.check("protected_endpoint_requires_auth", unauth_check)

    report.summary()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
