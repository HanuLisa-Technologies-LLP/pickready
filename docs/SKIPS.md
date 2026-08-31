# Skip inventory

spec-doc6 §3.3. One line per skip: the test, its category, and the reason.
`backend/tests/test_skip_inventory.py` reads the table below and fails the build
when the suite's actual skip set differs from it, naming the specific test that
appeared or disappeared rather than reporting a count that moved.

A skipped test is a guarantee that is not being enforced, and `SKIPPED` is one
word from `PASSED` in a summary line. That is not a hypothetical here: see the
baseline below, and `docs/TEST_BASELINE.md` for the numbers.

## Categories

| Category | Means |
|---|---|
| `live-credential-required` | Needs a real vendor key. Legitimate this phase: spec-doc6 D6 states no Anthropic or Voyage key is available, and nothing may be quietly satisfied with a fake. |
| `platform-specific` | The environment genuinely cannot run it (an OS, an absent tree). Must say which environment and why the canonical one is unaffected. |
| `deliberate-xfail-with-issue` | A known defect, tracked, expected to fail. Carries the issue reference. |
| `unjustified` | Everything else. Must be fixed or deleted, never recorded. `test_no_skip_is_left_categorised_unjustified` fails the build on any row left in this category. |

## Declared skip inventory

| Test | Category | Reason |
|---|---|---|
| `tests/test_ai_reach_semantic.py::test_real_embedding_model_ranks_known_catalogue` | live-credential-required | VOYAGE_CONTEXT_4 unset: semantic quality cannot be measured against the deterministic dev fallback |

## What the count was, and what closed it

The suite skipped **80 tests** at the start of this phase and skips **1** now.
Nothing in the list below was fixed by relaxing an assertion.

| Was skipped | Count | What it turned out to be | Now |
|---|---|---|---|
| `no database reachable` across 18 files | 71 | A native Windows `postgresql-x64-13` service was listening on `0.0.0.0:5432`. Docker's published port bound alongside it and lost, so every host-side connection reached PostgreSQL 13 with a password nobody had. Nothing was misconfigured in the repo; the port was simply taken. | Run against `docker-compose.test.yml`, which binds Postgres to **55432**. All 71 run. |
| `could not import 'moto'` in `test_object_storage.py` | 9 | `moto[s3]` is declared in `backend/requirements.txt` and was not installed. `pytest.importorskip` on a declared dependency turns an incomplete environment into a green run. | The fixture now targets the MinIO service in the test stack when `S3_TEST_ENDPOINT_URL` is set (the canonical run sets it) and `moto` otherwise. It never probes-and-skips: an endpoint that is set and unreachable fails and names the compose command. |

Two of those 71, in `test_db_enum_parity.py`, had no reachability guard and so
**failed** rather than skipping. Closing the environment gap exposed two real
defects behind them, both recorded in `docs/TEST_BASELINE.md`.

Three further skips appeared during this phase in `tests/test_rbac_conformance.py`
(`assign_roles` / `manage_staff` have no job-scoped route). They were
`unjustified` and are now assertions: the absence of a job-scoped route is
itself what makes the grant inert at the HTTP layer, so the row states that and
fails if such a route ever appears.

## Latent skip sites

Conditional `pytest.skip` calls that exist in the tree and do **not** fire in the
canonical environment. They are not in the inventory above because they do not
happen; they are listed so the next reader knows they are there and why they are
tolerated.

| Site | Fires when | Category | Assessment |
|---|---|---|---|
| `tests/test_prism_report.py:260` | `frontend/components/functional-skills-report.tsx` is absent from every parent directory. | `platform-specific` | The backend dev container mounts `backend/` alone, so the frontend tree is genuinely not there. CI and `scripts/test.sh` check out the whole repo, so it never fires in either. Its own docstring says so. |
| 18 `_factory_or_skip()` helpers | No database answers on `DATABASE_URL`. | `platform-specific` | Retained deliberately. With the test stack up they never fire. Without it they say which prerequisite is missing rather than producing 71 connection errors. `test_db_enum_parity.py` keeps NO such guard, on purpose: something has to fail loudly when the database is absent, or the gap that hid the two defects reopens. |

## Regenerating this file

```
RPN_SKIP_DUMP=/tmp/skips.md ./scripts/test.sh unit
```

writes the observed set as table rows in exactly the format above, with
`FILL-IN` where a category belongs. The build failure from a drifted inventory
prints the same rows, so the fix is always a copy and a category.
