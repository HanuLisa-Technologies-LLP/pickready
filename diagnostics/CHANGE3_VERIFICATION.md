# Change 3 - assessment relevance and candidate progress

Verified 2026-08-07. Live-browser inspection and screenshots were explicitly
waived by the requester. Production verification therefore uses the deployed
HTTP contract, production SQL, pipeline smoke tests, real-PostgreSQL acceptance
tests, and jsdom interaction tests.

## Shipped behavior

- Every base answer is classified through the shared bounded
  `agent_loop.run_loop` as substantive, gibberish, off-topic, shallow, or
  evasive. Empty and keyboard-mash inputs are rejected deterministically.
- Each rejection receives a distinct explanation naming the problem and a
  re-ask under the original question key.
- A re-ask or clarification probe does not increment the base-question counter.
  A valid answer advances exactly one slot.
- Re-asks are capped at two per base question. When the cap is reached, the
  submitted answer is retained with `evidence_gap=true` and the interview moves
  forward, preventing an infinite loop.
- Candidate messages persist `answer_label`; downstream report evidence
  explicitly retains capped evidence gaps.
- The candidate interface has completed/current/pending stages, a circular
  percentage indicator, `X / 45 Questions Answered`, AI Assessor identity and
  timestamps, candidate initials, persisted latest-answer editing, and
  Clear/Send controls.

## Automated acceptance proof

The real-PostgreSQL conversation test deliberately submitted an off-topic
answer and then a valid answer:

```text
off-topic result:
  answered_questions=0
  is_reask=true
  progress_label=Question 1 of 3

valid retry result:
  answered_questions=1
  is_reask=false
  progress_label=Question 2 of 3
```

A second acceptance case submitted three shallow answers:

```text
retry 1 answered_questions=0
retry 2 answered_questions=0
capped retry answered_questions=1
persisted answer_label=shallow
persisted evidence_gap=true
```

The jsdom interaction test rendered the authenticated assessment page, checked
the circular progress semantics and stages, verified AI Assessor, Clear, and
Send, submitted a substantive answer, observed the count change from 0 to 1,
and confirmed that the persisted response exposed its Edit control.

Local verification:

- focused relevance and conversation contract: **46 passed**
- platform policy audit: **17 passed**
- complete backend suite: **1,289 passed**
- frontend: **7 files / 19 tests passed**
- ESLint: passed
- Next.js production build and TypeScript: passed, 36 routes generated
- Alembic: one head, `0045_assessment_relevance_state`

## Deployment

Implementation commit:
`373023b415924e4a3d390c857f81c218d0f1f22a`

Deployment run
[`31200241549`](https://github.com/HanuLisa-Technologies-LLP/pickready/actions/runs/31200241549)
completed successfully:

- backend tests and agent evaluation: passed
- frontend tests, lint, and production build: passed
- no-traffic deployment and staged smoke: passed
- production promotion and production smoke: passed

Promoted revisions:

- backend: `pickready-backend-00124-vip` (100%)
- frontend: `pickready-frontend-00119-rul` (100%)

Live production health returned:

```json
{"status":"ok","database":"ok"}
```

The deployed OpenAPI document exposes the structured `ConversationOut` response
on the respond endpoint and a successful 204 contract for latest-answer edits.

## Production database proof

Read through the Cloud SQL Auth Proxy:

```text
alembic=0045_assessment_relevance_state
assessment_conversations.pending_kind nullable=YES
assessment_conversations.reasks_used nullable=NO
assessment_messages.answer_label nullable=YES
assessment_messages.evidence_gap nullable=NO
```

No real candidate conversation was mutated merely to manufacture production
evidence. The exact counter and cap semantics were instead exercised against
real PostgreSQL with RLS in isolated transactional fixtures, while the promoted
production revision, schema, API contract, health, staged smoke, and production
smoke were independently verified live.
