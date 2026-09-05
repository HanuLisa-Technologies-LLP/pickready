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

locals {
  environment = "pilot"

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

  # WITHOUT INGRESS, ONE TASK PER SERVICE.
  #
  # Two once traffic can arrive, one while it cannot. A second task buys
  # redundancy for requests that have no way in, and one is enough to prove
  # what an unreachable stage is for: that the image boots, resolves its
  # secrets, and reaches RDS and Redis from a private subnet.
  service_count = local.has_public_entry ? 2 : 1

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

  instance_class = "db.t4g.micro"
  # 50 GB growing to 100. gp3's baseline IOPS is a function of size, so the
  # floor is a performance floor as well as a capacity one.
  allocated_storage     = 50
  max_allocated_storage = 100
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

  secret_policy_arns  = module.secrets.policy_arns
  s3_policy_arn       = module.s3.access_policy_arn
  ecr_repository_arns = values(module.ecr.repository_arns)

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
  }

  services = {
    api = {
      image         = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      cpu           = 512
      memory        = 1024
      desired_count = local.service_count
      max_count     = local.service_count * 2
      port          = 8000
      health_path   = "/health"
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
      }
      # The backend writes nothing to disk by design: resume bytes never
      # persist on the application filesystem. This enforces an invariant the
      # code already claims.
      readonly_root = true
      secrets = {
        DATABASE_URL                  = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                     = module.secrets.secret_arns["REDIS_URL"]
        JWT_SECRET                    = module.secrets.secret_arns["JWT_SECRET"]
        OPENAI_GPT_TERRA              = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA               = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        VOYAGE_CONTEXT_4              = module.secrets.secret_arns["VOYAGE_CONTEXT_4"]
        FIREBASE_SERVICE_ACCOUNT_JSON = module.secrets.secret_arns["FIREBASE_SERVICE_ACCOUNT_JSON"]
        RAZORPAY_KEY_SECRET           = module.secrets.secret_arns["RAZORPAY_KEY_SECRET"]
        LLM_KEY_ENCRYPTION_SECRET     = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
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
      }
      # NO FIREBASE KEY. A background task never authenticates a browser
      # session, so it has no business reading the service account.
      secrets = {
        DATABASE_URL              = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                 = module.secrets.secret_arns["REDIS_URL"]
        OPENAI_GPT_TERRA          = module.secrets.secret_arns["OPENAI_GPT_TERRA"]
        OPENAI_GPT_LUNA           = module.secrets.secret_arns["OPENAI_GPT_LUNA"]
        VOYAGE_CONTEXT_4          = module.secrets.secret_arns["VOYAGE_CONTEXT_4"]
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
    }

    frontend = {
      image            = "${module.ecr.repository_urls["frontend"]}:${var.image_tag}"
      cpu              = 512
      memory           = 1024
      desired_count    = local.service_count
      max_count        = local.service_count * 2
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
      image         = "${module.ecr.repository_urls["analysis"]}:${var.image_tag}"
      cpu           = 2048
      memory        = 8192
      desired_count = local.service_count
      max_count     = local.service_count * 2
      port          = 8100
      health_path   = "/health"
      discoverable  = true
      # READ-ONLY ROOT with one exception: the import-time caches torch and
      # matplotlib insist on, which the image points at /tmp.
      readonly_root  = true
      writable_paths = ["/tmp"]
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
      memory_mb   = 1024
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
        SMTP_PASSWORD             = module.secrets.secret_arns["SMTP_PASSWORD"]
        TAVILY_API_KEY            = module.secrets.secret_arns["TAVILY_API_KEY"]
        MSG91_API_KEY             = module.secrets.secret_arns["MSG91_API_KEY"]
        LLM_KEY_ENCRYPTION_SECRET = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
      environment = {
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
  }

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
