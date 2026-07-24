"""Approval FSM unit tests (ESD §7) — pure core, no DB."""
import uuid

import pytest

from app.models.enums import ApprovalDecision, JobStatus
from app.models.enums import APPROVAL_CHAIN
from app.services.approval_fsm import (
    AlreadyTerminal,
    ApprovalConfigError,
    NotAssignedApprover,
    NotSubmitted,
    PriorLevelPending,
    next_active_level,
    plan_direct_publish,
    plan_submit,
    validate_transition,
)

A, B, C, D = (str(uuid.uuid4()) for _ in range(4))
STRANGER = str(uuid.uuid4())


def config_all_active() -> dict:
    return {
        "requested": {"active": True, "approver_user_id": A},
        "recommended": {"active": True, "approver_user_id": B},
        "approved": {"active": True, "approver_user_id": C},
        "ratified": {"active": True, "approver_user_id": D},
    }


def config_with_inactive() -> dict:
    cfg = config_all_active()
    cfg["recommended"] = {"active": False, "approver_user_id": None}
    return cfg


# ── next_active_level ────────────────────────────────────────────────────────

def test_next_active_from_start() -> None:
    assert next_active_level(config_all_active(), None) == JobStatus.requested


def test_next_active_skips_inactive() -> None:
    assert next_active_level(config_with_inactive(), JobStatus.requested) == JobStatus.approved


def test_next_active_none_after_last() -> None:
    assert next_active_level(config_all_active(), JobStatus.ratified) is None


def test_missing_level_treated_inactive() -> None:
    cfg = {"approved": {"active": True, "approver_user_id": C}}
    assert next_active_level(cfg, None) == JobStatus.approved


# ── submit ───────────────────────────────────────────────────────────────────

def test_submit_all_active_goes_to_requested() -> None:
    result = plan_submit(config_all_active())
    assert result.new_status == JobStatus.requested
    assert result.rows == []
    assert result.ratified is False


def test_submit_with_leading_inactive_logs_skip() -> None:
    cfg = config_all_active()
    cfg["requested"] = {"active": False, "approver_user_id": None}
    result = plan_submit(cfg)
    assert result.new_status == JobStatus.recommended
    assert [(r.level, r.decision) for r in result.rows] == [
        (JobStatus.requested, ApprovalDecision.skipped)
    ]
    assert result.rows[0].remarks == "level skipped (inactive)"


def test_submit_all_inactive_ratifies_with_all_skips_logged() -> None:
    cfg = {level.value: {"active": False} for level in
           (JobStatus.requested, JobStatus.recommended, JobStatus.approved, JobStatus.ratified)}
    result = plan_submit(cfg)
    assert result.new_status == JobStatus.ratified
    assert result.ratified is True
    assert len(result.rows) == 4
    assert all(r.decision == ApprovalDecision.skipped for r in result.rows)


def test_submit_without_config_errors() -> None:
    with pytest.raises(ApprovalConfigError):
        plan_submit(None)
    with pytest.raises(ApprovalConfigError):
        plan_submit({})


# ── direct publish (flat staff model, PRD v1.0 §4) ───────────────────────────

def test_direct_publish_ratifies_immediately() -> None:
    result = plan_direct_publish()
    assert result.new_status == JobStatus.ratified
    assert result.ratified is True


def test_direct_publish_logs_every_level_as_skipped() -> None:
    result = plan_direct_publish()
    # Bypass is auditable: all 4 chain levels logged skipped, never silent.
    assert [r.level for r in result.rows] == list(APPROVAL_CHAIN)
    assert all(r.decision == ApprovalDecision.skipped for r in result.rows)
    assert all(r.approver_user_id is None for r in result.rows)
    assert all("direct publish" in (r.remarks or "") for r in result.rows)


def test_direct_publish_needs_no_config() -> None:
    # Unlike plan_submit, the flat path is config-free (never raises).
    assert plan_direct_publish().ratified is True


# ── full all-active chain ────────────────────────────────────────────────────

def test_all_active_chain_walks_every_level() -> None:
    cfg = config_all_active()
    status = plan_submit(cfg).new_status
    for approver, expected_next in [
        (A, JobStatus.recommended),
        (B, JobStatus.approved),
        (C, JobStatus.ratified),
    ]:
        result = validate_transition(cfg, status, approver, ApprovalDecision.approved)
        # `approved` level passing moves to pending-ratified; not terminal yet.
        assert result.new_status == expected_next
        status = result.new_status

    final = validate_transition(cfg, status, D, ApprovalDecision.approved)
    assert final.new_status == JobStatus.ratified
    assert final.ratified is True
    assert [(r.level, r.decision) for r in final.rows] == [
        (JobStatus.ratified, ApprovalDecision.approved)
    ]


def test_intermediate_level_not_ratified() -> None:
    result = validate_transition(
        config_all_active(), JobStatus.requested, A, ApprovalDecision.approved
    )
    assert result.new_status == JobStatus.recommended
    assert result.ratified is False


# ── inactive-skip behavior ───────────────────────────────────────────────────

def test_inactive_level_skipped_and_logged_explicitly() -> None:
    result = validate_transition(
        config_with_inactive(), JobStatus.requested, A, ApprovalDecision.approved
    )
    assert result.new_status == JobStatus.approved  # recommended skipped
    levels = [(r.level, r.decision) for r in result.rows]
    assert (JobStatus.requested, ApprovalDecision.approved) in levels
    assert (JobStatus.recommended, ApprovalDecision.skipped) in levels
    skipped = next(r for r in result.rows if r.decision == ApprovalDecision.skipped)
    assert skipped.remarks == "level skipped (inactive)"
    assert skipped.approver_user_id is None


def test_trailing_inactive_levels_skip_to_ratified() -> None:
    cfg = {
        "requested": {"active": True, "approver_user_id": A},
        "recommended": {"active": True, "approver_user_id": B},
        "approved": {"active": False},
        "ratified": {"active": False},
    }
    result = validate_transition(cfg, JobStatus.recommended, B, ApprovalDecision.approved)
    assert result.new_status == JobStatus.ratified
    assert result.ratified is True
    skipped_levels = {r.level for r in result.rows if r.decision == ApprovalDecision.skipped}
    assert skipped_levels == {JobStatus.approved, JobStatus.ratified}


# ── wrong approver / out-of-order / terminal ─────────────────────────────────

def test_wrong_approver_rejected() -> None:
    with pytest.raises(NotAssignedApprover):
        validate_transition(
            config_all_active(), JobStatus.requested, STRANGER, ApprovalDecision.approved
        )


def test_out_of_order_later_approver_gets_prior_level_pending() -> None:
    # C approves at 'approved', but the job is still pending at 'requested'.
    with pytest.raises(PriorLevelPending):
        validate_transition(
            config_all_active(), JobStatus.requested, C, ApprovalDecision.approved
        )


def test_terminal_job_rejects_further_transitions() -> None:
    # With an ACTIVE ratified level, status `ratified` is ambiguous (pending
    # vs done) — the caller passes terminal from job.ratified_at.
    with pytest.raises(AlreadyTerminal):
        validate_transition(
            config_all_active(), JobStatus.ratified, D,
            ApprovalDecision.approved, terminal=True,
        )


def test_terminal_inferred_when_ratified_level_inactive() -> None:
    # Status ratified + inactive ratified level can only mean the chain
    # completed — terminal is inferred without an explicit flag.
    cfg = config_all_active()
    cfg["ratified"] = {"active": False}
    with pytest.raises(AlreadyTerminal):
        validate_transition(cfg, JobStatus.ratified, C, ApprovalDecision.approved)


def test_pending_at_active_ratified_level_is_not_terminal() -> None:
    # Job sits at status `ratified` awaiting D (active ratified level):
    # D's approval must go through and produce the terminal result.
    result = validate_transition(
        config_all_active(), JobStatus.ratified, D,
        ApprovalDecision.approved, terminal=False,
    )
    assert result.new_status == JobStatus.ratified
    assert result.ratified is True


def test_draft_job_cannot_be_approved() -> None:
    with pytest.raises(NotSubmitted):
        validate_transition(
            config_all_active(), JobStatus.draft, A, ApprovalDecision.approved
        )


# ── rejection ────────────────────────────────────────────────────────────────

def test_rejection_returns_job_to_draft_and_logs_row() -> None:
    result = validate_transition(
        config_all_active(), JobStatus.recommended, B,
        ApprovalDecision.rejected, remarks="budget freeze",
    )
    assert result.new_status == JobStatus.draft
    assert result.ratified is False
    assert [(r.level, r.decision, r.remarks) for r in result.rows] == [
        (JobStatus.recommended, ApprovalDecision.rejected, "budget freeze")
    ]


def test_approval_row_carries_approver_and_remarks() -> None:
    result = validate_transition(
        config_all_active(), JobStatus.requested, A,
        ApprovalDecision.approved, remarks="looks good",
    )
    row = result.rows[0]
    assert str(row.approver_user_id) == A
    assert row.remarks == "looks good"
