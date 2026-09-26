"""No stage substitutes something for work that did not happen (spec-doc6 4.1).

    "If retrieval returns nothing, if a required artifact is missing, if a gate
     fails: raise, audit, and surface an actionable message. Never fall back to
     a generic question bank, a template JD, a default weight or a generic
     report paragraph."

TWO HALVES, AND THEY ARE DIFFERENT KINDS OF CHECK
---------------------------------------------------
The first half is behavioural: the live refusal points on the Part A path
refuse, and the refusal carries a sentence a person can act on. Those are
ordinary assertions. It was written against the orchestration package until
2026-09-24; that package had no production caller and is deleted by the
Vivekium simplification release, so the half was rewritten onto the code that
actually runs: the pipeline gates (`hiring.gates`), the provenance ledger and
principal (`agents.provenance`), the run envelope (`agents.envelope`) and the
artifact contract (`agents.artifacts`). A guard test that exercises a module
nothing calls proves the module and nothing else.

The second half is a SWEEP over the source tree, and it is deliberately shaped
as three different rules rather than one.

  * On the Part A packages and the cross-cutting packages -- `hiring/`, `miti/`,
    `siddhi/`, `agents/`, `observability/` -- the rule is ABSOLUTE. Zero
    template outputs, zero generic remarks, zero substituted default scores,
    zero `except: pass`, zero silent broad handlers. This is the new path and
    it has no legacy to carry.

  * Everywhere else the rule is a RATCHET on the SET OF FILES, not on a count.
    The legacy fallbacks are being deleted by the activation work as each old
    path is replaced, so a count-based ratchet would go red for a collaborator
    every time one shrank, and a guard that goes red for a change that is fine
    is a guard people start editing rather than reading. A set-based ratchet
    shrinks silently, holds firm, and still fails the moment a NEW module
    acquires a fallback of the forbidden kind, which is the regression that
    matters.

  * The third sweep (2026-09-24) is the one the `except: pass` sweep could not
    see: a BROAD handler (bare, `Exception`, `BaseException`) whose body neither
    re-raises, nor logs, nor so much as reads the exception it caught. `return
    False` and `return []` are not `pass`, so the SNS signature check that
    swallowed everything and the link critic that turned any bug into "the
    links are fine" passed the older sweep for as long as they existed.

The inventories below are MEASUREMENTS, taken by sweeping the tree, not
wishes. Every entry is a real instance that exists today and is named with its
owner so it can be deleted.
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
from app.services.hiring import gates

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


def _stage(stage: str, **overrides) -> provenance.StageRecord:
    kwargs = dict(
        correlation_id=CORRELATION,
        stage=stage,
        agent_id=identity.SUTRA,
        tenant_id=str(TENANT),
        principal_user_id=PRINCIPAL.user_id,
        principal_role=PRINCIPAL.role,
        job_id=str(JOB),
    )
    kwargs.update(overrides)
    return provenance.StageRecord(**kwargs)


# ══════════════════════════════════════════════════════════════════════════
# 1. THE LIVE REFUSAL POINTS REFUSE, AND SAY WHAT TO DO
# ══════════════════════════════════════════════════════════════════════════


def test_an_empty_scorecard_is_refused_and_the_refusal_names_the_table() -> None:
    """The `framework_generated_at` failure, as a rule. Nineteen of thirty-five
    live jobs carried a generation stamp and zero competency rows, and every
    health check asked the stamp. G1 asks the TABLE first."""
    result = gates.scorecard_gate(matrix_items=[], approved_at="2026-09-24")
    assert not result.passed
    assert result.blocking
    assert any("check the table, not the timestamp" in r for r in result.reasons)


def test_an_unapproved_scorecard_is_refused_and_says_why_it_matters() -> None:
    result = gates.scorecard_gate(matrix_items=[{"name": "SQL"}], approved_at=None)
    assert not result.passed
    assert any("approved by a human" in r for r in result.reasons)


def test_an_approved_populated_scorecard_passes_straight_through() -> None:
    """The guard must not be a tax on the healthy path, and must not simply be
    always-fail."""
    result = gates.scorecard_gate(
        matrix_items=[{"name": "SQL"}], approved_at="2026-09-24"
    )
    assert result.passed
    assert result.reasons == ()


def test_a_flag_with_no_recorded_disposition_blocks_delivery() -> None:
    """No flag may auto-resolve: a report flagged for review cannot be
    delivered until a PERSON decided, and the refusal says so."""
    result = gates.human_review_gate(needs_review=True, disposition=None)
    assert not result.passed and result.blocking
    assert any("No flag may auto-resolve" in r for r in result.reasons)


def test_a_disposition_nobody_is_named_for_is_refused() -> None:
    result = gates.human_review_gate(
        needs_review=True, disposition=gates.DISPOSITION_CLEARED, decided_by=None
    )
    assert not result.passed
    assert any("no person attached" in r for r in result.reasons)


def test_there_is_no_automatic_disposition() -> None:
    assert not any("auto" in d for d in gates.DISPOSITIONS)
    for disposition in gates.DISPOSITIONS:
        assert gates.human_review_gate(
            needs_review=True, disposition=disposition, decided_by=uuid.uuid4()
        ).passed


def test_an_unknown_gate_is_refused_by_name_rather_than_skipped() -> None:
    """A dispatcher that answered an unknown name with a pass would turn a typo
    into a gate that never runs."""
    with pytest.raises(ValueError) as exc:
        gates.run_gate("G9_nonexistent")
    assert "G9_nonexistent" in str(exc.value)


def test_a_stage_that_published_nothing_is_reported_as_a_timestamp() -> None:
    """An agent stage that left no artifact behind is a record of TIME, not of
    work, and the ledger says so in the words the defect earned."""
    ledger = provenance.Ledger(CORRELATION)
    ledger.record(_stage(provenance.STAGE_MATRIX))
    problems = ledger.problems(expected=(provenance.STAGE_MATRIX,))
    assert any("a timestamp is not evidence" in p for p in problems)


def test_a_stage_with_its_artifact_is_healthy() -> None:
    ledger = provenance.Ledger(CORRELATION)
    ledger.record(_stage(provenance.STAGE_MATRIX, artifact_id="a-1"))
    assert ledger.problems(expected=(provenance.STAGE_MATRIX,)) == []


def test_a_stage_that_never_ran_is_named() -> None:
    ledger = provenance.Ledger(CORRELATION)
    problems = ledger.problems(expected=(provenance.STAGE_SCORING,))
    assert any("never ran" in p and provenance.STAGE_SCORING in p for p in problems)


def test_a_ledger_keyed_on_a_job_id_is_refused() -> None:
    """A job id in the correlation slot is hex, reads correctly in a log line,
    and joins the flow to nothing."""
    with pytest.raises(ValueError):
        provenance.Ledger(str(JOB))


def test_a_stage_from_another_flow_is_refused() -> None:
    ledger = provenance.Ledger(CORRELATION)
    other = provenance.correlation_for_job(uuid.uuid4())
    with pytest.raises(ValueError):
        ledger.record(_stage(provenance.STAGE_MATRIX, correlation_id=other))
    assert len(ledger) == 0


def test_a_run_with_no_human_principal_is_refused() -> None:
    """RBAC 34. A row that lost the human reads exactly like a human action."""
    with pytest.raises(provenance.MissingPrincipal):
        _envelope(principal=None).require_principal()


def test_a_principal_from_another_tenant_is_refused() -> None:
    """A cross-tenant action with a plausible-looking audit row attached."""
    stranger = provenance.Principal(
        user_id=str(uuid.uuid4()), role="recruiter", tenant_id=str(uuid.uuid4())
    )
    with pytest.raises(provenance.MissingPrincipal):
        _envelope(principal=stranger).require_principal()


def test_a_run_with_no_correlation_id_is_refused() -> None:
    with pytest.raises(run_envelope.MissingCorrelationId):
        _envelope(correlation_id=None).require_correlation_id()
    with pytest.raises(run_envelope.MissingCorrelationId):
        _envelope(correlation_id=str(JOB)).require_correlation_id()


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
#: The orchestration package left this list on 2026-09-24 because it is deleted:
#: it had no production caller, so a guard over it guarded nothing.
CLEAN_PACKAGES: tuple[str, ...] = (
    "services/hiring",
    "services/miti",
    "services/siddhi",
    "services/agents",
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
#:
#: `services/interview_telemetry.py` left on 2026-09-24: its four observers
#: now log `interview_telemetry.emit_failed` and an unreadable latency is
#: counted in the summary rather than passed over. The trajectory eval script
#: left with its deletion (Phase 7 WP-B5).
LEGACY_SWALLOWER_FILES: frozenset[str] = frozenset(
    {
        "api/candidates.py",
        "services/document_storage.py",
        "services/jd_generation.py",
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
        rel
        for rel in LEGACY_FALLBACK_FILES
        | LEGACY_SWALLOWER_FILES
        | LEGACY_SILENT_HANDLER_FILES
        if not (APP / rel).exists()
    )
    assert not missing, (
        "Listed in the legacy inventory and no longer on disk. Remove the "
        "entries: " + ", ".join(missing)
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. THE SILENT BROAD HANDLER
# ══════════════════════════════════════════════════════════════════════════
#
# The `except: pass` sweep above matches one SHAPE of swallowing, and the
# 2026-09 audit found the others walking straight past it: `except Exception:
# return False` around an SNS signature check, `except Exception: return []`
# around a link critic, `except Exception: serialized = str(value)`. None of
# them is `pass`, so all of them passed. What they share is the property that
# actually matters: the handler catches EVERYTHING, a programming error
# included, and then behaves as if nothing had happened, leaving no trace that
# anything did.
#
# So this sweep asks the property, not the shape. A handler is SILENT when:
#
#   * it is BROAD: bare `except:`, `except Exception`, `except BaseException`,
#     or a tuple containing one of them; and
#   * its body neither RE-RAISES, nor LOGS (any call to a `.debug`, `.info`,
#     `.warning`, `.error`, `.exception`, `.critical` or `.log` method), nor so
#     much as READS the exception it bound. Reading it is the floor of
#     recording an outcome: `failure = type(exc).__name__` puts the failure in
#     the result a caller receives, which `return None` does not.
#
# A NARROW handler is deliberately out of scope. `except ValueError: return
# None` around a parse is a decision about one named failure; the defect this
# sweep exists for is the handler that cannot tell a hostile input from a bug
# in the line above it.

_BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})
_LOG_METHODS = frozenset(
    {"debug", "info", "warning", "error", "exception", "critical", "log"}
)

#: Every file with a silent broad handler today, measured on 2026-09-24 after
#: the Phase 7 WP-B7 fixes and re-measured at the stage 3 integration (the
#: unreachable subsystems, the SMS sender and `validate_ppi.py` are deleted
#: and WP-B6 narrowed the Razorpay webhook to its unique violation). Same ratchet rule as above: it may shrink, it may
#: not grow. Each entry names its owner.
#:
#:   (functional_assessment, interviewer and video/processing left with the
#:   grading split, PLAN-p5 WP5-D.)
#:   services/projects/parsers.py   JUSTIFIED: a corrupt PDF or DOCX returns
#:                                  an artifact carrying `supported=False` and
#:                                  a written limitation, which is the recorded
#:                                  outcome; the parser reads hostile files and
#:                                  a library raises anything.
#:   services/projects/invisible_text.py
#:                                  JUSTIFIED: one unreadable page increments
#:                                  `stream_failures`, which the scan reports.
#:   scripts/validate_stack.py      an operator CLI check whose handler IS the
#:                                  verdict (`refused = True`, WARN in the
#:                                  caller); it never runs on a request.
LEGACY_SILENT_HANDLER_FILES: frozenset[str] = frozenset(
    {
        "scripts/validate_stack.py",
        # functional_assessment.py, interviewer.py and video/processing.py
        # left with the grading split (PLAN-p5 WP5-D): the orchestrator holds
        # no handler, the follow-up parser catches only a JSON decode error
        # and logs it, and the processing handler records then re-raises.
        "services/projects/invisible_text.py",
        "services/projects/parsers.py",
    }
)


def _is_broad(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in _BROAD_EXCEPTIONS
    if isinstance(node, ast.Attribute):
        return node.attr in _BROAD_EXCEPTIONS
    if isinstance(node, ast.Tuple):
        return any(_is_broad(element) for element in node.elts)
    return False


def _accounts_for_the_failure(handler: ast.ExceptHandler) -> bool:
    for statement in handler.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Raise):
                return True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS
            ):
                return True
            if (
                handler.name
                and isinstance(node, ast.Name)
                and node.id == handler.name
            ):
                return True
    return False


def _silent_broad_handlers(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ExceptHandler)
        and _is_broad(node.type)
        and not _accounts_for_the_failure(node)
    ]


def _files_with_silent_broad_handlers() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in _python_files():
        lines = _silent_broad_handlers(path.read_text(encoding="utf-8"))
        if lines:
            found[_rel(path)] = lines
    return found


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    f()\nexcept Exception:\n    return_value = None\n",
        "try:\n    f()\nexcept:\n    x = 1\n",
        "try:\n    f()\nexcept (ValueError, BaseException):\n    x = 1\n",
        "try:\n    f()\nexcept builtins.Exception as exc:\n    x = 1\n",
    ],
)
def test_the_detector_finds_a_silent_broad_handler(source: str) -> None:
    """The detector is itself pinned, both ways, because a sweep with a blind
    spot is worse than none: the green result is what stops anybody looking.
    The last case binds the exception and never reads it, which is still
    silence."""
    assert _silent_broad_handlers(source) == [3]


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    f()\nexcept Exception:\n    raise\n",
        "try:\n    f()\nexcept Exception:\n    logger.warning('x')\n",
        "try:\n    f()\nexcept Exception:\n    logging.getLogger(__name__).info('x')\n",
        "try:\n    f()\nexcept Exception as exc:\n    failure = type(exc).__name__\n",
        "try:\n    f()\nexcept ValueError:\n    x = None\n",
        "try:\n    f()\nexcept (TypeError, ValueError):\n    x = None\n",
    ],
)
def test_the_detector_accepts_a_handler_that_accounts_for_the_failure(
    source: str,
) -> None:
    assert _silent_broad_handlers(source) == []


def test_the_part_a_packages_have_no_silent_broad_handler() -> None:
    offenders = {
        rel: lines
        for rel, lines in _files_with_silent_broad_handlers().items()
        if rel.startswith(CLEAN_PACKAGES)
    }
    assert not offenders, (
        "A broad handler that neither raises, logs, nor reads what it caught "
        f"absorbs a programming error as if it were a bad input: {offenders}"
    )


def test_no_new_module_acquires_a_silent_broad_handler() -> None:
    """The ratchet. A file fixed on 2026-09-24 (`api/email_senders.py`,
    `services/answer_classification.py`, `services/interview_telemetry.py`,
    `services/verification/email.py`, `services/agent_loop.py`) is NOT in the
    inventory, so any of them regrowing a silent handler fails here."""
    found = _files_with_silent_broad_handlers()
    new = sorted(set(found) - LEGACY_SILENT_HANDLER_FILES)
    assert not new, (
        "These modules newly catch everything and leave no trace. Narrow the "
        "handler to the failures it means, log it, or re-raise: "
        + ", ".join(f"{rel}:{found[rel]}" for rel in new)
    )
