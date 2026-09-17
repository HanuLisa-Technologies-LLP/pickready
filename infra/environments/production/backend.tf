/**
 * Remote state, with locking.
 *
 * A FILE OF ITS OWN, deliberately, and the same shape as
 * `../pilot/backend.tf`. `terraform plan` refuses to run against an
 * uninitialised backend, and the offline planning profile has no credentials
 * and no network to initialise an S3 backend with, so `infra/plan-offline.sh`
 * plans a copy of this directory with this one file left out. That is the whole
 * reason it is not in `main.tf`.
 *
 * WHAT THIS FILE REPLACED, AND WHY ITS ABSENCE WAS THE WORST KIND OF DEFECT
 * -------------------------------------------------------------------------
 * `main.tf` carried this block COMMENTED OUT, with a note saying the bucket had
 * to be bootstrapped by hand first. That reasoning was sound when it was
 * written and the bucket did not exist. The bucket exists now -- pilot has been
 * applying against it since 2026-09-04 -- and the comment stayed.
 *
 * The consequence is not a warning. `.github/workflows/deploy.yml` runs a bare
 * `terraform init -input=false`, which with no backend block initialises the
 * LOCAL backend on an ephemeral runner. Every apply would therefore start from
 * EMPTY state: Terraform would try to create the whole environment from
 * scratch, collide on the globally unique S3 and ECR names, and then throw the
 * state away when the job ended. Nothing recovers from that except importing
 * every resource by hand. A commented-out backend block does not fail; it
 * silently deploys into nowhere.
 *
 * THE KEY IS DISTINCT PER ENVIRONMENT, and that is the one value here that
 * must never be copied between these files. Two environments sharing a key
 * means the second apply reads the first's state, decides every resource has
 * moved, and proposes destroying an entire environment.
 * `backend/tests/test_terraform_remote_state.py` asserts the distinctness,
 * because that is the property; this file is only today's instance of it.
 *
 * The bucket and the lock table were created by hand before any of this
 * existed, because Terraform cannot create the bucket that holds its own state.
 * They are NOT managed here and must not be: a Terraform run that could destroy
 * its own state store is one bad plan away from an unrecoverable environment.
 *
 * Neither name is a secret. The bucket blocks public access and its policy is
 * what protects it; naming it here is what makes two people running an apply
 * safe from each other.
 *
 * `region` is the BUCKET's region, not this environment's. One state bucket
 * serves all three environments, so this reads `ap-south-2` whatever region
 * `var.region` names.
 */

terraform {
  backend "s3" {
    bucket = "readypick-tfstate-rp-manju-0904"
    key    = "production/terraform.tfstate"
    region = "ap-south-2"
    # The DynamoDB table created during the manual bootstrap. Terraform 1.10+
    # can lock with an S3 object instead (`use_lockfile`), and this stays on the
    # table because the table is what exists: switching lock mechanisms while
    # somebody else holds a lock is how two applies end up running at once.
    dynamodb_table = "readypick-tfstate-lock"
    encrypt        = true
  }
}
