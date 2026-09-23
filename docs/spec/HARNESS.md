# The Vivekium harness, RPN-HARNESS-001

A harness is not a test suite. The suite answers "does this function do what its
author believed". The harness answers six questions the suite structurally
cannot:

1. Does the system work end to end, against its real seams?
2. Does it still work when its environment behaves badly?
3. Can a failure be reproduced?
4. Can we say WHY it failed, without adding print statements?
5. Can we measure whether a change made it better or worse?
6. Can we prove recovery, rather than assume it?

**This document is the contract.** Anything built under `backend/harness/`
conforms to it, or changes it here first.

## 0. What already exists, and is therefore NOT rebuilt

The most common way to get this wrong is to build a second version of something
this repository already has. Rule 5, one implementation per concept, applies to
the harness exactly as it applies to the product.

| Concern | Already exists | The harness does |
|---|---|---|
| Metrics arithmetic (MCC, kappa, nDCG, precision@k) | `app/evaluation/metrics.py` | imports it |
| Golden retrieval set and its versioning | `app/evaluation/golden.py`, `datasets/` | reads it |
| Release thresholds derived from a measured probe | `app/evaluation/release_gate.py` | WIRES IT IN, since nothing did |
| Known defect registry | `app/evaluation/regression.py` | reads it as a scenario source |
| Report envelope that refuses a bare float | `app/evaluation/reporting.py` | emits through it |
| Background work that accepts and records rather than running | `app/workers/dispatch.py` backend `record` | asserts over `recorded()` |
| Vendor response contracts, hand authored, never recorded | `tests/fixtures/vendor/`, `services/reliability/vendor_contract.py` | serves them through the fault layer |
| Per run agent trace with a stage allowlist | `services/observability/trace.py` | captures it into the trajectory |
| Append only event streams | `telemetry_events`, `audit_log` | reads them as state evidence |
| Deterministic synthetic world (stable hash, never `random`) | `app/scripts/seed_mock_data.py` | calls it as a fixture |

## 1. Isolation, which is the first requirement and not the last

The harness NEVER touches a deployed environment. It runs against the
`docker-compose.test.yml` stack: Postgres on 55432 with a tmpfs data directory,
Redis on 6381 with `noeviction`, MinIO on 9101. The same stack `scripts/test.sh`
uses, for the same reason.

Three structural guarantees:

- **`backend/harness/` is never imported by `backend/app/`.** Asserted by an AST
  sweep in `tests/test_harness_isolation.py`, the same shape as
  `test_judge_isolation.py`. A harness that production code can reach is a
  harness that can change production behaviour.
- **`TASK_DISPATCH_BACKEND` is `record`**, which `dispatch.backend()` already
  refuses outright in production. A scenario asserts over what WOULD have been
  dispatched.
- **No model credential is set.** Every generative path has a deterministic
  fallback, and a scenario that needs a model response gets it from the fault
  layer serving a vendor contract fixture, never from a vendor.

## 2. A scenario

Declarative, versioned, composable, machine evaluable. YAML under
`backend/harness/scenarios/`, loaded into a frozen dataclass.

The shipped example, abridged from
`scenarios/integration_assessment_bills_once.yaml`:

```yaml
id: integration.an_assessment_bills_once_and_scores_once
version: 1
tier: integration      # smoke | regression | integration | adversarial | safety | performance
description: >
  A candidate answers every question, the conversation completes, exactly one
  credit is charged and scoring is dispatched exactly once, INCLUDING when the
  completion request is retried.
tags: [assessment, billing, idempotency, dispatch]

given:                 # initial state, built by a NAMED world builder
  world: assessment_in_progress
  overrides:
    questions.count: 2

workload:
  - answer_every_question
  - answer_the_last_question_again

faults: []             # see section 4

expect:                # ground truth, section 3
  state:
    - assessment_conversations.status == completed
    - credit_ledger.entries_for_link == 1
    - credit_ledger.subunits_total == 540
  dispatched:
    pickready.run_functional_assessment: 1
    pickready.index_document: 1
  prohibited:
    - a_number_reached_a_client
  evaluators:
    - functional_correctness
    - state_correctness
    - safety_and_policy
```

Two details in that block are worth reading twice. `dispatched` is a MAPPING of
task name to an EXACT count, because "at least once" is what every dispatcher
gives for free and asserting it proves nothing; the counts that matter are the
ones a retry would change. And `subunits_total` is the tenant's BALANCE rather
than the charge: ten credits granted is six hundred sub-units, one completed
non-STEM assessment costs sixty, so 540 is the statement that exactly one charge
landed. A probe that read a cached total would agree with the cache rather than
with the ledger, which is why it sums.

Rules:

- **A scenario names a world builder, it does not inline SQL.** Builders live in
  `harness/world.py` and are shared. A scenario that builds its own state is a
  scenario nobody can compose.
- **`expect` is ground truth and is mandatory.** A scenario without it merely
  executes.
- **`tier` decides where it runs.** Section 9.

## 3. Ground truth

The harness must know what success MEANS, not merely that nothing raised. At
least one of these is required per scenario:

- **State assertions**, evaluated against the database FROM A SECOND CONNECTION
  after the request completes. This is not stylistic. A write that answered 200
  and then rolled back at commit is invisible to an assertion on the response
  body, and this repository has shipped exactly that bug: the post flush
  `audit_log` rollback, 2026-09-20.
- **Dispatch assertions**, over `dispatch.recorded()`.
- **Output assertions**, over the serialized payload a client would receive.
- **Trajectory assertions**, over the ordered stages actually reached.
- **Prohibited outcomes**, which are as load bearing as required ones. A
  scenario may assert that a number never reached a client, that a tenant
  boundary was never crossed, that no automatic rejection occurred.

## 4. Fault injection

Faults are injected at the REAL SEAMS the product already has, never by editing
product code for the harness's benefit. Every fault is a context manager,
reversible, and recorded in the run manifest so a replay reproduces it.

| Fault | Seam |
|---|---|
| model 429 / 500 / 503 / 401 / timeout / malformed / partial | `httpx.MockTransport` on the `llm_router` client, serving `tests/fixtures/vendor/` |
| embedding unavailable | the same, on the Voyage client |
| Redis down or slow | the `core/cache` client factory |
| object store 500 or missing key | the `object_storage` client |
| dispatch failure | the `record` backend, raising instead of recording |
| database slow or connection dropped | a wrapper on the session factory |
| clock movement | `harness/doubles/clock.py`, one implementation replacing the two hand rolled `FakeClock` classes |

**A fault must be observable in the result.** The point is never that the system
survived. It is that the system DEGRADED THE WAY IT SAID IT WOULD. A scenario
that injects a model outage asserts the deterministic fallback ran AND that the
run recorded `degraded=True`. Silent survival is a finding, not a pass.

## 5. Run identity and provenance

Every execution mints a `RunRecord`: `run_id`, UTC timestamp, git SHA, branch,
dirty flag, scenario id and version, tier, resolved configuration, dataset
versions, dispatch backend, and the outcome of every evaluator.

Artifacts land under `harness-runs/<run_id>/`. Five files, and this list is
what is actually written rather than what was first sketched:

| File | What it holds |
|---|---|
| `manifest.json` | The `RunRecord`: everything replay needs, written at `begin` so a crashed run still leaves evidence |
| `trajectory.json` | Every request and its response, the ordered stages, the facts the workload kept, the degradations, and `aborted` when a step could not continue |
| `side_effects.json` | What `dispatch.recorded()` holds, and the world's ids |
| `report.json`, `report.md` | The evaluation report, machine and human |
| `comparison.json` | Written by `compare` and by `gate`, when one ran |

**There is no captured-log file, and this paragraph used to promise one.** The
trajectory carries the request, the status, the body and the timing of every
call, which is what a log was wanted for; a second copy of the application's
own `structlog` output would be the largest artifact in the directory and the
one nobody reads. Reinstating it is a real option and would be a change here
first.

Retention is bounded, `--keep-last N` defaulting to 20, because an artifact
store that grows without limit is one somebody eventually deletes wholesale. A
promoted baseline is never pruned and does not consume one of the N slots.

## 6. Replay

`harness replay <run_id>` re-executes from the manifest: same scenario version,
same world, same faults, same clock, same seed. Replay is what makes a failure a
bug report rather than an anecdote.

Replay is honest about its limit. It reproduces everything the harness controls.
It cannot reproduce a genuine vendor response, which is precisely why the fault
layer serves contract fixtures rather than recordings.

## 7. Evaluators

Layered, independent, deterministic wherever deterministic is possible. A single
pass/fail number hides which dimension moved.

`functional_correctness`, `state_correctness`, `tenancy_isolation`,
`no_numbers_to_client`, `safety_and_policy`, `degradation_honesty`, `recovery`,
`latency`, `cost`.

**A model based evaluator is used only where deterministic evaluation cannot
answer the question**, and never for something verifiable directly. This follows
the existing rule that success criteria are deterministic code, because the
moment the guard matters most is the moment the provider is down.

**An evaluator that cannot compute reports `unavailable`, never `0.0`.**
`app/evaluation/metrics.py` already raises `InsufficientData` for this reason and
`tests/test_unavailable_not_zero.py` already enforces it.

## 8. Baselines and regression

`harness baseline promote <run_id>` records a run as the reference for its tier.
`harness compare <run_id>` diffs against it and fails on a regression crossing an
explicit threshold. Thresholds are DATA in `harness/thresholds.yaml`, never
literals, and each carries its reason.

A ratio bound may carry a `min_baseline`, below which it is NOT applied. That
was added after the gate fired on a one-second scenario that took 21 seconds on
a loaded machine: correct by its own arithmetic, wrong about the code, and a
gate that fires on noise is one somebody disables. The floor is data beside the
ratio and carries its own reason, like everything else in that file.

`app/evaluation/release_gate.py` is the gate for anything it already covers, and
`UNAVAILABLE` blocks rather than passes, which is its existing and correct
behaviour. **It had no caller at all outside its own test until now.** It is
wired in twice, deliberately, because the two answer different questions: the
`release_gate` EVALUATOR
(`harness/evaluators/release.py`) puts three results whose answers are known by
construction through the real gate and fails when its decision rule changes,
and the scenario
`safety.the_release_gate_blocks_what_it_cannot_measure` is what makes that
evaluator run on every commit. The load-bearing one is the first probe: a
result carrying no chance-corrected agreement must BLOCK, not pass and not
score zero. The evaluator does NOT run a jury, because the human-labelled sets
are empty by design and a deterministic assertion that depended on a live model
would move for reasons other than a code change.

## 9. Tiers and CI

| Tier | Runs | Budget |
|---|---|---|
| `smoke` | every commit | under 60s |
| `regression` | every commit | under 5 min |
| `integration` | every commit, the stack is already there | under 10 min |
| `adversarial`, `safety` | every commit | under 3 min |
| `performance` | manual and nightly | unbounded |

CI fails on a regression against the promoted baseline for the tier, and on any
safety or adversarial scenario failing outright.

**THREE EXIT CODES, AND CI RESPECTS ALL THREE.** 0 pass, 1 a scenario failed or
a run regressed, 3 UNAVAILABLE. The `harness` job in
`.github/workflows/deploy.yml` runs each tier as its own step and every step
fails on ANY non-zero status, which is what makes 3 a failure. That is the whole
reason the third code exists: an unavailable result means the thing could not be
measured, and treating it as success is the green-while-broken outcome this
document opens by describing. It is also what `release_gate.py` already does for
its own `UNAVAILABLE`, one layer in.

**THE CORPUS IS VALIDATED BEFORE ANY OF IT RUNS.**
`tests/test_harness_scenarios.py` resolves every world, workload step, fault,
evaluator and probe a scenario names against its registry, with no stack behind
it, and refuses a safety scenario that names no prohibited outcome or a fault
scenario that judges neither degradation nor recovery. A misspelled step name is
otherwise a run that executes less than it claimed and finds out after a
database, a migration and several minutes have been spent.

**`performance` runs only on a `workflow_dispatch`.** Its regression signal is
the baseline comparison of `duration_seconds`, not a ceiling inside a scenario:
section 9 gives the tier no agreed budget, the `latency` evaluator reports
`unavailable` for exactly that reason, and a performance scenario naming it
would block on a number nobody agreed to.

### What the harness found while it was being built

Recorded here rather than in a commit message, because each one is a property
somebody will otherwise re-discover.

- **A truncated model response was ACCEPTED as an answer. FIXED 2026-09-24.**
  `finish_reason: "length"` was outside `REFUSAL_FINISH_REASONS`, so
  `llm_router` returned the cut text and nothing marked it as cut off. The
  finding was pinned by
  `adversarial.a_truncated_model_response_is_accepted_as_an_answer` so it could
  only change deliberately, and it did: truncation is now its own failure class
  (`FAILURE_TRUNCATED`), retried once at double the completion budget (priced
  against the cost ceiling first) and then raised as `ResponseTruncated`. The
  scenario was flipped rather than deleted, and is now
  `adversarial.a_truncated_model_response_is_refused`: the fault cuts every
  call, so the call must end refused with the degradation recorded.
- **A vendor fault has to supply a credential to reach the transport.**
  `key_for_model` returns None when the key is unset and the router refuses
  before it builds a request, so with no credential a `model_failure` fault
  intercepts nothing and the scenario measures the no-credential refusal
  instead. `faults._supply_credentials` installs an obviously-fake value for the
  duration, AFTER the routing transport is installed and removed BEFORE it, and
  the `a_model_credential_was_configured` prohibited outcome is evaluated in the
  judging phase, which is what proves it was removed.
- **A route that RAISES must be `fail`, never `unavailable`.** `TestClient`
  re-raises a server exception, so no observation is recorded and an output
  probe finds nothing to read, which arrives as the same `ProbeError` a
  malformed assertion produces. Measured by reintroducing the 2026-09-20
  soft-delete defect in memory: the run reported `unavailable` with
  `UniqueViolationError` visible in the trajectory and nowhere in the verdict.
  `ctx.aborted` is what separates the two, and the runner now records the abort
  as a failing check of its own.
- **An unreachable database must be `unavailable`, never a traceback.** asyncpg
  raises at CONNECT time, before SQLAlchemy has anything to wrap, so catching
  `DBAPIError` alone let a missing database exit 1 with a stack trace, which in
  CI is indistinguishable from a product regression.

## 10. Operating it

```
harness run                    # every tier except performance
harness run --tier safety
harness run --scenario integration.an_assessment_bills_once_and_scores_once
harness replay <run_id>
harness compare <run_id>
harness baseline promote <run_id>
harness gate                   # CI: compare every tier that ran, or set a baseline
harness list
```

`gate` is the seventh command and was not in the first draft of this section.
It exists because the registry holds ONE baseline per tier, naming the scenario
it was taken from, so comparing an arbitrary run of that tier against it would
diff `duration_seconds` between two different scenarios and report which
scenario ran last as a regression. `gate` finds the newest run of the scenario
the baseline names, compares that, and establishes a baseline for a tier that
has none rather than skipping it: `compare` answers `unavailable` in that state,
correctly, and a step that swallowed it would be a gate reading green because
nobody had ever given it anything to read.

Implemented as `python -m harness`, with `scripts/harness.sh` as the shell entry
point. This mirrors the `Makefile` and `scripts/test.sh` split, for the same
stated reason: `make` is absent on the Windows workstation this is developed on,
and a capability that exists only behind a tool half the team lacks is not a
capability.
