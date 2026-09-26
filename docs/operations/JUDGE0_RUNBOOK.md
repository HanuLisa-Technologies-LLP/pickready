# Judge0 code sandbox: topology, cost, rollout and outage runbook

Owner of the code: `infra/modules/code_sandbox` (the host) and
`backend/app/services/code_execution` (the port and the one adapter). The
application ships with `CODE_EXECUTION_BACKEND=disabled`, and every Terraform
switch below defaults to off, so nothing in this document has happened until
somebody runs it.

**Honesty line.** At the time of writing no Judge0 host has been provisioned.
The Terraform validates and plans offline (`bash infra/plan-offline.sh`), which
proves the configuration is internally consistent and nothing about a real
account. The Judge0 fixtures the adapter is tested against are hand-authored
from the published 1.13.1 API (`backend/tests/fixtures/vendor/PROVENANCE.md`).
The first real evidence is stage V below.

## 1. Topology

```
 private subnets (10.0.10.0/24, 10.0.11.0/24)          sandbox subnet 10.0.30.0/24 (one AZ)
 ┌──────────────────────────────────────────┐          ┌──────────────────────────────────────┐
 │ API service  ─┐                          │   2358   │ EC2 t3.medium, AL2023 x86_64 (pinned)│
 │ task worker  ─┼─ SG judge0_client ───────┼─────────►│ SG judge0_host                       │
 │ agent (ECS)  ─┘   (Apply B)              │   HTTP   │  docker (systemd, no ECS agent)      │
 │                                          │          │   judge0-server   --privileged :2358 │
 │ frontend, analysis: NO judge0_client     │          │   judge0-workers  --privileged (x2)  │
 │                                          │          │   db (postgres 16.2), redis 7.2.4    │
 │ interface endpoints: ecr.api, ecr.dkr,   │◄─────────┤  443 to the endpoints (reused)       │
 │   secretsmanager, logs (SG endpoints)    │   443    │  443 to S3 via its OWN gateway       │
 └──────────────────────────────────────────┘          │   endpoint (2 buckets, GetObject)    │
 data subnets (RDS, ElastiCache)  ◄── NACL DENY ───────┤  no NAT, no IGW, no public IP        │
                                                       └──────────────────────────────────────┘
```

| Control | Where | What it stops |
|---|---|---|
| Separate host, no application secret | `aws_iam_role.host` | An escape finds three image pulls, one token, one log group. No DSN, no model key, no JWT secret. |
| No route out of the VPC | `aws_route_table.sandbox` has only the local route and the S3 gateway prefix list | Candidate code phoning home; the host fetching anything we did not mirror. |
| Dedicated S3 gateway endpoint policy | `data.aws_iam_policy_document.sandbox_s3_endpoint` | S3 as an exfiltration channel: `s3:GetObject` on the ECR layer bucket and the AL2023 repository bucket only. |
| Network ACL | `aws_network_acl.sandbox` | The data tier, denied both ways ahead of every allow; the sandbox port admitted from the private subnets only. |
| Security groups | `host` (2358 from `client` only; 443 to endpoints and S3), `client` (2358 to `host` only) | The frontend and analysis service reaching Judge0: they carry the shared `ecs` group, which is deliberately NOT the source. |
| IMDSv2, hop limit 1, DOCKER-USER drop | `aws_instance.host.metadata_options`, bootstrap | A container reading the instance role. |
| Judge0 configuration | `judge0.conf.tftpl` | Network in submissions (`ENABLE_NETWORK=false`, `ALLOW_ENABLE_NETWORK=false`), callbacks, compiler options, command-line arguments, additional files, telemetry; per-run CPU, wall, memory, stack, process, file-size caps. |
| cgroup v1 preflight | `judge0-preflight.service` | Judge0 1.13.1 running under cgroup v2, where isolate does not enforce its limits. It refuses and removes any leftover container. |
| No expected output leaves the app | `code_execution.provider.TestInput`, `judge0.py` | The answer key in the sandbox database or in a response. |
| Container logs stay on the box | CloudWatch agent collects `/var/log/judge0/judge0.log` only | Hidden stdin in shipped Rails request logs. |
| Hourly prune | `judge0-prune.timer` | A run's stdin living on the host longer than an hour if the app's delete failed. |

**Why EC2 and not Fargate or ECS on EC2.** Judge0's isolate sandbox needs
privileged containers and cgroup v1; Fargate allows neither. ECS on EC2 would
need three more interface endpoints (about 24 USD a month) and an agent that
must also run under cgroup v1, for one stateless box.

**The cgroup v1 risk, stated.** AWS documents cgroup v1 on AL2023 through
`grubby --args="systemd.unified_cgroup_hierarchy=0"` and in the same breath
calls it not a recommended or supported configuration that a future major
release will remove. AL2023 itself is supported into 2029. The preflight unit
makes a regression loud (alarm `*-judge0-preflight-refused`) rather than
silent. The documented fallback is an Ubuntu 22.04 baked AMI (Judge0's own
recommendation), which needs an image-build pipeline with internet access and
is not built.

## 2. Monthly incremental cost, ap-south-2

List prices were not retrievable from the build environment, so the figures
below are ap-south-1 list prices, which ap-south-2 tracks closely. **Confirm
with the AWS Pricing API for ap-south-2 before stage A2.**

| Item | Basis | USD / month |
|---|---|---|
| EC2 t3.medium, on-demand Linux, 730 h, standard credits | about 0.0448 per hour | about 32.7 |
| EBS gp3 root, 40 GiB | about 0.0912 per GiB-month | about 3.6 |
| ECR storage, three images (Judge0 is the bulk) | about 0.10 per GB-month, roughly 3 GB | about 0.3 |
| Secrets Manager, one secret | 0.40 | 0.4 |
| CloudWatch: five alarms, two metric filters, one small log group | 0.10 per alarm | about 0.6 |
| S3 gateway endpoint | free | 0 |
| Reused interface endpoints (data processing only) | 0.01 per GB | under 0.1 |
| Cross-AZ calls from tasks in the other zone | 0.01 per GB each way | under 0.1 |
| **Total** | | **about 37 to 38** |

Not included because not built: SSM Session Manager endpoints (three interface
endpoints, about 24 USD a month while present). The operating model is
replace-not-repair plus `aws ec2 get-console-output`. A cheaper t3.small (2 GiB,
total about 21) is NOT recommended: Judge0's server, two workers, Postgres,
Redis and a JVM compile do not fit.

The owner accepted roughly 40 USD a month (CONTRACT v2, P4) with provisioning
confined to the deploy stage.

## 3. Staged rollout

Every stage is a separate, reviewed plan. **Read every plan for destroys and
for changes to existing resources before applying.** Never run the backend test
suite beside the image mirror or an apply that builds anything.

### Stage 0: code with the feature off (safe today)
Deploy the application with `CODE_EXECUTION_BACKEND=disabled` (the default).
`is_enabled()` answers False and every coding surface records "code execution
unavailable". Nothing depends on a sandbox.

### Stage A1: the sandbox's surroundings, no instance
`terraform.tfvars`: `judge0_enabled = true`.
Creates the subnet, route table, S3 endpoint, NACL, both security groups, the
host role and instance profile, the three ECR repositories, the token secret
(generated), the caller policy, the log group, two metric filters and two
alarms. **The one change to an existing resource** is
`module.network.aws_security_group.endpoints` admitting the host group on 443
(an in-place update of an inline rule). Anything else changing is a stop.

Before A2, confirm at the S1 review:
- the AL2023 package bucket name for ap-south-2
  (`al2023-repos-ap-south-2-de612dc2` is AWS's documented pattern; override
  with `package_repository_bucket_arns` if it differs);
- that the pinned AL2023 AMI's `dnf` reaches that bucket through a gateway
  endpoint (its mirror list uses the regional S3 hostname);
- the AMI id to pin: `aws ssm get-parameter --name
  /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
  --region ap-south-2`, read ONCE by the operator and written into tfvars.

### Mirror the images
On an amd64 Docker host: `AWS_REGION=ap-south-2 ./scripts/mirror-judge0-images.sh pilot`.
It pulls `judge0/judge0:1.13.1`, `postgres:16.2` and `redis:7.2.4`, pushes them
to the three registries, and prints the `judge0_image_digests` block.

### Stage A2: the instance
`terraform.tfvars`: `judge0_instance_enabled = true`, `judge0_ami_id = "ami-..."`,
and the digests block. Creates the instance, its three EC2 alarms and the Cloud
Map registration `judge0.readypick.local`.

Verify on the host, without logging in:
1. `aws ec2 get-console-output --instance-id <id> --latest` shows
   `judge0-bootstrap phase=reboot reason=cgroup_v1`, then after the reboot the
   log group `/readypick/pilot/judge0-host` shows
   `judge0-preflight verdict=ok cgroup_fs=tmpfs docker_cgroup=1` and
   `judge0-stack status=started`.
2. `judge0-health status=up` appears within five minutes and
   `*-judge0-down` stays OK.

### Stage B: client wiring
`terraform.tfvars`: `judge0_clients_enabled = true` (it needs
`judge0_enabled`; a check block warns otherwise). Still with
`CODE_EXECUTION_BACKEND=disabled`. It is its own switch so that A1 and A2 stay
plans that touch no running service. Wired in `environments/pilot/main.tf`
through the `local.judge0_*` values, every one of them empty while the switch
is off:
- `module.code_sandbox[0].client_security_group_id` on the API service (the
  ecs module's per-service `extra_security_group_ids`), on the task worker
  Lambda only (the lambda module's per-function `extra_security_group_ids`;
  the two drafting Lambdas never execute code), and appended to the trigger's
  `ECS_SECURITY_GROUP_IDS` for the on-demand agent. NOT the frontend, the
  analysis service, the migration job or the drafting Lambdas;
- `token_secret_arn` mounted as `JUDGE0_AUTH_TOKEN` on the API, the task
  worker and the agent, with `token_read_policy_arn` on the role that reads
  it: the EXECUTION role for the two ECS entries (the ECS agent injects the
  secret; `extra_execution_policy_arns`), the function role for the task
  worker (its cold-start fetch; `extra_policy_arns`);
- `JUDGE0_URL` from the `sandbox_url` output on the same three.
The plan shows three policy attachments, a rolling deployment of the API
service, a new agent task definition revision, and configuration updates to
the task worker and the trigger. Nothing is destroyed. The offline plan
(`infra/plan-offline.sh`) turns this switch on, so CI plans every attachment.

### Stage V: verify the sandbox
Invoke the operator verification task (Phase 4 WP-4B2,
`pickready.verify_code_execution_sandbox`) with `RequestResponse`: a canary per
language, a fork bomb, an infinite loop, a memory hog, an output flood, a
network attempt, a request asking for `enable_network=true`, an
unauthenticated request (must be refused) and `GET /languages` against
`JUDGE0_LANGUAGE_IDS`. Every line must be green.

### Stage C: turn it on
`CODE_EXECUTION_BACKEND=judge0` on the API, the task worker and the agent.
Deploy, verify by digest (`scripts/verify-deployment.sh`), run a demo-tenant
coding assessment end to end, and watch the alarms for thirty minutes.

## 4. Outage runbook

### The kill switch (always first if candidates are affected)
Set `CODE_EXECUTION_BACKEND=disabled` on the API, the task worker and the agent
and redeploy. Coding surfaces then say code execution is unavailable; nothing
waits on the sandbox. Infrastructure is untouched.

### Alarm `*-judge0-down` (liveness failing for three minutes)
1. Log group `/readypick/<env>/judge0-host`: the last `judge0-stack`,
   `judge0-preflight` and `judge0-health` lines.
2. `aws ec2 describe-instance-status --instance-ids <id>`: status checks.
3. The box is stateless. **Replace it**:
   `terraform apply -replace='module.code_sandbox[0].aws_instance.host[0]'`.
   In-flight runs are lost; the adapter reports each as
   `ExecutionTicketLost`, which the submission flow (WP-4B2) must treat as
   "submit again" rather than as a result. The Cloud Map record follows the
   new private address.

### Alarm `*-judge0-preflight-refused`
cgroup v1 is not in effect (a kernel update dropped the argument, or the AMI
was bumped to a release that no longer honours it). Judge0 is deliberately not
running. Replace the instance (the bootstrap re-applies `grubby`); if the
refusal repeats on a fresh host, the pinned AMI no longer supports cgroup v1:
pin the previous AMI and open the Ubuntu 22.04 fallback.

### Alarms `*-judge0-system-check` / `*-judge0-instance-check`
EC2 recovers (system) or reboots (instance) automatically; the preflight unit
decides on boot whether Judge0 may start. If the instance check keeps failing,
replace the instance.

### Alarm `*-judge0-cpu-credits` (queue saturation)
Standard credits throttle rather than bill, and a throttled box turns correct
solutions into time-limit failures. Short term: nothing to do if the balance
recovers. Sustained: raise `judge0_instance_type` (a replacement) or accept the
unlimited-credit cost explicitly. `GET /workers` (the provider's `health()`)
reports queue depth; the host caps the queue at `MAX_QUEUE_SIZE=500`, beyond
which a submission is `ExecutionUnavailable(reason="queue_full")`.

### Rotating the token
`terraform apply -replace='module.code_sandbox[0].random_password.token'`
writes a new secret version. The host reads the token at every stack start and
the callers at container start, so then replace the instance and redeploy the
API, the task worker (update its configuration so new execution environments
start) and the agent task definition. Between the two halves, Run answers the
unavailable sentence; submissions wait and are retried.

### Bumping the AMI or an image
Change `judge0_ami_id` or re-run the mirror for a new upstream tag and update
the digests. `user_data_replace_on_change` makes either a REPLACEMENT, which is
intended: the plan must show exactly one instance replaced. Run stage V again
before trusting the new host.

## 5. What this change does not include

- The verification task and the application probe (`WP-4B2`), and their log
  metric filters on the task worker and API log groups.
- The staging and production environments (they carry no sandbox).
