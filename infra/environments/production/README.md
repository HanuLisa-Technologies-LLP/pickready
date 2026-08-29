# production

> ## NOTHING IN THIS DIRECTORY MAY BE APPLIED IN THIS PHASE.
>
> spec-doc6 §D5 and §17 make this a pass/fail criterion in the opposite direction
> from usual: *"No live AWS deployment has been executed. Running `terraform
> apply` against a real account in this phase is a failure of scope, not an
> accomplishment."*
>
> Two independent things stop it, and both are checked rather than assumed:
>
> 1. `vars.AWS_DEPLOY_ENABLED` is unset, so every job in
>    `.github/workflows/deploy.yml` that would touch AWS is skipped.
> 2. The `production` environment's required reviewer, whose existence
>    `scripts/verify-approval-gate.sh` asserts on every push. An environment with
>    no required reviewer promotes instantly and silently while the workflow file
>    still reads as gated.
>
> `backend/tests/test_deploy_secret_hygiene.py::test_the_production_apply_is_gated_and_disabled`
> asserts both, separately, because they fail differently.

## This file is DERIVED

`main.tf`, `variables.tf` and `outputs.tf` here are generated from `../staging`
by `../derive-production.py`. **Do not hand-edit them.** Edit staging and re-run
the script.

The derivation exists so the two roots cannot silently diverge in SHAPE, only in
the values that are supposed to differ. It fails loudly when a block it edits no
longer matches, rather than producing a production root quietly missing a change
staging received — which is the failure that makes a staging test stop predicting
anything.

## What is different from staging, and why each one

| | Reason |
|---|---|
| NAT gateway per AZ | A single NAT is one AZ failure from every task losing egress, which here means losing the model provider, so every assessment degrades at once |
| RDS Multi-AZ | A failover instead of a restore |
| Redis replica (and therefore automatic failover) | Redis here is the Celery broker and the working-memory layer, not a cache. Losing it is a queue nobody is draining and a candidate mid-assessment whose next question never arrives |
| ALB deletion protection ON | A destroyed load balancer takes the DNS alias target with it, so the outage outlasts the mistake by however long a replacement takes plus however long resolvers cache the old answer |
| ECS Exec OFF | A shell in a container holding real candidate data is a different thing from one holding seed data, and the difference should be a decision rather than an inheritance |
| Container Insights ON | "Is the worker saturated" is a production question and needs data behind it |
| 30-day backups, final snapshot on destroy | |
| 90-day logs and flow logs | Long enough to investigate an incident noticed weeks late |
| 30 retained images | A production rollback may reach back further than the last few deploys |

The WAF is **not** flipped on here, and that is deliberate rather than an
oversight in the derivation. spec-doc6 §13.2 asks for it built and disabled by
variable, and enabling it in production first — without a week of count-mode
metrics from staging — is exactly the move that blocks a real candidate's resume
upload. `docs/DEPLOY_AWS.md` §6 carries the ordered procedure.

## Required variables

Identical in shape to staging, different in value. See
`../staging/README.md` for what each one is and the command that produces it.

```hcl
account_id         = ""          # may be a SEPARATE account from staging
region             = ""
availability_zones = ["", ""]

domain_name    = ""              # the production hostname
hosted_zone_id = ""

storage_bucket_name     = ""
access_logs_bucket_name = ""
```

**A separate AWS account for production is worth considering and is not
required.** The infrastructure supports either: nothing here assumes staging and
production share an account, every resource name carries the environment, and the
IAM roles are per-environment. A separate account gives a blast radius that IAM
cannot accidentally cross; one account gives one bill and one set of quotas.

## Running the offline checks

The offline gates run here exactly as they do for staging, and need no
credentials:

```bash
./infra/validate.sh
./infra/plan-offline.sh          # plans BOTH environments
python infra/check-no-wildcard-iam.py
```

A green offline plan proves the configuration is internally consistent and that
the graph resolves. It proves nothing about a real account — not that the account
can create these resources, not that quotas suffice, not that IAM behaves.
`docs/DEPLOY_AWS.md` §1 states the boundary exactly.
