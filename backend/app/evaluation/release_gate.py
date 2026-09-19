"""RPN-AI-UP-001 W8. The release gate, and the measurement its threshold rests on.

WHY THIS FILE COULD NOT BE WRITTEN UNTIL 2026-09-09
-----------------------------------------------------
A gate is a threshold, and a threshold has to come from somewhere. Set it above
the judge's own self-disagreement and it fires on noise until somebody disables
it; set it below and it passes anything. W7.2 exists to supply that number, and
until the determinism probe completed there was nothing to write here but a
guess wearing a constant's name.

THE MEASUREMENT, IN FULL
--------------------------
`app/scripts/probe_judge_determinism.py --vendor groq --repeats 20`: six arms,
five cases each, twenty calls per case. Recorded in
`docs/verification/VERIFICATION_RESULTS.md`.

    model                 arm                    pooled   worst case
    qwen/qwen3.8-27b      temperature_0          0.0000   0.000
    qwen/qwen3.8-27b      temperature_0 + seed   0.0000   0.000
    openai/gpt-oss-120b   temperature_0          0.0300   0.150
    openai/gpt-oss-120b   temperature_0 + seed   0.0000   0.000
    openai/gpt-oss-20b    temperature_0          0.0200   0.100
    openai/gpt-oss-20b    temperature_0 + seed   0.0100   0.100

THREE THINGS IT SETTLED, AND ONE IT REFUSED TO
------------------------------------------------
**A SEED IS NOT DETERMINISM.** It helps and it does not guarantee.
`gpt-oss-120b` went 0.0300 to 0.0000 with a seed, and `gpt-oss-20b` still
disagreed with itself at 0.0100 WITH one. So reproducibility rests on REPEATS
WITH REPORTED DISPERSION, never on a seed alone. That is the same answer this
platform already reached for its own models, where `temperature=0.0` is refused
outright and `system_fingerprint` came back null: the vendor documents best
effort, and best effort is not something to gate on.

**DISPERSION LIVES AT BAND BOUNDARIES.** Every non-zero cell above is a boundary
case. `clear-strong`, `clear-absent` and `claim-without-detail` were twenty for
twenty on every model in every arm. A gate calibrated on obvious cases would be
calibrated on the wrong distribution, which is why the probe's cases were
written to straddle boundaries.

**A PANEL IS STEADIER THAN ITS MEMBERS.** The worst single juror moved on 3 of
20 calls for one case; a majority vote over three needs two to move together
before the pooled label does.

**IT DID NOT SETTLE JUDGE QUALITY, AND NEITHER DOES THIS GATE.** Self-agreement
is not accuracy: a model answering `matching` every time agrees with itself
perfectly. The reasoning and decision sets stay empty until a human labels them,
and while they are empty this gate reports UNAVAILABLE rather than a pass.

UNAVAILABLE IS NOT A PASS
---------------------------
The single most important line here. `eval_agents.py` already refuses to report
an unmeasurable quality figure as 0.0, because a number meaning nothing looks
exactly like a number meaning something. A gate has the same failure with higher
stakes: a metric that could not be computed must BLOCK, or the first thing a
broken evaluation harness does is wave every release through while showing
green.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The worst POOLED self-disagreement any juror showed in the W7.2 run above.
#: Pooled rather than worst-case, because this gate reads a judge's score over a
#: whole SET and the pooled figure is the set-level quantity.
MEASURED_SELF_DISAGREEMENT = 0.03

#: The worst SINGLE-CASE self-disagreement observed. Not the threshold, kept
#: because it says where the noise actually is: one boundary case on one model
#: moved three times in twenty.
MEASURED_WORST_CASE_DISAGREEMENT = 0.15

#: How much worse than the judge's own noise a fall must be before it counts as
#: a regression. Three times, which is a CHOICE and is written down as one: at
#: 1x the gate fires on the judge disagreeing with itself, and much past 5x a
#: real regression on a small set hides underneath it. Revisit it when the probe
#: is re-run, never by feel after a red build.
NOISE_MULTIPLIER = 3.0

#: The band inside which a fall in agreement is indistinguishable from the
#: judge's own variance. DERIVED, not typed: a gate constant somebody typed is a
#: gate constant somebody will retype.
NOISE_BAND = MEASURED_SELF_DISAGREEMENT * NOISE_MULTIPLIER

#: Outcomes. UNAVAILABLE is a distinct outcome and never a synonym for either of
#: the others, which is the entire reason there are three.
PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"

#: The outcomes that let a release proceed. Exactly one, ENUMERATED, so a fourth
#: outcome added later cannot quietly become permissive: a new name is not in
#: this set until somebody puts it there deliberately.
RELEASING_OUTCOMES: frozenset[str] = frozenset({PASS})


@dataclass(frozen=True)
class GateOutcome:
    """What the gate decided, and everything needed to argue with it."""

    outcome: str
    reason: str
    #: The metric read, when one was readable. None when nothing was measured.
    observed: float | None = None
    #: What it was compared against.
    floor: float | None = None
    #: The judge's own noise at the time of the decision, carried so a reader
    #: does not have to go and find the probe to learn whether a four-point fall
    #: meant anything.
    noise_band: float = NOISE_BAND
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def releasable(self) -> bool:
        return self.outcome in RELEASING_OUTCOMES

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "observed": self.observed,
            "floor": self.floor,
            "noise_band": self.noise_band,
            "releasable": self.releasable,
            "details": dict(self.details),
        }


def _read_agreement(result: Any) -> tuple[float | None, str]:
    """Pull the chance-corrected agreement out of a `JudgeResult`.

    MCC first, then Cohen's kappa. RAW AGREEMENT IS NEVER USED and its absence
    is deliberate: it overstates chance-corrected agreement by a mean of 38.6
    points, so a gate reading it would pass releases a kappa would refuse.

    Returns `(None, why)` when neither is a number, which is the ordinary state
    while the human-labelled sets are empty.
    """
    payload = result.as_dict() if hasattr(result, "as_dict") else dict(vars(result))
    for name in ("mcc", "cohens_kappa"):
        value = payload.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value), name
        if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
            return float(value["value"]), name
    return None, "neither mcc nor cohens_kappa was a number"


def evaluate(
    result: Any,
    *,
    floor: float,
    baseline: float | None = None,
) -> GateOutcome:
    """Decide whether `result` clears `floor`, and whether any fall is real.

    `floor` is supplied by the CALLER rather than declared here, because the bar
    a judge must clear is a product decision that differs per set; burying it in
    this module would make it look like a property of the mathematics.

    `baseline` is the previous release's figure when there is one. A fall inside
    `NOISE_BAND` is REPORTED and does not fail: that is the judge disagreeing
    with itself, measured, not a regression. A fall wider than the band fails
    even when the absolute value still clears the floor, because a system that
    genuinely got worse should not be waved through for having started high.
    """
    observed, source = _read_agreement(result)
    if observed is None:
        # THE MOST IMPORTANT BRANCH IN THE FILE. Not a pass, not a zero.
        return GateOutcome(
            outcome=UNAVAILABLE,
            reason=(
                f"no chance-corrected agreement to gate on: {source}. This is "
                "the expected state while the reasoning and decision sets are "
                "empty, because they must be human labelled and ground truth "
                "written by the same class of model being evaluated measures "
                "agreement with that model. An unmeasured gate BLOCKS: a metric "
                "reported as 0.0, or waved through as a pass, is how a broken "
                "harness releases everything while showing green."
            ),
            floor=floor,
        )

    if observed < floor:
        return GateOutcome(
            outcome=FAIL,
            reason=f"{source} {observed:.4f} is below the floor {floor:.4f}",
            observed=observed,
            floor=floor,
            details={"metric": source},
        )

    if baseline is not None:
        drop = baseline - observed
        if drop > NOISE_BAND:
            return GateOutcome(
                outcome=FAIL,
                reason=(
                    f"{source} fell {drop:.4f} from {baseline:.4f}, wider than "
                    f"the {NOISE_BAND:.4f} band the judge's own measured "
                    f"self-disagreement explains. It clears the floor and it "
                    f"still got worse."
                ),
                observed=observed,
                floor=floor,
                details={"metric": source, "baseline": baseline, "drop": drop},
            )
        if drop > 0:
            return GateOutcome(
                outcome=PASS,
                reason=(
                    f"{source} {observed:.4f} clears the floor {floor:.4f}. It "
                    f"fell {drop:.4f} from {baseline:.4f}, inside the "
                    f"{NOISE_BAND:.4f} band explained by the judge's own "
                    f"measured self-disagreement, so it is reported not failed."
                ),
                observed=observed,
                floor=floor,
                details={"metric": source, "baseline": baseline, "drop": drop},
            )

    return GateOutcome(
        outcome=PASS,
        reason=f"{source} {observed:.4f} clears the floor {floor:.4f}",
        observed=observed,
        floor=floor,
        details={"metric": source},
    )
