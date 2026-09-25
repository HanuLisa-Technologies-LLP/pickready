# CLAUDE.md section draft: Phase 4, package 4B1 (the coding data and executed question generation)

Draft for the orchestrator to merge into the release's CLAUDE.md section. It
covers only what `wip/p4-4b1` built: migration `0124_coding_execution`,
`app/models/coding.py`, `app/services/coding_assessment/{payload,keys}.py`,
`app/services/assessment_formats/coding_generation.py` and the prompt
`coding_question_generation.txt`. Runs, submissions, review, evidence and the
sweeps are package 4B2; the candidate routes are 4C.

### SUPERSESSIONS

- **2026-09-02 (proctoring and question formats), "coding is judged by
  READING and every evaluation and every recruiter view says the code was not
  executed"**: superseded for every coding question written in the v2 shape.
  A v2 question is EXECUTED against hidden tests in the sandbox. v1 rows
  (`payload_version` absent) stay readable exactly as written, with their
  `not_executed_note`, and nothing rewrites them.
- **2026-09-02, "The answer key never crosses the candidate boundary
  unprojected. `candidate_view` is an allowlist per type"**: still true, and
  for a v2 coding question it is no longer the only fence. The key is not in
  the payload at all (next section).

### A CODING QUESTION'S ANSWER KEY IS A TABLE OF ITS OWN, WITH ONE READER

Every other format keeps its key inside `candidate_questions.payload_json`
and relies on `candidate_view` to drop it. A coding key is different in kind:
a hidden test's stdin IS part of the answer, and a candidate program can echo
its stdin. So:

- **`coding_question_keys` holds the hidden tests, the reference solution and
  the reviewer's approach notes.** No response schema, no serializer, and
  exactly one module that names the table or its model:
  `services/coding_assessment/keys.py`. `tests/test_coding_key_confinement.py`
  walks the AST of `app/` and fails on a second reader, on a route or schema
  importing the keys module, and on a response schema declaring a field named
  after a part of the key.
- **The key JUDGES and never DISCLOSES.** `AnswerKey.test_inputs()` hands the
  sandbox a key and a stdin, never an expected output; `AnswerKey.passed(key,
  stdout)` compares inside the module. The executor that asks the question
  never holds the answer.
- **The database refuses a coding payload that carries the key**
  (`ck_candidate_questions_coding_key_private`), and a v2 payload that carries
  the approach notes. The migration counts violating rows first and raises
  with the count rather than failing on an opaque CHECK halfway through.
- **INSERT-ONLY twice over.** UPDATE is REVOKED from `pickready_app` (0014's
  default privileges grant it to every new table, so omitting it from a GRANT
  omits nothing) AND `coding_question_key_is_immutable` refuses an UPDATE from
  any role, the owner included. A grade computed against a key that later
  changed is unreproducible with nothing recording why.
- **A digest binds the key to its validation.** `validation_json.hidden_digest`
  is written with the key and recomputed on every read; a mismatch raises
  `KeyIntegrityError` rather than grading against tests nobody validated.
- **Every dataclass field holding part of the key is `repr=False`**, so an
  exception message or a debugging print cannot quote a test. Pinned by test.

### A CODING QUESTION IS ACCEPTED ONLY AFTER THE SANDBOX PROVES IT

`coding_generation.write_coding_question` is one `agent_loop` (background
bounds, `coding_generation_deadline_seconds`):

- **The model-written reference solution runs against every visible and
  hidden test in the sandbox, and one failure rejects the question.** The
  model wrote both the tests and the solution, so either can be wrong, and a
  question whose expected outputs no working program produced would grade a
  correct candidate as wrong. The reference must also finish inside
  `coding_reference_cpu_headroom` of the CPU limit.
- **Each starter program is probed.** It must run cleanly (a candidate's first
  Run must not crash on the scaffold) and must NOT pass every hidden test (a
  question solved by its own scaffold measures nothing).
- **Deterministic checks run BEFORE any sandbox run** (shape, counts, sizes,
  duplicate inputs, identical hidden outputs, a hidden input echoed in the
  statement, `public class Main` for Java, the candidate-facing guards and the
  meta-commentary sweep), so a malformed answer costs no sandbox time.
- **Loop reasons are CONTENT-FREE** ("hidden test 3 (h3)", outcome words),
  because `agent_loop` logs every reason. What the model needs to FIX a failed
  test (its own reference's stdout, a compiler message) travels in a note on
  the next request only. The model authored those tests; showing them back to
  it is not a disclosure, and it never reaches a log.
- **Disabled execution refuses BEFORE any model call**
  (`code_execution_disabled`). **A sandbox outage LATCHES**: the first
  `ExecutionUnavailable`, lost ticket, fault or refusal ends model calls for
  the rest of the loop and refuses with `code_execution_unavailable`. Asking a
  model again cannot fix a sandbox, and a retry storm spends a model call per
  attempt against a host that cannot answer.
- **The result is a draft OR one refusal word from `REFUSALS`, never both**,
  enforced in `CodingGeneration.__post_init__`. The composer (Phase 3) turns a
  refusal into a prose slot and records the word; it is never silent.
- **A behavioural skill or an unconfigured language RAISES.** Those are
  composition bugs, not degradations.
- **Limits are FROZEN into the payload at generation.** The reference was
  validated under them; Run and Submit read `payload.limits_for`, never live
  settings, so a settings change cannot move the limits under an issued
  question.
- **Never in the prompt: compensation, the resume, or anything a candidate
  wrote.** `build_messages` is the one place that decides what the model sees
  and is pure; a test plants CTC sentinels and asserts their absence.

### THE v2 PAYLOAD IS CANDIDATE-SAFE BY CONSTRUCTION

`coding_assessment.payload.CodingPayloadV2` forbids extra keys, so a hidden
test cannot be stored in a valid v2 payload at all. `candidate_projection`
is still an explicit field list (`CANDIDATE_FIELDS`), so a field added later
stays out of the candidate's view until somebody names it. The statement is
`candidate_questions.prompt`, one copy of the text the candidate read. The
payload validates languages against the product's language REGISTRY, not
this deployment's configured list, so a question issued while a language was
offered stays readable after a deployment stops offering it.

### NEW HARD RULES, ONE LINE EACH

- A hidden test, a reference solution or an expected output of a hidden test
  never reaches a payload, a response, a log line or a sandbox request.
- Only `coding_assessment/keys.py` names `coding_question_keys`.
- Nothing on the generation or key path can start a process or evaluate a
  string; the reference solution runs in the sandbox only (AST-swept).
- `persist_coding_question` writes the question row and its key in the
  CALLER's transaction: a question without a key cannot be graded, and a key
  without a question is a secret nobody can reach.
- `coding_hidden_tests_max` may never exceed 30 (`HIDDEN_TESTS_MAX`, the
  database CHECK); the settings validator refuses it at boot rather than at
  INSERT after the model and the sandbox were paid.

### OPEN, AND SAID OUT LOUD

- `write_coding_question` has NO production caller until Phase 3's composer
  calls it. Add it to `test_ai_reachability.REQUIRED_CALLERS` in the same
  change that wires it.
- `types.parse_payload`, `parse_answer` and `candidate_view` do not yet
  dispatch on `payload_version`: a v2 row would fail the v1 model there. The
  verified hunk is `docs/release/2026-09-vivekium/hunks/p4-4b1-types-v2-dispatch.patch`
  and must land with (or before) the composer that writes v2 rows.
