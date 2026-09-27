# Audit close-out, Vivekium release

Every item of `audit.md` Part 1 sections 3, 4 and 5 and of the master
prompt's section 4 checklist, checked against `origin/main` at `5f104c0` (what
pilot runs, deployed and digest-verified) plus the package branch
`wip/s4-audit-close`. Written 2026-09-28 by package `s4-audit-close`.

Three verdicts, and nothing else:

- **Closed**: the defect is fixed in code and a named test pins it. The
  commit is the one that ADDED that test on main (`git log --diff-filter=A`),
  which is the change that made the rule enforceable.
- **Resolved by deletion (RBD)**: the thing the item is about no longer
  exists, and a removal sweep keeps it gone.
- **Open**: still true, with the reason and who decides.

"On this branch" means `wip/s4-audit-close`, not yet on main; it ships in the
next release PR.

## Part 1 section 3, broken parts

### Critical and high

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Create Job skips the publish gate | Closed | `POST /jobs` saves a DRAFT and refuses `publish: true` (422); `POST /jobs/{id}/publish` is the only way live, behind `PUBLISH_JOB` and the rows. `tests/test_job_publish_gate.py` (fe1bdad). v10 changes WHAT publish requires (JD plus saved Skills, no SWOT); that is the v10 package's change, not a reopening. |
| 2 | Applying creates an assessment row, locks the matrix, redirects to a refusal | Closed | `assessment_invitations.invite_batch` is the only writer of `assessment_conversations`; applying writes none. `tests/test_apply_creates_no_assessment.py` (0ca928f). **v10 note**: the owner has since ruled that the FIRST GENUINE APPLICATION freezes JD and Skills. That is a deliberate lock point now, taken by `lock_contract` in the application transaction, not the side effect of an assessment row the audit found; it is built by the v10 package. |
| 3 | Candidates locked out of Employment History and BGV | Closed | The `/bgv/me*` routes take a candidate session; a route reaching both audiences fails. `tests/test_candidate_audience_consistency.py` (a063b7c), `tests/test_bgv_real_candidate_token.py` (bd23113), real sessions via `tests/candidate_session.py`. |
| 4 | Delivered reports can be overwritten | Closed | Insert-only three ways (`ON CONFLICT DO NOTHING`, trigger `prism_report_is_immutable`, UPDATE revoked, migration 0130); a second run is a no-op under the lock. `tests/test_report_insert_only.py` (5a2bc07), `tests/test_scoring_lock.py` (b790534). |
| 5 | Sourced and auto-matched candidates recorded as applied | Closed | `hiring_pipeline.start_sourced` is the one way a created link enters at `sourced`, with history; 0122 corrected existing rows. `tests/test_sourced_links.py` (ea80325), `tests/test_yukti_run_matching_db.py` (c091719). |
| 6 | The corporate sender is never used | Closed | `email_outbox.queue_candidate_email` is the one writer and resolves the sender (named active, else tenant default, else platform); the composer posts `sender_id`. `tests/test_email_outbox.py` (a74d286). |
| 7 | Two recruiter status routes bypass the pipeline | Closed (RBD for the routes) | `POST /candidates/links/{id}/decision` and `/status` are deleted; every move goes through `apply_transition`; the dashboard stage control goes through the invitation service. `tests/test_hr_review_screen_removed.py` (6ac89ff), `tests/test_dashboard_rbac_matrix.py`. |
| 8 | The reconcile sweep rebuilds a matrix a human emptied | Closed | `reconcile_job_setup` selects only jobs with zero rows of ANY kind or a draft that never reported back. `tests/test_reconcile_job_setup.py` (9640136). |

### Medium

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 9 | Video mode scores structured questions as unanswered | RBD | The video interview mode is deleted. `tests/test_video_interview_mode_removed.py` (7cf2569). |
| 10 | Graded against a question the candidate never saw | Closed | Vaada persists only what is shown; the repeat check is a criterion of the writer's own loop and a degraded write writes nothing. `tests/test_question_rubric_consistency.py` (c850344). |
| 11 | Scoring can start before the last answer is saved | Closed | `dispatch_after_commit` everywhere a request writes the row a task reads; the legacy allowlist is EMPTY. `tests/test_dispatch_after_commit_sweep.py` (8e803d9). |
| 12 | Wrong resume, double scoring, unscored candidates | Closed | Retrieval returns candidates; a link is read from its own `profile_id`; a model failure is `not_assessed`, stated. `tests/test_yukti_score_links.py` (63e54f3), `tests/test_yukti_run_matching_db.py` (c091719). |
| 13 | Matching runs before categories are saved | RBD | The matching categories are deleted (D2); the run is gated on saved skills. `tests/test_yukti_legacy_removed.py` (d0e61b3). |
| 14 | Resume parse lost when embedding fails | Closed | The parse commits with a NULL vector and the next run backfills. `tests/test_resume_parse_embedding_failure.py` (ea80325). |
| 15 | Match % shown vs ordering used | Closed | D3: no number reaches a client; the table orders by ONE key, `rank_score_sql`, and shows words. `tests/test_yukti_rank_expression.py` (e43ff0a), `test_platform_audit.test_no_number_reaches_a_client_with_no_exception`. |
| 16 | Tatva edits not restricted to the Hiring Manager | Closed | Authorization is per bucket (`SKILL_BUCKET_CAPABILITY`) in every skills write. `tests/test_job_skills_api.py` (358b7cd). |
| 17 | Moving a criterion between columns can 500 | Closed | A move checks the target bucket first: an active occupant is a 409, a removed one is revived. `tests/test_job_skills_service.py` (64369ff). |
| 18 | The grade can change after freeze | Closed | A grade change after a snapshot is a 409 under the SKILLS lock. `tests/test_grade_lock.py` (27e5644). v10 moves the lock point to the first genuine application (v10 package). |
| 19 | Reopen leaves the job marked finalised | RBD | There is no reopen; skills lock and the snapshot is the contract. `tests/test_tatva_matrix_editor_removed.py` (c058dd8). |
| 20 | Old matrix versions are not kept | Closed | `job_skill_snapshots` is insert-only twice; a conversation reads ITS snapshot; the unused resolver is deleted. `tests/test_assessment_contract.py` (a434415), `tests/test_start_locks_contract.py` (6e3dcca). |
| 21 | Only the first assessment reminder is ever sent | Closed | Automatic emails are idempotent on the STAGE (`email_log.dedupe_key`). `tests/test_reminder_stages.py` (a74d286). |
| 22 | The Proctoring report ignores job closure | Closed | 410 through `require_readable`. `tests/test_proctoring_report_closure.py` (971438c). |
| 23 | Progress Redis client reused across loops | Closed | `core/redis_loop.LoopBoundRedis` is the one implementation; in-flight progress is published from inside the task's loop. `tests/test_redis_loop_binding.py` (a561f88), `tests/test_task_progress_publish.py`. |
| 24 | Messages: no notification, no badge, retries double, fifty only | Closed | `pickready.notify_candidate_of_message`, an unread watermark, a per-draft client token (reuse for different words is 409), "Load earlier" paging. `tests/test_message_notification.py`, `tests/test_candidate_messages.py` (a74d286). |
| 25 | Duplicate candidate records | Closed | `candidate_identity`: request resolves by `user_id` only, sign-in links on a Firebase-VERIFIED address, `candidates.user_id` UNIQUE (0120). `tests/test_candidate_identity.py` (a7ba56a). |
| 26 | Template or fallback output not flagged | Closed | A template JD says so on screen; a remark records `model / template / catalogue`; a report names a model only when its critic accepted the call. `tests/test_jd_generation_gates.py` (27e5644), `tests/test_report_provenance.py` (18f4633), `tests/test_siddhi_remarks.py` (34816d3). |
| 27 | Silent failures on live paths | Closed | A broad handler must re-raise, log or read what it caught (absolute on Part A, a ratchet elsewhere). `tests/test_no_silent_degradation.py` (5481986). |

### Low and UX

| Item | Verdict | Evidence |
|---|---|---|
| Close Job says "your pipeline is unchanged" | Closed | The dialog says access stops and names the deletion DATE; the dispute path has a screen. `tests/test_job_closure_soft_deletion.py` (d63f889). |
| Two JD editing paths | Closed | One JD edit path, `PATCH /jobs/{id}/jd`; `PUT /jobs/{id}/jd` deleted; the client derives no sections. `tests/test_job_publish_gate.py` (fe1bdad). |
| The grade shown as "Level" | Closed | `jobs.level` is read and written by nothing; the badge reads "Grade". `tests/test_job_level_removed.py` (c2e91de). |
| "Matrix built from the JD after job creation" copy | RBD | The matrix and its copy are gone (D1). `tests/test_tatva_matrix_editor_removed.py` (c058dd8). |
| "PPI" and "retake after six months" in copy | Closed / RBD | Copy vocabulary swept by structure, both directions. `tests/test_user_facing_copy_names.py` (3d6c9f5), `tests/test_retake_removed.py` (1277324). |
| Idle timeout: polling counts as activity | Closed | Only `X-User-Activity: 1` touches the deadline. `tests/test_idle_timeout_activity.py` (cc68a4d). |
| Deleting a profile leaves the Firebase account | Closed | Deleted in the same transaction; failure is 503 and deletes nothing. `tests/test_account_deletion_identity.py` (163422d). |
| New Jobs lets a candidate fill the form before "already applied" | Closed | `tests/test_portal_jobs_already_applied.py` (14214c9). |

## Part 1 section 4, built but never used

| Item | Verdict | Evidence |
|---|---|---|
| The tool layer and its permissions | Closed (wired) | Evidence RAG reads go through `tools.execute` for Vaada, Miti and Siddhi. `tests/test_evidence_retrieval_through_tools.py` (5e824b0). |
| The side-effect action ledger | RBD | `services/agent_actions` deleted; every registered tool is a bounded READ. `tests/test_unreachable_subsystems_removed.py` (9de51df). |
| Reasoning runner, planner, budgets | RBD | Same test. |
| Agent learnings (and the revoke task) | RBD | `services/memory` and `pickready.revoke_learnings_from_source` deleted; the table stays as history (`docs/operations/LEGACY_TABLES.md`). Same test, `tests/test_legacy_scrap_removed.py` (67bb0d5). |
| The orchestration version resolver | RBD | Deleted; `job_skill_snapshots` answers the question. Same test. |
| Retrieved-chunk safety quarantine | **Open** | `services/safety/content.screen_chunks` is its only implementation and only `scripts/eval_adversarial.py` calls it; no live retrieval path does. Wiring it or deleting it with the 2026-08-18 rule is an OWNER decision (recorded in CLAUDE.md, "OPEN AT THE END OF THE RELEASE"). |
| The request coalescer | **Open** | `services/coalescing.py` survives. Its only live use is the exception type `TenantScopeMissing`, imported by `tools/executor._cache_key`; `single_flight`, `cached_derivation`, `request_key`, `stats` and `reset` have no production caller. `tests/test_unreachable_subsystems_removed.py` records it as KEPT because "the grading phase wires them through `tools.executor`", which is true of the exception only. `request_key` is pinned by `tests/test_cache_tenant_keying.py` as "the builder every new derived-representation cache is meant to use". Not deleted here: removing a designated shared primitive is a design decision, and the sweep test that pins it belongs to the cache-keying rule. Decide: delete the four functions and move `TenantScopeMissing` beside `_cache_key`, or keep them and correct the KEPT sentence. |
| The typed/video "one adapter" | RBD | `services/assessment_canonical.py` deleted with the video mode. `tests/test_video_interview_mode_removed.py` (7cf2569). |
| The PRISM export chokepoint (G4 plus number ban) | Closed (wired) | The PDF leaves only through `siddhi.delivery.gate_delivery`. `tests/test_prism_pdf_g4.py` (b6f2cf1), `tests/test_siddhi_delivery_single_path.py` (1277324). |
| The "AI Score hand-off" | RBD | `services/matching_categories.py` deleted; the report carries Yukti's frozen snapshot (`ai_score_json`). `tests/test_yukti_legacy_removed.py` (d0e61b3). |
| Five configured AI task types with no caller | RBD | `test_llm_task_routing.DELETED_TASK_TYPES`. |
| About twenty routes with no screen | Closed / RBD | Deleted or declared: `tests/test_dead_routes_removed.py`, `tests/test_route_callers.py` (5fdb1be); approval chain `tests/test_job_approval_chain_removed.py` (c2e91de). Now wired: dispute and retention (`assessment-retention-panel.tsx`), billing cancel and credit statement, BGV documents (`bgv-documents-card.tsx`) and HR-email correction (`employment-history-card.tsx`), consent history (`consent-history-card.tsx`), keep-my-profile (`app/keep-profile/[token]`), conversation unread (`lib/conversations.ts`). Deleted: the report library, the duplicate status routes, dashboard calibration. |
| Unreachable HR Review Screen and Email Templates editor | RBD | `tests/test_hr_review_screen_removed.py` (6ac89ff); the email-template routes in `tests/test_dead_routes_removed.py`. Their live panels moved to `candidate-case-panel.tsx` first. |
| Must-have per-competency threshold always empty | Closed | Runbook 12.1 fires on the per-ITEM grade: a Must-have graded Not Matching (or unanswered) fails and caps. `tests/test_band_caps.py` (5481986). |
| Human-review input to the final gate never passed | Closed | G4 reads the latest human disposition, which must postdate the report. `tests/test_miti_live.py`, `tests/test_miti_contract_digest.py`. |
| Situation-type weight layer always off | RBD | No weight is derived on the live path (D1, D2); `hiring/situations.py` survives only as data Miti's dimension map reads (`hiring/__init__.py` says so). |
| Question-count ceiling (40) unreachable | RBD | The ceiling and the pre-fill are deleted; one question per skill, never below the grade floor. `tests/test_prefill_removed.py` (a373d64). |
| Credit headroom has no caller; deficit written, never read | RBD | Both deleted (0128). `tests/test_legacy_scrap_removed.py` (67bb0d5). |

## Part 1 section 5, scrap

| Old feature | Verdict | Evidence |
|---|---|---|
| OTP / SMS login | RBD | `tests/test_login_otp_removed.py` (498bc48), `tests/test_phone_login_removed.py` (cc68a4d). |
| Celery aliases and wording | RBD | The aliases (one could force-rebuild a human matrix) are gone with the compiler. `tests/test_gcp_and_celery_wording_removed.py` (bd6c7e1). |
| Multi-vendor AI | RBD, one item held | Provider enums and `llm_provider_keys` dropped (0128). `tests/test_multivendor_ai_removed.py` (67bb0d5). `LLM_KEY_ENCRYPTION_SECRET` is HELD in `secret_names`, granted to no service, until the owner decides it. |
| GCP / Cloud Run | RBD | `tests/test_gcp_and_celery_wording_removed.py` (bd6c7e1). |
| The 40-question aspect form | RBD | One application form, six fields. `tests/test_aspect_form_removed.py` (a4d65d5), `tests/test_public_apply_unified.py` (14214c9). |
| Preset technical question bank | RBD | Tables dropped behind emptiness guards (0128). `tests/test_legacy_scrap_removed.py` (67bb0d5). |
| Old interviewer paths | RBD | `tests/test_dead_interviewer_modes_removed.py` (c850344). |
| `level` | Closed | Read and written by nothing; the column is kept (thirty pilot rows). `tests/test_job_level_removed.py` (c2e91de). |
| Role Intake | RBD | `tests/test_role_intake_removed.py` (a03e978); `job_swot_intakes` documented history-only in `docs/operations/LEGACY_TABLES.md`. |
| Company DNA stale comment in coverage config | Closed | `test_repo_hygiene.test_coverage_config_does_not_name_a_removed_feature` (0916365). |
| Old verification system | RBD | Dropped by 0121. `tests/test_verification_requests_retired.py` (ae9d082). |
| Stray compiled files, orphaned prompt, unused UI primitives and components | Closed | Re-checked 2026-09-28: no tracked `.pyc`; every prompt under `app/prompts` is named by non-test backend code; every component under `frontend/components` is imported by a non-test source. |
| `pickready.app` addresses | **Open** | Owner question: whether the mailboxes exist. `docs/operations/PICKREADY_APP_ADDRESSES.md`. |
| LangSmith beside OpenTelemetry | RBD | OpenTelemetry only. `tests/test_langsmith_removed.py` (c5386df). |
| Repository hygiene (`.codex/`, vendored skills, CLAUDE.md drift) | Closed | Asked of `git check-ignore`. `tests/test_repo_hygiene.py` (0916365); CLAUDE.md's drift (the RUNBOOK-AMBIGUITY count, the deleted module, the question counts) is marked AMENDED or SUPERSEDED in place. |

## Master prompt section 4, Part 2 (Claude repository audit)

| Item | Verdict | Evidence |
|---|---|---|
| Source of truth, manual deploy | Closed in code; **protection Open** | main is the only deployable branch (CONTRACT v7); the workflow's ref restriction is pinned by `tests/test_deploy_workflow_guard.py` (18485e0). Branch protection on `main` is NOT applied: the release account holds READ and protection needs ADMIN. The exact settings and command are in `docs/operations/BRANCH_PROTECTION.md` (this branch). |
| Tool boundary unused | Closed (wired) | `tests/test_evidence_retrieval_through_tools.py` (5e824b0). |
| Miti / Vaada loop dead | Closed | The ledger is written PER ANSWER. `tests/test_answer_ledger_per_turn.py` (c850344), `tests/test_vaada_miti_loop.py` (2df75c1). |
| RAG write-only | Closed | Evidence RAG is read by live code. `tests/test_evidence_rag_wiring.py` (5025ce0), `test_ai_reachability`. |
| Ranking ignores categories | Closed | D2: six fixed Yukti parts, one blended key. `tests/test_yukti_rank_expression.py` (e43ff0a). |
| Dual scoring, hash fallback, threshold gap | Closed | Miti is the sole grading authority, the hash is deleted, 12.1 fires per item. `tests/test_miti_sole_authority.py` (66fef19), `tests/test_hash_fallback_removed.py` (a7d19b6), `tests/test_grading_split_removed.py` (335fb45), `tests/test_band_caps.py` (5481986). |
| Unreachable subsystems, experience memory | RBD | `tests/test_unreachable_subsystems_removed.py` (9de51df). |
| RAG repair hole | Closed | `pickready.repair_semantic_index` (hourly, in Python and every environment's Terraform). `tests/test_semantic_index_repair.py` (5e824b0), `test_schedule_parity`. |
| Comment padding | RBD | The matcher's 25-30 word padding is deleted with its scoring half (`services/matching.py` docstring); the only continuation sentences left are seed-only (`scripts/seed_mock_data._SEED_CONTINUATIONS`), and a product remark says how it was written. `tests/test_yukti_legacy_removed.py` (d0e61b3). |
| Siddhi existence-only citations, hidden trail | Closed | A citation must SUPPORT its statement; the trail is shown on click. `tests/test_siddhi_support.py` (34816d3), `tests/test_citations_route.py` (f8642cc). |
| Bodha / Sutra shared identity | Closed | Separate runtime ids `AGENT_SWOT`, `AGENT_SKILLS`. `tests/test_agent_authorization.py`. |
| JD must-haves not flowing into Tatva | Closed | The draft evaluator requires the JD's required skills and the SWOT Weaknesses in Must-have. `tests/test_job_skills_draft.py` (3ea9009). v10 makes the JD the primary input and the SWOT optional (v10 package). |
| Databank platform-wide | Closed (owner ruling) | Platform-wide over CONSENTING candidates, entered at `sourced`, labelled "Databank, not an applicant". `tests/test_ranked_candidates_api.py`, `tests/test_yukti_projection.py`. |
| Success-pattern bias | RBD | Deleted with the matcher's scoring half. `tests/test_yukti_legacy_removed.py` (d0e61b3), `tests/test_yukti_judge.py`. |
| Candidate email replies not threaded | Closed in code; **inert on pilot** | Reply-token routing reused for candidate threads. `tests/test_inbound_conversation_reply.py`. Pilot ships `INBOUND_EMAIL_DOMAIN=""` because the region does not receive SES mail: an owner enables inbound. |
| No golden journey test | Closed | `tests/test_golden_journey.py` (b41899f), the harness scenario, and the CI job both image builds need. |

## Master prompt section 4, Part 3 (ChatGPT repository audit)

| Item | Verdict | Evidence |
|---|---|---|
| Branch divergence | Closed | The release landed on main; pilot is built only from main. Protection: see Part 2 row one. |
| Harness adoption | Closed | The harness tiers are a CI job; the golden journey is a harness scenario. `tests/test_harness_scenarios.py`. |
| Two retrieval architectures | Closed | Separate use cases sharing primitives; owners named in `docs/spec/RETRIEVAL.md`. |
| Semantic-index repair | Closed | As Part 2, RAG repair hole. |
| Threshold gap, dual scoring, fake fallback | Closed | As Part 2. |
| Seven-stage form mismatch | RBD | No seven-stage form or matrix; the recruiter sees Skills. |
| Gmail wording | Closed | "Email" is outbound email plus in-portal threads; no inbox sync, said in `docs/spec/CANDIDATE_COMMUNICATIONS.md`. |
| The `functional_assessment` god module | Closed | Four one-way stages under `services/assessment_pipeline`. `tests/test_assessment_pipeline_direction.py` (5025ce0). |
| DB versus artifact truth | Closed | AD-1 in `docs/spec/ARCHITECTURE.md`. `tests/test_architecture_database_authoritative.py` (f3ddacd). |
| Vocabulary sprawl | Closed | `tests/test_user_facing_copy_names.py` (3d6c9f5), `frontend/lib/user-facing-copy.test.ts`. |
| Compatibility code cutoff | Closed | `tests/test_compatibility_cutoff.py` (4084644). |
| Vertical journey tests | Closed | Golden journey plus `tests/test_end_to_end_journey.py`. |

## Closed by this package (`wip/s4-audit-close`)

| Item | Commit | Test |
|---|---|---|
| PR #5 item 5: signing in as the invited user accepts the invitation (the staff table read Active and Pending at once) | 2d82fcd | `tests/test_staff_invite_acceptance.py`, mutation-checked on all three wiring points |
| PR #5 item 2: saving your own permission row refreshes your own tab | 0ef8898 | `frontend/components/permission-matrix-modal.test.tsx`, mutation-checked |
| PR #5 dashboard test race (three waits on the CALL, asserting on the RESPONSE) | 0ef8898 | `frontend/components/candidate-dashboard/candidate-dashboard.test.tsx` |
| p3-w4 hunk 4: the proctoring speech and second-voice events carry `question_id` | c5d3399 | `tests/test_proctoring_audio_question_id.py`, mutation-checked |
| Branch protection written down (not applied: READ token) | 740fabf | `docs/operations/BRANCH_PROTECTION.md` |

## PR #5, item by item

| PR #5 item | Where it lives now |
|---|---|
| 1. The browser never re-asked for capabilities | main, PR #6: `lib/auth-context` revalidates on navigation (at most once a minute) and on a 403. |
| 2. Editing your own row did not refresh your own tab | This branch, 0ef8898. |
| 3. `PUT /admin/permissions` never invalidated its cache | RBD on main: the permission-template editor routes are deleted (`tests/test_dead_routes_removed.py`, `admin/permissions`). |
| 4. `POST /admin/tenants` crashed on every call | main: `rbac.invalidate_role_permissions(tenant.id)`, `tests/test_admin_create_tenant.py` (bcd99ca), which also moved the invitation dispatch after commit. |
| 5. Invitation status stuck at Pending | This branch, 2d82fcd, plus the /join second step (the page signs in BEFORE it posts the acceptance, so the port also admits "already accepted by this same user" there; PR #5 as written would have made /join report its own link as spent). |
| B. The AI brief's lost zero | **Not applied here**: the Create Job page and `lib/job-payload.ts` are job setup files owned by the v10 package. The adapted patch and its tests are `docs/release/2026-09-vivekium/hunks/pr5-jd-brief-zero.md` (65c0752). Still broken on main. |
| Dashboard test race | This branch, 0ef8898. |
| `test_yukti_live` float comparison | RBD: the module and `prescreen` are deleted. |

## Still open, in one place

1. **Branch protection on main**: an admin runs `docs/operations/BRANCH_PROTECTION.md`.
2. **The JD brief zero** (PR #5 item B): apply the hunk with the v10 work.
3. **Retrieval-time injection screen** not in force: owner decision.
4. **The request coalescer**: delete the four uncalled functions or correct the KEPT sentence (above).
5. **`pickready.app` mailboxes**: owner question.
6. **Inbound mail** inert on pilot; **`transcribe_enabled`** off until the deploy stage (p3-w5 hunk 3, gitignored tfvars); **`CODE_EXECUTION_BACKEND=disabled`** until the sandbox verification passes.
7. Carried from CLAUDE.md's release section: `agent_execution_traces` has no writer and `RequestTrace.add_cost` no caller; the older per-key JD path survives; five one-tenant tasks still bypass RLS pending a real-Postgres proof; `hm_access_granted` is a column with no model attribute.
8. **PR #5 itself** is not closed by this package: closing it and posting the item list are public actions on the repository and wait for the owner's go-ahead (the release account also holds READ, and the PR is the owner's). The comment text is in this package's report.
