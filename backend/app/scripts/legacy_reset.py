"""The legacy data reset: survey, export, purge, and the separate regrade.

    python -m app.scripts.legacy_reset --survey
    python -m app.scripts.legacy_reset --export
    python -m app.scripts.legacy_reset --purge                 # dry run
    python -m app.scripts.legacy_reset --purge --confirm --export-dir <dir> \
        --actor "<operator>" [--actor-user-id <uuid>]
    python -m app.scripts.legacy_reset --regrade               # plan only
    python -m app.scripts.legacy_reset --regrade --confirm     # needs live keys

THIS IS A PRODUCTION DATA OPERATION RUNNING AGAINST DEVELOPMENT DATA
--------------------------------------------------------------------
spec-doc6 section 6 says to treat it as one, and the discipline is the point.
Four properties carry that discipline, and each of them is enforced in code
rather than described in a runbook:

  * NOTHING IS DELETED THAT WAS NOT CLASSIFIED FIRST. `CLASSIFICATION` names
    every table in the schema and puts it in a bucket. `assert_classified`
    compares that list against the tables the database actually has and RAISES
    on a gap, so a table added between the survey and the purge stops the purge
    instead of being quietly skipped. spec-doc6 section 6.2 requires exactly
    this and it is the most valuable part of the survey.

  * NOTHING IS DELETED THAT WAS NOT EXPORTED, AND NO EXPORT IS TRUSTED THAT WAS
    NOT TEST-RESTORED. `--purge --confirm` refuses an export directory whose
    manifest does not say `restore_verified: true`, and refuses one whose
    recorded row counts no longer match the database. An export that has never
    been restored is not a backup, and an export taken before somebody else
    wrote more rows is a backup of a different database.

  * A HUMAN OBSERVATION IS NEVER CASCADE-DELETED WITH A MACHINE ARTIFACT.
    `review_dispositions` and `calibration_records` referenced `evaluations`
    with ON DELETE CASCADE until migration 0062. The purge DETACHES them --
    copying the job and application context onto the row first, then nulling
    the reference and recording why -- before it deletes anything. Team Review
    remarks (`candidate_team_reviews`) have no reference to an evaluation at
    all and are preserved untouched; that was checked against the live foreign
    keys rather than assumed.

  * COUNTS COME FROM ROWS, NEVER FROM STAMPS. Every number this module prints
    is a COUNT(*) over the table it describes. This codebase has already paid
    for the alternative: 19 of 35 live jobs once carried
    `framework_generated_at` while having zero competency rows, and every
    health check that asked the stamp reported success.

WHAT IS PRESERVED, PURGED AND REGENERATED
-------------------------------------------
Set by spec-doc6 decision D2 and reproduced in `CLASSIFICATION` with a reason
per table. Preserved: tenants, users, jobs, JDs, candidate accounts, resumes and
uploaded documents, applications and their timestamps, the full audit trail, and
Team Review remarks. Purged: evaluations and every dimension or competency
score, PRISM reports and their artefacts, AI Scores produced by the old logic,
old scorecards, old machine-derived ratings, old pre-screen grades. Regenerated:
the pre-screen grade only, by `--regrade`.

D2 names roughly half the schema. Every table it does not name is classified
here anyway, with the reason written next to it, and the default for anything
unnamed is PRESERVE: deleting more than was asked for is the dangerous
direction, and it is the one that cannot be undone by running the job again.

JOBS ARE NOT UNPUBLISHED
------------------------
`--purge` clears a job's framework approval stamps and its matching-category
finalisation stamp, and leaves `posting_start_date`, `status` and `archived_at`
exactly as they were. The posting stays live and applications keep arriving.
Evaluation is blocked by gate G1, which asks for scorecard ITEMS and then for a
human approval and finds neither. No second enforcement path is built here,
because a second one would be a second thing to keep in step.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.llm_providers import (
    MODEL_FOR_TASK,
    TOKEN_PRICES_USD_PER_MILLION,
    timeout_for,
)
from app.core.config import get_settings
from app.core.db import get_session_factory, superadmin_scope
from app.services.audit import audit

logger = logging.getLogger("pickready.legacy_reset")

# ── Buckets ──────────────────────────────────────────────────────────────────

#: The row survives untouched.
PRESERVE = "preserve"
#: The rows are exported and then deleted.
PURGE = "purge"
#: The row survives and named COLUMNS are cleared. Used where the row itself is
#: preserved data (an application, a job) and only the machine scoring written
#: onto it is being removed. Deleting the row instead would delete the
#: application, which D2 preserves.
RESET = "reset"
#: The row survives and a REFERENCE on it is nulled, with the context copied
#: onto the row first. Used only for human observations that pointed at a
#: purged evaluation.
DETACH = "detach"
#: Schema bookkeeping, not data.
INFRASTRUCTURE = "infrastructure"

BUCKETS: tuple[str, ...] = (PRESERVE, PURGE, RESET, DETACH, INFRASTRUCTURE)


@dataclass(frozen=True)
class TableRule:
    """One table's classification, and everything the purge needs to act on it.

    `order` is the deletion sequence, children before parents. The purge deletes
    explicitly in this order rather than leaning on ON DELETE CASCADE, because a
    cascade removes rows nobody counted: the report says it deleted 12
    `functional_skills_reports` while the database also removed 40
    `report_dimensions`, and the reconciliation is then a number that does not
    describe what happened.
    """

    table: str
    bucket: str
    reason: str
    #: The column carrying the tenant, or None for a table that is not
    #: tenant-scoped. A table with no tenant column cannot be purged inside the
    #: per-tenant transaction and is handled in the platform-scope phase.
    tenant_column: str | None = "tenant_id"
    #: Extra SQL predicate narrowing what the purge touches, without the WHERE.
    predicate: str | None = None
    #: For RESET: the columns cleared, and what they are set to.
    resets: tuple[tuple[str, str], ...] = ()
    order: int = 0
    #: False when spec-doc6 D2 does not name this table, so the survey can list
    #: exactly which classifications are this module's judgement rather than the
    #: decision's. Those are the ones a reviewer must sign off.
    named_by_d2: bool = True


# ── The classification ───────────────────────────────────────────────────────
#
# EVERY TABLE IN THE SCHEMA APPEARS HERE. `assert_classified` proves it against
# the live database, so this tuple cannot silently fall behind a migration.

CLASSIFICATION: tuple[TableRule, ...] = (
    # ── infrastructure ───────────────────────────────────────────────────────
    TableRule(
        "alembic_version",
        INFRASTRUCTURE,
        "The schema revision pointer. Not data, and rewriting it would make the "
        "database claim a shape it does not have.",
        tenant_column=None,
        named_by_d2=False,
    ),
    # ── purged: the delivered report and its artefacts ───────────────────────
    TableRule(
        "report_dimensions",
        PURGE,
        "Per-dimension grades inside a PRISM Report. D2 purges the reports and "
        "their artefacts.",
        order=10,
    ),
    TableRule(
        "report_skill_evidence",
        PURGE,
        "Structured evidence behind a report dimension. It cites assessment "
        "messages that are themselves being purged, so keeping it would leave "
        "citations pointing at nothing.",
        order=11,
    ),
    TableRule(
        "functional_skills_reports",
        PURGE,
        "The PRISM Report itself: the delivered, immutable artefact D2 names.",
        order=12,
    ),
    # ── purged: the evidence ledger ──────────────────────────────────────────
    TableRule(
        "evidence_claim_links",
        PURGE,
        "The stance edges between a claim and its evidence. Machine output of "
        "the old extraction pass.",
        order=20,
    ),
    TableRule(
        "evidence_claims",
        PURGE,
        "AI-generated candidate intelligence produced by the old logic. Its "
        "supporting evidence cites transcripts being purged.",
        order=21,
    ),
    TableRule(
        "evidence_items",
        PURGE,
        "Evidence references into resumes and transcripts, extracted by the old "
        "pipeline. A resume-sourced item survives its source but was tiered and "
        "scored under the retired rubric, so it is regenerated rather than kept.",
        order=22,
    ),
    # ── purged: the assessment ───────────────────────────────────────────────
    TableRule(
        "assessment_messages",
        PURGE,
        "The interview transcript. Part of the assessment D2 deletes. The "
        "candidate's own words are human authorship and survive only in the "
        "export, which is why the survey raises this for sign-off rather than "
        "treating it as routine.",
        order=30,
    ),
    TableRule(
        "assessment_conversations",
        PURGE,
        "One assessment run, and also the invitation. A COMPLETED one is legacy "
        "data and is purged. An ACTIVE one is not: spec-doc6 D2 says in as many "
        "words that applications are not interrupted, and deleting a "
        "conversation somebody is part-way through is the most direct way to "
        "interrupt one. The survey counted 198 active at the time this "
        "predicate was added, 197 of them seeded demo data and one a real "
        "candidate, and the one is the reason. Preserving it costs nothing: "
        "gate G1 blocks that candidate's evaluation until the job is "
        "re-defined, so they finish typing and their scoring waits, which is "
        "strictly better than losing their answers mid-assessment.",
        predicate="completed_at IS NOT NULL",
        order=31,
    ),
    TableRule(
        "candidate_questions",
        PURGE,
        "Per-candidate questions generated against the old scorecard.",
        order=32,
    ),
    TableRule(
        "candidate_technical_questions",
        PURGE,
        "Per-candidate technical questions and the rubric written with each. "
        "Scored against a scorecard that is being replaced.",
        order=33,
    ),
    TableRule(
        "technical_questions",
        PURGE,
        "The retired per-job preset bank. CLAUDE.md kept this table unread so "
        "'what was this candidate asked' stayed answerable for existing "
        "reports; those reports are themselves being purged, so the reason no "
        "longer holds and the history moves to the export.",
        order=34,
        named_by_d2=False,
    ),
    # ── purged: the scorecard and the pre-screen criteria ────────────────────
    TableRule(
        "job_competencies",
        PURGE,
        "The Tatva matrix: Must-have, Nice-to-have and Behavioural criteria. "
        "D2 purges old scorecards.",
        order=40,
    ),
    TableRule(
        "job_matching_categories",
        PURGE,
        "The coarse resume-only categories the AI Score was computed against. "
        "Regrading against the old categories would compute a new number from "
        "retired criteria, which is the least detectable kind of wrong.",
        order=41,
        named_by_d2=False,
    ),
    # ── purged: the evaluation, after its human observations are detached ────
    TableRule(
        "evaluations",
        PURGE,
        "One Miti run: the five dimension bands, the aggregate, the "
        "triangulation and the gate verdicts. The first table D2 names.",
        order=60,
    ),
    # ── purged: derived retrieval index over purged sources ──────────────────
    TableRule(
        "context_chunks",
        PURGE,
        "Only the chunks cut from assessment transcripts, whose source rows are "
        "being deleted. JD and resume chunks are preserved because their "
        "sources are, and they are re-embedded by app.scripts.reembed.",
        predicate="source_type = 'assessment'",
        order=42,
        named_by_d2=False,
    ),
    # ── detached: human observations that referenced an evaluation ───────────
    TableRule(
        "review_dispositions",
        DETACH,
        "G4: a person looked at a flag and decided something. Human authorship, "
        "and the proof of the no-auto-reject rule. The reference to the "
        "evaluation is nulled after the job and application context is copied "
        "onto the row; the row itself is never deleted.",
        order=50,
        named_by_d2=False,
    ),
    TableRule(
        "calibration_records",
        DETACH,
        "A person's later judgement about whether a grade turned out to be "
        "right. Human authorship. Same treatment, and it already carries its "
        "own job_id.",
        order=51,
        named_by_d2=False,
    ),
    # ── reset: the row is preserved, the machine scoring on it is not ────────
    TableRule(
        "job_candidate_links",
        RESET,
        "The application. Preserved by D2, including its timestamps. The "
        "pre-screen grade written onto it is not: match_score, its rationale, "
        "the four-parameter breakdown and the tier are all old machine output.",
        resets=(
            ("match_score", "NULL"),
            ("match_rationale", "NULL"),
            ("match_breakdown_json", "NULL"),
            ("tier", "NULL"),
        ),
        order=70,
    ),
    TableRule(
        "jobs",
        RESET,
        "The job and its JD are preserved and the posting is NOT touched. The "
        "scorecard approval stamps are cleared so that gate G1 can block "
        "evaluation until the job is re-defined, which is the enforcement D2 "
        "says to reuse rather than duplicate. Whether G1 is reachable from a "
        "live path is checked before the purge runs, not assumed.",
        resets=(
            ("framework_generated_at", "NULL"),
            ("framework_approved_at", "NULL"),
            ("matching_categories_finalized_at", "NULL"),
            ("question_reminder_sent_at", "NULL"),
            ("questions_generated_at", "NULL"),
            ("questions_approved_at", "NULL"),
            ("assessment_status", "'questions_pending_review'"),
        ),
        order=71,
    ),
    # ── preserved ────────────────────────────────────────────────────────────
    TableRule(
        "tenants",
        PRESERVE,
        "The customer. D2 preserves it, and every per-tenant transaction below is "
        "scoped by a row in this table.",
        tenant_column="id",
    ),
    TableRule("users", PRESERVE, "Staff accounts. D2 preserves them, and every audit row and human "
        "observation the reset keeps points at one."),
    TableRule(
        "candidates", PRESERVE, "Candidate accounts. D2 preserves them, along with the resumes and "
        "applications hanging off them.", tenant_column=None
    ),
    TableRule(
        "profiles",
        PRESERVE,
        "The resume and everything parsed from it. D2 preserves resumes and "
        "uploaded documents. The embedding on this row is re-embedded by "
        "app.scripts.reembed, never deleted here.",
        tenant_column="source_tenant_id",
    ),
    TableRule(
        "candidate_team_reviews",
        PRESERVE,
        "Team Review remarks. Human authorship, named by D2 as preserved. "
        "Checked against the live foreign keys: this table has no reference to "
        "an evaluation, so no cascade can reach it.",
    ),
    TableRule(
        "pipeline_status",
        PRESERVE,
        "The append-only hiring stage history. Application data, not scoring.",
    ),
    TableRule(
        "audit_log",
        PRESERVE,
        "The audit trail is never purged. Rows referencing deleted evaluations "
        "remain, and the deletion itself is audited beside them.",
    ),
    TableRule(
        "job_swot_intakes",
        PRESERVE,
        "The reporting authority's own answers in the SWOT session. Human "
        "authorship. The matrix DERIVED from it is purged; erasing the "
        "conversation as well would delete evidence of work a person did.",
        named_by_d2=False,
    ),
    TableRule(
        "company_dna",
        PRESERVE,
        "The Layer 2 artifact and the client's answers behind it. Produced by "
        "the new framework, and the input Sutra needs to compile a new "
        "scorecard at all.",
        named_by_d2=False,
    ),
    TableRule(
        "old_profile_reviews",
        PRESERVE,
        "A recruiter's decision on an old profile, and the billing event "
        "attached to it. Human decision plus money.",
        named_by_d2=False,
    ),
    TableRule(
        "interviews", PRESERVE, "Scheduled interviews. Application data with a real person's calendar "
        "behind it, and not machine scoring.", named_by_d2=False
    ),
    TableRule(
        "verification_requests",
        PRESERVE,
        "Employer verification correspondence. Not scoring.",
        named_by_d2=False,
    ),
    TableRule(
        "job_approvals",
        PRESERVE,
        "The job approval chain: who approved a job to be published, and when. "
        "Human decisions.",
        named_by_d2=False,
    ),
    TableRule(
        "job_assignments",
        PRESERVE,
        "Which recruiter, hiring manager or interview manager is assigned to a "
        "job. Access control, not scoring, and a job keeps its team through the "
        "reset because the job itself is preserved.",
        named_by_d2=False,
    ),
    TableRule(
        "job_company_dna_bindings",
        PURGE,
        "The frozen binding of a job to the Company DNA version and scorecard "
        "version its evaluations were run under. The scorecard it names is "
        "being archived, so the binding would assert a freeze over a matrix "
        "that no longer exists. It is re-created when the job's new scorecard "
        "is locked.",
        order=43,
        named_by_d2=False,
    ),
    TableRule(
        "email_log",
        PRESERVE,
        "What was actually sent to whom, including the copy. An outbound record "
        "cannot be retracted by deleting the log of it.",
        named_by_d2=False,
    ),
    TableRule(
        "email_templates", PRESERVE, "Client-authored email copy. Not machine output, and not scoring.", named_by_d2=False
    ),
    TableRule("companies", PRESERVE, "The client-authored careers page, which candidates read. Preserved with "
        "the tenant that wrote it.", named_by_d2=False),
    TableRule(
        "hiring_managers", PRESERVE, "Which hiring managers belong to this customer. Access control, not "
        "scoring.", named_by_d2=False
    ),
    TableRule(
        "compliance_documents",
        PRESERVE,
        "Tax and commercial documents. D2 preserves uploaded documents.",
        named_by_d2=False,
    ),
    TableRule("staff_invites", PRESERVE, "Staff invitations that have not been accepted yet. Deleting one would "
        "silently revoke an invite already in somebody's inbox.", named_by_d2=False),
    TableRule(
        "otp_challenges", PRESERVE, "Short-lived authentication challenges. They expire on their own and "
        "deleting them early would fail a login in flight.", named_by_d2=False
    ),
    TableRule(
        "role_permissions", PRESERVE, "The permission model is data rather than code, so this table IS the "
        "authorisation rules. Nothing about the reset touches them.", named_by_d2=False
    ),
    TableRule(
        "llm_provider_keys",
        PRESERVE,
        "Provider credentials, encrypted at rest. A global table, and nothing "
        "here is client hiring data.",
        tenant_column=None,
        named_by_d2=False,
    ),
    TableRule(
        "pricing_plans",
        PRESERVE,
        "The commercial plan catalogue. A global table that tenants reference, so "
        "deleting a row would strand a subscription.",
        tenant_column=None,
        named_by_d2=False,
    ),
    TableRule(
        "billing_transactions", PRESERVE, "Payment records. Money is never purged, and a payment that happened "
        "cannot be unmade by deleting the row describing it.", named_by_d2=False
    ),
    TableRule(
        "credit_ledger",
        PRESERVE,
        "The credit balance is SUM(subunits_delta) over this table, so deleting "
        "a row silently changes what a customer owes.",
        named_by_d2=False,
    ),
    TableRule(
        "webhook_events",
        PRESERVE,
        "Payment webhook idempotency keys. Deleting one re-opens a double-grant "
        "on redelivery.",
        tenant_column=None,
        named_by_d2=False,
    ),
    TableRule(
        "bd_leads",
        PRESERVE,
        "Ready Pick Now's own sales pipeline. Not client hiring data.",
        named_by_d2=False,
    ),
    TableRule(
        "agent_execution_traces",
        PRESERVE,
        "Operational telemetry: identifiers, counts and timings, never content. "
        "Not a product artefact and not a rating, so the default for a table D2 "
        "does not name applies.",
        named_by_d2=False,
    ),
    TableRule(
        "agent_learnings",
        PRESERVE,
        "Agent output hygiene (word ranges, JSON shape), never hiring criteria, "
        "and structurally unable to relax a threshold or skip a verifier. "
        "Flagged in the survey as a reviewable call: a reader who wants a clean "
        "slate would move it to the purge bucket, and nothing else changes.",
        tenant_column=None,
        named_by_d2=False,
    ),
)

#: Tables whose rows the export must carry so the export can be restored on its
#: own. Order matters: a restore inserts these first, parents before children.
CONTEXT_TABLES: tuple[str, ...] = (
    "tenants",
    "users",
    "candidates",
    "profiles",
    "jobs",
    "job_candidate_links",
)


class ClassificationGap(RuntimeError):
    """A table exists in the database that this module does not classify.

    Raised rather than logged. spec-doc6 section 6.2: any table D2 does not
    classify must be classified in the survey BEFORE the purge runs, and a gap
    discovered at purge time is a gap that was going to be resolved by whatever
    the DELETE happened to do.
    """


class ExportNotVerified(RuntimeError):
    """The export was never test-restored, or no longer matches the database."""


class GateNotWired(RuntimeError):
    """G1 is not reachable from any request handler or Celery task.

    Raised by `--purge --confirm`, because the archive-and-mark step's entire
    safety argument is that gate G1 blocks evaluation against an unapproved
    scorecard. If nothing on a live path can reach G1, the step clears the
    approval stamps and evaluation carries on regardless, which produces the
    exact state decision D2 exists to prevent while every report says the reset
    succeeded.
    """


# ── Is G1 actually enforced? ─────────────────────────────────────────────────
#
# spec-doc6 D2 and section 6.2 both say "no new mechanism is needed to enforce
# this: gate G1 already blocks evaluation without an approved scorecard". That
# premise is checked here rather than believed, and the check is a reachability
# query over the import graph, not a grep for the gate's name: a gate that is
# called from a module nothing on a live path imports is a gate that never runs.
#
# NO SECOND ENFORCEMENT PATH IS BUILT. spec-doc6 section 10.1 forbids it and it
# would be the wrong fix anyway. What this does is convert a false premise from
# something invisible into a refusal that names the file and the line, so the
# next reader can check the state for themselves in one command.

#: The gate function whose reachability decides whether the archive-and-mark
#: step means anything.
GATE_FUNCTION = "scorecard_gate"
#: A module under one of these packages is on a live path: it is a FastAPI
#: router or a Celery task, and therefore something a request or a queued
#: message can actually reach.
LIVE_ENTRY_PACKAGES: tuple[str, ...] = ("app.api", "app.workers")


@dataclass(frozen=True)
class GateWiring:
    """Where G1 is called, and whether anything live can get there."""

    call_sites: tuple[str, ...]
    reachable_from: tuple[str, ...]

    @property
    def enforced(self) -> bool:
        return bool(self.call_sites) and bool(self.reachable_from)

    def explain(self) -> str:
        if not self.call_sites:
            return (
                f"`{GATE_FUNCTION}` is never called anywhere in `app/`. G1 is "
                "documentation, not a check."
            )
        sites = ", ".join(self.call_sites)
        if self.enforced:
            return (
                f"G1 is called at {sites} and is reachable from "
                + ", ".join(sorted(self.reachable_from))
                + "."
            )
        return (
            f"G1 is called at {sites}, and NO module under "
            + " or ".join(LIVE_ENTRY_PACKAGES)
            + " can reach that module through any import chain. Evaluation on "
            "the live paths never consults it, so clearing a job's approval "
            "stamps blocks nothing."
        )


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(tree: Any, module: str) -> set[str]:
    """Every `app.*` module this one imports, including inside functions.

    A relative import is resolved against the importing module's package, and a
    `from a.b import c` yields BOTH `a.b` and `a.b.c` as candidates because the
    name may be a submodule or an attribute and the graph must not miss the
    submodule case.
    """
    import ast  # noqa: PLC0415

    found: set[str] = set()
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                base = f"{prefix}.{base}" if base else prefix
            if not base.startswith("app"):
                continue
            found.add(base)
            for alias in node.names:
                found.add(f"{base}.{alias.name}")
    return found


def inspect_gate_wiring(root: Path | None = None) -> GateWiring:
    """Read the import graph and answer whether G1 is on a live path."""
    import ast  # noqa: PLC0415

    app_root = root or Path(__file__).resolve().parents[1]
    graph: dict[str, set[str]] = {}
    call_sites: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        module = _module_name(path, app_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise GateNotWired(
                f"{path} could not be parsed, so the import graph is incomplete "
                f"and the G1 reachability answer would be a guess: {exc}"
            ) from exc
        graph[module] = _imported_modules(tree, module)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if name == GATE_FUNCTION:
                call_sites.append(
                    f"{path.relative_to(app_root.parent).as_posix()}:{node.lineno}"
                )

    targets = {
        module
        for module in graph
        if any(site.split(":")[0].replace("/", ".")[: -len(".py")] == module for site in call_sites)
    }
    reachable: list[str] = []
    for module in graph:
        if not module.startswith(LIVE_ENTRY_PACKAGES):
            continue
        seen: set[str] = set()
        queue = [module]
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(edge for edge in graph.get(current, ()) if edge in graph)
        if seen & targets:
            reachable.append(module)
    return GateWiring(tuple(sorted(set(call_sites))), tuple(sorted(reachable)))


def assert_gate_enforced(wiring: GateWiring | None = None) -> None:
    state = wiring or inspect_gate_wiring()
    if state.enforced:
        return
    raise GateNotWired(
        "The archive-and-mark step cannot run: its safety argument is that gate "
        "G1 blocks evaluation against an unapproved scorecard, and that is not "
        f"true in this codebase. {state.explain()} Reproduce with:\n"
        "    grep -rn 'services.hiring\\|services.miti\\|services.siddhi' "
        "backend/app/api backend/app/workers\n"
        "Part A must be wired to a live evaluation path (spec-doc6 phases 3 to "
        "5) before the purge clears a job's approval stamps. Do not add a "
        "second enforcement path to get past this; spec-doc6 section 10.1 "
        "forbids it and two paths to keep in step is the drift the rule exists "
        "to prevent."
    )


def rules_by_bucket(bucket: str) -> tuple[TableRule, ...]:
    return tuple(rule for rule in CLASSIFICATION if rule.bucket == bucket)


def rule_for(table: str) -> TableRule | None:
    for rule in CLASSIFICATION:
        if rule.table == table:
            return rule
    return None


def classified_tables() -> frozenset[str]:
    return frozenset(rule.table for rule in CLASSIFICATION)


def unclassified_tables(live_tables: Iterable[str]) -> tuple[str, ...]:
    """Live tables this module does not classify. The dangerous direction."""
    return tuple(sorted(frozenset(live_tables) - classified_tables()))


def absent_tables(live_tables: Iterable[str]) -> tuple[str, ...]:
    """Classified tables the database does not have yet.

    Not the same failure as an unclassified table and not treated as one. It
    means the database is behind the migrations: the survey reports it, and the
    purge refuses, because a purge that silently skipped a table it was written
    to act on would report a clean run over data it never touched.
    """
    return tuple(sorted(classified_tables() - frozenset(live_tables)))


def assert_classified(live_tables: Iterable[str]) -> None:
    """spec-doc6 section 6.2, enforced rather than described: no table reaches
    the purge without a bucket."""
    missing = unclassified_tables(live_tables)
    if missing:
        raise ClassificationGap(
            "tables in the database with no classification: " + ", ".join(missing)
        )


def assert_schema_current(live_tables: Iterable[str]) -> None:
    """Every classified table exists. Required before an export or a purge."""
    absent = absent_tables(live_tables)
    if absent:
        raise ClassificationGap(
            "the database is behind the migrations and does not have: "
            + ", ".join(absent)
            + ". Run `alembic upgrade head` first; a purge that skipped these "
            "would report success over rows it never looked at."
        )


def purge_order() -> tuple[TableRule, ...]:
    """Every rule the purge acts on, children before parents."""
    acting = [r for r in CLASSIFICATION if r.bucket in (PURGE, RESET, DETACH)]
    return tuple(sorted(acting, key=lambda r: (r.order, r.table)))


# ── SQL builders (pure) ──────────────────────────────────────────────────────


def _scope_clause(rule: TableRule, *, tenant_param: str = "tenant") -> str:
    parts: list[str] = []
    if rule.tenant_column:
        parts.append(f"{rule.tenant_column} = CAST(:{tenant_param} AS uuid)")
    if rule.predicate:
        parts.append(f"({rule.predicate})")
    return " AND ".join(parts) if parts else "TRUE"


def count_sql(rule: TableRule) -> str:
    return f"SELECT COUNT(*) FROM {rule.table} WHERE {_scope_clause(rule)}"


def select_sql(rule: TableRule) -> str:
    return f"SELECT * FROM {rule.table} WHERE {_scope_clause(rule)}"


def delete_sql(rule: TableRule) -> str:
    if rule.bucket != PURGE:
        raise ValueError(f"{rule.table} is not a purge table")
    return f"DELETE FROM {rule.table} WHERE {_scope_clause(rule)}"


def reset_sql(rule: TableRule) -> str:
    """UPDATE that clears the machine scoring and touches nothing else.

    The WHERE clause narrows to rows that still carry a value, so a second run
    reports zero rather than reporting the whole table again. The count this
    returns is therefore the number of rows actually changed, which is the
    number the reconciliation needs.
    """
    if rule.bucket != RESET:
        raise ValueError(f"{rule.table} is not a reset table")
    assignments = ", ".join(f"{col} = {value}" for col, value in rule.resets)
    dirty = " OR ".join(
        f"{col} IS DISTINCT FROM {value}" for col, value in rule.resets
    )
    return (
        f"UPDATE {rule.table} SET {assignments} "
        f"WHERE {_scope_clause(rule)} AND ({dirty})"
    )


#: What is written into `detached_note`. A detachment that left no trace is
#: indistinguishable from a row that never had a reference.
DETACH_NOTE = (
    "Evaluation purged by the spec-doc6 legacy data reset. The decision, its "
    "author and its job and application context are preserved; evaluation_ref "
    "holds the identifier the export can be joined on."
)


def detach_sql(rule: TableRule) -> str:
    """Copy the context off the evaluation, then null the reference.

    The context copy and the null happen in ONE statement so there is no window
    in which the reference is gone and the context has not landed. Doing it in
    two would work every time it was tested and lose the context of any row
    written between them.
    """
    if rule.bucket != DETACH:
        raise ValueError(f"{rule.table} is not a detach table")
    if not rule.tenant_column:
        raise ValueError(f"{rule.table} has no tenant column to scope a detach by")
    context_assignments = ""
    if rule.table == "review_dispositions":
        context_assignments = (
            "job_id = COALESCE(t.job_id, e.job_id), "
            "link_id = COALESCE(t.link_id, e.link_id), "
        )
    return (
        f"UPDATE {rule.table} AS t SET "
        f"{context_assignments}"
        "evaluation_ref = COALESCE(t.evaluation_ref, t.evaluation_id), "
        "evaluation_id = NULL, "
        "detached_at = now(), "
        "detached_note = :note "
        "FROM evaluations e "
        "WHERE e.id = t.evaluation_id "
        f"AND t.{rule.tenant_column} = CAST(:tenant AS uuid)"
    )


# ── JSON serialisation ───────────────────────────────────────────────────────


def json_default(value: Any) -> Any:
    """Every type a row can hold that `json` cannot write.

    A `Decimal` becomes a STRING, never a float: this schema stores money in
    integer sub-units and a ledger amount that round-trips through a float is a
    ledger amount that can disagree with itself.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"legacy_reset cannot serialise {type(value).__name__}")


def dumps(payload: Any) -> str:
    return json.dumps(payload, default=json_default, indent=2, sort_keys=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ── Survey ───────────────────────────────────────────────────────────────────


@dataclass
class TenantCounts:
    tenant_id: str
    name: str
    status: str
    is_demo: bool
    jobs: int = 0
    candidates: int = 0
    applications: int = 0
    tables: "OrderedDict[str, int]" = field(default_factory=OrderedDict)

    @property
    def rows_to_purge(self) -> int:
        return sum(
            count
            for table, count in self.tables.items()
            if (rule_for(table) or TableRule(table, PRESERVE, "")).bucket == PURGE
        )


@dataclass
class EdgeCase:
    key: str
    headline: str
    count: int
    detail: str
    #: True when the finding needs a person to agree before the purge runs.
    needs_signoff: bool = False
    samples: tuple[str, ...] = ()


@dataclass
class ObjectStoreReconciliation:
    performed: bool
    reason: str
    bucket: str = ""
    objects_listed: int = 0
    rows_with_object_uri: int = 0
    rows_with_legacy_uri: int = 0
    rows_with_no_uri: int = 0
    rows_missing_object: tuple[str, ...] = ()
    objects_missing_row: tuple[str, ...] = ()


@dataclass
class Survey:
    taken_at: datetime
    database: str
    schema_revision: str
    code_head_revision: str
    live_tables: tuple[str, ...]
    #: Classified tables this database does not have yet, because it is behind
    #: the migrations. Reported, never silently treated as empty.
    absent_tables: tuple[str, ...]
    totals: "OrderedDict[str, int]"
    tenants: tuple[TenantCounts, ...]
    per_job: tuple[dict[str, Any], ...]
    edge_cases: tuple[EdgeCase, ...]
    #: Properties of the SCHEMA the reset noticed and did not change. They are
    #: reported because a survey that only counted rows would have walked past
    #: them, and one of them is the same class of defect migration 0062 fixed.
    schema_findings: tuple[EdgeCase, ...]
    objects: ObjectStoreReconciliation
    #: Whether gate G1 is reachable from any request handler or Celery task.
    #: The archive-and-mark step's whole safety argument rests on it.
    gate_wiring: GateWiring

    @property
    def unnamed_by_d2(self) -> tuple[TableRule, ...]:
        return tuple(r for r in CLASSIFICATION if not r.named_by_d2)

    @property
    def signoff_required(self) -> tuple[EdgeCase, ...]:
        return tuple(e for e in self.edge_cases if e.needs_signoff)


async def _scalar(session: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> Any:
    return (await session.execute(text(sql), params or {})).scalar()


async def _rows(
    session: AsyncSession, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings().all()]


async def live_tables(session: AsyncSession) -> tuple[str, ...]:
    rows = await _rows(
        session,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
    )
    return tuple(row["tablename"] for row in rows)


def code_head_revision() -> str:
    """The newest revision id in `alembic/versions`, read from the files.

    Compared against `alembic_version` in the survey, because a purge planned
    against one schema and run against another is a purge whose classification
    was checked against the wrong list of tables.
    """
    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    ids: list[str] = []
    for path in sorted(versions.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("revision") and "=" in stripped:
                ids.append(stripped.split("=", 1)[1].strip().strip('"').strip("'"))
                break
    return max(ids) if ids else ""


def _tables_referenced(sql: str) -> set[str]:
    """Which classified tables a survey query reads.

    A word-match against the classification rather than a SQL parse, because
    the only thing it is used for is deciding whether a query CAN run on a
    database that is behind the migrations. It errs towards claiming a
    dependency, which is the safe direction: the cost of a false positive is a
    finding reported as not measured, and the cost of a false negative is a
    query that raises.
    """
    words = set(re.findall(r"[a-z_][a-z0-9_]*", sql.lower()))
    return {rule.table for rule in CLASSIFICATION if rule.table in words}


_EDGE_CASE_QUERIES: tuple[tuple[str, str, str, bool, str], ...] = (
    (
        "evaluated_without_resume",
        "Candidates with an evaluation but no resume",
        "Their pre-screen grade cannot be regenerated: --regrade requires a "
        "resume, so these applications end the reset with no grade at all until "
        "a resume is uploaded.",
        True,
        """
        SELECT COUNT(*) FROM evaluations e
          JOIN job_candidate_links l ON l.id = e.link_id
          LEFT JOIN profiles p ON p.id = l.profile_id
         WHERE p.id IS NULL OR p.resume_url IS NULL
        """,
    ),
    (
        "published_with_scorecard",
        "Published jobs whose scorecard is being archived",
        "The posting stays live and applications keep arriving. Their approval "
        "stamps are cleared so the job reads as pending review, and the "
        "INTENDED state is that gate G1 then blocks evaluation until a human "
        "approves a new scorecard. Whether G1 can actually do that is the "
        "first section of this document, and the purge refuses while it "
        "cannot.",
        True,
        """
        SELECT COUNT(*) FROM jobs j
         WHERE j.archived_at IS NULL
           AND j.posting_start_date IS NOT NULL
           AND EXISTS (SELECT 1 FROM job_competencies c WHERE c.job_id = j.id)
        """,
    ),
    (
        "stamped_without_competencies",
        "Jobs stamped as generated with zero scorecard rows",
        "The failure this project has already had once: a timestamp asserting "
        "work that produced no rows. These jobs were already unusable before "
        "the reset and the reset does not change that.",
        False,
        """
        SELECT COUNT(*) FROM jobs j
         WHERE j.framework_generated_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM job_competencies c WHERE c.job_id = j.id)
        """,
    ),
    (
        "active_conversations",
        "Assessment conversations still active",
        "Purging one ends an interview a candidate may be part way through. "
        "They keep their application; they lose the conversation and must be "
        "re-invited once the job's new scorecard is approved.",
        True,
        "SELECT COUNT(*) FROM assessment_conversations WHERE status <> 'completed'",
    ),
    (
        "orphan_report_dimensions",
        "Report dimensions with no report",
        "Already orphaned before the reset. Counted so the purge total can be "
        "reconciled against the report count rather than appearing to differ.",
        False,
        """
        SELECT COUNT(*) FROM report_dimensions d
         WHERE NOT EXISTS (
             SELECT 1 FROM functional_skills_reports r WHERE r.id = d.report_id
         )
        """,
    ),
    (
        "orphan_report_evidence",
        "Report skill evidence whose conversation is gone",
        "Same category: pre-existing orphans, counted so the totals add up.",
        False,
        """
        SELECT COUNT(*) FROM report_skill_evidence e
         WHERE NOT EXISTS (
             SELECT 1 FROM assessment_conversations c WHERE c.id = e.conversation_id
         )
        """,
    ),
    (
        "evaluations_without_report",
        "Evaluations that never produced a report",
        "Scoring that started and did not finish. Purged with the rest.",
        False,
        "SELECT COUNT(*) FROM evaluations WHERE report_id IS NULL",
    ),
    (
        "dispositions_to_detach",
        "Human dispositions that referenced a purged evaluation",
        "These would have been CASCADE-deleted before migration 0062. The purge "
        "detaches them instead, copying the job and application context onto "
        "the row first.",
        False,
        "SELECT COUNT(*) FROM review_dispositions WHERE evaluation_id IS NOT NULL",
    ),
    (
        "calibration_to_detach",
        "Human calibration records that referenced a purged evaluation",
        "Same treatment, same reason.",
        False,
        "SELECT COUNT(*) FROM calibration_records WHERE evaluation_id IS NOT NULL",
    ),
    (
        "team_reviews_preserved",
        "Team Review remarks preserved",
        "Every one of them. This table has no reference to an evaluation, so no "
        "cascade can reach it; the count is recorded before and after so the "
        "claim is a measurement rather than an assertion.",
        False,
        "SELECT COUNT(*) FROM candidate_team_reviews",
    ),
    (
        "graded_links",
        "Applications carrying an old pre-screen grade",
        "Every one of these has its match_score, rationale, breakdown and tier "
        "cleared, and is the input set --regrade works through.",
        False,
        "SELECT COUNT(*) FROM job_candidate_links WHERE match_score IS NOT NULL",
    ),
    (
        "regradeable_links",
        "Applications with a resume, eligible for regrading",
        "The work plan --regrade reports is computed from this set.",
        False,
        """
        SELECT COUNT(*) FROM job_candidate_links l
          JOIN profiles p ON p.id = l.profile_id
         WHERE l.archived_at IS NULL AND p.resume_url IS NOT NULL
        """,
    ),
)


async def collect_schema_findings(session: AsyncSession) -> tuple[EdgeCase, ...]:
    """Schema properties worth a reader's attention, measured rather than
    remembered.

    The reset changed two foreign keys and left everything else alone. These
    are the things it looked at and did not touch, and both are the kind of
    finding that is invisible until somebody goes looking: a survey that
    counted only rows would have reported a clean run over both.
    """
    findings: list[EdgeCase] = []

    cascading_authors = await _rows(
        session,
        """
        SELECT c.conname AS name
          FROM pg_constraint c
         WHERE c.contype = 'f'
           AND c.conrelid = 'candidate_team_reviews'::regclass
           AND c.confrelid = 'users'::regclass
           AND c.confdeltype = 'c'
        """,
    )
    findings.append(
        EdgeCase(
            key="team_review_author_cascade",
            headline="Team Review remarks are deleted with their author",
            count=len(cascading_authors),
            detail=(
                "`candidate_team_reviews.reviewer_user_id` is ON DELETE "
                "CASCADE, so deleting a user row erases every remark they "
                "wrote. RBAC section 29 requires a remark to preserve its "
                "author, and the comparable column elsewhere in this schema, "
                "`review_dispositions.decided_by`, is ON DELETE RESTRICT for "
                "exactly that reason. NOT CHANGED BY THIS RESET, and "
                "deliberately: no code path hard-deletes a user, the only "
                "route is `DELETE /admin/tenants/{id}` which erases the whole "
                "tenant on purpose, and changing this key to RESTRICT would "
                "make that deletion fail on its own cascade. The fix is a "
                "product decision about what deleting a user means, not a "
                "line in a purge script."
            ),
        )
    )

    stale_scale = await _rows(
        session,
        """
        SELECT c.conname AS name, pg_get_constraintdef(c.oid) AS definition
          FROM pg_constraint c
         WHERE c.contype = 'c'
           AND c.conrelid = 'candidate_team_reviews'::regclass
           AND pg_get_constraintdef(c.oid) LIKE '%very_high%'
        """,
    )
    findings.append(
        EdgeCase(
            key="team_review_rating_scale",
            headline="Team Review ratings still check the retired five-label scale",
            count=len(stale_scale),
            detail=(
                "The CHECK on `candidate_team_reviews.rating` accepts "
                "very_high, high, medium, low and developing. The product has "
                "had ONE four-grade scale since 2026-07-30 (Highly Matching, "
                "Matching, Moderately Matching, Not Matching) and "
                "`services/rating.py` is its single source. A reviewer "
                "submitting the current vocabulary is refused by the database. "
                "NOT CHANGED BY THIS RESET: it is a data-vocabulary migration "
                "with existing rows behind it, and it belongs with whoever owns "
                "the Team Review surface."
            ),
        )
    )
    return tuple(findings)


async def collect_survey(
    session: AsyncSession, *, object_store_lister: Any | None = None
) -> Survey:
    tables = await live_tables(session)
    assert_classified(tables)
    absent = absent_tables(tables)

    revision = await _scalar(session, "SELECT version_num FROM alembic_version")
    totals: OrderedDict[str, int] = OrderedDict()
    for rule in sorted(CLASSIFICATION, key=lambda r: r.table):
        if rule.bucket == INFRASTRUCTURE or rule.table in absent:
            continue
        totals[rule.table] = int(
            await _scalar(
                session, f"SELECT COUNT(*) FROM {rule.table} WHERE {_scope_clause_all(rule)}"
            )
        )

    tenant_rows = await _rows(
        session,
        "SELECT id, name, status, COALESCE(is_demo, false) AS is_demo "
        "FROM tenants ORDER BY name",
    )
    tenants: list[TenantCounts] = []
    for row in tenant_rows:
        counts = TenantCounts(
            tenant_id=str(row["id"]),
            name=str(row["name"]),
            status=str(row["status"]),
            is_demo=bool(row["is_demo"]),
        )
        params = {"tenant": str(row["id"])}
        counts.jobs = int(
            await _scalar(session, "SELECT COUNT(*) FROM jobs WHERE tenant_id = CAST(:tenant AS uuid)", params)
        )
        counts.applications = int(
            await _scalar(
                session,
                "SELECT COUNT(*) FROM job_candidate_links WHERE tenant_id = CAST(:tenant AS uuid)",
                params,
            )
        )
        counts.candidates = int(
            await _scalar(
                session,
                "SELECT COUNT(DISTINCT candidate_id) FROM job_candidate_links "
                "WHERE tenant_id = CAST(:tenant AS uuid)",
                params,
            )
        )
        for rule in purge_order():
            if not rule.tenant_column or rule.table in absent:
                continue
            counts.tables[rule.table] = int(
                await _scalar(session, count_sql(rule), params)
            )
        tenants.append(counts)

    evaluations_present = "evaluations" not in absent
    evaluation_count_expr = (
        "(SELECT COUNT(*) FROM evaluations e WHERE e.job_id = j.id)"
        if evaluations_present
        else "-1"
    )
    per_job = await _rows(
        session,
        f"""
        SELECT j.id::text          AS job_id,
               j.title             AS title,
               t.name              AS tenant,
               j.assessment_status AS assessment_status,
               (j.archived_at IS NULL AND j.posting_start_date IS NOT NULL) AS published,
               (j.framework_approved_at IS NOT NULL) AS scorecard_approved,
               (SELECT COUNT(*) FROM job_competencies c WHERE c.job_id = j.id) AS competencies,
               (SELECT COUNT(*) FROM job_matching_categories m WHERE m.job_id = j.id) AS categories,
               (SELECT COUNT(*) FROM job_candidate_links l WHERE l.job_id = j.id) AS applications,
               (SELECT COUNT(*) FROM functional_skills_reports r WHERE r.job_id = j.id) AS reports,
               {evaluation_count_expr} AS evaluations
          FROM jobs j JOIN tenants t ON t.id = j.tenant_id
         ORDER BY t.name, j.title
        """,
    )

    edge_cases: list[EdgeCase] = []
    for key, headline, detail, signoff, sql in _EDGE_CASE_QUERIES:
        required = _tables_referenced(sql)
        if required & set(absent):
            # NOT zero. A query that could not run and a query that found
            # nothing must never render the same way, which is the whole
            # argument behind reporting the object store as NOT PERFORMED.
            edge_cases.append(
                EdgeCase(
                    key=key,
                    headline=headline,
                    count=-1,
                    detail=(
                        detail
                        + " NOT MEASURED: this database does not yet have "
                        + ", ".join(sorted(required & set(absent)))
                        + "."
                    ),
                    needs_signoff=signoff,
                )
            )
            continue
        edge_cases.append(
            EdgeCase(
                key=key,
                headline=headline,
                count=int(await _scalar(session, sql)),
                detail=detail,
                needs_signoff=signoff,
            )
        )

    schema_findings = await collect_schema_findings(session)
    objects = await reconcile_object_store(session, lister=object_store_lister)
    return Survey(
        taken_at=datetime.now(timezone.utc),
        database=_safe_database_name(),
        schema_revision=str(revision),
        code_head_revision=code_head_revision(),
        live_tables=tables,
        absent_tables=absent,
        totals=totals,
        tenants=tuple(tenants),
        per_job=tuple(per_job),
        edge_cases=tuple(edge_cases),
        schema_findings=schema_findings,
        objects=objects,
        gate_wiring=inspect_gate_wiring(),
    )


def _scope_clause_all(rule: TableRule) -> str:
    """Scope with no tenant filter, for a platform-wide total."""
    return f"({rule.predicate})" if rule.predicate else "TRUE"


def _safe_database_name() -> str:
    """The database NAME only. The URL carries a password and this string is
    written into a markdown file that gets committed."""
    url = get_settings().database_url
    return url.rsplit("/", 1)[-1].split("?", 1)[0] if "/" in url else "unknown"


# ── Object store reconciliation ──────────────────────────────────────────────

#: Recognised durable URI schemes for a stored object. Anything else on a row is
#: a legacy provider that the bucket cannot be asked about.
OBJECT_SCHEMES: tuple[str, ...] = ("s3://", "gs://")


def classify_object_uri(uri: str | None) -> str:
    """One of: none | s3 | legacy. Pure, so the survey's arithmetic is testable
    without a bucket."""
    if not uri or not str(uri).strip():
        return "none"
    value = str(uri).strip()
    if value.startswith("s3://"):
        return "s3"
    return "legacy"


async def reconcile_object_store(
    session: AsyncSession, *, lister: Any | None = None
) -> ObjectStoreReconciliation:
    """Rows with no object, and objects with no row, in both directions.

    When no bucket is configured this reports NOT PERFORMED with the reason and
    still counts what the database alone can answer. It does not report zero
    mismatches: a check that did not run and a check that found nothing look
    identical in a summary line, and this project has already shipped six
    secret-hygiene assertions that read SKIPPED one word away from PASSED.
    """
    rows = await _rows(
        session,
        "SELECT id::text AS id, resume_url, resume_public_id FROM profiles",
    )
    buckets = {"none": 0, "s3": 0, "legacy": 0}
    s3_keys_by_row: dict[str, str] = {}
    for row in rows:
        kind = classify_object_uri(row["resume_url"])
        buckets[kind] += 1
        if kind == "s3":
            s3_keys_by_row[row["id"]] = str(row["resume_url"])[len("s3://") :].split("/", 1)[-1]

    settings = get_settings()
    bucket_name = (settings.s3_bucket or "").strip()
    if lister is None and not bucket_name:
        return ObjectStoreReconciliation(
            performed=False,
            reason=(
                "No S3 bucket is configured (S3_BUCKET is empty), so the bucket "
                "cannot be listed. The database side of the reconciliation is "
                "reported below and the bucket side is NOT PERFORMED."
            ),
            rows_with_object_uri=buckets["s3"],
            rows_with_legacy_uri=buckets["legacy"],
            rows_with_no_uri=buckets["none"],
        )

    keys = list(lister()) if lister is not None else _list_bucket_keys()
    stored = set(keys)
    missing_object = sorted(
        row_id for row_id, key in s3_keys_by_row.items() if key not in stored
    )
    referenced = set(s3_keys_by_row.values())
    missing_row = sorted(key for key in stored if key not in referenced)
    return ObjectStoreReconciliation(
        performed=True,
        reason="Bucket listed and compared against every profile row.",
        bucket=bucket_name,
        objects_listed=len(stored),
        rows_with_object_uri=buckets["s3"],
        rows_with_legacy_uri=buckets["legacy"],
        rows_with_no_uri=buckets["none"],
        rows_missing_object=tuple(missing_object[:50]),
        objects_missing_row=tuple(missing_row[:50]),
    )


def _list_bucket_keys() -> list[str]:
    from app.services.object_storage import client  # noqa: PLC0415

    s3 = client()
    bucket = (get_settings().s3_bucket or "").strip()
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in page.get("Contents", ()))
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")


# ── Survey rendering ─────────────────────────────────────────────────────────


def _count_cell(count: int) -> str:
    """A count that could not be taken reads NOT MEASURED, never 0.

    Same argument as the object store section: a check that did not run and a
    check that found nothing look identical in a summary line, and this project
    has already shipped assertions that read SKIPPED one word away from PASSED.
    """
    return "NOT MEASURED" if count < 0 else str(count)


def render_survey(survey: Survey) -> str:
    """LEGACY_RESET_SURVEY.md. Pure, so the document is testable."""
    out: list[str] = []
    w = out.append
    w("# Legacy reset survey")
    w("")
    w(
        "Read-only. Produced by `python -m app.scripts.legacy_reset --survey`. "
        "spec-doc6 section 6.1: nothing proceeds until this is reviewed."
    )
    w("")
    w(f"- Taken at: `{survey.taken_at.isoformat()}`")
    w(f"- Database: `{survey.database}`")
    w(f"- Schema revision in the database: `{survey.schema_revision}`")
    w(f"- Newest revision in `alembic/versions`: `{survey.code_head_revision}`")
    w(f"- Tables in the schema: {len(survey.live_tables)}, all classified below")
    if survey.absent_tables:
        w("")
        w(
            "> The database is BEHIND the migration files and does not have "
            + ", ".join(f"`{t}`" for t in survey.absent_tables)
            + ". `--export` and `--purge` refuse in this state, because a run "
            "that skipped a table it was written to act on would report a "
            "clean result over rows it never looked at. Run `alembic upgrade "
            "head` first. Every count below for those tables reads NOT "
            "MEASURED rather than zero."
        )
    w("")

    w("## The premise this reset was written against is false today")
    w("")
    wiring = survey.gate_wiring
    if wiring.enforced:
        w("Gate G1 is enforced on a live path. " + wiring.explain())
        w("")
        w(
            "The archive-and-mark step therefore does what decision D2 says it "
            "does: a job keeps its posting and its applications, and its "
            "evaluation is blocked until a human approves a new scorecard."
        )
    else:
        w("**GATE G1 IS NOT ENFORCED ON ANY LIVE PATH, AND THE RESET DEPENDS ON IT.**")
        w("")
        w(
            "spec-doc6 decision D2 and section 6.2 both say no new mechanism is "
            "needed because G1 already blocks evaluation without an approved "
            "scorecard. Measured against this codebase, that is not true."
        )
        w("")
        w("> " + wiring.explain())
        w("")
        w("Reproduce it:")
        w("")
        w("```")
        w(
            r"grep -rn 'services.hiring\|services.miti\|services.siddhi' "
            "backend/app/api backend/app/workers"
        )
        w("python -c \"from app.scripts.legacy_reset import inspect_gate_wiring as g; print(g())\"")
        w("```")
        w("")
        w(
            "The consequence is specific. Survey and export are unaffected and "
            "are safe to run now. The ARCHIVE-AND-MARK step is not: it clears "
            "`framework_approved_at`, `framework_generated_at` and "
            "`matching_categories_finalized_at` and then relies on G1 to stop "
            "evaluation, and if nothing on a live path can reach G1 then "
            "evaluation continues completely unimpeded against a scorecard "
            "nobody approved. That is the state D2 exists to prevent, reached "
            "while every report says the reset succeeded."
        )
        w("")
        w(
            "`--purge --confirm` REFUSES while this is true, naming the file "
            "and line. No second enforcement path is built here: spec-doc6 "
            "section 10.1 forbids it, and two enforcement paths to keep in step "
            "is the drift the rule exists to prevent. The dependency is on "
            "spec-doc6 phases 3 to 5 wiring Part A to a live path."
        )
    w("")

    w("## Sign-off required before the purge runs")
    w("")
    signoff = survey.signoff_required
    if not signoff:
        w("Nothing in this survey needs a decision.")
    else:
        w(
            "Each of these is a consequence of decision D2 that a person should "
            "agree to explicitly, because it is not recoverable by running the "
            "job again."
        )
        w("")
        w("| Finding | Rows | Why it needs a decision |")
        w("|---|---:|---|")
        for case in signoff:
            w(f"| {case.headline} | {_count_cell(case.count)} | {case.detail} |")
    w("")

    w("## Where the legacy evaluation data actually is")
    w("")
    w(
        "Decision D2 names `Evaluation` rows first. In this codebase that table "
        "is `evaluations`, added by migration 0059, and it has no writer: "
        "`app/services/miti/pipeline.py` is the only module that would produce "
        "one and nothing under `app/api/` or `app/workers/` imports it. The "
        "rows D2 MEANS are the ones the shipped pipeline actually wrote, and "
        "they are in the older tables."
    )
    w("")
    w("| Table | Rows | What it is |")
    w("|---|---|---|")
    for table in (
        "evaluations",
        "functional_skills_reports",
        "report_dimensions",
        "report_skill_evidence",
        "job_competencies",
        "job_matching_categories",
        "candidate_questions",
        "candidate_technical_questions",
        "technical_questions",
        "assessment_conversations",
        "assessment_messages",
        "evidence_items",
        "evidence_claims",
    ):
        rule = rule_for(table)
        count = survey.totals.get(table)
        shown = "NOT MEASURED (table absent)" if count is None else str(count)
        w(f"| `{table}` | {shown} | {(rule.reason if rule else '')} |")
    w("")
    w(
        "Applications carrying an old pre-screen grade are counted in the edge "
        "cases below and are cleared by the RESET rule on "
        "`job_candidate_links`, not by a DELETE: the application itself is "
        "preserved data."
    )
    w("")

    w("## Every table in the schema, classified")
    w("")
    w(
        "spec-doc6 section 6.2: any table decision D2 does not classify must be "
        "classified here before the purge runs. The `D2` column says whether "
        "the decision named the table or whether the classification is this "
        "module's reading of it."
    )
    w("")
    w("| Table | Bucket | D2 | Rows | Reason |")
    w("|---|---|---|---:|---|")
    for rule in sorted(CLASSIFICATION, key=lambda r: (r.bucket, r.table)):
        rows = survey.totals.get(rule.table)
        shown = (
            "NOT MEASURED"
            if rule.table in survey.absent_tables
            else "n/a"
            if rows is None
            else str(rows)
        )
        named = "named" if rule.named_by_d2 else "inferred"
        w(f"| `{rule.table}` | {rule.bucket} | {named} | {shown} | {rule.reason} |")
    w("")
    counts_by_bucket = {
        bucket: len(rules_by_bucket(bucket)) for bucket in BUCKETS
    }
    w(
        "Bucket totals: "
        + ", ".join(f"{bucket} {counts_by_bucket[bucket]}" for bucket in BUCKETS)
        + f", {len(CLASSIFICATION)} tables in all."
    )
    w("")

    w("## By tenant")
    w("")
    w("| Tenant | Status | Demo | Jobs | Candidates | Applications | Rows to purge |")
    w("|---|---|---|---:|---:|---:|---:|")
    for tenant in survey.tenants:
        w(
            f"| {tenant.name} | {tenant.status} | {'yes' if tenant.is_demo else 'no'} "
            f"| {tenant.jobs} | {tenant.candidates} | {tenant.applications} "
            f"| {tenant.rows_to_purge} |"
        )
    w("")
    purge_tables = [r.table for r in purge_order() if r.bucket == PURGE and r.tenant_column]
    w("### Purged rows per tenant, per table")
    w("")
    w("| Tenant | " + " | ".join(f"`{t}`" for t in purge_tables) + " |")
    w("|---" * (len(purge_tables) + 1) + "|")
    for tenant in survey.tenants:
        cells = " | ".join(str(tenant.tables.get(t, 0)) for t in purge_tables)
        w(f"| {tenant.name} | {cells} |")
    w("")

    w("## By job")
    w("")
    if survey.gate_wiring.enforced:
        w(
            "`published` jobs are NOT unpublished by the purge and their "
            "applications are not interrupted. Their scorecard is archived "
            "into the export and their approval stamps are cleared, so gate G1 "
            "blocks evaluation until the job is re-defined."
        )
    else:
        w(
            "`published` jobs are NOT unpublished by the purge and their "
            "applications are not interrupted. Their scorecard is archived "
            "into the export and their approval stamps are cleared. What is "
            "SUPPOSED to happen next is that gate G1 blocks evaluation until a "
            "human approves a new scorecard. It does not, today: see the first "
            "section. Nothing below should be read as saying these jobs are "
            "protected, and the purge refuses to reach this step while that is "
            "true."
        )
    w("")
    w(
        "| Tenant | Job | Status | Published | Scorecard approved | Competencies "
        "| Categories | Applications | Reports | Evaluations |"
    )
    w("|---|---|---|---|---|---:|---:|---:|---:|---:|")
    for job in survey.per_job:
        w(
            f"| {job['tenant']} | {job['title']} | {job['assessment_status']} "
            f"| {'yes' if job['published'] else 'no'} "
            f"| {'yes' if job['scorecard_approved'] else 'no'} "
            f"| {job['competencies']} | {job['categories']} | {job['applications']} "
            f"| {job['reports']} | {job['evaluations']} |"
        )
    w("")

    w("## Edge cases")
    w("")
    w("| Finding | Rows | Detail |")
    w("|---|---:|---|")
    for case in survey.edge_cases:
        w(f"| {case.headline} | {_count_cell(case.count)} | {case.detail} |")
    w("")

    w("## Schema findings the reset looked at and did not change")
    w("")
    w(
        "Both are the same class of defect migration 0062 fixed: a property of "
        "the schema that a survey counting only rows would have walked past."
    )
    w("")
    w("| Finding | Present | Detail |")
    w("|---|---:|---|")
    for finding in survey.schema_findings:
        w(f"| {finding.headline} | {_count_cell(finding.count)} | {finding.detail} |")
    w("")

    w("## Object store reconciliation")
    w("")
    objects = survey.objects
    if objects.performed:
        w(f"Bucket `{objects.bucket}`, {objects.objects_listed} objects listed.")
        w("")
        w("| Direction | Count |")
        w("|---|---:|")
        w(f"| Profile rows pointing at an object URI | {objects.rows_with_object_uri} |")
        w(f"| Profile rows on a legacy provider | {objects.rows_with_legacy_uri} |")
        w(f"| Profile rows with no resume URI at all | {objects.rows_with_no_uri} |")
        w(f"| Rows whose object is missing from the bucket | {len(objects.rows_missing_object)} |")
        w(f"| Objects in the bucket with no row | {len(objects.objects_missing_row)} |")
        if objects.rows_missing_object:
            w("")
            w("Rows whose object is missing (first 50): ")
            w("")
            for row_id in objects.rows_missing_object:
                w(f"- `{row_id}`")
        if objects.objects_missing_row:
            w("")
            w("Objects with no row (first 50): ")
            w("")
            for key in objects.objects_missing_row:
                w(f"- `{key}`")
    else:
        w("**NOT PERFORMED.** " + objects.reason)
        w("")
        w("| Direction | Count |")
        w("|---|---:|")
        w(f"| Profile rows pointing at an object URI | {objects.rows_with_object_uri} |")
        w(f"| Profile rows on a legacy provider | {objects.rows_with_legacy_uri} |")
        w(f"| Profile rows with no resume URI at all | {objects.rows_with_no_uri} |")
        w("")
        w(
            "The bucket half of this reconciliation is unanswered, not clean. "
            "Re-run the survey with `S3_BUCKET` set and AWS credentials "
            "available to settle it."
        )
    w("")

    w("## What runs next")
    w("")
    w("```")
    w("# 1. Export, and test-restore it in the same command. An export that has")
    w("#    never been restored is not a backup, so --purge --confirm refuses")
    w("#    one whose manifest does not say restore_verified: true. The scratch")
    w("#    database must be empty, migrated to head, and reachable as a")
    w("#    superuser (the restore turns foreign-key triggers off for the load).")
    w("python -m app.scripts.legacy_reset --export \\")
    w("    --scratch-database-url postgresql+asyncpg://<user>:<pw>@<host>/<empty_db>")
    w("")
    w("# 2. Dry run. This is the default, and it changes nothing.")
    w("python -m app.scripts.legacy_reset --purge")
    w("")
    w("# 3. Apply. Refuses while gate G1 is unreachable from a live path,")
    w("#    while the database is behind the migrations, while a table is")
    w("#    unclassified, and while the export is unverified or stale.")
    w("python -m app.scripts.legacy_reset --purge --confirm \\")
    w("    --export-dir <the directory step 1 printed> \\")
    w("    --actor '<operator name>' [--actor-user-id <users.id>]")
    w("")
    w("# 4. Re-embed BEFORE regrading: the regrade reads retrieval, and")
    w("#    re-embedding changes what retrieval returns.")
    w("python -m app.scripts.reembed --dry-run")
    w("python -m app.scripts.reembed --confirm        # needs VOYAGE_CONTEXT_4")
    w("")
    w("# 5. Regrade. Plan first; --confirm needs the model credentials and")
    w("#    VOYAGE_CONTEXT_4, and refuses without them.")
    w("python -m app.scripts.legacy_reset --regrade")
    w("python -m app.scripts.legacy_reset --regrade --confirm")
    w("```")
    w("")
    w(
        "Steps 4 and 5 cannot run in this phase: there is no OpenAI key and "
        "no Voyage key. Both are listed in `VERIFICATION_PENDING.md` with their "
        "measured work plans and the command that settles each one."
    )
    return "\n".join(out) + "\n"


# ── Export ───────────────────────────────────────────────────────────────────

EXPORT_ROOT = Path("legacy_reset_exports")
MANIFEST_NAME = "manifest.json"


#: Written into the export root the first time one is created. An export holds
#: real candidate resumes, real interview transcripts and real report text, and
#: the default location is inside the working tree. A self-ignoring directory
#: makes committing one impossible without a deliberate `git add -f`, and it
#: does that without editing a shared `.gitignore` that several people are
#: changing at once.
EXPORT_ROOT_GITIGNORE = (
    "# Legacy reset exports carry real candidate data: resumes, transcripts and\n"
    "# report text. Nothing in here is ever committed.\n"
    "*\n"
)


def export_directory(root: Path | None = None, *, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return (root or EXPORT_ROOT) / stamp


def ensure_export_root(directory: Path) -> None:
    """Create the export root and make it self-ignoring before anything lands."""
    root = directory.parent
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".gitignore"
    if not marker.exists():
        marker.write_text(EXPORT_ROOT_GITIGNORE, encoding="utf-8")


async def write_export(
    session: AsyncSession, directory: Path
) -> dict[str, Any]:
    """Everything about to be purged, plus the rows it references.

    The context tables are exported in full because an export that cannot be
    restored on its own is not a backup: inserting a report whose job and
    application no longer exist fails on a foreign key, and an export that only
    restores into the database it came from is a copy, not a backup.
    """
    ensure_export_root(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tables: OrderedDict[str, dict[str, Any]] = OrderedDict()

    async def _dump(name: str, sql: str, params: dict[str, Any] | None = None) -> None:
        rows = await _rows(session, sql, params)
        path = directory / f"{name}.json"
        path.write_text(dumps(rows), encoding="utf-8")
        tables[name] = {
            "rows": len(rows),
            "file": path.name,
            "sha256": sha256_of(path),
        }

    async def _columns(table: str) -> str:
        return ", ".join(f'"{col}"' for col in await storable_columns(session, table))

    for table in CONTEXT_TABLES:
        await _dump(f"context__{table}", f"SELECT {await _columns(table)} FROM {table}")

    for rule in purge_order():
        if rule.bucket == PURGE:
            await _dump(
                rule.table,
                f"SELECT {await _columns(rule.table)} FROM {rule.table} "
                f"WHERE {_scope_clause_all(rule)}",
            )
        elif rule.bucket == RESET:
            columns = ", ".join(col for col, _ in rule.resets)
            await _dump(
                rule.table,
                f"SELECT id, {columns} FROM {rule.table} "
                f"WHERE {_scope_clause_all(rule)}",
            )
        elif rule.bucket == DETACH:
            await _dump(
                rule.table,
                f"SELECT {await _columns(rule.table)} FROM {rule.table} "
                "WHERE evaluation_id IS NOT NULL",
            )

    objects = await reconcile_object_store(session)
    object_manifest = {
        "performed": objects.performed,
        "reason": objects.reason,
        "bucket": objects.bucket,
        "objects_listed": objects.objects_listed,
        "rows_with_object_uri": objects.rows_with_object_uri,
        "rows_with_legacy_uri": objects.rows_with_legacy_uri,
        "rows_with_no_uri": objects.rows_with_no_uri,
        "rows_missing_object": list(objects.rows_missing_object),
        "objects_missing_row": list(objects.objects_missing_row),
    }
    objects_path = directory / "s3_manifest.json"
    objects_path.write_text(dumps(object_manifest), encoding="utf-8")

    revision = await _scalar(session, "SELECT version_num FROM alembic_version")
    manifest = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "database": _safe_database_name(),
        "schema_revision": str(revision),
        "context_tables": list(CONTEXT_TABLES),
        "tables": tables,
        "s3_manifest": {
            "file": objects_path.name,
            "sha256": sha256_of(objects_path),
        },
        "restore_verified": False,
        "restore_reconciliation": None,
        "digest": _manifest_digest(tables),
    }
    (directory / MANIFEST_NAME).write_text(dumps(manifest), encoding="utf-8")
    return manifest


def _manifest_digest(tables: dict[str, dict[str, Any]]) -> str:
    """One digest over every file digest, so a manifest cannot be edited to
    excuse a file that changed."""
    joined = "\n".join(
        f"{name}:{meta['rows']}:{meta['sha256']}" for name, meta in sorted(tables.items())
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def read_manifest(directory: Path) -> dict[str, Any]:
    path = directory / MANIFEST_NAME
    if not path.exists():
        raise ExportNotVerified(f"No {MANIFEST_NAME} in {directory}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = _manifest_digest(manifest["tables"])
    if manifest.get("digest") != expected:
        raise ExportNotVerified(
            f"{directory}: the manifest digest does not match the file digests "
            "it lists. The manifest or an export file was changed after it was "
            "written."
        )
    for name, meta in manifest["tables"].items():
        path = directory / meta["file"]
        if not path.exists():
            raise ExportNotVerified(f"{directory}: {meta['file']} is missing")
        actual = sha256_of(path)
        if actual != meta["sha256"]:
            raise ExportNotVerified(
                f"{directory}: {meta['file']} has changed since it was exported"
            )
    return manifest


# ── Test restore ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def _restore_scope(session: AsyncSession):
    """A bulk data load into a SCRATCH database, not an application path.

    Row-level security still has to be got past, so the same explicit
    `app.bypass_rls` flag `superadmin_scope` uses is set here. What is different
    is `session_replication_role = replica`, which turns off foreign-key
    triggers for the transaction. That is what every restore does and why
    `pg_restore` loads data before it adds constraints: a self-referencing
    column (`evidence_items.superseded_by`) cannot be satisfied by any single
    insertion order, so an order-only restore would fail on data that is
    perfectly consistent. The constraints are checked immediately afterwards by
    the reconciliation, which counts what actually landed.

    It is deliberately NOT used anywhere near the source database.

    THE SCRATCH DATABASE MUST BE REACHABLE AS A SUPERUSER, because
    `session_replication_role` is a superuser setting. If it is not, Postgres
    refuses the SET and the restore stops there with that message, which is the
    right outcome: a restore that quietly fell back to insertion order would
    fail later, on one self-referencing row, and look like a corrupt export.
    """
    await session.execute(
        text(
            "SELECT set_config('app.tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        )
    )
    await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    yield session


async def restore_into(session: AsyncSession, directory: Path) -> dict[str, int]:
    """Insert the export into a scratch database and return the row counts.

    Context tables go in first, parents before children, then the purged rows in
    the reverse of the deletion order so a child never precedes its parent.
    `ON CONFLICT DO NOTHING` makes the restore idempotent, which matters because
    the first thing anybody does with a restore that half-failed is run it again.
    """
    manifest = read_manifest(directory)
    restored: dict[str, int] = {}

    ordered: list[tuple[str, str]] = [
        (f"context__{table}", table) for table in CONTEXT_TABLES
    ]
    # Reversed deletion order puts parents before children, and DETACH tables
    # are restored too: the export holds them as they were BEFORE the reference
    # was nulled, which is the only place the link between a human decision and
    # the evaluation it was about survives. Leaving them out would make the
    # backup unable to answer the one question the detachment gives up.
    for rule in reversed(purge_order()):
        if rule.bucket in (PURGE, DETACH):
            ordered.append((rule.table, rule.table))

    for name, table in ordered:
        meta = manifest["tables"].get(name)
        if meta is None:
            raise ExportNotVerified(f"{directory}: the export has no {name}.json")
        rows = json.loads((directory / meta["file"]).read_text(encoding="utf-8"))
        restored[table] = await _insert_rows(session, table, rows)
    return restored


async def storable_columns(session: AsyncSession, table: str) -> tuple[str, ...]:
    """Columns whose value can be written back, in schema order.

    GENERATED columns are excluded, and that is not an optimisation. Postgres
    refuses an INSERT that supplies one, so a full-row export of `profiles`
    (whose `resume_tsv` is generated from `resume_text`) is an export that
    cannot be restored. They carry no information either: a generated column is
    a function of columns that ARE exported, so it comes back identical.
    """
    rows = await _rows(
        session,
        """
        SELECT a.attname AS name
          FROM pg_attribute a
         WHERE a.attrelid = CAST(:table AS regclass)
           AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
         ORDER BY a.attnum
        """,
        {"table": table},
    )
    return tuple(row["name"] for row in rows)


async def _column_types(session: AsyncSession, table: str) -> dict[str, str]:
    rows = await _rows(
        session,
        """
        SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type
          FROM pg_attribute a
         WHERE a.attrelid = CAST(:table AS regclass)
           AND a.attnum > 0 AND NOT a.attisdropped
        """,
        {"table": table},
    )
    return {row["name"]: row["type"] for row in rows}


def _restore_literal(value: Any) -> Any:
    """Every exported value as something asyncpg will accept behind a CAST.

    JSON is written back as its text form, a vector as its bracketed literal, a
    boolean as true/false, and everything else as text. The CAST in the SQL is
    what turns it back into a uuid, a timestamptz, a jsonb or a vector, which
    means the restore does not have to know a single column's type in Python
    and cannot drift from the schema when a column is added.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=json_default)
    return str(value)


async def _insert_rows(
    session: AsyncSession, table: str, rows: Sequence[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    types = await _column_types(session, table)
    storable = set(await storable_columns(session, table))
    columns = [col for col in rows[0] if col in storable]
    unknown = [col for col in rows[0] if col not in types]
    if unknown:
        raise ExportNotVerified(
            f"{table}: the export carries columns this schema does not have "
            f"({', '.join(sorted(unknown))}). The export was taken against a "
            "different schema revision and restoring it would silently drop "
            "them."
        )
    # The parameter is bound as TEXT and cast in SQL. Binding it as its real
    # type would make the driver demand a `datetime` for a timestamptz and a
    # `UUID` for a uuid, which would put a type table in Python next to the one
    # Postgres already has. Casting from text keeps the schema the only place a
    # column's type is written down.
    values = ", ".join(
        f"CAST(CAST(:{col} AS text) AS {types[col]})" for col in columns
    )
    column_list = ", ".join(f'"{col}"' for col in columns)
    sql = text(
        f"INSERT INTO {table} ({column_list}) VALUES ({values}) "
        "ON CONFLICT DO NOTHING"
    )
    inserted = 0
    for row in rows:
        result = await session.execute(
            sql, {col: _restore_literal(row[col]) for col in columns}
        )
        inserted += result.rowcount or 0
    return inserted


#: How many identifiers go into one presence query. Large enough that the
#: 15,000-row tables take a handful of round trips, small enough that the
#: parameter array never approaches Postgres's limit.
PRESENCE_BATCH = 1000


def exported_ids(directory: Path) -> dict[str, list[str]]:
    """{table: [id, ...]} for every table the export carries rows for."""
    manifest = read_manifest(directory)
    ids: dict[str, list[str]] = {}
    for name, meta in manifest["tables"].items():
        rule = rule_for(name)
        if rule is not None and rule.bucket == RESET:
            continue
        table = name[len("context__") :] if name.startswith("context__") else name
        rows = json.loads((directory / meta["file"]).read_text(encoding="utf-8"))
        ids[table] = [str(row["id"]) for row in rows]
    return ids


async def _present_ids(session: AsyncSession, table: str, ids: Sequence[str]) -> int:
    """How many of these identifiers the database actually has.

    NOT `SELECT COUNT(*) FROM table`. The scratch database is migrated, and two
    migrations seed rows into `users`, so a total count answers "how many rows
    are here" when the question is "did every exported row arrive". The first
    version of this check reported a restore as failed because the target was
    doing its job.
    """
    present = 0
    for start in range(0, len(ids), PRESENCE_BATCH):
        batch = list(ids[start : start + PRESENCE_BATCH])
        present += int(
            await _scalar(
                session,
                f"SELECT COUNT(*) FROM {table} "
                # Bound as text and cast in SQL, for the same reason the insert
                # is: the driver would otherwise want a Python list typed as
                # uuid, and the type table belongs in Postgres.
                "WHERE id = ANY(CAST(CAST(:ids AS text) AS uuid[]))",
                {"ids": "{" + ",".join(batch) + "}"},
            )
        )
    return present


async def verify_export(
    directory: Path, scratch_url: str, *, source_session: AsyncSession
) -> dict[str, Any]:
    """Restore into a scratch database and reconcile counts in both directions.

    Forward: every row the export claims is present in the scratch database.
    Backward: every row the SOURCE database holds is in the export. A backup
    that restores cleanly and is missing a table is still a backup of the wrong
    thing.
    """
    manifest = read_manifest(directory)
    engine = create_async_engine(scratch_url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as scratch:
            async with scratch.begin():
                async with _restore_scope(scratch):
                    restored = await restore_into(scratch, directory)
            async with scratch.begin():
                async with _restore_scope(scratch):
                    in_scratch = {
                        table: await _present_ids(scratch, table, ids)
                        for table, ids in exported_ids(directory).items()
                    }
    finally:
        await engine.dispose()

    forward: list[dict[str, Any]] = []
    for table, count in sorted(in_scratch.items()):
        name = f"context__{table}" if table in CONTEXT_TABLES else table
        expected = manifest["tables"][name]["rows"]
        forward.append(
            {"table": table, "exported": expected, "restored": count, "match": expected == count}
        )
    if not forward:
        raise ExportNotVerified(
            f"{directory}: the restore checked nothing. An export that verifies "
            "zero tables would report itself as a good backup."
        )

    backward: list[dict[str, Any]] = []
    for rule in purge_order():
        if rule.bucket != PURGE:
            continue
        source = int(
            await _scalar(
                source_session,
                f"SELECT COUNT(*) FROM {rule.table} WHERE {_scope_clause_all(rule)}",
            )
        )
        exported = manifest["tables"][rule.table]["rows"]
        backward.append(
            {
                "table": rule.table,
                "in_database": source,
                "in_export": exported,
                "match": source == exported,
            }
        )

    reconciliation = {
        "scratch_database": scratch_url.rsplit("/", 1)[-1],
        "forward": forward,
        "backward": backward,
        "forward_ok": all(item["match"] for item in forward),
        "backward_ok": all(item["match"] for item in backward),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["restore_reconciliation"] = reconciliation
    manifest["restore_verified"] = bool(
        reconciliation["forward_ok"] and reconciliation["backward_ok"]
    )
    (directory / MANIFEST_NAME).write_text(dumps(manifest), encoding="utf-8")
    return reconciliation


# ── Purge ────────────────────────────────────────────────────────────────────


@dataclass
class TenantPurge:
    tenant_id: str
    name: str
    counts: "OrderedDict[str, int]" = field(default_factory=OrderedDict)
    applied: bool = False

    @property
    def total(self) -> int:
        return sum(self.counts.values())


async def assert_export_usable(
    session: AsyncSession, directory: Path
) -> dict[str, Any]:
    """The two questions the purge must answer before it deletes anything.

    Was this export test-restored, and does it still describe THIS database?
    Both are refusals rather than warnings. A warning printed above a DELETE is
    a warning nobody reads.
    """
    manifest = read_manifest(directory)
    if not manifest.get("restore_verified"):
        raise ExportNotVerified(
            f"{directory} has restore_verified=false. An export that has never "
            "been test-restored is not a backup. Re-run --export with "
            "--scratch-database-url pointing at an empty migrated database."
        )
    drift: list[str] = []
    for rule in purge_order():
        if rule.bucket != PURGE:
            continue
        live = int(
            await _scalar(
                session,
                f"SELECT COUNT(*) FROM {rule.table} WHERE {_scope_clause_all(rule)}",
            )
        )
        exported = manifest["tables"][rule.table]["rows"]
        if live != exported:
            drift.append(f"{rule.table}: database {live}, export {exported}")
    if drift:
        raise ExportNotVerified(
            "The database has changed since the export was taken, so the export "
            "does not cover everything the purge would delete: " + "; ".join(drift)
        )
    return manifest


async def purge_tenant(
    session: AsyncSession, *, tenant_id: str, name: str, apply: bool
) -> TenantPurge:
    """One tenant, one transaction. The caller owns the transaction boundary.

    In dry-run mode every statement is replaced by the COUNT it would have
    affected, so the numbers a reviewer reads are produced by the same scope
    clause the DELETE uses rather than by a second query somebody has to keep
    in step.
    """
    outcome = TenantPurge(tenant_id=tenant_id, name=name, applied=apply)
    params = {"tenant": tenant_id}
    for rule in purge_order():
        if not rule.tenant_column:
            continue
        if rule.bucket == PURGE:
            if apply:
                result = await session.execute(text(delete_sql(rule)), params)
                outcome.counts[rule.table] = result.rowcount or 0
            else:
                outcome.counts[rule.table] = int(
                    await _scalar(session, count_sql(rule), params)
                )
        elif rule.bucket == RESET:
            if apply:
                result = await session.execute(text(reset_sql(rule)), params)
                outcome.counts[f"{rule.table} (reset)"] = result.rowcount or 0
            else:
                outcome.counts[f"{rule.table} (reset)"] = int(
                    await _scalar(session, _reset_count_sql(rule), params)
                )
        elif rule.bucket == DETACH:
            if apply:
                result = await session.execute(
                    text(detach_sql(rule)), {**params, "note": DETACH_NOTE}
                )
                outcome.counts[f"{rule.table} (detached)"] = result.rowcount or 0
            else:
                outcome.counts[f"{rule.table} (detached)"] = int(
                    await _scalar(session, _detach_count_sql(rule), params)
                )
    return outcome


def _reset_count_sql(rule: TableRule) -> str:
    dirty = " OR ".join(f"{col} IS DISTINCT FROM {value}" for col, value in rule.resets)
    return (
        f"SELECT COUNT(*) FROM {rule.table} "
        f"WHERE {_scope_clause(rule)} AND ({dirty})"
    )


def _detach_count_sql(rule: TableRule) -> str:
    return (
        f"SELECT COUNT(*) FROM {rule.table} t JOIN evaluations e "
        f"ON e.id = t.evaluation_id WHERE t.{rule.tenant_column} = CAST(:tenant AS uuid)"
    )


PURGE_ACTION = "legacy_reset_purge"


async def run_purge(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    export_dir: Path | None,
    apply: bool,
    actor: str,
    actor_user_id: str | None,
) -> list[TenantPurge]:
    """Survey-checked, export-checked, one transaction per tenant, audited.

    The audit row is written INSIDE the tenant's transaction. Writing it after
    the commit would leave a window in which the deletion happened and nothing
    recorded it, which is the one state this operation must not be able to
    reach.
    """
    async with session_factory() as session:
        async with superadmin_scope(session):
            tables = await live_tables(session)
            assert_classified(tables)
            manifest: dict[str, Any] | None = None
            if apply:
                assert_schema_current(tables)
                # The archive-and-mark step clears a job's approval stamps and
                # relies on G1 to block evaluation afterwards. Checked, never
                # assumed: see `assert_gate_enforced`.
                assert_gate_enforced()
                if export_dir is None:
                    raise ExportNotVerified(
                        "--purge --confirm requires --export-dir. Nothing is "
                        "hard-deleted before it is exported."
                    )
                manifest = await assert_export_usable(session, export_dir)
            tenants = await _rows(
                session, "SELECT id::text AS id, name FROM tenants ORDER BY name"
            )

    outcomes: list[TenantPurge] = []
    for tenant in tenants:
        async with session_factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    outcome = await purge_tenant(
                        session,
                        tenant_id=tenant["id"],
                        name=tenant["name"],
                        apply=apply,
                    )
                    if apply:
                        await audit(
                            session,
                            tenant_id=tenant["id"],
                            actor_user_id=actor_user_id,
                            action=PURGE_ACTION,
                            target_type="tenant",
                            target_id=tenant["id"],
                            metadata={
                                "actor": actor,
                                "scope": "legacy assessment and report data "
                                "(spec-doc6 decision D2)",
                                "counts": dict(outcome.counts),
                                "rows_deleted": outcome.total,
                                "export_dir": str(export_dir),
                                "export_digest": (manifest or {}).get("digest"),
                                "export_schema_revision": (manifest or {}).get(
                                    "schema_revision"
                                ),
                            },
                        )
        outcomes.append(outcome)
        logger.info(
            "legacy_reset.tenant tenant=%s applied=%s rows=%d",
            tenant["name"], apply, outcome.total,
        )
    return outcomes


# ── Regrade ──────────────────────────────────────────────────────────────────
#
# CANNOT EXECUTE IN THIS PHASE. spec-doc6 decision D6: there is no OpenAI and
# no Voyage key, so the pre-screen grading calls cannot be made. `--regrade`
# without `--confirm` produces the work plan; `--regrade --confirm` refuses to
# start without a key rather than silently grading every candidate on the
# deterministic dev fallback, which would write a hash-derived ranking into the
# column a recruiter sorts on.

#: The task the pre-screen grade is routed under, and therefore the model and
#: the price it is estimated at. Read from the routing policy rather than
#: restated, so a change to the policy moves the estimate with it.
REGRADE_TASK = "rerank"
#: `matching._RERANK_BATCH_SIZE`. Imported below rather than duplicated; this
#: name exists so the work plan can say which constant it used.
REGRADE_BATCH_ATTRIBUTE = "_RERANK_BATCH_SIZE"
#: Rough per-call token shape for the estimate, and labelled as an estimate
#: everywhere it surfaces. A batch prompt carries the JD, the job's categories
#: and up to ten profile summaries; the completion is one JSON object per
#: profile with four short comments each.
ESTIMATED_PROMPT_TOKENS_PER_BATCH = 6000
ESTIMATED_COMPLETION_TOKENS_PER_BATCH = 2500


@dataclass
class RegradePlan:
    jobs: int
    candidates: int
    tenants: "OrderedDict[str, dict[str, int]]"
    batch_size: int
    model: str
    llm_calls: int
    embedding_calls: int
    estimated_cost_usd: float
    estimated_seconds: float
    keys_present: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "jobs": self.jobs,
            "candidates": self.candidates,
            "tenants": {k: dict(v) for k, v in self.tenants.items()},
            "batch_size": self.batch_size,
            "model": self.model,
            "llm_calls": self.llm_calls,
            "embedding_calls": self.embedding_calls,
            "estimated_cost_usd": round(self.estimated_cost_usd, 2),
            "estimated_minutes": round(self.estimated_seconds / 60.0, 1),
            "keys_present": self.keys_present,
        }


def _regrade_batch_size() -> int:
    from app.services import matching  # noqa: PLC0415

    return int(getattr(matching, REGRADE_BATCH_ATTRIBUTE))


async def plan_regrade(session: AsyncSession) -> RegradePlan:
    """The work plan, computed from rows rather than from an assumption.

    A candidate is in scope when the application is not archived, the profile
    carries a resume, and the pre-screen grade is absent. That last condition is
    what makes the run idempotent and resumable with no state file: the purge
    clears every `match_score`, a completed job leaves them set, and re-running
    after an interruption picks up exactly what is left. It is a ROW predicate,
    not a timestamp.
    """
    rows = await _rows(
        session,
        """
        SELECT t.name AS tenant,
               l.job_id::text AS job_id,
               COUNT(*) AS candidates
          FROM job_candidate_links l
          JOIN profiles p ON p.id = l.profile_id
          JOIN tenants t ON t.id = l.tenant_id
         WHERE l.archived_at IS NULL
           AND p.resume_url IS NOT NULL
           AND l.match_score IS NULL
         GROUP BY t.name, l.job_id
         ORDER BY t.name
        """,
    )
    batch_size = _regrade_batch_size()
    per_tenant: OrderedDict[str, dict[str, int]] = OrderedDict()
    llm_calls = 0
    embedding_calls = 0
    total_candidates = 0
    for row in rows:
        tenant = str(row["tenant"])
        candidates = int(row["candidates"])
        total_candidates += candidates
        bucket = per_tenant.setdefault(tenant, {"jobs": 0, "candidates": 0})
        bucket["jobs"] += 1
        bucket["candidates"] += candidates
        llm_calls += -(-candidates // batch_size)
        # One JD embedding per job, plus one batched document embedding call
        # for the profiles whose vector the purge did not touch but which the
        # matching run backfills when it is missing.
        embedding_calls += 2

    model = MODEL_FOR_TASK[REGRADE_TASK]
    prices = TOKEN_PRICES_USD_PER_MILLION[model]
    estimated_cost = (
        llm_calls
        * (
            ESTIMATED_PROMPT_TOKENS_PER_BATCH * prices["prompt"]
            + ESTIMATED_COMPLETION_TOKENS_PER_BATCH * prices["completion"]
        )
        / 1_000_000
    )
    settings = get_settings()
    return RegradePlan(
        jobs=len(rows),
        candidates=total_candidates,
        tenants=per_tenant,
        batch_size=batch_size,
        model=model,
        llm_calls=llm_calls,
        embedding_calls=embedding_calls,
        estimated_cost_usd=estimated_cost,
        estimated_seconds=llm_calls * timeout_for(REGRADE_TASK),
        keys_present=bool(
            settings.openai_gpt_terra
            and settings.openai_gpt_luna
            and settings.voyage_context_4
        ),
    )


class MissingCredentials(RuntimeError):
    """The run needs a live provider credential and there is not one."""


@dataclass
class RegradeProgress:
    job_id: str
    tenant: str
    scored: int
    remaining: int


async def run_regrade(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    apply: bool,
    matcher: Any | None = None,
) -> tuple[RegradePlan, list[RegradeProgress]]:
    """Re-run pre-screen grading, per tenant, per job, resumable.

    `matcher` is the coroutine that scores one job. It defaults to
    `matching.run_matching`, and the seam exists so the loop, its resumability
    and its reconciliation can be exercised end to end against a recorded
    provider without a credential. It is never used to substitute a result: a
    caller that passes nothing and has no key gets a refusal.
    """
    async with session_factory() as session:
        async with superadmin_scope(session):
            plan = await plan_regrade(session)
    if not apply:
        return plan, []

    if matcher is None:
        if not plan.keys_present:
            raise MissingCredentials(
                "--regrade --confirm needs OPENAI_GPT_TERRA, OPENAI_GPT_LUNA and "
                "VOYAGE_CONTEXT_4. "
                "Without them every candidate would be graded on the "
                "deterministic dev fallback, and a hash-derived ranking written "
                "into the column a recruiter sorts on is worse than no grade."
            )
        from app.services.matching import run_matching  # noqa: PLC0415

        matcher = run_matching

    progress: list[RegradeProgress] = []
    for tenant, buckets in plan.tenants.items():
        logger.info(
            "legacy_reset.regrade_tenant tenant=%s jobs=%d candidates=%d",
            tenant, buckets["jobs"], buckets["candidates"],
        )
    async with session_factory() as session:
        async with superadmin_scope(session):
            jobs = await _rows(
                session,
                """
                SELECT DISTINCT l.job_id::text AS job_id, t.name AS tenant
                  FROM job_candidate_links l
                  JOIN profiles p ON p.id = l.profile_id
                  JOIN tenants t ON t.id = l.tenant_id
                 WHERE l.archived_at IS NULL
                   AND p.resume_url IS NOT NULL
                   AND l.match_score IS NULL
                 ORDER BY t.name, job_id
                """,
            )

    for job in jobs:
        async with session_factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await matcher(session, uuid.UUID(job["job_id"]))
        async with session_factory() as session:
            async with superadmin_scope(session):
                scored = int(
                    await _scalar(
                        session,
                        "SELECT COUNT(*) FROM job_candidate_links "
                        "WHERE job_id = CAST(:job AS uuid) AND match_score IS NOT NULL",
                        {"job": job["job_id"]},
                    )
                )
                remaining = int(
                    await _scalar(
                        session,
                        "SELECT COUNT(*) FROM job_candidate_links l "
                        "JOIN profiles p ON p.id = l.profile_id "
                        "WHERE l.job_id = CAST(:job AS uuid) AND l.archived_at IS NULL "
                        "AND p.resume_url IS NOT NULL AND l.match_score IS NULL",
                        {"job": job["job_id"]},
                    )
                )
        progress.append(
            RegradeProgress(
                job_id=job["job_id"], tenant=job["tenant"], scored=scored, remaining=remaining
            )
        )
        logger.info(
            "legacy_reset.regrade_job job=%s scored=%d remaining=%d",
            job["job_id"], scored, remaining,
        )
    return plan, progress


def render_regrade_plan(plan: RegradePlan) -> str:
    """The work plan, as the lines that go into VERIFICATION_PENDING.md."""
    out = [
        "Pre-screen regrade work plan (estimates, list prices, not a quotation)",
        f"  jobs to re-score          : {plan.jobs}",
        f"  candidates in scope       : {plan.candidates}",
        f"  batch size                : {plan.batch_size}",
        f"  model                     : {plan.model}",
        f"  scoring calls             : {plan.llm_calls}",
        f"  embedding calls           : {plan.embedding_calls}",
        f"  estimated cost            : USD {plan.estimated_cost_usd:.2f}",
        f"  estimated wall clock      : {plan.estimated_seconds / 60.0:.1f} minutes "
        "at the per-call timeout, which is the worst case rather than the "
        "expected one",
        f"  credentials present       : {'yes' if plan.keys_present else 'no'}",
        "",
        "  per tenant:",
    ]
    for tenant, bucket in plan.tenants.items():
        out.append(
            f"    {tenant}: {bucket['jobs']} jobs, {bucket['candidates']} candidates"
        )
    if not plan.tenants:
        out.append("    (none)")
    return "\n".join(out)


# ── Entry point ──────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """The repository root, or the working directory when there is not one.

    Walks up looking for `.git` rather than counting `parents[N]`, because the
    count is wrong the moment the tree is mounted somewhere else: inside the
    dev container `backend/` is mounted at `/app`, and a fixed index resolves to
    `/`, where the survey then fails to write. Falling back to the working
    directory keeps `--survey` usable in that container, and `--out` overrides
    both.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd()


async def _survey(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as session:
        async with superadmin_scope(session):
            survey = await collect_survey(session)
    # Written into the documentation tree beside the other point-in-time
    # survey artifacts (docs/history/), not to the repository root.
    target = (
        Path(args.out)
        if args.out
        else _repo_root() / "docs" / "history" / "LEGACY_RESET_SURVEY.md"
    )
    target.write_text(render_survey(survey), encoding="utf-8")
    print(f"survey written to {target}")
    print(f"tenants={len(survey.tenants)} tables={len(survey.live_tables)}")
    for case in survey.signoff_required:
        print(f"  SIGN-OFF NEEDED: {case.headline} = {case.count}")
    return 0


async def _export(args: argparse.Namespace) -> int:
    directory = Path(args.export_dir) if args.export_dir else export_directory(
        _repo_root() / EXPORT_ROOT
    )
    factory = get_session_factory()
    async with factory() as session:
        async with superadmin_scope(session):
            tables = await live_tables(session)
            assert_classified(tables)
            assert_schema_current(tables)
            manifest = await write_export(session, directory)
            total = sum(meta["rows"] for meta in manifest["tables"].values())
            print(f"export written to {directory} ({total} rows, digest {manifest['digest']})")
            scratch = args.scratch_database_url
            if not scratch:
                print(
                    "restore_verified=false: no --scratch-database-url was given, "
                    "so this export has not been test-restored and --purge "
                    "--confirm will refuse it."
                )
                return 0
            reconciliation = await verify_export(directory, scratch, source_session=session)
    print(
        f"test restore into {reconciliation['scratch_database']}: "
        f"forward_ok={reconciliation['forward_ok']} "
        f"backward_ok={reconciliation['backward_ok']}"
    )
    for item in reconciliation["forward"]:
        if not item["match"]:
            print(
                f"  MISMATCH {item['table']}: exported {item['exported']}, "
                f"restored {item['restored']}"
            )
    for item in reconciliation["backward"]:
        if not item["match"]:
            print(
                f"  MISMATCH {item['table']}: database {item['in_database']}, "
                f"export {item['in_export']}"
            )
    return 0 if reconciliation["forward_ok"] and reconciliation["backward_ok"] else 1


async def _purge(args: argparse.Namespace) -> int:
    if args.confirm and not args.actor:
        raise SystemExit(
            "--purge --confirm requires --actor naming the operator. The audit "
            "row records who did this and it cannot record nobody."
        )
    export_dir = Path(args.export_dir) if args.export_dir else None
    outcomes = await run_purge(
        get_session_factory(),
        export_dir=export_dir,
        apply=bool(args.confirm),
        actor=args.actor or "",
        actor_user_id=args.actor_user_id,
    )
    mode = "APPLIED" if args.confirm else "DRY RUN, nothing was changed"
    print(f"purge {mode}")
    for outcome in outcomes:
        print(f"  {outcome.name}: {outcome.total} rows")
        for table, count in outcome.counts.items():
            if count:
                print(f"    {table}: {count}")
    print(f"total rows: {sum(o.total for o in outcomes)}")
    return 0


async def _regrade(args: argparse.Namespace) -> int:
    plan, progress = await run_regrade(get_session_factory(), apply=bool(args.confirm))
    print(render_regrade_plan(plan))
    for item in progress:
        print(f"  {item.tenant} job {item.job_id}: scored={item.scored} remaining={item.remaining}")
    if progress:
        stuck = [item for item in progress if item.remaining]
        print(
            f"reconciliation: {len(progress)} jobs processed, "
            f"{len(stuck)} still carrying ungraded applications"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legacy_reset",
        description="Survey, export, purge and regrade the legacy assessment data.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--survey", action="store_true", help="read-only, writes the survey")
    mode.add_argument("--export", action="store_true", help="write a restorable export")
    mode.add_argument("--purge", action="store_true", help="delete, dry run by default")
    mode.add_argument("--regrade", action="store_true", help="re-run pre-screen grading")
    parser.add_argument("--confirm", action="store_true", help="actually apply")
    parser.add_argument("--export-dir", help="the export directory to write or to use")
    parser.add_argument(
        "--scratch-database-url",
        help="an empty migrated database to test-restore the export into",
    )
    parser.add_argument("--actor", help="who is running this, recorded in the audit row")
    parser.add_argument("--actor-user-id", help="their users.id, when they have one")
    parser.add_argument("--out", help="where the survey markdown is written")
    return parser


#: The refusals a person is meant to read and act on. They are printed as the
#: message and an exit code rather than a traceback, because a stack trace above
#: a refusal buries the sentence that says what to do about it. Every other
#: exception still propagates with its traceback intact: a refusal is a designed
#: outcome and a crash is not, and rendering them the same way would hide the
#: crashes.
REFUSALS = (ClassificationGap, ExportNotVerified, GateNotWired, MissingCredentials)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.survey:
            return asyncio.run(_survey(args))
        if args.export:
            return asyncio.run(_export(args))
        if args.purge:
            return asyncio.run(_purge(args))
        return asyncio.run(_regrade(args))
    except REFUSALS as refusal:
        print(f"REFUSED: {refusal}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
