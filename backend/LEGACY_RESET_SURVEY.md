# Legacy reset survey

Read-only. Produced by `python -m app.scripts.legacy_reset --survey`. spec-doc6 section 6.1: nothing proceeds until this is reviewed.

- Taken at: `2026-08-31T13:47:56.158684+00:00`
- Database: `pickready`
- Schema revision in the database: `0063_team_review_verdicts`
- Newest revision in `alembic/versions`: `0069_calibration_divergence`
- Tables in the schema: 51, all classified below

## The premise this reset was written against is false today

Gate G1 is enforced on a live path. G1 is called at app/services/hiring/scorecard.py:442, app/services/miti/pipeline.py:348 and is reachable from app.api.assessments, app.api.candidates, app.api.dashboard, app.api.emails, app.api.jobs, app.api.matching, app.workers.tasks.

The archive-and-mark step therefore does what decision D2 says it does: a job keeps its posting and its applications, and its evaluation is blocked until a human approves a new scorecard.

## Sign-off required before the purge runs

Each of these is a consequence of decision D2 that a person should agree to explicitly, because it is not recoverable by running the job again.

| Finding | Rows | Why it needs a decision |
|---|---:|---|
| Candidates with an evaluation but no resume | 0 | Their pre-screen grade cannot be regenerated: --regrade requires a resume, so these applications end the reset with no grade at all until a resume is uploaded. |
| Published jobs whose scorecard is being archived | 35 | The posting stays live and applications keep arriving. Their approval stamps are cleared so the job reads as pending review, and the INTENDED state is that gate G1 then blocks evaluation until a human approves a new scorecard. Whether G1 can actually do that is the first section of this document, and the purge refuses while it cannot. |
| Assessment conversations still active | 198 | Purging one ends an interview a candidate may be part way through. They keep their application; they lose the conversation and must be re-invited once the job's new scorecard is approved. |

## Where the legacy evaluation data actually is

Decision D2 names `Evaluation` rows first. In this codebase that table is `evaluations`, added by migration 0059, and it has no writer: `app/services/miti/pipeline.py` is the only module that would produce one and nothing under `app/api/` or `app/workers/` imports it. The rows D2 MEANS are the ones the shipped pipeline actually wrote, and they are in the older tables.

| Table | Rows | What it is |
|---|---|---|
| `evaluations` | 0 | One Miti run: the five dimension bands, the aggregate, the triangulation and the gate verdicts. The first table D2 names. |
| `functional_skills_reports` | 577 | The PRISM Report itself: the delivered, immutable artefact D2 names. |
| `report_dimensions` | 7845 | Per-dimension grades inside a PRISM Report. D2 purges the reports and their artefacts. |
| `report_skill_evidence` | 0 | Structured evidence behind a report dimension. It cites assessment messages that are themselves being purged, so keeping it would leave citations pointing at nothing. |
| `job_competencies` | 542 | The Tatva matrix: Must-have, Nice-to-have and Behavioural criteria. D2 purges old scorecards. |
| `job_matching_categories` | 13 | The coarse resume-only categories the AI Score was computed against. Regrading against the old categories would compute a new number from retired criteria, which is the least detectable kind of wrong. |
| `candidate_questions` | 45 | Per-candidate questions generated against the old scorecard. |
| `candidate_technical_questions` | 15440 | Per-candidate technical questions and the rubric written with each. Scored against a scorecard that is being replaced. |
| `technical_questions` | 680 | The retired per-job preset bank. CLAUDE.md kept this table unread so 'what was this candidate asked' stayed answerable for existing reports; those reports are themselves being purged, so the reason no longer holds and the history moves to the export. |
| `assessment_conversations` | 574 | One assessment run, and also the invitation. A COMPLETED one is legacy data and is purged. An ACTIVE one is not: spec-doc6 D2 says in as many words that applications are not interrupted, and deleting a conversation somebody is part-way through is the most direct way to interrupt one. The survey counted 198 active at the time this predicate was added, 197 of them seeded demo data and one a real candidate, and the one is the reason. Preserving it costs nothing: gate G1 blocks that candidate's evaluation until the job is re-defined, so they finish typing and their scoring waits, which is strictly better than losing their answers mid-assessment. |
| `assessment_messages` | 240 | The interview transcript. Part of the assessment D2 deletes. The candidate's own words are human authorship and survive only in the export, which is why the survey raises this for sign-off rather than treating it as routine. |
| `evidence_items` | 58 | Evidence references into resumes and transcripts, extracted by the old pipeline. A resume-sourced item survives its source but was tiered and scored under the retired rubric, so it is regenerated rather than kept. |
| `evidence_claims` | 15 | AI-generated candidate intelligence produced by the old logic. Its supporting evidence cites transcripts being purged. |

Applications carrying an old pre-screen grade are counted in the edge cases below and are cleared by the RESET rule on `job_candidate_links`, not by a DELETE: the application itself is preserved data.

## Every table in the schema, classified

spec-doc6 section 6.2: any table decision D2 does not classify must be classified here before the purge runs. The `D2` column says whether the decision named the table or whether the classification is this module's reading of it.

| Table | Bucket | D2 | Rows | Reason |
|---|---|---|---:|---|
| `calibration_records` | detach | inferred | 0 | A person's later judgement about whether a grade turned out to be right. Human authorship. Same treatment, and it already carries its own job_id. |
| `review_dispositions` | detach | inferred | 0 | G4: a person looked at a flag and decided something. Human authorship, and the proof of the no-auto-reject rule. The reference to the evaluation is nulled after the job and application context is copied onto the row; the row itself is never deleted. |
| `alembic_version` | infrastructure | inferred | n/a | The schema revision pointer. Not data, and rewriting it would make the database claim a shape it does not have. |
| `agent_execution_traces` | preserve | inferred | 0 | Operational telemetry: identifiers, counts and timings, never content. Not a product artefact and not a rating, so the default for a table D2 does not name applies. |
| `agent_learnings` | preserve | inferred | 0 | Agent output hygiene (word ranges, JSON shape), never hiring criteria, and structurally unable to relax a threshold or skip a verifier. Flagged in the survey as a reviewable call: a reader who wants a clean slate would move it to the purge bucket, and nothing else changes. |
| `audit_log` | preserve | named | 3632 | The audit trail is never purged. Rows referencing deleted evaluations remain, and the deletion itself is audited beside them. |
| `bd_leads` | preserve | inferred | 0 | Ready Pick Now's own sales pipeline. Not client hiring data. |
| `billing_transactions` | preserve | inferred | 0 | Payment records. Money is never purged, and a payment that happened cannot be unmade by deleting the row describing it. |
| `candidate_team_reviews` | preserve | named | 0 | Team Review remarks. Human authorship, named by D2 as preserved. Checked against the live foreign keys: this table has no reference to an evaluation, so no cascade can reach it. |
| `candidates` | preserve | named | 44 | Candidate accounts. D2 preserves them, along with the resumes and applications hanging off them. |
| `companies` | preserve | inferred | 5 | The client-authored careers page, which candidates read. Preserved with the tenant that wrote it. |
| `company_dna` | preserve | inferred | 0 | The Layer 2 artifact and the client's answers behind it. Produced by the new framework, and the input Sutra needs to compile a new scorecard at all. |
| `compliance_documents` | preserve | inferred | 0 | Tax and commercial documents. D2 preserves uploaded documents. |
| `credit_ledger` | preserve | inferred | 198 | The credit balance is SUM(subunits_delta) over this table, so deleting a row silently changes what a customer owes. |
| `email_log` | preserve | inferred | 391 | What was actually sent to whom, including the copy. An outbound record cannot be retracted by deleting the log of it. |
| `email_templates` | preserve | inferred | 21 | Client-authored email copy. Not machine output, and not scoring. |
| `hiring_managers` | preserve | inferred | 17 | Which hiring managers belong to this customer. Access control, not scoring. |
| `interviews` | preserve | inferred | 0 | Scheduled interviews. Application data with a real person's calendar behind it, and not machine scoring. |
| `job_approvals` | preserve | inferred | 8 | The job approval chain: who approved a job to be published, and when. Human decisions. |
| `job_assignments` | preserve | inferred | 0 | Which recruiter, hiring manager or interview manager is assigned to a job. Access control, not scoring, and a job keeps its team through the reset because the job itself is preserved. |
| `job_swot_intakes` | preserve | inferred | 2 | The reporting authority's own answers in the SWOT session. Human authorship. The matrix DERIVED from it is purged; erasing the conversation as well would delete evidence of work a person did. |
| `llm_provider_keys` | preserve | inferred | 9 | Provider credentials, encrypted at rest. A global table, and nothing here is client hiring data. |
| `old_profile_reviews` | preserve | inferred | 0 | A recruiter's decision on an old profile, and the billing event attached to it. Human decision plus money. |
| `otp_challenges` | preserve | inferred | 82 | Short-lived authentication challenges. They expire on their own and deleting them early would fail a login in flight. |
| `pipeline_status` | preserve | named | 2 | The append-only hiring stage history. Application data, not scoring. |
| `pricing_plans` | preserve | inferred | 4 | The commercial plan catalogue. A global table that tenants reference, so deleting a row would strand a subscription. |
| `profiles` | preserve | named | 45 | The resume and everything parsed from it. D2 preserves resumes and uploaded documents. The embedding on this row is re-embedded by app.scripts.reembed, never deleted here. |
| `role_permissions` | preserve | inferred | 161 | The permission model is data rather than code, so this table IS the authorisation rules. Nothing about the reset touches them. |
| `staff_invites` | preserve | inferred | 0 | Staff invitations that have not been accepted yet. Deleting one would silently revoke an invite already in somebody's inbox. |
| `tenants` | preserve | named | 5 | The customer. D2 preserves it, and every per-tenant transaction below is scoped by a row in this table. |
| `users` | preserve | named | 50 | Staff accounts. D2 preserves them, and every audit row and human observation the reset keeps points at one. |
| `verification_requests` | preserve | inferred | 0 | Employer verification correspondence. Not scoring. |
| `webhook_events` | preserve | inferred | 0 | Payment webhook idempotency keys. Deleting one re-opens a double-grant on redelivery. |
| `assessment_conversations` | purge | named | 574 | One assessment run, and also the invitation. A COMPLETED one is legacy data and is purged. An ACTIVE one is not: spec-doc6 D2 says in as many words that applications are not interrupted, and deleting a conversation somebody is part-way through is the most direct way to interrupt one. The survey counted 198 active at the time this predicate was added, 197 of them seeded demo data and one a real candidate, and the one is the reason. Preserving it costs nothing: gate G1 blocks that candidate's evaluation until the job is re-defined, so they finish typing and their scoring waits, which is strictly better than losing their answers mid-assessment. |
| `assessment_messages` | purge | named | 240 | The interview transcript. Part of the assessment D2 deletes. The candidate's own words are human authorship and survive only in the export, which is why the survey raises this for sign-off rather than treating it as routine. |
| `candidate_questions` | purge | named | 45 | Per-candidate questions generated against the old scorecard. |
| `candidate_technical_questions` | purge | named | 15440 | Per-candidate technical questions and the rubric written with each. Scored against a scorecard that is being replaced. |
| `context_chunks` | purge | inferred | 0 | Only the chunks cut from assessment transcripts, whose source rows are being deleted. JD and resume chunks are preserved because their sources are, and they are re-embedded by app.scripts.reembed. |
| `evaluations` | purge | named | 0 | One Miti run: the five dimension bands, the aggregate, the triangulation and the gate verdicts. The first table D2 names. |
| `evidence_claim_links` | purge | named | 58 | The stance edges between a claim and its evidence. Machine output of the old extraction pass. |
| `evidence_claims` | purge | named | 15 | AI-generated candidate intelligence produced by the old logic. Its supporting evidence cites transcripts being purged. |
| `evidence_items` | purge | named | 58 | Evidence references into resumes and transcripts, extracted by the old pipeline. A resume-sourced item survives its source but was tiered and scored under the retired rubric, so it is regenerated rather than kept. |
| `functional_skills_reports` | purge | named | 577 | The PRISM Report itself: the delivered, immutable artefact D2 names. |
| `job_company_dna_bindings` | purge | inferred | 0 | The frozen binding of a job to the Company DNA version and scorecard version its evaluations were run under. The scorecard it names is being archived, so the binding would assert a freeze over a matrix that no longer exists. It is re-created when the job's new scorecard is locked. |
| `job_competencies` | purge | named | 542 | The Tatva matrix: Must-have, Nice-to-have and Behavioural criteria. D2 purges old scorecards. |
| `job_matching_categories` | purge | inferred | 13 | The coarse resume-only categories the AI Score was computed against. Regrading against the old categories would compute a new number from retired criteria, which is the least detectable kind of wrong. |
| `report_dimensions` | purge | named | 7845 | Per-dimension grades inside a PRISM Report. D2 purges the reports and their artefacts. |
| `report_skill_evidence` | purge | named | 0 | Structured evidence behind a report dimension. It cites assessment messages that are themselves being purged, so keeping it would leave citations pointing at nothing. |
| `technical_questions` | purge | inferred | 680 | The retired per-job preset bank. CLAUDE.md kept this table unread so 'what was this candidate asked' stayed answerable for existing reports; those reports are themselves being purged, so the reason no longer holds and the history moves to the export. |
| `job_candidate_links` | reset | named | 1077 | The application. Preserved by D2, including its timestamps. The pre-screen grade written onto it is not: match_score, its rationale, the four-parameter breakdown and the tier are all old machine output. |
| `jobs` | reset | named | 35 | The job and its JD are preserved and the posting is NOT touched. The scorecard approval stamps are cleared so that gate G1 can block evaluation until the job is re-defined, which is the enforcement D2 says to reuse rather than duplicate. Whether G1 is reachable from a live path is checked before the purge runs, not assumed. |

Bucket totals: preserve 30, purge 16, reset 2, detach 2, infrastructure 1, 51 tables in all.

## By tenant

| Tenant | Status | Demo | Jobs | Candidates | Applications | Rows to purge |
|---|---|---|---:|---:|---:|---:|
| Acme Corp | active | no | 2 | 36 | 39 | 193 |
| ACRM Corp | active | yes | 10 | 33 | 321 | 8132 |
| Sarkar Corp | active | yes | 12 | 37 | 392 | 9841 |
| Specter & Co. | active | yes | 10 | 33 | 325 | 7906 |
| TechStart Inc. | active | no | 1 | 0 | 0 | 15 |

### Purged rows per tenant, per table

| Tenant | `report_dimensions` | `report_skill_evidence` | `functional_skills_reports` | `evidence_claim_links` | `evidence_claims` | `evidence_items` | `assessment_messages` | `assessment_conversations` | `candidate_questions` | `candidate_technical_questions` | `technical_questions` | `job_competencies` | `job_matching_categories` | `context_chunks` | `job_company_dna_bindings` | `evaluations` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Acme Corp | 50 | 0 | 4 | 0 | 0 | 0 | 0 | 2 | 0 | 60 | 40 | 32 | 5 | 0 | 0 | 0 |
| ACRM Corp | 2354 | 0 | 177 | 58 | 15 | 58 | 160 | 177 | 20 | 4760 | 200 | 153 | 0 | 0 | 0 | 0 |
| Sarkar Corp | 3051 | 0 | 216 | 0 | 0 | 0 | 38 | 216 | 25 | 5860 | 240 | 187 | 8 | 0 | 0 | 0 |
| Specter & Co. | 2390 | 0 | 180 | 0 | 0 | 0 | 42 | 179 | 0 | 4760 | 200 | 155 | 0 | 0 | 0 | 0 |
| TechStart Inc. | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 0 | 0 | 0 |

## By job

`published` jobs are NOT unpublished by the purge and their applications are not interrupted. Their scorecard is archived into the export and their approval stamps are cleared, so gate G1 blocks evaluation until the job is re-defined.

| Tenant | Job | Status | Published | Scorecard approved | Competencies | Categories | Applications | Reports | Evaluations |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| Acme Corp | Full-Stack Developer | questions_pending_review | yes | no | 16 | 0 | 3 | 2 | 0 |
| Acme Corp | Senior Backend Engineer | questions_pending_review | yes | no | 16 | 5 | 36 | 2 | 0 |
| ACRM Corp | AI / Generative AI Engineer | ready_for_candidates | yes | yes | 15 | 0 | 32 | 24 | 0 |
| ACRM Corp | Data Analyst | questions_pending_review | yes | no | 15 | 0 | 32 | 16 | 0 |
| ACRM Corp | Data Engineer | questions_pending_review | yes | no | 16 | 0 | 32 | 20 | 0 |
| ACRM Corp | DevOps / Cloud Engineer | questions_pending_review | yes | no | 15 | 0 | 32 | 16 | 0 |
| ACRM Corp | Full Stack Developer (.NET) | questions_pending_review | yes | no | 15 | 0 | 32 | 13 | 0 |
| ACRM Corp | Java Backend Developer | questions_pending_review | yes | no | 16 | 0 | 32 | 14 | 0 |
| ACRM Corp | Machine Learning Engineer | questions_pending_review | yes | no | 16 | 0 | 32 | 19 | 0 |
| ACRM Corp | MERN Stack Developer | questions_pending_review | yes | no | 15 | 0 | 33 | 22 | 0 |
| ACRM Corp | Python Backend Developer | questions_pending_review | yes | no | 15 | 0 | 32 | 19 | 0 |
| ACRM Corp | React Frontend Developer | questions_pending_review | yes | no | 15 | 0 | 32 | 14 | 0 |
| Sarkar Corp | AI Engineer | questions_pending_review | yes | no | 16 | 8 | 33 | 19 | 0 |
| Sarkar Corp | AI / Generative AI Engineer | questions_pending_review | yes | no | 15 | 0 | 33 | 20 | 0 |
| Sarkar Corp | Data Analyst | questions_pending_review | yes | no | 15 | 0 | 32 | 23 | 0 |
| Sarkar Corp | Data Engineer | questions_pending_review | yes | no | 18 | 0 | 32 | 17 | 0 |
| Sarkar Corp | DevOps / Cloud Engineer | questions_pending_review | yes | no | 15 | 0 | 32 | 19 | 0 |
| Sarkar Corp | Full Stack Developer (.NET) | questions_pending_review | yes | no | 15 | 0 | 32 | 18 | 0 |
| Sarkar Corp | Java Backend Developer | questions_pending_review | yes | no | 15 | 0 | 32 | 20 | 0 |
| Sarkar Corp | Machine Learning Engineer | ready_for_candidates | yes | yes | 15 | 0 | 37 | 18 | 0 |
| Sarkar Corp | MERN Stack Developer | questions_pending_review | yes | no | 16 | 0 | 32 | 13 | 0 |
| Sarkar Corp | Part A Pipeline Validation Engineer | questions_pending_review | yes | no | 16 | 0 | 33 | 22 | 0 |
| Sarkar Corp | Python Backend Developer | questions_pending_review | yes | no | 16 | 0 | 32 | 14 | 0 |
| Sarkar Corp | React Frontend Developer | questions_pending_review | yes | no | 15 | 0 | 32 | 13 | 0 |
| Specter & Co. | AI / Generative AI Engineer | questions_pending_review | yes | no | 16 | 0 | 32 | 14 | 0 |
| Specter & Co. | Data Analyst | questions_pending_review | yes | no | 15 | 0 | 32 | 17 | 0 |
| Specter & Co. | Data Engineer | questions_pending_review | yes | no | 16 | 0 | 33 | 11 | 0 |
| Specter & Co. | DevOps / Cloud Engineer | questions_pending_review | yes | no | 16 | 0 | 32 | 19 | 0 |
| Specter & Co. | Full Stack Developer (.NET) | questions_pending_review | yes | no | 15 | 0 | 33 | 21 | 0 |
| Specter & Co. | Java Backend Developer | questions_pending_review | yes | no | 15 | 0 | 33 | 20 | 0 |
| Specter & Co. | Machine Learning Engineer | questions_pending_review | yes | no | 15 | 0 | 33 | 25 | 0 |
| Specter & Co. | MERN Stack Developer | questions_pending_review | yes | no | 16 | 0 | 32 | 19 | 0 |
| Specter & Co. | Python Backend Developer | questions_pending_review | yes | no | 16 | 0 | 33 | 16 | 0 |
| Specter & Co. | React Frontend Developer | questions_pending_review | yes | no | 15 | 0 | 32 | 18 | 0 |
| TechStart Inc. | PPI validation engineer | questions_pending_review | yes | no | 15 | 0 | 0 | 0 | 0 |

## Edge cases

| Finding | Rows | Detail |
|---|---:|---|
| Candidates with an evaluation but no resume | 0 | Their pre-screen grade cannot be regenerated: --regrade requires a resume, so these applications end the reset with no grade at all until a resume is uploaded. |
| Published jobs whose scorecard is being archived | 35 | The posting stays live and applications keep arriving. Their approval stamps are cleared so the job reads as pending review, and the INTENDED state is that gate G1 then blocks evaluation until a human approves a new scorecard. Whether G1 can actually do that is the first section of this document, and the purge refuses while it cannot. |
| Jobs stamped as generated with zero scorecard rows | 0 | The failure this project has already had once: a timestamp asserting work that produced no rows. These jobs were already unusable before the reset and the reset does not change that. |
| Assessment conversations still active | 198 | Purging one ends an interview a candidate may be part way through. They keep their application; they lose the conversation and must be re-invited once the job's new scorecard is approved. |
| Report dimensions with no report | 0 | Already orphaned before the reset. Counted so the purge total can be reconciled against the report count rather than appearing to differ. |
| Report skill evidence whose conversation is gone | 0 | Same category: pre-existing orphans, counted so the totals add up. |
| Evaluations that never produced a report | 0 | Scoring that started and did not finish. Purged with the rest. |
| Human dispositions that referenced a purged evaluation | 0 | These would have been CASCADE-deleted before migration 0062. The purge detaches them instead, copying the job and application context onto the row first. |
| Human calibration records that referenced a purged evaluation | 0 | Same treatment, same reason. |
| Team Review remarks preserved | 0 | Every one of them. This table has no reference to an evaluation, so no cascade can reach it; the count is recorded before and after so the claim is a measurement rather than an assertion. |
| Applications carrying an old pre-screen grade | 1075 | Every one of these has its match_score, rationale, breakdown and tier cleared, and is the input set --regrade works through. |
| Applications with a resume, eligible for regrading | 1077 | The work plan --regrade reports is computed from this set. |

## Schema findings the reset looked at and did not change

Both are the same class of defect migration 0062 fixed: a property of the schema that a survey counting only rows would have walked past.

| Finding | Present | Detail |
|---|---:|---|
| Team Review remarks are deleted with their author | 1 | `candidate_team_reviews.reviewer_user_id` is ON DELETE CASCADE, so deleting a user row erases every remark they wrote. RBAC section 29 requires a remark to preserve its author, and the comparable column elsewhere in this schema, `review_dispositions.decided_by`, is ON DELETE RESTRICT for exactly that reason. NOT CHANGED BY THIS RESET, and deliberately: no code path hard-deletes a user, the only route is `DELETE /admin/tenants/{id}` which erases the whole tenant on purpose, and changing this key to RESTRICT would make that deletion fail on its own cascade. The fix is a product decision about what deleting a user means, not a line in a purge script. |
| Team Review ratings still check the retired five-label scale | 0 | The CHECK on `candidate_team_reviews.rating` accepts very_high, high, medium, low and developing. The product has had ONE four-grade scale since 2026-07-30 (Highly Matching, Matching, Moderately Matching, Not Matching) and `services/rating.py` is its single source. A reviewer submitting the current vocabulary is refused by the database. NOT CHANGED BY THIS RESET: it is a data-vocabulary migration with existing rows behind it, and it belongs with whoever owns the Team Review surface. |

## Object store reconciliation

**NOT PERFORMED.** No S3 bucket is configured (S3_BUCKET is empty), so the bucket cannot be listed. The database side of the reconciliation is reported below and the bucket side is NOT PERFORMED.

| Direction | Count |
|---|---:|
| Profile rows pointing at an object URI | 0 |
| Profile rows on a legacy provider | 44 |
| Profile rows with no resume URI at all | 1 |

The bucket half of this reconciliation is unanswered, not clean. Re-run the survey with `S3_BUCKET` set and AWS credentials available to settle it.

## What runs next

```
# 1. Export, and test-restore it in the same command. An export that has
#    never been restored is not a backup, so --purge --confirm refuses
#    one whose manifest does not say restore_verified: true. The scratch
#    database must be empty, migrated to head, and reachable as a
#    superuser (the restore turns foreign-key triggers off for the load).
python -m app.scripts.legacy_reset --export \
    --scratch-database-url postgresql+asyncpg://<user>:<pw>@<host>/<empty_db>

# 2. Dry run. This is the default, and it changes nothing.
python -m app.scripts.legacy_reset --purge

# 3. Apply. Refuses while gate G1 is unreachable from a live path,
#    while the database is behind the migrations, while a table is
#    unclassified, and while the export is unverified or stale.
python -m app.scripts.legacy_reset --purge --confirm \
    --export-dir <the directory step 1 printed> \
    --actor '<operator name>' [--actor-user-id <users.id>]

# 4. Re-embed BEFORE regrading: the regrade reads retrieval, and
#    re-embedding changes what retrieval returns.
python -m app.scripts.reembed --dry-run
python -m app.scripts.reembed --confirm        # needs VOYAGE_API_KEY

# 5. Regrade. Plan first; --confirm needs ANTHROPIC_API_KEY and
#    VOYAGE_API_KEY and refuses without them.
python -m app.scripts.legacy_reset --regrade
python -m app.scripts.legacy_reset --regrade --confirm
```

Steps 4 and 5 cannot run in this phase: there is no Anthropic key and no Voyage key. Both are listed in `VERIFICATION_PENDING.md` with their measured work plans and the command that settles each one.
