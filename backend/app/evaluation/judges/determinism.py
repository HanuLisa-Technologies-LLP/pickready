"""RPN-AI-UP-001 W7.2. Does a juror agree with ITSELF, and how often not?

Run it with `python -m app.scripts.probe_judge_determinism`, which is a CLI over
this module and holds no logic of its own.

WHY THIS RUNS BEFORE ANY JUDGE CLIENT ANSWERS
-----------------------------------------------
W7.2 requires this probe to be executed and recorded BEFORE a judge is wired,
and the ordering is not ceremony. A judge's agreement with a human is compared
against a THRESHOLD in W8's release gate, and a threshold has to be set
somewhere. Set it above the model's own self-disagreement and the gate fires on
noise; set it below and the gate passes anything. The only defensible number is
one measured from the model, on this task, through this transport.

`jury.configured_jurors()` says the same thing from the other side. Until this
has run and been recorded, it returns an empty tuple, because a stub juror would
produce verdicts, those verdicts would produce a kappa, and the kappa would be a
number describing nothing while looking exactly like a measurement.

WHAT IT MEASURES, AND WHY BOTH ARMS
------------------------------------
Two arms per model, `repeats` calls each over the same fixed cases:

  1. TEMPERATURE 0.0, no seed. The naive assumption every judging pipeline
     makes. This platform has been burned by exactly that assumption once
     already: `gpt-5.6-terra` and `gpt-5.6-luna` REFUSE temperature 0.0
     outright (400 `unsupported_parameter`), which cost the product a stated
     determinism guarantee it believed it held for a whole phase.

  2. TEMPERATURE 0.0 WITH A FIXED SEED. Whether the seed buys anything over the
     first arm is what decides how a judge reports reproducibility: on a seed,
     or on repeats with reported dispersion.

SIGMA IS A DISAGREEMENT RATE, NOT A STANDARD DEVIATION OF A SCORE
------------------------------------------------------------------
The verdicts are CATEGORICAL, so a standard deviation over them would be
arithmetic on labels with no distance between them. What is reported is the
share of calls that differ from the case's own modal verdict, per case and
pooled. That is the quantity W8 needs: how much of a measured disagreement
between a judge and a human is the judge disagreeing with itself.

A run where every case is unanimous reports 0.0 and says so; it does NOT report
"deterministic", because twenty calls is twenty calls and the vendor documents
neither arm as a guarantee. The same honesty `verify_live.py` applies to
`system_fingerprint` coming back null.

AND A RUN THAT MEASURED NOTHING REPORTS `unavailable`, NEVER 0.0
------------------------------------------------------------------
Only calls that returned a verdict on the scale count toward the rate. An error
and an off-scale answer are calls that produced no verdict, and `usable_share`
travels beside every sigma so a reader knows how much of the run the number
rests on. This is not hypothetical: the probe's first run reported sigma=0.0000
with every case unanimous, out of five cases of nothing but HTTP 404s from a
model that had been retired.

NO CANDIDATE TEXT IS SENT
--------------------------
The probe cases are synthetic and written here. Sending a real candidate's
resume to a third vendor to measure a sampler would be an egress decision
nobody made.
"""
from __future__ import annotations

import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.evaluation.judges import gemini

#: A fixed seed, so the second arm is reproducible across runs of the probe
#: itself. Any constant would do; what matters is that it does not move.
PROBE_SEED = 20260909

#: Baseline pace between calls, chosen against the free tier's per-key
#: per-minute meter divided across the rotating keys, so the steady state does
#: not rely on the retry path at all. The retry path is the safety net, not the
#: plan: a probe that only completes because it backs off constantly is
#: measuring the meter again, more slowly.
PACING_SECONDS = 4.0

#: The scale the probe cases are judged on. Four grades, matching the product's
#: own vocabulary, so the measured dispersion is dispersion over a scale of the
#: shape a real judge would use. A binary scale would understate it: fewer
#: labels means fewer ways to disagree.
SCALE: tuple[str, ...] = (
    "highly_matching",
    "matching",
    "moderately_matching",
    "not_matching",
)

INSTRUCTION = (
    "You are grading how well a candidate's stated evidence supports a single "
    "hiring requirement. Reply with exactly one word from this list and nothing "
    "else: " + ", ".join(SCALE) + "."
)


@dataclass(frozen=True)
class ProbeCase:
    """One requirement and one piece of evidence, written to be judgeable.

    Deliberately spread across the scale, including cases near a band boundary.
    A probe made only of obvious cases measures the model's agreement with
    itself on questions it was never going to waver on, and reports a
    reassuring sigma that the real workload will not reproduce.
    """

    case_id: str
    requirement: str
    evidence: str

    def prompt(self) -> str:
        return (
            f"{INSTRUCTION}\n\n"
            f"REQUIREMENT: {self.requirement}\n"
            f"CANDIDATE EVIDENCE: {self.evidence}\n\n"
            "One word:"
        )


PROBE_CASES: tuple[ProbeCase, ...] = (
    ProbeCase(
        "clear-strong",
        "Has owned a production Kafka cluster end to end.",
        "Ran a nine-broker Kafka cluster for three years, owned partition "
        "rebalancing, on-call rotation and two major version upgrades.",
    ),
    ProbeCase(
        "clear-absent",
        "Has owned a production Kafka cluster end to end.",
        "Managed a retail storefront team of six and handled weekly inventory "
        "reconciliation in a spreadsheet.",
    ),
    ProbeCase(
        "boundary-thin",
        "Has led a team of engineers through a delivery under deadline.",
        "Was the most senior engineer on a three-person project and coordinated "
        "the work, though the reporting line stayed with the manager.",
    ),
    ProbeCase(
        "boundary-adjacent",
        "Has built data pipelines at scale.",
        "Built nightly ETL jobs in Airflow moving a few million rows, and "
        "helped a colleague debug a Spark job once.",
    ),
    ProbeCase(
        "claim-without-detail",
        "Has deep experience with distributed systems.",
        "Expert in distributed systems, microservices and cloud-native "
        "architecture. Passionate about scalability.",
    ),
)


@dataclass
class ArmResult:
    """One model, one arm, over every case."""

    model: str
    arm: str
    per_case: dict[str, list[str]] = field(default_factory=dict)

    def verdicts(self, case_id: str) -> list[str]:
        """Only the answers that are actually verdicts.

        An `error:` or `off_scale:` answer is a call that produced NO verdict.
        Counting it as one would let a model that failed half its calls and
        answered the rest identically report perfect self-agreement, which is
        the direction this probe must never be wrong in.
        """
        return [a for a in self.per_case[case_id] if a in SCALE]

    def case_disagreement(self, case_id: str) -> float | None:
        """Share of this case's VERDICTS that differ from its modal verdict.

        None, never 0.0, when the case produced no verdict at all. A number
        meaning "nothing was measured" and a number meaning "nothing disagreed"
        must not be the same number. Same rule `eval_agents.py` follows when it
        reports quality as UNAVAILABLE rather than as 0.0.
        """
        answers = self.verdicts(case_id)
        if not answers:
            return None
        modal_count = Counter(answers).most_common(1)[0][1]
        return (len(answers) - modal_count) / len(answers)

    def pooled_disagreement(self) -> float | None:
        """The mean over MEASURABLE cases. The number W8's threshold uses."""
        rates = [
            rate
            for rate in (self.case_disagreement(c) for c in self.per_case)
            if rate is not None
        ]
        return statistics.fmean(rates) if rates else None

    def usable_share(self) -> float:
        """Fraction of all calls in this arm that produced a real verdict."""
        total = sum(len(a) for a in self.per_case.values())
        if not total:
            return 0.0
        return sum(len(self.verdicts(c)) for c in self.per_case) / total

    def unanimous_cases(self) -> int:
        """Cases whose verdicts were unanimous. An unmeasurable case is not."""
        return sum(
            1
            for case_id in self.per_case
            if self.verdicts(case_id) and len(set(self.verdicts(case_id))) == 1
        )

    def measurable_cases(self) -> int:
        return sum(1 for case_id in self.per_case if self.verdicts(case_id))

    def as_dict(self) -> dict[str, object]:
        pooled = self.pooled_disagreement()
        return {
            "model": self.model,
            "arm": self.arm,
            "pooled_disagreement": None if pooled is None else round(pooled, 4),
            "usable_share": round(self.usable_share(), 4),
            "unanimous_cases": self.unanimous_cases(),
            "measurable_cases": self.measurable_cases(),
            "total_cases": len(self.per_case),
            "per_case": {
                case_id: {
                    "disagreement": (
                        None
                        if (rate := self.case_disagreement(case_id)) is None
                        else round(rate, 4)
                    ),
                    "distribution": dict(Counter(answers)),
                }
                for case_id, answers in self.per_case.items()
            },
        }


def run_arm(
    model: str, arm: str, repeats: int, keys: Sequence[str], *, seed: int | None
) -> ArmResult:
    result = ArmResult(model=model, arm=arm)
    rotation = 0
    for case in PROBE_CASES:
        answers: list[str] = []
        for _ in range(repeats):
            answers.append(
                gemini.call(
                    model,
                    case.prompt(),
                    keys,
                    seed=seed,
                    scale=SCALE,
                    offset=rotation,
                )
            )
            rotation += 1
            time.sleep(PACING_SECONDS)
        result.per_case[case.case_id] = answers
        rate = result.case_disagreement(case.case_id)
        shown = "unmeasurable" if rate is None else f"{rate:.3f}"
        print(
            f"  {model} [{arm}] {case.case_id}: disagreement={shown} "
            f"{dict(Counter(answers))}",
            file=sys.stderr,
            flush=True,
        )
    return result


def run_probe(models: Sequence[str], repeats: int) -> tuple[dict, list[ArmResult]]:
    """Both arms over every model. Returns the printable record and the arms."""
    keys = gemini.credentials()
    print(f"credential slots populated: {len(keys)}", file=sys.stderr, flush=True)
    results: list[ArmResult] = []
    for model in models:
        for arm, seed in (
            ("temperature_0", None),
            ("temperature_0_seeded", PROBE_SEED),
        ):
            print(f"{model} [{arm}] ...", file=sys.stderr, flush=True)
            results.append(run_arm(model, arm, repeats, keys, seed=seed))
    record = {
        "probe": "RPN-AI-UP-001 W7.2 judge determinism",
        "repeats": repeats,
        "cases": len(PROBE_CASES),
        "scale": list(SCALE),
        "seed": PROBE_SEED,
        "arms": [result.as_dict() for result in results],
    }
    return record, results
