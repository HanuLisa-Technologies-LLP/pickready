# ReadyPick on AWS

The deployment runbook. It replaces `DEPLOY_GCP.md` and `DEPLOY_GCP_RUNBOOK.md`,
which were removed with the Cloud Run infrastructure.

> ## NOTHING IN THIS DOCUMENT HAS BEEN EXECUTED.
>
> spec-doc6 §D5: *"No `terraform apply`. No live resource creation. No DNS
> cutover. Running `apply` against any real account in this phase is a failure of
> scope."* §17 repeats it in the acceptance list: *"No live AWS deployment has
> been executed. Running `terraform apply` against a real account in this phase
> is a failure of scope, not an accomplishment."*
>
> So this runbook is written to be followed and it has not been followed. Where a
> step names an expected output, that is what the command is documented to
> produce, not a result anybody observed here. Nothing below should be read as a
> report of something that happened.

---

## 1. What is actually verified, and what is not

This table is the one to read before repeating any claim from this document.

| Claim | How to reproduce | Status |
|---|---|---|
| All 11 modules and both environment roots are syntactically valid, resolve every reference and type-check their variables | `./infra/validate.sh` | **Verified, offline** |
| Every file is canonically formatted | `terraform fmt -check -recursive infra` | **Verified** |
| The resource graph resolves and every argument type-checks against the provider schema, for staging and production | `./infra/plan-offline.sh` | **Verified, offline.** Read §2 for exactly what this does and does not mean |
| No credential is delivered as a plain environment variable | `pytest backend/tests/test_deploy_secret_hygiene.py` | **Verified** |
| No service role can read another service's secret | same | **Verified** |
| The public job path is unauthenticated at the listener AND in the application, and nothing else is | same | **Verified** |
| No wildcard IAM grant outside the documented resource-less actions | `python infra/check-no-wildcard-iam.py` | **Verified** |
| No insecure configuration outside the reasoned exception list | `checkov --config-file infra/.checkov.yml` | **Verified** |
| The health endpoint refuses a task whose database or broker is unreachable | `pytest backend/tests/test_health.py` | **Verified** |
| An AWS account can create these resources | — | **NOT VERIFIED.** No account exists for this phase |
| Service quotas suffice | — | **NOT VERIFIED** |
| The IAM roles behave once something assumes them | — | **NOT VERIFIED** |
| The instance and node types are offered in the chosen region | — | **NOT VERIFIED.** The region has not been chosen |
| Anything applies, runs, or serves traffic | — | **NOT VERIFIED, and must not be in this phase** |

### What the offline plan proves

`./infra/plan-offline.sh` runs `terraform plan` for both environments with no
credentials, no account and no network. That is a real improvement over
`validate` alone, and it is worth being exact about the size of it.

The previous phase wrote that a plan "cannot complete without credentials". True
by default: the AWS provider makes four calls before it plans anything, including
`sts:GetCallerIdentity`. Each has a `skip_*` argument, and `var.planning_profile`
wires all four. The plan then runs against
`infra/environments/offline-plan.tfvars`, whose account is all zeros, whose
region `xx-plan-1` does not exist, and whose domain is under the RFC 2606
`.invalid` TLD reserved so that it can never resolve.

**It proves:** the configuration is internally consistent; the resource graph
resolves; every module input and output reference exists; every resource argument
type-checks against the provider schema; every `for_each` key resolves against
the collection it indexes; every variable validation passes.

That last pair is the gap over `validate`, and it is not theoretical. The first
offline plan run on this repository failed with `Invalid index` on
`var.secret_policy_arns["frontend"]` — an apply-time failure that eleven modules'
worth of `terraform validate` had been reporting as clean.

**It does not prove:** anything about a real AWS account. Not that an account can
create these resources, not that quotas suffice, not that IAM behaves once
something assumes a role, not that the instance types exist in the chosen region,
not that the domain resolves or that the hosted zone is the one the registrar
delegates to.

**A green plan is not "ready to run".** It has never spoken to AWS.

---

## 2. The decisions that are still the owner's

spec-doc6 §D5 removes these from the implementation's hands. Each is a Terraform
variable with **no default**, so a plan or an apply fails until it is supplied
rather than proceeding on a guess. They are documented individually in
`infra/environments/staging/README.md` and `infra/environments/production/README.md`.

| Variable | What it is |
|---|---|
| `account_id` | The 12-digit AWS account number for this environment |
| `region` | The AWS region |
| `availability_zones` | At least two, inside that region |
| `domain_name` | The hostname the environment is served on |
| `hosted_zone_id` | The Route53 zone id that already serves that domain |
| `storage_bucket_name` | Globally unique bucket for resumes and compliance documents |
| `access_logs_bucket_name` | Globally unique bucket for the load balancer's access logs |

### On the region

**`ap-south-1` (Mumbai) is the likely choice, and the decision is the owner's.**

The reasoning that points there: the product bills in INR, its candidates are in
India, and candidate resumes are personal data whose subjects would reasonably
expect it to stay in the jurisdiction they are in. Latency follows the same
argument.

It was previously written into the code as an assumption. spec-doc6 §D5 removes
it by name — *"Region assumption `ap-south-1` is removed as an assumption and
becomes a required variable. Do not hardcode it anywhere"* — because a default is
a decision that was made without anybody making it, and this one has real
consequences: it fixes where personal data lives and it is not cheap to change
after the first resume is stored.

Two things that follow from whichever region is chosen, worth checking before
committing to one:

- **Availability zones are region-specific names.** `aws ec2
  describe-availability-zones --region <region>` lists them.
- **Instance and node types are not offered everywhere.** `db.m7g.large` and
  `cache.t4g.small` are the production defaults; confirm both are available
  before the first apply, because the offline plan cannot.

`backend/tests/test_deploy_secret_hygiene.py::test_no_account_id_region_or_domain_is_hardcoded`
enforces the absence of a literal in executable Terraform. Comments and this
document may discuss a region; an argument may not name one.

---

## 3. Architecture

```
GitHub Actions  ->  Terraform  ->  AWS (region: var.region)

Traffic       Route53  -> ALB -> ECS      alias record, TLS 1.2+, 80 redirects
                                          to 443, WAF module built and disabled
Certificate   ACM                          DNS-validated, renews itself
Compute       ECS Fargate                  api / worker / beat / frontend / migrate
Database      RDS PostgreSQL 16            + pgvector, created by migration 0001
Object store  S3                           resumes, compliance docs, evidence
Cache/queue   ElastiCache Redis 7.1        Celery broker + working memory,
                                           noeviction, AUTH token, TLS
Registry      ECR                          immutable tags, SHA-named
Secrets       Secrets Manager              per-service IAM, enumerated
IaC           Terraform                    11 modules, 2 environment roots
```

Three network tiers, and the third is the one worth arguing for:

```
public    NAT gateways and the load balancer. Nothing else.
private   ECS tasks. Egress through NAT, no inbound from the internet.
data      RDS and ElastiCache. NO ROUTE TO THE INTERNET IN EITHER DIRECTION,
          not even outbound through NAT.
```

An attacker does not need to reach the database from the internet; they need the
database's host to reach them. Removing the route removes the path.

---

## 4. First-time setup

Six steps. Each has a verification command and the output to expect. The first
four cannot be Terraform, because Terraform cannot bootstrap what it depends on.

### Step 1 — The state bucket and locking

Terraform cannot create the bucket that holds its own state.

```bash
REGION=<your region>
BUCKET=readypick-tfstate-<something globally unique>

aws s3api create-bucket --bucket "$BUCKET" \
  --region "$REGION" --create-bucket-configuration LocationConstraint="$REGION"
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

**Verify:**

```bash
aws s3api get-bucket-versioning --bucket "$BUCKET"
```

**Expect:** `{"Status": "Enabled"}`.

Versioning is not optional. A corrupted state file with no previous version is an
environment Terraform can no longer manage, and the recovery is a manual `import`
of every resource in it.

**No DynamoDB lock table is needed.** Terraform 1.9 supports S3-native locking
through `use_lockfile = true`, which the commented backend block in each
environment already uses. A DynamoDB table is the older mechanism and is one more
thing to create, pay for and forget to delete.

Then uncomment the `backend "s3"` block in each environment's `main.tf` and fill
in the bucket and region. It ships commented out deliberately: an uncommented
backend pointing at a bucket that does not exist makes `terraform init` fail for
everybody, including somebody who only wanted to run the offline plan.

**Verify:**

```bash
cd infra/environments/staging && terraform init
```

**Expect:** `Successfully configured the backend "s3"!`

### Step 2 — OIDC for GitHub Actions

No AWS access key is stored in this repository. A long-lived key is a key that
outlives whoever added it.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

Then four roles, and four rather than one on purpose:

| Repository secret | Trust condition | Permissions |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `ref:refs/heads/main` | ECR push, Terraform apply on **staging** |
| `AWS_PLAN_ROLE_ARN` | any ref | Read-only, for the PR plan |
| `AWS_PROD_PLAN_ROLE_ARN` | `ref:refs/heads/main` | **Read-only** against production |
| `AWS_PROD_DEPLOY_ROLE_ARN` | `environment:production` | Terraform apply on production |

The production plan role is read-only and the production deploy role trusts the
**environment**, not the branch. That combination is what makes the approval gate
the only path to a production write: the plan job physically cannot apply, and
the apply job cannot assume its role until the environment releases it.

**Verify** each role's trust policy actually constrains the repository:

```bash
aws iam get-role --role-name <role> \
  --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition'
```

**Expect:** a `StringLike` or `StringEquals` on
`token.actions.githubusercontent.com:sub` naming
`repo:<owner>/<repo>:ref:refs/heads/main` or `repo:<owner>/<repo>:environment:production`.
**A trust policy with no `sub` condition lets any GitHub repository on earth
assume the role.** That is the single most consequential thing to get right in
this step, and it produces no error when it is wrong.

### Step 3 — The production environment's required reviewer

Settings → Environments → `production` → Required reviewers.

**This is the step the pipeline checks rather than assumes.** An environment with
no required reviewer promotes instantly and silently while the workflow file
still *reads* as gated.

**Verify:**

```bash
GH_TOKEN=<token> ./scripts/verify-approval-gate.sh
```

**Expect:** the script exits 0 and reports the reviewer. If none is configured it
exits non-zero, and it runs on every push for that reason.

### Step 4 — Repository variables

```
AWS_REGION           the region chosen in §2
AWS_DEPLOY_ENABLED   LEAVE UNSET until you intend to deploy
```

`AWS_DEPLOY_ENABLED` is the first of the two independent stops on production.
While it is unset, every job that would touch AWS is skipped: the steps are
complete, correct and reviewable, and they do not run.

**Verify:**

```bash
gh variable list
```

**Expect:** `AWS_REGION` present, `AWS_DEPLOY_ENABLED` absent.

### Step 5 — Variable values

Create `infra/environments/staging/terraform.tfvars` (gitignored) from the
template in that directory's `README.md`, and the same for production.

**Verify:**

```bash
cd infra/environments/staging && terraform plan -var="image_tag=sha-placeholder"
```

**Expect:** a plan, not a prompt. Terraform asking for a variable interactively
means one is missing; the offline plan cannot catch that, because it supplies its
own.

### Step 6 — Secret values

Terraform creates the secret **containers**; it never writes a value. A value in
Terraform is a value in the state file, and the state file is JSON behind a
bucket policy rather than a vault.

```bash
aws secretsmanager put-secret-value \
  --secret-id readypick-staging/ANTHROPIC_API_KEY \
  --secret-string "$(cat ~/.readypick/anthropic.key)"
```

**Verify** every container has a value, without printing one:

```bash
for s in $(aws secretsmanager list-secrets \
    --query "SecretList[?starts_with(Name,'readypick-staging/')].Name" --output text); do
  printf '%-60s' "$s"
  aws secretsmanager describe-secret --secret-id "$s" \
    --query 'length(VersionIdsToStages)' --output text
done
```

**Expect:** `1` beside every name. A `0` is an empty container, and the service
that needs it will fail at container start with a message about the secret rather
than about the feature.

Three that are composed rather than pasted:

- **`DATABASE_URL`** comes from a **least-privileged application role**, never
  from the RDS master credential. The master exists to create that role and to
  run migrations, and only the `migrate` service's IAM policy goes near it.
- **`REDIS_URL`** must use `rediss://` **with the AUTH token**:
  `rediss://:<token>@<primary endpoint>:6379/0`. Read the token with
  `terraform output -raw redis_auth_token`; it is marked sensitive, so it is
  redacted in ordinary output. Note the two esses — transit encryption is on, and
  a client that connects with `redis://` hangs in a way that looks like a network
  problem rather than a scheme problem.
- **`FIREBASE_SERVICE_ACCOUNT_JSON`** is the whole JSON document. Only the `api`
  service can read it: a background task never authenticates a browser session.
- **`HUGGINGFACE_TOKEN`** is a READ token from an account that has accepted the
  conditions on `pyannote/speaker-diarization-3.1` AND `pyannote/segmentation-3.0`
  (`analysis-service/README.md` has the steps). Only the `analysis` service can
  read it. The same token is also a repository secret named `HUGGINGFACE_TOKEN`,
  because the image build needs it to fetch the gated weights; the build takes
  it as a Docker secret mount, never a build ARG. Without it the service starts
  and reports `diarization: unavailable` at `/health`, and the proctoring report
  states that audio monitoring was unavailable rather than that nothing was heard.

### Step 7 — Account-level logging (optional, and named rather than implied)

`infra/.checkov.yml` skips `CKV_AWS_18` (S3 server access logging) with the
reasoning that the question actually being asked is *"who read which resume"*,
and that S3 server access logging is best-effort and hours-delayed. CloudTrail S3
data events answer it properly and are attributable to an IAM principal.

They are an account-level configuration, not a bucket one, so this Terraform does
not create them. If that audit trail is wanted, it is a deliberate step here:

```bash
aws cloudtrail put-event-selectors --trail-name <trail> \
  --advanced-event-selectors file://data-events.json
```

This is listed so the gap is visible rather than silently skipped.

---

## 5. Deploying

### Staging

```bash
./infra/validate.sh                             # offline, always safe
./infra/plan-offline.sh                         # offline, always safe
python infra/check-no-wildcard-iam.py           # offline, always safe

cd infra/environments/staging
terraform init
terraform plan  -var="image_tag=sha-$(git rev-parse --short=12 HEAD)"
terraform apply -var="image_tag=sha-$(git rev-parse --short=12 HEAD)"
```

**Expect** the certificate step to take several minutes: `aws_acm_certificate_validation`
blocks until ACM has seen the DNS records and moved the certificate to ISSUED. If
it runs past ten minutes the cause is almost always that `hosted_zone_id` is not
the zone the registrar delegates to, and waiting longer does not fix it.

**Verify DNS delegation before blaming the certificate:**

```bash
dig +short NS <your domain>
aws route53 get-hosted-zone --id <hosted_zone_id> --query 'DelegationSet.NameServers'
```

**Expect:** the two lists to match. If they do not, the zone Terraform is writing
into is not the zone the internet asks.

Then, in this order:

```bash
cd ../../..
./scripts/run-migration.sh staging
./scripts/smoke-test.sh staging
EXPECTED_BACKEND_DIGEST=sha256:... \
EXPECTED_FRONTEND_DIGEST=sha256:... \
  ./scripts/verify-deployment.sh staging
```

**`aws ecs run-task` returning is not the migration finishing.**
`run-migration.sh` polls for `STOPPED` and reads the exit code. A job that was
accepted and then died is what a pipeline reports as success, and this platform
has had that exact failure.

**Run the last two, in that order, and do not skip the second.** They answer
different questions:

- `smoke-test.sh` asks whether the service answers HTTP. On 2026-08-04 every
  deploy was green, every smoke test passed, and three reported features did not
  work.
- `verify-deployment.sh` asks whether the bytes serving traffic are the bytes
  that were tested. It reads the **running tasks'** image digests, not the
  service definition. The gap between those two is a circuit-breaker rollback,
  which is precisely the case the service definition reports as success.

**Verify the traffic layer specifically**, since it is new:

```bash
# 1. HTTP redirects and never serves.
curl -sSI http://<domain>/ | head -2
#    Expect: HTTP/1.1 301 Moved Permanently  /  location: https://<domain>:443/

# 2. TLS floor. 1.1 must fail; 1.2 must succeed.
curl -sS --tlsv1.1 --tls-max 1.1 https://<domain>/ -o /dev/null
#    Expect: a handshake failure, non-zero exit
curl -sS --tlsv1.2 https://<domain>/ -o /dev/null && echo "TLS 1.2 ok"

# 3. The public job path needs no session.
curl -sS -o /dev/null -w '%{http_code}\n' https://<domain>/api/v1/jobs/public/<published job id>
#    Expect: 200, with no cookie sent

# 4. Every other API path does.
curl -sS -o /dev/null -w '%{http_code}\n' https://<domain>/api/v1/jobs
#    Expect: 401

# 5. An UNPUBLISHED job id is 404, not 403.
curl -sS -o /dev/null -w '%{http_code}\n' https://<domain>/api/v1/jobs/public/<draft job id>
#    Expect: 404. RBAC §33: knowing the id must not be sufficient, and the
#    response must not reveal that the job exists.

# 6. The health check is deep, not a static 200.
curl -sS https://<domain>/health
#    Expect: {"status":"ok","database":"ok","cache":"ok"}
```

Step 5 is the one worth doing deliberately. It is the difference between "the
public path works" and "the public path is safe", and only the second one is a
property.

### Production

Production is deployed by the pipeline, behind the approval gate, and only once
`AWS_DEPLOY_ENABLED` is set. **Not in this phase.**

---

## 6. Enabling the WAF

`modules/waf` is complete and `enabled = false` in both environments, which is
what spec-doc6 §13.2 asks for: *"Build it; leave it disabled by variable so
enabling is a one-line decision."*

**Do not enable it in blocking mode first.** The managed rule sets inspect request
bodies, and this product's request bodies are resumes, client-written job
descriptions and interview answers. A resume containing an XML sample, a JD
containing a SQL snippet, a candidate describing an XSS finding in a security
interview: each is an ordinary document in this domain and each trips a rule
written for a form field.

The order:

1. `enabled = true`, `count_only = true` in **staging**. Nothing is blocked.
2. Run a week of real traffic, resume uploads included.
3. Read the sampled requests:

   ```bash
   aws wafv2 get-sampled-requests --web-acl-arn <arn> --rule-metric-name <rule> \
     --scope REGIONAL --time-window StartTime=<t0>,EndTime=<t1> --max-items 500
   ```

   **Expect:** any match on `SizeRestrictions_BODY`, `CrossSiteScripting_BODY` or
   `GenericRFI_BODY` to be a false positive. Those three are already excluded by
   default in `var.managed_rule_groups`; a match on anything else is what needs
   reading.
4. Only then `count_only = false`, staging first.
5. Repeat in production. Do not promote the decision, promote the evidence.

`terraform output waf_enforcing` reports whether a web ACL exists **and** is
refusing anything. It is deliberately separate from whether one is attached: a
count-only WAF on a compliance checklist reads as a control and blocks nothing.

---

## 7. Rollback

```bash
terraform apply -var="image_tag=sha-<the previous commit>"
```

ECR tags are **immutable**, so a SHA tag is a permanent name for a specific set of
bytes and this rolls back to something that definitely still exists. The lifecycle
policy keeps tagged images by **count**, never by age, for the same reason: an age
rule deletes the image a long-running service needs to restart from, and the
failure surfaces at 3am on the image that had been working for months.

**A schema migration does not roll back with the image.** Every migration in this
repository is written to be additive under a rolling deploy; migration `0058` is
the documented exception and says so in its own docstring.

**The load balancer does not roll back either.** A DNS alias points at the ALB, so
destroying and re-creating it means an outage lasting however long a replacement
takes plus however long resolvers cache the old answer. Production sets
`enable_deletion_protection = true` for exactly this.

---

## 8. Cost, honestly

Rough monthly figures before data transfer. They move with the region.

| | Staging | Production |
|---|---|---|
| ECS Fargate | ~$45 | ~$260 |
| RDS | ~$30 (t4g.small) | ~$250 (m7g.large Multi-AZ) |
| ElastiCache | ~$15 | ~$60 (with replica) |
| NAT | ~$32 (one) | ~$64 (per AZ) |
| ALB | ~$18 | ~$25 |
| VPC flow logs | ~$5 | ~$20 |
| S3, ECR, secrets, logs | ~$25 | ~$45 |
| WAF (only if enabled) | ~$0 | ~$15 |
| **Total** | **~$170** | **~$740** |

**Fargate does not scale to zero**, which is the one place it is not equivalent to
Cloud Run. There is a floor cost the previous platform did not have, and it is
stated here rather than discovered on a bill.

Two of these are new in this phase and are real: the **ALB** is the price of
serving traffic at all, and **VPC flow logs** are the price of being able to
answer "what actually reached what" after an incident. Flow logs are the highest
volume log this platform produces; retention is 30 days in staging and 90 in
production for that reason.

---

## 9. What was removed, and what was carried forward

Removed with the Cloud Run infrastructure: `infra/gcp/deploy.sh`,
`scripts/deploy.sh`, `scripts/promote.sh`, `scripts/setup-wif-once.sh`,
`scripts/complete-cicd-setup.sh`, `docs/DEPLOY_GCP.md`,
`docs/DEPLOY_GCP_RUNBOOK.md`.

**Two things were carried forward rather than deleted**, because the verification
discipline is worth more than the scripts were:

- digest verification → `scripts/verify-deployment.sh`
- the deploy-script secret-hygiene assertions →
  `backend/tests/test_deploy_secret_hygiene.py`, rewritten against the Terraform
  and the workflow. When the GCP script went, six of those assertions started
  reporting SKIPPED — which in a summary line reads almost like PASSED, and meant
  nothing was enforcing secret hygiene any more. They are now stronger than they
  were: the check is no longer "the deploy script does not print the DSN" but
  "the worker's IAM policy does not include the Firebase key at all".

They were extended again in this phase to cover the traffic layer: the access-log
bucket's privacy, the enumerated unauthenticated surface at both the listener and
the application, the absence of an imported private key in ACM, the absence of a
created or name-resolved hosted zone, and the WAF's log redaction.
