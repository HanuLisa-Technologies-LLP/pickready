"""The A2A artifact contract: what may be published, and what may be read.

The properties asserted here are the ones that stop an artifact from being a
prompt string with extra steps: a producer cannot publish a type it does not
declare, a consumer outside the allowed set is refused rather than warned,
tenant scope is compared arithmetically, and an agent's internal deliberation
never crosses the boundary.
"""
from __future__ import annotations

import pytest

from app.services.agents import artifacts, identity
from app.services.verification import base as verification

TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
JOB = "job-1"

MATRIX = {
    "must_have": [{"name": "Kafka", "rubric": "bands"}],
    "nice_to_have": [{"name": "Terraform", "rubric": "bands"}],
    "behavioural": [{"name": "Ownership", "rubric": "bands"}],
}


def _matrix(**overrides) -> artifacts.Artifact:
    kwargs = dict(
        producer=identity.SUTRA,
        artifact_type="tatva_matrix",
        payload=MATRIX,
        tenant_id=TENANT,
        job_id=JOB,
        source_refs=("jd:1",),
        validated=True,
    )
    kwargs.update(overrides)
    return artifacts.publish(**kwargs)


# ── publishing ───────────────────────────────────────────────────────────────


def test_an_agent_cannot_publish_a_type_it_does_not_declare() -> None:
    """A consumer verifying provenance would otherwise reject a genuine
    artifact, or accept one whose producer holds none of the tools its content
    would have required."""
    with pytest.raises(artifacts.ArtifactContractError):
        artifacts.publish(
            producer=identity.VAADA,
            artifact_type="tatva_matrix",
            payload=MATRIX,
            tenant_id=TENANT,
        )


def test_a_missing_required_field_is_refused_at_publish() -> None:
    """A malformed artifact that lands is worse than one that never did: the
    consumer discovers the gap mid-render and renders it."""
    with pytest.raises(artifacts.ArtifactContractError):
        artifacts.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload={"must_have": [], "nice_to_have": []},
            tenant_id=TENANT,
        )


def test_a_payload_carrying_internal_deliberation_is_refused() -> None:
    """A scratchpad quoted into a downstream prompt becomes text a grade is
    written from, and it reads exactly like evidence."""
    payload = dict(MATRIX)
    payload["chain_of_thought"] = "first I considered..."
    with pytest.raises(artifacts.ArtifactContractError):
        artifacts.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload=payload,
            tenant_id=TENANT,
        )


def test_deliberation_is_refused_at_any_depth() -> None:
    """A top-level-only check is one a nested field walks straight past."""
    payload = dict(MATRIX)
    payload["must_have"] = [{"name": "Kafka", "scratchpad": "hmm"}]
    with pytest.raises(artifacts.ArtifactContractError):
        artifacts.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload=payload,
            tenant_id=TENANT,
        )


def test_allowed_agents_defaults_to_the_declared_consumers_not_to_everyone() -> None:
    """Defaulting to everyone would make the policy field decorative."""
    artifact = _matrix()
    assert identity.VAADA in artifact.allowed_agents
    assert identity.MITI in artifact.allowed_agents
    assert identity.BODHA not in artifact.allowed_agents


def test_provenance_is_derived_from_the_refs_not_asserted_by_the_producer() -> None:
    """A producer marking its own provenance complete while recording no refs is
    the exact shape of a timestamp claiming work that did not happen."""
    assert _matrix().provenance_complete
    assert not _matrix(source_refs=()).provenance_complete


def test_the_serialised_envelope_omits_the_payload() -> None:
    """It sits beside a trace, and a trace carries identifiers and counts and
    never content."""
    assert "payload" not in _matrix().as_dict()


# ── downstream verification (spec 37) ────────────────────────────────────────


def test_a_declared_consumer_reading_a_sound_artifact_passes() -> None:
    verdict = artifacts.verify_for_consumer(
        _matrix(), identity.VAADA, tenant_id=TENANT, job_id=JOB
    )
    assert verdict.passed, verdict.as_dict()


def test_a_consumer_outside_the_allowed_set_fails_with_a_high_finding() -> None:
    """The reason artifacts exist is that an agent cannot reach into another
    agent's state; a permission check that only logs is not a boundary."""
    verdict = artifacts.verify_for_consumer(
        _matrix(allowed_agents=(identity.VAADA,)), identity.MITI, tenant_id=TENANT
    )
    assert not verdict.passed
    issues = {f.issue for f in verdict.by_severity(verification.SEVERITY_HIGH)}
    assert "consumer_not_permitted" in issues


def test_a_cross_tenant_artifact_is_refused() -> None:
    """Tenant isolation is never delegated to a model or to a default: two
    strings, compared."""
    verdict = artifacts.verify_for_consumer(
        _matrix(), identity.VAADA, tenant_id=OTHER_TENANT, job_id=JOB
    )
    assert not verdict.passed
    issues = {f.issue for f in verdict.by_severity(verification.SEVERITY_HIGH)}
    assert "tenant_scope_mismatch" in issues


def test_an_artifact_from_another_job_is_refused() -> None:
    """A matrix is per JOB, and consuming the wrong one grades a candidate
    against criteria they were never assessed on."""
    verdict = artifacts.verify_for_consumer(
        _matrix(), identity.VAADA, tenant_id=TENANT, job_id="job-2"
    )
    assert not verdict.passed


def test_a_forged_producer_is_caught_by_the_identity_table() -> None:
    """A consumer holding an artifact whose producer is not the declared one
    cannot know whose version of the contract it is reading."""
    forged = artifacts.Artifact(
        artifact_type="tatva_matrix",
        version=1,
        status=artifacts.STATUS_PUBLISHED,
        producer=identity.YUKTI,
        consumers=(identity.VAADA,),
        tenant_id=TENANT,
        payload=MATRIX,
        validated=True,
        allowed_agents=(identity.VAADA,),
    )
    verdict = artifacts.verify_for_consumer(forged, identity.VAADA, tenant_id=TENANT)
    issues = {f.issue for f in verdict.by_severity(verification.SEVERITY_HIGH)}
    assert "producer_identity_mismatch" in issues


def test_an_unpublished_artifact_is_not_readable_downstream() -> None:
    """A draft matrix is one a human has not confirmed, and the human gate is
    the product's only comparability guarantee."""
    verdict = artifacts.verify_for_consumer(
        _matrix(status=artifacts.STATUS_DRAFT), identity.VAADA, tenant_id=TENANT
    )
    assert not verdict.passed


def test_a_missing_required_field_is_caught_at_consume_too() -> None:
    """Checking only at publish trusts the version of the code that wrote the
    row, which is precisely the version the consumer cannot see."""
    thin = artifacts.Artifact(
        artifact_type="tatva_matrix",
        version=1,
        status=artifacts.STATUS_PUBLISHED,
        producer=identity.SUTRA,
        consumers=(identity.VAADA,),
        tenant_id=TENANT,
        payload={"must_have": []},
        validated=True,
        allowed_agents=(identity.VAADA,),
    )
    verdict = artifacts.verify_for_consumer(thin, identity.VAADA, tenant_id=TENANT)
    locations = {f.location for f in verdict.findings}
    assert "tatva_matrix.behavioural" in locations


def test_verification_returns_a_verdict_the_loop_can_consume() -> None:
    """A second accept/reject shape would mean the downstream check is the one
    verifier whose rejection cannot be fed back to a retry."""
    verdict = artifacts.verify_for_consumer(
        _matrix(), identity.BODHA, tenant_id=TENANT
    )
    critique = verdict.to_critique()
    assert not critique.ok
    assert critique.defects


# =============================================================================
# THE EIGHT A2A CONTRACT FIELDS (specdoc4 15, spec-doc6 4.1)
# =============================================================================
#
# "Every artifact carries the A2A contract fields specdoc4 15 requires (Message
#  / Task / Artifact / Status / Context / Provenance / Version / Correlation
#  ID)."
#
# WHY THERE ARE TWO CHECKS AND NOT ONE. `verify_for_consumer` answers "may this
# consumer read this artifact", and the honest answer to that is not changed by
# an absent correlation id: the content is in scope, from the declared producer,
# and safe to read. `require_contract_complete` answers "may this producer have
# published it", and the honest answer there is no.
#
# Splitting them puts each check where its failure is actionable. A consumer
# cannot repair a producer's missing provenance; refusing the read would punish
# the wrong side of the boundary for a defect that does not make the content
# unsafe. So the consumer records it as a low finding and the producer is
# refused outright.

import uuid  # noqa: E402

from app.services.agents import provenance  # noqa: E402

#: A real uuid, because a correlation id is DERIVED from the row it belongs to
#: and derivation goes through `uuid.UUID`. The module-level `JOB` above is the
#: string "job-1", which is deliberately not one: a malformed identifier must
#: raise where it is written rather than produce a well-shaped id that joins to
#: nothing.
_JOB_UUID = uuid.uuid4()
_CORRELATION = provenance.correlation_for_job(_JOB_UUID)
_PRINCIPAL = provenance.Principal(
    user_id="4b0f0e2c-6a1e-4c1a-9f2e-0d3b6a1c7e51",
    role="recruitment_manager",
    tenant_id=str(TENANT),
)


def _complete(**overrides) -> artifacts.Artifact:
    kwargs = dict(
        producer=identity.SUTRA,
        artifact_type="tatva_matrix",
        payload={"must_have": [], "nice_to_have": [], "behavioural": []},
        tenant_id=str(TENANT),
        job_id=str(_JOB_UUID),
        source_refs=("job_competencies:1",),
        validated=True,
        correlation_id=_CORRELATION,
        task_id="task-1",
        principal=_PRINCIPAL,
    )
    kwargs.update(overrides)
    return artifacts.publish(**kwargs)


def test_a_complete_artifact_carries_all_eight_contract_fields() -> None:
    fields = artifacts.contract_fields(_complete())
    assert set(fields) == set(provenance.A2A_CONTRACT_FIELDS)
    assert provenance.contract_gaps(fields) == []


def test_the_contract_field_view_is_derived_and_not_a_second_set_of_columns() -> None:
    """`message`, `task` and `artifact` are the identifiers the hand-off already
    carries under their own names. Storing them twice under the spec's names
    would create two fields that must agree."""
    artifact = _complete()
    fields = artifacts.contract_fields(artifact)
    assert fields["artifact"] == artifact.artifact_id
    assert fields["message"] == artifact.message_id
    assert fields["task"] == artifact.task_id
    assert fields["version"] == artifact.version
    assert fields["correlation_id"] == artifact.correlation_id
    assert fields["context"]["tenant_id"] == artifact.tenant_id
    assert fields["provenance"]["producer"] == artifact.producer


def test_publishing_without_the_contract_is_refused_by_the_producer_check() -> None:
    bare = artifacts.publish(
        producer=identity.SUTRA,
        artifact_type="tatva_matrix",
        payload={"must_have": [], "nice_to_have": [], "behavioural": []},
        tenant_id=str(TENANT),
    )
    with pytest.raises(artifacts.IncompleteContract) as exc:
        artifacts.require_contract_complete(bare)
    gaps = str(exc.value)
    # Every gap at once. A producer fixing one at a time learns about the next
    # one on the next run, which turns one fix into four deploys.
    assert "correlation_id" in gaps
    assert "provenance.principal_user_id" in gaps
    assert "provenance.source_refs" in gaps


def test_the_completeness_check_is_not_simply_always_fail() -> None:
    assert artifacts.require_contract_complete(_complete()) is not None


def test_an_incomplete_contract_is_a_different_error_class_from_a_bad_payload() -> None:
    """A missing payload field means the artifact cannot be READ; a missing
    correlation id means it can be read perfectly well and cannot be TRACED.
    Two classes because the two get fixed by different people."""
    assert issubclass(artifacts.IncompleteContract, artifacts.ArtifactContractError)
    with pytest.raises(artifacts.ArtifactContractError) as exc:
        artifacts.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload={"must_have": []},
            tenant_id=str(TENANT),
        )
    assert not isinstance(exc.value, artifacts.IncompleteContract)


def test_a_consumer_records_a_missing_correlation_without_refusing_the_read() -> None:
    """LOW, on purpose. See the section header: refusing here would punish the
    consumer for the producer's defect, and the content is safe to read."""
    bare = artifacts.publish(
        producer=identity.SUTRA,
        artifact_type="tatva_matrix",
        payload={"must_have": [], "nice_to_have": [], "behavioural": []},
        tenant_id=str(TENANT),
        job_id=str(_JOB_UUID),
        source_refs=("job_competencies:1",),
        validated=True,
    )
    verdict = artifacts.verify_for_consumer(
        bare, identity.VAADA, tenant_id=str(TENANT), job_id=str(_JOB_UUID)
    )
    issues = {finding.issue for finding in verdict.findings}
    assert "correlation_id_absent" in issues
    assert "principal_not_attributed" in issues
    assert verdict.passed


def test_an_artifact_published_for_another_tenants_principal_is_refused() -> None:
    """A cross-tenant write with a plausible-looking attribution attached."""
    stranger = provenance.Principal(
        user_id="9f1d2b3c-4e5f-4a6b-8c7d-1e2f3a4b5c6d",
        role="recruiter",
        tenant_id="11111111-2222-3333-4444-555555555555",
    )
    with pytest.raises(artifacts.ArtifactContractError):
        _complete(principal=stranger)


def test_the_message_id_distinguishes_two_publications_of_one_task() -> None:
    """Two republications of the same task must be tellable apart, or a consumer
    holding one cannot know whether it has the later version."""
    first = _complete()
    second = _complete()
    assert first.task_id == second.task_id
    assert first.message_id != second.message_id


def test_the_envelope_view_still_carries_no_payload() -> None:
    """`as_dict` is what sits beside a trace, so it must stay content-free even
    as contract fields are added to it."""
    artifact = _complete(
        payload={
            "must_have": [{"name": "distributed systems"}],
            "nice_to_have": [],
            "behavioural": [],
        }
    )
    rendered = artifact.as_dict()
    assert "payload" not in rendered
    assert "distributed systems" not in str(rendered)
    assert rendered["correlation_id"] == _CORRELATION
    assert rendered["principal_user_id"] == _PRINCIPAL.user_id
