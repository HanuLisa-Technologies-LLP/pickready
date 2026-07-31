"""Idempotent dev seed  -  run with `python -m app.scripts.seed_dev_data`.

Seeds:
- global role_permissions template rows (tenant_id NULL) from
  DEFAULT_PERMISSION_MATRIX  -  every capability x role, default False
- the platform Owner (manjuchro@gmail.com)  -  the SOLE super_admin (rev 2)
- demo tenant "Acme Corp" with client, 2 hiring managers, 1 HR, 1 recruiter
  (all client-org members  -  staff emails live on the tenant's domain)
- company page + approval_levels_config (recommended level inactive)
- llm_provider_keys rows encrypted from settings (empties skipped)
- default email templates for the demo tenant
- 3 demo candidates with profiles (2 consenting to the Databank) + embeddings
- 1 draft job + 1 ratified job

Safe to run repeatedly  -  every entity is looked up by its natural key first.
The session runs with app.bypass_rls='on' since seeding is a trusted,
cross-tenant operation and the tables FORCE row-level security.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import encrypt_secret
from app.models import (
    Candidate,
    Company,
    EmailTemplate,
    HiringManager,
    Job,
    JobStatus,
    LLMProviderKey,
    Profile,
    Role,
    RolePermission,
    Tenant,
    User,
    UserStatus,
)
from app.scripts.seed_resumes import seed_resume_corpus
from app.services.capabilities import ALL_CAPABILITIES, DEFAULT_PERMISSION_MATRIX
from app.services.email_render import DEFAULT_TEMPLATES
from app.services.embeddings import embed

DEMO_TENANT_NAME = "Acme Corp"
DEMO_TENANT_DOMAIN = "acme.example.com"

# Second tenant so the "choose your workspace" flow and cross-tenant data
# isolation are testable. `.test` is a reserved non-deliverable TLD.
TECHSTART_TENANT_NAME = "TechStart Inc."
TECHSTART_TENANT_DOMAIN = "techstart.test"

# The multi-context identifier: ONE email (and one phone) attached to users in
# two different contexts  -  a hiring_manager at Acme AND a recruiter at
# TechStart. This is the ONLY fixture that exercises the multi-role
# "choose your workspace" screen (contract rev 2, /auth/select-context).
MULTI_CONTEXT_EMAIL = "multi.role@pickready.test"
MULTI_CONTEXT_PHONE = "9000000030"

# NOTE ON EMAIL DELIVERY (dev/test): do NOT use @example.com for any fixture
# you expect to receive mail  -  Resend rejects example.com with a 422. Use the
# reserved @acme.test / @techstart.test / @pickready.test domains for
# non-deliverable fixtures. Also: in THIS environment the Resend key is
# restricted with no verified sending domain, so outbound email can ONLY be
# delivered to manjuchro@gmail.com regardless of the recipient here.

_DEMO_RESUMES = [
    (
        "Priya Sharma",
        "priya.sharma@example.com",
        True,
        "Priya Sharma, Senior Backend Engineer. 8 years of experience building "
        "distributed systems in Python (FastAPI, Django), PostgreSQL, Redis, "
        "Celery, and AWS. Led a team of 5 engineers at a fintech startup. "
        "B.Tech in Computer Science, IIT Madras, 2016. Certifications: AWS "
        "Solutions Architect Associate.",
    ),
    (
        "Arjun Mehta",
        "arjun.mehta@example.com",
        True,
        "Arjun Mehta, Full-Stack Developer. 5 years across React, TypeScript, "
        "Next.js, Node.js, and Python. Built multi-tenant SaaS dashboards and "
        "design systems with Tailwind and shadcn/ui. B.E. in Information "
        "Technology, Pune University, 2019.",
    ),
    (
        "Sneha Reddy",
        "sneha.reddy@example.com",
        False,
        "Sneha Reddy, Data Engineer. 6 years with Spark, Airflow, dbt, "
        "PostgreSQL, and Kafka; ML feature pipelines with pgvector and "
        "embedding models. M.Sc. in Data Science, BITS Pilani, 2018.",
    ),
]


async def _get_or_create_user(
    session: AsyncSession,
    email: str,
    role: Role,
    tenant_id: uuid.UUID | None,
    full_name: str,
    phone: str | None = None,
) -> User:
    user = (
        await session.execute(
            select(User).where(
                User.email == email, User.role == role, User.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            role=role,
            tenant_id=tenant_id,
            full_name=full_name,
            phone=phone,
            status=UserStatus.active,
        )
        session.add(user)
        await session.flush()
        print(f"  + user {email} ({role.value})")
    elif phone and user.phone != phone:
        user.phone = phone
        print(f"  ~ user {email}: phone updated")
    return user


async def _ensure_hiring_manager(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Idempotently mirror a hiring_manager User into the hiring_managers table
    (approval assignments key off it, mirroring the /companies/me/staff path)."""
    exists = (
        await session.execute(
            select(HiringManager).where(
                HiringManager.tenant_id == tenant_id,
                HiringManager.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(HiringManager(tenant_id=tenant_id, user_id=user_id))


async def _seed_permission_template(session: AsyncSession) -> None:
    """Global template rows (tenant_id NULL): every capability per role,
    allowed only where DEFAULT_PERMISSION_MATRIX says so.

    Idempotent AND reconciling: inserts missing (role, capability) rows and
    UPDATES the `allowed` flag on existing rows to match the current matrix.
    The flat-staff-model change (PRD v1.0 §4) flips many defaults (e.g.
    recruiter now has CREATE_JOB), so a plain insert-if-missing would leave
    stale template rows  -  re-seeding must bring them into line."""
    existing = {
        (r.role, r.capability): r
        for r in (
            await session.execute(
                select(RolePermission).where(RolePermission.tenant_id.is_(None))
            )
        ).scalars()
    }
    added = 0
    updated = 0
    for role, grants in DEFAULT_PERMISSION_MATRIX.items():
        for capability in ALL_CAPABILITIES:
            allowed = grants.get(capability, False)
            row = existing.get((role, capability))
            if row is None:
                session.add(
                    RolePermission(
                        tenant_id=None,
                        role=role,
                        capability=capability,
                        allowed=allowed,
                    )
                )
                added += 1
            elif row.allowed != allowed:
                row.allowed = allowed
                updated += 1
    if added:
        print(f"  + {added} global role_permission template rows")
    if updated:
        print(f"  ~ {updated} global role_permission rows reconciled to matrix")


async def _seed_llm_keys(session: AsyncSession) -> None:
    settings = get_settings()
    if not settings.llm_key_encryption_secret:
        print("  ! LLM_KEY_ENCRYPTION_SECRET unset  -  skipping llm_provider_keys seed")
        return
    existing = (await session.execute(select(LLMProviderKey))).scalars().first()
    if existing is not None:
        return  # already seeded
    # role_hint is informational (the router's chains are provider-ordered per
    # ESD §8.4): Groq keys lead the rerank chain, Gemini leads extraction.
    key_specs = [
        ("groq", "rerank", settings.groq_api_key_1),
        ("groq", "rerank", settings.groq_api_key_2),
        ("groq", "rerank", settings.groq_api_key_3),
        ("gemini", "extraction", settings.gemini_api_key_1),
        ("gemini", "extraction", settings.gemini_api_key_2),
        ("gemini", "extraction", settings.gemini_api_key_3),
        ("openrouter", "extraction", settings.openrouter_api_key_1),
        ("openrouter", "extraction", settings.openrouter_api_key_2),
        ("openrouter", "extraction", settings.openrouter_api_key_3),
    ]
    added = 0
    priority = 0
    last_provider = None
    for provider, role_hint, raw_key in key_specs:
        if provider != last_provider:
            priority = 0
            last_provider = provider
        if not raw_key:
            continue  # skip empties
        session.add(
            LLMProviderKey(
                provider=provider,
                role_hint=role_hint,
                key_encrypted=encrypt_secret(raw_key),
                priority=priority,
                healthy=True,
            )
        )
        priority += 1
        added += 1
    if added:
        print(f"  + {added} llm_provider_keys rows (encrypted)")


async def _seed_email_templates(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for name, (subject, body) in DEFAULT_TEMPLATES.items():
        exists = (
            await session.execute(
                select(EmailTemplate).where(
                    EmailTemplate.tenant_id == tenant_id, EmailTemplate.name == name
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                EmailTemplate(
                    tenant_id=tenant_id, name=name, subject=subject, body=body, version=1
                )
            )
            print(f"  + email template '{name}'")


async def _seed_candidates(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for full_name, email, consent, resume_text in _DEMO_RESUMES:
        candidate = (
            await session.execute(select(Candidate).where(Candidate.email == email))
        ).scalar_one_or_none()
        if candidate is not None:
            continue
        candidate = Candidate(
            tenant_id=None,  # shared Databank row
            full_name=full_name,
            email=email,
            city="Chennai",
            consent_databank=consent,
        )
        session.add(candidate)
        await session.flush()
        # embed() uses the deterministic dev fallback when BGE_M3_ENDPOINT is
        # unset  -  no GPU service needed to seed locally.
        embedding = (await embed([resume_text]))[0]
        session.add(
            Profile(
                candidate_id=candidate.id,
                source_tenant_id=tenant_id,
                resume_text=resume_text,
                embedding=embedding,
                parsed_fields_json=None,  # left for pickready.parse_resume demos
            )
        )
        print(f"  + candidate {full_name} (databank consent={consent})")


async def _seed_jobs(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID
) -> None:
    specs = [
        (
            "Senior Backend Engineer",
            JobStatus.draft,
            {
                "role": "Own backend services for the flagship product",
                "responsibilities": ["Design APIs", "Operate Postgres", "Mentor juniors"],
                "accountabilities": ["Service uptime", "Delivery velocity"],
                "education": "B.Tech/B.E. in CS or equivalent",
                "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Celery"],
                "experience_years": 6,
                "reporting_to": "Engineering Manager",
                "reportees": 0,
            },
        ),
        (
            "Full-Stack Developer",
            JobStatus.ratified,
            {
                "role": "Build customer-facing dashboards end to end",
                "responsibilities": ["Ship UI features", "Own the design system"],
                "accountabilities": ["Frontend quality"],
                "education": "Any engineering degree",
                "skills": ["React", "TypeScript", "Next.js", "Tailwind"],
                "experience_years": 4,
                "reporting_to": "Product Lead",
                "reportees": 0,
            },
        ),
    ]
    for title, status, jd in specs:
        exists = (
            await session.execute(
                select(Job).where(Job.tenant_id == tenant_id, Job.title == title)
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                Job(
                    tenant_id=tenant_id,
                    title=title,
                    department="Engineering",
                    level="Senior" if "Senior" in title else "Mid",
                    jd_json=jd,
                    status=status,
                    requirement_period="Q3 2026",
                    created_by=created_by,
                    ratified_at=(
                        datetime.now(timezone.utc) if status == JobStatus.ratified else None
                    ),
                )
            )
            print(f"  + job '{title}' ({status.value})")


async def seed() -> None:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Trusted cross-tenant seeding path  -  tables FORCE RLS, so opt into
            # the bypass clause the policies define (session-level setting).
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )

            print("Seeding global data...")
            await _seed_permission_template(session)
            # Platform Owner  -  the ONLY super_admin account permitted (rev 2:
            # settings.owner_email; the API layer rejects any other identity
            # holding the owner role). Any email domain (incl. gmail) and any
            # mobile number are permitted identifiers; auth only requires the
            # user row to exist.
            await _get_or_create_user(
                session, "manjuchro@gmail.com", Role.super_admin, None,
                "Manju (Platform Admin)", phone="9652802233",
            )
            await _seed_llm_keys(session)

            print("Seeding demo tenant...")
            tenant = (
                await session.execute(
                    select(Tenant).where(Tenant.domain == DEMO_TENANT_DOMAIN)
                )
            ).scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(name=DEMO_TENANT_NAME, domain=DEMO_TENANT_DOMAIN)
                session.add(tenant)
                await session.flush()
                print(f"  + tenant {DEMO_TENANT_NAME} ({DEMO_TENANT_DOMAIN})")

            # Client carries a mobile so first-login dual-OTP (FR-1.2) can
            # complete in dev; onboarding (Image 1 flow) always captures both.
            client = await _get_or_create_user(
                session, "client@acme.example.com", Role.client, tenant.id,
                "Acme Client", phone="9000000001",
            )
            # Each Acme member gets a DISTINCT phone so SMS OTP is testable.
            hm1 = await _get_or_create_user(
                session, "hm1@acme.example.com", Role.hiring_manager, tenant.id,
                "HM One", phone="9000000004",
            )
            hm2 = await _get_or_create_user(
                session, "hm2@acme.example.com", Role.hiring_manager, tenant.id, "HM Two"
            )
            # HR Manager / Recruiter are client-org staff (rev 2 role model)  - 
            # their emails live on the tenant's domain, never hanulisa.com.
            await _get_or_create_user(
                session, "hr1@acme.example.com", Role.hr_manager, tenant.id,
                "HR One", phone="9000000002",
            )
            await _get_or_create_user(
                session, "rec1@acme.example.com", Role.recruiter, tenant.id,
                "Recruiter One", phone="9000000003",
            )

            for hm_user in (hm1, hm2):
                await _ensure_hiring_manager(session, tenant.id, hm_user.id)

            company = (
                await session.execute(
                    select(Company).where(Company.tenant_id == tenant.id)
                )
            ).scalar_one_or_none()
            if company is None:
                session.add(
                    Company(
                        tenant_id=tenant.id,
                        brief="Acme Corp builds industrial automation platforms.",
                        culture="Ownership-driven, low-meeting, documentation-first.",
                        policies="Hybrid work; quarterly performance cycles.",
                        benefits="Health cover, learning budget, ESOP pool.",
                        # requested + approved + ratified active; recommended
                        # inactive (will be logged as explicitly skipped, ESD §7)
                        approval_levels_config={
                            "requested": {"active": True, "approver_user_id": str(client.id)},
                            "recommended": {"active": False, "approver_user_id": None},
                            "approved": {"active": True, "approver_user_id": str(hm1.id)},
                            "ratified": {"active": True, "approver_user_id": str(hm2.id)},
                        },
                    )
                )
                print("  + company page + approval levels config")

            await _seed_email_templates(session, tenant.id)
            await _seed_candidates(session, tenant.id)
            # The real sample-resume corpus -> Cloudinary + Databank (dev only,
            # idempotent, fails soft). Source dir resolved via SEED_RESUMES_DIR
            # or /resumes (see seed_resumes.py).
            await seed_resume_corpus(session, tenant.id)
            await _seed_jobs(session, tenant.id, hm1.id)

            # Multi-context identifier, part 1: the same person is a Recruiter
            # at Acme. Part 2 (hiring_manager at TechStart) is seeded below.
            # Recruiter (uncapped) is used here rather than hiring_manager so
            # the fixture never collides with the FR-2.2 max-5 HM cap on Acme.
            await _get_or_create_user(
                session, MULTI_CONTEXT_EMAIL, Role.recruiter, tenant.id,
                "Dev Multi-Context", phone=MULTI_CONTEXT_PHONE,
            )

            print("Seeding second tenant (TechStart Inc.)...")
            techstart = (
                await session.execute(
                    select(Tenant).where(Tenant.domain == TECHSTART_TENANT_DOMAIN)
                )
            ).scalar_one_or_none()
            if techstart is None:
                techstart = Tenant(
                    name=TECHSTART_TENANT_NAME, domain=TECHSTART_TENANT_DOMAIN
                )
                session.add(techstart)
                await session.flush()
                print(f"  + tenant {TECHSTART_TENANT_NAME} ({TECHSTART_TENANT_DOMAIN})")

            ts_client = await _get_or_create_user(
                session, "client@techstart.test", Role.client, techstart.id,
                "TechStart Client", phone="9000000010",
            )
            ts_hm = await _get_or_create_user(
                session, "hm@techstart.test", Role.hiring_manager, techstart.id,
                "TechStart HM", phone="9000000011",
            )
            await _ensure_hiring_manager(session, techstart.id, ts_hm.id)

            # Multi-context identifier, part 2: hiring_manager at TechStart. Now
            # the single email/phone resolves to 2 users across 2 tenants (a
            # recruiter at Acme and a hiring_manager at TechStart)  -  this is what
            # drives the "choose your workspace" screen.
            multi_hm_ts = await _get_or_create_user(
                session, MULTI_CONTEXT_EMAIL, Role.hiring_manager, techstart.id,
                "Dev Multi-Context", phone=MULTI_CONTEXT_PHONE,
            )
            await _ensure_hiring_manager(session, techstart.id, multi_hm_ts.id)

            # A minimal company page for TechStart so its org portal renders.
            ts_company = (
                await session.execute(
                    select(Company).where(Company.tenant_id == techstart.id)
                )
            ).scalar_one_or_none()
            if ts_company is None:
                session.add(
                    Company(
                        tenant_id=techstart.id,
                        brief="TechStart Inc. is an early-stage SaaS company.",
                        culture="Fast-moving, generalist, remote-first.",
                        policies="Fully remote; flexible hours.",
                        benefits="Equity, home-office stipend.",
                        approval_levels_config={
                            "requested": {"active": True, "approver_user_id": str(ts_client.id)},
                            "recommended": {"active": False, "approver_user_id": None},
                            "approved": {"active": False, "approver_user_id": None},
                            "ratified": {"active": True, "approver_user_id": str(ts_hm.id)},
                        },
                    )
                )
                print("  + TechStart company page + approval levels config")
            await _seed_email_templates(session, techstart.id)

            # A candidate LOGIN account (role=candidate, tenant_id NULL) with
            # BOTH email and mobile so candidate-portal SMS/email OTP is testable.
            # Distinct from the Databank Candidate rows above (those have no User).
            await _get_or_create_user(
                session, "candidate.demo@pickready.test", Role.candidate, None,
                "Candidate Demo", phone="9000000020",
            )

            await session.commit()
            print("Seed complete.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
