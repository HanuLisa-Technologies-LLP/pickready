# Open decisions and unresolved contradictions

**Status:** current. **Reviewed:** 2026-10-14.
**Rule:** nothing on this page is approved. A default named here is what the code does today, not a ruling. When the owner answers, move the answer to [REGISTER.md](REGISTER.md) and delete the row here in the same change.
**Question format for the owner:** state the current behaviour, the problem, a real example, what each option causes, and a recommendation ([../engineering/documentation-policy.md](../engineering/documentation-policy.md)).

## A. Follow-ups to the 2026-10-14 decisions

| ID | Open point | Why it matters | Default until ruled |
|---|---|---|---|
| Q-01 | **Approval lifecycle names.** J-10 adds states between DRAFT and PUBLISHED (submitted, in HR review, returned, approved). The names, and how they sit beside the six states in `ck_jobs_lifecycle_state`, are not decided. | The RBAC spec 17 names `SENT_TO_HIRING_MANAGER`, `IN_REVIEW`, `FINALIZED`; the code collapsed them in 2026-09-25. Reusing the wrong name revives a deleted path. | None. Nothing is built. |
| Q-02 | **Which roles are "HR/Recruitment Manager".** The code has two roles, `hr_manager` and `recruitment_manager`. Does each carry approval? | Example: a company with only a Recruitment Manager could never approve if only `hr_manager` did. | Assumed both, because the owner wrote the pair. Not approved. |
| Q-03 | **What is a "material change" and what is "hiring-relevant SWOT".** J-13 says cosmetic formatting and internal notes do not cancel approval. The field list is not written. | Without a list, two engineers will draw the line differently and approvals will cancel or survive unpredictably. | None. |
| Q-04 | **SWOT edit on a published job.** J-16 allows SWOT edits after publishing; J-13 says a material SWOT change cancels approval. For a job already live, does that unpublish it? | Example: the job is live, the Hiring Manager rewrites a Weakness, approval is cancelled. Candidates are applying. | None. |
| Q-05 | **Two freeze moments.** JD and Skills freeze at the first genuine application (J-03, unchanged). The SWOT freezes at the first assessment start (J-16). Confirm both stand. | The first application usually comes days before the first start, so the SWOT stays editable longer than the Skills. | Both as written. |
| Q-06 | **"Mandatory before assessment".** The SWOT freezes at first start. Must it also exist before invitations go out? | If it only has to exist at start, a candidate can be invited and then blocked at the door. | Not enforced anywhere yet. |
| Q-07 | **Reassignment and Hiring Manager assignment.** J-10 has the Recruiter assign a Hiring Manager. Today there is no assignment screen, and who may reassign is not stated. | The flow cannot start without it. | No UI exists. |
| Q-08 | **Coding checker details (S-20).** Which languages; which static checker per language; whether the candidate sees checker output while writing (the old Run button); the split between checker and AI review in the score; the recruiter wording ("reviewed, not run"). Earlier on 2026-10-14 the owner said the sandbox was mandatory and then said it was not necessary; the later statement is recorded. Please confirm. | The score drives hiring decisions and must not change silently. | The old 70 hidden-test and 30 review split cannot stand without tests. |
| Q-09 | **Yukti five-part model (S-21).** Who validates it and on what evidence; what replaces the application-answers part (including "why does this role interest you"); what "policy versioning" stores. | The current ranking would change. | Six-part weights stay in force. |
| Q-10 | **Email after SES-only (I-20).** Is `contact@readypick.ai` already a verified SES sender (the pilot sender today is `noreply@readypick.ai`)? What do local development and tests use once SMTP is gone? What is `manjunath@readypick.ai` the primary mailbox for (inbound routing, support notices)? Does `noreply@readypick.ai` retire? | A wrong sender fails every platform email, including security codes. | The pilot keeps `noreply@readypick.ai`. |
| Q-11 | **Injection screen behaviour (A-20).** When a retrieved chunk is flagged, is it quarantined (dropped, recorded) or does it fail the retrieval? | Failing the retrieval would let one poisoned line disable an assessment. | The 2026-08-18 design: quarantine and record. |
| Q-12 | **Pricing details nobody ruled on.** Plan change timing (the code and PRD say the next billing cycle; the 2026-09-30 ruling is silent); GST (18 percent in code, not in the ruling); what an employer above Pro does (the billing page points to a mailbox, see Q-10). | These are live behaviours customers see. | As implemented. |

## B. Carried over from earlier records, never ruled

| ID | Open point | Default today |
|---|---|---|
| Q-20 | A designed logo mark (today a drawn V). | The drawn V. |
| Q-21 | `LLM_KEY_ENCRYPTION_SECRET` is held and granted to no service. | Held. |
| Q-33 | **Two frontend advisories left open after the 2026-10-14 dependency fix.** `braces` 3.0.3 (high, no patched version published) and `postcss-selector-parser` 6.1.4 (medium, fix is a major bump that arrives with a Tailwind 3 upgrade). Neither fails the Security scan. Re-scan when a patch appears. | Open, accepted. |
| Q-23 | The `impeccable` design tool is unpinned by hash and gates CI. | Unpinned. |
| Q-24 | Inbound reply email is inert on pilot (`INBOUND_EMAIL_DOMAIN` empty); `transcribe_enabled` off until the deploy stage. | Inert, off. |
| Q-25 | Five one-tenant tasks still bypass RLS pending a real-Postgres proof (`generate_job_swot`, `draft_job_skills`, `execute_coding_submission`, `transcribe_voice_answer`, `process_assessment_video`). | Bypass, with a written reason each. |
| Q-26 | Leftovers: `agent_execution_traces` has no writer; `RequestTrace.add_cost` has no caller; the older per-key JD path (`jd_generation`, `jd_generation_system.txt`) survives; column `hm_access_granted` has no model attribute; the request coalescer has four uncalled functions (delete them or correct the note that keeps them). | Kept. |
| Q-32 | Technical debt left open by the 2026-09 audits, never ruled: pilot is single-AZ (`multi_az = false`; must flip before real tenant data); `audit_log` has no index serving the cross-tenant ordered scan; the two JSON-LD payloads (job apply page, employer page) are client-rendered; no distributed tracing across dispatch hops and no dashboards beyond CloudWatch defaults; retrieval quality unmeasured (golden set 60 of 300, none human verified); shadow eval, tenant canary, drift watch and the candidate explanation surface are designed, not built. | Accepted debt. |
| Q-30 | The candidate-facing explanation of how automated logic reaches a result (a legal and product question with an Article 15(1)(h) driver, raised in the 2026-09-08 upgrade brief). | No ruling on record; none built. |
| Q-31 | Durable execution for long workflows (brief item W5.2: Lambda durable functions or DBOS). | No ruling on record; not built. |
| Q-28 | `ENVIRONMENT=pilot` on the live site makes `Settings.is_production` false, so five guards written for production are inert (JSON log rendering, refusal of the `record` dispatch backend, refusal of pseudo-random embeddings without a key, the demo-seed opt-in, the diagnostics middleware). Two are load-bearing and masked only by correct configuration. Recorded 2026-09-17 ([SECURITY_REPORT SEC-24](../history/AUDITS.md)); never ruled. Options: set `ENVIRONMENT=production` (flips all five at once) or split "serves real users" from "release channel". | Guards inert; configuration keeps them safe. |
| Q-29 | Never verified (2026-09-17 security pass): the CSP against the Razorpay checkout and the Google sign-in popup; whether any secret still holds `PLACEHOLDER_NOT_CONFIGURED`; rate limits under load. | Unverified. |
| Q-27 | Documentation figures that were never re-measured: test counts and baselines recorded on 2026-08-29 ([../history/AUDITS.md](../history/AUDITS.md)); `DESIGN.md`, `PRODUCT.md` and `docs/spec/RBAC_SPECIFICATION.md` and the Runbook were scanned for retired product terms only, and `DESIGN.md` still describes the original R+P logo as the brand colour source. | Treat as stale. |

## C. Contradictions found by the 2026-10-14 audit

"Resolved" means the latest owner statement or the code was taken as the winner and the document corrected. "Open" means a person must choose.

| ID | Contradiction | Result |
|---|---|---|
| X-01 | The owner said the coding sandbox "should not stay on hold, it is mandatory", then that it is "not necessary". | Resolved by the later statement (S-20). Please confirm (Q-08). |
| X-02 | Approval wanted back (J-10) while a Hiring Manager's SWOT edits are allowed after publish (J-16). | Open (Q-04). |
| X-03 | RBAC spec (rank 1) has no HR approval step and gives HR Manager "Publish YES*". | Resolved: J-10 to J-12 amend it. The filed spec text is left unedited; an amendment note points to the register. |
| X-04 | The 2026-08-23 rule gave the PRISM subtitle as "Predictive Role Intelligence & Suitability Mapping"; the 2026-09-20 rename and the code say "Evidence-Based ...". | Resolved: the code and the later rule win (P-04). |
| X-05 | Documents said the Runbook is v1.1; the file is v1.4. | Resolved: v1.4. |
| X-06 | The ESD named the approval state machine and the matching pipeline as "unchanged and authoritative". Both were replaced in 2026-09-25. | Resolved: ESD rewritten. |
| X-07 | Deployment runbooks said "nothing has been executed" while pilot has been live since 2026-09. | Resolved: status banners corrected. |
| X-08 | Instruction files claimed migrations 0001 to 0130; the head is 0136. | Resolved. |
| X-09 | The 2026-09-06 rule allowed SMTP or SES; I-20 allows SES only. | Resolved by I-20; code pending. |
| X-10 | The "MANDATORY graphify query" instruction against W-20. | Resolved by W-20; Claude hook removed, Codex hook file pending. |
