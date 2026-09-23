"""The Judge0 CE adapter: the ONE module under `app/` that knows Judge0.

Everything Judge0-shaped lives here and nowhere else: the URL paths, the auth
headers, the numeric status ids, the language ids, base64 transport and the
batch cap. Domain code sees `provider.CodeExecutionProvider` and its
dataclasses; `tests/test_code_execution_architecture.py` fails the build if a
Judge0 detail appears anywhere else.

Written against the published Judge0 CE 1.13.1 API (`/submissions/batch`,
`/submissions/{token}`, `/workers`). The request fixtures and response shapes
the tests use are authored from that documentation and recorded as such in
`tests/fixtures/vendor/PROVENANCE.md`; none is a recording of traffic.

WHAT IS NEVER SENT
------------------
`expected_output` (the answer stays in the application, see `provider.py`),
`callback_url` (the sandbox never calls back into the VPC), `compiler_options`,
`command_line_arguments` and `additional_files` (each widens what a submission
can make the sandbox do). `enable_network` is sent explicitly false, and the
host refuses to honour true in any case (`ALLOW_ENABLE_NETWORK=false`).

WHAT IS NEVER LOGGED
--------------------
Source, stdin, stdout, stderr and the token. A log line carries the operation,
the batch size, the status class and the latency. A candidate program can echo
its stdin, and a hidden test's stdin is part of the answer key.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Awaitable, Callable, Sequence

import httpx

from app.core.config import Settings
from app.services.code_execution.errors import (
    ExecutionError,
    ExecutionRejected,
    ExecutionTicketLost,
    ExecutionUnavailable,
)
from app.services.code_execution.limits import ExecutionLimits
from app.services.code_execution.provider import (
    ExecutionOutcome,
    ExecutionTicket,
    ProviderHealth,
    TestExecution,
    TestInput,
)

logger = logging.getLogger(__name__)

__all__ = ["Judge0Provider", "PROVIDER_NAME", "MAX_SUBMISSION_BATCH_SIZE"]

PROVIDER_NAME = "judge0"

#: Judge0's own `MAX_SUBMISSION_BATCH_SIZE`, set to the same value in
#: `infra/modules/code_sandbox/judge0.conf.tftpl`. Larger batches are split.
MAX_SUBMISSION_BATCH_SIZE = 20

#: A runtime error whose peak memory reached this share of the limit is
#: reported as the memory limit. Judge0 enforces memory through the cgroup,
#: which kills the process with a signal rather than naming the cause.
MEMORY_LIMIT_SHARE = 0.95

_STATUS_IN_QUEUE = 1
_STATUS_PROCESSING = 2
_STATUS_ACCEPTED = 3
_STATUS_TIME_LIMIT = 5
_STATUS_COMPILATION_ERROR = 6
_STATUS_SIGXFSZ = 8
_RUNTIME_ERROR_STATUSES = frozenset({7, 8, 9, 10, 11, 12})
_INTERNAL_STATUSES = frozenset({13, 14})
_PENDING_STATUSES = frozenset({_STATUS_IN_QUEUE, _STATUS_PROCESSING})

_RESULT_FIELDS = "token,status,stdout,stderr,compile_output,time,wall_time,memory,memory_limit"
_MS_PER_SECOND = 1000

Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _unb64(value: Any) -> str:
    """Decode a base64 field Judge0 returned. Null is empty.

    Judge0 wraps its base64 at sixty columns; `b64decode` discards the
    newlines. Bytes that are not UTF-8 are replaced rather than raising,
    because a program is free to print them.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ExecutionRejected("Judge0 returned a non-string output field", reason="protocol")
    return base64.b64decode(value).decode("utf-8", errors="replace")


def _seconds_to_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(round(float(value) * _MS_PER_SECOND))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


class Judge0Provider:
    """`CodeExecutionProvider` over a self-hosted Judge0 CE instance."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        language_ids: dict[str, int],
        connect_timeout: float,
        request_timeout: float,
        poll_seconds: float,
        max_output_chars: int,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleeper = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = auth_token
        self._language_ids = dict(language_ids)
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=request_timeout,
            write=request_timeout,
            pool=connect_timeout,
        )
        self._poll_seconds = poll_seconds
        self._max_output_chars = max_output_chars
        self._transport = transport
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> "Judge0Provider":
        return cls(
            base_url=settings.judge0_url.strip(),
            auth_token=settings.judge0_auth_token.strip(),
            language_ids=settings.judge0_language_id_map,
            connect_timeout=settings.code_execution_connect_timeout_seconds,
            request_timeout=settings.code_execution_request_timeout_seconds,
            poll_seconds=settings.code_execution_poll_seconds,
            max_output_chars=settings.code_execution_max_output_chars,
        )

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        # Both headers carry the token: X-Auth-Token authenticates every call
        # and X-Auth-User authorises the privileged ones (DELETE, /workers).
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            headers={"X-Auth-Token": self._token, "X-Auth-User": self._token},
        )

    async def _request(
        self,
        op: str,
        method: str,
        path: str,
        *,
        batch_size: int,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """One call, with every failure mapped onto the domain's errors.

        Nothing is swallowed. Transport failures and 5xx, 429 and 503 ("queue
        is full") are `ExecutionUnavailable`; 401 and 403 are
        `ExecutionUnavailable(reason="credential")` logged at ERROR, because a
        wrong token is an outage an operator must fix, not a bad request; 404
        is `ExecutionTicketLost` (the host was replaced or pruned the run);
        any other 4xx is `ExecutionRejected`.
        """
        started = self._clock()
        try:
            async with self._client() as client:
                response = await client.request(method, path, params=params, json=json_body)
        except httpx.TimeoutException as exc:
            self._log(op, batch_size, "timeout", started)
            raise ExecutionUnavailable(f"sandbox {op} timed out", reason="timeout") from exc
        except httpx.TransportError as exc:
            self._log(op, batch_size, "network", started)
            raise ExecutionUnavailable(f"sandbox {op} could not connect", reason="network") from exc

        status = response.status_code
        self._log(op, batch_size, _status_class(status), started)
        if status in (401, 403):
            logger.error(
                "code_execution.credential_refused provider=%s op=%s status=%d",
                PROVIDER_NAME,
                op,
                status,
            )
            raise ExecutionUnavailable(
                f"sandbox refused the credential on {op}", reason="credential"
            )
        if status == 503:
            raise ExecutionUnavailable(f"sandbox queue is full on {op}", reason="queue_full")
        if status == 429 or status >= 500:
            raise ExecutionUnavailable(f"sandbox answered {status} on {op}", reason="server_error")
        if status == 404:
            raise ExecutionTicketLost(f"sandbox does not know the {op} target", reason="not_found")
        if status >= 400:
            raise ExecutionRejected(f"sandbox rejected {op} with {status}", reason="rejected")
        return response

    def _log(self, op: str, batch_size: int, status_class: str, started: float) -> None:
        logger.info(
            "code_execution.request provider=%s op=%s batch_size=%d status_class=%s latency_ms=%d",
            PROVIDER_NAME,
            op,
            batch_size,
            status_class,
            int((self._clock() - started) * _MS_PER_SECOND),
        )

    @staticmethod
    def _json(response: httpx.Response, op: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ExecutionRejected(f"sandbox {op} answered with non-JSON", reason="protocol") from exc

    # ── The five operations ──────────────────────────────────────────────

    def _submission_body(
        self, language_id: int, source: str, test: TestInput, limits: ExecutionLimits
    ) -> dict[str, Any]:
        return {
            "language_id": language_id,
            "source_code": _b64(source),
            "stdin": _b64(test.stdin),
            "cpu_time_limit": limits.cpu_seconds,
            "cpu_extra_time": limits.cpu_extra_seconds,
            "wall_time_limit": limits.wall_seconds,
            "memory_limit": limits.memory_kb,
            "stack_limit": limits.stack_kb,
            "max_processes_and_or_threads": limits.max_processes,
            "max_file_size": limits.max_file_kb,
            "enable_per_process_and_thread_time_limit": False,
            "enable_per_process_and_thread_memory_limit": False,
            "enable_network": False,
            "number_of_runs": 1,
            "redirect_stderr_to_stdout": False,
        }

    async def submit(
        self,
        *,
        language: str,
        source: str,
        tests: Sequence[TestInput],
        limits: ExecutionLimits,
    ) -> ExecutionTicket:
        if not tests:
            raise ExecutionRejected("a submission needs at least one test input", reason="no_tests")
        keys = [test.key for test in tests]
        if len(set(keys)) != len(keys):
            raise ExecutionRejected("test input keys must be unique", reason="duplicate_keys")
        language_id = self._language_ids.get(language)
        if language_id is None:
            raise ExecutionRejected(
                f"no sandbox language id is configured for {language!r}",
                reason="unknown_language",
            )

        tokens: list[str] = []
        for start in range(0, len(tests), MAX_SUBMISSION_BATCH_SIZE):
            chunk = tests[start : start + MAX_SUBMISSION_BATCH_SIZE]
            body = {"submissions": [self._submission_body(language_id, source, t, limits) for t in chunk]}
            try:
                response = await self._request(
                    "submit",
                    "POST",
                    "/submissions/batch",
                    batch_size=len(chunk),
                    params={"base64_encoded": "true"},
                    json_body=body,
                )
                accepted, refused_fields = self._tokens_from(self._json(response, "submit"), len(chunk))
                tokens.extend(accepted)
                if refused_fields:
                    # A per-entry validation error in a 201 batch. The field
                    # names are ours to fix and carry no candidate content.
                    raise ExecutionRejected(
                        f"sandbox refused a batch entry on fields {refused_fields}",
                        reason="rejected",
                    )
            except ExecutionError:
                # Part of the test set was accepted and part was not. A half
                # submitted test set cannot be graded, so every accepted run
                # is deleted rather than left on the host holding stdin
                # nobody will collect; the error still propagates unchanged.
                if tokens:
                    await self.discard(
                        ExecutionTicket(provider=PROVIDER_NAME, refs=tuple(tokens), keys=tuple(keys[: len(tokens)]))
                    )
                raise
        return ExecutionTicket(provider=PROVIDER_NAME, refs=tuple(tokens), keys=tuple(keys))

    @staticmethod
    def _tokens_from(payload: Any, expected: int) -> tuple[list[str], list[str]]:
        """The accepted tokens, and the field names of any refused entry."""
        if not isinstance(payload, list) or len(payload) != expected:
            raise ExecutionRejected("sandbox batch answer does not match the batch", reason="protocol")
        tokens: list[str] = []
        refused: list[str] = []
        for entry in payload:
            token = entry.get("token") if isinstance(entry, dict) else None
            if isinstance(token, str) and token:
                tokens.append(token)
            else:
                refused.extend(sorted(entry) if isinstance(entry, dict) else ["<not an object>"])
        return tokens, refused

    async def collect(self, ticket: ExecutionTicket) -> list[TestExecution] | None:
        if ticket.provider != PROVIDER_NAME or len(ticket.refs) != len(ticket.keys):
            raise ExecutionRejected("ticket does not belong to this provider", reason="foreign_ticket")
        rows: list[dict[str, Any]] = []
        for start in range(0, len(ticket.refs), MAX_SUBMISSION_BATCH_SIZE):
            chunk = ticket.refs[start : start + MAX_SUBMISSION_BATCH_SIZE]
            response = await self._request(
                "collect",
                "GET",
                "/submissions/batch",
                batch_size=len(chunk),
                params={"tokens": ",".join(chunk), "base64_encoded": "true", "fields": _RESULT_FIELDS},
            )
            payload = self._json(response, "collect")
            entries = payload.get("submissions") if isinstance(payload, dict) else None
            if not isinstance(entries, list) or len(entries) != len(chunk):
                raise ExecutionRejected("sandbox collect answer does not match the ticket", reason="protocol")
            for entry in entries:
                if entry is None:
                    raise ExecutionTicketLost("sandbox no longer holds a submitted run", reason="pruned")
                if not isinstance(entry, dict):
                    raise ExecutionRejected("sandbox collect entry is not an object", reason="protocol")
                rows.append(entry)

        if any(self._status_id(row) in _PENDING_STATUSES for row in rows):
            return None
        return [self._execution(key, row) for key, row in zip(ticket.keys, rows)]

    @staticmethod
    def _status_id(row: dict[str, Any]) -> int:
        status = row.get("status")
        status_id = status.get("id") if isinstance(status, dict) else None
        if not isinstance(status_id, int):
            raise ExecutionRejected("sandbox result carries no status id", reason="protocol")
        return status_id

    def _execution(self, key: str, row: dict[str, Any]) -> TestExecution:
        status_id = self._status_id(row)
        memory = row.get("memory")
        memory_kb = int(memory) if isinstance(memory, (int, float)) else None
        limit = row.get("memory_limit")

        if status_id == _STATUS_ACCEPTED:
            outcome = ExecutionOutcome.OK
        elif status_id == _STATUS_TIME_LIMIT:
            outcome = ExecutionOutcome.TIME_LIMIT
        elif status_id == _STATUS_COMPILATION_ERROR:
            outcome = ExecutionOutcome.COMPILE_ERROR
        elif status_id == _STATUS_SIGXFSZ:
            # The sandbox writes stdout to a file, so exceeding the file-size
            # limit IS exceeding the output limit.
            outcome = ExecutionOutcome.OUTPUT_LIMIT
        elif status_id in _RUNTIME_ERROR_STATUSES:
            near_limit = (
                memory_kb is not None
                and isinstance(limit, (int, float))
                and limit > 0
                and memory_kb >= MEMORY_LIMIT_SHARE * limit
            )
            outcome = ExecutionOutcome.MEMORY_LIMIT if near_limit else ExecutionOutcome.RUNTIME_ERROR
        else:
            # 13 and 14 are the sandbox's own faults. Status 4 (Wrong Answer)
            # cannot occur because no expected output is ever sent, and an id
            # this adapter has never heard of is not something to guess at.
            # Both are reported as a sandbox fault, never as the candidate's.
            if status_id not in _INTERNAL_STATUSES:
                logger.error(
                    "code_execution.unexpected_status provider=%s status_id=%d",
                    PROVIDER_NAME,
                    status_id,
                )
            outcome = ExecutionOutcome.INTERNAL_ERROR

        max_chars = self._max_output_chars
        return TestExecution(
            key=key,
            outcome=outcome,
            stdout=_truncate(_unb64(row.get("stdout")), max_chars),
            stderr=_truncate(_unb64(row.get("stderr")), max_chars),
            compile_output=_truncate(_unb64(row.get("compile_output")), max_chars),
            cpu_ms=_seconds_to_ms(row.get("time")),
            wall_ms=_seconds_to_ms(row.get("wall_time")),
            memory_kb=memory_kb,
        )

    async def run(
        self,
        *,
        language: str,
        source: str,
        tests: Sequence[TestInput],
        limits: ExecutionLimits,
        deadline_seconds: float,
    ) -> list[TestExecution]:
        """Submit, poll and collect, never past `deadline_seconds`.

        The deadline PREDICTS: another poll is started only when it can finish
        inside the budget (`elapsed + poll interval < deadline`), the same rule
        the agent loop and the router follow. The batch is discarded on every
        exit path once it exists.
        """
        started = self._clock()
        ticket = await self.submit(language=language, source=source, tests=tests, limits=limits)
        try:
            while True:
                if self._clock() - started + self._poll_seconds >= deadline_seconds:
                    raise ExecutionUnavailable(
                        "sandbox did not finish inside the deadline", reason="deadline"
                    )
                await self._sleep(self._poll_seconds)
                results = await self.collect(ticket)
                if results is not None:
                    return results
        finally:
            await self.discard(ticket)

    async def discard(self, ticket: ExecutionTicket) -> None:
        """Delete every run in the ticket from the sandbox.

        A failed delete is recorded at WARNING with the operation only and does
        not raise: the caller already holds its result, and the host's hourly
        prune (`judge0-prune.timer`) is the backstop that bounds how long a
        run's stdin can live there. The failure is logged, never dropped.
        """
        for ref in ticket.refs:
            try:
                await self._request(
                    "discard",
                    "DELETE",
                    f"/submissions/{ref}",
                    batch_size=1,
                    params={"fields": "token"},
                )
            except (ExecutionUnavailable, ExecutionRejected, ExecutionTicketLost) as exc:
                logger.warning(
                    "code_execution.discard_failed provider=%s reason=%s",
                    PROVIDER_NAME,
                    exc.reason,
                )

    async def health(self) -> ProviderHealth:
        response = await self._request("health", "GET", "/workers", batch_size=0)
        payload = self._json(response, "health")
        if not isinstance(payload, list):
            raise ExecutionRejected("sandbox /workers answer is not a list", reason="protocol")
        workers = sum(int(q.get("available", 0)) for q in payload if isinstance(q, dict))
        queued = sum(int(q.get("size", 0)) for q in payload if isinstance(q, dict))
        return ProviderHealth(
            provider=PROVIDER_NAME,
            available=workers > 0,
            workers=workers,
            queue_depth=queued,
            detail="workers available" if workers > 0 else "no workers available",
        )
