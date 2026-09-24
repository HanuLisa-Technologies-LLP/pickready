"""Cross-cutting platform invariants (killer-spec Part 4.4).

The Part 4 audit is a verification pass, and a verification pass that is done by
reading is done once. These are the checks that were run by hand for that audit,
written down so they run on every commit instead:

  * no numeric score reaches a client-facing response;
  * no third-party assessment instrument is named anywhere;
  * Gmail SMTP is the only outbound mail path;
  * no OTP surfaces in any portal UI;
  * no em dash reaches a user-visible string;
  * the LLM router caps what a person waits for.

Each one is a rule the product has already been shipped against once and could
silently regress on any future edit, which is exactly the class of thing worth a
test rather than a re-read.
"""
from __future__ import annotations

import pathlib
import re

import pytest

# ── Where the sources are, in BOTH layouts ──────────────────────────────────
#
# This used to be `parents[2] / "backend" / "app"`, which is correct for a
# git checkout and resolves to `/backend/app` inside the backend container,
# where the package actually lives at `/app/app`. That directory does not
# exist, `rglob` returned nothing, and every sweep below passed by scanning
# ZERO FILES -- in exactly the environment the project's own quick-start runs
# tests in. The rules were only ever really checked in CI, and the local run
# that "passed" was measuring nothing.
#
# Resolving from the imported package works in both layouts, and
# `test_the_sweeps_actually_have_something_to_sweep` makes a future silent
# emptying impossible.
import app as _app_package

BACKEND_APP = pathlib.Path(_app_package.__file__).resolve().parent


def _find_frontend() -> pathlib.Path:
    """The frontend tree, or a path that does not exist.

    Absent inside the backend container, which is fine: the Python sweeps still
    run there, and CI has both trees. What is NOT fine is not knowing which
    case you are in, so `_frontend_sources` is allowed to be empty while
    `_python_sources` is not.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "frontend"
        if (candidate / "app").exists():
            return candidate
    return pathlib.Path("/nonexistent-frontend")


REPO = BACKEND_APP.parent.parent
FRONTEND = _find_frontend()

EM_DASH = chr(8212)

DQ = r'"(?:[^"\\]|\\.)*"'
SQ = r"'(?:[^'\\]|\\.)*'"
PY_LITERAL = re.compile(DQ + "|" + SQ)


def _python_sources() -> list[pathlib.Path]:
    return sorted(BACKEND_APP.rglob("*.py"))


def _frontend_sources() -> list[pathlib.Path]:
    roots = [FRONTEND / "app", FRONTEND / "components", FRONTEND / "lib"]
    files: list[pathlib.Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.rglob("*.ts")))
            files.extend(sorted(root.rglob("*.tsx")))
    return files


def test_the_sweeps_actually_have_something_to_sweep() -> None:
    """The guard on every guard in this file.

    Each test below is a repo-wide sweep, and a sweep over an empty file list
    passes forever and protects nothing. That is not hypothetical: this module
    resolved `BACKEND_APP` to a path that does not exist inside the backend
    container, so every rule here was green while checking nothing, for as long
    as the file has existed.

    The frontend tree is legitimately absent in the backend container, so it is
    reported rather than required.
    """
    python = _python_sources()
    assert len(python) > 50, (
        f"the Python sweep found {len(python)} files under {BACKEND_APP}; "
        "these tests are not checking anything"
    )
    frontend = _frontend_sources()
    if FRONTEND.exists():
        assert len(frontend) > 50, (
            f"the frontend sweep found {len(frontend)} files under {FRONTEND}"
        )


# ── No third-party instrument, anywhere, including comments ─────────────────

FORBIDDEN_INSTRUMENTS = (
    "mbti",
    "myers-briggs",
    "hogan",
    "cliftonstrengths",
    "gallup",
    "big five",
    "16personalities",
)


def test_no_third_party_assessment_instrument_is_named() -> None:
    """The Vivekium Functional Index is proprietary work derived from first
    principles. Associating its name with a licensed instrument, even in a code
    comment, is the kind of thing that is read as a claim later."""
    offenders: list[str] = []
    for path in _python_sources() + _frontend_sources():
        # These name them in order to forbid them. `eval_report.py` carries
        # the labelled set the report evaluation measures against, and a
        # detector cannot be written without naming what it detects.
        if path.name in {
            "test_platform_audit.py",
            "test_functional_assessment.py",
            "eval_report.py",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for term in FORBIDDEN_INSTRUMENTS:
            if term in text:
                offenders.append(f"{path.name}: {term}")
    assert not offenders, f"third-party instrument named in: {offenders}"


def test_disc_is_only_ever_the_css_class() -> None:
    """`list-disc` is Tailwind, not the DISC assessment. The check is worth
    keeping separate so the useful signal is not drowned by false positives."""
    offenders: list[str] = []
    for path in _frontend_sources() + _python_sources():
        if path.name in {"test_platform_audit.py", "eval_report.py"}:
            continue
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for match in re.finditer(r"\bdisc\b", line, re.IGNORECASE):
                start = max(0, match.start() - 5)
                if "list-" in line[start : match.start()]:
                    continue
                offenders.append(f"{path.name}:{n}")
    assert not offenders, f"DISC referenced at: {offenders}"


# ── Gmail SMTP is the only outbound mail path ──────────────────────────────

def test_no_resend_or_mailtrap_integration_survives() -> None:
    """claude.md rule 5. "Resend" as an English verb is fine; an API client,
    a base URL or a key for either provider is not."""
    patterns = (
        re.compile(r"resend[._-]?api[._-]?key", re.IGNORECASE),
        re.compile(r"api\.resend\.com", re.IGNORECASE),
        re.compile(r"mailtrap", re.IGNORECASE),
    )
    offenders: list[str] = []
    for path in _python_sources() + _frontend_sources():
        if path.name == "test_platform_audit.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(f"{path.name}: {pattern.pattern}")
    assert not offenders, f"legacy email provider still referenced in: {offenders}"


def test_smtp_settings_only_accept_gmail() -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(smtp_host="smtp.sendgrid.net", smtp_user="a@gmail.com",
                 smtp_password="x", smtp_from_email="a@gmail.com")
    with pytest.raises(ValueError):
        # Port 465 / implicit SSL is not the sanctioned configuration.
        Settings(smtp_host="smtp.gmail.com", smtp_port=465, smtp_ssl=True,
                 smtp_starttls=False, smtp_user="a@gmail.com",
                 smtp_password="x", smtp_from_email="a@gmail.com")


# ── No OTP in any portal UI ────────────────────────────────────────────────

def test_no_otp_copy_reaches_any_portal() -> None:
    """Firebase owns authentication, and no one-time-code login step may
    appear in any UI (the SMS send path itself leaves in Phase 7 Wave B).

    THE ONE EXEMPTION IS GONE, AND THE RULE IS WHOLE AGAIN (2026-09-08). It
    was granted on 2026-09-05 for the corporate sender's mailbox-verification
    dialog, which proved a client controlled a business mailbox and
    authenticated nobody. That dialog was withdrawn with the sender OTP: SES
    refuses to send as any identity the account has not verified, so the code
    re-proved on registration what AWS enforces on every send. No portal
    surface may carry OTP copy, with no exception -- which is what claude.md
    said before the narrowing, and says again."""
    pattern = re.compile(r"\botp\b|one[- ]time password|verification code", re.IGNORECASE)
    offenders: list[str] = []
    for path in _frontend_sources():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(FRONTEND)}:{n}")
    assert not offenders, f"OTP copy present in: {offenders}"


def test_the_legacy_code_input_component_is_deleted() -> None:
    """It was retained unreachable until 2026-09-24 and is now deleted. A
    component that does not exist cannot put a code step back into a portal,
    and nothing may reach for it by name either."""
    assert not (FRONTEND / "components" / ("otp" + "-input.tsx")).exists()
    importers = [
        str(path.relative_to(FRONTEND))
        for path in _frontend_sources()
        if ("otp" + "-input") in path.read_text(encoding="utf-8")
    ]
    assert not importers, f"the deleted component is named by: {importers}"


# ── The vivekium forbidden terms (C7, owner-ruled final 2026-09-18) ─────────

#: Built from parts so this file's own sweep cannot read its pattern as a
#: violation, the same trick chr(8212) plays for the em dash. The brief,
#: verbatim: do not use these anywhere on the platform, in consent text,
#: emails or any system copy. The sanctioned phrasing is "employer clients
#: registered on the platform".
FORBIDDEN_TERMS = ("direct " + "employer", "manpower " + "agency")


def test_no_forbidden_relationship_terms_anywhere() -> None:
    """Frontend source, backend STRINGS, prompts and templates, one sweep.

    Case-insensitive, because a toast and an email template capitalise
    differently and the rule is about the words, not the casing.
    """
    offenders: list[str] = []
    for path in _frontend_sources():
        text = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN_TERMS:
            if term in text:
                offenders.append(f"{path.relative_to(FRONTEND)}: {term}")
    for path in _python_sources():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            for term in FORBIDDEN_TERMS:
                if term not in lowered:
                    continue
                for match in PY_LITERAL.finditer(line):
                    if term in match.group(0).lower():
                        offenders.append(f"{path.name}:{n}: {term}")
                        break
    for folder in ("prompts", "templates"):
        root = BACKEND_APP / folder
        if not root.exists():
            continue
        for path in root.rglob("*.txt"):
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for term in FORBIDDEN_TERMS:
                if term in text:
                    offenders.append(f"{path.name}: {term}")
    assert not offenders, f"forbidden relationship terms: {offenders}"


# ── The same two rules, over the DATABASE ──────────────────────────────────
#
# THE SOURCE SWEEPS ABOVE ONLY COVER TEXT THE CODE WRITES, and that is half
# the platform. Migration 0025 exists because 103 rows of seeded and generated
# copy carried em dashes straight onto the public application page, and it was
# found by loading a live job posting and reading it rather than by any test.
# The forbidden relationship terms arrive by exactly the same routes: a model
# writes a JD, a client types a company profile, a consent sentence is stored
# beside the act it records. C7 asks for the sweep "over source AND the
# database"; until now nothing here read a row.

#: (table, column) pairs holding copy a candidate or a client can read. Text
#: columns only: a jsonb document is swept whole, below, by casting it.
_CONTENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("jobs", "about_company"),
    ("jobs", "work_life"),
    ("jobs", "benefits"),
    ("jobs", "jd_markdown"),
    ("tenants", "details"),
    ("tenants", "culture"),
    # The consent record stores the VERBATIM sentence shown (migration 0117),
    # which is precisely the "consent text" the brief names first.
    ("candidate_consent_events", "consent_text"),
)

#: jsonb documents rendered as copy. Cast to text and swept whole: the keys
#: are fixed English identifiers this schema sets, so a match is in a value.
_CONTENT_JSON_COLUMNS: tuple[tuple[str, str], ...] = (("jobs", "jd_json"),)


def _database_offenders(needle: str) -> list[str]:
    """Rows whose stored copy contains `needle`, case-insensitively.

    A table this sweep names and cannot find is an ERROR, not a pass: the
    whole failure mode of a sweep is silently measuring nothing, and a
    renamed content column would otherwise take its rule with it.
    """
    import asyncio

    from sqlalchemy import text as sql
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    async def _sweep() -> list[str]:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                found: list[str] = []
                pairs = [
                    (table, column, column)
                    for table, column in _CONTENT_COLUMNS
                ] + [
                    (table, column, f"{column}::text")
                    for table, column in _CONTENT_JSON_COLUMNS
                ]
                for table, column, expression in pairs:
                    count = (
                        await conn.execute(
                            sql(
                                f"SELECT count(*) FROM {table} "
                                f"WHERE {expression} ILIKE :pattern"
                            ),
                            {"pattern": f"%{needle}%"},
                        )
                    ).scalar_one()
                    if count:
                        found.append(f"{table}.{column}: {count} row(s)")
                return found
        finally:
            await engine.dispose()

    return asyncio.run(_sweep())


def _skip_without_database() -> None:
    """Skip when no database is reachable, saying so.

    A skipped check is not a passed check. The source sweeps in this module
    run everywhere; these two need rows to read, and reporting green without
    a connection would be the exact dishonesty the rest of the file exists to
    prevent.
    """
    import asyncio

    from sqlalchemy import text as sql
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    async def _probe() -> bool:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sql("SELECT 1"))
                return True
        except Exception:  # noqa: BLE001 - any refusal to connect means no DB
            return False
        finally:
            await engine.dispose()

    if not asyncio.run(_probe()):
        pytest.skip("no database reachable: the stored-copy sweeps did not run")


def test_no_forbidden_relationship_terms_in_stored_copy() -> None:
    """C7, the half the source sweep cannot see."""
    _skip_without_database()
    offenders: list[str] = []
    for term in FORBIDDEN_TERMS:
        offenders.extend(
            f"{hit} contains {term!r}" for hit in _database_offenders(term)
        )
    assert not offenders, f"forbidden relationship terms in the database: {offenders}"


def test_no_em_dash_in_stored_copy() -> None:
    """What migration 0025 fixed, kept fixed. Generated content is written by
    a model on every published JD, so this is a live surface and not a
    backlog that stays cleaned."""
    _skip_without_database()
    offenders = _database_offenders(EM_DASH)
    assert not offenders, f"em dash in the database: {offenders}"


# ── No em dash in user-visible text ────────────────────────────────────────

def test_no_em_dash_in_frontend_source() -> None:
    offenders = [
        str(path.relative_to(FRONTEND))
        for path in _frontend_sources()
        if EM_DASH in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"em dash in frontend: {offenders}"


def test_no_em_dash_in_backend_string_literals() -> None:
    """Comments and docstrings may discuss the character; a STRING may not
    contain it, because a string is what reaches a toast, an email or a JD."""
    offenders: list[str] = []
    for path in _python_sources():
        in_doc = False
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            triples = stripped.count('"""') + stripped.count("'''")
            was_doc = in_doc
            if triples % 2 == 1:
                in_doc = not in_doc
            if was_doc or stripped.startswith(("#", '"""', "'''")):
                continue
            if EM_DASH not in line:
                continue
            # `chr(8212)` is how this rule is written down without breaking it.
            if "chr(8212)" in line:
                continue
            for match in PY_LITERAL.finditer(line):
                if EM_DASH in match.group(0):
                    offenders.append(f"{path.name}:{n}")
                    break
    assert not offenders, f"em dash in backend strings: {offenders}"


def test_prompt_and_template_files_are_clean() -> None:
    """The JD generator's prompts and the email bodies are the two places
    generated CONTENT is authored, so they are checked directly."""
    offenders: list[str] = []
    for folder in ("prompts", "templates"):
        root = BACKEND_APP / folder
        if not root.exists():
            continue
        offenders.extend(
            path.name
            for path in root.rglob("*.txt")
            if EM_DASH in path.read_text(encoding="utf-8", errors="replace")
        )
    assert not offenders, f"em dash in generated content: {offenders}"


# ── No number reaches a client ─────────────────────────────────────────────

def test_client_facing_ranking_payload_carries_no_score() -> None:
    from app.services.matching import client_breakdown, ranking_payload

    breakdown = {
        "skills_match": {"score": 91, "comment": "x " * 27},
        "experience_relevance": {"score": 74, "comment": "y " * 27},
        "overall": {"score": 83, "comment": "z " * 47},
    }
    for payload in (ranking_payload(breakdown), client_breakdown(breakdown)):
        flat = repr(payload)
        for score in ("91", "74", "83"):
            assert score not in flat, f"score {score} leaked in {flat[:200]}"


def test_match_percent_is_the_one_sanctioned_number() -> None:
    """Rule 1's single amendment (owner-ruled 2026-09-18, vivekium brief).

    The Executive Profile Match Score, `match_percent` on the recruiter
    candidate table, is the ONE number that reaches a client. This pins the
    exception at exactly that field: the serializer source names it once,
    and the per-parameter breakdown projections still leak nothing (the
    test above this one proves that with values).
    """
    import inspect

    from app.services import job_candidates

    source = inspect.getsource(job_candidates._row_payload)
    assert source.count('"match_percent"') == 1, (
        "match_percent must be defined exactly once in the row serializer"
    )
    # The word-label fields stay words: the amendment did not widen.
    for field in ("ctc_match_label", "notice_period_label",
                  "education_match_label", "bgv_status_label"):
        assert f'"{field}"' in source, f"{field} missing from the row payload"


def test_report_ratings_are_words_not_numbers() -> None:
    from app.services.functional_assessment import rating_label
    from app.services.rating import GRADES

    for score in (0, 25, 50, 75, 100):
        label = rating_label(score)
        assert not any(char.isdigit() for char in label), label
        assert label in set(GRADES)


def test_matching_labels_are_words_not_numbers() -> None:
    from app.services.matching import matching_label

    for score in (0, 3, 6, 8, 9.5):
        label = matching_label(score)
        assert not any(char.isdigit() for char in label), label


def test_the_assessment_and_the_ai_score_share_one_scale() -> None:
    """Two parallel five-label scales used to be kept in step by hand. One
    scale now, so "Matching" means the same thing wherever it appears."""
    from app.services.functional_assessment import rating_label
    from app.services.matching import MATCHING_LABELS, matching_label
    from app.services.rating import GRADES

    assert MATCHING_LABELS == GRADES
    for percent in range(0, 101):
        assert rating_label(percent) == matching_label(percent / 10.0)


# ── The LLM router bounds what a human waits for ───────────────────────────

#: A request handler is blocked on these AND the output is SHORT: a reply, a
#: label, an ordering, one question. A slower model does not make a 60-token
#: reply slow, so the latency brief's 15s / 30s contract is unchanged for them.
IMMEDIATE_INTERACTIVE_TASKS = (
    "conversation_turn",
    "situation_classification",
    "email_composition",
    "rerank",
)

#: A request handler is blocked and the output is a DOCUMENT.
#:
#: THIS TIER IS AN EXCEPTION AND IT IS DELIBERATE. The brief's flat 15s cap was
#: measured against a flash-class model; against a reasoning-tier model a
#: multi-thousand-token JD cannot finish inside it, so holding the cap would not
#: make the Generate JD button faster -- it would make every generation time
#: out and fall back to the deterministic template, permanently. That is the same argument the brief
#: already accepts for report_synthesis, one tier down. It is a NAMED, BOUNDED
#: list rather than a raised global cap, so a future task cannot join it by
#: accident.
#:
#: VIVEKIUM RELEASE: `swot_analysis` LEFT this tier (the SWOT is dispatched work
#: now, `pickready.generate_job_swot`) and `assessment_context` took its place:
#: Save Skills waits on the hidden context because it must land in the same
#: transaction as the human's save. Still two members, still capped.
GENERATIVE_INTERACTIVE_TASKS = ("jd_generation", "assessment_context")

GENERATIVE_INTERACTIVE_ATTEMPT_CAP = 30.0
GENERATIVE_INTERACTIVE_BUDGET_CAP = 60.0


def test_immediate_interactive_calls_are_capped_at_fifteen_seconds() -> None:
    """The latency brief's cap, applied where it belongs: to the calls a
    request handler is blocked on whose output is short."""
    from app.config.llm_providers import timeout_for

    for task in IMMEDIATE_INTERACTIVE_TASKS:
        assert timeout_for(task) <= 15.0, task


def test_the_generative_interactive_exception_stays_small_and_bounded() -> None:
    """The exception must not become the rule.

    Two things are asserted: the list is short, and the tasks on it are still
    capped -- just at a higher number. An exception with no ceiling of its own
    is not an exception, it is the absence of a rule.
    """
    from app.config.llm_providers import timeout_for, total_budget_for

    assert len(GENERATIVE_INTERACTIVE_TASKS) <= 2, (
        "Every task added here is a page a person waits longer on. Adding one "
        "is a product decision, not a config change."
    )
    for task in GENERATIVE_INTERACTIVE_TASKS:
        assert timeout_for(task) <= GENERATIVE_INTERACTIVE_ATTEMPT_CAP, task
        assert total_budget_for(task) <= GENERATIVE_INTERACTIVE_BUDGET_CAP, task


def test_the_two_interactive_tiers_do_not_overlap() -> None:
    """A task in both lists would be capped by whichever test ran first."""
    assert not set(IMMEDIATE_INTERACTIVE_TASKS) & set(GENERATIVE_INTERACTIVE_TASKS)


def test_every_task_has_a_total_budget_above_its_per_attempt_timeout() -> None:
    """A per-attempt timeout alone does not bound a request: four attempts at
    15s is a 60s wait. The total budget is what the caller actually feels."""
    from app.config.llm_providers import MODEL_FOR_TASK, timeout_for, total_budget_for

    for task in MODEL_FOR_TASK:
        assert total_budget_for(task) >= timeout_for(task), task
        # And it must not be so generous that it fails to bound anything.
        assert total_budget_for(task) <= 300.0, task


def test_immediate_interactive_budget_keeps_a_page_under_half_a_minute() -> None:
    from app.config.llm_providers import total_budget_for

    for task in IMMEDIATE_INTERACTIVE_TASKS:
        assert total_budget_for(task) <= 30.0, task


# ── Pagination ─────────────────────────────────────────────────────────────

def test_every_list_endpoint_is_bounded() -> None:
    """A list route with no `limit`, `page_size` or `skip` parameter returns
    the whole table, and the day that table is large is the day the page dies.
    Endpoints whose result set is fixed by the domain are exempt and named."""
    import inspect

    from app.api import admin, billing, candidates, emails, jobs, matching

    # Result sets bounded by the domain, not by pagination:
    #   compliance documents  exactly 7 slots, always all 7 (a short list is
    #                         the failure mode that section exists to prevent);
    #   permissions           the capability matrix, fixed size;
    #   approvals             at most 4 levels;
    #   staff / bd-users      one company's team, and the max-5 rule;
    #   email-templates       one row per template name.
    EXEMPT = {
        "compliance_document_slots",
        "customer_compliance_documents",
        "list_permissions",
        "update_permissions",
        "list_approvals",
        "list_staff",
        "list_bd_users",
        "list_email_templates",
        # A job is matched on at most MAXIMUM_CATEGORIES categories, refused at
        # the POST route rather than trimmed on read, so this list cannot grow.
        "list_matching_categories",
        "billing_config",
        # Returns fixed-size "recent" slices (25 ledger rows, 25 payments) as
        # part of one page payload. The FULL statement is GET /billing/ledger,
        # which is paginated and is checked by this test.
        "billing_overview",
    }
    unbounded: list[str] = []
    for module in (admin, billing, candidates, emails, jobs, matching):
        for name, fn in inspect.getmembers(module, inspect.iscoroutinefunction):
            if name.startswith("_") or name in EXEMPT:
                continue
            if not (name.startswith("list_") or name.endswith("_overview")
                    or name in {"matching_results", "billing_ledger"}):
                continue
            params = set(inspect.signature(fn).parameters)
            if not params & {"limit", "page_size", "skip", "page"}:
                unbounded.append(f"{module.__name__}.{name}")
    assert not unbounded, f"unbounded list endpoints: {unbounded}"
