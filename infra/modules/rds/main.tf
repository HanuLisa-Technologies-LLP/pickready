/**
 * RDS PostgreSQL with pgvector.
 *
 * PGVECTOR IS NOT INSTALLED BY THIS MODULE, AND THAT IS NOT AN OVERSIGHT.
 * `CREATE EXTENSION vector` is a SQL statement against a database, not an AWS
 * API call, so Terraform cannot run it without either a provisioner reaching
 * into a private subnet or a Postgres provider holding the master credential in
 * state. Both are worse than the alternative.
 *
 * The alternative is that migration `0001_initial` already runs
 * `CREATE EXTENSION IF NOT EXISTS vector`, and the migration job runs before
 * any service serves traffic. So the extension is created by the thing that
 * already had to connect anyway. What this module DOES do is make it possible:
 * `rds.force_ssl` and the parameter group are here, and
 * `shared_preload_libraries` is deliberately untouched because pgvector does
 * not need it.
 *
 * THE MASTER PASSWORD IS GENERATED AND STORED, NEVER SUPPLIED.
 * `manage_master_user_password` hands the whole lifecycle to Secrets Manager:
 * the password is generated inside AWS, is never in a variable, never in a
 * plan, and never in the state file. A `random_password` resource would put it
 * in state, which is a JSON file behind a bucket policy rather than a vault.
 *
 * THE APPLICATION DOES NOT USE THE MASTER CREDENTIAL. `DATABASE_URL` is a
 * separate secret holding a least-privileged application role; the master
 * exists to create that role and to run migrations. That separation is the
 * direct answer to the GCP-phase finding about a composed DSN being readable
 * more widely than it needed to be.
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

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, { Name = local.name })
}

resource "aws_db_parameter_group" "this" {
  name   = "${local.name}-pg16"
  family = var.parameter_group_family

  # TLS IS NOT OPTIONAL. Without this a client can connect in the clear inside
  # the VPC, and "inside the VPC" is not a trust boundary -- it is a network.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
    # STATIC, so it can only be applied at a reboot, and saying so is not
    # optional. Terraform's default is `immediate`, and RDS REFUSES `immediate`
    # for a static parameter -- so the omission did not make the setting apply
    # sooner, it made `terraform apply` fail against a real account. That went
    # unnoticed because `plan` accepts it happily and this environment had
    # never been applied with the parameter group already in its deployed
    # state: the drift only appears on the SECOND apply.
    apply_method = "pending-reboot"
  }

  # Log any statement slower than this. Two seconds rather than zero: logging
  # every statement on a busy database fills a log group and bills for it, and
  # the queries worth seeing are the ones that got slow.
  parameter {
    name  = "log_min_duration_statement"
    value = "2000"
  }

  # Failed authentication attempts. This is the signal that something is trying
  # credentials, and it is off by default.
  parameter {
    name  = "log_connections"
    value = "1"
  }

  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_name  = var.database_name
  username = var.master_username

  # Generated inside AWS, stored in Secrets Manager, never in a variable and
  # never in the state file. See the module docstring.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = var.kms_key_id

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  parameter_group_name   = aws_db_parameter_group.this.name

  # The data subnets have no route to the internet in either direction, so this
  # is belt to that braces -- and it is the setting somebody would flip while
  # debugging, which is why it is stated rather than inherited.
  publicly_accessible = false

  multi_az                = var.multi_az
  backup_retention_period = var.backup_retention_days
  backup_window           = "18:00-19:00" # 23:30 IST, off-peak for this market
  maintenance_window      = "sun:19:30-sun:20:30"

  # AUTO MINOR VERSION UPGRADES ARE ON. A minor Postgres release is a security
  # release most of the time, and the alternative to taking them in a
  # maintenance window is taking them during an incident.
  # IAM DATABASE AUTHENTICATION, ADDITIVE RATHER THAN INSTEAD OF.
  #
  # This does not disable password authentication and the application keeps
  # using its DSN: the connection pool holds a long-lived connection and an IAM
  # auth token expires every fifteen minutes, so reconnecting on every expiry
  # would be a worse trade than the one it replaces.
  #
  # What it buys is the OPERATOR path. Today an engineer who needs a psql
  # session reads the production DSN out of Secrets Manager, and from that
  # moment the password is in a shell history and a terminal buffer. With this
  # on, they mint a fifteen-minute token against their own IAM identity instead,
  # which is both attributable and short-lived.
  iam_database_authentication_enabled = true

  auto_minor_version_upgrade = true

  performance_insights_enabled          = var.environment == "production"
  performance_insights_kms_key_id       = var.environment == "production" ? var.kms_key_arn : null
  performance_insights_retention_period = var.environment == "production" ? 7 : null
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]

  # EVERY DATABASE REFUSES TO BE DESTROYED, AND SNAPSHOTS ON THE WAY OUT ANYWAY.
  # Two independent guards, because they fail differently: deletion protection
  # stops the API call, and the final snapshot survives somebody disabling it
  # first.
  #
  # BOTH USED TO BE KEYED ON `var.environment == "production"`, AND THAT WAS
  # BACKWARDS IN PRACTICE (corrected 2026-09-17). Production has never been
  # applied. Pilot is the only environment that has ever existed, it holds
  # three demo tenants and a month of manual setup, and it had neither guard:
  # one `terraform destroy` in the wrong directory, or one resource rename that
  # Terraform decided needed replacement, and the database was gone with no
  # snapshot to go back to. "This environment does not matter" is a judgement
  # somebody makes before an incident, not during one.
  #
  # So both are now variables defaulting to the SAFE value, and an environment
  # that genuinely wants a disposable database says so in its own root, where
  # the decision is reviewable in a diff. See the variables for what each one
  # costs when it is turned off.
  deletion_protection = var.deletion_protection
  skip_final_snapshot = var.skip_final_snapshot
  # NAMED WHENEVER A SNAPSHOT WILL BE TAKEN, not only in production. RDS
  # refuses the delete outright when `skip_final_snapshot` is false and no
  # identifier is set, which would turn a careful teardown into an error
  # somebody resolves by setting `skip_final_snapshot = true`.
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${local.name}-final-${var.snapshot_suffix}"
  copy_tags_to_snapshot     = true

  # `apply_immediately` false in production: a parameter change that forces a
  # reboot should land in the maintenance window, not the moment somebody
  # merges.
  apply_immediately = var.environment != "production"

  tags = merge(var.tags, { Name = local.name })

  lifecycle {
    # A version bump arriving through auto_minor_version_upgrade must not show
    # up as drift that Terraform then wants to revert.
    ignore_changes = [engine_version]
  }
}
