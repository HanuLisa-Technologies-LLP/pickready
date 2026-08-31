"""The four pipeline gates, applied on the live path, and the refusal to degrade.

WHAT WAS WRONG BEFORE
---------------------
`hiring/gates.py` has held four real, arithmetic, provider-free checks for the
whole of the previous phase. Their only caller was `miti/pipeline.py`, which no
route and no worker imported, so every one of them guarded nothing. spec-doc6 D2
says "gate G1 already blocks evaluation ... use it", and that sentence was
wrong: it blocked nothing, because nothing called it.

This module is where the gates meet a real flow. It does NOT restate them --
that would be a second copy of the rule, and the two would eventually disagree
about a boundary. It resolves the module that owns each gate, applies it to a
live session, records the result against the flow's correlation id, and raises
where the gate is blocking.

THE FOUR GATES, AND WHY TWO OF THEM DO NOT BLOCK
--------------------------------------------------
  G1 SCORECARD    BLOCKING.     Nothing is evaluated against unapproved criteria.
  G2 SUFFICIENCY  NON-BLOCKING. Fires, is recorded, lowers confidence.
  G3 INTEGRITY    NON-BLOCKING. Fires, is recorded, routes to a human.
  G4 DISPOSITION  BLOCKING.     No delivery without a recorded human decision.

G2 is non-blocking for a fairness reason: a blocking sufficiency gate refuses a
report to exactly the candidates who most need a person to look -- the
career-changer, the returner, the non-traditional background -- which is a
silent rejection with better manners. G3 is non-blocking for a stronger one: a
blocking integrity gate IS an auto-rejection, ending a candidacy without a
person ever seeing the finding or the evidence under it.

"Non-blocking" is not "optional". `record_evidence_sufficiency` and
`record_integrity` RAISE if they are handed no ledger to record into, because a
gate whose result went nowhere is indistinguishable from a gate that never ran,
and that indistinguishability is the whole defect this module exists to close.

EVERY REFUSAL HERE IS LOUD, AND NOTHING FALLS BACK
----------------------------------------------------
spec-doc6 4.1: "If retrieval returns nothing, if a required artifact is missing,
if a gate fails: raise, audit, and surface an actionable message. Never fall
back to a generic question bank, a template JD, a default weight or a generic
report paragraph." Every exception class below carries the sentence a person
acts on, not a code they look up.

THE KILL SWITCH FAILS CLOSED
-----------------------------
`RPN_PIPELINE_HALT` is checked FIRST in `run_stage`, before the principal, the
correlation id or the gate, and before anything is read. A halt that ran the
stage first and then refused would already have spent the credit, written the
partial artifact, or read the row it was declining to act on. If the halt module
itself cannot be resolved, the stage is refused: a kill switch that cannot be
consulted is not a kill switch that is off.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from app.services.agents import artifacts as a2a
from app.services.agents import envelope as run_envelope
from app.services.agents import provenance
from app.services.orchestration import activation

logger = logging.getLogger("pickready.orchestration")

__all__ = [
    "HALT_STAGE_FOR",
    "DegradationRefused",
    "EmptyRetrieval",
    "GateBlocked",
    "RequiredArtifactMissing",
    "StageRefused",
    "record_evidence_sufficiency",
    "record_integrity",
    "refuse_on_empty",
    "require_frozen_scorecard",
    "require_human_disposition",
    "run_stage",
]


class StageRefused(RuntimeError):
    """Base class. Every refusal in this module names what is missing."""


class GateBlocked(StageRefused):
    """A blocking gate did not pass. Carries the gate's own reasons.

    The `GateResult` travels rather than a paraphrase, so a caller renders the
    gate's sentences and an audit row records which condition failed. G1 has two
    conditions with different fixes -- no items at all, versus items nobody
    approved -- and this codebase has already paid once for confusing them.
    """

    def __init__(self, gate: str, reasons: Sequence[str]) -> None:
        super().__init__(f"{gate} blocked: " + " ".join(reasons))
        self.gate = gate
        self.reasons = tuple(reasons)


class DegradationRefused(StageRefused):
    """A stage was about to substitute something for work that did not happen."""


class EmptyRetrieval(DegradationRefused):
    """Retrieval returned nothing where the stage requires something.

    Raised rather than proceeding on an empty list. A stage handed nothing and
    carrying on produces output built from the model's own priors, which reads
    exactly like output built from evidence and is written into a document a
    client makes a hiring decision from.
    """


class RequiredArtifactMissing(DegradationRefused):
    """An upstream artifact this stage consumes does not exist.

    Named separately from `EmptyRetrieval` because the fix is upstream, not
    here: the stage that should have published it either did not run or refused.
    """


# ── The kill switch, consulted before anything else ──────────────────────────


async def _halt_check(
    stage: str,
    *,
    tenant_id: Any,
    actor_user_id: Any,
    job_id: Any,
    correlation_id: str,
    agent: str | None,
) -> None:
    """Refuse if this stage is halted, and refuse if the switch is unreadable.

    ONE implementation, in `hiring/pipeline_halt.py`, resolved late. There is
    deliberately no local copy of the environment variable: a second reader of
    the same switch is a switch that can be half-off.
    """
    halt = activation.load("pipeline_halt")
    halt_stage = HALT_STAGE_FOR.get(stage)
    if halt_stage is None:
        return
    await halt.enforce(
        halt_stage,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        job_id=job_id,
        correlation_id=correlation_id,
        agent=agent,
    )


#: Pipeline stage -> the halt stage that governs it. A table rather than a
#: naming convention: the halt module names its stages after the AGENT and this
#: module names them after what HAPPENED, and a convention that silently failed
#: to match would leave a stage unhaltable while looking governed.
HALT_STAGE_FOR: dict[str, str] = {
    provenance.STAGE_SWOT: "bodha_swot",
    provenance.STAGE_MATRIX: "sutra_matrix",
    provenance.STAGE_PRESCREEN: "yukti_prescreen",
    provenance.STAGE_SCORING: "miti_evaluation",
    provenance.STAGE_REPORT: "siddhi_report",
}


# ── G1: nothing is evaluated against an unapproved scorecard ─────────────────


async def require_frozen_scorecard(session: Any, job_id: Any) -> Any:
    """GATE G1, on the live path. Returns the frozen matrix or raises.

    A thin resolver over `hiring.scorecard.require_frozen_matrix`, which IS the
    gate. It is thin on purpose: a second implementation of "has a human
    approved these criteria" would be a second answer, and the one that
    disagreed would be whichever the caller happened to import.

    Called FIRST by every stage that scores, grades or reports on a candidate,
    before a resume or a transcript is read. Ordering, not politeness: a refusal
    that ran the work first has already spent the credit it was refusing.
    """
    require = activation.symbol(provenance.STAGE_MATRIX, "require_frozen_matrix")
    return await require(session, job_id)


# ── G2 and G3: fire, record, never block ─────────────────────────────────────


def _gates() -> Any:
    """`hiring.gates`, resolved late.

    Late because this package is imported by the service layer and `hiring`
    imports the service layer back; a module-scope import closes a cycle that
    surfaces as `AttributeError: partially initialized module` in whichever
    order production happens to import things.
    """
    import importlib

    return importlib.import_module("app.services.hiring.gates")


def _record_gate(
    ledger: provenance.Ledger,
    envelope: run_envelope.Envelope,
    *,
    stage: str,
    result: Any,
) -> Any:
    principal = envelope.require_principal()
    ledger.record(
        provenance.StageRecord(
            correlation_id=envelope.require_correlation_id(),
            stage=stage,
            agent_id=envelope.agent_id,
            tenant_id=envelope.tenant_id,
            principal_user_id=principal.user_id,
            principal_role=principal.role,
            job_id=envelope.job_id,
            candidate_id=envelope.candidate_id,
            gate=result.gate,
            gate_passed=result.passed,
            status="ok" if result.passed else "flagged",
        )
    )
    logger.info(
        "orchestration.gate %s",
        provenance.log_fields(
            correlation_id=envelope.correlation_id or "",
            stage=stage,
            agent_id=envelope.agent_id,
            tenant_id=envelope.tenant_id,
            job_id=envelope.job_id,
            candidate_id=envelope.candidate_id,
            gate=result.gate,
            gate_passed=result.passed,
        ),
    )
    return result


def record_evidence_sufficiency(
    ledger: provenance.Ledger,
    envelope: run_envelope.Envelope,
    *,
    independent_sources: int,
    judged_dimensions: int,
    must_have_coverage: Mapping[str, int] | None = None,
) -> Any:
    """GATE G2 at aggregation. Never blocks, always fires, always recorded.

    The return value carries the reasons so the caller lowers CONFIDENCE and
    routes to a human. It never lowers a SCORE: insufficient evidence and
    negative evidence are different findings, and conflating them is the
    fairness failure the Runbook names by name.
    """
    result = _gates().evidence_sufficiency_gate(
        independent_sources=independent_sources,
        judged_dimensions=judged_dimensions,
        must_have_coverage=dict(must_have_coverage or {}),
    )
    return _record_gate(ledger, envelope, stage=provenance.STAGE_SCORING, result=result)


def record_integrity(
    ledger: provenance.Ledger,
    envelope: run_envelope.Envelope,
    *,
    unresolved_contradictions: int,
    contradiction_severity: str = "none",
    authenticity_band: str | None = None,
) -> Any:
    """GATE G3 at aggregation. Fails loudly and blocks NOTHING.

    NO FLAG EVER AUTO-REJECTS, and the enforcement is the absence of the
    capability: this function returns a `GateResult` and has no way to express a
    rejection. It cannot end a candidacy because it holds nothing that could.
    What it does is put the finding, with its evidence, in front of a person.
    """
    result = _gates().integrity_gate(
        unresolved_contradictions=unresolved_contradictions,
        contradiction_severity=contradiction_severity,
        authenticity_band=authenticity_band,
    )
    return _record_gate(ledger, envelope, stage=provenance.STAGE_SCORING, result=result)


# ── G4: a human decided, before anything is delivered ────────────────────────


def require_human_disposition(
    ledger: provenance.Ledger,
    envelope: run_envelope.Envelope,
    *,
    needs_review: bool,
    disposition: str | None,
    decided_by: Any = None,
) -> Any:
    """GATE G4, before delivery. BLOCKING when review is needed.

    It asks whether a human DECIDED, not whether they approved. All four
    dispositions pass, `rejected` included. A gate that required approval could
    be satisfied by nagging until somebody clicked yes; a gate that requires a
    recorded decision is satisfiable only by a person having looked, and by
    nothing the pipeline can do on its own.

    There is no `auto_cleared` and there must never be one. `DISPOSITIONS` in
    `hiring/gates.py` is the only list, a Postgres CHECK refuses a value outside
    it, and `review_dispositions.decided_by` is ON DELETE RESTRICT so a
    disposition can never outlive the person who made it.
    """
    result = _gates().human_review_gate(
        needs_review=needs_review,
        disposition=disposition,
        decided_by=decided_by,
    )
    _record_gate(ledger, envelope, stage=provenance.STAGE_DISPOSITION, result=result)
    if not result.passed and result.blocking:
        raise GateBlocked(result.gate, result.reasons)
    return result


# ── The refusal to degrade ───────────────────────────────────────────────────


def refuse_on_empty(value: Any, *, what: str, action: str) -> Any:
    """Return `value`, or refuse because there is nothing to work from.

    `action` is required and is a sentence, not a code. A refusal a person
    cannot act on gets caught and swallowed by the next caller who needs the
    feature to work, which is how a loud failure becomes a quiet one.
    """
    if value is None or (hasattr(value, "__len__") and len(value) == 0):
        raise EmptyRetrieval(
            f"{what} returned nothing, and this stage has no defensible output "
            f"without it. {action}"
        )
    return value


def require_artifact(
    artifact: Any,
    *,
    artifact_type: str,
    produced_by: str,
    action: str,
) -> Any:
    """The upstream artifact this stage consumes, or a refusal naming its producer."""
    if artifact is None:
        raise RequiredArtifactMissing(
            f"this stage consumes {artifact_type!r}, which {produced_by} has not "
            f"published. {action}"
        )
    return artifact


# ── The one door every Part A stage goes through ─────────────────────────────


async def run_stage(
    stage: str,
    envelope: run_envelope.Envelope,
    ledger: provenance.Ledger,
    *,
    artifact: a2a.Artifact | None = None,
    gate_result: Any = None,
) -> provenance.StageRecord:
    """Authorise, halt-check, verify the contract, and record one stage.

    THE ORDER IS THE POINT, and it is the same ordering the tool layer uses.

        1. kill switch     -- before anything is read or written
        2. human principal -- RBAC 34, before the handler's output is accepted
        3. correlation id  -- so the record can be joined to the flow at all
        4. A2A contract    -- the artifact carries all eight fields
        5. gate            -- blocking gates raise, non-blocking ones record
        6. ledger + log    -- identifiers only, never content

    A refusal at any step happens before the record is written, so a refused
    stage leaves no row claiming it ran. That is the opposite of the
    `framework_generated_at` failure, where the stamp was written and the work
    was not.
    """
    principal = envelope.require_principal()
    correlation_id = envelope.require_correlation_id()

    await _halt_check(
        stage,
        tenant_id=envelope.tenant_id,
        actor_user_id=principal.user_id,
        job_id=envelope.job_id,
        correlation_id=correlation_id,
        agent=envelope.agent_id,
    )

    if artifact is not None:
        a2a.require_contract_complete(artifact)
        if artifact.correlation_id != correlation_id:
            raise StageRefused(
                f"stage {stage!r} published an artifact under correlation "
                f"{artifact.correlation_id!r} while running under "
                f"{correlation_id!r}. One flow, one id."
            )
    elif stage in provenance.ARTIFACT_BEARING_STAGES:
        raise DegradationRefused(
            f"stage {stage!r} completed without publishing an artifact. A stage "
            "that left nothing behind is a timestamp, and a timestamp is not "
            "evidence that work happened."
        )

    if gate_result is not None and not gate_result.passed and gate_result.blocking:
        raise GateBlocked(gate_result.gate, gate_result.reasons)

    record = ledger.record(
        provenance.StageRecord(
            correlation_id=correlation_id,
            stage=stage,
            agent_id=envelope.agent_id,
            tenant_id=envelope.tenant_id,
            principal_user_id=principal.user_id,
            principal_role=principal.role,
            job_id=envelope.job_id,
            candidate_id=envelope.candidate_id,
            artifact_id=artifact.artifact_id if artifact else None,
            artifact_type=artifact.artifact_type if artifact else None,
            artifact_version=artifact.version if artifact else None,
            gate=gate_result.gate if gate_result is not None else None,
            gate_passed=gate_result.passed if gate_result is not None else None,
        )
    )
    logger.info(
        "orchestration.stage %s",
        provenance.log_fields(
            correlation_id=correlation_id,
            stage=stage,
            agent_id=envelope.agent_id,
            tenant_id=envelope.tenant_id,
            job_id=envelope.job_id,
            candidate_id=envelope.candidate_id,
            artifact_id=record.artifact_id,
            artifact_type=record.artifact_type,
            principal_user_id=principal.user_id,
            principal_role=principal.role,
        ),
    )
    return record
