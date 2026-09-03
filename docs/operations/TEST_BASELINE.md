# Test baseline, spec-doc6 Phase 1

The number every later claim in this phase is measured against (§3.1). Recorded
on 2026-08-29 against branch `feat/specdoc6-activation`.

## Before

Backend suite, `python -m pytest -q` from `backend/`, no containerised stack,
`DATABASE_URL` at its `app/core/config.py` default of `localhost:5432`.

| | |
|---|---|
| Passed | 2163 |
| **Failed** | **2** |
| Skipped | 80 |
| Errored | 0 |
| Wall time | 142.4 s (148 s including process start) |
| Exit | 1 |

The two failures were both in `tests/test_db_enum_parity.py`, and both raised
`asyncpg.exceptions.InvalidPasswordError` rather than an assertion. Of the 80
skips, 71 said `no database reachable` and 9 said
`could not import 'moto'`.

Note on `moto`: it is declared in `backend/requirements.txt` and was simply not
installed in the host interpreter. Installing the declared requirements is not a
change to the repository, so the 9 storage tests would have run in a correctly
provisioned environment. They are counted in the before column because that is
what the machine produced.

## After

Backend suite against `docker-compose.test.yml`, via `./scripts/test.sh unit`.

| | |
|---|---|
| Passed | 3247 |
| **Failed** | **0** |
| **Skipped** | **1** |
| Errored | 0 |
| Wall time | 164.3 s of pytest; **189 s wall for the whole command**, from `docker compose down -v` through stack start, database recreate, `alembic upgrade head`, the suite, and teardown |
| Exit | 0 |

The single remaining skip is
`tests/test_ai_reach_semantic.py::test_real_embedding_model_ranks_known_catalogue`,
category `live-credential-required`: it needs `VOYAGE_CONTEXT_4`, which this
D6 states is unavailable this phase. It is declared in `docs/operations/SKIPS.md` and pinned
by `backend/tests/test_skip_inventory.py`.

The passed count moved run to run (2255, 2847, 3021, 3033, 3196, 3238, 3247) because
seven agents were adding tests to this worktree throughout. The stable,
comparable figures are the **failed** and **skipped** columns.

The before and after passed counts are therefore not comparable to each other.
What is comparable: **80 skips became 1**, and **2 failures became 0**, and the
71 tests that had never executed a single database statement now execute them
against a real PostgreSQL 16 with pgvector, a real Redis 7.2 configured
`noeviction`, and a real S3 server.

## What the environment gap was actually hiding

The spec attributed the gap to Docker being unavailable. It was not.

**A native Windows `postgresql-x64-13` service was listening on `0.0.0.0:5432`.**
Docker's published port bound alongside it (`netstat` shows two LISTENING
sockets on 5432, PIDs 9628 and 24096) and lost, so every host-side connection
reached PostgreSQL 13 with a password nobody had. Inside the container the same
credentials worked. Nothing in the repository was misconfigured; the port was
taken. The fix is the test stack binding Postgres to **55432**, Redis to
**6381** and MinIO to **9101**.

## Two real defects were behind the gap

### 1. The migration chain could not be walked at all

`backend/alembic/versions/0058_single_embedding_space.py` declared

```python
down_revision = "0057_report_needs_human_review"
```

which is the FILENAME of migration 0057. Its revision id is
`0057_report_review`. Alembic builds its revision map before it opens a
connection, so `alembic upgrade head` died with

```
KeyError: '0057_report_needs_human_review'
```

having executed nothing. **No fresh database could be created**: not in CI, not
on a new machine, and not by `scripts/run-migration.sh` against RDS.

Fixed towards the revision id rather than by renaming 0057, because the running
database is stamped `0057_report_review` and a rename would orphan it.

Pinned by
`tests/test_db_enum_parity.py::test_the_migration_chain_resolves_end_to_end`,
which uses no database on purpose: the environment where the chain is broken is
exactly the environment where nothing else in that module can run. Verified to
fail when the defect is reintroduced.

### 2. Every resume upload was refused by PostgreSQL

`0046_private_gcs_resumes` created

```sql
CHECK (resume_storage_provider IN ('cloudinary', 'gcs'))
```

The AWS move changed `app/services/resume_storage.STORAGE_PROVIDER` from `"gcs"`
to `"s3"` and rewrote the transport. **No migration widened the vocabulary.**

Every resume upload writes that column, so on a database migrated to head the
INSERT into `profiles` is refused:

```
asyncpg.exceptions.CheckViolationError: new row for relation "profiles"
violates check constraint "ck_profiles_resume_storage_provider"
```

which surfaces as a 500 on `POST /jobs/{id}/apply`, on the candidate's My
Profile resume replacement, and on the databank bulk upload. That is the whole
apply flow, for every candidate, on every tenant.

Ten tests already covered it. All ten were reporting SKIPPED with
`no database reachable`.

`test_db_enum_parity.py` was written to catch exactly this class of drift and
did not, because it walked columns whose SQLAlchemy type is `Enum` and
`resume_storage_provider` is a plain `String(20)` with a `CHECK ... IN`.

Fixed by migration `0061b_resume_storage_provider_s3.py`, additively:
`cloudinary` and `gcs` stay, because rows carry them and
`object_storage.is_legacy_uri` reads them to tell an un-migrated object apart
from a missing one.

Pinned by two new tests, both verified to fail when the migration is rolled back:

* `test_every_storage_provider_the_code_writes_is_accepted` reads the expected
  set OUT OF `services.resume_storage`, so changing the constant again without a
  migration fails here rather than in production.
* `test_a_row_carrying_the_current_provider_can_actually_be_stored` performs the
  INSERT and rolls back, because reading a constraint definition is not the same
  as writing a row.

## Determinism

spec-doc6 §11.2 asks for three consecutive full runs with any non-deterministic
test fixed or deleted. Seven agents were writing to this worktree throughout, and
the suite grew by roughly a thousand tests across the runs, so a full-suite
three-run comparison cannot separate flakiness from other people's commits.
Stated exactly, this is what was done:

* **Full suite, six runs over the phase.** Passed counts moved every time
  (2255, 2847, 3021, 3033, 3196, 3238, 3247) because tests were being added. The
  **skip set was byte-identical on every run**: one row,
  `test_real_embedding_model_ranks_known_catalogue`. The last two full runs both
  reported zero failures.
* **Integration subset, three consecutive runs.** `./scripts/test.sh integration`
  selects every file reaching for `create_async_engine`, `object_storage` or
  `S3_TEST_ENDPOINT_URL` (18 files at the start of the phase, 20 by the end),
  which is where all of this lane's time, ordering, filesystem, concurrency and
  content-hashing surface lives. Runs 1 and 2 were **identical**: 236 passed,
  1 failed, the same named test. Run 3 read 250 passed, 2 failed, because 14
  tests had been added to the selected files between run 2 and run 3.
* **No test in this lane was observed to be non-deterministic**, so nothing was
  deleted or quarantined for flakiness.

Every failure seen in any of those runs was in a file authored by another agent
during the phase (`test_company_dna_api.py`, `test_rbac_conformance.py`,
`test_stage_enum_separation.py`, `test_staff.py`, `test_eval_trajectory.py`),
and each named the same test on repeat, so they were in-flight defects rather
than flakes. Every one of them was gone by the final full run.

## Resolved during the phase, and worth keeping on the record

`tests/test_rls.py::test_bypass_write_respects_append_only_audit_grant` asserts
that the append-only `audit_log` grant survives `superadmin_scope`, proving the
bypass is a scoped RLS escape hatch and not a return to superuser power. It has
never actually run before this phase, because it skipped with
`no database reachable`.

It fails. The cause is a role membership:

```
GRANT readypick_test TO pickready_app   (inherit_option = t)
```

`pickready_app` therefore inherits every table privilege of the owning role,
including the `UPDATE` and `DELETE` on `audit_log` that `0001_initial` and
`0014_job_grade_and_grants` explicitly REVOKE from it.
`has_table_privilege('pickready_app','audit_log','UPDATE')` returns true, and
`REVOKE readypick_test FROM pickready_app` makes it false and the test pass.

The membership does not exist immediately after `docker compose up`, and is not
created by `alembic upgrade head`, by `tests/test_rls.py` alone, or by
`tests/test_company_dna_api.py` alone (all four checked directly against
`pg_auth_members`). It appears during a full-suite run, so some test in the
session creates the role in a context that grants it membership of the creating
role. The dev database at `infra/docker-compose.yml` has no such membership.

**Current status: it passes.** On a cluster created fresh by
`docker compose down -v && up`, the membership does not exist
(`pg_auth_members` is empty of non-system rows) and all three `test_rls.py`
tests pass, including this one, across the final full runs.

It is recorded anyway, with the evidence, for two reasons. The membership was
observed, reproducibly, on a cluster that had run the full suite, and whatever
created it was not identified. And if that membership ever exists in a deployed
cluster, the append-only audit guarantee is void while every test still reports
green, which is the precise failure shape this whole phase exists to remove. The
test is correct; it is the only thing that would notice.
