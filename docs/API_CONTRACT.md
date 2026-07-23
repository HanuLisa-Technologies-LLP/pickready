# PickReady — Internal Build Contract (API routes, task names, file ownership)

This is the coordination contract between parallel build tracks. Backend routers MUST expose exactly these routes; the frontend API client MUST call exactly these routes. Deviations require updating this file.

Base URL: `/api/v1`. Auth: JWT access token in an httpOnly cookie `pr_access` (+ `pr_refresh`); backend also accepts `Authorization: Bearer <token>`. Candidate-portal sessions use a distinct JWT audience (`pickready:candidate`).

## Auth (`/auth`)
| Method | Path | Body → Response |
|---|---|---|
| POST | `/auth/otp/request` | `{identifier, channel: "email"\|"sms", audience?: "internal"\|"candidate"}` → `{challenge_id}` (also used for first-login dual OTP: call once per channel) |
| POST | `/auth/otp/verify` | `{challenge_id, code}` → sets cookies, returns `{user: {id, role, tenant_id, full_name, email, email_verified, phone_verified}}` |
| POST | `/auth/refresh` | → rotates access cookie |
| POST | `/auth/logout` | → clears cookies |
| GET | `/auth/me` | → `{user}` |

## Super Admin console (`/admin`) — super_admin only, audit-logged
| POST | `/admin/tenants` | `{name, domain, client_email, client_phone}` → tenant + client user |
| GET | `/admin/tenants` | list |
| POST | `/admin/tenants/{tenant_id}/staff` | `{email, full_name, role: "hr_manager"\|"recruiter", phone?}` |
| GET | `/admin/permissions?tenant_id=` | list role_permissions (tenant or global template when omitted) |
| PUT | `/admin/permissions` | `{tenant_id?, entries: [{role, capability, allowed}]}` |
| GET | `/admin/audit-log?tenant_id=&limit=` | list |

## Client company (`/companies`)
| GET/PUT | `/companies/me` | company page `{brief, culture, policies, benefits}` |
| GET/POST | `/companies/me/hiring-managers` | POST `{email, full_name, phone?, approval_level?}` — max 5 enforced |
| PUT | `/companies/me/approval-levels` | `{config: {requested: {active, approver_user_id}, recommended: {...}, approved: {...}, ratified: {...}}}` |
| GET/POST/PUT | `/companies/me/email-templates` | template CRUD `{name, subject, body}` |

## Jobs & approval FSM (`/jobs`)
| POST | `/jobs` | JD create (hiring manager) — `{title, department, level, requirement_period, jd: {reporting_to, reportees, role, responsibilities, accountabilities, education, skills: [], experience_years}}` |
| GET | `/jobs` | role-scoped list (HR/Recruiter only see ratified) |
| GET | `/jobs/{id}` | detail + current status |
| POST | `/jobs/{id}/submit` | draft → first active level |
| POST | `/jobs/{id}/approve` | `{decision: "approved"\|"rejected", remarks?}` — actor must be assigned approver of the job's current level |
| GET | `/jobs/{id}/approvals` | transition history (incl. explicit skipped rows) |
| PUT | `/jobs/{id}/compensation` | `{compensation: {...}}` (HR, post-ratification) |
| PUT | `/jobs/{id}/jd` | HR JD edits post-ratification |

## Candidates & pipeline (`/candidates`)
| POST | `/candidates/jobs/{job_id}/upload-resume` | multipart `file` + `{email, full_name?, phone?}` → creates candidate+profile+link (source=fresh), enqueues parse_resume |
| GET | `/candidates/{candidate_id}/profile` | full Profile (resume fields + 40 aspects + verification) — review screen |
| POST | `/candidates/links/{link_id}/grant-access` | HR grants Hiring Manager access |
| POST | `/candidates/links/{link_id}/decision` | `{status: "rejected"\|"shortlisted"\|"hold", remarks?}` — hold ⇒ remarks mandatory (422 otherwise) |
| POST | `/candidates/links/{link_id}/status` | `{status: "rejected"\|"shortlisted"\|"offered"\|"joined", remarks?}` pipeline update |
| POST | `/candidates/links/{link_id}/interviews` | `{scheduled_at (ISO), notes?}` → .ics email via tenant domain |
| GET | `/candidates/jobs/{job_id}` | all links for a job with score/tier/status |

## Matching (`/matching`)
| POST | `/matching/jobs/{job_id}/run` | enqueue `pickready.run_matching` → `{task: "queued"}` |
| GET | `/matching/jobs/{job_id}/results` | links ordered by score: `[{link_id, candidate, source, match_score, tier, rationale}]` |

## Outreach & employer verification (`/verification`)
| POST | `/verification/outreach` | `{job_id, candidate_ids: []}` → sends 40-aspect outreach emails (fresh candidates only) |
| GET | `/verification/profile/{profile_id}` | verification request statuses |
| POST | `/verification/requests/{id}/override` | `{reason}` — HR override, audit-logged |
| GET | `/verification/form/{token}` | PUBLIC — employer form schema + candidate name |
| POST | `/verification/form/{token}` | PUBLIC — `{designation, doj, doe, last_drawn_ctc, last_drawn_gross, noc_status, exit_formalities_complete, bgv_status, proofs_details, prior_experience_details}` |
| POST | `/verification/inbound-email` | webhook (Resend inbound) → enqueue LLM reply parsing |

## Candidate portal (`/portal`) — candidate audience
| GET | `/portal/outreach/{token}` | PUBLIC — what's requested (fields to fill, 40 aspects minus already-covered) |
| POST | `/portal/outreach/{token}` | multipart: personal fields, `aspects` JSON, `resume` file, `employer_emails: []` (≤3) |
| GET | `/portal/jobs` | jobs from tenants that have contacted this candidate |
| POST | `/portal/jobs/{job_id}/apply` | multipart fresh `resume` (never reused — FR-9.2) |
| GET | `/portal/applications` | own application stage statuses |

## Dashboard (`/dashboard`)
| GET | `/dashboard/summary` | `{jobs: [{job_id, title, databank_matched, fresh_sourced, shortlisted, offered, joined}], total_jobs_worked}` — scoped to caller |

## Celery task names (enqueue with `celery_app.send_task(name, args=[...])`)
`pickready.send_email`, `pickready.send_sms`, `pickready.run_matching`, `pickready.parse_resume`, `pickready.send_verification_requests`, `pickready.parse_verification_reply`, `pickready.refresh_dashboard_views` — signatures in `backend/app/workers/celery_app.py`.

## File ownership (parallel tracks — do not edit outside your area)
- **Foundation (done)**: `backend/app/models/*`, `backend/app/core/*`, `backend/app/main.py`, `backend/app/services/capabilities.py`, `backend/app/workers/celery_app.py`
- **Track A (API)**: `backend/app/schemas/*`, `backend/app/api/*`, `backend/app/services/{rbac,approval_fsm,otp,audit,tiers}.py`, `backend/tests/*`
- **Track B (pipelines)**: `backend/app/workers/tasks.py` (+ helpers under workers/), `backend/app/services/{llm_router,matching,embeddings,resume_parsing,verification_parsing,email_render}.py`, `backend/alembic/*`, `backend/alembic.ini`, `backend/app/scripts/*`
- **Track C (frontend)**: everything under `frontend/`
- **Track D (infra)**: `backend/Dockerfile`, `frontend/Dockerfile`, `infra/*`

## Capability names
Use the constants in `backend/app/services/capabilities.py` — never inline strings, never `if role == ...` in business logic.
