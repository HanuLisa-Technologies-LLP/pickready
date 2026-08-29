# Ready Pick Now Candidate Dashboard
## Column Framework & Specification

**Version:** 1.0 Final
**Date:** 28 August 2026
**Platform:** Ready Pick Now (Hanulisa Technologies LLP)
**Product:** Interview-Ready Profiles as a Service (ePaaS)

> Provenance note (added when filed into the repository, 2026-08-29): this is the
> document spec-doc6 (RPN-SPEC-006) §0.1 item 5 names as **precedence rank 4**, and it is
> authoritative for the candidate list surface only. It was supplied by the product owner
> in the implementing session and filed here verbatim. Where spec-doc6 §8.2 states a
> reconciliation against this document (numbers, naming, role-awareness, the two "stage"
> concepts, the 39/40 aspect count, the override-rate metric, divergence routing), the
> reconciliation in spec-doc6 wins, because spec-doc6 outranks this file and the RBAC
> Specification outranks both on authorization questions.

---

## Design Principle

The dashboard is the client's daily working surface. UI simplicity is non-negotiable: the
backend engines (evidence model, scoring algorithm, risk detection) are complex; the row
layout is not. Every column must earn its space through repeated use, not by conveying
information that already lives elsewhere. Backend complexity stays in the dossier; the
table itself is a fast triage and decision tool.

---

## Column Structure & Sequencing

| # | Column Name | Content | Type | Populated When |
|---|---|---|---|---|
| 1 | **Candidate** | Name + System ID (subtext) + Source badge | Identity | Always |
| 2 | **Source** | Databank / Applied | Categorical | Always |
| 3 | **Pre-Screen Grade** | Early triage signal (A / B / C / Hold) | Signal | Immediately (before assessment) |
| 4 | **Ready Pick Score** | Score + band + confidence indicator | Verdict | After Ready Pick Profile written |
| 5 | **Ready Pick Note** | One-line reason, written for a client | Context | After Ready Pick Profile written |
| 6 | **Ready Pick Profile** | Action: opens the full evidence view | Interaction | Enabled once profile is written |
| 7 | **Team Review** | Action: recruiter's independent verdict | Interaction | Always available |
| 8 | **Stage** | Pipeline stage + move-to control | State & Action | Always; locked under review |

**Total columns:** 8
**Scanning order:** left to right follows decision logic, not backend computation order.

---

## Detailed Column Specifications

### Column 1: Candidate

**Content:**
- **Line 1:** Candidate name, bold, 13.5px
- **Line 2:** System ID (e.g. `JSRS-Y4BN-8HGX`), monospace, 11px, muted color
- **Line 3:** Source badge (if not in its own column, but here it IS separate, so badge does not appear in this cell)

**Styling:**
- Right-align sorting options: by name, by date added, by source
- Copy-to-clipboard on ID on hover
- No rank number displayed in this version (ranks only shown inside Ready Pick Profile when band overlaps are disclosed)

**Example:**
```
Manju H
JSRS-Y4BN-8HGX
```

> Implementation note per spec-doc6 C14: this placeholder personal name must NOT survive
> into code, fixtures, seed data or screenshots. Replace with clearly synthetic fixtures.

---

### Column 2: Source

**Content:**
- `Databank` (sourced by Ready Pick Now's research team)
- `Applied` (candidate applied directly via job posting)

**Styling:**
- Small pill badge, muted background
- Sortable / filterable at top of dashboard

**Why it's a separate column:**
Recruiters filter and sort by source frequently in high-volume funnels (e.g., "show me
only Applied candidates at this stage" or "sort Databank candidates by score"). Keeping it
as its own sortable column rather than embedding it as metadata preserves that workflow
efficiency.

---

### Column 3: Pre-Screen Grade

**Content:**
- A single letter or short label: **A** / **B** / **C** / **Hold**
- Meaning: early resume-to-JD triage before the full assessment process and Ready Pick Profile are complete
- Populated immediately when a candidate's resume is ingested

**Styling:**
- **Critical:** Must render with muted/outline styling ONLY, no solid filled pill, no bright color
- Use a light gray background, or a border-only style
- Font weight: regular (not bold)
- Size: 11px
- This visual distinctiveness is mandatory: it tells the recruiter "this is an early signal, not a final verdict"

**Why muted styling is non-negotiable:**
If Pre-Screen Grade and Ready Pick Score both render as solid colored pills, they read as
equally authoritative, and the dashboard silently reintroduces the exact bug from the
original design: showing a high confidence verdict (Highly Matching) before full evidence
exists (while Prism Report is still Pending). Muted styling prevents that misreading.

**Example:**
```
A (muted outline)
```

---

### Column 4: Ready Pick Score

**Content:**
- **Score number** (0 to 100) in monospace, 15px, bold
- **Band label** (one of: Ready to Pick, Strong / Ready to Pick / Consider with Reservations / Not Recommended / Under Review)
- **Band color** (green / amber / gray / red, matching band label)
- **Confidence indicator** (filled dot / outline dot, positioned next to the band label)
  - Filled dot = High or Moderate confidence
  - Outline dot = Low confidence
  - Grayed dot = Insufficient confidence
- **Score range in brackets** on hover tooltip (e.g., `82 [76 to 88]`)

**States:**

| State | Display | Reason |
|---|---|---|
| Ready to Pick, Strong | `87 · Ready to Pick, Strong` + filled dot | Score >= 85, confidence high |
| Ready to Pick | `78 · Ready to Pick` + filled dot | Score 72 to 84, confidence high |
| Consider with Reservations | `65 · Consider with Reservations` + outline dot | Score 60 to 71, confidence moderate/low |
| Not Recommended | `42 · Not Recommended` + grayed dot | Score < 60 |
| Under Review | `— · Under Review` (no number, red background) + grayed dot | D4 (Authenticity) < integrity floor; awaiting HR Manager disposition |
| Pending Ready Pick Profile | `— · Pending Ready Pick Profile` (neutral gray) | Assessment in progress; no verdict yet |

**Styling:**
- Solid colored pill, only this column is allowed to look authoritative
- Click to see full score range and confidence reasoning
- Text color = white on dark background (green/amber/red); readable contrast always

**Why this column is the only one that "looks finished":**
It's the only place a recruiter should see a decisive, colored verdict. Every other metric
(Pre-Screen Grade, Team Review) is either preliminary or subjective. This one is the
ready-to-decide signal.

---

### Column 5: Ready Pick Note

**Content:**
- One sentence, plain language, written from the Ready Pick Profile's "Why this candidate" section
- Examples:
  - "Closes the top-ranked SWOT gap (cloud infrastructure ownership); owns comparable production migration"
  - "Strong technical match but scope contradiction under review, pending candidate clarification"
  - "Career changer with adjacent skill transfer; has not yet owned the core competency at this scale"

**Styling:**
- Regular weight, 12px
- Color: text-dim (slightly muted gray, not full black)
- Max width: 210px; text truncates on hover to show full text
- Never bold, never colored (color is reserved for the score band in column 4)

**Why "Note" not "Remark":**
"Note" is a recruiter's shorthand for "something I should know before opening the full
profile." "Remark" reads as editorial commentary. The distinction matters at volume.

---

### Column 6: Ready Pick Profile

**Content:**
- Single action button: **"Ready Pick Profile"** or (if profile not yet written) **"Awaiting Profile"** (disabled)

**On click:**
Opens a slide-over panel on the right side containing:
- Candidate name and ID
- "Why this candidate" (from the assessment write-up)
- Dimension breakdown (D1 to D5 scores with top evidence per dimension)
- Evidence ledger (all claims, sources, verification status)
- Authenticity findings and any open flags
- Risk register
- "Ask in interview" (generated questions)
- Comparison note (if candidate is inside a band-overlap cluster)
- Configuration metadata (scorecard version, evaluator, timestamp)

> Implementation note per spec-doc6 D8 / C2: the panel shows **named per-dimension
> ratings, not raw D1 to D5 numbers**. Raw numbers are exposed only through an
> authenticated calibration/audit view restricted to Super Admin and HR Manager, and are
> always logged when viewed.

**Styling:**
- Button only, no status badge in the row itself
- Primary button styling when profile is available
- Disabled (grayed, cursor: not-allowed) when profile is not yet written
- Keyboard shortcut: pressing `/` then entering candidate ID jumps to their profile

**Why separated from Team Review:**
The profile is the system's reasoning. Team Review is the recruiter's independent read of
that reasoning. Conflating them would hide the accountability layer: who relied on what,
when, and what they concluded independently.

---

### Column 7: Team Review

**Content:**
- Single action button: **"Team Review"** (always enabled)

**On click:**
Opens a side panel containing:
- Recruiter's own structured verdict (independent of the Ready Pick Score)
- Checkbox verdicts: Pass / Hold / Reject
- Free-form notes section (what the recruiter saw or tested in a call, if any)
- Reference feedback (if a reference was checked)
- Timestamp of the last review

**Styling:**
- Button, secondary styling (less visual weight than Ready Pick Profile button)
- Never disabled, always available, even before Ready Pick Profile is written
- Distinct visual treatment from Ready Pick Profile (different button color, e.g., teal vs. primary blue)

**Why this column is essential:**
A recruiter's independent judgment is data. If a recruiter's verdict diverges from the
Ready Pick Score, that's either a signal that the scorecard needs recalibration or a sign
that the recruiter saw something the assessment missed. Keeping Team Review as a separate,
always-visible action surfaces that accountability.

---

### Column 8: Stage

**Content:**
- **Current stage label:** Applied / Screening / Shortlisted / Interview / Offer / Closed (read-only display)
- **Move-to dropdown:** lets recruiter advance or hold the candidate

**States:**

| Condition | Dropdown behavior | Reason |
|---|---|---|
| Normal flow | Enabled; pre-populated with the stage most likely given the Ready Pick Score | Ready Pick Score = 85+ suggests Interview, for example |
| Under integrity review (D4 flagged) | Disabled; tooltip: "Pending integrity review, HR Manager only" | No progression until human review clears the flag |
| Assessment in progress | Enabled; can move independently of Ready Pick Profile status | A candidate can sit in Screening even before their profile is written |

**Styling:**
- Dropdown select or button-based stage selector (design choice: either is fine)
- When disabled: 40% opacity, cursor: not-allowed
- Lock icon visible when disabled
- Tooltip appears on hover explaining why

**Why Stage is separate from score:**
A candidate's stage in your pipeline (Applied to Interview) is orthogonal to their
readiness (Ready Pick Score). A candidate can be "Applied" with a Ready Pick Score of 87;
another can be "Screening" with a score of 45 (if they were moved forward before the full
assessment completed). Keeping them as separate columns prevents the table from suggesting
they're the same thing.

---

## Visual Hierarchy & Styling Guide

### Column weight (scanning priority):
1. **Candidate**, anchor, heaviest visual weight
2. **Ready Pick Score**, the decisive column, solid and colorful
3. **Stage**, the action column, where progression happens
4. **Pre-Screen Grade, Ready Pick Note**, supporting context, lighter weight
5. **Source**, metadata, muted
6. **Ready Pick Profile, Team Review**, actions, not data, minimal visual intrusion

### Color palette:
- **Ready to Pick, Strong / Ready to Pick:** green (#2FD08A or equivalent)
- **Consider with Reservations:** amber (#E0B341 or equivalent)
- **Not Recommended / Under Review:** red (#EF5D6B or equivalent)
- **Pending / Insufficient data:** gray, muted (#6B7280 or equivalent)
- **All other text:** --text-dim (70% of full brightness, never pure black)

> Implementation note: the standing project rule is that text is never grey, enforced at
> the token (`--muted-foreground: var(--ink)`). Where this document's "never pure black"
> conflicts with that rule, the standing rule wins and the conflict is recorded in
> `CONTRADICTIONS.md`. The band colours above must also be reconciled against the navy
> `#012654` / teal `#00888A` brand palette in `DESIGN.md`.

### Typography:
- **Candidate name:** 13.5px, bold, high contrast
- **Column headers:** 10.5px, uppercase, letter-spaced, muted color
- **Score:** 15px, monospace, bold (when a number is shown)
- **Badge labels (band):** 11px, bold, color-matched to verdict
- **All other row content:** 12 to 13px, regular weight

### Spacing:
- Row height: 56 to 64px (comfortable, not cramped; allows subtext and badges)
- Column padding: 14px left/right
- Gap between actions (Profile / Team Review buttons): 10px

---

## State Behaviors & Transitions

### Row states:

| State | Visual indicator | When it occurs |
|---|---|---|
| Normal (Ready to Pick) | Green band, ready for interview | Standard path |
| Pending profile | Gray band in column 4, profile button disabled | Assessment in progress |
| Under review (D4 flagged) | Red left border on row, red band "Under Review", Stage dropdown disabled | Integrity gate active |
| Archived / Rejected | Faded opacity (~50%), Stage = Closed | Not a separate state; just a closed stage |

### Transitions:
- Clicking the Ready Pick Profile button does not change row state (it's just opening a panel)
- Moving a candidate's Stage via the dropdown does not automatically update Ready Pick Score or Pre-Screen Grade
- If an HR Manager disposition clears an integrity flag, the row automatically re-evaluates and the Stage dropdown re-enables
- If a new assessment version is published, the Ready Pick Score and Note update, but Team Review is left untouched (it's independent data)

---

## Interactions & Entry Points

### Primary workflows:

**Workflow 1: Fast triage (50 candidates, sort by Ready Pick Score)**
1. Land on dashboard, scan Ready Pick Score column
2. Sort descending
3. For top 10, click Ready Pick Profile to skim the "Why" section
4. Use Pre-Screen Grade as a secondary filter for borderline cases
5. Move ready candidates to Interview via Stage dropdown

**Workflow 2: Integrity review (1 candidate flagged)**
1. Row shows red border and "Under Review" in column 4
2. Stage dropdown is locked
3. Click Ready Pick Profile to read the contradiction
4. Contact candidate to clarify
5. HR Manager closes the flag, row goes green, Stage dropdown unlocks

**Workflow 3: Team calibration (sync on a borderline Ready Pick Note)**
1. Recruiter sees a candidate scored Ready to Pick (82) with a concerning note
2. Clicks Team Review to enter their own verdict
3. Clicks Ready Pick Profile to review the evidence
4. If Team Review differs from Ready Pick Score, a flag surfaces in the admin dashboard for the Standards Board to investigate

---

## Data Model & Backend Sequencing

### Order of operations (what happens when a candidate is added):

1. **Resume ingested**, Pre-Screen Grade populated (A/B/C/Hold)
2. **40-aspect questionnaire sent** (asynchronous, off-table)
3. **Questionnaire responses received**, Assessment begins
4. **Assessment written & scored**, Ready Pick Profile exists, Ready Pick Score & Note populate
5. **Recruiter reviews**, Team Review panel populated (independent of Step 4)
6. **Integrity check** (if D4 < floor), Stage dropdown locked until disposition
7. **Recruiter moves to next stage**, Stage updates

> Implementation note per spec-doc6 C8: the "40-aspect questionnaire" count must be
> reconciled against the Runbook and the implemented form
> (`backend/app/services/candidate_profile_form.py`). Do not implement a second form.

### What's never displayed:
- Individual dimension scores in the row (reserved for the profile panel)
- Evidence source counts (reserved for the profile panel)
- Confidence reasoning details (reserved for the profile panel)
- Recruiter notes from Team Review (reserved for that panel; never shown in the row)

---

## Accessibility & Mobile Considerations

### Keyboard navigation:
- Tab order: Candidate, Source, Pre-Screen Grade, Ready Pick Score, Ready Pick Note, Ready Pick Profile, Team Review, Stage
- Enter on any action button (Profile / Team Review) opens the respective panel
- Escape closes any open panel
- `/` + candidate ID jumps to that candidate's profile

### Screen readers:
- Column headers read as "Candidate Code Name" (not just "Candidate")
- Badge colors are never the sole indicator; text color + label is redundant
- "Under Review" state is announced as "Status: Under Review, awaiting integrity disposition" not just a visual red

### Mobile (narrow viewport):
- Columns 6 to 8 (actions + stage) stack into a vertical button group at the end of the row
- Pre-Screen Grade and Ready Pick Note can collapse into a single summary line if space is tight
- Ready Pick Score (column 4) never collapses, it's always visible
- Horizontal scroll is acceptable if the table exceeds viewport width; no column is hidden by default

---

## Success Metrics & Calibration

### What this dashboard should enable:

| Metric | Target | Measurement |
|---|---|---|
| Time to skim 100 candidates | < 4 min | Timer from login to decision on top 10 |
| Accuracy of Pre-Screen Grade vs. final Ready Pick Score | Correlation >= 0.72 | Coefficient over 30-day sample |
| Recruiter deviation from Ready Pick Score | < 15% override rate | # of Team Review verdicts differing from Ready Pick Score / total |
| False positives from integrity flags | < 8% | # of flags cleared after candidate clarification / total flags |
| Recruiter survey: "I know what to do next without opening the profile" | >= 80% agree | Post-use survey |

> Implementation note per spec-doc6 C18 / §8.2: implement the override-rate
> **measurement**; do NOT implement any nudge, warning, friction or visual discouragement
> when a recruiter disagrees with the score. A recruiter's independent judgment is data,
> and a target that quietly discourages disagreement destroys the calibration signal it
> exists to measure.

---

## Notes & Future Considerations

- **Rank within band:** Overlapping Ready Pick Scores (e.g., 78 to 82 all in "Ready to Pick" band) are reported as tied in the list, not false-ranked. Rank appears only inside the profile panel where the full uncertainty band is disclosed.
- **Comparison note:** When multiple candidates sit in the same band, the Ready Pick Profile shows a comparison to adjacent candidates so recruiters understand true separation vs. statistical tie.
- **Scorecard versioning:** If a scorecard (role weights, competency definitions) is updated mid-pipeline, existing candidates retain their old scorecard version in their profile. New candidates use the new version. This is always logged.
- **Audit trail:** Every state change (score update, flag disposition, stage movement, team review entry) is timestamped and logged for compliance and calibration review.

---

## Sign-off

**Product Owner:** Manju H (Ready Pick Now, Hanulisa Technologies LLP)
**Framework finalized:** 28 August 2026
**Ready for build:** Yes
**Known blockers:** None

---

### Appendix: Column Reference Card

```
+-------------------------------------------------------------+
| READY PICK NOW, CANDIDATE DASHBOARD                          |
+-------------------------------------------------------------+
| COL 1 CANDIDATE      | Name + ID + Source                    |
| COL 2 SOURCE         | Databank / Applied, sortable          |
| COL 3 PRE-SCREEN     | Early signal (A/B/C), muted only      |
| COL 4 READY PICK *   | Score + Band + Confidence, VERDICT    |
| COL 5 READY PICK NOTE| One-line why (from profile)           |
| COL 6 PROFILE        | Opens full evidence panel             |
| COL 7 TEAM REVIEW    | Your independent verdict              |
| COL 8 STAGE          | Pipeline stage + move control         |
|                                                              |
| * = Only column allowed to look "finished" (solid color)     |
| If score is not green, open Profile before deciding.         |
+-------------------------------------------------------------+
```
