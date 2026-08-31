"""No stage substitutes something for work that did not happen (spec-doc6 4.1).

    "If retrieval returns nothing, if a required artifact is missing, if a gate
     fails: raise, audit, and surface an actionable message. Never fall back to
     a generic question bank, a template JD, a default weight or a generic
     report paragraph."

TWO HALVES, AND THEY ARE DIFFERENT KINDS OF CHECK
---------------------------------------------------
The first half is behavioural: the enforcement layer refuses, and the refusal
carries a sentence a person can act on. Those are ordinary assertions.

The second half is a SWEEP over the source tree, and it is deliberately shaped
as two different rules rather than one.

  * On the Part A packages and the cross-cutting packages -- `hiring/`, `miti/`,
    `siddhi/`, `agents/`, `orchestration/`, `observability/` -- the rule is
    ABSOLUTE. Zero template outputs, zero generic remarks, zero substituted
    default scores, zero `except: pass`. This is the new path and it has no
    legacy to carry.

  * Everywhere else the rule is a RATCHET on the SET OF FILES, not on a count.
    The legacy fallbacks are being deleted by the activation work as each old
    path is replaced, so a count-based ratchet would go red for a collaborator
    every time one shrank, and a guard that goes red for a change that is fine
    is a guard people start editing rather than reading. A set-based ratchet
    shrinks silently, holds firm, and still fails the moment a NEW module
    acquires a fallback of the forbidden kind, which is the regression that
    matters.

The inventory below is a MEASUREMENT, taken by sweeping the tree, not a wish.
Every entry is a real fallback that exists today and is named in the report so
its owner can delete it.
"""
from __future__ import annotations

import ast
import pathlib
import re
import uuid

import pytest

from app.services.agents import artifacts as a2a
from app.services.agents import envelope as run_envelope
from app.services.agents import identity, provenance
from app.services.orchestration import activation, enforcement

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

TENANT = uuid.uuid4()
JOB = uuid.uuid4()
PRINCIPAL = provenance.Principal(
    user_id=str(uuid.uuid4()), role="recruiter", tenant_id=str(TENANT)
)
CORRELATION = provenance.correlation_for_job(JOB)


def _envelope(**overrides) -> run_envelope.Envelope:
    kwargs = dict(
        tenant_id=str(TENANT),
        agent_id=identity.SUTRA,
        task_type="job_setup",
        interactive=False,
        job_id=str(JOB),
        principal=PRINCIPAL,
        correlation_id=CORRELATION,
    )
    kwargs.update(overrides)
    return run_envelope.Envelope.for_run(**kwargs)


def _matrix_artifact(envelope: run_envelope.Envelope, **overrides) -> a2a.Artifact:
    kwargs = dict(
        producer=identity.SUTRA,
        artifact_type="tatva_matrix",
        payload={"must_have": [], "nice_to_have": [], "behavioural": []},
        tenant_id=str(TENANT),
        job_id=str(JOB),
        source_refs=(f"jobs:{JOB}",),
        validated=True,
        correlation_id=envelope.correlation_id,
        task_id=envelope.task_id,
        principal=PRINCIPAL,
    )
    kwargs.update(overrides)
    return a2a.publish(**kwargs)


# ══════════════════════════════════════════════════════════════════════════
# 1. THE ENFORCEMENT LAYER REFUSES, AND SAYS WHAT TO DO
# ══════════════════════════════════════════════════════════════════════════


def test_empty_retrieval_raises_rather_than_returning_nothing() -> None:
    """A stage handed nothing and carrying on builds its output from the
    model's own priors, which reads exactly like output built from evidence."""
    with pytest.raises(enforcement.EmptyRetrieval) as exc:
        enforcement.refuse_on_empty(
            [],
            what="rubric anchor retrieval for the Track Record dimension",
            action="Re-index the department model, then re-run the evaluation.",
        )
    assert "Re-index the department model" in str(exc.value)


def test_a_populated_retrieval_passes_straight_through() -> None:
    """The guard must not be a tax on the healthy path."""
    rows = [{"id": "chunk-1"}]
    assert enforcement.refuse_on_empty(rows, what="retrieval", action="x") is rows


def test_a_missing_upstream_artifact_names_its_producer() -> None:
    with pytest.raises(enforcement.RequiredArtifactMissing) as exc:
        enforcement.require_artifact(
            None,
            artifact_type="tatva_matrix",
            produced_by="Sutra",
            action="Finalise the job's criteria before scoring anyone against them.",
        )
    assert "Sutra" in str(exc.value)
    assert "Finalise the job's criteria" in str(exc.value)


def test_a_missing_stage_module_names_the_module_and_the_work() -> None:
    """The alternative -- `try: import ... except ImportError: <old module>` --
    would let a stage run on the thing it was meant to replace while every log
    line said otherwise."""
    with pytest.raises(activation.StageModuleMissing) as exc:
        activation.load("no_such_stage")
    assert "no_such_stage" in str(exc.value)


def test_every_declared_stage_module_names_the_work_that_supplies_it() -> None:
    for stage, spec in activation.STAGE_MODULES.items():
        assert spec.supplied_by, f"{stage} names no supplying work"
        assert spec.dotted.startswith("app.services."), stage


@pytest.mark.asyncio
async def test_a_stage_that_published_nothing_is_refused() -> None:
    """The `framework_generated_at` failure, as a rule. Nineteen of thirty-five
    live jobs carried a generation stamp and zero competency rows, and every
    health check asked the stamp."""
    ledger = provenance.Ledger(CORRELATION)
    with pytest.raises(enforcement.DegradationRefused) as exc:
        await enforcement.run_stage(provenance.STAGE_MATRIX, _envelope(), ledger)
    assert "timestamp is not" in str(exc.value)
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_a_stage_with_no_human_principal_is_refused() -> None:
    """RBAC 34. A row that lost the human reads exactly like a human action."""
    ledger = provenance.Ledger(CORRELATION)
    envelope = _envelope(principal=None)
    with pytest.raises(provenance.MissingPrincipal):
        await enforcement.run_stage(
            provenance.STAGE_MATRIX,
            envelope,
            ledger,
            artifact=_matrix_artifact(envelope),
        )
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_a_principal_from_another_tenant_is_refused() -> None:
    """A cross-tenant action with a plausible-looking audit row attached."""
    ledger = provenance.Ledger(CORRELATION)
    stranger = provenance.Principal(
        user_id=str(uuid.uuid4()), role="recruiter", tenant_id=str(uuid.uuid4())
    )
    envelope = _envelope(principal=stranger)
    with pytest.raises(provenance.MissingPrincipal):
        await enforcement.run_stage(provenance.STAGE_MATRIX, envelope, ledger)
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_a_stage_with_no_correlation_id_is_refused() -> None:
    ledger = provenance.Ledger(CORRELATION)
    envelope = _envelope(correlation_id=None)
    with pytest.raises(run_envelope.MissingCorrelationId):
        await enforcement.run_stage(provenance.STAGE_MATRIX, envelope, ledger)
    assert len(ledger) == 0


def test_a_principal_cannot_be_constructed_blank() -> None:
    for blank in ("", "   "):
        with pytest.raises(provenance.MissingPrincipal):
            provenance.Principal(user_id=blank, role="recruiter", tenant_id=str(TENANT))
        with pytest.raises(provenance.MissingPrincipal):
            provenance.Principal(
                user_id=str(uuid.uuid4()), role="recruiter", tenant_id=blank
            )


def test_an_artifact_without_its_contract_fields_is_refused_at_publish_time() -> None:
    """`require_contract_complete` raises and names every gap at once, because
    a producer fixing one at a time learns about the next one on the next run."""
    envelope = _envelope()
    bare = a2a.publish(
        producer=identity.SUTRA,
        artifact_type="tatva_matrix",
        payload={"must_have": [], "nice_to_have": [], "behavioural": []},
        tenant_id=str(TENANT),
        job_id=str(JOB),
    )
    with pytest.raises(a2a.IncompleteContract) as exc:
        a2a.require_contract_complete(bare)
    message = str(exc.value)
    assert "correlation_id" in message
    assert "principal_user_id" in message
    assert "source_refs" in message

    # And the complete one passes, so the check is not simply always-fail.
    assert a2a.require_contract_complete(_matrix_artifact(envelope)) is not None


def test_a_correlation_id_that_is_really_a_job_id_is_refused_at_publish() -> None:
    with pytest.raises(a2a.ArtifactContractError):
        a2a.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload={"must_have": [], "nice_to_have": [], "behavioural": []},
            tenant_id=str(TENANT),
            correlation_id=str(JOB),
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. THE SWEEP
# ══════════════════════════════════════════════════════════════════════════

#: The four fallback kinds spec-doc6 4.1 names, matched on the SYMBOL a
#: fallback is reached through rather than on prose. A name is what a caller
#: writes; a comment is not.
FALLBACK_PATTERNS: dict[str, re.Pattern[str]] = {
    "template_output": re.compile(
        r"_template_jd|_template_document|DEFAULT_TEMPLATES|fallback_draft"
        r"|_fallback_body|_FALLBACK_SUBJECTS"
    ),
    "generic_prose": re.compile(
        r"_fallback_remark_25|_fallback_remark_45|rating_differentiated_fallback"
        r"|_fallback_probes|_CHALLENGE_FALLBACK"
    ),
    "default_score": re.compile(
        r"_fallback_param_score|_FALLBACK_COMMENTS|_stable_score"
        r"|infer_grade_fallback|deterministic_fallback|retrieval_fallback"
    ),
    "dev_vector": re.compile(r"_dev_fallback_vector"),
}

#: Packages that must contain NONE of the above. The new path, plus the
#: cross-cutting packages that observe it. No legacy to carry, so no ratchet.
CLEAN_PACKAGES: tuple[str, ...] = (
    "services/hiring",
    "services/miti",
    "services/siddhi",
    "services/agents",
    "services/orchestration",
    "services/observability",
)

#: Every file that carries a legacy fallback today, measured by sweeping the
#: tree on 2026-08-29. The set may SHRINK freely as each old path is deleted by
#: the activation work; it may not GROW. Each entry is reported to its owner:
#:
#:   jd_generation.py       a TEMPLATE JD on every provider failure and on an
#:                          unparseable response. The single most literal
#:                          instance of what 4.1 forbids.
#:   lifecycle_email.py     canned subject lines and bodies when drafting fails.
#:   functional_assessment  `_fallback_remark_*`, `infer_grade_fallback` and the
#:                          `deterministic_fallback` scoring mode.
#:   gap_analysis.py        `_fallback_probes`, which are at least grounded in
#:                          the candidate's own words rather than generic.
#:   interviewer.py         `_CHALLENGE_FALLBACK`, wording for a non-answer.
#:   matching.py            `retrieval_fallback` ordering when no model ran.
#:   answer_quality.py      references the above in its docstring and constants.
#:   email_render.py        `DEFAULT_TEMPLATES` when a tenant has no template.
#:   embeddings.py          `_dev_fallback_vector`, guarded by the absence of a
#:                          key and deliberately kept for local development.
#:   models/assessment.py   the `scoring_mode` column's own vocabulary.
#:   api/companies.py       a page template, not a generation fallback.
#:   scripts/**             backfills, evaluation harnesses and seed data.
LEGACY_FALLBACK_FILES: frozenset[str] = frozenset(
    {
        "api/companies.py",
        "models/assessment.py",
        "scripts/backfill_functional_reports.py",
        "scripts/eval_report.py",
        "scripts/seed_dev_data.py",
        "scripts/seed_mock_data.py",
        "services/answer_quality.py",
        "services/email_render.py",
        "services/embeddings.py",
        "services/functional_assessment.py",
        "services/gap_analysis.py",
        "services/interviewer.py",
        "services/jd_generation.py",
        "services/lifecycle_email.py",
        "services/matching.py",
    }
)

#: Every `except ...: pass` that exists today, by file. Same ratchet rule. Most
#: are cache writes and best-effort cleanups rather than substituted data, but
#: anti-slop rule 1 is written against the SHAPE, and the shape is what makes
#: the next one invisible.
LEGACY_SWALLOWER_FILES: frozenset[str] = frozenset(
    {
        "api/candidates.py",
        "core/cache.py",
        "scripts/eval_trajectory.py",
        "scripts/validate_auth.py",
        "services/document_storage.py",
        "services/interview_telemetry.py",
        "services/jd_generation.py",
        "services/otp.py",
        "services/razorpay.py",
        "services/tenant_cache.py",
    }
)


def _python_files() -> list[pathlib.Path]:
    return [p for p in sorted(APP.rglob("*.py")) if "__pycache__" not in p.parts]


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(APP).as_posix()


def _files_with_fallbacks() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        kinds = {kind for kind, rx in FALLBACK_PATTERNS.items() if rx.search(text)}
        if kinds:
            found[_rel(path)] = kinds
    return found


def _files_that_swallow() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - would fail the import test first
            continue
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and (
                node.type is None
                or (len(node.body) == 1 and isinstance(node.body[0], ast.Pass))
            )
        ]
        if lines:
            found[_rel(path)] = lines
    return found


def test_the_part_a_packages_contain_no_fallback_of_any_forbidden_kind() -> None:
    offenders = {
        rel: kinds
        for rel, kinds in _files_with_fallbacks().items()
        if rel.startswith(CLEAN_PACKAGES)
    }
    assert not offenders, (
        "spec-doc6 4.1 forbids a template JD, a generic report paragraph, a "
        "default weight or a generic question bank on the Part A path. These "
        f"reach for one: {offenders}"
    )


def test_the_part_a_packages_never_swallow_an_exception() -> None:
    offenders = {
        rel: lines
        for rel, lines in _files_that_swallow().items()
        if rel.startswith(CLEAN_PACKAGES)
    }
    assert not offenders, (
        "A swallowed exception hands the caller an empty shape indistinguishable "
        f"from a legitimately empty result, and the caller renders it: {offenders}"
    )


def test_no_new_module_acquires_a_fallback() -> None:
    """A ratchet on the SET, so the inventory shrinks silently as each old path
    is deleted and fails loudly the moment a new module joins it."""
    new = sorted(set(_files_with_fallbacks()) - LEGACY_FALLBACK_FILES)
    assert not new, (
        "These modules newly reach for a template, a generic remark or a "
        "substituted score. Raise, audit and surface instead: " + ", ".join(new)
    )


def test_no_new_module_acquires_a_swallowed_exception() -> None:
    new = sorted(set(_files_that_swallow()) - LEGACY_SWALLOWER_FILES)
    assert not new, (
        "These modules newly swallow an exception with `pass` or catch bare: "
        + ", ".join(new)
    )


def test_the_legacy_inventory_is_a_measurement_and_not_a_wish() -> None:
    """Every file listed as carrying a legacy fallback must still exist.

    Without this the inventory rots into a list of files somebody deleted, and
    a stale allowlist is how a ratchet stops ratcheting: the next real offender
    happens to share a name with something long gone and passes.
    """
    missing = sorted(
        rel for rel in LEGACY_FALLBACK_FILES | LEGACY_SWALLOWER_FILES
        if not (APP / rel).exists()
    )
    assert not missing, (
        "Listed in the legacy inventory and no longer on disk. Remove the "
        "entries: " + ", ".join(missing)
    )
