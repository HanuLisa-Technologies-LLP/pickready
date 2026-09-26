"""The code-execution seam: a Judge0 host served from fixtures, and an echoing program.

TWO DOUBLES, BECAUSE THE SEAM HAS TWO QUESTIONS
------------------------------------------------
A scenario about the sandbox asks one of two things, and they need different
doubles.

**"What does the product do when the sandbox misbehaves?"** That is a FAULT,
and it is served where every other vendor fault in this harness is served: at
the HTTP seam, from an authored fixture, to the REAL adapter. The fault layer
configures the deployment the way a live one is configured
(`CODE_EXECUTION_BACKEND=judge0`, a sandbox address, a token), so the product's
own `code_execution.get_provider()` builds the real `Judge0Provider`, its real
`_request` receives the fixture's status, and its real classification decides
what the failure is. Nothing here returns a canned `ExecutionUnavailable`:
that would prove the Run route handles an exception a double was told to
raise, which is a statement about the double. `judge0_responder` below is the
handler; `faults.code_execution_failure` installs it.

**"What does the product do with a program's output?"** That needs a program
that RUNS, and nothing in this repository executes candidate code, by design
and by test (`tests/test_candidate_code_never_executes.py`). So the answer is
the product's own test double, `code_execution.fake.FakeProvider`, which
answers by LOOKUP and never executes, installed through the product's own
`code_execution.override_provider`, which refuses in production. The one thing
a harness adds is a script: `echoing_provider` scripts the program to ECHO its
stdin, so every input it is handed comes straight back as its output and in its
error text. That is the most hostile program a candidate can write against a
hidden test, because the answer key's INPUT becomes the program's OUTPUT, and
it is what the exfiltration scenario sweeps for.

A success is not a fault and is deliberately NOT in the fault registry, for
the reason `faults.model_answers` gives: a scenario declaring it under
`faults:` would be judged by `degradation_honesty` and fail for the right
reason while meaning the wrong thing. A workload step installs it with
`echoing_program`, for exactly the block that needs it.

THE FIXTURES ARE THE ONES 4A AUTHORED, AND NONE IS INVENTED HERE
-----------------------------------------------------------------
`tests/fixtures/vendor/judge0/` holds the Judge0 envelopes the adapter's own
tests are written against, each carrying `_provenance.observed: false`. They
are read through `vendor.load_fixture`, which asserts that provenance, and a
fault kind with no fixture RAISES `FixtureMissing` naming the file to author.
`timeout` is the one kind with no fixture, and none is possible: a timeout is
the ABSENCE of a response, exactly as it is on the model seam.

Nothing here reaches the network, the database or a model.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, Mapping, Sequence

import httpx

from app.services.code_execution import override_provider
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.code_execution.provider import ExecutionOutcome

from harness.doubles.vendor import FixtureMissing, load_fixture

__all__ = [
    "CODE_EXECUTION_FAULT_KINDS",
    "JUDGE0_ERROR_FIXTURES",
    "SANDBOX_HOST",
    "SANDBOX_URL",
    "echoing_program",
    "echoing_provider",
    "judge0_responder",
]

#: The address the fault configures as `JUDGE0_URL`. An RFC 2606 `.invalid`
#: name, so a request that escaped the routing transport could not resolve to
#: anything, and a value that leaked into a log or an artifact names itself.
SANDBOX_HOST = "code-sandbox.harness.invalid"
SANDBOX_URL = f"http://{SANDBOX_HOST}:2358"

#: The fault kinds served from an authored Judge0 envelope, and what the REAL
#: adapter makes of each (`judge0.Judge0Provider._request`):
#:
#:   unavailable   500, an unhandled sandbox fault  -> ExecutionUnavailable(server_error)
#:   queue_full    503, the sandbox's queue is full -> ExecutionUnavailable(queue_full)
#:   credential    401, the token was refused       -> ExecutionUnavailable(credential),
#:                                                     logged at ERROR for an operator
#:
#: All three are OUTAGES in the domain's words, never the candidate's fault,
#: which is the property the Run route's 503 and the submission's retry rest on.
JUDGE0_ERROR_FIXTURES: Mapping[str, str] = {
    "unavailable": "judge0/error_500_server_error.json",
    "queue_full": "judge0/error_503_queue_full.json",
    "credential": "judge0/error_401_unauthorized.json",
}

#: Every kind this seam serves, as data, so the fault registry and a test agree
#: without either restating the list.
CODE_EXECUTION_FAULT_KINDS: frozenset[str] = frozenset(set(JUDGE0_ERROR_FIXTURES) | {"timeout"})


def judge0_responder(kind: str) -> Callable[[httpx.Request], httpx.Response]:
    """A handler for `httpx.MockTransport` that answers `kind` on every call.

    Every call, not the first, for the reason the model seam gives: a sandbox
    that failed once and then answered would let a retry report recovery,
    which is a different claim from the one an outage scenario makes. The
    fixture is loaded HERE, when the fault is built, so a missing envelope is
    refused before anything is applied rather than on the first request.
    """
    if kind == "timeout":
        def timed_out(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("harness fault: sandbox timeout", request=request)

        return timed_out

    if kind in JUDGE0_ERROR_FIXTURES:
        fixture = load_fixture(JUDGE0_ERROR_FIXTURES[kind])

        def errored(request: httpx.Request) -> httpx.Response:
            return fixture.response(request)

        return errored

    raise FixtureMissing(
        f"no code-execution fault of kind {kind!r}. The kinds this seam serves "
        f"are {sorted(CODE_EXECUTION_FAULT_KINDS)}; another kind needs an "
        f"envelope authored under tests/fixtures/vendor/judge0/ from the "
        f"published Judge0 CE API, with _provenance.observed false."
    )


def echoing_provider(source: str, stdins: Sequence[str]) -> FakeProvider:
    """The product's fake sandbox, scripted so `source` ECHOES every input.

    Each input comes back as the program's stdout, which is what a candidate
    printing `sys.stdin.read()` gets, and one input, the LAST, is scripted to
    stop with a runtime error whose message quotes the input, because an
    exception message is the other channel a program can print through. Both
    channels are therefore full of whatever the product handed the sandbox.

    An input the scenario did not list still RAISES inside the fake
    (`LookupError`), which is the fake's own rule: a double that invented an
    outcome for an input nobody scripted would let the sweep pass against
    behaviour nobody wrote.
    """
    if not stdins:
        raise ValueError("an echoing program needs at least one input to echo")
    provider = FakeProvider()
    *echoed, last = list(stdins)
    for stdin in echoed:
        provider.script(source, stdin, ScriptedRun(stdout=stdin))
    provider.script(
        source,
        last,
        ScriptedRun(
            outcome=ExecutionOutcome.RUNTIME_ERROR,
            stdout=last,
            stderr=f"ValueError: could not parse {last!r}",
        ),
    )
    return provider


@contextmanager
def echoing_program(source: str, stdins: Sequence[str]) -> Iterator[FakeProvider]:
    """Install `echoing_provider` for the block, through the product's own door.

    `override_provider` restores whatever was installed before, so this nests
    inside a fault and unwinds innermost first like every other seam here. It
    REFUSES in production, which is the guarantee that no configuration can
    route a live candidate to a program that never runs.
    """
    provider = echoing_provider(source, stdins)
    with override_provider(provider):
        yield provider
