/**
 * ReadyPick PRODUCTION.
 *
 * Deliberately the same SHAPE as `../staging`, with every resilience and scale
 * decision flipped. Reading the two side by side should produce a short, boring
 * diff -- and if it ever produces a long one, the environments have drifted in
 * structure rather than in size, which is the thing that makes a staging test
 * stop predicting anything about production.
 *
 * WHAT IS DIFFERENT, AND WHY EACH ONE
 * -------------------------------------
 *   NAT per AZ            a single NAT is one AZ failure from every task
 *                         losing egress, which means losing the model provider
 *   RDS Multi-AZ          a failover instead of a restore
 *   Redis replica         Redis here is the Celery broker and the working
 *                         memory layer, not a cache: losing it is a queue
 *                         nobody is draining
 *   deletion protection   on, in the rds module, keyed off `environment`
 *   ECS Exec OFF          a shell in a container holding real candidate data
 *                         is a different thing from one holding seed data
 *   Container Insights    on; "is the worker saturated" needs data behind it
 *   30-day backups        and a final snapshot on destroy
 *
 * NOTHING IN THIS DIRECTORY MAY BE APPLIED IN THIS PHASE. spec-doc5 §D.1 and
 * the §D acceptance list make that a pass/fail criterion in the opposite
 * direction from usual: running `terraform apply` here is a failure of scope,
 * not an accomplishment. `infra/environments/README.md` says the same thing at
 * more length, and the CI pipeline stops at a required-reviewer gate before it.
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
  #   key          = "production/terraform.tfstate"
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
  environment = "production"

  # THE INTERNAL SERVICE NAMESPACE, OWNED HERE AND NOWHERE ELSE.
  #
  # The analysis service sits behind no load balancer and has no public path,
  # so a Cloud Map name is the only way the api and the worker can address it.
  # The namespace is built here rather than inside the ecs module because both
  # sides of the arrangement need the same string: the module registers the
  # service under it, and the two callers get the URL below in their
  # environment. Two places deriving one hostname from the same parts is two
  # places that can drift.
  internal_namespace = "${var.project}-${local.environment}.internal"

  # 8100 matches the analysis service's container port and the internal port
  # the network module opens from the task security group to itself. Plain
  # http: the hop is task to task inside a private subnet, the same reasoning
  # the load balancer's target groups already follow.
  analysis_service_url = "http://analysis.${local.internal_namespace}:8100"

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
  cidr_block         = "10.30.0.0/16"
  availability_zones = var.availability_zones

  # Flow logs: the only record of what actually reached what. Every other
  # control in the network module is preventive and leaves no trace of having
  # refused anything.
  kms_key_arn             = aws_kms_key.this.arn
  flow_log_retention_days = 90

  # ONE NAT PER AZ. A single NAT is one AZ failure away from every task losing
  # egress, which for this platform means losing the model provider -- so every
  # assessment degrades at once.
  single_nat_gateway = false

  tags = local.tags
}

# ── Registries ───────────────────────────────────────────────────────────────

module "ecr" {
  source = "../../modules/ecr"

  project     = var.project
  environment = local.environment
  kms_key_arn = aws_kms_key.this.arn

  # More than staging: a production rollback may need to reach back further
  # than the last few deploys.
  keep_images = 30

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

  instance_class        = "db.m7g.large"
  allocated_storage     = 100
  max_allocated_storage = 500
  # A failover instead of a restore.
  multi_az              = true
  backup_retention_days = 30

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

  node_type = "cache.t4g.small"
  # ONE REPLICA, WHICH IS ALSO WHAT ENABLES AUTOMATIC FAILOVER. Redis here is
  # the Celery broker and specdoc4's working-memory layer, not a cache: losing
  # it is a queue nobody is draining and a candidate mid-assessment whose next
  # question never arrives.
  replica_count = 1

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

  noncurrent_retain_days = 90

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

  # ON. A destroyed load balancer takes the DNS alias target with it, so the
  # outage outlasts the mistake by however long a replacement takes to create
  # and a resolver takes to forget the old answer.
  enable_deletion_protection = true

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

  # The Cloud Map namespace resolves inside this VPC and nowhere else.
  vpc_id              = module.network.vpc_id
  discovery_namespace = local.internal_namespace

  secret_policy_arns  = module.secrets.policy_arns
  s3_policy_arn       = module.s3.access_policy_arn
  ecr_repository_arns = values(module.ecr.repository_arns)

  kms_key_arn        = aws_kms_key.this.arn
  log_retention_days = 90

  # OFF. A shell in a container holding real candidate data is a real
  # capability. Turning it on for an incident is a deliberate, reviewed change
  # rather than something inherited from staging.
  enable_execute_command = false

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
      cpu           = 1024
      memory        = 2048
      desired_count = 2
      max_count     = 8
      port          = 8000
      health_path   = "/health"
      # REGISTERS THE TASKS WITH THE LOAD BALANCER. Without this the service
      # runs, is attached to no target group, and serves no traffic at all while
      # every dashboard reports it healthy.
      target_group_arn = module.alb.target_group_arns["api"]
      needs_s3         = true
      # Where the proctoring pipeline posts a fifteen-second audio chunk for a
      # speaker count. NOT a credential, so it is a plain environment entry:
      # it is an internal hostname that resolves in this VPC only. An empty
      # value would mean audio analysis is unavailable, which the report states
      # plainly rather than reading as "no second voice was heard".
      environment = {
        PROCTORING_ANALYSIS_SERVICE_URL = local.analysis_service_url
      }
      # The backend writes nothing to disk by design -- resume bytes never
      # persist on the application filesystem -- so this enforces an invariant
      # the code already claims.
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

    worker = {
      image = "${module.ecr.repository_urls["backend"]}:${var.image_tag}"
      command = [
        "celery", "-A", "app.workers.celery_app", "worker",
        "--loglevel=info", "--concurrency=4",
      ]
      cpu           = 2048
      memory        = 4096
      desired_count = 2
      max_count     = 10
      needs_s3      = true
      # NOT read-only: resume parsing writes a temp file for pypdf.
      readonly_root = false
      # The worker runs the proctoring reconciliation sweeps, which re-read a
      # session, so it reaches the analysis service on the same name the api
      # does.
      environment = {
        PROCTORING_ANALYSIS_SERVICE_URL = local.analysis_service_url
      }
      # NO FIREBASE KEY. A background task never authenticates a browser
      # session, so it has no business being able to read the service account.
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
      desired_count    = 2
      max_count        = 6
      port             = 3000
      health_path      = "/"
      target_group_arn = module.alb.target_group_arns["frontend"]
      readonly_root    = false # Next.js writes its own cache
      # The frontend holds NO secrets. The Razorpay key id it needs is public
      # and is fetched at runtime from GET /billing/config, which is why it was
      # never a NEXT_PUBLIC_ build variable.
      secrets = {}
    }

    # THE PROCTORING ANALYSIS SERVICE (analysis-service/).
    #
    # Speaker counting over a fifteen-second audio chunk, and an AI-text
    # estimate that ships disabled. Its own image and its own service, for
    # three reasons worth keeping separate:
    #
    #   IT CARRIES THE MODEL LIBRARIES. torch, pyannote.audio and transformers
    #   are several hundred megabytes the api and the worker never load, and
    #   an inference call that pinned a request worker would cost a candidate
    #   mid-assessment their next question.
    #
    #   IT HANDLES THE ONE MEDIA TYPE THAT LEAVES THE BROWSER. The chunk is
    #   decoded from an in-memory buffer and destroyed; nothing in the image
    #   writes audio anywhere, and the service is deliberately not next to code
    #   that persists files.
    #
    #   IT IS NOT PUBLIC. No target group, no listener rule, no path through
    #   the load balancer. It is reachable at its Cloud Map name from tasks in
    #   the ECS security group and from nothing else.
    analysis = {
      image = "${module.ecr.repository_urls["analysis"]}:${var.image_tag}"
      # CPU inference on a fifteen-second chunk. The memory figure is the
      # binding one: the diarization pipeline holds three models resident.
      cpu           = 2048
      memory        = 8192
      desired_count = 2
      max_count     = 4
      port          = 8100
      # The body says which component loaded. A container with no token is up
      # and honest rather than restarting forever over a decision nobody made
      # by mistake, so the check asks whether the process serves.
      health_path = "/health"
      # NO TARGET GROUP. Reached by name, inside the VPC, on 8100.
      discoverable = true
      # READ-ONLY ROOT. The service writes nothing to disk by design, and the
      # one exception is the import-time caches torch and matplotlib insist on:
      # the image points MPLCONFIGDIR, TORCH_HOME and XDG_CACHE_HOME at /tmp,
      # and the mount below is the only writable path in the container.
      readonly_root  = true
      writable_paths = ["/tmp"]
      # The Hugging Face token, and nothing else. The diarization models are
      # gated: the licence is accepted per account, so the service refuses to
      # load them without a token even though the weights are baked into the
      # image. It holds no DSN, no broker and no model-provider key, because
      # all it is handed is audio and all it answers is a speaker count.
      secrets = {
        HUGGINGFACE_TOKEN = module.secrets.secret_arns["HUGGINGFACE_TOKEN"]
      }
    }
  }

  tags = local.tags
}
