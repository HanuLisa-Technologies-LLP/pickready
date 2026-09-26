# Restoring the database

**Status: WRITTEN, NEVER REHEARSED.** Every command here is derived from the
Terraform in `infra/` and from AWS's documented behaviour. None of it has been
executed against a real instance in any environment, so read the RTO below as a
target rather than as a measurement. The first person to rehearse this should
replace the estimates with what actually happened and delete this paragraph's
first sentence.

`DEPLOY_AWS.md` §7 covers rolling back an **image**. This file covers the case
that document does not: the data is wrong, and the fix is to rewind it.

---

## 1. What is recoverable, and what is not

| | Mechanism | Window |
|---|---|---|
| Postgres | RDS automated backups plus transaction logs | `backup_retention_days`: 7 in pilot and staging, 30 in production |
| Postgres, on a deliberate teardown | Final snapshot | Kept until somebody deletes it |
| S3 (private bucket) | Versioning plus `noncurrent_retain_days` | Per the `s3` module |
| S3 (inbound mail) | Versioning, added 2026-09-17 | `retention_days` |
| Terraform state | S3 bucket versioning | Indefinite |
| **Redis** | **Nothing in pilot or staging.** Production keeps one daily snapshot | See §5 |
| Secrets Manager | AWS recovery window on delete | 30 days by default |

**Automated backups are deleted with the instance.** Thirty days of retention
protects nothing once the instance is gone, which is why
`skip_final_snapshot = false` is now the default in `infra/modules/rds` rather
than a production-only setting.

### RPO

**About five minutes.** RDS uploads transaction logs to S3 every five minutes,
so a point-in-time restore can land at any second inside the retention window
up to roughly five minutes before now. `LatestRestorableTime` on the instance
is the authoritative answer at the moment you need it:

```bash
aws rds describe-db-instances \
  --db-instance-identifier readypick-production \
  --query 'DBInstances[0].[LatestRestorableTime,EarliestRestorableTime]'
```

### RTO

**Target: under two hours. Unmeasured.** The restore itself is the smallest
part of it. The steps that follow the restore, in §3 and §4, are where the time
goes, and they are the steps nobody has walked through under pressure.

---

## 2. Decide first: is a rewind actually the answer

A point-in-time restore discards every write made after the restore point. For
this product that specifically means:

- assessments completed after the restore point are gone, and the customer was
  already charged for them in `credit_ledger`, which is also gone
- PRISM reports written after it are gone, and a report is immutable by design,
  so there is no regeneration path that produces the same document
- candidate applications submitted after it are gone, and the candidate has an
  email saying they applied

If the damage is confined to a few rows, restore into a **second instance** and
copy the rows across. That is §3 with the rename in §4 skipped, and it is very
often the right answer.

---

## 3. Restore

The restore creates a **new instance**. It never modifies the source, which is
what makes it safe to run while deciding.

```bash
ENV=production                       # or pilot, or staging
SOURCE="readypick-${ENV}"
TARGET="readypick-${ENV}-restore-$(date -u +%Y%m%d%H%M)"
RESTORE_TO="2026-09-17T09:14:00Z"    # UTC, inside the window from §1

# READ THE SECURITY GROUP OFF THE SOURCE INSTANCE rather than out of a
# Terraform output. There is no `rds_security_group_id` output, and the
# instance itself is in any case the authority on what it is attached to right
# now, which is what the restore has to reproduce.
SG="$(aws rds describe-db-instances --db-instance-identifier "$SOURCE" \
  --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)"

aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier "$SOURCE" \
  --target-db-instance-identifier "$TARGET" \
  --restore-time "$RESTORE_TO" \
  --db-subnet-group-name "$SOURCE" \
  --vpc-security-group-ids "$SG" \
  --db-parameter-group-name "$SOURCE" \
  --no-publicly-accessible \
  --no-multi-az \
  --deletion-protection \
  --enable-iam-database-authentication \
  --copy-tags-to-snapshot
```

**Every one of those arguments is load bearing, and the defaults are wrong.**
A restore does **not** inherit the source's networking or parameters: omit
`--db-subnet-group-name` and the instance lands in the **default VPC**, which in
this account has a route to the internet, with the default security group.
Omit `--db-parameter-group-name` and it comes up on the AWS default parameter
group, without `log_connections`, which is the setting that settled the
2026-09-11 outage in one minute after an hour of reading tracebacks.

`--no-multi-az` is deliberate even for production: a Multi-AZ restore takes
materially longer, and the first thing to establish is whether the data is
right. Turn it on afterwards with `modify-db-instance`.

**Wait for it, and do not trust the command returning:**

```bash
aws rds wait db-instance-available --db-instance-identifier "$TARGET"
```

### Verify before going any further

```bash
aws rds describe-db-instances --db-instance-identifier "$TARGET" \
  --query 'DBInstances[0].[Endpoint.Address,DBInstanceStatus,PubliclyAccessible]'
```

Then connect and check that the data is what you expected at that timestamp.
**Check the table, not the timestamp** -- a restored instance reports the
restore time whether or not the rows you needed are in it.

---

## 4. The two things that will be wrong afterwards, and both are silent

### 4.1 The master credential is NEW, and the application's is STALE

`infra/modules/rds` sets `manage_master_user_password = true`, so the master
password lives in a Secrets Manager secret that AWS rotates. **A restored
instance gets its own secret.** The old one still exists, still rotates, and
now belongs to an instance you may be about to delete.

The application does not use the master credential -- that is the whole point
of the 2026-09-11 change -- so the more important half is the other one.
`pickready_app` is a **role inside the database**, so it is restored with the
data, **carrying whatever password it had at the restore point**. Secrets
Manager still holds today's password. If `rotate-app-db-credential.sh` ran at
any time between the restore point and now, those two disagree, and every
connection fails with `password authentication failed` on a connection that
matched `hostssl` perfectly -- the exact error, with the exact misleading
`no encryption` suffix, that cost the first hour of the 2026-09-11
investigation.

**Fix it by rotating, not by guessing:**

```bash
./scripts/rotate-app-db-credential.sh "$ENV"
```

That mints a new password inside the task, proves it by opening a second
connection and reading through the RLS policies, and only then writes it to
Secrets Manager. Run it **after** `DATABASE_URL` points at the restored host,
because it composes the new DSN from the existing one.

### 4.2 `DATABASE_URL` points at the old endpoint

A restored instance has a different identifier and therefore a different
hostname. Two ways forward, and they are not equivalent.

**Option A, the rename swap.** Terraform's `identifier` still names the
original, so renaming the restored instance into that name means Terraform
manages it with no import. It is also an outage and a one-way door.

```bash
aws rds modify-db-instance --db-instance-identifier "$SOURCE" \
  --new-db-instance-identifier "${SOURCE}-damaged" --apply-immediately
aws rds wait db-instance-available --db-instance-identifier "${SOURCE}-damaged"

aws rds modify-db-instance --db-instance-identifier "$TARGET" \
  --new-db-instance-identifier "$SOURCE" --apply-immediately
aws rds wait db-instance-available --db-instance-identifier "$SOURCE"
```

The endpoint hostname follows the identifier, so `DATABASE_URL` needs no edit
for the host. It still needs §4.1.

**Do not delete `${SOURCE}-damaged` on the day of the incident.** It is the
only copy of the writes the restore discarded, and somebody will want a row out
of it within the week. Deletion protection is on, which is deliberate friction.

**Option B, repoint the secret.** Leave the restored instance under its own
name and rewrite the host in `DATABASE_URL`. Faster and reversible, and it
leaves Terraform managing an instance nobody is using: the next `apply` will
happily converge the *original* and the product will still be talking to the
restore. **If you take Option B, say so in the environment's `main.tf` in the
same hour**, or the next deploy quietly undoes the recovery.

Either way, the DSN keeps `?ssl=require`. asyncpg's default `prefer` mode
retries a refused connection **without TLS**, which both carries a tenant's
data across the VPC in the clear and replaces the real error with a misleading
one.

### 4.3 Then restart the services

ECS tasks hold a connection pool to the old endpoint and will not notice a
rename.

```bash
CLUSTER="readypick-${ENV}"
# DISCOVERED, not listed, for the reason `scripts/deploy-services.sh` gives in
# its own comment: a hand-kept list is exactly what silently stops covering the
# newest service.
aws ecs list-services --cluster "$CLUSTER" --query 'serviceArns[]' --output text \
  | tr '\t' '\n' | sed 's#.*/##' \
  | while read -r svc; do
      aws ecs update-service --cluster "$CLUSTER" \
        --service "$svc" --force-new-deployment >/dev/null
      echo "restarting $svc"
    done
```

Lambda needs nothing: `workers/runtime.worker_session` builds a fresh engine
per invocation, which is the same property that makes Lambda concurrency drive
peak connection count.

---

## 5. Redis is not restored, and that has consequences

Redis holds four things (`infra/modules/elasticache`): the rate limiter, the
caches, the background run-status record, and the **proctoring warning
counter**. It is not a broker any more, so nothing queued is lost -- but a
rewound database and a Redis that was never rewound disagree, and every one of
those disagreements is silent.

- A **proctoring warning counter** for a session whose row no longer exists,
  or which now reads fewer warnings than Redis does. The server decides from
  Redis, so a candidate could be terminated on warnings the record does not
  show.
- A **run-status record** saying a scoring run finished, against a report row
  that the restore removed. The recruiter's screen stops polling and shows a
  report that is not there.
- **Cached `role_permissions` rows** for a tenant whose grants have moved.

**So flush it, deliberately, as part of the restore:**

```bash
redis-cli -h <primary endpoint> --tls -a "$(terraform -chdir=infra/environments/$ENV output -raw redis_auth_token)" FLUSHALL
```

Note `--tls`: transit encryption is on, and a client connecting without it
**hangs** rather than refusing, which looks like a network problem and is not.

**What a flush costs, stated rather than discovered:** every in-flight
assessment's warning count returns to zero, every polling screen reads PENDING
until its run's status is rewritten, and the first request per tenant pays a
cache miss. All three are strictly better than acting on a stale number.
Nothing durable is lost, because nothing durable lives there -- that is why
`maxmemory-policy` is `noeviction` and why `/health` probes Redis.

Production's single daily snapshot is **not** a recovery mechanism for this.
It is a restart convenience, and restoring it would reinstate exactly the stale
counters this section says to discard.

---

## 6. S3 is not rewound either

A database restore moves rows back; it does not touch objects. Afterwards:

- **Orphaned objects**: resumes, reports and compressed assessment videos
  uploaded after the restore point, now referenced by no row. They are billed
  and invisible. The lifecycle rules do not reach them, because those rules
  are scoped by prefix and age, not by whether anything points at them.
- **`project-intake/`**: temporary project originals whose `candidate_projects`
  row no longer exists. `pickready.reconcile_project_intake` sweeps hourly and
  the bucket-wide backstop expiry catches the rest, so this one self-heals.
- **Inbound mail**: unaffected and still readable, which is the point of §1's
  versioning row. An employer's verification reply survives a database rewind
  even though the `bgv_verifications` row it belongs to may not.

**Do not write a sweep that deletes orphans.** "No row references this object"
is also true of an object whose row is about to be re-created by a retry, and
an erasure that is wrong is not recoverable in the direction that matters.

---

## 7. Restoring Terraform state

Different failure, same file. If a state file is corrupted or truncated, the
bucket is versioned (`DEPLOY_AWS.md` §4, Step 1) and the previous version is
the recovery:

```bash
aws s3api list-object-versions --bucket readypick-tfstate-rp-manju-0904 \
  --prefix production/terraform.tfstate --max-items 5
aws s3api get-object --bucket readypick-tfstate-rp-manju-0904 \
  --key production/terraform.tfstate --version-id <the good one> restored.tfstate
aws s3api put-object --bucket readypick-tfstate-rp-manju-0904 \
  --key production/terraform.tfstate --body restored.tfstate
```

Take the DynamoDB lock out of the picture first, or an apply started by
somebody else will write over the restore.

---

## 8. What this file does not cover

- **A region-wide failure.** There is no cross-region replica and no
  cross-region backup copy, in any environment. The recovery from losing
  `ap-south-2` is a rebuild from Terraform into another region plus a restore
  from whatever snapshots were copied there, and **nothing copies them there
  today**. That is an owner decision with a cost attached, not an oversight
  this runbook can close.
- **Secrets Manager deletion.** A deleted secret has a 30-day recovery window
  and `restore-secret` brings it back. Past 30 days the values are gone and
  every one of them has to be minted again.
- **A rehearsal.** Until somebody runs §3 against staging and records the
  wall-clock time, the RTO above is an estimate written by whoever wrote this
  file.
