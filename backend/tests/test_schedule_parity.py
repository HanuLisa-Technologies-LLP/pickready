"""The Python schedule and the Terraform schedule must agree.

WHY THIS TEST EXISTS
--------------------
`app/workers/schedule.py` is the source of truth for the periodic sweeps, and
each environment root mirrors it as EventBridge Scheduler rules. Two copies of
one fact stay honest only when something compares them, which is the discipline
`test_runbook_parity.py` already applies to the hiring weights.

The failure this prevents is specific and this codebase has paid for it once
already: a beat entry fired `pickready.probe_llm_models` every hour for a whole
release after the module it imported was deleted, and nothing in the suite
touched it because nothing in the suite ever called it. Half of that is caught
by `test_task_registry.py`, which asserts every scheduled entry names a task
the registry has. The other half is here: an entry that exists in one place and
not the other.

The two halves fail differently and both matter.

  A rule in Terraform with no entry in Python is a rule that fires a task name
  the application may not have. It costs one CloudWatch error every interval,
  for ever, with nobody reading it.

  An entry in Python with no rule in Terraform is a sweep that NEVER RUNS. That
  one is silent by construction: the reconciliation sweeps do nothing when
  there is nothing to repair, so "not running" and "nothing to do" produce the
  same empty log.

WHY IT PARSES THE TERRAFORM RATHER THAN A PLAN
-----------------------------------------------
A plan needs an AWS account. This assertion has to hold in CI with no
credentials at all, the same argument `test_deploy_secret_hygiene.py` makes for
reading `service_secrets` out of source.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.workers.schedule import SCHEDULE

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: Discovered, not listed, so an environment added without a schedule block is
#: still checked. DOT-PREFIXED DIRECTORIES ARE SKIPPED: `infra/plan-offline.sh`
#: copies each environment to a `.<env>-offline-plan` sibling (the module paths
#: are relative, so the copy has to sit at the same depth) and removes it on
#: exit. One left behind by an interrupted run is a scratch directory, not an
#: environment, and picking it up makes this suite fail for a reason that has
#: nothing to do with the schedule.
ENVIRONMENTS = sorted(
    path
    for path in (ROOT / "infra" / "environments").iterdir()
    if path.is_dir() and not path.name.startswith(".")
)

#: One `"<rule name>" = { task = "...", rate_expression = "..." }` entry.
_ENTRY = re.compile(
    r'"(?P<rule>[\w-]+)"\s*=\s*\{\s*'
    r'task\s*=\s*"(?P<task>[\w.]+)"\s*'
    r'rate_expression\s*=\s*"(?P<rate>[^"]+)"\s*'
    r"\}",
    re.MULTILINE,
)


def _terraform_schedule(environment: pathlib.Path) -> dict[str, tuple[str, str]]:
    """The `schedules` map from one environment's `module "scheduler"` block."""
    source = (environment / "main.tf").read_text(encoding="utf-8")
    start = source.index('module "scheduler"')
    block = source[start:]
    block = block[block.index("schedules = {") :]
    return {
        match.group("rule"): (match.group("task"), match.group("rate"))
        for match in _ENTRY.finditer(block)
    }


def _python_schedule() -> dict[str, tuple[str, str]]:
    return {entry.rule: (entry.task, entry.rate_expression) for entry in SCHEDULE}


def test_the_environments_are_discovered_rather_than_listed() -> None:
    """A guard on the guard.

    Every assertion below is parametrised over what is on disk, so an
    environment added without a schedule block would simply not be checked if
    this list came back empty or short.
    """
    names = {path.name for path in ENVIRONMENTS}
    assert {"pilot", "staging", "production"} <= names, names


@pytest.mark.parametrize("environment", ENVIRONMENTS, ids=lambda p: p.name)
def test_every_terraform_rule_matches_the_python_schedule(
    environment: pathlib.Path,
) -> None:
    terraform = _terraform_schedule(environment)
    python = _python_schedule()

    assert terraform, (
        f"{environment.name}: no schedules parsed out of its scheduler module. "
        "Either the block moved or the entry shape changed, and a parser that "
        "silently matches nothing would let every assertion below pass by vacuum."
    )

    missing_in_terraform = sorted(set(python) - set(terraform))
    assert not missing_in_terraform, (
        f"{environment.name} has no rule for {missing_in_terraform}. Those "
        "sweeps would never run, and a reconciliation sweep that never runs is "
        "indistinguishable from one with nothing to repair."
    )

    missing_in_python = sorted(set(terraform) - set(python))
    assert not missing_in_python, (
        f"{environment.name} schedules {missing_in_python}, which "
        "app/workers/schedule.py does not declare. A rule naming a task the "
        "registry may not have costs one error every interval, for ever."
    )

    for rule, (task, rate) in python.items():
        assert terraform[rule] == (task, rate), (
            f"{environment.name}: rule {rule!r} is {terraform[rule]} in "
            f"Terraform and {(task, rate)} in Python."
        )


def test_the_rate_expressions_are_well_formed() -> None:
    """`rate(N unit)` with the unit agreeing with N.

    AWS refuses `rate(1 minutes)` and `rate(5 minute)`, and the error arrives at
    apply time naming the expression rather than the entry that built it.
    """
    for entry in SCHEDULE:
        match = re.fullmatch(r"rate\((\d+) (minute|minutes|hour|hours)\)", entry.rate_expression)
        assert match, f"{entry.rule}: {entry.rate_expression!r} is not a rate expression"
        count = int(match.group(1))
        plural = match.group(2).endswith("s")
        assert plural == (count != 1), (
            f"{entry.rule}: {entry.rate_expression!r} mixes a count and a unit "
            "AWS will refuse at apply time"
        )


def test_every_scheduled_rule_carries_a_reason() -> None:
    """`why` is not decoration.

    A sweep nobody can justify is a sweep nobody will delete when it stops
    being needed, and the schedule is the one place in this product where the
    cost of an entry is invisible: it runs for ever and does nothing visible
    when there is nothing to repair.
    """
    for entry in SCHEDULE:
        assert len(entry.why.split()) >= 8, f"{entry.rule} has no real reason recorded"
