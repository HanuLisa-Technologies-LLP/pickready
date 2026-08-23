"""The shared evidence ledger, and the four properties that make it worth having.

  * it stores a REFERENCE, never the sentence, because this table is far more
    widely readable than the transcript it points at;
  * `relevance` is engineering metadata and never crosses a client boundary;
  * trust is an ORDER, so a claim the product only inferred does not read as a
    claim somebody evidenced;
  * retiring evidence keeps the row, because a written report is a permanent
    record of what it was written from.

The pure halves are asserted here directly. The database halves run against the
migrated Postgres service and skip when none is reachable, matching
`test_cross_tenant_isolation`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.services.evidence import ledger


def _item(
    trust: str = ledger.TRUST_OBSERVED,
    status: str = ledger.STATUS_ACTIVE,
    relevance: float = 0.5,
    source_type: str = ledger.SOURCE_ANSWER,
) -> ledger.EvidenceItem:
    return ledger.EvidenceItem(
        evidence_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
        source_type=source_type,
        source_id=uuid.uuid4(),
        text_ref=ledger.text_ref(table="assessment_messages", row_id=uuid.uuid4()),
        provenance={"agent": "miti"},
        freshness=ledger.freshness(datetime.now(timezone.utc)),
        trust=trust,
        relevance=relevance,
        status=status,
    )


def _claim(supporting=(), contradicting=()) -> ledger.Claim:
    return ledger.Claim(
        claim_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
        subject="candidate",
        dimension="Stream Processing",
        claim="has run partition rebalances in production",
        supporting_evidence=tuple(supporting),
        contradicting_evidence=tuple(contradicting),
    )


# ── the text is never stored ─────────────────────────────────────────────────


def test_an_evidence_item_has_no_field_the_text_could_be_stored_in() -> None:
    """The shape is the enforcement.

    A `text` column would be filled the first week it existed, and a ledger row
    is readable by anyone with database access while the transcript it points at
    needs `view_review_screen`. The rule has to travel with the model, not with
    somebody's memory of a review comment.
    """
    fields = set(ledger.EvidenceItem.__dataclass_fields__)
    assert "text" not in fields
    assert "content" not in fields
    assert "excerpt" not in fields
    # No free-form escape hatch either. `provenance` and `freshness` are the
    # only mappings, and both are asserted below to carry no source text.
    assert fields & {"provenance", "freshness"} == {"provenance", "freshness"}


def test_the_write_path_takes_a_locator_and_not_a_sentence() -> None:
    """`record_evidence` names its parameter `ref`, deliberately.

    A parameter called `text` is the last place this rule can be stated before
    somebody passes the answer itself.
    """
    import inspect

    params = inspect.signature(ledger.record_evidence).parameters
    assert "ref" in params
    assert "text" not in params
    assert "content" not in params


def test_a_reference_round_trips_to_a_table_and_a_row() -> None:
    row_id = uuid.uuid4()
    ref = ledger.text_ref(table="context_chunks", row_id=row_id, fragment="sentence-2")
    assert ledger.parse_text_ref(ref) == ("context_chunks", str(row_id), "sentence-2")


def test_the_resolver_refuses_a_table_nobody_allowlisted() -> None:
    """An allowlist for the same reason `_SAFE_STAGE_KEYS` is one.

    Without it the resolver dereferences anything with an id column, and the
    next person adding a source discovers that by finding a candidate's row
    already being read through it.
    """
    assert "users" not in ledger._RESOLVABLE
    assert "profiles" not in ledger._RESOLVABLE
    assert set(ledger._RESOLVABLE) == {"context_chunks", "assessment_messages"}


# ── relevance is internal ────────────────────────────────────────────────────


def test_relevance_never_appears_in_a_client_projection() -> None:
    """Spec 47 calls it internal engineering metadata and the product's oldest
    rule says no number reaches a client. Both are one assertion here."""
    projection = _item(relevance=0.87).client_projection()
    assert "relevance" not in projection
    assert not any(isinstance(value, (int, float)) for value in projection.values())


def test_no_number_survives_into_a_claims_client_projection() -> None:
    """Including through the nested evidence, which is where it would hide."""
    claim = _claim(
        supporting=[_item(relevance=0.9), _item(ledger.TRUST_VALIDATED, relevance=0.4)],
        contradicting=[_item(relevance=0.2)],
    )
    projection = claim.client_projection()
    assert "relevance" not in projection
    assert "confidence" not in projection
    flat = repr(projection)
    for item in claim.supporting_evidence + claim.contradicting_evidence:
        assert str(item.relevance) not in flat
        assert str(item.freshness["age_days"]) not in projection.get("freshness", "")
    for nested in projection["supporting_evidence"] + projection["contradicting_evidence"]:
        assert not any(isinstance(value, (int, float)) for value in nested.values())


def test_freshness_reaches_a_reader_as_a_word_and_not_as_a_day_count() -> None:
    now = datetime.now(timezone.utc)
    assert ledger.freshness(now - timedelta(days=10), now=now)["band"] == (
        ledger.FRESHNESS_CURRENT
    )
    assert ledger.freshness(now - timedelta(days=200), now=now)["band"] == (
        ledger.FRESHNESS_RECENT
    )
    assert ledger.freshness(now - timedelta(days=900), now=now)["band"] == (
        ledger.FRESHNESS_DATED
    )
    # The day count still exists, for engineering, in the payload only.
    assert ledger.freshness(now - timedelta(days=10), now=now)["age_days"] == 10


def test_freshness_boundaries_are_inclusive_upward() -> None:
    """claude.md rule 8, applied here so a boundary day never reads as staler
    than the day before it."""
    now = datetime.now(timezone.utc)
    assert ledger.freshness(now - timedelta(days=90), now=now)["band"] == (
        ledger.FRESHNESS_CURRENT
    )
    assert ledger.freshness(now - timedelta(days=365), now=now)["band"] == (
        ledger.FRESHNESS_RECENT
    )


# ── the trust lattice ────────────────────────────────────────────────────────


def test_a_claim_supported_only_by_inference_does_not_read_as_supported() -> None:
    """The failure this prevents is the product agreeing with itself.

    "The resume lists Kafka under Skills" inferred four times is the exact shape
    of evidence that looks like corroboration and carries none. Without a
    lattice it would be four supporting rows and a supported claim.
    """
    claim = _claim(supporting=[_item(ledger.TRUST_INFERRED) for _ in range(4)])
    assert claim.status == ledger.CLAIM_INFERRED_ONLY
    assert claim.status != ledger.CLAIM_SUPPORTED


def test_one_observed_item_is_enough_and_many_inferred_ones_are_not() -> None:
    assert (
        _claim(supporting=[_item(ledger.TRUST_OBSERVED)]).status
        == ledger.CLAIM_SUPPORTED
    )
    assert (
        _claim(supporting=[_item(ledger.TRUST_INFERRED)] * 9).status
        == ledger.CLAIM_INFERRED_ONLY
    )


def test_trust_is_ordered_and_an_unknown_level_ranks_below_everything() -> None:
    """Deny by default, matching `permissions.granted_tools`. A value nobody
    recognises must not be able to promote a claim."""
    assert (
        ledger.trust_rank(ledger.TRUST_AUTHORITATIVE)
        > ledger.trust_rank(ledger.TRUST_VALIDATED)
        > ledger.trust_rank(ledger.TRUST_OBSERVED)
        > ledger.trust_rank(ledger.TRUST_INFERRED)
    )
    assert ledger.trust_rank("very_authoritative") < ledger.trust_rank(
        ledger.TRUST_INFERRED
    )
    assert _claim(supporting=[_item("very_authoritative")]).status == (
        ledger.CLAIM_INFERRED_ONLY
    )


def test_contradiction_is_never_outweighed_by_support() -> None:
    """The silent averaging spec 14 forbids, in its cheapest form.

    A claim with strong evidence on both sides is the most interesting row in
    the ledger and the easiest one to lose.
    """
    claim = _claim(
        supporting=[_item(ledger.TRUST_AUTHORITATIVE) for _ in range(5)],
        contradicting=[_item(ledger.TRUST_OBSERVED)],
    )
    assert claim.status == ledger.CLAIM_CONTRADICTED


def test_a_claim_with_nothing_behind_it_is_unsupported_not_supported() -> None:
    assert _claim().status == ledger.CLAIM_UNSUPPORTED


# ── retirement keeps the row ─────────────────────────────────────────────────


def test_a_revoked_item_stops_supporting_its_claim() -> None:
    """A mis-parse or a guardrail quarantine must stop counting immediately,
    or a poisoned chunk keeps holding a grade up."""
    claim = _claim(supporting=[_item(status=ledger.STATUS_REVOKED)])
    assert claim.status == ledger.CLAIM_UNSUPPORTED
    # And it is still visible to anyone asking what was once counted.
    assert len(claim.supporting_evidence) == 1
    assert claim.live_support == ()


def test_a_superseded_item_stops_counting_and_is_still_there() -> None:
    superseded = _item(status=ledger.STATUS_SUPERSEDED)
    claim = _claim(supporting=[superseded, _item(ledger.TRUST_VALIDATED)])
    assert claim.status == ledger.CLAIM_SUPPORTED
    assert superseded in claim.supporting_evidence
    assert superseded not in claim.live_support


def test_a_revoked_item_does_not_keep_a_claim_contradicted() -> None:
    """The direction that would be unfair if it were wrong: a withdrawn
    contradiction must stop counting against a candidate."""
    claim = _claim(
        supporting=[_item(ledger.TRUST_VALIDATED)],
        contradicting=[_item(status=ledger.STATUS_REVOKED)],
    )
    assert claim.status == ledger.CLAIM_SUPPORTED


# ── the write path refuses what it cannot represent ──────────────────────────


@pytest.mark.parametrize("value", ["linkedin", "reference_check", ""])
def test_a_source_nothing_can_resolve_is_refused(value) -> None:
    """Evidence pointing at a source nobody can dereference is evidence nobody
    can check, which is worse than no evidence at all."""
    with pytest.raises(ledger.LedgerError):
        ledger._require(value, ledger.SOURCE_TYPES, "source_type")


@pytest.mark.parametrize("value", ["0.8", "high", "trusted"])
def test_a_trust_level_outside_the_lattice_is_refused(value) -> None:
    """A stored value nobody's comparison recognises makes a claim silently
    stop reading as supported, which is a failure with no error attached."""
    with pytest.raises(ledger.LedgerError):
        ledger._require(value, ledger.TRUST_LEVELS, "trust")


def test_nothing_in_the_ledger_calls_a_model() -> None:
    """A ledger that needed a provider would fail exactly when an explanation
    for a grade is most wanted."""
    source = (ledger.__file__ or "").replace("\\", "/")
    with open(source, "r", encoding="utf-8") as handle:
        body = handle.read()
    assert "invoke_llm" not in body
    assert "llm_router" not in body


# ── the database halves ──────────────────────────────────────────────────────


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping ledger integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_tenant(session, tenant: uuid.UUID, job: uuid.UUID) -> None:
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:t, :n, :d, 'pending')"
        ),
        {"t": str(tenant), "n": f"ledger-{tenant}", "d": f"{tenant}.ledger.test"},
    )
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
            "VALUES (:j, :t, 'Ledger', '{}'::jsonb, 'draft')"
        ),
        {"j": str(job), "t": str(tenant)},
    )


@pytest.mark.asyncio
async def test_evidence_from_another_tenant_is_never_returned() -> None:
    """The Postgres policy is the boundary and the WHERE clause is defence in
    depth. Both are exercised: the read runs in tenant A's scope and asks for
    tenant A's job, and tenant B's identically shaped claim must not appear."""
    engine, factory = await _factory_or_skip()
    a_tenant, a_job = uuid.uuid4(), uuid.uuid4()
    b_tenant, b_job = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed_tenant(session, a_tenant, a_job)
                await _seed_tenant(session, b_tenant, b_job)
                for tenant, job in ((a_tenant, a_job), (b_tenant, b_job)):
                    claim_id = await ledger.record_claim(
                        session,
                        tenant_id=tenant,
                        job_id=job,
                        link_id=None,
                        subject="candidate",
                        dimension="Kafka",
                        claim="has run partition rebalances",
                    )
                    evidence_id = await ledger.record_evidence(
                        session,
                        tenant_id=tenant,
                        job_id=job,
                        link_id=None,
                        source_type=ledger.SOURCE_RESUME,
                        source_id=job,
                        ref=ledger.text_ref(table="context_chunks", row_id=uuid.uuid4()),
                        trust=ledger.TRUST_OBSERVED,
                        relevance=0.6,
                    )
                    await ledger.attach_evidence(
                        session,
                        tenant_id=tenant,
                        claim_id=claim_id,
                        evidence_id=evidence_id,
                        stance=ledger.STANCE_SUPPORTS,
                    )
                await session.commit()

        async with factory() as session:
            async with tenant_scope(session, a_tenant):
                mine = await ledger.load_claims(
                    session, tenant_id=a_tenant, job_id=a_job
                )
                assert len(mine) == 1
                assert mine[0].status == ledger.CLAIM_SUPPORTED
                # Naming the other tenant's job explicitly returns nothing:
                # neither the policy nor the filter lets it through.
                assert (
                    await ledger.load_claims(
                        session, tenant_id=a_tenant, job_id=b_job
                    )
                    == []
                )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                    {"a": str(a_tenant), "b": str(b_tenant)},
                )
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_superseding_keeps_the_old_row_and_points_at_the_new_one() -> None:
    """A report already written is a permanent record of what it was written
    from. Deleting the row it pointed at turns "what was this grade based on"
    into an unanswerable question."""
    engine, factory = await _factory_or_skip()
    tenant, job = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed_tenant(session, tenant, job)
                claim_id = await ledger.record_claim(
                    session,
                    tenant_id=tenant,
                    job_id=job,
                    link_id=None,
                    subject="candidate",
                    dimension="Kafka",
                    claim="has run partition rebalances",
                )
                old = await ledger.record_evidence(
                    session,
                    tenant_id=tenant,
                    job_id=job,
                    link_id=None,
                    source_type=ledger.SOURCE_RESUME,
                    source_id=job,
                    ref=ledger.text_ref(table="context_chunks", row_id=uuid.uuid4()),
                    trust=ledger.TRUST_OBSERVED,
                )
                new = await ledger.record_evidence(
                    session,
                    tenant_id=tenant,
                    job_id=job,
                    link_id=None,
                    source_type=ledger.SOURCE_RESUME,
                    source_id=job,
                    ref=ledger.text_ref(table="context_chunks", row_id=uuid.uuid4()),
                    trust=ledger.TRUST_OBSERVED,
                )
                for evidence_id in (old, new):
                    await ledger.attach_evidence(
                        session,
                        tenant_id=tenant,
                        claim_id=claim_id,
                        evidence_id=evidence_id,
                        stance=ledger.STANCE_SUPPORTS,
                    )
                await ledger.supersede_evidence(
                    session, tenant_id=tenant, evidence_id=old, replaced_by=new
                )
                await session.commit()

                claim = await ledger.load_claim(
                    session, tenant_id=tenant, claim_id=claim_id
                )
                assert claim is not None
                by_id = {item.evidence_id: item for item in claim.supporting_evidence}
                assert set(by_id) == {old, new}
                assert by_id[old].status == ledger.STATUS_SUPERSEDED
                assert by_id[old].superseded_by == new
                assert claim.live_support == (by_id[new],)
                assert claim.status == ledger.CLAIM_SUPPORTED

                # Revoking the survivor drops the claim back to unsupported,
                # and neither row has gone anywhere.
                await ledger.revoke_evidence(
                    session,
                    tenant_id=tenant,
                    evidence_id=new,
                    reason="quarantined by conversation_guardrails",
                )
                await session.commit()
                claim = await ledger.load_claim(
                    session, tenant_id=tenant, claim_id=claim_id
                )
                assert claim is not None
                assert len(claim.supporting_evidence) == 2
                assert claim.status == ledger.CLAIM_UNSUPPORTED
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant)}
                )
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_claim_is_one_row_however_many_times_it_is_recorded() -> None:
    """Two rows asserting the same thing with half the evidence each read as
    two independent findings and are one."""
    engine, factory = await _factory_or_skip()
    tenant, job = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed_tenant(session, tenant, job)
                first = await ledger.record_claim(
                    session,
                    tenant_id=tenant,
                    job_id=job,
                    link_id=None,
                    subject="candidate",
                    dimension="Kafka",
                    claim="has run partition rebalances",
                )
                second = await ledger.record_claim(
                    session,
                    tenant_id=tenant,
                    job_id=job,
                    link_id=None,
                    subject="candidate",
                    dimension="Kafka",
                    claim="has run partition rebalances",
                )
                await session.commit()
                assert first == second
                assert len(
                    await ledger.load_claims(session, tenant_id=tenant, job_id=job)
                ) == 1
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant)}
                )
                await session.commit()
        await engine.dispose()
