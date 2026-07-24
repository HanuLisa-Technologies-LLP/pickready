# PickReady — Internal Build Contract (API routes, task names, file ownership)

> **REVISION 3 (2026-07-24) — PRD v1.0 alignment (simplification).** Supersedes the
> parts of rev 2 noted inline as ~~struck / SUPERSEDED~~. Source: PRD v1.0 §4 (FINAL)
> + the user's four settlements (see build-log). Key changes:
> - **Flat staff roles.** HR Manager / Recruiter / Hiring Manager are EQUAL — all
>   hold `create_job`, share ONE candidate pool, and see the same actions. The
>   per-role permission matrix is flattened (all three resolve to the same
>   capability set). `if role == …` remains banned (claude.md rule 3).
> - **Direct job publish (no approval chain).** `POST /jobs` now PUBLISHES
>   immediately: `create_job` runs `fsm.apply_direct_publish` (draft → `ratified`
>   in one step, the four approval levels logged as `skipped`), then kicks off
>   Databank matching. The multi-level approval FSM is BYPASSED, not deleted, so
>   `POST /jobs/{id}/submit|approve` and `GET /jobs/{id}/approvals` still exist but
>   are **SUPERSEDED** (no longer part of the normal flow). `ratified_at` remains
>   the single "published/terminal" marker across the codebase — there is no
>   separate `published` JobStatus.
> - **Public job link + open application.** A published job is reachable at
>   `picready.com/{job_uuid}`; the canonical public read is `GET /jobs/public/{job_id}`
>   (unauthenticated, published-only, public fields only). Candidates apply OPEN
>   (public register → 40-aspect questionnaire → resume upload OR reuse → apply),
>   no longer outreach-gated. Outreach (Section 5) still exists but is no longer
>   the only entry.
> - **AI JD generation** — `POST /jobs/generate-jd` expands a short brief into a
>   full structured JD via the LLM router (FR-3.3 Path A).
> - **Resume reuse** — a candidate's last stored resume is reused across
>   applications (`reuse_previous=true` on apply). REVERSES the old rule 6
>   fresh-upload-only requirement (claude.md rule 6 updated 2026-07-24). Each
>   application still mints its own Profile + aspects; only the resume FILE carries.
> - **Email over SMTP** — all outbound email is sent over SMTP from the backend
>   (`SMTP_*` env), replacing the Mailtrap HTTP Sending API (claude.md rule 5).
>   Route shapes are unchanged; only the transport moved.

> **REVISION 2 (2026-07-23) — role model correction, unified login, 4-parameter ranking.**
> Pickready.docx is the source of truth. Key corrections over ESD.md §4/PRD §4:
> - **Owner** (formerly "Super Admin"): the platform itself (Hanulisa). Exactly ONE account — `manjuchro@gmail.com` (settings.owner_email). API layer rejects any other identity holding the owner role. Owner only onboards tenants and edits permission templates; Owner does NOT create staff.
> - **Client-org hierarchy**: Client → HR Manager → Recruiter → Hiring Manager, ALL members of the client organization (none are Hanulisa staff). The Client (or an HR Manager granted `manage_staff`) creates staff of any of the 3 sub-roles. Only Hiring Manager is capped at 5; HR/Recruiter uncapped.
> - **Three portals, ONE login**: Owner portal (`/admin`), Client-org portal (`/org` — shared URL space for all 4 org roles, nav/actions driven by capabilities), Candidate portal (`/portal`). Single login flow with a "choose your workspace" step ONLY when an identifier matches multiple users.
> - **4-parameter ranking** replaces the plain 0–100 re-rank (retrieval stages unchanged): skills_match 35% / experience_relevance 30% / role_alignment 20% / education_fit 15%, each 1–10 + comment, plus a holistic overall (weighted avg, 1 decimal) + 5th comment. Tier = assign_tier(overall × 10), boundary rule unchanged. Stored in `job_candidate_links.match_breakdown_json`.
> - **Aspect numbering contract** (aspects_json keys, portal questionnaire): **8–13 = education & qualifications** (8 highest degree level, 9 specialization, 10 institution, 11 year of completion, 12 professional certifications, 13 additional qualifications), **23 = current/most recent designation and core duties**, **40 = Databank matching consent**. Backend scoring pulls aspects 23 and 8–13 specifically when present; resume text is fallback only.

## Auth contract changes (rev 2)
| POST | `/auth/otp/verify` | single matching user → cookies + `{user, capabilities: [...]}`. MULTIPLE matching users (cross-tenant/role) → NO cookies, returns `{contexts: [{user_id, role, tenant_id, tenant_name, portal}], context_token}` |
| POST | `/auth/select-context` | `{context_token, user_id}` → cookies + `{user, capabilities}` (context_token: short-TTL signed JWT proving OTP success; single-use) |
| GET | `/auth/me` | → `{user, capabilities: [...]}` — capabilities resolved from RBAC engine (owner gets `["*"]`) |

## Staff management (rev 2 — replaces `/companies/me/hiring-managers` and `/admin/tenants/{id}/staff`, both REMOVED)
| GET | `/companies/me/staff` | all staff users of the tenant `[{id, email, full_name, phone, role, status, approval_level?}]` |
| POST | `/companies/me/staff` | `{email, full_name, phone?, role: "hr_manager"\|"recruiter"\|"hiring_manager", approval_level?}` — capability `manage_staff`; server-side 409 when adding a 6th hiring_manager; hiring_manager rows still mirrored into `hiring_managers` table |
| DELETE | `/companies/me/staff/{user_id}` | deactivate staff account (status=disabled), capability `manage_staff` |

## Matching results (rev 2)
`GET /matching/jobs/{job_id}/results` entries add `breakdown`:
```json
{"skills_match": {"score": 8, "comment": "..."}, "experience_relevance": {"score": 7, "comment": "..."},
 "role_alignment": {"score": 9, "comment": "..."}, "education_fit": {"score": 6, "comment": "..."},
 "overall": {"score": 7.7, "comment": "holistic — not a concatenation"}}
```
`match_score` column stays populated as `overall × 10` (sorting/dashboard unchanged); `match_rationale` = overall comment. Numerical values remain API/audit data and are not rendered in the frontend.


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

## Jobs (`/jobs`) — flat roles, DIRECT PUBLISH (rev 3)
| Method | Path | Body → Response |
|---|---|---|
| POST | `/jobs` | JD create by ANY staff role holding `create_job` — `{title, department, level, requirement_period, jd: {reporting_to, reportees, role, responsibilities, accountabilities, education, skills: [], experience_years}}`. **PUBLISHES immediately** (draft → `ratified` via `fsm.apply_direct_publish`, approval levels logged `skipped`), enqueues `pickready.run_matching`, and returns `JobOut` incl. `public_url` (`picready.com/{job_uuid}`). |
| POST | `/jobs/generate-jd` | **NEW (rev 3, FR-3.3 Path A)** — `{title, requirements?, skills?[], experience?, company_context?, department?, level?}` → returns a generated `jd` dict (drop into `POST /jobs` `jd`). Capability `create_job`. 503 if the JD-generation service is unavailable; the service itself degrades to a marked template when the LLM chain is down (never 500 on LLM failure). |
| GET | `/jobs/public/{job_id}` | **NEW (rev 3, FR-3.4)** — PUBLIC, unauthenticated. Returns `PublicJobOut` (title, JD, `company_name`) for a **published** job only (`ratified_at` set); 404 for any unpublished/unknown id (never reveals existence). Powers the open application page at `picready.com/{job_uuid}`. |
| GET | `/jobs` | tenant-scoped list; staff see published jobs (`ratified_at` set), each with `public_url`. |
| GET | `/jobs/{id}` | detail; `public_url` present once published. |
| PUT | `/jobs/{id}/compensation` | `{compensation: {...}}` (post-publish). |
| PUT | `/jobs/{id}/jd` | JD edits post-publish. |
| ~~POST `/jobs/{id}/submit`~~ | | **SUPERSEDED (rev 3)** — approval chain bypassed by direct publish; route retained but off the normal path. |
| ~~POST `/jobs/{id}/approve`~~ | | **SUPERSEDED (rev 3)** — `{decision, remarks?}`; multi-level approval no longer used. |
| ~~GET `/jobs/{id}/approvals`~~ | | **SUPERSEDED (rev 3)** — transition history; publish logs all levels as `skipped`. |

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

## Telemetry (`/telemetry`)
| POST | `/telemetry/landing-view` | public, anonymous, rate-limited landing-page audit event; no request body or visitor PII retained |
| POST | `/telemetry/rating-comments-view/{link_id}` | authenticated review-screen audit event; capability and profile-access checks apply |

## Outreach & employer verification (`/verification`)
| POST | `/verification/outreach` | `{job_id, candidate_ids: []}` → sends 40-aspect outreach emails (fresh candidates only) |
| GET | `/verification/profile/{profile_id}` | verification request statuses |
| POST | `/verification/requests/{id}/override` | `{reason}` — HR override, audit-logged |
| GET | `/verification/form/{token}` | PUBLIC — employer form schema + candidate name |
| POST | `/verification/form/{token}` | PUBLIC — `{designation, doj, doe, last_drawn_ctc, last_drawn_gross, noc_status, exit_formalities_complete, bgv_status, proofs_details, prior_experience_details}` |
| POST | `/verification/inbound-email` | webhook (Resend inbound) → enqueue LLM reply parsing |

## Candidate self sign-up (`/auth`)
| POST | `/auth/register-candidate` | PUBLIC — `{full_name, email, phone?}` → creates a candidate account (role=candidate, tenant_id NULL) + Candidate record, then the candidate signs in via the unified OTP login. 409 if a candidate already exists for that email. No password (OTP-only). A previously-sourced Candidate row with the same email is reused/linked rather than duplicated. |

## Candidate portal (`/portal`) — candidate audience
| GET | `/portal/outreach/{token}` | PUBLIC — what's requested (fields to fill, 40 aspects minus already-covered) |
| POST | `/portal/outreach/{token}` | multipart: personal fields, `aspects` JSON, `resume` file, `employer_emails: []` (≤3) |
| GET | `/portal/jobs` | **OPEN board (rev 3)** — EVERY published (`ratified`) job across all tenants; no longer contact-gated (FR-3.5/9.1). |
| GET | `/portal/jobs/{job_id}` | view a single published job (the `picready.com/{job_uuid}` target); any authenticated candidate, no prior-contact gate. |
| POST | `/portal/jobs/{job_id}/apply` | **OPEN application (rev 3)** — multipart: `aspects` (JSON, the 40-question questionnaire incl. `40`=Databank consent) + EITHER `resume` file OR `reuse_previous=true` (reuse last stored resume — FR-6.2/9.2, REVERSES the old fresh-only rule). Published-job-only (404 otherwise); each apply mints its OWN Profile + aspects (only the resume FILE carries over on reuse). 409 on duplicate application. |
| GET | `/portal/applications` | own application stage statuses |

## Dashboard (`/dashboard`)
| GET | `/dashboard/summary` | `{jobs: [{job_id, title, databank_matched, fresh_sourced, shortlisted, offered, joined}], total_jobs_worked}` — scoped to caller |

## Celery task names (enqueue with `celery_app.send_task(name, args=[...])`)
`pickready.send_email`, `pickready.send_sms`, `pickready.run_matching`, `pickready.parse_resume`, `pickready.send_verification_requests`, `pickready.parse_verification_reply`, `pickready.refresh_dashboard_views` — signatures in `backend/app/workers/celery_app.py`.

> **Email transport (rev 3):** `pickready.send_email` now sends over **SMTP** from the
> backend (`SMTP_*` env — Mailtrap SMTP or Gmail SMTP app-password), replacing the
> Mailtrap HTTP Sending API. Task name, args, and all `/verification`, outreach, and
> interview-invite call sites are unchanged — only the transport moved.

## File ownership (parallel tracks — do not edit outside your area)
- **Foundation (done)**: `backend/app/models/*`, `backend/app/core/*`, `backend/app/main.py`, `backend/app/services/capabilities.py`, `backend/app/workers/celery_app.py`
- **Track A (API)**: `backend/app/schemas/*`, `backend/app/api/*`, `backend/app/services/{rbac,approval_fsm,otp,audit,tiers}.py`, `backend/tests/*`
- **Track B (pipelines)**: `backend/app/workers/tasks.py` (+ helpers under workers/), `backend/app/services/{llm_router,matching,embeddings,resume_parsing,verification_parsing,email_render}.py`, `backend/alembic/*`, `backend/alembic.ini`, `backend/app/scripts/*`
- **Track C (frontend)**: everything under `frontend/`
- **Track D (infra)**: `backend/Dockerfile`, `frontend/Dockerfile`, `infra/*`

## Capability names
Use the constants in `backend/app/services/capabilities.py` — never inline strings, never `if role == ...` in business logic.
