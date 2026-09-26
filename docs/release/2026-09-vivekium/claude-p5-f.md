# CLAUDE.md section draft: Phase 5 WP5-F and WP5-G, the PRISM read surface and the copy

Package p5-f (stage 3), on top of p5-d. No migration. No new capability.

## Current hard rules, the PRISM Report's read surface (2026-09-26)

### ONE ROUTER, ONE SERIALIZER, AND THE URLS DID NOT MOVE

`api/assessments.py` is GONE. It held the report, the PDF, the transcript and
the three immutability handlers and nothing else by the time Phase 5 reached
it, so they moved whole into `api/assessment_reports.py`, mounted under the
SAME `/api/v2/assessments` prefix. A report link in an inbox is a URL, so the
move is safe only because the URL set is byte-identical:
`tests/test_report_routes_moved.py` compares the MOUNTED application's
(method, path) set against the list issued links carry, requires every one of
them to be served by that one module, and still gets 403 from the three
immutability handlers.

- **`services/prism_view` is the ONE serializer.** The report route and the
  PDF route both call `prism_view.report_out`, so the screen and the document
  cannot state different grades. The radar builder and the report category
  constants moved there from `functional_assessment`.
- **A skill the evaluation could not complete is a WORD and a SENTENCE**
  ("Not assessed", `STATUS_NOTE_NOT_ASSESSED`), draws NO radar spoke (the
  innermost band would state Not Matching), and a spoke with no recorded
  requirement draws no requirement shape. The legend names only the shapes
  drawn. On the screen the requirement shape is drawn only on a chart whose
  EVERY spoke states one: a partial shape would pin the missing spokes to the
  centre, which reads as "requires nothing".
- **A pre-0030 report with nothing assessed states "Not assessed".** The
  legacy recompute used to fall back to 0, which grades Not Matching about a
  candidate nobody graded (`tests/test_prism_view.py`).

### THE PDF LEAVES THROUGH G4, AND THERE IS NO SECOND DOOR

`download_report_pdf` runs the candidate's retention consent, then
`siddhi.delivery.gate_delivery` (409 with `delivery.PDF_BLOCKED_REASON` while a
report routed to a person has no recorded disposition), then
`delivery.prism_pdf`, which requires the clearance the gate minted and whose
renderer runs the number ban on its own input.

- **`tests/test_siddhi_delivery_single_path.py` walks the AST**: the raw
  `render_report_pdf` has exactly one caller (`delivery.prism_pdf`), which has
  exactly one caller (the route), `delivery.__all__` is exactly the gate, its
  non-raising twin and the gated renderer, and a clearance a caller builds
  itself is refused.
- **The payload says the PDF is withheld and why** (`pdf_available`,
  `pdf_blocked_reason`, from `delivery.clearance_or_reason`, the SAME gate), so
  the modal shows the server's sentence instead of a button the route would
  refuse. That notice is NOT a permission message and does not go through
  `permission-notice`: nobody lacks a grant, a decision is owed.
- **The on-screen report is deliberately NOT gated**: it is where the person
  who owes the decision reads the report.

### THREE READS ARE AUDITED, IN THE ONE INSERT, AND ONE READ IS NOT

`audit.PRISM_PDF_DOWNLOADED` (written only once the bytes exist: a refused
render downloaded nothing), `audit.ASSESSMENT_TRANSCRIPT_VIEWED` (after the
tenant and closure gates, before the answers load) and
`audit.PRISM_CITATIONS_VIEWED`. Each is one `record_action` INSERT in the
request's transaction (the 2026-09-20 rule), asserted from a SECOND connection
after commit in `test_prism_pdf_g4.py`, `test_transcript_read_is_audited.py`
and `test_citations_route.py`, and each test also asserts that a refused read
(404 cross tenant, 410 after closure) records nothing. Mutation-checked: point
either read's action elsewhere and its test fails.

- **Opening the report is not audited per open**; it is the reading surface a
  reviewer works from.

### CLICK A REMARK, SEE WHAT IT RESTS ON

`GET /api/v2/assessments/reports/links/{link_id}/citations`
(`VIEW_REVIEW_SCREEN`, 404 across tenants, 410 after closure, audited) serves
`siddhi.trail.citation_view`: the stored trail's locators resolved at READ
time to the question asked, the candidate's answer and any passage read,
scoped to this application's own messages, questions and chunks, excerpts
capped. Words and the candidate's own text only: no id, no locator, no
position, no digit. A report written before the trail answers
`trail_available: false`, which is a different answer from an empty trail.

- **`components/report-citations.tsx` renders it as a disclosure** under each
  rated remark; it never derives a citation (a citation the client invented
  would read as provenance and be none). Teal marks the cited words, which is
  teal's one meaning.
- **FETCHED ON THE FIRST REMARK OPENED, AT MOST ONCE PER REPORT OPEN.** Every
  read is an audited read of the candidate's answers, so the modal keeps one
  shared promise and a failed fetch is not cached. Fetching on open would
  record a citation view for every report a recruiter merely glanced at.

### THE AI MATCH SECTION

The report's first section prints **AI Match**, on screen
(`AI_MATCH_TITLE`) and in the PDF (`report_pdf.AI_MATCH_HEADING`): the same
words the candidate table uses for the same pre-assessment check. The payload
key stays `ai_score`, because stored reports carry it. A report written from
the Vivekium release on renders Yukti's frozen snapshot (grade word, the
server's header sentence, evidence tags with a lucide check or cross and a
screen-reader prefix, never an emoji); an older report renders the four
legacy rows it was written with. SUPERSEDES the 2026-08-23 heading "AI Score"
in `SECTION_HEADINGS`; the section ORDER is unchanged.

## Current hard rules, the retake and the vocabulary (2026-09-26)

### THE RETAKE IS GONE, AND A SWEEP KEEPS IT GONE

SUPERSEDES the 2026-07-27 "Six-month retake rule" and the 2026-07-30 "Report
REUSE is retired ... the six-month classification still runs" lines:
`services/retake.py` is deleted (nothing called it after Phase 3 removed the
apply block and the invitation read), with its tests, the
`recent_prior_report` / `assessment_required` / `assessment_notice` fields,
the invitation page's explanation and the public site's "A retake creates a
new report" line. Every application is assessed against its own job's locked
contract; there is no waiting period to explain.
`tests/test_retake_removed.py` asserts the module is gone, the payloads carry
none of the fields, and no live backend or frontend source names the module,
its symbols or its sentence (whitespace-normalised, a declared allowlist of
three files each with its reason).

### RECRUITER-VISIBLE COPY USES THE PRODUCT'S WORDS (CONTRACT v4 item 5)

JD, SWOT, Skills, AI Match, Tatva Assessment, PRISM Report, Proctoring Report.
NOT PPI, matrix, framework, matching categories, AI Score or retake.
EXTENDS the 2026-08-23 rule that the rename is user-visible copy only: the
code keeps `ppi`, `PPIReportModal`, `/framework`, `ai_score`, because routes
are quoted in issued links and keys are read from immutable stored reports.

- **Two sweeps, both reading STRUCTURE rather than text.**
  `backend/tests/test_user_facing_copy_names.py` reads every
  `HTTPException(detail=...)` literal under `app/api` and every non-docstring,
  non-log prose literal in the modules that WRITE copy (the PRISM serializer
  and PDF, gap analysis, the Updates and empty-state catalogues, activity
  phrasing, the email renderers, the G4 sentence).
  `frontend/lib/user-facing-copy.test.ts` walks the TypeScript AST of `app`,
  `components` and `lib` for JSX text and prose literals, skipping imports,
  `className`, API paths, console calls and every comment. Both pin their
  patterns in both directions and assert a floor on what was swept, because a
  sweep over nothing passes.
- **Fixed with them:** the gap analysis focus sentence ("this job's skills"),
  the JD-versus-SWOT recommendation ("before the job's skills are saved"), the
  workflow animation (PRISM Report), the docs page (the six-month reuse card
  was describing a deleted feature; it now states the one-contract rule), and
  the story section, which still expanded the retired PPI name.

## Open, said out loud

- `siddhi.synthesis.SECTION_TITLES["ai_score"]` still reads "AI Score". It is
  the composed section's internal title and reaches no reader today; renaming
  it is a p5-c file and was left alone.
- The frontend typecheck reports `@monaco-editor/react` / `monaco-editor`
  missing in the main checkout's `node_modules` (Phase 4's dependency, not yet
  installed there). No error is in a file this package touched.
