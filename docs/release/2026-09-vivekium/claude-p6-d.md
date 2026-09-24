# CLAUDE.md section draft: Phase 6 WP6-D (BGV candidate auth, recruiter deletions)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly. No
migration.

## A ROUTE THAT ASKS FOR TWO AUDIENCES ANSWERS NOBODY

Every `/bgv/me*` route (history GET/PUT, append employer, HR address
correction, documents GET/POST/DELETE: seven in all) declared
`get_current_user`, which accepts the OWNER and ORG audiences only, beside
`get_candidate_db`, which requires the CANDIDATE audience through
`get_current_candidate`. A token carries one audience, so every real candidate
got a 401 and Employment History was unreachable for the life of the feature.
Every test passed, because every test overrode BOTH dependencies, and a
dependency override answers whatever the test says.

- **The candidate routes take `get_current_candidate`** and resolve through
  `candidate_identity.require_candidate`. The raw-SQL resolver they used also
  matched the candidate row by the signed-in user's email, which is the
  unverified-address takeover shape WP6-A removed everywhere else.
- **`tests/test_candidate_audience_consistency.py` walks the RESOLVED
  dependency tree of every route FastAPI serves**, sub-dependencies included,
  so `require_capability(...)`'s closure is seen for the `get_current_user`
  and `get_tenant_db` it depends on. A route reaching both a candidate-side
  dependency (`get_current_candidate`, `get_candidate_db`,
  `get_optional_candidate`) and a staff-side one (`get_current_user`,
  `get_tenant_db`, `get_superadmin_db`) fails, named. `get_current_any` and
  `get_public_db` belong to neither side.
- **`tests/test_bgv_real_candidate_token.py` makes the calls a browser makes,
  with NO overrides**: a candidate signed in through `tests/candidate_session`
  (real rows, real Redis session, real `sid`), a staff session minted by
  `auth._issue_session` under the ORG audience, writes read back on a second
  connection. The staff token is proven live on `/auth/me` and then refused on
  every `/bgv/me*` route; the candidate token is refused on the recruiter's.
  **A test of a candidate route that overrides the principal is a claim, not
  a test**: new candidate-route tests use the real session helper.
- The three older BGV test files keep their overrides for speed, but each
  override now REFUSES the other audience exactly as the real dependency does,
  and the candidate rows are linked by `user_id`, the only thing a request
  resolves by.

## THE CANDIDATE IS TOLD WHERE A CORRECTION IS POSSIBLE

`GET/PUT /bgv/me` employments carry `correction_needed`. It is True while a
request to that employer BOUNCED and was never answered, which is exactly the
condition `PUT /bgv/me/employers/{id}/hr-email` accepts a correction on: both
read `bgv_delivery.AWAITING_CORRECTION_SQL`, so the screen can never offer a
correction the route refuses as "nothing to correct", nor hide one it would
take. No column: a correction's resend calls `mark_sent`, which moves
`delivery_status` off BOUNCED, and a corrected address that bounces in turn
brings the flag back. `bgv_delivery.employments_needing_correction` returns
employment ids only; no tenant id or name reaches the candidate.

- **`OwnEmploymentOut` is a subclass, not a field on `EmploymentOut`**, because
  `EmploymentOut` is also the recruiter's shape and a flag that is always
  False there would read as "nothing bounced". The field is REQUIRED, so a
  candidate response that forgot to compute it fails validation rather than
  saying there is nothing to fix.

## THE TWO ROUTES THAT BYPASSED `apply_transition` ARE DELETED

`POST /candidates/links/{link_id}/decision` and `POST
/candidates/links/{link_id}/status` wrote a `pipeline_status` row and an audit
row and nothing else: `job_candidate_links.status` never moved, and the FSM
edge check, the BGV offer gate, the Updates feed row and the stage email were
all skipped, so the table, the feed, the gate and the candidate each disagreed
about what had happened. Their only screens were unlinked or imported by
nothing. Deleted with them: `FORWARD_STATUSES`, the "40-question application"
409, and `schemas/candidates.DecisionIn`, `StatusIn`, `StatusOut` (the
unrelated `schemas/bgv_workflow.DecisionIn` stays).

- **SURVIVES: the `decide_profile` and `update_pipeline_status`
  capabilities** (they gate `POST /pipeline/applications/{id}/change-status`
  and the dashboard decision), `EV_HM_DECISION` (emitted by `api/pipeline.py`
  and `api/dashboard.py`), and the `profile_decision` /
  `pipeline_status_updated` rows already in `audit_log`, which are history.
- **Every pipeline move goes through `apply_transition`.** This restates the
  2026-07-27 rule ("only `apply_transition` writes either") that these two
  routes had been breaking since before it was written.
- `tests/test_hr_review_screen_removed.py`: the routes are absent from the app
  and from OpenAPI, a backend sweep refuses the handlers, the gate, the
  sentence and the route strings, and a frontend half (strict xfail until the
  frontend deletions merge, then enforced) refuses `org/review`,
  `org/templates` and the four components.

## ALSO

- `api/candidates.upload_resume` dedupes through
  `candidate_identity.find_canonical_by_email` (case-insensitive, signed-in
  record first) instead of an exact, unordered `Candidate.email ==`.
  `bgv.py` and `candidates.py` left `LEGACY_RESOLVERS`.
- **Not converted here, deliberately**: the four bare `dispatch(...)` calls in
  `api/bgv.py` (`append_employer_route`, `_resend_after_correction`, `send`,
  `submit_employer_checkbox_form`) stay on the dispatch-after-commit
  allowlist. Moving them changes a lost invoke from a loud 500 with rollback
  into a logged error after commit, and `dispatch_after_commit` requires a
  named repair sweep for that case; `pickready.bgv_auto_maintenance` has none
  today.
