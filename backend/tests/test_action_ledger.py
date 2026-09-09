"""The agent action ledger, the adapter gate, and UNKNOWN (RPN-AI-UP-001 W5).

Four properties are worth defending here, and each has a failure mode that is
invisible without a test:

  * ONE EXTERNAL EFFECT for one logical action, however many times it is
    attempted. The failure is a candidate receiving the same invitation twice
    and a client watching the platform do it.
  * A TIMEOUT IS NOT A FAILURE. It is UNKNOWN, and it is resolved by reading
    the world back. Retrying it is the duplicate the ledger exists to prevent,
    and the state machine must make that retry unreachable rather than
    discouraged.
  * AN IRREVERSIBLE ACTION WITH NO COMMITTED APPROVAL NEVER REACHES THE
    ADAPTER. Asserted with an adapter that RAISES when it is invoked, not with
    a mock that records having been called: a test whose only assertion is that
    a mock was not called still passes when the gate calls something else, and
    a raising adapter fails loudly at the exact moment the send would happen.
  * THE IDEMPOTENCY KEY IS STABLE across attempts and UNSTABLE across logically
    different actions. A key that varies per attempt dedupes nothing; a key
    that collides across actions silently never performs the second one.

The pure tests always run. The ledger tests skip cleanly with no database and
run for real in the container, the same convention `test_billing.py` follows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.models.agent_action import (
    ACTION_FAILED,
    ACTION_PENDING,
    ACTION_ROLLED_BACK,
    ACTION_RUNNING,
    ACTION_SUCCEEDED,
    ACTION_UNKNOWN,
    AUTHORIZATION_HUMAN,
    AUTHORIZATION_POLICY,
    RISK_IRREVERSIBLE,
    RISK_REVERSIBLE,
)
from app.services.agent_actions import gate, idempotency, ledger, stopping, unknowns
from app.services.agent_actions.world_state import (
    CHECKPOINT_VERSION,
    BudgetRemaining,
    WorldState,
    WorldStateError,
)
from app.services.evidence import ledger as evidence_ledger
from app.services.rating import GRADE_HIGHLY, GRADE_MATCHING, GRADE_NOT
from app.services.reliability.budget import Budget
from app.services.safety import actions as safety_actions

TENANT_MARKER = "Action Ledger Test"


# ── The idempotency key ──────────────────────────────────────────────────────

def test_the_key_is_stable_across_attempts() -> None:
    """Derived twice from the same logical inputs, it is the same key.

    This is the property a per-attempt UUID or a timestamp would break, and
    breaking it makes every retry a fresh action with a fresh row, so nothing
    ever dedupes and the ledger looks perfectly healthy while doing nothing.
    """
    link_id = uuid.uuid4()
    first = idempotency.assessment_invitation_key(
        link_id=link_id, template="assessment_invite", template_version=3
    )
    second = idempotency.assessment_invitation_key(
        link_id=link_id, template="assessment_invite", template_version=3
    )
    assert first == second


def test_the_key_is_unstable_across_logically_different_actions() -> None:
    """Every logical input in the key must move the key when it moves.

    The report key carries three versions because all three change what the
    report SAYS. If a version did not reach the key, a rescore would dedupe
    against the original and the new report would silently never be written.
    """
    link_id, candidate_id = uuid.uuid4(), uuid.uuid4()
    base = idempotency.assessment_invitation_key(
        link_id=link_id, template="assessment_invite", template_version=3
    )
    assert base != idempotency.assessment_invitation_key(
        link_id=uuid.uuid4(), template="assessment_invite", template_version=3
    )
    assert base != idempotency.assessment_invitation_key(
        link_id=link_id, template="assessment_reminder", template_version=3
    )
    assert base != idempotency.assessment_invitation_key(
        link_id=link_id, template="assessment_invite", template_version=4
    )

    report = idempotency.prism_report_key(
        candidate_id=candidate_id,
        scorecard_version="7",
        assessment_version="2",
        report_version="1",
    )
    for moved in (
        {"scorecard_version": "8"},
        {"assessment_version": "3"},
        {"report_version": "2"},
    ):
        other = idempotency.prism_report_key(
            candidate_id=candidate_id,
            **{
                "scorecard_version": "7",
                "assessment_version": "2",
                "report_version": "1",
                **moved,
            },
        )
        assert other != report, moved


def test_an_invitation_key_and_a_report_key_never_collide() -> None:
    """The column is UNIQUE across the whole table, so the kind is in the key."""
    shared = uuid.uuid4()
    assert idempotency.derive(
        idempotency.KIND_ASSESSMENT_INVITATION, shared
    ) != idempotency.derive(idempotency.KIND_PRISM_REPORT, shared)


def test_a_timestamp_is_refused_as_a_key_part() -> None:
    """The one form of the unstable-input mistake a machine can catch."""
    with pytest.raises(idempotency.IdempotencyKeyError):
        idempotency.derive("some_action", datetime.now(timezone.utc))


def test_an_empty_or_separator_bearing_part_is_refused() -> None:
    with pytest.raises(idempotency.IdempotencyKeyError):
        idempotency.derive("some_action", "   ")
    with pytest.raises(idempotency.IdempotencyKeyError):
        idempotency.derive("some_action", "a:b")


def test_the_args_digest_is_order_independent_and_argument_sensitive() -> None:
    """It exists to catch a key reused for different arguments, so it must not
    also fire because two callers built the same mapping in a different order."""
    link = uuid.uuid4()
    one = idempotency.args_digest({"link_id": link, "template": "invite"})
    two = idempotency.args_digest({"template": "invite", "link_id": link})
    assert one == two
    assert one != idempotency.args_digest({"link_id": link, "template": "reminder"})


# ── The state machine ────────────────────────────────────────────────────────

def test_unknown_has_no_edge_back_to_running() -> None:
    """The absent edge IS the enforcement.

    Retrying a FAILED action is correct because FAILED means the attempt
    definitely had no effect. Retrying an UNKNOWN one issues the side effect a
    second time, and a rule that lived in a docstring would be a rule the next
    caller breaks.
    """
    assert ACTION_RUNNING not in ledger.TRANSITIONS[ACTION_UNKNOWN]
    assert ledger.TRANSITIONS[ACTION_UNKNOWN] == frozenset(
        {ACTION_SUCCEEDED, ACTION_FAILED}
    )
    # And the retryable one does have it, or the distinction buys nothing.
    assert ACTION_RUNNING in ledger.TRANSITIONS[ACTION_FAILED]


def test_a_rolled_back_action_is_terminal() -> None:
    """The effect happened and was undone. Doing it again is a NEW action."""
    assert ledger.TRANSITIONS[ACTION_ROLLED_BACK] == frozenset()


def test_running_counts_as_unresolved() -> None:
    """A process that died between the invoke and the answer leaves RUNNING,
    and that row's effect may exist exactly as an UNKNOWN one's may."""
    assert ACTION_RUNNING in ledger.UNRESOLVED_STATES
    assert ACTION_UNKNOWN in ledger.UNRESOLVED_STATES
    assert ACTION_PENDING not in ledger.UNRESOLVED_STATES


class _SessionThatMustNotBeUsed:
    """Any attribute access is a failure.

    The risk-class refusal must happen before the gate touches the database,
    because a mislabelled sensitive action should not even be recorded as
    executable.
    """

    def __getattr__(self, name: str):
        raise AssertionError(f"the gate reached the database ({name})")


@pytest.mark.asyncio
async def test_a_sensitive_action_cannot_be_declared_reversible() -> None:
    """`safety/actions.py` already names them; the gate refuses a mislabel.

    Without this, a caller opts out of the approval gate by writing the wrong
    word in the request.
    """
    request = gate.ActionRequest(
        tenant_id=uuid.uuid4(),
        agent="email",
        tool=safety_actions.REJECT_CANDIDATE,
        idempotency_key="reject:whoever",
        risk_class=RISK_REVERSIBLE,
    )
    with pytest.raises(gate.RiskClassRefused):
        await gate.execute(
            _SessionThatMustNotBeUsed(), request, _adapter_that_must_not_run
        )


# ── The world state (W5.3) ───────────────────────────────────────────────────

def test_the_checkpoint_carries_the_world_and_not_the_transcript() -> None:
    """The field set is the enforcement, exactly as `EvaluatorInput`'s is.

    Asserted as an EXACT set rather than as the absence of a `transcript` name,
    because a future field called `context` or `notes` would pass a narrower
    test and reopen the whole hole.
    """
    from app.services.agent_actions.world_state import WORLD_STATE_FIELDS

    assert WORLD_STATE_FIELDS == {
        "objective",
        "completed_actions",
        "pending_actions",
        "evidence_ids",
        "budget_remaining",
        "approvals_held",
        "unknowns_outstanding",
    }


def test_the_checkpoint_round_trips() -> None:
    state = WorldState(
        objective="establish whether this candidate has run Kafka in production",
        completed_actions=("assessment_invitation:a:invite:1",),
        pending_actions=("prism_report:b:1:1:1",),
        evidence_ids=("11111111-1111-1111-1111-111111111111",),
        budget_remaining=BudgetRemaining(cost_usd=0.11, iterations=4, replans=2),
        approvals_held=("22222222-2222-2222-2222-222222222222",),
        unknowns_outstanding=("33333333-3333-3333-3333-333333333333",),
    )
    assert WorldState.from_dict(state.as_dict()) == state
    assert state.as_dict()["checkpoint_version"] == CHECKPOINT_VERSION


def test_a_checkpoint_from_an_unknown_version_raises() -> None:
    """Reading an older shape with today's rules is a resume that believes it
    knows what already committed and does not."""
    payload = WorldState(objective="anything").as_dict()
    payload["checkpoint_version"] = CHECKPOINT_VERSION + 1
    with pytest.raises(WorldStateError):
        WorldState.from_dict(payload)


def test_a_completed_action_leaves_the_pending_list() -> None:
    """A key in both lists would tell a resuming run to consider doing an
    already-committed action again."""
    state = WorldState(objective="o").with_pending_action("k").with_completed_action("k")
    assert state.completed_actions == ("k",)
    assert state.pending_actions == ()


def test_the_budget_snapshot_is_taken_from_the_real_budget() -> None:
    """One definition of "remaining", not two."""
    budget = Budget(task_type="scoring")
    budget.begin_iteration()
    budget.spend(0.01)
    snapshot = BudgetRemaining.from_budget(budget)
    assert snapshot.cost_usd == budget.remaining_usd
    assert snapshot.iterations == budget.max_iterations - budget.iterations
    assert not snapshot.exhausted


def test_any_one_exhausted_ceiling_exhausts_the_snapshot() -> None:
    """A loop can spin without spending, which is what the iteration ceiling
    catches and the cost ceiling does not."""
    assert BudgetRemaining(cost_usd=0.0, iterations=5, replans=3).exhausted
    assert BudgetRemaining(cost_usd=1.0, iterations=0, replans=3).exhausted
    assert BudgetRemaining(cost_usd=1.0, iterations=5, replans=0).exhausted


# ── The unknowns ledger (W5.4) ───────────────────────────────────────────────

def _evidence(source_type: str, trust: str, *, specifics: bool = False):
    return evidence_ledger.EvidenceItem(
        evidence_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=None,
        source_type=source_type,
        source_id=uuid.uuid4(),
        text_ref=evidence_ledger.text_ref(table="profiles", row_id=uuid.uuid4()),
        provenance={"has_specifics": specifics},
        trust=trust,
    )


def _claim(*, supporting=(), contradicting=()):
    return evidence_ledger.Claim(
        claim_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=None,
        subject="candidate",
        dimension="track_record",
        claim="has run Kafka in production",
        supporting_evidence=tuple(supporting),
        contradicting_evidence=tuple(contradicting),
    )


def test_a_fact_with_nothing_behind_it_is_unknown() -> None:
    assert unknowns.state_for_claim(_claim()) == unknowns.FACT_UNKNOWN


def test_the_products_own_inference_is_unknown_and_not_claimed() -> None:
    """`inferred_only` is the product agreeing with itself.

    Reading it as a claim would let the platform's own inference retire the
    question, which is precisely the question an investigator should go and
    answer.
    """
    claim = _claim(
        supporting=[
            _evidence(evidence_ledger.SOURCE_MEMORY, evidence_ledger.TRUST_INFERRED)
        ]
    )
    assert claim.status == evidence_ledger.CLAIM_INFERRED_ONLY
    assert unknowns.state_for_claim(claim) == unknowns.FACT_UNKNOWN


def test_a_resume_line_is_claimed_and_not_verified() -> None:
    """Supported, but standing on E0/E1 evidence: somebody said it and nobody
    has checked. The tier test is `tiers.above_e1`, the same one Runbook 14.1
    uses, so the two cannot drift."""
    claim = _claim(
        supporting=[
            _evidence(
                evidence_ledger.SOURCE_RESUME,
                evidence_ledger.TRUST_OBSERVED,
                specifics=True,
            )
        ]
    )
    assert claim.status == evidence_ledger.CLAIM_SUPPORTED
    assert unknowns.state_for_claim(claim) == unknowns.FACT_CLAIMED


def test_an_assessment_answer_verifies_a_fact() -> None:
    claim = _claim(
        supporting=[
            _evidence(evidence_ledger.SOURCE_ANSWER, evidence_ledger.TRUST_OBSERVED)
        ]
    )
    assert unknowns.state_for_claim(claim) == unknowns.FACT_VERIFIED


def test_contradiction_wins_over_support() -> None:
    """Checked first, exactly as `support_state` checks it first. Any rule that
    let support cancel a contradiction would be the silent averaging the
    evidence spec forbids."""
    claim = _claim(
        supporting=[
            _evidence(evidence_ledger.SOURCE_ANSWER, evidence_ledger.TRUST_OBSERVED)
        ],
        contradicting=[
            _evidence(
                evidence_ledger.SOURCE_VALIDATION, evidence_ledger.TRUST_VALIDATED
            )
        ],
    )
    assert unknowns.state_for_claim(claim) == unknowns.FACT_CONTRADICTED


def test_a_contradiction_is_not_an_investigators_work_item() -> None:
    """It is answered by a person, not by more retrieval, and no flag in this
    product ever auto-resolves one."""
    facts = unknowns.facts_for_claims(
        [
            _claim(),
            _claim(
                supporting=[
                    _evidence(
                        evidence_ledger.SOURCE_ANSWER, evidence_ledger.TRUST_OBSERVED
                    )
                ],
                contradicting=[
                    _evidence(
                        evidence_ledger.SOURCE_VALIDATION,
                        evidence_ledger.TRUST_VALIDATED,
                    )
                ],
            ),
        ]
    )
    assert len(unknowns.outstanding(facts)) == 1
    assert len(unknowns.contradicted(facts)) == 1


# ── Stop when proved (W5.5) ──────────────────────────────────────────────────

def _state_with_unknowns(count: int = 1) -> WorldState:
    return WorldState(
        objective="establish the band",
        budget_remaining=BudgetRemaining(cost_usd=0.2, iterations=5, replans=3),
        unknowns_outstanding=tuple(str(uuid.uuid4()) for _ in range(count)),
    )


def test_an_investigation_continues_while_the_band_could_still_move() -> None:
    decision = stopping.should_continue(
        state=_state_with_unknowns(),
        projection=stopping.BandProjection(
            best_case=GRADE_HIGHLY, worst_case=GRADE_NOT
        ),
    )
    assert decision.proceed
    assert decision.reason == stopping.REASON_WORK_REMAINS


def test_a_pinned_band_stops_the_investigation_as_proved() -> None:
    """Outstanding unknowns and budget to spend, and it still stops.

    That is the whole point of W5.5: the question is not "is there anything
    left to look at" but "could looking change the delivered word".
    """
    decision = stopping.should_continue(
        state=_state_with_unknowns(3),
        projection=stopping.BandProjection(
            best_case=GRADE_MATCHING, worst_case=GRADE_MATCHING
        ),
    )
    assert not decision.proceed
    assert decision.reason == stopping.REASON_PROVED
    assert decision.proved


def test_an_exhausted_budget_stops_without_claiming_proof() -> None:
    """A run that ran out of money is not a run that finished, and the report
    written after it carries unknowns that are still unknown."""
    state = WorldState(
        objective="establish the band",
        budget_remaining=BudgetRemaining(cost_usd=0.0, iterations=5, replans=3),
        unknowns_outstanding=(str(uuid.uuid4()),),
    )
    decision = stopping.should_continue(
        state=state,
        projection=stopping.BandProjection(
            best_case=GRADE_HIGHLY, worst_case=GRADE_NOT
        ),
    )
    assert not decision.proceed
    assert decision.reason == stopping.REASON_BUDGET
    assert not decision.proved


def test_a_loop_that_stops_finding_evidence_is_stopped() -> None:
    decision = stopping.should_continue(
        state=_state_with_unknowns(),
        projection=stopping.BandProjection(
            best_case=GRADE_HIGHLY, worst_case=GRADE_NOT
        ),
        rounds_without_new_evidence=stopping.MAX_ROUNDS_WITHOUT_NEW_EVIDENCE,
    )
    assert not decision.proceed
    assert decision.reason == stopping.REASON_NOT_CONVERGING


def test_barren_rounds_are_counted_from_the_end() -> None:
    """Three productive rounds must not hide the two barren ones after them."""
    assert stopping.rounds_without_new_evidence([["a"], ["b"], [], []]) == 2
    assert stopping.rounds_without_new_evidence([[], [], ["c"]]) == 0


def test_a_band_outside_the_four_grades_is_refused() -> None:
    with pytest.raises(stopping.StopConditionError):
        stopping.BandProjection(best_case="Excellent", worst_case=GRADE_NOT)


# ── The ledger against a real database ───────────────────────────────────────

async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001, any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable, skipping action ledger test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _tenant(session) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:id, :name, :domain, 'pending')"
        ),
        {
            "id": str(tenant_id),
            "name": f"{TENANT_MARKER} {tenant_id.hex[:8]}",
            "domain": f"{tenant_id.hex[:12]}.action.test",
        },
    )
    await session.commit()
    return tenant_id


async def _drop_tenant(factory, tenant_id: uuid.UUID) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("DELETE FROM tenants WHERE id = :tid"), {"tid": str(tenant_id)}
            )
            await session.commit()


def _request(tenant_id: uuid.UUID, key: str, **overrides) -> gate.ActionRequest:
    fields = {
        "tenant_id": tenant_id,
        "agent": "email",
        "tool": "send_assessment_invitation",
        "idempotency_key": key,
        "risk_class": RISK_REVERSIBLE,
        "args": {"link_id": str(uuid.uuid4())},
        "authorization": AUTHORIZATION_POLICY,
    }
    fields.update(overrides)
    return gate.ActionRequest(**fields)


async def _adapter_that_must_not_run(action):
    raise AssertionError(
        f"the adapter was reached for {action.tool} in state {action.state}"
    )


@pytest.mark.asyncio
async def test_the_same_logical_action_twice_produces_one_external_effect() -> None:
    """Two attempts, one send. The second reads the ledger and returns."""
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )
                request = _request(tenant_id, key)
                sends: list[str] = []

                async def adapter(action):
                    sends.append(action.idempotency_key)
                    return gate.AdapterResult(external_id="provider-message-1")

                first = await gate.execute(session, request, adapter)
                second = await gate.execute(session, request, adapter)

                assert first.performed is True
                assert second.performed is False
                assert sends == [key], "the effect must happen exactly once"
                assert second.state == ACTION_SUCCEEDED
                assert second.external_id == "provider-message-1"
                assert second.action.attempt == 1
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_timeout_lands_in_unknown_and_is_resolved_by_reading_back() -> None:
    """The three-valued world, end to end.

    A timeout is neither success nor failure, the blind retry is refused by the
    gate rather than discouraged in a comment, and the only way out is somebody
    looking.
    """
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )
                request = _request(tenant_id, key)

                async def times_out(action):
                    raise TimeoutError("the provider never answered")

                with pytest.raises(TimeoutError):
                    await gate.execute(session, request, times_out)

                action = await ledger.load(session, key)
                assert action is not None
                assert action.state == ACTION_UNKNOWN
                assert action.attempt == 1
                assert action.committed_at is None

                # A blind retry is REFUSED, and the adapter is never reached.
                with pytest.raises(gate.IndeterminateAction):
                    await gate.execute(session, request, _adapter_that_must_not_run)

                async def read_back(_action):
                    return gate.ReadBack(
                        verdict=gate.READ_BACK_FOUND, external_id="provider-message-7"
                    )

                resolved = await gate.resolve_unknown(session, action, read_back)
                assert resolved.state == ACTION_SUCCEEDED
                assert resolved.verified_at is not None
                assert resolved.external_id == "provider-message-7"

                # And now the action is settled: another attempt sends nothing.
                after = await gate.execute(
                    session, request, _adapter_that_must_not_run
                )
                assert after.performed is False
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_read_back_that_proves_absence_makes_the_retry_safe() -> None:
    """The other half of the resolution, and the only route from UNKNOWN to a
    state a retry may leave. The retry is safe precisely because somebody
    looked."""
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )
                request = _request(tenant_id, key)

                async def times_out(action):
                    raise TimeoutError("the provider never answered")

                with pytest.raises(TimeoutError):
                    await gate.execute(session, request, times_out)

                action = await ledger.load(session, key)
                assert action is not None

                async def read_back(_action):
                    return gate.ReadBack(verdict=gate.READ_BACK_ABSENT)

                resolved = await gate.resolve_unknown(session, action, read_back)
                assert resolved.state == ACTION_FAILED
                assert resolved.verified_at is not None

                sends: list[str] = []

                async def adapter(action_row):
                    sends.append(action_row.idempotency_key)
                    return gate.AdapterResult(external_id="provider-message-9")

                retried = await gate.execute(session, request, adapter)
                assert retried.performed is True
                assert sends == [key]
                assert retried.action.attempt == 2
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_inconclusive_read_back_leaves_the_action_unknown() -> None:
    """Guessing in either direction is either a duplicate effect or an effect
    reported as delivered that never was."""
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )
                request = _request(tenant_id, key)

                async def times_out(action):
                    raise TimeoutError("the provider never answered")

                with pytest.raises(TimeoutError):
                    await gate.execute(session, request, times_out)
                action = await ledger.load(session, key)
                assert action is not None

                async def read_back(_action):
                    return gate.ReadBack(verdict=gate.READ_BACK_INDETERMINATE)

                unchanged = await gate.resolve_unknown(session, action, read_back)
                assert unchanged.state == ACTION_UNKNOWN

                outstanding = await ledger.unresolved(session)
                assert key in {row.idempotency_key for row in outstanding}
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_irreversible_action_with_no_approval_never_reaches_the_adapter() -> None:
    """Asserted with an adapter that RAISES if it is invoked.

    A mock-was-not-called assertion would still pass if the gate called
    something else; a raising adapter fails at the exact moment the send would
    have happened.
    """
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.prism_report_key(
                    candidate_id=uuid.uuid4(),
                    scorecard_version="1",
                    assessment_version="1",
                    report_version="1",
                )
                request = _request(
                    tenant_id,
                    key,
                    agent="siddhi",
                    tool="deliver_prism_report",
                    risk_class=RISK_IRREVERSIBLE,
                    authorization=AUTHORIZATION_HUMAN,
                )
                with pytest.raises(gate.ApprovalRequired):
                    await gate.execute(session, request, _adapter_that_must_not_run)

                action = await ledger.load(session, key)
                assert action is not None
                assert action.state == ACTION_PENDING
                assert action.sealed_at is None
                assert action.attempt == 0
                assert action.committed_at is None
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_committed_approval_is_what_opens_the_gate() -> None:
    """The same request, once an approval exists, performs exactly once and
    records which approval it rested on."""
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.prism_report_key(
                    candidate_id=uuid.uuid4(),
                    scorecard_version="1",
                    assessment_version="1",
                    report_version="1",
                )
                approval_id = uuid.uuid4()
                request = _request(
                    tenant_id,
                    key,
                    agent="siddhi",
                    tool="deliver_prism_report",
                    risk_class=RISK_IRREVERSIBLE,
                    authorization=AUTHORIZATION_HUMAN,
                )
                with pytest.raises(gate.ApprovalRequired):
                    await gate.execute(session, request, _adapter_that_must_not_run)

                approved = _request(
                    tenant_id,
                    key,
                    agent="siddhi",
                    tool="deliver_prism_report",
                    risk_class=RISK_IRREVERSIBLE,
                    authorization=AUTHORIZATION_HUMAN,
                    approval_id=approval_id,
                    args=request.args,
                )
                deliveries: list[str] = []

                async def adapter(action_row):
                    deliveries.append(action_row.idempotency_key)
                    return gate.AdapterResult(external_id="report-object-key")

                outcome = await gate.execute(session, approved, adapter)
                assert outcome.performed is True
                assert deliveries == [key]
                assert outcome.action.sealed_at is not None
                assert outcome.action.approval_id == approval_id
                assert outcome.action.committed_at is not None
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_key_reused_for_different_arguments_is_refused() -> None:
    """Without this, the second and genuinely different action reads as
    "already done" and is never performed, with nothing saying so."""
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )

                async def adapter(action_row):
                    return gate.AdapterResult(external_id="provider-message-1")

                await gate.execute(session, _request(tenant_id, key), adapter)
                with pytest.raises(ledger.IdempotencyKeyReused):
                    await gate.execute(
                        session,
                        _request(tenant_id, key, args={"link_id": "a different one"}),
                        _adapter_that_must_not_run,
                    )
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_retry_of_an_unknown_action_is_refused_at_the_state_machine() -> None:
    """Below the gate as well as at it.

    The gate refuses the retry, and so does `begin_attempt`, so a future caller
    that reaches the ledger directly cannot issue the duplicate either.
    """
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )

                async def times_out(action):
                    raise TimeoutError("the provider never answered")

                with pytest.raises(TimeoutError):
                    await gate.execute(session, _request(tenant_id, key), times_out)
                action = await ledger.load(session, key)
                assert action is not None
                with pytest.raises(ledger.IllegalActionTransition):
                    await ledger.begin_attempt(session, action)
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_compensated_action_is_never_performed_again_under_its_key() -> None:
    """ROLLED_BACK is terminal, and the gate says why rather than repeating it.

    Compensation is not an undo of the send: the recipient already read it. So
    doing the thing again is a NEW logical action with a new key, and the
    ledger refuses to pretend otherwise.
    """
    engine, factory = await _factory_or_skip()
    tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                key = idempotency.assessment_invitation_key(
                    link_id=uuid.uuid4(), template="assessment_invite",
                    template_version=1,
                )
                request = _request(tenant_id, key)

                async def adapter(action_row):
                    return gate.AdapterResult(external_id="provider-message-1")

                outcome = await gate.execute(session, request, adapter)
                rolled_back = await ledger.record_rollback(session, outcome.action)
                assert rolled_back.state == ACTION_ROLLED_BACK

                with pytest.raises(gate.GateError):
                    await gate.execute(session, request, _adapter_that_must_not_run)
    finally:
        if tenant_id is not None:
            await _drop_tenant(factory, tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_recruiters_own_session_may_write_its_tenants_action_row() -> None:
    """The RLS WITH CHECK admits a tenant-scoped write, same as
    `candidate_updates` (0079).

    A recruiter's session is what starts an assessment invitation, so a
    bypass-only policy would 403 every action the product actually takes. The
    other direction still has to hold: the same session cannot attribute a row
    to somebody else's tenant.
    """
    engine, factory = await _factory_or_skip()
    tenant_id = None
    other_tenant_id = None
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                tenant_id = await _tenant(session)
                other_tenant_id = await _tenant(session)

        key = idempotency.assessment_invitation_key(
            link_id=uuid.uuid4(), template="assessment_invite", template_version=1
        )
        async with factory() as session:
            async with tenant_scope(session, tenant_id):
                action, created = await ledger.reserve(
                    session,
                    tenant_id=tenant_id,
                    agent="email",
                    tool="send_assessment_invitation",
                    idempotency_key=key,
                    args_sha256=idempotency.args_digest({"link_id": str(key)}),
                    risk_class=RISK_REVERSIBLE,
                    authorization=AUTHORIZATION_POLICY,
                )
                assert created is True
                assert action.state == ACTION_PENDING

                with pytest.raises(DBAPIError):
                    await ledger.reserve(
                        session,
                        tenant_id=other_tenant_id,
                        agent="email",
                        tool="send_assessment_invitation",
                        idempotency_key=key + ":other",
                        args_sha256=idempotency.args_digest({"link_id": "x"}),
                        risk_class=RISK_REVERSIBLE,
                        authorization=AUTHORIZATION_POLICY,
                    )
    finally:
        for victim in (tenant_id, other_tenant_id):
            if victim is not None:
                await _drop_tenant(factory, victim)
        await engine.dispose()
