---
paths:
  - "backend/tests/**"
  - "backend/harness/**"
  - "frontend/**/*.test.{ts,tsx}"
---
# Testing (digest; full rules in docs/engineering/testing.md)

- One module while developing; the relevant suite once at the end. Never two suites on one database; never beside a container build.
- Assert committed state from a second connection. Candidate routes use a real session (`tests/candidate_session.py`).
- New SQL must meet real Postgres. A mock-only assertion is not a test. A sweep needs a floor and a positive control.
- A removal ships a sweep (`tests/removal_sweep.py`). A new skip is declared in `docs/operations/SKIPS.md`.
- Tests read some documents by path (table in docs/engineering/testing.md); do not move or reword them.
- `backend/harness/` is never imported by `backend/app/`. Harness exit code 3 (unavailable) is a failure.
