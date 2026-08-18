"""The agent framework's evaluation gate. Offline, stubbed, and CI-runnable.

RELATIONSHIP TO `eval_interview.py`
-----------------------------------
That script evaluates the conversational agent's JUDGEMENT across a labelled set
and CI already gates on it. This one evaluates the FRAMEWORK the other agents
now run inside: that the routing table agrees with the permission matrix, that
every registered tool is reachable, that the verifiers still reject what they
were built to reject, and that nine specific past defects are still fixed.

Both are fully offline and call no provider, for the reason stated in
`eval_interview`: a rate that moves must mean the CODE changed, not that a model
sampled differently.

WHAT IT DOES NOT CLAIM
----------------------
It does not measure whether a real model writes a GOOD report. That needs a live
model and a recruiting expert, and the dataset section says so out loud whenever
the labelled set is absent, which today it is. Reporting an unmeasurable quality
figure as 0.0 would be worse than reporting nothing.

EXIT CODE
---------
Non-zero when a regression case fails or a structural invariant breaks. That is
what makes it a gate rather than a report.
"""
from __future__ import annotations

import json
import sys

from app.evaluation import dataset, regression
from app.orchestration_checks import structural_invariants


def main() -> int:
    report: dict[str, object] = {}

    # ── Regression cases ─────────────────────────────────────────────────────
    results = regression.run_all()
    report["regression"] = regression.summary(results)

    # ── Structural invariants ────────────────────────────────────────────────
    problems = structural_invariants()
    report["structural"] = {"problems": problems, "ok": not problems}

    # ── Dataset coverage ─────────────────────────────────────────────────────
    cases = dataset.load()
    coverage = dataset.stratification_report(cases)
    report["dataset"] = coverage
    # Absent labels are reported, never scored. A quality metric computed
    # against no ground truth is a number that means nothing and looks like
    # something.
    report["quality_metrics"] = (
        "UNAVAILABLE: no expert-labelled evaluation set is present. "
        "Structural metrics above are unaffected."
        if not cases
        else "available"
    )

    print(json.dumps(report, indent=2, default=str))

    failed = report["regression"]["failed"] or problems  # type: ignore[index]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
