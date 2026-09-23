"""`python -m harness`: the commands of HARNESS.md section 10.

WHY THE ENGINE IS IMPORTED LAZILY, AND ITS ABSENCE IS AN EXIT RATHER THAN A GUESS
---------------------------------------------------------------------------------
The execution engine (`runner.py`, `world.py`, `evaluators/`) is a separate
piece of work. This module resolves scenarios, mints a run, writes its manifest
and emits a report, and then asks the engine to execute. When the engine is not
there it says so in one sentence and exits non-zero.

It does NOT fall back to a fabricated result that reports a pass, and the
distinction is the whole reason this paragraph exists. A harness that answered
"pass" for a scenario it never executed would be the most expensive bug this
repository could ship: every other check in the tree would be reported as green
by the one thing built to notice when they are not. The run written in that case
has status `unavailable`, its evaluators carry `unavailable` with the reason,
and the artifacts are real, so the absence is evidence rather than silence.

THREE EXIT CODES, AND `unavailable` HAS ITS OWN
-------------------------------------------------
0 for a pass, 1 for a failure or a regression, 3 for unavailable. Separate
because CI has to be able to tell "this regressed" from "this could not be
measured", and collapsing the second into the first makes an unwired harness
indistinguishable from a broken product, while collapsing it into 0 is the
green-while-broken failure above. 2 belongs to argparse and is left to it.

Provenance: docs/spec/HARNESS.md sections 5, 6, 8 and 10.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import pathlib
import sys
from typing import Any, Callable, Sequence

from app.evaluation.golden import GOLDEN_VERSION

from harness import CONTRACT
from harness.artifacts import DEFAULT_KEEP_LAST, ArtifactError, ArtifactStore
from harness.baseline import (
    BaselineError,
    ThresholdError,
    baselines,
    compare,
    promote,
)
from harness.report import ReportError
from harness.report import write as write_report
from harness.run import FAIL, PASS, UNAVAILABLE, EvaluationOutcome, RunRecord, new_run
from harness.scenario import Scenario, ScenarioError, Tier, load_all, select

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNAVAILABLE = 3

SCENARIOS_DIR = pathlib.Path(__file__).resolve().parent / "scenarios"

#: The seed every run uses unless one is given. Fixed rather than random,
#: because HARNESS.md section 6 requires a replay to reproduce "same clock, same
#: seed", and a seed nobody chose is one a failing run cannot hand back.
DEFAULT_SEED = 20260921

#: What goes on the RECORD, kept short because it is repeated once per
#: evaluator in every report this state produces.
_ENGINE_ABSENT_REASON = (
    "the harness execution engine is not wired ({reason}), so nothing executed"
)

#: What goes on STDERR, once per invocation, where the full argument belongs.
_ENGINE_ABSENT_NOTE = (
    "the harness execution engine is not wired: `harness.runner` does not exist "
    "({reason}). The run record and its report have been written with every "
    "evaluator reporting `unavailable`, because reporting a pass for a scenario "
    "that never executed is the one outcome a harness must never produce."
)


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _dataset_versions(scenario: Scenario) -> dict[str, str]:
    """What this run was measured against, stamped before it starts.

    The golden set version comes from `app/evaluation/golden.py` rather than
    being retyped here, and the scenario stamps its own version beside it. A
    score that falls has three candidate explanations and only these separate
    the third from the other two.
    """
    return {"golden": GOLDEN_VERSION, "scenario": f"v{scenario.version}"}


def _load_engine() -> Any:
    """Import the execution engine, or return None because it does not exist yet.

    A lazy import inside the function rather than at module scope, so that
    `harness list`, `harness compare` and `harness baseline promote` all work on
    a checkout where the engine has not been written.

    ABSENCE IS DETECTED WITH `find_spec`, NOT BY CATCHING `ImportError`. An
    engine that exists and fails to import, because one of ITS imports is
    broken, is a real defect, and `except ImportError` would report it as "not
    wired yet" and hide it behind a message that invites the reader to do
    nothing. Only a module that is genuinely not on disk returns None here;
    anything else propagates with its own traceback.
    """
    if importlib.util.find_spec("harness.runner") is None:
        return None
    return importlib.import_module("harness.runner")


def _unwired_run(run: RunRecord, scenario: Scenario, reason: str) -> None:
    """Record, on the run itself, that nothing executed and why.

    One `unavailable` outcome per evaluator the scenario asked for, so the
    report lists them by name. A single blanket line would make it look as if
    the scenario declared no evaluators, which is a different defect entirely.
    """
    evaluators = scenario.expect.evaluators or ("harness_engine",)
    for evaluator in evaluators:
        run.evaluations.append(
            EvaluationOutcome(
                evaluator=evaluator,
                outcome=UNAVAILABLE,
                reason=reason,
            )
        )
    run.finish()


def _emit(
    run: RunRecord, scenario: Scenario, store: ArtifactStore, *, quiet: bool
) -> None:
    store.write_manifest(run)
    json_path, md_path = write_report(run, store, description=scenario.description)
    if not quiet:
        print(f"  {run.status:<12} {scenario.qualified_id}  run {run.run_id}")
        print(f"               report {md_path}")
        print(f"               json   {json_path}")


def _execute(
    run: RunRecord, scenario: Scenario, store: ArtifactStore, *, quiet: bool
) -> tuple[int, str | None]:
    """Run one scenario through the engine, or record its absence.

    Returns the exit code this scenario contributes and, when the engine is
    missing, the sentence to print once at the end. Printing it once rather
    than per scenario keeps a twenty scenario run readable.
    """
    engine = _load_engine()
    if engine is None:
        absent = "harness/runner.py does not exist"
        _unwired_run(run, scenario, _ENGINE_ABSENT_REASON.format(reason=absent))
        _emit(run, scenario, store, quiet=quiet)
        return EXIT_UNAVAILABLE, _ENGINE_ABSENT_NOTE.format(reason=absent)
    engine.execute(run=run, scenario=scenario, store=store)
    if run.finished_at is None:
        run.finish()
    _emit(run, scenario, store, quiet=quiet)
    if run.status == FAIL:
        return EXIT_FAILED, None
    if run.status == UNAVAILABLE:
        return EXIT_UNAVAILABLE, None
    return EXIT_OK, None


def _worst(codes: Sequence[int]) -> int:
    """A run is as good as its worst scenario.

    `EXIT_FAILED` beats `EXIT_UNAVAILABLE` because a real failure is the more
    actionable of the two and should be the exit code a pipeline reports; both
    beat success, which is the half that must never be lost.
    """
    if EXIT_FAILED in codes:
        return EXIT_FAILED
    if EXIT_UNAVAILABLE in codes:
        return EXIT_UNAVAILABLE
    return EXIT_OK


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    store = ArtifactStore(args.artifacts)
    scenarios = select(
        load_all(SCENARIOS_DIR),
        tier=None if args.tier is None else Tier(args.tier),
        scenario_id=args.scenario,
    )
    print(f"harness {CONTRACT}: {len(scenarios)} scenario(s)")
    codes: list[int] = []
    engine_note: str | None = None
    for scenario in scenarios:
        run = new_run(
            scenario,
            repo_root=_repo_root(),
            dispatch_backend=args.dispatch_backend,
            seed=args.seed,
            config={"tier_filter": args.tier, "scenario_filter": args.scenario},
            dataset_versions=_dataset_versions(scenario),
        )
        store.begin(run)
        code, note = _execute(run, scenario, store, quiet=False)
        codes.append(code)
        engine_note = engine_note or note
    deleted = store.prune(args.keep_last)
    if deleted:
        print(f"pruned {len(deleted)} old run(s), keeping the last {args.keep_last}")
    if engine_note:
        print("")
        print(engine_note, file=sys.stderr)
    return _worst(codes)


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-execute from a manifest, refusing a scenario that has moved underneath it.

    The version check is the point of a replay. A scenario edited between the
    failure and the replay would re-run as something else while every artifact
    still carried the original id, which is the one situation in which a replay
    actively misleads.
    """
    store = ArtifactStore(args.artifacts)
    original = store.read_run(args.run_id)
    scenarios = {item.id: item for item in load_all(SCENARIOS_DIR)}
    scenario = scenarios.get(original.scenario_id)
    if scenario is None:
        print(
            f"run {args.run_id} names scenario {original.scenario_id!r}, which no "
            "longer exists. A replay cannot reconstruct a scenario from a "
            "manifest: the manifest records which scenario ran, not what it said.",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    if scenario.version != original.scenario_version:
        print(
            f"run {args.run_id} ran {original.scenario_id} at version "
            f"{original.scenario_version}, and the file is now at version "
            f"{scenario.version}. Replaying would exercise a different scenario "
            "under the original's name. Check out the commit the run recorded "
            f"({original.git.described}) and replay there.",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE

    run = new_run(
        scenario,
        repo_root=_repo_root(),
        dispatch_backend=original.dispatch_backend,
        seed=original.seed,
        config={**original.config, "replay_of": original.run_id},
        dataset_versions=original.dataset_versions or _dataset_versions(scenario),
    )
    # The faults of the ORIGINAL run, not of the scenario file, because
    # HARNESS.md section 6 requires the same faults and a scenario's fault list
    # may have been edited even when its version did not change in a way the
    # check above catches.
    run.faults = original.faults
    store.begin(run)
    print(f"replaying {original.run_id} as {run.run_id}")
    code, note = _execute(run, scenario, store, quiet=False)
    if note:
        print("")
        print(note, file=sys.stderr)
    return code


def cmd_compare(args: argparse.Namespace) -> int:
    store = ArtifactStore(args.artifacts)
    result = compare(args.run_id, store)
    print(f"{result.outcome}: {result.reason}")
    if result.baseline_run_id:
        print(f"baseline for tier {result.tier.value}: {result.baseline_run_id}")
    for regression in result.regressions:
        print(f"  REGRESSION  {regression.described()}")
    store.write_json(args.run_id, "comparison.json", result.as_dict())
    if result.outcome == FAIL:
        return EXIT_FAILED
    if result.outcome == PASS:
        return EXIT_OK
    return EXIT_UNAVAILABLE


def cmd_baseline_promote(args: argparse.Namespace) -> int:
    store = ArtifactStore(args.artifacts)
    entry = promote(args.run_id, store)
    print(
        f"promoted {entry['run_id']} as the baseline for tier {entry['tier']} "
        f"({entry['scenario_id']} v{entry['scenario_version']}, status "
        f"{entry['status']})"
    )
    for metric, value in sorted(entry["metrics"].items()):
        print(f"  {metric}: {value:g}")
    return EXIT_OK


def cmd_gate(args: argparse.Namespace) -> int:
    """The CI regression gate: compare each tier against its baseline, or set one.

    WHY THIS IS A COMMAND AND NOT A SHELL LOOP IN THE WORKFLOW
    ------------------------------------------------------------
    The registry holds ONE baseline per tier, naming the scenario it was taken
    from. Comparing an arbitrary run of that tier against it would diff
    `duration_seconds` between two different scenarios, and the resulting
    "regression" would be a statement about which scenario happened to run last
    rather than about the code. So the gate has to find the newest run OF THE
    SCENARIO THE BASELINE NAMES, which is a lookup rather than a one-liner, and
    a lookup written in YAML is one nobody can test.

    A TIER WITH NO BASELINE HAS ONE ESTABLISHED, RATHER THAN BEING SKIPPED.
    `compare` answers `unavailable` in that state and it is right to: a
    comparison with nothing to compare against has computed nothing. Skipping it
    would leave a gate reading green because nobody had ever given it anything
    to read, which is the same failure `release_gate.py` refuses for itself. The
    caller is expected to have run and PASSED the tier first, which is what
    makes promoting here safe: the workflow orders it that way and a failing
    tier never reaches this step.

    A BASELINE WHOSE SCENARIO DID NOT RUN IS `unavailable`, NOT A PASS. That
    happens when a scenario is renamed or deleted, and the honest answer is that
    the gate could not be evaluated. Promoting a different scenario silently
    instead would move the reference without anybody deciding to.
    """
    store = ArtifactStore(args.artifacts)
    entries = store.list_runs()
    if not entries:
        print(
            f"harness gate: no runs under {store.root}. The gate compares runs "
            "that have already happened, so it is run AFTER the tiers, not "
            "instead of them.",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE

    registry = baselines(store)
    tiers = sorted({entry.tier for entry in entries})
    codes: list[int] = []
    for tier in tiers:
        of_tier = [entry for entry in entries if entry.tier == tier]
        recorded = registry.get(tier)
        if recorded is None:
            newest = of_tier[0]
            promoted = promote(newest.run_id, store)
            print(
                f"{tier:<12} no baseline; promoted {promoted['run_id']} "
                f"({promoted['scenario_id']} v{promoted['scenario_version']}, "
                f"status {promoted['status']}) as the reference"
            )
            continue
        scenario_id = str(recorded.get("scenario_id") or "")
        matching = [entry for entry in of_tier if entry.scenario_id == scenario_id]
        if not matching:
            print(
                f"{tier:<12} the baseline was taken from {scenario_id!r}, which "
                "did not run here, so there is nothing to compare like with "
                "like. Re-promote a baseline for this tier deliberately.",
                file=sys.stderr,
            )
            codes.append(EXIT_UNAVAILABLE)
            continue
        result = compare(matching[0].run_id, store)
        print(f"{tier:<12} {result.outcome}: {result.reason}")
        for regression in result.regressions:
            print(f"               REGRESSION  {regression.described()}")
        store.write_json(matching[0].run_id, "comparison.json", result.as_dict())
        if result.outcome == FAIL:
            codes.append(EXIT_FAILED)
        elif result.outcome != PASS:
            codes.append(EXIT_UNAVAILABLE)
    return _worst(codes)


def cmd_list(args: argparse.Namespace) -> int:
    store = ArtifactStore(args.artifacts)
    entries = store.list_runs()
    if not entries:
        print(f"no runs under {store.root}")
        return EXIT_OK
    print(f"{'STARTED':<26} {'STATUS':<12} {'TIER':<12} {'RUN':<34} SCENARIO")
    for entry in entries:
        marker = " *baseline" if entry.promoted else ""
        print(
            f"{entry.started_at:<26} {entry.status:<12} {entry.tier:<12} "
            f"{entry.run_id:<34} {entry.scenario_id} v{entry.scenario_version}{marker}"
        )
    return EXIT_OK


# ── Argument parsing ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description=(
            "The Vivekium harness. Runs scenarios against the test stack, "
            "records what happened, and compares a run against its tier's "
            "promoted baseline. See docs/spec/HARNESS.md."
        ),
    )
    parser.add_argument(
        "--artifacts",
        default=None,
        help="artifact store root (default: <repo>/harness-runs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="execute scenarios")
    run_parser.add_argument(
        "--tier",
        choices=[tier.value for tier in Tier],
        default=None,
        help="run one tier (default: every tier except performance)",
    )
    run_parser.add_argument("--scenario", default=None, help="run one scenario by id")
    run_parser.add_argument(
        "--keep-last",
        type=int,
        default=DEFAULT_KEEP_LAST,
        help=f"artifact retention (default: {DEFAULT_KEEP_LAST})",
    )
    run_parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"seed recorded and replayed (default: {DEFAULT_SEED})",
    )
    run_parser.add_argument(
        "--dispatch-backend",
        default="record",
        help=(
            "recorded on the run. `record` is the only isolated one and is what "
            "scripts/harness.sh exports"
        ),
    )
    run_parser.set_defaults(handler=cmd_run)

    replay_parser = sub.add_parser("replay", help="re-execute a recorded run")
    replay_parser.add_argument("run_id")
    replay_parser.set_defaults(handler=cmd_replay)

    compare_parser = sub.add_parser("compare", help="diff a run against its baseline")
    compare_parser.add_argument("run_id")
    compare_parser.set_defaults(handler=cmd_compare)

    baseline_parser = sub.add_parser("baseline", help="manage promoted baselines")
    baseline_sub = baseline_parser.add_subparsers(dest="baseline_command", required=True)
    promote_parser = baseline_sub.add_parser(
        "promote", help="record a run as the reference for its tier"
    )
    promote_parser.add_argument("run_id")
    promote_parser.set_defaults(handler=cmd_baseline_promote)

    gate_parser = sub.add_parser(
        "gate",
        help=(
            "CI: compare every tier that ran against its promoted baseline, "
            "establishing one where none exists"
        ),
    )
    gate_parser.set_defaults(handler=cmd_gate)

    list_parser = sub.add_parser("list", help="list recorded runs, newest first")
    list_parser.set_defaults(handler=cmd_list)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse and dispatch, turning the package's own errors into one clear line.

    The four error classes caught here are the ones that mean "your input or
    your store is wrong", and every one of them already carries the file, the
    field or the path in its message. A traceback would bury that under twenty
    frames of argparse, so the message is printed and the code is returned.
    Anything else propagates, because an unexpected exception in a harness is
    itself a finding.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (ScenarioError, ArtifactError, BaselineError, ThresholdError, ReportError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
