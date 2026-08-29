# ReadyPick — DESIGN.md

The design system for ReadyPick's product surfaces. Authored from the brand
foundation in spec-doc5 §C.1, in the nine-section structure the
`awesome-design-md` collection uses.

**On the borrowed structure.** `github.com/voltagent/awesome-design-md` is a
library of *other companies'* extracted design systems — Stripe, Linear, Vercel.
There is no ReadyPick entry and none was copied. What is borrowed is the
nine-section format. Linear and Vercel were read for **restraint**, not for
palette: they are the closest comparable feel to what an evidence-driven
enterprise HR platform should project, and neither of their colour systems
appears anywhere below.

**This file is the source of truth for tokens.** `frontend/app/globals.css` is
its implementation and `scripts/check-contrast.mjs` is its enforcement. If the
three disagree, this file is wrong or the CSS is — resolve it, do not leave
them disagreeing.

---

## 1. Visual Theme & Atmosphere

ReadyPick is a B2B enterprise hiring-intelligence platform sold to CHROs and
hiring managers, positioned against traditional executive search. Every screen
is read by somebody making a decision about a person's career, often with the
candidate's own words on the same page.

**The interface should read as confident and precise, not playful.** Three
consequences follow, and they are constraints rather than preferences:

- **Evidence is the subject, chrome is not.** A grade, a remark and the answer
  it rests on are the content. Anything competing with them — a gradient across
  a card, an icon tile above every heading, a decorative illustration — is
  taking attention from the thing the page exists to show.
- **Confidence is shown by restraint.** The product's most defensible claim is
  that it can say *why*. A UI that shouts undercuts that; a UI that lays the
  evidence out calmly supports it.
- **Nothing decorative moves.** Motion is used to explain a transition, never to
  entertain. A report is not a place for delight.

The one distinctive geometric idea is the **interlocking shared stroke** of the
R+P monogram — a navy R and a teal P that share an edge. It informs interface
details (a paired element where two things meet at a shared boundary; a rule
that changes colour where a section changes ownership) rather than being
sprinkled as a motif.

**What this is not.** Not a consumer app, not a dashboard-as-cockpit, not a
data-viz showcase. Density is fine. Ornament is not.

---

## 2. Colour Palette & Roles

### Brand foundation

Sampled from `logo300.jpeg` by weighted centroid over each colour cluster —
not estimated, and not read off a single pixel, because the source is JPEG and
a single pixel is compression noise.

| Role | Hex | Measured centroid | Cluster size | spec-doc5 §C.1 |
|---|---|---|---|---|
| **Primary — Navy** | `#012654` | `#012653` | 102,974 px | `#012654` (Δ 0,0,−1) |
| **Secondary — Teal** | `#00888A` | `#01888C` | 48,891 px | `#00888A` (Δ +1,0,+2) |
| Background | `#FFFFFF` | — | 87% of the mark | Off-white / white |

Both measurements land inside the spec's stated ±2 per channel. The spec's
values are used verbatim, since they are inside the measured cluster and are the
number the client stated.

### The ramps

Navy carries hue 213 at 98% saturation; teal carries hue 181 at 100%. Both
ramps hold their hue and move lightness, so a tint of navy still reads as navy
rather than drifting toward a generic blue.

| Token | Hex | Contrast on white | Use |
|---|---|---|---|
| `navy-50` | `#EFF4FB` | 1.11 | Selected row, subtle fill |
| `navy-100` | `#D7E4F4` | 1.29 | Hover fill on a navy surface |
| `navy-200` | `#A7C3E7` | 1.81 | Borders on navy fills |
| `navy-400` | `#0360D4` | 5.79 | Link on white, focus ring |
| `navy-500` | `#02408D` | 9.90 | Hover on primary |
| **`navy-600`** | **`#012654`** | **14.93** | **Primary action, active nav, brand mark** |
| `navy-700` | `#011E42` | 16.59 | Pressed |
| `navy-900` | `#000E1E` | 19.42 | Dark-theme canvas |

| Token | Hex | Contrast on white | Use |
|---|---|---|---|
| `teal-50` | `#EBFAFA` | 1.07 | Evidence-supported fill |
| `teal-100` | `#CEF2F3` | 1.19 | Accent fill |
| `teal-400` | `#00C9CC` | 2.05 | Dark-theme accent, chart series 2 |
| `teal-500` | `#009799` | 3.57 | Large text only (≥ 24px) |
| **`teal-600`** | **`#00888A`** | **4.30** | **Fills, rules, icons — see the warning** |
| `teal-700` | `#006F70` | 5.99 | **Teal text on white uses this** |
| `teal-900` | `#003738` | 13.10 | Dark-theme teal text |

> **The brand teal does not pass AA for body text on white.** `#00888A` measures
> **4.30:1**, below the 4.5:1 WCAG AA needs for normal text. This is a measured
> fact about the brand colour, not a preference, and it is the single most
> important line in this file because the mistake it prevents — teal labels on
> white cards — is the one a designer reaches for first.
>
> The rule: **teal-600 is a FILL, a RULE and an ICON colour. Teal TEXT on white
> is `teal-700`.** `scripts/check-contrast.mjs` computes both ratios from the
> tokens and fails the build rather than trusting anyone to remember.

### Semantic roles

| Role | Light | Dark | Note |
|---|---|---|---|
| `--canvas` page background | `#FBFCFE` | `#000E1E` (navy-900) | Dark canvas is navy, not neutral grey — the palette is navy-forward and a grey canvas would orphan it |
| `--surface` card | `#FFFFFF` | `#011E42` (navy-700) | |
| `--ink` all text | `#080F1C` | `#FFFFFF` | **Text is never grey** — see §3 |
| `--border` | `#E2E8F2` | `#0A2E5C` | Non-text only |
| `--primary` | navy-600 | navy-200 | Primary action |
| `--accent` | teal-600 | teal-400 | Evidence, corroboration, the shared-stroke rule |
| `--destructive` | `#B4232A` | `#FF8A8A` | |
| `--warning` | `#8A5A00` | `#F5C86B` | Held for review |

### Hard rules

1. **No purple, no violet, no indigo, anywhere.** The palette that preceded this
   one was indigo-violet `#5028E0`, which is precisely the tell Impeccable's
   `ai-color-palette` detector flags — and it flagged four call sites in this
   repo before the change. Replacing it is the point of the recolour.
2. **No gradients between two hues.** A single-hue tint (navy-600 → navy-500)
   is permitted for a hero surface. Blue-to-purple is the specific thing being
   removed and must not return in another pairing.
3. **Navy and teal are not interchangeable.** Navy is STRUCTURE — actions,
   navigation, the frame. Teal is EVIDENCE — what is corroborated, what is
   cited, where a claim rests on something. Using teal for a primary button
   would spend the one colour that carries meaning on the one element that
   needs none.
4. **A grade is a word, never a colour alone.** The four grades may be tinted,
   but colour never carries the grade on its own — that would put a hiring
   decision behind colour vision.

---

## 3. Typography Rules

| Role | Family | Weight | Size / leading |
|---|---|---|---|
| Display | Fraunces | 600 | 40/44, −0.02em |
| Page title | Inter Tight | 620 | 28/34, −0.015em |
| Section | Inter Tight | 600 | 20/28 |
| Body | Inter Tight | 400 | 15/24 |
| Small / label | Inter Tight | 500 | 13/18, +0.01em |
| Reference code, IDs | JetBrains Mono | 500 | 13/18 |

**Not Inter-as-default.** Impeccable flags default Inter as a slop tell and it is
right about the reason: it is the typeface a UI reaches for when nobody chose
one. **Inter Tight** is the working face — tighter, more editorial, and it holds
a dense table better at 13px. **Fraunces** appears on display type only, where
the product has one chance to look like it was designed. **JetBrains Mono**
carries the COMPANY-JOB-CANDIDATE reference code, which must be select-all and
transcribable by eye.

**Text is never grey.** A client decision from 2026-07-27 and it holds: every
text token resolves to pure ink in light and pure white in dark, including
`--muted-foreground`, which shadcn uses for descriptions, hints and table
headers. Grey survives only on borders, input outlines and muted *backgrounds*.
The single documented exception is `::placeholder`, dimmed so an empty field
cannot be mistaken for a filled one. Enforced at the TOKEN, never at the call
site — the shadcn primitives are not hand-edited, so the variable they already
read is the only place a rule can be applied once.

**No em dashes in any string** — labels, helper text, empty states, toasts,
emails, page titles, generated JD text or seeded content. A standing rule since
2026-07-28, enforced by `tests/test_platform_audit.py`.

---

## 4. Component Stylings

**Buttons.** Height 36 (`sm` 32, `lg` 40). Radius 8. Primary is navy-600 with
white text; hover navy-500; pressed navy-700. Secondary is a navy-600 outline on
transparent. Ghost has no border until hover. Destructive is the destructive
token, never red-tinted navy.

**Cards.** Radius 12, 1px border, `--surface` background, no shadow at rest.
**No card inside a card** — Impeccable flags nesting and the reason is real: two
borders 16px apart read as a rendering fault. A grouping inside a card is a
horizontal rule and a heading.

**Tables.** The densest surface in the product and the one recruiters live in.
Row height 44, 13px label header in ink, `navy-50` on hover, `navy-100` on
selected. Sorted in SQL, never in JavaScript, and the header reflects the
server's order.

**Grade chips.** The word, in ink, on a tinted fill: Highly Matching `teal-100`,
Matching `teal-50`, Moderately Matching `navy-50`, Not Matching a neutral fill
with a `--warning` left rule. **The word is always present** — never the fill
alone.

**Form fields.** Height 36, radius 8, and the border is `--field-border`
(navy-tinted) rather than the divider hairline. A control boundary below 3:1
against its surface is invisible, which WCAG 1.4.11 states and a user discovers
by not finding the field. Verified by `scripts/check-contrast.mjs` from the
token values; never adjusted by eye.

**Icons.** Lucide, 16 or 20, 1.5 stroke, `currentColor`. **No rounded-square
icon tile above a heading** — another Impeccable detector, and it is right: it
is decoration that adds a shape without adding information.

**Left rules.** `border-l-4` is flagged as a `side-tab` antipattern in generic
use, and generic use is banned. It survives in exactly two places, where it is
semantic rather than ornamental: the report's **Validation** section (marking
the candidate's own unrated words) and the **posting-window** state. Both are
documented in `.impeccable-exceptions.md` with their reason.

---

## 5. Layout Principles

- **12 columns, 1280 max, 24 gutter.** Content stops at 1280 even on a wide
  monitor: a report line 2000px wide is unreadable regardless of resolution.
- **8px spacing scale.** 4 for intra-component only.
- **One primary action per view.** If a screen appears to need two, one is
  secondary.
- **The report is a document, not a dashboard.** Sections in the fixed order,
  full width, one column, nothing beside them. Its order is pinned in code
  (`REPORT_SECTION_ORDER` and `report_pdf.SECTION_ORDER`) and a test reads both
  out of source and compares them, because the failure being prevented is a
  recruiter approving on screen and mailing a PDF that reads differently.
- **Navigation is per portal and never mixed.** Four portals — Provider,
  Customer, Candidate, Business Development — and a nav item from one never
  appears in another.

---

## 6. Depth & Elevation

Four levels, and three of them are borders.

| Level | Treatment | Use |
|---|---|---|
| 0 | 1px border, no shadow | Cards, panels, table containers — the default |
| 1 | `0 1px 2px rgb(1 38 84 / 0.06)` | Dropdown, popover, hovering row |
| 2 | `0 8px 24px rgb(1 38 84 / 0.10)` | Dialog, sheet |
| 3 | `0 16px 48px rgb(1 38 84 / 0.16)` | The landing hero only |

**Shadows are tinted navy, never black.** A neutral-black shadow over a
navy-tinted canvas reads as dirt. In dark mode elevation is a lighter surface
rather than a shadow — a shadow on a near-black canvas is invisible and the
attempt to make it visible produces a halo.

---

## 7. Do's and Don'ts

**Do**

- Put the evidence next to the claim. The product's whole argument is that it
  can say why.
- Say "held for review" and show what triggered it. A flag with no reason reads
  as an accusation.
- Use teal to mark what is corroborated — it is the one colour with a meaning.
- Keep the four grades as words everywhere, including in the PDF.
- Let a dense table be dense. Recruiters scan; padding is not kindness.

**Don't**

- No purple or violet, and no two-hue gradients. That palette is the reason this
  file exists.
- No teal text on white below 24px — use `teal-700`.
- No card inside a card.
- No rounded-square icon tile above a heading.
- No grey text. Ever. Placeholders only.
- No em dash in any string, in either language.
- **No number reaches a client.** Not a score, a percentage, a rank, a
  confidence, a weight, or a "top 12%". The single documented exception is the
  radar chart's band index, which is a rendering coordinate — a radar has no
  geometry without a radius — and is never displayed as a number.
- No `animate-bounce`, and no spring overshoot on anything a person is waiting
  on. A grade arriving with a bounce is the product being cheerful about a
  hiring decision.

---

## 8. Responsive Behaviour

| Breakpoint | Width | Behaviour |
|---|---|---|
| `sm` | < 640 | Single column. Tables become stacked cards — never a horizontal scroll on a phone. Nav collapses to a sheet. |
| `md` | 640–1024 | Two columns where content allows. Tables scroll horizontally inside their own container; the page body never does. |
| `lg` | 1024–1440 | Full layout, persistent sidebar. |
| `xl` | > 1440 | Content stays at 1280 and centres. |

**The candidate assessment surface is mobile-first and it is the only one that
is.** Candidates answer on phones; recruiters and CHROs work at a desk. The
assessment page is designed at 375 and scaled up; every other surface is
designed at 1280 and adapted down.

**Dark mode is not optional.** A standing enterprise-SaaS convention, and this
palette supports it naturally — navy is already the dark surface. Both themes
are a pure CSS variable swap; no component branches on theme.

---

## 9. Agent Prompt Guide

For an agent generating or modifying a ReadyPick surface:

> Build for a B2B enterprise hiring-intelligence platform sold to CHROs.
> Confident and precise, never playful. Navy `#012654` for structure — primary
> actions, navigation, the frame. Teal `#00888A` for evidence — what is
> corroborated, what is cited. Never purple, never violet, never a two-hue
> gradient. Teal text on white must be `#006F70`, because the brand teal
> measures 4.30:1 and fails AA for body text. Inter Tight for UI, Fraunces for
> display, JetBrains Mono for reference codes — never default Inter. Text is
> never grey; grey is for borders and muted backgrounds only. Cards are a 1px
> border and no shadow at rest, and never nested. No icon tile above a heading.
> No `border-l-4` unless it is semantic and documented. 8px spacing scale, 1280
> max width, one primary action per view. Dark mode must work and is a variable
> swap. And the rule that outranks the rest: **no number ever reaches a client**
> — not a score, a percentage, a rank or a confidence. Grades are the four
> words: Highly Matching, Matching, Moderately Matching, Not Matching.

**Before considering a surface done:** `/impeccable critique`, then
`/impeccable audit`, then `/impeccable polish`. `npx impeccable detect --json .`
runs in CI, so drift is caught mechanically rather than only by review.
