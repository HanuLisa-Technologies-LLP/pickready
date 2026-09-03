# staging

Every value this environment needs that nobody has supplied yet, with the command
that produces it.

> **NOTHING HERE HAS BEEN APPLIED.** spec-doc6 §D5: no `terraform apply`, no live
> resource creation, no DNS cutover. Running an apply against a real account in
> this phase is a failure of scope, not an accomplishment.

## What staging is for, and therefore what is smaller

Staging exists to prove a deploy works, not to survive an availability-zone
failure. One NAT gateway, no RDS Multi-AZ, no Redis replica, one task per
service, seven-day backups, no ALB deletion protection, 30-day flow logs. Each is
a cost decision, each is stated in `main.tf` rather than defaulted, and the
production file's differences are readable as a diff.

`production/main.tf` is DERIVED from this file by `../derive-production.py`, so
the two roots cannot silently diverge in shape, only in the values that are meant
to differ. Edit staging and re-run the script; do not hand-edit production.

## Required variables

None of these has a default. That is the deliverable spec-doc6 §D5 describes:
*"The codebase must be complete except for those values."* A plan or an apply
fails until each is supplied, rather than proceeding on a guess.

Copy this into `terraform.tfvars` (which `infra/.gitignore` keeps out of the
repository) and fill it in.

```hcl
account_id         = ""          # 12 digits
region             = ""          # e.g. ap-south-1
availability_zones = ["", ""]    # at least two, inside `region`

domain_name    = ""              # e.g. staging.example.com
hosted_zone_id = ""              # the EXISTING zone id, begins with Z

storage_bucket_name     = ""     # globally unique
access_logs_bucket_name = ""     # globally unique, and NOT the same bucket
```

### `account_id`

```bash
aws sts get-caller-identity --query Account --output text
```

Not discovered with `data "aws_caller_identity"`, for two reasons: §D5 makes it a
declared variable, and that data source is a live STS call the offline plan
cannot make. It is used in the access-log bucket policy's `aws:SourceAccount`
condition and in the KMS key policy, so a wrong value fails closed rather than
opening anything.

### `region`

**The owner's decision.** `docs/operations/DEPLOY_AWS.md` §2 records why `ap-south-1` is the
likely answer for an India-billed tenant with Indian data-residency expectations,
and why it is no longer written into the code as an assumption.

Before committing to one, confirm the instance types exist there:

```bash
aws ec2 describe-regions --query 'Regions[].RegionName' --output text
```

### `availability_zones`

```bash
aws ec2 describe-availability-zones --region "$REGION" \
  --query 'AvailabilityZones[?State==`available`].ZoneName' --output text
```

At least two. This is a floor rather than a resilience preference: the RDS subnet
group requires a span of two AZs even for a single-AZ instance.

### `domain_name`

The hostname this environment is served on. No default, because §D5 says no
domain name is available in this phase and one must not be invented. It becomes
the ACM certificate's subject, the Route53 alias record, and `FRONTEND_URL` on
every task — which is what `jobs.public_job_url` builds a candidate's application
link from and what the CORS allowlist is keyed on.

### `hosted_zone_id`

```bash
aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" \
  --query 'HostedZones[0].Id' --output text
```

**An id, never a name lookup, and never a created zone.** A Route53 hosted zone
gets four assigned name servers at creation, different from the four the
registrar already delegates to, so creating a second zone for a domain that
already has one produces a zone full of correct records that nothing on the
internet resolves — while the real zone keeps serving the old answers. Everything
looks applied and nothing changed.

Confirm the zone is the one the registrar actually delegates to:

```bash
dig +short NS "$DOMAIN"
aws route53 get-hosted-zone --id "$ZONE" --query 'DelegationSet.NameServers'
```

The two lists must match.

### `storage_bucket_name` and `access_logs_bucket_name`

S3 bucket names are global across every AWS account on earth, so a derived name
is one that may already belong to somebody else and the collision arrives at
apply time as a bare `BucketAlreadyExists`.

**They must be two different buckets.** The storage bucket is encrypted with this
environment's customer-managed KMS key because it holds resumes. The Elastic Load
Balancing log delivery service cannot write to a CMK-encrypted bucket and fails
SILENTLY when it cannot — logging stops and the load balancer stays healthy — so
the access-log bucket is SSE-S3. Merging them would either break logging or
downgrade the encryption on the resumes.

## Running it

```bash
# Offline, always safe, needs no credentials at all:
./infra/validate.sh
./infra/plan-offline.sh
python infra/check-no-wildcard-iam.py

# Against a real account, once the values above exist:
terraform init
terraform plan -var="image_tag=sha-$(git rev-parse --short=12 HEAD)"
```

`terraform plan` asking for a variable interactively means one is missing. The
offline plan cannot catch that, because it supplies its own from
`../offline-plan.tfvars` — every value in which is deliberately impossible.

The full ordered runbook, with a verification command and an expected output for
each step, is `docs/operations/DEPLOY_AWS.md`.
