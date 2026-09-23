"""Portable Intelligence, against a real database (change request 23).

THE CONSTRAINT THIS FILE EXISTS TO PROVE
------------------------------------------
The owner reversed the 2026-07-30 "reuse is retired" ruling on 2026-09-22, and
the reversal is only safe if one sentence holds absolutely:

    a prior job's SCORE or GRADE is never reused as evidence, for anything.

Everything else here is supporting work. The three tests that carry the weight
are:

* `test_a_prior_score_cannot_reach_a_new_jobs_report`, which builds a real
  completed report on job A carrying a distinctive score and a distinctive
  grade word, runs the whole portable path into job B, and reads every byte
  that came out looking for either of them;
* `test_the_table_has_no_verdict_column`, which asks `information_schema`
  rather than the model, because the model is what a future migration would
  quietly get ahead of;
* `test_the_portable_path_cannot_read_a_report_at_all`, which walks the import
  graph and the SQL, because a path that CAN reach a grade is one refactor away
  from carrying one.

Then the two that protect the other half of the split:
`test_a_behavioural_criterion_is_never_established` and
`test_a_tenant_session_sees_no_portable_row`.

The database-backed tests seed and tear down their own tenant, so they are safe
beside other suites on the shared stack.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.services import consent_catalog
from app.services import portable_evidence as pe
from app.services import retake
from app.services.assessment_questions import generate as question_generation

TABLE = "portable_evidence_items"

#: The distinctive marks planted on job A's report. Chosen so a grep over the
#: portable output is unambiguous: 91 is not a plausible ordinal, a slug or a
#: character count, and "Highly Matching" is one of exactly four strings the
#: product will ever print as a grade.
PRIOR_SCORE = 91
PRIOR_GRADE = "Highly Matching"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(f"SELECT 1 FROM {TABLE} LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 - no database, or the migration is not applied
        return False
    finally:
        await engine.dispose()


# ── The pure layer ───────────────────────────────────────────────────────────


class _Criterion:
    def __init__(self, name: str, category: str) -> None:
        self.name = name
        self.category = category


def _fact(subject: str, *, kind: str = pe.KIND_CORE_SKILL, age_days: int = 5) -> pe.PortableFact:
    return pe.PortableFact(
        kind=kind,
        subject=subject,
        statement=f"The candidate's resume claims {subject}.",
        source_kind=pe.SOURCE_RESUME,
        source_ref="profiles:00000000-0000-0000-0000-000000000001#skills",
        observed_on=date.today() - timedelta(days=age_days),
    )


def test_a_behavioural_criterion_is_never_established():
    """THE SHARPEST EDGE IN THE FEATURE, and the evidence is deliberately ideal.

    The portable fact here matches the criterion name exactly, is fresh, and is
    of a covering kind. A must-have with the identical fact IS established two
    lines down, so the refusal is the category and nothing else. Every
    behavioural dimension is freshly assessed on every application.
    """
    fact = _fact("Ownership")
    behavioural = pe.coverage(
        [_Criterion("Ownership", "behavioural")], [fact], max_age_days=183
    )
    assert behavioural.established == ()
    assert behavioural.behavioural == ("Ownership",)

    must_have = pe.coverage(
        [_Criterion("Ownership", "must_have")], [fact], max_age_days=183
    )
    assert must_have.established_names == ("Ownership",)


def test_the_job_specific_vocabulary_keeps_leadership_and_scenarios_fresh():
    """Leadership, scenario and strategic criteria are never established.

    They are the other three members of the Job Specific layer, and they are
    recognised from the words a criterion is made of rather than from a list of
    criterion names, so one nobody has seen before still resolves.
    """
    criteria = [
        _Criterion("Stakeholder management", "must_have"),
        _Criterion("Incident judgement", "must_have"),
        _Criterion("Roadmap alignment", "nice_to_have"),
    ]
    facts = [_fact(c.name) for c in criteria]
    result = pe.coverage(criteria, facts, max_age_days=183)
    assert result.established == ()
    assert set(result.to_assess) == {c.name for c in criteria}


def test_the_bias_of_the_vocabulary_is_towards_asking():
    """A technical name that grazes the vocabulary is asked, not established.

    Documented rather than regretted: a false "job specific" costs one
    question, and a false "portable" costs a grade decided on evidence nobody
    put to the person. Only one of those is recoverable.
    """
    assert pe.criterion_is_job_specific("Configuration management")
    assert not pe.criterion_is_job_specific("Kafka")


def test_an_undated_or_stale_fact_never_establishes_anything():
    """No date is not a recent date, and `recorded_at` is not a substitute.

    When we wrote a fact down is not when it was true. Conflating the two is
    how a 2019 achievement described in a resume uploaded yesterday reads as
    current, which is the lesson the ledger's own `event_date` column carries.
    """
    undated = pe.PortableFact(
        kind=pe.KIND_CORE_SKILL,
        subject="Kafka",
        statement="The candidate's resume claims Kafka.",
        source_kind=pe.SOURCE_RESUME,
        source_ref="profiles:x#skills",
        observed_on=None,
    )
    stale = _fact("Kafka", age_days=400)
    for facts in ([undated], [stale]):
        result = pe.coverage(
            [_Criterion("Kafka", "must_have")], facts, max_age_days=183
        )
        assert result.established == ()
        assert result.to_assess == ("Kafka",)


def test_a_profile_fact_is_portable_but_establishes_no_capability():
    """Storable is a wider question than covering, and the gap is deliberate.

    "Notice period: 60 days" is worth carrying between jobs and evidences no
    capability, so it can never stand in for asking about one.
    """
    assert pe.KIND_PROFILE_FACT in pe.PORTABLE_KINDS
    assert pe.KIND_PROFILE_FACT not in pe.COVERING_KINDS
    result = pe.coverage(
        [_Criterion("Notice period", "must_have")],
        [_fact("Notice period", kind=pe.KIND_PROFILE_FACT)],
        max_age_days=183,
    )
    assert result.established == ()


def test_every_criterion_is_accounted_for():
    """No criterion may fall out of all three buckets.

    A criterion silently dropped between the matrix and the conversation is the
    "insufficient evidence is not negative evidence" rule failing in its other
    direction: an item nobody grades at all.
    """
    criteria = [
        _Criterion("Kafka", "must_have"),
        _Criterion("Postgres", "nice_to_have"),
        _Criterion("Ownership", "behavioural"),
        _Criterion("Stakeholder management", "must_have"),
    ]
    result = pe.coverage(criteria, [_fact("Kafka")], max_age_days=183)
    accounted = (
        set(result.established_names) | set(result.to_assess) | set(result.behavioural)
    )
    assert accounted == {c.name for c in criteria}
    # And the guard itself refuses a forged result rather than passing it on.
    with pytest.raises(pe.PortableEvidenceError):
        pe.Coverage().assert_total(criteria)


def test_word_boundaries_hold_when_matching_a_criterion():
    """'Java' must not establish 'JavaScript'. The disqualifier lesson."""
    result = pe.coverage(
        [_Criterion("JavaScript", "must_have")], [_fact("Java")], max_age_days=183
    )
    assert result.established == ()


def test_the_candidate_sentence_names_things_and_counts_nothing():
    """THE CANDIDATE FACING COPY, swept for every rule it has to obey.

    No digit anywhere, because a count is a number reaching a client and rule 1
    has exactly one amendment and this is not it. No em dash. And the
    behavioural half is stated out loud: a candidate told the platform already
    holds their skills evidence will read a behavioural question as the
    platform having forgotten.
    """
    result = pe.coverage(
        [
            _Criterion("Kafka", "must_have"),
            _Criterion("Postgres", "must_have"),
            _Criterion("Ownership", "behavioural"),
        ],
        [_fact("Kafka")],
        max_age_days=183,
    )
    sentence = pe.coverage_sentence(result)
    assert sentence is not None
    assert "Kafka" in sentence and "Postgres" in sentence
    assert not any(character.isdigit() for character in sentence)
    assert chr(8212) not in sentence
    assert "behavioural questions are always asked fresh" in sentence
    # Nothing established is nothing to announce, rather than a sentence
    # claiming the record is empty.
    assert pe.coverage_sentence(pe.Coverage()) is None


def test_the_write_path_refuses_a_verdict():
    """Three refusals, and the first two are the ones a careless caller hits.

    None of them is the real guarantee, which is that the table has no column
    for a verdict. They are what stops the same number riding along inside a
    JSONB payload or inside prose one level down.
    """
    with pytest.raises(pe.PortableEvidenceError):
        pe._sweep_for_verdict({"score": 91})
    with pytest.raises(pe.PortableEvidenceError):
        pe._sweep_for_verdict({"prior": {"dimensions": [{"grade": PRIOR_GRADE}]}})
    with pytest.raises(pe.PortableEvidenceError):
        pe._sweep_statement(f"The candidate graded {PRIOR_GRADE} on Kafka.")
    # And a legitimate fact is not caught by any of them.
    pe._sweep_for_verdict({"designation": "Staff Engineer"})
    pe._sweep_statement("The candidate's resume claims Kafka among their skills.")


def test_the_vocabularies_match_their_owners_in_both_directions():
    """Every literal restated in this module is pinned against its owner.

    The copies exist because importing the owners would close an import cycle
    (`app.services.evidence` walks through `verification` into
    `functional_assessment`), which is the bargain `evidence_confidence`
    already makes. The copy costs these assertions.
    """
    from app.services import ppi
    from app.services.evidence import ledger

    assert pe.NEVER_COVERED_CATEGORIES == {ppi.CATEGORY_BEHAVIOURAL}
    assert pe.NEVER_COVERED_CATEGORIES <= set(ppi.CATEGORIES)
    assert pe.TRUST_LEVELS == ledger.TRUST_LEVELS
    assert pe.TRUST_AUTHORITATIVE == ledger.TRUST_AUTHORITATIVE
    assert pe.TRUST_VALIDATED == ledger.TRUST_VALIDATED
    assert pe.TRUST_OBSERVED == ledger.TRUST_OBSERVED
    assert pe.TRUST_INFERRED == ledger.TRUST_INFERRED
    assert pe.COVERING_KINDS <= pe.PORTABLE_KINDS


def test_the_migration_literals_match_the_module():
    """The CHECK constraints and the Python vocabulary cannot drift apart.

    A migration states its values as literals on purpose, so that a later
    rename of a constant cannot change what a historical migration did. That
    convention is only safe with a test comparing the two.
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0113_portable_evidence.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"_KINDS", "_SOURCE_KINDS", "_TRUST", "_STATUSES"}:
                literals[name] = {
                    element.value
                    for element in node.value.elts  # type: ignore[attr-defined]
                }
    assert literals["_KINDS"] == set(pe.PORTABLE_KINDS)
    assert literals["_SOURCE_KINDS"] == set(pe.SOURCE_KINDS)
    assert literals["_TRUST"] == set(pe.TRUST_LEVELS)
    assert literals["_STATUSES"] == {
        pe.STATUS_ACTIVE, pe.STATUS_SUPERSEDED, pe.STATUS_REVOKED
    }


def test_no_report_section_travels_and_the_old_ruling_still_binds():
    """The 2026-09-22 reversal did NOT reopen report reuse.

    `PORTABLE_CATEGORIES` is still empty and `copy_report` still copies
    nothing. What the ruling reversed is the reuse of EVIDENCE; a report
    section is a grade, and a grade is still never portable.
    `app/scripts/eval_report.py` gates CI on the same fact.
    """
    assert retake.PORTABLE_CATEGORIES == frozenset()


def test_the_portable_path_cannot_read_a_report_at_all():
    """Structural, over the source: there is no route from here to a grade.

    An assertion about behaviour would pass while the capability sat there
    waiting for a caller. This asserts the capability is absent: the module
    names no report table in its SQL and imports no report model, so a
    refactor cannot carry a grade by accident.
    """
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "services" / "portable_evidence.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden_modules = {
        "app.models.assessment",
        "app.models.hiring",
        "app.services.functional_assessment",
        "app.services.miti",
        "app.services.siddhi",
        "app.services.hiring.scorecard",
    }
    assert not (imported & forbidden_modules), sorted(imported & forbidden_modules)

    # THE SQL HALF. A raw statement is invisible to an import graph, so every
    # `text(...)` argument in the module is read separately. The STATEMENTS
    # are checked rather than the whole file, deliberately: the module's own
    # docstrings name these tables in order to say it does not read them, and
    # a sweep that could not tell prose from code would either fail on that
    # sentence or force the sentence to be deleted. The 2026-08-29 grep is the
    # cautionary tale: it matched comments and missed imports.
    statements: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
        ):
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    statements.append(argument.value.casefold())
    assert statements, "no SQL was found, so this would pass vacuously"
    sql = " ".join(statements)
    for table in (
        "functional_skills_reports",
        "report_dimensions",
        "evaluations",
        "report_skill_evidence",
    ):
        assert table not in sql, table


# ── The database-backed half ─────────────────────────────────────────────────


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.other_tenant = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.profile = uuid.uuid4()
        self.job_a = uuid.uuid4()
        self.job_b = uuid.uuid4()
        self.link_a = uuid.uuid4()
        self.report_a = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable, or 0113_portable_evidence is not applied")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for tenant_id, label in (
                        (w.tenant, "A"), (w.other_tenant, "B"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO tenants (id, name, domain, "
                                " spf_dkim_status) VALUES (:id, :name, :domain, "
                                " 'pending')"
                            ),
                            {
                                "id": str(tenant_id),
                                "name": f"Portable-{label}-{tenant_id.hex[:6]}",
                                "domain": f"{tenant_id.hex[:10]}.portable.test",
                            },
                        )
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidates (id, full_name, email, "
                            " consent_databank, retain_assessment_consent) "
                            "VALUES (:id, 'Anita Rao', :email, true, true)"
                        ),
                        {
                            "id": str(w.candidate),
                            "email": f"{w.candidate.hex[:10]}@portable.test",
                        },
                    )
                    # The AUTHORITY for cross-employer reuse is the catalogue
                    # row, not `retain_assessment_consent`: that flag speaks
                    # about retention and is wired to the download verb, and
                    # reading it here would stretch one consent over two
                    # purposes. The flag stays set above only so this world
                    # still exercises the download path it genuinely governs.
                    await consent_catalog.record_items(
                        session,
                        candidate_id=w.candidate,
                        keys=[consent_catalog.CROSS_EMPLOYER_EVIDENCE_REUSE],
                        source="test",
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO profiles (id, candidate_id, "
                            " parsed_fields_json, resume_storage_provider) "
                            "VALUES (:id, :cid, CAST(:fields AS jsonb), 's3')"
                        ),
                        {
                            "id": str(w.profile),
                            "cid": str(w.candidate),
                            "fields": '{"skills": ["Kafka", "Postgres"]}',
                        },
                    )
                    for job_id, title in ((w.job_a, "Platform"), (w.job_b, "Data")):
                        await session.execute(
                            sa.text(
                                "INSERT INTO jobs (id, tenant_id, title, jd_json, "
                                " jd_markdown, status, lifecycle_state) "
                                "VALUES (:id, :tid, :title, CAST('{}' AS jsonb), "
                                " '## The role', 'draft', 'DRAFT')"
                            ),
                            {
                                "id": str(job_id),
                                "tid": str(w.tenant),
                                "title": f"{title} Engineer",
                            },
                        )
                    await session.execute(
                        sa.text(
                            "INSERT INTO job_candidate_links (id, tenant_id, "
                            " job_id, candidate_id, source) "
                            "VALUES (:id, :tid, :jid, :cid, 'fresh')"
                        ),
                        {
                            "id": str(w.link_a),
                            "tid": str(w.tenant),
                            "jid": str(w.job_a),
                            "cid": str(w.candidate),
                        },
                    )
                    # JOB A'S COMPLETED REPORT, carrying the marks this file
                    # hunts for afterwards.
                    await session.execute(
                        sa.text(
                            "INSERT INTO functional_skills_reports (id, tenant_id, "
                            " job_id, job_candidate_link_id, grade, status, "
                            " overall_summary, overall_score, validation_json, "
                            " suggested_probes_json, gap_analysis_json, "
                            " synthesized_at) "
                            "VALUES (:id, :tid, :jid, :lid, 'non_managerial', "
                            " 'ready', :summary, :score, CAST('{}' AS jsonb), "
                            " CAST('[]' AS jsonb), CAST('{}' AS jsonb), now())"
                        ),
                        {
                            "id": str(w.report_a),
                            "tid": str(w.tenant),
                            "jid": str(w.job_a),
                            "lid": str(w.link_a),
                            "summary": f"Kafka depth assessed as {PRIOR_GRADE}.",
                            "score": PRIOR_SCORE,
                        },
                    )
                    # Job B's matrix: one coverable must-have, one behavioural,
                    # one leadership must-have.
                    for ordinal, (category, name) in enumerate(
                        [
                            ("must_have", "Kafka"),
                            ("must_have", "Stakeholder management"),
                            ("behavioural", "Kafka"),
                        ],
                        start=1,
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO job_competencies (id, tenant_id, "
                                " job_id, category, name, required_level, "
                                " ordinal, is_active) VALUES (:id, :tid, :jid, "
                                " :cat, :name, 3, :ord, true)"
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "tid": str(w.tenant),
                                "jid": str(w.job_b),
                                "cat": category,
                                "name": name,
                                "ord": ordinal,
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = :id"),
                        {"id": str(w.candidate)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                        {"ids": [str(w.tenant), str(w.other_tenant)]},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


def test_the_table_has_no_verdict_column(world: World):
    """ASKED OF `information_schema`, NEVER OF THE MODEL.

    The model is what a future migration would quietly get ahead of. The
    absence of a verdict column is the real enforcement of "a grade is never
    portable", so it is checked against the live schema, and the assertion
    names the whole vocabulary rather than the words somebody thought of.
    """
    async def _columns() -> set[str]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        sa.text(
                            "SELECT column_name FROM information_schema.columns "
                            " WHERE table_name = :t"
                        ),
                        {"t": TABLE},
                    )
                ).all()
        return {row[0] for row in rows}

    columns = _run(_columns())
    assert columns, "the table is missing entirely"
    forbidden = {
        "score", "grade", "band", "tier", "percent", "percentage", "rating",
        "match_score", "match_percent", "required_level", "verdict",
        "composite", "overall_score", "delivered_score", "confidence",
        "report_id", "evaluation_id",
    }
    assert not (columns & forbidden), sorted(columns & forbidden)
    # And no tenant column, for the reason `candidate_employments` has none.
    assert "tenant_id" not in columns


def test_a_prior_score_cannot_reach_a_new_jobs_report(world: World):
    """THE TEST THE WHOLE FEATURE IS GATED ON.

    Job A holds a completed report scoring 91 and saying "Highly Matching".
    Job B then runs the entire portable path: harvest, load, coverage, the
    pre-fill text and the candidate sentence. Every string that comes out is
    searched for both marks.

    It is a round trip over a real table rather than an assertion about a mock,
    because the failure being prevented is a column or a join appearing later:
    a mock would keep passing through exactly that change.
    """
    async def _exercise() -> tuple[list[str], list[dict]]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await pe.harvest(
                        session,
                        candidate_id=world.candidate,
                        job_id=world.job_b,
                        tenant_id=world.tenant,
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    rows = (
                        await session.execute(
                            sa.text(
                                f"SELECT * FROM {TABLE} WHERE candidate_id = :cid"
                            ),
                            {"cid": str(world.candidate)},
                        )
                    ).mappings().all()
                    result = await retake.load_coverage(
                        session, world.candidate, world.job_b
                    )
        emitted = [pe.prefill_text(item) for item in result.established]
        sentence = pe.coverage_sentence(result)
        if sentence:
            emitted.append(sentence)
        emitted.extend(item.fact.statement for item in result.established)
        emitted.extend(str(item.provenance) for item in result.established)
        return emitted, [dict(row) for row in rows]

    emitted, rows = _run(_exercise())

    assert rows, "the harvest recorded nothing, so this would pass vacuously"
    assert emitted, "coverage established nothing, so this would pass vacuously"

    haystack = " ".join(emitted) + " " + " ".join(str(row) for row in rows)
    # ON WORD BOUNDARIES, not as a substring. The rows carry uuids, and a uuid
    # contains every two-digit number eventually; a substring search would fail
    # on `...-891f-...` and the failure would say nothing about a score
    # travelling. The boundary is what makes a hit mean what the test claims.
    assert re.search(rf"(?<!\w){PRIOR_SCORE}(?!\w)", haystack) is None
    assert PRIOR_GRADE.casefold() not in haystack.casefold()
    assert str(world.report_a) not in haystack
    # Not one of the four grade words, in any casing, anywhere.
    from app.services.rating import GRADES

    for grade in GRADES:
        assert grade.casefold() not in haystack.casefold(), grade


def test_a_tenant_session_sees_no_portable_row(world: World):
    """THE TENANT ISOLATION MODEL, proved against the policy rather than the code.

    The policy is bypass only, in both directions, which is stricter than
    `bgv_inquiries` on purpose: this table's only two readers already run in an
    audited bypass scope, so a tenant equality arm would be a door nobody walks
    through. A session in the candidate's OWN tenant, and a session in an
    unrelated one, both see zero rows and can write none.
    """
    async def _probe() -> tuple[int, int, bool]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await pe.record(
                        session,
                        candidate_id=world.candidate,
                        kind=pe.KIND_CORE_SKILL,
                        subject="Kafka",
                        statement="The candidate's resume claims Kafka.",
                        source_kind=pe.SOURCE_RESUME,
                        source_ref=f"profiles:{world.profile}#skills",
                        observed_on=date.today(),
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    visible_to_bypass = (
                        await session.execute(
                            sa.text(
                                f"SELECT count(*) FROM {TABLE} "
                                " WHERE candidate_id = :cid"
                            ),
                            {"cid": str(world.candidate)},
                        )
                    ).scalar_one()

        seen_by_tenant = 0
        write_refused = False
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.other_tenant):
                    seen_by_tenant = (
                        await session.execute(
                            sa.text(f"SELECT count(*) FROM {TABLE}")
                        )
                    ).scalar_one()
        async with sessions() as session:
            try:
                async with session.begin():
                    async with tenant_scope(session, world.tenant):
                        await session.execute(
                            sa.text(
                                f"INSERT INTO {TABLE} (candidate_id, kind, "
                                " subject, statement, trust, source_kind, "
                                " source_ref, recorded_at) VALUES (:cid, "
                                " 'core_skill', 'Smuggled', 'x', 'observed', "
                                " 'resume', 'profiles:x', now())"
                            ),
                            {"cid": str(world.candidate)},
                        )
            except Exception:  # noqa: BLE001 - the policy is what we are testing
                write_refused = True
        return visible_to_bypass, seen_by_tenant, write_refused

    visible_to_bypass, seen_by_tenant, write_refused = _run(_probe())
    # The row exists, so the zero below is the policy and not an empty table.
    assert visible_to_bypass >= 1
    assert seen_by_tenant == 0
    assert write_refused


def test_load_for_link_needs_a_link_to_stand_on(world: World):
    """An employer with no pipeline row for this person reads nothing.

    The same legitimacy test `candidates.get_bgv_results` makes before it will
    show a background check, and the right one here for the same reason: a
    portable record follows a person, so "which employers may read it" cannot
    be answered by a tenant column that does not exist.
    """
    async def _probe() -> tuple[int, int]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await pe.record(
                        session,
                        candidate_id=world.candidate,
                        kind=pe.KIND_CORE_SKILL,
                        subject="Kafka",
                        statement="The candidate's resume claims Kafka.",
                        source_kind=pe.SOURCE_RESUME,
                        source_ref=f"profiles:{world.profile}#skills",
                        observed_on=date.today(),
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    linked = await pe.load_for_link(
                        session,
                        candidate_id=world.candidate,
                        tenant_id=world.tenant,
                    )
                    unlinked = await pe.load_for_link(
                        session,
                        candidate_id=world.candidate,
                        tenant_id=world.other_tenant,
                    )
        return len(linked), len(unlinked)

    linked, unlinked = _run(_probe())
    assert linked >= 1
    assert unlinked == 0


def test_recording_the_same_fact_twice_revives_rather_than_colliding(world: World):
    """The soft delete under the hard unique constraint, handled this time.

    `uq_portable_evidence_subject` has no predicate and `status` is a soft
    retirement, which is exactly the pairing that 500'd the matrix editor in
    pilot on 2026-09-20. Safe here only because the one writer is an UPSERT
    that revives. A re-observation of a retired fact restores it.
    """
    async def _probe() -> tuple[uuid.UUID, uuid.UUID, str]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    first = await pe.record(
                        session,
                        candidate_id=world.candidate,
                        kind=pe.KIND_CORE_SKILL,
                        subject="Kafka",
                        statement="The candidate's resume claims Kafka.",
                        source_kind=pe.SOURCE_RESUME,
                        source_ref=f"profiles:{world.profile}#skills",
                        observed_on=date.today(),
                    )
                    await session.execute(
                        sa.text(
                            f"UPDATE {TABLE} SET status = 'revoked' WHERE id = :id"
                        ),
                        {"id": str(first)},
                    )
                    second = await pe.record(
                        session,
                        candidate_id=world.candidate,
                        kind=pe.KIND_CORE_SKILL,
                        subject="Kafka",
                        statement="The candidate's resume still claims Kafka.",
                        source_kind=pe.SOURCE_RESUME,
                        source_ref=f"profiles:{world.profile}#skills",
                        observed_on=date.today(),
                    )
            # Read back from a SECOND transaction: a returned id is not
            # evidence that a row committed.
            async with session.begin():
                async with superadmin_scope(session):
                    status = (
                        await session.execute(
                            sa.text(f"SELECT status FROM {TABLE} WHERE id = :id"),
                            {"id": str(first)},
                        )
                    ).scalar_one()
        return first, second, status

    first, second, status = _run(_probe())
    assert first == second, "the row was re-inserted rather than revived"
    assert status == pe.STATUS_ACTIVE


def test_a_closed_job_cannot_take_a_portable_row_with_it(world: World):
    """Job deletion forgets the provenance and keeps the fact.

    Named for the hazard the change request called out: job closure is being
    given a purge of job-scoped assessment data. `source_job_id` is ON DELETE
    SET NULL rather than CASCADE precisely so that sweep can never reach a
    person's standing record.
    """
    async def _probe() -> tuple[int, uuid.UUID | None]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await pe.record(
                        session,
                        candidate_id=world.candidate,
                        kind=pe.KIND_CORE_SKILL,
                        subject="Kafka",
                        statement="The candidate's resume claims Kafka.",
                        source_kind=pe.SOURCE_RESUME,
                        source_ref=f"profiles:{world.profile}#skills",
                        observed_on=date.today(),
                        source_job_id=world.job_b,
                        source_tenant_id=world.tenant,
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM jobs WHERE id = :id"),
                        {"id": str(world.job_b)},
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    row = (
                        await session.execute(
                            sa.text(
                                f"SELECT count(*) AS n, max(source_job_id::text) "
                                f" AS j FROM {TABLE} WHERE candidate_id = :cid"
                            ),
                            {"cid": str(world.candidate)},
                        )
                    ).mappings().one()
        return int(row["n"]), row["j"]

    surviving, source_job = _run(_probe())
    assert surviving >= 1
    assert source_job is None


def test_consent_gates_reuse_and_absence_is_never_consent(world: World):
    """A candidate who did not consent gets every criterion assessed fresh.

    An explicit False and a never-asked NULL are treated identically, which is
    the safe direction the consent module already takes everywhere else.
    """
    async def _probe(value: bool | None) -> pe.Coverage:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    # Absence of a row is the refusal: the catalogue has
                    # no "no" to store, so a missing row must read as one.
                    if value:
                        await consent_catalog.record_items(
                            session,
                            candidate_id=world.candidate,
                            keys=[consent_catalog.CROSS_EMPLOYER_EVIDENCE_REUSE],
                            source="test",
                        )
                    else:
                        await session.execute(
                            sa.text(
                                "DELETE FROM candidate_consents "
                                "WHERE candidate_id = :id AND item_key = :key"
                            ),
                            {
                                "id": str(world.candidate),
                                "key": consent_catalog.CROSS_EMPLOYER_EVIDENCE_REUSE,
                            },
                        )
                    await pe.record(
                        session,
                        candidate_id=world.candidate,
                        kind=pe.KIND_CORE_SKILL,
                        subject="Kafka",
                        statement="The candidate's resume claims Kafka.",
                        source_kind=pe.SOURCE_RESUME,
                        source_ref=f"profiles:{world.profile}#skills",
                        observed_on=date.today(),
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    return await retake.load_coverage(
                        session, world.candidate, world.job_b
                    )

    assert _run(_probe(True)).established_names == ("Kafka",)
    assert _run(_probe(False)).established == ()
    assert _run(_probe(None)).established == ()


def test_the_harvest_records_only_portable_kinds(world: World):
    """Everything the harvest writes is in the closed portable vocabulary.

    The CHECK constraint would refuse anything else, so this is really an
    assertion that the harvest produces what it claims to: skills from the
    parsed resume, nothing invented, and no model call anywhere in the path.
    """
    async def _probe() -> list[tuple[str, str, str]]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await pe.harvest(
                        session,
                        candidate_id=world.candidate,
                        job_id=world.job_b,
                        tenant_id=world.tenant,
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    rows = (
                        await session.execute(
                            sa.text(
                                f"SELECT kind, subject, source_kind FROM {TABLE} "
                                " WHERE candidate_id = :cid ORDER BY subject"
                            ),
                            {"cid": str(world.candidate)},
                        )
                    ).all()
        return [tuple(row) for row in rows]

    rows = _run(_probe())
    assert rows
    assert {row[0] for row in rows} <= pe.PORTABLE_KINDS
    assert {row[2] for row in rows} <= pe.SOURCE_KINDS
    assert {row[1] for row in rows} == {"Kafka", "Postgres"}


def test_a_portable_criterion_is_citable_through_siddhi(world: World):
    """A criterion established by the Portable layer can be CITED, not just used.

    `Section.render` has no bypass, so a statement about a portable-established
    criterion either carries a ref in the evaluation's known set or the report
    does not exist. The portable node joins the item's grounding rather than
    replacing it, so a criterion with both kinds cites both and a reader can
    tell which is which.
    """
    from app.services.siddhi import citations
    from app.services.siddhi.evidence import (
        KIND_PORTABLE,
        EvidenceIndex,
        portable_node,
    )

    index = EvidenceIndex.build(items=["Kafka", "Postgres"])
    index = EvidenceIndex(nodes=index.nodes + (portable_node("Kafka"),))

    refs = index.grounding("Kafka")
    assert refs == (f"{KIND_PORTABLE}:kafka",)
    # The node is IN the index, so it is in the persisted trail too: a ref that
    # is citable but absent from the index is a ref the trail cannot explain.
    assert f"{KIND_PORTABLE}:kafka" in index.refs

    report = citations.Report(known_refs=index.refs)
    section = report.section("must_have", "Must-have")
    section.add(citations.Statement(citations.KIND_GRADE, "Kafka: Matching", refs))
    assert report.render()[0]["statements"][0]["evidence_refs"] == list(refs)

    # An uncited statement about the same criterion is still refused: nothing
    # about the portable layer widens the chokepoint.
    bad = citations.Report(known_refs=index.refs)
    bad.section("must_have", "Must-have").add(
        citations.Statement(citations.KIND_GRADE, "Kafka: Matching", ())
    )
    with pytest.raises(citations.UncitedStatement):
        bad.render()


def test_an_answer_node_and_a_portable_node_both_travel(world: World):
    """Both provenances are returned, rather than one winning.

    A criterion the Portable layer established still produced a recorded
    exchange, so both nodes exist and both are true. A reader auditing the
    grade is entitled to see that the answer was the platform restating what it
    held, which is only visible if the portable ref travels beside it.
    """
    from app.services.siddhi.evidence import (
        KIND_ANSWER,
        KIND_PORTABLE,
        EvidenceIndex,
        portable_node,
    )

    index = EvidenceIndex.build(
        items=["Kafka"],
        exchanges={"Kafka": [{"question": "Tell us about Kafka.", "answer": "Established from this candidate's portable record."}]},
    )
    index = EvidenceIndex(nodes=index.nodes + (portable_node("Kafka"),))
    refs = index.grounding("Kafka")
    assert any(ref.startswith(f"{KIND_ANSWER}:") for ref in refs)
    assert f"{KIND_PORTABLE}:kafka" in refs


# ── The wiring: question generation and the report's citations ───────────────


def test_an_established_criterion_keeps_its_row_in_the_matrix():
    """STRUCTURAL: the only thing that can remove a question row is the ceiling.

    "Insufficient evidence is not negative evidence" has a second direction
    that is easier to break: a criterion the portable record covered must not
    be DROPPED, because a dropped criterion is one nobody grades, charts or
    remarks on, and its absence from the report would read as the candidate
    having nothing to say about it.

    Asserted over the source rather than over an outcome, because the failure
    is a future edit to the filter: `if slot.index not in trimmed` growing an
    `and slot.index not in prefills` would pass every behavioural test written
    today and silently shorten every report afterwards.
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "services" / "assessment_questions" / "generate.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    comprehensions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ListComp)
        and isinstance(node.elt, ast.Call)
        and isinstance(node.elt.func, ast.Name)
        and node.elt.func.id == "CandidateQuestion"
    ]
    assert len(comprehensions) == 1, "more than one place builds the question rows"
    conditions = {
        name.id
        for generator in comprehensions[0].generators
        for condition in generator.ifs
        for name in ast.walk(condition)
        if isinstance(name, ast.Name)
    }
    assert "trimmed" in conditions
    # Neither the resume pre-fills nor the portable coverage may filter a row
    # out. They decide what a row SAYS, never whether it exists.
    assert "prefills" not in conditions
    assert "covered" not in conditions
    assert "coverage" not in conditions


def test_the_report_nodes_come_from_the_question_rows(world: World):
    """The citation is derived from what was WRITTEN, not recomputed from today.

    A report is a permanent record of what it was written from, so the node
    has to come from `candidate_questions.prefill_source`. Re-running the
    coverage rule at synthesis would answer a question about the record as it
    stands now, and a portable fact could have been added or retired since.
    """
    from app.services import functional_assessment as fa
    from app.services import resume_prefill as rp
    from app.services.siddhi.evidence import KIND_PORTABLE

    class _Competency:
        def __init__(self, name: str) -> None:
            self.id = uuid.uuid4()
            self.name = name

    class _Question:
        def __init__(self, competency_id, source) -> None:
            self.competency_id = competency_id
            self.prefill_source = source

    kafka = _Competency("Kafka")
    postgres = _Competency("Postgres")
    asked = _Competency("Stakeholder management")
    state = {
        "competencies": [kafka, postgres, asked],
        "candidate_questions": [
            _Question(kafka.id, rp.PREFILL_SOURCE_PORTABLE),
            # A second question on the SAME criterion: one node per item, not
            # one per question, because an item is what a statement cites.
            _Question(kafka.id, rp.PREFILL_SOURCE_PORTABLE),
            _Question(postgres.id, rp.PREFILL_SOURCE_RESUME),
            _Question(asked.id, None),
        ],
    }
    nodes = fa.portable_evidence_nodes(state)  # type: ignore[arg-type]
    assert [node.ref for node in nodes] == [f"{KIND_PORTABLE}:kafka"]
    assert all(node.kind == KIND_PORTABLE for node in nodes)
    # A resume pre-fill is NOT a portable node. The two provenances are
    # different claims and a reader with one ref kind could not tell them
    # apart.
    assert not any(node.item == "Postgres" for node in nodes)


def test_ppi_computes_the_same_split_the_candidate_was_told(world: World):
    """Question generation and the apply-time sentence agree, by construction.

    Both call `portable_evidence.coverage` with the same window, so a candidate
    told at apply time that their Kafka evidence is established is not then
    asked about Kafka. They are separate call sites because they run at
    different moments against a matrix that may have been edited in between,
    which is why the rule lives in one function rather than being inlined at
    either.
    """
    from app.models.candidate import JobCandidateLink
    from app.models.job import Job

    async def _probe() -> tuple[tuple[str, ...], tuple[str, ...]]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    link_id = uuid.uuid4()
                    await session.execute(
                        sa.text(
                            "INSERT INTO job_candidate_links (id, tenant_id, "
                            " job_id, candidate_id, source) "
                            "VALUES (:id, :tid, :jid, :cid, 'fresh')"
                        ),
                        {
                            "id": str(link_id),
                            "tid": str(world.tenant),
                            "jid": str(world.job_b),
                            "cid": str(world.candidate),
                        },
                    )
            async with session.begin():
                async with superadmin_scope(session):
                    job = await session.get(Job, world.job_b)
                    link = await session.get(JobCandidateLink, link_id)
                    from_ppi = await question_generation.portable_coverage(session, job, link)
                    from_retake = await retake.load_coverage(
                        session, world.candidate, world.job_b
                    )
        return from_ppi.established_names, from_retake.established_names

    from_ppi, from_retake = _run(_probe())
    assert from_ppi == ("Kafka",)
    assert from_ppi == from_retake
