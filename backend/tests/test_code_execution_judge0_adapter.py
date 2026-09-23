"""The Judge0 adapter, driven over `httpx.MockTransport` and authored fixtures.

Every response body below is a hand-authored envelope from
`tests/fixtures/vendor/judge0/` (see PROVENANCE.md). The adapter under test is
the real one, with only its transport replaced, so the request it BUILDS is
what these tests read: the half that matters most is what it never sends.
"""
from __future__ import annotations

import base64
import json
import logging
import pathlib
from typing import Any, Callable

import httpx
import pytest

from app.services.code_execution import judge0
from app.services.code_execution.errors import (
    ExecutionRejected,
    ExecutionTicketLost,
    ExecutionUnavailable,
)
from app.services.code_execution.judge0 import Judge0Provider
from app.services.code_execution.limits import ExecutionLimits
from app.services.code_execution.provider import ExecutionOutcome, ExecutionTicket, TestInput

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "vendor" / "judge0"

LIMITS = ExecutionLimits(
    cpu_seconds=2.0,
    cpu_extra_seconds=0.5,
    wall_seconds=5.0,
    memory_kb=262144,
    stack_kb=65536,
    max_processes=60,
    max_file_kb=1024,
    max_output_chars=4000,
)

#: Request fields that must never leave the application. Each one widens what
#: a submission can make the sandbox do, or hands it the answer key.
FORBIDDEN_FIELDS = (
    "expected_output",
    "callback_url",
    "compiler_options",
    "command_line_arguments",
    "additional_files",
)

TOKEN = "s3cr3t-judge0-token-sentinel"


def envelope(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert data["_provenance"]["observed"] is False
    return data


def respond(name: str) -> httpx.Response:
    data = envelope(name)
    return httpx.Response(data["status"], json=data["body"])


class Recorder:
    """A MockTransport handler serving fixtures by (method, path)."""

    def __init__(self, routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response] | str]):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        key = (request.method, "/submissions/{token}" if request.method == "DELETE" else path)
        route = self.routes.get(key)
        if route is None:
            raise AssertionError(f"unexpected request {request.method} {path}")
        return route(request) if callable(route) else respond(route)

    def bodies(self, method: str = "POST") -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.method == method]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def provider(handler: Callable[[httpx.Request], httpx.Response], clock: FakeClock | None = None) -> Judge0Provider:
    clock = clock or FakeClock()

    async def sleep(seconds: float) -> None:
        clock.now += seconds

    return Judge0Provider(
        base_url="http://judge0.test:2358",
        auth_token=TOKEN,
        language_ids={"python": 71, "java": 62, "cpp": 54, "javascript": 63},
        connect_timeout=2.0,
        request_timeout=5.0,
        poll_seconds=0.5,
        max_output_chars=4000,
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        clock=clock,
    )


def two_tests() -> list[TestInput]:
    return [TestInput(key="h1", stdin="2 3\n"), TestInput(key="h2", stdin="10 20\n")]


# ── The request: what is sent, and what is never sent ────────────────────────


async def test_no_expected_output_or_other_widening_field_is_ever_sent() -> None:
    recorder = Recorder({("POST", "/submissions/batch"): "submissions_batch_create_201.json"})
    ticket = await provider(recorder).submit(
        language="python", source="print(sum(map(int, input().split())))", tests=two_tests(), limits=LIMITS
    )

    assert ticket.refs == tuple(entry["token"] for entry in envelope("submissions_batch_create_201.json")["body"])
    assert ticket.keys == ("h1", "h2")
    (body,) = recorder.bodies()
    assert len(body["submissions"]) == 2
    for submission in body["submissions"]:
        for field in FORBIDDEN_FIELDS:
            assert field not in submission, field
        assert submission["enable_network"] is False
        assert submission["language_id"] == 71
    # And not merely absent at the top level of each entry: nowhere in the
    # serialized request at all.
    raw = recorder.requests[0].content.decode()
    for field in FORBIDDEN_FIELDS:
        assert field not in raw, field


def test_the_test_input_type_cannot_carry_an_expected_output() -> None:
    """The structural half: there is no field to put the answer in."""
    assert set(TestInput.__dataclass_fields__) == {"key", "stdin"}


async def test_every_limit_is_sent_explicitly_and_the_source_is_base64() -> None:
    recorder = Recorder({("POST", "/submissions/batch"): "submissions_batch_create_201.json"})
    source = "print('héllo')\n"
    await provider(recorder).submit(language="python", source=source, tests=two_tests(), limits=LIMITS)

    submission = recorder.bodies()[0]["submissions"][0]
    assert recorder.requests[0].url.params["base64_encoded"] == "true"
    assert base64.b64decode(submission["source_code"]).decode() == source
    assert base64.b64decode(submission["stdin"]).decode() == "2 3\n"
    assert submission["cpu_time_limit"] == 2.0
    assert submission["cpu_extra_time"] == 0.5
    assert submission["wall_time_limit"] == 5.0
    assert submission["memory_limit"] == 262144
    assert submission["stack_limit"] == 65536
    assert submission["max_processes_and_or_threads"] == 60
    assert submission["max_file_size"] == 1024
    assert submission["number_of_runs"] == 1
    assert submission["redirect_stderr_to_stdout"] is False
    assert submission["enable_per_process_and_thread_time_limit"] is False
    assert submission["enable_per_process_and_thread_memory_limit"] is False


async def test_both_auth_headers_carry_the_token() -> None:
    recorder = Recorder({("POST", "/submissions/batch"): "submissions_batch_create_201.json"})
    await provider(recorder).submit(language="python", source="x", tests=two_tests(), limits=LIMITS)
    headers = recorder.requests[0].headers
    assert headers["X-Auth-Token"] == TOKEN
    assert headers["X-Auth-User"] == TOKEN


async def test_a_large_test_set_is_split_into_batches_of_the_host_cap() -> None:
    def create(request: httpx.Request) -> httpx.Response:
        count = len(json.loads(request.content)["submissions"])
        start = len(recorder.requests) * 100
        return httpx.Response(201, json=[{"token": f"t{start + i}"} for i in range(count)])

    recorder = Recorder({("POST", "/submissions/batch"): create})
    tests = [TestInput(key=f"h{i}", stdin=str(i)) for i in range(45)]
    ticket = await provider(recorder).submit(language="python", source="x", tests=tests, limits=LIMITS)

    sizes = [len(body["submissions"]) for body in recorder.bodies()]
    assert sizes == [20, 20, 5]
    assert all(size <= judge0.MAX_SUBMISSION_BATCH_SIZE for size in sizes)
    assert len(ticket.refs) == 45 and ticket.keys == tuple(t.key for t in tests)


async def test_an_unconfigured_language_is_refused_before_any_request() -> None:
    recorder = Recorder({})
    with pytest.raises(ExecutionRejected) as caught:
        await provider(recorder).submit(language="cobol", source="x", tests=two_tests(), limits=LIMITS)
    assert caught.value.reason == "unknown_language"
    assert recorder.requests == []


async def test_a_batch_entry_error_refuses_the_batch_and_deletes_what_was_accepted() -> None:
    recorder = Recorder(
        {
            ("POST", "/submissions/batch"): "submissions_batch_create_entry_error_201.json",
            ("DELETE", "/submissions/{token}"): "submissions_delete_200.json",
        }
    )
    with pytest.raises(ExecutionRejected) as caught:
        await provider(recorder).submit(language="python", source="x", tests=two_tests(), limits=LIMITS)
    assert "language_id" in str(caught.value)
    # The whole batch is refused, so the one accepted entry is not left on the
    # host holding stdin nobody will collect.
    accepted = envelope("submissions_batch_create_entry_error_201.json")["body"][0]["token"]
    assert [(r.method, r.url.path) for r in recorder.requests] == [
        ("POST", "/submissions/batch"),
        ("DELETE", f"/submissions/{accepted}"),
    ]


async def test_a_later_chunk_failing_deletes_the_earlier_accepted_chunk() -> None:
    calls = {"n": 0}

    def create(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            count = len(json.loads(request.content)["submissions"])
            return httpx.Response(201, json=[{"token": f"t{i}"} for i in range(count)])
        return respond("error_503_queue_full.json")

    recorder = Recorder(
        {
            ("POST", "/submissions/batch"): create,
            ("DELETE", "/submissions/{token}"): "submissions_delete_200.json",
        }
    )
    tests = [TestInput(key=f"h{i}", stdin=str(i)) for i in range(25)]
    with pytest.raises(ExecutionUnavailable) as caught:
        await provider(recorder).submit(language="python", source="x", tests=tests, limits=LIMITS)
    assert caught.value.reason == "queue_full"
    deleted = [r.url.path for r in recorder.requests if r.method == "DELETE"]
    assert deleted == [f"/submissions/t{i}" for i in range(20)]


# ── Collect: the status map ───────────────────────────────────────────────────


def mixed_ticket() -> ExecutionTicket:
    rows = envelope("submissions_batch_get_mixed.json")["body"]["submissions"]
    return ExecutionTicket(
        provider="judge0",
        refs=tuple(row["token"] for row in rows),
        keys=("ok", "compile", "runtime", "tle", "memory", "output", "internal"),
    )


async def test_every_outcome_class_is_mapped_from_the_documented_statuses() -> None:
    recorder = Recorder({("GET", "/submissions/batch"): "submissions_batch_get_mixed.json"})
    results = await provider(recorder).collect(mixed_ticket())

    assert results is not None
    by_key = {r.key: r for r in results}
    assert by_key["ok"].outcome is ExecutionOutcome.OK
    assert by_key["ok"].stdout == "5\n"
    assert by_key["ok"].cpu_ms == 12 and by_key["ok"].wall_ms == 31 and by_key["ok"].memory_kb == 3264

    assert by_key["compile"].outcome is ExecutionOutcome.COMPILE_ERROR
    assert "expected ';'" in by_key["compile"].compile_output

    assert by_key["runtime"].outcome is ExecutionOutcome.RUNTIME_ERROR
    assert "ZeroDivisionError" in by_key["runtime"].stderr

    assert by_key["tle"].outcome is ExecutionOutcome.TIME_LIMIT
    assert by_key["memory"].outcome is ExecutionOutcome.MEMORY_LIMIT
    assert by_key["output"].outcome is ExecutionOutcome.OUTPUT_LIMIT
    assert by_key["internal"].outcome is ExecutionOutcome.INTERNAL_ERROR

    request = recorder.requests[0]
    assert request.url.params["base64_encoded"] == "true"
    assert request.url.params["tokens"] == ",".join(mixed_ticket().refs)
    assert "memory_limit" in request.url.params["fields"]


async def test_a_runtime_error_far_below_the_memory_limit_stays_a_runtime_error() -> None:
    rows = envelope("submissions_batch_get_mixed.json")["body"]["submissions"]
    runtime = dict(rows[2])
    assert runtime["memory"] < judge0.MEMORY_LIMIT_SHARE * runtime["memory_limit"]
    recorder = Recorder(
        {("GET", "/submissions/batch"): lambda r: httpx.Response(200, json={"submissions": [runtime]})}
    )
    (result,) = await provider(recorder).collect(
        ExecutionTicket(provider="judge0", refs=(runtime["token"],), keys=("r",))
    )
    assert result.outcome is ExecutionOutcome.RUNTIME_ERROR


async def test_an_unknown_status_is_a_sandbox_fault_never_the_candidates(caplog: pytest.LogCaptureFixture) -> None:
    row = dict(envelope("submissions_batch_get_mixed.json")["body"]["submissions"][0])
    row["status"] = {"id": 4, "description": "Wrong Answer"}
    recorder = Recorder({("GET", "/submissions/batch"): lambda r: httpx.Response(200, json={"submissions": [row]})})
    with caplog.at_level(logging.ERROR, logger=judge0.__name__):
        (result,) = await provider(recorder).collect(
            ExecutionTicket(provider="judge0", refs=(row["token"],), keys=("x",))
        )
    assert result.outcome is ExecutionOutcome.INTERNAL_ERROR
    assert any("unexpected_status" in r.getMessage() for r in caplog.records)


async def test_output_is_truncated_to_the_configured_limit() -> None:
    row = dict(envelope("submissions_batch_get_mixed.json")["body"]["submissions"][0])
    row["stdout"] = base64.b64encode(b"z" * 9000).decode()
    recorder = Recorder({("GET", "/submissions/batch"): lambda r: httpx.Response(200, json={"submissions": [row]})})
    (result,) = await provider(recorder).collect(ExecutionTicket(provider="judge0", refs=("t",), keys=("x",)))
    assert len(result.stdout) == 4000


async def test_a_batch_with_any_pending_run_is_still_pending() -> None:
    recorder = Recorder({("GET", "/submissions/batch"): "submissions_batch_get_pending.json"})
    rows = envelope("submissions_batch_get_pending.json")["body"]["submissions"]
    ticket = ExecutionTicket(provider="judge0", refs=tuple(r["token"] for r in rows), keys=("a", "b"))
    assert await provider(recorder).collect(ticket) is None


async def test_a_pruned_run_is_a_lost_ticket_not_an_empty_result() -> None:
    recorder = Recorder({("GET", "/submissions/batch"): "submissions_batch_get_pruned.json"})
    with pytest.raises(ExecutionTicketLost):
        await provider(recorder).collect(ExecutionTicket(provider="judge0", refs=("gone",), keys=("a",)))


async def test_a_foreign_ticket_is_refused_before_any_request() -> None:
    recorder = Recorder({})
    with pytest.raises(ExecutionRejected):
        await provider(recorder).collect(ExecutionTicket(provider="fake", refs=("x",), keys=("a",)))
    assert recorder.requests == []


# ── Failures are classified, never swallowed ──────────────────────────────────


@pytest.mark.parametrize(
    "fixture,reason",
    [
        ("error_401_unauthorized.json", "credential"),
        ("error_403_forbidden.json", "credential"),
        ("error_503_queue_full.json", "queue_full"),
        ("error_500_server_error.json", "server_error"),
    ],
)
async def test_outages_and_credential_failures_are_unavailable(
    fixture: str, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder({("POST", "/submissions/batch"): fixture})
    with caplog.at_level(logging.INFO, logger=judge0.__name__):
        with pytest.raises(ExecutionUnavailable) as caught:
            await provider(recorder).submit(language="python", source="x", tests=two_tests(), limits=LIMITS)
    assert caught.value.reason == reason
    if reason == "credential":
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors and "credential_refused" in errors[0].getMessage()


async def test_a_validation_failure_is_our_bug_and_is_rejected() -> None:
    recorder = Recorder({("POST", "/submissions/batch"): "error_422_unprocessable.json"})
    with pytest.raises(ExecutionRejected):
        await provider(recorder).submit(language="python", source="x", tests=two_tests(), limits=LIMITS)


@pytest.mark.parametrize(
    "exc,reason",
    [(httpx.ConnectTimeout("slow"), "timeout"), (httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "network")],
)
async def test_transport_failures_are_unavailable(exc: Exception, reason: str) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ExecutionUnavailable) as caught:
        await provider(boom).submit(language="python", source="x", tests=two_tests(), limits=LIMITS)
    assert caught.value.reason == reason


# ── run(), discard(), health() ────────────────────────────────────────────────


async def test_run_polls_until_done_then_deletes_every_run() -> None:
    polls = {"n": 0}

    def get(request: httpx.Request) -> httpx.Response:
        polls["n"] += 1
        pending = envelope("submissions_batch_get_pending.json")["body"]
        if polls["n"] < 3:
            return httpx.Response(200, json=pending)
        done = [dict(row, status={"id": 3, "description": "Accepted"}, stdout=pending["submissions"][0]["stdout"]) for row in pending["submissions"]]
        return httpx.Response(200, json={"submissions": done})

    recorder = Recorder(
        {
            ("POST", "/submissions/batch"): "submissions_batch_create_201.json",
            ("GET", "/submissions/batch"): get,
            ("DELETE", "/submissions/{token}"): "submissions_delete_200.json",
        }
    )
    results = await provider(recorder).run(
        language="python", source="x", tests=two_tests(), limits=LIMITS, deadline_seconds=10
    )
    assert [r.outcome for r in results] == [ExecutionOutcome.OK, ExecutionOutcome.OK]
    assert polls["n"] == 3
    deleted = [r for r in recorder.requests if r.method == "DELETE"]
    assert len(deleted) == 2
    assert all(r.headers["X-Auth-User"] == TOKEN for r in deleted)


async def test_run_stops_before_a_poll_that_cannot_finish_and_still_deletes() -> None:
    recorder = Recorder(
        {
            ("POST", "/submissions/batch"): "submissions_batch_create_201.json",
            ("GET", "/submissions/batch"): "submissions_batch_get_pending.json",
            ("DELETE", "/submissions/{token}"): "submissions_delete_200.json",
        }
    )
    clock = FakeClock()
    with pytest.raises(ExecutionUnavailable) as caught:
        await provider(recorder, clock).run(
            language="python", source="x", tests=two_tests(), limits=LIMITS, deadline_seconds=2.0
        )
    assert caught.value.reason == "deadline"
    # Predictive: with a 0.5s poll and a 2.0s budget the fourth poll would end
    # exactly on the deadline, so it is never started.
    assert sum(1 for r in recorder.requests if r.method == "GET") == 3
    assert clock.now < 2.0
    assert sum(1 for r in recorder.requests if r.method == "DELETE") == 2


async def test_a_failed_delete_is_logged_and_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    recorder = Recorder({("DELETE", "/submissions/{token}"): "error_500_server_error.json"})
    with caplog.at_level(logging.WARNING, logger=judge0.__name__):
        await provider(recorder).discard(ExecutionTicket(provider="judge0", refs=("t1",), keys=("a",)))
    assert any("discard_failed" in r.getMessage() for r in caplog.records)


async def test_health_reads_the_worker_pool() -> None:
    recorder = Recorder({("GET", "/workers"): "workers_200.json"})
    health = await provider(recorder).health()
    assert health.available is True and health.workers == 2 and health.queue_depth == 0


# ── Logging carries no content ────────────────────────────────────────────────


async def test_no_log_line_carries_source_stdin_stdout_or_the_token(caplog: pytest.LogCaptureFixture) -> None:
    source_sentinel = "SOURCE_SENTINEL_7f3a"
    stdin_sentinel = "STDIN_SENTINEL_91bc"
    stdout_sentinel = "STDOUT_SENTINEL_44de"

    def get(request: httpx.Request) -> httpx.Response:
        row = dict(
            envelope("submissions_batch_get_mixed.json")["body"]["submissions"][0],
            stdout=base64.b64encode(stdout_sentinel.encode()).decode(),
        )
        return httpx.Response(200, json={"submissions": [row, dict(row)]})

    recorder = Recorder(
        {
            ("POST", "/submissions/batch"): "submissions_batch_create_201.json",
            ("GET", "/submissions/batch"): get,
            ("DELETE", "/submissions/{token}"): "error_500_server_error.json",
        }
    )
    with caplog.at_level(logging.DEBUG):
        results = await provider(recorder).run(
            language="python",
            source=f"print('{source_sentinel}')",
            tests=[TestInput(key="h1", stdin=stdin_sentinel), TestInput(key="h2", stdin=stdin_sentinel)],
            limits=LIMITS,
            deadline_seconds=10,
        )
    assert results[0].stdout == stdout_sentinel
    # Every logger, including httpx's own: content and the credential appear
    # in none of them.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "code_execution.request" in logged  # the sweep is not vacuous
    for sentinel in (source_sentinel, stdin_sentinel, stdout_sentinel, TOKEN):
        assert sentinel not in logged, sentinel
    # The adapter's own lines do not even carry the run references. (httpx's
    # transport log may print a request URL, and a run reference is opaque and
    # useless without the credential, so that one is not asserted there.)
    refs = {entry["token"] for entry in envelope("submissions_batch_create_201.json")["body"]}
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == judge0.__name__)
    for ref in refs:
        assert ref not in ours, ref
