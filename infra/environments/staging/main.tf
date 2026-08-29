/**
 * ReadyPick STAGING.
 *
 * Composes every module. Two environment roots rather than one root with a
 * workspace, and that is deliberate: a `terraform.workspace` conditional means
 * one plan file describes two environments, and the moment somebody runs the
 * wrong workspace the blast radius is production. Two directories means the
 * production plan cannot be produced by accident from the staging one.
 *
 * WHAT STAGING IS FOR, AND THEREFORE WHAT IS SMALLER HERE
 * --------------------------------------------------------
 * Staging exists to prove a deploy works, not to survive an AZ failure. So:
 * one NAT gateway, no RDS Multi-AZ, no Redis replica, one task per service,
 * seven-day backups, no deletion protection. Every one of those is a cost
 * decision and every one is stated in this file rather than defaulted, so the
 * production file's differences are readable as a diff.
 *
 * READ `infra/environments/README.md` BEFORE RUNNING ANYTHING. spec-doc5 §D.1
 * is explicit that this phase produces a codebase that is buildable and
 * planable but that NO LIVE DEPLOYMENT IS EXECUTED -- and it makes that a
 * pass/fail criterion in the opposite direction from usual.
 */

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
  }

  # REMOTE STATE, WITH LOCKING. Commented out because it must be bootstrapped
  # once by hand -- Terraform cannot create the bucket that holds its own state
  # -- and an uncommented backend block pointing at a bucket that does not exist
  # makes `terraform init` fail for everybody, including somebody who only
  # wanted to validate.
  #
  # `terraform validate` and `plan -backend=false` work without it, which is
  # exactly the mode this phase runs in.
  #
  # backend "s3" {
  #   bucket       = "readypick-tfstate"
  #   key          = "staging/terraform.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true # S3-native locking; DynamoDB is no longer needed
  # }
}

provider "aws" {
  region = var.region

  # ── THE PLANNING PROFILE (spec-doc6 §13.3) ─────────────────────────────────
  #
  # These four are the calls the AWS provider makes BEFORE it plans anything:
  # STS GetCallerIdentity, the region catalogue, and the EC2 instance metadata
  # endpoint. They are the entire reason the previous phase concluded that
  # `terraform plan` "cannot complete without credentials", and they can simply
  # be switched off. With `planning_profile = true` a plan runs offline, in CI,
  # with dummy credentials from the environment and nothing else.
  #
  # FALSE BY DEFAULT. A real apply must keep every one of these checks: with
  # them skipped, a misconfigured profile fails later and far less clearly.
  #
  # Read `var.planning_profile` for exactly what an offline plan proves and what
  # it does not. It does not prove "ready to run".
  skip_credentials_validation = var.planning_profile
  skip_requesting_account_id  = var.planning_profile
  skip_region_validation      = var.planning_profile
  skip_metadata_api_check     = var.planning_profile

  default_tags {
    tags = local.tags
  }
}

locals {
  environment = "staging"

  tags = {
    Project     = var.project
    Environment = local.environment
    ManagedBy   = "terraform"
    Repository  = "readypick"
  }
}

# ── KMS ──────────────────────────────────────────────────────────────────────
#
# ONE CUSTOMER-MANAGED KEY PER ENVIRONMENT, used by S3, RDS, ElastiCache,
# Secrets Manager and the log groups. One key rather than five because the
# question it answers -- "who can decrypt this environment's data" -- has one
# answer, and five keys would be five key policies to keep in step.
#
# Customer-managed rather than the AWS-managed default because an AWS-managed
# key has no key policy you can read: "who can decrypt" collapses into "whoever
# has the IAM permission", and the two questions stop being separable.

# THE KEY POLICY IS WRITTEN OUT, NOT DEFAULTED.
#
# This is the argument the block above already makes, finished. A
# customer-managed key was chosen over the AWS-managed default because an
# AWS-managed key "has no key policy you can read: who can decrypt collapses into
# whoever has the IAM permission, and the two questions stop being separable".
#
# Omitting the policy on a customer-managed key re-creates exactly that. AWS
# substitutes a default policy granting the account root full access and
# delegating every decision back to IAM, so the key reads as customer-managed
# and answers the same single question. Stating the policy is what makes the
# choice mean anything.
data "aws_iam_policy_document" "kms" {
  # THE ACCOUNT ROOT KEEPS ADMINISTRATIVE CONTROL. Without this statement the
  # key becomes unmanageable: KMS does not let IAM policies grant access to a
  # key whose own policy does not delegate to the account, so a key policy that
  # omits it can be neither used nor deleted by anybody, ever.
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

  # THE AWS SERVICES THAT ENCRYPT THIS ENVIRONMENT'S DATA AT REST, enumerated.
  # Not a wildcard service principal: this list is the answer to "what can
  # decrypt this environment's data", and it should be readable as a list.
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

    # SCOPED TO THIS ACCOUNT. A service principal with no account condition is
    # the confused-deputy shape: the principal reads as narrow because it is a
    # named AWS service, and it is reachable from any account using that service.
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

# ── Network ──────────────────────────────────────────────────────────────────

module "network" {
  source = "../../modules/network"

  project            = var.project
  environment        = local.environment
  region             = var.region
  cidr_block         = "10.20.0.0/16"
  availability_zones = var.availability_zones

  # Flow logs: the only record of what actually reached what. Every other
  # control in the network module is preventive and leaves no trace of having
  # refused anything.
  kms_key_arn             = aws_kms_key.this.arn
  flow_log_retention_days = 30

  # ONE NAT. Staging is disposable; a per-AZ pair is $64/month buying
  # resilience for an environment whose whole purpose is to be thrown away.
  single_nat_gateway = true

  tags = local.tags
}

# ── Registries ───────────────────────────────────────────────────────────────

module "ecr" {
  source = "../../modules/ecr"

  project     = var.project
  environment = local.environment
  kms_key_arn = aws_kms_key.this.arn

  # Fewer than production: staging images are rebuilt constantly and nobody
  # rolls back to the thirtieth one.
  keep_images = 10

  tags = local.tags
}

# ── Secrets ──────────────────────────────────────────────────────────────────

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

  instance_class        = "db.t4g.small"
  allocated_storage     = 20
  max_allocated_storage = 50
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
  # No replica in staging. In production this is 1, because a Redis failure
  # there is not a cache miss -- it is every Celery task and every
  # working-memory read.
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

  noncurrent_retain_days = 7

  tags = local.tags
}

# ── Traffic: certificate, load balancer, DNS, WAF ────────────────────────────
#
# THE LAYER THE PREVIOUS PHASE DID NOT HAVE. Seven modules stood up a VPC, a
# cluster, a database, a cache, a registry and a secret store, and nothing could
# route a request to any of it. Ordering here is expressed as data dependencies
# rather than as `depends_on`: the certificate must be ISSUED before the HTTPS
# listener exists, and the load balancer must exist before a DNS record can
# alias to it.

module "acm" {
  source = "../../modules/acm"

  project     = var.project
  environment = local.environment

  domain_name    = var.domain_name
  hosted_zone_id = var.hosted_zone_id

  tags = local.tags
}

module "alb" {
  source = "../../modules/alb"

  project     = var.project
  environment = local.environment
  account_id  = var.account_id

  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
  security_group_id = module.network.alb_security_group_id

  # From the VALIDATION resource, not from the certificate. A listener created
  # against a PENDING_VALIDATION certificate is accepted by AWS and then fails
  # the TLS handshake for every visitor.
  certificate_arn = module.acm.certificate_arn

  access_logs_bucket_name = var.access_logs_bucket_name

  # No deletion protection in staging; on in production. Staging is meant to be
  # destroyable, which is most of what it is for.
  enable_deletion_protection = false

  target_groups = {
    api = {
      port = 8000
      # DEEP, NOT A STATIC 200. `/health` resolves a pooled database session AND
      # issues a broker round trip, so a task with a wrong DSN or an unreachable
      # Redis fails this check and the ECS circuit breaker rolls the deploy back.
      # A static 200 would promote that same task.
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

  # THE ENUMERATED UNAUTHENTICATED SURFACE (RBAC §15 and §33).
  #
  # These are the paths behind the public job link a candidate reaches from
  # LinkedIn or a forwarded email, mounted at both API prefixes because the
  # handler object is mounted twice and a link already sitting in somebody's
  # inbox may carry either. The handler returns `PublicJobOut`, which carries no
  # status, no creator, no compensation and no approval trail, and it 404s an
  # unpublished, archived or expired job without revealing which of the three.
  #
  # THAT is what makes RBAC §33 hold, not the fact that the id is a UUID:
  # "Obscurity is NOT authorization", and the projection is the half doing the
  # work. The load balancer contributes the narrower half, which is that these
  # patterns and nothing else reach the API before the application is asked.
  #
  # Adding to this list widens the unauthenticated surface of the product.
  # `backend/tests/test_deploy_secret_hygiene.py` reads it back out of this file
  # so that an addition fails a test rather than only appearing in a rule set.
  public_path_patterns = [
    "/api/v1/jobs/public/*",
    "/api/v2/jobs/public/*",
  ]

  routes = {
    # Everything else under /api is the application's to authorize. It reaches
    # the API target group by routing, and `require_capability` decides whether
    # the caller may actually have it.
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

  hosted_zone_id = var.hosted_zone_id
  hostnames      = [var.domain_name]

  alb_dns_name = module.alb.dns_name
  # The LOAD BALANCER's zone, which is an AWS-owned zone and is NOT
  # `var.hosted_zone_id`. Passing the product's own zone here produces a record
  # that resolves to nothing.
  alb_zone_id = module.alb.zone_id
}

module "waf" {
  source = "../../modules/waf"

  project     = var.project
  environment = local.environment

  # BUILT AND OFF (spec-doc6 §13.2). `enabled = false` creates nothing at all,
  # rather than a permissive web ACL that costs money and proves nothing.
  # Turning it on is one line, and the procedure in `docs/DEPLOY_AWS.md` says to
  # turn it on in `count_only` mode first: the managed rule sets inspect request
  # bodies, and this product's request bodies are resumes, client-written job
  # descriptions and interview answers, which is a corpus no generic rule set
  # was ever tuned against.
  enabled    = false
  count_only = true

  alb_arn     = module.alb.arn
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

  secret_policy_arns  = module.secrets.policy_arns
  s3_policy_arn       = module.s3.access_policy_arn
  ecr_repository_arns = values(module.ecr.repository_arns)

  kms_key_arn        = aws_kms_key.this.arn
  log_retention_days = 14

  # ECS Exec ON in staging, OFF in production. A shell in a container holding
  # real candidate data is a different thing from a shell in one holding seed
  # data, and the difference should be a decision rather than an inheritance.
  enable_execute_command = true

  common_environment = {
    APP_ENV                       = local.environment
    AWS_REGION                    = var.region
    S3_BUCKET                     = module.s3.bucket_name
    EMBEDDING_DIMENSIONS          = "1024"
    RESUME_SIGNED_URL_TTL_SECONDS = "300"
    # The origin the product is actually served on. `jobs.public_job_url` builds
    # the candidate-facing application link from it and the CORS allowlist in
    # `app/main.py` is keyed on it, so a wrong value here is a job link that
    # goes nowhere and a browser that refuses every API call.
    FRONTEND_URL = "https://${var.domain_name}"
  }

  services = {
    api = {
      image         = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      cpu           = 512
      memory        = 1024
      desired_count = 1
      max_count     = 2
      port          = 8000
      health_path   = "/health"
      # REGISTERS THE TASKS WITH THE LOAD BALANCER. Without this the service
      # runs, is attached to no target group, and serves no traffic at all while
      # every dashboard reports it healthy.
      target_group_arn = module.alb.target_group_arns["api"]
      needs_s3         = true
      # The backend writes nothing to disk by design -- resume bytes never
      # persist on the application filesystem -- so this enforces an invariant
      # the code already claims.
      readonly_root = true
      secrets = {
        DATABASE_URL                  = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                     = module.secrets.secret_arns["REDIS_URL"]
        JWT_SECRET                    = module.secrets.secret_arns["JWT_SECRET"]
        ANTHROPIC_API_KEY             = module.secrets.secret_arns["ANTHROPIC_API_KEY"]
        VOYAGE_API_KEY                = module.secrets.secret_arns["VOYAGE_API_KEY"]
        FIREBASE_SERVICE_ACCOUNT_JSON = module.secrets.secret_arns["FIREBASE_SERVICE_ACCOUNT_JSON"]
        RAZORPAY_KEY_SECRET           = module.secrets.secret_arns["RAZORPAY_KEY_SECRET"]
        LLM_KEY_ENCRYPTION_SECRET     = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
    }

    worker = {
      image = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      command = [
        "celery", "-A", "app.workers.celery_app", "worker",
        "--loglevel=info", "--concurrency=2",
      ]
      cpu           = 1024
      memory        = 2048
      desired_count = 1
      max_count     = 3
      needs_s3      = true
      # NOT read-only: resume parsing writes a temp file for pypdf.
      readonly_root = false
      # NO FIREBASE KEY. A background task never authenticates a browser
      # session, so it has no business being able to read the service account.
      secrets = {
        DATABASE_URL              = module.secrets.secret_arns["DATABASE_URL"]
        REDIS_URL                 = module.secrets.secret_arns["REDIS_URL"]
        ANTHROPIC_API_KEY         = module.secrets.secret_arns["ANTHROPIC_API_KEY"]
        VOYAGE_API_KEY            = module.secrets.secret_arns["VOYAGE_API_KEY"]
        SMTP_PASSWORD             = module.secrets.secret_arns["SMTP_PASSWORD"]
        TAVILY_API_KEY            = module.secrets.secret_arns["TAVILY_API_KEY"]
        MSG91_API_KEY             = module.secrets.secret_arns["MSG91_API_KEY"]
        LLM_KEY_ENCRYPTION_SECRET = module.secrets.secret_arns["LLM_KEY_ENCRYPTION_SECRET"]
      }
    }

    beat = {
      image = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      command = [
        "celery", "-A", "app.workers.celery_app", "beat", "--loglevel=info",
      ]
      cpu           = 256
      memory        = 512
      desired_count = 1
      max_count     = 1
      readonly_root = false
      # EXACTLY ONE, AND ZERO DURING A DEPLOY. Two schedulers double every
      # scheduled task, which here means two reconciliation sweeps and two sets
      # of reminder emails. 0/100 stops the old one before the new one starts.
      min_healthy_percent = 0
      max_percent         = 100
      # The broker and nothing else. A scheduler that could read a model
      # credential is a scheduler that could spend money.
      secrets = {
        REDIS_URL = module.secrets.secret_arns["REDIS_URL"]
      }
    }

    frontend = {
      image            = "${module.ecr.repository_urls["frontend"]}:${var.image_tag}"
      cpu              = 512
      memory           = 1024
      desired_count    = 1
      max_count        = 2
      port             = 3000
      health_path      = "/"
      target_group_arn = module.alb.target_group_arns["frontend"]
      readonly_root    = false # Next.js writes its own cache
      # The frontend holds NO secrets. The Razorpay key id it needs is public
      # and is fetched at runtime from GET /billing/config, which is why it was
      # never a NEXT_PUBLIC_ build variable.
      secrets = {}
    }
  }

  tags = local.tags
}
