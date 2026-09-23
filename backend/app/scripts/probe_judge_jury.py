"""Does the JURY MACHINERY work, end to end, against real models?

    python -m app.scripts.probe_judge_jury [--json]

WHAT THIS PROVES, AND THE MUCH LARGER THING IT DOES NOT
---------------------------------------------------------
It proves the pipeline: a real panel is constructed from real credentials, each
juror makes a real call, verdicts are pooled by majority with ties abstaining,
and `build_result` turns them into MCC, Cohen's kappa, a confusion matrix and an
accuracy INTERVAL. Every one of those steps had only ever run against a fake
before this script existed.

It proves NOTHING about judge quality, and that distinction is why this is a
script and not a dataset. **The cases below are SYNTHETIC.** W7.1's rule is
absolute and this does not bend it: ground truth produced by the same class of
model being evaluated measures agreement with that model, not quality. The
reasoning and decision sets under `app/evaluation/datasets/` stay EMPTY until a
human labels them, and this script writes nothing into them. High agreement here
means the plumbing works and the cases were easy. It is not evidence about
candidates.

WHY THE CASES LIVE HERE AND NOT IN THE DATASET DIRECTORY
-----------------------------------------------------------
A synthetic case in `datasets/reasoning/cases.json` would be indistinguishable
from a human-labelled one six months from now, and `golden.py` reports
`human_verified` counts that something would then be quietly inflating. A
fabricated case that lives in a file named `probe` cannot be mistaken for ground
truth by a loader, a report, or a reader.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from app.evaluation.judges import jury
from app.evaluation.judges.protocol import (
    ABSTENTION_HANDLING,
    AGGREGATION,
    POSITION_HANDLING,
    JudgeProtocol,
)

SCALE = jury.DEFAULT_SCALE


@dataclass(frozen=True)
class SyntheticCase:
    """A `JudgedCase`-shaped case that is honest about being invented.

    It carries `requirement` and `evidence` directly, which is what
    `ModelJuror.verdict` reads. A real `JudgedCase` carries a `payload_ref`
    instead, because a human-labelled case points at a stored evidence pack
    rather than inlining one.

    `provenance` says `synthetic` and `human_verified` is False, in the same
    field names the real type uses, so anything that ever consumed both would
    SEE the difference rather than have to know it.
    """

    case_id: str
    requirement: str
    evidence: str
    expected_label: str
    payload_ref: str = "synthetic"
    provenance: str = "synthetic"
    human_verified: bool = False
    notes: str = ""


#: Deliberately spread across the scale, including two near a band boundary. A
#: probe made only of obvious cases measures the panel's agreement on questions
#: it was never going to disagree about.
CASES: tuple[SyntheticCase, ...] = (
    SyntheticCase(
        "strong-direct",
        "Has owned a production Kafka cluster end to end.",
        "Ran a nine-broker Kafka cluster for three years, owned partition "
        "rebalancing, the on-call rotation and two major version upgrades.",
        "highly_matching",
    ),
    SyntheticCase(
        "absent",
        "Has owned a production Kafka cluster end to end.",
        "Managed a retail storefront team of six and handled weekly inventory "
        "reconciliation in a spreadsheet.",
        "not_matching",
    ),
    SyntheticCase(
        "partial-leadership",
        "Has led a team of engineers through a delivery under deadline.",
        "Was the most senior engineer on a three-person project and coordinated "
        "the work, though the reporting line stayed with the manager.",
        "moderately_matching",
    ),
    SyntheticCase(
        "adjacent-scale",
        "Has built data pipelines at scale.",
        "Built nightly Airflow jobs moving a few million rows, and helped a "
        "colleague debug a Spark job once.",
        "moderately_matching",
    ),
    SyntheticCase(
        "claim-no-evidence",
        "Has deep experience with distributed systems.",
        "Expert in distributed systems, microservices and cloud-native "
        "architecture. Passionate about scalability.",
        "not_matching",
    ),
    SyntheticCase(
        "solid-not-exceptional",
        "Has shipped and maintained a customer-facing web application.",
        "Maintained a Django storefront for two years, shipped features "
        "fortnightly and handled its production incidents.",
        "matching",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="W7.4/W7.5 jury pipeline probe")
    parser.add_argument("--json", action="store_true", help="indent the record")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="how many times the protocol states each case was judged",
    )
    args = parser.parse_args()

    jurors = jury.configured_jurors(SCALE)
    if not jurors:
        # Not a crash and not a zero. The REASON is the output.
        print("JURY UNAVAILABLE")
        print(jury.unavailable_reason())
        return 0

    print(f"panel of {len(jurors)}:", file=sys.stderr)
    for juror in jurors:
        print(f"  {juror.judge_id}", file=sys.stderr)

    protocol = JudgeProtocol(
        scale=tuple(SCALE),
        population=(
            "SYNTHETIC requirement-and-evidence pairs written for this probe. "
            "Not a candidate population and not human labelled. A judge "
            "validated on one benchmark is validated only there."
        ),
        abstention_handling=ABSTENTION_HANDLING[0],
        aggregation=AGGREGATION[0],
        position_handling=POSITION_HANDLING[0],
        repeats=args.repeats,
        judge_ids=tuple(juror.judge_id for juror in jurors),
    )

    # Per-case detail as it happens: a panel of three making a network call each
    # takes long enough that a silent run is indistinguishable from a hung one.
    for case in CASES:
        verdicts = [juror.verdict(case) for juror in jurors]
        pooled = jury.pool(case.case_id, verdicts, SCALE)
        agree = "OK  " if pooled.label == case.expected_label else "DIFF"
        print(
            f"  {agree} {case.case_id:<22} expected={case.expected_label:<20} "
            f"pooled={pooled.label:<20} members={verdicts}",
            file=sys.stderr,
            flush=True,
        )

    result = jury.judge_set(
        CASES, jurors, protocol, dataset_version="synthetic-probe", judge_label="jury"
    )

    print("JURY_PROBE_JSON_START")
    payload = result.as_dict() if hasattr(result, "as_dict") else vars(result)
    print(json.dumps(payload, indent=2 if args.json else None, default=str))
    print("JURY_PROBE_JSON_END")

    print(
        "\nTHESE CASES ARE SYNTHETIC. The numbers above describe the jury "
        "PIPELINE, never judge quality: ground truth written by the same class "
        "of model being evaluated measures agreement with that model. The "
        "reasoning and decision sets stay empty until a human labels them."
    )
    # A MEASUREMENT, not a gate. It returns 0 whatever the panel decided, for
    # the reason `ai_baseline.py` records.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
