"""The release gate, finally given a caller.

WHY THIS EVALUATOR EXISTS AT ALL
----------------------------------
`app/evaluation/release_gate.py` is calibrated against a real measurement (the
W7.2 probe: worst pooled self-disagreement 0.0300, so `NOISE_BAND` is that
times three rather than a guess), it is fully tested, and until now a
repository-wide search found it referenced by nothing except its own test.
HARNESS.md section 0 lists it under "already exists" with the harness's job
recorded as "WIRES IT IN, since nothing did".

A gate nothing calls is a gate nobody notices breaking, and the specific way
this one breaks is silent: somebody makes `UNAVAILABLE` permissive during a red
build, and from that moment the gate waves every release through while showing
green. That is the failure its own docstring names, so the property worth
gating on is the DECISION RULE and not any particular judge run.

WHAT THIS EVALUATOR JUDGES, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------------------
It judges the gate's three-way contract against three results whose answers are
known by construction: one carrying no chance-corrected agreement, one below
the floor, one clearing it. The first is the load-bearing one -- it must block,
not pass and not score zero.

It does NOT run a jury. The human-labelled reasoning and decision sets are
empty by design, because ground truth written by the same class of model being
evaluated measures agreement with that model rather than quality, and a gate
run against them would report `unavailable` forever. Making a deterministic
assertion depend on a live model is also the thing `eval_interview.py` already
refuses by being fully stubbed and offline: a rate that moves must mean the
CODE changed.

So the day a labelled set exists, this evaluator is unchanged and the gate it
protects is still the one that reads it.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.evaluation import release_gate

from harness.run import EvaluationOutcome

from .base import EvaluationInput, cannot_compute, failed, passed

__all__ = ["release_gate_contract"]

#: What each of the three probes must answer. Enumerated here rather than
#: derived from the gate, so that a change to the gate's behaviour breaks this
#: rather than being mirrored by it.
_REQUIRED: Mapping[str, str] = {
    "unmeasured": release_gate.UNAVAILABLE,
    "below_floor": release_gate.FAIL,
    "clearing": release_gate.PASS,
}


async def release_gate_contract(data: EvaluationInput) -> EvaluationOutcome:
    """Did the real gate block what it must block, and pass only what clears."""
    name = "release_gate"
    observed = data.ctx.facts.get("release_gate")
    if not isinstance(observed, Mapping):
        return cannot_compute(
            name,
            "this run did not exercise the release gate, so its decision rule "
            "was not observed. The `exercise_the_release_gate` workload step "
            "is what produces the three verdicts this reads.",
        )
    wrong: list[str] = []
    for key, required in _REQUIRED.items():
        answer = _outcome_of(observed.get(key))
        if answer != required:
            wrong.append(f"{key}: expected {required}, got {answer}")
    if wrong:
        return failed(
            name,
            "the release gate's decision rule has changed: "
            + "; ".join(wrong)
            + ". An UNAVAILABLE that stops blocking is how a broken evaluation "
            "harness releases everything while showing green.",
            observed=dict(observed),
        )
    if not observed.get("unmeasured", {}).get("releasable") is False:
        return failed(
            name,
            "the gate reported an unmeasured result as releasable, which is "
            "the one outcome it exists to refuse",
            observed=dict(observed),
        )
    return passed(
        name,
        "the release gate blocked an unmeasured result, failed one below the "
        f"floor and passed one clearing it, against a measured noise band of "
        f"{release_gate.NOISE_BAND:.4f}",
        observed=dict(observed),
        noise_band=release_gate.NOISE_BAND,
    )


def _outcome_of(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("outcome"))
    return "nothing was recorded"
