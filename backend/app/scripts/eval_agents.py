"""The agent framework's evaluation gate. Offline, stubbed, and CI-runnable.

RELATIONSHIP TO `eval_interview.py`
-----------------------------------
That script evaluates the conversational agent's JUDGEMENT across a labelled set
and CI already gates on it. This one evaluates the FRAMEWORK the other agents
now run inside: that every tool grant names a registered tool and every
registered tool is held and is a bounded read, that the verifiers still reject
what they were built to reject, that specific past defects are still fixed, and
that NO AGENT NAME POINTS AT CODE NOTHING REACHES.

WHAT IT STOPPED MEASURING, AND WHY
----------------------------------
It used to report the orchestration router's routing table and the activation
frontier of a stage-module table. Both lived in an orchestration package that
no route and no worker reached, deleted in the Vivekium release. A measure of
code nothing runs is a number about nothing, so the measures went with it. The
agent activation report below reads the live identity table against the live
import graph, which is the question the frontier was standing in for.

That last one is new and is the defect it was added for. Every Part A agent name
resolved to the module Part A was replacing, while the three-layer framework was
imported by no route and no worker. Logs and A2A artifacts showed Bodha, Sutra,
Yukti, Vaada, Miti and Siddhi succeeding; none of them was running. The whole
unit suite was green throughout, because no test of a module can ask whether a
request handler can reach it.

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
from app.import_graph import reachable_modules, structural_invariants
from app.services.agents import identity


def main() -> int:
    report: dict[str, object] = {}

    # ── Regression cases ─────────────────────────────────────────────────────
    results = regression.run_all()
    report["regression"] = regression.summary(results)

    # ── Structural invariants ────────────────────────────────────────────────
    problems = structural_invariants()
    report["structural"] = {"problems": problems, "ok": not problems}

    # ── Part A activation ────────────────────────────────────────────────────
    #
    # WHY THIS IS IN THE GATE AND NOT ONLY IN A TEST. For a whole phase every
    # agent name in `identity.py` pointed at the OLD module while the
    # three-layer framework was imported by no route and no worker, so logs and
    # A2A artifacts showed all six agents succeeding and Part A ran nowhere. No
    # unit test could see it: the question is not whether a module is correct
    # but whether a request handler can get to it. `structural_invariants`
    # answers it above; this section REPORTS it, so a reader of the gate's
    # output can see which stages are live rather than inferring it from a
    # passing exit code.
    reachable = reachable_modules()
    report["activation"] = {
        "reachable_module_count": len(reachable),
        "agents": identity.activation_status(reachable),
    }

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
