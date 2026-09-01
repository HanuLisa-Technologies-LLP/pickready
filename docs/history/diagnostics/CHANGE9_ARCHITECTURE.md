# Change 9 architecture and production verification

Date: 2026-08-07 (Asia/Calcutta)  
Release commit: `57a7461b112cb900203a3a01502fc0e860729115`  
GitHub Actions run: https://github.com/HanuLisa-Technologies-LLP/pickready/actions/runs/31184823992

## Result

The existing `services.agent_loop.run_loop` primitive was extended rather than
replaced. The release is deployed to all four application workloads and
receiving 100% of production traffic.

The loop now has:

- structured `Defect(type, location, detail)` critic output while retaining the
  older free-text `reasons` view for compatibility;
- hard interactive/background attempt and wall-clock limits;
- conservative generated-output token budgets (4,096 interactive and 12,000
  background), in addition to the router's exact per-call `max_tokens`;
- deterministic banned-phrase and similarity gates;
- loop-level LangSmith chain instrumentation containing operational metadata
  only, never candidate or prompt text;
- a `LoopResult` that exposes typed defects and generated-token consumption.

The important failure contract is unchanged: an exhausted or unavailable loop
returns its safe fallback with `degraded=true`; it never turns a provider outage
into an application outage.

## Surfaces

### PPI report narratives

`functional_assessment.bounded_remark` already used the common loop. This change
strengthened its critic with typed defects for empty output, word bounds,
end-user numeric scores, banned/near-banned phrases, and missing evidence
anchors. It uses the background limits because report synthesis runs in the
Celery worker.

### PPI interview probes

`generate_suggested_questions` now uses `run_loop` with the external prompt
`backend/prompts/report_interview_probes.txt`. Its deterministic critic requires
8–10 role-specific questions, an exact weak-skill anchor, no numeric score,
no banned/near-banned phrase, and no pair that exceeds the configured
similarity ceiling. The call remains inside the existing
`run_functional_assessment` Celery path.

### PPI framework generation

The pre-existing framework-generation loop now receives the explicit
background token budget as well as its existing iteration/deadline bounds.

### AI Reach

AI Reach deliberately remains a single deterministic threshold-and-rank pass.
Its role-token weighting, semantic threshold, and descending rank have a
predictable correct answer; adding iterative LLM refinement would add latency,
cost, and non-determinism without improving the decision. This is the judgment
allowed by the brief's “if it benefits” clause.

## Typed critic example

```python
Defect(
    type="banned_phrase",
    location="remark.PostgreSQL",
    detail="remove or rewrite the banned or near-banned phrase",
)
```

Each failed attempt is reflected as a fresh correction turn. Corrections are
not concatenated indefinitely, and a failed provider attempt cannot remove the
remaining bounded attempts.

## Local and CI verification

```text
complete backend suite:             1283 passed in 85.12s
focused loop/tracing/report tests:     42 passed
agent evaluation:                    all PASS; lowest score 1.00
frontend tests/lint/build:            passed
```

Pipeline evidence for the exact release SHA:

```text
Backend tests and agent evaluation   success
Frontend tests, lint and build        success
Deploy (staged, no traffic)           success
Smoke test staged revision            success
Production environment approval       approved by Saravankumar25
Shift 100% production traffic         success
Smoke test production                 success
```

The production smoke test returned 200 for `/health`,
`/api/v1/dashboard/summary`, `/api/v1/jobs`, `/api/v1/auth/me`, and the
frontend root. It also confirmed the expected assessment route contract.

## Live production loop proof

Cloud Run Job execution `pickready-migrate-cbht9` ran the production image with
a synthetic Platform Engineer report input. It read no customer record and
performed no database mutation.

```text
execution:       pickready-migrate-cbht9
image:           57a7461b112cb900203a3a01502fc0e860729115
completed:       true
duration:        26.71 seconds
succeeded tasks: 1
```

All available providers rejected this particular live request:

```text
OpenRouter: HTTP 402
Gemini:     HTTP 429
Groq:       HTTP 429
```

The job nevertheless completed successfully through the bounded degraded
fallback path. This is live proof of the failure-safe behavior, not a mocked
provider result.

## LangSmith trace status

The production runtime has `LANGSMITH_API_KEY` wired from Secret Manager and
the loop instrumentation is enabled whenever that key is present. The current
stored key returns HTTP 403 from the LangSmith sessions/runs API, so the trace
created by the live execution cannot be read back and no honest trace link can
be supplied. The SDK failure is intentionally non-fatal and the application
continued correctly. This external credential blocker is recorded in
`diagnostics/FAILURES.md`; resolving it requires a valid LangSmith workspace key
from the workspace owner.

No prompt or completion content was sent by the loop-level trace. Content
tracing remains opt-in and disabled by default.

## Production revisions

```text
backend:  pickready-backend-00115-dav   100% traffic
frontend: pickready-frontend-00110-hin  100% traffic
worker:   pickready-worker-00040-gtk
beat:     pickready-beat-00040-l9v
image:    57a7461b112cb900203a3a01502fc0e860729115
```

The user explicitly waived live-browser checks on 2026-08-07. Change 9 has no
browser-dependent proof; production verification uses the gated smoke suite,
Cloud Run execution state, logs, and deployed revision metadata.
