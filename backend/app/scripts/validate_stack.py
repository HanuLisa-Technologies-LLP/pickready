"""Broad production-readiness harness (run INSIDE the backend container).

    docker compose -f infra/docker-compose.yml exec -T backend \
        python -m app.scripts.validate_stack

Prints a PASS / FAIL / WARN line per check plus a summary table, and exits
non-zero if any HARD check FAILED. WARN never fails the run  -  it flags a known,
expected gap (e.g. the SMTP credentials the user hasn't added yet).

Checks
------
  1. DB reachable + Alembic migrations at head
  2. Redis reachable (ping)
  3. Celery worker responsive (control.inspect().ping())
  4. Required env present in the backend process
       - FIREBASE_SERVICE_ACCOUNT_JSON  (hard)
       - CLOUDINARY_URL                 (hard)
       - SMTP_HOST / SMTP_USER / SMTP_PASSWORD (WARN only  -  user hasn't set them yet)
  5. Seeded data sane
       - exactly one super_admin, and it is the configured Owner
       - >= 25 candidates with a resume_url set (Cloudinary seed)
       - the multi-context identifier resolves to 2+ users
  6. A matching run has persisted a 4-parameter breakdown for >= 1 job
  7. PRD v1.0 alignment
       - >= 1 PUBLISHED job with a resolvable public link/id (`/apply/{job_uuid}`)
       - the flattened permission matrix: hr_manager / recruiter / hiring_manager
         all resolve to the SAME allowed-capability set (direct-publish, shared
         pool  -  no per-role divergence)
       - >= 1 candidate can be resolved for open application (published jobs exist,
         so the public register→apply flow has a target)

Every check is isolated  -  one failure (or crash) never aborts the run. Checks
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
        print(f"[{status}] {name}" + (f"  -  {detail}" if detail else ""))
        self.results.append(Result(name, status, detail))

    def check(self, name: str, fn) -> None:
        """`fn()` returns (status, detail); a raised exception is a hard FAIL."""
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001  -  a check crash is just a FAIL
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

            # 1b) Migrations at head  -  compare alembic_version to the script head.
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
                    return FAIL, f"DB at {current}, script head is {head}, run alembic upgrade head"
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

                # COUNTED, NOT SAMPLED.
                #
                # This used to read `LIMIT 1` and report on whichever row came
                # back. A sample of one is not evidence about a population in
                # either direction: it reported WARN because ONE legacy row out
                # of 1037 was short a key, and it would just as readily have
                # reported PASS while half the table was malformed. That is the
                # same shape as every other defect this repo has been bitten by
                # -- a check whose green means less than it appears to.
                #
                # `?` is the jsonb key-exists operator, so this is an index-free
                # but single-pass scan over a table of a size where that is
                # cheap, and it names the actual number.
                expected = ("skills_match", "experience_relevance", "role_alignment",
                            "education_fit", "overall")
                shortfalls = []
                for key in expected:
                    bad = (
                        await s.execute(
                            text(
                                "SELECT count(*) FROM job_candidate_links "
                                "WHERE match_breakdown_json IS NOT NULL "
                                "  AND NOT (match_breakdown_json ? :key)"
                            ),
                            {"key": key},
                        )
                    ).scalar_one()
                    if bad:
                        shortfalls.append(f"{key} missing on {bad}")
                if shortfalls:
                    return WARN, (
                        f"{links} links across {jobs} job(s); "
                        + ", ".join(shortfalls)
                    )
                return PASS, (
                    f"{links} scored links across {jobs} job(s); every breakdown "
                    "carries all five keys"
                )

            await _acheck(report, "matching_breakdowns_persisted", breakdowns_persisted)

            # 7a) At least one PUBLISHED job with a resolvable public link/id.
            #     PRD v1.0 replaces the multi-level approval FSM with direct
            #     publish + a public job link `/apply/{job_uuid}`. "Published" is
            #     discovered tolerantly so this survives the rename: a job counts
            #     as publicly linkable if status is a published/terminal value OR
            #     it carries a ratified_at marker (the pre-rename terminal state).
            #     The public link/id is the job's own UUID `id`.
            async def published_job_public_link():
                row = (
                    await s.execute(
                        text(
                            "SELECT id, status FROM jobs "
                            "WHERE lower(CAST(status AS text)) IN "
                            "        ('published', 'ratified', 'open', 'live') "
                            "   OR ratified_at IS NOT NULL "
                            "ORDER BY created_at DESC LIMIT 1"
                        )
                    )
                ).first()
                if row is None:
                    return FAIL, (
                        "no published/terminal job found, the public job link "
                        "`/apply/{job_uuid}` has no target (publish a job / seed one)"
                    )
                job_id, job_status = row[0], row[1]
                if not job_id:
                    return FAIL, f"published job (status={job_status}) has no id for a public link"
                return PASS, (
                    f"published job {job_id} (status={job_status}) "
                    f"→ public link /apply/{job_id}"
                )

            await _acheck(report, "published_job_has_public_link", published_job_public_link)

            # 7b) Flattened permission matrix (PRD v1.0 §4 FINAL): the three staff
            #     roles are EQUAL  -  hr_manager, recruiter, hiring_manager must
            #     resolve to the SAME allowed-capability set. Query the global
            #     template rows (tenant_id IS NULL) and compare the allowed sets.
            async def flat_permission_matrix():
                staff_roles = ("hr_manager", "recruiter", "hiring_manager")
                rows = (
                    await s.execute(
                        text(
                            "SELECT role, capability FROM role_permissions "
                            "WHERE tenant_id IS NULL AND allowed IS TRUE "
                            "  AND role IN ('hr_manager','recruiter','hiring_manager')"
                        )
                    )
                ).all()
                caps_by_role: dict[str, set[str]] = {r: set() for r in staff_roles}
                for role, cap in rows:
                    caps_by_role.setdefault(role, set()).add(cap)
                present = [r for r in staff_roles if caps_by_role.get(r)]
                if len(present) < len(staff_roles):
                    missing = [r for r in staff_roles if not caps_by_role.get(r)]
                    return FAIL, (
                        f"no allowed capabilities for role(s) {missing} in the global "
                        "template, cannot verify the flattened matrix"
                    )
                sets = [frozenset(caps_by_role[r]) for r in staff_roles]
                if sets[0] == sets[1] == sets[2]:
                    return PASS, (
                        f"all 3 staff roles share an identical {len(sets[0])}-capability "
                        "set (flat matrix)"
                    )
                # Report the pairwise differences so the divergence is actionable.
                union = set().union(*sets)
                diffs = {
                    r: sorted(union - caps_by_role[r]) for r in staff_roles
                    if union - caps_by_role[r]
                }
                return FAIL, (
                    "staff roles resolve to DIFFERENT capability sets (matrix not "
                    f"flattened), per-role missing vs union: {diffs}"
                )

            await _acheck(report, "flat_staff_permission_matrix", flat_permission_matrix)

            # 7c) A candidate can be resolved for open application. The PRD v1.0
            #     open flow is public register → 40-aspect questionnaire → resume
            #     (upload OR reuse) → apply against a published job. That flow needs
            #     BOTH a published job (a target) and at least one candidate row.
            async def candidate_for_open_application():
                jobs = (
                    await s.execute(
                        text(
                            "SELECT count(*) FROM jobs "
                            "WHERE lower(CAST(status AS text)) IN "
                            "        ('published', 'ratified', 'open', 'live') "
                            "   OR ratified_at IS NOT NULL"
                        )
                    )
                ).scalar_one()
                cands = (
                    await s.execute(text("SELECT count(*) FROM candidates"))
                ).scalar_one()
                if jobs < 1:
                    return FAIL, "no published job, nothing for an open application to target"
                if cands < 1:
                    return FAIL, "no candidate rows, open application flow has no subject"
                return PASS, f"{cands} candidate(s) resolvable against {jobs} published job(s)"

            await _acheck(report, "candidate_resolvable_for_open_application", candidate_for_open_application)
    finally:
        await engine.dispose()


async def _acheck(report: Report, name: str, coro_fn) -> None:
    """Async analogue of Report.check  -  isolates each DB check."""
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
    except Exception:  # noqa: BLE001  -  fall back to WARN in the caller
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
    """Hard-required backend env present; SMTP credentials are WARN-only."""
    settings = get_settings()
    hard = {
        "FIREBASE_SERVICE_ACCOUNT_JSON": settings.firebase_service_account_json,
        "S3_BUCKET": settings.s3_bucket,
        # The two model credentials, one per tier. Without either, every task
        # on that tier degrades to its deterministic fallback -- which is
        # correct behaviour and an unacceptable steady state, so both are
        # HARD here rather than warnings. They are listed separately because
        # one present and one absent is the state that otherwise reads as
        # healthy while half the product is degraded.
        "OPENAI_GPT_TERRA": settings.openai_gpt_terra,
        "OPENAI_GPT_LUNA": settings.openai_gpt_luna,
    }
    missing_hard = [k for k, v in hard.items() if not v]
    if missing_hard:
        return FAIL, f"missing required env: {', '.join(missing_hard)}"
    return PASS, ", ".join(hard) + " present"


def _embedding_check():
    """Report the embedding model HONESTLY, including when it is the fallback.

    Without `VOYAGE_CONTEXT_4` the platform still runs: `services/embeddings`
    returns deterministic pseudo-random vectors so local dev and CI work end to
    end. Those vectors carry no semantic meaning, so every ranking built on them
    is arbitrary-but-stable. A stack validator that reported "embeddings: ok"
    in that state would be the exact failure this project has already been
    burned by -- a green tick standing in for a thing that does not work.
    """
    from app.services import embeddings

    if embeddings.is_semantic():
        return PASS, f"{embeddings.EMBEDDING_MODEL} at {embeddings.EMBEDDING_DIM} dims"
    return (
        WARN,
        "VOYAGE_CONTEXT_4 unset: embeddings are the DETERMINISTIC DEV FALLBACK. "
        "Matching, AI Reach and every RAG surface will return a stable but "
        "MEANINGLESS ordering. Never ship this.",
    )


def _smtp_env_check():
    """Outbound email now goes over SMTP from the backend (PRD v1.0, replaces the
    Gmail SMTP). Missing credentials are warning-only so
    dev must still boot without them (missing keys warn, never hard-crash)."""
    settings = get_settings()
    required = {
        "SMTP_HOST": settings.smtp_host,
        "SMTP_USER": settings.smtp_user,
        "SMTP_PASSWORD": settings.smtp_password,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return WARN, (
            f"{', '.join(missing)} not set, email delivery disabled (known gap; "
            "configure Gmail SMTP with a Google app password)"
        )
    return PASS, f"SMTP configured (host={settings.smtp_host}, port={settings.smtp_port})"


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
    print("ReadyPick stack readiness harness")
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
    report.check("smtp_credentials_present", _smtp_env_check)
    report.check("embedding_model_configured", _embedding_check)

    # DB + seed sanity.
    try:
        asyncio.run(_run_db_checks(report))
    except Exception as exc:  # noqa: BLE001  -  never let DB setup abort the summary
        report.record("db_checks_bootstrap", FAIL, f"could not run DB checks: {exc!r}")
        traceback.print_exc()

    report.summary()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
