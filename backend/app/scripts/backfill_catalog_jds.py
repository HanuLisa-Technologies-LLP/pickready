"""Fill in the structured JD fields the MVP jobs catalogue import left empty.

The catalogue import (migration 0006) seeded each job with only a role line,
a skills list and an education line. Everything a candidate actually reads —
responsibilities, accountabilities, the reporting line, headcount — was blank,
so the public application page and the org job detail page both rendered "—".

The content here is deterministic and hand-authored per role, then shaded by
the job's level and the hiring company, so every seeded job reads like a real
posting written for that company. It is intentionally NOT LLM-generated: mock
data must be reproducible and must not depend on a provider being reachable.

Existing values are never overwritten — a job whose JD was authored through
the product (or by the AI JD-generation path) is left exactly as it is.

Dry-run by default; pass ``--apply`` to persist.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import select

from app.core.db import get_session_factory, superadmin_scope
from app.models.job import Job
from app.models.tenant import Tenant

# Fields this script is responsible for. A job is "complete" once all of them
# carry real content; anything genuinely authored is preserved.
MANAGED_FIELDS = (
    "role",
    "description",
    "responsibilities",
    "accountabilities",
    "reporting_to",
    "reportees",
)

# ── Per-level shading ───────────────────────────────────────────────────────
# Junior/Mid/Senior change the reporting line, the headcount and the scope
# sentence appended to the role summary, so three postings for the same title
# don't read identically.
LEVEL_PROFILE: dict[str, dict[str, Any]] = {
    "Junior": {
        "reportees": 0,
        "scope": (
            "This is an early-career position: you will work inside an established "
            "team, with code review and design guidance available throughout."
        ),
    },
    "Mid": {
        "reportees": 0,
        "scope": (
            "You will own features end to end, from design through release and "
            "production support, and will mentor newer engineers on the team."
        ),
    },
    "Senior": {
        "reportees": 2,
        "scope": (
            "You will set the technical direction for your area, review the team's "
            "designs, and carry a small number of direct reports."
        ),
    },
}
DEFAULT_LEVEL = "Mid"


def _reporting_to(level: str, discipline: str) -> str:
    if level == "Senior":
        return f"Head of {discipline}"
    if level == "Junior":
        return f"Senior {discipline} Engineer"
    return f"{discipline} Engineering Manager"


# ── Per-role content ────────────────────────────────────────────────────────
# discipline      → drives the reporting line
# summary         → the `role` field (a company/level sentence is appended)
# responsibilities/accountabilities → the two list fields
ROLE_LIBRARY: dict[str, dict[str, Any]] = {
    "React Frontend Developer": {
        "discipline": "Frontend",
        "summary": (
            "Build and maintain the customer-facing web application in React and "
            "TypeScript, turning product designs into accessible, fast interfaces"
        ),
        "responsibilities": [
            "Build responsive, accessible React components from design specifications and iterate on them with product and design",
            "Manage client-side state and data fetching against the platform's REST APIs, handling loading, empty and error states properly",
            "Write component and integration tests, and keep the frontend test suite green in CI",
            "Profile and improve page-load and interaction performance across desktop and mobile breakpoints",
            "Take part in code review and help maintain the shared component library",
        ],
        "accountabilities": [
            "The released UI matches the agreed design and behaves correctly across supported browsers",
            "Accessibility standards are met for every screen shipped",
            "Frontend defects reaching production are triaged and resolved within the agreed response window",
            "Reusable components are contributed back to the shared library rather than duplicated",
        ],
    },
    "MERN Stack Developer": {
        "discipline": "Full Stack",
        "summary": (
            "Deliver features across the MongoDB, Express, React and Node stack, "
            "owning each one from the data model through to the rendered screen"
        ),
        "responsibilities": [
            "Design MongoDB schemas and indexes that support the product's access patterns",
            "Build and document Express/Node REST endpoints, including authentication, validation and error handling",
            "Implement the matching React front end and wire it to those endpoints",
            "Write unit and integration tests across both tiers and keep them running in CI",
            "Diagnose production issues across the stack and see the fix through to release",
        ],
        "accountabilities": [
            "Features are delivered working end to end, not handed over half-finished at a tier boundary",
            "API contracts are documented and kept backwards-compatible for existing clients",
            "Database queries stay within the agreed latency budget as data volume grows",
            "Security basics, input validation, authorisation checks, dependency hygiene, are covered before release",
        ],
    },
    "Full Stack Developer (.NET)": {
        "discipline": "Full Stack",
        "summary": (
            "Build and maintain line-of-business applications on ASP.NET Core and "
            "SQL Server, covering both the service layer and the web front end"
        ),
        "responsibilities": [
            "Design and implement ASP.NET Core web APIs, including validation, authorisation and structured logging",
            "Model relational data in SQL Server and write efficient queries and migrations",
            "Build the corresponding web UI and integrate it with the service layer",
            "Write unit and integration tests using the team's .NET test tooling",
            "Support deployments and investigate production defects through to root cause",
        ],
        "accountabilities": [
            "Services meet their functional specification and the agreed response-time targets",
            "Schema changes ship as reviewed, reversible migrations, never manual production edits",
            "Test coverage on business logic is maintained at the team's agreed threshold",
            "Production incidents in owned services are investigated and written up",
        ],
    },
    "AI / Generative AI Engineer": {
        "discipline": "AI",
        "summary": (
            "Design and ship generative-AI features, retrieval pipelines, agent "
            "workflows and LLM integrations, that hold up in production"
        ),
        "responsibilities": [
            "Build retrieval-augmented generation pipelines, including chunking, embedding and vector-store retrieval strategy",
            "Design and implement multi-step agent workflows with explicit state, retries and fallback behaviour",
            "Integrate multiple LLM providers behind a routing layer, with health tracking and graceful degradation",
            "Define evaluation sets and measure output quality, latency and cost before and after each change",
            "Harden prompts and output parsing against malformed, adversarial or truncated model responses",
        ],
        "accountabilities": [
            "Generative features degrade gracefully rather than failing outright when a provider is unavailable",
            "Model and prompt changes are evaluated against a held-out set before release, not shipped on intuition",
            "Inference cost per request stays within the agreed budget",
            "No customer data is sent to a provider outside the approved data-handling policy",
        ],
    },
    "Machine Learning Engineer": {
        "discipline": "Machine Learning",
        "summary": (
            "Take machine-learning models from experiment to production service, "
            "and keep them accurate and observable once they are there"
        ),
        "responsibilities": [
            "Build training and feature-engineering pipelines over the platform's data sets",
            "Evaluate candidate models against agreed offline metrics and document the trade-offs",
            "Package and deploy models as versioned, monitored inference services",
            "Instrument production models for drift, latency and accuracy, and act on what the monitoring shows",
            "Work with data engineering to secure the data quality the models depend on",
        ],
        "accountabilities": [
            "Deployed models meet the accuracy and latency targets agreed with the product owner",
            "Every model in production is versioned and reproducible from source data and code",
            "Model drift is detected and escalated before it affects customers",
            "Evaluation results and known limitations are documented for downstream consumers",
        ],
    },
    "Data Analyst": {
        "discipline": "Analytics",
        "summary": (
            "Turn the platform's operational data into analysis the business acts "
            "on, through SQL, dashboards and clearly written findings"
        ),
        "responsibilities": [
            "Write and optimise SQL against the analytics warehouse to answer business questions",
            "Build and maintain dashboards covering the agreed operational and commercial metrics",
            "Investigate data-quality issues and trace them back to the producing system",
            "Present findings to non-technical stakeholders with the caveats stated plainly",
            "Document metric definitions so the same number means the same thing across teams",
        ],
        "accountabilities": [
            "Published figures are accurate and reproducible from a documented query",
            "Recurring reports are delivered on the agreed schedule",
            "Metric definitions are kept current and contradictions between reports are resolved",
            "Limitations and confidence in an analysis are stated rather than implied",
        ],
    },
    "DevOps / Cloud Engineer": {
        "discipline": "Platform",
        "summary": (
            "Own the cloud infrastructure, deployment pipelines and observability "
            "that the engineering teams build on"
        ),
        "responsibilities": [
            "Define and maintain cloud infrastructure as versioned, reviewable code",
            "Build and maintain CI/CD pipelines that give teams safe, repeatable releases",
            "Run the container platform, including image builds, rollout strategy and resource limits",
            "Maintain logging, metrics and alerting so failures are detected before customers report them",
            "Take part in the on-call rotation and lead incident response and post-incident review",
        ],
        "accountabilities": [
            "Production availability is kept within the agreed service objective",
            "Infrastructure changes are applied through reviewed code, never by manual console edits",
            "Backups and restores are exercised on the agreed schedule, not merely configured",
            "Alerts are actionable, noisy or unowned alerts are fixed or removed",
        ],
    },
    "Python Backend Developer": {
        "discipline": "Backend",
        "summary": (
            "Build and operate the Python services behind the product, with an "
            "emphasis on correct data handling and dependable APIs"
        ),
        "responsibilities": [
            "Design and implement FastAPI services with typed request and response schemas",
            "Model relational data in PostgreSQL and ship every schema change as a reviewed migration",
            "Move slow or unreliable work onto the asynchronous task queue rather than blocking a request",
            "Write unit and integration tests covering business rules and edge cases",
            "Investigate production issues through logs and traces, and see fixes through to release",
        ],
        "accountabilities": [
            "APIs match their documented contract and stay backwards-compatible for existing clients",
            "Tenant data isolation is preserved by every query path without exception",
            "Background jobs are idempotent and safe to retry",
            "Owned services meet their agreed error-rate and latency targets",
        ],
    },
    "Data Engineer": {
        "discipline": "Data",
        "summary": (
            "Build the ingestion and transformation pipelines that make the "
            "platform's data trustworthy for analytics and machine learning"
        ),
        "responsibilities": [
            "Build batch and streaming ingestion pipelines from source systems into the warehouse",
            "Implement transformation layers with tested, documented business logic",
            "Define and enforce data-quality checks, and alert on the ones that fail",
            "Model warehouse schemas for the analytical access patterns actually in use",
            "Monitor pipeline cost and runtime, and tune both as volumes grow",
        ],
        "accountabilities": [
            "Pipelines complete within their agreed window and failures are alerted, not discovered later",
            "Data lineage is documented from source system through to published table",
            "Data-quality checks cover the fields downstream consumers depend on",
            "Backfills and reprocessing are repeatable and do not double-count",
        ],
    },
    "Java Backend Developer": {
        "discipline": "Backend",
        "summary": (
            "Build and maintain the Spring Boot services that carry the platform's "
            "core transactional workload"
        ),
        "responsibilities": [
            "Design and implement Spring Boot REST services with validation, authorisation and structured logging",
            "Model relational data with JPA and ship schema changes as reviewed, reversible migrations",
            "Integrate asynchronous messaging for work that must not block a request",
            "Write unit and integration tests, including tests against a real database",
            "Profile and tune JVM and query performance under representative load",
        ],
        "accountabilities": [
            "Services meet their documented API contract and agreed latency targets",
            "Transactional correctness is preserved, no partial writes or lost updates",
            "Schema changes are reversible and applied through migrations only",
            "Production defects in owned services are root-caused, not merely restarted away",
        ],
    },
}


def _role_key(title: str) -> str | None:
    """Match a job title to the role library, tolerating minor title drift."""
    if title in ROLE_LIBRARY:
        return title
    normalized = title.strip().casefold()
    for key in ROLE_LIBRARY:
        if key.casefold() == normalized:
            return key
    return None


def build_jd_fields(title: str, level: str | None, company: str | None) -> dict[str, Any] | None:
    """The managed JD fields for one job, or None if the title isn't in the library."""
    key = _role_key(title)
    if key is None:
        return None
    role = ROLE_LIBRARY[key]
    profile = LEVEL_PROFILE.get(level or DEFAULT_LEVEL, LEVEL_PROFILE[DEFAULT_LEVEL])
    # "Specter & Co." already ends in a full stop — don't produce "Co..".
    company_name = (company or "").strip()
    at_company = f" at {company_name.rstrip('.')}" if company_name else ""
    where = company_name.rstrip(".") or "The company"
    return {
        "role": f"{role['summary']}{at_company}. {profile['scope']}",
        "description": (
            f"{where} is hiring a {title} to {role['summary'][0].lower()}{role['summary'][1:]}. "
            f"{profile['scope']} You will work alongside product, design and platform "
            "colleagues, and are expected to take a feature from problem statement "
            "through to production and its ongoing support."
        ),
        "responsibilities": list(role["responsibilities"]),
        "accountabilities": list(role["accountabilities"]),
        "reporting_to": _reporting_to(level or DEFAULT_LEVEL, role["discipline"]),
        "reportees": profile["reportees"],
    }


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _is_placeholder(field: str, value: Any, title: str) -> bool:
    """True when a field is technically set but carries no real content.

    The catalogue import left `role` as a bare copy of the job title and
    `description` as a one-line "We are hiring a X to join Y." stub. Both read
    as empty to a candidate, so they are treated as fillable — while a JD that
    was actually authored (through the product or the AI JD-generation path)
    is left untouched.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if field == "role":
        return text.casefold() == title.strip().casefold()
    if field == "description":
        return text.casefold().startswith("we are hiring a ") and len(text) < 160
    return False


async def run(*, apply: bool, force: bool = False) -> tuple[int, int, list[str]]:
    """Returns (jobs scanned, jobs updated, titles skipped for lack of a template)."""
    scanned = 0
    updated = 0
    unmatched: list[str] = []
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                jobs = (
                    await session.execute(select(Job).order_by(Job.created_at, Job.id))
                ).scalars().all()
                companies = {
                    tenant.id: tenant.name
                    for tenant in (await session.execute(select(Tenant))).scalars().all()
                }
                for job in jobs:
                    jd = dict(job.jd_json or {})
                    # --force regenerates the managed fields for catalogue-imported
                    # jobs only, so a re-run can correct this script's own output
                    # without ever touching a JD a human or the AI path authored.
                    catalogue = bool(str(jd.get("import_note") or "").strip())
                    if force and catalogue:
                        missing = list(MANAGED_FIELDS)
                    else:
                        missing = [
                            f
                            for f in MANAGED_FIELDS
                            if _is_blank(jd.get(f)) or _is_placeholder(f, jd.get(f), job.title)
                        ]
                    if not missing:
                        continue
                    scanned += 1
                    fields = build_jd_fields(job.title, job.level, companies.get(job.tenant_id))
                    if fields is None:
                        if job.title not in unmatched:
                            unmatched.append(job.title)
                        continue
                    # Only fill what is actually blank — never overwrite authored content.
                    for field in missing:
                        jd[field] = fields[field]
                    if apply:
                        job.jd_json = jd
                    updated += 1
                if not apply:
                    await session.rollback()
    return scanned, updated, unmatched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist the generated JD fields")
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate managed fields on catalogue-imported jobs (authored JDs are still never touched)",
    )
    args = parser.parse_args()
    scanned, updated, unmatched = asyncio.run(run(apply=args.apply, force=args.force))
    mode = "applied" if args.apply else "dry run"
    print(f"[{mode}] jobs with incomplete JDs: {scanned}; filled: {updated}")
    if unmatched:
        print("no role template for: " + ", ".join(sorted(unmatched)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
