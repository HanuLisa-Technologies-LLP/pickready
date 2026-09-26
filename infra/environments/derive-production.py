"""Derive the production environment root from staging.

Written as a DERIVATION rather than as a hand-authored second file so the two
roots cannot silently diverge in SHAPE -- only in the values that are supposed
to differ. Every substitution below is a resilience or scale decision; if one of
them stops matching, the script fails loudly rather than producing a production
root that is quietly missing a change staging got.

EVERY SUBSTITUTION MUST MATCH EXACTLY ONCE (2026-09-24). This script had
drifted from both roots: staging's backend key had moved to `backend.tf`, a
Celery `--concurrency` flag it rewrote no longer existed, and production had
been hand-edited to carry wording and sizes the derivation did not produce. The
first edit that stopped matching aborted the run, so the script could not be
run at all and every later change reached production by hand, which is the
drift this file exists to prevent. The EDITS below reproduce the production
root byte for byte from the staging root as it stands, and an edit that
matches twice is refused as loudly as one that matches never, because
`str.replace(..., 1)` rewriting the first of two occurrences is a silent choice
of which block production gets.

Run it from anywhere: `python infra/environments/derive-production.py`, then
review `git diff infra/environments/production`.
"""
import pathlib

# Resolved from this file, never from an absolute path. The paths used to be
# `C:\dev\pickready\...` literally, so running the script from any other
# checkout (a worktree, a CI runner, another machine) read and rewrote the
# production root of a DIFFERENT working copy, and the one being reviewed kept
# a production file nobody had derived.
HERE = pathlib.Path(__file__).resolve().parent
STAGING = HERE / "staging"
PROD = HERE / "production"

s = (STAGING / "main.tf").read_text(encoding="utf-8")

HEADER_OLD = '''/**
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
 */'''

HEADER_NEW = '''/**
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
 *   Redis replica         Redis carries the proctoring warning counter and
 *                         the run-status record, so losing it refuses every
 *                         assessment turn rather than costing a cache miss
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
 */'''

EDITS = [
    (HEADER_OLD, HEADER_NEW),
    ('  environment = "staging"', '  environment = "production"'),
    # The replica count is a local so the alarm set and the module agree.
    ('  redis_replica_count = 0', '  redis_replica_count = 1'),
    ('  cidr_block         = "10.20.0.0/16"',
     '  cidr_block         = "10.30.0.0/16"'),
    ('  flow_log_retention_days = 30', '  flow_log_retention_days = 90'),
    ('''  # ONE NAT. Staging is disposable; a per-AZ pair is $64/month buying
  # resilience for an environment whose whole purpose is to be thrown away.
  single_nat_gateway = true''',
     '''  # ONE NAT PER AZ. A single NAT is one AZ failure away from every task losing
  # egress, which for this platform means losing the model provider -- so every
  # assessment degrades at once.
  single_nat_gateway = false'''),
    ('''  # Fewer than production: staging images are rebuilt constantly and nobody
  # rolls back to the thirtieth one.
  keep_images = 10''',
     '''  # More than staging: a production rollback may need to reach back further
  # than the last few deploys.
  keep_images = 30'''),
    ('''  instance_class        = "db.t4g.small"
  allocated_storage     = 20
  max_allocated_storage = 50
  multi_az              = false
  backup_retention_days = 7''',
     '''  instance_class        = "db.m7g.large"
  allocated_storage     = 100
  max_allocated_storage = 500
  # A failover instead of a restore.
  multi_az              = true
  backup_retention_days = 30'''),
    ('''  # Staging is disposable and this still stays on. A staging database is
  # rebuilt on purpose, not by accident, and a deliberate teardown can
  # afford one extra apply to turn the guard off. What it cannot afford is
  # `terraform destroy` run in the wrong directory.''',
     '''  # Production. There is nothing to weigh here.'''),
    ('''  node_type = "cache.t4g.micro"
  # No replica in staging. In production this is 1, because a Redis failure
  # there is not a cache miss: the proctoring gate answers 503 rather than
  # silently not warning, so it is every assessment turn.''',
     '''  node_type = "cache.t4g.small"
  # ONE REPLICA, WHICH IS ALSO WHAT ENABLES AUTOMATIC FAILOVER. Redis carries
  # the proctoring warning counter and the run-status record, not just a cache.
  # The proctoring gate answers 503 rather than silently not warning, so losing
  # Redis is a candidate mid-assessment whose next question never arrives.'''),
    ('  noncurrent_retain_days = 7', '  noncurrent_retain_days = 90'),
    # ── traffic layer ──
    #
    # The WAF is deliberately NOT flipped. spec-doc6 §13.2 asks for it built and
    # "disabled by variable so enabling is a one-line decision", and that holds
    # for both environments: switching it on in production first, without a week
    # of count-mode metrics from staging, is exactly the move that blocks a real
    # candidate's resume upload.
    ("""  # No deletion protection in staging; on in production. Staging is meant to be
  # destroyable, which is most of what it is for.
  enable_deletion_protection = false""",
     """  # ON. A destroyed load balancer takes the DNS alias target with it, so the
  # outage outlasts the mistake by however long a replacement takes to create
  # and a resolver takes to forget the old answer.
  enable_deletion_protection = true"""),
    # ── the ECS cluster ──
    ('''  log_retention_days = 14

  # ECS Exec ON in staging, OFF in production. A shell in a container holding
  # real candidate data is a different thing from a shell in one holding seed
  # data, and the difference should be a decision rather than an inheritance.
  enable_execute_command = true''',
     '''  log_retention_days = 90

  # OFF. A shell in a container holding real candidate data is a real
  # capability. Turning it on for an incident is a deliberate, reviewed change
  # rather than something inherited from staging.
  enable_execute_command = false'''),
    # ── service sizing ──
    ('''      cpu           = 512
      memory        = 1024
      desired_count = 1
      max_count     = 2
      port          = 8000''',
     '''      cpu           = 1024
      memory        = 2048
      desired_count = 2
      max_count     = 8
      port          = 8000'''),
    ('''      command       = ["agent"]
      cpu           = 1024
      memory        = 2048''',
     '''      command       = ["agent"]
      cpu           = 2048
      memory        = 4096'''),
    ('''      desired_count    = 1
      max_count        = 2
      port             = 3000''',
     '''      desired_count    = 2
      max_count        = 6
      port             = 3000'''),
    ('''      desired_count = 1
      max_count     = 2
      port          = 8100''',
     '''      desired_count = 2
      max_count     = 4
      port          = 8100'''),
    # ── the Lambda half of background work ──
    ('''  log_retention_days = 14
  failure_topic_arn''',
     '''  log_retention_days = 90
  failure_topic_arn'''),
    ('''      reserved_concurrency    = var.reserve_lambda_concurrency ? 10 : null
      secret_policy_key       = "task-worker"''',
     '''      reserved_concurrency    = var.reserve_lambda_concurrency ? 40 : null
      secret_policy_key       = "task-worker"'''),
    # ── alarms: the connection ceiling is a property of the instance class ──
    ('  # this instance class: db.t4g.small (2 GiB, ~225 connections).',
     '  # this instance class: db.m7g.large (8 GiB, ~901 connections).'),
    ('  db_connection_alarm_threshold = 180', '  db_connection_alarm_threshold = 720'),
]

for old, new in EDITS:
    count = s.count(old)
    if count != 1:
        raise SystemExit(
            f"Staging contains a block this derivation edits {count} times, "
            "and it must contain it exactly once:\n\n"
            + old[:200]
            + "\n\nFix the derivation rather than hand-editing production, or the "
              "two roots will drift in shape instead of only in size."
        )
    s = s.replace(old, new, 1)

PROD.mkdir(parents=True, exist_ok=True)
(PROD / "main.tf").write_text(s, encoding="utf-8")
print("production/main.tf derived")

for name in ("variables.tf", "outputs.tf"):
    (PROD / name).write_text((STAGING / name).read_text(encoding="utf-8"), encoding="utf-8")
    print("copied", name)
