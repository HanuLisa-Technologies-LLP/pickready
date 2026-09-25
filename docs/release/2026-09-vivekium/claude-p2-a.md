# CLAUDE.md section draft, Phase 2 WP-A: Yukti core (2026-09-25)

Package `p2-a`. No migration (WP-B owns `phase2_yukti`). New modules:
`app/services/compensation_guard.py`, `app/services/yukti/{__init__,config,
anonymise,inputs,judge,grounding,validation_fit,scoring}.py`, prompt
`app/prompts/yukti_matching_system.txt` (version 1), task type
`yukti_matching` (Terra), lock namespace `locks.YUKTI_LINK`.

## Current hard rules, Yukti reads resumes against the saved skills (2026-09-25)

### SIX FIXED PARTS, AND NONE OF THEM IS VISIBLE TO A RECRUITER

`yukti/config.py` is the whole structure as DATA: Must-have evidenced (40),
Nice-to-have evidenced (15), experience level (15), role fit (15), company
need fit (5), validation fit (10). The weights sum to 100 and the module
REFUSES TO IMPORT when they do not, because a table that sums to 99 still
ranks, silently, in an order nobody designed. Nothing under `app/api` or
`app/schemas` may import it (`test_yukti_config.py` walks their imports).
The weights and the validation value tables are ASSUMPTIONS the owner has not
ruled on (PLAN-p2 Q1 to Q3); a ruling is an edit to that file and nothing else.

- **A missing part is EXCLUDED, never zero.** A job with no Nice-to-have, a
  sourced candidate who never answered the application questions, a SWOT that
  names no need: the part is left out and the others renormalise. A zero is
  arithmetically identical to negative evidence, and "not asked" is not
  "answered badly". Every exclusion is recorded in words in the provenance.
- **Parts one to five need grounded resume evidence or the link is
  `not_assessed`** (`no_grounded_evidence`). Part six alone is application
  paperwork; ranking somebody on their notice period with nothing read from
  their resume would present a form as a match.
- **Behavioural skills are never judged from a resume.** They are not sent
  to the model at all; the assessment tests them.

### THE MODEL RETURNS WORDS AND QUOTES. CODE DECIDES WHAT THEY ARE WORTH

One call per batch of five (`yukti/judge.py`), Terra, temperature 0.0, one
corrective retry that names the exact defects. The model returns `strong`,
`some` or `none` plus a quote copied from the resume; `VERDICT_VALUES`
converts the word server side. Everything is referenced by OPAQUE refs
(`s1`, `n1`, `c1`): no database id, no candidate name, no employer reaches
the prompt.

- **A model failure is `not_assessed`, never a substitute score.** An outage
  is `model_unavailable`, twice-malformed output is `model_output_invalid`.
  There is no deterministic fallback reading (rule 6, owner acceptance 6).
- **A transient failure never overwrites a good result.** `scoring.
  apply_outcome` keeps a `scored` result through a transient failure when the
  resume AND the contract digest are unchanged, stamping only
  `last_attempt_failed`. A changed resume or contract makes the old result
  stale, and the honest state is then `not_assessed`. A `legacy` row is not
  kept this way: its number came from the retired matcher.

### EVERY QUOTE IS CHECKED AGAINST THE RESUME, BY CODE

`yukti/grounding.py` calls no model. A quote grounds when, normalised the
same way on both sides, it is at least three words and a word-boundary
substring of the resume the model was SHOWN (anonymised, redacted, guarded).

- **Never tell a recruiter "no X" when the resume says X.** A skill judged
  `none` whose name (or an ontology equivalent) is on a resume line becomes
  `some` with that line, recorded as `contradicted_negative`. A negative tag
  exists only for a Must-have whose name appears nowhere in the resume.
- **An ungrounded claim is dropped and recorded, never trusted.** A skill
  falls back to its literal term line or to `none`; experience, role fit and
  a need are EXCLUDED (unknown, not negative).
- **A skill tag stores the skill ID, never its name.** The name is resolved
  from the live row at read time, so a rename changes the label and never the
  score or the order. Model-written tags (experience, role fit, needs) pass
  `clean_tag`: at most five words and forty characters, no digit, no em dash,
  no score vocabulary, no culture or protected term, unchanged by
  `inspect_agent_output`, no meta-commentary.

### THE PROVENANCE CARRIES PRESENCE, NEVER A PART'S NUMBER

`yukti_provenance_json` records which parts were present and why the others
were excluded, the contract digest and version, the model and prompt
version, and booleans for redaction, truncation and neutralised injection.
Its only numeric leaf is `contract_version`, walked by a test: a JSON holding
six sub-scores is one careless projection away from a client. The blended
ranking score is not stored at all (CONTRACT v2): it is derived in SQL at read
time (Phase 2 WP-C).

### `apply_outcome` IS THE ONE WRITER OF THE LINK'S YUKTI COLUMNS, UNDER A TRY-LOCK

`score_links` takes `locks.YUKTI_LINK` per link with `pg_try_advisory_xact_lock`
and SKIPS a held link rather than waiting (the holder is doing its work), then
writes each outcome onto its link in the caller's transaction. It commits
nothing; a caller that rolls back writes nothing. It never writes
`match_score`, `match_rationale`, `match_breakdown_json`, `tier` or
`prescreen_grade`: those are history. The profile read MUST be the link's own
`profile_id`; `inputs.candidate_input` raises on any other (audit #12).

### COMPENSATION NEVER REACHES A MODEL, AND ONE MODULE SAYS HOW

`services/compensation_guard.py` is the one implementation: `strip_keys` for
structured data (moved from `matching._strip_compensation`) and
`redact`/`redact_text` for prose, which drops whole LINES stating pay (a long
PDF-extracted line is split into sentences first). Key stripping never saw
prose, and prose is where pay travels: "Current CTC: 18 LPA" in a resume, a
"Compensation" section in a pasted JD, a salary budget in a SWOT.

- **The pattern is wide on purpose, and says so.** It drops a payroll
  engineer's "salary computation engine" line. A missed CTC line is a breach
  nothing downstream can detect; a dropped work line costs one sentence of
  evidence. Tech words that only look like pay are NOT matched: "package",
  bare "pay", "gross", "lakh" as a volume.
- **`tests/test_ctc_never_in_prompt.py` is the acceptance test (A7).** Its
  AST inventory of every `chat_completion`/`invoke_llm` call site in `app/`
  must EQUAL `PROMPT_BUILDERS`, so a new builder fails until it is registered
  with how it keeps pay out. `CANARY` builders run for real over inputs seeded
  with a sentinel in every place pay lives. `PENDING` builders carry the same
  canary as a STRICT xfail naming the hunk that closes it, so the mark fails
  the day the fix lands. `REVIEWED` is a human claim recorded as data. There
  is exactly ONE `owner_exception`, `bgv.parse_reply`, which extracts
  last-drawn pay from an employer's reply by design and is an open owner
  question. **Every phase registers its own builders here when it changes
  them (CONTRACT v2, C7).**

### THE NAME-BLIND PASS MOVED, AND TWO THINGS CHANGED IN THE MOVE

`yukti/anonymise.py` is `hiring/prescreen.anonymise` moved, and it is now the
one implementation (the prescreen copy goes with WP-F). Before this, Yukti's
own prompt received the RAW resume (PLAN-p2 NF-3).

- **Names are removed on word boundaries**, as employers already were: a
  candidate called Ram lost "program", one called Anu lost "manual". The cost
  of the scrub then depended on the candidate's NAME.
- **The identity shapes (email, phone, profile link) go FIRST.** After the
  name scrub, "priya@priyacodes.dev" had become "@priyacodes.dev", which no
  longer matched the address pattern, so a personal domain reached the model.
- **`protected_terms` is load bearing**: the job's skill names are never
  scrubbed, so an employer called Oracle or Docker cannot cost a candidate the
  evidence they are being ranked on.

## Supersessions

- 2026-08-28 (spec-doc5) "`claim_extraction` is Luna and MUST NOT EVALUATE"
  is unchanged; what moves is Yukti: it rode the Luna `rerank` hint and now
  has its own judging task type on Terra. `rerank` itself is deleted by WP-F
  when its last caller goes.
- 2026-08-18 "Compensation stripping ... enforced at one call site"
  (`matching._strip_compensation`): superseded by `compensation_guard`, which
  covers prose as well as keys and is asserted over every prompt builder.
