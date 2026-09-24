# CLAUDE.md section draft: Phase 7 WP-B5 (the unreachable subsystems)

Draft for the orchestrator to fold into the release's single top section. Written
in the file's own voice; nothing here edits `claude.md` directly.

## SIX SUBSYSTEMS NOTHING COULD REACH ARE DELETED, AND THE TABLES STAY

`test_ai_reachability.py` had recorded `services/reasoning` and
`services/orchestration` as NOT_LIVE since 2026-09-09, and `services/memory` as
LIVE only through a task nobody dispatched and nothing scheduled. The agent
action ledger and the three-level degradation layer were reached by nothing but
their own tests and eval cases. Every unit test of all of them was green while
none could run in production, which is the exact state Part A was in for a
whole phase, so they were deleted rather than kept for later.

- **Deleted:** `services/reasoning` (planner, runner), `services/memory`
  (experience and the five layers), `services/orchestration` (coordinator,
  router, enforcement, activation, versioning), `services/agent_actions`,
  `services/reliability/degradation.py`, `scripts/eval_trajectory.py`, and
  `pickready.revoke_learnings_from_source`. The task had no schedule entry and
  no Terraform rule, so there was none to remove.
- **Kept, deliberately:** `services/coalescing` and the whole tool layer (the
  grading phase wires them through `tools.executor`), `reliability/budget.py`
  (live through `agents/envelope`), and the `agent_learnings`,
  `agent_actions` and `agent_execution_traces` TABLES, as history (S4).
- **`tests/test_unreachable_subsystems_removed.py` keeps them gone**, through the
  shared whitespace-normalised sweep. Files owned by packages that ran in
  parallel are PENDING_HAND_OFFS with a reason each, and a second test fails the
  moment an entry outlives the reference it excuses.

### `resolve_for_application` IS GONE, AND THE SKILLS SNAPSHOT IS THE ANSWER

**SUPERSEDES the 2026-09-23 paragraph "`orchestration/versioning.resolve_for_
application` HAS NO PRODUCTION CALLER"** (mark it in place). The resolver is
deleted. "What was this candidate assessed against" is answered by
`job_skill_snapshots`: `assessment_contract.lock_contract` writes the immutable
snapshot at the first start and binds the conversation to it, so the answer is
a ROW carrying its content and digest. Also supersedes the 2026-09-09 sentence
that `job_scorecard_bindings` is what `resolve_for_application` reads.

### `app/orchestration_checks.py` IS `app/import_graph.py`

**SUPERSEDES every reference to `orchestration_checks`** (2026-08-29 section and
the 2026-09-09 judge-isolation rule, which now read `import_graph`). What
survives is what live code needs: `reachable_modules()` (the isolation tests
are built on it), `unreachable_agent_modules()`, and `structural_invariants()`,
which is now identities, reachability and `tool_layer_problems()`.
`halt_coverage` and the router and planner checks went with their subjects;
`pipeline_halt.declared_stages()` keeps its own tests.

- **EVERY REGISTERED TOOL IS A BOUNDED READ, AND THAT IS WHY THE LEDGER COULD
  GO.** `tool_layer_problems` fails any tool whose `risk` is not
  `RiskClass.READ`. The first tool with a side effect must bring an idempotency
  key from stable logical inputs and an UNKNOWN outcome resolved by reading
  back; there is no ledger to provide either, and this check makes that a
  failing build rather than a discovery. **SUPERSEDES the 2026-09-09 row "An
  agent action with a side effect | `services/agent_actions/`"** in the "Where
  to make a change" table, and the rule "`agent_actions` has no UNKNOWN to
  RUNNING edge" (the principle stands; the package does not).
- **A file that does not parse RAISES.** `_import_edges` used to read one as
  importing nothing, which made a syntax error look like an unreachable module.

### THE EVALS SHRANK WITH THE CODE THEY MEASURED

- `eval_agents.py` drops the routing table and the stage-activation frontier.
  It still gates CI (`deploy.yml`, `scripts/test.sh`) on regression cases and
  structural invariants and still reports quality metrics as UNAVAILABLE.
  **AMENDS the 2026-08-18 line "routing against permissions"**: there is no
  router.
- `eval_adversarial.py` keeps all sixteen spec 41 line items.
  `database_outage` now asserts the live degradation (`agent_loop.run_loop`
  returns the caller's fallback with `degraded=True` and the error class);
  `memory_poisoning` asserts the channel's ABSENCE (no memory package, no live
  module reads `agent_learnings`), mutation-checked with a probe reader.
- The `routes-match-permissions` regression case went with the router.

### THE TOOL MANIFEST IS TEST DATA, SO IT LIVES UNDER `tests/`

`app/services/tools/manifest.py` and `tool_manifest.json` had one reader, the
pinning test, so they shipped in the production image and ran nowhere. Moved
byte for byte to `tests/support/tool_manifest.py` and
`tests/fixtures/tool_manifest.json`; regenerate with
`python -c "from tests.support import tool_manifest; tool_manifest.write()"`
from `backend/`. Nothing under `app/` may import `tests.support`, and the sweep
test asserts it.

### ORPHANED BY THE DELETION, RECORDED RATHER THAN HIDDEN

`observability/trace.persist` and `RequestTrace.add_cost` had one caller, the
deleted runner, so `agent_execution_traces` has no writer again. They are in
`ENTRY_POINTS_WITHOUT_CALLERS` with that reason. **SUPERSEDES the 2026-09-22
"`RequestTrace.add_cost` had never been called" fix note**: it has no caller
again. The grading phase wires it from the tool layer or deletes the module.

### THE END-TO-END JOURNEY STOPPED PROVING A PATH NOTHING TOOK

`tests/test_end_to_end_journey.py` drove the enforcement door and the resolver.
It now records stages against the provenance ledger directly, runs the
`hiring.gates` functions the live scorer runs, saves the Skills step and locks
the contract at the start (asserting a later rename does not move the bound
candidate), and publishes the way `POST /jobs/{id}/publish` does. It used to
write `status = 'published'`, which is not a `JobStatus` value: harmless only
while nothing read the job back through the model. It also rolls back instead of
running its teardown inside an aborted transaction, which had been hiding the
real failure under a second one.
