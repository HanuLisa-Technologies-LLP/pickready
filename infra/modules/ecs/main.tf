/**
 * ECS Fargate: the cluster, the services, and the per-service IAM roles.
 *
 * FARGATE, because spec-doc5 §D.3 names it as "the direct equivalent of the
 * Cloud Run model this platform is migrating from" -- and it is: a container,
 * a request, no host to patch. The one thing it is not equivalent on is
 * scale-to-zero, and that is called out in the autoscaling block below rather
 * than discovered later on a bill.
 *
 * ONE TASK ROLE PER SERVICE. THIS IS THE POINT OF THE MODULE.
 * ---------------------------------------------------------------
 * spec-doc5 §D.4: "IAM policies must be scoped per-service, not a shared broad
 * role -- the DATABASE_URL exposure finding from the GCP phase (a composed DSN
 * readable via a broad permission) is the exact class of mistake to design out
 * here from the start rather than harden later."
 *
 * So `var.services` is a map, and each entry gets:
 *
 *   its own TASK ROLE       what the running code can do
 *   its own EXECUTION ROLE  what the ECS agent can do on its behalf
 *   its own secret policy   from the `secrets` module, enumerated per service
 *
 * The two roles are separate because they are used at different times by
 * different principals, and conflating them is the usual mistake: the execution
 * role pulls the image and fetches secrets to INJECT, before the container
 * starts; the task role is what the application's own AWS SDK calls use. A
 * single role means the application can read every secret the platform injects,
 * whether or not it was given one.
 *
 * SECRETS ARE INJECTED BY ECS, NOT COMPOSED BY THE APPLICATION.
 * The `secrets` block on the container definition makes ECS fetch the value and
 * set the environment variable. That is what "mounted rather than composed into
 * a loggable env var where the platform allows it" means here -- the value never
 * passes through a shell, a startup script or a log line on its way in.
 *
 * THE WORKER AND BEAT RUN THE SAME IMAGE WITH A DIFFERENT COMMAND, which is why
 * `command` is per-service and the image is not. They are separate SERVICES
 * rather than separate containers in one task because they scale differently:
 * the worker scales on queue depth and beat must be exactly one, forever.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
  }
}

locals {
  name = "${var.project}-${var.environment}"
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    # Per-task CloudWatch metrics. Off by default and worth the small cost:
    # without it, "is the worker actually saturated" is a question with no data
    # behind it, and the answer decides whether to scale or to fix.
    name  = "containerInsights"
    value = var.environment == "production" ? "enhanced" : "disabled"
  }

  tags = merge(var.tags, { Name = local.name })
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

resource "aws_cloudwatch_log_group" "this" {
  for_each = var.services

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, { Service = each.key })
}

# ── The execution role: what the ECS AGENT may do, before the container runs ─

data "aws_iam_policy_document" "execution_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  for_each = var.services

  name               = "${local.name}-${each.key}-execution"
  description        = "ECS agent: pull the image, fetch this service's secrets, write its logs. NOT what the application code runs as."
  assume_role_policy = data.aws_iam_policy_document.execution_assume.json

  tags = merge(var.tags, { Service = each.key, RoleKind = "execution" })
}

data "aws_iam_policy_document" "execution" {
  for_each = var.services

  # SCOPED TO THIS ENVIRONMENT'S REPOSITORIES, not `ecr:*`. The managed
  # `AmazonECSTaskExecutionRolePolicy` grants pull on every repository in the
  # account, which is how a staging task ends up able to pull a production
  # image.
  statement {
    sid       = "AuthToTheRegistry"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # This action has no resource form. It grants a token, not access.
  }

  statement {
    sid    = "PullOnlyThisEnvironmentsImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = var.ecr_repository_arns
  }

  statement {
    sid    = "WriteThisServicesLogsAndNoOthers"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.this[each.key].arn}:*"]
  }

  statement {
    sid    = "DecryptTheLogGroup"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution" {
  for_each = var.services

  name   = "${local.name}-${each.key}-execution"
  role   = aws_iam_role.execution[each.key].id
  policy = data.aws_iam_policy_document.execution[each.key].json
}

# The per-service secret policy from the `secrets` module. Attached to the
# EXECUTION role, because ECS injects the value before the container starts --
# the application never calls Secrets Manager itself.
#
# ONLY THE SERVICES THAT ACTUALLY MOUNT A SECRET GET AN ATTACHMENT.
#
# `frontend` declares `secrets = {}` on purpose: the Razorpay key id it needs is
# public and is fetched at runtime from `GET /billing/config`, which is why it
# was never a `NEXT_PUBLIC_` build variable. Attaching a Secrets Manager policy
# to it anyway would be a grant nothing uses, which is the same shape as the
# GCP-phase finding this module exists to design out.
#
# Iterating over every service was also a latent apply-time failure rather than
# a mere over-grant: `secret_policy_arns` has no `frontend` key, so
# `var.secret_policy_arns["frontend"]` raised `Invalid index`. `terraform
# validate` cannot see it, because it cannot evaluate a `for_each` key against a
# map's contents; the offline plan added in spec-doc6 §13.3 is what found it.
resource "aws_iam_role_policy_attachment" "execution_secrets" {
  for_each = { for name, service in var.services : name => service if length(service.secrets) > 0 }

  role = aws_iam_role.execution[each.key].name
  # Indexed rather than looked up with a default: a service that DOES mount
  # secrets and has no scoped policy must FAIL THE PLAN rather than run with
  # none. The precondition below says so in words rather than as `Invalid
  # index`, because the index error names the map and not the mistake.
  policy_arn = var.secret_policy_arns[each.key]

  lifecycle {
    precondition {
      condition     = contains(keys(var.secret_policy_arns), each.key)
      error_message = "The service mounts secrets but has no entry in the `secrets` module's `service_secrets`. Add its exact secret list there; do not widen another service's policy to cover it."
    }
  }
}

# ── The task role: what the APPLICATION CODE may do ─────────────────────────

resource "aws_iam_role" "task" {
  for_each = var.services

  name               = "${local.name}-${each.key}-task"
  description        = "What ${each.key}'s own code can do. Separate from the execution role: conflating them lets the application read every secret the platform injects."
  assume_role_policy = data.aws_iam_policy_document.execution_assume.json

  tags = merge(var.tags, { Service = each.key, RoleKind = "task" })
}

# S3 access goes on the TASK role, because it is the application's own boto3
# client making the call. Only the services that actually store files get it --
# `beat` schedules and never touches an object.
resource "aws_iam_role_policy_attachment" "task_s3" {
  for_each = { for name, service in var.services : name => service if service.needs_s3 }

  role       = aws_iam_role.task[each.key].name
  policy_arn = var.s3_policy_arn
}

# ECS Exec, for an operator opening a shell in a running task. PRODUCTION ONLY
# BY EXPLICIT OPT-IN, and off by default: a shell in a container holding
# candidate data is a real capability, and it should be a decision rather than
# an inheritance.
data "aws_iam_policy_document" "exec" {
  statement {
    effect = "Allow"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"] # SSM messaging has no resource form.
  }
}

resource "aws_iam_role_policy" "exec" {
  for_each = var.enable_execute_command ? var.services : {}

  name   = "${local.name}-${each.key}-exec"
  role   = aws_iam_role.task[each.key].id
  policy = data.aws_iam_policy_document.exec.json
}

# ── Task definitions ─────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "this" {
  for_each = var.services

  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name = each.key
      # BY DIGEST WHERE ONE IS SUPPLIED, otherwise by SHA tag. spec-doc5 §D.6
      # asks for verification "by image digest, not by CI exit code"; pinning
      # the digest in the task definition is what makes the verification
      # meaningful, because the running task cannot then be a different image
      # that happens to share a tag.
      image     = each.value.image
      command   = each.value.command
      essential = true

      portMappings = each.value.port == null ? [] : [
        {
          containerPort = each.value.port
          protocol      = "tcp"
        }
      ]

      # Plain values only. Anything sensitive is in `secrets` below, which ECS
      # fetches and injects -- so the value never passes through a shell, a
      # startup script or a log line.
      environment = [
        for key, value in merge(var.common_environment, each.value.environment) :
        { name = key, value = tostring(value) }
      ]

      secrets = [
        for key, arn in each.value.secrets : { name = key, valueFrom = arn }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this[each.key].name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = each.key
        }
      }

      # A HEALTH CHECK ONLY WHERE THERE IS SOMETHING TO CHECK. A worker has no
      # HTTP endpoint, and a health check that shells out to `celery inspect
      # ping` would report unhealthy during a long task -- restarting the
      # worker mid-assessment, which is the opposite of what a health check is
      # for.
      healthCheck = each.value.health_path == null ? null : {
        command     = ["CMD-SHELL", "curl -fsS http://localhost:${each.value.port}${each.value.health_path} || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      # Root filesystem read-only where the workload allows it. The backend
      # writes nothing to disk by design -- resume bytes never persist on the
      # application filesystem -- so this is enforcing an invariant the code
      # already claims rather than adding a constraint.
      readonlyRootFilesystem = each.value.readonly_root
      linuxParameters = {
        initProcessEnabled = true # reaps zombies; Celery forks
      }
    }
  ])

  tags = merge(var.tags, { Service = each.key })
}

# ── Services ─────────────────────────────────────────────────────────────────

resource "aws_ecs_service" "this" {
  for_each = var.services

  name            = "${local.name}-${each.key}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this[each.key].arn
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [var.ecs_security_group_id]
    # PRIVATE SUBNETS, so no public IP. Egress is through NAT, which is why the
    # network module has one.
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.value.target_group_arn == null ? [] : [each.value.target_group_arn]
    content {
      target_group_arn = load_balancer.value
      container_name   = each.key
      container_port   = each.value.port
    }
  }

  # A rolling deploy with a circuit breaker. `rollback = true` is what makes
  # this different from watching it fail: a deployment whose tasks will not
  # become healthy is reverted automatically rather than leaving the service
  # half-migrated while somebody notices.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # 100/200 gives a genuine zero-downtime rolling deploy: the new tasks come up
  # alongside the old ones and nothing is drained until they are healthy.
  # `beat` overrides this to 0/100, because two schedulers running at once
  # would double every scheduled task.
  deployment_minimum_healthy_percent = each.value.min_healthy_percent
  deployment_maximum_percent         = each.value.max_percent

  # A new service's tasks need time to pass a health check before the LB starts
  # counting failures against them.
  health_check_grace_period_seconds = each.value.target_group_arn == null ? null : 90

  propagate_tags = "SERVICE"
  tags           = merge(var.tags, { Service = each.key })

  lifecycle {
    # CI updates the task definition with a new image; Terraform must not then
    # revert it to whatever the last apply pinned. This is the standard
    # arrangement when a pipeline deploys and Terraform owns the shape.
    ignore_changes = [task_definition, desired_count]
  }
}

# ── Autoscaling ──────────────────────────────────────────────────────────────
#
# FARGATE DOES NOT SCALE TO ZERO, and that is the one place it is not equivalent
# to Cloud Run. `min_capacity` is at least 1 for every service, so this platform
# has a floor cost Cloud Run did not -- stated here rather than discovered on a
# bill. A service that genuinely should idle at zero belongs on Lambda or on a
# scheduled task, not on a Fargate service with a min of 0 that cannot exist.

resource "aws_appautoscaling_target" "this" {
  for_each = { for name, service in var.services : name => service if service.max_count > service.desired_count }

  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.this[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = each.value.desired_count
  max_capacity       = each.value.max_count
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = aws_appautoscaling_target.this

  name               = "${local.name}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension
  service_namespace  = each.value.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 65

    # SCALE OUT FAST, SCALE IN SLOWLY. The asymmetry is deliberate: scaling out
    # late costs latency a candidate feels mid-assessment, while scaling in
    # early costs a few minutes of an instance nobody is using.
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}
