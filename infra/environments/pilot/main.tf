/**
 * ReadyPick PILOT, in ap-south-2.
 *
 * The first environment of the Lambda-plus-on-demand-ECS architecture, and the
 * first one this repository has ever actually applied. Staging and production
 * next door describe the Celery topology this replaced; they are kept because
 * they are the record of what was deployed before, and they are NOT the shape
 * to copy. Copy this one.
 *
 * WHAT IS DIFFERENT FROM THE ENVIRONMENTS BESIDE IT
 * --------------------------------------------------
 * There is no `worker` service and no `beat` service. The work they did is now:
 *
 *   short work        `readypick-task-worker`, a Lambda. Delivery, resume
 *                     parsing, the reconciliation sweeps. Billed per
 *                     invocation, so an idle platform costs nothing for it.
 *   long work         `readypick-agent`, an ECS task definition with NO
 *                     SERVICE. A Lambda calls RunTask when work arrives; the
 *                     container does one piece of work and exits. Fargate does
 *                     not scale to zero, which is exactly why this is a task
 *                     definition and not a service.
 *   the schedule      EventBridge Scheduler rules, one per sweep, invoking the
 *                     task worker with the payload a dispatch would have sent.
 *                     No singleton process to lose.
 *
 * WHY THIS IS NOT A NEW `terraform/` TREE
 * ----------------------------------------
 * The infrastructure brief asks for `terraform/` at the repository root. This
 * repository already had a complete Terraform tree under `infra/`, with eleven
 * modules, an offline planning profile, a wildcard-IAM checker and CI wiring,
 * all of which this environment reuses. A second tree would be two answers to
 * "where is the infrastructure", and the older one is the one CI reads.
 * Recorded in DEPLOYMENT_LOG.md.
 *
 * WHAT AN APPLY OF THIS FILE DOES NOT GIVE YOU
 * ---------------------------------------------
 * Secrets are created EMPTY. Every model credential, the JWT signing key and
 * the Hugging Face token are containers with no value until a human runs
 * `aws secretsmanager put-secret-value`. A service started against an empty
 * secret starts and fails on first use, so the deploy order in
 * DEPLOYMENT_LOG.md puts the secrets before the services deliberately.
 */

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }

  # The backend lives in `backend.tf`, on its own, and that separation is
  # load-bearing rather than tidy: `terraform plan` refuses to run against an
  # uninitialised backend, and the offline planning profile has no credentials
  # and no network to initialise an S3 one with. `infra/plan-offline.sh` copies
  # this directory WITHOUT that one file, which is how this environment can have
  # both real remote state and a plan CI can run with no AWS account at all.
}

provider "aws" {
  region = var.region

  # See staging's file for what these four are. FALSE for a real apply: with
  # them skipped a misconfigured profile fails later and far less clearly.
  skip_credentials_validation = var.planning_profile
  skip_requesting_account_id  = var.planning_profile
  skip_region_validation      = var.planning_profile
  skip_metadata_api_check     = var.planning_profile

  default_tags {
    tags = local.tags
  }
}

# A SECOND REGION, FOR ONE SERVICE. Amazon Transcribe has no endpoint in
# ap-south-2 at all -- not "unavailable to this account", the DNS name does not
# resolve -- so a deployment here must call it in ap-south-1. A Transcribe job
# is region-local over S3, reading and writing a bucket in its own region, so
# the small working bucket below comes with it. Nothing else in this
# environment uses this alias, and nothing else should.
provider "aws" {
  alias  = "transcribe"
  region = var.transcribe_region

  skip_credentials_validation = var.planning_profile
  skip_requesting_account_id  = var.planning_profile
  skip_region_validation      = var.planning_profile
  skip_metadata_api_check     = var.planning_profile

  default_tags {
    tags = local.tags
  }
}

locals {
  environment = "pilot"

  # The role that OWNS every database object. Not the RDS master: the master's
  # password is rotated by Secrets Manager on a seven-day schedule, and on
  # 2026-09-11 that rotation took the whole product down because `DATABASE_URL`
  # held a copy of it. Ownership was moved here so the master is needed exactly
  # once, and the application's own credential is one ReadyPick rotates.
  #
  # A LITERAL, and it has to be: Terraform does not create this role. SQL does
  # (`app.scripts.provision_app_db_role`, through
  # `scripts/rotate-app-db-credential.sh`), for the same reason `CREATE
  # EXTENSION vector` lives in migration 0001 rather than in the rds module --
  # a Postgres provider holding the master credential in state is worse than
  # the duplication of a name.
  db_owner_role = "readypick_owner"

  # The frontend's tag, falling back to the shared one. See the variable's own
  # description for why the two can differ and what an apply that ignores it
  # does to the running service.
  frontend_image_tag = var.frontend_image_tag != "" ? var.frontend_image_tag : var.image_tag
  # Same fallback, same reason. See the variable's description for why the
  # analysis image so often lags the other two.
  analysis_image_tag = var.analysis_image_tag != "" ? var.analysis_image_tag : var.image_tag

  # The internal service namespace, owned here and nowhere else. The analysis
  # service sits behind no load balancer, so a Cloud Map name is the only way
  # anything addresses it. Both sides need the same string, so it is built once.
  internal_namespace   = "${var.project}.local"
  analysis_service_url = "http://analysis.${local.internal_namespace}:8100"

  # WHETHER THIS ENVIRONMENT HAS A PUBLIC ENTRY POINT AT ALL.
  #
  # Derived from a VARIABLE, so it is known at plan time and the `count`s below
  # plan cleanly. That is the difference between this and a count keyed on an
  # ARN, which does not exist until apply and cannot be planned.
  has_domain = var.domain_name != null && var.domain_name != ""

  # WHETHER ANYTHING CAN REACH THIS ENVIRONMENT AT ALL.
  #
  # Not the same question as `has_domain`. The load balancer needs a
  # CERTIFICATE; ACM needs a domain to ISSUE one, but it will hold one you
  # IMPORT for any name. A pilot with no domain is therefore reachable over
  # https on the load balancer's own AWS hostname, behind a self-signed
  # certificate whose subject alternative name matches it. One warning, about
  # the issuer, which a visitor clicks through.
  #
  # There is deliberately no plaintext alternative: the application sets Secure
  # cookies and uvicorn runs with `--proxy-headers`, so over plain http every
  # auth cookie is refused and an http-only environment is one nobody can sign
  # in to. A visible warning beats a silent login failure.
  has_public_entry = local.has_domain || (
    var.fallback_certificate_arn != null && var.fallback_certificate_arn != ""
  )

  # The origin the product believes it is served on. `jobs.public_job_url`
  # builds the candidate-facing application link from it and `app/main.py` keys
  # its CORS allowlist on it.
  #
  # With a domain it is the domain. Without one but with a certificate it is
  # the load balancer's own name, which is a real, working address. With
  # neither there is no public origin at all, and this says so in the one way
  # that cannot be mistaken for a working address: `.invalid` is reserved by
  # RFC 2606 precisely so it never resolves, so a job link built from it is
  # obviously broken rather than subtly wrong.
  frontend_url = (
    local.has_domain ? "https://${var.domain_name}" :
    local.has_public_entry ? "https://${module.alb[0].dns_name}" :
    "https://${var.project}.invalid"
  )

  # ── Receiving a reply ──────────────────────────────────────────────────────
  #
  # A SUBDOMAIN of the product's own zone, because receiving mail means owning
  # the MX record and the apex's MX belongs to whatever mailbox the company
  # actually reads. Empty without a domain, and that is a real state: the
  # deployment still SENDS verification requests, and the employer's reply
  # arrives in the sending mailbox instead of in the thread. The application
  # records that rather than hiding it (`conversations.reply_address`).
  reply_domain = local.has_domain ? "reply.${var.domain_name}" : ""
  has_inbound  = local.reply_domain != ""

  # Composed from the BUCKET NAME rather than read from the module's output,
  # because `ses_inbound` subscribes the function and therefore depends on it;
  # taking the ARN from that module would be a cycle. The name is deterministic
  # and both sides are given the same string.
  inbound_mail_objects = "arn:aws:s3:::${var.project}-${local.environment}-inbound-mail/inbound/*"

  # WITHOUT INGRESS, ONE TASK PER SERVICE.
  #
  # Two once traffic can arrive, one while it cannot. A second task buys
  # redundancy for requests that have no way in, and one is enough to prove
  # what an unreachable stage is for: that the image boots, resolves its
  # secrets, and reaches RDS and Redis from a private subnet.
  # PILOT RUNS LEAN, EXPLICITLY (2026-09-11 cost decision). One task per
  # service at rest, because this environment holds three demo tenants and
  # zero candidates and was paying for warm redundancy nobody consumes: the
  # analysis service alone (2 vCPU / 8 GB each) idled at two tasks for a
  # proctoring feature no assessment has ever exercised here. Autoscaling
  # CEILINGS are unchanged or higher, so behaviour under real load is
  # preserved: target tracking (CPU 65) grows each service toward its
  # max_count and shrinks it back, and rolling deploys still start the new
  # task before draining the old, so a deploy is not an outage. What IS
  # accepted is that an AZ failure briefly downs the demo site; production
  # keeps its own sizing and this block does not touch it.
  service_count = 1

  tags = {
    Project     = var.project
    Environment = local.environment
    ManagedBy   = "terraform"
    Repository  = "readypick"
  }
}

# ── KMS ──────────────────────────────────────────────────────────────────────
#
# ONE CUSTOMER-MANAGED KEY, used by S3, RDS, ElastiCache, Secrets Manager, SNS
# and the log groups. One key rather than six because the question it answers,
# "who can decrypt this environment's data", has one answer, and six keys would
# be six key policies to keep in step.
#
# The policy is written out rather than defaulted, for the reason staging's file
# sets out at length: an omitted policy makes a customer-managed key behave like
# an AWS-managed one, which is the thing choosing customer-managed was avoiding.

data "aws_iam_policy_document" "kms" {
  statement {
    sid    = "AccountRootAdministersTheKey"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"] # A key policy's resource IS the key it is attached to.
  }

  statement {
    sid    = "ServicesThatEncryptThisEnvironmentsDataAtRest"
    effect = "Allow"
    principals {
      type = "Service"
      identifiers = [
        "s3.amazonaws.com",
        "rds.amazonaws.com",
        "elasticache.amazonaws.com",
        "secretsmanager.amazonaws.com",
        "sns.amazonaws.com",
        # SES ENCRYPTS THE EVENT PAYLOAD ITSELF before handing it to SNS, so
        # publishing to an encrypted topic needs the SES principal on the KEY,
        # not just on the topic. Without it CreateConfigurationSetEventDestination
        # fails outright: "Access denied to KMS key for SNS topic".
        #
        # This is also why the topic uses this environment's own CMK rather
        # than `alias/aws/sns`: an AWS-managed key has a fixed policy that
        # cannot be granted to SES at all, so encryption would have had to be
        # dropped to make delivery tracking work.
        "ses.amazonaws.com",
        "logs.${var.region}.amazonaws.com",
      ]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:CreateGrant",
      "kms:DescribeKey",
    ]
    resources = ["*"]

    # Scoped to this account. A service principal with no account condition is
    # the confused-deputy shape: it reads as narrow because it names an AWS
    # service, and it is reachable from any account using that service.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "ReadyPick ${local.environment}"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.kms.json

  tags = { Name = "${var.project}-${local.environment}" }
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.project}-${local.environment}"
  target_key_id = aws_kms_key.this.key_id
}

# ── Where an alarm goes ──────────────────────────────────────────────────────
#
# The topic lives HERE rather than inside `observability`, for the same reason
# the KMS key does: two modules use it. The `lambda` module publishes a
# permanently failed asynchronous invocation to it, and `observability` alarms
# to it while depending on that module's function names. Owning it in either
# one would be a dependency cycle.

data "aws_iam_policy_document" "alarm_topic" {
  statement {
    sid     = "AllowCloudWatchAlarms"
    effect  = "Allow"
    actions = ["SNS:Publish"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    resources = [aws_sns_topic.alarms.arn]
    # Without an account condition a named service principal is the
    # confused-deputy shape: it reads as narrow and is reachable from any
    # account using that service.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.account_id]
    }
  }

  statement {
    sid     = "AllowLambdaFailureDestinations"
    effect  = "Allow"
    actions = ["SNS:Publish"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    resources = [aws_sns_topic.alarms.arn]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic" "alarms" {
  name              = "${var.project}-${local.environment}-alarms"
  kms_master_key_id = aws_kms_key.this.arn
  tags              = local.tags
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarm_topic.json
}

# AN EMAIL SUBSCRIPTION IS PENDING UNTIL ITS RECIPIENT CLICKS THE CONFIRMATION
# LINK, and Terraform reports it as created either way. A plan that says
# "1 to add" is not evidence that anybody is being notified, which is exactly
# the kind of thing this codebase has been burned by before. Confirm it in the
# inbox, then check the subscription in the console.
resource "aws_sns_topic_subscription" "alarm_email" {
  for_each = toset(var.alarm_emails)

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.value
}

# ── Network ──────────────────────────────────────────────────────────────────

module "network" {
  source = "../../modules/network"

  project            = var.project
  environment        = local.environment
  region             = var.region
  cidr_block         = "10.0.0.0/16"
  availability_zones = var.availability_zones

  # Flow logs: the only record of what actually reached what. Every other
  # control in the network module is preventive and leaves no trace of having
  # refused anything.
  kms_key_arn             = aws_kms_key.this.arn
  flow_log_retention_days = 30

  # ONE NAT, a locked decision. A per-AZ pair buys resilience this pilot is not
  # buying yet, at roughly $32 a month each.
  single_nat_gateway = true

  tags = local.tags
}

# ── Registries ───────────────────────────────────────────────────────────────

module "ecr" {
  source = "../../modules/ecr"

  project     = var.project
  environment = local.environment
  kms_key_arn = aws_kms_key.this.arn

  # RETAINED BY COUNT, NEVER BY AGE. An age rule deletes the image a
  # long-running service needs in order to restart.
  keep_images = 20

  tags = local.tags
}

# ── Secrets ──────────────────────────────────────────────────────────────────
#
# Containers only. See the file docstring: every value is a human's to put in.

module "secrets" {
  source = "../../modules/secrets"

  project     = var.project
  environment = local.environment
  region      = var.region
  kms_key_id  = aws_kms_key.this.key_id
  kms_key_arn = aws_kms_key.this.arn

  tags = local.tags
}

# ── Data ─────────────────────────────────────────────────────────────────────

module "rds" {
  source = "../../modules/rds"

  project           = var.project
  environment       = local.environment
  subnet_ids        = module.network.data_subnet_ids
  security_group_id = module.network.rds_security_group_id

  # t4g.medium, up from t4g.micro (2026-09-10). Two reasons, and neither is
  # raw connection count. 1GB of RAM is not comfortable working memory for
  # pgvector HNSW index operations once real candidates land, and RDS derives
  # its default max_connections from instance memory, so the bump also lifts
  # the connection ceiling roughly fourfold against Lambda concurrency spikes
  # (worker_session builds a fresh engine per invocation by design, so Lambda
  # concurrency, not the app pool size, is what actually drives peak
  # connections here).
  #
  # AN RDS PROXY WAS EVALUATED AND DELIBERATELY NOT BUILT (owner decision,
  # 2026-09-10). The AWS pinning documentation settles it: for PostgreSQL the
  # proxy pins a session on any SET command, on set_config(), and on named
  # prepared statements. This application issues SET LOCAL ROLE inside every
  # tenant-scoped transaction (core/db.tenant_scope), a session-level
  # set_config on every worker connection (workers/runtime.worker_session),
  # and asyncpg caches prepared statements by default, so effectively every
  # session would pin immediately: no multiplexing, a held backend connection
  # per client for its whole lifetime, and the one benefit left is queuing
  # connection storms. Revisit only if CloudWatch DatabaseConnections ever
  # approaches the instance ceiling; the fix that makes a proxy worthwhile is
  # an application change (transaction-scoped worker bypass, statement cache
  # off), not a Terraform change.
  #
  # `db_pool_size=12, db_max_overflow=3` in the app were sized against the
  # micro instance's ceiling and are deliberately untouched by this bump: they
  # bind the long-lived ECS api service, which was never the pressure point.
  # Raise them only against a measured need.
  instance_class = "db.t4g.medium"
  # 50 GB growing to 200 (raised from 100 on 2026-09-10). Chunk embeddings are
  # 1024 floats each with an HNSW index on top, many chunks per document, and
  # they grow faster than relational rows; the ceiling gets room before this
  # needs revisiting. The STARTING allocation stays 50: gp3's baseline IOPS is
  # a function of size, so the floor is a performance floor as well as a
  # capacity one, and pre-paying for unused space buys neither.
  allocated_storage     = 50
  max_allocated_storage = 200
  # MUST FLIP TO true BEFORE ANY REAL (NON-DEMO) TENANT'S DATA LIVES HERE.
  # Today this environment holds three demo tenants and zero candidates, and
  # Multi-AZ roughly doubles the RDS bill for redundancy protecting data that
  # does not yet exist. The day a real customer is onboarded to pilot, this
  # line is part of that onboarding. Production already runs multi_az = true.
  multi_az              = false
  backup_retention_days = 7

  kms_key_id  = aws_kms_key.this.key_id
  kms_key_arn = aws_kms_key.this.arn

  tags = local.tags
}

module "elasticache" {
  source = "../../modules/elasticache"

  project           = var.project
  environment       = local.environment
  subnet_ids        = module.network.data_subnet_ids
  security_group_id = module.network.redis_security_group_id

  node_type = "cache.t4g.micro"
  # A single node. Redis is no longer the message broker, so a failure here is
  # not lost work: it is a rate limiter that fails open, a cache that misses,
  # and a proctoring warning counter that answers 503 rather than silently not
  # warning. The health check probes it, so a task that loses Redis leaves the
  # target group instead of serving assessments it cannot monitor.
  replica_count = 0

  kms_key_arn = aws_kms_key.this.arn

  tags = local.tags
}

module "s3" {
  source = "../../modules/s3"

  project     = var.project
  environment = local.environment
  region      = var.region
  kms_key_arn = aws_kms_key.this.arn

  # NAMED, NOT DERIVED. S3 bucket names are global across every AWS account, so
  # a derived name is a name that may already belong to somebody else.
  bucket_name = var.storage_bucket_name

  noncurrent_retain_days = 30

  tags = local.tags
}

# The account id, for the ARNs in this environment that have to be written out
# by hand: a Transcribe job's ARN is not an attribute of any resource here,
# because the jobs are created by the application at run time.
#
# `var.account_id` AND NOT `data "aws_caller_identity"`. The data source calls
# STS, which the OFFLINE PLAN cannot do: it runs against account 000000000000
# in a region that does not exist and has never contacted AWS, so the lookup
# fails DNS resolution and takes the whole plan with it. Pilot was the one
# environment that could not be planned offline for exactly this reason, and a
# pre-apply check that cannot run is a check nobody reads. The variable is the
# same account and is already required.

# ── Speech to text, in the one region that has it ────────────────────────────
#
# A WORKING BUCKET, not a store. `run_transcription` copies the extracted audio
# in, runs the job, copies the transcript back to the product's own bucket and
# deletes both objects. The expiry rule below is the backstop for a delete that
# did not happen, not the mechanism.
#
# SSE-S3 rather than the environment's KMS key, and that is forced rather than
# chosen: the key is regional and lives in `var.region`, so an object encrypted
# with it cannot be written here.

resource "aws_s3_bucket" "transcribe" {
  count    = var.transcribe_enabled ? 1 : 0
  provider = aws.transcribe

  # NAMED, NOT DERIVED, the same rule the storage bucket follows: S3 names are
  # global across every AWS account, so a derived name is one that may already
  # belong to somebody else.
  bucket = var.transcribe_bucket_name
}

resource "aws_s3_bucket_public_access_block" "transcribe" {
  count    = var.transcribe_enabled ? 1 : 0
  provider = aws.transcribe

  bucket                  = aws_s3_bucket.transcribe[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "transcribe" {
  count    = var.transcribe_enabled ? 1 : 0
  provider = aws.transcribe

  bucket = aws_s3_bucket.transcribe[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "transcribe" {
  count    = var.transcribe_enabled ? 1 : 0
  provider = aws.transcribe

  bucket = aws_s3_bucket.transcribe[0].id

  rule {
    id     = "expire-working-objects"
    status = "Enabled"
    filter {}
    # ONE DAY. The pipeline deletes its own objects; anything still here
    # outlived a job that failed between the copy in and the delete, and a
    # candidate's assessment audio must not sit in a second region waiting for
    # somebody to notice.
    expiration {
      days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# The agent task is the only caller: video processing is Route.ECS. Scoped to
# this product's own job names and to the working bucket, never `*`.
data "aws_iam_policy_document" "transcribe" {
  count = var.transcribe_enabled ? 1 : 0

  statement {
    sid = "RunTranscriptionJobs"
    actions = [
      "transcribe:StartTranscriptionJob",
      "transcribe:GetTranscriptionJob",
    ]
    resources = [
      "arn:aws:transcribe:${var.transcribe_region}:${var.account_id}:transcription-job/readypick-*",
    ]
  }

  statement {
    sid = "WorkingObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.transcribe[0].arn}/*"]
  }
}

resource "aws_iam_role_policy" "agent_transcribe" {
  count = var.transcribe_enabled ? 1 : 0

  name   = "${var.project}-${local.environment}-transcribe"
  role   = element(split("/", module.ecs.task_role_arns["agent"]), 1)
  policy = data.aws_iam_policy_document.transcribe[0].json
}

# ── Outbound mail ────────────────────────────────────────────────────────────
#
# `ses:SendRawEmail` and nothing else. The transport builds the MIME message
# itself (attachments, Reply-To), so the simple send action would not serve it,
# and no read, no identity management and no configuration-set write is reach
# the delivery path needs.
#
# The task worker is where mail actually leaves the platform, because delivery
# is Route.LAMBDA. The API holds the same grant for the one send that is not
# dispatched: the corporate-sender ownership code, which a person is waiting on
# in the browser.
data "aws_iam_policy_document" "ses_send" {
  statement {
    sid       = "SendRawEmail"
    actions   = ["ses:SendRawEmail"]
    resources = ["*"]
  }

  # READ-ONLY IDENTITY LOOKUP, which is what replaced the sender OTP. A
  # corporate sender is eligible when the ACCOUNT holds a verified SES identity
  # covering it -- the address itself, or its domain. Asking SES is the honest
  # check; a mailbox round trip of our own only proved the same thing twice.
  # Nothing here creates or deletes an identity from application code.
  statement {
    sid = "ReadSendingIdentities"
    actions = [
      "ses:GetIdentityVerificationAttributes",
      "ses:ListIdentities",
    ]
    resources = ["*"]
  }
}

# ── SES delivery events: one configuration set, one topic, one subscription ──
#
# SHARED, NOT PER TENANT. A topic per company multiplies an AWS resource by a
# number the product grows without bound, and every one would carry the same
# policy and the same single subscriber. Correlation is DATA instead: each send
# is tagged with the tenant, sender and candidate it belongs to, and the webhook
# resolves the row from the SES message id it already stores.
#
# Standard, never FIFO: SES refuses a FIFO topic as an event destination.

resource "aws_sns_topic" "ses_events" {
  name = "${var.project}-${local.environment}-ses-events"
  # THIS ENVIRONMENT'S OWN CMK, not `alias/aws/sns`. An AWS-managed key has a
  # fixed policy that cannot be granted to the SES service principal, and SES
  # encrypts the event payload itself before publishing -- so the managed key
  # makes CreateConfigurationSetEventDestination fail outright. The choice is
  # the CMK or no encryption at all, and a bounce event names the candidate's
  # address, so it is the CMK. The matching grant is in the key policy above.
  kms_master_key_id = aws_kms_key.this.arn
}

# SES publishes as a SERVICE PRINCIPAL, so the grant lives on the topic rather
# than on any role this platform holds. Conditioned on our own account, or SES
# in any other account could publish events here and move a delivery status.
data "aws_iam_policy_document" "ses_events_topic" {
  statement {
    sid       = "AllowSESPublish"
    effect    = "Allow"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.ses_events.arn]

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "ses_events" {
  arn    = aws_sns_topic.ses_events.arn
  policy = data.aws_iam_policy_document.ses_events_topic.json
}

resource "aws_sesv2_configuration_set" "this" {
  configuration_set_name = "${var.project}-${local.environment}"

  delivery_options {
    # TLS WHERE THE RECEIVER OFFERS IT. REQUIRE bounces mail to any receiving
    # domain without STARTTLS, which is a deliverability decision this product
    # has no business making on a candidate's behalf.
    tls_policy = "OPTIONAL"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }
}

resource "aws_sesv2_configuration_set_event_destination" "sns" {
  configuration_set_name = aws_sesv2_configuration_set.this.configuration_set_name
  event_destination_name = "${var.project}-${local.environment}-sns"

  event_destination {
    enabled = true

    sns_destination {
      topic_arn = aws_sns_topic.ses_events.arn
    }

    # NO OPEN AND NO CLICK. Both require SES to rewrite the message -- a
    # tracking pixel and wrapped links -- inside mail telling a candidate about
    # their own application. The delivery OUTCOME is all this product needs.
    matching_event_types = [
      "SEND",
      "DELIVERY",
      "BOUNCE",
      "COMPLAINT",
      "REJECT",
      "RENDERING_FAILURE",
      "DELIVERY_DELAY",
    ]
  }
}

# The webhook verifies the SNS signature and pins the topic ARN, so an https
# subscription is safe to declare. SNS posts a SubscriptionConfirmation first
# and the endpoint confirms it only after that signature check passes, which is
# why the API has to know the topic ARN BEFORE this is created. That ordering
# is the reason the topic and the task definition's environment ship together.
resource "aws_sns_topic_subscription" "ses_events_webhook" {
  count = local.has_public_entry ? 1 : 0

  topic_arn = aws_sns_topic.ses_events.arn
  protocol  = "https"
  endpoint  = "${local.frontend_url}/api/v1/email-senders/events/ses"

  # RAW DELIVERY OFF. The handler reads the SNS envelope itself -- Type,
  # TopicArn, SigningCertURL, Signature -- and raw delivery strips exactly the
  # fields the signature check needs.
  raw_message_delivery = false

  # Terraform waits for the endpoint to confirm. If it cannot, that is a real
  # failure worth surfacing rather than a subscription silently left pending.
  confirmation_timeout_in_minutes = 5
}

resource "aws_iam_role_policy" "task_worker_ses" {
  name   = "${var.project}-${local.environment}-ses-send"
  role   = element(split("/", module.lambda.execution_role_arns["task-worker"]), 1)
  policy = data.aws_iam_policy_document.ses_send.json
}

# ── The API's right to start work ────────────────────────────────────────────
#
# THE API COULD NOT INVOKE A SINGLE LAMBDA, and that was not a research bug: it
# broke every dispatched background task in the product. `TASK_DISPATCH_BACKEND`
# is `aws`, so `dispatch()` invokes `readypick-task-worker`, and `dispatch` RAISES
# on failure by design. Resume parsing, email delivery, matching runs and the
# reconciliation sweeps all start with that call. Both synchronous agents failed
# the same way, which is how it was found: Company Research answered 503 with
# `AccessDeniedException ... not authorized to perform: lambda:InvokeFunction`,
# and JD generation had the identical gap.
#
# It survived because nothing in the deploy asks this question. The functions
# exist, the task definitions carry the right environment, every service reports
# healthy, and the failure only appears when a human clicks something that
# dispatches. `terraform plan` cannot see a MISSING grant.
#
# ENUMERATED BY NAME, never `lambda:*` and never a prefix, which is the same
# rule `service_secrets` follows one module over. A revision qualifier is
# deliberately absent: these are invoked by function NAME, so an alias-less ARN
# is the exact grant rather than a wildcard standing in for one.
data "aws_iam_policy_document" "api_invoke_agents" {
  statement {
    sid     = "InvokeTheFunctionsTheApiActuallyCalls"
    actions = ["lambda:InvokeFunction"]
    resources = [
      # workers/dispatch.py: WORKER_FUNCTION and TRIGGER_FUNCTION.
      "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.project}-task-worker",
      "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.project}-assessment-trigger",
      # workers/agent_client.py: the two agents a recruiter waits on.
      "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.project}-jd-gen",
      "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.project}-company-profile",
    ]
  }
}

resource "aws_iam_role_policy" "api_invoke_agents" {
  name   = "${var.project}-${local.environment}-invoke-agents"
  role   = element(split("/", module.ecs.task_role_arns["api"]), 1)
  policy = data.aws_iam_policy_document.api_invoke_agents.json
}

resource "aws_iam_role_policy" "api_ses" {
  name   = "${var.project}-${local.environment}-ses-send"
  role   = element(split("/", module.ecs.task_role_arns["api"]), 1)
  policy = data.aws_iam_policy_document.ses_send.json
}

# ── Traffic ──────────────────────────────────────────────────────────────────
#
# The certificate, the DNS record and the WAF are CONDITIONAL on a domain being
# configured, and the load balancer is not: without a domain the environment
# still gets a load balancer at its own AWS name, which is what the deployment
# brief asks for.
#
# There is no plaintext-only mode, and that is deliberate rather than an
# omission. The application sets Secure cookies and the container runs uvicorn
# with `--proxy-headers`, so over plain http every auth cookie is refused and
# nobody can sign in. An http-only environment would not be a smaller product;
# it would be a product with no login.

module "acm" {
  source = "../../modules/acm"
  count  = local.has_domain ? 1 : 0

  project     = var.project
  environment = local.environment

  domain_name    = var.domain_name
  hosted_zone_id = var.hosted_zone_id
  # The bare apex is what the frontend, cookies and CORS allowlist all key on
  # (`local.frontend_url` above). `www` is added here purely so a visitor who
  # types it does not hit a certificate name mismatch; the DNS module below
  # aliases it to the same load balancer rather than standing up a redirect.
  subject_alternative_names = ["www.${var.domain_name}"]

  tags = local.tags
}

module "alb" {
  source = "../../modules/alb"
  count  = local.has_public_entry ? 1 : 0

  project     = var.project
  environment = local.environment
  account_id  = var.account_id

  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
  security_group_id = module.network.alb_security_group_id

  # From the VALIDATION resource, not from the certificate itself: a listener
  # created against a PENDING_VALIDATION certificate is accepted by AWS and
  # then fails the TLS handshake for every visitor.
  # From the ACM VALIDATION resource where there is a domain, so a listener is
  # never created against a PENDING_VALIDATION certificate: AWS accepts that
  # and then fails the TLS handshake for every visitor. Otherwise the imported
  # stopgap, which is ISSUED the moment it is imported.
  certificate_arn = local.has_domain ? module.acm[0].certificate_arn : var.fallback_certificate_arn

  access_logs_bucket_name = var.access_logs_bucket_name

  # Off for a pilot, which is meant to be destroyable.
  enable_deletion_protection = false

  target_groups = {
    api = {
      # DEEP, NOT A STATIC 200. `/health` resolves a pooled database session
      # AND pings Redis, so a task with a wrong DSN or an unreachable cache
      # fails this check and the ECS circuit breaker rolls the deploy back. A
      # static 200 would promote that same task.
      port                    = 8000
      health_path             = "/health"
      health_interval_seconds = 30
      health_timeout_seconds  = 10
    }
    frontend = {
      port                    = 3000
      health_path             = "/"
      health_interval_seconds = 30
      health_timeout_seconds  = 10
    }
  }

  default_target_group = "frontend"
  public_target_group  = "api"

  # THE ENUMERATED UNAUTHENTICATED SURFACE (RBAC sections 15 and 33). These are
  # the paths behind the public job link a candidate reaches from a forwarded
  # email. Adding to this list widens the unauthenticated surface of the
  # product, and `backend/tests/test_deploy_secret_hygiene.py` reads it back out
  # of the environment files so an addition fails a test.
  public_path_patterns = [
    "/api/v1/jobs/public/*",
    "/api/v2/jobs/public/*",
  ]

  routes = {
    api = {
      priority      = 100
      target_group  = "api"
      path_patterns = ["/api/*", "/docs", "/openapi.json"]
    }
  }

  tags = local.tags
}

module "dns" {
  source = "../../modules/dns"
  count  = local.has_domain ? 1 : 0

  hosted_zone_id = var.hosted_zone_id
  hostnames      = [var.domain_name, "www.${var.domain_name}"]

  alb_dns_name = module.alb[0].dns_name
  # The LOAD BALANCER's zone, an AWS-owned zone, NOT `var.hosted_zone_id`.
  # Passing the product's own zone here produces a record that resolves to
  # nothing.
  alb_zone_id = module.alb[0].zone_id
}

module "waf" {
  source = "../../modules/waf"
  count  = local.has_public_entry ? 1 : 0

  project     = var.project
  environment = local.environment

  # BUILT AND OFF. `enabled = false` creates nothing at all, rather than a
  # permissive web ACL that costs money and proves nothing. Turn it on in
  # `count_only` mode first: the managed rule sets inspect request bodies, and
  # this product's request bodies are resumes, client-written job descriptions
  # and interview answers, which is a corpus no generic rule set was tuned
  # against.
  enabled    = false
  count_only = true

  alb_arn     = module.alb[0].arn
  kms_key_arn = aws_kms_key.this.arn

  tags = local.tags
}

# ── Compute ──────────────────────────────────────────────────────────────────

module "ecs" {
  source = "../../modules/ecs"

  project     = var.project
  environment = local.environment
  region      = var.region

  private_subnet_ids    = module.network.private_subnet_ids
  ecs_security_group_id = module.network.ecs_security_group_id

  vpc_id              = module.network.vpc_id
  discovery_namespace = local.internal_namespace

  secret_policy_arns = module.secrets.policy_arns

  # The WRITE half, and it lands on the TASK role rather than the

  # execution role: the execution role injects secrets before the

  # container starts, while `app.scripts.provision_app_db_role` writes

  # the rotated DSN with the application's own SDK.

  secret_writer_policy_arns = module.secrets.writer_policy_arns
  s3_policy_arn             = module.s3.access_policy_arn
  ecr_repository_arns       = values(module.ecr.repository_arns)

  kms_key_arn        = aws_kms_key.this.arn
  log_retention_days = 30

  # OFF. A shell in a container holding real candidate data is a different
  # thing from a shell in one holding seed data, and this environment is the
  # one with pilot customers in it.
  enable_execute_command = false

  common_environment = {
    # `ENVIRONMENT`, NOT `APP_ENV`. `Settings.environment` reads this name,
    # and `APP_ENV` is a Cloud Run convention from the previous platform that
    # nothing has ever read. With it set, every deployed service believed it
    # was in `development` -- the default -- which is what the pilot's first
    # agent run reported in its own log.
    ENVIRONMENT                   = local.environment
    AWS_REGION                    = var.region
    S3_BUCKET                     = module.s3.bucket_name
    EMBEDDING_DIMENSIONS          = "1024"
    RESUME_SIGNED_URL_TTL_SECONDS = "300"
    FRONTEND_URL                  = local.frontend_url
    # `aws` is the only backend that reaches a Lambda. `local` runs tasks in a
    # thread and `record` runs nothing, and neither belongs on a deployed
    # service: `record` is refused in production by the dispatcher itself.
    TASK_DISPATCH_BACKEND = "aws"
    # ONE TRANSPORT PER DEPLOYMENT, never a fallback chain: the same shape
    # TASK_DISPATCH_BACKEND has. SES sends as the tenant's own verified
    # sender, which is the whole point of the corporate-sender feature and
    # something an authenticated Gmail mailbox structurally cannot do.
    EMAIL_TRANSPORT = "ses"
    # THE CONFIGURATION SET IS WHAT MAKES DELIVERY TRACKING EXIST. SES only
    # publishes events for a message sent under a configuration set that has an
    # event destination, so a send without this name attached is a send nobody
    # ever learns the outcome of. The topic ARN is what the webhook pins an
    # incoming message against; empty there means refuse everything.
    SES_CONFIGURATION_SET = aws_sesv2_configuration_set.this.configuration_set_name
    SES_SNS_TOPIC_ARN     = aws_sns_topic.ses_events.arn
    # THE DOMAIN A REPLY COMES BACK TO, and the domain the application builds a
    # thread's Reply-To on. Both halves read this ONE value, so the address a
    # verification request asks an employer to reply to and the address SES is
    # configured to receive cannot drift into each other's blind spot. Empty
    # without a domain, which is a real state rather than a broken one: the
    # deployment still sends, and `conversations.reply_address` records that the
    # reply will arrive in the sending mailbox instead of in the thread.
    INBOUND_EMAIL_DOMAIN = local.reply_domain
    # ONE RERANKER PER DEPLOYMENT, never a fallback chain: the same shape
    # TASK_DISPATCH_BACKEND and EMAIL_TRANSPORT have, validated against a
    # closed set by `reranker.configured_backend()`, which RAISES on anything
    # outside it rather than defaulting.
    #
    # PILOT RUNS THE CROSS-ENCODER AND THE OTHER ENVIRONMENTS DO NOT, which is
    # deliberate. `rerank-2.5` was proven live on 2026-09-09 (see
    # VERIFICATION_RESULTS.md) and the CODE default is `lexical`, so leaving
    # this unset anywhere means the deterministic pass runs there. Turning it
    # on changes which evidence an agent reads FIRST, and retrieval quality is
    # still unmeasured -- the golden set is 24 hand-authored cases against a
    # floor of 300 -- so the change is made where it can be watched before it
    # is made where it cannot.
    #
    # IT IS SET ON THE LAMBDAS TOO, and that uniformity is the point. Agents
    # reach retrieval through `services/tools/implementations`, which runs in
    # the API, in `task-worker` and in `agent`. Setting it on the ECS services
    # alone would give two halves of one product different rankings for the
    # same query, which is worse than either value applied everywhere.
    #
    # This is not a claim the cross-encoder will always answer. When it cannot,
    # the lexical pass runs and the record carries `reranker="lexical",
    # degraded=true` with a reason, because a degradation is RECORDED and never
    # silent. What the value guarantees is that a deployment believing it runs
    # a cross-encoder is not quietly running the placeholder, which is the
    # failure W6.1 exists to end.
    RETRIEVAL_RERANKER = "voyage"
  }

  services = {
    api = {
      image         = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      cpu           = 512
      memory        = 1024
      desired_count = local.service_count
      # The ceiling the OLD sizing allowed (4), kept: lowering the resting
      # count must not lower what the service can grow to under load.
      max_count   = 4
      port        = 8000
      health_path = "/health"
      # REGISTERS THE TASKS WITH THE LOAD BALANCER, when there is one. Without
      # it the service runs, is attached to no target group, and serves no
      # traffic while every dashboard reports it healthy. Null here is not that
      # mistake: there is no load balancer to be attached to yet, and the ECS
      # module's own validation refuses a target group without a port so the
      # two cannot get out of step.
      target_group_arn = local.has_public_entry ? module.alb[0].target_group_arns["api"] : null
      needs_s3         = true
      environment = {
        PROCTORING_ANALYSIS_SERVICE_URL = local.analysis_service_url
        # PUBLIC BY DESIGN, and a plain variable rather than a secret for that
        # reason: the browser reads it from GET /billing/config at runtime.
        # Its partner, RAZORPAY_KEY_SECRET, is server-side only and is mounted
        # from Secrets Manager below. Checkout needs both.
        RAZORPAY_KEY_ID = var.razorpay_key_id
      }
      # The backend writes no APPLICATION data to disk by design: resume bytes
      # never persist on the filesystem, and that invariant is what
      # `readonly_root` enforces.
      #
      # `/tmp` IS THE NARROW EXCEPTION, AND IT IS LOAD BEARING FOR SIGN-IN.
      # Verifying a Firebase ID token fetches Google's public signing certs,
      # and `google-auth` caches that response through `cachecontrol`, which
      # writes it to a NamedTemporaryFile. With no writable temp directory
      # that raises FileNotFoundError deep inside verification, where it is
      # indistinguishable from a bad credential: the route answered 401
      # "Invalid Firebase session" for every valid Google and password
      # sign-in on the live site, which is what sent the search to Firebase's
      # authorized-domain list instead of here. An empty ephemeral volume, so
      # the rest of the filesystem stays immutable for the life of the task.
      readonly_root  = true
      writable_paths = ["/tmp"]
      # The backend image runs as uid 10001 (`pickready`). Mounting the volume
      # is only half the fix: a Fargate task volume arrives root-owned 0755, so
      # it has to be handed to that uid before the application starts.
      writable_paths_uid = "10001"
      secrets = {
        DATABASE_URL                  = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                     = module.secrets.secret_arns["REDIS_URL"]
        JWT_SECRET                    = module.secrets.secret_arns["JWT_SECRET"]
        OPENAI_GPT_TERRA              = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA               = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        VOYAGE_CONTEXT_4              = module.secrets.secret_arns["VOYAGE_CONTEXT_4"]
        VOYAGE_RERANK_2_5             = module.secrets.secret_arns["VOYAGE_RERANK_2_5"]
        FIREBASE_SERVICE_ACCOUNT_JSON = module.secrets.secret_arns["FIREBASE_SERVICE_ACCOUNT_JSON"]
        RAZORPAY_KEY_SECRET           = module.secrets.secret_arns["RAZORPAY_KEY_SECRET"]
        LLM_KEY_ENCRYPTION_SECRET     = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
        # AI Reach calls Tavily from the request handler, so the API is the
        # process that needs this. See the IAM list in modules/secrets.
        TAVILY_API_KEY = module.secrets.secret_arns["TAVILY_API_KEY"]
      }
    }

    # THE ASSESSMENT AGENT. A task definition and nothing else: no service, no
    # desired count, no autoscaling. `readypick-assessment-trigger` calls
    # RunTask against this family when an assessment, a matrix compilation or a
    # matching pass is dispatched, the container runs that one piece of work,
    # and the process exits. The task stops, and that is when the meter stops.
    #
    # This is the whole cost argument for the architecture. Fargate does not
    # scale to zero, so long AI work that runs a few times an hour must not be
    # a standing service.
    agent = {
      image = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      # `agent` is the entry point role in docker-entrypoint.sh: read one
      # dispatched task from the environment, run it, exit.
      command       = ["agent"]
      cpu           = 1024
      memory        = 2048
      on_demand     = true
      desired_count = 0
      max_count     = 0
      # FARGATE'S MAXIMUM, and it is a SIGTERM grace period rather than a
      # ceiling: this task runs for as long as its work takes and stops when the
      # process exits. Two minutes is what an interrupted run gets to finish the
      # piece it is on, instead of the 30-second default that would leave a
      # conversation scored with no report written.
      stop_timeout = 120
      needs_s3     = true
      # NOT read-only: resume and project parsing write temp files.
      readonly_root = false
      environment = {
        PROCTORING_ANALYSIS_SERVICE_URL = local.analysis_service_url
        # SPEECH TO TEXT. `pickready.process_assessment_video` is Route.ECS, so
        # this task is the only thing that ever calls Transcribe. The region is
        # separate from `AWS_REGION` because ap-south-2 has no Transcribe
        # endpoint, and the bucket travels with the region because a job cannot
        # read a bucket outside its own. With the feature off, a recording
        # lands in `transcription_failed` saying so rather than carrying a
        # fabricated transcript.
        TRANSCRIBE_ENABLED = var.transcribe_enabled ? "true" : "false"
        TRANSCRIBE_REGION  = var.transcribe_region
        # A SPLAT AND A JOIN, never `[0]` behind a conditional. Terraform does
        # not reliably short-circuit an index expression, so `[0]` on a
        # count = 0 resource fails the plan even on the branch that never runs.
        # An empty splat joins to "", which is exactly "no working bucket".
        TRANSCRIBE_BUCKET = join("", aws_s3_bucket.transcribe[*].id)
      }
      # NO FIREBASE KEY. A background task never authenticates a browser
      # session, so it has no business reading the service account.
      secrets = {
        DATABASE_URL              = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                 = module.secrets.secret_arns["REDIS_URL"]
        OPENAI_GPT_TERRA          = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA           = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        VOYAGE_CONTEXT_4          = module.secrets.secret_arns["VOYAGE_CONTEXT_4"]
        VOYAGE_RERANK_2_5         = module.secrets.secret_arns["VOYAGE_RERANK_2_5"]
        LLM_KEY_ENCRYPTION_SECRET = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
    }

    # THE MIGRATION JOB. A task definition with no service, run as a one-shot
    # task by `scripts/run-migration.sh` BEFORE the services are updated.
    #
    # Never a step in the API's startup: several tasks boot at once during a
    # rollout and would race each other through the same migration. Alembic
    # takes a lock, so the losers crash-loop.
    #
    # ONE SECRET, the DSN, and nothing else at all. It connects, applies DDL and
    # exits; anything else it could read is reach its work does not need, and
    # `tests/test_deploy_secret_hygiene.py` asserts exactly that.
    migrate = {
      image = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      # The entry point role that runs `alembic upgrade head` and exits.
      command       = ["migrate"]
      cpu           = 512
      memory        = 1024
      on_demand     = true
      desired_count = 0
      max_count     = 0
      # Fargate's maximum, and only a SIGTERM grace period: the migration's real
      # bound is `run-migration.sh`'s own 900-second wait, which polls for
      # STOPPED and reads the exit code. That script deliberately does NOT treat
      # a timeout as success, because a half-applied schema is the one state a
      # deploy must never be layered on top of.
      stop_timeout  = 120
      readonly_root = true
      secrets = {
        DATABASE_URL = module.secrets.secret_arns["DATABASE_URL"]
      }
      # THE ONLY PLACE IN THE CLUSTER THAT CAN ESCALATE, AND THE ONLY PLACE
      # THAT CAN REWRITE THE DSN. Both belong to the one-shot job and to
      # nothing that serves a request.
      #
      # `DATABASE_URL` carries a least-privileged role that owns no object, so
      # `alembic upgrade` has to SET ROLE to the owner for DDL, and
      # `app.scripts.provision_app_db_role` has to be able to replace the DSN
      # when it rotates that role's password. The API, the agent and the
      # Lambda worker read the same secret and have neither variable, so
      # neither power is reachable from a route.
      #
      # Why any of this exists: `DATABASE_URL` used to hold a copy of the RDS
      # MASTER password, which `manage_master_user_password` has Secrets
      # Manager rotate on a schedule. It rotated on 2026-09-11, the copy went
      # stale, and every database connection in the product failed at once.
      #
      # The owner is a DEDICATED NOLOGIN role rather than the RDS master, and
      # PostgreSQL 16 is the reason: a role holds no ADMIN OPTION on itself, so
      # the master cannot grant itself to the application role, and it is not a
      # superuser here. It can grant a role it CREATED, so
      # `app.scripts.provision_app_db_role` creates this one, hands it every
      # object with REASSIGN OWNED, and grants it onward -- after which the
      # master is unused and its rotation stops mattering.
      environment = {
        POSTGRES_MIGRATION_ROLE = local.db_owner_role
        APP_DSN_SECRET_ID       = module.secrets.secret_arns["DATABASE_URL"]
      }
    }

    frontend = {
      image            = "${module.ecr.repository_urls["frontend"]}:${local.frontend_image_tag}"
      cpu              = 512
      memory           = 1024
      desired_count    = local.service_count
      max_count        = 4
      port             = 3000
      health_path      = "/"
      target_group_arn = local.has_public_entry ? module.alb[0].target_group_arns["frontend"] : null
      readonly_root    = false # Next.js writes its own cache
      # The frontend holds NO secrets. The Razorpay key id it needs is public
      # and is fetched at runtime from GET /billing/config, which is why it was
      # never a NEXT_PUBLIC_ build variable.
      secrets = {}
    }

    # THE PROCTORING ANALYSIS SERVICE. Speaker counting over a fifteen-second
    # audio chunk. Its own image because it carries torch and pyannote, its own
    # service because an inference call that pinned a request worker would cost
    # a candidate mid-assessment their next question, and NOT PUBLIC: no target
    # group, no listener rule, reachable only at its Cloud Map name from inside
    # the ECS security group.
    analysis = {
      image         = "${module.ecr.repository_urls["analysis"]}:${local.analysis_image_tag}"
      cpu           = 2048
      memory        = 8192
      desired_count = local.service_count
      # Two, not four: each analysis task is 2 vCPU / 8 GB, the costliest
      # step in the cluster, and its workload (fifteen-second audio chunks)
      # has never occurred in this environment.
      max_count    = 2
      port         = 8100
      health_path  = "/health"
      discoverable = true
      # READ-ONLY ROOT with one exception: the import-time caches torch and
      # matplotlib insist on, which the image points at /tmp.
      readonly_root  = true
      writable_paths = ["/tmp"]
      # The image runs as uid 10001. A Fargate task volume mounts root-owned
      # 0755, so without this the mount is unwritable and torch and matplotlib
      # fail at import -- the same trap that broke sign-in on the API.
      writable_paths_uid = "10001"
      # The Hugging Face token, and nothing else. It holds no DSN and no
      # model-provider key, because all it is handed is audio and all it
      # answers is a speaker count.
      secrets = {
        HUGGINGFACE_TOKEN = module.secrets.secret_arns["HUGGINGFACE_TOKEN"]
      }
    }
  }

  tags = local.tags
}

# ── Background work ──────────────────────────────────────────────────────────

module "lambda" {
  source = "../../modules/lambda"

  project     = var.project
  environment = local.environment
  region      = var.region
  account_id  = var.account_id

  # IN THE VPC, all four. Three of them reach RDS and Redis, which live in
  # subnets with no route to the internet in either direction. The trigger does
  # not need the database, and it is in the VPC anyway: it calls ecs:RunTask
  # through the VPC endpoint rather than out through NAT, which keeps the one
  # function holding iam:PassRole off the public internet entirely.
  vpc_subnet_ids     = module.network.private_subnet_ids
  security_group_ids = [module.network.ecs_security_group_id]

  # ARM64, matching the `ecs` module's default. The same backend image backs the
  # API service, the on-demand agent and three of these four functions, so one
  # architecture is not a preference here, it is a correctness requirement.
  architecture = "arm64"

  secret_policy_arns = module.secrets.policy_arns

  kms_key_arn        = aws_kms_key.this.arn
  log_retention_days = 30
  failure_topic_arn  = aws_sns_topic.alarms.arn

  functions = {
    "task-worker" = {
      package     = "image"
      description = "Every short background task: delivery, resume parsing, the reconciliation sweeps."
      image_uri   = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      handler     = "app.workers.entrypoints.lambda_worker.lambda_handler"
      # ITSELF, AND ONLY ITSELF. A Route.LAMBDA sweep that fans out to
      # Route.LAMBDA work is this function invoking this function: one function
      # serves every short task. `pickready.reconcile_context_index` is the
      # first task in the product that does it, and production answered
      # AccessDeniedException because no earlier sweep had ever needed the
      # grant -- every previous one dispatched to Route.ECS, which goes through
      # ecs:RunTask and is a different permission.
      invokable_function_keys = ["task-worker"]
      memory_mb               = 1024
      # Ten minutes. The binding case is a delivery task backing off sixty
      # seconds between attempts; the retry loop refuses an attempt that cannot
      # finish inside what is left, so this is a ceiling rather than a target.
      timeout_seconds = 600
      # A CEILING, not a reservation. The database has a connection limit and
      # each concurrent invocation opens its own engine; twenty is comfortably
      # inside db.t4g.micro's limit with the API's pool alongside it.
      reserved_concurrency = var.reserve_lambda_concurrency ? 20 : null
      secret_policy_key    = "task-worker"
      # The SAME map the ECS services use. ECS injects these; Lambda has no
      # equivalent, so the function fetches them at cold start with the
      # policy below. Only the ARNs are here.
      secrets = {
        DATABASE_URL              = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                 = module.secrets.secret_arns["REDIS_URL"]
        OPENAI_GPT_TERRA          = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA           = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        VOYAGE_CONTEXT_4          = module.secrets.secret_arns["VOYAGE_CONTEXT_4"]
        VOYAGE_RERANK_2_5         = module.secrets.secret_arns["VOYAGE_RERANK_2_5"]
        TAVILY_API_KEY            = module.secrets.secret_arns["TAVILY_API_KEY"]
        MSG91_API_KEY             = module.secrets.secret_arns["MSG91_API_KEY"]
        LLM_KEY_ENCRYPTION_SECRET = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
      environment = {
        # Must match the ECS services: agents reach retrieval from here too,
        # and two halves of one product ranking the same query differently
        # is worse than either value applied everywhere. See the note on
        # RETRIEVAL_RERANKER in common_environment above.
        RETRIEVAL_RERANKER = "voyage"
        # AWS_REGION IS NOT SET HERE. It is one of Lambda's RESERVED keys: the
        # runtime injects it with the function's own region, and CreateFunction
        # answers 400 for any request that also supplies it. Nothing is lost --
        # `Settings.aws_region` reads that same variable, so boto3 and the
        # application agree with the platform rather than with a literal.
        ENVIRONMENT                   = local.environment
        S3_BUCKET                     = module.s3.bucket_name
        FRONTEND_URL                  = local.frontend_url
        EMBEDDING_DIMENSIONS          = "1024"
        RESUME_SIGNED_URL_TTL_SECONDS = "300"
        # A task can dispatch another task: `run_matching` dispatches a report
        # synthesis per newly completed candidate.
        TASK_DISPATCH_BACKEND           = "aws"
        PROCTORING_ANALYSIS_SERVICE_URL = local.analysis_service_url
        # THE OUTBOUND MAIL HOP. Delivery is Route.LAMBDA, so this function is
        # where a message actually leaves the platform. It reads the same
        # single transport the services do; the Gmail app password is not
        # mounted here any more, because a credential for a transport this
        # deployment does not use is reach the function does not need.
        EMAIL_TRANSPORT = "ses"
        # SES sends only for an identity the ACCOUNT has verified, so this is
        # not a free-text display address: it must be a verified sender.
        SMTP_FROM_EMAIL = var.platform_from_email
        SMTP_FROM_NAME  = "ReadyPick"
        # This function is the hop that actually writes the Reply-To header, so
        # it needs the same value the API used to build the address.
        INBOUND_EMAIL_DOMAIN = local.reply_domain
        # This function is the last hop before SES, so it is the one that must
        # attach the configuration set. Without it SES accepts the message and
        # publishes no event, and every row stays `sent` for ever.
        SES_CONFIGURATION_SET = aws_sesv2_configuration_set.this.configuration_set_name
        SES_SNS_TOPIC_ARN     = aws_sns_topic.ses_events.arn
      }
    }

    "jd-gen" = {
      package     = "image"
      description = "Writes one job description draft. Invoked synchronously by the request handler that is already waiting for it."
      image_uri   = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      handler     = "app.workers.entrypoints.agents.jd_generation_handler"
      memory_mb   = 512
      # The task's own model budget is 25s per attempt and 50s in total, inside
      # an agent loop bounded at two attempts. Ten minutes is the ceiling that
      # cannot be reached rather than the time this takes.
      timeout_seconds      = 600
      reserved_concurrency = var.reserve_lambda_concurrency ? 10 : null
      secret_policy_key    = "jd-gen"
      # The SAME map the ECS services use. ECS injects these; Lambda has no
      # equivalent, so the function fetches them at cold start with the
      # policy below. Only the ARNs are here.
      secrets = {
        DATABASE_URL              = module.secrets.secret_arns["DATABASE_URL"]
        OPENAI_GPT_TERRA          = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA           = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        LLM_KEY_ENCRYPTION_SECRET = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
      environment = {
        # AWS_REGION IS NOT SET HERE. It is one of Lambda's RESERVED keys: the
        # runtime injects it with the function's own region, and CreateFunction
        # answers 400 for any request that also supplies it. Nothing is lost --
        # `Settings.aws_region` reads that same variable, so boto3 and the
        # application agree with the platform rather than with a literal.
        ENVIRONMENT           = local.environment
        TASK_DISPATCH_BACKEND = "aws"
      }
    }

    "company-profile" = {
      package     = "image"
      description = "Drafts one company's three profile sections from public sources. Synchronous, like jd-gen."
      image_uri   = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      handler     = "app.workers.entrypoints.agents.company_profile_handler"
      memory_mb   = 512
      # A web search plus a model call. Longer than jd-gen's real budget
      # because the search is a third party with its own latency.
      timeout_seconds      = 300
      reserved_concurrency = var.reserve_lambda_concurrency ? 5 : null
      secret_policy_key    = "company-profile"
      # The SAME map the ECS services use. ECS injects these; Lambda has no
      # equivalent, so the function fetches them at cold start with the
      # policy below. Only the ARNs are here.
      secrets = {
        DATABASE_URL              = module.secrets.secret_arns["DATABASE_URL"]
        OPENAI_GPT_TERRA          = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA           = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        TAVILY_API_KEY            = module.secrets.secret_arns["TAVILY_API_KEY"]
        LLM_KEY_ENCRYPTION_SECRET = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
      environment = {
        # AWS_REGION IS NOT SET HERE. It is one of Lambda's RESERVED keys: the
        # runtime injects it with the function's own region, and CreateFunction
        # answers 400 for any request that also supplies it. Nothing is lost --
        # `Settings.aws_region` reads that same variable, so boto3 and the
        # application agree with the platform rather than with a literal.
        ENVIRONMENT           = local.environment
        TASK_DISPATCH_BACKEND = "aws"
      }
    }

    # THE ONLY ZIP, AND THE ONLY THING HOLDING iam:PassRole.
    #
    # Thirty lines and boto3. It must stay that way: passing a role is a
    # privilege-escalation primitive, since anything that can pass a role can
    # run code as it, and the mitigation is that this function's whole source
    # fits on a screen.
    "assessment-trigger" = {
      package         = "zip"
      description     = "Starts one on-demand assessment-agent task and returns. Holds iam:PassRole and reads no secret."
      source_dir      = "${path.root}/../../../lambda/assessment_trigger"
      handler         = "handler.lambda_handler"
      memory_mb       = 128
      timeout_seconds = 30
      # No secret_policy_key. It reads nothing, which is why it has no entry in
      # the secrets module's map at all.
      run_task_cluster_arn          = module.ecs.cluster_arn
      run_task_task_definition_arns = [module.ecs.on_demand_task_definition_arns["agent"]]
      # Both roles: RunTask passes the task role the container runs as AND the
      # execution role the ECS agent uses to pull the image and fetch secrets
      # before it starts, so a grant naming only the first fails at RunTask.
      run_task_role_arns = [
        module.ecs.task_role_arns["agent"],
        module.ecs.execution_role_arns["agent"],
      ]
      environment = {
        # AWS_REGION IS NOT SET HERE. It is one of Lambda's RESERVED keys: the
        # runtime injects it with the function's own region, and CreateFunction
        # answers 400 for any request that also supplies it. Nothing is lost --
        # `Settings.aws_region` reads that same variable, so boto3 and the
        # application agree with the platform rather than with a literal.
        ECS_CLUSTER            = module.ecs.cluster_name
        ECS_TASK_DEFINITION    = module.ecs.on_demand_task_families["agent"]
        ECS_CONTAINER_NAME     = "agent"
        PRIVATE_SUBNET_IDS     = join(",", module.network.private_subnet_ids)
        ECS_SECURITY_GROUP_IDS = module.network.ecs_security_group_id
      }
    }

    # The inbound-mail parser. A SECOND ZIP FUNCTION, and the reason is the
    # same one the trigger gives: it sits on the open internet's side of the
    # product, because anything that can send mail to the reply domain reaches
    # it. Standard library and boto3 only, so what a stranger can reach is one
    # file a reviewer reads in full. It holds no database credential and no
    # model key; the one thing it can do is POST to a webhook that authorises
    # itself on a token the message already carried.
    "inbound-email" = {
      package     = "zip"
      description = "Parses one received message and posts it to the inbound-email webhook. Reads one S3 object and nothing else."
      source_dir  = "${path.root}/../../../lambda/inbound_email"
      handler     = "handler.handler"
      memory_mb   = 256
      # A large attachment is fetched whole before it is parsed. Thirty seconds
      # is generous for that and short enough that a hung webhook is a failed
      # invocation rather than a held one.
      timeout_seconds = 30
      # OUTSIDE THE VPC. It talks to S3 and to the product's own public
      # endpoint, so putting it in a private subnet would buy nothing and cost
      # a NAT hop for every reply.
      in_vpc = false
      # No secret_policy_key: it reads no secret, which is why it has no entry
      # in the secrets module's map at all.
      s3_read_object_arns = [local.inbound_mail_objects]
      environment = {
        WEBHOOK_URL = "${local.frontend_url}/api/v1/verification/inbound-email"
      }
    }
  }

  tags = local.tags
}

# ── Receiving a verification reply ───────────────────────────────────────────
#
# The MX record, the SES receipt rule, the bucket the raw message lands in, and
# the notification that wakes the parser. See `modules/ses_inbound` for why the
# rule matches the whole subdomain and why the message goes through S3 rather
# than straight to the function.

module "ses_inbound" {
  source = "../../modules/ses_inbound"
  count  = local.has_inbound ? 1 : 0

  project     = var.project
  environment = local.environment
  account_id  = var.account_id
  region      = var.region

  reply_domain   = local.reply_domain
  hosted_zone_id = var.hosted_zone_id

  lambda_function_arn  = module.lambda.function_arns["inbound-email"]
  lambda_function_name = module.lambda.function_names["inbound-email"]

  # PILOT IS THE ONE RECEIVING IN THIS REGION. SES allows exactly one active
  # receipt rule set per region per account, so a second environment setting
  # this would silently take pilot's mail. Stated here so that change is a
  # conflict in a diff rather than an outage nobody can see.
  activate_rule_set = true

  tags = local.tags
}

# ── The schedule ─────────────────────────────────────────────────────────────
#
# MIRRORS `backend/app/workers/schedule.py`, and
# `backend/tests/test_schedule_parity.py` reads both and fails if they drift.
# Two copies of one fact stay honest only when something compares them.

module "scheduler" {
  source = "../../modules/scheduler"

  project     = var.project
  environment = local.environment
  account_id  = var.account_id

  target_function_arn = module.lambda.function_arns["task-worker"]
  timezone            = "Asia/Kolkata"

  schedules = {
    "readypick-refresh-dashboard-views" = {
      task            = "pickready.refresh_dashboard_views"
      rate_expression = "rate(5 minutes)"
    }
    "readypick-remind-unapproved-framework" = {
      task            = "pickready.remind_unapproved_technical_questions"
      rate_expression = "rate(60 minutes)"
    }
    "readypick-reconcile-job-setup" = {
      task            = "pickready.reconcile_job_setup"
      rate_expression = "rate(15 minutes)"
    }
    "readypick-reconcile-assessment-credits" = {
      task            = "pickready.reconcile_assessment_credits"
      rate_expression = "rate(60 minutes)"
    }
    "readypick-reconcile-project-intake" = {
      task            = "pickready.reconcile_project_intake"
      rate_expression = "rate(60 minutes)"
    }
    "readypick-reconcile-proctoring-sessions" = {
      task            = "pickready.reconcile_proctoring_sessions"
      rate_expression = "rate(60 minutes)"
    }
    "readypick-purge-proctoring-events" = {
      task            = "pickready.purge_proctoring_events"
      rate_expression = "rate(60 minutes)"
    }
    # RPN-AI-UP-001 W2.2. The Terraform half of the entry in
    # app/workers/schedule.py. tests/test_schedule_parity.py fails on drift,
    # because an entry in Python with no rule here is the SILENT half: a sweep
    # does nothing when there is nothing to repair, so "not running" and
    # "nothing to do" produce the same empty log.
    "readypick-reconcile-context-index" = {
      task            = "pickready.reconcile_context_index"
      rate_expression = "rate(60 minutes)"
    }
    # Registered since the credit work and scheduled by nothing until now: it
    # was dispatched only when a bundle was granted, so a report lost to a
    # failed dispatch or a killed container stayed lost, for a candidate who
    # had done the work and a customer who had been charged.
    "readypick-release-held-assessments" = {
      task            = "pickready.release_held_assessments"
      rate_expression = "rate(60 minutes)"
    }
  }

  tags = local.tags
}

# ── Observability ────────────────────────────────────────────────────────────

module "observability" {
  source = "../../modules/observability"

  project     = var.project
  environment = local.environment
  region      = var.region

  alarm_topic_arn = aws_sns_topic.alarms.arn

  # The load balancer alarms exist only when the load balancer does. Gated on a
  # bool derived from a variable, so the count is known at plan time.
  enable_alb_alarms        = local.has_public_entry
  load_balancer_arn_suffix = local.has_public_entry ? module.alb[0].arn_suffix : null
  target_group_arn_suffix  = local.has_public_entry ? module.alb[0].target_group_arn_suffixes["api"] : null

  db_instance_id = module.rds.instance_id

  # One alarm per function. See the module: a single aggregate error rate would
  # be dominated by whichever function is busiest, so the task worker running
  # every email in the product could hide the JD writer failing every call.
  function_names = module.lambda.function_names

  # The metric filter over `ecs_task.failed`. There is no ECS metric for "a
  # task exited non-zero", and the Lambda that started it returned as soon as
  # RunTask was accepted, so this log line is the only report of the failure.
  agent_log_group_name = module.ecs.log_group_names["agent"]

  kms_key_arn = aws_kms_key.this.arn
  tags        = local.tags
}
