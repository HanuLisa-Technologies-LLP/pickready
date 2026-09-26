"""The golden end-to-end journey (CONTRACT v4 item 1), driven once, used twice.

ONE DRIVER, TWO JUDGES
-----------------------
`tests/test_golden_journey.py` and the harness scenario
`integration_golden_journey.yaml` drive THIS sequence. Two copies of a forty
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
The model, at the router (`harness.doubles.golden_model`); the code execution
provider, at its provider seam (`code_execution.override_provider` with the
product's own `FakeProvider`, scripted by lookup and never executing); and
speech to text, at `video.transcribe.run_transcription`, the one function that
calls Amazon Transcribe. Object storage is the harness's in-memory S3, because
it is infrastructure the CI job does not run, like the database it does.
Everything else is the product: the routes, `require_capability`, RLS, the
proctoring gate, the dispatcher (on its `record` backend), the task bodies
(run through `runtime.run_task`, the one path to a task body), Miti, Siddhi,
Yukti and the proctoring report.

THE DEPLOYMENT IT RUNS AS (`deployment`)
-----------------------------------------
Four settings are DEPLOYMENT DATA and are set for the duration of the journey,
then restored: a bucket and its KMS key (media writes refuse without one),
Transcribe enabled (voice is offered only where it can work), and the question
mix. The mix is the one deliberate departure from the defaults: at the default
70/20/10 every budget the skills store allows (8 to 15) carries exactly ONE
objective question, so a single assessment can never hold both a multiple
choice and a fill-in-the-blank item. `0.7 / 0.1 / 0.2` makes a budget of ten
seven prose, one coding and two objective questions (one of each objective
format for this grade), which is every format CONTRACT v4 item 1 names, in one
candidate's assessment, through the production `budget.mix` arithmetic.

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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol

from app.workers import dispatch as dispatch_mod

__all__ = [
    "GATES",
    "Client",
    "Deployment",
    "JourneyError",
    "JourneyState",
    "deployment",
    "digest_lines",
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
    "proctoring_opened",
    "started",
    "voice_transcribed",
    "coding_run",
    "completed",
    "coding_executed",
    "graded",
    "reported",
    "proctoring_reported",
    "reranked",
    "profile_read",
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
    "Introduced idempotency keys on every inbound settlement file so a "
    "redelivered file can never post twice.",
    "Led the migration of the reconciliation jobs from cron scripts to a "
    "queue based worker with retries and alerting.",
    "Mentored two engineers through their first on-call rotation on the "
    "payments platform.",
)
RESUME_FILENAME = "meera-iyer-resume.docx"

#: The rival applicant's resume. It opens with `golden_model.RIVAL_MARKER`,
#: which is what the scripted Yukti reading keys on.
RIVAL_RESUME_LINES = (
    "Arjun Rao",
    "Payments operations analyst with five years at a card issuer.",
    "Ran the daily reconciliation reports in Python and escalated breaks to the "
    "settlement team.",
    "Maintained the PostgreSQL queries behind the finance dashboards.",
)
RIVAL_RESUME_FILENAME = "arjun-rao-resume.docx"

#: What the candidate says aloud on the spoken turn, as Transcribe returns it.
SPOKEN_ANSWER = (
    "When the partner bank sent the settlement file twice I stopped the posting "
    "job, compared the file identifiers against the ledger, reversed the "
    "duplicated entries inside one transaction and then added an idempotency "
    "check so the same file could never post again."
)

#: Typed prose answers, cycled per prose turn: substantive, specific, first
#: person, and free of digits so no sentence reads as a number about anybody.
TYPED_ANSWERS = (
    "I owned the reconciliation service end to end. When a partner bank changed "
    "its file layout without notice I wrote a parser that validated every row "
    "against the ledger before posting, rolled it out behind a flag, and the "
    "break rate fell to nothing within the week.",
    "The hardest call was choosing to partition the settlement entries table by "
    "value date rather than by bank. It made the nightly reconciliation queries "
    "cheap, and I proved it by replaying a month of production traffic against "
    "a copy before we switched.",
    "A rollback I led went wrong because a downstream report still read the old "
    "column. I restored it from the audit trail, wrote the incident review, and "
    "since then every schema change ships with a reader inventory.",
    "I explain it to new engineers as three rules: never post a file twice, "
    "never trust a total without the entries, and always leave a trail another "
    "person can follow at three in the morning.",
    "Under a regulator deadline I split the reconciliation backlog by bank, "
    "fixed the two banks with the most breaks first, and kept the finance team "
    "updated twice a day until the backlog was clear.",
    "If I rebuilt it I would make every posting idempotent from day one and "
    "keep reconciliation state in the database rather than in files, because "
    "that is where every outage I handled started.",
    "A colleague wanted to retry failed postings automatically. I showed that a "
    "retry without an idempotency key would double post, we agreed on keys "
    "first, and then turned retries on.",
    "Before production I run the reconciliation against a replay of the "
    "previous day, compare every matched pair with the ledger, and only promote "
    "the change when the two agree exactly.",
)

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

#: A system check every browser passed, and a face descriptor: a vector, never
#: an image, at the width the database CHECK accepts.
PROCTORING_SESSION = {
    "consent": True,
    "device_context": {
        "user_agent": "Mozilla/5.0 golden-journey",
        "platform": "Win32",
        "screen_count": 1,
        "camera_width": 1280,
        "camera_height": 720,
    },
    "system_check": {
        "camera": True,
        "microphone": True,
        "browser_supported": True,
        "fullscreen_supported": True,
        "face_detected": True,
        "inference_adequate": True,
        "measured_fps": 24.0,
    },
    "face_descriptor": [round(0.01 * (index % 50), 2) for index in range(128)],
}

#: One logged-only proctoring event: the candidate tried to paste once. It
#: warns nobody and must still reach the report, in words.
PROCTORING_EVENT = "BLOCKED_ACTION_ATTEMPTED"

#: A ceiling on turns, never the loop condition: the SERVER's status ends the
#: loop, and a follow-up or re-ask legitimately adds turns.
MAX_TURNS = 40

#: The question mix set by `deployment`, and why, in the module docstring.
QUESTION_SHARES = {"prose": 0.7, "coding": 0.1, "objective": 0.2}


def resume_docx(lines: tuple[str, ...] = RESUME_LINES) -> bytes:
    """A real DOCX, so the upload validator and the text extractor both run
    on the bytes a candidate's browser would send."""
    import docx  # noqa: PLC0415

    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@dataclass
class Deployment:
    """The doubles installed for one journey, so a judge can read them."""

    store: Any
    sandbox: Any
    transcriptions: list[str] = field(default_factory=list)


@contextmanager
def _settings(**values: Any) -> Iterator[None]:
    from app.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    prior = {name: getattr(settings, name) for name in values}
    for name, value in values.items():
        setattr(settings, name, value)
    try:
        yield
    finally:
        for name, value in prior.items():
            setattr(settings, name, value)


@contextmanager
def _swap(module: Any, name: str, value: Any) -> Iterator[None]:
    prior = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, prior)


@contextmanager
def deployment() -> Iterator[Deployment]:
    """Install the journey's doubles and deployment data, and restore every
    one of them on the way out, whatever happened inside."""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.services import code_execution, object_storage  # noqa: PLC0415
    from app.services.code_execution.fake import FakeProvider  # noqa: PLC0415
    from app.services.video import transcribe  # noqa: PLC0415
    from harness.doubles.golden_model import script_sandbox  # noqa: PLC0415
    from harness.doubles.storage import InMemoryObjectStore  # noqa: PLC0415

    store = InMemoryObjectStore()
    sandbox = FakeProvider()
    installed = Deployment(store=store, sandbox=sandbox)

    def _transcribe(**kwargs: Any) -> dict[str, Any]:
        installed.transcriptions.append(str(kwargs.get("audio_key") or ""))
        return {"results": {"transcripts": [{"transcript": SPOKEN_ANSWER}]}}

    with ExitStack() as stack:
        stack.enter_context(
            _settings(
                s3_bucket="golden-journey-private",
                s3_kms_key_id="golden-journey-key",
                transcribe_enabled=True,
                assessment_share_prose=QUESTION_SHARES["prose"],
                assessment_share_coding=QUESTION_SHARES["coding"],
                assessment_share_objective=QUESTION_SHARES["objective"],
            )
        )
        stack.enter_context(_swap(object_storage, "_client", store))
        stack.enter_context(_swap(transcribe, "run_transcription", _transcribe))
        script_sandbox(sandbox, get_settings().code_execution_language_list)
        stack.enter_context(code_execution.override_provider(sandbox))
        yield installed


@contextmanager
def digest_lines() -> Iterator[list[tuple[str, str, str]]]:
    """Every contract digest line the journey writes, as (stage, conversation,
    digest). Observed at the one function that writes the line, because the
    application's lifespan reconfigures logging when the client starts it; the
    line's FORMAT is pinned by `tests/test_assessment_contract.py`."""
    from app.services import assessment_contract  # noqa: PLC0415

    lines: list[tuple[str, str, str]] = []
    real = assessment_contract.log_digest

    def _record(stage: str, conversation_id: Any, contract: Any) -> None:
        lines.append((stage, str(conversation_id), contract.digest))
        real(stage, conversation_id, contract)

    with _swap(assessment_contract, "log_digest", _record):
        yield lines


class JourneyError(AssertionError):
    """A step answered something the journey cannot continue past."""


class Client(Protocol):
    def as_staff(self) -> None: ...

    def as_candidate(self, who: str = "candidate") -> None:
        """Act as a candidate the world seeded: `candidate` (assessed) or
        `rival` (applies and is never invited)."""

    def call(self, step: str, method: str, path: str, **kwargs: Any) -> tuple[int, Any]: ...


@dataclass
class JourneyState:
    """The ids the journey created, and what each response said."""

    tenant: uuid.UUID
    staff: uuid.UUID
    candidate: uuid.UUID
    rival: uuid.UUID
    job: uuid.UUID | None = None
    link: uuid.UUID | None = None
    rival_link: uuid.UUID | None = None
    conversation: uuid.UUID | None = None
    proctoring_session: uuid.UUID | None = None
    responses: dict[str, Any] = field(default_factory=dict)
    answered: dict[str, int] = field(default_factory=dict)
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


def _apply(
    client: Client, state: JourneyState, who: str, lines: tuple[str, ...], filename: str
) -> uuid.UUID:
    """One candidate replaces their main resume with a real DOCX and applies
    with it. The application's own profile is a snapshot of the main resume,
    parsed on its own: an application is an immutable copy of what was sent."""
    client.as_candidate(who)
    status, body = client.call(
        f"upload_resume_{who}",
        "PUT",
        f"{V1}/portal/me/resume",
        files={
            "resume": (
                filename,
                resume_docx(lines),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    _expect(f"upload_resume_{who}", status, body, 200)
    _run_dispatched(state, "pickready.parse_resume")
    status, body = client.call(
        f"apply_{who}",
        "POST",
        f"{V1}/portal/jobs/{state.job}/apply",
        data={
            "reuse_previous": "true",
            "validation": json.dumps(VALIDATION),
            "application_source": "direct",
        },
    )
    _expect(f"apply_{who}", status, body, 201)
    _run_dispatched(state, "pickready.parse_resume")
    return uuid.UUID(str(body["link_id"]))


# ── The assessment, one turn at a time ──────────────────────────────────────


def _count(state: JourneyState, kind: str) -> None:
    state.answered[kind] = state.answered.get(kind, 0) + 1


def _typed(state: JourneyState) -> str:
    answer = TYPED_ANSWERS[state.answered.get("typed", 0) % len(TYPED_ANSWERS)]
    _count(state, "typed")
    return answer


def _speak(client: Client, state: JourneyState, turn_seq: int, gate: Gate) -> dict[str, Any]:
    """Record, upload, transcribe (the dispatched task, Transcribe doubled at
    its boundary) and read back the final transcript. The respond body names
    the recording and nothing else: the transcript IS the answer."""
    conversation = state.conversation
    status, body = client.call(
        "voice_begin",
        "POST",
        f"{ASSESS}/conversations/{conversation}/voice/begin",
        json={"turn_seq": turn_seq},
    )
    _expect("voice_begin", status, body, 200)
    voice_id = body["id"]
    status, body = client.call(
        "voice_upload",
        "POST",
        f"{ASSESS}/conversations/{conversation}/voice/{voice_id}/audio",
        files={"file": ("answer.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 4096, "audio/webm;codecs=opus")},
    )
    _expect("voice_upload", status, body, 200)
    _run_dispatched(state, "pickready.transcribe_voice_answer")
    status, body = client.call(
        "voice_status", "GET", f"{ASSESS}/conversations/{conversation}/voice/{voice_id}"
    )
    _expect("voice_status", status, body, 200)
    if body.get("status") != "transcribed" or body.get("transcript") != SPOKEN_ANSWER:
        raise JourneyError(f"the spoken answer was not transcribed: {body}")
    state.responses["voice_id"] = voice_id
    gate("voice_transcribed", state)
    _count(state, "voice")
    return {"turn_seq": turn_seq, "answer": "", "voice_answer_id": voice_id}


def _code(client: Client, state: JourneyState, question_id: str, turn_seq: int, gate: Gate) -> dict[str, Any]:
    """Press Run on the visible samples, then send the same program as the
    final answer through `respond`, exactly as the editor does."""
    from harness.doubles.golden_model import CODING  # noqa: PLC0415

    base = f"{ASSESS}/conversations/{state.conversation}/coding/{question_id}"
    status, body = client.call(
        "coding_run",
        "POST",
        f"{base}/runs",
        json={"language": "python", "source": CODING.candidate, "client_token": "golden-journey-run"},
    )
    _expect("coding_run", status, body, 202)
    run_id = body["run_id"]
    for _poll in range(5):
        status, body = client.call("coding_run_poll", "GET", f"{base}/runs/{run_id}")
        _expect("coding_run_poll", status, body, 200)
        if body.get("status") != "queued":
            break
    state.responses["coding_run"] = body
    gate("coding_run", state)
    _count(state, "coding")
    return {
        "turn_seq": turn_seq,
        "answer": "",
        "answer_payload": {"language": "python", "code": CODING.candidate},
    }


def _answer_every_turn(client: Client, state: JourneyState, opened: dict[str, Any], gate: Gate) -> None:
    from app.services.assessment_formats import types  # noqa: PLC0415
    from harness.doubles.golden_model import FILL_BLANK_ANSWER, MCQ_CORRECT  # noqa: PLC0415

    turn = opened
    for _ in range(MAX_TURNS):
        if turn.get("status") == "completed":
            return
        if turn.get("status") != "active":
            raise JourneyError(f"the assessment stopped as {turn.get('status')}: {turn}")
        turn_seq = int(turn["turn_seq"])
        question = turn.get("question") or {}
        kind = question.get("question_type")
        if kind == types.MCQ_SINGLE:
            body = {"turn_seq": turn_seq, "answer_payload": {"selected_option_id": MCQ_CORRECT}}
            _count(state, "mcq")
        elif kind == types.FILL_BLANK:
            body = {"turn_seq": turn_seq, "answer_payload": {"values": [FILL_BLANK_ANSWER]}}
            _count(state, "fill_blank")
        elif kind == types.CODING:
            body = _code(client, state, str(question["id"]), turn_seq, gate)
        elif "voice" not in state.answered and turn.get("voice_input_available"):
            body = _speak(client, state, turn_seq, gate)
        else:
            body = {"turn_seq": turn_seq, "answer": _typed(state)}
        status, turn = client.call(
            "respond", "POST", f"{ASSESS}/conversations/{state.conversation}/respond", json=body
        )
        _expect("respond", status, turn, 200)
    raise JourneyError(f"the assessment did not complete within {MAX_TURNS} turns")


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

    # ── Two candidates upload a resume and apply ──────────────────────────
    state.link = _apply(client, state, "candidate", RESUME_LINES, RESUME_FILENAME)
    state.rival_link = _apply(client, state, "rival", RIVAL_RESUME_LINES, RIVAL_RESUME_FILENAME)
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

    # ── The candidate opens the proctored assessment ─────────────────────
    client.as_candidate()
    status, body = client.call(
        "proctoring_session",
        "POST",
        f"{V2}/proctoring/links/{state.link}/session",
        json=PROCTORING_SESSION,
    )
    _expect("proctoring_session", status, body, 200)
    state.proctoring_session = uuid.UUID(str(body["session_id"]))
    status, body = client.call(
        "read_consent", "GET", f"{ASSESS}/conversations/links/{state.link}/consent"
    )
    _expect("read_consent", status, body, 200)
    keys = [str(item["key"]) for item in body["consent"]["items"]]
    status, body = client.call(
        "accept_consent",
        "POST",
        f"{ASSESS}/conversations/links/{state.link}/consent",
        json={"consent_keys": keys},
    )
    _expect("accept_consent", status, body, 200)
    gate("proctoring_opened", state)

    status, opened = client.call("start", "POST", f"{ASSESS}/conversations/links/{state.link}/start")
    _expect("start", status, opened, 200)
    if opened.get("status") != "active":
        raise JourneyError(f"the start did not open a turn: {opened}")
    state.conversation = uuid.UUID(str(opened["conversation_id"]))
    gate("started", state)

    status, body = client.call(
        "proctoring_event",
        "POST",
        f"{V2}/proctoring/sessions/{state.proctoring_session}/events",
        json={
            "events": [
                {
                    "event_type": PROCTORING_EVENT,
                    "occurred_at": _now_iso(),
                    "metadata": {"action": "paste"},
                }
            ]
        },
    )
    _expect("proctoring_event", status, body, 200)

    _answer_every_turn(client, state, opened, gate)
    gate("completed", state)

    # ── The work completion asked for, in the order the product needs it ──
    # The coding answer is executed first: scoring holds while a submission
    # is owed, and the submission's own completion dispatches scoring again.
    _run_dispatched(state, "pickready.execute_coding_submission")
    gate("coding_executed", state)
    _run_dispatched(state, "pickready.index_document")
    _run_dispatched(state, "pickready.run_functional_assessment")
    gate("graded", state)
    gate("reported", state)
    _run_dispatched(state, "pickready.generate_proctoring_report")

    # ── The recruiter reads what was written, in words ────────────────────
    client.as_staff()
    status, body = client.call(
        "proctoring_report", "GET", f"{V2}/proctoring/links/{state.link}/report"
    )
    _expect("proctoring_report", status, body, 200)
    state.responses["proctoring_report"] = body
    gate("proctoring_reported", state)

    status, body = client.call("ranked_after", "GET", f"{V1}/jobs/{state.job}/candidates")
    _expect("ranked_after", status, body, 200)
    state.responses["ranked_after"] = body
    gate("reranked", state)

    status, body = client.call("executive_profile", "GET", f"{ASSESS}/reports/links/{state.link}")
    _expect("executive_profile", status, body, 200)
    state.responses["executive_profile"] = body
    status, body = client.call("transcript", "GET", f"{ASSESS}/transcripts/links/{state.link}")
    _expect("transcript", status, body, 200)
    state.responses["transcript"] = body
    gate("profile_read", state)
    return state


def _now_iso() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).isoformat()
