"""Migration 0122_yukti (PLAN-p2 section 3.10, test 14).

The whole round trip runs INSIDE one transaction that is rolled back: the
revision is downgraded, a pre-release world is seeded under the old schema,
the revision is upgraded, and every step is asserted against the rows. DDL is
transactional in Postgres, so the shared test database is left exactly as it
was (the `test_skills_contract_migration.py` precedent).

What is pinned, and why each one matters on a live database:

* every tenant reads a weight of 70 the moment the column exists, and 101 is
  refused by the CHECK;
* a report whose Must-have dimension scored below the Not Matching boundary is
  `must_have_failed`, and the boundary literal matches `services/rating`;
* a scored legacy link keeps its number as a `legacy` Yukti reading;
* THE STATUS CORRECTION touches only what the matcher or a recruiter created
  and nobody applied through. A real applicant from before 2026-07-30, whose
  `validation_json` is NULL because the fields did not exist yet, is NOT
  touched; nor is a databank link somebody was already invited from;
* each corrected link gets exactly one `pipeline_status` row, with no actor.

Mutation check recorded in the Phase 2 WP-B report: replacing the provenance
clause (`source = 'databank' OR (source_type = 'sourced' AND ...)`) with
`true` corrects the real applicant and fails
`test_the_upgrade_backfills_and_corrects_only_what_it_should`.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import uuid

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.services import hiring_pipeline, rating

MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0122_yukti.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0122", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

LINK_COLUMNS = {
    "yukti_pre_score",
    "yukti_status",
    "yukti_failure_reason",
    "evidence_tags_json",
    "yukti_provenance_json",
    "yukti_scored_at",
    "yukti_profile_id",
}


def test_the_boundary_literal_is_ratings_not_matching_boundary() -> None:
    below = migration.NOT_MATCHING_BELOW
    assert rating.grade_for_percent(below - 0.01) == rating.GRADE_NOT
    assert rating.grade_for_percent(below) != rating.GRADE_NOT


def test_the_stage_label_literal_is_the_pipelines() -> None:
    assert migration.SOURCED_STAGE_LABEL == hiring_pipeline.STAGE_LABELS[hiring_pipeline.SOURCED]


def test_the_revision_chains_where_the_stage_two_rule_says() -> None:
    assert migration.revision == "0122_yukti"
    assert migration.down_revision == "0121_candidate_comms"


def _seed(connection) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in (
        "tenant", "job", "legacy", "matcher", "upload", "applicant", "invited",
        "failed_report", "passed_report",
    )}
    run = connection.execute
    run(
        text("INSERT INTO tenants (id, name, domain, spf_dkim_status) VALUES (:t, :n, :d, 'pending')"),
        {"t": ids["tenant"], "n": f"m0122-{ids['tenant']}", "d": f"{ids['tenant']}.m0122.test"},
    )
    run(
        text(
            "INSERT INTO jobs (id, tenant_id, title, status, assessment_grade) "
            "VALUES (:j, :t, 'Analyst', 'ratified', 'managerial')"
        ),
        {"j": ids["job"], "t": ids["tenant"]},
    )
    links = {
        # key: (source, source_type, application_source, match_score, validation)
        "legacy": ("fresh", "applied", "direct", 88.4, {"notice_period": "Immediate"}),
        "matcher": ("databank", "databank", "direct", None, None),
        "upload": ("fresh", "sourced", "direct", None, None),
        "applicant": ("fresh", "applied", "direct", None, None),
        "invited": ("databank", "databank", "direct", None, None),
    }
    for key, (source, source_type, app_source, score, validation) in links.items():
        candidate, profile = uuid.uuid4(), uuid.uuid4()
        ids[f"{key}_profile"] = profile
        run(
            text("INSERT INTO candidates (id, tenant_id, full_name, email) VALUES (:c, :t, :n, :e)"),
            {"c": candidate, "t": ids["tenant"], "n": key, "e": f"{candidate}@m0122.test"},
        )
        run(
            text("INSERT INTO profiles (id, candidate_id, resume_text) VALUES (:p, :c, 'cv')"),
            {"p": profile, "c": candidate},
        )
        run(
            text(
                "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, profile_id, "
                "source, source_type, application_source, status, match_score, validation_json) "
                "VALUES (:l, :t, :j, :c, :p, :s, :st, :a, 'applied', :m, CAST(:v AS jsonb))"
            ),
            {"l": ids[key], "t": ids["tenant"], "j": ids["job"], "c": candidate, "p": profile,
             "s": source, "st": source_type, "a": app_source, "m": score,
             "v": json.dumps(validation) if validation else None},
        )
    run(
        text(
            "INSERT INTO assessment_conversations (id, tenant_id, job_id, job_candidate_link_id, grade) "
            "VALUES (:i, :t, :j, :l, 'managerial')"
        ),
        {"i": uuid.uuid4(), "t": ids["tenant"], "j": ids["job"], "l": ids["invited"]},
    )
    for key, link, score in (("failed_report", "legacy", 55), ("passed_report", "invited", 60)):
        run(
            text(
                "INSERT INTO functional_skills_reports (id, tenant_id, job_id, "
                "job_candidate_link_id, grade, overall_summary, synthesized_at) "
                "VALUES (:r, :t, :j, :l, 'managerial', 'summary', now())"
            ),
            {"r": ids[key], "t": ids["tenant"], "j": ids["job"], "l": ids[link]},
        )
        run(
            text(
                "INSERT INTO report_dimensions (id, tenant_id, report_id, category, name, score, "
                "remark, ordinal) VALUES (:i, :t, :r, 'must_have', 'Kafka', :s, 'remark', 1)"
            ),
            {"i": uuid.uuid4(), "t": ids["tenant"], "r": ids[key], "s": score},
        )
    return ids


#: The report tables 0130 makes insert-only by trigger. This test replays 0122
#: on a HEAD schema, so it first returns them to the world 0122 actually runs
#: in, where its `must_have_failed` backfill is an ordinary UPDATE. The
#: enclosing transaction is rolled back, which restores both triggers.
IMMUTABLE_AFTER_0130 = ("functional_skills_reports", "report_dimensions")


def _round_trip(connection) -> None:
    for table in IMMUTABLE_AFTER_0130:
        connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_immutable"))
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()
        assert not LINK_COLUMNS & {
            c["name"] for c in sa_inspect(connection).get_columns("job_candidate_links")
        }
        ids = _seed(connection)
        migration.upgrade()
    run = connection.execute

    assert LINK_COLUMNS <= {
        c["name"] for c in sa_inspect(connection).get_columns("job_candidate_links")
    }
    assert run(
        text("SELECT yukti_assessment_weight_pct FROM tenants WHERE id = :t"), {"t": ids["tenant"]}
    ).scalar_one() == 70
    assert run(
        text("SELECT count(*) FROM tenants WHERE yukti_assessment_weight_pct <> 70")
    ).scalar_one() == 0, "the server default seeds every tenant"

    failed = dict(
        run(text("SELECT id, must_have_failed FROM functional_skills_reports WHERE id IN (:a, :b)"),
            {"a": ids["failed_report"], "b": ids["passed_report"]}).all()
    )
    assert failed == {ids["failed_report"]: True, ids["passed_report"]: False}

    rows = {
        row.id: row
        for row in run(
            text(
                "SELECT id, status, current_stage, yukti_status, yukti_pre_score, "
                "yukti_profile_id, evidence_tags_json FROM job_candidate_links WHERE job_id = :j"
            ),
            {"j": ids["job"]},
        )
    }
    legacy = rows[ids["legacy"]]
    assert (legacy.yukti_status, legacy.yukti_pre_score) == ("legacy", pytest.approx(88.4))
    assert legacy.yukti_profile_id == ids["legacy_profile"]
    assert legacy.evidence_tags_json == []
    assert legacy.status == "applied", "a real applicant is never corrected"

    for key in ("matcher", "upload"):
        assert rows[ids[key]].status == "sourced", key
        assert rows[ids[key]].current_stage == migration.SOURCED_STAGE_LABEL
        history = run(
            text("SELECT status, set_by, remarks FROM pipeline_status WHERE job_candidate_link_id = :l"),
            {"l": ids[key]},
        ).all()
        assert [(h.status, h.set_by, h.remarks) for h in history] == [
            ("sourced", None, migration.CORRECTION_REMARK)
        ], key
    for key in ("applicant", "invited"):
        assert rows[ids[key]].status == "applied", key
        assert rows[ids[key]].yukti_status == "pending", key

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            run(
                text("UPDATE tenants SET yukti_assessment_weight_pct = 101 WHERE id = :t"),
                {"t": ids["tenant"]},
            )
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            run(
                text("UPDATE job_candidate_links SET yukti_status = 'scored' WHERE id = :l"),
                {"l": ids["applicant"]},
            )


async def test_the_upgrade_backfills_and_corrects_only_what_it_should() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
                await connection.run_sync(_round_trip)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
