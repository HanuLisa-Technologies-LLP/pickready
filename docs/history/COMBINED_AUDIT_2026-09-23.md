# Vivekium Combined Audit

This file combines three audits, each reproduced verbatim:

- **Part 1: Local working-folder audit** (Claude Code, local codebase on branch `integrate/pr6-rbac-stem-swot`, 2026-09-23)
- **Part 2: Claude repository architecture audit** (`vivekium_architecture_audit_claude.md`)
- **Part 3: ChatGPT repository architecture audit** (`vivekium_repo_architecture_audit_chatgpt.md`)

---

# PART 1: LOCAL WORKING-FOLDER AUDIT

# Vivekium (ReadyPick) — Codebase Audit

**Date:** 2026-09-23
**What was read:** the local working folder on branch `integrate/pr6-rbac-stem-swot`: backend, frontend, workers, infrastructure, harness and evaluation code. I did not rely on GitHub, on the live production site, or on the design documents; where the documents and the code disagree, the code is what's reported here.
**Method:** every part of the flow was traced from the screen, through the server, to the database and the AI calls. When something looked unused, I searched for anything that calls it before calling it dead. The most serious findings were checked a second time by reading the code directly.
**What was not done:** I did not run the test suite and did not call any live service. Anything marked *(likely)* comes from reading the code and has not been proven by running it.

---

## 0. The short version

The product is **substantially built** and the core path works end to end: create a job, write the SWOT, build the Tatva matrix, match candidates, invite them, run the assessment, and produce the PRISM and Proctoring reports. The AI design is careful: every model call is bounded, there is a deterministic fallback when the AI fails, and the scoring arithmetic is deterministic.

What goes wrong happens where the pieces meet:

1. **Creating a job publishes it immediately**, skipping the "the Tatva matrix must be saved first" rule.
2. **Applying to a job silently creates an assessment session**, which (a) pays for AI question generation for people nobody invited, (b) permanently locks the Tatva matrix against reopening, and (c) sends the candidate to a "This assessment is not available" page right after they apply.
3. **Candidates cannot use the Employment History / BGV screens at all.** Those screens only accept staff sign-ins, so every candidate request is refused.
4. **The RAG (retrieval) system is write-only.** Every job description, resume and assessment is chunked, summarised by AI and embedded (which costs money), but no production feature ever reads the index back.
5. **Large parts of the "agent framework"** (tool layer, action ledger, reasoning runner, learning memory, version resolver, safety quarantine) **are built and tested but never run in production.**
6. **A delivered PRISM report can be rewritten in place** if scoring runs a second time, even though reports are supposed to be immutable.
7. **Several AI tasks run on a mislabelled task type or on the wrong tier of model.**
8. A fair amount of **leftover code** remains from OTP login, Celery, the multi-vendor AI setup, GCP, the 40-question aspect form, and the old review and template pages.

Details follow.

---

## 1. Product flow as the code actually implements it

### 1.1 Client (company) side

| Step you intended | What actually happens today |
|---|---|
| **Job description creation** | Recruiter fills title, department, grade (Non-managerial / Managerial / Leadership / CXO), an experience band of at most 5 years, and a short skills brief, then presses Generate. An AI writer drafts a single markdown JD with seven sections. If the AI fails, a clearly labelled template is used, **but the screen still says "Draft ready" as if AI wrote it.** The job cannot be created unless the Company Profile "About" section is filled in. |
| **Job posting** | Pressing Publish **creates and publishes the job in one step.** The job is live for exactly 30 days plus a 5-day grace period in which only existing applicants can edit. It can be renewed only once it is in grace or expired. Closing a job hides assessment data immediately and purges it 30 days later. Publishing also immediately starts AI matching and AI generation of the matching categories. |
| **Job SWOT form** | A panel on the job page. AI drafts a Strengths / Weaknesses / Opportunities / Threats document from the job. The Hiring Manager edits and saves it. Once a human has edited it, regenerating needs explicit confirmation and the old version is kept. Saving the SWOT triggers the matrix build, **but only the first time.** Later SWOT edits never reach the matrix (see 3.1). |
| **Matching Requirements "7 stage form"** | **There is no seven-stage form on screen.** The seven stages (competency → observable evidence → evidence sources → assessment method → weight → threshold → disqualifier) happen invisibly inside Sutra for each criterion. The server sends all seven to the screen, but the screen shows only name, grade word, edit and remove. Separately, a **Matching Categories** card (5–8 AI-proposed resume-matching categories) is shown and must be saved; once saved it cannot be reopened. |
| **Tatva Assessment Matrix** | A chip editor with three columns: Must-have, Nice-to-have, Behavioural. You can add one item or paste a list, rename, remove (a soft delete; re-adding brings it back), and drag between columns. **Save Matrix** first lets AI fill in the technical detail for any hand-added criterion, then freezes the matrix. The job becomes "ready for candidates" only when **both** the matrix and the matching categories are saved. |
| **Run AI matching** | Button on the job page, available only when the job is ready. The server pre-screens every linked candidate deterministically (grades A/B/C/Hold), then an AI scores each resume against each matching category in batches of 10 (1–10 per category, with a 25–30 word comment). The screen shows stage progress. |
| **Ranked candidates + AI report** | A table with one row per candidate showing: the four grade words, a **Match %** (the one number allowed on screen), CTC match, notice period, education match, BGV status, and a "New" flag. **The table is not sorted by Match %**: assessed candidates come first by assessment score, then everyone else by resume keys. A 62% candidate can therefore sit above a 91% one. |
| **Send assessment invitations** | Select candidates, then Invite. Credits are checked, each candidate moves to "Assessment invited", an AI-drafted invitation email is sent, and AI generates that candidate's questions. **The credit check covers one report, not the whole batch**, and **emails are drafted inside the click**, which is slow for large batches. The emails are sent without an editing step. |
| **Receive assessment responses** | The candidate completes the assessment. On completion the account is charged, scoring runs in the background, and the report appears in the table. |
| **Executive profile = PRISM + Proctoring** | A modal shows the PRISM report: AI Score, Overall, Must-have, Nice-to-have, Behavioural, Gap Analysis, Validation, and the Proctoring section. Three radar charts, no numbers, plus a PDF (only if the candidate consented), a transcript, and video if recorded. **Once a job is closed, the PRISM parts are blocked (the "410 Gone" rule), but the separate Proctoring report is still readable.** |

### 1.2 Candidate side

| Step you intended | What actually happens today |
|---|---|
| **Login** | Google or email/password through Firebase. First sign-in automatically creates a candidate record. The session ends when the browser closes, with a 30-minute idle timeout. **No "Forgot password".** Phone login appears in comments but has no screen. |
| **Profile + resume** | My Profile holds: main resume (AI extracts the details), data-retention choices, Employment History, Background Verification, Projects (AI reviews each project), the profile questionnaire, and Delete Profile. **Employment History is broken for every candidate** (see 3.3). |
| **Apply for jobs** | The "New Jobs" board ranks roles by similarity to the candidate's resume. The apply dialog asks for 6 fields: current CTC, expected CTC, notice period, joining date, document readiness, and "why this role". **The public apply link asks for much more**: the full 40-question aspect form plus mandatory Age and Gender, which nothing downstream uses. |
| **View status** | "Applied Jobs" shows a timeline per application, and "Updates" is an in-portal feed of fixed messages. Links in the feed point at a specific application, but the page ignores that and just opens the list. |
| **Write assessments** | Through the email invite link or the Start button (shown only once invited): choose typed or video mode, give consent, pass the proctoring check, then answer one question at a time. **Right after applying, the New Jobs board sends the candidate to the assessment page, which refuses them** because they haven't been invited yet (see 3.2). |
| **Status and emails in the portal** | A Messages page for chat with recruiters (no live socket; refresh manually). **No notification or badge when a recruiter writes.** Emails go through Gmail SMTP or SES. There is no Support channel for candidates. |

---

## 2. Technical flow, in plain terms

### 2.1 The overall shape

- **Screens:** a Next.js web app with four portals: Candidate, Customer (company), Provider (Vivekium's own admin), and Business Development.
- **Server:** a Python (FastAPI) API. Every company's data is walled off by database-level row security. Permissions are stored as data ("capabilities"), not hard-coded per role.
- **Background work:** anything slow is handed off. Short jobs (parsing a resume, sending an email, sweeps) run as **AWS Lambda** calls. Long jobs (matching a whole pool, building a matrix, scoring an assessment) each start their own short-lived **Fargate container**. Retries happen in exactly one place. 16 scheduled sweeps run hourly or every 15 minutes.
- **Storage:** Postgres (with vector search), Redis (sessions, rate limits, proctoring warning counters, background-job progress), and S3 (resumes, videos, documents).
- **AI:** one vendor with two chat models and one embedding model:
  - **Terra**: the stronger "judge or write" model.
  - **Luna**: the cheaper "extract or classify" model.
  - **voyage-4**: turns text into vectors for search.

### 2.2 How one AI call works (the "router")

Every AI call in the product goes through a single gateway. It:
- looks up the task's rules (which model, temperature, time limit per attempt, total time budget, maximum cost);
- refuses up front if the worst-case cost is over budget;
- retries with back-off, and refuses to start an attempt that cannot finish before the deadline;
- trips a circuit breaker after repeated failures, and immediately on a bad credential;
- asks for strict JSON when needed, and logs a trace (without candidate text by default).

On top of the gateway, most generative tasks run in an **agent loop**: plan → generate → check with deterministic rules → if rejected, feed the exact rejection back and try again → give up after N attempts or T seconds and return a fallback marked "degraded".

**Gap:** if the model stops because it ran out of room, the gateway accepts the half-written answer as if it were complete. The test harness has already detected this and currently records it as known behaviour rather than fixing it.

### 2.3 Each agent, simply put

| Agent | What it does | Model | Status |
|---|---|---|---|
| **JD writer** | Drafts the seven-section job description | Terra | Live |
| **Company profile researcher** | Drafts the company profile from web research (official site, LinkedIn, Glassdoor, AmbitionBox) | Terra | Live |
| **Bodha** (SWOT) | Drafts the Job SWOT from the job | Terra | Live |
| **Sutra** (Tatva matrix) | Turns SWOT points into criteria: Weaknesses → Must-have, Strengths/Opportunities → Nice-to-have, Threats → Behavioural. Matches each against a built-in department model, uses AI only to name criteria it can't match, then runs the seven stages, weights and caps | Terra (filed under the *JD writing* task type) | Live |
| **Drishti** | Optional per-department strategic profile that adjusts weights | Terra | Live, but applies only if the job's department text exactly matches |
| **Matching Categories agent** | Proposes 5–8 resume-matching categories | Terra (also filed as *JD writing*) | Live |
| **Resume extractor** | Pulls structured data out of a resume | Luna | Live |
| **Yukti** (AI Score / matching) | Deterministic pre-screen, then AI scoring of resume vs. categories | Luna (a judging job on the cheaper tier) | Live |
| **Question writer** | Writes this candidate's questions from the JD, the frozen matrix and their resume, in six formats (evidence-based, MCQ, multi-answer, fill-in-the-blank, coding, short answer) | Terra | Live |
| **Vaada** (interviewer) | Rewrites each next question for the candidate, decides on at most one follow-up, re-asks when the answer is empty, gibberish or evasive | Terra | Live |
| **Answer classifier** | Labels each answer: real / empty / gibberish / off-topic / evasive | Terra (deterministic for empty and gibberish) | Live |
| **Miti** (scoring) | Five isolated evaluators score five dimensions in parallel. A deterministic aggregator combines them and applies the Must-have caps. Insufficient evidence lowers confidence, not the score | Terra | Live |
| **Siddhi** (PRISM report) | Assembles the report through a rule that every statement must cite its evidence. AI writes the 45–50 word remarks and the gap-analysis probes | Terra | Live |
| **Proctoring** | Face, gaze, tab and audio events detected in the browser. The server counts warnings; a separate analysis service handles speaker detection (the AI-text detector is off by default) | None (rules and ML models) | Live |
| **Video pipeline** | Transcribe (AWS), split into answers, feed the same scoring, compress and store in S3, delete the raw file | AWS Transcribe | Live |
| **Email agent** | Drafts lifecycle, outreach and BGV emails. The BGV email is checked against a fixed set of facts | Terra | Live |
| **Project evidence** | Reviews candidate project uploads without ever running them | Terra | Live |
| **AI Reach** (BD) | Finds prospect companies: similar to existing customers, plus Tavily web search | Terra | Live |
| **Contextual prefix writer** | Writes a one-line context note for each RAG chunk | Luna | Live, but its output is never read (see 2.4) |
| Competency transformation, situation classification, claim extraction, evidence tiering, technical questions | Configured as AI task types | — | **Nobody calls them.** The code doing that work is deterministic. |

### 2.4 The RAG flow, exactly

**Writing side (runs in production):**
1. A JD is published, a resume is parsed, or an assessment is completed. An hourly sweep also catches anything missed.
2. The document is split into roughly 400-character chunks along headings, with a small overlap.
3. Luna writes a short context line for each chunk (one AI call per chunk).
4. Each chunk is embedded with voyage-4 (1024 numbers) and stored with its company ID.

**Reading side (built, never used by the product):**
1. Two searches run: a vector search and a keyword search (keywords OR'd together).
2. The two result lists are merged by rank position ("reciprocal rank fusion").
3. Voyage's re-ranker reorders the results. If it's unavailable, a simple keyword re-rank is used and that fact is recorded.
4. If nothing is found, it retries once with a wider section filter, never a wider company scope.
5. Whole chunks are packed into a context block, and any chunk that looks like a prompt injection is quarantined.

**The only caller of the reading side is a "tool" that no production feature ever runs.** No question writer, scorer or report writer receives retrieved context. So you pay for chunk summaries and embeddings on every document and get nothing back. Retrieval quality is also unmeasured: the 60 test queries have not been checked by a human.

### 2.5 The harness (what "harness" means here)

A separate test engine, deliberately never imported by the product, that asks: *does the system behave the way it says it will when the environment misbehaves?*

- **Scenarios:** 25 scripted cases across smoke, regression, integration, adversarial, safety and performance tiers.
- **Faults it can inject:** AI model errors, embedding failures, Redis down or slow, S3 failure, background dispatch failure, and moving the clock.
- **How it checks:** results are read back from the database over a second connection, so a write that looked successful but was rolled back is still caught. Every injected fault must show up visibly in the result; silently surviving counts as a finding.
- **Exit codes:** 0 = pass, 1 = fail or regression, **3 = could not be measured.** "Unmeasurable" is never treated as a pass.
- **Release gate:** built from a measured noise level. It currently always answers "not releasable on quality", because the human-labelled decision set has **0 cases**.
- **In CI** on every push: the harness tiers (performance only on manual runs), plus the interview, agent, retrieval and worked-example evals.
- **Code only, not in CI:** the LLM judge jury (Groq and Gemini judges), the adversarial, trajectory and report evals, and live vendor verification.
- **Caveat:** the interview eval mostly exercises an older interviewer path that the live product no longer uses.

### 2.6 Security and tenancy

- Web requests run inside a per-company database scope.
- **Every background job runs with row security fully bypassed**, so company isolation in background work depends only on each query remembering to filter by company.
- Sign-in uses Firebase identity plus the app's own session cookies, backed by a revocable Redis session.

---

## 3. Broken parts (ranked by impact)

### Critical / high

1. **Creating a job skips the publish gate.** The Create Job page always creates and publishes in one step. That bypasses the "frozen Tatva matrix required before publishing" check and the Publish permission (only "create job" is needed). Jobs go live with no SWOT and no matrix, and stay labelled "Draft" internally while live. The proper publish action exists but no screen calls it, which also means the JD is never indexed on this path.
2. **Applying creates an assessment session for everyone.** When a job is ready, every application creates an assessment row and starts AI question generation. The consequences:
   - AI money is spent on applicants nobody invited, with no credit check.
   - The matrix "reopen" guard counts that row as "already invited", so **the first application permanently locks the matrix.**
   - The candidate is sent straight to the assessment page, which says "not available".
3. **Candidates are locked out of Employment History and BGV.** Those screens require a *staff* sign-in and a *candidate* database scope at the same time, which no single login can satisfy, so every request fails. As a result the employment declaration can never be submitted, the offer gate can never be cleared for experienced candidates, and BGV document upload and HR-email correction have no screen anyway. Tests pass only because they fake the sign-in.
4. **Delivered reports can be overwritten.** If scoring runs twice (a sweep racing the first run, a duplicate trigger), the second run rewrites the existing report in place and adds a second evaluation record. The lock only stops two runs at *the same time*, not one after another.
5. **Sourced and auto-matched candidates are recorded as "applied".** Single resume uploads, and databank candidates that matching auto-links, get the default status "applied": no validation answers, no history, no feed entry. They can then be invited directly, which breaks the "sourced is not applied" rule.
6. **The corporate email sender is never used.** The screen accepts a sender, but it is never saved on the email, so every lifecycle email goes out from the platform mailbox and revoking a sender does nothing.
7. **Two recruiter status routes bypass the pipeline.** The Hiring-Manager decision and a direct status change write history without moving the candidate's actual status. They also skip the BGV offer gate, the feed update and emails. The dashboard and the job table can then disagree about where a candidate is.
8. **The reconcile sweep rebuilds matrices humans emptied.** A reviewer who deletes every criterion gets them all revived by the 15-minute sweep. This same bug was already fixed in another place on 2026-09-21, but not in the sweep.

### Medium

9. **Video mode scores structured questions as unanswered.** MCQ, fill-in-the-blank and coding answers given on video get no score. Video mode also re-asks questions that were already pre-filled from the resume.
10. **Graded against a question the candidate never saw.** When a rewritten question is rejected as a repeat, the candidate sees the old text, but the new text and its rubric are the ones saved and used for grading.
11. **Scoring can start before the last answer is saved.** Background tasks are handed off before the database commit. With the local runner, or a fast task, scoring or emails can run on incomplete data, or find no row at all and skip.
12. **Matching can score the wrong resume or skip a candidate.** Search picks each person's "best" resume, which may not be the one they applied with. People can be scored twice. A candidate whose AI scoring fails twice is left unscored, only logged.
13. **Matching runs before categories are saved.** Publishing and databank upload trigger matching straight away, so candidates are scored on the four legacy categories and then again later.
14. **Resume parse is lost if embedding fails.** An embedding outage rolls back the whole resume parse, even though a comment in the code claims the opposite.
15. **Match % shown vs. ordering used.** Match % includes a hidden "longevity" adjustment and isn't used for sorting. The sort keys are fixed to "skills" and "experience" even if the recruiter removed or renamed those categories.
16. **Tatva edits aren't restricted to the Hiring Manager.** Anyone who can create a job can add, edit, reorder or reopen the matrix. The specific edit permissions exist but are never enforced on these screens.
17. **Moving a criterion between columns can crash (500).** It happens if the target column already has, or once had, a criterion with that name. This is the same bug class as the 2026-09-21 pilot incident.
18. **The grade can change after the matrix is frozen.** The grade decides the question count and the matrix size limit.
19. **Reopen leaves the job marked "finalised".** So after reopening the matrix, the SWOT still refuses edits.
20. **Old matrix versions are not kept.** Saving again overwrites the criteria in place; the version record only stores a number and a date. Also, the tool meant to assess each candidate "against the version in force when they applied" is never called.
21. **Only the first assessment reminder is ever sent.** The 72-hour reminder is suppressed by the "one email of this type per application" check, but still counted as sent.
22. **The Proctoring report ignores job closure.** It stays readable after the job is closed, unlike the rest of the executive profile.
23. **Background progress tracking likely breaks after the first update** *(likely)*. The Redis client used for job progress is reused across event loops and then gives up for good. That can leave "matching progress" or outreach status stuck on warm Lambdas.
24. **Messages have no notification**, no unread badge, a retry sends the message twice, and only the last 50 messages are shown.
25. **Duplicate candidate records.** First sign-in always creates a new candidate even if a sourced record with the same email exists. Different parts of the app then pick different records, so the sourced-to-applied conversion silently fails.
26. **Template or fallback output is not flagged.** The JD template shows "Draft ready", default matching categories are unmarked, and fallback remarks and gap probes still carry the model name as if AI wrote them.
27. **Silent failures on live paths.** For example, the item scorer can swallow any error and return "no score" without logging it.

### Low / UX

- The Close Job message says "your candidate pipeline is unchanged", which is untrue: report access stops immediately.
- The job page edits the JD as separate text boxes, while Create uses one markdown document. There are two JD editing paths, and one of them doesn't keep the markdown in sync.
- The job's grade is shown as "Level" on every candidate row.
- Screen text says the matrix is built "from the JD after job creation"; it is actually built after the SWOT is saved.
- User-facing text still says "PPI" in places, and promises a "retake after six months", which no longer exists.
- Idle timeout: polling counts as activity, so an open video page never times out.
- Deleting a profile leaves the Firebase account, so the next sign-in silently creates a fresh profile.
- The "New Jobs" board lets a candidate fill the whole apply form before telling them they already applied.

---

## 4. Built but never used (dead weight)

**Framework packages with no production caller:**
- The tool layer and its permissions.
- The side-effect action ledger ("UNKNOWN" handling).
- The reasoning runner, planner and budgets.
- Agent learnings (nothing writes them, and a task exists to revoke them).
- The orchestration version resolver.
- Retrieved-chunk safety quarantine.
- The request coalescer.
- The "one adapter" between typed and video assessments.
- The PRISM export chokepoint (G4 plus the number ban for exports).
- The "AI Score hand-off", about 400 lines whose output is discarded.

**Other dead parts:**
- **Five configured AI task types** with no caller (listed in 2.3).
- **About 20 server routes with no screen:**
  - the job approval chain (submit / approve / send to hiring manager / publish);
  - the assessment **dispute and retention** routes (the Close dialog even tells users about the dispute process);
  - billing cancel and billing ledger;
  - BGV documents and HR-email correction, and the consent history;
  - "keep my profile" renewal;
  - the report library;
  - several duplicate candidate-status routes, dashboard calibration, and conversation unread counts.
- **Unreachable pages:** the old HR Review Screen and the Email Templates editor. They compile and are reachable by typing the URL, but have no links.
- **Rules that never take effect:**
  - The Must-have per-competency threshold (Runbook 12.1) is always empty.
  - The human-review input to the final gate is never passed in.
  - The situation-type weight layer is always off on the live path.
  - The question-count ceiling (40) can never be reached, because the largest range is 38.
- **Credit dead ends:** "credit headroom" has no caller, and the stored "credit deficit" is written but never read.

---

## 5. Scrap from deprecated features still in the code

| Old feature | What's left | Verdict |
|---|---|---|
| **OTP / SMS login** | Three login handlers that are no longer routed, the OTP login service, the SMS sending task, the MSG91 settings and secret, an unused OTP input component, "MSG91" in the public docs page, and an old auth validation script that tests routes that no longer exist | Delete |
| **Celery** | Two task "aliases" kept for queued messages that can no longer exist; one of them can still force-rebuild a human-reviewed matrix. "Celery broker" wording in the test compose and production scripts | Delete the aliases, fix the wording |
| **Multi-vendor AI (Groq / Gemini / OpenRouter)** | Old provider enums, the provider-key table and its encryption secret, which is **still injected into four pilot services** | Delete, and stop injecting the secret |
| **GCP / Cloud Run** | GCP settings in the example env file, pointing to deploy files that no longer exist | Delete |
| **40-question aspect form** | Still the public apply form, the outreach form, a hard-coded duplicate question list on the frontend, a "40-question application" gate on one status route, and the `/verification/outreach` route | Retire or unify |
| **Preset technical question bank** | Old tables still read in three places, the unused "technical_questions" AI task type, the unused "questions approved" column, a stale reminder task name | Remove the task type and column; keep the table for history |
| **Old interviewer paths** | Old "generate / reword" interviewer modes, used only by the CI eval | Point the eval at the live path, then delete |
| **`level` (old seniority field)** | Still written by the job edit form and read by the matching prompt, the relevance ranking, the SWOT prompt and public pages | Replace with grade and experience band |
| **Role Intake** | The transcript table is kept on purpose, but nothing writes to it any more | Document as history-only |
| **Company DNA** | Gone from live code; one stale comment in the coverage config | Fine |
| **Old verification system** | Table-list and schema mentions, the verification outreach route | Delete |
| **Leftover and stray files** | Stale compiled files for deleted modules (Intercom, swot_intake, verification_parsing), one orphaned prompt, eight unused UI primitives, three unused components | Delete |
| **Branding** | `pickready.app` email addresses still visible on the billing page and as the default sender | Owner decision |
| **LangSmith tracing** | Still wired, but no key is provided anywhere, and it runs beside the OpenTelemetry tracing | Pick one |

**Not scrap (deliberate):** `pickready` task and infrastructure names, `ppi` code names, the "shortlisted" status, the history tables, the Groq and Gemini *judges* (evaluation only).

**Repository hygiene:** `.codex/` and 13 new `.claude/skills/` folders are not git-ignored, so one `git add -A` would commit about 4 MB of third-party skill files. CLAUDE.md also has drift: it says there are 12 RUNBOOK-AMBIGUITY markers (there are 0), references a module that no longer exists, and describes question counts that no longer apply.

---

## 6. Scope for improvement (recommended order)

**Fix first (correctness):**
1. Make Create Job save a draft, and make publishing go through the real publish action with its matrix gate and Publish permission.
2. Stop creating assessment rows at apply time, or make everything (the reopen guard, question generation, the post-apply redirect) require an actual invitation. Change the post-apply screen to "Application submitted".
3. Fix the candidate sign-in requirement on the Employment History and BGV screens, and add one test that uses a real candidate login instead of a faked one.
4. After taking the scoring lock, check "report already exists → stop". Hand off background work only *after* the database commit.
5. Give sourced and auto-linked candidates the status "sourced", with history.
6. Save the chosen sender on each email.
7. Route every status change through the one pipeline gateway, and delete the two bypass routes and the old review page.
8. Make the reconcile sweep respect a matrix a human emptied, and add a name-clash check on drag-between-columns.

**Then (fairness and cost):**
- Check credits for the whole invite batch; draft emails in the background, with an optional review step.
- Match by candidate, always scoring the resume they applied with; sort the table by Match % or show the unadjusted number; build the sort keys from the job's own categories.
- Handle structured questions properly in video mode, or don't serve them there.
- Treat "the AI ran out of room" as a failure in the gateway.
- Either **wire the RAG index into question writing and report writing, or stop indexing** until something reads it.
- Put Sutra naming and matching categories on their own task types, move question writing off the "judge" task type, and decide whether Yukti's scoring belongs on Terra.

**Then (clarity and trust):**
- Show each criterion's seven-stage detail and provenance in the Tatva chip, plus the SWOT points Sutra rejected.
- Keep full snapshots of each frozen matrix version, and wire the version resolver into scoring.
- Enforce the Hiring-Manager-only edit permissions on the matrix, and lock the grade after freeze.
- Flag template and fallback output on screen and in the report record.
- Add message notifications, Forgot password, a "Keep my profile" button, and screens for dispute and retention.
- Unify the public apply form with the six-field in-portal form, and remove the mandatory Age and Gender.
- Give background jobs a company-scoped database session wherever the job belongs to one company.

**Finally (cleanup):**
- Delete the scrap in section 5 and the dead framework packages in section 4, or decide deliberately to wire them in.
- Ignore `.codex/` and the new skills folders in git; refresh CLAUDE.md where it has drifted.
- Seed the human-labelled decision set, so the release gate can measure quality instead of always answering "unmeasurable".


---

# PART 2: CLAUDE REPOSITORY ARCHITECTURE AUDIT

# Vivekium (PickReady) — Code-Level Architecture Audit

**Audited:** branch `integrate/pr6-rbac-stem-swot` @ `e67585d` (the line production actually runs), compared against `main` @ `f67eb58`
**Method:** read the code, not the docs. Every claim below was checked against running code paths (what a route or background worker actually reaches), not against docstrings or specs.
**Language:** abstracted on purpose — no file or function names, only concepts.

Legend: ✅ works as intended · ⚠️ works but differs from intent / risky · ❌ broken, dead, or not implemented

---

## 0. TL;DR — the 12 things that matter

1. ❌ **`main` is not production.** Production was deployed from a feature line; PR #7 (open) reconciles it. The deploy pipeline allows manual deploys of any branch, which is how this happened.
2. ❌ **The "typed tool layer" (the harness's core safety boundary) is called by zero live code.** Only evaluation scripts call it. Every agent reads the database directly.
3. ❌ **RAG is write-only.** Documents are chunked, embedded and indexed continuously, but no live agent ever retrieves from that index.
4. ❌ **The Miti ↔ Vaada "real-time" loop is dead.** Vaada checks the evidence ledger mid-conversation, but the ledger is only written after the conversation ends. On a first assessment, Vaada always sees an empty ledger.
5. ❌ **Whole subsystems are unreachable:** the orchestration coordinator, the reasoning planner/runner, the agent world-state/stopping layer, experience memory. Built, tested, never executed.
6. ⚠️ **Pre-assessment ranking ignores most of what matching computes.** The recruiter's table is sorted by the "Skills" category score, then "Experience". The overall score, the custom categories, role alignment, education and the tenure signal do not affect order. If the recruiter renames/removes the Skills category, the order falls back to application time.
7. ⚠️ **Two scoring authorities in one report.** Per-item grades come from the old rubric scorer; the overall grade comes from Miti.
8. ⚠️ **A fake-score fallback still exists.** If the model fails during item scoring, a hash of the answer becomes a score in the 45–94 range (flagged for human review, but still a plausible number).
9. ⚠️ **"Run AI Matching" pulls in candidates who never applied** — any consenting Databank candidate on the whole platform, not scoped to the company.
10. ⚠️ **Matching prompts include the company's past shortlisted/offered/joined candidates as "success patterns"** — a clone-hiring bias channel.
11. ⚠️ **Siddhi's citations are structural, not semantic.** It checks that each statement points at *some* recorded answer, not that the answer supports the statement. The citation trail is stored but never shown to the recruiter.
12. ❌ **The "7-stage Matching Requirements form" does not exist.** The seven stages are Sutra's internal pipeline. The recruiter sees a 5–8 item category list.

---

## 1. Repository state

| Item | Finding |
|---|---|
| Default branch | `main` @ f67eb58 — stale |
| Production line | `feat/ai-upgrade…` → consolidated into `integrate/pr6-rbac-stem-swot` |
| PR #7 | Open. "Merge ai-upgrade branch into main … everything deployed since 2026-09-06" |
| PR #5 | Also still open against `main` (permission fixes, tenant-creation crash) |
| Deploy trigger | Push to `main` **or manual dispatch on any branch** — the manual path is how prod diverged |
| Size | ~166k lines of backend Python, ~1,700 files, 329 backend test files |

**Fix first:** merge PR #7, protect `main`, remove "deploy any branch" from manual dispatch (or restrict it to tags on `main`).

---

## 2. Client-side product flow — intended vs implemented

| # | Your intended step | What the code does | Status |
|---|---|---|---|
| 1 | Job description creation | AI-assisted JD drafting; JD stored as structured fields + a markdown document. Occupational (STEM) classification runs on it. | ✅ |
| 2 | Job posting | Fixed posting window + grace tail; renewing a job splits applicants into "Old Profiles" and "New Profiles". | ✅ |
| 3 | Job SWOT form | AI drafts four sections from the job record only (no web, no invented company facts). Human edits win: once a person saves, regeneration is refused unless explicitly confirmed; the previous version is snapshotted for undo; concurrent edits are version-checked. A failed draft is shown as a failure, never a template. | ✅ |
| 4 | Matching Requirements "7-stage form" | **Does not exist as a form.** Recruiter sees a list of 5–8 matching categories (AI proposes 5; server enforces min 5, max 8). The "seven stages" are Sutra's internal transformation (see §4). | ❌ vs intent |
| 5 | Tatva Assessment Matrix | Built by Sutra from the saved SWOT. Recruiter edits it as skill chips across Must-have / Nice-to-have / Behavioural, then freezes it. Only a human action can freeze. A job is "ready for candidates" only when **both** the matrix is frozen **and** the matching categories are finalised. | ✅ |
| 6 | Run AI matching | Hybrid retrieval + per-category LLM scoring (see §4, Yukti). | ✅ / ⚠️ |
| 7 | Ranked candidates + AI report | Word grades and 25–30 word comments per category, plus an overall comment. **Order is skills-first, not overall** (see §6). | ⚠️ |
| 8 | Send assessment invitations | Signed, expiring invite link bound to both the application and the invited email. Forces portal login first, then lands on the assessment. | ✅ |
| 9 | Receive assessment responses | Mandatory proctoring, adaptive conversation, multiple question formats (text, code editor, video, MCQ-style). | ✅ |
| 10 | Executive profile = PRISM + Proctoring | Generated **independently** (a proctoring failure cannot erase a PRISM report) and rendered together, with Proctoring as the last section. Reports are **held** (not generated) if the company is out of credits, and released on top-up. | ✅ |

### How the SWOT becomes the Tatva Matrix (not visible in the UI)

| SWOT section | Becomes |
|---|---|
| Weaknesses | **Must-have** (highest weight) |
| Strengths | Nice-to-have (deliberately *down*-weighted) |
| Opportunities | Nice-to-have |
| Threats | Behavioural |

- Every blank-line-separated paragraph is one candidate competency.
- A department competency "menu" fills gaps. The company's strategic context (Drishti) is injected as a second layer.
- Hard ceiling: **max 6 scored items** (Must + Nice combined). Behavioural is bounded by the grade's question budget. Anything cut is listed as a visible rejection.
- A matrix with human-added items cannot be silently rebuilt from the SWOT.

⚠️ **Product gap:** the JD's own required skills do **not** flow directly into Must-have. Must-have comes only from SWOT *Weaknesses* (plus the department menu). A thin Weaknesses section produces a thin Must-have list, regardless of how rich the JD is.

---

## 3. Candidate-side product flow

| # | Your intended step | What the code does | Status |
|---|---|---|---|
| 1 | Login | Firebase (email/password + Google). One login can map to several workspaces; the user picks one. | ✅ |
| 2 | Profile + resume | Structured profile form + main resume + project evidence uploads. Resume: upload → parse → structured fields → resume-stage pre-screen grade → embedding → search index. Parsing and indexing are separate so an embedding outage doesn't force a re-parse. | ✅ |
| 3 | Apply for jobs | Public job page → auth → mandatory validation fields → application. Candidate can edit during the grace window. | ✅ |
| 4 | View status | Applications page with a status per application. | ✅ |
| 5 | Write assessments | Consent screen → device/system check → proctored conversation. Declining proctoring = no assessment (no bypass). | ✅ |
| 6 | Further status + emails in portal "along with Gmail" | Durable **Updates** feed + native **Messages** threads + outbound email (Gmail SMTP / SES). | ⚠️ |

**On "along with Gmail":** there is **no Gmail inbox integration**.
- Outbound mail is sent through Gmail SMTP, with the company's address as Reply-To. When a candidate hits Reply, it lands in the **company's mailbox, not in the portal thread**.
- The only inbound-email path that writes into portal threads is for **background-verification replies from past employers**.
- If "in this portal itself along with Gmail" means candidate email replies should appear in the portal, that is **not implemented**.

**Features present that are not in your intended flow** (scope you may not realise exists): Background Verification (candidate-owned, with employer inquiry emails), Databank outreach, credits/billing/subscriptions, a BD/sales portal, public employer pages, support tickets, STEM classification, an owner/super-admin console.

---

## 4. The six agents — what each actually does

**Framing:** the six names are a *label table* over older modules, not six independent agents. Bodha and Sutra run under the **same internal agent identity**, so they have identical data permissions — no separation between them.

### Bodha — Job SWOT
- **Trigger:** recruiter opens / generates the SWOT on the JD tab.
- **Input:** title, department, level, experience band, JD text, derived skills/responsibilities, company About/Work-life/Benefits.
- **Does:** one LLM call → four sections → stored as a draft. Human edits latch permanently.
- **Not done:** no critic/retry loop (unlike most other agents); no market or web context.
- **Dead part:** the "situation type" multiplier (from the retired Role Intake interview) is hard-wired off, yet still listed as part of Bodha.

### Sutra — Tatva Matrix (the "seven stages")
Per SWOT paragraph:

| Stage | What happens | Who decides |
|---|---|---|
| 1. Competency | Name it (match the department menu, else ask the model) | Menu / LLM |
| 2. Observable evidence | "What would we see if this were true?" | LLM |
| 3. Evidence sources | Lookup | Deterministic |
| 4. Assessment method | Chosen *from* the sources | Deterministic |
| 5. Weight | Department baseline × company emphasis × SWOT quadrant, clamped to declared bounds, every term recorded | Deterministic |
| 6. Threshold | Evidence-quality requirement | Deterministic |
| 7. Disqualifier | Optional | Deterministic |

- Refuses to build without a saved SWOT. **No fallback matrix** (the old JD-noun-phrase fallback was deleted — good).
- Output is a *draft*. Only the Hiring Manager's freeze makes it binding.

### Yukti — Matching (see §6 for the full pipeline)
- Resume-only judgement per matching category, 1–10 internally, word grades externally.
- Overall = plain average computed in code (the model never sets it).
- If the model is unavailable: a deterministic resume-evidence pre-screen, **capped below the top grade** (a resume alone can never earn "Highly Matching"). Good design.

### Vaada — Candidate conversation
- **Coverage plan is fixed per job:** same competencies, same order, for every candidate (keeps reports comparable).
- **Wording is per candidate:** each question plus its rubric is written from the candidate's resume at the moment it's asked, in one call, and both are saved before the candidate sees the question.
- **Two small LangGraph state machines:**
  - *After an answer:* budget check → substance check → "is a follow-up worth it?" (LLM) → validate.
  - *Before the next question:* plan → rephrase naturally referencing prior answers (LLM, temperature 0.7) → validate that the meaning, named technologies and single-ask were preserved; otherwise use the stored text.
- **Hard limits:** max one follow-up per question and a persisted cap per conversation. The total turn count cannot be extended by any model output.
- **Any failure** → falls back to the stored question. The candidate always finishes.
- ❌ The "ask about contradictions Miti found" behaviour reads an evidence ledger that is still empty at that point (see §7.3).

### Miti — Scoring
- Runs **after** the conversation, inside report generation — **not in real time**, despite its label.
- **Gate G1:** no frozen, approved matrix → scoring refuses (no default matrix). Correct.
- **Five isolated evaluators run concurrently**, one per internal dimension: Verified Competence, Track Record & Impact, Role & Context Fit, Authenticity & Consistency, Trajectory & Potential.
  - Each sees only its own evidence, with the candidate's name stripped.
  - Temperature 0.
  - None sees the others' results.
- Then: contradiction triangulation → deterministic aggregation with caps. One "Not Matching" Must-have caps the overall at "Moderately Matching".
- **Evaluator failure = "insufficient evidence", not "poor candidate".** Correct semantics.
- ⚠️ Miti produces the **overall** grade. The **per-item** grades still come from the older rubric scorer — two authorities.

### Siddhi — PRISM report
- **Makes no model calls itself.** It is a deterministic assembler and validator.
  - The prose (45–50 word remarks, gap probes, overall summary) is written upstream by the scoring stage.
  - Siddhi turns every grade, remark, gap and probe into a "statement" and refuses any statement with no evidence pointer.
- **"No evidence of X" statements** cite the evidence that was *searched*, so an assessment gap is not reported as a candidate gap. Good.
- ⚠️ **Citation = pointer existence, not support.** A remark on "Kafka" passes if it points at the item's recorded answers, even if those answers never mention Kafka.
- ⚠️ **The citation trail is stored on the immutable report but never sent to the UI.** Recruiters cannot see it.
- ⚠️ **One uncited statement fails the entire report task.** Correct in principle, but there is no partial-delivery path and no alert surface for it.
- ⚠️ **The "Miti grades must equal Siddhi grades" check compares a value with itself.** The code admits it has no teeth yet.

---

## 5. Technical flow (end to end)

```
JOB SETUP
  JD saved ──► SWOT draft (LLM) ──► human edits/saves
                                        │
  Company context (Drishti, Layer 2) ───┤
  Department competency menu (Layer 1) ─┤
                                        ▼
                     Sutra: 7 stages per SWOT point ──► draft matrix ──► HUMAN FREEZE
  JD ──► matching categories (LLM, 5 default) ──► HUMAN FINALISE
                     both done ──► job "ready for candidates"

SOURCING / RANKING
  resume upload ──► parse ──► pre-screen grade ──► profile embedding + keyword index
                                             └──► chunk index (RAG) ── (never read)
  Run AI Matching ──► lock per job ──► JD embedding
     ├─ top 50 by meaning (whole-profile vector)
     ├─ top 50 by keywords (vocabulary-expanded)
     └─ every applicant (always)      + consenting Databank candidates (platform-wide)
     ──► pre-screen refresh ──► LLM per-category scores + comments (batches of 10)
     ──► overall = mean ──► tier ──► tenure nudge ──► save ──► hand-off record

ASSESSMENT
  invite (signed link) ──► login ──► consent ──► system check ──► proctored session
  per turn: [Vaada deliver graph] ask ──► answer ──► [Vaada decide graph] follow-up?
  conversation complete ──► background job:
     ┌─ rubric item scoring ─┐
     └─ validation capture ──┴─► synthesis:
            Miti G1 ─► evidence ledger ─► 5 evaluators ─► triangulate ─► aggregate+caps
            remarks (LLM + critic loop) ─► gap probes ─► Siddhi assembly/citations
            ─► immutable report (held if no credits)
  separately ──► proctoring report ──► rendered as last section of PRISM
```

**Infrastructure:**
- **LLM:** OpenAI only (Anthropic removed 2026-08-31). One router handles every model call:
  - LangGraph-driven retry loop, circuit breaker, per-attempt and total time budgets;
  - failure-class recovery: context overflow is retried compressed, refusals are not retried, schema violations are retried with the validator's message fed back.
- **Background work:** Celery is gone.
  - 32 task types run on Lambda.
  - 10 long ones (matching, matrix compile, report generation, etc.) run on ECS/Fargate via a tiny trigger Lambda (least privilege).
  - There is also a local-thread mode for dev and a record-only mode for tests.
- **Duplicate protection across containers:** database advisory locks (one matching run per job, one scoring per application).
- **Embeddings:** Voyage. **Vector store:** Postgres + pgvector.

---

## 6. Ranking — what actually decides the order

The ranked table is sorted **server-side** in this order:

1. Assessed candidates first
2. Assessment overall score
3. **Skills category score** (resume stage)
4. **Experience category score** — or, for managerial grades and above, behavioural first, then experience
5. Behavioural average (only exists after assessment)
6. Application time (tie-break)

**Consequences:**
- ⚠️ For the **unassessed pool** (the list the recruiter uses to choose whom to invite), order = Skills score → Experience score → application time. Role alignment, education, "behavioural signal", every custom category, the overall mean, and the tenure/longevity nudge **do not affect order**.
- ❌ The tenure/longevity adjustment only changes the stored overall score, which is not an ORDER BY key for the unassessed pool. It has no visible effect.
- ❌ If a recruiter deletes or renames the default "Skills present" category, that sort key is empty for everyone. The unassessed pool is then effectively ordered by **when they applied**.

---

## 7. Broken parts (verified, ranked)

| # | Severity | Finding |
|---|---|---|
| 1 | P0 | **Source of truth broken.** Production ≠ `main`; manual deploy of any branch is allowed. |
| 2 | P0 | **Harness boundary unused.** The typed tool executor (permissions, validation, timeouts, compensation stripping) has no live caller. All six agents read the database directly. The "salary never reaches a prompt" guarantee is enforced per call site, not by the layer. |
| 3 | P1 | **Miti ↔ Vaada loop dead.** Evidence and claims are written only during post-conversation scoring. Mid-conversation, Vaada's "probe contradictions" logic reads an empty ledger. |
| 4 | P1 | **RAG write-only.** Indexing and an hourly repair sweep run; hybrid retrieval, retry-with-widening, reranking and context assembly are only reachable through the unused tool layer. |
| 5 | P1 | **Ranking ignores most categories** (§6). |
| 6 | P1 | **Dual scoring authority** (rubric item grades + Miti overall). |
| 7 | P1 | **Hash-based fake item score** on model failure (45–94). |
| 8 | P1 | **Tatva threshold gap.** Per-competency numeric Must-have minimums are not modelled; the code deliberately leaves them empty rather than inventing them. Correct direction, unfinished feature. |
| 9 | P2 | **Unreachable subsystems:** orchestration coordinator/router/enforcement, reasoning planner/runner, agent world-state/stopping/idempotency, a Siddhi delivery module, the tool manifest, a canonical-assessment module. |
| 10 | P2 | **Experience memory dead.** Learnings are written only by the unreachable reasoning runner; the live "revoke learnings" task revokes nothing. |
| 11 | P2 | **RAG repair hole.** The sweep only finds documents with *no* chunks; chunks stored without embeddings (after an embedding outage) stay keyword-only forever. Moot until retrieval is wired. |
| 12 | P2 | **Comment padding.** If the model misses the 25–30 word range, canned filler sentences are appended ("Reviewers should confirm this evidence during a structured screening conversation…"). This is visible slop in client-facing text. |
| 13 | P2 | **Siddhi citation checks existence, not support**; the trail is hidden from recruiters. |
| 14 | P2 | **Bodha and Sutra share one permission identity**, so there is no least-privilege split. |

---

## 8. What "harness" is exactly implemented

**Short answer:** a custom, typed agent-execution framework with local LangGraph state machines. It is not CrewAI, and not one big LangGraph graph. **Most of the framework is built but not wired.**

| Layer | What it is | Live? |
|---|---|---|
| Agent identity table | Six names → runtime ids, triggers, portals, produced/consumed artifact types; a test checks every listed module is reachable | ✅ live (metadata) |
| Bounded agent loop | generate → deterministic critic → feed critique back → retry within attempt/time/token budget → degrade | ✅ used by: report remarks, Drishti questions, Sutra naming, matching categories, question writing, gap probes, interviewer, lifecycle emails, answer classification, BGV, company research. **Not** used by SWOT drafting or the main matching scorer. |
| Typed tool layer | Permissioned, schema-validated, time-bounded tool calls (read JD, read resume, retrieve context…) with deny-by-default grants | ❌ zero live callers |
| Artifacts / provenance | Immutable typed hand-off records (SWOT evidence, matrix, AI score, answer events, PRISM) with producer, consumers, version, sources | ⚠️ published *after* the database commit as provenance; nothing reads them to make decisions — the database is the real source of truth |
| Gates G1–G4 | Frozen matrix / evidence sufficiency / consistency / human hold | ✅ live inside Miti |
| LangGraph | Vaada decide + deliver graphs; report graph (scoring ∥ validation → synthesis); LLM router retry loop; web research | ✅ live |
| Orchestration coordinator / router / enforcement | Declared agent routing and activation | ❌ unreachable |
| Reasoning planner / runner + experience memory | Plan → observe → reflect → replan → learn | ❌ unreachable |
| Agent actions (world state, stopping, idempotency) | Agent-level action ledger | ❌ unreachable |
| Worker harness | Lambda / ECS / local / record backends; task spec = name + handler + route + retry policy | ✅ live |
| Test harness | Separate scenario runner (8 scenarios: adversarial resume, duplicate delivery, out-of-order transitions, billing-once, ranked-pool performance) | ✅ exists; **no full hiring-journey scenario** |

---

## 9. RAG — what is actually implemented

There are **two** retrieval systems.

**A. Candidate retrieval (live, used by matching)**
- Whole-profile embedding vs JD embedding (pgvector), top 50.
- Full-text keyword search with a shared vocabulary/ontology expansion (OR-style), top 50.
- Union with every applicant (retrieval ranks; it never decides eligibility).
- No chunks, no reranker, no fusion formula — just union order.

**B. Evidence RAG (built, indexing live, retrieval dead)**
```
document (resume / JD / assessment transcript)
 → structure-aware chunking (Q&A kept together)
 → optional contextual prefix before embedding
 → embed (Voyage) → Postgres chunk table (tenant-scoped, per-chunk ACL)
 ── everything below this line has no live caller ──
 → semantic search + keyword search
 → Reciprocal Rank Fusion
 → optional Voyage rerank
 → dedupe → token-budgeted context → (would feed an agent)
 → one widening retry if empty (never crosses tenant)
```

**Where it should plug in:**
- Miti's evaluators (evidence passages per competency).
- Vaada's question writing (resume passages relevant to the competency being probed).
- Siddhi's citations (real passage-level support instead of "points at the item's answers").

---

## 10. Deprecated scrap still in the repo

| Leftover | Where it shows | Action |
|---|---|---|
| Old task names: framework generation, technical-question generation, technical-question approval reminder | Still registered background tasks (compatibility aliases) | Delete after the queue window |
| SMS sending task + SMS service | Still imported by both email services | Remove; the product rule is no SMS |
| OTP service with phone verification | Still present; login is Firebase | Remove what remains after workspace-selection logic is extracted |
| Retired Role Intake (SWOT intake) | Table still defined; still referenced in provenance stage names and verification comments; the setup-review screen comment still describes it | Keep migration history; delete model + references |
| Situation-type multiplier | Always off; still listed under Bodha | Delete or re-source it from the SWOT |
| Retired employer-verification system | Its table survives, unread | Archive / drop |
| "Questions approved" field | Deprecated API field mirroring matrix approval; old column still exists | Remove from API after clients update |
| 4 legacy matching keys + "4-parameter" description | Fallback for old jobs; module description still says "4-parameter LLM scoring" | Backfill old jobs, delete the fallback |
| PFI naming | Behavioural sort key still called "pfi" | Rename |
| Technical Interview module | Deleted, still referenced in comments | Clean up |
| Candidate Settings page | Pure redirect to Profile | Remove later |
| Name soup | ReadyPick (frontend package, ECS trigger), PickReady (every task name), PPI / framework / functional skills report (persistence), Vivekium (product), Tatva / PRISM (UI) | Converge runtime names; leave persisted names |
| Unreachable packages | Orchestration, reasoning, agent actions, memory writers | Wire or delete — don't keep both |

---

## 11. Scope of improvement (priority order)

**P0**
1. Merge PR #7, protect `main`, deploy only from `main`.

**P1 — make the AI claims true**

2. **Wire Evidence RAG into Miti and Vaada** (biggest real quality gain available):
   - retrieve resume/project passages per competency for question writing;
   - retrieve transcript passages per competency for the evaluators.
3. **Record evidence per answer, during the conversation**, so the Vaada↔Miti loop actually works.
4. **One scoring authority.** Put item rubric scoring *inside* Miti; replace the hash fallback with "Not assessed".
5. **Fix ranking.** Sort the unassessed pool by the overall resume score, or by a recruiter-chosen category — not a hard-coded "skills_match" key.
6. **Route agents through the tool layer** — or delete it. At minimum, route matching, question writing and Miti through it so permission and compensation-stripping are enforced once.
7. **Feed JD must-have skills into Tatva Must-have** alongside SWOT Weaknesses.

**P1 — fairness / trust**

8. Scope or remove "success pattern" calibration from the matching prompt.
9. Filter Databank candidates per company, or clearly label "not an applicant" rows.
10. Replace comment padding with regenerate-or-abstain.

**P2 — simplify**

11. Delete unreachable subsystems (or wire them). A second, unused architecture is the main source of confusion.
12. Split the assessment/report module (it knows scoring, Miti, Siddhi, gaps, validation, persistence, fallbacks) into a one-way pipeline: evidence → item evaluation → Miti → Siddhi → persist.
13. Give Bodha and Sutra separate permission identities.
14. Show Siddhi's citations to recruiters (click a remark → see the answer).
15. Add one golden end-to-end scenario to the existing harness: company → job → SWOT → freeze → apply → match → invite → assess → Miti → Siddhi → proctoring → recruiter opens report. Use real Postgres; mock only the model provider.
16. Candidate email replies → portal threads (reply-token routing already exists for BGV; reuse it), if that is what "along with Gmail" means.

---

## 12. Differences from the ChatGPT audit

| Topic | ChatGPT said | Code shows |
|---|---|---|
| Tool/harness boundary | "Several flows bypass it" | **Every** live flow bypasses it — zero callers |
| Shared RAG | Agents use it for evidence retrieval | **No** live retrieval; index is write-only |
| Miti timing | Post-interview (implicit) | Correct, but it missed that Vaada's contradiction loop is therefore dead |
| Siddhi | "Citation system ensures claims have evidence" | Structural pointer check only; hidden from UI; makes no LLM calls itself |
| Ranking | "Deterministic overall → ranking" | Table sorts by Skills → Experience; overall and custom categories are ignored |
| Unreachable subsystems | Not mentioned | Orchestration, reasoning, agent actions, memory |
| Databank in matching | Not mentioned | Platform-wide consenting candidates auto-added |
| Success-pattern prompt bias | Not mentioned | Present |
| Bodha/Sutra permission split | Not mentioned | Same identity |
| Branch divergence, seven stages internal, dual scoring, hash fallback, Vaada graphs, gates, workers, proctoring isolation, RAG repair hole, no Gmail sync | Stated | **Confirmed** |

---

## 13. Not covered in depth

- **Frontend:** only the page map and the main job/report screens were checked.
- **Billing / credits, BD portal, BGV, employer pages, support:** outside your stated flow.
- **Department competency models:** the actual content of each department's menu.
- **Infrastructure:** Terraform beyond confirming the proctoring analysis service is deployed in production.
- **Tests:** not executed; PR #7's "6,381 backend + 261 frontend tests passing" is its own claim.


---

# PART 3: CHATGPT REPOSITORY ARCHITECTURE AUDIT

# PickReady / Vivekium Repository Architecture Audit

## Scope

This review is based on the current repository codebase and traces the frontend flows, API boundaries, workers, matching, assessment conversation, scoring, RAG, agent identity/handoffs, reports, proctoring, candidate portal, and the newer hiring-intelligence modules.

An important repository-state detail: `main` is currently not the true source of the latest system. Open PR #7 states that production deployments since September 6 came directly from the AI-upgrade line, while other work continued separately on `main`. The current reconciliation head is `e67585d...` and combines those lines.

PR #7 currently spans 927 changed files / 148 commits and reports 6,381 backend tests + 261 frontend tests passing. These are the PR's reported results; they were not independently re-executed during this audit.

Because of this divergence, the integration head was treated as the closest representation of the current system rather than relying only on stale `main`.

That branch situation itself is the first architecture problem.

---

# 1. What the product actually is right now

Your intended high-level flow is largely recognizable in the code.

## Employer

Job creation  
→ publish job  
→ Job SWOT  
→ configure matching criteria  
→ generate/review/freeze Tatva Matrix  
→ run AI matching  
→ ranked candidates + resume-stage AI report  
→ select candidates  
→ send assessment  
→ candidate completes assessment  
→ Miti evaluates evidence  
→ Siddhi produces PRISM  
→ proctoring report produced independently  
→ recruiter sees PRISM + Proctoring together

## Candidate

Login  
→ profile + resume + additional information  
→ apply to jobs  
→ see applications/status  
→ receive assessment  
→ take adaptive assessment  
→ see later status/updates  
→ native employer conversations in portal  
→ important communications also sent externally by email

So the fundamental product you intended is there.

But the technical implementation underneath it is in the middle of a major architectural transition.

There is a newer architecture layered over a lot of older PPI/matching/report machinery rather than having fully replaced it.

That is the central theme of the repo.

---

# 2. Your six agents, simply explained

| Agent | What it means in plain English | What it currently does |
|---|---|---|
| **Bodha** | Understand what this particular job needs | Handles the Job SWOT intelligence |
| **Sutra** | Convert the job context into an assessment blueprint | Builds the Tatva Assessment Matrix |
| **Yukti** | Decide who looks promising before assessment | Resume/JD matching, pre-screening, ranking |
| **Vaada** | Conduct the actual candidate conversation | Adaptive assessment interviewer |
| **Miti** | Judge the evidence collected about the candidate | Scoring, evidence evaluation, consistency checking |
| **Siddhi** | Turn everything into a recruiter-readable conclusion | PRISM synthesis, citations, gaps, validation points |

That six-agent concept is good.

The important technical reality is:

> These are product identities over existing execution systems, not six clean independent microservices or six completely isolated LangGraph agents.

Some are much cleaner than others.

---

# 3. Bodha: what actually happens

Bodha now essentially means:

> Take the final job and obtain the four pieces of job-specific context that a generic JD cannot tell us.

The current user-facing artifact is the **Job SWOT Analysis**:

- Strengths
- Weaknesses
- Opportunities
- Threats

The Hiring Manager can:

- type these manually;
- have AI generate them;
- edit generated content;
- regenerate;
- preserve/restore human edits.

The important thing is that the AI generation does not automatically become truth.

The saved SWOT becomes the human-approved job context that downstream logic consumes.

## What changed from the older system

There used to be a more elaborate conversational **Role Intake** system.

That has been retired.

On the latest integration line, the actual old Role Intake implementation is gone. Tests specifically check that it remains removed.

The stale `main` branch still contains older material around this, which is another consequence of the branch divergence.

## Assessment

This is considerably cleaner now.

A four-section editable document is much more understandable than making a Hiring Manager converse with another AI agent just to provide contextual information.

---

# 4. Sutra: probably the most important setup agent

Sutra takes the job intelligence and constructs the **Tatva Matrix**.

This is where the “seven stages” need clarification.

## The seven stages are not a seven-page Matching Requirements form

Currently the recruiter-facing matching setup is essentially an editable set of matching categories.

The **seven stages belong to Sutra's internal transformation of job intelligence into the Tatva assessment matrix.**

Conceptually it does:

1. Determine the competency.
2. Define what observable evidence would prove it.
3. Decide where that evidence could come from.
4. Decide how it should be assessed.
5. Determine its importance in this job context.
6. Determine the evidence threshold/quality expectations.
7. Add disqualifying conditions where appropriate.

Then these ultimately become things the client understands:

- Must-have
- Nice-to-have
- Behavioural

This distinction matters.

If the original design meant:

> The recruiter literally completes a seven-stage Matching Requirements form

then that is not what the current product implements.

You currently have:

**Recruiter-facing Matching Categories**

and separately

**Sutra's internal seven-stage Tatva transformation.**

They should not be described as the same thing.

---

# 5. There are actually three layers of job intelligence now

Sutra is no longer simply:

JD + SWOT → Matrix.

It has effectively become:

## Layer 1: Department intelligence

Generic understanding of what matters for that function/department.

## Layer 2: Drishti

Company/function-specific strategic context.

For example:

- what this company actually expects from this particular function;
- what outcomes matter;
- what environment the person operates in.

## Layer 3: Job SWOT

The situational context for this exact opening.

Then:

Department model  
+ Drishti context  
+ Job SWOT  
→ Tatva Matrix.

## Drishti is interesting, but architecturally awkward

Drishti behaves almost like another agent.

Yet the official architecture says there are six agents.

So there is already conceptual drift:

> Product architecture says six agents.  
> Technical architecture contains additional agent-like intelligence systems.

Either explicitly describe Drishti as a **context engine**, not an agent, or reconsider whether it deserves to exist as a separate product concept.

Otherwise eventually there will be Bodha, Sutra, Yukti, Vaada, Miti, Siddhi, Drishti, and more names until nobody understands the system.

---

# 6. Matching requirements: what is really implemented

This is distinct from Tatva.

For each job, the system generates a configurable list of **resume-stage matching categories**.

The recruiter can:

- add one;
- remove one;
- rename/edit one;
- save/finalize the collection.

The product wants roughly **5–8 criteria**, rather than forcing one universal set on every job.

The current defaults include concepts around:

- skill relevance;
- experience relevance;
- role/responsibility alignment;
- education/qualification;
- resume-visible behavioural signals.

After finalization, those criteria are frozen for that candidate-comparison cycle.

That is correct architecture.

Changing the criteria after candidate A was evaluated but before candidate B was evaluated destroys comparability.

## One thing to change

“Behavioural signal” based on a resume is conceptually dangerous wording.

A resume can indicate communication patterns or claimed leadership experiences.

It cannot establish behaviour in the same sense that the Tatva assessment does.

A better label would be:

**Resume Behavioural Indicators — Unverified**

or remove it from pre-assessment ranking entirely.

---

# 7. What Yukti actually does when you click “Run AI Matching”

This is more sophisticated than a simple embedding comparison.

The sequence is approximately:

JD  
→ understand/search job vocabulary  
→ retrieve possible candidates  
→ ensure all explicit applicants are included  
→ resume-level pre-screen  
→ LLM evaluates each candidate against this job's finalized categories  
→ deterministic overall calculation  
→ ranking  
→ AI comments/report

## Candidate retrieval

There are currently three sources contributing to the candidate pool.

### Semantic search

The JD is embedded and compared against candidate profile/resume embeddings.

### Keyword/full-text search

Job terms are expanded using the shared vocabulary/ontology, then searched against resumes.

Importantly, this is OR-style retrieval rather than requiring the resume to contain every keyword.

### Explicit applicants

Every candidate already linked to the job is included even if retrieval did not surface them.

That last part is very important.

It means:

> Retrieval helps rank/discover; retrieval does not decide eligibility.

An applicant cannot disappear simply because their resume used different vocabulary.

## Then the AI scoring happens

For each candidate, Yukti evaluates every finalized matching category.

The model creates:

- a judgment for each category;
- a short explanation;
- an overall explanation.

But the model does not get to invent the final mathematical overall value.

The category values are aggregated deterministically.

There are no old 35%/30%/20%/15% category weightings anymore.

That is an improvement.

## When the LLM isn't available

The system has a deterministic resume-evidence pre-screen fallback.

Importantly, that fallback is capped so that a resume-only evaluation cannot obtain the strongest possible assessed status.

Conceptually:

> A resume can make someone look promising.  
> It cannot prove that they can actually do the job.

That distinction is good.

---

# 8. Ranked Candidates and the first AI report

The “AI report” before assessment is a **resume-stage report**.

It is intentionally different from PRISM.

That report explains things such as:

- skills match;
- experience relevance;
- role alignment;
- qualification fit;
- overall resume-stage interpretation.

The UI deliberately avoids raw numeric scoring for recruiters and presents word-based ratings and explanations.

This separation is sensible:

**AI Matching Report**  
= what the resume suggests.

**PRISM Report**  
= what the actual assessment evidence supports.

This distinction should remain very clear in the UI.

---

# 9. Assessment invitation

A candidate cannot properly enter the assessment pipeline until setup is ready.

Setup is effectively complete when two independent things have been finalized:

**Matching categories are frozen**

and

**Tatva Matrix is approved/frozen.**

They are intentionally separate.

Once the recruiter selects someone:

candidate  
→ invitation  
→ candidate-specific assessment prepared  
→ candidate opens assessment

There is no longer a separate generic “technical question bank” acting as another assessment system.

Technical evaluation is folded into the Tatva matrix.

That is the correct direction.

---

# 10. Vaada: this one is genuinely becoming an agent

Vaada is much more than a scripted questionnaire now.

There are actually **two small LangGraph state machines** around the conversation.

## Graph A: after the candidate answers

It decides:

> Is there something useful to follow up on?

It checks things like:

- whether the answer actually answered the question;
- whether it was shallow/evasive;
- whether a follow-up budget remains;
- whether that topic has already been probed;
- whether a particular evidence gap remains.

If it is worth probing, it generates one follow-up.

Otherwise it moves on.

## Graph B: before the next base question

It decides:

> How should I say the next intended question given the conversation so far?

It can make the interview sound conversational rather than:

Question 1.  
Answer.  
Question 2.  
Answer.

But there is an important protection:

> Vaada is allowed to change the wording, but it isn't allowed to silently change what is being assessed.

For questions tied to a fixed rubric, it verifies that important terminology and substance remain.

## Hard limits

Vaada cannot interview forever.

The number of adaptive probes is bounded.

It also limits repeated questions.

If the conversational AI fails:

> it returns to the previously stored question.

That is a very good degradation strategy.

A model outage costs **adaptivity**, not the candidate's ability to finish the assessment.

---

# 11. Miti: this is the strongest part of the newer AI design

After the interview, Miti doesn't just ask:

> Give this candidate a score.

It tries to create an evidence-based evaluation.

It has five internal dimensions:

- Verified Competence
- Track Record & Impact
- Role & Context Fit
- Authenticity & Consistency
- Trajectory & Potential

These are internal reasoning dimensions.

Recruiters aren't supposed to treat them as another five arbitrary scores.

## The important design: evaluator isolation

Five evaluators operate independently.

Each evaluator receives:

- only the competencies relevant to its dimension;
- only the evidence relevant to those competencies;
- the appropriate rubric;
- role context.

It does not receive:

- the candidate's identity as a normal field;
- the other evaluator scores;
- the final composite;
- unrelated evidence.

Candidate names are additionally stripped from evidence where possible.

This is trying to prevent halo effects.

For example:

> The Trajectory evaluator should not think “Verified Competence rated this candidate highly, so I probably should too.”

The five evaluations run concurrently rather than sequentially.

That strengthens the isolation.

## If the evaluator fails

This is particularly good.

Provider failure does not mean:

> candidate = poor.

It means:

> insufficient evidence/evaluation unavailable.

That is the correct semantic distinction.

---

# 12. Miti then triangulates the evidence

After the independent evaluators finish:

evidence  
→ isolated judgments  
→ contradiction analysis  
→ evidence sufficiency checks  
→ integrity checks  
→ deterministic aggregation  
→ final overall assessment state

There are several gates.

Conceptually:

## G1: Are we evaluating against a real approved Tatva Matrix?

Blocking.

No approved matrix = no legitimate scoring.

## G2: Is the evidence sufficient?

Can trigger warnings/review but does not automatically reject someone.

## G3: Is the evidence internally consistent/authentic?

Again, it can trigger human review rather than turning an integrity flag into automatic rejection.

## G4: Has a human resolved an integrity hold where one is required?

This controls delivery/progression where human disposition is mandatory.

This part is thoughtfully designed.

---

# 13. One genuine hole inside Miti

There is an important unfinished control.

The architecture talks about per-Must-have thresholds.

But the currently frozen Tatva threshold structure is about evidence requirements, freshness/independence, etc., not a clean human-approved minimum score such as:

> Distributed systems must be at least X.

The current implementation deliberately leaves that numeric threshold mapping empty rather than inventing a value.

That is the correct failure direction.

But it also means:

> **The intended per-competency numeric Must-have threshold control is not actually implemented yet.**

Other evidence caps and review controls still work.

But this specific control is incomplete.

---

# 14. There are actually two scoring systems inside the assessment

This is one of the largest architecture concerns.

Before Miti calculates the final evaluation, the older functional assessment machinery still performs **per-Tatva-item scoring**.

Then Miti independently performs its five-dimension evidence evaluation and determines the aggregate/overall result.

So effectively:

Candidate answers  
→ rubric/item scoring  
→ individual Tatva item grades

and simultaneously/afterwards:

same evidence  
→ Miti's 5 evaluators  
→ triangulation  
→ deterministic aggregate  
→ overall assessment

This can be made logically correct.

But right now it is difficult to understand.

The report can have:

**item grades produced by one evaluation mechanism**

while

**overall grade is produced by another evaluation mechanism.**

That deserves a much cleaner contract.

Preferred architecture:

> One scoring authority. Miti owns evaluation. Per-item rubric scoring becomes a clearly defined sub-stage inside Miti rather than appearing to be a parallel legacy scorer.

---

# 15. There is also one fallback that should be removed completely

The older item-level scorer still has a deterministic fallback that can derive a plausible-looking score from a stable hash when model scoring fails.

The system now correctly marks reports produced through this route as requiring human review.

That is better than silently trusting it.

But this fallback should still be removed.

A provider outage should produce:

> **Not assessed / evaluation unavailable**

not a reproducible fake number that happens to fall somewhere inside the rating range.

Miti already gets this right.

The older scorer should behave like Miti.

---

# 16. Siddhi: what it actually does

Siddhi is not primarily the scorer.

Think of Siddhi as:

> Take all the evaluated evidence and create a defensible recruiter document.

It creates the PRISM report from:

- resume-stage AI Score;
- Tatva assessment results;
- Must-have evidence;
- Nice-to-have evidence;
- behavioural evidence;
- overall assessment;
- candidate-provided validation information;
- evidence confidence;
- claim-versus-evidence analysis;
- gaps;
- recommended human validation points.

## A very good architectural feature

Siddhi has a citation/evidence system.

Statements are connected to evidence references.

The composition layer checks whether important assertions actually have something behind them.

So conceptually:

> “Candidate demonstrated Kafka expertise”

cannot simply appear because the language model likes the sentence.

There must be a trace back to the evidence used to justify it.

That's one of the strongest ideas in the repo.

---

# 17. PRISM and Proctoring

The intended equation:

> Executive Profile = PRISM + Proctoring

is essentially what the recruiter experience now does.

But backend architecture keeps them independent:

Assessment completion  
→ PRISM generation

then separately

→ Proctoring analysis/report

The recruiter report experience later renders the Proctoring section together with PRISM.

That separation is good.

A proctoring processing failure should not erase or invalidate an otherwise completed competency assessment.

Also importantly:

> Proctoring observations do not automatically alter the candidate's competency grade.

They are informational/integrity evidence requiring human interpretation.

This should remain exactly that way.

---

# 18. Candidate product flow

The candidate side is now quite complete.

## Login

Candidate authentication exists and protects their portal.

## Profile

This is no longer just name + resume.

The candidate profile includes substantial structured information in addition to the primary resume.

There is also project evidence and other candidate intelligence functionality around it.

## Resume upload

The resume is:

upload  
→ parse  
→ structured profile extraction  
→ embedding/indexing asynchronously.

Parsing and indexing are deliberately separate operations so an embedding provider failure doesn't force the successful resume parse to repeat.

## Apply to jobs

Public job → authentication/profile → application.

Applications then appear inside the candidate portal.

## Application status

Candidate can see the state of each application rather than having to infer everything from email.

## Assessment

Invited candidates receive the assessment in their portal and complete it there.

Adaptive questioning happens server-side through Vaada.

## Updates

There is a durable Updates feed for lifecycle changes.

## Messages

There is now a real candidate ↔ employer conversation system.

But this is where the wording needs correction:

> It is **not Gmail integration**.

The system can:

- send external emails;
- show lifecycle communication inside Vivekium;
- maintain native conversation threads inside Vivekium.

It does not read the candidate's Gmail inbox and mirror Gmail inside the portal.

If the requirement literally means:

> Candidate should see his Gmail messages inside Vivekium

that feature is not currently implemented.

A cleaner product is native portal threads + outbound/inbound email threading rather than full Gmail inbox access.

---

# 19. What the RAG system really does

There is a real RAG engine now.

It's substantially better than a basic vector store.

The shared RAG pipeline is approximately:

**Document**

→ structured chunking

→ optional contextual prefixing

→ embedding

→ Postgres/pgvector index

→ semantic retrieval

+ full-text keyword retrieval

→ Reciprocal Rank Fusion

→ optional cross-encoder reranking

→ duplicate removal

→ token-budgeted context construction

→ agent

## Sources

The shared retrieval layer can represent sources such as:

- resumes;
- JDs;
- assessment context.

Structured project evidence takes a different evidence route rather than simply becoming generic chunks.

## Chunking

It is structure aware.

It tries to respect logical sections rather than blindly cutting every N tokens.

Assessment Q&A is kept together so the system doesn't retrieve a question without the answer.

## Contextual embeddings

It can generate a small contextual prefix for a chunk before embedding it.

Example conceptually:

Raw chunk:

> Implemented Kafka consumers across three services...

Embedding input may become:

> Candidate resume, experience at X, backend engineering section. Implemented Kafka consumers...

But the original chunk remains unchanged.

That's a reasonable contextual-RAG technique.

## Retrieval

Two independent retrieval mechanisms:

semantic  
+ lexical

They are fused using Reciprocal Rank Fusion rather than simply averaging incomparable similarity values.

## Reranking

Voyage reranking can reorder the fused shortlist.

If that is unavailable, it has deterministic degradation.

## Acquisition retry

If initial retrieval returns nothing useful, it can broaden the search exactly once.

It may:

- widen the candidate chunk pool;
- remove a section filter.

It does **not** widen tenant boundaries or start searching unrelated documents.

Good.

---

# 20. But there are actually TWO retrieval architectures

This is one of the biggest things to know.

The shared chunk-level RAG described above is **not what Run AI Matching primarily uses**.

Yukti still has its own retrieval engine:

JD whole-document embedding  
→ profile/resume whole-document embedding similarity

plus

JD vocabulary  
→ resume full-text search

→ union candidates

→ LLM matching.

Meanwhile the newer shared RAG engine performs chunk-level evidence/context retrieval.

So “RAG/retrieval” means two different systems.

## Is that automatically wrong?

No.

Candidate discovery and evidence retrieval genuinely have different requirements.

But they currently duplicate a lot of infrastructure and concepts.

A cleaner definition would be:

**Candidate Retrieval Engine**

Goal: find/rank candidate profiles.

and

**Evidence Retrieval Engine**

Goal: find exact evidence passages for an agent.

Then share the common infrastructure:

- embedding gateway;
- lexical normalization;
- vocabulary ontology;
- filtering;
- fusion utilities;
- telemetry;
- reranking;
- provenance.

Right now the distinction exists mostly because the two systems evolved independently.

---

# 21. One concrete RAG repair hole

The indexing flow handles embedding outages sensibly.

If the embedding call fails, it can still store the chunk text.

Therefore:

keyword search works  
semantic search doesn't.

Good degradation.

However, the reconciliation process searches primarily for **documents with no chunks**.

A document that already has chunks but whose chunks have `NULL` embeddings doesn't qualify as “missing from the index.”

So there is currently a hole:

> A transient embedding failure can leave a chunk permanently lexical-only unless something later causes it to be re-indexed.

Add a second reconciler:

## Semantic Index Repair

Find:

- missing embeddings;
- embeddings created with retired model versions;
- dimension mismatches;
- failed contextualization if retryable.

Then repair those independently.

---

# 22. What harness is actually implemented?

This is not CrewAI.

It isn't simply LangGraph either.

The most accurate description is:

> **A custom typed agent execution harness, with selective LangGraph state machines inside specific agent workflows.**

The intended harness has several layers.

## Identity

The six product agents have canonical identities:

Bodha → Sutra → Yukti → Vaada → Miti → Siddhi.

The identity layer declares:

- what starts them;
- what they produce;
- what they consume;
- what runtime implementation currently backs them.

## Typed tools

There is a strongly designed tool layer around operations like:

- read JD;
- read resume;
- read assessment;
- read frozen framework;
- retrieve RAG context;
- validate structured output.

The design is good:

- typed inputs;
- typed outputs;
- explicit timeouts;
- bounded retries;
- read/write risk classes;
- tenant scope;
- agent-specific grants;
- cache rules;
- telemetry;
- deny-by-default permissions.

## Agent loop

There is a bounded generation/review/regeneration loop.

Conceptually:

model attempt  
→ deterministic critic  
→ if accepted, finish  
→ otherwise give critique back  
→ retry within budget  
→ degrade/fallback when exhausted.

It predicts whether another attempt can finish before starting it.

That's much better than naïve `for retry in range(3)`.

## Agent-to-agent artifacts

There is also an immutable typed artifact concept for things such as:

- SWOT evidence;
- Tatva matrix;
- AI score;
- answer events;
- scoring state;
- evidence gaps;
- PRISM output.

Artifacts carry provenance including:

- tenant;
- job;
- candidate where applicable;
- producer;
- allowed consumers;
- version;
- source references;
- workflow/correlation identity;
- quality state.

This is a good design.

## Worker harness

Celery is gone.

The new task layer has:

### Lambda

For short background operations.

### ECS/Fargate

For longer operations like full matching/scoring.

### Local threads

For development.

### Record-only dispatcher

For tests.

Every task declaration contains:

- its name;
- actual handler;
- runtime destination;
- retry policy.

This is significantly cleaner than having task routing scattered across Celery configuration.

## Cross-process duplicate protection

The system also uses database advisory locks for things like:

- one matching run per job;
- one scoring operation per candidate/application.

That matters because two Fargate containers do not share Python memory.

Very good decision.

---

# 23. But the harness currently has a serious adoption problem

This is probably the most important purely technical finding.

The repository's harness philosophy says:

> agents reach data through the typed tool execution layer.

But when the actual current business flows are traced:

- matching;
- SWOT;
- Tatva compilation;
- PPI/question logic;
- functional assessment;
- parts of the interview;

several of them still access application/database state directly.

They don't universally pass through the tool executor.

Similarly, not every generative path goes through the same agent loop.

For example, Yukti's primary matching path has its own LLM orchestration logic.

Therefore:

> **The harness is implemented, but the product has not fully migrated onto the harness.**

This distinction is crucial.

The framework is excellent on paper and well tested in isolation.

But an invariant only exists if live business paths cannot bypass it.

Right now several can.

This should be one of the highest-priority AI architecture refactors after fixing the Git release process.

---

# 24. LangGraph: what exactly uses it

Another correction to a simplistic description:

Vivekium is **not one giant LangGraph graph**.

LangGraph is used locally where it fits.

## Vaada

Two small graphs:

answer  
→ decide whether to probe  
→ generate/validate probe

and

next intended question  
→ contextual composition  
→ validation.

## Assessment/report path

Another graph:

PPI/item scoring ─┐  
                  ├→ synthesis  
Validation capture ─┘

So scoring and candidate-verbatim validation can execute independently, then report synthesis waits for both.

That's a good use of LangGraph.

The outer product process is still governed primarily by:

- persisted job/application states;
- workers;
- explicit service orchestration;
- gates;
- artifacts;
- database state.

Keep it that way.

Trying to turn the whole hiring platform into one enormous LangGraph would make it worse.

---

# 25. Parts that are actually broken or incomplete

These are different from ordinary improvement opportunities.

## 1. Git/source-of-truth is broken

This is P0.

Production has been deployed from a feature branch while `main` diverged.

PR #7 exists specifically to repair this.

`main` is also currently reported as not protected by GitHub.

That means today:

> “Read main” and “read what production was built from” can give different answers.

Fix this before almost anything architectural.

One protected canonical trunk.

Deployments only from commits reachable from it.

## 2. Harness claims are stronger than harness enforcement

The code describes the tool executor as the one path agents use.

Live flows still bypass it.

Either:

**migrate all six agents to it**

or

**stop claiming it is a universal boundary.**

Migration is the better option.

## 3. New RAG is not the universal retrieval system

The new engine is good, but Yukti still has a separate retrieval stack.

Clarify ownership and share primitives.

## 4. Per-competency Must-have scoring thresholds are incomplete

The code explicitly refuses to fabricate them.

That's good.

But the feature remains incomplete.

## 5. Dual assessment scoring authority

Old rubric item scoring and new Miti evaluation coexist.

This should become one explicit scoring architecture.

## 6. Fake deterministic item-score fallback still exists

A provider outage should not generate a plausible candidate grade.

Abstain.

## 7. The “seven-stage Matching Requirements form” doesn't exist as described

The current seven stages are an internal Sutra transformation.

Recruiter matching requirements are a separate editable criteria list.

If seven actual client-side stages were expected, product and architecture have drifted.

## 8. Candidate Gmail integration is not implemented

Native portal messages + external email exist.

Gmail inbox synchronization does not.

---

# 26. Architectural debt to address next

There is a lot of sophistication here, but that sophistication is creating its own risk.

## Functional assessment has become a god-system

It currently knows about:

- rubric scoring;
- LangGraph;
- evidence;
- Miti;
- Siddhi;
- reports;
- validation;
- gaps;
- confidence;
- claims;
- persistence;
- legacy projections;
- fallback behavior.

There are multiple places where lazy imports are required specifically to avoid import cycles.

There are tests dedicated to preserving the import graph because particular import orderings have broken before.

That's a warning.

Split it into a strict pipeline:

Assessment Evidence  
→ Item Evaluation  
→ Miti Evaluation  
→ Siddhi Composition  
→ Report Persistence

with dependency direction enforced.

## Database state and agent artifacts are both acting like truth

Today artifacts are useful, but many flows still fundamentally communicate by reading database rows.

Sometimes an artifact is published **after the database commit**, and artifact publication failure is deliberately allowed not to undo the main operation.

That's a valid reliability tradeoff.

But it means the architecture is really:

> database state machine + provenance artifacts

not:

> artifact-driven agent system.

Choose and document that.

A sensible recommendation is to keep the database authoritative and treat artifacts as **verified handoff/provenance records**.

Trying to make both authoritative will eventually create disagreement.

## Too many conceptual vocabularies

The system currently has combinations of:

- PPI;
- Tatva;
- PRISM;
- functional assessment;
- Vivekium Profile;
- AI Score;
- Miti dimensions;
- matching categories;
- department intelligence;
- Drishti;
- evidence graph;
- validations;
- gaps.

A recruiter should see maybe four concepts:

**AI Match**  
**Tatva Assessment**  
**PRISM Report**  
**Proctoring Report**

Everything else should remain implementation detail.

---

# 27. Deprecated scrap found

Some leftovers are healthy compatibility; others should eventually disappear.

## Safe historical leftovers — keep them

### Company DNA migration history

Company DNA itself has been removed.

Old database migrations still mention it because migration history must remain reproducible.

Do not delete those migrations.

### Historic Technical section

A standalone technical-assessment track was removed and folded into Tatva.

The system still understands the historical category so old reports continue rendering.

Correct.

### Historic report fields

The old suggested-question/probe storage remains available for previous reports/rollback compatibility even though the new Gap Analysis has replaced it.

Again reasonable.

## Compatibility aliases that can eventually die

Old background task names still exist for things such as the previous PPI framework/technical-question generation.

They simply redirect into the new Tatva pipeline so rolling deployments/queued tasks don't explode.

Once every environment and queue is beyond the compatibility window, remove them.

## Old naming that is becoming expensive

A lot of internal persistence still says things like:

- PPI;
- framework;
- functional skills report;

while the product now says:

- Tatva;
- PRISM.

Some persistence names are not worth migrating merely for aesthetics.

But runtime concepts and APIs should gradually converge so engineers don't need a dictionary.

## Candidate Settings

The former candidate Settings route effectively exists as a compatibility redirect into the expanded Profile experience.

That's fine.

Eventually removable after old links/bookmarks age out.

## Role Intake

The current integration line correctly removed it.

The stale default branch is part of why remnants can still appear depending on which branch someone inspects.

---

# 28. The history of fixes shows where testing still needs to improve

Several comments in the code record previous failures that are very instructive.

At different points the codebase had situations resembling:

- a complete RAG index implementation existed but had **no real caller**, so the index stayed empty;
- jobs carried “generated” timestamps while actually containing zero generated criteria;
- matching succeeded and committed results, then report generation failed afterwards, causing the entire UI to say matching failed;
- a report quality gate was reading evidence from the wrong structure and could flag essentially every multi-dimension report;
- one report-writing path had an incorrect import that would fail when real evaluation reached it, but the deployed environment had no real candidates exercising the path.

Most of these have been corrected.

The lesson is more important than the individual bugs:

> You have very strong unit/component tests, but this architecture needs stronger **vertical-path tests**.

Add golden journeys such as:

Company  
→ create job  
→ SWOT  
→ freeze Tatva  
→ apply as candidate  
→ match  
→ invite  
→ complete real assessment  
→ Miti  
→ Siddhi  
→ proctoring  
→ recruiter opens final candidate profile.

Run that journey against a real Postgres instance with model providers mocked only at the provider boundary.

That catches:

> all individual pieces work but nobody wired A into B.

---

# 29. Recommended target architecture

Do not rewrite this.

There is too much good machinery already.

Simplify it around one canonical flow:

```text
                    COMPANY / JOB INTELLIGENCE
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
     Department Model       Drishti            Job SWOT
           │                   │                   │
           └───────────────────┼───────────────────┘
                               ▼
                         SUTRA / TATVA
                  approved frozen assessment
                               │
              ┌────────────────┴─────────────────┐
              ▼                                  ▼
      YUKTI PRE-SCREEN                       VAADA
  Resume ↔ JD matching                 Candidate assessment
              │                                  │
              │                                  ▼
              │                             Evidence Ledger
              │                                  │
              └──────────────────┐               ▼
                                 │             MITI
                                 │      Evidence-based evaluation
                                 │               │
                                 └───────────────┤
                                                 ▼
                                              SIDDHI
                                          PRISM synthesis
                                                 │
                             ┌───────────────────┴──────────────┐
                             ▼                                  ▼
                         PRISM Report                    Proctoring Report
                             └───────────────────┬──────────────┘
                                                 ▼
                                       Vivekium Candidate Profile
```

And underneath all six agents:

```text
Agent
  ↓
One Tool/Context Boundary
  ↓
Shared Retrieval/Evidence Infrastructure
  ↓
Bounded Agent Loop / Local LangGraph where useful
  ↓
Typed Result
  ↓
Quality Gate
  ↓
Database transaction
  ↓
Immutable provenance artifact
```

That is the architecture the repository is already trying to become.

The main work is no longer inventing more architecture.

It is **finishing the migration so there is only one architecture.**

---

# 30. Priority order

## P0 — Fix repository/deployment truth

Merge/reconcile PR #7 properly, protect `main`, and make production deployments originate only from the canonical branch with required CI.

Right now this is more dangerous than whether one evaluator could be improved.

## P1 — Finish the harness migration

Make the typed tools/context boundary real on Bodha, Sutra, Yukti, Vaada, Miti and Siddhi.

A direct database/context bypass should become an architecture-test failure.

## P1 — Establish one scoring authority

Fold existing item rubric evaluation clearly underneath Miti.

Delete pseudo-score fallback.

Missing evaluator = Unassessed, not synthetic grade.

## P1 — Clean retrieval boundaries

Keep Candidate Retrieval and Evidence RAG as separate use cases, but share the low-level:

- embedding;
- lexical;
- ontology;
- reranking;
- observability;

infrastructure.

Add semantic-index repair for chunks left without embeddings.

## P1 — Close the Tatva threshold gap

If the Hiring Manager is supposed to define a genuine Must-have minimum:

- model it explicitly;
- require human approval;
- version it with the matrix;
- have Miti enforce exactly that field.

## P2 — Split the assessment/report monolith

Miti, Siddhi, evidence capture, graph orchestration and persistence should be separate modules with one-way dependencies.

This will remove much of the lazy-import/import-cycle complexity.

## P2 — Eliminate compatibility code after a defined cutoff

Old task aliases, old PPI terminology in runtime surfaces, redirects and deprecated projections should not live forever merely because they are harmless today.

## P2 — Add real vertical harness tests

Not another 500 unit tests.

A handful of complete hiring journeys exercising:

- actual database state;
- dispatch;
- frozen Tatva;
- candidate conversation;
- evidence;
- Miti;
- Siddhi;
- proctoring;

will give substantially more confidence.

---

# Final assessment

Vivekium is no longer a CRUD hiring app with a few LLM calls.

There is a serious evidence-driven hiring engine here.

The current weakness is **architectural consolidation**, not lack of sophistication.

The repository already contains:

- gates;
- provenance;
- evaluator isolation;
- retrieval;
- artifact contracts;
- state machines;
- bounded loops;
- cross-process locks;
- async execution;
- human-review semantics.

Adding more mechanisms now would make the architecture worse.

The next phase should focus on:

> **deleting duplicate paths, enforcing one execution model, and making the strongest safeguards impossible to bypass.**
