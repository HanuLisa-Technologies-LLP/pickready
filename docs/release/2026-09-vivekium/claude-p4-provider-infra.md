# CLAUDE.md section draft: Phase 4, packages 4A and 4E (code execution port and the Judge0 sandbox)

Draft for the orchestrator to merge into the release's CLAUDE.md section. It
covers only what `wip/p4-provider-infra` built.

### CANDIDATE CODE RUNS ON ONE HOST, AND THE DOMAIN NEVER KNOWS WHICH

`app/services/code_execution/` is a port and one adapter. Domain code imports
`provider` (or the package) and calls `get_provider()`; only
`code_execution/judge0.py` knows Judge0's paths, headers, status ids, language
ids and base64 transport. `tests/test_code_execution_architecture.py` enforces
it over the AST of all of `app/`: nothing outside the adapter, the port's one
backend switch and the settings may name the sandbox, no module outside the
package imports the adapter or the double, and nothing in the package can start
a process or evaluate a string.

- **Five operations, not one.** `run/submit/collect/discard/health`. `run` is
  the interactive convenience; a final submission must persist its ticket
  BEFORE it polls, so a worker killed mid-poll collects the same run rather
  than submitting the candidate's code twice.
- **A provider never receives an expected output.** `TestInput` is a key and a
  stdin, asserted by field set. Comparison is `outputs_match` in domain code
  (line endings normalised, trailing whitespace dropped, LEADING whitespace
  significant). A sandbox that could grade would hold the answer key.
- **`CODE_EXECUTION_BACKEND` is `judge0` or `disabled`, default disabled, never
  a fallback chain.** A missing URL or token is not a boot refusal; `is_enabled()`
  answers False and every surface must record "unavailable". The fake is NOT a
  backend value: it is installed only through `override_provider`, which
  refuses when `is_production` (the live pilot runs `ENVIRONMENT=production`).
- **Four errors, because the caller acts differently on each.**
  `ExecutionUnavailable` (retry; 401/403 are `reason="credential"` and logged
  at ERROR), `ExecutionRejected` (our bug, never retried),
  `ExecutionTicketLost` (the host was replaced or pruned the run: submit
  again), `ExecutionNotConfigured`.
- **Limits are sent on every run and REFUSED above the host caps, never
  clamped.** `limits.HOST_MAXIMA` mirrors the MAX_* values in
  `infra/modules/code_sandbox/judge0.conf.tftpl` and a test parses the template.
- **No log line carries source, stdin, stdout or the token**, asserted with
  sentinels across every logger. A candidate program can echo its stdin, and a
  hidden test's stdin is part of the answer key.

### THE SANDBOX HOST HOLDS NO APPLICATION SECRET, AND HAS NO WAY OUT

`infra/modules/code_sandbox`: one t3.medium on a pinned AL2023 AMI, Docker
under systemd, Judge0 CE 1.13.1 with its own Postgres and Redis.

- **No route out of the VPC.** The sandbox route table has the local route and
  a DEDICATED S3 gateway endpoint whose policy allows `s3:GetObject` on the ECR
  layer bucket and the AL2023 repository bucket only. That policy's `*`
  principal is declared in `check-no-wildcard-iam.py` with its reason.
- **The host role pulls three images, reads one token, writes one log group.**
  Nothing from `modules/secrets`.
- **Network ACL denies the data tier ahead of every allow; the host group
  admits 2358 from `judge0_client` only.** The shared `ecs` group is
  deliberately NOT the source, because the frontend and analysis service
  carry it.
- **IMDSv2, hop limit 1, and a DOCKER-USER drop** for 169.254.169.254.
- **cgroup v1 or nothing.** `judge0-preflight.service` refuses to start the
  stack unless `/sys/fs/cgroup` is tmpfs AND Docker reports cgroup version 1,
  and removes any leftover container. AWS calls cgroup v1 on AL2023
  unsupported; the preflight alarm is how that risk stays visible.
- **The endpoints group is extended through the network module's
  `endpoint_client_security_group_ids`, never a standalone rule.** Inline and
  standalone rules on one group overwrite each other on alternate applies.
- **Container logs never leave the box**; only the host's own log line file is
  shipped, because Judge0 request logs can carry hidden stdin.
- **`*.tftpl` is pinned to LF in `.gitattributes`**: a CRLF checkout on Windows
  would put a carriage return into every line of the host's bootstrap.

### DISABLED BY DEFAULT, STAGED BY SWITCH

`judge0_enabled` and `judge0_instance_enabled` default to false in pilot, and
an apply with defaults plans exactly the resources it did before (252 in the
offline plan, before and after). The offline plan tfvars turn BOTH on, with an
all-zero AMI and dummy digests, so CI plans every resource of the module.
`docs/operations/JUDGE0_RUNBOOK.md` is the staged rollout (A1 surroundings,
mirror, A2 instance, B client wiring, V verification task, C the flag) and the
outage runbook. The kill switch is `CODE_EXECUTION_BACKEND=disabled`.
