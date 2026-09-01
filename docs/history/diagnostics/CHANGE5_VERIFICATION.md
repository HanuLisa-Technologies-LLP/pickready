# Change 5 verification — residual pipeline cleanup

Date: 2026-08-07 (Asia/Calcutta)
Release commit: `e25ed1b49939ea9e0d10a3959dfdbc444e95abb7`
GitHub Actions run: https://github.com/HanuLisa-Technologies-LLP/pickready/actions/runs/31182228942

## Result

Change 5 is deployed and receiving 100% of production traffic.

The original `PipelineStatus` mismatch had already been fixed before this
change. The remaining Workify state is confirmed to be intentional: two jobs
have complete 15-competency frameworks and are waiting for a client to review
and approve them. They are not crashed, half-provisioned, or waiting on the
removed technical-question-bank workflow.

The pending-state banner now includes a direct **Review and save framework**
action that scrolls to the PPI framework editor. The action is not shown while
framework generation is still running.

The user explicitly waived live-browser checks on 2026-08-07. UI proof therefore
uses component DOM tests plus an authenticated fetch and grep of the deployed
job-detail JavaScript bundle.

## Production data truth

Direct production SQL through Cloud SQL Auth Proxy:

```text
tenant        job id                               title
Workify Corp  bf839cd4-fee0-4223-b77b-eccf610b5932 Probe Backend Engineer
Workify Corp  d0557bd6-b48d-4fe4-a110-f337b06257e0 Verification Data Engineer

both rows:
assessment_status:     questions_pending_review
framework_approved_at: NULL
active competencies:   15
```

Production status totals:

```text
ACRM Corp      ready_for_candidates      10
Sarkar Corp    ready_for_candidates      13
Specter & Co.  ready_for_candidates      10
Workify Corp   questions_pending_review   2
Workify Corp   ready_for_candidates       1
```

Authenticated production API responses for both pending Workify jobs:

```json
[
  {
    "job_id": "bf839cd4-fee0-4223-b77b-eccf610b5932",
    "setup_status": "questions_pending_review",
    "ready_for_candidates": false,
    "framework_pending": false,
    "framework_approved": false,
    "competencies": 15,
    "blocking_reason": null
  },
  {
    "job_id": "d0557bd6-b48d-4fe4-a110-f337b06257e0",
    "setup_status": "questions_pending_review",
    "ready_for_candidates": false,
    "framework_pending": false,
    "framework_approved": false,
    "competencies": 15,
    "blocking_reason": null
  }
]
```

This is the exact state the new CTA handles: generation is complete, all minimum
competencies exist, and human approval is the only remaining gate.

## UI affordance

`frontend/components/job-setup-review.tsx` now renders:

```text
Framework pending review
No candidate can be invited to this job until you save the PPI framework below.
Applications still arrive in the meantime.
[ Review and save framework ]
```

The CTA targets `#ppi-framework`, whose card has a sticky-header-safe scroll
offset. While `framework_pending=true`, the screen instead explains that
criteria generation is still running and does not present an unusable approval
action.

Component proof:

```text
Test Files  5 passed (5)
Tests       15 passed (15)
```

The two new cases assert:

1. a review-ready job explains why invitations are blocked and links to
   `#ppi-framework`;
2. a still-generating job explains the running state and has no review CTA.

## Deployed bundle proof

An authenticated HTTP request fetched the production
`/org/jobs/{job_id}` route and all 19 route-specific JavaScript chunks. The CTA
string was present in exactly one deployed chunk:

```text
page status:          200
route contains job:   true
route chunks fetched: 19
timed-out chunks:     0
CTA bundle hits:      1
hit:
https://pickready-frontend-fcunsks2nq-el.a.run.app/_next/static/chunks/23m-e5-iey8xw.js
```

This verifies the production bundle, not the repository source or local build.

## Enum-parity guard

### Corrected database fact

There is no PostgreSQL native type named `pipelinestatus` in this schema.
`pipeline_status.status` and `job_candidate_links.status` are `VARCHAR(30)`;
the latter is constrained by `ck_jcl_status`. That distinction matters because
the historical incident was caused by migration 0018 widening the database
`CHECK` vocabulary while Python's `PipelineStatus` remained narrower.

The new real-Postgres regression suite covers the schema that actually exists:

- every PostgreSQL native enum introduced now or later;
- every `CHECK ... IN (...)` vocabulary attached to an SQLAlchemy
  Enum-mapped column;
- every distinct stored value in every SQLAlchemy Enum-mapped column;
- the `ck_jcl_status` vocabulary explicitly, compared exactly with all
  `PipelineStatus` values before any newly allowed value needs to be stored.

It sweeps every model in `Base.metadata`, including Role, UserStatus,
OTPChannel, JobStatus, ApprovalDecision, LinkSource, Tier, PipelineStatus,
VerificationStatus, and SubmittedVia mappings.

Focused real-Postgres output:

```text
$ pytest tests/test_db_enum_parity.py -q
..                                                                       [100%]
2 passed in 1.20s
```

Live constraint definition contains the same 12 values as Python:

```text
applied
assessment_invited
assessment_in_progress
assessment_completed
shortlisted
rejected
interview_scheduled
interview_completed
offer_extended
joined
hold
offered
```

## Local verification

```text
backend enum parity:  2 passed (real PostgreSQL)
frontend components: 15 passed
frontend ESLint:      passed
Next production build: passed; all 36 static pages generated
```

## Gated deployment

Run `31182228942` completed successfully for the exact release SHA:

```text
Backend tests and agent evaluation  success
Frontend tests, lint and build       success
Deploy (staged, no traffic)          success
Smoke test staged revision           success
Production environment approval      approved
Shift 100% production traffic        success
Smoke test production                success
```

Production state:

```text
backend:  pickready-backend-00112-bel   100% traffic
frontend: pickready-frontend-00107-fir  100% traffic
worker:   pickready-worker-00039-l4j
beat:     pickready-beat-00039-njc
image tag:
e25ed1b49939ea9e0d10a3959dfdbc444e95abb7
```

No schema change, workflow auto-approval, mock data, stub, TODO, or end-user
numeric assessment score was introduced.
