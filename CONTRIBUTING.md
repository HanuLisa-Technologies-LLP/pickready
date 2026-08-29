# Contributing to ReadyPick

## One command, from a clean clone, to a green suite

```
git clone <repo> && cd pickready
./scripts/test.sh
```

That is the whole prerequisite list: Docker, Python 3.12 or newer, and
`pip install -r backend/requirements.txt`. The script starts the containerised
test stack, recreates and migrates the test database, runs the backend suite, and
tears the stack down again.

### The three entry points

| Command | Equivalent | What it runs |
|---|---|---|
| `make test` | `./scripts/test.sh unit` | The full backend suite against real Postgres, Redis and S3. |
| `make test-integration` | `./scripts/test.sh integration` | Only the tests that reach for `create_async_engine`, `object_storage` or `S3_TEST_ENDPOINT_URL`. About 216 tests in 18 files, roughly a minute. |
| `make test-all` | `./scripts/test.sh all` | Backend suite, the two agent evaluation gates, and the frontend suite. |

Useful flags, passed to `scripts/test.sh`:

* `--keep` leaves the stack running afterwards. The next run reuses it and still
  recreates the database, so repeated runs stay clean and start in about ten
  seconds instead of forty.
* `--no-up` assumes the stack is already running.
* everything after a bare `--` goes straight to pytest:
  `./scripts/test.sh unit --keep -- -k resume -x`

### `make` is not required, and on this project it is often not present

Both files are real. `scripts/test.sh` holds the logic; the `Makefile` targets
are one line each and call it. `make` is absent from the Git Bash environment on
the Windows workstation this was developed on, which is why the capability does
not live inside a Makefile recipe. If `make` is missing, use the script directly
and nothing is lost.

## The test stack

`docker-compose.test.yml`, project name `readypick-test`. Three services, and
their versions track `infra/` rather than being chosen:

| Service | Image | Host port | Tracks |
|---|---|---|---|
| Postgres + pgvector | `pgvector/pgvector:pg16` | **55432** | `infra/modules/rds/variables.tf`: `engine_version = "16"`, `parameter_group_family = "postgres16"`. RDS pins the major only and lets the minor move under `auto_minor_version_upgrade`, so the major is the only thing there is to match. |
| Redis | `redis:7.2-alpine` | **6381** | `infra/modules/elasticache/variables.tf`: `engine_version = "7.1"`, family `redis7`. There is no `redis:7.1` image; see below. |
| MinIO (S3) | `minio/minio:RELEASE.2025-04-22T22-12-26Z` | **9101** API, **9102** console | The `s3` module. `app/core/config.py` already declares `s3_endpoint_url` for exactly this. |

### Why those ports and not the defaults

On the workstation this was written on, a native Windows `postgresql-x64-13`
service was listening on `0.0.0.0:5432`. Docker's published port bound alongside
it and lost, so every host-side connection reached PostgreSQL 13 with a password
nobody had, and 71 integration tests answered `no database reachable` and
reported SKIPPED. The suite was green while `POST /jobs/{id}/apply` was refused
by a CHECK constraint for every candidate on every tenant. See
`docs/TEST_BASELINE.md`.

6380 is the local dev stack's Redis (`infra/docker-compose.yml`), so the test
stack takes 6381. The two stacks can run side by side.

### Two version notes worth knowing

**Redis 7.1 does not exist as an image.** ElastiCache's `7.1` is an
AWS-only version designation; the open-source line carrying its feature set
shipped as 7.2, and `docker manifest inspect redis:7.1-alpine` returns nothing.
The test stack runs `redis:7.2-alpine`, inside the same `redis7` parameter-group
family the module declares. This is a naming mismatch and not a defect, and it
is recorded here so the next person does not go looking for a 7.1 image.

**Redis is `noeviction`, not `allkeys-lru`.** It is the Celery broker, not a
cache, and the test stack carries production's semantics
(`infra/modules/elasticache/main.tf` sets the same). Under memory pressure the
LRU default silently evicts queued TASKS, and the symptom is work that was
accepted and never happened with nothing recording the drop.

### MinIO rather than LocalStack

The only AWS surface this codebase touches is S3 object operations:
`HeadObject`, `PutObject` with `IfNoneMatch: *`, `GetObject`,
`ServerSideEncryption`. MinIO is a real S3 server implementing all of them
(verified: a repeat conditional PUT answers `PreconditionFailed` 412, a missing
key answers 404, SSE-S3 round-trips), not an emulation of one, and it is roughly
a tenth of LocalStack's image for services nothing here calls.

MinIO answers `NotImplemented` for SSE-S3 unless a key source is configured, and
`object_storage.put_if_absent` sends `ServerSideEncryption=AES256` on every
write, so the compose file sets `MINIO_KMS_SECRET_KEY` to a fixed committed test
key. Without it the storage tests would have to drop the parameter, which would
mean asserting a call the application does not make. It protects nothing and
encrypts only tmpfs that is discarded at teardown.

### The storage tests run against MinIO, or against `moto`, and never silently

`tests/test_object_storage.py` targets the MinIO service when
`S3_TEST_ENDPOINT_URL` is set. `scripts/test.sh` sets it, so the canonical run
exercises a real S3 server. Unset, the tests use `moto` in-process, which is
what a bare `pytest tests/test_object_storage.py` gets.

If the variable is set and the endpoint is unreachable, the tests **fail** and
name the compose command. They never probe-and-skip. A storage suite that
quietly downgrades to a mock when the server is missing reports PASSED for a
code path nobody ran.

## Skips

`docs/SKIPS.md` is the declared inventory: one row per skip, with a category and
a reason. `backend/tests/test_skip_inventory.py` compares it against what the
session actually skipped and fails the build on any difference, naming the
specific test that appeared or disappeared.

There is exactly one declared skip, and it needs a vendor key that spec-doc6 D6
states is unavailable this phase.

If you add a skip, the build will tell you. Your options, in order of
preference: make the test run, delete it, or add a row with a category and a
reason somebody can act on. The category `unjustified` exists so a triage pass
can name one; a row left in it fails the build.

To regenerate the observed set:

```
RPN_SKIP_DUMP=/tmp/skips.md ./scripts/test.sh unit
```

## Migrations

`alembic upgrade head` must reach head from nothing. Two rules the tree has
already been bitten by:

* **`down_revision` names a REVISION ID, never a filename.** They differ often.
  A filename there raises `KeyError` before a single statement executes, so no
  fresh database can be created anywhere.
* **One head.** Two migrations authored against the same parent fork the
  history, and a rolling deploy applies one branch and leaves the other
  silently unrun. Rebase yours onto the current head before opening a PR.

`tests/test_db_enum_parity.py::test_the_migration_chain_resolves_end_to_end`
asserts both, and needs no database so it still runs in the environment where
the chain is broken.

## No API keys are needed, and none should be set

`ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` are deliberately unset by
`scripts/test.sh` and by CI. The suite must pass with no model credential at
all: every generative path has a deterministic fallback, and a key here would
let a vendor outage fail the build. Nothing in this repository may state or
imply that a live vendor call has succeeded.

## Standing rules

These are enforced, not aspirational. `backend/tests/test_platform_audit.py` and
`frontend/scripts/impeccable-gate.mjs` are where most of them land.

* No em dash (U+2014) in any string, in either language, in code or in seeded
  data. Build any matching character class from `chr(8212)` so a repo-wide sweep
  cannot rewrite the code that strips it.
* No placeholder markers and no apologetic hedging in shipped code. The exact
  banned token list lives in `CLAUDE.md` and in the CI grep that enforces it,
  deliberately not restated here: a document that spells them out is a document
  the grep flags, and an exception carved for it is an exception somebody else
  will use. The shape is the usual one, the three all-caps annotations plus any
  phrase promising that the real version comes later.
* No silent fallbacks. A broad handler whose body discards the error, and a
  bare handler with no exception type, are both refused. Fail loudly, and say
  what would fix it: every raise in this codebase is expected to name the thing
  that went wrong and, where there is one, the command that repairs it.
* A test whose only assertion is that a mock was called is not a test.
* Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`), one logical
  change per commit, each green on the full suite.

`CLAUDE.md` is the standing context for the whole project and takes precedence
over this file wherever they overlap.
