# Lambda functions: the short half of this platform's background work.
#
# WHAT RUNS HERE AND WHY
# ----------------------
# Four functions, and they are not four of a kind:
#
#   readypick-task-worker       every short task the product has -- delivery,
#                               resume parsing, the reconciliation sweeps.
#                               Invoked asynchronously by the API and by
#                               EventBridge Scheduler.
#   readypick-jd-gen            the job description writer, invoked
#   readypick-company-profile   synchronously by a request handler that is
#                               already waiting for the draft.
#   readypick-assessment-trigger  calls ecs:RunTask and returns.
#
# The first three run the BACKEND IMAGE with a different handler, because they
# import the application: the model router, the prompt registry, a database
# session. Building a separate artifact carrying the same code would let an
# agent and the API disagree about what a prompt says or what a grade means.
# The fourth is a zip of one file, and it must stay that way, because it is the
# only thing in this account that holds iam:PassRole over the agent's task role.
#
# WHY EVERY FUNCTION HAS ITS OWN ROLE
# -----------------------------------
# The same argument the `ecs` module makes for task roles. A shared execution
# role means the trigger can read the model credentials and the agents can run
# ECS tasks, and neither needs to. The GCP-phase finding was one runtime
# identity holding every secret: nothing was misconfigured, the grant was
# simply wider than the need, and a wildcard looks identical whether it is
# over-broad or exactly right.

locals {
  name = "${var.project}-${var.environment}"

  # A function's own name, without the environment suffix. The application
  # addresses these by literal name (`workers/dispatch.WORKER_FUNCTION`), so
  # the names are fixed rather than derived: a dispatch that targeted
  # "readypick-pilot-task-worker" would have to be a deploy-time substitution
  # into application code, and code that reads its own infrastructure's naming
  # convention is code that breaks when the convention changes.
  function_names = { for name, fn in var.functions : name => "${var.project}-${name}" }

  build_dir = coalesce(var.build_dir, "${path.root}/.terraform-build")

  zip_functions = { for name, fn in var.functions : name => fn if fn.package == "zip" }

  runtask_functions = {
    for name, fn in var.functions : name => fn if length(fn.run_task_role_arns) > 0
  }
  # KEYED ON A LITERAL, not on an ARN. `secret_policy_key` is a static string
  # in the composition, so this map's key set is known at plan time; a filter on
  # the ARN itself would be a for_each Terraform cannot evaluate until apply.
  secret_functions = {
    for name, fn in var.functions :
    name => fn.secret_policy_key if fn.secret_policy_key != null
  }

  function_environment = {
    for name, fn in var.functions : name => merge(
      fn.environment,
      length(fn.secrets) == 0 ? {} : { READYPICK_SECRETS = jsonencode(fn.secrets) },
    )
  }
}

# ── Packaging ────────────────────────────────────────────────────────────────
#
# `archive_file` rather than a build step in CI, for the zip function only. The
# source is a single file with no third-party dependency beyond boto3, which the
# Lambda runtime already provides, so there is nothing to install and nothing
# for a build to get wrong. A function that needed `pip install` would be an
# image, like the other three.

data "archive_file" "zip" {
  for_each = local.zip_functions

  type        = "zip"
  source_dir  = each.value.source_dir
  output_path = "${local.build_dir}/${each.key}.zip"
}

# ── Logs ─────────────────────────────────────────────────────────────────────
#
# Created HERE rather than left to the service. Lambda creates its own log group
# on first invocation with retention set to "never expire", so a group left
# implicit accumulates for ever and the retention this module sets would apply
# to nothing.

resource "aws_cloudwatch_log_group" "this" {
  for_each = var.functions

  name              = "/aws/lambda/${local.function_names[each.key]}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = merge(var.tags, { Function = each.key })
}

# ── Execution roles ──────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  for_each = var.functions

  name               = "${local.function_names[each.key]}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = merge(var.tags, { Function = each.key })
}

# Logs, scoped to this function's own group. Not the managed
# AWSLambdaBasicExecutionRole, which grants logs:* on every group in the
# account: a function that can write to another function's log stream can
# forge another function's record of what it did.
data "aws_iam_policy_document" "logs" {
  for_each = var.functions

  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this[each.key].arn}:*"]
  }
}

resource "aws_iam_role_policy" "logs" {
  for_each = var.functions

  name   = "logs"
  role   = aws_iam_role.this[each.key].id
  policy = data.aws_iam_policy_document.logs[each.key].json
}

# The VPC attachment. A function with an ENI needs to create and delete one,
# and these four actions are the whole of what the managed
# AWSLambdaVPCAccessExecutionRole grants. They are resource-"*" because an ENI
# does not exist yet when the permission is evaluated; the condition below is
# what keeps it from being a general licence to attach interfaces anywhere.
data "aws_iam_policy_document" "vpc" {
  for_each = { for name, fn in var.functions : name => fn if fn.in_vpc }

  statement {
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "vpc" {
  for_each = { for name, fn in var.functions : name => fn if fn.in_vpc }

  name   = "vpc-access"
  role   = aws_iam_role.this[each.key].id
  policy = data.aws_iam_policy_document.vpc[each.key].json
}

# Secrets, through the policy the `secrets` module already built for this
# consumer: enumerated ARNs, never a prefix, plus the scoped kms:Decrypt that
# reading them requires. Attached rather than rebuilt, because a second policy
# saying almost the same thing is a second thing to keep in step, and the one
# that drifts is the one nobody is testing.
resource "aws_iam_role_policy_attachment" "secrets" {
  for_each = local.secret_functions

  role       = aws_iam_role.this[each.key].name
  policy_arn = var.secret_policy_arns[each.value]
}

# ecs:RunTask and PassRole, for exactly one function.
#
# THREE THINGS ARE NAMED, and each closes a different hole:
#
#   the task definitions  RunTask's own resource. Without it the function can
#                         start ANY task definition in the account, which with
#                         PassRole below is a general remote-code primitive.
#                         The ARN carries a revision wildcard so the grant
#                         survives a deploy that registers a new revision;
#                         pinning one revision produces a trigger that stops
#                         working on the next image push with an AccessDenied
#                         that reads like a broken role.
#   the cluster           a condition, because a task definition ARN does not
#                         say where the task runs.
#   the roles             PassRole on "*" lets anything run code as anything.
#
# There is no ecs:DescribeTasks grant, deliberately. The handler reads the task
# ARN out of RunTask's own response and returns; it never polls. An unused
# grant is one nobody notices going stale.
data "aws_iam_policy_document" "run_task" {
  for_each = local.runtask_functions

  statement {
    sid       = "RunOnlyTheAgentsOwnTaskDefinitions"
    effect    = "Allow"
    actions   = ["ecs:RunTask"]
    resources = each.value.run_task_task_definition_arns
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [each.value.run_task_cluster_arn]
    }
  }

  statement {
    sid       = "PassOnlyTheAgentsOwnRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = each.value.run_task_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "run_task" {
  for_each = local.runtask_functions

  name   = "run-task"
  role   = aws_iam_role.this[each.key].id
  policy = data.aws_iam_policy_document.run_task[each.key].json
}

# Publishing a permanently failed asynchronous invocation.
data "aws_iam_policy_document" "failure_destination" {
  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [var.failure_topic_arn]
  }
}

resource "aws_iam_role_policy" "failure_destination" {
  for_each = var.functions

  name   = "async-failure-destination"
  role   = aws_iam_role.this[each.key].id
  policy = data.aws_iam_policy_document.failure_destination.json
}

# ── Functions ────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "this" {
  for_each = var.functions

  function_name = local.function_names[each.key]
  description   = each.value.description
  role          = aws_iam_role.this[each.key].arn
  memory_size   = each.value.memory_mb
  timeout       = each.value.timeout_seconds

  package_type = each.value.package == "image" ? "Image" : "Zip"

  # STATED, not defaulted. Lambda defaults to x86_64 and this deployment's
  # images are built for the `ecs` module's ARM64. A mismatch is accepted at
  # create and fails at cold start with an exec format error.
  architectures = [var.architecture]

  # An image function: the backend image, with the handler passed as the
  # container command. One image serves three functions.
  #
  # THE ENTRY POINT IS OVERRIDDEN TOO, and it has to be. The image's own
  # ENTRYPOINT is `docker-entrypoint.sh`, which reads its FIRST argument as a
  # role (api, agent, lambda, migrate). Lambda sets only the COMMAND, so the
  # handler arrived where the role was expected and the container died with
  #
  #   /app/docker-entrypoint.sh: exec: app.workers...lambda_handler: not found
  #
  # exit status 127, before the runtime interface client ever started. Naming
  # the `lambda` role here puts the handler back in the argument position it
  # belongs in, and keeps every role of this image behind the same entry point.
  image_uri = each.value.package == "image" ? each.value.image_uri : null
  dynamic "image_config" {
    for_each = each.value.package == "image" ? [each.value.handler] : []
    content {
      entry_point = ["/app/docker-entrypoint.sh", "lambda"]
      command     = [image_config.value]
    }
  }

  # A zip function: the archive and its handler.
  filename         = each.value.package == "zip" ? data.archive_file.zip[each.key].output_path : null
  source_code_hash = each.value.package == "zip" ? data.archive_file.zip[each.key].output_base64sha256 : null
  handler          = each.value.package == "zip" ? each.value.handler : null
  runtime          = each.value.package == "zip" ? each.value.runtime : null

  reserved_concurrent_executions = coalesce(each.value.reserved_concurrency, -1)

  dynamic "vpc_config" {
    for_each = each.value.in_vpc ? [1] : []
    content {
      subnet_ids         = var.vpc_subnet_ids
      security_group_ids = var.security_group_ids
    }
  }

  # `READYPICK_SECRETS` carries the ARNs, never the values: it is the list the
  # function fetches at cold start, and ARNs are not secret. See
  # `app.workers.secrets_bootstrap` for why the function fetches them itself
  # rather than having them injected, which is what ECS does and Lambda cannot.
  dynamic "environment" {
    for_each = length(local.function_environment[each.key]) > 0 ? [each.key] : []
    content {
      variables = local.function_environment[environment.value]
    }
  }

  # X-Ray is off. It samples, costs per trace, and this platform's diagnostics
  # are structured log lines with identifiers and counts in them. Turning it on
  # is an owner decision, not a default.
  tracing_config {
    mode = "PassThrough"
  }

  depends_on = [
    aws_iam_role_policy.logs,
    aws_cloudwatch_log_group.this,
  ]

  lifecycle {
    # CI publishes new code; Terraform owns the shape. Same arrangement the ECS
    # services use, and for the same reason: a pipeline that deploys and a
    # Terraform that reverts would fight on every apply.
    ignore_changes = [image_uri, filename, source_code_hash]
  }

  tags = merge(var.tags, { Function = each.key })
}

# ── Asynchronous invocation policy ───────────────────────────────────────────
#
# ZERO platform retries, on purpose, and this is the decision most worth
# stating. Lambda's default is two, which stacks on top of the retry loop
# inside `app.workers.runtime`: three in-process attempts under two platform
# attempts is nine sends of one email. One owner of retry, and it is the one
# that can tell a transient SMTP failure from a permanent one.
#
# What replaces the platform retry is the failure destination: an invocation
# that fails is published, once, to a topic somebody is subscribed to. A drop
# with only a metric behind it is how work disappears quietly.

resource "aws_lambda_function_event_invoke_config" "this" {
  for_each = var.functions

  function_name          = aws_lambda_function.this[each.key].function_name
  maximum_retry_attempts = each.value.async_retry_attempts
  # Six hours is the default and is deliberately kept: an event that has been
  # sitting for six hours is one the reconciliation sweeps have already
  # repaired, and a shorter window would drop it before they ran.
  maximum_event_age_in_seconds = 21600

  destination_config {
    on_failure {
      destination = var.failure_topic_arn
    }
  }
}
