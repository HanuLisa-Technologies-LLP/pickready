"""The nine evaluators of HARNESS.md section 7, every one deterministic.

WHY NOT ONE OF THEM CALLS A MODEL
-----------------------------------
Section 7: "A model based evaluator is used only where deterministic evaluation
cannot answer the question, and never for something verifiable directly."
Every question below is verifiable directly. Whether a number reached a client
is a sweep over the payloads; whether a tenant boundary held is a status code
and a row count; whether the system degraded honestly is whether it said so.

The rule behind that rule is the one `agent_loop` already follows: success
criteria are deterministic code, because the moment the guard matters most is
the moment the provider is down. An LLM judge here would make the harness fail
exactly when the thing it is judging fails, and for the same reason.

WHY SEVERAL OF THEM REPORT `unavailable` ON AN ORDINARY RUN
-------------------------------------------------------------
`degradation_honesty` and `recovery` answer questions that only exist once a
fault has been injected. On a scenario with no faults they say so, by name, and
block. They do NOT pass: a pass would mean "the system degraded honestly" about
a run where nothing was asked to degrade, and a tier full of those would report
resilience the product has never demonstrated.

Provenance: docs/spec/HARNESS.md sections 4 and 7.
"""
from __future__ import annotations

import statistics
from typing import Awaitable, Callable, Mapping

from app.evaluation.metrics import InsufficientData, mean_interval

from harness.probes import read_prohibited
from harness.run import EvaluationOutcome
from harness.scenario import Tier

from .base import EvaluationInput, cannot_compute, failed, passed, verdict

__all__ = ["EVALUATORS"]


# ── Correctness ──────────────────────────────────────────────────────────────


async def functional_correctness(data: EvaluationInput) -> EvaluationOutcome:
    """Did the system do what the scenario asked, over its real routes.

    Three things sink it, and they are separate on purpose. An unhandled 500
    is a defect whatever the assertions say, because a scenario asserting a
    refusal would otherwise pass on a crash that happened to return the right
    status family. An aborted workload means the later assertions never ran, so
    reporting on the earlier ones alone would understate the damage. And the
    output and dispatch assertions are the scenario's own statement of what
    correct means here.
    """
    name = "functional_correctness"
    if data.ctx.aborted:
        return failed(
            name,
            f"the workload aborted before it finished: {data.ctx.aborted}",
            aborted=data.ctx.aborted,
        )
    errors = data.server_errors
    if errors:
        return failed(
            name,
            "the product answered with a server error: " + "; ".join(errors[:5]),
            server_errors=list(errors),
        )
    checks = data.of_kind("output") + data.of_kind("dispatched")
    return verdict(
        name,
        checks,
        nothing_to_judge=(
            "this scenario declared no output or dispatch assertion, so there "
            "is nothing for functional correctness to judge. Naming the "
            "evaluator without giving it ground truth measures nothing."
        ),
        all_held=(
            f"{len(checks)} output and dispatch assertion(s) held over "
            f"{len(data.ctx.observations)} real request(s), with no server error"
        ),
        some_failed="the system did not do what the scenario asked",
    )


async def state_correctness(data: EvaluationInput) -> EvaluationOutcome:
    """Does the DATABASE hold what the scenario says it should.

    Judged only over `state` checks, which are the ones read from a second
    connection after the response. That separation is the whole point: a run
    can be functionally correct in every payload it returned and still have
    written nothing, which is precisely what the 2026-09-20 `audit_log`
    rollback looked like from the outside.
    """
    name = "state_correctness"
    checks = data.of_kind("state")
    if checks and data.reader is None:
        return cannot_compute(
            name,
            "state assertions were declared but no second connection could be "
            "opened, so nothing about the committed state was read",
        )
    return verdict(
        name,
        checks,
        nothing_to_judge=(
            "this scenario declared no state assertion. A run judged only on "
            "its responses cannot tell a committed write from one that "
            "answered 200 and rolled back at commit."
        ),
        all_held=(
            f"{len(checks)} state assertion(s) held, read from a second "
            "connection after the response"
        ),
        some_failed="the committed state is not what the scenario expects",
    )


# ── Safety ───────────────────────────────────────────────────────────────────


async def tenancy_isolation(data: EvaluationInput) -> EvaluationOutcome:
    """Did anything cross a tenant boundary, in either direction.

    MEASURABLE ONLY IN A WORLD WITH TWO TENANTS, and it says so otherwise. A
    single-tenant world cannot distinguish "isolation held" from "there was
    nothing to isolate from", and reporting the second as the first is how a
    boundary test becomes decorative.

    Both halves are checked. The STATUS half: a cross-tenant read answers 404
    and never 403, because the status code is itself an oracle and a 403
    confirms that a job id belongs to somebody. The CONTENT half: no foreign
    tenant's id may appear in any payload, which catches a route that answered
    404 for the row it was asked about while leaking a neighbour's in a list.
    """
    name = "tenancy_isolation"
    world = data.ctx.world
    if len(world.tenants) < 2:
        return cannot_compute(
            name,
            "this world holds one tenant, so a crossing could not have been "
            "observed. Isolation is measured against a SECOND customer whose "
            "rows genuinely exist; against an id that exists nowhere, a "
            "product with no isolation at all answers 404 too.",
            tenants=len(world.tenants),
        )
    acting = str(world.ids.get("tenant", ""))
    foreign = [str(item) for item in world.tenants if str(item) != acting]
    foreign_ids = {
        str(value)
        for key, value in world.ids.items()
        if key.endswith("_b") and value is not None
    }
    leaks: list[str] = []
    for item in data.ctx.observations:
        rendered = repr(item.body)
        for identifier in foreign_ids:
            if identifier and identifier in rendered:
                leaks.append(f"{item.method} {item.path} rendered {identifier}")
    occurred_403, detail_403 = read_prohibited("a_cross_tenant_read_answered_403", data.ctx)
    occurred_ok, detail_ok = read_prohibited("a_cross_tenant_read_succeeded", data.ctx)
    if occurred_403 or occurred_ok or leaks:
        return failed(
            name,
            "a tenant boundary was crossed or announced: "
            + "; ".join(
                part
                for part in (
                    detail_403 if occurred_403 else "",
                    detail_ok if occurred_ok else "",
                    "; ".join(leaks[:4]),
                )
                if part
            ),
            foreign_tenants=foreign,
            leaks=leaks[:8],
        )
    return passed(
        name,
        f"every read across the boundary was refused as not-found, and no "
        f"payload rendered any of the {len(foreign)} foreign tenant's rows",
        foreign_tenants=foreign,
    )


async def no_numbers_to_client(data: EvaluationInput) -> EvaluationOutcome:
    """Rule 1, over every payload that actually crossed the boundary.

    Delegates to the product's own enforcement rather than restating it:
    `siddhi.numbers` for a delivered report and for score-shaped keys, and
    `conversation_guardrails.contains_forbidden_number` for prose. A second
    implementation of this rule in the harness would be the copy nobody
    maintains, and the day the two disagreed the harness would be the one that
    was wrong while looking authoritative.
    """
    name = "no_numbers_to_client"
    if not data.ctx.observations:
        return cannot_compute(
            name,
            "no request was made, so no payload reached a client and the rule "
            "could not be exercised",
        )
    occurred, detail = read_prohibited("a_number_reached_a_client", data.ctx)
    if occurred:
        return failed(name, f"a number reached a client: {detail}", detail=detail)
    return passed(
        name,
        f"{len(data.ctx.observations)} payload(s) swept clean, with no "
        "sanctioned exception (D3)",
    )


async def safety_and_policy(data: EvaluationInput) -> EvaluationOutcome:
    """Every prohibited outcome the scenario named, plus the two that always apply.

    The two that always apply are the harness's own isolation guarantees from
    HARNESS.md section 1: no model credential is configured, and nothing was
    dispatched anywhere but the recorder. They are checked on every scenario
    naming this evaluator rather than only where an author remembered, because
    a run that quietly acquired a credential would start calling a real vendor
    while still reporting itself as an isolated harness run.
    """
    name = "safety_and_policy"
    checks = list(data.of_kind("prohibited"))
    credential, credential_detail = read_prohibited(
        "a_model_credential_was_configured", data.ctx
    )
    if credential:
        return failed(
            name,
            "a model credential is configured, so this run was not isolated: "
            + credential_detail,
        )
    if not checks:
        return cannot_compute(
            name,
            "this scenario declared no prohibited outcome. Safety is asserted "
            "as an ABSENCE, and an absence nobody named is one nobody checked.",
        )
    return verdict(
        name,
        tuple(checks),
        nothing_to_judge="unreachable: the empty case is handled above",
        all_held=(
            f"{len(checks)} prohibited outcome(s) did not occur, over "
            f"{len(data.ctx.observations)} real request(s)"
        ),
        some_failed="a prohibited outcome occurred",
    )


# ── Resilience ───────────────────────────────────────────────────────────────


async def degradation_honesty(data: EvaluationInput) -> EvaluationOutcome:
    """Did the system degrade the way it SAID it would, and record that it had.

    HARNESS.md section 4: "The point is never that the system survived. It is
    that the system DEGRADED THE WAY IT SAID IT WOULD ... Silent survival is a
    finding, not a pass."

    So a run with a fault and no recorded degradation FAILS, and it fails
    loudly, because that is the state in which a product is quietly returning
    template output as generation (rule 6) or a lexical pass as a cross-encoder
    rerank. Every degradation carries the SIGNAL it was read from, so a reader
    can go and check it rather than taking the harness's word.
    """
    name = "degradation_honesty"
    if data.ctx.fault_layer_absent:
        return cannot_compute(
            name,
            "the fault layer is not available "
            f"({data.ctx.fault_layer_absent}), so no fault was injected and "
            "nothing about degradation was measured",
        )
    if not data.faults_applied:
        return cannot_compute(
            name,
            "no fault was injected, so there was nothing to degrade from. A "
            "pass here would claim resilience this run never demonstrated.",
        )
    if not data.ctx.degradations:
        return failed(
            name,
            f"{len(data.faults_applied)} fault(s) were injected "
            f"({', '.join(data.faults_applied)}) and the run recorded no "
            "degradation at all. The system survived silently, which is a "
            "finding rather than a pass: a degradation nobody recorded is "
            "indistinguishable from a degradation that was never needed.",
            faults=list(data.faults_applied),
        )
    unsigned = [item for item in data.ctx.degradations if not item.signal.strip()]
    if unsigned:
        return failed(
            name,
            f"{len(unsigned)} degradation(s) were recorded with no signal "
            "naming where the admission came from, which is an assertion "
            "rather than evidence",
        )
    return passed(
        name,
        f"{len(data.faults_applied)} fault(s) produced "
        f"{len(data.ctx.degradations)} recorded degradation(s): "
        + "; ".join(
            f"{item.kind} via {item.signal}" for item in data.ctx.degradations[:4]
        ),
        faults=list(data.faults_applied),
        degradations=[item.as_dict() for item in data.ctx.degradations],
    )


async def recovery(data: EvaluationInput) -> EvaluationOutcome:
    """Once the fault is gone, does the system work again.

    HARNESS.md's sixth question is "can we prove recovery, rather than assume
    it", and the proof is a request made AFTER the fault has unwound. The
    runner makes it outside the fault block, so a product that had latched a
    breaker, cached a failure or left a client in a bad state answers here
    rather than in the next scenario, where it would read as an unrelated
    flake.
    """
    name = "recovery"
    if data.ctx.fault_layer_absent:
        return cannot_compute(
            name,
            "the fault layer is not available "
            f"({data.ctx.fault_layer_absent}), so nothing was broken and "
            "recovery could not be observed",
        )
    if not data.faults_applied:
        return cannot_compute(
            name,
            "no fault was injected, so there was nothing to recover from",
        )
    probe = data.ctx.facts.get("recovery_probe")
    if not isinstance(probe, Mapping):
        return cannot_compute(
            name,
            "the runner recorded no post-fault probe, so recovery was never "
            "asked about",
        )
    status = probe.get("status")
    if status != 200:
        return failed(
            name,
            f"after the fault unwound, the readiness probe answered {status}. "
            "The system did not return to service on its own, which makes the "
            "fault a state change rather than an interruption.",
            probe=dict(probe),
        )
    return passed(
        name,
        "the readiness probe answered 200 after every fault had unwound, from "
        "the same process that had just been failing",
        probe=dict(probe),
    )


# ── Efficiency ───────────────────────────────────────────────────────────────

#: The per-tier wall-clock budgets of HARNESS.md section 9, in seconds, applied
#: to ONE scenario's requests rather than to the tier. A tier budget divided by
#: a scenario count would move every time a scenario was added, which is the
#: one thing a budget must not do.
#:
#: `performance` is deliberately absent: section 9 gives it an unbounded
#: budget, and an evaluator that invented one would be setting a threshold
#: nobody agreed to.
_TIER_BUDGET_SECONDS: Mapping[Tier, float] = {
    Tier.SMOKE: 60.0,
    Tier.REGRESSION: 300.0,
    Tier.INTEGRATION: 600.0,
    Tier.ADVERSARIAL: 180.0,
    Tier.SAFETY: 180.0,
}


async def latency(data: EvaluationInput) -> EvaluationOutcome:
    """How long the product took, with the dispersion that qualifies it.

    A MEAN WITH ITS INTERVAL, never a bare number. One slow request in a
    scenario of three is not a latency regression, and a point estimate with no
    dispersion beside it is what turns noise into a decision -- the argument
    `metrics.Measurement` already makes and refuses to let a caller skip.

    The verdict is against the TIER budget, which is the only latency number
    anybody has agreed to. It is a wall-clock ceiling for the whole scenario
    rather than a per-request one, because a scenario that makes forty cheap
    requests and one that makes two expensive ones are both held to what
    section 9 says the tier may cost.
    """
    name = "latency"
    samples = [item.elapsed_ms for item in data.ctx.observations]
    if not samples:
        return cannot_compute(
            name, "no request was made, so there is no latency to report"
        )
    budget = _TIER_BUDGET_SECONDS.get(data.scenario.tier)
    total_ms = sum(samples)
    try:
        interval = mean_interval(samples)
    except InsufficientData as exc:
        # One sample has no dispersion, so the mean is reported as the total
        # instead and the verdict still stands on the budget. Reporting a
        # zero-width interval would claim a precision a single measurement
        # does not have.
        if budget is None:
            return cannot_compute(
                name,
                f"tier {data.scenario.tier.value} has no agreed budget and a "
                f"single request has no dispersion to report ({exc})",
                total_ms=round(total_ms, 1),
            )
        over = total_ms > budget * 1000.0
        return (failed if over else passed)(
            name,
            f"one request, {total_ms:.0f}ms in total, against the "
            f"{budget:.0f}s budget for tier {data.scenario.tier.value}",
            total_ms=round(total_ms, 1),
            budget_seconds=budget,
        )
    if budget is None:
        return cannot_compute(
            name,
            f"tier {data.scenario.tier.value} has an unbounded budget "
            "(HARNESS.md section 9), so there is no ceiling to judge against. "
            f"Observed: {len(samples)} request(s), {total_ms:.0f}ms in total, "
            f"mean {statistics.fmean(samples):.0f}ms.",
            total_ms=round(total_ms, 1),
            requests=len(samples),
        )
    over = total_ms > budget * 1000.0
    return EvaluationOutcome(
        evaluator=name,
        outcome="fail" if over else "pass",
        reason=(
            f"{len(samples)} request(s) took {total_ms:.0f}ms in total against "
            f"the {budget:.0f}s budget for tier {data.scenario.tier.value}"
        ),
        score=statistics.fmean(samples),
        interval=(interval.low, interval.high),
        sample_size=len(samples),
        details={
            "total_ms": round(total_ms, 1),
            "budget_seconds": budget,
            "slowest": max(samples),
        },
    )


async def cost(data: EvaluationInput) -> EvaluationOutcome:
    """What this run SPENT: credits charged, and model calls attempted.

    Credits are read from the ledger through the second connection, because a
    charge that answered 200 and rolled back is exactly the shape of bug this
    whole harness is built around, and a cost figure taken from a response body
    would report money the customer was never charged.

    Model calls are counted from the run's own record rather than billed,
    because no credential is configured (HARNESS.md section 1): the useful
    number here is how many times the product REACHED for a model, which is
    what a cost regression looks like before it reaches an invoice.
    """
    name = "cost"
    if data.reader is None:
        return cannot_compute(
            name,
            "no second connection was opened, so the credit ledger could not "
            "be read and spend is unknown",
        )
    if "tenant" not in data.ctx.world.ids:
        return cannot_compute(
            name,
            "this world seeds no tenant, so there is no ledger to read and no "
            "spend to report",
        )
    spent = await data.reader.scalar(
        "SELECT COALESCE(-SUM(subunits_delta), 0) FROM credit_ledger "
        "WHERE tenant_id = :t AND subunits_delta < 0",
        {"t": str(data.ctx.world.id("tenant"))},
    )
    model_calls = len(
        [stage for stage in data.ctx.trajectory if stage.startswith("model_call_")]
    )
    return passed(
        name,
        f"{int(spent or 0)} credit sub-unit(s) were charged and the product "
        f"reached for a model {model_calls} time(s)",
        subunits_charged=int(spent or 0),
        credits_charged=round(int(spent or 0) / 60.0, 4),
        model_calls=model_calls,
        dispatches=len(_recorded_names()),
    )


def _recorded_names() -> list[str]:
    from app.workers import dispatch

    return dispatch.recorded_names()


EVALUATORS: Mapping[str, Callable[[EvaluationInput], Awaitable[EvaluationOutcome]]] = {
    "functional_correctness": functional_correctness,
    "state_correctness": state_correctness,
    "tenancy_isolation": tenancy_isolation,
    "no_numbers_to_client": no_numbers_to_client,
    "safety_and_policy": safety_and_policy,
    "degradation_honesty": degradation_honesty,
    "recovery": recovery,
    "latency": latency,
    "cost": cost,
}
