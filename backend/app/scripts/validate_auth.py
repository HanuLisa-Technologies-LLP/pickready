"""Auth surface validation against the RUNNING stack.

    docker compose -f infra/docker-compose.yml exec -T backend \
        python -m app.scripts.validate_auth

Hits the real HTTP auth surface the way a browser would and prints a PASS/FAIL
line per check plus a summary table. Exits non-zero if ANY check failed, so it
is usable as a smoke gate.

WHAT IT CHECKS, AND WHAT IT NO LONGER CARRIES
---------------------------------------------
Firebase owns identity (claude.md rule 2), so a live sign-in needs a real
Firebase credential this script never holds. What it CAN prove without one is
the contract around that exchange: the retired one-time-code routes are not
mounted, a protected route refuses an anonymous caller, and the session route
rejects a bogus or empty token. `scripts/mint-smoke-token.py` and
`scripts/smoke-test.sh` are the authenticated live probes.

Until 2026-09-24 this file also carried a full code-login walkthrough (seed
discovery, a Redis counter reset, per-role logins), unreachable behind an early
return since the Firebase cut-over. It exercised routes that no longer exist
and was deleted rather than left to be mistaken for a live check.
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings

API_PREFIX = "/api/v1"
TIMEOUT = 20.0

#: The retired one-time-code routes. Asserted ABSENT: a 404 from each.
RETIRED_ROUTES = ("/auth/otp/request", "/auth/otp/verify", "/auth/register-candidate")


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
        print(f"[{status}] {name}" + (f"  -  {detail}" if detail else ""))
        self.results.append(Result(name, passed, detail))

    def check(self, name: str, fn) -> None:
        """Run `fn()` which returns (passed, detail); a raised exception is a
        FAIL, printed with its traceback, never an abort."""
        try:
            passed, detail = fn()
        except Exception as exc:  # noqa: BLE001  -  a check crash is recorded as a FAIL
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


def _pick_base_url() -> str:
    for base in ("http://localhost:8000", "http://backend:8000"):
        try:
            r = httpx.get(base + "/health", timeout=5)
        except httpx.HTTPError as exc:
            print(f"  {base} unreachable ({type(exc).__name__})")
            continue
        if r.status_code == 200:
            return base
    # Default; every check below surfaces the connection error as a FAIL.
    return "http://localhost:8000"


def _report(base: str) -> int:
    report = Report()

    def retired_routes():
        results = {
            path: httpx.post(f"{base}{API_PREFIX}{path}", json={}, timeout=TIMEOUT).status_code
            for path in RETIRED_ROUTES
        }
        if all(code == 404 for code in results.values()):
            return True, "the retired one-time-code routes are not mounted"
        return False, f"retired auth route status codes: {results}"

    report.check("[firebase] legacy_code_routes_removed", retired_routes)

    def protected_endpoint():
        response = httpx.get(f"{base}{API_PREFIX}/auth/me", timeout=TIMEOUT)
        if response.status_code == 401:
            return True, "/auth/me -> 401 without credentials"
        return False, f"/auth/me returned {response.status_code}"

    report.check("[firebase] protected_endpoint_requires_auth", protected_endpoint)

    def bogus_token():
        response = httpx.post(
            f"{base}{API_PREFIX}/auth/firebase/session",
            json={"id_token": "not-a-real-firebase-id-token-xxxxxxxx"},
            timeout=TIMEOUT,
        )
        if response.status_code == 401:
            return True, "bogus Firebase token rejected with 401"
        return False, f"expected 401, got {response.status_code}"

    report.check("[firebase] session_route_rejects_bogus_token", bogus_token)

    def schema_validation():
        response = httpx.post(
            f"{base}{API_PREFIX}/auth/firebase/session",
            json={"id_token": ""},
            timeout=TIMEOUT,
        )
        if response.status_code == 422:
            return True, "empty id_token rejected with 422"
        return False, f"expected 422, got {response.status_code}"

    report.check("[firebase] session_route_validates_token", schema_validation)
    report.summary()
    return 1 if report.failed else 0


def main() -> int:
    settings = get_settings()
    print("Vivekium auth validation")
    print(f"  environment = {settings.environment}")
    base = _pick_base_url()
    print(f"  base_url    = {base}")
    return _report(base)


if __name__ == "__main__":
    sys.exit(main())
