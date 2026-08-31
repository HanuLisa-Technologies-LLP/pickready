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
    """The ReadyPick Functional Index is proprietary work derived from first
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
    """Firebase owns authentication. The MSG91 SMS send-path is retained as a
    feature (claude.md rule 2) but must not appear as a login step in any UI."""
    pattern = re.compile(r"\botp\b|one[- ]time password|verification code", re.IGNORECASE)
    offenders: list[str] = []
    for path in _frontend_sources():
        # The legacy input component is retained but must stay unreferenced;
        # that is asserted separately below.
        if path.name == "otp-input.tsx":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(FRONTEND)}:{n}")
    assert not offenders, f"OTP copy present in: {offenders}"


def test_the_legacy_otp_input_is_not_wired_into_any_page() -> None:
    """Retained, not reachable. A component nobody imports cannot put an OTP
    step back into a portal by accident."""
    importers = [
        str(path.relative_to(FRONTEND))
        for path in _frontend_sources()
        if path.name != "otp-input.tsx"
        and "otp-input" in path.read_text(encoding="utf-8")
    ]
    assert not importers, f"otp-input is imported by: {importers}"


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
    "swot_intake",
    "company_dna_intake",
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
GENERATIVE_INTERACTIVE_TASKS = ("jd_generation",)

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
