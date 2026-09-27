# CLAUDE.md draft: the S4 flow, frontend half (CONTRACT v10)

Owner ruling 2026-09-28, in chat:

```
JD ──→ Skills ──→ Final Job Posting ──→ Publish
SWOT ──→ separate internal hiring intelligence, editable independently
First genuine application ──→ FREEZE JD + Skills
```

The API shapes are `docs/release/2026-09-vivekium/s4-api-shapes.md` (backend
half, `wip/s4-flow-backend`). This is what the screens do with them.

## What changed

- **The job page's setup runs JD, Skills, Final Job Posting, Publish**
  (`app/(org)/org/jobs/[id]/page.tsx`). Monitoring follows Publish (it stays
  editable after the freeze), and the SWOT lives in its own section,
  **"Hiring intelligence (internal)"**, with one line saying candidates never
  see it and it can be edited at any time. SUPERSEDES the 2026-09-25 page
  order (JD, SWOT, Skills, Monitoring, Publish) and D1's "JD, then the Job
  SWOT, then Skills".
- **The Publish checklist names the JD and the skills only**
  (`components/job-publish-card.tsx`). The SWOT is never a prerequisite.
- **The Final Job Posting** (`components/job-posting.tsx`,
  `FinalJobPostingPreview`) reads `GET .../posting-preview` and renders the
  posting exactly as the public apply page does: the title, the department
  and band, the JD through `JdDocument`, the three buckets through
  `PostingSkillsList`, the company narrative. The grade is shown OUTSIDE the
  posting sheet, labelled as not shown to candidates. The page re-reads it
  with the checklist and whenever the Skills panel's names or saved state move
  (`onLoaded`, because a draft landing by poll calls no `onChanged`).
- **One renderer for skills on every candidate surface**:
  `PostingSkillsList` on the public apply page, the portal's apply dialog, the
  employer page's open roles and the preview. `postingSkillsFrom` reads
  `skill_buckets`, keeps the server's label and the NAMES only, and returns
  null for `[]` (a legacy job with no saved skills), so no empty headings.
- **The apply page renders `jd_markdown`** through `JdDocument` when present
  (the per-section blocks are the fallback for a pre-document job) and shows
  the company narrative, so the preview's promise is true.
- **Frozen** (`setup.frozen`, `skills_locked` on an older server): a banner
  with the server's `frozen_reason` (it carries its own date; a bare
  `frozen_at` date only when no sentence came), no JD edit, the title,
  experience band and grade disabled with the same sentence as their hint,
  every skill control read-only, and a details save that SENDS NONE of the
  frozen fields (so the company sections stay editable without the server
  refusing an unchanged field). The SWOT stays editable; its re-draft offer is
  withheld (`canRedraftSkills && !frozen`).
- **Skills without a SWOT**: an empty list offers "Draft skills" (drafted on
  the click, no confirmation, because nothing would be replaced); the state
  sentence says the SWOT is optional. `draft_blocked_reason` (a JD too thin)
  is rendered verbatim and withdraws Draft skills and Draft again. A JD save
  re-reads the skills, because it may dispatch the first draft.

## New hard rules, and why

- **The frontend hard-codes no sentence about the freeze.** The retired
  "locked because a candidate has started the assessment" sentences are gone
  from the page and the Skills panel; `frozen_reason` is rendered verbatim
  in the banner, the field hints and the Skills panel. A paraphrase is a
  second author for a rule, and the two drift. The Skills panel's fallback
  when no sentence arrives is a state word ("Frozen. These skills can no
  longer change."), not a reason.
- **A preview is the server's statement of the posting**, never assembled
  from the recruiter's editors: otherwise it can promise a posting the
  candidate never sees.
- **A skill reaches a candidate as a name under its bucket label and nothing
  else.** `postingSkillsFrom` drops everything but label and names, so a
  payload that carried more still renders less.
- **A page test must stabilise every hook a load callback depends on.** The
  job page's `loadJob` depends on `toast`; a `useToast` mock returning a fresh
  `vi.fn()` per render re-ran the load effect for ever and pinned a vitest
  worker at full CPU with no failure and no output. `page.test.tsx` hoists a
  stable toaster, permissions object and AI activity handle.
- **Never round-trip a source file through PowerShell 5.1 `Get-Content`
  without `-Encoding UTF8`**: it reads a BOM-less UTF-8 file as the ANSI
  codepage and a middle dot came back as mojibake in a committed file.
