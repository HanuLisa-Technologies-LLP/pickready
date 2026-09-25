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

THE TWO GRADE COLUMNS ARE SEEDED WITH REAL NUMBERS BEHIND THEM
---------------------------------------------------------------
Every row carries a stored Yukti score, and the assessed rows a report with an
`overall_score`, so the words the browser receives are chosen from real
numbers by the real SQL. That is what lets `test_the_page_json_carries_no_number`
mean something: a JSON walk over a page with nothing numeric behind it would
pass for a serializer that leaked every score it had.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Iterator

import re

import pytest
import sqlalchemy as sa

from app.models.enums import Role
from app.services import calibration, dashboard, rating, team_review
from app.services.hiring import gates as hiring_gates
from app.services.yukti import config as yukti_config
from app.services.yukti import ranking as yukti_ranking
from tests.dashboard_world import (  # noqa: F401 - fixtures used by name
    BASE,
    SKIP_REASON,
    Caller,
    caller,
    engine,
    no_permission_cache,
    schema_is_current,
)

#: The tenant weight the migration seeds for every tenant: the assessment
#: counts for 70 of the blended rank. Read by `yukti.ranking` from the row.
WEIGHT_PCT = 70

SCORED = yukti_config.STATUS_SCORED
PENDING = yukti_config.STATUS_PENDING
NOT_ASSESSED = yukti_config.STATUS_NOT_ASSESSED


class Seeded:
    """One candidate's inputs, named for what it demonstrates."""

    def __init__(
        self,
        label,
        *,
        status=SCORED,
        pre=None,
        reason=None,
        evaluation=None,
        report=None,
        must_have_failed=False,
        flagged=False,
    ):
        self.label = label
        self.status = status
        self.pre = pre
        self.reason = reason
        #: Miti's adjusted composite on the EVALUATION (profile, note, dot).
        self.evaluation = evaluation
        #: The delivered report's `overall_score`, which the rank blends.
        self.report = report
        self.must_have_failed = must_have_failed
        self.flagged = flagged

    def expected_rank(self):
        return yukti_ranking.rank_score(
            self.pre, self.status, self.report, self.must_have_failed, WEIGHT_PCT
        )


#: One job, eight candidates, deliberately spread across every state the two
#: grade columns have to distinguish. Never a person's name.
CANDIDATES = (
    Seeded("Strong Assessed", pre=90.0, evaluation=88.0, report=95),
    Seeded("Ready Assessed", pre=70.0, evaluation=78.0, report=80),
    Seeded("Reserved Assessed", pre=60.0, evaluation=65.0, report=65),
    Seeded("Flagged Assessed", pre=88.0, evaluation=84.0, report=84, flagged=True),
    Seeded(
        "Capped Assessed", pre=100.0, evaluation=90.0, report=90, must_have_failed=True
    ),
    Seeded("Resume Only Applicant", pre=76.0),
    Seeded("Unread Applicant", status=PENDING),
    Seeded(
        "Failed Read Applicant",
        status=NOT_ASSESSED,
        reason=yukti_config.FAILURE_MODEL_UNAVAILABLE,
    ),
)
BY_LABEL = {seeded.label: seeded for seeded in CANDIDATES}

#: Keys a page may carry a number under: counts and pagination, nothing else.
COUNT_KEYS = frozenset({"total", "page", "page_size", "team_review_count"})


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

            for seeded in CANDIDATES:
                label = seeded.label
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
                        "yukti_status, yukti_pre_score, yukti_failure_reason) "
                        "VALUES (:id, :tenant, :job, :cand, 'fresh', 'applied', "
                        "'applied', :status, :pre, :reason)"
                    ),
                    {
                        "id": link_id,
                        "tenant": state.tenant,
                        "job": state.job,
                        "cand": candidate_id,
                        "status": seeded.status,
                        "pre": seeded.pre,
                        "reason": seeded.reason,
                    },
                )
                if seeded.report is not None:
                    await conn.execute(
                        sa.text(
                            "INSERT INTO functional_skills_reports (id, tenant_id, "
                            "job_id, job_candidate_link_id, grade, status, "
                            "overall_summary, overall_score, must_have_failed, "
                            "validation_json, suggested_probes_json, synthesized_at) "
                            "VALUES (:id, :tenant, :job, :link, 'non_managerial', "
                            "'ready', 'Synthetic report.', :overall, :failed, "
                            "'{}'::jsonb, '[]'::jsonb, now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "tenant": state.tenant,
                            "job": state.job,
                            "link": link_id,
                            "overall": seeded.report,
                            "failed": seeded.must_have_failed,
                        },
                    )
                score = seeded.evaluation
                flagged = seeded.flagged
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
    """Land, sort by Vivekium Grade, skim the top, move one to Interview.

    The specification's own five steps, in order. What each assertion is
    defending:

      * the sort runs in SQL on THE rank (the same expression the ranked table
        sorts by) and puts every gradeless row LAST, which an ascending or a
        NULLS FIRST order would not;
      * the flagged candidate does not present a grade to be triaged on;
      * the move is made through the server's own transition list, so the UI
        never has to know the FSM.
    """
    _as(caller, world, Role.recruiter)

    # 1 and 2. Land on the dashboard, sort descending by grade.
    page = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job), "sort": "grade"}
    ).json()
    assert page["page_size"] == 25
    labels = [_label(world, row) for row in page["rows"]]

    # Descending by the rank, every gradeless row last. The flagged candidate
    # HAS a rank and still shows no grade: Under Review withholds it, which is
    # why it sits with the unread and failed rows at the end.
    graded = sorted(
        (seeded for seeded in CANDIDATES
         if seeded.expected_rank() is not None and not seeded.flagged),
        key=lambda seeded: seeded.expected_rank(),
        reverse=True,
    )
    assert labels[: len(graded)] == [seeded.label for seeded in graded]
    assert set(labels[len(graded):]) == {
        "Flagged Assessed", "Unread Applicant", "Failed Read Applicant"
    }
    assert labels[0] == "Strong Assessed"

    # 3. Skim the "why" on the top candidate.
    top = page["rows"][0]
    assert top["ranking_label"] == rating.GRADE_HIGHLY
    profile = caller.http.get(
        f"{BASE}/jobs/{world.job}/candidates/{top['link_id']}/profile"
    ).json()
    assert profile["artifact"] == "ready_pick_profile"
    assert profile["why_this_candidate"].startswith("Owns a comparable")

    # 4. Use AI Match as a secondary filter. Filtered in SQL on the resume-only
    #    reading, so the total is the whole match rather than this page's part.
    filtered = caller.http.get(
        f"{BASE}/candidates",
        params={"job_id": str(world.job), "ai_match": rating.GRADE_MATCHING},
    ).json()
    expected = {
        seeded.label for seeded in CANDIDATES
        if seeded.status == SCORED
        and rating.grade_for_percent(seeded.pre) == rating.GRADE_MATCHING
    }
    assert filtered["total"] == len(expected) == 2
    assert {_label(world, row) for row in filtered["rows"]} == expected
    assert {row["ai_match_label"] for row in filtered["rows"]} == {
        rating.GRADE_MATCHING
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


def _label(world: World, row: dict) -> str:
    return next(label for label, link in world.links.items() if str(link) == row["link_id"])


def test_column_4_is_the_word_for_the_ranked_tables_rank(
    caller: Caller, world: World
) -> None:
    """The dashboard and the job page order one job's candidates ONE way.

    Every row's Vivekium Grade is the word for `yukti.ranking.rank_score` over
    that row's seeded inputs: the resume check blended 30/70 with the Tatva
    Assessment, a failed Must-have capping it at Moderately Matching, and the
    resume check alone where there is no graded report. A dashboard that read
    anything else (the evaluation's composite, the old score) fails here.
    """
    _as(caller, world, Role.hr_manager)
    page = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job)}
    ).json()
    rows = {_label(world, row): row for row in page["rows"]}
    for seeded in CANDIDATES:
        row = rows[seeded.label]
        if seeded.flagged:
            continue
        expected = yukti_ranking.grade_word(seeded.expected_rank())
        if expected is None:
            assert row["ranking_label"] not in rating.GRADES, seeded.label
        else:
            assert row["ranking_label"] == expected, seeded.label
            assert row["ranking_state"] == dashboard.GRADE_STATES[expected]

    capped = rows["Capped Assessed"]
    assert capped["ranking_label"] == rating.GRADE_MODERATELY
    assert dashboard.MUST_HAVE_CAPPED_NOTE in capped["ranking_note"]
    # The resume said Highly Matching; column 3 keeps saying so, because the
    # cap is the ASSESSMENT's finding and column 3 is the resume's.
    assert capped["ai_match_label"] == rating.GRADE_HIGHLY

    resume_only = rows["Resume Only Applicant"]
    assert resume_only["ranking_label"] == rating.GRADE_MATCHING
    assert resume_only["ranking_note"].startswith(dashboard.BASIS_RESUME_ONLY)
    assert resume_only["confidence_label"] == dashboard.CONFIDENCE_NOT_ASSESSED_LABEL

    unread = rows["Unread Applicant"]
    assert unread["ai_match_label"] == unread["ranking_label"] == "Not checked yet"

    failed = rows["Failed Read Applicant"]
    assert failed["ai_match_label"] == "Not assessed"
    assert failed["ai_match_note"] == dashboard.NOT_ASSESSED_NOTES[
        yukti_config.FAILURE_MODEL_UNAVAILABLE
    ]


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _walk(inner, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk(inner, f"{path}[{index}]")
    else:
        yield path, value


_NUMERIC_TEXT = re.compile(r"^\s*-?\d+(\.\d+)?\s*%?\s*$")


def test_the_page_json_carries_no_number(caller: Caller, world: World) -> None:
    """D3, on the bytes a browser receives, over rows with real scores behind
    them: no numeric leaf outside the counts, no digit-only string, and no key
    that reads as a score, a percent, a band or the old letter grade.

    Walked on the RESPONSE rather than on the schema, because a schema that
    declares a word can still be handed a stringified number.
    """
    _as(caller, world, Role.client)
    page = caller.http.get(
        f"{BASE}/candidates", params={"job_id": str(world.job)}
    ).json()
    assert len(page["rows"]) == len(CANDIDATES)
    for path, value in _walk(page):
        leaf_key = path.rsplit(".", 1)[-1].split("[", 1)[0]
        for part in ("score", "percent", "band", "pre_screen"):
            assert part not in leaf_key, path
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            assert leaf_key in COUNT_KEYS, f"{path} = {value!r}"
        if isinstance(value, str):
            assert not _NUMERIC_TEXT.match(value), f"{path} = {value!r}"


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
    while reporting a total for the wider one. The retired letter grade is an
    unknown value now like any other."""
    _as(caller, world, Role.hr_manager)
    for params in (
        {"source_type": "walk_in"},
        {"ai_match": "A"},
        {"ai_match": "Ready to Pick"},
        {"sort": "score"},
    ):
        assert (
            caller.http.get(f"{BASE}/candidates", params=params).status_code == 422
        ), params


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
    assert flagged["ranking_label"] == "Under Review"
    assert flagged["ranking_label"] not in rating.GRADES
    assert (
        flagged["ranking_screen_reader_label"]
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
    # And the grade is back, because it is no longer being withheld.
    assert row["ranking_label"] == yukti_ranking.grade_word(
        BY_LABEL["Flagged Assessed"].expected_rank()
    )


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
    assert row["ranking_label"] == rating.GRADE_MATCHING

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

    # 4. The divergence is RECORDED for the Standards Board, as audit data.
    #    The queue route that listed it was deleted with the override-rate
    #    metric (PLAN-p7 WP-B6): it had no screen. The record is what survives.
    records = asyncio.run(_divergence_records(world, link_id))
    assert len(records) == 1
    entry = records[0]
    assert entry["verdict"] == team_review.VERDICT_REJECT
    assert entry["predicted_grade"] == rating.grade_for_percent(78.0)
    assert entry["outcome_assessment"] == calibration.ASSESSMENT_TOO_HIGH
    assert str(entry["recorded_by"]) == str(world.users[Role.recruiter])
    assert (
        caller.http.get(f"{BASE}/calibration/divergences").status_code == 404
    ), "the deleted divergence queue answered"


async def _divergence_records(state: World, link_id) -> list[dict]:
    """The calibration records a link's Team Reviews raised, read back from a
    second connection."""
    eng = engine()
    try:
        async with eng.connect() as conn:
            await conn.execute(sa.text("SET app.bypass_rls = 'on'"))
            result = await conn.execute(
                sa.text(
                    "SELECT cr.predicted_grade, cr.outcome_assessment, "
                    "       cr.recorded_by, tr.rating AS verdict "
                    "FROM calibration_records cr "
                    "JOIN candidate_team_reviews tr ON tr.id = cr.team_review_id "
                    "WHERE cr.tenant_id = :tenant AND cr.source = :source "
                    "  AND tr.job_candidate_link_id = :link"
                ),
                {
                    "tenant": state.tenant,
                    "source": calibration.SOURCE_TEAM_REVIEW_DIVERGENCE,
                    "link": link_id,
                },
            )
            return [dict(row) for row in result.mappings().all()]
    finally:
        await eng.dispose()


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

    assert len(asyncio.run(_divergence_records(world, link_id))) == 1

    # And coming back into agreement withdraws it entirely. The machine graded
    # this candidate Moderately Matching, which `hold` agrees with.
    _as(caller, world, Role.recruiter)
    caller.http.put(
        f"{BASE}/jobs/{world.job}/candidates/{link_id}/team-review",
        json={"verdict": team_review.VERDICT_HOLD, "remarks": "On reflection, hold."},
    )
    assert asyncio.run(_divergence_records(world, link_id)) == []


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
