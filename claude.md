# claude.md, ReadyPick Build Conventions

## Current release authority — Product Development Specification v4 (2026-08-14)

- **ReadyPick is a standalone AI-native product.** Product and marketing copy
  uses only ReadyPick branding. Do not reuse another product's name, logo,
  collateral, positioning language, client identity, or go-to-market story.
- **Customer roles are hierarchical, not flat.** The chain is Super Admin
  (`client`) -> Recruitment Manager -> Recruiter -> Hiring Manager. A person
  may manage only roles strictly beneath their own and may grant only a
  capability they hold. `users.permissions_json` remains the sparse per-user
  overlay, and every operational endpoint in jobs, pipeline and candidates must
  continue to enforce it through `require_capability(...)`; never add role-name
  branches to business routers. Legacy `hr_manager` ranks beside Recruitment
  Manager until existing accounts are migrated deliberately.
- **Job setup has two fixed, job-specific outputs.** The Reporting Authority
  SWOT intake informs a PPI matrix of Must-have, Nice-to-have and Behavioural
  criteria; the Matching Agent separately proposes at least five coarse,
  resume-only matching categories. Both are human-reviewed and finalized once
  per job. The PPI matrix supports drag/drop between Must-have and Nice-to-have.
- **One candidate conversation, one scoring agent.** Questions are generated
  per candidate from the JD, saved SWOT-informed matrix and resume, while the
  matrix stays identical for everyone on that job. Must-have and Nice-to-have
  use question rubrics; Behavioural uses judgement-based scoring. There is no
  standalone technical agent or split behavioural bot.
- **Validation is factual application data.** Current CTC, expected CTC,
  notice period, joining date, document readiness and the exact answer to
  "Why does this role interest you?" are captured before assessment, never
  scored, and shown as an explicit recruiter Q&A view. CTC is annual INR and
  the UI gives `4,00,000` as the worked example.
- **Client-visible grades are words only:** Highly Matching, Matching,
  Moderately Matching, Not Matching. Never expose scores, percentages or
  letter grades. Any Not Matching Must-have caps Overall at Moderately
  Matching. Rated PPI and Overall remarks are 45-50 words; AI Score category
  remarks and gap probes are 25-30 words.
- **Reports contain AI Score, then PPI Assessment, then Validation, then Gap
  Analysis & Action Plan.** Suggested interview questions are removed. Gap
  groups reuse item remarks, order Not Matching before Moderately Matching,
  state empty groups, and ground every probe in the candidate's actual answer.
  Exactly four number-free radar charts are shown: Overall, Must-have,
  Nice-to-have and Behavioural.
- **Credit gates are immediate.** Warn at or below 30%; at zero block job
  creation and new assessment starts. An active conversation may finish, but
  report finalization waits for top-up. Never fail silently or degrade access.
- **Company profile edits begin with professional web research.** Prefer the
  official site, LinkedIn, Glassdoor and AmbitionBox; reject Facebook, X,
  Reddit and Instagram. Show sources and require an explicit Edit action before
  a person can change or save the generated draft.
- **The customer AI Dashboard is deleted.** Do not restore its route, component
  or navigation entry. Items explicitly deferred by spec v4 (LinkedIn sourcing,
  go-to-market execution, sourcing-seat/ToS choices and Resume Alignment Agent)
  remain unimplemented until decided.

## Current hard rules, per-candidate technical questions + loop engineering (2026-08-06)

- **There is no preset technical question bank, and a company can never author
  one again.** `technical_questions` was a per-JOB list of stored strings a
  company created, edited and finalised through the Company Portal, and every
  applicant read the same strings whatever their resume said. The five routes
  (`GET/POST/PUT/DELETE /jobs/{id}/questions`, `POST /jobs/{id}/finalize`), the
  screens behind them, the generator and its schemas are DELETED, not
  deprecated. Pinned by `test_the_preset_technical_bank_generator_is_gone` and
  `test_the_preset_bank_routes_are_gone`. The TABLE survives unread: reports
  written before today were scored against those rows, and dropping it turns
  "what was this candidate actually asked?" into an unanswerable question.
- **A generated question is only sound if its RUBRIC was generated WITH it.**
  This is what unlocked the change. The old rule forbade generating a technical
  question mid-conversation (`interviewer.MODE_REWORD`) because the answer was
  scored against a preset question's stored rubric, so a fresh question would be
  graded against a rubric for a question nobody was asked.
  `technical_interview.write_question` writes both in ONE call and persists both
  before the candidate reads either. That is a STRONGER guarantee than the bank
  gave, where a recruiter could edit a stored prompt in the UI and leave its
  rubric behind.
- **The coverage plan stays deterministic; only the questions vary.**
  `technical_interview.skill_plan` is a PURE function of the JD and the grade,
  so every candidate for a job is probed on the same skills in the same order.
  That is what keeps two reports comparable now that no two candidates are asked
  the same words. Counts are unchanged: 20/17/15/12. Same rule the PPI framework
  follows, applied to the technical half.
- **Every generative task runs inside `services/agent_loop.run_loop`:
  plan -> execute -> evaluate -> (reflect -> improve)* -> verify.** Success
  criteria are DETERMINISTIC code, never an LLM judge -- the moment the guard
  matters most is the moment the provider is down, and a judge makes the
  criteria unfalsifiable as well as adding a second flaky dependency. A
  rejection is fed back VERBATIM as an instruction, which is the whole point: "you
  returned three of the five rubric bands" is a defect a model fixes when told,
  and the one-shot code it replaced threw the response away and shipped a canned
  string. Bounded TWICE -- `max_attempts` AND `deadline_seconds`, checked BEFORE
  each attempt, because N attempts at the per-task timeout is a multiple of what
  the user experiences. `run_loop` NEVER raises; it returns `fallback` with
  `degraded=True`, and `LoopResult.degraded` is the honest record that gets
  counted. Interactive loops get 2 attempts / 26s; background ones 3 / 240s.
- **A loop deadline must PREDICT the next attempt, not merely observe the
  elapsed time.** `elapsed >= deadline` sounds right and is not: one
  `conversation_turn` call is bounded by the router at 24s and the interactive
  deadline is 26s, so after a slow first attempt `24 >= 26` is False, attempt
  two starts, and the real worst case is 48 seconds with a candidate watching a
  text box. The check is `elapsed + longest_attempt_so_far >= deadline`, so an
  attempt that cannot FINISH inside the budget is never started, and a failed
  attempt's duration counts -- a timeout is the slowest and most informative
  thing that can happen.
- **A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED.** Measured on the live
  database 2026-08-06: 19 of 35 jobs, across three entire tenants, carried
  `framework_generated_at` and had ZERO competency rows. Every one of those jobs
  was permanently stuck at `questions_pending_review` with an empty framework
  nobody could approve, so no candidate on any of them could ever be assessed --
  and that IS what "the portal does not work for other companies" was. It stayed
  invisible because every health check asked the stamp rather than the table,
  including `remind_unapproved_technical_questions`, which filters on
  `framework_generated_at IS NOT NULL` and therefore specifically EXCLUDED the
  jobs whose generation had failed. Three changes, and all three are load-bearing:
  `ppi.generate_framework` now stamps ONLY when rows exist; the setup and
  framework GETs repair on read and report `framework_pending`; and
  `pickready.reconcile_job_setup` sweeps every tenant every 15 minutes asking
  the TABLE. Verified by repairing all 19 live jobs with every LLM provider
  down, on the deterministic fallback.
- **Job setup generates ONE thing, and that is why it could be renamed.**
  `pickready.generate_technical_questions` ran the bank generator FIRST and the
  framework generator second in one session, so any failure in the first half
  took the gating half with it. The task is now
  `pickready.generate_ppi_framework`; the old name stays registered as a
  delegating alias, because a beat entry, a queued message and a worker
  registration cannot be changed atomically during a rolling deploy.
- **A recruiter can read what a candidate was actually asked and answered**
  (`GET /assessments/transcripts/links/{link_id}`, `view_review_screen`). Keyed
  on the LINK, not the report: the transcript exists from the first answer and
  the report does not exist until the assessment finishes, so hanging it off the
  report would make the stalled-assessment case -- the one a recruiter most
  wants -- unreachable. Exchanges are paired SERVER-SIDE, because the follow-up
  rule (a probe reuses its parent's `question_key`, which is exactly how the
  scorers file it) would otherwise be reimplemented per client and drift.
  Paginated from day one; a non-managerial interview is up to 120 messages. No
  score, no rubric, no required level and no number crosses this boundary, and
  an answer is never re-worded or summarised -- a summary of an answer is not
  evidence of what someone said.

## Current hard rules, the conversational agent (2026-08-05)

- **"The pipeline passed" is not evidence that anything works.** On 2026-08-04
  every deploy was green, every revision was promoted, and production was
  serving the newest commit -- while three reported features did not work. The
  mechanisms were a change that shipped half of itself, a seed script judged by
  its exit code rather than by the rows it wrote, and smoke tests that only ever
  asserted status codes. A green run means the service answers HTTP. Verify a
  claim against the thing a user touches: a row count from the live database, a
  grep of the DEPLOYED image (`docker run --rm --entrypoint sh <digest> -c
  'grep -rl ... /app'`), or an actual API response. Never against the source
  tree, and never against a `--no-traffic` staged revision.
- **How freely a question may be generated is decided by HOW ITS ANSWER IS
  SCORED, never by preference.** A PPI answer is scored against its COMPETENCY
  across every answer filed under it, so the question is written fresh each turn
  from the JD, the resume, the competency and the transcript
  (`interviewer.MODE_GENERATE`). A technical answer is scored against THAT
  QUESTION'S own stored prompt and `rubric_json`
  (`functional_assessment._llm_score`), so only the phrasing may move
  (`MODE_REWORD`) and `_substance_preserved` refuses a rewrite that dropped a
  named technology. Generating a fresh technical question would grade an answer
  against a rubric written for a question nobody was asked.
- **The COVERAGE PLAN stays deterministic: which criterion, in what order, how
  many.** That is what keeps two candidates comparable, keeps billing where it
  is, and makes a run reproducible. What varies per candidate is how each
  criterion is approached, never which criteria there are.
- **A non-answer is never met with silence.** `answer_classification.classify`
  separates substantive / empty / gibberish / off_topic / evasive. Empty and
  gibberish are settled DETERMINISTICALLY with no model call, because the model
  being down is exactly when the guard matters; off_topic and evasive need the
  model, because they are well-formed prose that does not answer the question.
  Every degradation path returns "substantive": a false "evasive" silently
  penalises a real answer, and "I have not used Kafka" is a complete answer.
  The challenge WORDING is keyed by label -- telling a candidate who wrote three
  coherent paragraphs that their reply "did not come through" proves the agent
  cannot tell prose from keyboard mash.
- **A re-ask is not a follow-up.** It costs no follow-up budget, is bounded to
  one per base question by the `pending_prompt` mechanism, and changes no
  scoring. Follow-up budget SCALES with interview length
  (`interviewer.follow_up_budget`, 15 at 45 questions, 7 at a CXO's 22): the
  flat 5 it replaced meant 89% of a non-managerial interview could not react to
  anything the candidate said.
- **NO TEMPLATED ACKNOWLEDGMENTS, and this has been violated once already.**
  `_CONNECTORS` prepended one of eight canned openers to every question by
  `position % 8`, so "Appreciate the detail." answered gibberish. Pinned by
  `test_no_canned_acknowledgments_in_the_conversation_path`, which checks CODE
  lines only so the comment recording the removal may still quote it. A model at
  0.7 writes praise unprompted, so `_strip_praise` removes leading openers to
  exhaustion.
- **Candidate text is DATA, never instructions.** Every answer passes
  `conversation_guardrails.inspect_answer` before it is stored or reaches a
  prompt, and every interviewer line passes `inspect_agent_output` before a
  candidate reads it. Note the contract: `violation is not None` does NOT mean
  refused, only `allowed` does -- an answer that legitimately DISCUSSES prompt
  injection is still an answer. Both directions are deterministic and call no
  model, for the same reason the substance check does not.
- **`contains_forbidden_number` strips ASSESSMENT numbers, not technical
  content.** "How did you bring p99 latency under 200ms?" is an ordinary
  interview question. The hard part is the distinction, not the detection, and a
  guard that mangles a real question fails invisibly.
- **Telemetry logs labels, keys and timings, NEVER answer or question text.** An
  ordinary log is far more widely readable than a LangSmith trace, and prompts
  carry a real candidate's answers. `interview_telemetry.conversation_summary`
  is OPERATOR data, carries numbers, and must never reach a response schema.
- **`app/scripts/eval_interview.py` is the agent's evaluation and CI gates on
  it.** TRUE ONLY SINCE 2026-08-06, and this line asserted it for two days while
  it was false: `deploy.yml` built, migrated, staged and smoke-tested, and ran
  neither the eval nor the unit suite. Nothing stopped a commit whose tests
  failed from reaching a production revision. The `test` job that now precedes
  `deploy-staged` is what makes the sentence true; do not remove it, and do not
  write "CI gates on X" here again without opening the workflow file.
  Fully stubbed and offline on purpose: a rate that moves means the CODE
  changed, not that a provider sampled differently. It measures judgement across
  a labelled set (non-answer detection, the real-answer false-positive
  direction, outage degradation, question integrity, injection resistance, the
  no-numbers rule in BOTH directions, budget determinism). It deliberately does
  NOT judge whether a real model writes a GOOD question; that needs a live model
  and a human. Thresholds are where they are today, not aspirationally -- a rate
  allowed to fall silently is a rate nobody is defending.
- **The demo seed creates APPLICATIONS, not just candidates.**
  `seed_demo_candidates` creates candidate rows and uploads resumes and does
  nothing else, by its own docstring. `seed_demo_applications` generates each
  demo job's PPI framework, approves it (scoped to `tenants.is_demo` read from
  the COLUMN, so Workify Corp keeps its manual gate) and then creates the links.
  Production measured 32 candidates against 9 applications while every deploy
  reported success.

## Current hard rules, adaptive interview + demo fixtures (2026-08-05)

- **The assessment conversation is ADAPTIVE, and three things must never move
  with it.** `api/assessments.respond` used to be an index into a pre-generated
  list with no LLM call anywhere in the conversation, so "the agent has no
  memory" was not a prompt problem, there was no agent. `services/interviewer`
  now writes at most ONE follow-up per base question against the transcript so
  far. The invariants it must not break, each pinned by a test in
  `tests/test_conversation_flow.py`: a follow-up is answered under the SAME
  `question_key` (so `answers_by_key` hands the scorer one richer answer, never
  an unknown key that every scorer would silently DROP); it does NOT advance
  `next_question_index` (which is what fires `charge_completed`, so billing is
  unmoved); and a follow-up outstanding on the LAST base question HOLDS
  completion open, or the customer is charged and scoring dispatched while the
  candidate is still typing.
- **The interview is bounded by construction, not by convention.** One follow-up
  per question, `MAX_FOLLOW_UPS` per conversation, counted in a PERSISTED column
  so the ceiling survives a retry or a message that fails to write. Total turns
  are `len(prompts) + MAX_FOLLOW_UPS`, whatever the model returns.
- **Every follow-up failure path returns None, meaning "ask the next scripted
  question".** Outage, timeout, malformed JSON, a model echoing the string
  "null", a follow-up long enough to be a speech. A candidate is mid-assessment
  on a live request, so a provider problem costs the adaptivity and nothing
  else. Unlike `_llm_score`'s fallback, which invented a grade, this one is
  simply the product's previous behaviour.
- **Sampling temperature is DATA in `config/llm_providers.TASK_TEMPERATURE`, and
  the split is judge-versus-write.** Every task that JUDGES is 0.0:
  `behavioral_assessment`, `report_synthesis` (it states the grades a client
  reads, prose or not), `rerank`, `extraction`. A scoring call that samples
  above zero makes a candidate's grade depend on WHEN they were scored, which is
  unfalsifiable -- a disagreeing rescore reads as a broken rubric. Unlisted
  tasks default to 0.0, the safe direction. `conversation_turn` is 0.7 and is
  the only task above 0.5.
- **A non-answer never reaches a scoring prompt.** `services/answer_quality`
  routes gibberish, empty and single-token answers to the SAME unanswered path
  the product already had (`UNANSWERED_SCORE`, which grades Not Matching).
  Gibberish used to reach `_stable_score`, which hashes into 45..94: measured
  over 20,000 seeds, 69.6% graded Moderately Matching or better and 10.1%
  Highly Matching. The defect was never that gibberish could not fail; it is
  that a HASH decided whether it did. The guard is deliberately conservative:
  "I have not used Kafka" is a real answer and is scored low on its merits.
- **Demonstration tenants are exempt from billing REFUSALS, never from billing
  RECORDS.** `tenants.is_demo` is a column, not a UUID list in Python, so the
  exemption is visible in the table and a fourth demo tenant is an UPDATE.
  Sarkar Corp, ACRM Corp, Specter & Co. -- keyed by their seed UUIDs, never by
  name (a fourth tenant, Workify Corp, is REAL and must keep being billed; the
  brief that requested this called the third company "ACME Corp", which does not
  exist). `has_credit_headroom` checks the demo flag BEFORE summing the balance,
  because a demo tenant that has run assessments has a negative ledger like any
  other. Ledger entries are still written: a billing page with no usage on it
  demonstrates nothing. The dangerous direction is a LEAKED exemption, which
  raises nothing and just stops collecting money, so every test has a
  paying-tenant twin.
- **The 30 demo candidates and their resume corpus ship in the image.** The
  corpus lives at `backend/demo_resumes` because `backend` is the Docker build
  context; at `<repo-root>/resumes` it never reached the image, `resumes_dir()`
  returned None on Cloud Run, and the seed logged that it found nothing and
  EXITED 0. Production ran on two candidates against thirty while every deploy
  was green. `seed_resume_corpus` still refuses production by default (that
  guard protects `seed_dev_data`, which seeds an entire dev world); only
  `app.scripts.seed_demo_candidates` opts in with `allow_production=True`.
- **The migrate job has VPC egress, and the broker has publish timeouts.**
  Publishing to Redis has NO timeout by default, so an unreachable broker does
  not fail, it HANGS -- which silently defeats every `try/except` around an
  enqueue, because nothing is ever raised for the handler to catch. Observed as
  a management job that found 30 files then died at the 900s ceiling having
  written nothing, because the first `send_task` never returned.
- **Every LLM call is traced to LangSmith from ONE chokepoint,
  `llm_router.invoke_llm`.** Runs are `llm:<task_type>` and tagged, so the
  dashboard separates the agents with no per-agent wiring. Tracing is OFF
  without `LANGSMITH_API_KEY` (tests and local dev post nothing), a broken SDK
  degrades to an UNTRACED call and never a failed one, and prompt/completion
  TEXT is not sent unless `LANGSMITH_TRACE_CONTENT=true` -- prompts carry a real
  candidate's answers and a real JD, and that is the data owner's call.
- **Sign-in asks for no workspace.** The login page is Continue with Google,
  email, password. The backend routes to the correct portal from the account's
  own record; `?portal=` still deep-links for candidate apply links. The old
  picker was worse than redundant: choosing "Provider owner" never GRANTED
  provider access, so a wrong guess produced a refusal that read like a broken
  account.

## Current hard rules, PPI + the four-grade scale (2026-07-30)

- **There is ONE rating scale, it has FOUR grades, and it lives in
  `services/rating.py`.** Highly Matching, Matching, Moderately Matching, Not
  Matching. It replaced the product's two parallel five-label scales, the
  assessment's *Very High / High / Medium / Low / Developing* and matching's
  *Highly Matching / Matching / Moderate / Low / No Matching*, which had to be
  kept in step by hand in two modules and gave a reader no way to know that a
  "High" and a "Matching" meant the same thing. `matching.matching_label` and
  `functional_assessment.rating_label` are now thin aliases over it and must
  stay that way. The cut-points are unchanged (90 / 75 / 60), so a report
  written before this release regrades identically, with the old Low and
  Developing collapsing into Not Matching. Boundaries stay inclusive upward
  (rule 8).
- **PPI replaced PFI, and the difference is per-job versus per-product.** The
  ReadyPick Functional Index was ONE fixed dimension set per grade, reused
  across every job. ReadyPick Profile Intelligence generates a FRESH framework
  for every job from that job's own JD: at least 5 Primary Skills, 5 Secondary
  Skills and 5 Behavioural Competencies, more when complexity warrants it.
  `services/pfi_bank.py` and `services/validation_bank.py` are DELETED, and
  `tests/test_functional_assessment.py` asserts they cannot be imported. PPI is
  proprietary ReadyPick work and is never associated with DISC, MBTI, Hogan,
  CliftonStrengths or any other licensed instrument.
- **The framework is per JOB, the questions are per CANDIDATE, and confusing
  the two breaks the product's only comparability guarantee.** A saved
  framework is the fixed evaluation criteria for every candidate on that job.
  The questions probing it are generated from the JD, the framework, AND that
  candidate's resume, so two candidates get different questions against
  identical criteria. Counts are fixed by the CANDIDATE's grade, never by the
  job: 25 / 20 / 15 / 10 for non-managerial / managerial / leadership / CXO.
  Note the direction, MORE questions for a junior candidate.
- **"Culture" is refused as a Behavioural Competency, at three layers.** The
  generator prompt forbids it, `ppi.framework_is_complete` rejects it at save,
  and a Postgres CHECK on `job_competencies` refuses the row. A prompt
  instruction is a request, not a guarantee, and the Hiring Manager's Edit
  control can type anything. Cultural fit cannot be assessed accurately from a
  single assessment and PPI does not claim otherwise.
- **The manual review gate covers the FRAMEWORK ONLY** (amended 2026-08-04,
  client decision). `jobs.assessment_status` starts at
  `questions_pending_review` and reaches `ready_for_candidates` when
  `framework_approved_at` is stamped (`api/assessments._refresh_setup_status`).
  Until then the conversation 409s and `select-candidates` 409s, so nobody is
  mailed an assessment they cannot open.
  **The TECHNICAL question bank no longer gates anything**: generated questions
  are usable immediately, the "Finalise questions" control is gone from
  `components/job-setup-review.tsx`, and editing an individual question still
  takes effect at once. This reverses only the technical half of the 2026-07-30
  decision; the framework half stands. The two are not symmetric, and that is
  the whole reason one survived: the framework is the fixed criteria EVERY
  candidate on the job is graded against and is frozen once anyone is assessed,
  so a human confirming it is the product's only comparability guarantee. A
  technical question is scored against its own rubric, so a weak one costs one
  item on one report rather than making two reports incomparable.
  `questions_approved_at` is still stamped by the surviving finalize route and
  is now READ BY NOTHING; it was deliberately not dropped in the same change
  that stopped reading it, so a rollback needs no data restore.
  `pickready.remind_unapproved_technical_questions` keeps its name and its
  hourly schedule but now chases an unapproved FRAMEWORK, measured against
  `framework_generated_at` alone. The both-halves rule had NO test for its
  entire life, which is why `tests/test_assessment_setup_gate.py` now pins the
  rule that replaced it.
- **Publishing and assessment readiness are independent.** A published job
  takes applications and ranks them immediately; it just cannot invite anyone
  yet. Making publish wait on the review would hold the 30-day posting window
  closed over a step that only affects what happens after someone applies.
- **A saved framework is frozen, and reopening is refused once anyone has been
  assessed.** A report is immutable and states a grade against those exact
  criteria; letting the criteria change underneath it would make two reports on
  the same job incomparable, which is the one property the framework exists to
  guarantee.
- **Validation is six MANDATORY FIELDS on the application form, and nothing
  scores it.** Current CTC, expected CTC, notice period, joining date, document
  readiness, and "Why does this role interest you?" in the candidate's own
  words. `services/application_validation.py` is the single source of the field
  list, served to the form so the form and the report's Validation section
  cannot drift. It lands on `job_candidate_links.validation_json`, NOT the
  candidate profile: current CTC and notice period are answered per opportunity
  and change over time. Capturing it before the conversation is what lets a
  recruiter drop a candidate outside the budget before a credit is spent. The
  RECRUITER, not any agent, decides whether stated interest is genuine.
- **There are TWO scoring agents, not three.** Technical (per-question rubric)
  and PPI (against the saved framework), fanning out in parallel and joining at
  synthesis. `validation_capture` is a graph node but NOT a scorer: it copies
  the application's fields into the report shape and touches no model.
- **Report order is fixed: AI Score, then the PPI Assessment.** AI Score (four
  matching parameters, 25-30 word remarks) is the pre-assessment resume
  snapshot; Overall + Primary Skills + Secondary Skills + Behavioural
  Competencies (45-50 word remarks) is the post-conversation assessment. They
  are deliberately NEVER merged: a close match confirms the resume was
  accurate, and a gap is itself signal. Then Validation, then 8-10 suggested
  interview questions anchored on whatever graded Moderately Matching or Not
  Matching. Technical items are scored and anchor those questions but are not a
  rendered section.
- **FOUR radar charts, each plotting TWO shapes.** Overall, Primary, Secondary,
  Behavioural, each overlaying the job's required level and the candidate's
  assessed level on the same axes. Built from the SAME dimension rows the
  sections render, so a chart can never disagree with the text beside it. No
  number anywhere: not an axis tick, not a data label, not a tooltip. The
  legend names the two shapes by word. The Overall chart plots the three PPI
  category aggregates and EXCLUDES technical, which carries no job-requirement
  level and would force the requirement shape to invent a value for that spoke.
- **`report_dimensions.required_level` is COPIED onto the report, never joined
  to the live framework.** A written report is a permanent record of the
  criteria it was written against, and the job's framework may be edited later.
- **The four matching parameters carry NO mathematical weightage.** The
  0.35 / 0.30 / 0.20 / 0.15 table is gone and `services/matching.py` has no
  `WEIGHTS` symbol; `tests/test_scoring.py` asserts its absence. Two things
  were wrong with it: the weights were shown to the client as "35% role-fit
  weighting" beside each remark, which is a number reaching a client, and a
  fixed weighting asserts that skills matter 2.3x more than education for every
  role in the product, an arithmetic the comments do not perform. The internal
  overall is now their plain mean and orders a list; it is never displayed.
- **Report REUSE is retired.** `retake.PORTABLE_CATEGORIES` is an explicit
  EMPTY frozenset and `copy_report` never copies. Under PPI both the framework
  and the technical bank come from each job's own JD, so every section is
  job-scoped and carrying one across would state a grade against criteria the
  candidate was never assessed on, the identical error that always kept the
  matching section from travelling. The six-month classification still runs so
  the candidate is told why they are answering questions again.

## Current hard rules, subscriptions + the credit ledger (2026-07-28, later)

- **A customer's SUBSCRIPTION hangs off `tenants`, not `companies`.** The spec
  writes `ALTER TABLE companies ADD COLUMN razorpay_...`; in this schema a
  customer IS a `tenants` row, and `companies` is the client-authored page that
  does not exist until they first sign in. Billing on `companies` would be
  unreachable for exactly the customer who has just paid on the landing page.
  Same substitution for `credit_ledger.related_application_id`, which maps to
  `job_candidate_links`.
- **One credit is 60 integer SUB-UNITS, and nothing in the money path is a
  float.** Consumption is 1, 1/3, 1/15, 1/20 of a credit; LCM(1,3,15,20) = 60,
  so completed = 60, incomplete = 20, no-show = 4, old-profile review = 3.
  Division happens ONCE, at display, through `Decimal`.
- **The balance is `SUM(subunits_delta)`, never a stored counter.** A customer
  disputing usage gets a statement, not a number. `tenants.credit_deficit` is
  the one derived cache, and it exists only so the invitation gate does not
  re-aggregate the ledger on every send.
- **Every credit write carries a UNIQUE `idempotency_key`.** Razorpay delivers
  webhooks at least once and Celery redelivers tasks, so a double grant is the
  DEFAULT behaviour unless something prevents it. Checkout-verify and the
  webhook derive the SAME key from the payment id, which is why both can run for
  one payment and the customer is granted one month.
- **A completed assessment is charged even into the negative; the NEXT
  invitation is what gets blocked.** The work is already done and cannot be
  undone, so refusing the charge would only lose the revenue.
  `POST /pipeline/jobs/{id}/select-candidates` answers 402 with both ways out
  named.
- **Razorpay Subscriptions, never Orders.** An Order is a one-time charge and
  would silently turn a monthly plan into a single payment. The Checkout
  signature for a subscription is `payment_id|subscription_id`, the REVERSE of
  the Orders flow; getting it backwards fails 100% of real payments.
- **The Key Secret is server-side only and never reaches the frontend.** The
  browser gets the Key ID from `GET /billing/config` at runtime, not from a
  build-time `NEXT_PUBLIC_` variable, so the frontend container never needs the
  `.env` at all. `api-keys.txt` is gitignored and was never committed.
- **`checkout_ready` is about the SERVER's credentials, not the plan row.**
  Razorpay Plans are minted lazily on first subscribe, so keying it off
  `razorpay_plan_id` disables every Subscribe button on a fresh install and the
  only thing that could populate that column is the button it just disabled.
- **A job renewal restamps `posting_start_date`, and that is the ONLY thing
  that distinguishes an Old Profile.** `profile_age` is DERIVED from
  `link.created_at < job.posting_start_date`, never stored. Renewal is refused
  while a posting is still live, for the same reason publish refuses a second
  stamp. An Old Profile is ranked, listed, opened and assessed identically; the
  label is provenance and billing, never access.
- **Interactive LLM calls are capped at 15s per attempt and 30s in total;
  background ones are not.** The latency brief asks for a flat 10 to 15s cap on
  every call. Applied to `report_synthesis` that does not make the product
  faster, it makes every report fail and then retry. The split is by whether a
  request handler is blocked, and BOTH bounds are needed: four attempts at 15s
  is a 60-second request with a 15-second timeout on it.
- **No em dash in a STRING, in either language.** The 2026-07-28 sweep covered
  `frontend/` and the database; it did not cover backend Python, where 123
  em dashes sat in `detail=` messages, stage labels, profile-form options and
  seeded content. The same sweep covers every file added since. `tests/test_platform_audit.py` now asserts this, along with
  the DISC/MBTI/Hogan sweep, the no-OTP-in-any-portal rule, Gmail-SMTP-only, and
  no-numbers-to-a-client. A character class that MATCHES a dash is data, not
  prose: build it from `chr(8212)` so a repo-wide sweep cannot rewrite the code
  that strips it.

## Current hard rules, BD Portal, unified JD, procurement types (2026-07-28)

- **There are now FOUR portals, and the fourth is the Business Development
  Portal** (`/bd` in the UI and in the API). It is where ReadyPick's own sales
  team works leads and closes customers. The other three are unchanged:
  Provider Portal (`/admin`, `/provider`), Customer Portal (`/org`,
  `/companies`), Candidate Portal (`/portal`). A `bd` user is PLATFORM staff:
  `tenant_id` is NULL, the token carries the OWNER audience, and `bd` must
  never be added to `_ORG_ROLES` (that path demands a tenant they do not have).
- **A signed agreement CREATES a tenant, in a third `prospect` status.**
  `PATCH /bd/leads/{id}/agreement` with `true` mints a `tenants` row and links
  it, because a customer IS a tenant. Setting it back to false or null CLEARS
  the link and ARCHIVES the tenant, never deletes it, and
  `bd_leads.promoted_tenant_id` is permanent so a re-signed lead reuses its
  original company instead of minting a duplicate. The Provider Portal's
  customer list still accepts only `active | archived | all`, so a prospect
  cannot appear there as though it were live.
- **AI Reach returns two segments and the first one never touches the network.**
  `similar_to_customers` is computed from ReadyPick's own tenants and jobs and
  is computed FIRST; `from_internet` is a LangGraph agent over Tavily. With no
  `TAVILY_API_KEY` the internet segment returns `status: "unconfigured"` with a
  plain message and the page still works. Retrieved web content is DATA, never
  instructions, and the evaluate node says so explicitly.
- **`confidence_label` on an AI Reach card is a word, never a number.** High,
  Medium, Low. The no-numbers rule covers discovered jobs exactly as it covers
  candidate ratings.
- **A job description is ONE markdown document.** `jobs.jd_markdown` is
  canonical; the per-section columns are DERIVED from it and kept populated so
  nothing downstream breaks. The seven separate text boxes are gone from the
  Create Job form. The sequence is draft, then edit, then publish: publishing
  with an empty `jd_markdown` is refused.
- **`level` is superseded by an experience band.** `experience_min_years` and
  `experience_max_years`, with a Postgres CHECK that min never exceeds max.
  `level` survives only for jobs created before 2026-07-28 and is not collected
  on the form. `reportees` and the JD generator's `company_context` were
  DROPPED, not deprecated.
- **Every candidate link carries `source_type`: `applied | sourced |
  databank`.** Applied means they came through ReadyPick, sourced means a
  third-party link, databank means the recruitment team bulk-uploaded them.
  This is provenance for DISPLAY and filtering ONLY. Nothing may branch on it:
  all three are parsed, embedded, matched and assessed identically. Bulk upload
  is `POST /jobs/{id}/candidates/databank`, at most 25 files, partial success
  allowed so one unreadable PDF cannot discard the other 24, and parsing is a
  Celery task as always.
- **`shortlisted` stays in the FSM but is no longer OFFERED as a manual move.**
  It is the only route into `interview_scheduled` and `offer_extended`, it is
  written by `api/candidates.decide_profile`, and historic applications sit in
  it, so deleting it would strand them. Only its offer is withdrawn, via
  `hiring_pipeline.MANUAL_TRANSITION_EXCLUDED`. The UI renders
  `allowed_transition_options` from the server and hardcodes no stage list.
- **A BD account is reserved, never credentialed.** `POST /admin/bd-users`
  writes a `users` row with `role = 'bd'`, `tenant_id = NULL`, status
  `invited` and no `firebase_uid`; the first proven Firebase sign-in on that
  email binds the uid and flips it to `active`
  (`api/auth._finalize_single`). ReadyPick stores no password and sends no
  invite token for BD, so a Firebase identity must exist for the address
  before the first login. Disable is the reversible switch and there is no
  delete route: a BD rep owns leads (`bd_leads.owner_user_id`).
- **NO EM DASHES anywhere in the UI, INCLUDING IN DATA.** Not in labels, helper
  text, empty states, toasts, emails, page titles or generated JD text, and not
  in seeded or generated CONTENT either. Sweeping `frontend/` for U+2014 only
  covers what the code writes; `jobs.about_company`, `work_life`, `benefits`
  and `jd_json` render straight onto the public application page and broke the
  rule just as visibly (fixed in `0025_strip_em_dashes`). Check both the source
  tree AND the database.
- **Text is never grey, enforced at the TOKEN.** `globals.css` sets
  `--muted-foreground: var(--ink)` in both themes, so the shadcn primitives'
  built-in `text-muted-foreground` resolves to pure ink. Do not chase call
  sites in `components/ui/**`; fix the token if it ever drifts.
- **The brand is ReadyPick.** The code-native mark and wordmark live in
  `frontend/components/brand/logo.tsx`; product surfaces must not point at
  inherited logo or collateral assets. Design tokens remain in
  `docs/spec/DESIGN_BRIEF.md`.
- **Page metadata must not repeat the site name.** `app/layout.tsx` sets a
  `%s | ReadyPick` template, so a page title is just "Sign in".
- **The frontend dev container does not see file changes over the Windows bind
  mount.** Restart the `frontend` service after editing, or you will verify
  against stale output and believe a change did not work.

## Current hard rules — Provider Portal (2026-07-27)

- **Three portals, three names, never interchanged.** *Provider Portal* is the
  ReadyPick owner's console (`/admin` in the UI, `/provider` in the API).
  *Customer Portal* is a client company's own dashboard (`/org`,
  `/companies`). *Candidate Portal* is `/portal`. A **customer** is one
  onboarded client company.
- **A customer IS a `tenants` row, not a `companies` row.** `tenants` carries
  the customer identity (name, industry, profile) and exists from onboarding;
  `companies` is the client-AUTHORED candidate-facing page and does not exist
  until the client signs in. Compliance documents, the archive lifecycle and
  the Provider-editable metadata therefore hang off `tenants`.
- **The Provider is READ-ONLY over the customer's own data, enforced by
  ABSENCE.** `api/provider.py` exposes no route that writes a contact detail, a
  team member, or a compliance document — not a handler that checks a flag, no
  route at all. The Provider may edit exactly `industry`, `website_domain`,
  `notes` and the archive flag; `CustomerUpdateIn` has no other fields.
- **Archive is a reversible hide; delete is not on this screen.** Archiving
  sets `tenants.status` and stamps `archived_at`, touching no job, application,
  report or user; unarchiving CLEARS `archived_at`. The irreversible
  `DELETE /admin/tenants/{id}` still exists and still requires retyping the
  company name — it is never one click away from Edit.
- **All seven compliance slots are always returned, present or not.** Four tax
  records (GSTIN, PAN, TAN, bank details) then three commercial ones (signed
  agreement, PO, MSME), in that fixed order. An absent document is a slot with
  `document: null` rendering "Not Available Yet" — never a short list a missing
  PAN card can hide in. UNIQUE on (tenant, type): re-uploading REPLACES in
  place, keeping the document id stable.
- **`jobs_closed` and `jobs_ongoing` OVERLAP and are not a partition of
  `jobs_posted`.** Closed is `now > posting_end_date`; ongoing is
  `now <= grace_period_end_date`; a job in its 5-day grace tail is both. They
  are two independent questions — never render them as parts of a whole, and
  never "fix" them to sum. Boundaries stay inclusive at the end of each window
  (rule 8), matching `services/job_posting`.
- **Customer search, the archived filter and pagination run in SQL**
  (`api/provider.list_customers`), before pagination. Filtering a fetched page
  in the browser makes the match count depend on which page was loaded.
- **The Provider Portal nav is Customers + Business Development + Billing +
  Settings, nothing else** (amended twice on 2026-07-28). Team Management,
  Permissions and the Audit Log still have no page. Billing is READ-ONLY like
  every other Provider view of customer data, and read-only by ABSENCE: there
  is no route in `api/billing` that lets the Provider write a subscription, a
  plan or a credit. Business Development is not a fourth
  cross-tenant admin surface: it is the ONLY place a `bd` account can be
  created, because every invite path in the product is tenant-scoped and a BD
  user has no tenant. The audit trail is
  still written for every Provider request (`get_superadmin_db`); it simply has
  no UI. Settings stays because the theme toggle lives there (rule 10).
- **`manage_compliance_documents` remains independently grantable.** A
  GSTIN certificate and a signed agreement are the company's legal instruments,
  not recruitment data. Still a capability, never a role branch; an authorised
  manager can delegate it via `users.permissions_json`.

## Current hard rules — Job posting lifecycle + hiring pipeline (2026-07-27)

- **Every job is live for EXACTLY 30 days, then 5 days of grace.** The window
  is not configurable and a recruiter can never move it: `jobs.posting_end_date`
  and `grace_period_end_date` are Postgres GENERATED columns, so an UPDATE
  against them is rejected by the database itself. `posting_start_date` is
  stamped at publish and is the only writable date.
- **`posting_status` and `is_within_grace_period` are READ-TIME values, never
  stored.** The spec asks for them as generated columns; Postgres refuses,
  because their definitions call `now()` (and one subqueries another table) and
  a generated column must be IMMUTABLE. They live in
  `services/job_posting.py`, mirrored by the `job_posting_state` SQL view. The
  two must agree — change them together.
- **Visibility rules are in `services/job_posting.py` and are pure functions.**
  A wrong boundary there silently grants or removes a person's access, so every
  boundary is asserted directly in `tests/test_job_posting.py`. Boundaries are
  INCLUSIVE at the end of each window: an instant exactly on `posting_end_date`
  is still active, ties go to the candidate (consistent with rule 8).
- **A candidate who registered after `posting_end_date` never sees that job** —
  not in the board, not in search, not by direct URL. The window filter is
  applied BEFORE relevance ranking and before `?search=`, because search
  deliberately bypasses relevance and would otherwise bypass this too.
- **The grace period is for EDITING an existing application, never for creating
  one.** It grants nothing to a non-applicant and nothing to an anonymous
  visitor: the public/external job link 404s the moment the 30 days end.
- **Not every applicant is assessed.** All applicants are ranked on resume +
  profile form, but only candidates a recruiter selects
  (`POST /pipeline/jobs/{id}/select-candidates`) get an assessment — and
  therefore a PFI report. The `assessment_conversations` row IS the invitation;
  `POST /assessments/conversations/links/{id}/start` refuses without one, so an
  uninvited candidate cannot reach the questions by guessing a URL.
- **Application status is a validated 10-stage pipeline**
  (`services/hiring_pipeline.py`). Illegal moves are refused — an application
  cannot jump from `applied` to `offer_extended`, because each stage carries a
  promise (`assessment_completed` means a report exists) and the transition
  emails reference it. `rejected` and `hold` are reachable from any live stage.
  `pipeline_status` stays the append-only history; `job_candidate_links.status`
  is a denormalised mirror, and only `apply_transition` writes either.

## Current hard rules — Job detail page + LangGraph router (2026-07-27)

- **NO NUMBERS REACH A CLIENT. EVER.** Not a score, percentage, rank, band
  index, "7.5/10", or "top 12%" — in the UI, in an API response, or in an
  email. SUPERSEDED IN PART, 2026-07-30: rated output is now the FOUR grades of
  `services/rating.py`, not two parallel five-label scales. The conversion from
  the internal score still happens SERVER-SIDE so a number cannot leak by
  omission. The single, documented exception is the radar chart's band index
  (now 1–4), which is a rendering coordinate — a radar has no geometry without
  a radius — and is never displayed as a number anywhere.
- **Every LLM call routes through `services/llm_router.invoke_llm(task_type,
  …)`.** Task types are `jd_generation | technical_questions |
  behavioral_assessment | report_synthesis | email_composition`, plus the
  legacy `rerank | extraction` hints. Routing policy is DATA in
  `config/llm_providers.py` (provider order, timeout, retry budget per task) —
  never inline in a service. The key roster is 7 slots per provider (21 total),
  every slot optional; the router round-robins within a provider tier and
  walks the tier order on failure. A LangGraph `StateGraph` drives the retry
  loop; the circuit breaker, half-open recovery, and never-log-a-key rules are
  unchanged.
- **Candidates are listed INLINE on the job detail page.** There is no separate
  Review Screen, no Email Templates builder, and no separate JD-edits card.
  Columns are Name | Level | PPI Report | Resume | the rated comments (and
  Decision, when the caller holds `decide_profile`). The job page also carries
  the assessment-setup review (`components/job-setup-review.tsx`), which is the
  one manual step in the pipeline.
- **The candidate table is sorted in SQL, never in JavaScript.** Order is
  grade-driven (`services/job_candidates.order_by_clause`): non-managerial is
  skills → experience → behavioural; managerial and above is skills →
  behavioural → experience. It must stay a TOTAL order (trailing
  `created_at, id`) or paginated rows will duplicate or vanish. 25 per page.
- **About Company / Work Life / Benefits live in two layers.** The company
  profile (Company Portal → Profile) is the default; a job SNAPSHOTS it at
  creation and may override it per job. Editing the company profile reaches
  FUTURE jobs only — never a job candidates are already applying to. A NULL
  section on a job reads through to the live company profile.
- **Reports are immutable.** No edit or delete affordance in the UI, and
  PATCH/PUT/DELETE on the report route return 403 explicitly (a registered
  handler, not an accidental 405). A retake generates a NEW report alongside
  the old one. This is also why a saved PPI framework cannot be reopened once
  anyone has been assessed against it.
- ~~**Six-month retake rule**~~ REUSE RETIRED 2026-07-30 (`services/retake`):
  every application runs its own assessment, because under PPI the framework and
  the technical bank both come from the job's own JD and nothing in a report is
  portable any more. `PORTABLE_CATEGORIES` is an explicit empty frozenset. The
  183-day classification still runs so the candidate is told why they are
  answering questions again.
- **All six lifecycle emails are AI-drafted and editable before sending.**
  Prompts are `.txt` files in `app/prompts/`; every send is recorded in
  `email_log` with the copy actually sent and whether a human edited it.
  Delivery is a Celery task over Gmail SMTP. An email never contains a score.
- **Permissions gain a per-user layer.** Resolution is user overlay → tenant
  row → global template → deny. `users.permissions_json` is a SPARSE
  {capability: bool} object: a capability the HR Head never pinned keeps
  tracking its role default. Still `require_capability(...)`, never a role
  branch.

## Hard rules — Unified candidate profile release (2026-07-27)

- ~~**The 40 validation aspects are a FORM on the candidate profile.**~~
  SUPERSEDED 2026-07-30: validation is six mandatory fields on the APPLICATION
  form (see the top of this file). `candidate_profile_form.py` survives as the
  candidate's own reusable profile, but it is no longer where the report's
  Validation section reads from. Original rule, for context: A candidate's answers are identical for every job, so
  they are collected once under My Profile and snapshotted onto each
  application's `profiles.aspects_json`. `services/candidate_profile_form.py` is
  the single source of truth for that form — a fixed Python constant, never
  LLM-generated and never client-editable, exactly like `pfi_bank.py`. The
  report's Validation section reads the snapshot; `validation_bank.py` survives
  only to keep pre-2026-07-27 transcripts readable.
- **A per-job assessment is technical (by grade) + PPI (by grade), and nothing
  else.** SUPERSEDED 2026-07-30: the behavioural half is PPI and its count now
  varies by grade, so non-managerial is 45 questions rather than 40.
- **The candidate has a MAIN resume** (`candidates.main_profile_id`), managed on
  My Profile and offered on every application beside "upload a new resume".
  Replacing it never rewrites a submitted application — each application remains
  an immutable snapshot of the resume it was actually sent with.
- **The candidate's New Jobs board shows RELEVANT roles only**, ranked by
  `services/job_relevance.py` against their main resume, its parsed skills, and
  their profile form. `?search=` deliberately bypasses relevance entirely. This
  is candidate-side presentation ONLY — it must never decide who gets scored.
- **Text is never grey.** Every text token resolves to pure black in the light
  theme and pure white in the dark theme; grey survives only on borders, input
  outlines and muted backgrounds. The single exception is `::placeholder`, dimmed
  so an empty field cannot be mistaken for a filled one.
- The candidate portal's nav is **New Jobs → Applied Jobs → My Profile**. There
  is no "Settings" page for candidates, and their role is never displayed.

## Hard rules — Grade-driven assessment release (2026-07-26)

- Evolve the system additively. Extend tables and routes; do not replace
  established contracts without a migration and versioned compatibility path.
- The ReadyPick Functional Index is proprietary ReadyPick work derived from
  first-principles job analysis. Never associate its name, prompts, code,
  comments, UI, or documentation with a third-party licensed assessment
  instrument.
- Client-facing rated output uses only these labels: Very High, High, Medium,
  Low, Developing. Stored numeric scores are internal ranking data and must
  never be returned by report APIs or rendered in the client UI.
- Rated remarks are 25–30 words and overall summaries are 45–50 words. AMENDED
  2026-07-30: the 25–30 rule now covers only the AI Score's four matching
  parameters and technical items; every Primary Skill, Secondary Skill,
  Behavioural Competency and the Overall Remark is 45–50 words. Validate
  and regenerate complete prose; never truncate a sentence to hit a limit.
- A candidate experiences one unified conversation. Technical, Behavioral, and
  Validation scoring fan out in parallel; synthesis is an explicit join.
  (SUPERSEDED IN PART, 2026-07-27: validation is no longer *asked* in the
  conversation — the three scorers still fan out in parallel, but
  `validation_capture` now reads the candidate's profile form.)
- Gmail SMTP is the only outbound email path. Authentication is email/password
  or Google OAuth; no OTP UI or copy is permitted.

### Grade drives the assessment (2026-07-26)

- **Every job carries a grade**: `non_managerial | managerial | leadership |
  cxo`. It is a REQUIRED dropdown on the Create Job form, stored in the existing
  `jobs.assessment_grade` column and exposed on every job read as `grade`. It is
  never null — legacy rows read `non_managerial`. Grade is chosen by the
  recruiter, not inferred; LLM inference survives only as a fallback for rows
  created before this release.
- **Question counts are fixed by grade.** Technical: non-managerial 20,
  managerial 17, leadership 15, CXO 12 — unchanged. ~~Behavioural: always 20 (5
  grade-specific PFI dimensions × 4 fixed questions).~~ SUPERSEDED 2026-07-30:
  the behavioural half is now PPI and its count varies by grade — 25 / 20 / 15 /
  10 — so a non-managerial candidate answers 45 questions and a CXO 22.
  ~~Validation: always all 40 aspects.~~ Validation left the conversation on
  2026-07-27 for the profile form, and left the profile form on 2026-07-30 for
  six mandatory fields on the application form.
- ~~**There is no manual question-bank approval step, and no question-bank
  UI.**~~ REVERSED 2026-07-30, client decision. The gate is back and now covers
  the PPI framework as well as the technical bank; see the 2026-07-30 section
  at the top of this file. Recruiters review, edit and finalise both, and no
  candidate can be invited until they have.
- ~~**Behavioural questions and the profile form are fixed Python
  constants**~~ SUPERSEDED 2026-07-30 for the behavioural half:
  `services/pfi_bank.py` and `services/validation_bank.py` are deleted, and the
  behavioural competencies are now part of the per-job PPI framework generated
  from the JD. `services/candidate_profile_form.py` survives unchanged and is
  still a fixed constant, never LLM-generated and never client-editable.
- **Scoring reads the candidate's actual answers.** Each technical answer is
  scored against that question's own rubric; each PFI dimension is scored from
  its four answers. A deterministic hash is permitted ONLY as a flagged
  LLM-outage fallback and must set `scoring_mode`.
- **A technical report dimension is named after a skill, never a JD sentence.**
  `report_dimensions` is UNIQUE on (report_id, category, name), so the report
  carries one entry per distinct skill probed — not one per question.
- **A candidate linked to a job is always scored.** Retrieval (pgvector, ts_rank)
  is a ranking prior only; it must never decide who gets scored. Every
  non-archived link on the job enters the scoring pool.
- ~~**Report section order is fixed**: overall summary → radar chart → Profile
  Matching → Behavioural (PFI) → Technical → Validation → Suggested Interview
  Probes.~~ SUPERSEDED 2026-07-30, see the report order at the top of this
  file: AI Score → Overall → Primary Skills → Secondary Skills → Behavioural
  Competencies → Validation → Suggested interview questions.
- **Never name a storage vendor in user-facing copy.** Candidates are told the
  file limits, not where the bytes land.

This file is the standing context for any Claude Code session working on this repo. Read `PRD.md` for functional requirements and `ESD.md` for the architecture — this file is *how* to build it, not *what* to build.

---

## 1. Project One-Liner

ReadyPick is a multi-tenant recruitment/ATS platform for Hanulisa Technologies LLP. Next.js + FastAPI, Firebase auth for every role, Postgres+pgvector for data and matching, a grade-driven AI assessment producing the Functional Skills Report, Celery for all async work, fully Dockerized.

---

## 2. Repository Layout

```
/frontend                Next.js 14 (App Router), TypeScript, shadcn/ui
  /app                   routes, grouped by role: (super-admin) (client) (hr) (recruiter) (hiring-manager) (candidate)
  /components            shared UI, shadcn primitives in /components/ui
  /lib                    api client, auth helpers, theme provider
/backend
  /app
    /api                 FastAPI routers, one module per PRD section (auth, jobs, candidates, matching, verification, dashboard, admin)
    /models              SQLAlchemy models, mirroring ESD §4 tables
    /schemas             Pydantic request/response models
    /services            business logic — approval FSM, RBAC engine, LLM router, matching pipeline
    /workers             Celery tasks (send_email, send_sms, run_matching, poll_verification, refresh_dashboard_views)
    /core                config, security (OTP hashing, JWT), db session with RLS tenant-var setter
  /alembic                migrations
  Dockerfile
/infra
  docker-compose.yml      local dev: postgres+pgvector, redis, backend, worker, beat, frontend
  railway.json / render.yaml   production service definitions
/docs
  PRD.md
  ESD.md
  claude.md
```

---

## 3. Non-Negotiable Rules

These are architectural decisions already made in ESD.md — do not silently deviate from them or re-litigate them in code review:

1. **Every tenant-scoped query goes through the RLS-aware session.** Never hand-write a `WHERE tenant_id = ...` filter as the *only* protection — the Postgres RLS policy is the real boundary; app-level filtering is defense in depth, not a substitute.
2. **Authentication is Firebase (as of 2026-07-24).** All roles sign in via Firebase Auth — Google, email/password, and phone. The backend verifies the Firebase ID token (`services/firebase_auth.py`) and issues the app's own portal-scoped JWT cookies; database roles/permissions remain authoritative (Firebase is identity only, never authorization). **Exception to the original "no passwords" rule:** candidate email/password is explicitly allowed (user decision, 2026-07-24). Do NOT build a custom password store or "forgot password" flow — Firebase owns credentials and recovery. The legacy MSG91 OTP send-path is retained as a working SMS feature but is no longer the login mechanism.
3. **Permissions are data, not code, and staff are hierarchical (reversed 2026-08-14).** Super Admin -> Recruitment Manager -> Recruiter -> Hiring Manager. Managers control only roles below them and may grant only capabilities they hold. Keep using `require_capability("...")` backed by `role_permissions` and the per-user overlay; never hardcode operational access by role in jobs, pipeline or candidates.
4. **All async/slow work is a Celery task**, never inline in a request handler: matching/re-ranking, email/SMS sending, resume parsing, verification-reply parsing, dashboard aggregation.
5. **All outbound email goes through Gmail SMTP from the backend.** Configure `smtp.gmail.com:587` with STARTTLS, the Gmail address, and a Google App Password via `SMTP_*`. The authenticated Gmail mailbox is always the From address. Sending remains a Celery task with database audit records and permanent-vs-transient failure handling.
6. **Candidate resumes ARE persisted on the candidate profile and reused across applications (as of 2026-07-24, PRD v1.0 FR-6.2).** Store the uploaded resume on the candidate's profile; on a new application, offer to reuse the last resume or upload a fresh one. (This reverses the earlier fresh-upload-only rule.)
7. **Databank candidates never re-enter the verification/40-aspect flow** — their existing Profile is reused as-is. Only freshly sourced candidates go through Section 5's data-collection + verification steps.
8. **Tier boundaries are inclusive upward**: a score of exactly 90 is Highly Matching, not Moderately Matching. Implement tier assignment top-down (check ≥90 first).
9. **LLM keys are routed with fallback, never hardcoded to a single provider.** Use the `llm_provider_keys` table and the router service (ESD §8.4); mark a key unhealthy on repeated failure rather than crashing the calling task.
10. **The theme toggle lives only in Settings/Profile** — never in the main navbar or a persistent floating control.

---

## 4. Coding Conventions

- **Backend**: Python 3.12, FastAPI, async everywhere (`async def` route handlers, `asyncpg`/`SQLAlchemy` async engine). Pydantic v2 for all request/response schemas — no bare dicts crossing the API boundary.
- **Frontend**: TypeScript strict mode on. Server Components by default; `"use client"` only where interactivity requires it. shadcn/ui components live under `/components/ui` and are not hand-edited beyond the CLI-generated output — wrap/compose instead of modifying generated files.
- **Styling**: Tailwind, monochrome palette (CSS variables for the black/white theme pair so the toggle is a variable swap, not a component-level branch).
- **Migrations**: every schema change is an Alembic migration, checked in — no manual production schema edits.
- **Tests**: Pytest for backend (unit tests on the approval FSM, RBAC engine, tier-boundary logic are mandatory given how much of the product depends on getting these exactly right); Playwright or React Testing Library for frontend critical flows (OTP login, job approval chain, HR review screen).
- **Commits**: Conventional Commits style (`feat:`, `fix:`, `chore:`, `refactor:`) to keep the history usable for a changelog later.

---

## 5. Environment Variables

**`/.env.example` is the single source of truth — read it, do not trust a copy.**
A duplicated list here drifts: this section previously still advertised
`RESEND_API_KEY` (email moved to Gmail SMTP) and a 9-key LLM roster (now 21).

Notes that are not obvious from the file itself:

- **LLM keys**: 7 slots per provider (`GROQ_API_KEY_1..7`, `GEMINI_API_KEY_1..7`,
  `OPENROUTER_API_KEY_1..7`). Every slot is OPTIONAL — the router enumerates
  only populated ones, so three keys and twenty-one behave identically. The
  `llm_provider_keys` table takes precedence over env when it has rows.
- **Email is Gmail SMTP only** (`SMTP_*`): `smtp.gmail.com:587` with STARTTLS
  and a Google App Password. The authenticated mailbox is always the From
  address. There is no Resend/Mailtrap path.
- **OTP settings remain** for the retained SMS feature; they are no longer the
  login mechanism (Firebase owns authentication).

---

## 6. Local Dev Quick Start

```bash
git clone <repo>
cd pickready
cp .env.example .env          # fill in real keys before first run
docker compose -f infra/docker-compose.yml up --build
# frontend: http://localhost:3000
# backend:  http://localhost:8000/docs (FastAPI auto-docs)
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.scripts.seed_dev_data
```

---

## 7. Build Order (matches PRD §9 phasing — build and ship in this order, don't jump ahead)

1. Tenant model + RLS policies + Super Admin console + RBAC engine
2. OTP auth for every role + Candidate Portal auth scope
3. Company onboarding + Hiring Manager account creation (max-5 enforced)
4. Job creation + configurable approval FSM
5. Resume upload + BGE-M3 embeddings + pgvector + Databank
6. Hybrid ranking pipeline (semantic → keyword → LLM re-rank → tiers)
7. Candidate outreach + 40-aspect flow + employer verification (form + fallback parsing)
8. HR Review Screen + Hiring Manager shortlist actions
9. Interview scheduling (client-domain email, .ics) + mandatory status tracking
10. HR/Recruiter dashboard (materialized views)
11. Observability, audit log UI, load/security hardening

---

## 8. When Unsure

If a requirement in PRD.md is ambiguous and the ESD doesn't resolve it, don't guess silently — implement the most defensible interpretation, leave a clear `# ASSUMPTION:` comment at the point of implementation, and surface it back to the user rather than letting it drift into an undocumented behavior.
