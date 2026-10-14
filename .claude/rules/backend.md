---
paths:
  - "backend/app/**/*.py"
  - "backend/scripts/**"
---
# Backend (digest; full rules in docs/engineering/backend.md)

- Authorize with `require_capability(...)`, never a role branch. A new capability needs its seeding migration.
- Slow work is dispatched with `dispatch_after_commit`; never call it and then raise. Every `@task` declares `rls=`; `bypass` needs `rls_reason`. A sweep is added to `workers/schedule.py` AND every environment's scheduler.
- Write every `audit_log` column in the one insert; never assign to the returned row.
- Ledger and money writes carry a stable `idempotency_key`. Never hold a lock across a model call.
- Model calls only through `services/llm_router.invoke_llm`; policy lives in `config/llm_providers.py`. Register every prompt builder in `tests/test_ctc_never_in_prompt.py`.
- No bare `except`; a broad handler re-raises, logs or reads what it caught; absorb only `SQLAlchemyError`. Record every degradation.
- No em dash in any string. No dead code, no dual paths. A new setting goes in `.env.example`.
- Email today: `email_outbox` is the one writer; SES-only is decided and not built (register I-20).
- Read `docs/engineering/backend.md` before a non-trivial change.
