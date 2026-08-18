"""The tool layer: what it refuses, what it validates, and what it bounds.

These are the properties an agent calling a tool depends on and cannot check
for itself:

  * a tool an agent was not granted is refused BEFORE the handler runs, so a
    successful prompt injection can still only name a tool the agent holds;
  * a handler's bad output shape is the TOOL's failure and is raised rather
    than handed on, because a caller cannot tell an invented empty result from
    a real one;
  * retries are bounded by attempts AND by a wall clock that PREDICTS the next
    attempt, the same rule `agent_loop` settled on after a 26-second deadline
    permitted a 48-second request;
  * nothing that reaches telemetry carries a payload.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from app.services import tools
from app.services.tools import errors, executor, permissions, registry, telemetry


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 1


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubled: int


@pytest.fixture
def sandbox(monkeypatch):
    """An empty registry and a permissive matrix, restored afterwards.

    Registering throwaway tools into the real registry would make one test's
    fixtures visible to the audit tests below, which is precisely the drift
    those tests exist to catch.
    """
    snapshot = registry._reset_for_tests()
    monkeypatch.setattr(permissions, "AGENT_TOOLS", {"tester": frozenset({"probe"})})
    try:
        yield
    finally:
        registry._restore_for_tests(snapshot)


def _spec(handler, **overrides) -> registry.ToolSpec:
    defaults = dict(
        name="probe",
        handler=handler,
        input_model=_In,
        output_model=_Out,
        description="test tool",
        needs_session=False,
        max_attempts=2,
        timeout_seconds=0.5,
        deadline_seconds=2.0,
    )
    defaults.update(overrides)
    return registry.register(registry.ToolSpec(**defaults))


@pytest.mark.asyncio
async def test_a_granted_tool_returns_a_validated_model(sandbox) -> None:
    async def handler(payload: _In):
        return {"doubled": payload.value * 2}

    _spec(handler)
    result = await executor.execute("probe", "tester", {"value": 21})

    assert isinstance(result.value, _Out)
    assert result.value.doubled == 42
    assert result.attempts == 1
    assert result.cached is False


@pytest.mark.asyncio
async def test_an_ungranted_tool_is_refused_before_the_handler_runs(sandbox) -> None:
    calls: list[int] = []

    async def handler(payload: _In):
        calls.append(1)
        return {"doubled": 0}

    _spec(handler)
    with pytest.raises(errors.ToolPermissionError):
        await executor.execute("probe", "not_an_agent", {"value": 1})

    # The point of the ordering. A refusal that ran the handler first would have
    # already read the row it was refusing to let the agent see.
    assert calls == []


@pytest.mark.asyncio
async def test_an_unknown_tool_is_not_found_rather_than_crashing(sandbox) -> None:
    with pytest.raises(errors.ToolNotFound):
        await executor.execute("nonexistent", "tester", {})


@pytest.mark.asyncio
async def test_a_bad_payload_never_reaches_the_handler(sandbox) -> None:
    calls: list[int] = []

    async def handler(payload: _In):
        calls.append(1)
        return {"doubled": 1}

    _spec(handler)
    with pytest.raises(errors.ToolInputError):
        await executor.execute("probe", "tester", {"value": "not a number"})
    assert calls == []


@pytest.mark.asyncio
async def test_an_undeclared_input_field_is_rejected(sandbox) -> None:
    async def handler(payload: _In):
        return {"doubled": 2}

    _spec(handler)
    with pytest.raises(errors.ToolInputError):
        await executor.execute("probe", "tester", {"value": 1, "surprise": True})


@pytest.mark.asyncio
async def test_a_handler_returning_the_wrong_shape_is_the_tools_failure(sandbox) -> None:
    """Raised, never repaired. A shape the model refuses is a shape nothing
    downstream was written against, and repairing it means inventing a field."""

    async def handler(payload: _In):
        return {"doubled": "not an integer"}

    _spec(handler)
    with pytest.raises(errors.ToolOutputError):
        await executor.execute("probe", "tester", {"value": 1})


@pytest.mark.asyncio
async def test_a_bad_output_shape_is_never_retried(sandbox) -> None:
    """It is deterministic: the same inputs build the same bad shape."""
    calls: list[int] = []

    async def handler(payload: _In):
        calls.append(1)
        return {"wrong_key": 1}

    _spec(handler, max_attempts=3)
    with pytest.raises(errors.ToolOutputError):
        await executor.execute("probe", "tester", {"value": 1})
    assert calls == [1]


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_and_can_succeed(sandbox) -> None:
    calls: list[int] = []

    async def handler(payload: _In):
        calls.append(1)
        if len(calls) == 1:
            raise errors.RetryableToolError("probe", "upstream was briefly unavailable")
        return {"doubled": 4}

    _spec(handler, max_attempts=2)
    result = await executor.execute("probe", "tester", {"value": 2})
    assert result.value.doubled == 4
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_a_deterministic_failure_is_not_retried(sandbox) -> None:
    """Retrying a genuine bug three times only makes it three times slower."""
    calls: list[int] = []

    async def handler(payload: _In):
        calls.append(1)
        raise ValueError("this will fail identically every time")

    _spec(handler, max_attempts=3)
    with pytest.raises(errors.ToolExecutionError):
        await executor.execute("probe", "tester", {"value": 1})
    assert calls == [1]


@pytest.mark.asyncio
async def test_a_hanging_handler_is_bounded_by_the_per_attempt_timeout(sandbox) -> None:
    async def handler(payload: _In):
        await asyncio.sleep(5)
        return {"doubled": 0}

    _spec(handler, max_attempts=1, timeout_seconds=0.1)
    with pytest.raises(errors.ToolTimeout):
        await executor.execute("probe", "tester", {"value": 1})


@pytest.mark.asyncio
async def test_the_deadline_predicts_the_next_attempt(sandbox) -> None:
    """The scar this check exists for.

    One attempt bounded at 0.3s under a 0.4s deadline passes `elapsed >=
    deadline` as False, so a naive loop starts a second attempt and the real
    worst case is 0.6s. Predicting means the second attempt is never started.
    """
    calls: list[int] = []

    async def handler(payload: _In):
        calls.append(1)
        await asyncio.sleep(5)
        return {"doubled": 0}

    _spec(handler, max_attempts=3, timeout_seconds=0.3, deadline_seconds=0.4)
    with pytest.raises(errors.ToolTimeout):
        await executor.execute("probe", "tester", {"value": 1})
    assert calls == [1]


@pytest.mark.asyncio
async def test_a_tool_needing_a_session_refuses_to_run_without_one(sandbox) -> None:
    async def handler(payload: _In, *, session=None):
        return {"doubled": 1}

    _spec(handler, needs_session=True)
    with pytest.raises(errors.ToolInputError):
        await executor.execute("probe", "tester", {"value": 1})


def test_a_duplicate_registration_is_an_error_not_a_replacement(sandbox) -> None:
    """Silently overwriting would make the resolved tool depend on import order."""

    async def handler(payload: _In):
        return {"doubled": 1}

    _spec(handler)
    with pytest.raises(ValueError):
        _spec(handler)


def test_only_an_idempotent_tool_may_declare_a_cache_ttl(sandbox) -> None:
    async def handler(payload: _In):
        return {"doubled": 1}

    with pytest.raises(ValueError):
        _spec(handler, idempotent=False, cache_ttl_seconds=60)


@pytest.mark.asyncio
async def test_telemetry_records_identifiers_and_never_payloads(sandbox, caplog) -> None:
    async def handler(payload: _In):
        return {"doubled": payload.value * 2}

    _spec(handler)
    telemetry.reset_tool_stats()
    secret = 987654321
    with caplog.at_level("INFO", logger="app.services.tools.telemetry"):
        await executor.execute("probe", "tester", {"value": secret})

    stats = telemetry.tool_stats()["probe"]
    assert stats["calls"] == 1 and stats["ok"] == 1

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "probe" in logged and "tester" in logged
    # The input and the output are a real person's data everywhere but here.
    assert str(secret) not in logged
    assert str(secret * 2) not in logged


# ── Audit over the REAL registry ─────────────────────────────────────────────
# These run against the tools the product actually ships, and are the reason
# the permission matrix cannot quietly drift away from the registry.


def test_every_permission_grant_names_a_registered_tool() -> None:
    registered = tools.names()
    for agent, granted in permissions.AGENT_TOOLS.items():
        unknown = granted - registered
        assert not unknown, f"{agent} is granted tools that do not exist: {sorted(unknown)}"


def test_every_registered_tool_is_granted_to_someone() -> None:
    """A tool no agent holds is either dead code or a missing grant.

    Both are worth failing over: the second is a feature that silently does not
    work, which is this codebase's most expensive recurring defect.
    """
    for name in tools.names():
        assert permissions.agents_holding(name), f"no agent holds {name}"


def test_no_agent_holds_a_tool_outside_the_declared_agent_list() -> None:
    assert set(permissions.AGENT_TOOLS) <= set(permissions.AGENTS)


def test_the_email_agent_cannot_read_a_resume_or_a_transcript() -> None:
    """The narrowest grant that still works.

    An email states a decision that was already made. Holding the evidence
    behind it is reach the agent has no use for, and reach it does not have is
    reach a future prompt cannot accidentally start using.
    """
    granted = permissions.granted_tools(permissions.AGENT_EMAIL)
    assert "extract_resume" not in granted
    assert "extract_assessment" not in granted


def test_a_live_transcript_is_never_cached() -> None:
    """It grows between two reads by design.

    An agent scoring a transcript two answers stale is scoring the wrong
    assessment, so this tool must never declare itself idempotent.
    """
    spec = tools.get("extract_assessment")
    assert spec is not None
    assert spec.idempotent is False
    assert spec.cache_ttl_seconds == 0


# ── The read tools' pure helpers ─────────────────────────────────────────────
# The handlers themselves need a database. These are the parts of them that
# carry a product guarantee, and a guarantee is worth a test whether or not the
# code around it is convenient to reach.


def test_compensation_is_stripped_by_the_tool_layer(monkeypatch) -> None:
    """ESD 16, made a property of the SHAPE rather than of a call site.

    Every agent prompt is downstream of these tools, so stripping here means
    the next agent that reads a JD inherits the guarantee instead of having to
    remember it.
    """
    from app.services.tools import implementations as impl

    for key in ("salary", "salary_range", "compensation", "ctc", "expected_ctc", "pay_band", "budget"):
        assert impl._is_compensation_key(key), key
    for key in ("skills", "responsibilities", "education", "title"):
        assert not impl._is_compensation_key(key), key

    labels = impl._labels(
        [{"skill": "Kafka", "salary_band": "L5", "expected_ctc": "4,00,000"}]
    )
    assert labels == ("Kafka",)


def test_labels_accept_every_shape_a_jd_field_actually_arrives_in() -> None:
    """List, string, list-of-dicts. An agent should not branch on which."""
    from app.services.tools import implementations as impl

    assert impl._labels("Kafka") == ("Kafka",)
    assert impl._labels(["Kafka", "Terraform"]) == ("Kafka", "Terraform")
    assert impl._labels([{"name": "Kafka"}]) == ("Kafka",)
    assert impl._labels(None) == ()
    # Deduplicated case-insensitively, so a JD listing a skill twice does not
    # weight it twice in the prompt built from this.
    assert impl._labels(["Kafka", "kafka"]) == ("Kafka",)


def test_an_unanswered_last_question_is_never_paired_with_someone_elses_answer() -> None:
    """Why the pairing walks ordinals instead of zipping alternate rows.

    The last question of an ABANDONED assessment has no answer, and that is the
    case a recruiter most wants to look at.
    """
    from app.services.tools.implementations import pair_exchanges

    class _Message:
        def __init__(self, speaker: str, content: str) -> None:
            self.speaker = speaker
            self.content = content

    messages = [
        _Message("agent", "Q1"),
        _Message("candidate", "A1"),
        _Message("agent", "Q2"),
        _Message("candidate", "A2"),
        _Message("agent", "Q3 never answered"),
    ]
    pairs = pair_exchanges(messages)
    assert [(q.content, a.content) for q, a in pairs] == [("Q1", "A1"), ("Q2", "A2")]


def test_a_follow_up_keeps_its_parents_question_key() -> None:
    """A probe reuses its parent's key by design; that is how the scorers file
    it as more evidence for one question rather than as an unknown key every
    scorer would silently drop."""
    from app.services.tools.implementations import pair_exchanges

    class _Message:
        def __init__(self, speaker: str, content: str, question_key: str | None = None) -> None:
            self.speaker = speaker
            self.content = content
            self.question_key = question_key

    messages = [
        _Message("agent", "Q1", "comp-1"),
        _Message("candidate", "A1"),
        _Message("agent", "Follow up on Q1", "comp-1"),
        _Message("candidate", "A2"),
    ]
    pairs = pair_exchanges(messages)
    assert [q.question_key for q, _ in pairs] == ["comp-1", "comp-1"]
