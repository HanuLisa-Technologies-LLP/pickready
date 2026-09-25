"""Owner acceptance criterion 7: CTC never appears in any LLM prompt.

THREE PARTS, AND WHAT EACH ONE PROVES
-------------------------------------
1. THE INVENTORY. Every call into the model router in `app/` (a call to
   `chat_completion` or `invoke_llm`) is found by walking the AST, keyed by
   its file and enclosing function, and the set must EQUAL `PROMPT_BUILDERS`.
   A new builder fails here until somebody registers it and says how it keeps
   compensation out; a deleted one fails until its row goes. That is the
   point: a sweep over a hand-kept list passes forever once the list is stale.
2. THE CANARIES. For the builders registered as `CANARY`, the real builder
   runs with the model doubled at the router boundary, over inputs seeded
   with a sentinel in every place compensation lives: `jobs.compensation_json`,
   the application's `validation_json` (current and expected CTC), the parsed
   resume's `current_ctc`, a resume line, a job description line and a SWOT
   sentence. Neither the sentinel token nor the amount may appear in anything
   sent. A builder registered `PENDING` has the same canary as a STRICT xfail
   naming the hunk that fixes it: it fails loudly the day the fix lands until
   the mark is removed, so a pending entry cannot outlive its reason.
3. THE GUARD ITSELF. `compensation_guard` unit cases, in both directions: pay
   lines go, technical lines that merely look like pay stay.

WHAT `REVIEWED` MEANS, SAID PLAINLY
-----------------------------------
A `REVIEWED` builder has no canary: its inputs were read and carry no
compensation source (a candidate's answer to a question, a public web page, a
fixed fact block). That is a human claim recorded as data, not a proof. It is
still worth more than silence, because it is attached to one named call site
and the inventory makes a new call site impossible to add without making it.
Every other phase registers its own builders here when it changes them
(CONTRACT v2, C7).
"""
from __future__ import annotations

import ast
import json
import pathlib
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import compensation_guard, llm_router
from tests import skills_fixtures as fx

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
ROUTER_ENTRY_POINTS = frozenset({"chat_completion", "invoke_llm"})

CANARY = "canary"
PENDING = "pending"
REVIEWED = "reviewed"
EXCEPTION = "owner_exception"


@dataclass(frozen=True)
class Builder:
    owner: str
    reads: str
    guard: str
    note: str = ""


#: Every call site into the model router, and how it keeps compensation out.
PROMPT_BUILDERS: dict[str, Builder] = {
    # ── Phase 2: Yukti, and what Phase 2 registers for others (C7) ──────────
    "app/services/yukti/judge.py::judge_batch": Builder(
        "Phase 2 WP-A", "saved skills, saved SWOT needs, JD, anonymised resumes", CANARY
    ),
    "app/services/resume_parsing.py::extract_structured_fields": Builder(
        "Phase 2 WP-B",
        "the resume text, through compensation_guard.redact_text",
        CANARY,
    ),
    "app/services/swot_analysis.py::draft": Builder(
        "Phase 1 (orchestrator hunk, applied at the stage 2 integration)",
        "job fields and the JD markdown, redacted",
        CANARY,
        "build_context strips compensation keys and redacts jd_document, benefits, about_company, work_life",
    ),
    "app/services/jd_generation.py::generate_job_description": Builder(
        "Phase 1",
        "a staff brief through an allowlist (no compensation key)",
        CANARY,
        "the free-text `requirements` key is typed by staff; the only caller is a backfill script",
    ),
    "app/services/jd_generation.py::generate_jd_document": Builder(
        "Phase 1", "a staff brief through an allowlist (no compensation key)", CANARY
    ),
    # ── Phase 1: Sutra ──────────────────────────────────────────────────────
    "app/services/hiring/sutra.py::draft_skills._execute": Builder(
        "Phase 1 (orchestrator hunk, applied at the stage 2 integration)",
        "JD markdown, SWOT and company profile, each redacted",
        CANARY,
        "sutra._payload redacts job_description, the SWOT sections and the company profile sections",
    ),
    "app/services/hiring/sutra.py::build_context._execute": Builder(
        "Phase 1 (orchestrator hunk, applied at the stage 2 integration)",
        "the same `_payload` as the draft",
        CANARY,
        "the same redacted `_payload`",
    ),
    "app/services/hiring/drishti_conversation.py::_deepen._execute": Builder(
        "Phase 1", "the hiring team's own answer in the Drishti conversation", REVIEWED
    ),
    # ── Phase 3 / 4: the assessment ─────────────────────────────────────────
    "app/services/assessment_questions/generate.py::_write_prose.execute": Builder(
        "Phase 3 WP2 (redacted at the stage 2 integration)",
        "resume passages and parsed fields, redacted; the skills and role summary",
        CANARY,
        "_write_prose redacts the resume text and summary; _resume_excerpt strips compensation keys",
    ),
    "app/services/coding_assessment/review.py::review_code_quality.execute": Builder(
        "Phase 4 WP-4B2",
        "the coding question, the candidate's program and its test outcome words",
        REVIEWED,
    ),
    "app/services/assessment_formats/generation.py::anchor_evidence.execute": Builder(
        "Phase 3", "resume items to anchor an evidence question", PENDING,
        "Phase 3 redacts resume text with compensation_guard",
    ),
    "app/services/assessment_formats/generation.py::write_structured.execute": Builder(
        "Phase 3 / Phase 4", "JD markdown and skills for a structured question", PENDING,
        "Phase 3 redacts the JD with compensation_guard",
    ),
    "app/services/assessment_formats/coding_generation.py::write_coding_question.execute": Builder(
        "Phase 4 WP-4B1",
        "job title, grade, experience band, Sutra's role summary and one skill",
        CANARY,
        "build_messages is the one place the request is assembled; no compensation, JD or resume field",
    ),
    "app/services/ppi_interview.py::write_question.execute": Builder(
        "Phase 3", "JD markdown and resume for one question", PENDING,
        "Phase 3 retires or redacts this writer",
    ),
    "app/services/interviewer.py::_decide_assess": Builder(
        "Phase 3", "the question and the candidate's answer", REVIEWED
    ),
    "app/services/interviewer.py::challenge_non_answer": Builder(
        "Phase 3", "the question and the non-answer", REVIEWED
    ),
    "app/services/answer_classification.py::classify._execute": Builder(
        "Phase 3", "the question and the candidate's answer", REVIEWED
    ),
    "app/services/assessment_formats/evaluation.py::evaluate.execute": Builder(
        "Phase 3", "one question, its rubric and the answer", REVIEWED
    ),
    "app/services/assessment_formats/scoring.py::semantically_equivalent.execute": Builder(
        "Phase 3", "two short fill-in-the-blank strings", REVIEWED
    ),
    # ── Phase 5: grading and reporting ──────────────────────────────────────
    "app/services/miti/live.py::_invoke": Builder(
        "Phase 5", "evidence passages through the tool layer", PENDING,
        "Phase 5's tool layer adopts compensation_guard (tools/implementations keeps a third copy of the key markers today)",
    ),
    "app/services/rag/contextual.py::_generate_one": Builder(
        "Phase 5", "a document and one of its chunks (resumes and JDs are indexed)", PENDING,
        "Phase 5 redacts documents before indexing or before the prefix call",
    ),
    "app/services/functional_assessment.py::infer_grade": Builder(
        "Phase 5", "the job title for a grade, legacy rows only", REVIEWED
    ),
    "app/services/functional_assessment.py::_llm_score": Builder(
        "Phase 5", "a question, its rubric and the answer", REVIEWED
    ),
    "app/services/functional_assessment.py::bounded_remark.execute": Builder(
        "Phase 5", "grades and evidence already written for the report", REVIEWED
    ),
    "app/services/gap_analysis.py::_write_probes.execute": Builder(
        "Phase 5", "item remarks and the candidate's answers", REVIEWED
    ),
    "app/services/projects/ai_reasoning.py::interpret": Builder(
        "Phase 5", "the reduced project evidence pack", REVIEWED
    ),
    "app/api/candidates.py::rewrite_team_review": Builder(
        "Phase 5", "the reviewer's own typed remark", REVIEWED
    ),
    # ── Phase 6: communication and BGV ──────────────────────────────────────
    "app/services/lifecycle_email.py::draft._execute": Builder(
        "Phase 6", "candidate name, job title, company name", REVIEWED
    ),
    "app/services/outreach_content.py::generate_outreach_email": Builder(
        "Phase 6 / Phase 2 WP-C", "job facts and positive evidence words", REVIEWED
    ),
    "app/services/bgv_agent.py::_generate": Builder(
        "Phase 6", "a seven-field fact block with no pay field", REVIEWED
    ),
    "app/services/bgv.py::parse_reply": Builder(
        "Phase 6",
        "an employer's HR reply, and the prompt asks it to EXTRACT `compensation`",
        EXCEPTION,
        "OWNER QUESTION: verifying last-drawn pay with a former employer is a BGV "
        "requirement, and it is the one place a prompt carries pay by design",
    ),
    # ── Phase 7 / platform: public web research ─────────────────────────────
    "app/services/company_research.py::research_company._execute": Builder(
        "Phase 7", "public web pages about the company", REVIEWED
    ),
    "app/services/web_research.py::_evaluate_node._judge": Builder(
        "Phase 7", "public job pages for business development", REVIEWED
    ),
}


# ── 1. The inventory ─────────────────────────────────────────────────────────


def _call_sites() -> set[str]:
    sites: set[str] = set()
    router_module = APP / "services" / "llm_router.py"
    for path in sorted(APP.rglob("*.py")):
        if path == router_module:
            continue
        rel = path.relative_to(APP.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def _scoped(self, node: ast.AST, name: str) -> None:
                stack.append(name)
                self.generic_visit(node)
                stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._scoped(node, node.name)

            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name)
                    else None
                )
                if name in ROUTER_ENTRY_POINTS:
                    sites.add(f"{rel}::{'.'.join(stack) or '<module>'}")
                self.generic_visit(node)

        Visitor().visit(tree)
    return sites


def test_every_prompt_builder_is_registered_and_no_registration_is_stale() -> None:
    found = _call_sites()
    assert found, "the AST walk found no router call at all, so it is looking in the wrong place"
    unregistered = sorted(found - set(PROMPT_BUILDERS))
    stale = sorted(set(PROMPT_BUILDERS) - found)
    assert not unregistered, (
        "these call the model router and are not in PROMPT_BUILDERS; register "
        f"each with how it keeps compensation out of the prompt: {unregistered}"
    )
    assert not stale, f"these registrations name a call site that no longer exists: {stale}"


def test_every_registration_says_how_and_who() -> None:
    for site, builder in PROMPT_BUILDERS.items():
        assert builder.guard in {CANARY, PENDING, REVIEWED, EXCEPTION}, site
        assert builder.owner and builder.reads, site
        if builder.guard in {PENDING, EXCEPTION}:
            assert builder.note, f"{site} must say what closes it"


def test_the_owner_exception_is_exactly_one_call_site() -> None:
    """An exception is a ruling, not a category to grow. A second one needs its
    own owner decision and a change to this assertion."""
    assert [s for s, b in PROMPT_BUILDERS.items() if b.guard == EXCEPTION] == [
        "app/services/bgv.py::parse_reply"
    ]


# ── 2. The canaries ──────────────────────────────────────────────────────────

SENTINEL = "CTC_SENTINEL_7319"
AMOUNT = "73,19,000"
NEEDLES = (SENTINEL, AMOUNT, "7319000", "7319")

RESUME_WITH_PAY = "\n".join(
    [
        "Senior data engineer",
        "Owned the Kafka ingestion pipeline serving the fraud team in production",
        f"Current CTC: {AMOUNT} per annum ({SENTINEL})",
        "Tuned slow SQL queries with query plans",
        f"Expected salary {SENTINEL}",
    ]
)
JD_WITH_PAY = fx.JD_MARKDOWN + f"\n## Compensation\nCTC {AMOUNT} ({SENTINEL}) per annum, negotiable."
COMPENSATION = {"ctc_min": 7319000, "ctc_max": 7319000, "note": SENTINEL, "currency": "INR"}
VALIDATION = {
    "current_ctc": f"{AMOUNT} {SENTINEL}",
    "expected_ctc": f"{AMOUNT} {SENTINEL}",
    "notice_period": "30 days",
    "document_readiness": "All documents ready",
}


class CapturingRouter:
    """`llm_router.chat_completion` (and `invoke_llm`), answered by a callable,
    recording every message list it was sent."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.sent: list[list[dict]] = []

    async def __call__(self, task_type, messages, *args, **kwargs):
        self.sent.append([dict(m) for m in messages])
        return self.answer(task_type, messages)

    def text(self) -> str:
        return json.dumps(self.sent, ensure_ascii=False)


def _assert_no_pay(router: CapturingRouter) -> None:
    assert router.sent, "the builder never reached the router, so the canary proved nothing"
    sent = router.text()
    leaked = [needle for needle in NEEDLES if needle in sent]
    assert not leaked, f"compensation reached a prompt: {leaked}"


def _install(monkeypatch, router: CapturingRouter) -> None:
    monkeypatch.setattr(llm_router, "chat_completion", router)
    monkeypatch.setattr(llm_router, "invoke_llm", router)


def _yukti_answer(task_type: str, messages: list[dict]) -> str:
    payload = json.loads(messages[1]["content"])
    return json.dumps(
        {
            "results": [
                {
                    "candidate": candidate["ref"],
                    "skills": [
                        {"skill": s["ref"], "verdict": "none", "quote": ""}
                        for s in payload["skills"]
                    ],
                    "experience_level": {"verdict": "none", "quote": "", "tag": ""},
                    "role_fit": {"verdict": "none", "quote": "", "tag": ""},
                    "company_needs": [
                        {"need": n["ref"], "verdict": "none", "quote": "", "tag": ""}
                        for n in payload["needs"]
                    ],
                }
                for candidate in payload["candidates"]
            ]
        }
    )


async def _seed_yukti_world() -> tuple[fx.World, uuid.UUID]:
    w = await fx.seed(
        skills=[
            ("must_have", "Kafka stream processing", True, "sutra", None, "Ran Kafka in production."),
            ("nice_to_have", "Airflow orchestration", True, "sutra", None, None),
            ("behavioural", "Incident ownership", True, "sutra", None, None),
        ],
        saved=True,
        compensation=COMPENSATION,
    )
    link = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                candidate, profile = uuid.uuid4(), uuid.uuid4()
                await session.execute(
                    text(
                        "UPDATE jobs SET jd_markdown = :md WHERE id = :j"
                    ),
                    {"md": JD_WITH_PAY, "j": w.job},
                )
                await session.execute(
                    text(
                        "UPDATE job_swot_analyses SET weaknesses = :we WHERE job_id = :j"
                    ),
                    {
                        "we": fx.SWOT["weaknesses"]
                        + f" The salary budget is capped at {AMOUNT} ({SENTINEL}).",
                        "j": w.job,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
                        "VALUES (:c, :t, 'Canary Candidate', :e, false)"
                    ),
                    {"c": candidate, "t": w.tenant, "e": f"{candidate}@canary.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text, "
                        "parsed_fields_json) VALUES (:p, :c, :t, :r, CAST(:pf AS jsonb))"
                    ),
                    {
                        "p": profile, "c": candidate, "t": w.tenant, "r": RESUME_WITH_PAY,
                        "pf": json.dumps({"current_ctc": SENTINEL, "salary": AMOUNT, "skills": ["Kafka"]}),
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                        "profile_id, source, validation_json) VALUES (:l, :t, :j, :c, :p, "
                        "'fresh', CAST(:v AS jsonb))"
                    ),
                    {
                        "l": link, "t": w.tenant, "j": w.job, "c": candidate, "p": profile,
                        "v": json.dumps(VALIDATION),
                    },
                )
    return w, link


async def test_the_yukti_prompt_carries_no_compensation(monkeypatch) -> None:
    """The real path: `score_links` builds the job context from the contract,
    the saved SWOT and the JD, anonymises and redacts the resume, and calls the
    router. Every compensation source is seeded with the sentinel."""
    from app.models import JobCandidateLink, Profile
    from app.models.job import Job
    from app.services.yukti import scoring

    router = CapturingRouter(_yukti_answer)
    _install(monkeypatch, router)
    w, link_id = await _seed_yukti_world()
    try:
        async with fx.sessions()() as session:
            await session.begin()
            async with superadmin_scope(session):
                job = await session.get(Job, w.job)
                link = await session.get(JobCandidateLink, link_id)
                profile = await session.get(Profile, link.profile_id)
                summary = await scoring.score_links(session, job, [(link, profile)])
            await session.rollback()
    finally:
        await fx.drop(w)

    _assert_no_pay(router)
    sent = router.text()
    # The canary must have carried the real content, or it proves nothing.
    assert "Owned the Kafka ingestion pipeline" in sent
    assert "Kafka stream processing" in sent
    assert "Nobody on the team has run streaming pipelines in production." in sent
    assert "Incident ownership" not in sent, "a behavioural skill is never judged from a resume"
    assert "Canary Candidate" not in sent
    outcome = summary.outcomes[link_id]
    assert outcome.provenance["resume_compensation_redacted"] is True
    # The CTC comparison still happened, deterministically, outside the prompt.
    assert outcome.provenance["validation_parts"]["ctc"] == "Within range"


async def test_the_jd_document_prompt_carries_no_compensation(monkeypatch) -> None:
    from app.services import jd_generation

    router = CapturingRouter(lambda task, messages: "## Description\nA role.")
    _install(monkeypatch, router)
    await jd_generation.generate_jd_document(
        {
            "title": "Senior Data Engineer",
            "skills": ["Kafka", "SQL"],
            "experience_min_years": 5,
            "experience_max_years": 9,
            "grade": "managerial",
            "compensation": SENTINEL,
            "ctc_min": 7319000,
            "salary_note": AMOUNT,
        }
    )
    _assert_no_pay(router)


async def test_the_jd_description_prompt_carries_no_compensation_keys(monkeypatch) -> None:
    from app.services import jd_generation

    router = CapturingRouter(lambda task, messages: "{}")
    _install(monkeypatch, router)
    await jd_generation.generate_job_description(
        {"title": "Senior Data Engineer", "skills": ["Kafka"], "ctc": SENTINEL, "compensation": AMOUNT}
    )
    _assert_no_pay(router)


async def test_resume_extraction_carries_no_compensation(monkeypatch) -> None:
    from app.services import resume_parsing

    router = CapturingRouter(lambda task, messages: json.dumps({"skills": []}))
    _install(monkeypatch, router)
    await resume_parsing.extract_structured_fields(RESUME_WITH_PAY)
    _assert_no_pay(router)


def _job_namespace() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Senior Data Engineer",
        department="Data",
        assessment_grade="managerial",
        role_classification=None,
        experience_min_years=5,
        experience_max_years=9,
        jd_json={"skills": ["Kafka"], "ctc": SENTINEL},
        jd_markdown=JD_WITH_PAY,
        compensation_json=COMPENSATION,
        about_company="We run payment rails.",
        work_life="Hybrid.",
        benefits="Health cover.",
    )


async def test_the_swot_prompt_carries_no_compensation(monkeypatch) -> None:
    from app.services import swot_analysis

    router = CapturingRouter(
        lambda task, messages: json.dumps(
            {"strengths": "a", "weaknesses": "b", "opportunities": "c", "threats": "d"}
        )
    )
    _install(monkeypatch, router)
    await swot_analysis.draft(None, _job_namespace())
    _assert_no_pay(router)


def test_the_sutra_payload_carries_no_compensation() -> None:
    from app.services.hiring import sutra

    payload = sutra._payload(_job_namespace(), dict(fx.SWOT), [])
    router = CapturingRouter(lambda task, messages: "{}")
    router.sent.append([{"role": "user", "content": json.dumps(payload)}])
    _assert_no_pay(router)


def test_the_coding_question_request_carries_no_compensation() -> None:
    """`write_coding_question.execute` sends exactly `build_messages`, which
    is pure, so the canary runs the builder itself over a job whose
    compensation and JD carry the sentinel. The role summary is Sutra's
    output, written from the redacted `sutra._payload` canaried above."""
    from app.services.assessment_contract import ContractSkill
    from app.services.assessment_formats import coding_generation as gen
    from app.services.code_execution import limits

    languages = ["python"]
    messages = gen.build_messages(
        job=_job_namespace(),
        skill=ContractSkill(
            id=uuid.uuid4(),
            name="Log analysis and incident triage",
            bucket="must_have",
            priority=1,
            evidence_line="Has traced a production incident from logs to its root cause.",
        ),
        role_summary="Keeps the payments platform running and leads its incident response.",
        grade="managerial",
        languages=languages,
        reference_language="python",
        limits={key: limits.for_language(key) for key in languages},
    )
    router = CapturingRouter(lambda task, messages: "{}")
    router.sent.append(messages)
    _assert_no_pay(router)


async def test_the_prose_question_request_carries_no_compensation(monkeypatch) -> None:
    """`_write_prose.execute` sends the request `_write_prose` assembles; the
    canary hands it a resume and a summary carrying the sentinel, as a caller
    that forgot to redact would."""
    from app.services.assessment_questions import generate

    competency = uuid.uuid4()
    router = CapturingRouter(lambda task, messages: "{}")
    _install(monkeypatch, router)
    await generate._write_prose(
        None,
        job=SimpleNamespace(title="Senior Data Engineer"),
        contract=SimpleNamespace(grade="managerial", role_summary="Runs the data platform."),
        slots=[
            SimpleNamespace(
                index=0, category="must_have", skill_name="Kafka", competency_id=competency
            )
        ],
        skills={competency: SimpleNamespace(evidence_line="Has run Kafka in production.")},
        resume_excerpt=RESUME_WITH_PAY,
        resume_text=RESUME_WITH_PAY,
        project_evidence="",
    )
    _assert_no_pay(router)


# ── 3. The guard ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "Current CTC: 18 LPA",
        "Expected CTC 24,00,000",
        f"CTC {AMOUNT}",
        "Salary expectation: negotiable",
        "Last drawn salary Rs. 12,00,000",
        "Remuneration as per industry standards",
        "Compensation: INR 18L fixed plus variable",
        "Take-home pay of 1.2 lakh per month",
        "Package: 12 lakhs per annum",
        "Stipend of Rs 20000 during internship",
        "\u20b9 45,000 monthly",
        "Expected: 73,19,000",
        "18.5 LPA fixed",
        "Gross pay slips available on request",
    ],
)
def test_a_pay_line_is_removed(line: str) -> None:
    kept = compensation_guard.redact_text(f"Owned the Kafka pipeline\n{line}\nTuned SQL")
    assert kept == "Owned the Kafka pipeline\nTuned SQL"
    assert compensation_guard.redact(line).removed


@pytest.mark.parametrize(
    "line",
    [
        "Published an npm package used by four teams",
        "Built the pay-as-you-go billing service",
        "Integrated PayPal and Stripe checkout",
        "Improved gross margin reporting for finance",
        "Processed 5 lakh transactions per day",
        "Reduced p99 latency from 900ms to 120ms",
        "Handled 2,000 requests per second",
        "Worked hand in hand with product",
        "Completed a take-home coding exercise",
    ],
)
def test_a_technical_line_that_looks_like_pay_is_kept(line: str) -> None:
    assert compensation_guard.redact_text(line) == line
    assert not compensation_guard.redact(line).removed


def test_a_long_single_line_resume_loses_only_the_pay_sentence() -> None:
    blob = (
        "Owned the Kafka ingestion pipeline for the fraud team. " * 5
        + "Current CTC is 18 LPA. "
        + "Tuned slow SQL queries with query plans. " * 5
    )
    kept = compensation_guard.redact_text(blob)
    assert "Kafka ingestion pipeline" in kept
    assert "Tuned slow SQL queries" in kept
    assert "CTC" not in kept and "18 LPA" not in kept


def test_empty_input_redacts_to_empty() -> None:
    assert compensation_guard.redact(None) == compensation_guard.Redaction("", False)
    assert compensation_guard.redact_text("") == ""


def test_strip_keys_drops_every_pay_shaped_key_at_any_depth() -> None:
    value = {
        "title": "Engineer",
        "ctc_min": 1,
        "Expected_Salary": 2,
        "compensation_json": {"x": 1},
        "history": [{"company": "A", "gross_pay": 3, "lpa": 4}],
        "nested": ({"stipend": 5, "keep": 6},),
    }
    assert compensation_guard.strip_keys(value) == {
        "title": "Engineer",
        "history": [{"company": "A"}],
        "nested": ({"keep": 6},),
    }


def test_mentions_compensation() -> None:
    assert compensation_guard.mentions_compensation("Current CTC 12 LPA")
    assert not compensation_guard.mentions_compensation("Kafka and SQL")
    assert not compensation_guard.mentions_compensation(None)
