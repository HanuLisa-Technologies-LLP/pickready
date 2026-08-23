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
