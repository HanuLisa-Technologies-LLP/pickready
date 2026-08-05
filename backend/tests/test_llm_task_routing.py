"""Every task type the code CALLS must be one the router can ROUTE.

THE DEFECT THIS EXISTS TO PREVENT
---------------------------------
`conversation_turn` was added to `TASK_TEMPERATURE` on 2026-08-04 when the
adaptive interview was built, and to nothing else. `TASK_ROUTES` never gained
it. `provider_order` raises ValueError on an unknown task type deliberately, so
a typo fails loudly rather than silently picking an arbitrary provider chain.

Loudly, except for where it was called from. Every caller in the conversation
path catches broadly and degrades to the scripted question, because that IS the
right answer to a provider outage with a candidate mid-assessment. So the guard
that protects candidates from an outage perfectly concealed a config typo:

  * 100% of conversational LLM calls raised ValueError
  * 100% degraded to the stored question
  * the interview looked exactly as unadaptive as before any of the work
  * every test passed, because every test stubs `invoke_llm`
  * every deploy was green

It was found by probing production, not by CI, which is the third time in two
days that a config-shaped defect reached a customer behind a green pipeline.

WHY A GREP AND NOT A TYPE
-------------------------
`TaskType` is a `Literal`, and nothing enforces a Literal at runtime -- the call
sites pass plain strings and mypy is not in this pipeline. The only check that
would actually have failed is one that reads the call sites, so that is what
this does.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import llm_providers

#: Every `invoke_llm("...")` first argument in the app tree.
_CALL_RE = re.compile(r"invoke_llm\(\s*[\"']([a-z_]+)[\"']", re.MULTILINE)

_APP = Path(__file__).resolve().parents[1] / "app"


def _called_task_types() -> dict[str, list[str]]:
    """Task type -> the files that call it."""
    found: dict[str, list[str]] = {}
    for path in _APP.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for task_type in _CALL_RE.findall(source):
            found.setdefault(task_type, []).append(str(path.relative_to(_APP)))
    return found


def test_the_grep_finds_the_call_sites() -> None:
    """Guards the guard. A regex that matched nothing would pass forever."""
    called = _called_task_types()
    assert called, "found no invoke_llm call sites at all; the pattern is wrong"
    assert "conversation_turn" in called, (
        "the conversation path no longer calls invoke_llm with "
        "conversation_turn; if that is intentional, update this test"
    )


def test_every_called_task_type_is_routable() -> None:
    """The one assertion that would have caught the live defect."""
    unroutable = {
        task: files
        for task, files in _called_task_types().items()
        if not llm_providers.is_known_task(task)
    }
    assert not unroutable, (
        "these task types are called in code but are missing from "
        f"TASK_ROUTES, so every call raises ValueError: {unroutable}"
    )


@pytest.mark.parametrize("task_type", sorted(llm_providers.TASK_ROUTES))
def test_every_route_is_completely_configured(task_type: str) -> None:
    """A route with no timeout or budget silently inherits a default written for
    something else. `conversation_turn` is interactive and would have taken the
    45s background default, which is three full seconds longer than the whole
    turn is allowed to take."""
    order = llm_providers.provider_order(task_type)
    assert order, f"{task_type} has an empty provider order"
    assert set(order) <= set(llm_providers.PROVIDERS), (
        f"{task_type} routes to a provider that does not exist: {order}"
    )
    assert len(set(order)) == len(order), f"{task_type} lists a provider twice"

    assert llm_providers.timeout_for(task_type) > 0
    assert llm_providers.retry_budget_for(task_type) > 0
    # The total budget must cover at least one full attempt, or the call is
    # dead on arrival: the first attempt would exceed the budget before it
    # could return.
    total = llm_providers.TASK_TOTAL_BUDGET.get(task_type)
    if total is not None:
        assert total >= llm_providers.timeout_for(task_type), (
            f"{task_type} cannot complete one attempt inside its total budget"
        )


def test_the_conversation_is_configured_as_interactive() -> None:
    """A candidate is watching a text box, and there can be TWO of these calls
    in one turn (classify, then write the question). The background defaults
    would make a turn feel broken even when nothing is wrong."""
    assert llm_providers.timeout_for("conversation_turn") <= 15.0
    assert llm_providers.TASK_TOTAL_BUDGET["conversation_turn"] <= 30.0


def test_the_temperature_table_and_the_routing_table_agree() -> None:
    """The two tables drifting apart is the exact shape of the original defect:
    a task type known to one and unknown to the other."""
    temperature_only = set(llm_providers.TASK_TEMPERATURE) - set(
        llm_providers.TASK_ROUTES
    )
    assert not temperature_only, (
        "these task types have a sampling temperature but no route, so every "
        f"call raises: {sorted(temperature_only)}"
    )
