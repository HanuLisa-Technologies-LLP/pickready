# Change 2 — Job action placement and bulk editing

Date: 2026-08-07  
Implementation: `62ec310e0253187d5e5b2b25e88a634814c9b50e`  
Deployment run: `31204355650`

- The matching and assessment-invitation actions render after
  `JobSetupReview` and before `PipelineFunnel` / `CandidateRankingTable`.
- AI matching has explicit `idle`, `running`, `done`, and `error` state,
  announced through a `role=status` element and exposed as `data-state`.
- Skills and competencies accept newline/comma pasted lists; the backend bulk
  endpoint accepts up to 100 names atomically, de-duplicates while preserving
  order, and applies the selected requirement level to every item. A 10-item
  paste is therefore supported without ten add dialogs.
- Frontend: 19 component tests PASS, ESLint PASS, production Next.js build PASS.
- Backend focused setup/RBAC tests PASS; full CI integration suite PASS.
- Staged no-traffic deploy and staged smoke PASS.
- Approved exact-revision promotion and production smoke PASS.
- Production traffic: backend `pickready-backend-00133-mey` 100%, frontend
  `pickready-frontend-00128-yef` 100%.

The user waived live-browser checking; DOM/source order, component tests,
production build, staged smoke, and production smoke are the verification
substitutes.
