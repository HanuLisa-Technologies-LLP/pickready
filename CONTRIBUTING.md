# Contributing to Vivekium

All documentation is indexed at [docs/README.md](docs/README.md). Before
changing code, read [claude.md](claude.md): it carries the rules a change must
not break, and its "Where to make a change" table is the fastest route to the
right file.

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
`docs/operations/TEST_BASELINE.md`.

6380 is the local dev stack's Redis (`infra/docker-compose.yml`), so the test
stack takes 6381. The two stacks can run side by side.

### Two version notes worth knowing

**Redis 7.1 does not exist as an image.** ElastiCache's `7.1` is an
AWS-only version designation; the open-source line carrying its feature set
shipped as 7.2, and `docker manifest inspect redis:7.1-alpine` returns nothing.
The test stack runs `redis:7.2-alpine`, inside the same `redis7` parameter-group
family the module declares. This is a naming mismatch and not a defect, and it
is recorded here so the next person does not go looking for a 7.1 image.

**Redis is `noeviction`, not `allkeys-lru`.** It carries a live assessment's
proctoring warning counter and the run-status record a recruiter is watching, not just a
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

## The harness

`docs/spec/HARNESS.md` is the contract; `backend/harness/` is the
implementation. It is not a second test suite. The suite answers "does this
function do what its author believed"; the harness answers whether the system
works end to end against its real seams, whether it degrades the way it said it
would, and whether a change made it better or worse.

### Running it

```
./scripts/harness.sh run --tier smoke      # brings the stack up and tears it down
./scripts/harness.sh run --no-up --keep run --tier safety
```

`scripts/harness.sh` is the shell entry point, for the same reason
`scripts/test.sh` is: `make` is absent on the Windows workstation this is
developed on, and a capability that exists only behind a tool half the team
lacks is not a capability.

**IT DROPS AND RECREATES `readypick_test`, exactly as `scripts/test.sh` does.**
That is the right default -- a run whose result depends on rows a previous run
left cannot reproduce, and reproduction is the entire point of section 6 -- and
it is the wrong thing to do to a stack somebody else is using. When the stack is
already up and already migrated, address the harness directly and it touches
nothing:

```
cd backend
export DATABASE_URL="postgresql+asyncpg://readypick_test:readypick_test@127.0.0.1:55432/readypick_test"
export REDIS_URL="redis://127.0.0.1:6381/0"
export JWT_SECRET="readypick-test-suite-signing-key-not-a-secret"
export TASK_DISPATCH_BACKEND=record
unset OPENAI_GPT_TERRA OPENAI_GPT_LUNA VOYAGE_CONTEXT_4

python -m harness run                      # every tier except performance
python -m harness run --tier regression
python -m harness run --scenario safety.a_cross_tenant_read_is_404_and_never_403
python -m harness list
```

The three unset variables are not an inconvenience, they are section 1's third
structural guarantee. Every generative path has a deterministic fallback, and a
scenario that needs a vendor response gets it from the fault layer serving a
hand-authored contract fixture. A key in your shell lets a vendor outage turn
the harness red and lets a scenario silently exercise a real vendor instead of
the contract it declared. `a_model_credential_was_configured` is a prohibited
outcome that several scenarios name, so it is checked rather than trusted.

### Reading the exit code

```
0   pass
1   a scenario failed, or a run regressed against its baseline
3   UNAVAILABLE: something could not be measured
```

**3 is a failure.** CI treats it as one and so should you. An unavailable
result means the harness could not answer the question: the stack is not there,
a scenario named an evaluator that does not exist, an assertion would not parse.
Treating that as a pass is the green-while-broken outcome the whole thing exists
to prevent, and it is the same rule `app/evaluation/release_gate.py` already
applies to its own `UNAVAILABLE`.

The two non-zero codes are kept apart because they need different people. A 1 is
a diff to read. A 3 is usually an environment to fix, and the report names which.

### Reproducing a failed run from its `run_id`

Every run writes `harness-runs/<run_id>/`: the manifest (the commit, the branch,
whether the tree was dirty, the scenario version, the world, the faults, the
seed, the dispatch backend), the trajectory (every request, its status, its body
and its timing, the stages reached, and `aborted` when a step could not
continue), the side effects, and the report in both Markdown and JSON. In CI the
whole directory is uploaded as a build artifact.

```
python -m harness replay <run_id>
```

Replay re-executes from the manifest: same scenario version, same world, same
faults, same seed. It REFUSES when the scenario file has moved to a new version
underneath it, and names the commit the original run recorded so you can check
that out and replay there. A scenario edited between the failure and the replay
would otherwise re-run as something else while every artifact still carried the
original's id, which is the one situation in which a replay actively misleads.

Replay is honest about its limit: it reproduces everything the harness controls
and cannot reproduce a genuine vendor response, which is precisely why the fault
layer serves contract fixtures rather than recordings.

### Adding a scenario

One YAML file under `backend/harness/scenarios/`, named for its id.

1. **Pick the tier.** It decides where the scenario runs and what CI fails on,
   so a typo would silently move a safety scenario out of the set CI fails
   outright on. The loader refuses an unknown one rather than defaulting it.
   The id is prefixed with its tier and a test enforces that, because
   `harness list` is unreadable at twenty rows without it.
2. **Name a world builder, do not inline SQL.** Builders live in
   `harness/world.py` and are shared; `given.overrides` is the escape hatch for
   a scenario that needs one field different. A scenario that builds its own
   state is a scenario nobody can compose.
3. **Name workload steps, do not inline requests.** Steps live in
   `harness/workload.py` and a step NEVER asserts: ground truth lives in the
   scenario, so a step that judged its own result would put the expectation in
   the same file as the thing being expected.
4. **Write `expect`, and it is mandatory.** The loader refuses a scenario
   without it, because a scenario that asserts nothing merely executes and
   reports a pass it never earned. Prefer a STATE assertion: it is read from a
   second connection after the response, which is the only way to tell a
   committed write from one that answered 200 and rolled back at commit. This
   repository has shipped exactly that bug.
5. **Say what must NOT happen.** `prohibited` is as load bearing as the rest.
   A safety scenario with no prohibited outcome is refused by
   `tests/test_harness_scenarios.py`, and `safety_and_policy` reports
   `unavailable` for one, because an absence nobody named is one nobody checked.
6. **Name only evaluators that can actually compute.** An evaluator that cannot
   answer reports `unavailable`, and one `unavailable` makes the whole run
   `unavailable`, which blocks. `latency` has no budget for the `performance`
   tier and will do exactly that; `degradation_honesty` and `recovery` report
   `unavailable` when no fault was injected.
7. **If you inject a fault, judge the degradation.** The point is never that the
   system survived, it is that it degraded the way it said it would. A scenario
   with a fault that names neither `degradation_honesty` nor `recovery` is
   refused by the corpus test.

Then run `pytest tests/test_harness_scenarios.py -q`, which resolves every name
you wrote against its registry in seconds and with no stack behind it. Finding a
misspelled probe there costs a red dot; finding it in a tier run costs a
database, a migration and twenty minutes of `unavailable`.

### Adding a probe, a step, a world or an evaluator

Each is a registry with one definition per name, and the argument is the same in
all four places: two scenarios asking the same question two slightly different
ways disagree silently, and the disagreement surfaces as a flaky harness rather
than as a contradiction anybody can see.

- **A state probe** goes in `harness/probes.py` and reads through the
  `StateReader`. There is deliberately no overload that takes the request's
  session, and that absence is the enforcement.
- **A prohibited outcome** goes in the same file and answers `(occurred,
  detail)`. The detail becomes the note on a pass as well as a failure, which is
  what keeps a passing prohibition from reading as a probe that did nothing.
- **A workload step** goes in `harness/workload.py`. Drive real routes; inject
  only WHO is calling.
- **A world builder** goes in `harness/world.py` and declares the relations it
  needs, so a database that has not been migrated far enough produces
  `unavailable` naming the missing table rather than whatever the first insert
  happened to raise.
- **An evaluator** goes in `harness/evaluators/builtin.py`, takes the whole run
  and answers ONE dimension. It must be deterministic wherever deterministic is
  possible, and it must report `unavailable` rather than `0.0` when it cannot
  compute. `base.py` gives you `passed`, `failed`, `cannot_compute`, `scored`
  and `verdict`; there is deliberately no helper that turns a missing
  measurement into a zero.

### The one structural rule

**`backend/harness/` is never imported by `backend/app/`.**
`tests/test_harness_isolation.py` asserts it by AST sweep, the same shape as
`test_judge_isolation.py`. A harness that production code can reach is a harness
that can change production behaviour.

### Baselines

```
python -m harness compare <run_id>
python -m harness baseline promote <run_id>
python -m harness gate            # what CI runs
```

Thresholds are DATA in `harness/thresholds.yaml` and each carries the reason it
is set where it is; the loader refuses one without a reason. A threshold typed
into a comparison function is a threshold that gets nudged during a red build
and never nudged back, with nothing in the diff saying what it was protecting.

## Skips

`docs/operations/SKIPS.md` is the declared inventory: one row per skip, with a category and
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
