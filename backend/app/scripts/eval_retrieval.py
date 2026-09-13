"""The retrieval eval gate. Fully offline, judge free, arithmetic only.

    python -m app.scripts.eval_retrieval [--gate] [--run RUN_ID] [--json]

RELATIONSHIP TO THE OTHER EVAL SCRIPTS
---------------------------------------
`eval_interview.py` evaluates the conversational agent's judgement across a
labelled set. `eval_agents.py` evaluates the FRAMEWORK the agents run inside.
This one evaluates RETRIEVAL, and it is the only one of the three whose ground
truth needs no expert: a query paired with a relevant chunk id is objectively
checkable, which is why W7.1 lets the retrieval set be built without an expert
rating each case and why W7.3 calls these metrics the backbone of the whole
programme.

All three are fully offline and call no provider, for the reason `eval_interview`
states: a rate that moves must mean the CODE changed, not that a model sampled
differently. Nothing here opens a database, embeds a query or reaches a network.

WHAT --gate DECIDES, AND WHAT IT REFUSES TO DECIDE
----------------------------------------------------
Two questions, kept apart because they fail for different reasons:

  1. THE HARNESS SELF CHECK, which gates today. The shipped fixture run stamps
     the values its rankings produce under a reviewed metric implementation;
     this recomputes them and exits non-zero on any difference. Change the nDCG
     discount, the cutoff handling or the gain scale and the gate goes red
     rather than every reported number moving quietly.

  2. THE RETRIEVAL QUALITY QUESTION, which does not gate yet and says so. It
     needs a RECORDED run, produced by executing retrieval against a real
     index, and there is none: `services/rag/index.index_document` has no
     caller and `context_chunks` holds zero rows in the only deployed
     environment. It reports `unavailable` with that reason and does not
     contribute a zero and does not fail the run.

A gate that certified a hand-authored fixture as evidence about the product
would be the "the pipeline passed" failure this codebase already has a section
about. A gate that refused to run until production data existed would be no gate
at all. This is both halves, separated.

EXIT CODE
---------
Non-zero when the golden set will not load, or when the harness self check
fails. Never non-zero for `unavailable`: an unmeasured quantity is not a
failing one, which is the same rule that keeps it from being reported as 0.0.

Provenance: RPN-AI-UP-001 W7.3, and its acceptance criterion that this run
"fully offline against the golden retrieval set and prints recall@20, nDCG@10
and nDCG@100 with confidence intervals".
"""
from __future__ import annotations

import argparse
import json
import sys

from app.evaluation import golden, retrieval_eval

DEFAULT_RUN = "reference-fixture"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eval_retrieval",
        description="Score a retrieval run against the golden set, offline.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero when the harness self check fails",
    )
    parser.add_argument(
        "--run",
        default=DEFAULT_RUN,
        help=f"run id under datasets/retrieval/<version>/runs (default {DEFAULT_RUN})",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the full report as JSON"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        evaluation = retrieval_eval.evaluate_default(args.run)
    except golden.GoldenSetError as exc:
        # RAISED, not degraded. These artefacts ship in the tree; a missing or
        # malformed one is a packaging defect, and reporting it as an empty set
        # would print "nothing to measure" in the voice reserved for the honest
        # empty case.
        print(f"golden set unusable: {exc}", file=sys.stderr)
        # Name what DOES exist. A mistyped run id and an absent artefact produce
        # the same exception, and the two need opposite responses.
        known = golden.available_runs()
        if known:
            print(f"runs present: {', '.join(known)}", file=sys.stderr)
        return 2

    reasoning = retrieval_eval.judged_set_report(golden.SET_REASONING)
    decision = retrieval_eval.judged_set_report(golden.SET_DECISION)

    if args.json:
        print(
            json.dumps(
                {
                    "retrieval": evaluation.as_dict(),
                    "reasoning": reasoning.as_dict(),
                    "decision": decision.as_dict(),
                },
                indent=2,
                default=str,
            )
        )
    else:
        _print_human(evaluation, reasoning, decision)

    if args.gate and evaluation.harness.status == retrieval_eval.HarnessCheck.FAILED:
        return 1
    return 0


def _print_human(
    evaluation: retrieval_eval.RetrievalEvaluation,
    reasoning: object,
    decision: object,
) -> None:
    from app.evaluation.reporting import render_lines

    report = evaluation.report
    context = report.context
    print("=" * 78)
    print("RETRIEVAL EVAL, offline, judge free")
    print("=" * 78)
    print()
    for line in render_lines(report):
        print(line)
    print()
    print(
        f"  corpus {context['corpus_chunks']} chunks, "
        f"{context['queries_scored']} of {context['golden_queries']} golden "
        f"queries scored, run {context['run_id']} "
        f"({context['run_provenance']}, depth {context['run_depth']})"
    )
    print()

    print("-" * 78)
    print("recall@20 BY STRATUM")
    print("-" * 78)
    for axis, rows in sorted(evaluation.by_stratum.items()):
        print(f"  {axis}")
        for value, measurement in sorted(rows.items()):
            if measurement.available and measurement.interval is not None:
                body = (
                    f"{measurement.value:.4f}  "
                    f"[{measurement.interval.low:.4f}, {measurement.interval.high:.4f}]"
                    f"  n={measurement.sample_size}"
                )
            else:
                body = f"unavailable  ({measurement.unavailable_reason})"
            print(f"    {value.ljust(20)} {body}")
            # The caveats travel with the number. A clamped or zero-width
            # interval printed bare reads as a tighter measurement than it is,
            # and this is the section where the sample sizes are smallest.
            if measurement.note:
                print(f"    {' ' * 20} note: {measurement.note}")
    print()

    strata = context["stratification"]
    print("-" * 78)
    print("GOLDEN SET COMPOSITION")
    print("-" * 78)
    print(f"  cases            {strata['cases']} (floor {strata['size_targets']['floor']})")
    print(
        f"  cells populated  {strata['cells']['populated']} of "
        f"{strata['cells']['total']}"
    )
    print(f"  human verified   {strata['human_verified']} of {strata['cases']}")
    print("  provenance       observed against target")
    for key, target in sorted(strata["provenance"]["target"].items()):
        observed = strata["provenance"]["observed"].get(key, 0.0)
        print(f"    {key.ljust(20)} {observed:6.1%} against {target:6.1%}")
    print()

    print("-" * 78)
    print("HARNESS SELF CHECK")
    print("-" * 78)
    print(f"  status  {evaluation.harness.status}")
    print(f"  reason  {evaluation.harness.reason}")
    for difference in evaluation.harness.differences:
        print(f"    {difference}")
    print()

    print("-" * 78)
    print("THE OTHER TWO GOLDEN SETS")
    print("-" * 78)
    for other in (reasoning, decision):
        for line in render_lines(other):  # type: ignore[arg-type]
            print(line)
        print()


if __name__ == "__main__":
    sys.exit(main())
