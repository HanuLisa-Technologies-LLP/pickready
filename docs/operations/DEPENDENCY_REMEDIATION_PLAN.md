# Frontend dependency remediation plan
**Status:** proposed, **not approved**. Nothing described here has been done. **Measured:** 2026-10-14 from the code-scanning alerts of the `Security scan` job on pull request 16 (Trivy over `frontend/package-lock.json`). Owner approval is required before any dependency changes (OPEN Q-33).

## Why this matters now

The required `Security scan` fails on any fixable CRITICAL or HIGH finding. Advisories published after the last green run on `main` (2026-09-30) now fail it, so **every pull request into `main` is blocked, including documentation-only ones**. The lockfile is identical on `main`, so the same findings apply to the code that is deployed. Not bypassed, not excepted in `.trivyignore.yaml`.

Highest risk: `next/og` `ImageResponse` has a remote code execution advisory. The app renders three routes with it: `app/icon.tsx`, `app/apple-icon.tsx`, `app/opengraph-image.tsx`.

## Findings (15) and fixes

| Package (installed) | Kind | Findings | Fixed in | Blocks the gate | Change needed |
|---|---|---|---|---|---|
| `next` 16.3.5 | direct, `^16.3.5` | GHSA-vcvr-r3jv-pc5j (critical, RCE in `next/og`); CVE-2026-94483 (high, SSRF in image optimisation); CVE-2026-94484, -94485, -94543, -94544 (medium); CVE-2026-94486 (low) | 16.3.6 (critical); 16.3.8 (all seven) | yes | Lockfile only: the range already allows 16.3.8 |
| `sharp` 0.35.4 | direct, `^0.35.4`, also an override `$sharp` | GHSA-wq5f-xc86-pv6w (high, librsvg) | 0.35.5 | yes | Lockfile only |
| `source-map-js` 1.2.1 | transitive (`postcss`) | CVE-2026-93749 (high, DoS) | 1.2.2 | yes | Lockfile only |
| `@grpc/grpc-js` 1.9.16 | transitive (`@firebase/firestore`, from `firebase ^11.10.0`) | CVE-2026-101916 (high, authentication bypass); CVE-2026-101915 (low) | 1.13.6 or 1.14.5 | yes | Needs an `overrides` entry or a newer `firebase`; the installed range probably stops at 1.9.x (verify). The app imports no Firestore code |
| `postcss-selector-parser` 6.1.4 | transitive (`tailwindcss` 3, `postcss-nested`) | CVE-2026-104844 (medium) | 7.1.6 | no (medium) | Major version; defer or schedule with a Tailwind upgrade |
| `dompurify` 3.4.13 | transitive (`monaco-editor 0.56.0`), pinned by override | GHSA-p98j-92pf-mc4p, GHSA-6688-9rhm-gjv2 (low) | 3.4.16 | no (low) | Raise the exact override pin |
| `braces` 3.0.3 | transitive (`chokidar`, `micromatch`) | CVE-2026-93687 (high, DoS) | none published | no (the gate ignores unfixed) | None possible; track and re-scan |

Gate-blocking work is four packages (`next`, `sharp`, `source-map-js`, `@grpc/grpc-js`). Three are semver-compatible lockfile updates; `@grpc/grpc-js` is the one with compatibility risk.

## Compatibility risks

- **Next 16.3.5 to 16.3.8** is a patch release. Watch `proxy.ts` (generated routes with no extension must stay listed), image optimisation config, and the three `ImageResponse` routes.
- **`sharp` 0.35.5** is a patch; it is also pinned through an override, so confirm one copy installs.
- **`@grpc/grpc-js` 1.9 to 1.13 or 1.14** is a minor jump under a package that expects 1.9. Safe in practice only because nothing in the app uses Firestore; verify that sign-in (Firebase auth) still loads, since `firebase` is imported only for identity.
- **Lockfile churn.** Review the lockfile diff for unrelated version moves; accept only the packages above.

## Validation (before the pull request is opened for merge)

1. Branch from current `main`, one commit per concern, dependency files only.
2. `npm ci`, then `npm audit --omit=dev` and a local Trivy filesystem scan with the same flags as CI (`severity CRITICAL,HIGH`, `ignore-unfixed`): zero fixable findings.
3. Frontend unit tests, the design gate (`frontend/scripts/impeccable-gate.mjs`) and `next build`.
4. Request `/icon`, `/apple-icon`, `/opengraph-image` and a signed-out page; confirm 200 and a sign-in round trip.
5. CI: all ten required checks green, including `Golden end-to-end journey` and `Security scan`.

## Deployment impact

Frontend image only. No migration, no backend or infrastructure change. Merging to `main` starts `deploy.yml`; whether pilot rebuilds depends on `PILOT_DEPLOY_ENABLED`. Rollback: revert the commit, or redeploy the previous image digest.

## Order

1. Owner approves this plan.
2. Dependency pull request, reviewed and merged on its own.
3. Pull request 16 (documentation) takes `main` in, the Security scan passes, and it merges.
4. Delete this plan, add a one-paragraph record to `docs/history/AUDITS.md`, and close OPEN Q-33 in the same change.
