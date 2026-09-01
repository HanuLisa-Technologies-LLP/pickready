# Verification results

Produced by `backend/scripts/verify_live.py` against the live vendor
endpoints. Everything below is an OBSERVED result; nothing here is inferred
from documentation.

- Run at: 2026-08-31 16:36:50Z
- Commit: `b87a5aa53c587bdd605ce05391593c8414bdb239`
- Reasoning path: `gpt-5.6-terra`
- Extraction path: `gpt-5.6-luna`
- Embedding path: `voyage-4`

Every path exercised returned the shape this codebase was built against.

| Path | Vendor | Model | Result | ms | Detail |
|---|---|---|---:|---:|---|
| `reasoning` | OpenAI | `gpt-5.6-terra` | PASS | 5711 | 1037 characters of text returned |
| `extraction` | OpenAI | `gpt-5.6-luna` | PASS | 4848 | top-level object with keys ['comments', 'education', 'experience', 'role_fit', 'skills'] |
| `embedding` | Voyage | `voyage-4` | PASS | 1219 | 2 vectors, 1024 wide |
| `credential_failure` | OpenAI | `gpt-5.6-luna` | PASS | 973 | 401 classified as credential, breaker trips on first |
| `timeout` | OpenAI | `gpt-5.6-terra` | PASS | 1825 | raised inside the budget: openai exhausted for task_type=report_synthesis: transport (ConnectTimeout); transport (ConnectTimeout); transport (ConnectTimeout) |
| `rate_limit` | OpenAI | `gpt-5.6-luna` | NOT PROVOKED | 5531 | the call succeeded, so no rate limit was reached. The classifier and the retry-after reader remain proven only against recorded fixtures. |

## What a NOT PROVOKED row means

The branch could not be reached without abusing the vendor. It is not a pass
and it is not a failure: it means the behaviour remains proven only against the
hand-authored fixtures in `backend/tests/fixtures/vendor/`.

## Next

Remove the corresponding rows from `VERIFICATION_PENDING.md` **only** for the
paths that show PASS above. A row is removed by a run that succeeded, never by
a run that was attempted.
