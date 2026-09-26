"""The harness core: the refusals, the round trip, the retention and the report.

WHAT THESE TESTS ARE DEFENDING
--------------------------------
Every assertion below corresponds to a way the harness could go quietly wrong,
which is the only interesting failure mode for a thing whose job is to notice
when other things go quietly wrong:

  * A scenario that loads without ground truth would execute and report a pass
    it never earned.
  * A manifest that does not round trip would make replay reproduce something
    other than the run it came from, which is worse than no replay at all,
    because the result carries the original's id.
  * A prune that deletes a promoted baseline would not lose an artifact, it
    would disable the regression gate: `compare` would answer "no baseline" and
    a caller reading the exit code sees the gate go quiet rather than red.
  * A threshold with no reason is one that gets relaxed during a red build with
    nothing in the diff saying what it was protecting.
  * A report that does not name the failing scenario is a report somebody has
    to go and reproduce the failure to understand.

Nothing here touches a database, a stack or a model credential. That is
deliberate: these are properties of the harness's own arithmetic and file
handling, and a test of them that needed infrastructure would be one that stops
running the first time the infrastructure is unavailable.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from harness.artifacts import ArtifactError, ArtifactStore
from harness.baseline import (
    Threshold,
    ThresholdError,
    compare,
    load_thresholds,
    promote,
)
from harness.report import ReportError
from harness.report import write as write_report
from harness.run import FAIL, PASS, UNAVAILABLE, Check, EvaluationOutcome, RunRecord
from harness.scenario import (
    Scenario,
    ScenarioError,
    Tier,
    load_all,
    load_scenario,
    select,
)

VALID = """
id: assessment.completes_and_bills_once
version: 3
tier: regression
description: >
  A completed assessment charges exactly one credit and dispatches scoring
  once, even when the completion request is retried.
tags: [billing, assessment, idempotency]
given:
  world: single_tenant_job_with_invited_candidate
  overrides:
    job.role_classification: stem
workload:
  - answer_every_question
faults: []
expect:
  state:
    - credit_ledger.entries_for_link == 1
  dispatched:
    pickready.run_functional_assessment: 1
  evaluators:
    - functional_correctness
"""


def _write(directory: pathlib.Path, name: str, body: str) -> pathlib.Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ── The loader, mostly in its refusals ───────────────────────────────────────


def test_a_well_formed_scenario_parses_into_everything_a_replay_needs(
    tmp_path: pathlib.Path,
) -> None:
    """The positive direction first, so the refusals below are known to be
    refusals of something rather than the loader failing on everything."""
    scenario = load_scenario(_write(tmp_path, "valid.yaml", VALID))
    assert scenario.id == "assessment.completes_and_bills_once"
    assert scenario.version == 3
    assert scenario.tier is Tier.REGRESSION
    assert scenario.qualified_id == "assessment.completes_and_bills_once@v3"
    assert scenario.given.world == "single_tenant_job_with_invited_candidate"
    assert scenario.given.overrides == {"job.role_classification": "stem"}
    assert scenario.workload == ("answer_every_question",)
    assert scenario.expect.dispatched == {"pickready.run_functional_assessment": 1}
    assert scenario.expect.evaluators == ("functional_correctness",)
    # The prose is normalised so a folded YAML block does not arrive carrying
    # the newlines of its own indentation into a markdown report.
    assert "\n" not in scenario.description


def test_a_scenario_with_no_expect_is_refused(tmp_path: pathlib.Path) -> None:
    body = VALID.split("expect:")[0]
    with pytest.raises(ScenarioError) as exc:
        load_scenario(_write(tmp_path, "no_expect.yaml", body))
    message = str(exc.value)
    assert "no_expect.yaml" in message
    assert "expect" in message
    # The message has to carry the REASON, not only the field, because the
    # author's next move depends on understanding that an executing scenario
    # is not a passing one.
    assert "merely executes" in message


def test_a_scenario_with_an_empty_expect_is_refused_too(tmp_path: pathlib.Path) -> None:
    """An empty block is the same defect wearing a present key's clothes, and it
    is the one a careless edit actually produces."""
    body = VALID.split("expect:")[0] + "expect:\n  state: []\n"
    with pytest.raises(ScenarioError, match="empty"):
        load_scenario(_write(tmp_path, "empty_expect.yaml", body))


def test_an_unknown_tier_is_refused_rather_than_defaulted(
    tmp_path: pathlib.Path,
) -> None:
    body = VALID.replace("tier: regression", "tier: regresion")
    with pytest.raises(ScenarioError) as exc:
        load_scenario(_write(tmp_path, "typo_tier.yaml", body))
    message = str(exc.value)
    assert "regresion" in message
    # And it lists what IS permitted, because a typo is fixed in one second
    # with the list and in five minutes without it.
    assert "adversarial" in message and "safety" in message


def test_a_missing_id_or_version_is_refused(tmp_path: pathlib.Path) -> None:
    without_id = "\n".join(
        line for line in VALID.splitlines() if not line.startswith("id:")
    )
    with pytest.raises(ScenarioError, match="'id'"):
        load_scenario(_write(tmp_path, "no_id.yaml", without_id))

    without_version = "\n".join(
        line for line in VALID.splitlines() if not line.startswith("version:")
    )
    with pytest.raises(ScenarioError, match="'version'"):
        load_scenario(_write(tmp_path, "no_version.yaml", without_version))


def test_a_misspelled_top_level_key_is_named_rather_than_ignored(
    tmp_path: pathlib.Path,
) -> None:
    """`expects:` parses as valid YAML and asserts nothing. A forgiving loader
    would refuse the file for having no `expect` while the author stares at the
    block they can plainly see."""
    body = VALID.replace("expect:", "expects:")
    with pytest.raises(ScenarioError) as exc:
        load_scenario(_write(tmp_path, "misspelled.yaml", body))
    assert "expects" in str(exc.value)


def test_a_duplicate_id_across_the_directory_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Two files answering to one name means `--scenario` picks one of them and
    the report names the other."""
    _write(tmp_path, "first.yaml", VALID)
    _write(tmp_path, "second.yaml", VALID.replace("version: 3", "version: 4"))
    with pytest.raises(ScenarioError) as exc:
        load_all(tmp_path)
    message = str(exc.value)
    assert "duplicate" in message
    assert "first.yaml" in message and "second.yaml" in message


def test_selecting_nothing_raises_rather_than_returning_an_empty_run(
    tmp_path: pathlib.Path,
) -> None:
    """A run over zero scenarios exits zero and reads as a pass, which is the
    same defect `scripts/test.sh` guards against in its integration selector."""
    scenarios = load_all(pathlib.Path(_write(tmp_path, "one.yaml", VALID)).parent)
    assert select(scenarios, tier=Tier.REGRESSION) == scenarios
    with pytest.raises(ScenarioError, match="reads as a pass"):
        select(scenarios, tier=Tier.SAFETY)
    with pytest.raises(ScenarioError, match="no scenario with id"):
        select(scenarios, scenario_id="nope")


def test_a_fault_is_parsed_in_both_of_its_legitimate_spellings(
    tmp_path: pathlib.Path,
) -> None:
    body = VALID.replace(
        "faults: []",
        "faults:\n  - redis_down\n  - model_error:\n      status: 429\n",
    )
    scenario = load_scenario(_write(tmp_path, "faulted.yaml", body))
    assert [fault.name for fault in scenario.faults] == ["redis_down", "model_error"]
    assert scenario.faults[0].params == {}
    assert scenario.faults[1].params == {"status": 429}


# ── The run record ───────────────────────────────────────────────────────────


def _scenario(tmp_path: pathlib.Path) -> Scenario:
    return load_scenario(_write(tmp_path, "valid.yaml", VALID))


def _run(run_id: str = "a" * 32, started_at: str = "2026-09-21T09:00:00+00:00") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        started_at=started_at,
        scenario_id="assessment.completes_and_bills_once",
        scenario_version=3,
        tier=Tier.REGRESSION,
        world="single_tenant_job_with_invited_candidate",
        workload=("answer_every_question",),
        faults=(),
        seed=20260921,
        dispatch_backend="record",
        git_sha="deadbeef",
        git_branch="integrate/pr6",
        git_dirty=True,
        dataset_versions={"golden": "2026.Q3.2", "scenario": "v3"},
    )


def test_a_manifest_round_trips_losslessly(tmp_path: pathlib.Path) -> None:
    """Through JSON, not merely through a dict.

    A dict round trip would pass with a tuple, an enum or a dataclass in a
    field and prove nothing about what a replay actually reads off disk. The
    serialisation is where a tuple silently becomes a list and an enum silently
    becomes something that no longer compares equal to a `Tier`.
    """
    run = _run()
    run.faults = _scenario(tmp_path).faults
    run.checks.append(
        Check(
            kind="dispatched",
            expression="pickready.run_functional_assessment",
            expected=1,
            actual=2,
            passed=False,
            note="the retry dispatched a second time",
        )
    )
    run.evaluations.append(
        EvaluationOutcome(
            evaluator="functional_correctness",
            outcome=FAIL,
            reason="scoring was dispatched twice for one application",
            score=0.5,
            interval=(0.2, 0.8),
            sample_size=2,
            details={"link_id": "abc"},
        )
    )
    run.finish()

    restored = RunRecord.from_manifest(json.loads(json.dumps(run.to_manifest())))
    assert restored.to_manifest() == run.to_manifest()
    assert restored.tier is Tier.REGRESSION
    assert restored.checks[0].actual == 2
    assert restored.evaluations[0].interval == (0.2, 0.8)
    assert restored.git.described.endswith("(working tree dirty)")


def test_the_status_is_derived_and_unavailable_blocks_rather_than_passes() -> None:
    """A status somebody sets by hand is one that can disagree with the checks
    beside it, and the disagreement always resolves optimistically."""
    empty = _run()
    empty.finish()
    # Nothing was asserted and nothing was evaluated. That is not a pass.
    assert empty.status == UNAVAILABLE

    passing = _run()
    passing.checks.append(
        Check(kind="state", expression="ledger == 1", expected=1, actual=1, passed=True)
    )
    passing.finish()
    assert passing.status == PASS

    blocked = _run()
    blocked.checks.append(
        Check(kind="state", expression="ledger == 1", expected=1, actual=1, passed=True)
    )
    blocked.evaluations.append(
        EvaluationOutcome(
            evaluator="degradation_honesty",
            outcome=UNAVAILABLE,
            reason="no fault was injected, so degradation could not be observed",
        )
    )
    blocked.finish()
    assert blocked.status == UNAVAILABLE

    failing = _run()
    failing.checks.append(
        Check(kind="state", expression="ledger == 1", expected=1, actual=2, passed=False)
    )
    failing.evaluations.append(
        EvaluationOutcome(
            evaluator="functional_correctness",
            outcome=UNAVAILABLE,
            reason="not reached",
        )
    )
    # A failure outranks an unavailable: the run failed, and saying it could not
    # be measured would hide the thing that was measured.
    failing.finish()
    assert failing.status == FAIL


def test_an_evaluator_may_not_report_a_score_without_its_interval() -> None:
    """The same invariant `metrics.Measurement` enforces, restated at the JSON
    boundary where the type is lost. A point estimate with no dispersion beside
    it is what turns eval noise into a release decision."""
    with pytest.raises(ValueError, match="interval"):
        EvaluationOutcome(
            evaluator="latency", outcome=PASS, reason="fast enough", score=0.9
        )
    with pytest.raises(ValueError, match="unavailable"):
        EvaluationOutcome(
            evaluator="latency",
            outcome=UNAVAILABLE,
            reason="no timings recorded",
            score=0.0,
            interval=(0.0, 0.0),
        )
    with pytest.raises(ValueError, match="reason"):
        EvaluationOutcome(evaluator="latency", outcome=PASS, reason="   ")


# ── The artifact store ───────────────────────────────────────────────────────


def _store_with_runs(tmp_path: pathlib.Path, count: int) -> tuple[ArtifactStore, list[str]]:
    store = ArtifactStore(tmp_path / "harness-runs")
    ids: list[str] = []
    for index in range(count):
        run_id = f"{index:032d}"
        run = _run(run_id=run_id, started_at=f"2026-09-{index + 1:02d}T09:00:00+00:00")
        run.checks.append(
            Check(kind="state", expression="ledger == 1", expected=1, actual=1, passed=True)
        )
        run.finish()
        store.begin(run)
        store.write_manifest(run)
        ids.append(run_id)
    return store, ids


def test_list_runs_is_newest_first_and_reads_the_manifest_not_the_filesystem(
    tmp_path: pathlib.Path,
) -> None:
    store, ids = _store_with_runs(tmp_path, 3)
    listed = [entry.run_id for entry in store.list_runs()]
    assert listed == list(reversed(ids))


def test_a_run_directory_without_a_manifest_is_an_error_not_a_skip(
    tmp_path: pathlib.Path,
) -> None:
    """A half written run directory is evidence that something died, and a
    listing that quietly omitted it would hide the run worth looking at."""
    store, _ = _store_with_runs(tmp_path, 1)
    (store.root / "orphan").mkdir()
    with pytest.raises(ArtifactError, match="orphan"):
        store.list_runs()


def test_prune_keeps_exactly_n_and_never_deletes_a_promoted_baseline(
    tmp_path: pathlib.Path,
) -> None:
    """Both halves in one test, because they interact: the promoted run is the
    OLDEST, so a prune that counted it toward `keep_last` would spare it by
    accident and the assertion would pass for the wrong reason."""
    store, ids = _store_with_runs(tmp_path, 6)
    oldest = ids[0]
    promote(oldest, store)

    deleted = store.prune(keep_last=2)

    surviving = {entry.run_id for entry in store.list_runs()}
    # The two newest, plus the promoted baseline which does not consume a slot.
    assert surviving == {ids[-1], ids[-2], oldest}
    assert set(deleted) == {ids[1], ids[2], ids[3]}
    assert oldest not in deleted
    assert (store.root / oldest).is_dir()


def test_prune_refuses_to_keep_nothing(tmp_path: pathlib.Path) -> None:
    """A store keeping zero runs deletes the run that has just finished,
    including the failing one somebody is about to read."""
    store, _ = _store_with_runs(tmp_path, 2)
    with pytest.raises(ArtifactError, match="at least 1"):
        store.prune(keep_last=0)


def test_an_unreadable_baseline_registry_stops_the_prune(
    tmp_path: pathlib.Path,
) -> None:
    """Treating it as empty would let the prune delete the very runs it exists
    to protect, which is the failure this whole guard is about."""
    store, ids = _store_with_runs(tmp_path, 2)
    promote(ids[0], store)
    store.baselines_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ArtifactError, match="baselines.json"):
        store.prune(keep_last=1)


# ── Thresholds and the comparison ────────────────────────────────────────────


def test_the_shipped_thresholds_all_load_and_all_carry_a_reason() -> None:
    thresholds = load_thresholds()
    assert thresholds, "an empty threshold table is a comparison that cannot fail"
    for threshold in thresholds.values():
        assert threshold.reason.strip(), threshold.metric
        declared = [
            value
            for value in (threshold.max_increase, threshold.max_increase_ratio)
            if value is not None
        ]
        assert len(declared) == 1, threshold.metric
    # The counts that must never rise are the ones CI fails on.
    assert thresholds["checks_failed"].max_increase == 0


def test_a_threshold_with_no_reason_raises(tmp_path: pathlib.Path) -> None:
    """A guard threshold moves only with its reason written beside it. A
    reasonless entry that loaded could be added quietly during a red build."""
    path = _write(
        tmp_path,
        "thresholds.yaml",
        "version: 1\nthresholds:\n  checks_failed:\n    max_increase: 0\n",
    )
    with pytest.raises(ThresholdError) as exc:
        load_thresholds(path)
    assert "reason" in str(exc.value)
    assert "checks_failed" in str(exc.value)

    blank = _write(
        tmp_path,
        "blank.yaml",
        "version: 1\nthresholds:\n  checks_failed:\n    max_increase: 0\n"
        '    reason: "   "\n',
    )
    with pytest.raises(ThresholdError, match="reason"):
        load_thresholds(blank)


def test_a_threshold_declaring_two_bounds_is_refused(tmp_path: pathlib.Path) -> None:
    """Two bounds on one metric means the looser one is the real rule and
    nobody can tell which it is."""
    path = _write(
        tmp_path,
        "two.yaml",
        "version: 1\nthresholds:\n  duration_seconds:\n    max_increase: 1\n"
        "    max_increase_ratio: 0.5\n    reason: because\n",
    )
    with pytest.raises(ThresholdError, match="exactly one"):
        load_thresholds(path)


def test_comparing_against_no_baseline_is_unavailable_and_never_a_pass(
    tmp_path: pathlib.Path,
) -> None:
    """The rule borrowed from `release_gate.py`: a metric that could not be
    computed must block, or a broken harness waves every release through."""
    store, ids = _store_with_runs(tmp_path, 1)
    result = compare(ids[0], store)
    assert result.outcome == UNAVAILABLE
    assert result.baseline_run_id is None
    assert "promote" in result.reason


def test_a_new_failing_check_is_reported_as_a_regression_naming_its_reason(
    tmp_path: pathlib.Path,
) -> None:
    store = ArtifactStore(tmp_path / "harness-runs")
    clean = _run(run_id="1" * 32, started_at="2026-09-01T09:00:00+00:00")
    clean.checks.append(
        Check(kind="state", expression="ledger == 1", expected=1, actual=1, passed=True)
    )
    clean.finish()
    store.begin(clean)
    store.write_manifest(clean)
    promote(clean.run_id, store)

    broken = _run(run_id="2" * 32, started_at="2026-09-02T09:00:00+00:00")
    broken.checks.append(
        Check(kind="state", expression="ledger == 1", expected=1, actual=2, passed=False)
    )
    broken.finish()
    store.begin(broken)
    store.write_manifest(broken)

    result = compare(broken.run_id, store)
    assert result.outcome == FAIL
    assert result.baseline_run_id == clean.run_id
    regressed = {item.metric for item in result.regressions}
    assert "checks_failed" in regressed
    # The reason travels with the regression, so the person deciding whether to
    # relax the bound reads what it was protecting at the moment they decide.
    assert all(item.reason.strip() for item in result.regressions)


def test_a_ratio_threshold_tolerates_noise_and_catches_a_real_slowdown() -> None:
    threshold = Threshold(
        metric="duration_seconds",
        max_increase=None,
        max_increase_ratio=0.5,
        reason="a ratio, because a laptop and CI differ by more than any fixed number",
    )
    assert not threshold.exceeded_by(10.0, 14.0)
    assert threshold.exceeded_by(10.0, 16.0)
    # Off a zero baseline a ratio has no meaning, and a metric that could never
    # regress once it touched zero is a gate that switches itself off.
    assert threshold.exceeded_by(0.0, 1.0)
    assert not threshold.exceeded_by(0.0, 0.0)


# ── The report ───────────────────────────────────────────────────────────────


def test_the_markdown_names_the_failing_scenario_and_shows_expected_against_actual(
    tmp_path: pathlib.Path,
) -> None:
    store = ArtifactStore(tmp_path / "harness-runs")
    run = _run()
    run.checks.append(
        Check(
            kind="dispatched",
            expression="pickready.run_functional_assessment",
            expected=1,
            actual=2,
            passed=False,
            note="the retry dispatched a second time",
        )
    )
    run.checks.append(
        Check(kind="state", expression="ledger == 1", expected=1, actual=1, passed=True)
    )
    run.evaluations.append(
        EvaluationOutcome(
            evaluator="functional_correctness",
            outcome=FAIL,
            reason="scoring was dispatched twice for one application",
        )
    )
    run.finish()
    store.begin(run)

    json_path, md_path = write_report(
        run, store, description="A completed assessment charges exactly one credit."
    )
    markdown = md_path.read_text(encoding="utf-8")

    assert run.scenario_id in markdown
    assert "FAILED" in markdown
    assert f"Why {run.scenario_id} failed" in markdown
    assert "pickready.run_functional_assessment" in markdown
    # Expected against actual, both present, so a reader does not have to open
    # the run to learn what the assertion saw.
    assert "`1`" in markdown and "`2`" in markdown
    assert "scoring was dispatched twice" in markdown
    # And the path to the artifacts, which is the last of the five questions a
    # reader asks.
    assert str(store.run_dir(run.run_id)) in markdown

    body = json.loads(json_path.read_text(encoding="utf-8"))
    assert body["summary"]["checks_failed"] == 1
    assert body["summary"]["status"] == FAIL
    assert body["evaluation"]["dataset_version"]


def test_a_run_that_asserted_nothing_says_so_rather_than_rendering_a_clean_report(
    tmp_path: pathlib.Path,
) -> None:
    """The check pass rate is UNAVAILABLE with no checks, never 1.0. A scenario
    that asserted nothing did not pass everything."""
    store = ArtifactStore(tmp_path / "harness-runs")
    run = _run()
    run.evaluations.append(
        EvaluationOutcome(
            evaluator="functional_correctness",
            outcome=UNAVAILABLE,
            reason="the harness execution engine is not wired",
        )
    )
    run.finish()
    store.begin(run)
    _, md_path = write_report(run, store)
    markdown = md_path.read_text(encoding="utf-8")

    assert "UNAVAILABLE" in markdown
    assert "No assertions were evaluated" in markdown
    assert "check_pass_rate" in markdown
    assert "unavailable" in markdown
    assert "1.0000" not in markdown


def test_a_run_with_no_dataset_stamp_cannot_be_reported_on(
    tmp_path: pathlib.Path,
) -> None:
    """`EvalReport` refuses construction without a dataset version because a
    score that drops has three candidate explanations and only the stamp
    separates the third. This module does not work around that refusal."""
    store = ArtifactStore(tmp_path / "harness-runs")
    run = _run()
    run.dataset_versions = {}
    run.finish()
    store.begin(run)
    with pytest.raises(ReportError, match="dataset versions"):
        write_report(run, store)


# ── Telling a broken product from a broken stack ─────────────────────────────
#
# Both of these were found by DELIBERATELY breaking something and watching what
# the harness said, which is the only way to test a classifier whose whole job
# is to be right about which of two things happened.


def test_an_unreachable_database_is_classified_before_it_becomes_a_traceback() -> None:
    """The three shapes of "the stack is not there", enumerated.

    asyncpg raises its own `PostgresError` at CONNECT time, before SQLAlchemy
    has anything to wrap, so catching `DBAPIError` alone leaves a missing
    database escaping as a traceback and exiting 1. In CI that is
    indistinguishable from a product regression, which is the one distinction
    the three exit codes exist to make. `OSError` is the same story for a
    server nobody is listening on.
    """
    import asyncpg
    from sqlalchemy.exc import DBAPIError

    from harness.runner import _UNREACHABLE_DATABASE

    assert DBAPIError in _UNREACHABLE_DATABASE
    assert asyncpg.PostgresError in _UNREACHABLE_DATABASE
    assert OSError in _UNREACHABLE_DATABASE
    assert issubclass(asyncpg.exceptions.InvalidCatalogNameError, _UNREACHABLE_DATABASE)


def test_a_connection_failure_is_described_by_its_driver_error() -> None:
    """`DBAPIError.orig` or nothing. Without the unwrap every connection
    problem reads as the same generic wrapper, and a reader cannot tell a
    missing database from a rejected password."""
    from sqlalchemy.exc import OperationalError

    from harness.runner import _describe

    original = RuntimeError('database "nope" does not exist')
    wrapped = OperationalError("SELECT 1", {}, original)
    assert _describe(wrapped) == 'RuntimeError: database "nope" does not exist'
    assert _describe(original) == 'RuntimeError: database "nope" does not exist'


def test_an_aborted_workload_sinks_the_run_rather_than_excusing_it() -> None:
    """A route that RAISED must produce `fail`, never `unavailable`.

    The runner records the abort as a failing check of its own, and this is the
    arithmetic that then has to hold: one failing check outranks any number of
    evaluators that could not compute. Measured by reintroducing the 2026-09-20
    soft-delete defect in memory: before the fix the run reported `unavailable`
    with `UniqueViolationError` visible in the trajectory and nowhere in the
    verdict, so a pipeline reading the exit code saw "could not measure".
    """
    run = RunRecord(
        run_id="r",
        started_at="2026-09-22T00:00:00+00:00",
        scenario_id="s",
        scenario_version=1,
        tier=Tier.REGRESSION,
        world="no_state",
        workload=("x",),
        faults=(),
        seed=1,
        dispatch_backend="record",
        git_sha=None,
        git_branch=None,
        git_dirty=None,
    )
    run.checks.append(
        Check(
            kind="output",
            expression="the workload ran to completion",
            expected="every declared step completed",
            actual="step 'x' raised IntegrityError",
            passed=False,
        )
    )
    run.evaluations.append(
        EvaluationOutcome(
            evaluator="state_correctness",
            outcome=UNAVAILABLE,
            reason="no state assertion was evaluated",
        )
    )
    run.finish()
    assert run.status == FAIL


def test_a_ratio_bound_is_not_applied_below_its_floor() -> None:
    """A proportional rise off a tiny baseline is noise wearing a regression's
    clothes, and a gate that fires on noise is one somebody disables.

    Measured while verifying this harness: `safety.the_release_gate_...`
    baselines at about a second, so a 0.5 ratio put its ceiling at 1.5 seconds,
    and the same scenario replayed on a machine carrying five other agents took
    21. Both halves are asserted, because a floor that swallowed a real
    slowdown would be worse than the noise it removed.
    """
    threshold = Threshold(
        metric="duration_seconds",
        max_increase=None,
        max_increase_ratio=0.5,
        reason="a ratio, because the same scenario runs on a laptop and in CI",
        min_baseline=10.0,
    )
    assert threshold.exceeded_by(1.0, 21.0) is False
    assert "not applied" in threshold.describe_bound(1.0)
    assert threshold.exceeded_by(20.0, 40.0) is True
    assert threshold.describe_bound(20.0) == "at most 30"
    # ZERO IS BELOW THE FLOOR TOO. The any-rise-off-zero rule exists for a
    # count metric, whose healthy permanent state is zero; a duration that
    # baselined at 0 is a sub-second scenario, and 0 to 1 second is the
    # machine noise the floor absorbs. Checking zero before the floor fired
    # the gate on exactly that (2026-09-23, CI), which is the fires-on-noise
    # failure this docstring opens with.
    assert threshold.exceeded_by(0.0, 1.0) is False
    assert "not applied" in threshold.describe_bound(0.0)


def test_a_floor_beside_an_absolute_bound_is_refused() -> None:
    """The pair reads as a second bound nobody applies, which is the shape the
    two-bounds refusal already exists to prevent."""
    with pytest.raises(ThresholdError, match="min_baseline"):
        Threshold(
            metric="checks_failed",
            max_increase=0.0,
            max_increase_ratio=None,
            reason="a failing assertion is a regression",
            min_baseline=10.0,
        )


def test_the_shipped_duration_threshold_carries_its_floor() -> None:
    """The floor is DATA beside the ratio, not a constant in the comparison."""
    duration = load_thresholds()["duration_seconds"]
    assert duration.max_increase_ratio == 0.5
    assert duration.min_baseline == 10.0
