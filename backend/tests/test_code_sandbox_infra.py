"""The code sandbox's Terraform: isolation that must hold before anything runs.

Read from the source, the way `test_deploy_secret_hygiene.py` reads the rest of
the tree, because none of this can be observed from an account that has never
been applied to. Each assertion names a property the module docstring claims;
a claim with no assertion is the "true in prose only" failure this repository
has recorded more than once.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MODULE = REPO / "infra" / "modules" / "code_sandbox"
MAIN = (MODULE / "main.tf").read_text(encoding="utf-8")
CONF = (MODULE / "judge0.conf.tftpl").read_text(encoding="utf-8")
BOOT = (MODULE / "cloud-init.sh.tftpl").read_text(encoding="utf-8")
PILOT = REPO / "infra" / "environments" / "pilot"
NETWORK = REPO / "infra" / "modules" / "network"

#: EC2 refuses user data over this many bytes (before base64).
USER_DATA_LIMIT = 16384


def _code(text: str) -> str:
    """Terraform with comments removed, so prose cannot satisfy an assertion."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith(("#", "//")))


CODE = _code(MAIN)


def block(kind: str, type_: str, name: str, source: str = CODE) -> str:
    """The body of `<kind> "<type>" "<name>" { ... }`, by brace counting."""
    match = re.search(rf'{kind}\s+"{re.escape(type_)}"\s+"{re.escape(name)}"\s*\{{', source)
    assert match, f'{kind} "{type_}" "{name}" is not declared'
    depth = 0
    for index in range(match.end() - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.end() : index]
    raise AssertionError(f"unbalanced braces in {type_}.{name}")


def names_of(type_: str, source: str = CODE) -> list[str]:
    return re.findall(rf'resource\s+"{re.escape(type_)}"\s+"(\w+)"', source)


# ── The host is reachable from the client group only ─────────────────────────


def test_the_host_group_declares_no_inline_rule() -> None:
    """Inline and standalone rules on one group overwrite each other."""
    host = block("resource", "aws_security_group", "host")
    assert "ingress" not in host and "egress" not in host


def test_the_only_ingress_is_the_sandbox_port_from_the_client_group() -> None:
    ingress = [
        block("resource", "aws_vpc_security_group_ingress_rule", name)
        for name in names_of("aws_vpc_security_group_ingress_rule")
    ]
    assert len(ingress) == 1
    (rule,) = ingress
    assert "aws_security_group.host.id" in rule
    assert "referenced_security_group_id = aws_security_group.client.id" in rule
    assert "local.sandbox_port" in rule
    assert "cidr_ipv4" not in rule and "prefix_list_id" not in rule
    assert re.search(r"sandbox_port\s*=\s*2358\b", CODE)


def test_the_host_egress_is_the_endpoints_and_the_s3_prefix_list_only() -> None:
    host_egress = [
        block("resource", "aws_vpc_security_group_egress_rule", name)
        for name in names_of("aws_vpc_security_group_egress_rule")
        if re.search(
            r"^\s*security_group_id\s*=\s*aws_security_group\.host\.id",
            block("resource", "aws_vpc_security_group_egress_rule", name),
            re.MULTILINE,
        )
    ]
    assert len(host_egress) == 2
    targets = sorted(
        "endpoints" if "var.interface_endpoints_security_group_id" in rule else "s3"
        for rule in host_egress
        if re.search(r"prefix_list_id\s*=\s*aws_vpc_endpoint\.sandbox_s3\.prefix_list_id", rule)
        or "var.interface_endpoints_security_group_id" in rule
    )
    assert targets == ["endpoints", "s3"]
    for rule in host_egress:
        assert re.search(r"from_port\s*=\s*443\b", rule) and re.search(r"to_port\s*=\s*443\b", rule)
        assert "cidr_ipv4" not in rule


def test_the_client_group_only_reaches_the_sandbox_port() -> None:
    client = block("resource", "aws_security_group", "client")
    assert "ingress" not in client and "egress" not in client
    rule = block("resource", "aws_vpc_security_group_egress_rule", "client_to_host")
    assert "referenced_security_group_id = aws_security_group.host.id" in rule


# ── No route out, and S3 is not an exfiltration channel ──────────────────────


def test_the_sandbox_route_table_has_no_route_out() -> None:
    table = block("resource", "aws_route_table", "sandbox")
    assert "route" not in table.replace("route_table", "")
    assert not names_of("aws_route"), "a route resource would add a path out"
    for forbidden in ("aws_nat_gateway", "aws_internet_gateway", "aws_eip"):
        assert forbidden not in CODE, forbidden
    subnet = block("resource", "aws_subnet", "sandbox")
    assert "map_public_ip_on_launch = false" in subnet


def test_the_sandbox_s3_endpoint_reads_two_buckets_and_nothing_else() -> None:
    endpoint = block("resource", "aws_vpc_endpoint", "sandbox_s3")
    assert 'vpc_endpoint_type = "Gateway"' in endpoint
    assert "route_table_ids   = [aws_route_table.sandbox.id]" in endpoint
    assert "policy            = data.aws_iam_policy_document.sandbox_s3_endpoint.json" in endpoint
    policy = block("data", "aws_iam_policy_document", "sandbox_s3_endpoint")
    assert policy.count("statement") == 1
    assert re.search(r'actions\s*=\s*\["s3:GetObject"\]', policy)
    assert "local.ecr_layer_bucket_arn" in policy and "local.package_bucket_arns" in policy
    assert '"Allow"' in policy and '"Deny"' not in policy
    assert re.search(r'starport-layer-bucket/\*"', CODE)
    assert re.search(r'al2023-repos-\$\{var\.region\}-de612dc2/\*"', CODE)


def test_the_network_acl_denies_the_data_tier_before_any_allow() -> None:
    deny_numbers, allow_numbers = [], []
    for name in names_of("aws_network_acl_rule"):
        rule = block("resource", "aws_network_acl_rule", name)
        number = int(re.search(r"rule_number\s*=\s*(\d+)", rule).group(1))
        if '"deny"' in rule:
            assert "var.data_subnet_cidr_blocks" in rule
            deny_numbers.append(number)
        else:
            allow_numbers.append(number)
    assert deny_numbers and allow_numbers
    # count.index adds at most a handful; the bands must not meet.
    assert max(deny_numbers) + 10 < min(allow_numbers)
    for direction in ("deny_data_in", "deny_data_out"):
        assert direction in names_of("aws_network_acl_rule")
    inbound_port = block("resource", "aws_network_acl_rule", "sandbox_port_in")
    assert "var.private_subnet_cidr_blocks" in inbound_port and "local.sandbox_port" in inbound_port


# ── The instance ──────────────────────────────────────────────────────────────


def test_the_instance_is_private_imdsv2_hop_one_and_standard_credits() -> None:
    instance = block("resource", "aws_instance", "host")
    assert "associate_public_ip_address = false" in instance
    assert 'http_tokens                 = "required"' in instance
    assert "http_put_response_hop_limit = 1" in instance
    assert 'cpu_credits = "standard"' in instance
    assert "encrypted             = true" in instance
    assert "subnet_id                   = aws_subnet.sandbox.id" in instance
    assert "vpc_security_group_ids      = [aws_security_group.host.id]" in instance
    assert "key_name" not in instance, "no SSH key: the host is replaced, never logged into"
    assert "count = var.create_instance ? 1 : 0" in instance


def test_the_ami_is_a_pinned_variable_never_a_lookup() -> None:
    """A lookup would break the offline plan and could replace the host unplanned."""
    for path in MODULE.glob("*.tf"):
        code = _code(path.read_text(encoding="utf-8"))
        assert 'data "aws_ami"' not in code, path.name
        assert "aws_ssm_parameter" not in code, path.name
    assert "ami                         = var.ami_id" in block("resource", "aws_instance", "host")


def test_the_host_role_holds_one_secret_and_three_repositories() -> None:
    policy = block("data", "aws_iam_policy_document", "host")
    secret_resources = re.findall(r"resources\s*=\s*\[([^\]]*secretsmanager[^\]]*)\]", policy)
    assert "aws_secretsmanager_secret.token.arn" in policy
    # Every secret the host can read is the token, and it reads nothing from
    # the application's secrets module.
    assert "module.secrets" not in CODE and "secret_arns" not in policy
    assert secret_resources == [] or all("token" in r for r in secret_resources)
    assert "values(module.images.repository_arns)" in policy
    assert "log_group.host.arn" in policy
    assert '"*"' not in policy.replace('resources = ["*"]', "", 1), "only the registry login may name every resource"


# ── The sandbox configuration ─────────────────────────────────────────────────

REQUIRED_CONF = {
    "JUDGE0_TELEMETRY_ENABLE": "false",
    "ENABLE_NETWORK": "false",
    "ALLOW_ENABLE_NETWORK": "false",
    "ENABLE_CALLBACKS": "false",
    "ENABLE_COMPILER_OPTIONS": "false",
    "ENABLE_COMMAND_LINE_ARGUMENTS": "false",
    "ENABLE_ADDITIONAL_FILES": "false",
    "ENABLE_WAIT_RESULT": "false",
    "ENABLE_SUBMISSION_DELETE": "true",
    "SUBMISSION_CACHE_DURATION": "0",
    "ALLOW_ENABLE_PER_PROCESS_AND_THREAD_TIME_LIMIT": "false",
    "ALLOW_ENABLE_PER_PROCESS_AND_THREAD_MEMORY_LIMIT": "false",
    "MAX_SUBMISSION_BATCH_SIZE": "20",
    "COUNT": "2",
    "MAX_NUMBER_OF_RUNS": "1",
    "MAX_CPU_TIME_LIMIT": "5",
    "MAX_WALL_TIME_LIMIT": "10",
    "MAX_MEMORY_LIMIT": "524288",
    "MAX_MAX_PROCESSES_AND_OR_THREADS": "120",
    "MAX_MAX_FILE_SIZE": "4096",
}


def _conf() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in CONF.splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            assert key not in values, f"{key} is set twice"
            values[key] = value.strip()
    return values


@pytest.mark.parametrize("key,value", sorted(REQUIRED_CONF.items()))
def test_the_sandbox_configuration_sets_every_security_flag(key: str, value: str) -> None:
    assert _conf().get(key) == value


def test_the_configuration_template_holds_no_secret() -> None:
    conf = _conf()
    for secret in ("AUTHN_TOKEN", "AUTHZ_TOKEN", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "SECRET_KEY_BASE"):
        assert secret not in conf, secret


def test_the_batch_cap_matches_the_adapter() -> None:
    from app.services.code_execution import judge0

    assert int(_conf()["MAX_SUBMISSION_BATCH_SIZE"]) == judge0.MAX_SUBMISSION_BATCH_SIZE


# ── The bootstrap ─────────────────────────────────────────────────────────────


def test_judge0_never_starts_without_cgroup_v1() -> None:
    assert "stat -fc %T /sys/fs/cgroup" in BOOT
    assert "{{.CgroupVersion}}" in BOOT
    assert "verdict=refused" in BOOT and "exit 1" in BOOT
    assert 'grubby --update-kernel=ALL --args="systemd.unified_cgroup_hierarchy=0"' in BOOT
    stack_unit = BOOT[BOOT.index("judge0-stack.service <<'UNIT'") :]
    stack_unit = stack_unit[: stack_unit.index("UNIT\n", 20)]
    assert "Requires=docker.service judge0-preflight.service" in stack_unit


def test_the_bootstrap_carries_no_secret_and_ships_no_container_log() -> None:
    template_vars = set(re.findall(r"\$\{(\w+)\}", BOOT))
    assert template_vars == {
        "judge0_conf",
        "region",
        "token_secret_arn",
        "registry",
        "server_image",
        "postgres_image",
        "redis_image",
        "log_group_name",
    }
    # The token is read at every start into tmpfs, never templated in.
    assert "secretsmanager get-secret-value" in BOOT and "/run/judge0" in BOOT
    # Only the host's own log file is collected.
    assert BOOT.count('"file_path"') == 1 and "/var/log/judge0/judge0.log" in BOOT
    assert "/var/lib/docker" not in BOOT


def test_containers_cannot_reach_the_metadata_service() -> None:
    assert "iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP" in BOOT


def test_the_privileged_containers_exist_only_on_the_sandbox_host() -> None:
    """Judge0 needs privileged containers. Nothing else in the platform may."""
    assert "--privileged" in BOOT
    for path in (REPO / "infra").rglob("*.tf"):
        if ".terraform" in path.parts:
            continue
        assert not re.search(r"privileged\s*=\s*true", _code(path.read_text(encoding="utf-8"))), path


def _rendered_boot() -> str:
    """The user data as Terraform renders it, with representative values."""
    values = {
        "judge0_conf": CONF,
        "region": "ap-south-2",
        "token_secret_arn": "arn:aws:secretsmanager:ap-south-2:000000000000:secret:readypick-pilot/JUDGE0_AUTH_TOKEN-AbCdEf",
        "registry": "000000000000.dkr.ecr.ap-south-2.amazonaws.com",
        "server_image": "000000000000.dkr.ecr.ap-south-2.amazonaws.com/readypick-pilot/judge0@sha256:" + "0" * 64,
        "postgres_image": "000000000000.dkr.ecr.ap-south-2.amazonaws.com/readypick-pilot/judge0-postgres@sha256:" + "1" * 64,
        "redis_image": "000000000000.dkr.ecr.ap-south-2.amazonaws.com/readypick-pilot/judge0-redis@sha256:" + "2" * 64,
        "log_group_name": "/readypick/pilot/judge0-host",
    }
    assert "%{" not in BOOT and "$${" not in BOOT, "template directives this renderer does not model"
    return re.sub(r"\$\{(\w+)\}", lambda m: values[m.group(1)], BOOT)


def test_the_rendered_user_data_fits_the_ec2_limit() -> None:
    assert len(_rendered_boot().encode("utf-8")) < USER_DATA_LIMIT


def test_the_rendered_user_data_is_valid_bash(tmp_path: pathlib.Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not on PATH, so the syntax of the bootstrap cannot be checked here")
    script = tmp_path / "user-data.sh"
    script.write_bytes(_rendered_boot().encode("utf-8"))
    result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_the_templates_are_pinned_to_lf() -> None:
    """A CRLF checkout on Windows would ship a carriage return into every line."""
    attributes = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^\*\.tftpl\s+text\s+eol=lf", attributes, re.MULTILINE)


# ── Disabled by default ──────────────────────────────────────────────────────


def test_the_pilot_creates_nothing_unless_both_switches_are_turned_on() -> None:
    variables = (PILOT / "variables.tf").read_text(encoding="utf-8")
    for switch in ("judge0_enabled", "judge0_instance_enabled"):
        body = variables[variables.index(f'variable "{switch}"') :]
        body = body[: body.index("\n}") + 2]
        assert re.search(r"default\s*=\s*false", body), switch

    pilot = _code((PILOT / "main.tf").read_text(encoding="utf-8"))
    call = pilot[pilot.index('module "code_sandbox"') :]
    call = call[: call.index("\n}") + 2]
    assert "count  = var.judge0_enabled ? 1 : 0" in call
    assert "create_instance = var.judge0_instance_enabled" in call
    # The one change to an existing resource is gated by the same switch.
    assert (
        "endpoint_client_security_group_ids = var.judge0_enabled ? [module.code_sandbox[0].host_security_group_id] : []"
        in pilot
    )


def test_the_network_module_admits_extra_endpoint_clients_without_a_standalone_rule() -> None:
    network = _code((NETWORK / "main.tf").read_text(encoding="utf-8"))
    endpoints = block("resource", "aws_security_group", "endpoints", network)
    assert "concat([aws_security_group.ecs.id], var.endpoint_client_security_group_ids)" in endpoints
    variables = (NETWORK / "variables.tf").read_text(encoding="utf-8")
    body = variables[variables.index('variable "endpoint_client_security_group_ids"') :]
    assert re.search(r"default\s*=\s*\[\]", body[: body.index("\n}") + 2])


def test_the_offline_plan_exercises_the_whole_module() -> None:
    tfvars = (REPO / "infra" / "environments" / "offline-plan.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^judge0_enabled\s*=\s*true", tfvars, re.MULTILINE)
    assert re.search(r"^judge0_instance_enabled\s*=\s*true", tfvars, re.MULTILINE)
    assert re.search(r'^judge0_ami_id\s*=\s*"ami-0+"', tfvars, re.MULTILINE)
