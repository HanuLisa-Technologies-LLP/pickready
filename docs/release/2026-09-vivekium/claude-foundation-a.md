## Current hard rules, commit-safe dispatch and the Phase 3 carve (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Foundation stage, part
A. No migration.

### A TASK ABOUT A ROW IS DISPATCHED AFTER THE ROW IS COMMITTED

**SUPERSEDES IN PART rule 4 and the 2026-09-05 "one way to start background
work".** `dispatch("pickready.x", args=[...])` is still the transport, but a
request that WRITES the row a task reads uses
`dispatch_after_commit(session, "pickready.x", args=[...])` from
`app/workers/dispatch.py`. A dispatch placed before `get_tenant_db` commits
has two failures: the task can start before the row is visible, and a request
that raises afterwards rolls the row back while the task is already on its
way (`_ensure_conversation_ready` dispatched and then raised 409, so the work
was sent about a state that was never kept).

- **The handle comes back NOW, the invoke happens at COMMIT.** The run id is
  minted client-side exactly as `dispatch` mints it, so a route can put it in
  its response; an unclaimed id reads as PENDING (`workers/status`), which is
  what it is. The invoke runs from SQLAlchemy's `after_commit` through
  `core/after_commit.on_commit`, the generalisation of
  `realtime.publish_after_commit`.
- **A rollback dispatches NOTHING, and so does a session closed without
  committing.** The pending list is discarded when the OUTERMOST transaction
  ends without a commit, so a later commit on the same session cannot fire a
  stale dispatch.
- **Programming errors raise in the request; operational ones are logged
  after the commit.** The task name, the configured backend and the JSON-ness
  of the arguments are checked at the call, where raising is correct. A
  `DispatchError` from the invoke itself is logged at ERROR
  (`after_commit.callback_failed`, naming the task and run id) and NEVER raised
  out of `commit()`: the write is durable by then, and raising would answer
  500 to a request that succeeded (the 2026-09-16 lesson). Each converted call
  site must name the sweep that repairs a lost invoke.
- **Never call it and then raise.** The raise rolls the dispatch back with the
  transaction. Return a state the client can act on (`preparing`) instead.
- **Documented limit:** a callback registered inside a SAVEPOINT that later
  rolls back still fires on the outer commit. Register after the savepoint.
- **`tests/test_dispatch_after_commit_sweep.py` refuses NEW bare dispatch
  calls** in `app/api/**`, `services/proctoring/**` and `services/video/**`,
  with `LEGACY_CALL_SITES` naming every existing one by file and function and
  the phase expected to convert it. The list only SHRINKS: an entry allowing
  more calls than remain fails as stale. It must be EMPTY by the end of the
  release. The detector follows aliased imports because its first run found
  one (`api/email_senders.py`, `dispatch as dispatch_email`); a sweep matching
  a name is blind to a rename.
- `realtime.publish_after_commit` is still its own `once=True` listener and
  still fires on a later commit after a rollback. Re-basing it on `on_commit`
  is WP1's (coordinated with Phase 6), so there is one after-commit mechanism.

### THREE FILES WERE CARVED, AND NOTHING THEY DO CHANGED

PLAN-p3 WP0, pure moves, landed before the phases that edit these files.

- **`api/assessments.py` is the STAFF side** (job setup review, the Tatva
  matrix, the Job SWOT, reports, transcripts). The invitation resolver, the
  conversation, mode and consent routes are `api/assessment_conversation.py`;
  the video interview and session-media routes are `api/assessment_recording.py`.
  All three mount under `/api/v2/assessments` in the order the routes were
  declared, and all 33 routes were compared route by route (path, method,
  endpoint, response model, status code, dependencies).
- **A helper imported by name is patched by name, per module.**
  `assessment_recording` imports `_candidate_link` and friends from
  `assessment_conversation`, so a test faking one must set it on EVERY module
  that bound it, the same trap as the 2026-08-29 `sys.modules` note.
- **`services/assessment_questions/` holds the per-candidate questions.**
  `budget.py` is the per-grade ranges, the typical splits and
  `conversation_may_close`; `generate.py` is the generator, the allocation,
  format composition and portable coverage. `services/ppi` keeps the matrix
  and reads the ceiling and range through FUNCTION-LOCAL imports, because
  `budget` imports ppi's category constants and a module-level import both
  ways is a cycle. The package `__init__` imports nothing for the same reason.
  There is NO re-export shim: `ppi.max_questions` and friends are gone.
- **The Phase 3 tasks live in `workers/tasks_questions.py`,
  `tasks_proctoring.py` and `tasks_media.py`**, same names, routes and retry
  policies. `workers/tasks.py` imports them at the bottom, and that import IS
  the registration: `registry.resolve` imports `app.workers.tasks` and nothing
  else, so a task module missing from that line is a task no dispatch reaches.
  `test_task_registry` sweeps each new module's deferred imports by name.
