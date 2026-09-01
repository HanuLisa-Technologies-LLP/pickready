# GCP Cost Optimization Report

Audit date: 2026-08-24 (Asia/Calcutta)  
Project: `pick-ready-503913`  
Primary region: `asia-south1`  
Audit mode: read-only; no infrastructure changes were applied

## 1. Executive Summary

The original estimated service subtotal supplied for this audit is **₹8,609.06**. The largest reported services are Cloud SQL (₹5,192.41, 60.3%), Cloud Run (₹2,442.71, 28.4%), and Memorystore for Redis (₹815.43, 9.5%). Together they account for 98.2% of the supplied subtotal.

The live configuration materially differs from the premise in two ways:

1. The database instance is named `pickready-postgres`, not `pick-ready-postgres`.
2. The live database is Cloud SQL **Enterprise**, not Enterprise Plus. It is a zonal `db-custom-2-13312` PostgreSQL 16 instance. The supplied bill can therefore include an earlier configuration or a different billing interval; the current snapshot must not be assumed to explain every historical rupee.

Cloud SQL cost is driven primarily by continuously provisioned compute (2 vCPU and 13 GiB RAM), not its approximately 112 MB of used storage. Over the available 24 daily telemetry points, CPU averaged 3.39% (maximum daily mean 3.60%), memory averaged 41.73% (maximum daily mean 42.08%), and disk use peaked at 117,133,312 bytes (1.12% of the 10 GiB allocation). The instance appears CPU-heavy relative to observed use, but memory consumption is material enough that a production resize requires a staged load test and approval.

Cloud Run has two production request services and two production worker pools. The request services each keep one warm instance. More importantly, `pickready-worker` continuously provisions 2 vCPU/2 GiB and `pickready-beat` continuously provisions 1 vCPU/512 MiB. Monitoring recorded approximately 4.10 million vCPU-seconds for the worker and 2.05 million vCPU-seconds for beat over the observed period; those fixed workers dominate Cloud Run CPU allocation. They are active application components, not orphaned capacity.

Redis is a 1 GiB Basic-tier instance, already the smallest provisioned capacity tier. Its dataset is tiny (average memory ratio 0.47%), but it averaged 24.7 connected clients, processed approximately 3.41 million command calls in the available telemetry, and is referenced by the backend and both workers. It is live and operationally necessary in the current Celery architecture.

Artifact Registry contains one 15.70 GB regional repository, 136 image records across three packages, and no cleanup policy. At least 125 Cloud Run service revisions plus frequently redeployed worker pools and 88 migration executions show high deployment churn. This explains the 15.7 GB artifact footprint and contributes to vulnerability-scanning charges. Because the repository is less than a month old and historical images are plausible production rollback points, no cleanup was applied automatically.

**Total changes applied:** 0.  
**Changes not applied:** all capacity, retention, security, networking, database, Redis, and production-service changes.  
**Applied savings:** ₹0.00.  
**Estimated post-audit subtotal:** ₹8,609.06 if the supplied subtotal and workload remain otherwise unchanged.  
**Savings confidence:** HIGH for the zero-change result; LOW to MEDIUM for approval-gated opportunities because SKU-level billing export and peak telemetry were unavailable.

The highest-priority operational issue is not a cost optimization: plaintext database credentials are embedded in production Cloud Run service, worker-pool, migration-job, and legacy diagnostic-job environment definitions. The credentials are deliberately redacted from this report. Rotate them and migrate all database DSNs to Secret Manager under an approved, coordinated rollout.

## 2. Before State

| Service | Cost | Configuration | Primary Cost Driver | Risk |
| ------- | ---: | ------------- | ------------------- | ---- |
| Cloud SQL | ₹5,192.41 | PostgreSQL 16.14; Enterprise; zonal; `db-custom-2-13312`; 10 GiB SSD; auto-grow; backups and PITR on | Fixed 2-vCPU/13-GiB compute; storage is negligible | High: production database |
| Cloud Run | ₹2,442.71 | 2 services with min 1 plus 2 manual worker pools with 1 instance each | Always-running worker CPU/RAM, warm-service memory, request execution, deployment jobs | High: all workloads explicitly production |
| Memorystore Redis | ₹815.43 | Basic, 1 GiB, Redis 7, one node, no replica | Fixed minimum provisioned capacity | High: active Celery broker/cache dependency |
| Vulnerability scanning | ₹99.47 | Scanning active on the only Docker repository | New-image scanning from frequent image pushes | Medium: security control |
| Secret Manager | ₹30.13 | 27 secrets; 30 versions; automatic replication | Secret versions and accesses | High: production credentials |
| Artifact Registry | ₹28.89 | One regional Docker repository, 15.70 GB, 136 image records, no cleanup policy | Retained image layers/build churn | Medium: rollback/deployment supply chain |
| Compute Engine | ₹0.02 | No VMs, disks, snapshots, forwarding rules, routers, NATs, or reservations; one serverless internal range | Negligible networking metadata/usage | Low |
| Cloud Storage | ₹0.00 | One regional private bucket; 33 objects; 1.25 MiB; lifecycle rules already present | Negligible object storage | High if user data is deleted; no action justified |

## 3. Billing and Utilization Analysis

### Cloud SQL

**Resource:** `pickready-postgres` (`pick-ready-503913:asia-south1:pickready-postgres`)  
**Current cost:** ₹5,192.41 supplied service subtotal  
**Current configuration:** PostgreSQL 16.14, Enterprise edition, 2 vCPU/13 GiB (`db-custom-2-13312`), zonal in `asia-south1-b`, 10 GiB PD-SSD, auto-resize enabled with no explicit cap, no replica, public IPv4 enabled, no private IP, encrypted-only SSL mode, no authorized networks listed, Query Insights on, RUNNABLE.  
**Protection:** automated backups enabled; 15 retained; latest 15 inspected were successful; PITR enabled; seven days transaction-log retention. Deletion protection is off.  
**Likely cost driver:** dedicated compute is fixed while the instance is running. Storage, backup volume, and database size are small. There is no HA or read-replica multiplier. Google documents Cloud SQL pricing as CPU/memory plus storage/networking, with replicas and HA adding compute charges ([official pricing](https://cloud.google.com/sql/pricing)).  
**Utilization evidence:** 24 available daily points showed CPU 3.25%-3.60% (3.39% average), memory 41.51%-42.08% (41.73% average), disk 95.4-117.1 MB, and disk utilization 0.90%-1.12%. Approximately 285,219 PostgreSQL new-connection events were observed across the returned labeled series; the missing active-connection series prevents a reliable concurrency conclusion.  
**Architectural context:** backend, worker, beat, and migration job all attach the instance. Backend health reported `database: ok`.  
**Assessment:** CPU is lightly used. Memory is not comparably idle: roughly 5.4 GiB is in use. A conservative first-stage memory reduction to 10 GiB may be viable, but requires restart planning, peak/high-resolution memory review, connection-pool review, and load testing. The live edition is already Enterprise, so there is no Enterprise Plus downgrade to perform.  
**Opportunity:** approval-gated resize review and connection-pool tuning.  
**Risk:** HIGH.  
**Estimated savings:** `Savings cannot be reliably quantified from current telemetry.` SKU-level billing data and exact currency pricing were unavailable.  
**Confidence:** MEDIUM that compute is the cost driver; LOW for resize savings; MEDIUM for the technical right-sizing signal.  
**Recommended action:** export SKU-level billing, capture 14-30 days of p95/p99 memory and connections, load-test `db-custom-2-10240`, then schedule a maintenance-window resize only if headroom is acceptable.

### Cloud Run

**Current cost:** ₹2,442.71 supplied service subtotal.

| Resource | Type | CPU / memory | Scaling | 30-day evidence | Assessment |
| --- | --- | --- | --- | --- | --- |
| `pickready-backend` | Service | 2 vCPU / 2 GiB | min 1, max 10, concurrency 160, request-based CPU | 5,554 requests; 34 5xx; ~1.427M billable-instance seconds; CPU daily-median average 0.56%; memory daily-median average 11.72%; startup p95 sample average ~7.7s | Warm instance is expensive mainly for idle memory but avoids a material cold start |
| `pickready-frontend` | Service | 1 vCPU / 1 GiB | min 1, max 10, concurrency 80, request-based CPU | 18,230 requests; 20 5xx; ~1.426M billable-instance seconds; CPU daily-median average 0.64%; memory daily-median average 12.69%; startup p95 sample average ~1.23s | Scale-to-zero is plausible technically but production latency/SLO approval is required |
| `pickready-worker` | Worker pool | 2 vCPU / 2 GiB | manual 1 | ~4.100M vCPU-s and 4.100M GiB-s allocated; CPU daily-median average 2.76%, maximum returned daily median 92.48%; memory daily-median maximum 17.23%; active logs and task errors | Largest fixed Cloud Run allocation; active and performance-sensitive |
| `pickready-beat` | Worker pool | 1 vCPU / 512 MiB | manual 1 | ~2.050M vCPU-s and 1.025M GiB-s allocated; CPU daily-median average 1.63%; memory daily-median maximum 15.50%; active logs | Single instance is architecturally required to avoid duplicate schedules; already minimum memory |

Google documents that request-based services charge while processing/starting/stopping and charge a lower idle rate for minimum instances, while worker pools are charged for the time their instances run ([official Cloud Run pricing](https://cloud.google.com/run/pricing)).

The two services are healthy and receive 100% traffic on their latest ready revisions. Both use second-generation execution, startup CPU boost, the production runtime service account, and max scale 10. Backend uses Direct VPC egress for private ranges and attaches Cloud SQL. Worker pools also use Direct VPC and attach Cloud SQL. No Serverless VPC Access connector or Cloud NAT was found.

The worker logged at least 1,000 entries since 2026-08-23 and 18 ERROR-severity entries in the returned window. Errors primarily involved exhausted/cooling-down external LLM providers and a tracing context-manager failure. Beat returned 535 log entries with zero error-severity entries. This is evidence of active workload and also a reason not to reduce worker CPU without workload remediation and peak testing.

**Fixed versus variable:** worker pools are predominantly fixed instance-based cost. Services combine variable request execution with idle minimum-instance memory. Migration jobs are variable and short-lived; `pickready-migrate` has 88 executions.  
**Opportunity:** evaluate min 0 for frontend first; profile backend cold-start reduction; right-size worker memory only after peak telemetry; use CUDs only after the baseline is deliberately retained.  
**Risk:** HIGH because all four workloads declare `ENVIRONMENT=production`.  
**Estimated savings:** `Savings cannot be reliably quantified from current telemetry.` Allocation data identifies where spend occurs, but the billing account's SKU/currency rates and free-tier allocation were unavailable.  
**Confidence:** HIGH on cost-driver attribution; MEDIUM on frontend scale-to-zero; LOW on worker right-sizing without peak distributions.

### Memorystore for Redis

**Resource:** `pickready-redis`  
**Current cost:** ₹815.43  
**Configuration:** Redis 7, Basic tier, 1 GiB, one node in `asia-south1-c`, no read replica, no persistence, direct peering on the default VPC, no transit encryption.  
**Likely cost driver:** fixed provisioned 1 GiB Basic capacity. Memorystore bills by provisioned GiB and tier/region ([official pricing](https://cloud.google.com/memorystore/docs/redis/pricing)).  
**Utilization:** average data-memory ratio 0.471%, maximum 0.483%; average connected clients 24.7, maximum 31; approximately 3.41M command calls; zero evicted keys and zero rejected connections.  
**Architectural context:** backend, Celery worker, and beat all reference the instance.  
**Assessment:** materially underused for memory but not orphaned. It is already at the 1 GiB minimum and Basic tier.  
**Opportunity:** no in-place capacity saving. Removing Redis requires an application/queue redesign and is outside safe optimization.  
**Risk:** HIGH.  
**Estimated savings:** none without redesign; redesign savings ceiling is the reported ₹815.43, not a forecast.  
**Confidence:** HIGH.

### Vulnerability Scanning

**Current cost:** ₹99.47.  
**Configuration:** scanning is active on the `pickready` repository.  
**Likely cost driver:** frequent new backend/frontend image pushes. The repository was created on 2026-07-31 yet already has 136 image records. CI builds and pushes both images on each deployment, even when only one application may have changed. Google confirms automatic repository scanning is billable when Container Scanning is enabled ([Artifact Registry pricing](https://cloud.google.com/artifact-registry/pricing)).  
**Assessment:** the security control is justified. Avoidable cost is build churn, not the scanner itself.  
**Opportunity:** add path-based change detection, avoid rebuilding unchanged images, and consolidate retry deployments.  
**Risk:** LOW for CI path filtering after tests; HIGH for disabling scanning.  
**Estimated savings:** cannot exceed the reported ₹99.47 scanning subtotal for the period; actual savings cannot be reliably quantified from current telemetry.  
**Confidence:** HIGH that churn contributes; LOW on rupee savings without per-scan SKU export.

### Artifact Registry

**Resource:** regional Docker repository `pickready` in `asia-south1`.  
**Current cost:** ₹28.89.  
**Configuration/evidence:** 15,702,445,434 bytes stored; 70 backend images, 63 frontend images, and three legacy `pickready-backend` images. Of these, 125 are older than seven days and 120 are older than 14 days. There are 17 untagged records. No cleanup policy or dry-run policy is configured. Active service and worker revisions use the current backend/frontend digests; older revisions provide rollback context.  
**Likely cost driver:** image retention and frequent builds, not multi-region storage. The repository is co-located with Cloud Run, which avoids cross-region transfer.  
**Opportunity:** retention policy that preserves active digests and at least 10 recent versions per package, initially in dry-run.  
**Risk:** MEDIUM because aggressive cleanup can remove rollback images or referenced job images.  
**Estimated savings:** at most the reported ₹28.89 storage subtotal if all storage disappeared, which is neither intended nor safe. Real savings cannot be reliably quantified from current telemetry.  
**Confidence:** HIGH that a policy will prevent growth; LOW for immediate savings because the repository is young and layers are deduplicated.

### Secret Manager

**Current cost:** ₹30.13.  
**Configuration:** 27 automatically replicated secrets and 30 total versions. `DATABASE_URL` has one enabled and one disabled version; `POSTGRES_PASSWORD` has two enabled versions; all other secrets have one enabled version.  
**Likely cost driver:** version storage and accesses. Access-frequency data was unavailable because no data-access export was configured.  
**Assessment:** cost is small and justified. No version was deleted. The urgent concern is that several workloads bypass Secret Manager for the database DSN.  
**Opportunity:** migrate plaintext DSNs into Secret Manager and later review old versions after rotation/rollback windows. This may slightly increase Secret Manager access cost but improves security.  
**Risk:** HIGH for secret changes.  
**Estimated savings:** none material; security takes precedence.  
**Confidence:** HIGH.

### Cloud Storage

One `asia-south1` Standard bucket, `pick-ready-503913-private-assets`, stores 33 objects totaling 1,307,411 bytes. Public access prevention and uniform bucket-level access are enabled. Soft delete retains objects for seven days. Existing lifecycle rules delete only `quarantine/` and `migration-staging/` objects after 30 days and abort incomplete multipart uploads after seven days. No user objects were changed. Cost is negligible and the present controls are appropriate.

### Compute Engine and networking

No VMs, persistent disks, snapshots, forwarding rules, Cloud Routers, Cloud NATs, or reservations were found. One reserved internal `/28` serverless address supports Direct VPC egress. The ₹0.02 reported cost does not justify further action. Cloud SQL public IPv4 remains enabled; authorized networks are empty and `sslMode` is encrypted-only. Networking was not changed.

### Billing observability limitations

The project is linked to billing account `01CD5F-4B3BA7-9CC83E`, but the active identity cannot read that account's budgets and the Cloud Billing Budget API is disabled. `bq ls --project_id=pick-ready-503913` returned no datasets, so no billing-export dataset exists in this project. Only the supplied service totals—not SKU, credit, tax, CUD, egress, or daily cost rows—were available. The default and required log sinks exist, with no BigQuery billing/log export sink.

## 4. Actions Actually Performed

No cost-changing action qualified as SAFE or clearly LOW-RISK after production/dependency checks.

| Resource | Change | Reason | Command Used | Risk | Expected Savings | Result |
| -------- | ------ | ------ | ------------ | ---- | ---------------- | ------ |
| All resources | None | Every material option touches production capacity, rollback retention, credentials, or live dependencies | N/A | None | ₹0.00 | Healthy pre-change state preserved |

Read-only HTTP checks returned:

- Frontend: HTTP 200, 191,607-byte response.
- Backend `/health`: HTTP 200 with application and database healthy.
- Cloud SQL: RUNNABLE; last 15 listed automated backups SUCCESSFUL.
- Cloud Run services and worker pools: latest revisions Ready; services route 100% to latest revisions.

## 5. Changes NOT Performed

| Resource | Proposed Change | Why Not Applied | Approval Required? | Estimated Savings |
| -------- | --------------- | --------------- | ------------------ | ----------------- |
| Cloud SQL | Resize 13 GiB to 10 GiB while retaining 2 vCPU | Production restart/performance risk; peak telemetry and SKU rates missing | Yes | Savings cannot be reliably quantified from current telemetry. |
| Cloud SQL | Reduce vCPU | Low average CPU alone does not cover burst/connection behavior | Yes | Savings cannot be reliably quantified from current telemetry. |
| Cloud SQL | Edition downgrade | Live edition is already Enterprise; premise was stale | No; no change exists | ₹0 |
| Cloud SQL | Disable backups/PITR or reduce retention | Violates recovery requirements | Yes; not recommended | Not estimated |
| Cloud SQL | Change HA | Instance is already zonal; disabling HA is inapplicable | No | ₹0 |
| Cloud SQL | Change public/private networking or SSL | Production connectivity/security impact | Yes | No demonstrated saving |
| Cloud SQL | Enable deletion protection | Reliability improvement but not a cost optimization; outside automatic savings scope | Yes | ₹0 |
| Cloud Run backend | Set min instances 1 to 0 | Production; observed ~7.7s startup p95 sample would affect latency | Yes | Savings cannot be reliably quantified from current telemetry. |
| Cloud Run frontend | Set min instances 1 to 0 | Production latency/SLO decision despite lower ~1.23s startup sample | Yes | Savings cannot be reliably quantified from current telemetry. |
| Cloud Run worker | Reduce CPU or memory | Active tasks and observed CPU spike signal; peak distributions/load tests missing | Yes | Savings cannot be reliably quantified from current telemetry. |
| Cloud Run beat | Remove/scale down | Exactly one scheduler is required; 512 MiB is already minimum practical allocation | Yes; not recommended | Not estimated |
| Redis | Delete/downgrade | Active clients/commands and production dependency; already minimum Basic capacity | Yes; redesign required | Ceiling ₹815.43, not a forecast |
| Artifact Registry | Delete old images | Rollback/reference risk; repository less than one month old | Yes | Less than ₹28.89; exact amount unavailable |
| Artifact Registry | Add enforcing cleanup policy | Future deletion behavior requires rollback-retention agreement | Yes | Prevents growth; immediate savings unknown |
| Vulnerability scanning | Disable scanning | Would weaken security | Yes; not recommended | Ceiling ₹99.47, not a forecast |
| Secret Manager | Delete/disable versions | Production-secret rollback risk | Yes | Negligible/unquantified |
| Cloud Storage | Delete objects/change retention | User data and recovery-policy risk; cost already ₹0 | Yes; not recommended | ₹0 |
| Compute | Delete serverless address | It supports Direct VPC connectivity | Yes; not recommended | Negligible |

## 6. Estimated Cost Impact

| Measure | Amount | Confidence |
| --- | ---: | --- |
| Supplied current subtotal | ₹8,609.06 | MEDIUM: user-provided, not independently queried |
| Changes applied | ₹0.00 | HIGH |
| Estimated post-change subtotal | ₹8,609.06 | HIGH if workload/rates are unchanged |
| Estimated monthly savings from this audit run | ₹0.00 | HIGH |

Potential savings are intentionally not summed. Without a billing export, exact period boundaries, SKU rates in INR, credits/free-tier allocation, and post-change load tests, doing so would fabricate precision. For the approval-gated changes above: **Savings cannot be reliably quantified from current telemetry.**

Useful upper bounds—not forecasts—are ₹815.43 for eliminating Redis, ₹99.47 for eliminating scanning, and ₹28.89 for eliminating all Artifact Registry storage. None of those eliminations is recommended. Cloud SQL and Cloud Run have meaningful savings potential, but require SKU-level modeling.

## 7. Production Safety Assessment

| Safety question | Result |
| --- | --- |
| Were production services modified? | No |
| Were databases modified? | No |
| Was data deleted? | No |
| Was HA changed? | No |
| Were backups or PITR changed? | No |
| Were security controls changed? | No |
| Was vulnerability scanning changed? | No |
| Was networking changed? | No |
| Were secrets or secret versions modified? | No |
| Was Redis modified? | No |
| Were Artifact Registry images/policies modified? | No |
| Were bucket lifecycle, versioning, retention, or objects modified? | No |

The final state matches the before state. HTTP health and control-plane readiness checks succeeded after discovery.

## 8. Remaining Optimization Opportunities

### P0 — urgent

#### Remove plaintext database credentials from runtime definitions

- **Resource:** backend service, both worker pools, migration job, and legacy `pickready-grant-role` job.
- **Proposed change:** rotate both exposed database credentials; bind all DSNs/passwords through Secret Manager; remove or neutralize the failed legacy diagnostic job after evidence/ownership review.
- **Expected savings:** none; this prevents compromise and potentially catastrophic cost/data impact.
- **Confidence:** HIGH.
- **Risk:** HIGH during rotation; HIGH if left unresolved.
- **Prerequisites:** coordinated dual-credential rollout or maintenance window, database-user inventory, rollback secret version, verification of every consumer.
- **Approval required:** Yes.
- **Rollback:** temporarily restore the prior secret version and redeploy known-good revisions; do not leave old credentials active after successful verification.

#### Enable Cloud SQL deletion protection

- **Resource:** `pickready-postgres`.
- **Proposed change:** enable deletion protection.
- **Expected savings:** none; reduces accidental-loss risk.
- **Confidence:** HIGH.
- **Risk:** LOW operationally, but this is a production-policy change.
- **Prerequisites:** owner confirmation that IaC/decommission workflows account for the flag.
- **Approval required:** Yes.
- **Rollback:** disable the flag with a second approved patch.

### P1 — high value

#### Export billing data and model Cloud SQL right-sizing

- **Resource:** billing account and `pickready-postgres`.
- **Proposed change:** enable detailed billing export; then test 2 vCPU/10 GiB (`db-custom-2-10240`) as a first-stage reduction.
- **Expected savings:** Savings cannot be reliably quantified from current telemetry.
- **Confidence:** MEDIUM.
- **Risk:** HIGH for resize; LOW for export.
- **Prerequisites:** billing-account permission, 30-day p95/p99 memory/connections, load test, maintenance window, tested rollback.
- **Approval required:** Yes.
- **Rollback:** patch back to `db-custom-2-13312`; confirm RUNNABLE and application health.

#### Review Cloud Run warm-instance SLOs

- **Resource:** frontend first, then backend.
- **Proposed change:** canary min 0 during an agreed window or retain min 1 only during business hours via an approved scheduler/automation.
- **Expected savings:** Savings cannot be reliably quantified from current telemetry.
- **Confidence:** MEDIUM for frontend; LOW for backend because startup is slower.
- **Risk:** MEDIUM/HIGH (cold starts and latency).
- **Prerequisites:** SLO, synthetic probes, p95/p99 latency baseline, rollback command.
- **Approval required:** Yes.
- **Rollback:** restore min 1 and verify Ready/HTTP 200.

#### Reduce CI/CD image and revision churn

- **Resource:** `.github/workflows/deploy.yml`, build/deploy scripts, Artifact Registry.
- **Proposed change:** path filters, build only changed component, skip deploy when digest/config is unchanged, retain rollback tags deliberately.
- **Expected savings:** bounded by portions of ₹99.47 scanning and ₹28.89 storage, plus small job/deploy compute; not reliably quantifiable.
- **Confidence:** HIGH that growth/scans decline; LOW on exact INR.
- **Risk:** LOW if release tests prove unchanged components are correctly skipped.
- **Prerequisites:** dependency map between backend/frontend and shared configuration.
- **Approval required:** repository change approval, not production resource deletion.
- **Rollback:** revert workflow commit.

### P2 — medium

#### Artifact cleanup policy in dry-run, then enforce

- **Resource:** `pickready` repository.
- **Proposed change:** retain at least 10 newest versions per package and all protected release tags; delete only untagged/old versions older than an agreed 30- or 60-day rollback window.
- **Expected savings:** immediate savings likely small; prevents continued 15.7-GB growth.
- **Confidence:** HIGH on growth control; LOW on exact savings.
- **Risk:** MEDIUM.
- **Prerequisites:** enumerate every active service, worker, job, and rollback digest; dry-run review for at least one release cycle.
- **Approval required:** Yes before enforcement.
- **Rollback:** disable policy; deleted images would need rebuild/repush, so policy review is essential.

#### Worker memory profile

- **Resource:** `pickready-worker`.
- **Proposed change:** collect high-resolution p99/maximum resident memory under representative matching/resume workloads; consider 1 GiB only if safe.
- **Expected savings:** Savings cannot be reliably quantified from current telemetry.
- **Confidence:** LOW until peak data exists.
- **Risk:** HIGH (OOM/task retries).
- **Prerequisites:** fix current LLM/tracing failures, workload replay, queue-depth monitoring.
- **Approval required:** Yes.
- **Rollback:** redeploy 2 GiB immediately and requeue failed idempotent tasks.

### P3 — low

- Review whether the always-on Celery/Redis architecture should eventually use event-driven Cloud Run jobs, Cloud Tasks, or Pub/Sub. This is a redesign, not an optimization to execute under this audit.
- Consider a Cloud SQL CUD and Cloud Run flexible CUD only after right-sizing and only for the proven continuous baseline. Committing before rightsizing can lock in waste.
- Review two enabled `POSTGRES_PASSWORD` versions after credential rotation and rollback expiry; do not disable either before dependency verification.

## 9. Recommended Next Steps

The commands below are examples for an approved maintenance plan. **Do not run them as an undivided script.** Capture the current configuration and perform the stated validation between steps.

### 9.1 Enable database deletion protection

```bash
gcloud sql instances patch pickready-postgres \
  --project=pick-ready-503913 \
  --deletion-protection
```

Rollback after separate approval:

```bash
gcloud sql instances patch pickready-postgres \
  --project=pick-ready-503913 \
  --no-deletion-protection
```

### 9.2 Stage a Cloud SQL memory resize

Only after billing modeling, load testing, and a maintenance window:

```bash
gcloud sql instances patch pickready-postgres \
  --project=pick-ready-503913 \
  --tier=db-custom-2-10240
```

Rollback:

```bash
gcloud sql instances patch pickready-postgres \
  --project=pick-ready-503913 \
  --tier=db-custom-2-13312
```

Verify after either command:

```bash
gcloud sql instances describe pickready-postgres \
  --project=pick-ready-503913 \
  --format='yaml(name,state,settings.tier,settings.edition)'

curl --fail --silent --show-error \
  https://pickready-backend-fcunsks2nq-el.a.run.app/health
```

### 9.3 Canary frontend scale-to-zero

After an approved latency experiment:

```bash
gcloud run services update pickready-frontend \
  --project=pick-ready-503913 \
  --region=asia-south1 \
  --min=0
```

Rollback:

```bash
gcloud run services update pickready-frontend \
  --project=pick-ready-503913 \
  --region=asia-south1 \
  --min=1
```

Note: validate the exact flag against the installed `gcloud run services update --help` before the approved change; older CLI versions may expose this as `--min-instances`.

### 9.4 Artifact cleanup policy

Create a reviewed `cleanup-policies.json` that protects release tags and retains an agreed rollback count. Start with dry run:

```bash
gcloud artifacts repositories set-cleanup-policies pickready \
  --project=pick-ready-503913 \
  --location=asia-south1 \
  --policy=cleanup-policies.json \
  --dry-run
```

After at least one release-cycle review, remove `--dry-run` only with approval. To roll back policy behavior before deletion:

```bash
gcloud artifacts repositories delete-cleanup-policies pickready \
  --project=pick-ready-503913 \
  --location=asia-south1 \
  --policynames=DELETE_POLICY_ID,KEEP_POLICY_ID
```

### 9.5 Billing export and budgets

Ask a Billing Account Administrator to enable the Billing Budget API, create monthly/forecast thresholds (50%, 75%, 90%, 100%), and enable detailed BigQuery billing export. The current identity cannot perform or verify these actions. Do not enable a billing export without selecting the owning dataset, location, retention, and access model.

## 10. FinOps Recommendations

- **Budgets and alerts:** create billing-account budgets with actual and forecast thresholds, Pub/Sub notification, an accountable owner, and a documented response runbook. API access is currently absent.
- **Billing export:** enable detailed usage-cost export with resource-level data to a dedicated FinOps dataset. Preserve credits, adjustments, tags, and price fields. Build daily service/SKU/environment views.
- **Labels and allocation:** add `environment=production`, `service`, `owner`, `cost-center`, and `data-classification` labels where supported. Current production status is carried mainly in environment variables rather than cost-allocation metadata.
- **Environment separation:** use separate projects for production and non-production. This project appears production-only; do not mix future development resources into it.
- **CUDs:** model Cloud SQL CUDs and Cloud Run flexible CUDs only after right-sizing. The two worker pools provide a measurable continuous baseline; a commitment should not cover uncertain service peaks.
- **Cloud SQL:** review memory/CPU using p95/p99 and peak telemetry, connection churn, Query Insights, and slow queries. Do not interpret 112 MB storage as a compute-sizing signal.
- **Cloud Run:** review warm-instance SLOs separately for frontend/backend. Profile worker peak CPU/RAM and queue depth. Keep beat at one instance unless scheduler architecture changes.
- **Redis:** retain while Celery depends on it. Track commands, connected clients, memory, evictions, rejected connections, and queue latency. Revisit only during an approved queue redesign.
- **Artifact Registry:** establish protected rollback tags and a dry-run cleanup policy; keep repository/runtimes co-located.
- **Scanning governance:** retain scanning; reduce duplicate builds and unchanged-component pushes; report scans per successful release.
- **Monthly review:** review cost by service/SKU/environment, utilization, new resources, cleanup-policy effects, CUD coverage/utilization, and anomaly root causes.
- **Anomaly detection:** alert on daily spend deviation, Cloud Run allocation-time jumps, unexpected worker instance count, Cloud SQL storage growth, image push bursts, and Secret Manager access spikes.
- **Ownership:** assign one business owner and one technical owner to every expensive resource. Require an expiry/review date for temporary jobs, images, and exceptions.
- **Security-cost linkage:** rotate the exposed database credentials immediately under change control. Security incidents create costs far beyond the modest savings targeted here.

## 11. Final Resource State

- **Cloud SQL:** unchanged; Enterprise PostgreSQL 16.14, zonal, 2 vCPU/13 GiB, RUNNABLE, backups/PITR/Query Insights enabled, no replicas, application health OK.
- **Cloud Run:** unchanged; backend and frontend Ready with 100% latest-revision traffic and min 1; worker and beat Ready with one manual instance each; migration job Ready. Frontend/backend HTTP checks passed.
- **Memorystore:** unchanged; Basic 1 GiB Redis 7, READY, active clients/commands.
- **Artifact Registry:** unchanged; one 15.70 GB regional Docker repository, scanning active, no cleanup policy, no images deleted.
- **Vulnerability Scanning:** unchanged and active.
- **Secret Manager:** unchanged; 27 secrets/30 versions; no version accessed for payload, disabled, destroyed, or deleted by this audit.
- **Cloud Storage:** unchanged; one private regional bucket, 33 objects/1.25 MiB, existing lifecycle and soft-delete controls retained.
- **Compute Engine:** unchanged; no VM/disk/snapshot/forwarding-rule/router/NAT/reservation resources; one serverless internal address retained.

## Appendix A — Read-only Command Log

The following commands were executed. Commands returning configuration that contained credentials were treated as sensitive; credentials are not reproduced in this report.

### Project and billing

```bash
gcloud config get-value project
gcloud billing projects describe pick-ready-503913 --format=json
bq ls --project_id=pick-ready-503913 --format=prettyjson
gcloud billing budgets list --billing-account=01CD5F-4B3BA7-9CC83E --format=json
gcloud logging sinks list --project=pick-ready-503913 --format=json
```

Results: project correct; billing linked; no BigQuery datasets listed; budget query failed because the API is disabled and the identity lacks access; only default/required log sinks exist.

### Cloud SQL

```bash
gcloud sql instances describe pick-ready-postgres --project=pick-ready-503913 --format=json
gcloud sql instances list --project=pick-ready-503913 --format=json
gcloud sql instances describe pickready-postgres --project=pick-ready-503913 --format="json(name,state,databaseVersion,databaseInstalledVersion,region,gceZone,settings.tier,settings.edition,settings.availabilityType,settings.dataDiskSizeGb,settings.dataDiskType,settings.storageAutoResize,settings.storageAutoResizeLimit,settings.backupConfiguration,settings.insightsConfig,settings.ipConfiguration,settings.databaseFlags,settings.maintenanceWindow,settings.deletionProtectionEnabled,ipAddresses,replicaNames,masterInstanceName)"
gcloud sql instances describe pickready-postgres --project=pick-ready-503913 --format="yaml(name,state,databaseVersion,databaseInstalledVersion,region,gceZone,settings.tier,settings.edition,settings.availabilityType,settings.dataDiskSizeGb,settings.dataDiskType,settings.storageAutoResize,settings.storageAutoResizeLimit,settings.backupConfiguration,settings.insightsConfig,settings.ipConfiguration,settings.databaseFlags,settings.maintenanceWindow,settings.deletionProtectionEnabled,ipAddresses,replicaNames,masterInstanceName)"
gcloud sql backups list --instance=pickready-postgres --project=pick-ready-503913 --limit=20 --format="table(id,status,type,startTime,endTime,location)"
gcloud sql users list --instance=pickready-postgres --project=pick-ready-503913 --format="table(name,type,passwordPolicy.status)"
```

The first describe returned 404 because the supplied name was wrong; inventory resolved the correct name.

### Cloud Run

```bash
gcloud run services list --project=pick-ready-503913 --platform=managed --format=json
gcloud run services list --project=pick-ready-503913 --region=asia-south1 --platform=managed --format=json
gcloud run revisions list --project=pick-ready-503913 --region=asia-south1 --platform=managed --format=json
gcloud run worker-pools list --project=pick-ready-503913 --region=asia-south1 --format=json
gcloud run jobs list --project=pick-ready-503913 --region=asia-south1 --format=json
```

Several attempted `--format=table(...)`/`value(...)` projections failed because slash-containing annotation/label keys were parsed by this `gcloud` version. The JSON commands above were then processed read-only in PowerShell to produce the summaries and revision counts.

### Redis, artifacts, scanning, secrets, storage, and compute

```bash
gcloud redis instances list --project=pick-ready-503913 --region=asia-south1 --format=json
gcloud artifacts repositories list --project=pick-ready-503913 --format=json
gcloud artifacts repositories describe pickready --project=pick-ready-503913 --location=asia-south1 --format=json
gcloud artifacts repositories describe pickready --project=pick-ready-503913 --location=asia-south1 --format="yaml(cleanupPolicies,cleanupPolicyDryRun,sizeBytes,vulnerabilityScanningConfig)"
gcloud artifacts packages list --project=pick-ready-503913 --repository=pickready --location=asia-south1 --format=json
gcloud artifacts docker images list asia-south1-docker.pkg.dev/pick-ready-503913/pickready --include-tags --format=json
gcloud secrets list --project=pick-ready-503913 --format=json
gcloud secrets list --project=pick-ready-503913 --format="value(name.basename())"
gcloud secrets versions list SECRET_NAME --project=pick-ready-503913 --format=json
gcloud storage buckets list --project=pick-ready-503913 --format=json
gcloud storage du --summarize --readable-sizes gs://pick-ready-503913-private-assets
gcloud storage ls --recursive --long gs://pick-ready-503913-private-assets
gcloud compute instances list --project=pick-ready-503913 --format=json
gcloud compute disks list --project=pick-ready-503913 --format=json
gcloud compute addresses list --project=pick-ready-503913 --format=json
gcloud compute snapshots list --project=pick-ready-503913 --format=json
gcloud compute forwarding-rules list --project=pick-ready-503913 --format=json
gcloud compute reservations list --project=pick-ready-503913 --format=json
gcloud compute networks subnets describe default --project=pick-ready-503913 --region=asia-south1 --format=json
gcloud compute routers list --project=pick-ready-503913 --format=json
gcloud compute routers nats list --project=pick-ready-503913 --router=default --region=asia-south1 --format=json
```

`gcloud secrets versions list PLACEHOLDER ...` was also attempted once and correctly returned NOT_FOUND before the real secret-name loop. The NAT query returned 404 because no `default` router exists.

### Monitoring, logging, health, and local architecture review

PowerShell used `gcloud auth print-access-token` only in-memory and called the read-only Cloud Monitoring endpoint:

```text
GET https://monitoring.googleapis.com/v3/projects/pick-ready-503913/timeSeries
```

with metric filters, a 30-day interval, one-day alignment, `view=FULL`, and `pageSize=1000`. Metrics queried were:

```text
cloudsql.googleapis.com/database/cpu/utilization
cloudsql.googleapis.com/database/memory/utilization
cloudsql.googleapis.com/database/disk/bytes_used
cloudsql.googleapis.com/database/disk/utilization
cloudsql.googleapis.com/database/disk/read_ops_count
cloudsql.googleapis.com/database/disk/write_ops_count
cloudsql.googleapis.com/database/network/connections
cloudsql.googleapis.com/database/postgresql/new_connection_count
run.googleapis.com/request_count
run.googleapis.com/request_latencies
run.googleapis.com/container/billable_instance_time
run.googleapis.com/container/cpu/allocation_time
run.googleapis.com/container/memory/allocation_time
run.googleapis.com/container/cpu/utilizations
run.googleapis.com/container/memory/utilizations
run.googleapis.com/container/instance_count
run.googleapis.com/container/startup_latencies
redis.googleapis.com/stats/memory/usage_ratio
redis.googleapis.com/clients/connected
redis.googleapis.com/stats/cpu_utilization
redis.googleapis.com/commands/calls
redis.googleapis.com/stats/evicted_keys
redis.googleapis.com/stats/reject_connections_count
```

The metric-descriptor endpoint was also queried read-only for Cloud SQL, Cloud Run, and Redis metric prefixes. Two first-pass CPU/memory distribution alignments returned HTTP 400 because `ALIGN_MEAN` is invalid for DELTA/DISTRIBUTION; those erroneous values were discarded and the metrics were re-queried with `ALIGN_PERCENTILE_50`.

Logging and HTTP commands were equivalent to:

```bash
gcloud logging read 'resource.type="cloud_run_worker_pool" AND resource.labels.worker_pool_name="pickready-worker" AND timestamp>="2026-08-23T00:00:00Z"' --project=pick-ready-503913 --limit=1000 --format=json
gcloud logging read 'resource.type="cloud_run_worker_pool" AND resource.labels.worker_pool_name="pickready-beat" AND timestamp>="2026-08-23T00:00:00Z"' --project=pick-ready-503913 --limit=1000 --format=json
gcloud logging read 'resource.type="cloud_run_worker_pool" AND resource.labels.worker_pool_name="pickready-worker" AND severity>=ERROR AND timestamp>="2026-08-23T00:00:00Z"' --project=pick-ready-503913 --limit=100 --format=json
```

PowerShell `Invoke-WebRequest` performed GET requests to the frontend root and backend `/health`. An initial incorrectly quoted logging filter failed and was replaced by the successful commands above.

Local read-only inspection used:

```bash
rg -n --hidden --glob '!**/.git/**' "worker-pools|cloud run|gcloud run|artifact|cleanup|memorystore|min-instances|manual-instance|pickready-worker|pickready-beat" .
rg -n -C 5 "docker build|docker push|gcloud builds|run deploy|worker-pools deploy|min-instances|instances=1" .github/workflows/deploy.yml scripts/deploy.sh infra/gcp/deploy.sh
git status --short
gcloud run services update --help
gcloud artifacts repositories set-cleanup-policies --help
gcloud artifacts repositories delete-cleanup-policies --help
```

Existing user modifications in `backend/app/scripts/probe_llm_models.py` and `backend/app/services/llm_router.py` were observed and left untouched.
