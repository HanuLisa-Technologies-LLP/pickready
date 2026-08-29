# tflint configuration (spec-doc6 §11.3).
#
# WHAT TFLINT CATCHES THAT THE OTHER TWO GATES DO NOT
# -----------------------------------------------------
# The three Terraform gates in CI answer different questions, and running all
# three is not redundancy:
#
#   terraform validate    Is this configuration internally consistent?
#   terraform plan        Does the graph resolve, and do the arguments
#                         type-check against the provider schema?
#   tflint                Is this valid Terraform that AWS will nonetheless
#                         refuse, and is it written the way this repository
#                         writes Terraform?
#
# The AWS ruleset is the part that earns its place. An invalid instance type, a
# resource name over its length limit, an invalid engine version: all of them
# pass `validate` and `plan` cleanly and fail at APPLY, in an account, halfway
# through creating an environment. That is the most expensive place to find out,
# and it is precisely the class the offline plan in §13.3 is honest about not
# being able to reach.

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.44.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"

  # `deep_check` would call AWS to validate ids against the real account. Off,
  # for the same reason the planning profile exists: this must run with no
  # credentials at all.
  deep_check = false
}

config {
  call_module_type = "all"
}

# ── Naming ───────────────────────────────────────────────────────────────────
#
# snake_case for every declaration. Enforced rather than assumed because the
# module boundary is where a naming slip becomes a readability problem: a caller
# reads `module.alb.target_group_arns` and should not have to check which
# convention that module happened to use.
rule "terraform_naming_convention" {
  enabled = true
  format  = "snake_case"
}

# ── Documentation ────────────────────────────────────────────────────────────
#
# Every variable and output carries a description. This is the rule doing the
# most work in this repository: the variables ARE the interface between the
# owner and the infrastructure, and spec-doc6 §D5 makes seven of them values
# nobody has supplied yet. A variable with no description is a value somebody
# will guess at.
rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

# ── Structure ────────────────────────────────────────────────────────────────

rule "terraform_required_version" {
  enabled = true
}

rule "terraform_required_providers" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}

rule "terraform_comment_syntax" {
  enabled = true
}

# `terraform_module_pinned_source` is DISABLED, and this is the one exception
# worth explaining rather than leaving as a silent omission.
#
# It requires a module source to be pinned to a version or a commit. That is
# right for a module pulled from a registry, where the source can change under
# you. Every module here is a RELATIVE PATH inside this repository
# (`../../modules/alb`), so it is already pinned by the commit that contains it:
# there is no version to drift to, and the rule would ask for a ref on a path
# that cannot have one.
rule "terraform_module_pinned_source" {
  enabled = false
}
