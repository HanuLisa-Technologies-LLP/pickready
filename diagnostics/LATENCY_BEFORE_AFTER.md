# Change 10 — Latency before/after

Measurement started: 2026-08-07 23:20 IST  
Project/region: `pick-ready-503913` / `asia-south1`

## Real 24-hour baseline

Cloud Monitoring `run.googleapis.com/request_latencies`, 24-hour aligned
distribution percentiles:

| Service | p50 | p95 | p99 |
|---|---:|---:|---:|
| pickready-backend | 37.27 ms | 1,166.65 ms | 21,259.22 ms |
| pickready-frontend | 46.52 ms | 12,654.32 ms | 36,117.96 ms |

Cloud Logging cold-start messages over the identical window:

| Service | Cold starts |
|---|---:|
| pickready-backend | 25 |
| pickready-frontend | 23 |

The 729 sampled backend request logs produced these top routes (UUID and numeric
path components normalized):

| Route | n | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| `/api/v1/auth/me` | 180 | 28.7 ms | 58.9 ms | 165.7 ms |
| `/api/v1/auth/refresh` | 87 | 18.3 ms | 182.7 ms | 4,354.7 ms |
| `/api/v1/jobs` | 67 | 27.1 ms | 49.1 ms | 52.7 ms |
| `/api/v1/dashboard/summary` | 54 | 39.6 ms | 526.7 ms | 678.3 ms |
| `/api/v2/assessments/conversations/{id}/respond` | 48 | 95.5 ms | 24,116.2 ms | 36,329.6 ms |
| `/health` | 45 | 17.6 ms | 766.7 ms | 839.3 ms |
| `/api/v1/companies/me/profile` | 37 | 24.6 ms | 73.7 ms | 4,454.6 ms |
| `/openapi.json` | 31 | 9.6 ms | 1,020.6 ms | 1,093.1 ms |
| `/api/v1/billing/config` | 21 | 4.8 ms | 391.0 ms | 10,194.4 ms |
| `/api/v1/auth/select-context` | 18 | 19.3 ms | 72.6 ms | 88.4 ms |

Cloud SQL emitted zero slow-query log entries in the window. Query Insights
and slow-statement logging were not enabled, so this is explicitly “no
observations,” not proof that slow queries cannot exist.

Query Insights was then enabled with 1,024-character query capture and
application tags. The database health probe remained green after the patch.

Resource p95 values were low enough that current sizing is retained:

| Service | CPU p95 | Memory p95 | Retained size |
|---|---:|---:|---|
| backend | 8.50% | 26.92% | 2 CPU / 2 GiB |
| frontend | 19.45% | 23.92% | 1 CPU / 1 GiB |

The extra backend headroom is retained for synchronous AI/report bursts; the
baseline shows tail latency, not resource saturation, is the problem.

## Changes

- Backend and frontend revision minimum instances changed from 0 to 1.
- Startup CPU boost made explicit for both services.
- Tenant/filter/sort composite indexes added for jobs-to-candidates, profiles,
  and candidates list paths.
- Redis caches added for company profile, job competencies, and role
  permissions. Keys include tenant id, TTL is 120 seconds, writes explicitly
  invalidate; Redis failure safely falls through to PostgreSQL.
- Recharts report UI is dynamically imported only when the modal opens.
- ReportLab is lazily imported only for a PDF request.
- Existing multi-stage slim images and lazy FastEmbed import were retained.

## After window

Optimized revisions were promoted at approximately 18:13 UTC:

- backend `pickready-backend-00136-fug` — 100%
- frontend `pickready-frontend-00131-pos` — 100%

Cloud Monitoring had two complete one-minute distribution points available
between 18:13 and 18:18 UTC (a real ~5-minute elapsed window; deliberately not
described as 24 hours):

| Service | p50 | p95 | p99 |
|---|---:|---:|---:|
| pickready-backend | 38.24 ms | 494.20 ms | 505.29 ms |
| pickready-frontend | 575.25 ms | 599.91 ms | 602.10 ms |

Cold starts in that window were one per service: the expected initial creation
of each new revision. No subsequent scale-from-zero cold start occurred.

A separate 20-request end-to-end probe from India (includes network/TLS, so it
is not substituted for the server metric above) measured:

| Target | p50 | p95 | p99 | HTTP |
|---|---:|---:|---:|---:|
| backend `/health` | 108.5 ms | 128.3 ms | 419.8 ms | 200 |
| frontend `/login` | 323.3 ms | 413.2 ms | 623.2 ms | 200 |

Production is at Alembic `0047_latency_indexes`; all three new indexes exist.
Both services report `minScale=1` and startup CPU boost enabled. The short
after-window traffic mix differs from the 24-hour baseline, so the apparent
tail improvement is encouraging but not claimed as a controlled 24-hour
comparison. The document preserves the real baseline for a later 24-hour
follow-up.
