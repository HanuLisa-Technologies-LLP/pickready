# Change 1 — Private regional asset storage verification

Date: 2026-08-07  
Implementation: `cc47d48106ae82270a0eb628b9b0bc01ab4a0dc4`  
Deployment run: `31202031149`

## Release gates

- Backend CI, agent evaluation, frontend tests/lint/build: PASS.
- No-traffic staged deploy and staged smoke: PASS.
- Production approval, exact-revision promotion and production smoke: PASS.
- Production traffic after promotion:
  - backend `pickready-backend-00127-lom`: 100%
  - frontend `pickready-frontend-00122-nok`: 100%

## Storage controls

- Bucket: `pick-ready-503913-private-assets`
- Region: `ASIA-SOUTH1`
- Uniform bucket-level access: enabled
- Public access prevention: enforced
- Runtime identity has object-admin access; anonymous/public access is absent.
- Lifecycle: quarantine and migration staging objects delete after 30 days;
  incomplete multipart uploads abort after 7 days.
- Durable database references are `gs://` object references. Browsers receive
  bytes only through authenticated application endpoints.
- Resume delivery tokens expire after 300 seconds and bind both `profile_id`
  and `tenant_id`. Unit tests prove expiry and cross-tenant/profile rejection.

## Production migration

The prompt estimated 36 legacy assets. The authoritative production query
found 35 resume-bearing profile rows and zero compliance-document rows.

- Migrated rows: 35/35
- Unique content-addressed GCS objects: 32
- Post-copy SHA-256 downloads matching database checksums: 35/35
- Profiles with `resume_storage_provider = 'cloudinary'`: 0
- Resume URLs containing `cloudinary`: 0
- GCS profiles missing preserved legacy public-id mapping: 0

Cloudinary was excluded from subsequent runtime secret mounts and removed from
the application dependency/configuration after these checks reached zero.
Compliance uploads (there were no legacy rows) now use the same private bucket
under the `compliance/` prefix and download through authorized API responses.

## Non-browser verification

The user waived live-browser checking. Inline/attachment behavior is covered by
API/component tests, storage access by production database and object checksum
checks, and the deployed application by staged and production smoke tests.
