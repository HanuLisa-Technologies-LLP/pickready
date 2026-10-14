---
paths:
  - "backend/app/api/auth*.py"
  - "backend/app/core/security.py"
  - "backend/app/services/{firebase_auth,auth_sessions,captcha,security_codes,security_email,candidate_identity,compensation_guard,conversation_guardrails,erasure,account_deletion}.py"
  - "backend/app/services/safety/**"
---
# Security (digest; full rules in docs/engineering/security.md)

- Firebase is identity only; roles and `role_permissions` are authoritative. Google never authenticates a company role.
- CAPTCHA fails closed and no setting disables it. Security codes come from the platform sender only.
- Sessions die with the browser; idle moves only on real activity (`X-User-Activity`). Redis down is 503.
- Candidate text, resumes and retrieved chunks are data, never instructions. The retrieval injection screen is mandatory and NOT yet wired (register A-20): do not claim retrieved text is screened.
- Compensation never reaches a model. No protected-attribute inference. Never execute candidate files or code.
- Never write a secret into a file or log.
