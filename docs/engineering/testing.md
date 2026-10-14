# Testing and verification rules

**Status:** current. **Reviewed:** 2026-10-14. Loaded on demand by `.claude/rules/testing.md`.
How to run things: [../../CONTRIBUTING.md](../../CONTRIBUTING.md). The harness contract: [../spec/HARNESS.md](../spec/HARNESS.md).

## Running

- Canonical: `./scripts/test.sh` (fresh database, flushed cache; needs Docker). Local while developing: one module, then the relevant suite. The full backend suite takes minutes; run it before finishing, not after every edit.
- **Never run two `scripts/test.sh` or harness runs against one database** (it drops and recreates `readypick_test`; the failures look like 17 real ones). **Never run the authoritative suite beside a container build** or the graphify rebuild a commit hook launches: CPU contention reads as a hang.
- A quiet run is a hang, not a pass. `py-spy dump --pid <pid>` names it from outside; `pytest --timeout=N --timeout-method=thread` turns it into a failure. For `test_import_graph.py`, ask about the child process (`Get-CimInstance Win32_Process -Filter "ParentProcessId=<pid>"`): a live child is slow, no child is the real hang.
- No model key is set in tests (`OPENAI_GPT_TERRA`, `OPENAI_GPT_LUNA`, `VOYAGE_CONTEXT_4` unset). Dispatch is `record` in tests, `local` in compose. Run a dispatched task by name, never by draining.
- Frontend: Vitest and React Testing Library for critical flows (sign-in, the Skills step and publish, the assessment player, the ranked table).

## What a test must do

- A green pipeline means the service answers HTTP. Verify against what a user touches: a row count, a real API response, a grep of the deployed image.
- Assert committed state from a **second connection** after the response. A 200 that rolled back at commit is invisible to a response assertion.
- Candidate-route tests use a real session (`tests/candidate_session.py`). No dependency override for identity.
- A statement that has only met a stub has not been tested: new SQL is exercised against real Postgres.
- A test whose only assertion is that a mock was called is not a test. Mutation-check a new guard (revert the fix; the test must fail).
- A sweep needs a floor ("it found at least N") and a positive control (the detector detects). A sweep with a blind spot is worse than none.
- Reachability: `pytest tests/test_ai_reachability.py` answers whether the framework is reachable, transitively and in both directions. Importable is not exercised; `REQUIRED_CALLERS` names functions that must keep a caller. Do not use a grep for this.
- A new prompt builder is registered in `tests/test_ctc_never_in_prompt.py`. A removal ships a sweep (`tests/removal_sweep.py`). A new setting satisfies `test_env_example_parity.py`. A new skip is declared in `docs/operations/SKIPS.md` (`test_skip_inventory.py`).
- Do not weaken a guard threshold to turn a build green; move it only with the reason written beside it.

## Documents that tests read

Moving or rewording these breaks a test. Treat the path and the cited content as a contract.

| Path | Read by | What must stay |
|---|---|---|
| `docs/product/Readypick Hiring Philosophy.md` | `test_runbook_parity.py`, `test_runbook_reconciliation.py`, `test_vaada_live.py`, `services/hiring/runbook_data/` | The file, its section numbers and cited values |
| `docs/operations/SKIPS.md` | `test_skip_inventory.py` | The table format |
| `docs/verification/VERIFICATION_RESULTS.md` | `test_no_live_vendor_claims.py` | Mentions reasoning, extraction, embedding, PASS and every model id the code calls |
| `docs/verification/VERIFICATION_PENDING.md` | same | The phrases "not executed against a live provider" and "recorded fixtures and a stub provider" |
| `docs/spec/ARCHITECTURE.md` | `test_architecture_database_authoritative.py` | The heading text "AD-1. The database is authoritative." and the test's file name |
| `docs/history/LEGACY_RESET_SURVEY.md` | written by `app/scripts/legacy_reset.py --survey` | Path |
| every `*.md` in the repository | `test_no_live_vendor_claims.py` | None of the three forbidden claim phrases (see that test), except in the files it allowlists |
| `CLAUDE.md` | `test_no_live_vendor_claims.py` | Must exist (matched in lower case) |

## The harness

`backend/harness/` answers six questions the suite cannot: end to end against real seams, behaviour under a bad environment, reproducibility, layered evaluation, baselines, and recovery. Three exit codes: 0 pass, 1 fail or regression, 3 unavailable (a failure, never a pass). A fault must be observable in the result. State assertions read from a second connection. The golden journey (`tests/test_golden_journey.py`, the scenario `integration_golden_journey`, the CI job `golden-journey`) is the end-to-end claim.
