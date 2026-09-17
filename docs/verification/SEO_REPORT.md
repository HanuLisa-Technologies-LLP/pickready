# SEO_REPORT.md

**Scope.** The public web surface of the ReadyPick Next.js frontend, plus the
indexability of everything that must stay OUT of the index.
**Date.** 2026-09-17. **Method.** Read of every route under `frontend/app/`,
then a production `next build` and a read of the rendered HTML, `robots.txt`
and `sitemap.xml`. Claims marked VERIFIED were checked against build output,
not against source.

**The one-line summary.** Before this pass the product had no `robots.txt`, no
sitemap, no canonical on any route, no social card, and **nothing anywhere
preventing a crawler from indexing the four authenticated portals or the three
tokenised links**. That last item is the finding that mattered; the rest is
ordinary SEO hygiene.

---

## Severity summary

| Severity | Count | Fixed | Deferred |
|---|---|---|---|
| CRITICAL | 2 | 2 | 0 |
| HIGH | 3 | 3 | 0 |
| MEDIUM | 4 | 4 | 0 |
| LOW | 2 | 1 | 1 |

---

## CRITICAL

### S1. Nothing prevented indexing of the authenticated portals or the tokenised links

**Affected.** `app/(org)/`, `app/(candidate)/`, `app/(super-admin)/`, `app/(bd)/`
(all four shells), `app/assessments/invite/[token]/`,
`app/verify-employment/[token]/`, `app/(candidate)/portal/outreach/[token]/`.

**Root cause.** Two independent gaps that combined. All four portal shells are
`"use client"`, and a Client Component **cannot** export `metadata`, so no page
inside any of them set `robots`. Separately, `next.config.js` `headers()` set
only HSTS, so there was no `X-Robots-Tag` either, and there was no `robots.txt`
to carry a disallow. The three tokenised routes are worse than the portals
because they are reachable with **no authentication at all**: they inherited the
root layout's fully indexable title and description.

**Fix.** A server-component `layout.tsx` was added at each route-group root
(`app/(org)/layout.tsx` and the three siblings) exporting
`robots: { index: false, follow: false }` and returning `children` untouched, so
every page beneath is covered by inheritance rather than by each page
remembering. The same was added beside each tokenised `page.tsx`. `app/robots.ts`
adds the disallow rules as defence in depth, which matters because a `noindex`
meta tag only works after a crawler has already fetched the page.

**Verification.** VERIFIED against built HTML: `noindex, nofollow` on `/org`,
`/portal`, `/admin`, `/bd`; `noindex` on `/login`, `/register`, `/join`; and
**no** robots meta on `/about`, so the fix did not over-apply.

### S2. No robots.txt and no sitemap existed anywhere

**Root cause.** Neither file was ever created, in either the `app/` convention
or `public/`.

**Fix.** `app/robots.ts` and `app/sitemap.ts`. The sitemap lists the seven
genuinely public URLs only and deliberately does **not** enumerate employer
slugs: that would need a build-time call to an API that may be down, and it
would publish the customer list. The reason is written into the file.

**Verification.** VERIFIED: both prerender, and their output was read.

---

## HIGH

### S3. No canonical URL on any route

**Root cause.** `metadataBase` was never set, so no relative canonical could
resolve, and no page declared `alternates.canonical`.

**Fix.** `metadataBase: new URL("https://readypick.ai")` in `app/layout.tsx`,
plus `alternates.canonical` on the root and every public page. This matters here
because real entry links carry query strings (`/login?initial_context=all`,
`/register?role=candidate`), and without a canonical each is indexable as a
distinct URL.

### S4. No Open Graph image and no Twitter card

**Root cause.** The `openGraph` block carried `type`, `siteName`, `title` and
`description` and no `images`; `twitter` was absent entirely.

**Fix.** `app/opengraph-image.tsx` generates a 1200x630 card with `next/og` on
the navy canvas with the teal accent. `public/` held no suitable asset, and one
was **not invented as a path to a file that does not exist**. A `twitter` block
with `summary_large_image` was added. `openGraph.images` is deliberately NOT set
in the config object: Next's file-based metadata takes precedence, so the
generated route wires `og:image` itself.

**Verification.** VERIFIED: the route prerenders to a real 43KB PNG, and
`og:image` plus `twitter:card` appear in the rendered HTML of `/`.

### S5. Every public nav and footer anchor was a dead link

**Affected.** `app/(public)/site-header.tsx` (`/#workflow`, `/#features`,
`/#pricing`) and `app/(public)/site-footer.tsx` (`/#how-it-works`, `/#features`,
`/#workflow`).

**Root cause.** Those fragment ids live inside the landing sections, and
`app/page.tsx` serves the deliberate "Site Under Construction" page, which mounts
none of them. Because both components are mounted by `app/(public)/layout.tsx`,
the dead links appeared on **every** public page.

**Fix.** The header and footer now read the same `NEXT_PUBLIC_LANDING_LIVE`
launch flag as the root page, so an anchor renders only when the section it
points at is actually mounted. No link is dead in either state.

---

## MEDIUM

### S6. `JobPosting` structured data was missing on the public job page
`app/apply/[job_uuid]/page.tsx` serves a genuinely public job posting and carried
no JSON-LD, so listings could not appear in Google's job surfaces. Added,
emitting **only** fields the payload actually carries: no salary, no
`employmentType`, no `jobLocation`, no `validThrough`, because that data does not
exist and inventing it would be a fabricated claim in machine-readable form.
Returns null when there is no description.

### S7. `Organization` structured data was missing on employer pages
Added to `app/(public)/employers/[slug]/employer-profile.tsx` from real fields
only. `industry` is carried as `knowsAbout`, because schema.org's `Organization`
has no `industry` property.

### S8. Heading hierarchy skipped a level on the most-trafficked public page
`app/apply/[job_uuid]/page.tsx` went `h1` straight to `h3`, because `CardTitle`
renders an `h3` and `Section` renders its title through it. Fixed with a
screen-reader-only `h2` per tab panel, which also gives the two force-mounted
panels the accessible names they lacked. Zero visual change.

### S9. `rel="noopener"` omitted on external links
Five files used `rel="noreferrer"` alone. Every current evergreen browser implies
`noopener` from `noreferrer`, so this was not exploitable; it was standardised to
`rel="noopener noreferrer"` for older embedded webviews and for consistency. A
sixth file, `components/bd/ai-reach.tsx`, had the same defect and was fixed too.

---

## LOW

### S10. Auth pages were indexable with no unique content
`/login`, `/register`, `/join` now carry `robots: { index: false }`. `follow` is
left alone so link equity still flows.

### S11. Employer page title is derived from the slug, not the fetched record. DEFERRED
`app/(public)/employers/[slug]/page.tsx` title-cases the slug in
`generateMetadata` to avoid a second network call. If a company's stored `name`
differs in casing or punctuation, the `<title>` and the on-page `<h1>` disagree
in a search result. Deferred deliberately: fixing it costs a server-side fetch on
every render of a public page, a real latency trade for a cosmetic
inconsistency.

---

## What was deliberately NOT done

- **`/apply/{id}` was left INDEXABLE.** Disallowing it would have blocked the
  crawl that the `JobPosting` structured data exists to feed, and would have left
  robots.txt and the page's own meta tag contradicting each other on one URL. The
  reasoning is recorded in `app/robots.ts`.
- **No `noindex` was removed from anything.** Every change in this pass adds
  restriction or adds metadata; nothing was made more visible.
- **No fabricated content.** The public pages were checked for invented
  statistics, fake customer logos and testimonials: there were none before this
  pass and there are none now. The pricing figures in `app/(public)/pricing.tsx`
  are real and documented in that file's own header; no number was changed.

---

## Known limitation, stated plainly

**Both JSON-LD payloads are client-rendered.** `apply/[job_uuid]/page.tsx` and
`employer-profile.tsx` are `"use client"` and fetch in `useEffect`, so the script
tag is not in the initial HTML. Google executes JavaScript and will see it; other
crawlers may not. Moving it server-side means moving the fetch, which is a larger
change than this pass took on. This is a real limitation, not a solved problem.

## Not measured

Lighthouse and Core Web Vitals were **not** run against a deployed environment in
this pass. Bundle-level work that plausibly helps LCP is recorded in
`PERFORMANCE_REPORT.md`, but no field or lab measurement was taken, so no claim
is made about a score moving.
