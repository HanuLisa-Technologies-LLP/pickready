"""CLI over `app/evaluation/judges/determinism.py`. RPN-AI-UP-001 W7.2.

    python -m app.scripts.probe_judge_determinism [--repeats N] [--json]

THIS FILE HOLDS NO MODEL ID AND NO LOGIC, AND THAT IS THE POINT
----------------------------------------------------------------
The first draft put the panel and the probe here, and
`test_no_other_model_id_appears_in_executable_code` failed it. The test was
right. `MODEL_FOR_TASK` is a closed mapping onto two ids so that a candidate's
GRADE cannot come from an unreviewed model, and `app/evaluation/` is the one
exempt prefix precisely because `test_judge_isolation.py` proves by AST that no
route or worker can reach it. A judge id in a script would have been exempt from
nothing and proved by nothing.

`app/scripts/*` is the sanctioned door into the eval layer -- the same shape as
`eval_retrieval.py` and `eval_agents.py` -- so the command lives here and
everything it commands lives there.
"""
from __future__ import annotations

import argparse
import json

from app.evaluation.judges import determinism, gemini


def main() -> int:
    parser = argparse.ArgumentParser(description="W7.2 judge determinism probe")
    parser.add_argument(
        "--repeats",
        type=int,
        default=20,
        help="calls per case per arm. W7.2 asks for 20.",
    )
    parser.add_argument("--json", action="store_true", help="indent the record")
    parser.add_argument(
        "--models",
        nargs="*",
        default=list(gemini.JUDGE_MODELS),
        help="override the panel under test",
    )
    args = parser.parse_args()

    record, results = determinism.run_probe(args.models, args.repeats)

    print("JUDGE_DETERMINISM_JSON_START")
    print(json.dumps(record, indent=2 if args.json else None))
    print("JUDGE_DETERMINISM_JSON_END")

    print("\nSUMMARY (pooled self-disagreement, lower is more reproducible)")
    for result in results:
        pooled = result.pooled_disagreement()
        # `unavailable` rather than a number, always, when nothing was measured.
        # A reader scanning this block must not be able to mistake an arm that
        # failed every call for an arm that agreed with itself.
        sigma = "unavailable" if pooled is None else f"{pooled:.4f}"
        print(
            f"  {result.model:<20} {result.arm:<22} "
            f"sigma={sigma:<12} "
            f"usable={result.usable_share():.2f}  "
            f"unanimous={result.unanimous_cases()}/{result.measurable_cases()}"
        )

    # A MEASUREMENT, not a gate. It returns 0 even when a model disagrees with
    # itself on every call, for the reason `ai_baseline.py` records: a
    # measurement that exits non-zero gets wired into a pipeline as a readiness
    # check and then stops being run when it is inconvenient.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
