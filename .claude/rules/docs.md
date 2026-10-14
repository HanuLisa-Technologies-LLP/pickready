---
paths:
  - "docs/**"
  - "*.md"
---
# Documentation (digest; full policy in docs/engineering/documentation-policy.md)

- Update the owning document, the register (if a decision moved) and any index in the same change.
- Current documents hold no obsolete text: move it to `docs/history/` as a short record (what, when, why obsolete, replaced by, `git show` ref). No "superseded" banners on current documents.
- Label status: current, decided-pending, proposed, measurement. "Reviewed" is not "verified". Never describe a pending decision as working.
- The latest dated owner statement wins; record it in `docs/decisions/REGISTER.md`. Unanswered questions go to `OPEN.md` and are never approved.
- Keep paths that tests or code read (table in docs/engineering/testing.md). Relative links, exact case, LF.
- No secrets. No claim that a live vendor call, deploy or test succeeded without evidence in `docs/verification/`.
