#!/usr/bin/env python
"""Exercise every vendor code path exactly once against the real endpoints.

    THIS COMMAND HAS NEVER BEEN RUN.

There is no `OPENAI_GPT_TERRA`, no `OPENAI_GPT_LUNA` and no
`VOYAGE_CONTEXT_4` in this phase, so nothing in this repository has been
executed against a live provider. This script exists so that the moment a key
arrives, the gap between "the code parses the shape we believe the vendor
sends" and "the vendor sends that shape" closes in one command with a printed
table, rather than by degrees over a production incident.

IT HAS ONE MORE THING TO SETTLE THAN IT USED TO. `gpt-5.6-terra` and
`gpt-5.6-luna` are the product owner's strings and have never been resolved
against a models endpoint, so this command is also the first thing that would
discover a wrong id. That arrives as a 404 or a 403 on the path concerned
rather than as a shape disagreement, and the table reports it as a FAIL with
the status in the detail column.

WHAT IT DOES (spec-doc6 §12.1)
------------------------------
Exactly one realistic round trip per code path, and no more:

  reasoning   the reasoning model, plain text, through `llm_router.invoke_llm`.
  extraction  the extraction model, JSON mode, through `llm_router.invoke_llm`.
  embedding   voyage-context-4, one document batch, through `embeddings.embed`.

Then one call per failure branch that can be provoked SAFELY:

  credential  a deliberately invalid key, expecting 401 or 403.
  rate_limit  the classifier and the `retry-after` reader, exercised against
              whatever the endpoint returns; reported as OBSERVED only if a 429
              actually arrives, and as NOT PROVOKED otherwise. A 429 cannot be
              manufactured on demand without abusing the vendor, and a check
              that quietly reported "pass" for a branch it never reached would
              be worse than no check.
  timeout     a deliberately tiny per-attempt timeout, expecting the router to
              classify a transport timeout and stop inside its budget.

"Realistic" is doing work in that sentence. A one-token "hi" would prove the
credential resolves and nothing else. Each success path sends a prompt shaped
like the one the product actually sends, and asserts the response against the
same contract `tests/test_vendor_contracts.py` asserts the fixtures against --
so a pass here means the hand-authored fixture was right, and a failure names
the fixture it disagreed with.

WHAT IT COSTS
-------------
Three completions and one embedding batch, all small. Well under a cent at
list price. The reason to keep it to one round trip per path is not cost: it is
that a verification script which loops is a verification script somebody
eventually runs in CI, and then the suite can fail because a vendor is down.

WHAT IT WRITES
--------------
`VERIFICATION_RESULTS.md` at the repository root, with the table, the exact
model ids, the UTC timestamp, and the git SHA it ran against. That file does
not exist in this repository, and its absence is the honest record that this
command has not been run. `VERIFICATION_PENDING.md` lists the rows it would
settle.

    python scripts/verify_live.py --help     # never needs a key
    python scripts/verify_live.py --dry-run  # prints the plan, calls nothing
    python scripts/verify_live.py            # needs all three keys

Run from `backend/`.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
from dataclasses import dataclass, field

# `python scripts/verify_live.py` puts `backend/scripts` on the path, not
# `backend`. Adding the parent is what lets this run as a script rather than
# only as `python -m`, which is how the command is written in
# VERIFICATION_PENDING.md and in spec-doc6 §12.1.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import llm_providers  # noqa: E402
from app.services import embeddings, llm_router  # noqa: E402
from app.services.reliability import vendor_contract  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RESULTS_PATH = REPO_ROOT / "VERIFICATION_RESULTS.md"

#: The banner every mode prints, including `--help`. spec-doc6 §12.1 asks for a
#: command that makes its own unrun state obvious at a glance, and a line at the
#: top of `--help` is the only place a reader is guaranteed to look.
NEVER_RUN_BANNER = (
    "NOT YET EXECUTED: this command has never been run against a live vendor. "
    "There is no OPENAI_GPT_TERRA, no OPENAI_GPT_LUNA and no VOYAGE_CONTEXT_4 "
    "in this phase, and no result in this repository comes from a real "
    "request. The two model ids have never been resolved against a models "
    "endpoint either. Absence of VERIFICATION_RESULTS.md is the record of that."
)

PASS = "PASS"
FAIL = "FAIL"
NOT_PROVOKED = "NOT PROVOKED"
SKIPPED = "SKIPPED"


# ── Outcomes ─────────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    """One path's result. `detail` is printed; it never carries a credential."""

    path: str
    vendor: str
    model: str
    what_it_proves: str
    status: str = SKIPPED
    detail: str = ""
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        # NOT PROVOKED is not a failure. It means the branch could not be
        # reached without abusing the vendor, which is a true statement about
        # the run rather than a green tick standing in for one.
        return self.status in (PASS, NOT_PROVOKED)


@dataclass
class Plan:
    outcomes: list[Outcome] = field(default_factory=list)

    def add(self, outcome: Outcome) -> Outcome:
        self.outcomes.append(outcome)
        return outcome


def build_plan() -> Plan:
    """Every path this command would exercise, in order.

    Split from the running so `--dry-run` prints exactly what a real run would
    do, from the same list, rather than from a comment that drifts.
    """
    plan = Plan()
    plan.add(
        Outcome(
            path="reasoning",
            vendor="OpenAI",
            model=llm_providers.MODEL_TERRA,
            what_it_proves=(
                "the model id resolves at all, and Chat Completions returns the "
                "choice list, message content and usage object that "
                "vendor_contract.OPENAI_CHAT_COMPLETIONS declares"
            ),
        )
    )
    plan.add(
        Outcome(
            path="extraction",
            vendor="OpenAI",
            model=llm_providers.MODEL_LUNA,
            what_it_proves=(
                "the model id resolves, the json_object response format is "
                "accepted with the token 'json' present in the messages, and "
                "the response parses as one top-level object"
            ),
        )
    )
    plan.add(
        Outcome(
            path="embedding",
            vendor="Voyage",
            model=llm_providers.EMBEDDING_MODEL,
            what_it_proves=(
                f"the embeddings endpoint honours an explicit output_dimension "
                f"of {embeddings.EMBEDDING_DIM} and returns a data row per "
                f"input carrying an explicit index"
            ),
        )
    )
    plan.add(
        Outcome(
            path="credential_failure",
            vendor="OpenAI",
            model=llm_providers.MODEL_LUNA,
            what_it_proves=(
                "a deliberately invalid key returns 401 or 403, which is what "
                "trips the breaker on the first occurrence"
            ),
        )
    )
    plan.add(
        Outcome(
            path="timeout",
            vendor="OpenAI",
            model=llm_providers.MODEL_TERRA,
            what_it_proves=(
                "a deliberately tiny per-attempt timeout is classified as a "
                "transport failure and the router stops inside its budget"
            ),
        )
    )
    plan.add(
        Outcome(
            path="rate_limit",
            vendor="OpenAI",
            model=llm_providers.MODEL_LUNA,
            what_it_proves=(
                "a 429 carries a retry-after the router reads rather than "
                "guesses at. Reported NOT PROVOKED unless a 429 actually "
                "arrives: a rate limit cannot be manufactured on demand "
                "without abusing the vendor"
            ),
        )
    )
    return plan


# ── The realistic payloads ───────────────────────────────────────────────────
#
# Shaped like what the product sends, because a one-token prompt proves the
# credential resolves and nothing about the response shape a grade is written
# from. No real candidate data: these are synthetic and self-contained.

REASONING_MESSAGES = [
    {
        "role": "system",
        "content": (
            "You assess one competency against one rubric level and cite the "
            "evidence you used. State what the evidence shows, name its "
            "originator, and say plainly when a claim rests on a single source."
        ),
    },
    {
        "role": "user",
        "content": (
            "Competency: owns delivery of a service through to production. "
            "Evidence: (1) resume line, 'led the migration of the billing "
            "service to a new queue'; (2) reference form from a named former "
            "manager describing the same migration and its completion date; "
            "(3) interview transcript in which the candidate restates the "
            "resume line. Assess the evidence and say how many independent "
            "originators it has."
        ),
    },
]

EXTRACTION_MESSAGES = [
    {
        "role": "system",
        "content": (
            "Extract the stated facts. Do not evaluate, rank or grade. "
            "Return skills, experience, education and role_fit as integers "
            "from 0 to 100 and a comments object."
        ),
    },
    {
        "role": "user",
        "content": (
            "Role: backend engineer, four to seven years, Python and "
            "Postgres, queue experience required. Resume: six years in "
            "Python; built and operated a Postgres-backed billing service; "
            "ran a queue migration end to end; BEng."
        ),
    },
]

EMBEDDING_TEXTS = [
    "Backend engineer with six years of Python and Postgres, queue migrations.",
    "Civil engineer specialising in structural inspection of highway bridges.",
]


# ── The paths ────────────────────────────────────────────────────────────────


async def _run_reasoning(outcome: Outcome) -> None:
    started = time.monotonic()
    text = await llm_router.invoke_llm("report_synthesis", REASONING_MESSAGES)
    outcome.elapsed_ms = (time.monotonic() - started) * 1000
    if not text.strip():
        outcome.status = FAIL
        outcome.detail = "the response carried no text"
        return
    outcome.status = PASS
    outcome.detail = f"{len(text)} characters of text returned"


async def _run_extraction(outcome: Outcome) -> None:
    started = time.monotonic()
    text = await llm_router.invoke_llm(
        "rerank", EXTRACTION_MESSAGES, response_format_json=True
    )
    outcome.elapsed_ms = (time.monotonic() - started) * 1000
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        outcome.status = FAIL
        outcome.detail = (
            f"JSON mode returned a {type(decoded).__name__}; every JSON-mode "
            f"caller in this codebase parses a top-level object"
        )
        return
    outcome.status = PASS
    outcome.detail = f"top-level object with keys {sorted(decoded)[:6]}"


async def _run_embedding(outcome: Outcome) -> None:
    started = time.monotonic()
    vectors = await embeddings.embed(EMBEDDING_TEXTS)
    outcome.elapsed_ms = (time.monotonic() - started) * 1000
    if not embeddings.is_semantic():
        outcome.status = FAIL
        outcome.detail = (
            "the deterministic dev fallback ran, so no request reached Voyage"
        )
        return
    widths = {len(v) for v in vectors}
    if widths != {embeddings.EMBEDDING_DIM}:
        outcome.status = FAIL
        outcome.detail = f"vector widths {sorted(widths)}"
        return
    outcome.status = PASS
    outcome.detail = f"{len(vectors)} vectors, {embeddings.EMBEDDING_DIM} wide"


async def _run_credential_failure(outcome: Outcome) -> None:
    """A deliberately invalid key. Never the real one, and never printed."""
    import httpx

    started = time.monotonic()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            llm_providers.OPENAI_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": "Bearer sk-deliberately-invalid-key-for-verification",
                "content-type": "application/json",
            },
            json=llm_router.build_payload(
                model=llm_providers.MODEL_LUNA,
                messages=[{"role": "user", "content": "verification probe"}],
                json_mode=False,
                max_tokens=16,
                temperature=llm_providers.temperature_for("rerank"),
            ),
        )
    outcome.elapsed_ms = (time.monotonic() - started) * 1000
    status = response.status_code
    kind = llm_providers.classify_status(status)
    if status in llm_providers.CREDENTIAL_STATUSES and kind == "credential":
        outcome.status = PASS
        outcome.detail = f"{status} classified as {kind}, breaker trips on first"
        return
    outcome.status = FAIL
    outcome.detail = (
        f"an invalid key returned {status}, classified as {kind}; the breaker "
        f"rule assumes 401 or 403"
    )


async def _run_timeout(outcome: Outcome) -> None:
    """A one-millisecond attempt timeout. The safest failure to provoke."""
    started = time.monotonic()
    try:
        await llm_router.invoke_llm(
            "report_synthesis",
            REASONING_MESSAGES,
            timeout=0.001,
            total_budget=10.0,
        )
    except llm_router.LLMUnavailableError as exc:
        outcome.elapsed_ms = (time.monotonic() - started) * 1000
        if outcome.elapsed_ms > 10_000 * 1.5:
            outcome.status = FAIL
            outcome.detail = "the router ran past its total budget"
            return
        outcome.status = PASS
        outcome.detail = f"raised inside the budget: {exc}"
        return
    outcome.elapsed_ms = (time.monotonic() - started) * 1000
    outcome.status = FAIL
    outcome.detail = "a 1ms attempt timeout produced a successful call"


async def _run_rate_limit(outcome: Outcome) -> None:
    """One call, and an honest report of whether a 429 came back.

    Deliberately does NOT hammer the endpoint to force one. A verification
    script that provokes a rate limit is a script that costs somebody else
    their quota, and the branch it would prove is already covered against a
    recorded fixture in `tests/test_vendor_contracts.py`.
    """
    import httpx

    started = time.monotonic()
    try:
        await llm_router.invoke_llm(
            "rerank", EXTRACTION_MESSAGES, response_format_json=True
        )
    except llm_router.LLMUnavailableError as exc:
        outcome.elapsed_ms = (time.monotonic() - started) * 1000
        if "rate_limit" in str(exc):
            outcome.status = PASS
            outcome.detail = "a 429 arrived and was classified as rate_limit"
            return
        outcome.status = NOT_PROVOKED
        outcome.detail = f"no 429 arrived; the call failed for another reason: {exc}"
        return
    except httpx.HTTPError as exc:  # pragma: no cover -- transport, not router
        outcome.elapsed_ms = (time.monotonic() - started) * 1000
        outcome.status = NOT_PROVOKED
        outcome.detail = f"transport failure rather than a 429: {type(exc).__name__}"
        return
    outcome.elapsed_ms = (time.monotonic() - started) * 1000
    outcome.status = NOT_PROVOKED
    outcome.detail = (
        "the call succeeded, so no rate limit was reached. The classifier and "
        "the retry-after reader remain proven only against recorded fixtures."
    )


RUNNERS = {
    "reasoning": _run_reasoning,
    "extraction": _run_extraction,
    "embedding": _run_embedding,
    "credential_failure": _run_credential_failure,
    "timeout": _run_timeout,
    "rate_limit": _run_rate_limit,
}


# ── Driving ──────────────────────────────────────────────────────────────────


async def run_plan(plan: Plan, only: set[str] | None = None) -> None:
    """Execute each path once, recording the outcome rather than raising.

    One path's failure must not hide the next path's result: the whole value of
    the table is being able to see that Sonnet answered and Voyage did not.
    A `VendorContractViolation` is caught here for the same reason and reported
    as a FAIL naming the fixture, which is the message worth reading.
    """
    for outcome in plan.outcomes:
        if only and outcome.path not in only:
            outcome.detail = "not selected"
            continue
        # Each path is its own first live use, so the once-per-path memo is
        # cleared between them. In production it is exactly right that the
        # check runs once; here every path is being verified deliberately.
        vendor_contract.reset_first_use()
        llm_router.reset_provider_stats()
        llm_router.clear_provider_breaker()
        try:
            await RUNNERS[outcome.path](outcome)
        except vendor_contract.VendorContractViolation as exc:
            outcome.status = FAIL
            outcome.detail = str(exc)
        except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
            outcome.status = FAIL
            outcome.detail = f"{type(exc).__name__}: {exc}"


def render_table(plan: Plan) -> str:
    width = max(len(o.path) for o in plan.outcomes)
    lines = [
        f"{'PATH'.ljust(width)}  {'RESULT'.ljust(12)}  {'MS'.rjust(7)}  DETAIL",
        f"{'-' * width}  {'-' * 12}  {'-' * 7}  {'-' * 40}",
    ]
    for o in plan.outcomes:
        lines.append(
            f"{o.path.ljust(width)}  {o.status.ljust(12)}  "
            f"{o.elapsed_ms:7.0f}  {o.detail}"
        )
    return "\n".join(lines)


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_results(plan: Plan) -> pathlib.Path:
    """Write `VERIFICATION_RESULTS.md`.

    Only ever written by a real run. The file's existence is the evidence, so
    `--dry-run` must never create it and neither must a run that made no call.
    """
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    rows = "\n".join(
        f"| `{o.path}` | {o.vendor} | `{o.model}` | {o.status} | "
        f"{o.elapsed_ms:.0f} | {o.detail.replace('|', '/')} |"
        for o in plan.outcomes
    )
    failures = [o for o in plan.outcomes if not o.ok]
    verdict = (
        "Every path exercised returned the shape this codebase was built "
        "against."
        if not failures
        else f"{len(failures)} path(s) did not match. Read the detail column "
        f"before changing any parser: a shape disagreement means either the "
        f"published documentation was wrong or the schema has changed."
    )
    RESULTS_PATH.write_text(
        f"""# Verification results

Produced by `backend/scripts/verify_live.py` against the live vendor
endpoints. Everything below is an OBSERVED result; nothing here is inferred
from documentation.

- Run at: {stamp}
- Commit: `{git_sha()}`
- Reasoning path: `{llm_providers.MODEL_TERRA}`
- Extraction path: `{llm_providers.MODEL_LUNA}`
- Embedding path: `{llm_providers.EMBEDDING_MODEL}`

{verdict}

| Path | Vendor | Model | Result | ms | Detail |
|---|---|---|---:|---:|---|
{rows}

## What a NOT PROVOKED row means

The branch could not be reached without abusing the vendor. It is not a pass
and it is not a failure: it means the behaviour remains proven only against the
hand-authored fixtures in `backend/tests/fixtures/vendor/`.

## Next

Remove the corresponding rows from `VERIFICATION_PENDING.md` **only** for the
paths that show PASS above. A row is removed by a run that succeeded, never by
a run that was attempted.
""",
        encoding="utf-8",
    )
    return RESULTS_PATH


#: Every credential a full run needs, derived from the routing table rather
#: than typed out, so a repointed model cannot leave this list naming a variable
#: nothing reads.
REQUIRED_ENV_VARS: tuple[str, ...] = tuple(
    sorted(llm_providers.ENV_VAR_FOR_MODEL.values())
) + ("VOYAGE_CONTEXT_4",)


def missing_keys() -> list[str]:
    return [
        name
        for name in REQUIRED_ENV_VARS
        if not (os.environ.get(name) or "").strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_live.py",
        description=(
            f"{NEVER_RUN_BANNER}\n\n"
            "One realistic round trip per vendor code path, plus one per "
            "safely provokable failure branch. Prints a pass/fail table and "
            "writes VERIFICATION_RESULTS.md."
        ),
        epilog=(
            f"Needs {', '.join(REQUIRED_ENV_VARS)}. Run from backend/. "
            f"The rows it would settle are listed in VERIFICATION_PENDING.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and exit. Calls nothing and writes nothing.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(RUNNERS),
        help="run one path only. Repeatable.",
    )
    args = parser.parse_args(argv)

    plan = build_plan()

    print(NEVER_RUN_BANNER)
    print()

    if args.dry_run:
        print("PLAN (nothing below was called):")
        for o in plan.outcomes:
            print(f"  {o.path:<20} {o.vendor:<10} {o.model}")
            print(f"  {'':<20} proves: {o.what_it_proves}")
        return 0

    absent = missing_keys()
    if absent:
        print(f"REFUSING TO RUN: {', '.join(absent)} is not set.")
        print(
            "Nothing was called. This is the state the repository is in as "
            "shipped, and it is why VERIFICATION_RESULTS.md does not exist."
        )
        return 2

    asyncio.run(run_plan(plan, set(args.only) if args.only else None))

    print(render_table(plan))
    print()
    written = write_results(plan)
    print(f"Wrote {written}")

    failures = [o for o in plan.outcomes if not o.ok]
    if failures:
        print(f"{len(failures)} path(s) did not match the recorded contract.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
