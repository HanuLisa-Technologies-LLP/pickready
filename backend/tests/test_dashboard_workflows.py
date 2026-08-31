"""The Dashboard Specification's three named workflows, as user journeys.

spec-doc6 §8.1: "Implement the three named workflows (fast triage, integrity
review, team calibration) as tested user journeys, not just as rendered
components."

WHAT "AS A JOURNEY" MEANS HERE
------------------------------
Each test below is a SEQUENCE of real HTTP requests in the order a person makes
them, with the assertion on what the NEXT screen says. That is the difference
that matters: a component test proves a locked dropdown renders locked; only a
journey proves that an HR Manager recording a disposition is what unlocks it,
which is workflow 2's entire point and is a claim about three routes and a
database, not about a component.

The fixtures are obviously synthetic (spec-doc6 C14). No real names.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa

from app.models.enums import Role
from app.services import calibration, rating, team_review
from app.services.hiring import gates as hiring_gates
from app.services.hiring import prescreen
from tests.dashboard_world import (  # noqa: F401 - fixtures used by name
    BASE,
    SKIP_REASON,
    Caller,
    caller,
    engine,
    no_permission_cache,
    schema_is_current,
)

#: One job, five candidates, deliberately spread across the states column 4
#: has to distinguish. Named for what they demonstrate, never for a person.
CANDIDATES = (
    # (label, prescreen grade, ready pick score, integrity finding)
    ("Strong Assessed", prescreen.GRADE_A, 88.0, False),
    ("Ready Assessed", prescreen.GRADE_A, 78.0, False),
    ("Reserved Assessed", prescreen.GRADE_B, 65.0, False),
    ("Flagged Assessed", prescreen.GRADE_B, 84.0, True),
    ("Unassessed Applicant", None, None, False),
)


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.job = uuid.uuid4()
        self.users: dict[Role, uuid.UUID] = {}
        self.links: dict[str, uuid.UUID] = {}
        self.evaluations: dict[str, uuid.UUID] = {}


ROLES = (
    Role.client,
    Role.hr_manager,
    Role.recruiter,
    Role.hiring_manager,
    Role.interview_manager,
)


async def _seed(state: World) -> None:
    eng = engine()
    try:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            await conn.execute(
                sa.text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:id, 'Journey Testing Co', :domain, 'pending')"
                ),
                {"id": state.tenant, "domain": f"{state.tenant}.journey.test"},
            )
            for role in ROLES:
                user_id = uuid.uuid4()
                state.users[role] = user_id
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, tenant_id, role, email, full_name, "
                        "status, auth_providers) VALUES (:id, :tenant, :role, "
                        ":email, :name, 'active', CAST('{}' AS jsonb))"
                    ),
                    {
                        "id": user_id,
                        "tenant": state.tenant,
                        "role": role.value,
                        "email": f"{user_id}@journey.test",
                        "name": f"Test {role.value.replace('_', ' ').title()}",
                    },
                )
            await conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                    "lifecycle_state) VALUES (:id, :tenant, 'Test Role', "
                    "CAST('{}' AS jsonb), 'ratified', 'CANDIDATE_APPLICATIONS')"
                ),
                {"id": state.job, "tenant": state.tenant},
            )
            await conn.execute(
                sa.text(
                    "INSERT INTO job_assignments (tenant_id, job_id, user_id, "
                    "assignment_role) VALUES (:tenant, :job, :user, 'recruiter')"
                ),
                {
                    "tenant": state.tenant,
                    "job": state.job,
                    "user": state.users[Role.recruiter],
                },
            )
            await conn.execute(
                sa.text(
                    "INSERT INTO job_assignments (tenant_id, job_id, user_id, "
                    "assignment_role) VALUES (:tenant, :job, :user, "
                    "'interview_manager')"
                ),
                {
                    "tenant": state.tenant,
                    "job": state.job,
                    "user": state.users[Role.interview_manager],
                },
            )

            for label, grade, score, flagged in CANDIDATES:
                candidate_id = uuid.uuid4()
                link_id = uuid.uuid4()
                state.links[label] = link_id
                await conn.execute(
                    sa.text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, "
                        "consent_databank) VALUES (:id, :tenant, :name, :email, "
                        "false)"
                    ),
                    {
                        "id": candidate_id,
                        "tenant": state.tenant,
                        "name": f"Test Candidate {label}",
                        "email": f"{candidate_id}@journey.test",
                    },
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, "
                        "candidate_id, source, status, source_type, "
                        "prescreen_grade) VALUES (:id, :tenant, :job, :cand, "
                        "'fresh', 'applied', 'applied', :grade)"
                    ),
                    {
                        "id": link_id,
                        "tenant": state.tenant,
                        "job": state.job,
                        "cand": candidate_id,
                        "grade": grade,
                    },
                )
                if score is None:
                    continue
                evaluation_id = uuid.uuid4()
                state.evaluations[label] = evaluation_id
                gates = (
                    [
                        {
                            "gate": hiring_gates.G3,
                            "passed": False,
                            "blocking": False,
                            "reasons": [
                                "The account's internal consistency graded partial."
                            ],
                        }
                    ]
                    if flagged
                    else []
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO evaluations (id, tenant_id, job_id, link_id, "
                        "aggregate_json, dimension_scores, gate_results_json, "
                        "confidence) VALUES (:id, :tenant, :job, :link, "
                        "CAST(:agg AS jsonb), CAST(:dims AS jsonb), "
                        "CAST(:gates AS jsonb), 'high')"
                    ),
                    {
                        "id": evaluation_id,
                        "tenant": state.tenant,
                        "job": state.job,
                        "link": link_id,
                        "agg": json.dumps(
                            {
                                "adjusted_composite": score,
                                "raw_composite": score + 2,
                                "overall_grade": rating.grade_for_percent(score),
                                "category_grades": {
                                    "must_have": rating.grade_for_percent(score)
                                },
                                "category_scores": {"must_have": score},
                                "confidence": "high",
                                "why_this_candidate": (
                                    "Owns a comparable production migration "
                                    "end to end."
                                ),
                            }
                        ),
                        "dims": json.dumps(
                            {
                                "verified_competence": {
                                    "band": "strong",
                                    "evidence_refs": ["ev-1"],
                                }
                            }
                        ),
                        "gates": json.dumps(gates),
                    },
                )
    finally:
        await eng.dispose()


async def _teardown(state: World) -> None:
    eng = engine()
    try:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            await conn.execute(
                sa.text("DELETE FROM tenants WHERE id = :id"), {"id": state.tenant}
            )
    finally:
        await eng.dispose()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    if not asyncio.run(schema_is_current()):
        pytest.skip(SKIP_REASON)
    state = World()
    asyncio.run(_seed(state))
    try:
        yield state
    finally:
        asyncio.run(_teardown(state))


def _as(caller: Caller, world: World, role: Role) -> None:
    caller.as_user(world.users[role], world.tenant, role)


# ── Workflow 1: fast triage ──────────────────────────────────────────────────


def test_workflow_one_fast_triage(caller: Caller, world: World) -> None:
    """Land, sort by Ready Pick Score, skim the top, move one to Interview.

    The specification's own five steps, in order. What each assertion is
    defending:

      * the sort runs in SQL and puts the unassessed candidate LAST rather than
        first, which an ascending or a NULLS FIRST order would do;
      * the flagged candidate does not present a number to be triaged on;
      * the move is made through the server's own transition list, so the UI
        never has to know the FSM.
    """
    _as(caller, world, Role.recruiter)

    # 1 and 2. Land on the dashboard, sort descending by score.
    page = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job), "sort": "score"}
    ).json()
    assert page["page_size"] == 25
    names = [row["full_name"] for row in page["rows"]]
    scores = [row["ready_pick_score"] for row in page["rows"]]

    # Descending, with every unscored row last. The flagged candidate has a
    # stored composite of 84 and still shows no number: Under Review withholds
    # it, which is why it sits with the unassessed rows at the end.
    scored = [s for s in scores if s is not None]
    assert scored == sorted(scored, reverse=True)
    assert scores[len(scored):] == [None] * (len(scores) - len(scored))
    assert names[0].endswith("Strong Assessed")

    # 3. Skim the "why" on the top candidate.
    top = page["rows"][0]
    assert top["band_label"] == "Ready to Pick, Strong"
    profile = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{top['link_id']}/profile"
    ).json()
    assert profile["artifact"] == "ready_pick_profile"
    assert profile["why_this_candidate"].startswith("Owns a comparable")

    # 4. Use the Pre-Screen Grade as a secondary filter. Filtered in SQL, so
    #    the total is the whole match rather than the part on this page.
    filtered = caller.http.get(
        f"{BASE}/candidates",
        params={"job_id": str(world.job), "pre_screen_grade": prescreen.GRADE_A},
    ).json()
    assert filtered["total"] == 2
    assert {row["pre_screen_grade"] for row in filtered["rows"]} == {
        prescreen.GRADE_A
    }

    # 5. Move the top candidate onward. The options come from the server.
    stage = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{top['link_id']}/stage"
    ).json()
    assert stage["can_move"] is True
    target = stage["allowed_transitions"][0]["status"]
    moved = caller.http.post(
        f"{BASE}/jobs/{world.job}/candidates/{top['link_id']}/stage",
        json={"status": target},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["stored_status"] == target


def test_a_page_is_twenty_five_rows_and_the_order_is_total(
    caller: Caller, world: World
) -> None:
    """Two rows sharing a score must not swap between two fetches.

    Without a trailing `created_at, id` a paginated list can show one candidate
    twice and another not at all, which is a defect nobody reports because it
    looks like a refresh.
    """
    _as(caller, world, Role.hr_manager)
    first = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job), "page_size": 2}
    ).json()
    second = caller.http.get(
        f"{BASE}/candidates",
        params={"job_id": str(world.job), "page_size": 2, "page": 2},
    ).json()
    third = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job), "page_size": 2}
    ).json()
    assert [r["link_id"] for r in first["rows"]] == [
        r["link_id"] for r in third["rows"]
    ]
    assert not set(r["link_id"] for r in first["rows"]) & set(
        r["link_id"] for r in second["rows"]
    )
    assert first["total"] == second["total"] == len(CANDIDATES)


def test_an_unknown_filter_value_is_refused_rather_than_ignored(
    caller: Caller, world: World
) -> None:
    """Dropping it answers a narrower question than the one that was asked,
    while reporting a total for the wider one."""
    _as(caller, world, Role.hr_manager)
    assert (
        caller.http.get(
            f"{BASE}/candidates", params={"source_type": "walk_in"}
        ).status_code
        == 422
    )


def test_all_three_source_values_are_filterable(caller: Caller, world: World) -> None:
    """spec-doc6 C40: a two-value Source filter silently hides every `sourced`
    candidate, which is every applicant who arrived by a shared job link."""
    _as(caller, world, Role.hr_manager)
    page = caller.http.get(f"{BASE}/candidates").json()
    assert page["source_types"] == ["applied", "sourced", "databank"]
    for value in page["source_types"]:
        assert (
            caller.http.get(
                f"{BASE}/candidates", params={"source_type": value}
            ).status_code
            == 200
        )


# ── Workflow 2: integrity review ─────────────────────────────────────────────


def test_workflow_two_integrity_review(caller: Caller, world: World) -> None:
    """Flagged row locks the stage, HR Manager disposes, row re-enables.

    The whole point of running this as a journey: the unlock is a claim about
    what a DIFFERENT person doing a DIFFERENT thing on a THIRD route causes.
    A component test of a locked dropdown cannot make it.
    """
    link_id = world.links["Flagged Assessed"]

    # 1 and 2. The recruiter sees the flag and cannot move the candidate.
    _as(caller, world, Role.recruiter)
    page = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job)}
    ).json()
    flagged = next(r for r in page["rows"] if r["link_id"] == str(link_id))
    assert flagged["under_integrity_review"] is True
    assert flagged["band_label"] == "Under Review"
    assert flagged["ready_pick_score"] is None
    assert (
        flagged["band_screen_reader_label"]
        == "Status: Under Review, awaiting integrity disposition"
    )

    locked = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/stage"
    ).json()
    assert locked["can_move"] is False
    assert locked["disabled_reason"] == "Pending integrity review, HR Manager only"
    assert locked["allowed_transitions"] == []

    refused = caller.http.post(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/stage",
        json={"status": "assessment_invited"},
    )
    assert refused.status_code == 403

    # 3. The recruiter reads the contradiction. G3 blocks NOTHING about the
    #    person: the profile is fully readable while the flag is open.
    profile = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/profile"
    ).json()
    assert profile["under_integrity_review"] is True
    assert profile["open_flags"][0]["gate"] == hiring_gates.G3

    # 4 and 5. The HR Manager, and only the HR Manager, closes the flag.
    _as(caller, world, Role.recruiter)
    assert (
        caller.http.post(
            f"{BASE}/jobs/{world.job}/candidates/{link_id}/integrity-disposition",
            json={"disposition": "cleared"},
        ).status_code
        == 403
    )

    _as(caller, world, Role.hr_manager)
    cleared = caller.http.post(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/integrity-disposition",
        json={"disposition": "cleared", "note": "Spoke to the candidate."},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["under_integrity_review"] is False

    # The row re-evaluates itself and the stage control unlocks, for the
    # recruiter who could not use it a moment ago.
    _as(caller, world, Role.recruiter)
    unlocked = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/stage"
    ).json()
    assert unlocked["can_move"] is True
    assert unlocked["allowed_transitions"]
    reopened = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job)}
    ).json()
    row = next(r for r in reopened["rows"] if r["link_id"] == str(link_id))
    assert row["under_integrity_review"] is False
    # And the number is back, because it is no longer being withheld.
    assert row["ready_pick_score"] == 84


def test_a_disposition_is_a_decision_and_not_an_approval(
    caller: Caller, world: World
) -> None:
    """All four dispositions are accepted, including `rejected`.

    A gate that required APPROVAL could be satisfied by nagging. A gate that
    requires a recorded DECISION is satisfied only by somebody having looked.
    There is no `auto_cleared` and there must never be one.
    """
    _as(caller, world, Role.hr_manager)
    assert "auto_cleared" not in hiring_gates.DISPOSITIONS
    link_id = world.links["Reserved Assessed"]
    for disposition in sorted(hiring_gates.DISPOSITIONS):
        response = caller.http.post(
            f"{BASE}/jobs/{world.job}/candidates/{link_id}/integrity-disposition",
            json={"disposition": disposition},
        )
        assert response.status_code == 200, f"{disposition}: {response.text}"
    assert (
        caller.http.post(
            f"{BASE}/jobs/{world.job}/candidates/{link_id}/integrity-disposition",
            json={"disposition": "auto_cleared"},
        ).status_code
        == 422
    )


# ── Workflow 3: team calibration ─────────────────────────────────────────────


def test_workflow_three_team_calibration(caller: Caller, world: World) -> None:
    """Enter a verdict, review the evidence, and see the divergence surface.

    Step 4 of the specification's workflow is "a flag surfaces in the admin
    dashboard for the Standards Board to investigate". That is the assertion
    this journey exists for, and it is the one that cannot be made about a
    component.
    """
    link_id = world.links["Ready Assessed"]

    # 1 and 2. A recruiter sees a candidate the machine graded Matching and
    #    records a contrary verdict.
    _as(caller, world, Role.recruiter)
    page = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job)}
    ).json()
    row = next(r for r in page["rows"] if r["link_id"] == str(link_id))
    assert row["band_label"] == "Ready to Pick"

    written = caller.http.put(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
        json={
            "verdict": team_review.VERDICT_REJECT,
            "remarks": "Could not evidence the migration when asked directly.",
        },
    )
    assert written.status_code == 200, written.text
    panel = written.json()
    mine = next(
        entry
        for entry in panel["entries"]
        if entry["reviewer_user_id"] == str(world.users[Role.recruiter])
    )
    # RBAC 29: author and timestamp, always.
    assert mine["verdict"] == team_review.VERDICT_REJECT
    assert mine["editable"] is True
    assert mine["created_at"] and mine["updated_at"]

    # NO NUDGE. The response to a disagreement is exactly the response to an
    # agreement: the panel, and nothing else. No warning, no confirmation
    # prompt, no severity, no flag the UI could render as disapproval.
    assert set(panel) == {
        "link_id",
        "candidate_name",
        "system_id",
        "verdicts",
        "verdict_labels",
        "entries",
        "can_write",
    }

    # 3. The reviewer opens the evidence.
    profile = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/profile"
    ).json()
    assert profile["overall_rating"] == rating.grade_for_percent(78.0)

    # 4. The divergence reaches the Standards Board, and reaches nobody else.
    assert (
        caller.http.get(f"{BASE}/calibration/divergences").status_code == 403
    ), "a recruiter must never be shown their own override rate"

    _as(caller, world, Role.client)
    board = caller.http.get(f"{BASE}/calibration/divergences").json()
    entry = next(
        item for item in board["divergences"] if item["link_id"] == str(link_id)
    )
    assert entry["verdict"] == team_review.VERDICT_REJECT
    assert entry["predicted_grade"] == rating.grade_for_percent(78.0)
    assert entry["outcome_assessment"] == calibration.ASSESSMENT_TOO_HIGH
    assert entry["reviewer_user_id"] == str(world.users[Role.recruiter])
    # The remark itself is NOT here. It belongs to its author and is read on
    # the panel, with their name attached.
    assert "remarks" not in entry

    # The metric carries counts and a rate. No target, no threshold, no verdict.
    assert set(board["override_rate"]) == {"comparable", "diverged", "rate"}
    assert board["override_rate"]["diverged"] >= 1


def test_nobody_edits_another_reviewers_remark(caller: Caller, world: World) -> None:
    """RBAC §29, enforced by the write path having no way to name a reviewer.

    A second reviewer's PUT creates their OWN row; the first reviewer's row is
    untouched and reads as not editable by them.
    """
    link_id = world.links["Strong Assessed"]
    _as(caller, world, Role.recruiter)
    caller.http.put(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
        json={"verdict": team_review.VERDICT_PASS, "remarks": "Strong throughout."},
    )
    _as(caller, world, Role.interview_manager)
    panel = caller.http.put(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
        json={"verdict": team_review.VERDICT_HOLD, "remarks": "Wants a second look."},
    ).json()

    by_reviewer = {entry["reviewer_user_id"]: entry for entry in panel["entries"]}
    recruiter = by_reviewer[str(world.users[Role.recruiter])]
    interviewer = by_reviewer[str(world.users[Role.interview_manager])]
    assert recruiter["verdict"] == team_review.VERDICT_PASS
    assert recruiter["remarks"] == "Strong throughout."
    assert recruiter["editable"] is False
    assert interviewer["editable"] is True


def test_a_reviewer_who_changes_their_mind_leaves_one_divergence_not_three(
    caller: Caller, world: World
) -> None:
    """The Standards Board's queue counts opinions, not keystrokes.

    Keyed on the review rather than appended, so a reviewer refining a verdict
    updates their divergence, and a reviewer coming back into agreement
    WITHDRAWS it: leaving the row would assert a disagreement they no longer
    hold.
    """
    link_id = world.links["Reserved Assessed"]
    _as(caller, world, Role.recruiter)
    for verdict in (
        team_review.VERDICT_REJECT,
        team_review.VERDICT_PASS,
        team_review.VERDICT_REJECT,
    ):
        caller.http.put(
            f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
            json={"verdict": verdict, "remarks": f"Verdict {verdict}."},
        )

    _as(caller, world, Role.hr_manager)
    board = caller.http.get(f"{BASE}/calibration/divergences").json()
    for_link = [d for d in board["divergences"] if d["link_id"] == str(link_id)]
    assert len(for_link) == 1

    # And coming back into agreement withdraws it entirely. The machine graded
    # this candidate Moderately Matching, which `hold` agrees with.
    _as(caller, world, Role.recruiter)
    caller.http.put(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
        json={"verdict": team_review.VERDICT_HOLD, "remarks": "On reflection, hold."},
    )
    _as(caller, world, Role.hr_manager)
    board = caller.http.get(f"{BASE}/calibration/divergences").json()
    assert not [d for d in board["divergences"] if d["link_id"] == str(link_id)]


def test_reading_the_calibration_internals_writes_an_audit_row(
    caller: Caller, world: World
) -> None:
    """spec-doc6 D8: raw numbers are "always logged when viewed".

    Asserted on the audit table rather than on a mock, and the Super Admin's
    read is marked EXCEPTIONAL because RBAC §7.5 makes their reach into another
    role's surface an override that has to be recorded as one.
    """
    link_id = world.links["Strong Assessed"]
    _as(caller, world, Role.client)
    view = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/calibration"
    )
    assert view.status_code == 200, view.text
    body = view.json()
    # The numbers D8 keeps off every other surface.
    assert body["adjusted_composite"] == 88.0
    assert body["raw_composite"] == 90.0
    assert any(d["raw_score"] is not None for d in body["dimensions"])

    rows = asyncio.run(_audit_rows(world, calibration.CALIBRATION_INTERNALS_VIEWED))
    assert rows, "a raw-numbers read left no audit row"
    assert rows[-1]["actor_role"] == Role.client.value
    assert rows[-1]["exceptional"] is True


async def _audit_rows(state: World, action: str) -> list[dict]:
    eng = engine()
    try:
        async with eng.connect() as conn:
            await conn.execute(sa.text("SET app.bypass_rls = 'on'"))
            result = await conn.execute(
                sa.text(
                    "SELECT actor_role, exceptional, at FROM audit_log "
                    "WHERE tenant_id = :tenant AND action = :action "
                    "ORDER BY at"
                ),
                {"tenant": state.tenant, "action": action},
            )
            return [dict(row) for row in result.mappings().all()]
    finally:
        await eng.dispose()


def test_a_divergence_reaches_the_super_admin_activity_view(
    caller: Caller, world: World
) -> None:
    """spec-doc6 §8.2 asks for the divergence to "surface in the Super Admin
    activity view".

    It does so by being an ordinary audit row, so it appears wherever company
    activity is read without a second reader having to know about calibration.
    """
    link_id = world.links["Ready Assessed"]
    _as(caller, world, Role.recruiter)
    caller.http.put(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
        json={"verdict": team_review.VERDICT_REJECT, "remarks": "Still not convinced."},
    )
    rows = asyncio.run(
        _audit_rows(world, calibration.CALIBRATION_DIVERGENCE_RAISED)
    )
    assert rows
