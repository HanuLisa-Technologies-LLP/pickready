# OFFLINE PLAN INPUTS. THESE ARE NOT DEPLOYMENT VALUES AND NEVER WILL BE.
#
# spec-doc6 §13.3 asks for a `terraform plan` that runs with no credentials, so
# that the configuration's internal consistency is checked by CI rather than
# asserted. A plan needs every required variable to have a value, and §D5 says
# every account-specific value is a variable with NO DEFAULT, so those two
# requirements meet here: this file supplies the shape without supplying a fact.
#
# EVERY VALUE BELOW IS DELIBERATELY IMPOSSIBLE.
#
#   account_id    all zeros, which is not an issuable AWS account number
#   region        `xx-plan-1` is syntactically a region and is not one. The
#                 provider only accepts it because the planning profile sets
#                 `skip_region_validation`, which is the point: a plan that
#                 succeeds here has demonstrably not consulted AWS.
#   domain_name   `.invalid` is reserved by RFC 2606 precisely so that it can
#                 never resolve. There is no domain to guess at, and §D5 says
#                 not to ask for one.
#   bucket names  contain `-never-created-` and would collide with nothing
#
# So a plan produced from this file cannot be applied by accident: the apply
# would fail at the first API call, in an account that does not exist, in a
# region that does not exist. Nothing here is a placeholder waiting to be filled
# in. The real values go in `terraform.tfvars`, which is gitignored, and every
# one of them is documented with the command that produces it in the
# environment's own README.md.
#
# WHAT A PLAN RUN AGAINST THIS FILE PROVES:
#   the configuration is internally consistent, the resource graph resolves,
#   every module input and output reference is real, and every resource argument
#   type-checks against the provider schema.
#
# WHAT IT DOES NOT PROVE:
#   anything at all about a real account. Not that the account can create these
#   resources, not that quotas suffice, not that IAM behaves as written, not
#   that the instance types are offered in the chosen region, not that the
#   domain resolves. It has never spoken to AWS. See `docs/DEPLOY_AWS.md`.

planning_profile = true

account_id         = "000000000000"
region             = "xx-plan-1"
availability_zones = ["xx-plan-1a", "xx-plan-1b"]

domain_name    = "offline-plan.invalid"
hosted_zone_id = "ZOFFLINEPLANNOTAREALZONE"

storage_bucket_name     = "readypick-never-created-storage"
access_logs_bucket_name = "readypick-never-created-alb-logs"
