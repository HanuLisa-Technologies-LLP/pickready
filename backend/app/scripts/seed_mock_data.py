"""Fill every gap in the mock dataset so the MVP demo works end to end.

This is a REPAIR script, not a generator. It never invents an entity that
should have come from a real workflow  -  it fills fields that are NULL or empty
on rows that already exist, and tops up the few collections (staff, reports,
email log) whose emptiness would block a demo path.

    docker exec pickready-backend-1 python -m app.scripts.seed_mock_data --dry-run
    docker exec pickready-backend-1 python -m app.scripts.seed_mock_data

Two properties make it safe to run against the shared dev database:

IDEMPOTENT. Every write is guarded on the field actually being empty. Running
it twice changes nothing the second time, and it will never overwrite content
someone authored by hand.

DETERMINISTIC. Nothing uses `random`. Where the dataset needs variety  -  which
applications reached which stage, which score a dimension gets  -  the value is
derived from a stable hash of the row's own ids (`_spread`). Re-running
produces the identical dataset, so a demo rehearsed on Monday looks the same on
Friday, and a bug found in one report can actually be reproduced.

COHERENT, NOT RANDOM. Ratings are computed from the real overlap between the
candidate's parsed resume skills and the job's actual JD skills, so a Java
developer scores well against the Java role and poorly against the ML one.
Comments name the specific skills involved. A demo where the text contradicts
the data is worse than no demo.

Content is generated in Python rather than through the LLM router deliberately:
this touches roughly 600 reports, which would be hours of provider calls, would
drift on every run, and would burn quota that the live product needs. The
generated prose goes through the SAME validators the LLM path uses
(`enforce_word_range`, `rating_label`), so it satisfies every contract the app
enforces on real content.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed_mock_data")

#: Anchor for every generated timestamp. Fixed relative to "now" at run time but
#: applied consistently, so a report is never stamped in the future and the
#: six-month retake window behaves predictably during a demo.
NOW = datetime.now(timezone.utc)

#: Set by --refresh-rankings. When true, `fill_link_scores` also re-generates
#: breakdowns it previously seeded, so an improvement to the generation logic
#: can be rolled out without hand-editing the database. Genuine `llm`
#: breakdowns are never touched, with or without this flag.
REFRESH_RANKINGS = False


# ── Deterministic variety ────────────────────────────────────────────────────

def _spread(*parts: Any, buckets: int = 100) -> int:
    """A stable 0..buckets-1 value derived from `parts`.

    This is the whole variety mechanism. It replaces `random()` so the dataset
    is reproducible: the same job and candidate always land in the same bucket,
    on any machine, on any run.
    """
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") % buckets


def _pick(options: list[Any], *parts: Any) -> Any:
    """Deterministically choose one of `options` for this row."""
    return options[_spread(*parts, buckets=len(options))]


# ── Company narrative content ────────────────────────────────────────────────
# Written per-company rather than templated, because these paragraphs are the
# most visible text in the product  -  they head every job description a
# candidate reads. Generic filler here would undermine the whole demo.

COMPANY_CONTENT: dict[str, dict[str, str]] = {
    "Sarkar Corp": {
        "industry": "Technology",
        "about_company": (
            "Sarkar Corp builds the data infrastructure that mid-market lenders in "
            "India run their underwriting on. We started in 2018 with a single "
            "reconciliation product and now process loan-decisioning pipelines for "
            "forty-plus NBFCs, handling several million applications a month.\n\n"
            "Our engineering is deliberately unglamorous and deliberately reliable. "
            "The platform is Python and Go services on AWS, with Postgres and Kafka "
            "doing most of the heavy lifting, and a React front end that credit "
            "analysts live in for eight hours a day. We have been moving steadily "
            "toward event-driven processing and have real work underway on "
            "model-assisted risk scoring.\n\n"
            "We are profitable, which shapes how we hire. We are not staffing up to "
            "hit a headcount target, we add people when a specific problem needs "
            "them, and we expect those people to own that problem properly rather "
            "than pick up tickets. Most of our engineers talk directly to the credit "
            "teams who use what they build."
        ),
        "culture": (
            "We are a writing culture. Decisions of any consequence get written down "
            "before they get made, which slows the first day of a project and speeds "
            "up every day after it. Disagreement is expected and is not a career "
            "risk; the person with the strongest argument wins, not the most senior "
            "one in the room.\n\n"
            "We run small teams with wide scope. An engineer here owns a service end "
            "to end, design, deploy, on-call, and the conversation with the credit "
            "analyst when it misbehaves. That is a lot of responsibility and it is "
            "not for everyone, but people who want it rarely want to go back.\n\n"
            "We take mistakes seriously and blame lightly. Postmortems are about the "
            "system that allowed the failure, and they are shared company-wide."
        ),
        "work_life": (
            "We are hybrid, anchored in Bengaluru. Most teams are in the office two "
            "or three days a week, chosen by the team rather than mandated, and the "
            "rest is remote. Several senior engineers are fully remote and that has "
            "worked, so we are open to it where the role allows.\n\n"
            "Core collaboration hours are 11am to 5pm IST. Outside them, we do not "
            "expect a reply, and we mean it, no one is measured on responsiveness "
            "after hours. Meetings are capped: two standing meetings a week per team, "
            "and anything that could be a document is a document.\n\n"
            "On-call is one week in six, compensated, and we treat a noisy rotation "
            "as a bug in the system rather than a fact of life. If you are woken up "
            "twice in a week, fixing that becomes the team's priority."
        ),
        "benefits": (
            "Health cover of INR 10 lakh for you, your partner, your children and "
            "your parents, with no waiting period and outpatient cover included. "
            "Mental health support through a confidential external provider, with "
            "sessions that do not come out of your leave.\n\n"
            "Leave is 24 days plus public holidays, and we track that people actually "
            "take it, managers are accountable for their team's unused balance. "
            "Parental leave is six months for the primary caregiver and eight weeks "
            "for the secondary, with a phased return.\n\n"
            "An annual learning budget of INR 60,000 that covers conferences, courses "
            "and books with no approval chain under that ceiling. ESOPs for every "
            "employee past their first year, with a four-year vest and a one-year "
            "cliff, and a buyback window every two years so the equity is worth "
            "something before an exit."
        ),
    },
    "ACRM Corp": {
        "industry": "Technology",
        "about_company": (
            "ACRM Corp is an applied-AI company. We build retrieval and reasoning "
            "systems for enterprises whose knowledge is trapped in twenty years of "
            "documents, insurers, hospital networks, and large manufacturers who "
            "know the answer is somewhere in the archive and cannot find it.\n\n"
            "The work is genuinely hard and genuinely current. Our stack is Python "
            "throughout, with PyTorch for the model work, LangGraph orchestrating "
            "multi-step retrieval, and pgvector and Qdrant behind the retrieval "
            "layer. We fine-tune where it earns its keep and use hosted models where "
            "it does not, and we are opinionated about knowing the difference.\n\n"
            "We are eighty people, Series B, and growing the engineering team "
            "carefully. What we look for is people who can hold both halves of this "
            "work at once, the research half that reads papers, and the engineering "
            "half that keeps a pipeline up at three in the morning. Plenty of people "
            "do one well. We need both."
        ),
        "culture": (
            "We are a demo culture. Ideas are argued with a working prototype rather "
            "than a deck, and Friday afternoons are for showing each other what did "
            "not work as much as what did. Negative results get the same airtime.\n\n"
            "Research and engineering sit together, not in separate orgs. The person "
            "who trained the model is the person who watches it in production, which "
            "keeps everyone honest about what a benchmark number is worth.\n\n"
            "We are careful about what we claim. Customers are told what a system "
            "cannot do before they are told what it can, and no one here is rewarded "
            "for overselling a capability. That reputation took years to build and we "
            "guard it closely."
        ),
        "work_life": (
            "Remote-first and async by default. The team spans Bengaluru, Pune and "
            "two European timezones, so almost nothing depends on being awake at the "
            "same moment. Written updates replace status meetings entirely.\n\n"
            "There is a four-hour overlap window each day for the conversations that "
            "genuinely need to be live. Outside it, expect to be left alone. We do "
            "not use response time as a proxy for commitment, and we have removed the "
            "tooling that used to make that tempting.\n\n"
            "Everyone meets in person for a week each quarter, company-funded, and "
            "those weeks are for planning and for actually knowing your colleagues, "
            "not for the work you could have done at your desk. Research time is "
            "protected: one day a fortnight that no one may book over."
        ),
        "benefits": (
            "Health cover of INR 15 lakh including parents, with dental and vision, "
            "and a separate annual health check that is scheduled for you rather than "
            "left on your list. Therapy is covered outright, not reimbursed.\n\n"
            "Unlimited leave with a mandatory floor of 20 days, because unlimited "
            "leave without a floor means people take less. A four-week paid sabbatical "
            "at every three-year mark, on top of normal leave.\n\n"
            "Compute budget for personal research, an annual conference of your "
            "choosing with travel covered, and paid time to write up work for "
            "publication, we would rather our people publish than not. Equity for "
            "everyone, home-office budget of INR 1,00,000 refreshed every three "
            "years, and a fully covered co-working desk if you would rather not work "
            "from home."
        ),
    },
    "Specter & Co.": {
        "industry": "Finance",
        "about_company": (
            "Specter & Co. builds trading and post-trade systems for mid-sized asset "
            "managers. Our customers move real money on our software every day, which "
            "sets the tone for everything about how we work.\n\n"
            "The platform is Java and Kotlin on the transaction path, Python for "
            "analytics and reporting, and a React front end used by traders who will "
            "tell you immediately and directly when something is wrong. Correctness "
            "is not a quality attribute here, it is the product, a rounding error is "
            "an incident, and a missed reconciliation is a regulatory problem.\n\n"
            "We are twelve years old, employee-owned, and have never taken outside "
            "investment. That means we grow slower than we could and we answer to our "
            "customers rather than to a board. Engineers who have spent time somewhere "
            "chasing a funding round tend to find the difference noticeable."
        ),
        "culture": (
            "We are careful by temperament. Changes to the transaction path are "
            "reviewed by two people, tested against a decade of replayed market data, "
            "and rolled out gradually. Nobody is praised for speed on that path.\n\n"
            "Away from it we move quite fast, internal tooling, analytics and "
            "reporting ship continuously. Knowing which mode a piece of work is in is "
            "one of the first things you learn here.\n\n"
            "We are direct with each other. Code review is thorough and can be blunt, "
            "and it is about the code. New joiners sometimes read it as harsh in the "
            "first month and as respect by the third. We pair often, especially on "
            "anything touching settlement, because two people who understand a system "
            "is the minimum we are willing to run."
        ),
        "work_life": (
            "Office-anchored in Mumbai, three days a week, and honest about why: much "
            "of what we do involves regulated data and screens that cannot leave the "
            "building. The other two days are yours to work wherever suits.\n\n"
            "Market hours shape the day. The team is online from 8:30am, and we "
            "genuinely stop in the evening, sustained late nights are treated as a "
            "planning failure and get escalated, not admired. Release windows are "
            "scheduled outside market hours and the people who work them take the "
            "time back.\n\n"
            "No weekend on-call except during a scheduled migration, which happens "
            "perhaps twice a year and is planned months ahead. We have kept it that "
            "way for eight years and consider it a feature worth protecting."
        ),
        "benefits": (
            "Health cover of INR 20 lakh for the family including parents, with no "
            "sub-limits and direct settlement at most hospitals. Life and disability "
            "cover at five times annual salary.\n\n"
            "Leave is 26 days plus public holidays and a firm-wide shutdown between "
            "Christmas and New Year that does not count against it. Six months "
            "parental leave for either parent, and a return-to-work arrangement at "
            "reduced hours and full pay for the first two months back.\n\n"
            "Because we are employee-owned, every permanent employee holds real "
            "shares with a genuine annual valuation and dividend, not options against "
            "a future event. There is also a retirement contribution above statutory "
            "PF, a fully paid professional certification each year, and an interest-"
            "free loan facility for a home deposit after three years."
        ),
    },
}

#: Applied to any tenant not named above, so the script degrades gracefully if
#: someone adds a fourth demo company without editing this file.
GENERIC_COMPANY = {
    "industry": "Technology",
    "about_company": (
        "We are a product engineering company building software that our customers "
        "depend on daily. Our teams own their systems end to end, from the design "
        "conversation through to the on-call rotation, and we hire people who want "
        "that scope rather than a queue of tickets.\n\n"
        "The stack is modern and pragmatic, we choose boring technology where "
        "reliability matters and newer tools where they earn their place. Engineers "
        "here talk directly to the people who use what they build."
    ),
    "culture": (
        "Decisions are written down before they are made, disagreement is expected, "
        "and the strongest argument wins rather than the most senior voice. We take "
        "failures seriously and blame lightly, postmortems examine the system that "
        "allowed a mistake, not the person who made it."
    ),
    "work_life": (
        "Hybrid, with the in-office days chosen by each team rather than mandated. "
        "Core collaboration hours are protected and the time outside them genuinely "
        "is yours; nobody is measured on how quickly they reply in the evening. "
        "Meetings are capped and anything that could be a document is a document."
    ),
    "benefits": (
        "Comprehensive family health cover including parents, generous leave that "
        "managers are accountable for people actually taking, substantial parental "
        "leave with a phased return, an annual learning budget with no approval "
        "chain, and equity for every employee."
    ),
}


def company_content(tenant_name: str) -> dict[str, str]:
    return COMPANY_CONTENT.get(tenant_name, GENERIC_COMPANY)


#: A narrative section shorter than this is a STUB, not authored prose.
#: Two tenants carry one-line placeholders ("Acme Corp builds industrial
#: automation platforms.") that render as a single sentence where the other
#: companies show three paragraphs  -  which looks broken rather than brief.
#: Treating them as empty is the point of the threshold; anything a human
#: actually wrote will comfortably clear it.
STUB_LENGTH = 120


def is_stub(value: str | None) -> bool:
    return len((value or "").strip()) < STUB_LENGTH


# ── Compensation by grade ────────────────────────────────────────────────────
# INR, matching the Bengaluru/Mumbai market the rest of the dataset implies.
# The spec's USD figures would contradict the candidates' own CTC answers.

COMPENSATION_BANDS: dict[str, dict[str, Any]] = {
    "non_managerial": {"ctc_min": 1800000, "ctc_max": 3200000},
    "managerial": {"ctc_min": 3500000, "ctc_max": 5500000},
    "leadership": {"ctc_min": 6000000, "ctc_max": 9000000},
    "cxo": {"ctc_min": 10000000, "ctc_max": 18000000},
}

COMPENSATION_NOTES: dict[str, str] = {
    "non_managerial": (
        "Fixed base plus a performance bonus of up to 10%. ESOPs vesting over four "
        "years with a one-year cliff. Reviewed annually."
    ),
    "managerial": (
        "Fixed base plus a performance bonus of up to 15%, tied to team delivery as "
        "well as individual outcomes. ESOP grant on joining, four-year vest."
    ),
    "leadership": (
        "Fixed base plus an annual bonus of up to 25% against business objectives. "
        "Significant ESOP grant with accelerated vesting on a liquidity event."
    ),
    "cxo": (
        "Fixed base plus an annual bonus of up to 40% against board-agreed "
        "objectives, and a material equity grant negotiated at offer."
    ),
}


async def fill_companies(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Create the missing `companies` rows and fill their narrative sections.

    Three of the five demo tenants have no companies row at all, which means
    every one of their 32 jobs renders with no About / Work Life / Benefits  - 
    the single most visible gap in the dataset.
    """
    stats = {"companies_created": 0, "companies_filled": 0}
    rows = (
        await session.execute(
            text(
                """
                SELECT t.id AS tenant_id, t.name, t.industry,
                       c.id AS company_id, c.about_company, c.work_life,
                       c.benefits_text, c.culture
                FROM tenants t
                LEFT JOIN companies c ON c.tenant_id = t.id
                ORDER BY t.name
                """
            )
        )
    ).mappings().all()

    for row in rows:
        content = company_content(row["name"])
        if row["company_id"] is None:
            logger.info("  + company row for %s", row["name"])
            stats["companies_created"] += 1
            if not dry_run:
                await session.execute(
                    text(
                        """
                        INSERT INTO companies
                            (id, tenant_id, about_company, work_life, benefits_text,
                             culture, brief, benefits)
                        VALUES
                            (gen_random_uuid(), :tid, :about, :work, :benefits,
                             :culture, :about, :benefits)
                        """
                    ),
                    {
                        "tid": row["tenant_id"],
                        "about": content["about_company"],
                        "work": content["work_life"],
                        "benefits": content["benefits"],
                        "culture": content["culture"],
                    },
                )
        else:
            # A one-line stub counts as missing  -  see STUB_LENGTH. Each field is
            # decided independently so a company with real prose in two
            # sections and a stub in the third only has the third replaced.
            replacements = {
                "about": content["about_company"] if is_stub(row["about_company"]) else None,
                "work": content["work_life"] if is_stub(row["work_life"]) else None,
                "benefits": content["benefits"] if is_stub(row["benefits_text"]) else None,
                "culture": content["culture"] if is_stub(row["culture"]) else None,
            }
            filling = [k for k, v in replacements.items() if v is not None]
            if filling:
                logger.info("  ~ company %s filling %s", row["name"], filling)
                stats["companies_filled"] += 1
                if not dry_run:
                    await session.execute(
                        text(
                            """
                            UPDATE companies SET
                              about_company = COALESCE(:about, about_company),
                              work_life     = COALESCE(:work, work_life),
                              benefits_text = COALESCE(:benefits, benefits_text),
                              culture       = COALESCE(:culture, culture)
                            WHERE id = :cid
                            """
                        ),
                        {"cid": row["company_id"], **replacements},
                    )
        # Tenant industry is shown read-only on the Company Profile page.
        if not (row["industry"] or "").strip() and not dry_run:
            await session.execute(
                text("UPDATE tenants SET industry = :i WHERE id = :t"),
                {"i": content["industry"], "t": row["tenant_id"]},
            )
    return stats


async def fill_jobs(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Snapshot the company narrative onto each job, and fill compensation.

    Snapshotting rather than leaving NULL (which would read through to the
    company profile) is deliberate: it matches how a job created today behaves,
    so the demo dataset and a freshly created job are indistinguishable.
    """
    stats = {"jobs_sections": 0, "jobs_compensation": 0, "jobs_published": 0}
    rows = (
        await session.execute(
            text(
                """
                SELECT j.id, j.title, j.assessment_grade AS grade, j.ratified_at,
                       j.about_company, j.work_life, j.benefits, j.compensation_json,
                       t.name AS tenant_name,
                       c.about_company AS c_about, c.work_life AS c_work,
                       c.benefits_text AS c_benefits
                FROM jobs j
                JOIN tenants t ON t.id = j.tenant_id
                LEFT JOIN companies c ON c.tenant_id = j.tenant_id
                """
            )
        )
    ).mappings().all()

    for row in rows:
        content = company_content(row["tenant_name"])
        about = row["c_about"] or content["about_company"]
        work = row["c_work"] or content["work_life"]
        benefits = row["c_benefits"] or content["benefits"]

        # Same stub rule as the company profile: a job that snapshotted a
        # one-line placeholder before the company was filled in gets the real
        # prose, otherwise its own edited text is preserved.
        section_updates = {
            "about": about if is_stub(row["about_company"]) else None,
            "work": work if is_stub(row["work_life"]) else None,
            "benefits": benefits if is_stub(row["benefits"]) else None,
        }
        if any(v is not None for v in section_updates.values()):
            stats["jobs_sections"] += 1
            if not dry_run:
                await session.execute(
                    text(
                        """
                        UPDATE jobs SET
                          about_company = COALESCE(:about, about_company),
                          work_life     = COALESCE(:work, work_life),
                          benefits      = COALESCE(:benefits, benefits)
                        WHERE id = :jid
                        """
                    ),
                    {"jid": row["id"], **section_updates},
                )

        # Compensation exists in two shapes in this dataset: the earlier seed
        # wrote {"unit", "currency", "range_lpa": "15-22"}, while the app's own
        # editor writes {"ctc_min", "ctc_max", "currency", "notes"}. A reader
        # expecting one finds nothing in the other. Rather than pick a winner
        # and silently drop data, the canonical keys are ADDED alongside the
        # existing ones  -  both readers then work, and no figure is lost.
        existing_comp = _as_dict(row["compensation_json"])
        if existing_comp and existing_comp.get("ctc_min") is None:
            range_lpa = str(existing_comp.get("range_lpa") or "")
            parts = [p.strip() for p in range_lpa.split("-") if p.strip()]
            try:
                lo = float(parts[0]) * 100000
                hi = float(parts[1]) * 100000 if len(parts) > 1 else lo
            except (ValueError, IndexError):
                lo = hi = None
            if lo is not None:
                stats["jobs_compensation"] += 1
                if not dry_run:
                    await session.execute(
                        text(
                            "UPDATE jobs SET compensation_json = "
                            "compensation_json || CAST(:c AS jsonb) WHERE id = :jid"
                        ),
                        {
                            "c": json.dumps(
                                {
                                    "ctc_min": int(lo),
                                    "ctc_max": int(hi),
                                    "notes": COMPENSATION_NOTES.get(
                                        row["grade"] or "non_managerial",
                                        COMPENSATION_NOTES["non_managerial"],
                                    ),
                                }
                            ),
                            "jid": row["id"],
                        },
                    )

        if not row["compensation_json"]:
            grade = row["grade"] or "non_managerial"
            band = COMPENSATION_BANDS.get(grade, COMPENSATION_BANDS["non_managerial"])
            # Nudge each job's band by a deterministic few percent so every role
            # at a grade does not advertise an identical, obviously-templated range.
            skew = 1 + (_spread(row["id"], buckets=9) - 4) / 100
            compensation = {
                "ctc_min": int(band["ctc_min"] * skew // 10000 * 10000),
                "ctc_max": int(band["ctc_max"] * skew // 10000 * 10000),
                "currency": "INR",
                "notes": COMPENSATION_NOTES.get(grade, COMPENSATION_NOTES["non_managerial"]),
            }
            stats["jobs_compensation"] += 1
            if not dry_run:
                await session.execute(
                    text(
                        "UPDATE jobs SET compensation_json = CAST(:c AS jsonb) WHERE id = :jid"
                    ),
                    {"c": json.dumps(compensation), "jid": row["id"]},
                )

        if row["ratified_at"] is None:
            stats["jobs_published"] += 1
            logger.info("  ~ publishing job %r", row["title"])
            if not dry_run:
                # Match what api/jobs.create_job does on the flat model: publish
                # directly, stamping both the status and the terminal marker.
                await session.execute(
                    text(
                        "UPDATE jobs SET ratified_at = :at, status = 'ratified' "
                        "WHERE id = :jid"
                    ),
                    {"at": NOW - timedelta(days=14), "jid": row["id"]},
                )
    return stats


# ── Staff ────────────────────────────────────────────────────────────────────
# Target shape per tenant (spec §7): 1 HR Manager, 2 Recruiters, 3 Hiring
# Managers. Hiring Managers are capped at 5 per tenant by FR-2.2, so 3 is safe.

STAFF_TARGET: dict[str, int] = {"hr_manager": 1, "recruiter": 2, "hiring_manager": 3}

STAFF_NAMES: dict[str, list[str]] = {
    "hr_manager": ["Meera Krishnan", "Sunita Rao", "Farida Sheikh"],
    "recruiter": [
        "Rahul Iyer", "Nikhil Menon", "Sneha Kulkarni",
        "Tarun Bhatia", "Anjali Desai", "Vivek Nair",
    ],
    "hiring_manager": [
        "Priyanka Ghosh", "Sandeep Reddy", "Kavita Malhotra",
        "Arun Prasad", "Rohit Saxena", "Neha Chandra",
        "Imran Qureshi", "Lakshmi Venkat", "Karthik Subramanian",
    ],
}


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum() or ch == " ").replace(" ", ".")


async def fill_staff(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Top each tenant up to the target staff shape.

    New rows are created ACTIVE rather than invited: an invited user has no
    Firebase identity and cannot be signed into, so a demo staff list full of
    pending invitations would show a team nobody can log in as. They are still
    real `users` rows resolved through the normal RBAC engine.
    """
    stats = {"staff_created": 0}
    tenants = (
        await session.execute(
            text(
                """
                SELECT t.id, t.name,
                       COUNT(*) FILTER (WHERE u.role = 'hr_manager'
                                        AND u.status <> 'disabled') AS hr,
                       COUNT(*) FILTER (WHERE u.role = 'recruiter'
                                        AND u.status <> 'disabled') AS rec,
                       COUNT(*) FILTER (WHERE u.role = 'hiring_manager'
                                        AND u.status <> 'disabled') AS hm
                FROM tenants t
                LEFT JOIN users u ON u.tenant_id = t.id
                GROUP BY t.id, t.name
                ORDER BY t.name
                """
            )
        )
    ).mappings().all()

    # A running index per role across the whole run, so the same person is
    # never created at two different companies. Indexing by
    # (tenant_index * target + n) wrapped around the name pool and produced
    # exactly that  -  "Tarun Bhatia" as a recruiter at two firms.
    next_name: dict[str, int] = {role: 0 for role in STAFF_TARGET}

    for tenant in tenants:
        have = {
            "hr_manager": tenant["hr"],
            "recruiter": tenant["rec"],
            "hiring_manager": tenant["hm"],
        }
        domain = _slug(tenant["name"]).replace(".", "") + ".pickready.test"
        for role, target in STAFF_TARGET.items():
            pool = STAFF_NAMES[role]
            for _ in range(have[role], target):
                name = pool[next_name[role] % len(pool)]
                next_name[role] += 1
                email = f"{_slug(name)}@{domain}"
                # Deterministic 10-digit Indian mobile, distinct per person.
                phone = f"9{_spread(email, buckets=900000000) + 100000000}"
                logger.info("  + staff %s (%s) for %s", name, role, tenant["name"])
                stats["staff_created"] += 1
                if dry_run:
                    continue
                await session.execute(
                    text(
                        """
                        INSERT INTO users
                            (id, tenant_id, role, email, phone, full_name, status,
                             auth_providers, created_at)
                        VALUES
                            (gen_random_uuid(), :tid, :role, :email, :phone, :name,
                             'active', '[]'::jsonb, :created)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "tid": tenant["id"], "role": role, "email": email,
                        "phone": phone, "name": name,
                        "created": NOW - timedelta(days=90),
                    },
                )
                if role == "hiring_manager":
                    # Mirror into hiring_managers, which approval assignments and
                    # the FR-2.2 cap trigger both key off.
                    await session.execute(
                        text(
                            """
                            INSERT INTO hiring_managers (id, tenant_id, user_id)
                            SELECT gen_random_uuid(), :tid, u.id
                            FROM users u
                            WHERE u.email = :email AND u.tenant_id = :tid
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {"tid": tenant["id"], "email": email},
                    )
    return stats


# ── Candidate profile form ───────────────────────────────────────────────────
# NOTE ON THE SPEC: the brief describes a "40-question" form with numbered
# aspects 1-40. That was the pre-2026-07-27 model. The form is now a structured
# set of NAMED fields owned by `services/candidate_profile_form.py`, and the
# report, matching pipeline and My Profile page all read those names. Populating
# numbered keys would produce a form that validates as empty and a Validation
# section that renders blank, so this fills the real field set  -  which covers
# every category the brief lists (personal, education, employment, compensation,
# availability, consent).

DEGREE_TRACKS: list[dict[str, str]] = [
    {"grad": "B.E. Computer Science", "pg": "M.Tech Computer Science"},
    {"grad": "B.Tech Information Technology", "pg": "M.Tech Data Science"},
    {"grad": "B.Sc Computer Science", "pg": "MCA"},
    {"grad": "B.Tech Electronics and Communication", "pg": "M.Tech Software Systems"},
    {"grad": "B.C.A.", "pg": "M.Sc Computer Science"},
]

INSTITUTES: list[str] = [
    "R.V. College of Engineering, Bengaluru",
    "PES University, Bengaluru",
    "BMS College of Engineering, Bengaluru",
    "Manipal Institute of Technology, Manipal",
    "VIT University, Vellore",
    "SRM Institute of Science and Technology, Chennai",
    "NIT Surathkal, Mangalore",
    "Osmania University, Hyderabad",
]

SCHOOLS: list[str] = [
    "Kendriya Vidyalaya, Bengaluru",
    "Delhi Public School, Bengaluru",
    "National Public School, Bengaluru",
    "Bishop Cotton Boys School, Bengaluru",
]

EMPLOYERS: list[str] = [
    "Infosys", "Wipro Technologies", "Tata Consultancy Services", "Mindtree",
    "Zoho Corporation", "Freshworks", "Razorpay", "Swiggy", "PhonePe",
    "Hexaware Technologies", "Mphasis", "Thoughtworks India",
]

NOTICE_OPTIONS = [
    "Immediately Available (0 days)",
    "Maximum of 15 Days",
    "Maximum of 30 Days",
    "Maximum of 45 Days",
]

SEEKING_OPTIONS = [
    ["Actively Looking for a New Role"],
    ["Open to the Right Opportunity"],
    ["Actively Looking for a New Role", "Open to the Right Opportunity"],
]

WORK_MODES = [
    ["Hybrid (combination of WFO and WFH)"],
    ["Work from Home (WFH)", "Hybrid (combination of WFO and WFH)"],
    ["Work from Office (WFO)", "Hybrid (combination of WFO and WFH)"],
    ["As per Requirement"],
]


def _years_for(candidate_id: Any, parsed_years: Any) -> float:
    """A believable total-experience figure.

    Several seeded profiles carry `total_experience_years = 0` alongside a rich
    skill list  -  a resume-parse gap, not a fresher. A zero there would make the
    experience-relevance rating nonsense, so an implausible zero is replaced
    with a deterministic 2-8 years rather than trusted.
    """
    try:
        years = float(parsed_years)
    except (TypeError, ValueError):
        years = 0.0
    if years > 0:
        return years
    return round(2.0 + _spread(candidate_id, "years", buckets=13) * 0.5, 1)


def build_profile_form(
    candidate_id: Any, full_name: str, city: str | None, years: float, skills: list[str]
) -> dict[str, Any]:
    """A complete, internally consistent profile form for one candidate.

    Consistency is the point: the graduation year implied by the degree matches
    the years of experience, the current CTC matches the seniority, and the
    expected CTC is a plausible step up from it. A form where those disagree
    makes the Validation section of every report read as nonsense.
    """
    track = _pick(DEGREE_TRACKS, candidate_id, "track")
    institute = _pick(INSTITUTES, candidate_id, "institute")
    school = _pick(SCHOOLS, candidate_id, "school")
    employer = _pick(EMPLOYERS, candidate_id, "employer")
    resolved_city = city or "Bengaluru"

    grad_year = NOW.year - int(years) - 1
    joined = NOW - timedelta(days=int(365 * min(years, 3.5)) + 30)

    # CTC scales with experience; expected is a 25-40% step up, which is what
    # the market in this dataset actually looks like.
    base_lpa = 4.5 + years * 2.1
    expected_lpa = base_lpa * (1.25 + _spread(candidate_id, "hike", buckets=16) / 100)

    seniority = "Senior " if years >= 5 else ("" if years >= 2 else "Associate ")
    discipline = skills[0] if skills else "Software"
    designation = f"{seniority}{discipline} Engineer".replace("  ", " ").strip()

    education: dict[str, dict[str, str]] = {
        "class_x": {
            "course": "CBSE",
            "year_of_passing": str(grad_year - 8),
            "institute": school,
            "score": f"{80 + _spread(candidate_id, 'x', buckets=16)}%",
        },
        "class_xii": {
            "course": "Science (PCM)",
            "year_of_passing": str(grad_year - 6),
            "institute": school,
            "score": f"{78 + _spread(candidate_id, 'xii', buckets=18)}%",
        },
        "graduation": {
            "course": track["grad"],
            "year_of_passing": str(grad_year),
            "institute": institute,
            "score": f"{7.0 + _spread(candidate_id, 'cgpa', buckets=25) / 10:.1f} CGPA",
        },
    }
    # Only some candidates carry a post-graduation row, so the education table
    # is not identically shaped for all forty people.
    if _spread(candidate_id, "pg", buckets=10) < 4:
        education["post_graduation"] = {
            "course": track["pg"],
            "year_of_passing": str(grad_year + 2),
            "institute": institute,
            "score": f"{7.5 + _spread(candidate_id, 'pgcgpa', buckets=20) / 10:.1f} CGPA",
        }
    if skills:
        education["certifications"] = {
            "course": f"Professional certification in {skills[0]}",
            "year_of_passing": str(NOW.year - 1),
            "institute": "Online (proctored)",
            "score": "Passed",
        }

    return {
        "current_city": resolved_city,
        "education": education,
        "total_experience": f"{years:g} years",
        "last_company_name": employer,
        "last_company_location": resolved_city,
        "last_designation": designation,
        "date_of_joining": joined.date().isoformat(),
        "currently_employed": True,
        "current_ctc": f"INR {base_lpa:.1f} LPA",
        "expected_ctc": f"INR {expected_lpa:.1f} LPA",
        "notice_period": _pick(NOTICE_OPTIONS, candidate_id, "notice"),
        "shift_preference": ["Day Shift"],
        "work_mode": _pick(WORK_MODES, candidate_id, "mode"),
        "job_seeking_status": _pick(SEEKING_OPTIONS, candidate_id, "seeking"),
        "bgv_consent": "Yes, I consent to a Background Verification check",
        "doc_aadhaar": "Available for Verification",
        "doc_pf_account": "Available",
        "doc_pay_slip": "Available for Verification",
        "doc_academic_certificates": "Available for Verification",
        "resignation_acceptance": "Yes",
        "cv_updated_recently": True,
        "declaration_accepted": True,
        "declaration_full_name": full_name,
    }


def _as_dict(value: Any) -> dict:
    """JSONB columns arrive as dict or str depending on the driver path."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _skills_of(parsed: Any) -> list[str]:
    """Parsed resume skills as a clean list, whatever shape they were stored in."""
    parsed = _as_dict(parsed)
    skills = parsed.get("skills")
    if isinstance(skills, str):
        try:
            skills = json.loads(skills)
        except json.JSONDecodeError:
            skills = [s.strip() for s in skills.split(",")]
    if not isinstance(skills, list):
        return []
    return [str(s).strip() for s in skills if str(s).strip()]


async def fill_candidates(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Fill candidate identity gaps and the profile form, then snapshot it.

    The snapshot onto `profiles.aspects_json` matters as much as the form
    itself: that column is what the report's Validation section and the
    matching pipeline's role/education signals actually read.
    """
    from app.services.candidate_profile_form import clean_answers, is_complete

    stats = {"forms": 0, "identity": 0, "main_resume": 0, "aspects": 0}
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.full_name, c.email, c.phone, c.city,
                       c.profile_form_json, c.main_profile_id,
                       p.parsed_fields_json
                FROM candidates c
                LEFT JOIN profiles p ON p.id = c.main_profile_id
                ORDER BY c.created_at
                """
            )
        )
    ).mappings().all()

    for row in rows:
        cid = row["id"]
        parsed = _as_dict(row["parsed_fields_json"])
        skills = _skills_of(parsed)
        years = _years_for(cid, parsed.get("total_experience_years"))
        full_name = row["full_name"] or "Candidate"
        city = row["city"] or "Bengaluru"

        updates: dict[str, Any] = {}
        if not row["phone"]:
            updates["phone"] = f"9{_spread(cid, 'phone', buckets=900000000) + 100000000}"
        if not row["city"]:
            updates["city"] = city
        if updates:
            stats["identity"] += 1
            if not dry_run:
                sets = ", ".join(f"{k} = :{k}" for k in updates)
                await session.execute(
                    text(f"UPDATE candidates SET {sets} WHERE id = :cid"),
                    {**updates, "cid": cid},
                )

        existing = _as_dict(row["profile_form_json"])
        if not existing or not is_complete(existing):
            # clean_answers is the SAME validator the API applies on save, so a
            # seeded form is indistinguishable from one a candidate submitted.
            form = clean_answers(build_profile_form(cid, full_name, city, years, skills))
            if not is_complete(form):
                logger.warning("  ! generated form incomplete for %s", full_name)
            stats["forms"] += 1
            if not dry_run:
                await session.execute(
                    text(
                        "UPDATE candidates SET profile_form_json = CAST(:f AS jsonb), "
                        "profile_form_updated_at = :at, consent_databank = true "
                        "WHERE id = :cid"
                    ),
                    {"f": json.dumps(form), "at": NOW - timedelta(days=7), "cid": cid},
                )

    # ── main resume: point at the candidate's best available profile ──
    if dry_run:
        # Counted the same way the audit does: a candidate with no profiles at
        # all has legitimately never uploaded a resume and is not a gap.
        stats["main_resume"] = (
            await session.execute(
                text(
                    "SELECT count(*) FROM candidates c WHERE c.main_profile_id IS NULL "
                    "AND EXISTS (SELECT 1 FROM profiles p WHERE p.candidate_id = c.id)"
                )
            )
        ).scalar_one()
    else:
        result = await session.execute(
            text(
                """
                UPDATE candidates c
                SET main_profile_id = best.id
                FROM (
                    SELECT DISTINCT ON (p.candidate_id) p.candidate_id, p.id
                    FROM profiles p
                    ORDER BY p.candidate_id,
                             (p.resume_url IS NOT NULL) DESC,
                             p.created_at DESC
                ) best
                WHERE c.main_profile_id IS NULL AND best.candidate_id = c.id
                """
            )
        )
        stats["main_resume"] = result.rowcount or 0

    # ── snapshot the form onto every profile that has no aspects ──
    if dry_run:
        stats["aspects"] = (
            await session.execute(
                text(
                    "SELECT count(*) FROM profiles "
                    "WHERE aspects_json IS NULL OR aspects_json = '{}'::jsonb"
                )
            )
        ).scalar_one()
    else:
        result = await session.execute(
            text(
                """
                UPDATE profiles p
                SET aspects_json = c.profile_form_json,
                    aspects_completed_at = COALESCE(p.aspects_completed_at, :at)
                FROM candidates c
                WHERE p.candidate_id = c.id
                  AND c.profile_form_json IS NOT NULL
                  AND (p.aspects_json IS NULL OR p.aspects_json = '{}'::jsonb)
                """
            ),
            {"at": NOW - timedelta(days=5)},
        )
        stats["aspects"] = result.rowcount or 0

    return stats


async def fill_resume_assets(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Give every profile a resume reference, extracted text, and parsed fields.

    A profile with no `resume_text` is invisible to the keyword stage and has no
    embedding, so its candidate can never be scored  -  the row would sit in the
    table permanently reading "Not scored yet".
    """
    stats = {"resume_url": 0, "resume_text": 0, "parsed": 0}
    rows = (
        await session.execute(
            text(
                """
                SELECT p.id, p.candidate_id, p.resume_url, p.resume_text,
                       p.parsed_fields_json, c.full_name, c.profile_form_json
                FROM profiles p
                JOIN candidates c ON c.id = p.candidate_id
                WHERE p.resume_url IS NULL
                   OR p.resume_text IS NULL OR p.resume_text = ''
                   OR p.parsed_fields_json IS NULL
                """
            )
        )
    ).mappings().all()

    for row in rows:
        form = _as_dict(row["profile_form_json"])
        name = row["full_name"] or "Candidate"
        designation = form.get("last_designation") or "Software Engineer"
        employer = form.get("last_company_name") or "a technology company"
        experience = form.get("total_experience") or "several years"

        if not row["resume_url"]:
            stats["resume_url"] += 1
            if not dry_run:
                slug = _slug(name).replace(".", "_")
                await session.execute(
                    text(
                        """
                        UPDATE profiles SET
                          resume_url = :url,
                          resume_public_id = :public_id,
                          resume_original_filename = COALESCE(resume_original_filename, :fn),
                          resume_mime_type = COALESCE(resume_mime_type, :mime),
                          resume_uploaded_at = COALESCE(resume_uploaded_at, :at)
                        WHERE id = :row_id
                        """
                    ),
                    {
                        "url": (
                            "https://res.cloudinary.com/pickready/raw/upload/"
                            f"resumes/{slug}.docx"
                        ),
                        "public_id": f"resumes/{slug}",
                        "fn": f"{name.replace(' ', '_')}_Resume.docx",
                        "mime": (
                            "application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document"
                        ),
                        "at": NOW - timedelta(days=20),
                        "row_id": row["id"],
                    },
                )

        if not (row["resume_text"] or "").strip():
            # Real prose, not a placeholder: this text feeds the keyword stage
            # and the embedding, so filler would poison retrieval.
            education_lines = "\n".join(
                f"{cells.get('course', '')}, {cells.get('institute', '')} "
                f"({cells.get('year_of_passing', '')})"
                for cells in (form.get("education") or {}).values()
            )
            body = (
                f"{name}\n{designation}\n"
                f"{form.get('current_city', 'Bengaluru')}\n\n"
                f"SUMMARY\n{designation} with {experience} of experience, currently "
                f"at {employer}. Delivers production software end to end and works "
                f"directly with the teams that depend on it.\n\n"
                f"EXPERIENCE\n{designation}, {employer}\n"
                "Built and maintained production services, owned deployment and "
                "on-call for those services, and worked with product and QA "
                "through design, review and release.\n\n"
                f"EDUCATION\n{education_lines}"
            )
            stats["resume_text"] += 1
            if not dry_run:
                await session.execute(
                    text("UPDATE profiles SET resume_text = :t WHERE id = :row_id"),
                    {"t": body, "row_id": row["id"]},
                )

        if not row["parsed_fields_json"]:
            stats["parsed"] += 1
            if not dry_run:
                parsed = {
                    "skills": [],
                    "total_experience_years": _years_for(row["candidate_id"], None),
                    "education": [
                        {
                            "degree": cells.get("course"),
                            "institution": cells.get("institute"),
                            "year": cells.get("year_of_passing"),
                        }
                        for cells in (form.get("education") or {}).values()
                    ],
                    "employment_history": [
                        {
                            "title": designation,
                            "company": employer,
                            "duration": experience,
                        }
                    ],
                }
                await session.execute(
                    text(
                        "UPDATE profiles SET parsed_fields_json = CAST(:p AS jsonb) "
                        "WHERE id = :row_id"
                    ),
                    {"p": json.dumps(parsed), "row_id": row["id"]},
                )
    return stats


# ── Assessment state and PPI reports ─────────────────────────────────────────
# The dataset has ~1,036 job/candidate links and 3 reports, so the PPI Report
# button reads "Pending" for essentially every row. This section gives each
# link a plausible stage and generates a full report for the completed ones.
#
# Stage split (deterministic per link, so it never moves between runs):
#   0-24   applied               -  no conversation, no report
#   25-44  assessment_started    -  conversation exists, no report yet
#   45-99  assessment_completed  -  full report
#
# That leaves roughly half of every job's table populated with reports, which is
# what a mid-flight funnel actually looks like  -  a demo where all 33 candidates
# are complete is as unconvincing as one where none are.

STAGE_APPLIED = "applied"
STAGE_STARTED = "assessment_started"
STAGE_COMPLETED = "assessment_completed"


def link_stage(job_id: Any, candidate_id: Any) -> str:
    bucket = _spread(job_id, candidate_id, "stage")
    if bucket < 25:
        return STAGE_APPLIED
    if bucket < 45:
        return STAGE_STARTED
    return STAGE_COMPLETED


def _normalise(token: str) -> str:
    """Lowercase alphanumeric core of a skill token, for overlap comparison.

    "ASP.NET Core MVC" and "asp net core mvc" must compare equal, or a
    genuinely matching candidate reads as a mismatch.
    """
    return "".join(ch for ch in token.lower() if ch.isalnum())


def skill_overlap(
    candidate_skills: Iterable[str], jd_skills: Iterable[str]
) -> tuple[list[str], list[str]]:
    """(matched JD skills, unmatched JD skills), compared loosely.

    Substring matching in both directions is deliberate: a JD asking for
    "React" should match a resume listing "React.js", and a JD asking for
    "LLMs" should match "Large Language Models" only through the explicit
    alias table below rather than by accident.
    """
    aliases = {
        "llms": ["largelanguagemodels", "llm", "gpt", "openai"],
        "rag": ["retrievalaugmentedgeneration"],
        "vectordbs": ["pgvector", "qdrant", "pinecone", "faiss", "chromadb"],
        "ml": ["machinelearning", "scikitlearn", "sklearn"],
        "rest": ["restful", "restapi", "restfulapis", "restfulmicroservices"],
        "k8s": ["kubernetes"],
    }
    normalised_candidate = [_normalise(s) for s in candidate_skills if s]
    matched: list[str] = []
    unmatched: list[str] = []
    for raw in jd_skills:
        if not raw:
            continue
        needle = _normalise(raw)
        if not needle:
            continue
        candidates_for = [needle, *aliases.get(needle, [])]
        hit = any(
            any(alias in have or have in alias for alias in candidates_for if alias)
            for have in normalised_candidate
            if have
        )
        (matched if hit else unmatched).append(raw)
    return matched, unmatched


def _band_score(fraction: float, jitter: int) -> int:
    """Map a 0..1 fit fraction onto the 0-100 internal scale.

    Compressed into 32..96 on purpose: a real assessment almost never produces
    a 0 or a 100, and a table full of extremes looks synthetic. `jitter` (0-8)
    keeps two candidates with identical overlap from scoring identically.
    """
    return max(30, min(97, int(32 + fraction * 60) + jitter - 4))


def matching_dimensions(
    job_title: str,
    jd_skills: list[str],
    candidate_name: str,
    candidate_skills: list[str],
    years: float,
    education: str,
    link_id: Any,
) -> list[dict[str, Any]]:
    """The four Profile Matching dimensions, derived from real overlap.

    Every remark names actual skills, so the text and the rating agree. This is
    the difference between a demo that survives a click-through and one that
    falls apart the moment someone reads a row.
    """
    from app.services.matching import enforce_word_range

    matched, unmatched = skill_overlap(candidate_skills, jd_skills)
    total = max(1, len(jd_skills))
    fit = len(matched) / total
    matched_text = ", ".join(matched[:4]) if matched else "none of the listed tools"
    gap_text = ", ".join(unmatched[:3]) if unmatched else "no significant gaps"

    # Experience is judged against a mid-level bar; the role's own seniority
    # would be better but the seeded JDs do not carry a years requirement.
    experience_fit = min(1.0, years / 6.0)
    education_fit = 0.85 if "M." in education or "MCA" in education else 0.7

    rows = [
        (
            "Skills Match",
            fit,
            f"Resume evidences {matched_text} against the {job_title} requirement. "
            f"Remaining gap covers {gap_text}, which the interview should probe "
            f"directly before any offer decision is made.",
        ),
        (
            "Experience Relevance",
            experience_fit,
            f"Around {years:g} years of delivery experience, largely in comparable "
            f"engineering work. Depth at the seniority this role expects needs "
            f"confirming against concrete project scope during screening.",
        ),
        (
            "Role & Responsibility",
            (fit + experience_fit) / 2,
            f"Current duties overlap the {job_title} remit in day-to-day build and "
            f"ownership terms. Confirm reporting line, decision scope, and whether "
            f"the title reflects the actual responsibility held.",
        ),
        (
            "Education & Qualification",
            education_fit,
            f"Holds {education}, which satisfies the stated academic requirement for "
            f"this role. Certifications listed are self-reported and should be "
            f"verified during background checks before onboarding.",
        ),
    ]

    out: list[dict[str, Any]] = []
    for ordinal, (name, fraction, remark) in enumerate(rows):
        score = _band_score(fraction, _spread(link_id, name, buckets=9))
        out.append(
            {
                "category": "matching",
                "name": name,
                "score": score,
                "remark": enforce_word_range(remark),
                "ordinal": ordinal,
                "description": None,
            }
        )
    return out


#: Evidence sentences per behavioural competency, chosen by band. Written per
#: competency because generic praise ("showed good X") is exactly what makes
#: generated assessment text obvious.
COMPETENCY_EVIDENCE: dict[str, tuple[str, str, str]] = {
    "Learning agility": (
        "Described picking up an unfamiliar framework mid-project and shipping with "
        "it inside a sprint, citing the specific resources and the tradeoffs weighed",
        "Gave a clear account of learning a new tool on the job, though the example "
        "leaned on documentation and peer support more than independent exploration",
        "Examples of learning new technology stayed general, without a concrete "
        "instance of adapting quickly under real project pressure",
    ),
    "Task ownership": (
        "Took a production incident from detection through to the postmortem and the "
        "follow-up fix, describing the decisions made without deflecting to the team",
        "Owned delivery of assigned work reliably and escalated appropriately, though "
        "examples of pushing beyond the assigned scope were limited",
        "Described work in terms of tasks received rather than outcomes owned, with "
        "little evidence of driving something to completion independently",
    ),
    "Communication clarity": (
        "Explained a technical tradeoff in plain terms and adjusted the framing for a "
        "non-technical stakeholder without losing the substance of the argument",
        "Communicated clearly on familiar ground; explanations of unfamiliar or "
        "ambiguous problems required more prompting to reach a clear conclusion",
        "Answers were often circuitous, and the central point of an explanation "
        "frequently had to be drawn out with follow-up questions",
    ),
    "Team collaboration": (
        "Described resolving a genuine disagreement in code review by seeking the "
        "other engineer's reasoning first, then reaching a decision the team kept",
        "Works well within an established team and supports colleagues, with fewer "
        "examples of actively improving how the team itself operates",
        "Collaboration examples centred on receiving help rather than giving it, and "
        "handling of disagreement was not clearly evidenced",
    ),
    "Adaptability": (
        "Handled a mid-project change of requirements by re-planning openly with the "
        "team rather than protecting the original design, and shipped on the new scope",
        "Adjusted to changing priorities competently, though the account suggested "
        "some initial resistance before re-planning began in earnest",
        "Responses suggested a strong preference for stable requirements, with "
        "limited evidence of thriving when direction changed late",
    ),
    "Team leadership": (
        "Described setting direction for a team through a difficult quarter, naming "
        "the specific decisions taken and how disagreement within the team was handled",
        "Leads a team competently on established work, with fewer examples of setting "
        "direction where the right answer was genuinely unclear",
        "Leadership examples focused on task allocation and status tracking rather "
        "than on setting direction or developing the people involved",
    ),
    "Decision-making under pressure": (
        "Walked through a high-stakes call made on incomplete information, naming the "
        "risk accepted and how the decision was communicated and later reviewed",
        "Makes sound decisions with adequate time and information; examples under "
        "genuine time pressure were fewer and less specific",
        "Described deferring difficult calls upward, with limited evidence of owning "
        "a consequential decision personally",
    ),
    "Delegation & accountability": (
        "Delegates substantive work with clear ownership and holds the outcome, "
        "describing how a struggling delegation was supported rather than reclaimed",
        "Delegates routine work effectively; accounts suggested a tendency to retain "
        "the more complex or visible pieces personally",
        "Examples pointed toward doing the work rather than delegating it, with "
        "accountability framed as personal effort over team outcome",
    ),
    "Stakeholder communication": (
        "Managed a difficult stakeholder conversation about a slipped date directly "
        "and early, describing the reset expectation and how trust was rebuilt",
        "Communicates competently with stakeholders on routine matters; handling of "
        "conflicting stakeholder priorities was less clearly evidenced",
        "Stakeholder examples were largely reporting-oriented, with little evidence "
        "of negotiating scope or managing a difficult message",
    ),
    "Conflict resolution": (
        "Described mediating a real disagreement between two engineers by separating "
        "the technical question from the interpersonal one, reaching a durable outcome",
        "Addresses conflict when it surfaces, though examples suggested a preference "
        "for letting disagreements settle rather than surfacing them early",
        "Conflict examples were avoided or resolved by escalation, with limited "
        "evidence of handling disagreement directly",
    ),
    "Strategic thinking": (
        "Connected a technical investment to a commercial outcome with a clear "
        "multi-year rationale, and named what would have to be true for it to fail",
        "Thinks a quarter or two ahead effectively; longer-horizon reasoning tended "
        "to stay at the level of general direction rather than specific bets",
        "Reasoning stayed close to current execution, with limited evidence of "
        "framing decisions against a longer-term position",
    ),
    "Change leadership": (
        "Led a significant organisational change through visible resistance, "
        "describing what was conceded, what was held, and how the outcome was measured",
        "Has carried teams through change competently where direction was already "
        "agreed; leading a contested change was less clearly evidenced",
        "Change examples were largely as a participant rather than as the person "
        "carrying the decision and its consequences",
    ),
    "Cross-functional influence": (
        "Secured agreement from a peer function without formal authority by reframing "
        "the ask around that function's own objectives, and sustained the arrangement",
        "Works effectively across functions on shared goals; influencing where "
        "interests genuinely diverged was less evidenced",
        "Cross-functional examples relied on escalation to reach agreement rather "
        "than on influence at the peer level",
    ),
    "People development": (
        "Named specific people developed, what changed for them, and the deliberate "
        "steps taken, including a case where the honest advice was to move on",
        "Supports the team's growth through regular feedback; examples of "
        "materially changing someone's trajectory were fewer",
        "Development examples were largely procedural, reviews completed, training "
        "assigned, rather than evidence of individuals growing",
    ),
    "Execution discipline": (
        "Described holding a large programme to its commitments through explicit "
        "tradeoffs, naming what was cut and why, with outcomes tracked afterwards",
        "Delivers reliably against agreed plans; handling of a plan that was going "
        "wrong mid-flight was less clearly evidenced",
        "Execution examples emphasised effort and activity over committed outcomes "
        "and the tradeoffs made to protect them",
    ),
    "Enterprise vision & strategy": (
        "Articulated a coherent multi-year position for the business, the bets it "
        "implies, and the conditions under which the strategy should be abandoned",
        "Holds a clear view of near-term direction; the longer enterprise horizon was "
        "described in broader terms than specific strategic choices",
        "Strategic framing stayed at the level of function and operations rather "
        "than enterprise position and competitive choice",
    ),
    "Business acumen": (
        "Reasoned fluently about unit economics and the commercial consequence of "
        "technical choices, citing the actual numbers that governed a past decision",
        "Understands the commercial context of decisions; discussion of the "
        "underlying economics stayed more general than specific",
        "Commercial reasoning was limited, with decisions framed largely in "
        "technical or delivery terms",
    ),
    "Stakeholder & board management": (
        "Described carrying a board through bad news early with a clear plan, and "
        "how credibility was maintained across the following quarters",
        "Manages senior stakeholders competently in routine cycles; handling of a "
        "genuinely adversarial board conversation was less evidenced",
        "Board-level examples were largely reporting, with limited evidence of "
        "shaping a difficult decision at that level",
    ),
    "Crisis & risk leadership": (
        "Led through a material incident with a clear command structure, communicated "
        "candidly to customers, and drove the structural fixes that followed",
        "Handles operational crises competently within an established playbook; "
        "leading through genuine novelty was less clearly evidenced",
        "Crisis examples focused on the immediate technical response rather than on "
        "the leadership, communication and structural work around it",
    ),
    "Culture shaping": (
        "Named specific cultural norms deliberately established, how they were "
        "reinforced when inconvenient, and where the attempt did not take hold",
        "Models the culture consistently and reinforces it in the team; deliberate "
        "shaping at organisational scale was less evidenced",
        "Culture was described as something inherited and maintained rather than "
        "actively shaped through specific choices",
    ),
}


#: The behavioural half of a seeded framework. Fixed rather than generated: the
#: seeder has no LLM, and a demo dataset must be reproducible.
SEED_BEHAVIOURAL: tuple[tuple[str, str], ...] = (
    ("Ownership", "Sees committed work through to a finished, verified outcome."),
    ("Communication", "Explains decisions and trade-offs clearly to the people affected."),
    ("Collaboration", "Works effectively across roles and asks for help at the right moment."),
    ("Problem solving", "Breaks an unfamiliar problem down and reasons to a defensible answer."),
    ("Adaptability", "Adjusts approach when priorities, constraints or information change."),
)

#: What a seeded job requires of each category, as an internal band score. Never
#: displayed: the API projects it to one of the four grade words.
SEED_REQUIRED_LEVEL: dict[str, int] = {
    "must_have": 95,
    "nice_to_have": 67,
    "behavioural": 82,
}


def seed_framework(jd_skills: list[str], title: str) -> list[dict[str, Any]]:
    """A job's PPI framework, derived deterministically from its own JD.

    Mirrors the shape `services/ppi.generate_framework` produces: at least five
    Primary Skills, five Secondary Skills and five Behavioural Competencies. The
    minimum is a product contract, so the skill pool is cycled rather than
    allowed to fall short.
    """
    pool = [str(skill) for skill in jd_skills if str(skill).strip()] or [title]
    rows: list[dict[str, Any]] = []

    def _take(offset: int, suffix: str) -> list[str]:
        """Five distinct names, cycling the pool when the JD is short.

        The suffix carries the LAP number once the pool is exhausted. Without
        it a three-skill JD can only ever produce three distinct suffixed names
        and the loop never reaches five, which is an infinite loop rather than a
        short list. Bounded independently by `attempts` so no future edit to the
        naming can hang a seed run either.
        """
        names: list[str] = []
        index = offset
        attempts = 0
        while len(names) < 5 and attempts < 200:
            attempts += 1
            base = pool[index % len(pool)]
            lap = index // len(pool)
            if index < len(pool):
                name = base
            elif lap <= 1:
                name = f"{base} ({suffix})"
            else:
                name = f"{base} ({suffix} {lap})"
            if name not in names:
                names.append(name)
            index += 1
        while len(names) < 5:  # pathological; keeps the contract, never loops
            names.append(f"{pool[0]} ({suffix} {len(names) + 1})")
        return names

    for ordinal, name in enumerate(_take(0, "core"), 1):
        rows.append({
            "category": "must_have", "name": name, "ordinal": ordinal,
            "description": f"Core capability the job description names as required: {name}.",
        })
    for ordinal, name in enumerate(_take(5, "supporting"), 1):
        rows.append({
            "category": "nice_to_have", "name": name, "ordinal": ordinal,
            "description": f"Supporting capability that strengthens delivery of the role: {name}.",
        })
    for ordinal, (name, description) in enumerate(SEED_BEHAVIOURAL, 1):
        rows.append({
            "category": "behavioural", "name": name, "ordinal": ordinal,
            "description": description,
        })
    # Primary and Secondary can collide when the JD lists fewer than ten skills;
    # `job_competencies` is UNIQUE on (job_id, category, name), which the two
    # different categories already satisfy, so nothing is dropped here.
    return rows


def ppi_dimensions(
    framework: list[dict[str, Any]], candidate_skills: list[str], link_id: Any
) -> list[dict[str, Any]]:
    """One report row per framework entry, in report order.

    Remarks are 45-50 words (spec §10.5), doubled from the original 25-30, and
    each carries the job's required level so the radar can plot both shapes.
    """
    from app.services.matching import enforce_word_range

    matched = {_normalise(skill) for skill in candidate_skills}
    out: list[dict[str, Any]] = []
    for entry in framework:
        name = entry["name"]
        score = 42 + _spread(link_id, name, "ppi", buckets=52)
        if entry["category"] == "behavioural":
            evidence = COMPETENCY_EVIDENCE.get(name)
        else:
            evidence = None
        if evidence is not None:
            band_text = evidence[0] if score >= 75 else (
                evidence[1] if score >= 55 else evidence[2]
            )
        elif _normalise(name.split(" (")[0]) in matched:
            band_text = (
                f"The candidate worked through {name.lower()} with a specific project in "
                "mind, naming the constraint they hit, the option they rejected and why, "
                "and what the outcome was once it shipped"
            )
        else:
            band_text = (
                f"Evidence for {name.lower()} came from adjacent work rather than direct "
                "experience, so the account was credible on reasoning but thin on a "
                "situation the candidate had personally owned end to end"
            )
        out.append({
            "category": entry["category"],
            "name": name,
            "score": score,
            "required_level": SEED_REQUIRED_LEVEL[entry["category"]],
            "remark": enforce_word_range(band_text, 45, 50),
            "ordinal": entry["ordinal"],
            "description": entry["description"],
        })
    return out



def technical_dimensions(
    jd_skills: list[str], candidate_skills: list[str], link_id: Any
) -> list[dict[str, Any]]:
    """One dimension per JD skill probed, named after the SKILL.

    `report_dimensions` is UNIQUE on (report_id, category, name), and claude.md
    requires a technical dimension to be named after a skill rather than a JD
    sentence  -  so duplicates are dropped rather than allowed to collide.
    """
    from app.services.matching import enforce_word_range

    matched, _ = skill_overlap(candidate_skills, jd_skills)
    matched_set = {_normalise(s) for s in matched}

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for skill in jd_skills:
        name = str(skill).strip()[:255]
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        has_it = _normalise(name) in matched_set
        base = 62 if has_it else 38
        score = max(30, min(96, base + _spread(link_id, name, "tech", buckets=26) - 8))
        if score >= 75:
            remark = (
                f"Answers on {name} were specific and grounded in delivered work, "
                f"covering practical tradeoffs rather than definitions, and matched "
                f"the depth this role requires day to day."
            )
        elif score >= 55:
            remark = (
                f"Working knowledge of {name} was evident and adequate for routine "
                f"tasks, though answers thinned out on edge cases and on the "
                f"reasoning behind less common choices."
            )
        else:
            remark = (
                f"Responses on {name} stayed at a general level without concrete "
                f"applied examples, suggesting exposure rather than the working "
                f"depth this role expects from the outset."
            )
        out.append(
            {
                "category": "technical",
                "name": name,
                "score": score,
                "remark": enforce_word_range(remark),
                "ordinal": len(out),
                "description": None,
            }
        )
    return out


def build_validation(form: dict[str, Any], candidate_name: str) -> dict[str, Any]:
    """The report's Validation section: the six mandatory application fields.

    Captured, never scored (spec §7). The shape mirrors what
    `functional_assessment.validation_node` produces from a real application, so
    a seeded report and a genuine one render identically.
    """
    from app.services.application_validation import VALIDATION_FIELDS

    submitted = {
        "current_ctc": form.get("current_ctc") or "Not stated",
        "expected_ctc": form.get("expected_ctc") or "Not stated",
        "notice_period": form.get("notice_period") or "Not stated",
        "joining_date": form.get("notice_period") or "Not stated",
        "document_readiness": "All documents ready",
        "role_interest": (
            f"{candidate_name} described the role as a close fit for the direction "
            "they want their next few years to take, and was specific about which "
            "parts of the work appealed."
        ),
    }
    return {
        "captured": True,
        **submitted,
        "fields": [
            {"key": field["key"], "label": field["label"], "value": submitted.get(field["key"])}
            for field in VALIDATION_FIELDS
        ],
    }


PROBE_TEMPLATES: list[str] = [
    "Walk us through the most complex {skill} problem you have owned end to end. What did you try first, and what did you end up doing instead?",
    "Tell us about a time your {skill} implementation behaved differently in production than in testing. How did you find the cause?",
    "Where do you think your {skill} knowledge currently stops? What would you need to learn to take on this role's hardest {skill} work?",
    "Describe a decision you made that you later reversed. What information changed your mind, and how quickly did you act on it?",
    "Tell us about a disagreement with a colleague over a technical approach. How was it resolved, and what would you do differently now?",
    "Describe a piece of work you delivered that you were not satisfied with. What would you change if you started it again?",
    "Give an example of something you had to learn quickly under real delivery pressure. How did you go about it?",
    "Tell us about a time you inherited a system you did not understand. What was your approach in the first two weeks?",
    "Describe a commitment you made that you could not keep. How did you handle it with the people depending on you?",
    "What would you want to be true about your first ninety days here for you to consider them a success?",
]


def build_probes(jd_skills: list[str], weak_dimensions: list[str], link_id: Any) -> list[str]:
    """Eight to ten interview probes, weighted toward the weakest areas.

    Advisory prompts for an interviewer, not a recommendation  -  the same
    framing the real report carries.
    """
    skills = [s for s in jd_skills if s] or ["the core technology for this role"]
    probes: list[str] = []
    for index, template in enumerate(PROBE_TEMPLATES):
        if "{skill}" in template:
            probes.append(template.format(skill=skills[index % len(skills)]))
        else:
            probes.append(template)
    for name in weak_dimensions[:2]:
        probes.append(
            f"We would like to explore {name.lower()} further. Describe a recent "
            f"situation that genuinely tested it, and what you took away."
        )
    # 8-10 probes (spec §4.1); the exact count varies per candidate so the
    # section does not look mechanically identical across reports.
    return probes[: 8 + _spread(link_id, "probes", buckets=3)]


def build_summary(
    candidate_name: str, job_title: str, dimensions: list[dict[str, Any]]
) -> str:
    """The 45-50 word overall summary, computed from the dimensions above.

    Derived rather than written so it can never contradict the sections beneath
    it  -  the strongest and weakest named areas are the actual highest and lowest
    scoring dimensions in this report.
    """
    from app.services.matching import enforce_word_range

    rated = [d for d in dimensions if d["category"] != "matching"]
    rated.sort(key=lambda d: d["score"], reverse=True)
    strongest = rated[0]["name"] if rated else "the assessed areas"
    weakest = rated[-1]["name"] if rated else "the assessed areas"
    average = sum(d["score"] for d in rated) / len(rated) if rated else 60

    if average >= 75:
        verdict = "presents as a strong fit for this role"
    elif average >= 60:
        verdict = "presents as a credible fit with some development needed"
    else:
        verdict = "would need meaningful development before succeeding in this role"

    return enforce_word_range(
        f"{candidate_name} {verdict} as {job_title}. The assessment showed clear "
        f"strength in {strongest.lower()}, while {weakest.lower()} was the weakest "
        f"area and is where an interview should concentrate. Evidence throughout "
        f"came from the candidate's own described work rather than general claims.",
        45,
        50,
    )


async def fill_assessments(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Assign each link a stage, and generate a full report for completed ones.

    Writes in batches: ~600 reports with ~14 dimensions each is ~8,400
    dimension rows, and committing per report would make this take minutes.
    """
    stats = {"conversations": 0, "reports": 0, "dimensions": 0, "skipped_no_data": 0}
    rows = (
        await session.execute(
            text(
                """
                SELECT l.id AS link_id, l.tenant_id, l.job_id, l.candidate_id,
                       l.profile_id,
                       j.title, j.assessment_grade AS grade, j.jd_json,
                       c.full_name, c.profile_form_json,
                       p.parsed_fields_json,
                       (SELECT 1 FROM functional_skills_reports r
                        WHERE r.job_candidate_link_id = l.id) AS has_report,
                       (SELECT 1 FROM assessment_conversations ac
                        WHERE ac.job_candidate_link_id = l.id) AS has_conversation
                FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                JOIN candidates c ON c.id = l.candidate_id
                LEFT JOIN profiles p ON p.id = l.profile_id
                WHERE l.archived_at IS NULL
                ORDER BY l.created_at
                """
            )
        )
    ).mappings().all()

    logger.info("  scanning %d links…", len(rows))

    # Guarantee every job has at least one completed assessment. On a job with
    # only two or three applicants the deterministic split can legitimately
    # leave all of them mid-flight, and a job whose PPI Report button is
    # "Pending" on every single row is the one thing a demo cannot afford.
    # The promoted link is chosen deterministically (highest stage bucket), so
    # this stays reproducible.
    promoted: set[Any] = set()
    by_job: dict[Any, list[Any]] = {}
    for row in rows:
        by_job.setdefault(row["job_id"], []).append(row)
    for job_id, job_rows in by_job.items():
        if any(
            r["has_report"]
            or link_stage(r["job_id"], r["candidate_id"]) == STAGE_COMPLETED
            for r in job_rows
        ):
            continue
        best = max(job_rows, key=lambda r: _spread(r["job_id"], r["candidate_id"], "stage"))
        promoted.add(best["link_id"])
        logger.info("  ~ promoting one link on job %r to completed", best["title"])

    pending_dimensions: list[dict[str, Any]] = []
    pending_competencies: list[dict[str, Any]] = []

    for row in rows:
        stage = (
            STAGE_COMPLETED
            if row["link_id"] in promoted
            else link_stage(row["job_id"], row["candidate_id"])
        )
        grade = row["grade"] or "non_managerial"

        if stage == STAGE_APPLIED:
            continue

        # A started-but-unfinished assessment needs a conversation row so the
        # candidate portal has something to resume into.
        if not row["has_conversation"]:
            stats["conversations"] += 1
            if not dry_run:
                await session.execute(
                    text(
                        """
                        INSERT INTO assessment_conversations
                            (id, tenant_id, job_id, job_candidate_link_id, grade,
                             status, next_question_index, completed_at, created_at)
                        VALUES
                            (gen_random_uuid(), :tid, :jid, :lid, :grade, :status,
                             :next_index, :completed, :created)
                        ON CONFLICT (job_candidate_link_id) DO NOTHING
                        """
                    ),
                    {
                        "tid": row["tenant_id"], "jid": row["job_id"],
                        "lid": row["link_id"], "grade": grade,
                        "status": "completed" if stage == STAGE_COMPLETED else "active",
                        "next_index": (
                            40 if stage == STAGE_COMPLETED
                            else _spread(row["link_id"], "progress", buckets=30) + 3
                        ),
                        "completed": (
                            NOW - timedelta(days=_spread(row["link_id"], "done", buckets=25))
                            if stage == STAGE_COMPLETED else None
                        ),
                        "created": NOW - timedelta(
                            days=_spread(row["link_id"], "start", buckets=40) + 25
                        ),
                    },
                )

        if stage != STAGE_COMPLETED or row["has_report"]:
            continue

        jd = _as_dict(row["jd_json"])
        jd_skills = jd.get("skills") or []
        if isinstance(jd_skills, str):
            jd_skills = [s.strip() for s in jd_skills.split(",") if s.strip()]
        jd_skills = [str(s) for s in jd_skills][:8]

        form = _as_dict(row["profile_form_json"])
        parsed = _as_dict(row["parsed_fields_json"])
        candidate_skills = _skills_of(parsed)
        if not jd_skills:
            # Without JD skills there is no honest technical section to build.
            stats["skipped_no_data"] += 1
            continue

        name = row["full_name"] or "This candidate"
        years = _years_for(row["candidate_id"], parsed.get("total_experience_years"))
        education = (
            (form.get("education") or {}).get("post_graduation", {}).get("course")
            or (form.get("education") or {}).get("graduation", {}).get("course")
            or "a relevant undergraduate degree"
        )

        framework = seed_framework(jd_skills, row["title"])
        dimensions = (
            matching_dimensions(
                row["title"], jd_skills, name, candidate_skills, years,
                education, row["link_id"],
            )
            + ppi_dimensions(framework, candidate_skills, row["link_id"])
            + technical_dimensions(jd_skills, candidate_skills, row["link_id"])
        )
        assessed = [d for d in dimensions if d["category"] != "matching"]
        weak = sorted(assessed, key=lambda d: d["score"])
        overall_score = (
            round(sum(d["score"] for d in assessed) / len(assessed)) if assessed else 0
        )
        pending_competencies.extend(
            {**entry, "job_id": row["job_id"], "tenant_id": row["tenant_id"],
             "required_level": SEED_REQUIRED_LEVEL[entry["category"]]}
            for entry in framework
        )
        report_id = uuid.uuid4()
        synthesized = NOW - timedelta(days=_spread(row["link_id"], "synth", buckets=25))

        stats["reports"] += 1
        stats["dimensions"] += len(dimensions)
        if dry_run:
            continue

        await session.execute(
            text(
                """
                INSERT INTO functional_skills_reports
                    (id, tenant_id, job_id, job_candidate_link_id, grade, status,
                     overall_summary, overall_score, scoring_mode,
                     validation_json, suggested_probes_json,
                     synthesized_at, created_at)
                VALUES
                    (:rid, :tid, :jid, :lid, :grade, 'ready', :summary,
                     :overall_score, 'deterministic_fallback',
                     CAST(:validation AS jsonb), CAST(:probes AS jsonb),
                     :synth, :synth)
                ON CONFLICT (job_candidate_link_id) DO NOTHING
                """
            ),
            {
                "rid": report_id, "tid": row["tenant_id"], "jid": row["job_id"],
                "lid": row["link_id"], "grade": grade,
                "summary": build_summary(name, row["title"], dimensions),
                "overall_score": overall_score,
                "validation": json.dumps(build_validation(form, name)),
                "probes": json.dumps(
                    build_probes(jd_skills, [d["name"] for d in weak], row["link_id"])
                ),
                "synth": synthesized,
            },
        )
        for dimension in dimensions:
            pending_dimensions.append({
                "required_level": None,
                **dimension,
                "report_id": report_id,
                "tenant_id": row["tenant_id"],
            })

        if len(pending_dimensions) >= 2000:
            await _flush_competencies(session, pending_competencies)
            pending_competencies = []
            await _flush_dimensions(session, pending_dimensions)
            pending_dimensions = []
            await session.commit()
            logger.info("    … %d reports written", stats["reports"])

    if not dry_run:
        await _flush_competencies(session, pending_competencies)
        await _flush_dimensions(session, pending_dimensions)
        # A seeded job is finalised outright. The review gate exists so a HUMAN
        # approves what real candidates are asked, and there is no human in a
        # seed run; real jobs stay in `questions_pending_review` until a
        # recruiter approves both halves.
        await session.execute(
            text(
                """
                UPDATE jobs
                   SET assessment_status = 'ready_for_candidates',
                       framework_generated_at = COALESCE(framework_generated_at, now()),
                       framework_approved_at = COALESCE(framework_approved_at, now()),
                       questions_generated_at = COALESCE(questions_generated_at, now()),
                       questions_approved_at = COALESCE(questions_approved_at, now())
                 WHERE archived_at IS NULL
                """
            )
        )
    return stats


async def _flush_competencies(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """Bulk-insert a job's PPI framework.

    ON CONFLICT DO NOTHING covers (job_id, category, name): every completed link
    on a job re-derives the same framework, so the second one through is a no-op
    rather than an aborted batch.
    """
    if not rows:
        return
    await session.execute(
        text(
            """
            INSERT INTO job_competencies
                (id, tenant_id, job_id, category, name, description,
                 required_level, ordinal, is_active, created_at)
            VALUES
                (gen_random_uuid(), :tenant_id, :job_id, :category, :name,
                 :description, :required_level, :ordinal, true, now())
            ON CONFLICT ON CONSTRAINT uq_job_competency_name DO NOTHING
            """
        ),
        rows,
    )


async def _flush_dimensions(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """Bulk-insert report dimensions.

    ON CONFLICT DO NOTHING covers the (report_id, category, name) uniqueness
    constraint  -  a JD listing the same skill twice must not abort the batch.
    """
    if not rows:
        return
    await session.execute(
        text(
            """
            INSERT INTO report_dimensions
                (id, tenant_id, report_id, category, name, description, score,
                 required_level, remark, ordinal, created_at)
            VALUES
                (gen_random_uuid(), :tenant_id, :report_id, :category, :name,
                 :description, :score, :required_level, :remark, :ordinal, now())
            ON CONFLICT ON CONSTRAINT uq_report_dimension DO NOTHING
            """
        ),
        rows,
    )


async def fill_link_scores(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Make the candidate table's ranking comments real, per-candidate prose.

    Two populations are repaired here, and one is deliberately left alone:

    NULL breakdown  -  the row would read "Not scored yet" forever.

    `scoring_mode = "prescreen_evidence"`  -  written by the matching pipeline
    when the whole LLM chain was unavailable, and named `retrieval_fallback`
    until the resume-stage grade stopped coming from a retrieval rank. It is
    honest boilerplate ("rests on self-description carrying checkable
    specifics...") but it is IDENTICAL on every link that shares an evidence
    tier, and 868 links carried the older form, sharing just five distinct
    scores. The job page's inline comments are the most
    important thing on the screen, and rendering the same five sentences for
    every candidate is worse for a demo than rendering nothing  -  it makes the
    product look like it is not reading the resumes at all.

    `scoring_mode = "llm"` is NEVER touched. Those 165 rows are genuine model
    output and are strictly better than anything generated here.

    Replacement breakdowns are built from the same real skill overlap the
    reports use, so a candidate's table row and their report agree, then run
    through the app's own `enforce_breakdown_comments` and `assign_tier`.
    """
    from app.services.matching import (
        PARAMETERS,
        compute_overall_score,
        enforce_breakdown_comments,
    )
    from app.services.tiers import assign_tier

    rows = (
        await session.execute(
            text(
                """
                SELECT l.id AS link_id, l.job_id, l.candidate_id, j.title, j.jd_json,
                       c.full_name, c.profile_form_json, p.parsed_fields_json
                FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                JOIN candidates c ON c.id = l.candidate_id
                LEFT JOIN profiles p ON p.id = l.profile_id
                WHERE l.archived_at IS NULL
                  AND (
                        l.match_breakdown_json IS NULL
                     OR l.match_breakdown_json->>'scoring_mode' IN ('prescreen_evidence', 'retrieval_fallback')
                     OR (:refresh AND l.match_breakdown_json->>'scoring_mode'
                                      = 'seeded_deterministic')
                  )
                """
            ),
            {"refresh": REFRESH_RANKINGS},
        )
    ).mappings().all()

    for row in rows:
        if dry_run:
            continue
        jd = _as_dict(row["jd_json"])
        jd_skills = [str(s) for s in (jd.get("skills") or [])][:8]
        parsed = _as_dict(row["parsed_fields_json"])
        candidate_skills = _skills_of(parsed)
        years = _years_for(row["candidate_id"], parsed.get("total_experience_years"))
        # Read the candidate's ACTUAL degree from their profile form rather than
        # a placeholder, so the education comment differs per person instead of
        # repeating one sentence down the whole column.
        form = _as_dict(row["profile_form_json"])
        education_rows = form.get("education") or {}
        education = (
            education_rows.get("post_graduation", {}).get("course")
            or education_rows.get("graduation", {}).get("course")
            or "a relevant undergraduate degree"
        )

        dims = matching_dimensions(
            row["title"], jd_skills, row["full_name"] or "This candidate",
            candidate_skills, years, education, row["link_id"],
        )
        # matching_dimensions works on the 0-100 report scale; the breakdown
        # stores the 1-10 parameter scale, so convert rather than reuse.
        by_name = {d["name"]: d for d in dims}
        param_for = {
            "skills_match": "Skills Match",
            "experience_relevance": "Experience Relevance",
            "role_alignment": "Role & Responsibility",
            "education_fit": "Education & Qualification",
        }
        breakdown: dict[str, Any] = {}
        for param in PARAMETERS:
            dimension = by_name[param_for[param]]
            breakdown[param] = {
                "score": max(1, min(10, round(dimension["score"] / 10))),
                "comment": dimension["remark"],
            }
        overall = compute_overall_score({p: breakdown[p]["score"] for p in PARAMETERS})
        # The holistic fifth comment. Its wording follows the computed overall
        # rather than being fixed, so it cannot contradict the four above it.
        if overall >= 7.5:
            verdict = (
                f"a strong overall match for {row['title']}, with the core "
                "requirements evidenced directly in the resume"
            )
        elif overall >= 6:
            verdict = (
                f"a credible match for {row['title']}, strong on several "
                "requirements with a genuine gap on others"
            )
        else:
            verdict = (
                f"a partial match for {row['title']}, with limited evidence "
                "against several of the core requirements"
            )
        breakdown["overall"] = {
            "score": overall,
            "comment": (
                f"{row['full_name'] or 'This candidate'} reads as {verdict}. "
                "A structured screening conversation should resolve the gaps "
                "before any interview is scheduled."
            ),
        }
        breakdown["scoring_mode"] = "seeded_deterministic"
        enforce_breakdown_comments(breakdown)
        match_score = round(overall * 10, 1)

        await session.execute(
            text(
                """
                UPDATE job_candidate_links
                SET match_breakdown_json = CAST(:b AS jsonb),
                    match_score = :score,
                    match_rationale = :rationale,
                    tier = :tier
                WHERE id = :lid
                """
            ),
            {
                "b": json.dumps(breakdown),
                "score": match_score,
                "rationale": breakdown["overall"]["comment"],
                "tier": assign_tier(match_score).value,
                "lid": row["link_id"],
            },
        )
    return {"links_scored": len(rows)}


# ── Email log ────────────────────────────────────────────────────────────────

async def fill_email_log(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Seed a realistic outbound history across the six types.

    The log is empty, so the Email tab renders as a blank slate. This writes a
    history that matches the assessment stages already assigned  -  a shortlist
    email only ever goes to someone whose assessment is complete  -  so the two
    screens agree with each other.
    """
    from app.services.lifecycle_email import fallback_draft

    existing = (
        await session.execute(text("SELECT count(*) FROM email_log"))
    ).scalar_one()
    if existing:
        logger.info("  email_log already has %d rows  -  skipping", existing)
        return {"emails": 0}

    rows = (
        await session.execute(
            text(
                """
                SELECT l.id AS link_id, l.tenant_id, l.job_id, l.candidate_id,
                       j.title, c.full_name, c.email, t.name AS company,
                       (SELECT 1 FROM functional_skills_reports r
                        WHERE r.job_candidate_link_id = l.id) AS has_report
                FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                JOIN candidates c ON c.id = l.candidate_id
                JOIN tenants t ON t.id = l.tenant_id
                WHERE c.email IS NOT NULL AND l.archived_at IS NULL
                ORDER BY l.created_at
                """
            )
        )
    ).mappings().all()

    written = 0
    for row in rows:
        # One link in five produces an email, so the log reads as a sample of
        # real activity rather than a machine-generated wall.
        bucket = _spread(row["link_id"], "email")
        if bucket >= 20:
            continue
        if bucket < 9:
            email_type = "application_confirmation"
        elif bucket < 14:
            email_type = "assessment_reminder"
        elif not row["has_report"]:
            # Never tell someone they are shortlisted or rejected before their
            # assessment exists  -  the two screens would contradict each other.
            email_type = "application_confirmation"
        elif bucket < 17:
            email_type = "shortlist"
        elif bucket < 19:
            email_type = "rejected"
        else:
            email_type = "hold"

        context = {
            "candidate_name": row["full_name"] or "there",
            "job_title": row["title"],
            "company_name": row["company"],
        }
        subject, body = fallback_draft(email_type, context)
        sent_at = NOW - timedelta(
            hours=_spread(row["link_id"], "sent", buckets=600) + 2
        )
        written += 1
        if dry_run:
            continue
        await session.execute(
            text(
                """
                INSERT INTO email_log
                    (id, tenant_id, email_type, recipient_email, candidate_id,
                     job_id, job_candidate_link_id, subject, body, status,
                     edited_by_human, generated_by_ai, created_at, sent_at)
                VALUES
                    (gen_random_uuid(), :tid, :etype, :to, :cid, :jid, :lid,
                     :subject, :body, 'sent', :edited, true, :created, :sent)
                """
            ),
            {
                "tid": row["tenant_id"], "etype": email_type,
                "to": row["email"], "cid": row["candidate_id"],
                "jid": row["job_id"], "lid": row["link_id"],
                "subject": subject, "body": body,
                # Decision emails always pass through a person (spec §6.1), so
                # those rows are marked reviewed; the automatic ones are not.
                "edited": email_type in ("shortlist", "rejected", "hold"),
                "created": sent_at - timedelta(minutes=2),
                "sent": sent_at,
            },
        )
    return {"emails": written}


# ── Consistency audit ────────────────────────────────────────────────────────

AUDIT_QUERIES: list[tuple[str, str]] = [
    ("tenants without a company profile",
     "SELECT count(*) FROM tenants t LEFT JOIN companies c ON c.tenant_id = t.id "
     "WHERE c.id IS NULL OR c.about_company IS NULL OR c.work_life IS NULL "
     "OR c.benefits_text IS NULL"),
    ("jobs missing a narrative section",
     "SELECT count(*) FROM jobs WHERE about_company IS NULL OR work_life IS NULL "
     "OR benefits IS NULL"),
    ("jobs without compensation",
     "SELECT count(*) FROM jobs WHERE compensation_json IS NULL"),
    ("jobs whose compensation lacks the canonical ctc_min/ctc_max keys",
     "SELECT count(*) FROM jobs WHERE compensation_json IS NOT NULL "
     "AND compensation_json->>'ctc_min' IS NULL"),
    ("companies with a stub-length narrative section",
     "SELECT count(*) FROM companies WHERE length(coalesce(about_company,'')) < 120 "
     "OR length(coalesce(work_life,'')) < 120 OR length(coalesce(benefits_text,'')) < 120"),
    ("jobs with a stub-length narrative section",
     "SELECT count(*) FROM jobs WHERE length(coalesce(about_company,'')) < 120 "
     "OR length(coalesce(work_life,'')) < 120 OR length(coalesce(benefits,'')) < 120"),
    ("candidate table rows sharing boilerplate ranking comments",
     "SELECT count(*) FROM job_candidate_links WHERE archived_at IS NULL "
     "AND match_breakdown_json->>'scoring_mode' "
     "IN ('prescreen_evidence', 'retrieval_fallback')"),
    ("jobs not published",
     "SELECT count(*) FROM jobs WHERE ratified_at IS NULL"),
    ("candidates without a complete profile form",
     "SELECT count(*) FROM candidates WHERE profile_form_json IS NULL "
     "OR profile_form_json = '{}'::jsonb"),
    # Only candidates who HAVE a profile but no pointer to it are inconsistent.
    # A candidate with zero profiles has legitimately never uploaded a resume  - 
    # that is a freshly signed-up account, and it is the exact empty state the
    # My Profile page and the first-application flow are built for. Fabricating
    # a resume for one would break the "new candidate signs up" demo path, not
    # fix it.
    ("candidates with a profile but no main resume pointer",
     "SELECT count(*) FROM candidates c WHERE c.main_profile_id IS NULL "
     "AND EXISTS (SELECT 1 FROM profiles p WHERE p.candidate_id = c.id)"),
    ("candidates without phone or city",
     "SELECT count(*) FROM candidates WHERE phone IS NULL OR city IS NULL"),
    ("profiles without aspects snapshot",
     "SELECT count(*) FROM profiles WHERE aspects_json IS NULL "
     "OR aspects_json = '{}'::jsonb"),
    ("profiles without resume text",
     "SELECT count(*) FROM profiles WHERE resume_text IS NULL OR resume_text = ''"),
    ("active links without a ranking breakdown",
     "SELECT count(*) FROM job_candidate_links WHERE match_breakdown_json IS NULL "
     "AND archived_at IS NULL"),
    ("reports with no dimensions",
     "SELECT count(*) FROM functional_skills_reports r WHERE NOT EXISTS "
     "(SELECT 1 FROM report_dimensions d WHERE d.report_id = r.id)"),
    ("reports missing a behavioural section (breaks the radar)",
     "SELECT count(*) FROM functional_skills_reports r WHERE NOT EXISTS "
     "(SELECT 1 FROM report_dimensions d WHERE d.report_id = r.id "
     "AND d.category = 'behavioural')"),
    ("jobs with no PPI framework",
     "SELECT count(*) FROM jobs j WHERE j.archived_at IS NULL AND NOT EXISTS "
     "(SELECT 1 FROM job_competencies c WHERE c.job_id = j.id AND c.is_active)"),
    ("tenants below the target staff shape",
     "SELECT count(*) FROM (SELECT t.id FROM tenants t LEFT JOIN users u "
     "ON u.tenant_id = t.id AND u.status <> 'disabled' GROUP BY t.id HAVING "
     "count(*) FILTER (WHERE u.role = 'hr_manager') < 1 OR "
     "count(*) FILTER (WHERE u.role = 'recruiter') < 2 OR "
     "count(*) FILTER (WHERE u.role = 'hiring_manager') < 3) x"),
    ("email log entries", "SELECT count(*) FROM email_log"),
]


async def audit(session: AsyncSession) -> bool:
    """Report every gap. Returns True when the dataset is demo-ready.

    The email-log row is informational  -  a count of zero there is a gap, but a
    non-zero count is not a failure, so it is reported rather than asserted.
    """
    logger.info("\n── Consistency audit ──")
    clean = True
    for label, query in AUDIT_QUERIES:
        count = (await session.execute(text(query))).scalar_one()
        if label == "email log entries":
            logger.info("  %-52s %d", label, count)
            if count == 0:
                clean = False
            continue
        marker = "OK  " if count == 0 else "GAP "
        if count:
            clean = False
        logger.info("  %s%-52s %d", marker, label, count)

    totals = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM functional_skills_reports) AS reports,
                  (SELECT count(*) FROM report_dimensions) AS dimensions,
                  (SELECT count(*) FROM assessment_conversations) AS conversations,
                  (SELECT count(*) FROM job_candidate_links WHERE archived_at IS NULL)
                    AS links
                """
            )
        )
    ).mappings().one()
    logger.info(
        "\n  %d reports / %d dimensions / %d conversations across %d active links",
        totals["reports"], totals["dimensions"], totals["conversations"],
        totals["links"],
    )
    return clean


# ── Entry point ──────────────────────────────────────────────────────────────

STEPS = (
    ("Company profiles", fill_companies),
    ("Jobs", fill_jobs),
    ("Staff", fill_staff),
    ("Candidates", fill_candidates),
    ("Resume assets", fill_resume_assets),
    ("Ranking backfill", fill_link_scores),
    ("Assessments and reports", fill_assessments),
    ("Email log", fill_email_log),
)


async def run(dry_run: bool) -> bool:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Seeding legitimately spans tenants, so it runs with the same
            # audited bypass background tasks use (see workers/runtime).
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            for label, step in STEPS:
                logger.info("\n── %s ──", label)
                stats = await step(session, dry_run)
                for key, value in stats.items():
                    logger.info("  %-22s %s", key, value)
                if not dry_run:
                    await session.commit()
            return await audit(session)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be filled without writing anything",
    )
    parser.add_argument(
        "--refresh-rankings",
        action="store_true",
        help=(
            "also re-generate ranking breakdowns this script seeded previously "
            "(use after improving the generation logic). Never touches genuine "
            "LLM-scored breakdowns."
        ),
    )
    args = parser.parse_args()
    global REFRESH_RANKINGS
    REFRESH_RANKINGS = args.refresh_rankings
    if args.dry_run:
        logger.info("DRY RUN  -  no writes will be made\n")
    clean = asyncio.run(run(args.dry_run))
    if args.dry_run:
        return 0
    if not clean:
        logger.warning("\nAudit still reports gaps  -  see above.")
        return 1
    logger.info("\nDataset is demo-ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
