"""The shipped scenario corpus, validated without a stack behind it.

WHY THIS IS A SEPARATE MODULE FROM `test_harness_core.py`
----------------------------------------------------------
`test_harness_core.py` tests the LOADER against scenarios it writes itself, so
its assertions hold whatever is in `harness/scenarios/`. These tests are about
the DIRECTORY THAT SHIPS: every name a scenario writes down resolves to
something that exists, every tier CI fails on is actually populated, and no
scenario quietly asserts nothing.

The distinction matters because the two fail for different reasons and need
different fixes. A loader bug is a harness defect; a scenario naming
`read_candidate_tabel` is a typo whose only symptom, without this module, is a
run that executes less than it claimed and still reports a pass on whatever
assertions happened not to depend on the missing step. `workload.run_step`
refuses an unknown name at RUN time, which is the right behaviour and is far
too late: it costs a stack, a database and several minutes to find out.

NOTHING HERE OPENS A CONNECTION OR MAKES A REQUEST. Resolution is a question
about registries, and a registry is a dict. That is what makes it affordable to
run this on every commit, ahead of the tiers, so a misspelled probe name is a
red dot in seconds rather than an `unavailable` twenty minutes in.
"""
from __future__ import annotations

import pathlib

import pytest

from harness import faults, probes
from harness.assertions import parse
from harness.cli import SCENARIOS_DIR
from harness.evaluators import registered as registered_evaluators
from harness.scenario import DEFAULT_TIERS, Scenario, Tier, load_all
from harness.workload import registered as registered_steps
from harness.world import registered as registered_worlds

#: Loaded once. `load_all` is pure text work with no stack behind it, and
#: parsing twenty-five files per test would turn a cheap module into a slow one
#: for no extra coverage.
SCENARIOS: tuple[Scenario, ...] = load_all(SCENARIOS_DIR)

#: The tiers CI fails on outright (HARNESS.md section 9). Each one must be
#: POPULATED, because `select` refuses a run over zero scenarios and a tier
#: that quietly emptied would take its gate with it: the failure would be a
#: `ScenarioError` about an empty selection, which reads as a harness problem
#: rather than as "nobody is checking safety any more".
GATING_TIERS: tuple[Tier, ...] = (Tier.ADVERSARIAL, Tier.SAFETY)


def _ids(scenarios: tuple[Scenario, ...]) -> list[str]:
    return [item.id for item in scenarios]


def test_the_directory_is_not_empty_and_every_file_loaded() -> None:
    """A corpus that silently became empty would pass every other test here."""
    assert SCENARIOS, f"no scenarios loaded from {SCENARIOS_DIR}"
    on_disk = sorted(
        path
        for pattern in ("*.yaml", "*.yml")
        for path in SCENARIOS_DIR.rglob(pattern)
    )
    assert len(SCENARIOS) == len(on_disk), (
        f"{len(on_disk)} scenario file(s) on disk produced {len(SCENARIOS)} "
        "loaded scenario(s)"
    )


@pytest.mark.parametrize("tier", DEFAULT_TIERS, ids=lambda item: item.value)
def test_every_default_tier_has_at_least_one_scenario(tier: Tier) -> None:
    """`harness run` with no `--tier` runs these five, and each must be real.

    Not merely the two gating tiers: a `regression` tier with nothing in it
    would make `harness compare` answer about a baseline nothing produced, and
    the regression gate would read green for ever.
    """
    populated = [item for item in SCENARIOS if item.tier is tier]
    assert populated, f"tier {tier.value} has no scenario"


@pytest.mark.parametrize("tier", GATING_TIERS, ids=lambda item: item.value)
def test_the_tiers_ci_fails_outright_on_are_populated(tier: Tier) -> None:
    populated = [item for item in SCENARIOS if item.tier is tier]
    assert len(populated) >= 2, (
        f"tier {tier.value} carries {len(populated)} scenario(s). CI fails "
        "outright on this tier, and a tier with almost nothing in it is a gate "
        "that reads green because nobody is asking it anything."
    )


def test_every_scenario_names_a_world_that_exists() -> None:
    known = set(registered_worlds())
    unknown = {
        item.id: item.given.world
        for item in SCENARIOS
        if item.given.world not in known
    }
    assert not unknown, (
        f"scenario(s) naming a world the registry does not hold: {unknown}. "
        f"Known worlds: {', '.join(sorted(known))}"
    )


def test_every_workload_step_exists() -> None:
    known = set(registered_steps())
    unknown = {
        item.id: [step for step in item.workload if step not in known]
        for item in SCENARIOS
        if any(step not in known for step in item.workload)
    }
    assert not unknown, (
        f"scenario(s) naming a workload step that does not exist: {unknown}. "
        f"Known steps: {', '.join(sorted(known))}"
    )


def test_every_scenario_actually_does_something() -> None:
    """An empty workload makes every assertion a statement about the seed.

    The loader permits it, deliberately: `given` plus `expect` is a legitimate
    shape for a scenario whose subject is a world builder. Nothing in this
    corpus is that, and a scenario that lost its workload in an edit would
    otherwise keep passing while exercising no route at all.
    """
    idle = [item.id for item in SCENARIOS if not item.workload]
    assert not idle, f"scenario(s) with no workload: {idle}"


def test_every_fault_resolves() -> None:
    known = set(faults.registered())
    unknown = {
        item.id: [fault.name for fault in item.faults if fault.name not in known]
        for item in SCENARIOS
        if any(fault.name not in known for fault in item.faults)
    }
    assert not unknown, (
        f"scenario(s) naming a fault the layer does not have: {unknown}. "
        f"Known faults: {', '.join(sorted(known))}"
    )


def test_every_evaluator_resolves() -> None:
    """An unknown evaluator answers `unavailable`, which BLOCKS and never passes.

    So this is not defending against a silent pass; it is defending against a
    typo that costs a whole run and reads, in the report, as a harness that
    could not compute. Caught here, it is one line of output.
    """
    known = set(registered_evaluators())
    unknown = {
        item.id: [name for name in item.expect.evaluators if name not in known]
        for item in SCENARIOS
        if any(name not in known for name in item.expect.evaluators)
    }
    assert not unknown, (
        f"scenario(s) naming an evaluator that is not registered: {unknown}. "
        f"Known evaluators: {', '.join(sorted(known))}"
    )


def test_every_state_probe_resolves() -> None:
    """The selector, with its optional `:argument` split off exactly as the
    probe registry splits it. A scenario writing `audit_log.count:some_action`
    names one probe and one argument, and validating the whole string would
    refuse a correct scenario."""
    known = set(probes.state_names())
    unknown: dict[str, list[str]] = {}
    for item in SCENARIOS:
        missing = []
        for expression in item.expect.state:
            selector = parse(expression).selector
            name = selector.partition(":")[0].strip()
            if name not in known:
                missing.append(name)
        if missing:
            unknown[item.id] = missing
    assert not unknown, (
        f"scenario(s) naming a state probe that does not exist: {unknown}. "
        f"Known probes: {', '.join(sorted(known))}"
    )


def test_every_prohibited_outcome_resolves() -> None:
    known = set(probes.prohibited_names())
    unknown = {
        item.id: [name for name in item.expect.prohibited if name not in known]
        for item in SCENARIOS
        if any(name not in known for name in item.expect.prohibited)
    }
    assert not unknown, (
        f"scenario(s) naming a prohibited outcome that does not exist: "
        f"{unknown}. Known: {', '.join(sorted(known))}"
    )


def test_every_output_and_state_assertion_parses() -> None:
    """A malformed expression is reported as `unavailable` at run time.

    Which is correct and is also the most expensive possible moment to find a
    missing operator: the stack is up, the world is seeded, the requests have
    been made, and the run reports that it could not judge any of it.
    """
    broken: dict[str, list[str]] = {}
    for item in SCENARIOS:
        failures = []
        for expression in tuple(item.expect.state) + tuple(item.expect.output):
            try:
                parse(expression)
            except ValueError as exc:
                failures.append(f"{expression!r}: {exc}")
        if failures:
            broken[item.id] = failures
    assert not broken, f"scenario(s) with an unparseable assertion: {broken}"


def test_a_safety_scenario_asserts_an_absence() -> None:
    """Safety is asserted as an ABSENCE, and one nobody named is one nobody
    checked. `safety_and_policy` says exactly that and reports `unavailable`
    for a scenario with no prohibited outcome, so a safety scenario that lost
    its `prohibited` block would block the tier rather than pass it -- and it
    would do so with a message about the evaluator rather than about the
    scenario."""
    silent = [
        item.id
        for item in SCENARIOS
        if item.tier is Tier.SAFETY and not item.expect.prohibited
    ]
    assert not silent, (
        f"safety scenario(s) naming no prohibited outcome: {silent}"
    )


def test_a_fault_scenario_judges_its_degradation() -> None:
    """A fault must be OBSERVABLE in the result (HARNESS.md section 4).

    "The point is never that the system survived. It is that the system
    DEGRADED THE WAY IT SAID IT WOULD." A scenario that injects a fault and
    names neither `degradation_honesty` nor `recovery` has measured that the
    process stayed up, which is the silent-survival pass the contract calls a
    finding.
    """
    judged = {"degradation_honesty", "recovery"}
    unjudged = [
        item.id
        for item in SCENARIOS
        if item.faults and not judged.intersection(item.expect.evaluators)
    ]
    assert not unjudged, (
        "scenario(s) injecting a fault without judging the degradation or the "
        f"recovery: {unjudged}"
    )


def test_no_scenario_file_carries_an_em_dash() -> None:
    """Rule 7, over the corpus, including the descriptions a report quotes.

    Built from its code point rather than typed, for the reason
    `test_platform_audit.py` already gives: a repository-wide sweep for the
    character would otherwise rewrite the code that detects it.
    """
    em_dash = chr(8212)
    offenders = [
        str(path)
        for pattern in ("*.yaml", "*.yml")
        for path in SCENARIOS_DIR.rglob(pattern)
        if em_dash in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"scenario file(s) containing an em dash: {offenders}"


def test_the_regression_tier_names_the_defects_it_pins() -> None:
    """A regression scenario describes a defect that WAS OBSERVED.

    Checked as a floor on the description rather than as prose analysis: a
    regression case written against an imagined defect passes as soon as the
    code exists and stops testing anything the moment the implementation is
    replaced, which is the rule `app/evaluation/regression.py` states for its
    own registry. A description long enough to say what broke, when, and what
    the symptom was is the cheapest enforceable proxy.
    """
    thin = {
        item.id: len(item.description)
        for item in SCENARIOS
        if item.tier is Tier.REGRESSION and len(item.description) < 200
    }
    assert not thin, (
        "regression scenario(s) whose description does not say what actually "
        f"broke: {thin}"
    )


def test_ids_are_namespaced_by_their_tier() -> None:
    """`--scenario` takes an id, and a report prints one. An id whose prefix
    disagrees with its tier sends a reader to the wrong section of this file
    and makes `harness list` unreadable when it is twenty rows long."""
    wrong = {
        item.id: item.tier.value
        for item in SCENARIOS
        if not item.id.startswith(f"{item.tier.value}.")
    }
    assert not wrong, (
        f"scenario id(s) not prefixed with their tier: {wrong}"
    )


def test_every_scenario_id_is_unique_and_stable_enough_to_replay() -> None:
    assert len(set(_ids(SCENARIOS))) == len(SCENARIOS)
    assert all(item.version >= 1 for item in SCENARIOS)
