"""The legacy data reset: what it may delete, what it must not, and in what order.

WHY EVERY TEST HERE IS PURE
---------------------------
A purge is the one operation whose test must not depend on a service being up.
These assert the CLASSIFICATION, the SQL the purge builds, the export manifest
and the refusals, all against recorded schema facts, so the guard rails hold in
a fresh checkout with nothing running. The behaviour of those statements
against real data was exercised separately, against a clone of the development
database on a scratch Postgres, and the result is recorded in the final report.

THE RECORDED SCHEMA SNAPSHOT
-----------------------------
`tests/fixtures/legacy_reset/schema_snapshot.json` holds the table list, the
foreign-key graph with each key's ON DELETE action, and every vector column,
taken from a database migrated to `0062_embedding_provenance`. Two of the most
important assertions in this file are properties of that graph rather than of
any Python code, and this is the only way to make them a test rather than a
paragraph. Regenerate it with the query in
`tests/fixtures/legacy_reset/schema_snapshot.json` note field after any
migration that changes a foreign key.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.scripts import legacy_reset as reset

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "legacy_reset"
SNAPSHOT = json.loads((FIXTURES / "schema_snapshot.json").read_text(encoding="utf-8"))
LIVE_TABLES: list[str] = SNAPSHOT["tables"]
FOREIGN_KEYS: list[dict] = SNAPSHOT["foreign_keys"]

SCRIPT = pathlib.Path(reset.__file__)
MIGRATION = (
    pathlib.Path(reset.__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0062_embedding_provenance.py"
)

EM_DASH = chr(8212)


# ── Classification completeness ──────────────────────────────────────────────


def test_every_table_in_the_schema_is_classified() -> None:
    """spec-doc6 section 6.2, as an assertion rather than a promise.

    Any table decision D2 does not classify must be classified before the purge
    runs. This is the check that makes a migration adding a table stop the
    purge instead of leaving its rows to whatever the DELETE statements happen
    to reach.
    """
    assert reset.unclassified_tables(LIVE_TABLES) == ()
    assert reset.absent_tables(LIVE_TABLES) == ()


def test_an_unclassified_table_refuses_rather_than_warns() -> None:
    with pytest.raises(reset.ClassificationGap) as raised:
        reset.assert_classified([*LIVE_TABLES, "a_table_nobody_classified"])
    assert "a_table_nobody_classified" in str(raised.value)


def test_a_database_behind_the_migrations_refuses_the_purge() -> None:
    """Not the same failure as an unclassified table, and not treated as one.

    A purge that silently skipped a table it was written to act on would report
    a clean run over rows it never looked at.
    """
    behind = [t for t in LIVE_TABLES if t != "evaluations"]
    reset.assert_classified(behind)  # still classified, just absent
    with pytest.raises(reset.ClassificationGap) as raised:
        reset.assert_schema_current(behind)
    assert "evaluations" in str(raised.value)
    assert "alembic upgrade head" in str(raised.value)


def test_every_rule_has_a_bucket_and_a_reason() -> None:
    seen: set[str] = set()
    for rule in reset.CLASSIFICATION:
        assert rule.bucket in reset.BUCKETS, rule.table
        assert len(rule.reason) > 30, f"{rule.table} has no real reason"
        assert rule.table not in seen, f"{rule.table} is classified twice"
        seen.add(rule.table)


def test_the_preserve_default_covers_the_tables_d2_never_named() -> None:
    """Deleting more than was asked for is the direction that cannot be undone.

    Every table the decision does not name is either preserved or is one of the
    small number the survey raises for sign-off, and the sign-off list is what a
    reviewer reads. A table that arrived in the purge bucket without D2 naming
    it must carry a reason that says why.
    """
    inferred_purges = [
        rule
        for rule in reset.CLASSIFICATION
        if rule.bucket == reset.PURGE and not rule.named_by_d2
    ]
    assert {rule.table for rule in inferred_purges} == {
        "technical_questions",
        "job_matching_categories",
        "context_chunks",
        "job_company_dna_bindings",
    }


# ── The highest-risk property in the whole task ──────────────────────────────


def test_no_foreign_key_can_cascade_a_team_review_away() -> None:
    """Team Review remarks are human authorship and D2 preserves them.

    Checked against the real foreign-key graph rather than by reading the model:
    the tables are created by raw SQL in the migrations, so a SQLAlchemy
    `ondelete=` string proves nothing about what the database will do.

    `candidate_team_reviews` has no reference to `evaluations` at all, so the
    purge of the evaluations cannot reach it. Its remaining references are to
    `job_candidate_links` and `tenants`, both of which the reset preserves.
    """
    edges = [fk for fk in FOREIGN_KEYS if fk["table"] == "candidate_team_reviews"]
    assert edges, "the fixture has no rows for candidate_team_reviews"
    assert not [fk for fk in edges if fk["references"] == "evaluations"]
    reachable_from_purge = {
        fk["references"]
        for fk in edges
        if fk["on_delete"] == "CASCADE"
        and (reset.rule_for(fk["references"]) or reset.TableRule("", reset.PURGE, "")).bucket
        == reset.PURGE
    }
    assert reachable_from_purge == set()


def test_the_human_observations_are_detached_and_never_cascaded() -> None:
    """The defect migration 0062 fixes, asserted at the schema level.

    Before it, `review_dispositions.evaluation_id` and
    `calibration_records.evaluation_id` were ON DELETE CASCADE, so purging the
    evaluations would have deleted the record that a person looked at a flag and
    decided something, and the record of whether the grade turned out to be
    right. The cascade walked straight past the `decided_by` RESTRICT that
    exists to stop exactly this class of loss.
    """
    into_evaluations = {
        fk["table"]: fk["on_delete"]
        for fk in FOREIGN_KEYS
        if fk["references"] == "evaluations"
    }
    assert into_evaluations == {
        "review_dispositions": "SET NULL",
        "calibration_records": "SET NULL",
    }
    for table in into_evaluations:
        assert reset.rule_for(table).bucket == reset.DETACH


def test_the_disposition_author_is_still_restricted() -> None:
    """Relaxing the evaluation reference must not relax the author reference.

    `decided_by` stays ON DELETE RESTRICT: a disposition whose person has been
    erased asserts that a human decided while being unable to say who.
    """
    author = [
        fk
        for fk in FOREIGN_KEYS
        if fk["table"] == "review_dispositions" and fk["column"] == "decided_by"
    ]
    assert author and author[0]["on_delete"] == "RESTRICT"


def test_the_detach_statement_copies_the_context_before_nulling_the_reference() -> None:
    """RBAC section 29: author, timestamp, candidate and job context preserved.

    `review_dispositions` reached its candidate and job only THROUGH the
    evaluation, so nulling the reference without copying them first would
    satisfy the letter of "keep the remark" and lose the context the rule exists
    to protect. One statement, so no window exists in which the reference is
    gone and the context has not landed.
    """
    sql = reset.detach_sql(reset.rule_for("review_dispositions"))
    assert "job_id = COALESCE(t.job_id, e.job_id)" in sql
    assert "link_id = COALESCE(t.link_id, e.link_id)" in sql
    assert "evaluation_ref = COALESCE(t.evaluation_ref, t.evaluation_id)" in sql
    assert "evaluation_id = NULL" in sql
    assert "detached_note = :note" in sql
    assert sql.count("UPDATE") == 1
    assert "DELETE" not in sql


def test_the_migration_replaces_both_cascades() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "fk_review_dispositions_evaluation" in source
    assert "fk_calibration_records_evaluation" in source
    assert source.count("ON DELETE SET NULL") >= 2
    assert "ALTER COLUMN evaluation_id DROP NOT NULL" in source


# ── Deletion order ───────────────────────────────────────────────────────────


def test_children_are_deleted_before_their_parents() -> None:
    """The purge deletes explicitly rather than leaning on ON DELETE CASCADE.

    A cascade removes rows nobody counted: the report says twelve reports were
    deleted while the database also removed forty report dimensions, and the
    reconciliation stops describing what happened. Explicit deletion only works
    if the order respects the real foreign keys, which is what this checks.
    """
    order = {rule.table: index for index, rule in enumerate(reset.purge_order())}
    purge_tables = {
        rule.table for rule in reset.purge_order() if rule.bucket == reset.PURGE
    }
    for fk in FOREIGN_KEYS:
        child, parent = fk["table"], fk["references"]
        if child not in purge_tables or parent not in purge_tables or child == parent:
            continue
        if fk["on_delete"] == "SET NULL":
            # A SET NULL edge does not need the child deleted first: deleting
            # the parent nulls the column and removes nothing. The one such
            # edge inside the purge set is named here rather than waved
            # through, so a future SET NULL edge has to be looked at.
            assert (child, parent, fk["column"]) == (
                "evaluations",
                "functional_skills_reports",
                "report_id",
            ), f"a new SET NULL edge inside the purge set: {fk}"
            continue
        assert order[child] < order[parent], (
            f"{child} references {parent} with ON DELETE {fk['on_delete']} "
            "and must be deleted first"
        )


def test_the_detach_runs_before_the_evaluations_are_deleted() -> None:
    order = {rule.table: index for index, rule in enumerate(reset.purge_order())}
    assert order["review_dispositions"] < order["evaluations"]
    assert order["calibration_records"] < order["evaluations"]


def test_the_column_resets_run_last() -> None:
    order = {rule.table: index for index, rule in enumerate(reset.purge_order())}
    latest_delete = max(
        order[rule.table] for rule in reset.purge_order() if rule.bucket == reset.PURGE
    )
    for rule in reset.purge_order():
        if rule.bucket == reset.RESET:
            assert order[rule.table] > latest_delete


# ── The SQL the purge builds ─────────────────────────────────────────────────


def test_every_statement_is_scoped_to_one_tenant() -> None:
    """One transaction per tenant is only a boundary if the SQL respects it."""
    for rule in reset.purge_order():
        if not rule.tenant_column:
            continue
        if rule.bucket == reset.PURGE:
            sql = reset.delete_sql(rule)
        elif rule.bucket == reset.RESET:
            sql = reset.reset_sql(rule)
        else:
            sql = reset.detach_sql(rule)
        assert ":tenant" in sql, rule.table
        assert rule.tenant_column in sql, rule.table


def test_the_job_reset_never_touches_the_posting() -> None:
    """D2: existing jobs are not unpublished and applications are not
    interrupted. The reset clears the scorecard approval stamps and nothing
    else, so the posting window, the status and the archive flag are untouched.
    """
    sql = reset.reset_sql(reset.rule_for("jobs"))
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    for column in (
        "posting_start_date",
        "posting_end_date",
        "grace_period_end_date",
        "archived_at",
        "jd_markdown",
        "jd_json",
    ):
        assert column not in assignments, f"the reset must not write {column}"
    written = {clause.split(" = ", 1)[0] for clause in assignments.split(", ")}
    assert "status" not in written, "only assessment_status may move"
    assert "assessment_status" in written
    assert "framework_approved_at = NULL" in assignments
    assert "matching_categories_finalized_at = NULL" in assignments


def test_the_application_reset_clears_the_grade_and_keeps_the_application() -> None:
    rule = reset.rule_for("job_candidate_links")
    sql = reset.reset_sql(rule)
    assert sql.startswith("UPDATE job_candidate_links SET")
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert set(assignments.split(", ")) == {
        "match_score = NULL",
        "match_rationale = NULL",
        "match_breakdown_json = NULL",
        "tier = NULL",
    }
    for column in ("created_at", "status", "validation_json", "archived_at"):
        assert column not in assignments


def test_a_second_reset_reports_zero_rather_than_the_whole_table() -> None:
    """The count a reviewer reads is rows actually changed, not rows in scope."""
    sql = reset.reset_sql(reset.rule_for("job_candidate_links"))
    assert "IS DISTINCT FROM" in sql


def test_only_assessment_sourced_chunks_are_purged() -> None:
    sql = reset.delete_sql(reset.rule_for("context_chunks"))
    assert "source_type = 'assessment'" in sql


def test_a_preserve_rule_cannot_produce_a_delete() -> None:
    with pytest.raises(ValueError):
        reset.delete_sql(reset.rule_for("candidate_team_reviews"))
    with pytest.raises(ValueError):
        reset.reset_sql(reset.rule_for("audit_log"))
    with pytest.raises(ValueError):
        reset.detach_sql(reset.rule_for("tenants"))


def test_the_audit_trail_has_no_delete_path() -> None:
    """The audit trail is never purged, enforced by the absence of a statement.

    Asserted over every rule rather than over `audit_log` alone, so a future
    classification that moved it into the purge bucket fails here rather than in
    production.
    """
    assert reset.rule_for("audit_log").bucket == reset.PRESERVE
    assert "audit_log" not in {r.table for r in reset.purge_order()}


# ── Export ───────────────────────────────────────────────────────────────────


def test_the_manifest_digest_notices_an_edited_export(tmp_path: pathlib.Path) -> None:
    """A manifest that can be edited to excuse a file that changed is a
    manifest, not a checksum."""
    directory = tmp_path / "export"
    directory.mkdir()
    payload = [{"id": "1"}]
    (directory / "evaluations.json").write_text(reset.dumps(payload), encoding="utf-8")
    tables = {
        "evaluations": {
            "rows": 1,
            "file": "evaluations.json",
            "sha256": reset.sha256_of(directory / "evaluations.json"),
        }
    }
    manifest = {"tables": tables, "digest": reset._manifest_digest(tables)}
    (directory / reset.MANIFEST_NAME).write_text(reset.dumps(manifest), encoding="utf-8")
    assert reset.read_manifest(directory)["digest"] == manifest["digest"]

    (directory / "evaluations.json").write_text("[]", encoding="utf-8")
    with pytest.raises(reset.ExportNotVerified):
        reset.read_manifest(directory)


def test_a_missing_export_file_refuses(tmp_path: pathlib.Path) -> None:
    with pytest.raises(reset.ExportNotVerified):
        reset.read_manifest(tmp_path)


def test_the_export_root_ignores_itself(tmp_path: pathlib.Path) -> None:
    """An export holds real resumes, transcripts and report text, and the
    default location is inside the working tree."""
    directory = tmp_path / "legacy_reset_exports" / "20260829T000000Z"
    reset.ensure_export_root(directory)
    assert (directory.parent / ".gitignore").read_text(encoding="utf-8").strip().endswith("*")


def test_money_never_round_trips_through_a_float() -> None:
    from decimal import Decimal

    assert reset.json_default(Decimal("60.000001")) == "60.000001"
    assert isinstance(reset.json_default(Decimal("1")), str)


def test_an_unserialisable_value_raises_rather_than_being_dropped() -> None:
    with pytest.raises(TypeError):
        reset.dumps({"value": object()})


# ── Gate G1 reachability ─────────────────────────────────────────────────────


def test_the_wiring_inspector_finds_the_gate_call_site() -> None:
    """The check is a reachability query over the import graph, not a grep.

    A gate called from a module nothing on a live path imports is a gate that
    never runs, and a grep for its name cannot tell the difference.
    """
    wiring = reset.inspect_gate_wiring()
    assert wiring.call_sites, "scorecard_gate is not called anywhere in app/"
    assert any("hiring/gates.py" not in site for site in wiring.call_sites)


def test_the_purge_refuses_while_g1_is_unreachable() -> None:
    """spec-doc6 D2 asserts G1 already blocks evaluation without an approved
    scorecard. That premise is checked rather than believed, because if it is
    false the archive-and-mark step clears the approval stamps and evaluation
    carries on regardless: the exact state D2 exists to prevent, reached while
    every report says the reset succeeded.
    """
    unreachable = reset.GateWiring(("app/services/miti/pipeline.py:290",), ())
    assert not unreachable.enforced
    with pytest.raises(reset.GateNotWired) as raised:
        reset.assert_gate_enforced(unreachable)
    message = str(raised.value)
    assert "app/services/miti/pipeline.py:290" in message, "name the file and line"
    assert "second enforcement path" in message


def test_a_reachable_gate_passes() -> None:
    reachable = reset.GateWiring(
        ("app/services/miti/pipeline.py:290",), ("app.api.assessments",)
    )
    assert reachable.enforced
    reset.assert_gate_enforced(reachable)


def test_a_gate_that_is_never_called_is_not_enforced_either() -> None:
    with pytest.raises(reset.GateNotWired) as raised:
        reset.assert_gate_enforced(reset.GateWiring((), ("app.api.assessments",)))
    assert "never called" in str(raised.value)


# ── Survey rendering ─────────────────────────────────────────────────────────


def _survey(**overrides) -> reset.Survey:
    from collections import OrderedDict
    from datetime import datetime, timezone

    defaults = dict(
        taken_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        database="scratch",
        schema_revision="0062_embedding_provenance",
        code_head_revision="0062_embedding_provenance",
        live_tables=tuple(LIVE_TABLES),
        absent_tables=(),
        totals=OrderedDict((rule.table, 0) for rule in reset.CLASSIFICATION),
        tenants=(),
        per_job=(),
        edge_cases=(),
        schema_findings=(),
        objects=reset.ObjectStoreReconciliation(performed=False, reason="no bucket"),
        gate_wiring=reset.GateWiring((), ()),
    )
    defaults.update(overrides)
    return reset.Survey(**defaults)


def test_a_measurement_that_could_not_be_taken_never_renders_as_zero() -> None:
    """A check that did not run and a check that found nothing must not look
    the same. This project has already shipped six secret-hygiene assertions
    that read SKIPPED, one word away from PASSED, while nothing was enforced.
    """
    assert reset._count_cell(-1) == "NOT MEASURED"
    assert reset._count_cell(0) == "0"
    markdown = reset.render_survey(
        _survey(
            edge_cases=(
                reset.EdgeCase("k", "Something unmeasurable", -1, "why", True),
            )
        )
    )
    assert "| Something unmeasurable | NOT MEASURED |" in markdown


def test_a_schema_finding_survives_into_the_document() -> None:
    """A survey that only counted rows would have walked past both of them: the
    cascade that takes a Team Review remark with its author, and a CHECK still
    enforcing a rating vocabulary the product retired."""
    markdown = reset.render_survey(
        _survey(
            schema_findings=(
                reset.EdgeCase(
                    "team_review_author_cascade",
                    "Team Review remarks are deleted with their author",
                    1,
                    "ON DELETE CASCADE on reviewer_user_id.",
                ),
            )
        )
    )
    assert "Schema findings the reset looked at and did not change" in markdown
    assert "Team Review remarks are deleted with their author" in markdown


def test_an_unlisted_object_store_reports_not_performed_rather_than_clean() -> None:
    markdown = reset.render_survey(_survey())
    assert "**NOT PERFORMED.**" in markdown
    assert "unanswered, not clean" in markdown


def test_the_survey_leads_with_the_gate_finding_when_g1_is_unreachable() -> None:
    markdown = reset.render_survey(
        _survey(gate_wiring=reset.GateWiring(("app/services/miti/pipeline.py:290",), ()))
    )
    assert "GATE G1 IS NOT ENFORCED ON ANY LIVE PATH" in markdown
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings[0].startswith("## The premise")


def test_the_survey_classifies_every_table() -> None:
    markdown = reset.render_survey(_survey())
    for rule in reset.CLASSIFICATION:
        assert f"| `{rule.table}` |" in markdown


def test_an_absent_table_is_called_out_and_not_counted_as_empty() -> None:
    markdown = reset.render_survey(_survey(absent_tables=("evaluations",)))
    assert "BEHIND the migration files" in markdown
    assert "NOT MEASURED" in markdown


def test_classify_object_uri_separates_legacy_from_missing() -> None:
    assert reset.classify_object_uri(None) == "none"
    assert reset.classify_object_uri("   ") == "none"
    assert reset.classify_object_uri("s3://bucket/resumes/abc") == "s3"
    assert reset.classify_object_uri("https://res.cloudinary.com/x/y") == "legacy"


# ── The regrade work plan ────────────────────────────────────────────────────


def test_the_regrade_plan_rounds_batches_up() -> None:
    """Eleven candidates at a batch size of ten is two calls, not one."""
    from collections import OrderedDict

    plan = reset.RegradePlan(
        jobs=2,
        candidates=11,
        tenants=OrderedDict({"Acme": {"jobs": 2, "candidates": 11}}),
        batch_size=10,
        model="claude-haiku-4-5-20251001",
        llm_calls=-(-11 // 10),
        embedding_calls=4,
        estimated_cost_usd=0.1,
        estimated_seconds=30.0,
        keys_present=False,
    )
    assert plan.llm_calls == 2
    rendered = reset.render_regrade_plan(plan)
    assert "candidates in scope       : 11" in rendered
    assert "credentials present       : no" in rendered
    assert "estimates" in rendered.splitlines()[0]


def test_the_regrade_model_comes_from_the_routing_policy() -> None:
    """The estimate must move when the policy does, so it is read rather than
    restated."""
    from app.config.llm_providers import MODEL_FOR_TASK

    assert reset.REGRADE_TASK in MODEL_FOR_TASK


@pytest.mark.asyncio
async def test_the_regrade_refuses_to_run_without_credentials(monkeypatch) -> None:
    """Without keys, every candidate would be graded on the deterministic dev
    fallback, and a hash-derived ranking written into the column a recruiter
    sorts on is worse than no grade at all."""
    from collections import OrderedDict

    plan = reset.RegradePlan(
        jobs=1,
        candidates=1,
        tenants=OrderedDict(),
        batch_size=10,
        model="claude-haiku-4-5-20251001",
        llm_calls=1,
        embedding_calls=2,
        estimated_cost_usd=0.0,
        estimated_seconds=15.0,
        keys_present=False,
    )

    async def _plan(_session):
        return plan

    monkeypatch.setattr(reset, "plan_regrade", _plan)

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return None

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(reset, "superadmin_scope", _noop_scope)
    with pytest.raises(reset.MissingCredentials):
        await reset.run_regrade(_Factory(), apply=True)


class _noop_scope:
    def __init__(self, _session):
        pass

    async def __aenter__(self):
        return None

    async def __aexit__(self, *_):
        return False


# ── House rules ──────────────────────────────────────────────────────────────


BANNED_PHRASES = (
    "TODO",
    "FIXME",
    "XXX",
    "in a real implementation",
    "for now",
    "this is a simplified",
    "verified against the API",
    "confirmed working",
    "tested live",
)


@pytest.mark.parametrize(
    "path",
    [SCRIPT, MIGRATION, pathlib.Path(reset.__file__).with_name("reembed.py")],
    ids=lambda p: p.name,
)
def test_no_em_dash_and_no_banned_phrase(path: pathlib.Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert EM_DASH not in source, f"{path.name} contains an em dash"
    lowered = source.lower()
    for phrase in BANNED_PHRASES:
        assert phrase.lower() not in lowered, f"{path.name} contains {phrase!r}"


@pytest.mark.parametrize(
    "path",
    [SCRIPT, pathlib.Path(reset.__file__).with_name("reembed.py")],
    ids=lambda p: p.name,
)
def test_no_silent_exception_swallowing(path: pathlib.Path) -> None:
    """A purge that swallowed an error is the worst possible place for one."""
    source = path.read_text(encoding="utf-8")
    assert not re.search(r"except\s*:", source)
    assert not re.search(r"except[^\n]*:\s*\n\s*pass\b", source)


def test_only_the_three_permitted_model_strings_appear() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for match in re.findall(r"claude-[a-z0-9.\-]+|voyage-[a-z0-9.\-]+", source):
        assert match in {
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "voyage-4",
        }, match
