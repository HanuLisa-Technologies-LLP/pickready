"""The identity table, the agent cards, and the execution envelope.

What is asserted here is that the naming layer POINTS AT the runtime that
already existed rather than duplicating it, that a card publishes capability and
nothing else, and that an envelope carries enough to reconstruct a run without
carrying anything that would be unsafe to persist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import agent_loop
from app.services.agents import envelope as env
from app.services.agents import identity
from app.services.orchestration import router
from app.services.reliability import budget as budgeting
from app.services.tools import permissions

# ── identity ─────────────────────────────────────────────────────────────────


def test_the_identity_table_is_internally_sound() -> None:
    """A runtime id nothing routes to, or an artifact nobody produces, is a
    naming table that describes a pipeline the code does not have."""
    assert identity.validate_identities() == []


def test_every_named_agent_executes_as_a_known_runtime_agent() -> None:
    """A runtime id absent from the permission table would hold no tools, and
    `granted_tools` denies by default -- so the agent would fail at its first
    tool call rather than at the front door."""
    for agent_id, agent in identity.AGENTS.items():
        assert agent.runtime_id in permissions.AGENTS, agent_id
        assert agent.runtime_id in router.ROUTES.values(), agent_id


def test_miti_holds_no_extract_jd_tool() -> None:
    """A scorer that can re-read the JD can grade against the source rather than
    against the locked matrix the candidate was actually assessed on, which is
    the one property making two reports on a job comparable."""
    assert "extract_jd" not in identity.granted_tools(identity.MITI)
    assert "extract_assessment" in identity.granted_tools(identity.MITI)


def test_reach_resolves_through_the_one_permission_table() -> None:
    """A second copy of the grants drifts, and the drift is a silently widened
    permission."""
    for agent_id, agent in identity.AGENTS.items():
        assert identity.granted_tools(agent_id) == permissions.granted_tools(
            agent.runtime_id
        )


def test_an_unknown_agent_is_refused_rather_than_defaulted() -> None:
    """Defaulting to some agent would hand the caller that agent's tool grant."""
    with pytest.raises(identity.UnknownAgent):
        identity.get("nonexistent")


def test_sutra_and_yukti_activate_concurrently() -> None:
    """Serialising them would add a whole matrix generation to the wall clock
    before the first AI Score appears, for no dependency that exists."""
    step = [group for group in identity.ACTIVATION if identity.SUTRA in group][0]
    assert identity.YUKTI in step


# ── agent cards ──────────────────────────────────────────────────────────────


def test_an_agent_card_carries_no_credential_prompt_or_memory_handle() -> None:
    """The point of publishing a card is that another agent can discover what
    this one DOES without reaching into how it does it."""
    forbidden = (
        "key",
        "secret",
        "token",
        "prompt",
        "credential",
        "password",
        "memory",
        "endpoint",
    )
    for card in identity.agent_cards():
        for field_name in card:
            assert not any(word in field_name.casefold() for word in forbidden), (
                card["id"],
                field_name,
            )


def test_every_card_publishes_at_least_one_skill() -> None:
    """A card with no skills is an agent nothing can discover, which makes the
    card decorative."""
    for card in identity.agent_cards():
        assert card["skills"], card["id"]


# ── envelope ─────────────────────────────────────────────────────────────────


def _envelope() -> env.Envelope:
    return env.Envelope.for_run(
        tenant_id="tenant-1",
        agent_id=identity.SUTRA,
        task_type=router.TASK_JOB_SETUP,
        interactive=False,
        job_id="job-1",
        context_version="ctx-9",
        prompt_version="prompt-4",
        policy_version="policy-2",
        plan_version="plan-7",
    )


def test_child_propagates_the_workflow_and_names_its_parent() -> None:
    """Bodha through Siddhi is one workflow across six agents and several Celery
    tasks; without a shared id, one candidate's assessment is six unrelated
    trace queries."""
    parent = _envelope()
    child = parent.child(identity.MITI)

    assert child.workflow_id == parent.workflow_id
    assert child.parent_task_id == parent.task_id
    assert child.task_id != parent.task_id
    assert child.execution_id != parent.execution_id
    assert child.agent_id == identity.MITI


def test_child_cannot_be_handed_a_different_tenant() -> None:
    """A sub-task with a caller-supplied scope is the cross-tenant read this
    envelope exists to make impossible to write by accident."""
    parent = _envelope()
    child = parent.child(identity.MITI)
    assert child.tenant_id == parent.tenant_id
    assert child.job_id == parent.job_id


def test_the_versions_travel_so_a_run_can_be_reconstructed() -> None:
    """A finalised report states grades against criteria, and the only
    defensible answer to a dispute is to rebuild the run from the immutable
    versions it was written from."""
    child = _envelope().child(identity.MITI)
    for key in (
        "context_version",
        "agent_version",
        "prompt_version",
        "policy_version",
        "plan_version",
    ):
        assert child.as_dict()[key] is not None


def test_the_envelope_is_frozen() -> None:
    """A stage that edited a version mid-run would produce a trace claiming a
    version the first half was not written against."""
    with pytest.raises(Exception):
        _envelope().context_version = "other"  # type: ignore[misc]


def test_serialising_the_envelope_carries_identifiers_and_no_content() -> None:
    """It is handed to the trace whole, so the safety has to be a property of
    the shape rather than of the caller's discipline."""
    payload = _envelope().as_dict()
    flat = " ".join(str(v).casefold() for v in payload.values())
    for word in ("resume", "answer", "transcript", "remark", "prompt_text"):
        assert word not in flat


def test_the_budget_is_derived_from_the_modules_that_own_the_limits() -> None:
    """A second copy of a ceiling is a ceiling that eventually stops agreeing
    with the one somebody thought they had lowered."""
    interactive = env.RunBudget.for_task("interviewer", interactive=True)
    background = env.RunBudget.for_task("ppi_report", interactive=False)

    assert interactive.max_steps == budgeting.MAX_ITERATIONS
    assert interactive.max_retries == budgeting.MAX_REPLANS
    assert interactive.max_latency_ms == int(agent_loop.INTERACTIVE_DEADLINE * 1000)
    assert background.max_latency_ms == int(agent_loop.BACKGROUND_DEADLINE * 1000)
    assert background.max_latency_ms > interactive.max_latency_ms


def test_an_expired_deadline_is_visible_without_a_clock_argument_being_wrong() -> None:
    """A deadline nothing reads is documentation."""
    envelope = _envelope()
    assert not envelope.expired()
    assert envelope.expired(datetime.now(timezone.utc) + timedelta(days=1))


# =============================================================================
# A NAME MUST NOT POINT AT CODE NOTHING REACHES
# =============================================================================
#
# THE DEFECT. `implemented_by` read `services/ppi`, `services/matching`,
# `services/functional_assessment` and so on -- the OLD modules, in a path-like
# spelling nothing could resolve -- while the three-layer framework in
# `hiring/`, `miti/` and `siddhi/` was imported by no route and no worker.
#
# The consequence was not cosmetic. Every log line and every A2A artifact showed
# Bodha, Sutra, Yukti, Vaada, Miti and Siddhi running and succeeding, so anybody
# reading a trace would have concluded Part A was live. It was not. All four
# pipeline gates were real, arithmetic, provider-free checks guarding nothing,
# because their only caller was a module nothing imported.
#
# EVERY UNIT TEST IN THIS FILE PASSED THROUGHOUT. That is the point: no test of
# any module can see this, because the question is not "is this module correct"
# but "can a request handler get to it". So the check is over the IMPORT GRAPH,
# computed statically from `app/api/**`, `app/workers/**` and `app/main.py`.
#
# Static rather than by importing, deliberately. Importing the package to find
# out would answer what pytest's import order happens to have loaded, which is
# the same ordering luck that hid the import-cycle defect for weeks.

from app.orchestration_checks import (  # noqa: E402
    reachable_modules,
    unreachable_agent_modules,
)


def test_the_reachability_graph_finds_the_obvious_entry_points() -> None:
    """A guard whose analyser silently returned an empty set would pass every
    assertion below for the wrong reason, so the analyser is checked first."""
    reachable = reachable_modules()
    assert "app.services.matching" in reachable
    assert "app.services.functional_assessment" in reachable
    # A module that exists and that nothing imports. If this were reported as
    # reachable, the graph would be over-approximating and the guard useless.
    assert "app.scripts.worked_example" not in reachable
    assert len(reachable) > 100


@pytest.mark.parametrize("agent_id", sorted(identity.AGENTS))
def test_every_agent_name_resolves_to_reachable_code(agent_id: str) -> None:
    """THE TEST THAT WOULD HAVE CAUGHT IT.

    Every module an agent says implements it must be transitively importable
    from a route or a worker. A name pointing at unreachable code makes every
    log line and every artifact claim work that cannot have happened.
    """
    reachable = reachable_modules()
    agent = identity.get(agent_id)
    assert agent.implemented_by, f"{agent_id} names no implementing module"
    unreachable = [m for m in agent.implemented_by if m not in reachable]
    assert not unreachable, (
        f"{agent.name} says it is implemented by {unreachable}, which no route "
        "or worker can reach."
    )


@pytest.mark.parametrize("agent_id", sorted(identity.AGENTS))
def test_every_named_module_is_an_importable_dotted_path(agent_id: str) -> None:
    """The previous spelling, `services/ppi`, could not be resolved, imported or
    checked. A shape rule here is what stops the next entry being written that
    way and quietly opting out of the reachability check."""
    agent = identity.get(agent_id)
    for module in agent.implemented_by + agent.activates_to:
        assert module.startswith("app.services."), module
        assert "/" not in module, module


def test_the_map_cannot_lag_behind_an_activation() -> None:
    """The ratchet. The moment an agent's Part A implementation becomes
    reachable, `implemented_by` must name some of it -- otherwise the table
    still points at the module Part A replaced, which is the original defect
    reappearing one stage at a time.

    Granularity is per AGENT rather than per file on purpose: a per-file rule
    would go red every time a collaborator lands one more module of a stage
    that is already correctly mapped.
    """
    assert unreachable_agent_modules() == []


def test_every_agent_declares_the_part_a_modules_it_activates_to() -> None:
    """`activates_to` is what makes the ratchet possible. An agent with an empty
    one opts itself out of the check silently."""
    for agent_id, agent in identity.AGENTS.items():
        assert agent.activates_to, (
            f"{agent_id} declares no Part A target, so nothing can notice when "
            "its stage goes live"
        )


def test_activation_status_reports_what_has_not_landed_yet() -> None:
    """Honest reporting of the frontier, so a skip in the journey test can name
    the stage rather than saying "something is missing"."""
    status = identity.activation_status(reachable_modules())
    assert set(status) == set(identity.AGENTS)
    for agent_id, row in status.items():
        assert set(row) >= {
            "implemented_by",
            "activates_to",
            "activated",
            "activated_but_unmapped",
            "not_yet_reachable",
            "live_but_unreachable",
        }, agent_id
