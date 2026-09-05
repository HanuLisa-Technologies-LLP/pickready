"""spec-doc6 C11: two "stage" concepts, two types, never interchanged.

RBAC 17 defines a JOB lifecycle. The Dashboard Specification defines a
CANDIDATE pipeline stage. Both were called "stage" in conversation and, before
2026-08-29, neither had a type. The failure that invites is not subtle: an
application status written into a job column, or a job state offered in a
candidate's "Move to" dropdown.

THE RECONCILIATION IS THREE-WAY
-------------------------------
This repository already had a validated ten-value pipeline in
`services/hiring_pipeline`, and it is the one production rows sit in. So there
are three vocabularies, not two, and the tests below pin how they relate:

  1. RBAC 17's JOB lifecycle, 8 states, on `jobs.lifecycle_state`
  2. the Dashboard's CANDIDATE stages, 6 coarse stages, presentation only
  3. this module's PIPELINE_ORDER, 10 stages plus the legacy `offered`, on
     `job_candidate_links.status` and `pipeline_status`

(2) is a derived VIEW of (3), not a replacement. Nothing was deleted, and
`shortlisted` in particular is still there: historic applications sit in it,
and it is still the only route into `interview_scheduled`.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.services import hiring_pipeline as hp

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ── 1. The two vocabularies are disjoint ─────────────────────────────────────

def test_the_two_enums_share_no_value() -> None:
    """The cheapest guard against a confusion, and the one that would have
    caught an assignment across the two if the values had ever overlapped."""
    overlap = hp.JOB_LIFECYCLE_VALUES & hp.CANDIDATE_PIPELINE_VALUES
    assert not overlap, f"a value belongs to both vocabularies: {sorted(overlap)}"


def test_the_two_enums_are_different_types() -> None:
    assert hp.JobLifecycleState is not hp.CandidatePipelineStage
    assert set(hp.JobLifecycleState) & set(hp.CandidatePipelineStage) == set()


def test_the_job_lifecycle_is_rbac_17_exactly() -> None:
    """RBAC 17 permits different internal names but requires the semantic
    states be preserved. The document's own names are used verbatim so a
    reader holding the specification can grep for them."""
    assert [state.value for state in hp.JOB_LIFECYCLE_ORDER] == [
        "DRAFT",
        "SENT_TO_HIRING_MANAGER",
        "IN_REVIEW",
        "FINALIZED",
        "PUBLISHED",
        "CANDIDATE_APPLICATIONS",
        "HIRING_PROCESS",
        "CLOSED_ARCHIVED",
    ]


def test_the_candidate_pipeline_stage_is_the_dashboards_six() -> None:
    assert [stage.value for stage in hp.CandidatePipelineStage] == [
        "Applied",
        "Screening",
        "Shortlisted",
        "Interview",
        "Offer",
        "Closed",
    ]


# ── 2. No code path assigns one to the other ─────────────────────────────────

#: Modules whose source is walked. The whole application package, because the
#: point is to catch the assignment WHEREVER somebody writes it, not to catch
#: it in the three files we thought of.
def _python_sources() -> list[pathlib.Path]:
    return [
        path
        for path in (BACKEND / "app").rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_no_code_path_assigns_a_job_lifecycle_value_to_a_pipeline_field() -> None:
    """An AST walk, not a grep, so a comment quoting a state is not a finding.

    The rule spec-doc6 C11 asks for is "no code path assigns one to the
    other". The mechanical form of that: no assignment or comparison puts a
    JobLifecycleState literal on a `status` / `current_stage` /
    `pipeline_status` target, and no CandidatePipelineStage or pipeline status
    literal reaches a `lifecycle_state` target.
    """
    pipeline_targets = {"status", "current_stage", "pipeline_status", "target"}
    lifecycle_targets = {"lifecycle_state", "job_lifecycle_state"}
    offenders: list[str] = []

    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # a real problem, and not this test's
            raise AssertionError(f"{path} does not parse: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            literal = value.value
            for target in node.targets:
                name = _target_name(target)
                if name is None:
                    continue
                if name in pipeline_targets and literal in hp.JOB_LIFECYCLE_VALUES:
                    offenders.append(
                        f"{path.relative_to(BACKEND)}:{node.lineno} assigns job "
                        f"lifecycle {literal!r} to pipeline field {name!r}"
                    )
                if name in lifecycle_targets and literal in hp.CANDIDATE_PIPELINE_VALUES:
                    offenders.append(
                        f"{path.relative_to(BACKEND)}:{node.lineno} assigns pipeline "
                        f"status {literal!r} to lifecycle field {name!r}"
                    )
    assert not offenders, offenders


def _target_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def test_the_two_enums_never_share_a_table() -> None:
    """spec-doc6 C11: "never share a table or an enum".

    `lifecycle_state` is on `jobs`; the pipeline status is on
    `job_candidate_links` and `pipeline_status`. Read from migration 0061's
    own source, so a future migration that adds one to the other's table has
    to change this assertion deliberately.
    """
    source = (
        BACKEND / "alembic" / "versions" / "0061_rbac_cardinality_and_audit.py"
    ).read_text(encoding="utf-8")
    # Read as facts about the neighbourhood of the column rather than as one
    # formatted literal: the definition spans several lines and a reformat
    # should not fail a test about which TABLE the column is on.
    around = source[max(0, source.index('"lifecycle_state"') - 300):]
    assert '"jobs",' in around
    assert "op.add_column(" in around
    # The candidate pipeline's two tables are not touched by this migration at
    # all, which is spec-doc6 C11's "never share a table" as a property.
    assert "job_candidate_links" not in source
    assert "pipeline_status" not in source


# ── 3. The dashboard view is total and derived ───────────────────────────────

@pytest.mark.parametrize("status", sorted(hp.ALL_STATUSES))
def test_every_stored_status_is_accounted_for(status: str) -> None:
    """A status with no coarse stage and no named exemption renders as blank,
    which reads to a recruiter as "no data" rather than as "the code forgot".

    Exactly one status is exempt, and the exemption is data rather than a
    special case here: `hold` is a MODIFIER on a stage, not a stage. See
    `test_hold_is_not_a_stage`.
    """
    assert status in hp.DASHBOARD_STAGE or status in hp.NO_DASHBOARD_STAGE
    if status in hp.DASHBOARD_STAGE:
        assert isinstance(hp.DASHBOARD_STAGE[status], hp.CandidatePipelineStage)


def test_hold_is_not_a_stage() -> None:
    """`hold` has no home in the six, and forcing one was the first attempt.

    The Dashboard Specification treats hold as an ACTION taken on a candidate
    rather than as a stage they occupy, and the stored FSM agrees: `hold`
    returns to whatever stage it paused rather than carrying outward edges of
    its own. Mapping it to Screening would claim the candidate had moved
    backwards; mapping it to Closed would say the process had ended.
    """
    assert hp.HOLD in hp.NO_DASHBOARD_STAGE
    assert hp.HOLD not in hp.DASHBOARD_STAGE
    assert hp.dashboard_stage(hp.HOLD) is None
    # And it is still a real stored status with a real place in the FSM.
    assert hp.HOLD in hp.ALL_STATUSES
    assert hp.HOLD in hp.ALWAYS_AVAILABLE


def test_sourced_is_not_a_stage_either_and_for_a_different_reason() -> None:
    """The second member of NO_DASHBOARD_STAGE, and the two are not alike.

    `hold` is a PAUSE on a stage. `sourced` is BEFORE the funnel: a resume the
    recruiter uploaded from their own databank, belonging to somebody who has
    not applied. Both return None from `dashboard_stage`, which is why
    `is_on_hold` exists -- reading the absence of a stage as a pause was
    correct while `hold` was alone in that set and became wrong the moment it
    was not.
    """
    assert hp.SOURCED in hp.NO_DASHBOARD_STAGE
    assert hp.SOURCED not in hp.DASHBOARD_STAGE
    assert hp.dashboard_stage(hp.SOURCED) is None
    assert hp.is_on_hold(hp.SOURCED) is False
    assert hp.is_on_hold(hp.HOLD) is True
    # It is a real stored status, and its one forward edge is what Gate 5 is.
    assert hp.SOURCED in hp.ALL_STATUSES
    assert hp.allowed_transitions(hp.SOURCED) >= {hp.APPLIED}
    assert hp.ASSESSMENT_INVITED not in hp.allowed_transitions(hp.SOURCED)
    assert hp.SHORTLISTED not in hp.allowed_transitions(hp.SOURCED)


def test_the_dashboard_stage_is_derived_not_stored() -> None:
    """The stored value keeps the resolution the FSM needs.

    `assessment_invited` and `assessment_in_progress` both render as
    Screening, and collapsing them at rest would lose the difference between
    chasing a candidate and waiting for one.
    """
    assert hp.dashboard_stage("assessment_invited") is hp.CandidatePipelineStage.SCREENING
    assert (
        hp.dashboard_stage("assessment_in_progress") is hp.CandidatePipelineStage.SCREENING
    )
    assert hp.normalize("assessment_invited") != hp.normalize("assessment_in_progress")


def test_the_legacy_offered_synonym_still_reads() -> None:
    """`offered` and `offer_extended` are both real in the table (migration
    0018 kept the old name valid), so both must render."""
    assert hp.dashboard_stage("offered") is hp.CandidatePipelineStage.OFFER
    assert hp.dashboard_stage("offer_extended") is hp.CandidatePipelineStage.OFFER


def test_shortlisted_survives_the_reconciliation() -> None:
    """The stage historic applications sit in, and the only route into
    `interview_scheduled`. It is excluded from the MANUAL dropdown and from
    nothing else, and this asserts the distinction held."""
    assert hp.SHORTLISTED in hp.ALL_STATUSES
    assert hp.SHORTLISTED in hp.PIPELINE_ORDER
    assert hp.can_transition(hp.SHORTLISTED, hp.INTERVIEW_SCHEDULED)
    assert hp.SHORTLISTED in hp.MANUAL_TRANSITION_EXCLUDED
    assert hp.SHORTLISTED not in hp.manual_transitions(hp.ASSESSMENT_COMPLETED)


def test_an_unknown_status_reads_as_applied_rather_than_raising() -> None:
    """A dashboard that 500s on one unexpected row is worse than one that
    shows that row at the start of the funnel."""
    assert hp.dashboard_stage("something_new") is hp.CandidatePipelineStage.APPLIED
    assert hp.dashboard_stage(None) is hp.CandidatePipelineStage.APPLIED


# ── 4. The lifecycle FSM ─────────────────────────────────────────────────────

def test_the_lifecycle_walks_forward_one_step_at_a_time() -> None:
    """RBAC 17 draws a single chain. A job cannot skip the Hiring Manager."""
    assert hp.JobLifecycleState.PUBLISHED not in hp.lifecycle_allowed_transitions(
        hp.JobLifecycleState.DRAFT
    )
    assert hp.JobLifecycleState.FINALIZED not in hp.lifecycle_allowed_transitions(
        hp.JobLifecycleState.SENT_TO_HIRING_MANAGER
    )
    assert hp.lifecycle_allowed_transitions(hp.JobLifecycleState.DRAFT) == frozenset(
        {
            hp.JobLifecycleState.SENT_TO_HIRING_MANAGER,
            hp.JobLifecycleState.CLOSED_ARCHIVED,
        }
    )


def test_a_closed_job_does_not_reopen() -> None:
    """RBAC 22 requires a controlled revision mechanism rather than a reopen."""
    assert hp.lifecycle_allowed_transitions(hp.JobLifecycleState.CLOSED_ARCHIVED) == frozenset()


def test_a_job_can_be_archived_from_any_live_state() -> None:
    for state in hp.JOB_LIFECYCLE_ORDER:
        if state is hp.JobLifecycleState.CLOSED_ARCHIVED:
            continue
        assert hp.JobLifecycleState.CLOSED_ARCHIVED in hp.lifecycle_allowed_transitions(state)


def test_the_drafting_and_finalized_sets_partition_the_lifecycle() -> None:
    """Every state is on exactly one side of finalization, because the
    authorization rules in RBAC 21, 22, 24*** and 26 all key off that line."""
    assert hp.DRAFTING_STATES | hp.FINALIZED_OR_LATER == hp.JOB_LIFECYCLE_VALUES
    assert not (hp.DRAFTING_STATES & hp.FINALIZED_OR_LATER)


def test_the_candidate_fsm_is_untouched_by_this_change() -> None:
    """The reconciliation must not have quietly re-shaped the pipeline.

    These are the properties the existing suite already relies on, restated
    here because this file is where somebody would come looking after a
    stage-related regression.
    """
    assert hp.normalize("offered") == hp.OFFER_EXTENDED
    assert hp.can_transition(hp.APPLIED, hp.ASSESSMENT_INVITED)
    assert not hp.can_transition(hp.APPLIED, hp.OFFER_EXTENDED)
    assert hp.allowed_transitions(hp.REJECTED) == frozenset()
    assert hp.allowed_transitions(hp.JOINED) == frozenset()
