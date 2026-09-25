"""The coding sweeps: repair lost work, watch the sandbox, verify it once.

RECONCILE (every fifteen minutes). A submission is dispatched after its
request commits, and that invoke can fail after the row is durable; a task
can die mid-poll; a review can fail. Each leaves a row that owes work with
nothing working on it, and "not done" and "nothing to do" produce the same
empty log. So this asks the TABLE:
  * executions still `pending` or `submitted` after
    `coding_submission_redispatch_minutes`, and reviews `pending` or `failed`
    after `coding_review_retry_minutes`, are re-dispatched. The task's own
    advisory lock makes a re-dispatch of a row that is merely slow a no-op.
  * a row that has failed `ATTEMPTS_BEFORE_ALARM` times is logged at ERROR as
    `coding.submission_stuck`, and re-dispatched anyway: NOT a give-up
    threshold, the discipline `services/deletion_requests` already states.
  * a COMPLETED conversation with no report, whose coding work is all done or
    has outlived `coding_execution_max_wait_hours`, is handed to scoring. The
    first case repairs a lost scoring dispatch; the second is the end of the
    wait, after which the grader reads the open answer as "Not assessed".

PROBE (every five minutes). When code execution is disabled it logs
`status=disabled` and returns, so an operator reading the log sees the policy
rather than silence. Otherwise it runs one fixed canary through the provider
and asks for its health, and logs the status and the latency, never content.

VERIFY (registered, NOT scheduled). The operator's acceptance check before
`CODE_EXECUTION_BACKEND` is flipped on: a canary per configured language and
the hostile programs the sandbox must contain (an infinite loop, a memory hog,
an output flood, a process bomb, a network attempt). It returns a structured
verdict, and the verdict is green only when EVERY check ran and passed. Checks
that only the adapter can perform (an unauthenticated request must be
refused; the sandbox's language list must match its configured ids) are run
when the adapter offers `self_checks()`, and are otherwise recorded as NOT
PERFORMED, which is not green: a skipped check is not a passed check.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentConversation, FunctionalSkillsReport
from app.models.coding import EXECUTION_COMPLETE, CodingSubmission
from app.services import code_execution
from app.services.code_execution import (
    CodeExecutionProvider,
    ExecutionError,
    ExecutionOutcome,
    TestInput,
    outputs_match,
)
from app.services.code_execution import limits as execution_limits
from app.services.code_execution.languages import configured_languages
from app.services.coding_assessment.submissions import (
    CONVERSATION_COMPLETED,
    OPEN_EXECUTION,
    OPEN_REVIEW,
)
logger = logging.getLogger(__name__)

__all__ = [
    "ATTEMPTS_BEFORE_ALARM",
    "ReconcilePlan",
    "reconcile",
    "CANARY_PROGRAMS",
    "CANARY_STDIN",
    "CANARY_EXPECTED",
    "probe",
    "Check",
    "SandboxVerdict",
    "verify_sandbox",
]


# ── Reconcile ───────────────────────────────────────────────────────────────

#: Failed attempts before a row is reported as stuck. NOT a give-up threshold:
#: the row is re-dispatched either way. The same value and the same meaning as
#: `services/deletion_requests.ATTEMPTS_BEFORE_ALARM`, declared here rather than
#: imported so the coding sweep does not load the erasure machinery.
ATTEMPTS_BEFORE_ALARM = 5


@dataclass(frozen=True)
class ReconcilePlan:
    """What the sweep found. Ids only; the task dispatches them."""

    redispatch: tuple[str, ...]
    stuck: tuple[str, ...]
    score_links: tuple[str, ...]


async def reconcile(session: AsyncSession, *, now: datetime | None = None) -> ReconcilePlan:
    """Ask the table what coding work is owed. Reads only."""
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    execution_cutoff = moment - timedelta(minutes=settings.coding_submission_redispatch_minutes)
    review_cutoff = moment - timedelta(minutes=settings.coding_review_retry_minutes)
    wait_cutoff = moment - timedelta(hours=settings.coding_execution_max_wait_hours)

    owing = (
        await session.execute(
            select(CodingSubmission.id, CodingSubmission.execution_attempts).where(
                or_(
                    and_(
                        CodingSubmission.execution_status.in_(OPEN_EXECUTION),
                        CodingSubmission.created_at <= execution_cutoff,
                    ),
                    and_(
                        CodingSubmission.execution_status == EXECUTION_COMPLETE,
                        CodingSubmission.review_status.in_(OPEN_REVIEW),
                        CodingSubmission.created_at <= review_cutoff,
                    ),
                )
            )
            .order_by(CodingSubmission.created_at)
        )
    ).all()
    redispatch = tuple(str(row_id) for row_id, _attempts in owing)
    stuck = tuple(str(row_id) for row_id, attempts in owing if attempts >= ATTEMPTS_BEFORE_ALARM)

    is_open = or_(
        CodingSubmission.execution_status.in_(OPEN_EXECUTION),
        CodingSubmission.review_status.in_(OPEN_REVIEW),
    )
    has_coding = exists().where(CodingSubmission.conversation_id == AssessmentConversation.id)
    open_and_young = exists().where(
        CodingSubmission.conversation_id == AssessmentConversation.id,
        is_open,
        CodingSubmission.created_at > wait_cutoff,
    )
    reported = exists().where(
        FunctionalSkillsReport.job_candidate_link_id == AssessmentConversation.job_candidate_link_id
    )
    links = (
        await session.execute(
            select(AssessmentConversation.job_candidate_link_id)
            .where(
                AssessmentConversation.status == CONVERSATION_COMPLETED,
                AssessmentConversation.completed_at <= review_cutoff,
                has_coding,
                ~open_and_young,
                ~reported,
            )
            .order_by(AssessmentConversation.completed_at)
        )
    ).scalars().all()
    return ReconcilePlan(
        redispatch=redispatch, stuck=stuck, score_links=tuple(str(link) for link in links)
    )


# ── Probe ───────────────────────────────────────────────────────────────────

#: One program per language that adds the two integers on standard input.
#: Plain text handed to the sandbox as a program to run; nothing here runs it.
CANARY_PROGRAMS: dict[str, str] = {
    "python": "a, b = map(int, input().split())\nprint(a + b)\n",
    "java": (
        "import java.util.Scanner;\n"
        "public class Main {\n"
        "    public static void main(String[] args) {\n"
        "        Scanner in = new Scanner(System.in);\n"
        "        long a = in.nextLong();\n"
        "        long b = in.nextLong();\n"
        "        System.out.println(a + b);\n"
        "    }\n"
        "}\n"
    ),
    "cpp": (
        "#include <iostream>\n"
        "int main() { long long a, b; std::cin >> a >> b; std::cout << a + b << std::endl; return 0; }\n"
    ),
    "javascript": (
        "const data = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\n"
        "console.log(data[0] + data[1]);\n"
    ),
}
CANARY_STDIN = "2 3\n"
CANARY_EXPECTED = "5\n"
_CANARY_KEY = "canary"


def _canary_language() -> str:
    configured = [spec.key for spec in configured_languages()]
    return "python" if "python" in configured else configured[0]


async def probe(provider: CodeExecutionProvider | None = None) -> dict[str, Any]:
    """One canary and one health read. Logs `code_execution.probe status=...`
    with the latency only. Never raises for a sandbox failure: the probe's job
    is to REPORT it, and the log line is what the alarm counts."""
    if provider is None and not code_execution.is_enabled():
        logger.info("code_execution.probe status=disabled")
        return {"status": "disabled"}
    language = _canary_language()
    started = time.monotonic()
    try:
        sandbox = provider or code_execution.get_provider()
        results = await sandbox.run(
            language=language,
            source=CANARY_PROGRAMS[language],
            tests=(TestInput(key=_CANARY_KEY, stdin=CANARY_STDIN),),
            limits=execution_limits.for_language(language),
            deadline_seconds=get_settings().coding_probe_deadline_seconds,
        )
        health = await sandbox.health()
    except ExecutionError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "code_execution.probe status=failed reason=%s latency_ms=%d", exc.reason, latency_ms
        )
        return {"status": "failed", "reason": exc.reason, "latency_ms": latency_ms}
    latency_ms = int((time.monotonic() - started) * 1000)
    passed = (
        len(results) == 1
        and results[0].outcome is ExecutionOutcome.OK
        and outputs_match(results[0].stdout, CANARY_EXPECTED)
    )
    if not passed or not health.available:
        reason = "wrong_result" if not passed else "no_workers"
        logger.error("code_execution.probe status=failed reason=%s latency_ms=%d", reason, latency_ms)
        return {"status": "failed", "reason": reason, "latency_ms": latency_ms}
    logger.info(
        "code_execution.probe status=ok latency_ms=%d workers=%s queue_depth=%s",
        latency_ms, health.workers, health.queue_depth,
    )
    return {"status": "ok", "latency_ms": latency_ms}


# ── Verify ──────────────────────────────────────────────────────────────────

CHECK_PASSED = "passed"
CHECK_FAILED = "failed"
CHECK_NOT_PERFORMED = "not_performed"

#: Hostile programs, as plain text. Each is handed to the sandbox, which must
#: CONTAIN it; the expected outcomes are what containment looks like.
_HOSTILE: tuple[tuple[str, str, str, frozenset[ExecutionOutcome]], ...] = (
    (
        "infinite_loop",
        "python",
        "while True:\n    pass\n",
        frozenset({ExecutionOutcome.TIME_LIMIT}),
    ),
    (
        "memory_hog",
        "python",
        "blocks = []\nwhile True:\n    blocks.append(bytearray(64 * 1024 * 1024))\n",
        frozenset({ExecutionOutcome.MEMORY_LIMIT, ExecutionOutcome.RUNTIME_ERROR}),
    ),
    (
        "output_flood",
        "python",
        "import sys\nline = 'x' * 1024 + '\\n'\nwhile True:\n    sys.stdout.write(line)\n",
        frozenset({ExecutionOutcome.OUTPUT_LIMIT, ExecutionOutcome.RUNTIME_ERROR, ExecutionOutcome.TIME_LIMIT}),
    ),
    (
        "process_bomb",
        "cpp",
        "#include <unistd.h>\nint main() { while (true) { fork(); } return 0; }\n",
        frozenset({ExecutionOutcome.TIME_LIMIT, ExecutionOutcome.RUNTIME_ERROR}),
    ),
)
#: A program that prints `blocked` when it cannot open a socket to a public
#: address and `open` when it can. Containment is `blocked`.
_NETWORK_PROGRAM = (
    "import socket\n"
    "try:\n"
    "    socket.create_connection(('1.1.1.1', 53), timeout=3).close()\n"
    "    print('open')\n"
    "except OSError:\n"
    "    print('blocked')\n"
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class SandboxVerdict:
    """Green only when every check was performed AND passed."""

    status: str
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def green(self) -> bool:
        return self.status == "green"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks],
        }


async def _one(
    sandbox: CodeExecutionProvider, *, language: str, source: str, stdin: str, deadline: float
) -> code_execution.TestExecution:
    results = await sandbox.run(
        language=language,
        source=source,
        tests=(TestInput(key="check", stdin=stdin),),
        limits=execution_limits.for_language(language),
        deadline_seconds=deadline,
    )
    if len(results) != 1:
        raise ExecutionError("the sandbox answered for the wrong number of inputs", reason="incomplete_results")
    return results[0]


def _verdict(checks: Iterable[Check]) -> SandboxVerdict:
    checks = tuple(checks)
    green = bool(checks) and all(check.status == CHECK_PASSED for check in checks)
    return SandboxVerdict(status="green" if green else "red", checks=checks)


async def verify_sandbox(provider: CodeExecutionProvider | None = None) -> SandboxVerdict:
    """The operator's acceptance check. See the module docstring."""
    if provider is None and not code_execution.is_enabled():
        logger.info("code_execution.verify status=disabled")
        return SandboxVerdict(status="disabled")
    sandbox = provider or code_execution.get_provider()
    deadline = get_settings().coding_verify_deadline_seconds
    configured = [spec.key for spec in configured_languages()]
    checks: list[Check] = []

    async def run_check(name: str, language: str, source: str, stdin: str, judge) -> None:
        if language not in configured:
            checks.append(Check(name, CHECK_NOT_PERFORMED, f"{language} is not configured"))
            return
        try:
            result = await _one(sandbox, language=language, source=source, stdin=stdin, deadline=deadline)
        except ExecutionError as exc:
            checks.append(Check(name, CHECK_FAILED, f"sandbox error: {exc.reason}"))
            return
        passed, detail = judge(result)
        checks.append(Check(name, CHECK_PASSED if passed else CHECK_FAILED, detail))

    for language in configured:
        await run_check(
            f"canary_{language}",
            language,
            CANARY_PROGRAMS[language],
            CANARY_STDIN,
            lambda r: (
                r.outcome is ExecutionOutcome.OK and outputs_match(r.stdout, CANARY_EXPECTED),
                f"outcome {r.outcome.value}",
            ),
        )
    for name, language, source, contained in _HOSTILE:
        await run_check(
            name,
            language,
            source,
            "",
            lambda r, contained=contained: (r.outcome in contained, f"outcome {r.outcome.value}"),
        )
    await run_check(
        "network_attempt",
        "python",
        _NETWORK_PROGRAM,
        "",
        lambda r: (
            outputs_match(r.stdout, "blocked\n"),
            "no network" if outputs_match(r.stdout, "blocked\n") else f"outcome {r.outcome.value}",
        ),
    )
    try:
        health = await sandbox.health()
        checks.append(
            Check(
                "health_after_hostile_runs",
                CHECK_PASSED if health.available else CHECK_FAILED,
                health.detail,
            )
        )
    except ExecutionError as exc:
        checks.append(Check("health_after_hostile_runs", CHECK_FAILED, f"sandbox error: {exc.reason}"))
    checks.extend(await _adapter_checks(sandbox))
    verdict = _verdict(checks)
    logger.info(
        "code_execution.verify status=%s checks=%d failed=%s not_performed=%s",
        verdict.status,
        len(checks),
        [c.name for c in checks if c.status == CHECK_FAILED],
        [c.name for c in checks if c.status == CHECK_NOT_PERFORMED],
    )
    return verdict


#: The checks only the adapter can make, by name. Each must be reported.
ADAPTER_CHECK_NAMES: tuple[str, ...] = ("unauthenticated_refused", "language_ids_match")


async def _adapter_checks(sandbox: CodeExecutionProvider) -> Sequence[Check]:
    """Run the adapter's own checks when it offers them.

    `self_checks()` is an OPTIONAL adapter capability rather than part of the
    port, because what it checks (an unauthenticated request, the sandbox's own
    language ids) is a wire detail the domain must not know. An adapter that
    does not offer it gets every named check recorded as NOT PERFORMED, so the
    verdict cannot be green.
    """
    self_checks = getattr(sandbox, "self_checks", None)
    if self_checks is None:
        return [
            Check(name, CHECK_NOT_PERFORMED, "the provider does not offer this check")
            for name in ADAPTER_CHECK_NAMES
        ]
    try:
        reported: dict[str, tuple[bool, str]] = await self_checks()
    except ExecutionError as exc:
        return [Check(name, CHECK_FAILED, f"sandbox error: {exc.reason}") for name in ADAPTER_CHECK_NAMES]
    checks: list[Check] = []
    for name in ADAPTER_CHECK_NAMES:
        if name not in reported:
            checks.append(Check(name, CHECK_NOT_PERFORMED, "the provider did not report this check"))
            continue
        passed, detail = reported[name]
        checks.append(Check(name, CHECK_PASSED if passed else CHECK_FAILED, detail))
    return checks
