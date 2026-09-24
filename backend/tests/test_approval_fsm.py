"""The direct-publish record (ESD 7), pure, no DB.

The multi-level chain this file used to walk is deleted (PLAN-p7 WP-B6; see
`services/approval_fsm.py`), and `tests/test_dead_routes_removed.py` keeps it
deleted. What remains is the one transition a live route runs, and the rule
it carries: the bypassed levels are LOGGED, never silently approved.
"""
from app.models.enums import APPROVAL_CHAIN, ApprovalDecision, JobStatus
from app.services import approval_fsm
from app.services.approval_fsm import plan_direct_publish


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


def test_only_the_direct_publish_path_survives() -> None:
    """The planner, its wrappers and its errors went with the chain."""
    for gone in (
        "plan_submit",
        "validate_transition",
        "next_active_level",
        "apply_submit",
        "apply_transition",
        "ApprovalError",
        "NotAssignedApprover",
        "PriorLevelPending",
        "ApprovalConfigError",
    ):
        assert not hasattr(approval_fsm, gone), gone
    # The clock seam the harness binds (`harness/faults.CLOCK_SEAMS`).
    assert callable(approval_fsm._now)
