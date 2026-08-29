"""No wildcard IAM in the Terraform (spec-doc6 §10.1 rule 7).

    "No wildcard IAM, no `"Resource": "*"`, no shared broad role.
     CI-checked in Terraform."

WHY THIS PARSES STATEMENTS INSTEAD OF GREPPING FOR AN ASTERISK
---------------------------------------------------------------
A wildcard looks IDENTICAL whether it is over-broad or exactly right. That
observation is the foundation of the per-service IAM design in this repository,
and it applies to the checker as well as to the policies: a grep for `"*"`
reports four things here, all four are correct, and a check whose output is
entirely false positives is one everybody learns to scroll past. That is worse
than no check, because it occupies the slot a real one would have.

So the rule this enforces is narrower and true:

    A WILDCARD IS A FINDING WHEN IT WIDENS A GRANT.
    It is not a finding when it widens a REFUSAL.

Three consequences, and each has real examples in this tree:

  effect = "Deny"     A `Deny` on `s3:*` over a bucket, conditioned on
                      `aws:SecureTransport = false`, refuses every S3 operation
                      over plaintext. Narrowing that action list would let the
                      operations nobody listed through in the clear. The
                      wildcard is the point of the statement.

  resource-less       Some IAM actions have no resource type at all.
  actions             `ecr:GetAuthorizationToken` returns a registry token and
                      grants access to no image; `ssmmessages:*` opens an SSM
                      channel. Writing a real ARN in `resources` for these does
                      not narrow the grant, it makes the statement match nothing
                      and the call fail. The allowlist below is exhaustive and
                      each entry carries its reason.

  managed policies    `AmazonECSTaskExecutionRolePolicy` contains no visible
                      asterisk in this repository and grants ECR pull on EVERY
                      repository in the account, which is precisely how a
                      staging task ends up able to pull a production image.
                      `modules/ecs` writes its own scoped equivalent. A grep for
                      `*` would never have caught it, so it is checked by name.

Adding an allowlist entry means editing this file, which means somebody reviews
the reason. That is the intended cost.

    python infra/check-no-wildcard-iam.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent

#: IAM actions that genuinely have no resource form. An ARN here would make the
#: statement match nothing.
RESOURCELESS_ACTIONS = {
    "ecr:GetAuthorizationToken": "returns a registry auth token; grants access to no image. The pull actions beside it in the same policy ARE scoped to this environment's repository ARNs.",
    "ssmmessages:CreateControlChannel": "opens an SSM Session Manager channel; the AWS API defines no resource type for it.",
    "ssmmessages:CreateDataChannel": "opens an SSM Session Manager channel; the AWS API defines no resource type for it.",
    "ssmmessages:OpenControlChannel": "opens an SSM Session Manager channel; the AWS API defines no resource type for it.",
    "ssmmessages:OpenDataChannel": "opens an SSM Session Manager channel; the AWS API defines no resource type for it.",
}

#: RESOURCE POLICIES, where `resources = ["*"]` does not mean "every resource".
#:
#: An identity policy's `Resource` names what the holder may act on, so `*` there
#: is the whole account. A RESOURCE policy is attached TO one resource, and its
#: `Resource` element can only mean that resource: `*` in a KMS key policy is the
#: key itself, and KMS rejects a key policy naming any other ARN.
#:
#: The `kms:*` action on the account root is not a convenience either. KMS does
#: not let an IAM policy grant access to a key whose own policy does not delegate
#: to the account, so a key policy without that statement produces a key nobody
#: can use, manage or delete, ever. AWS returns a specific error for it.
#:
#: Keyed by document name so this stays an enumeration rather than a rule about
#: shapes: a new resource policy has to be added here, with its reason.
RESOURCE_POLICY_DOCUMENTS = {
    "kms": "a KMS key policy. Its `Resource` can only be the key it is attached to, and the `kms:*` grant to the account root is what makes the key manageable at all.",
}

#: AWS managed policies whose grant crosses the whole account. Checked by name,
#: because none of them contains an asterisk anything could grep for.
ACCOUNT_WIDE_MANAGED_POLICIES = (
    "AmazonECSTaskExecutionRolePolicy",
    "AdministratorAccess",
    "PowerUserAccess",
    "AmazonS3FullAccess",
    "SecretsManagerReadWrite",
    "AmazonEC2FullAccess",
    "IAMFullAccess",
)

_STATEMENT = re.compile(r"^(\s*)statement\s*\{", re.MULTILINE)
_WILDCARD_ACTION = re.compile(r'"([a-z0-9-]*:)?\*"')


def _statements(source: str) -> list[tuple[int, str]]:
    """Every `statement { ... }` block, as (1-based start line, body).

    Brace counting rather than a regex over the whole block: an IAM statement
    contains nested `condition` and `principals` blocks, and a non-greedy match
    to the first `}` would cut the statement in half and read its `resources`
    line as belonging to the next one.
    """
    found: list[tuple[int, str]] = []
    for match in _STATEMENT.finditer(source):
        index = source.index("{", match.start())
        depth = 0
        for position in range(index, len(source)):
            character = source[position]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    found.append((source.count("\n", 0, match.start()) + 1, source[index : position + 1]))
                    break
    return found


def _field(body: str, name: str) -> str:
    """The raw text of one `name = ...` assignment inside a statement body."""
    match = re.search(rf"\b{name}\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", body, re.DOTALL)
    return match.group(1) if match else ""


def main() -> int:
    findings: list[str] = []

    for path in sorted(ROOT.rglob("*.tf")):
        if ".terraform" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT.parent)

        for line, body in _statements(source):
            # Which policy document does this statement belong to? A resource
            # policy is judged by a different rule; see RESOURCE_POLICY_DOCUMENTS.
            owner = None
            for match in re.finditer(r'data\s+"aws_iam_policy_document"\s+"(\w+)"', source):
                if source.count("\n", 0, match.start()) + 1 <= line:
                    owner = match.group(1)
            if owner in RESOURCE_POLICY_DOCUMENTS:
                continue

            effect = _field(body, "effect")

            # A wildcard inside a Deny widens the REFUSAL. See the docstring.
            if '"Deny"' in effect:
                continue

            actions = _field(body, "actions")
            resources = _field(body, "resources")
            listed = set(re.findall(r'"([^"]+)"', actions))

            if _WILDCARD_ACTION.search(actions):
                findings.append(
                    f"{relative}:{line} allows a wildcard ACTION {actions.strip()}. "
                    f"That grants every operation the service will ever add."
                )

            if re.search(r'"\*"', resources):
                unexplained = listed - RESOURCELESS_ACTIONS.keys()
                if unexplained:
                    findings.append(
                        f"{relative}:{line} allows resources = [\"*\"] for "
                        f"{sorted(unexplained)}, which are not on the "
                        f"resource-less allowlist in this file."
                    )

            # A wildcard PRINCIPAL on an Allow is a resource policy open to the
            # world. On a Deny it is "deny everyone", which is why the Deny
            # short-circuit above comes first.
            principals = _field(body, "identifiers")
            if re.search(r'"\*"', principals):
                findings.append(
                    f"{relative}:{line} allows a wildcard PRINCIPAL, which "
                    f"grants this resource to every AWS account."
                )

        for policy in ACCOUNT_WIDE_MANAGED_POLICIES:
            for match in re.finditer(rf"policy/(service-role/)?{policy}\b", source):
                findings.append(
                    f"{relative}:{source.count(chr(10), 0, match.start()) + 1} "
                    f"attaches the account-wide managed policy {policy}. "
                    f"Write the scoped equivalent instead: this one grants "
                    f"across every resource in the account."
                )

    # A hand-written policy JSON string bypasses `aws_iam_policy_document`
    # entirely, so neither of the checks above would see it.
    for path in sorted(ROOT.rglob("*.tf")):
        if ".terraform" in path.parts:
            continue
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'"Resource"\s*:\s*"\*"', text):
                findings.append(
                    f"{path.relative_to(ROOT.parent)}:{number} is an inline "
                    f"policy JSON with Resource: \"*\"."
                )

    if findings:
        print("Wildcard IAM findings (spec-doc6 §10.1 rule 7):\n")
        for finding in findings:
            print(f"  {finding}")
        print(
            "\nA wildcard looks identical whether it is over-broad or exactly "
            "right. Scope the resource, or add the action to "
            "RESOURCELESS_ACTIONS in this file with the reason it has no "
            "resource form."
        )
        return 1

    print(
        "No wildcard IAM grant. Deny statements and the "
        f"{len(RESOURCELESS_ACTIONS)} documented resource-less actions are the "
        "only asterisks in the tree."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
