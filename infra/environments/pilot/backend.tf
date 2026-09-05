/**
 * Remote state, with locking.
 *
 * A FILE OF ITS OWN, deliberately. `terraform plan` refuses to run against an
 * uninitialised backend, and the offline planning profile has no credentials
 * and no network to initialise an S3 backend with, so `infra/plan-offline.sh`
 * plans a copy of this directory with this one file left out. That is the whole
 * reason it is not in `main.tf`.
 *
 * The bucket and the lock table were created by hand before any of this
 * existed, because Terraform cannot create the bucket that holds its own state.
 * They are NOT managed here and must not be: a Terraform run that could destroy
 * its own state store is one bad plan away from an unrecoverable environment.
 *
 * Neither name is a secret. The bucket blocks public access and its policy is
 * what protects it; naming it here is what makes two people running an apply
 * safe from each other.
 */

terraform {
  backend "s3" {
    bucket = "readypick-tfstate-rp-manju-0904"
    key    = "pilot/terraform.tfstate"
    region = "ap-south-2"
    # The DynamoDB table created during the manual bootstrap. Terraform 1.10+
    # can lock with an S3 object instead (`use_lockfile`), and this stays on the
    # table because the table is what exists: switching lock mechanisms while
    # somebody else holds a lock is how two applies end up running at once.
    dynamodb_table = "readypick-tfstate-lock"
    encrypt        = true
  }
}
