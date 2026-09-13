variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "account_id" {
  description = <<-EOT
    The AWS account these functions live in.

    PASSED IN RATHER THAN LOOKED UP. `data "aws_caller_identity"` calls STS,
    which means the OFFLINE PLAN cannot evaluate it: `infra/plan-offline.sh`
    runs against account 000000000000 in region xx-plan-1 with dummy
    credentials and has never contacted AWS, so an STS call there fails DNS
    resolution and takes the whole plan with it. The plan is the only pre-apply
    check this repository has, and a check that cannot run is a check nobody
    reads. `scheduler` already takes the account id this way for the same
    reason.
  EOT
  type        = string
}

variable "region" {
  type = string
}

variable "functions" {
  description = <<-EOT
    One entry per function, and EACH ENTRY GETS ITS OWN EXECUTION ROLE. Same
    argument the `ecs` module makes for task roles: a shared role means every
    function can read every secret the platform injects, and a wildcard looks
    identical whether it is over-broad or exactly right.

    `package` is "image" or "zip".

      image  the backend container, invoked through the Lambda runtime
             interface client. `image_uri` and `handler` are both required:
             the handler is passed as the container command, which is what
             lets ONE image serve the task worker and both agent functions
             without a per-function build.

      zip    a directory of plain Python, archived by this module.
             `source_dir` and `handler` are required. Used for exactly one
             function, the ECS trigger, which imports boto3 and nothing else
             and must stay small enough for a reviewer to read in full,
             because it is the thing that holds iam:PassRole.

    `secrets` is `{ENV_NAME -> secret ARN}`, the SAME shape an ECS container
    definition takes. ECS injects those itself; Lambda has no equivalent and
    offers only its own environment variables, which are readable in the
    console and in `GetFunctionConfiguration`, so a credential must never go
    there. The map is passed as ARNs and the function fetches the values at
    cold start with the policy below (`app.workers.secrets_bootstrap`).

    Getting this wrong is quiet: `Settings` falls back to its defaults and the
    function fails against `127.0.0.1:5432` inside a VPC where nothing listens
    on localhost.

    `secret_policy_key` names this function's entry in `secret_policy_arns`,
    the map of policies the `secrets` module built. A KEY rather than an ARN,
    because an ARN is not known until apply and a `for_each` cannot be keyed on
    a value Terraform has not computed yet. The `ecs` module makes the same
    trade for the same reason.

    Null is a real and deliberate answer: the trigger reads no secret, which is
    why it has no entry in that module's map at all.

    `reserved_concurrency` is a CEILING, not a reservation of throughput. It is
    set where a runaway would cost real money or exhaust a downstream (the
    database's connection limit, a model provider's rate limit).

    Null means unreserved: the function shares the account pool. That is a real
    answer rather than an omission on an account whose total concurrency is
    still at the new-account default of 10, where AWS refuses any reservation
    that would leave fewer than 10 unreserved and the account cap is therefore
    a harder ceiling than anything this could ask for.
  EOT
  type = map(object({
    package              = string
    description          = string
    memory_mb            = number
    timeout_seconds      = number
    image_uri            = optional(string, null)
    source_dir           = optional(string, null)
    handler              = optional(string, null)
    runtime              = optional(string, "python3.12")
    environment          = optional(map(string), {})
    secrets              = optional(map(string), {})
    secret_policy_key    = optional(string, null)
    reserved_concurrency = optional(number, null)
    in_vpc               = optional(bool, true)
    # Asynchronous invocations only. Zero on every function here, because the
    # retry loop lives inside the task runtime and two stacked retry mechanisms
    # multiply: three in-process attempts under two platform attempts is nine
    # sends of one email.
    async_retry_attempts = optional(number, 0)
    #: Grants ecs:RunTask on exactly these task definition families, in
    #: exactly this cluster, passing exactly these roles. All three halves
    #: are named: RunTask on "*" would start any task definition in the
    #: account, and PassRole on "*" is a general remote-code primitive.
    run_task_role_arns            = optional(list(string), [])
    run_task_cluster_arn          = optional(string, null)
    run_task_task_definition_arns = optional(list(string), [])
    #: Grants lambda:InvokeFunction on exactly these functions, named by their
    #: KEY in this same map rather than by ARN.
    #
    # A KEY, NOT AN ARN, AND THAT IS THE WHOLE DESIGN. The ARN is built from
    # `local.function_names`, a pure local over the keys of this variable.
    # Taking an ARN as input would mean an environment writing
    # `module.lambda.function_arns["task-worker"]` into `module.lambda`'s own
    # input, which is a cycle; and a `for_each` keyed on an ARN cannot be
    # planned, which is the trade the `ecs` module already makes.
    #
    # WHY IT EXISTS. A Route.LAMBDA task that DISPATCHES another Route.LAMBDA
    # task is a Lambda invoking a Lambda, and `readypick-task-worker` invokes
    # ITSELF: one function serves every short task. Nothing needed this until
    # `pickready.reconcile_context_index` had to queue one
    # `pickready.index_document` per unindexed document. Every earlier sweep
    # dispatched to Route.ECS, which goes through the `ecs:RunTask` grant above
    # and is a different permission entirely, so the gap stayed invisible until
    # a sweep fanned out to its own function and production answered
    # AccessDeniedException.
    invokable_function_keys = optional(list(string), [])
    #: Grants s3:GetObject under exactly these object prefixes, written as
    #: full object ARNs ("arn:aws:s3:::bucket/prefix/*").
    #
    # A BUCKET NAME AND NOT A MODULE OUTPUT. The inbound-mail bucket lives in
    # `ses_inbound`, which must SUBSCRIBE this function and therefore depends
    # on it; taking the bucket's ARN from that module's output would be a
    # cycle. A bucket name is deterministic, so the environment composes the
    # ARN from the same string both modules are given. The same trade
    # `run_task_task_definition_arns` already makes.
    #
    # GetObject only. The function reads one message and forwards it; it never
    # lists the bucket (which would enumerate every customer's correspondence)
    # and never deletes (the lifecycle rule owns expiry, and a function that
    # could delete could destroy a reply before anybody read it).
    s3_read_object_arns = optional(list(string), [])
  }))

  validation {
    condition = alltrue([
      for name, fn in var.functions : contains(["image", "zip"], fn.package)
    ])
    error_message = "package must be \"image\" or \"zip\"."
  }

  validation {
    # A function told to fetch secrets and given no policy would fail at cold
    # start with an AccessDenied per secret; one given a policy and no map
    # would hold a grant it never uses. Both are refused at plan time.
    condition = alltrue([
      for name, fn in var.functions :
      (length(fn.secrets) > 0) == (fn.secret_policy_key != null)
    ])
    error_message = "A function's `secrets` map and its `secret_policy_key` go together: the map says what to fetch and the key grants permission to fetch it."
  }

  validation {
    condition = alltrue([
      for name, fn in var.functions :
      fn.package != "image" || (fn.image_uri != null && fn.handler != null)
    ])
    error_message = "An image function needs both image_uri and handler: the handler is the container command that selects which entry point this image serves."
  }

  validation {
    condition = alltrue([
      for name, fn in var.functions :
      fn.package != "zip" || (fn.source_dir != null && fn.handler != null)
    ])
    error_message = "A zip function needs source_dir and handler."
  }

  validation {
    # PassRole with no resource list is the escalation. Refused at plan time
    # rather than reviewed later: anything that can pass a role can run code as
    # that role, so the list must be explicit and it must be short. The cluster
    # and the task definitions are named for the same reason, one level down.
    condition = alltrue([
      for name, fn in var.functions :
      length(fn.run_task_role_arns) == 0 || (
        fn.run_task_cluster_arn != null &&
        length(fn.run_task_task_definition_arns) > 0
      )
    ])
    error_message = "A function granted ecs:RunTask must name both the cluster it may run tasks in and the task definitions it may run."
  }
}

variable "secret_policy_arns" {
  description = "{consumer -> the IAM policy from the `secrets` module}. Its keys are static, which is what lets a function's `secret_policy_key` drive a for_each."
  type        = map(string)
  default     = {}
}

variable "architecture" {
  description = <<-EOT
    The instruction set the image-backed functions run on, and it MUST match the
    `ecs` module's `cpu_architecture`: the same backend image serves the API
    service, the on-demand agent and three of these four functions.

    It is stated rather than defaulted to Lambda's own default, because those
    two defaults DISAGREE. Lambda defaults to x86_64 and the ecs module defaults
    to ARM64, and the failure that mismatch produces is a function that is
    created without complaint and then fails at cold start with an exec format
    error naming nothing useful.

    The zip function follows the same value: it is pure Python and runs on
    either, and pinning it to the same one keeps the deployment a single answer
    to "what architecture is this".
  EOT
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64, in Lambda's own spelling."
  }
}

variable "vpc_subnet_ids" {
  description = "Private subnets. A function in the VPC reaches RDS and Redis; one outside it reaches only the public AWS endpoints."
  type        = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "failure_topic_arn" {
  description = <<-EOT
    Where a permanently failed asynchronous invocation is published.

    REQUIRED, not optional. Without it a function that exhausts its attempts
    drops the event with nothing but a metric to say so, which is the silent
    loss this whole architecture is built to make impossible. Optional would
    also make the destination block conditional on a value that is not known
    until apply, which cannot be planned.
  EOT
  type        = string
}

variable "build_dir" {
  description = "Where zip archives are written. Outside the repository tree, because a build artifact committed by accident is a build artifact deployed by accident."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
