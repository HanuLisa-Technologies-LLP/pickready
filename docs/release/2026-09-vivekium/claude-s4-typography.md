# CLAUDE.md section draft: the typography system (2026-09-28)

Owner request: a premium, clean, highly readable type pass across the whole
UI, with a clear hierarchy between page titles, section headings, labels,
body, metadata and helper text, without changing layout, content, colours or
behaviour. `DESIGN.md` section 3 is the normative description; this is the
part a future session needs so it does not undo it.

## What changed

- **The type system is TOKENS.** `tailwind.config.ts` adds font-size ROLES
  that carry size, line height, tracking and weight together: `title`,
  `title-sm`, `heading`, `subheading`, `body`, `body-sm`, `label`, `meta`,
  `eyebrow`, `table`, `table-head`. `app/globals.css` adds three component
  classes where a role needs more than a size: `type-page-title` (24px under
  `sm`, 28px from `sm`), `type-eyebrow` (the uppercase overline) and
  `type-prose` (body at a 65 character measure). The raw steps keep their
  sizes; `lg` moved from 30px to 28px leading, and every step from `lg` up
  carries tracking that tightens with size.
- **The primitives render through the roles**, so every page inherited the
  hierarchy without a page edit: `PageHeader`, `CardTitle`/`CardDescription`,
  dialog, alert-dialog and sheet titles and descriptions, `Label`, the form
  hint and error, `Table` (14/20 cells in a `py-3` row, the 44px row DESIGN.md
  always specified; 12px uppercase headers), toast, tooltip (now capped at a
  readable width), menu group labels, `Section`, `EmptyState`, `ErrorState`,
  `Field`, `DetailItem`, `RowCard` and the app shell's rail labels.
- **The page sweep was class-level only**: forty-six hand-spelled overlines
  (eight tracking values) became `type-eyebrow`; bespoke h1s became the page
  title role; card and section headings became `subheading` / `heading`;
  stat figures became 600 with `tabular-nums`; report remarks, transcripts and
  the JD got `max-w-prose`.
- **Base layer**: headings are −0.011em and balanced (the blanket
  `tracking-tight` on every h1 to h4 is gone), paragraphs wrap `pretty`,
  `time` joins `table` in tabular figures, kerning and contextual alternates
  are pinned on.

## Hard rules

- **Set type with a ROLE, not four utilities.** A heading spelled out by hand
  is how the product reached eight overline trackings and two page-title
  sizes. A deliberate exception is still a `leading-*` / `tracking-*` /
  `font-*` next to the role, which wins and is visible in the diff.
- **A new role is half a change until `lib/utils.ts` knows it.** tailwind-merge
  files any unknown `text-<name>` as a COLOUR, so `cn("text-label",
  "text-ink")` would drop the size with no error. `TYPE_ROLES` must equal the
  config's role keys; `lib/utils.test.ts` reads the config and fails when they
  drift (mutation-checked: dropping `meta` from the list fails the parity
  test; emptying the merge registration fails the two behaviour tests).
- **Headings are 600.** Bold at display sizes on Inter Tight reads as
  shouting.
- **Running text stops at `max-w-prose`.** Tables, forms and grids do not.
- **Text is still never grey.** Hierarchy comes from size and weight; no role
  sets a colour.

## Supersedes, in place

- `DESIGN.md` section 3's 2026-09-19 note (lg at 30px, description at `mt-3`,
  "letter spacing did not move") is AMENDED there.
- `DESIGN.md` section 4's "13px label header" table line now reads the 12px
  `text-table-head` role.

## Not done, said out loud

- Marketing pages (`app/(public)/*`, `app/page.tsx`, the workflow animation)
  keep their own display scale; they inherit the base layer and the stepped
  tracking only. Moving their h1s to Fraunces is a brand decision, not a
  readability fix.
- The candidate dashboard's bespoke cell sizes (`text-[13.5px]`,
  `text-[12px]`, rating chips at 11px) are specified by the Candidate
  Dashboard Specification and were left as they are.
- Page-level spacing wrappers were not rewritten; the rhythm is documented in
  DESIGN.md section 3 and applied through `PageHeader` and the primitives.
- Five files a parallel flow package owns were not edited (see the report's
  HUNKS); they inherit the tokens through the primitives.
