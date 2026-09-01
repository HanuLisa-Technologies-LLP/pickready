# Change 4 — Optional proctoring scaffold (flag OFF)

Date: 2026-08-07

Implementation/fix source: `ae608cd978ba4b6d8c30c20727a730f9d4495fcb`

Successful deployment run: `31206568795`

Production: backend `pickready-backend-00139-zik` 100%, frontend
`pickready-frontend-00134-six` 100%.

## Safeguards implemented

- Build-time frontend flag: `NEXT_PUBLIC_PROCTORING_ENABLED`; absent/false by
  default and explicitly false in `.env.local.example`.
- Backend config flag: `PROCTORING_ENABLED=false` by default.
- Consent UI describes webcam identity check and screen capture as optional.
- It explicitly says this is neither an interview nor a background check.
- Decline continues the existing assessment with zero scoring/ranking/
  eligibility penalty; proctoring state is not sent to any scoring endpoint.
- Media constraints request video only. Both camera and display requests set
  `audio: false`; tracks stop when the component unmounts.
- DPDP Act 2023 language covers withdrawal, access, correction and erasure,
  subject to applicable legal obligations.

## Tests

DOM tests cover flag-off absence, penalty-free decline, and video-only media
constraints. The normal assessment starts independently of this component,
which is the structural proof that declining cannot affect scoring.

The user waived live-browser checks and screenshots. The consent UI is tested
only with its explicit test/staging override; it remains absent from production.

Production environment inspection found neither `PROCTORING_ENABLED` nor
`NEXT_PUBLIC_PROCTORING_ENABLED`; both code defaults are false. Backend health,
including database connectivity, returned HTTP 200 after promotion.

## Deliberately unresolved legal items

1. Retention period.
2. Operational data-request process.
3. Full legal review.

No claim is made that any of these has been resolved.
