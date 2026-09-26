"""The golden end-to-end journey (CONTRACT v4 item 1), driven once, used twice.

ONE DRIVER, TWO JUDGES
-----------------------
`tests/test_golden_journey.py` and the harness scenario
`integration_golden_journey.yaml` drive THIS sequence. Two copies of a thirty
step journey would disagree within a release, and the disagreement would
surface as one of them passing a path the other no longer takes. What differs
is who is calling and who judges:

* the pytest module calls as REAL sessions (the production cookie minting path,
  no dependency override) and judges each gate from a SECOND CONNECTION the
  moment the gate is reached;
* the harness calls through its `Application` (the principal injected, as every
  harness step does) and judges the end state through its probes.

WHAT IS FAKE, AND ONLY THAT
-----------------------------
The model, at the router (`harness.doubles.golden_model`), the code execution
provider, at its provider seam (`code_execution.override_provider` with the
`FakeProvider` double), and speech to text, at the transcription service seam.
Everything else is the product: the routes, `require_capability`, RLS, the
dispatcher (on its `record` backend), the task bodies (run through
`runtime.run_task`, the one path to a task body), Miti, Siddhi, Yukti and the
proctoring report.

A DISPATCHED TASK IS RUN BY NAME, NEVER BY DRAINING
-----------------------------------------------------
`_run_dispatched(name)` runs every recorded dispatch of exactly that name that
has not yet run, and refuses when there is none. Draining the whole record
would run the mail worker against a real SMTP host and would hide a missing
dispatch behind whatever else happened to be queued. Each gate names the work
it expects to have been asked for.
"""
from __future__ import annotations

import io
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Protocol

from app.workers import dispatch as dispatch_mod

__all__ = [
    "GATES",
    "Client",
    "JourneyError",
    "JourneyState",
    "drive",
]

V1 = "/api/v1"
V2 = "/api/v2"
ASSESS = f"{V2}/assessments"

JD_MARKDOWN = (
    "## About the role\n"
    "Own the settlement platform end to end: reconciliation, ledgers and the "
    "services that move money between twelve partner banks.\n\n"
    "## Responsibilities\n"
    "- Design and run the reconciliation services in Python.\n"
    "- Tune PostgreSQL for the settlement ledger.\n\n"
    "## Required skills\n"
    "- Python\n- PostgreSQL\n- Payments reconciliation\n"
)

SWOT = {
    "strengths": "The team ships settlement changes weekly with no data loss.",
    "weaknesses": (
        "Nobody on the team has reconciled a multi-bank settlement break alone. "
        "Incident reviews stall for want of an owner."
    ),
    "opportunities": "Two new partner banks go live next quarter.",
    "threats": "A regulator audit lands in six months.",
}

#: The ordered gates. The pytest module judges state at every one of these,
#: the harness records each as a trajectory stage.
GATES = (
    "job_created",
    "jd_saved",
    "swot_saved",
    "skills_drafted",
    "skills_saved",
    "job_published",
    "applied",
    "matched",
    "invited",
    "questions_written",
)


RESUME_LINES = (
    "Meera Iyer",
    "Settlement engineer with six years on multi-bank payment switches.",
    "Built the Python reconciliation service that matches settlement files "
    "from twelve partner banks against the internal ledger.",
    "Tuned the PostgreSQL settlement ledger: partitioned the entries table and "
    "added covering indexes for the nightly reconciliation queries.",
    "Owned the rollback when a malformed settlement file broke reconciliation "
    "in production, and wrote the incident review.",
)
RESUME_FILENAME = "meera-iyer-resume.docx"


def resume_docx() -> bytes:
    """A real DOCX, so the upload validator and the text extractor both run
    on the bytes a candidate's browser would send."""
    import docx  # noqa: PLC0415

    document = docx.Document()
    for line in RESUME_LINES:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@contextmanager
def object_store() -> Iterator[Any]:
    """The harness's in-memory S3 at `object_storage._client`, with a bucket
    configured, restored exactly on the way out. Object storage is
    infrastructure the CI job does not run, like the database it does."""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.services import object_storage  # noqa: PLC0415
    from harness.doubles.storage import InMemoryObjectStore  # noqa: PLC0415

    settings = get_settings()
    store = InMemoryObjectStore()
    prior_client = object_storage._client  # noqa: SLF001
    prior_bucket = settings.s3_bucket
    object_storage._client = store  # noqa: SLF001
    settings.s3_bucket = "golden-journey-private"
    try:
        yield store
    finally:
        object_storage._client = prior_client  # noqa: SLF001
        settings.s3_bucket = prior_bucket


#: The six mandatory application fields, answered in full.
VALIDATION = {
    "current_ctc": "18,00,000",
    "expected_ctc": "24,00,000",
    "notice_period": "30 days",
    "joining_date": "2026-11-03",
    "document_readiness": "All documents ready",
    "role_interest": (
        "The role pairs settlement engineering with direct ownership of the "
        "reconciliation platform, which is the work I want to do next."
    ),
}


class JourneyError(AssertionError):
    """A step answered something the journey cannot continue past."""


class Client(Protocol):
    def as_staff(self) -> None: ...

    def as_candidate(self) -> None: ...

    def call(self, step: str, method: str, path: str, **kwargs: Any) -> tuple[int, Any]: ...


@dataclass
class JourneyState:
    """The ids the journey created, and what each response said."""

    tenant: uuid.UUID
    staff: uuid.UUID
    candidate: uuid.UUID
    job: uuid.UUID | None = None
    link: uuid.UUID | None = None
    conversation: uuid.UUID | None = None
    responses: dict[str, Any] = field(default_factory=dict)
    ran: set[str] = field(default_factory=set)
    tasks_run: list[str] = field(default_factory=list)


Gate = Callable[[str, JourneyState], None]


def _expect(step: str, status: int, body: Any, *allowed: int) -> Any:
    if status not in allowed:
        raise JourneyError(
            f"{step} answered {status}, expected {allowed}: "
            f"{json.dumps(body, default=str)[:800]}"
        )
    return body


def _run_dispatched(state: JourneyState, name: str, *, at_least: int = 1) -> int:
    """Run every recorded, not yet run dispatch of `name` through the one path
    to a task body, and refuse when fewer than `at_least` were asked for."""
    from app.workers import runtime  # noqa: PLC0415
    from app.workers.registry import resolve  # noqa: PLC0415

    pending = [
        item
        for item in dispatch_mod.recorded()
        if item.name == name and item.id not in state.ran
    ]
    if len(pending) < at_least:
        raise JourneyError(
            f"expected {at_least} dispatch of {name}, found {len(pending)}; "
            f"recorded: {dispatch_mod.recorded_names()}"
        )
    spec = resolve(name)
    for item in pending:
        state.ran.add(item.id)
        runtime.run_task(dispatch_mod.payload_for(item.id, spec, item.args, item.kwargs))
        state.tasks_run.append(name)
    return len(pending)


def drive(client: Client, state: JourneyState, gate: Gate) -> JourneyState:
    """The journey, from an empty funded tenant to the recruiter reading words."""
    # ── Job setup ─────────────────────────────────────────────────────────
    client.as_staff()
    status, body = client.call(
        "create_job",
        "POST",
        f"{V1}/jobs",
        json={
            "title": "Settlement Platform Engineer",
            "grade": "non_managerial",
            "jd_markdown": JD_MARKDOWN,
            "experience_min_years": 4,
            "experience_max_years": 8,
        },
    )
    _expect("create_job", status, body, 201)
    state.job = uuid.UUID(str(body["id"]))
    gate("job_created", state)

    status, body = client.call(
        "save_jd", "PATCH", f"{V1}/jobs/{state.job}/jd", json={"jd_markdown": JD_MARKDOWN}
    )
    _expect("save_jd", status, body, 200)
    gate("jd_saved", state)

    status, body = client.call("read_swot", "GET", f"{ASSESS}/jobs/{state.job}/swot-analysis")
    _expect("read_swot", status, body, 200)
    status, body = client.call(
        "save_swot",
        "PUT",
        f"{ASSESS}/jobs/{state.job}/swot-analysis",
        json={**SWOT, "expected_version": int(body.get("version") or 0)},
    )
    _expect("save_swot", status, body, 200)
    gate("swot_saved", state)

    _run_dispatched(state, "pickready.draft_job_skills")
    status, body = client.call("read_skills", "GET", f"{ASSESS}/jobs/{state.job}/skills")
    _expect("read_skills", status, body, 200)
    if body.get("draft_status") != "drafted":
        raise JourneyError(f"the skills draft did not land: {body}")
    state.responses["skills_drafted"] = body
    gate("skills_drafted", state)

    status, body = client.call("save_skills", "POST", f"{ASSESS}/jobs/{state.job}/skills/save")
    _expect("save_skills", status, body, 200)
    state.responses["skills_saved"] = body
    gate("skills_saved", state)

    status, body = client.call("publish", "POST", f"{V1}/jobs/{state.job}/publish")
    _expect("publish", status, body, 200)
    state.responses["published"] = body
    gate("job_published", state)

    # ── The candidate uploads a resume and applies ────────────────────────
    client.as_candidate()
    status, body = client.call(
        "upload_resume",
        "PUT",
        f"{V1}/portal/me/resume",
        files={
            "resume": (
                RESUME_FILENAME,
                resume_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    _expect("upload_resume", status, body, 200)
    _run_dispatched(state, "pickready.parse_resume")
    status, body = client.call(
        "apply",
        "POST",
        f"{V1}/portal/jobs/{state.job}/apply",
        data={
            "reuse_previous": "true",
            "validation": json.dumps(VALIDATION),
            "application_source": "direct",
        },
    )
    _expect("apply", status, body, 201)
    state.link = uuid.UUID(str(body["link_id"]))
    # The application's own profile is a snapshot of the main resume, parsed
    # on its own: an application is an immutable copy of what was sent.
    _run_dispatched(state, "pickready.parse_resume")
    gate("applied", state)

    # ── AI Matching (Yukti, the resume stage) ─────────────────────────────
    client.as_staff()
    status, body = client.call("run_matching", "POST", f"{V1}/matching/jobs/{state.job}/run")
    _expect("run_matching", status, body, 202)
    # Publish asked for a run too; both read the same saved skills.
    _run_dispatched(state, "pickready.run_matching", at_least=2)
    status, body = client.call("ranked_before", "GET", f"{V1}/jobs/{state.job}/candidates")
    _expect("ranked_before", status, body, 200)
    state.responses["ranked_before"] = body
    gate("matched", state)

    # ── The invitation: one credit question for the batch ────────────────
    status, body = client.call(
        "invite",
        "POST",
        f"{V1}/pipeline/jobs/{state.job}/select-candidates",
        json={"link_ids": [str(state.link)]},
    )
    _expect("invite", status, body, 202)
    if body.get("invited") != 1:
        raise JourneyError(f"the invitation invited nobody: {body}")
    gate("invited", state)
    _run_dispatched(state, "pickready.generate_candidate_questions")
    gate("questions_written", state)
    return state
