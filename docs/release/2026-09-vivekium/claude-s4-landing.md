# CLAUDE.md section draft: the landing page goes live (s4-landing, 2026-09-28)

Owner, verbatim: "remove the site under construction page and rewire the
landing page for readypick.ai". No migration, no backend behaviour change.

## What changed

- **`/` serves the landing page, unconditionally.** `app/page.tsx` renders
  `LandingPage` with the landing metadata; the `SiteUnderConstruction`
  component, the `NEXT_PUBLIC_LANDING_LIVE` read in the root page and in
  `app/(public)/layout.tsx`, the `landingLive` prop on `SiteHeader` and
  `SiteFooter`, the `.env.example` block and its `EXTERNAL` entry in
  `test_env_example_parity.py` left together. The variable had never been
  wired into any build path (Dockerfile, remote buildspec, deploy workflow),
  which is why production served the holding page.
  `tests/test_holding_page_removed.py` is the whitespace-normalised sweep and
  also asserts the root page reads no `process.env` and the frame takes no
  gate prop.
- **The landing copy describes the product as it ships**: JD, then Skills,
  then the final job posting, then publish (the SWOT is internal hiring
  intelligence and is never sold as a step); AI Match reads resumes against
  the saved skills and shows evidence tags and a word grade; one proctored
  assessment asks about every skill (typed or spoken prose, multiple choice,
  fill in the blank; no coding or sandbox claim, AMENDED 2026-09-28 because
  Judge0 is on hold); the team reads a PRISM Report
  and a Proctoring Report. The product tour's ranking bar (a score with its
  digits removed) became the evidence each resume shows.
- **An employer is GIVEN a workspace, so every employer call to action is
  Request access** (`lib/site.REQUEST_ACCESS_HREF`, the mailbox the site
  already published as its contact). `/register` is candidate sign-up; the
  old "Get started" sent employers there. Candidate paths are `/employers`
  (the directory of companies hiring, labelled as such) and `/register`.
  The pricing card sends a customer's team to `/org/billing` and everybody
  else to `/login?next=%2Forg%2Fbilling`.
- **One title, one description, one share card**: `LANDING_TITLE`,
  `SITE_DESCRIPTION` and `SHARE_CARD` in `lib/site.ts` are read by the root
  layout, `/` and `publicPageMetadata`. `/` restates the card image because a
  page that sets `openGraph` replaces the inherited object.
- **The generated share card spelled the previous product name**; it now
  draws Vivekium split as the site wordmark is.

## New hard rules

- **A link on a public page is a claim about where a click lands, and a test
  reads it.** `lib/landing-links.test.ts` walks every `href` and
  `router.push` literal in the landing page, its sections and the public
  frame: a page that exists under `app/`, admitted by `proxy.ts` without a
  session (the one declared signed-in target is `/org/billing`, reached
  through `/login?next` otherwise), an anchor the page mounts, the one
  request-access mailbox, no bare token-addressed route (`/join`,
  `/assessments/invite`, `/keep-profile`, `/verify-employment`) and no dead
  query parameter (`initial_context`, `role=candidate` were read by nothing).
- **A launch gate is removed with everything that reads it**, the same rule
  as a feature removal: the variable, every reader, the env documentation,
  its parity declaration and a sweep.
- **Truncated text inside a grid needs `min-w-0` (or `minmax(0, 1fr)`
  tracks)**: a truncated line's min-content is its FULL width, and an `auto`
  or `1fr` track grows to fit it. This pushed the hero past a 390px screen.
  **Never combine an aspect ratio with a minimum height** on a box that must
  fit a phone: the minimum height drags the width up through the ratio (the
  product tour was 544px wide on a 390px screen and clipped).

## Supersessions to mark in place

- `DEPLOYMENT_LOG.md`, `docs/verification/PRODUCTION_READINESS.md` and
  `docs/verification/SEO_REPORT.md` record that `/` served the holding page
  "as the owner requires". They are dated records and were NOT edited; the
  owner lifted that requirement on 2026-09-28. No CLAUDE.md section states the
  holding page as a rule, so none needs a marker.
- PRODUCT.md's "Operating Context" still describes the technical question bank
  and PPI framework review; it is the design tooling's context, not the
  landing page, and was left for the owner of that file.

## Open

- ~~The landing page states coding runs in a sandbox~~ SUPERSEDED 2026-09-28
  (owner: Judge0 is ON HOLD and its AWS resources are destroyed): the hero,
  features, how-it-works and pricing lines that said coding runs in a sandbox
  now name what ships (typed or spoken prose, multiple choice, fill in the
  blank). Spoken answers still depend on `transcribe_enabled`, which the
  deploy stage turns on.
- Request access is a `mailto:` to the published personal mailbox; a mailbox
  on the product's own domain is still the open `pickready.app` owner
  question.
- `/about` was brought onto the same standard in a follow-up: no "dedicated
  human validation on every profile" and no "flat job subscription". Its
  founder caption's middle dot is correct UTF-8 (C2 B7) in the source and in
  the built page; an earlier report of mojibake was a Windows PowerShell 5.1
  decoding artifact, and nothing was changed there.
- The product tour's report scene filled its chart with invalid CSS
  (`hsl(var(--teal-600) / ,.28)`), which fell back to the SVG default fill,
  black; the same malformed value sat in three backgrounds. All now use valid
  token syntax or Tailwind token utilities, and the leftover violet colours
  are teal and navy tokens.
