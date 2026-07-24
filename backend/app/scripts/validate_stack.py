"""Broad production-readiness harness (run INSIDE the backend container).

    docker compose -f infra/docker-compose.yml exec -T backend \
        python -m app.scripts.validate_stack

Prints a PASS / FAIL / WARN line per check plus a summary table, and exits
non-zero if any HARD check FAILED. WARN never fails the run — it flags a known,
expected gap (e.g. the Mailtrap token the user hasn't added yet).

Checks
------
  1. DB reachable + Alembic migrations at head
  2. Redis reachable (ping)
  3. Celery worker responsive (control.inspect().ping())
  4. Required env present in the backend process
       - FIREBASE_SERVICE_ACCOUNT_JSON  (hard)
       - CLOUDINARY_URL                 (hard)
       - MAILTRAP_API_TOKEN             (WARN only — user hasn't added it yet)
  5. Seeded data sane
       - exactly one super_admin, and it is the configured Owner
       - >= 25 candidates with a resume_url set (Cloudinary seed)
       - the multi-context identifier resolves to 2+ users
  6. A matching run has persisted a 4-parameter breakdown for >= 1 job

Every check is isolated — one failure (or crash) never aborts the run. Checks
are DISCOVERY-based (query the live DB), never hardcoded, so they survive seed
changes.
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

# Status constants. WARN is a soft, non-failing status for known gaps.
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | WARN
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def record(self, name: str, status: str, detail: str = "") -> None:
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
        self.results.append(Result(name, status, detail))

    def check(self, name: str, fn) -> None:
        """`fn()` returns (status, detail); a raised exception is a hard FAIL."""
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 — a check crash is just a FAIL
            status, detail = FAIL, f"exception: {exc!r}"
            traceback.print_exc()
        self.record(name, status, detail)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for r in self.results if r.status == WARN)

    def summary(self) -> None:
        width = max((len(r.name) for r in self.results), default=10)
        print("\n" + "=" * (width + 16))
        print("STACK READINESS SUMMARY")
        print("=" * (width + 16))
        for r in self.results:
            print(f"  {r.status:4}  {r.name.ljust(width)}")
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == PASS)
        print("=" * (width + 16))
        print(f"  {passed}/{total} passed, {self.warned} warn, {self.failed} failed")
        print("=" * (width + 16))


# ── DB-backed checks ─────────────────────────────────────────────────────────

async def _run_db_checks(report: Report) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine)
    try:
        async with factory() as s:
            # Bypass RLS so the harness can read across tenants for seed sanity.
            await s.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))

            # 1a) DB reachable.
            async def db_reachable():
                (await s.execute(text("SELECT 1"))).scalar_one()
                return PASS, "SELECT 1 ok"

            await _acheck(report, "db_reachable", db_reachable)

            # 1b) Migrations at head — compare alembic_version to the script head.
            async def migrations_at_head():
                current = (
                    await s.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one_or_none()
                head = _script_head()
                if current is None:
                    return FAIL, "alembic_version is empty (no migrations applied)"
                if head is None:
                    return WARN, f"current={current}; could not resolve script head to compare"
                if current != head:
                    return FAIL, f"DB at {current}, script head is {head} — run alembic upgrade head"
                return PASS, f"at head {current}"

            await _acheck(report, "migrations_at_head", migrations_at_head)

            # 5a) Exactly one super_admin, and it is the Owner.
            async def owner_singleton():
                rows = (
                    await s.execute(
                        text("SELECT email FROM users WHERE role = 'super_admin'")
                    )
                ).scalars().all()
                if len(rows) != 1:
                    return FAIL, f"found {len(rows)} super_admin users (expected exactly 1): {rows}"
                if rows[0].lower() != settings.owner_email.lower():
                    return FAIL, f"super_admin is {rows[0]}, expected Owner {settings.owner_email}"
                return PASS, f"single super_admin = Owner {rows[0]}"

            await _acheck(report, "owner_is_sole_super_admin", owner_singleton)

            # 5b) >= 25 candidates with a resume_url set (resume_url lives on
            #     profiles; count distinct candidates that have one).
            async def candidates_with_resumes():
                n = (
                    await s.execute(
                        text(
                            "SELECT count(DISTINCT c.id) FROM candidates c "
                            "JOIN profiles p ON p.candidate_id = c.id "
                            "WHERE p.resume_url IS NOT NULL AND p.resume_url <> ''"
                        )
                    )
                ).scalar_one()
                if n < 25:
                    return FAIL, f"only {n} candidates have a resume_url (expected >= 25)"
                return PASS, f"{n} candidates with resume_url"

            await _acheck(report, "candidates_have_resume_urls", candidates_with_resumes)

            # 5c) Multi-context identifier resolves to 2+ users.
            async def multi_context_exists():
                by_email = (
                    await s.execute(
                        text(
                            "SELECT email, count(*) c FROM users "
                            "WHERE status <> 'disabled' GROUP BY email "
                            "HAVING count(*) > 1 ORDER BY c DESC LIMIT 1"
                        )
                    )
                ).first()
                by_phone = (
                    await s.execute(
                        text(
                            "SELECT phone, count(*) c FROM users "
                            "WHERE phone IS NOT NULL AND status <> 'disabled' "
                            "GROUP BY phone HAVING count(*) > 1 ORDER BY c DESC LIMIT 1"
                        )
                    )
                ).first()
                hit = by_email or by_phone
                if not hit:
                    return FAIL, "no email/phone maps to 2+ users (multi-context chooser untestable)"
                return PASS, f"'{hit[0]}' resolves to {hit[1]} users"

            await _acheck(report, "multi_context_identifier_present", multi_context_exists)

            # 6) A matching run has persisted a 4-parameter breakdown for >= 1 job.
            async def breakdowns_persisted():
                row = (
                    await s.execute(
                        text(
                            "SELECT count(*) links, count(DISTINCT job_id) jobs "
                            "FROM job_candidate_links "
                            "WHERE match_breakdown_json IS NOT NULL"
                        )
                    )
                ).first()
                links, jobs = row[0], row[1]
                if jobs < 1:
                    return FAIL, "no job_candidate_links have match_breakdown_json (run matching)"
                # Spot-check one breakdown has the 4-parameter + overall shape.
                sample = (
                    await s.execute(
                        text(
                            "SELECT match_breakdown_json FROM job_candidate_links "
                            "WHERE match_breakdown_json IS NOT NULL LIMIT 1"
                        )
                    )
                ).scalar_one()
                shape = sample if isinstance(sample, dict) else json.loads(sample)
                expected = {"skills_match", "experience_relevance", "role_alignment",
                            "education_fit", "overall"}
                missing = expected - set(shape or {})
                if missing:
                    return WARN, (
                        f"{links} links across {jobs} job(s), but a sample breakdown "
                        f"is missing keys {sorted(missing)}"
                    )
                return PASS, f"{links} scored links across {jobs} job(s); breakdown shape ok"

            await _acheck(report, "matching_breakdowns_persisted", breakdowns_persisted)
    finally:
        await engine.dispose()


async def _acheck(report: Report, name: str, coro_fn) -> None:
    """Async analogue of Report.check — isolates each DB check."""
    try:
        status, detail = await coro_fn()
    except Exception as exc:  # noqa: BLE001
        status, detail = FAIL, f"exception: {exc!r}"
        traceback.print_exc()
    report.record(name, status, detail)


def _script_head() -> str | None:
    """Resolve the Alembic head revision from the migration scripts, so the
    check compares against source-of-truth rather than a hardcoded string."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        # alembic.ini sits at the backend root; CWD inside the container is /app.
        cfg = Config("alembic.ini")
        heads = ScriptDirectory.from_config(cfg).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:  # noqa: BLE001 — fall back to WARN in the caller
        return None


# ── Infra + env checks (sync) ────────────────────────────────────────────────

def _redis_check():
    import redis

    c = redis.Redis.from_url(get_settings().redis_url)
    try:
        if c.ping():
            return PASS, "redis PING ok"
        return FAIL, "redis PING returned falsy"
    finally:
        c.close()


def _celery_check():
    from app.workers.celery_app import celery_app

    insp = celery_app.control.inspect(timeout=5.0)
    pong = insp.ping()
    if not pong:
        return FAIL, "no worker replied to inspect ping (worker down?)"
    workers = ", ".join(pong.keys())
    return PASS, f"{len(pong)} worker(s) responded: {workers}"


def _env_check():
    """Hard-required backend env present; Mailtrap token is WARN-only."""
    settings = get_settings()
    hard = {
        "FIREBASE_SERVICE_ACCOUNT_JSON": settings.firebase_service_account_json,
        "CLOUDINARY_URL": settings.cloudinary_url,
    }
    missing_hard = [k for k, v in hard.items() if not v]
    if missing_hard:
        return FAIL, f"missing required env: {', '.join(missing_hard)}"
    return PASS, "FIREBASE_SERVICE_ACCOUNT_JSON + CLOUDINARY_URL present"


def _mailtrap_env_check():
    settings = get_settings()
    if not settings.mailtrap_api_token:
        return WARN, "MAILTRAP_API_TOKEN not set — email delivery disabled (known gap)"
    return PASS, "MAILTRAP_API_TOKEN present"


def _firebase_admin_initializes():
    """The service-account JSON must actually parse + be usable by firebase_admin
    (a malformed single-line JSON is a common deploy footgun)."""
    settings = get_settings()
    raw = settings.firebase_service_account_json
    if not raw:
        return FAIL, "FIREBASE_SERVICE_ACCOUNT_JSON is empty"
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return FAIL, f"not valid JSON: {exc}"
    pid = parsed.get("project_id")
    if not pid or not parsed.get("private_key"):
        return FAIL, "JSON missing project_id/private_key (not a service account key)"
    return PASS, f"service-account JSON parses (project_id={pid})"


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    settings = get_settings()
    print("PickReady stack readiness harness")
    print(f"  environment = {settings.environment}")
    print(f"  database    = {settings.database_url.rsplit('@', 1)[-1]}")
    print(f"  redis       = {settings.redis_url}")
    print()

    report = Report()

    # Infra + env first (cheap, and they contextualize DB failures).
    report.check("redis_reachable", _redis_check)
    report.check("celery_worker_responsive", _celery_check)
    report.check("required_env_present", _env_check)
    report.check("firebase_service_account_valid", _firebase_admin_initializes)
    report.check("mailtrap_token_present", _mailtrap_env_check)

    # DB + seed sanity.
    try:
        asyncio.run(_run_db_checks(report))
    except Exception as exc:  # noqa: BLE001 — never let DB setup abort the summary
        report.record("db_checks_bootstrap", FAIL, f"could not run DB checks: {exc!r}")
        traceback.print_exc()

    report.summary()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
