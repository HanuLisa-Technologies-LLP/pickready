# Change 6 - PPI report authenticity and PDF export

Verified 2026-08-07. Live-browser inspection was explicitly waived by the
requester; production verification below uses the deployed HTTP endpoint, the
production database, automated DOM tests, PDF text/image inspection, and a
rendered-page visual review.

## Implementation

- `report_skill_evidence` persists the actual questions and candidate answers
  grouped by skill, plus technical precision, depth, problem-solving structure,
  role relevance, concrete examples, and explicit evidence gaps.
- Evidence extraction runs in the existing
  `pickready.run_functional_assessment` Celery task before report synthesis.
- Narrative generation is rating-specific and is guarded by the shared bounded
  `agent_loop.run_loop` banned-phrase and similarity gates.
- `GET /api/v2/assessments/reports/links/{link_id}/pdf` applies the same
  tenant/capability checks as the report JSON endpoint and returns a private,
  no-store PDF attachment.
- The report modal exposes one-click PDF download only after an immutable report
  has loaded.
- ReportLab was selected for server-side PDF generation. It avoids adding a
  browser runtime to the backend image, produces deterministic A4 pagination,
  and supports the existing Cloud Run worker environment. Radar charts are
  rasterized with Pillow before embedding.

## Five-candidate narrative fixture

The authenticity fixture generated five reports for the same skill and role
requirement from five different stored transcripts in a real PostgreSQL
transaction:

| Candidate evidence | Rating | Generated narrative | Words |
|---|---|---|---:|
| Partitioned a high-volume order stream, measured lag, and reduced recovery time from 18 to 4 minutes | Highly Matching | The candidate described partitioning a high-volume order stream, measuring consumer lag, and reducing recovery time from 18 minutes to 4 minutes. This directly demonstrates technical precision and an owned production outcome. The account covers the action, reasoning, and result expected for this role. | 45 |
| Diagnosed duplicate deliveries, introduced idempotency keys, and reduced duplicate processing below 0.1 percent | Matching | The candidate explained diagnosing duplicate deliveries and introducing idempotency keys, with duplicate processing falling below 0.1 percent. That is relevant, concrete evidence of practical Kafka work. The evidence supports the role requirement, while an interviewer should confirm whether the candidate designed the approach independently. | 45 |
| Helped monitor Kafka dashboards and escalated lag incidents but did not tune partitions independently | Moderately Matching | The candidate has monitored Kafka dashboards and escalated lag incidents, which provides partial operational exposure. They did not describe independently tuning partitions or owning the resulting performance change. This supports adjacent capability, but leaves a clear gap around diagnosis depth, decision ownership, and measurable outcomes. | 46 |
| Has read Kafka documentation but has not used Kafka in a production environment | Not Matching | The candidate directly stated that they have not used Kafka in production. This is an honest knowledge signal, but it provides no evidence of applied capability against the role requirement. The gap is practical experience rather than communication quality, and should be treated as such. | 46 |
| Built a small local Kafka demo, without production traffic, incident response, or performance measurements | Not Matching | The candidate described a local Kafka demonstration, but no production traffic, incident response, or measured performance outcome. The example shows introductory familiarity, not the production capability required by the role. Evidence is absent for independent diagnosis, operational trade-offs, and sustained ownership under real load. | 45 |

Deterministic results:

- Maximum pairwise `SequenceMatcher` similarity: **0.0905**
- Required maximum: **less than 0.72**
- Banned-phrase occurrences: **0**
- Real PostgreSQL fixture rows: **5 conversations, 10 messages, 5 evidence
  rows**, all rolled back after assertions
- Explicit no-evidence rows are retained rather than silently omitted.

## Local verification

- Backend full suite before the final typography-only correction:
  **1,287 passed**.
- Focused platform-audit and report suite after the correction: **5 passed**.
- Frontend: **6 test files / 16 tests passed**, ESLint passed, and the
  production Next.js build completed with **36 pages**.
- Report fixture: **4 A4 pages**, sharp charts, candidate/job/tenant/date,
  page number and confidentiality footer on every page; no clipped content,
  overlap, or orphaned section headings after rendered-page review.
- The local OpenAPI document includes the PDF route.

## Deployment

Implementation commits:

- `d41f5ddd53e477e83eb80eba7d9b230651491553`
- `4a99fba62e83185ed5937e62ba34e152ceb670e6`

The first CI run (`31188817694`) stopped safely before deployment. Its only
failure was the platform typography audit finding three em-dash string literals
in `report_pdf.py`; the supplied runner log records `1 failed, 1286 passed`.
The later duplicate-key and audit-log permission messages in the PostgreSQL
container log are expected negative-path test assertions, not failures.

Replacement run
[`31189011533`](https://github.com/HanuLisa-Technologies-LLP/pickready/actions/runs/31189011533)
completed successfully:

- frontend tests, lint and production build: passed
- backend tests and agent evaluation: passed
- no-traffic deployment and staged smoke test: passed
- Production environment approval and 100% traffic promotion: passed
- production smoke test: passed

Promoted revisions:

- backend: `pickready-backend-00121-loq` (100%)
- frontend: `pickready-frontend-00116-xev` (100%)
- worker: `pickready-worker-00042-cww`

## Production database proof

Read through the Cloud SQL Auth Proxy with RLS settings explicitly established:

```text
alembic=0044_report_skill_evidence
rls=true,force=true
policy=report_skill_evidence_tenant_isolation
evidence_rows=0
reports=2
```

The zero evidence-row count is expected and honest: both existing production
reports predate this release and reports are immutable. The next completed
assessment will persist its evidence before synthesis.

## Production PDF proof

An authenticated request downloaded the existing Karthik Kumar / Machine
Learning Engineer report from the promoted backend:

```text
HTTP/1.1 200 OK
content-disposition: attachment; filename="ppi-assessment-report-Karthik-Kumar.pdf"
cache-control: private, no-store
content-type: application/pdf
Content-Length: 77270
pages=6
embedded_images=4
candidate_text=true
job_text=true
footer_on_every_page=true
```

The production artifact is
`diagnostics/change6-production-report.pdf`. All six rendered pages were
visually reviewed: charts and legends are legible, cards remain inside page
bounds, section transitions are clean, and no text overlaps or clips.

Because this report is an immutable historical record created before Change 6,
its body correctly retains the old generic narrative. The production download
therefore proves endpoint authorization, PDF composition, charts, pagination,
branding, validation content and headers; the five transactional fixtures above
prove the new evidence-aware narrative path used for future reports.
