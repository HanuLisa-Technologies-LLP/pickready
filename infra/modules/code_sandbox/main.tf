/**
 * The code sandbox: one self-hosted Judge0 CE 1.13.1 host where candidate code
 * runs, and the only place it runs.
 *
 * WHY A SEPARATE HOST, AND NOT A CONTAINER BESIDE THE API
 * --------------------------------------------------------
 * Judge0's isolate sandbox needs privileged containers and cgroup v1. Fargate
 * allows neither, and every Fargate task and in-VPC Lambda in this platform
 * holds application secrets. So candidate code gets a box of its own that
 * holds NO application secret at all: its role can pull three images, read
 * one token and write one log group, and nothing else. A sandbox escape lands
 * on a host with nothing worth stealing and nowhere to send it.
 *
 * WHY ONE PLAIN EC2 INSTANCE, NOT ECS ON EC2
 * -------------------------------------------
 * ECS on EC2 needs three more interface endpoints (ecs, ecs-agent,
 * ecs-telemetry, roughly twenty four dollars a month) and an agent that must
 * also run under cgroup v1, for no benefit to a single stateless box. Docker
 * under systemd is the whole runtime. The box is STATELESS: Judge0's Postgres
 * and Redis hold only in-flight runs, deleted after collection and pruned
 * hourly, so the answer to any fault is to replace the instance.
 *
 * THE NETWORK, IN THE ORDER A PACKET MEETS IT
 * --------------------------------------------
 *   route table   the VPC-local route and a DEDICATED S3 gateway endpoint.
 *                 No NAT, no internet gateway: there is no route out.
 *   S3 endpoint   its policy admits s3:GetObject on the ECR layer bucket and
 *                 the Amazon Linux package bucket for this region, nothing
 *                 else, so S3 is not an exfiltration channel either.
 *   network ACL   DENIES the data subnets (RDS, ElastiCache) both ways ahead
 *                 of every allow; admits the sandbox port only from the
 *                 application tier.
 *   host group    ingress on 2358 from the `judge0_client` group ONLY;
 *                 egress 443 to the interface endpoints and the S3 prefix
 *                 list only.
 *   client group  attached to what may call the sandbox (the API, the task
 *                 worker, the agent), and to nothing else. The shared `ecs`
 *                 group is NOT the source, because the frontend and the
 *                 analysis service carry it too.
 *   IMDS          IMDSv2 only, hop limit one: a bridged container cannot reach
 *                 the metadata service, so an escape into a container cannot
 *                 read the instance role. The host also drops the address in
 *                 DOCKER-USER.
 *
 * A DISABLED-BY-DEFAULT MODULE
 * -----------------------------
 * The environment instantiates this only when `judge0_enabled` is true, and
 * creates the instance only when `judge0_instance_enabled` is true as well, so
 * an apply with defaults creates nothing and the rollout is staged:
 * docs/operations/JUDGE0_RUNBOOK.md.
 */
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
  }
}

locals {
  name = "${var.project}-${var.environment}-judge0"

  sandbox_port = 2358

  # The three images the host runs, each mirrored into its own registry.
  repositories = ["judge0", "judge0-postgres", "judge0-redis"]

  # Documented by AWS: ECR serves image layers from this bucket, and the AL2023
  # package repositories from the second. Both are read-only public content.
  ecr_layer_bucket_arn = "arn:aws:s3:::prod-${var.region}-starport-layer-bucket/*"
  package_bucket_arns = (
    length(var.package_repository_bucket_arns) > 0
    ? var.package_repository_bucket_arns
    : ["arn:aws:s3:::al2023-repos-${var.region}-de612dc2/*"]
  )

  registry = split("/", module.images.repository_urls["judge0"])[0]

  image_refs = {
    for name in local.repositories :
    name => "${module.images.repository_urls[name]}@${lookup(var.image_digests, name, "")}"
  }
}

# ── Registries ───────────────────────────────────────────────────────────────
#
# The host has no internet route, so it cannot pull from Docker Hub. The images
# are mirrored here by digest (scripts/mirror-judge0-images.sh) and the host
# pulls BY DIGEST. The same module every other registry in this environment
# uses: immutable tags, scan on push, the environment key.

module "images" {
  source = "../ecr"

  project      = var.project
  environment  = var.environment
  repositories = local.repositories
  kms_key_arn  = var.kms_key_arn
  keep_images  = 5

  tags = var.tags
}

# ── The token between the application and the sandbox ───────────────────────
#
# In Secrets Manager with every other credential. GENERATED rather than typed,
# the pattern `modules/secrets` uses for INBOUND_WEBHOOK_SECRET: nobody has to
# invent a value, and the value lives in exactly two places, this secret and
# /run on the host. The application reads it as JUDGE0_AUTH_TOKEN once the
# client wiring is applied (the runbook's Apply B), through the same
# per-service secret mounts as every other credential.

resource "aws_secretsmanager_secret" "token" {
  name        = "${var.project}-${var.environment}/JUDGE0_AUTH_TOKEN"
  description = "ReadyPick ${var.environment}: JUDGE0_AUTH_TOKEN, the code sandbox API token"
  kms_key_id  = var.kms_key_id

  recovery_window_in_days = var.environment == "production" ? 30 : 7

  tags = merge(var.tags, { Name = "${var.project}-${var.environment}-JUDGE0_AUTH_TOKEN" })
}

resource "random_password" "token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret_version" "token" {
  secret_id     = aws_secretsmanager_secret.token.id
  secret_string = random_password.token.result
}

data "aws_iam_policy_document" "token_read" {
  statement {
    sid       = "ReadTheSandboxTokenOnly"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.token.arn]
  }

  statement {
    sid       = "DecryptItThroughSecretsManagerOnly"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

# For the CALLERS (the API, the task worker, the agent), attached in the
# runbook's Apply B. The host has its own role below and does not use this.
resource "aws_iam_policy" "token_read" {
  name        = "${local.name}-token-read"
  description = "Read the code sandbox API token, and nothing else."
  policy      = data.aws_iam_policy_document.token_read.json
  tags        = var.tags
}

# ── Network ──────────────────────────────────────────────────────────────────

resource "aws_subnet" "sandbox" {
  vpc_id                  = var.vpc_id
  availability_zone       = var.availability_zone
  cidr_block              = var.sandbox_cidr_block
  map_public_ip_on_launch = false

  tags = merge(var.tags, { Name = "${local.name}-sandbox", Tier = "sandbox" })
}

# NO ROUTE BEYOND THE VPC. The only non-local entry is the S3 gateway endpoint
# below, which AWS adds to this table as a prefix-list route.
resource "aws_route_table" "sandbox" {
  vpc_id = var.vpc_id
  tags   = merge(var.tags, { Name = "${local.name}-sandbox" })
}

resource "aws_route_table_association" "sandbox" {
  subnet_id      = aws_subnet.sandbox.id
  route_table_id = aws_route_table.sandbox.id
}

# A RESOURCE policy on the endpoint: the principal is necessarily everyone who
# uses THIS endpoint (the ECR layer download is a presigned URL and the package
# repository is anonymous), and the endpoint is reachable only from the sandbox
# route table, so the restriction that matters is the resource list. Declared
# in infra/check-no-wildcard-iam.py RESOURCE_POLICY_DOCUMENTS with this reason.
data "aws_iam_policy_document" "sandbox_s3_endpoint" {
  statement {
    sid    = "ImageLayersAndOsPackagesOnly"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:GetObject"]
    resources = concat([local.ecr_layer_bucket_arn], local.package_bucket_arns)
  }
}

resource "aws_vpc_endpoint" "sandbox_s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.sandbox.id]
  policy            = data.aws_iam_policy_document.sandbox_s3_endpoint.json

  tags = merge(var.tags, { Name = "${local.name}-s3" })
}

resource "aws_network_acl" "sandbox" {
  vpc_id     = var.vpc_id
  subnet_ids = [aws_subnet.sandbox.id]
  tags       = merge(var.tags, { Name = "${local.name}-sandbox" })
}

# Rules are evaluated lowest number first, so the data-tier DENIES (100s) sit
# ahead of every ALLOW (200s and up).
resource "aws_network_acl_rule" "deny_data_in" {
  count          = length(var.data_subnet_cidr_blocks)
  network_acl_id = aws_network_acl.sandbox.id
  rule_number    = 100 + count.index
  egress         = false
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = var.data_subnet_cidr_blocks[count.index]
}

resource "aws_network_acl_rule" "deny_data_out" {
  count          = length(var.data_subnet_cidr_blocks)
  network_acl_id = aws_network_acl.sandbox.id
  rule_number    = 100 + count.index
  egress         = true
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = var.data_subnet_cidr_blocks[count.index]
}

resource "aws_network_acl_rule" "sandbox_port_in" {
  count          = length(var.private_subnet_cidr_blocks)
  network_acl_id = aws_network_acl.sandbox.id
  rule_number    = 200 + count.index
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = var.private_subnet_cidr_blocks[count.index]
  from_port      = local.sandbox_port
  to_port        = local.sandbox_port
}

# Return traffic for the host's own HTTPS calls. With no route out of the VPC,
# the only things that can answer are the interface endpoints and S3.
resource "aws_network_acl_rule" "ephemeral_in" {
  network_acl_id = aws_network_acl.sandbox.id
  rule_number    = 300
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 1024
  to_port        = 65535
}

resource "aws_network_acl_rule" "https_out" {
  network_acl_id = aws_network_acl.sandbox.id
  rule_number    = 200
  egress         = true
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 443
  to_port        = 443
}

# Responses to the application tier's calls on the sandbox port.
resource "aws_network_acl_rule" "ephemeral_out" {
  count          = length(var.private_subnet_cidr_blocks)
  network_acl_id = aws_network_acl.sandbox.id
  rule_number    = 300 + count.index
  egress         = true
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = var.private_subnet_cidr_blocks[count.index]
  from_port      = 1024
  to_port        = 65535
}

resource "aws_security_group" "host" {
  name        = "${local.name}-host"
  description = "Judge0 host: 2358 from judge0_client only; 443 to endpoints and S3 only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${local.name}-host" })
}

resource "aws_security_group" "client" {
  name        = "${local.name}-client"
  description = "Attached to what may call the code sandbox, and to nothing else"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${local.name}-client" })
}

resource "aws_vpc_security_group_ingress_rule" "host_from_clients" {
  security_group_id            = aws_security_group.host.id
  description                  = "The sandbox API, from judge0_client only"
  ip_protocol                  = "tcp"
  from_port                    = local.sandbox_port
  to_port                      = local.sandbox_port
  referenced_security_group_id = aws_security_group.client.id
}

resource "aws_vpc_security_group_egress_rule" "host_to_endpoints" {
  security_group_id            = aws_security_group.host.id
  description                  = "ECR, Secrets Manager and Logs through the interface endpoints"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = var.interface_endpoints_security_group_id
}

resource "aws_vpc_security_group_egress_rule" "host_to_s3" {
  security_group_id = aws_security_group.host.id
  description       = "Image layers and OS packages through the sandbox S3 endpoint"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = aws_vpc_endpoint.sandbox_s3.prefix_list_id
}

resource "aws_vpc_security_group_egress_rule" "client_to_host" {
  security_group_id            = aws_security_group.client.id
  description                  = "The sandbox API"
  ip_protocol                  = "tcp"
  from_port                    = local.sandbox_port
  to_port                      = local.sandbox_port
  referenced_security_group_id = aws_security_group.host.id
}

# ── The host's role: three images, one token, one log group ─────────────────

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.name}-host"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "host" {
  statement {
    sid       = "RegistryLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullTheThreeSandboxImagesOnly"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = values(module.images.repository_arns)
  }

  statement {
    sid       = "ReadTheSandboxTokenOnly"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.token.arn]
  }

  statement {
    sid       = "DecryptThroughSecretsManagerAndEcrOnly"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "secretsmanager.${var.region}.amazonaws.com",
        "ecr.${var.region}.amazonaws.com",
      ]
    }
  }

  statement {
    sid    = "WriteTheHostLogOnly"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      aws_cloudwatch_log_group.host.arn,
      "${aws_cloudwatch_log_group.host.arn}:log-stream:*",
    ]
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "${local.name}-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.name}-host"
  role = aws_iam_role.host.name
  tags = var.tags
}

# ── Logs and alarms ──────────────────────────────────────────────────────────
#
# ONLY the host's own lines are shipped (bootstrap, preflight verdict, stack
# start and stop, liveness, prune). Judge0's container logs stay on the box:
# its request logs can carry request bodies, which means hidden stdin.

resource "aws_cloudwatch_log_group" "host" {
  name              = "/${var.project}/${var.environment}/judge0-host"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_cloudwatch_log_metric_filter" "down" {
  name           = "${local.name}-down"
  log_group_name = aws_cloudwatch_log_group.host.name
  pattern        = "\"judge0-health status=down\""

  metric_transformation {
    name          = "CodeSandboxDown"
    namespace     = "ReadyPick/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "down" {
  alarm_name          = "${local.name}-down"
  alarm_description   = "The code sandbox answered its own liveness probe with a failure for three minutes. Candidates pressing Run see the unavailable sentence; submissions wait and are retried. Runbook: docs/operations/JUDGE0_RUNBOOK.md, Outage."
  namespace           = "ReadyPick/${var.environment}"
  metric_name         = aws_cloudwatch_log_metric_filter.down.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
  tags                = var.tags
}

resource "aws_cloudwatch_log_metric_filter" "preflight_refused" {
  name           = "${local.name}-preflight-refused"
  log_group_name = aws_cloudwatch_log_group.host.name
  pattern        = "\"judge0-preflight verdict=refused\""

  metric_transformation {
    name          = "CodeSandboxPreflightRefused"
    namespace     = "ReadyPick/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "preflight_refused" {
  alarm_name          = "${local.name}-preflight-refused"
  alarm_description   = "The code sandbox refused to start Judge0 because cgroup v1 is not in effect. It will not start until the host is replaced or the kernel arguments restored. Runbook: Preflight refusal."
  namespace           = "ReadyPick/${var.environment}"
  metric_name         = aws_cloudwatch_log_metric_filter.preflight_refused.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
  tags                = var.tags
}

# ── The instance ─────────────────────────────────────────────────────────────

resource "aws_instance" "host" {
  count = var.create_instance ? 1 : 0

  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.sandbox.id
  vpc_security_group_ids      = [aws_security_group.host.id]
  iam_instance_profile        = aws_iam_instance_profile.host.name
  associate_public_ip_address = false
  monitoring                  = false

  # Standard credits: a busy box throttles and the credit alarm says so,
  # rather than an unlimited-mode bill nobody chose.
  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  maintenance_options {
    auto_recovery = "default"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    kms_key_id            = var.kms_key_arn
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/cloud-init.sh.tftpl", {
    region           = var.region
    token_secret_arn = aws_secretsmanager_secret.token.arn
    registry         = local.registry
    server_image     = local.image_refs["judge0"]
    postgres_image   = local.image_refs["judge0-postgres"]
    redis_image      = local.image_refs["judge0-redis"]
    log_group_name   = aws_cloudwatch_log_group.host.name
    judge0_conf      = templatefile("${path.module}/judge0.conf.tftpl", {})
  })
  # The box is stateless: a changed image digest or configuration REPLACES it
  # rather than editing a running sandbox in place.
  user_data_replace_on_change = true

  tags = merge(var.tags, { Name = "${local.name}-host" })

  lifecycle {
    precondition {
      condition     = var.ami_id != ""
      error_message = "create_instance needs a pinned ami_id (Amazon Linux 2023 x86_64)."
    }
    precondition {
      condition     = alltrue([for name in local.repositories : contains(keys(var.image_digests), name)])
      error_message = "create_instance needs a digest for each of judge0, judge0-postgres and judge0-redis; run scripts/mirror-judge0-images.sh first."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "system_check" {
  count = var.create_instance ? 1 : 0

  alarm_name          = "${local.name}-system-check"
  alarm_description   = "The code sandbox host failed its SYSTEM status check. EC2 recovers it onto new hardware; this is the notification that it did."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.host[0].id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:recover", var.alarm_topic_arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "instance_check" {
  count = var.create_instance ? 1 : 0

  alarm_name          = "${local.name}-instance-check"
  alarm_description   = "The code sandbox host failed its INSTANCE status check. EC2 reboots it; the preflight unit decides on boot whether Judge0 may start."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.host[0].id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:reboot", var.alarm_topic_arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "cpu_credits" {
  count = var.create_instance ? 1 : 0

  alarm_name          = "${local.name}-cpu-credits"
  alarm_description   = "The code sandbox is close to exhausting its CPU credits. Standard credits throttle rather than bill, and a throttled box turns correct solutions into time-limit failures. Runbook: Queue saturation."
  namespace           = "AWS/EC2"
  metric_name         = "CPUCreditBalance"
  dimensions          = { InstanceId = aws_instance.host[0].id }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.cpu_credit_alarm_threshold
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [var.alarm_topic_arn]
  tags                = var.tags
}

# ── The internal name the application reaches it on ─────────────────────────

resource "aws_service_discovery_service" "judge0" {
  count = var.register_in_namespace ? 1 : 0

  name        = "judge0"
  description = "The code sandbox host, resolved to its private address inside this VPC only."

  dns_config {
    namespace_id   = var.discovery_namespace_id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  tags = var.tags

  lifecycle {
    precondition {
      condition     = var.discovery_namespace_id != null
      error_message = "register_in_namespace needs discovery_namespace_id."
    }
  }
}

resource "aws_service_discovery_instance" "judge0" {
  count = var.register_in_namespace && var.create_instance ? 1 : 0

  instance_id = aws_instance.host[0].id
  service_id  = aws_service_discovery_service.judge0[0].id

  attributes = {
    AWS_INSTANCE_IPV4 = aws_instance.host[0].private_ip
  }
}
