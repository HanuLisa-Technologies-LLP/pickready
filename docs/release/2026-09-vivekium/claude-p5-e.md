## Current hard rules, Evidence RAG through the tool layer and the semantic repair sweep (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. PLAN-p5 WP5-E plus
CONTRACT v4 items 2 and 3. No migration: the three provenance columns have
existed on `context_chunks` since 0062 and were simply never written, so the
reserved revision `0127_chunk_embedding_model` was NOT used.
`docs/spec/RETRIEVAL.md` is the specification.

### EVIDENCE RAG HAS ONE ENTRY POINT, AND IT IS THE TOOL LAYER

`services/evidence_retrieval` is the only way Vaada, Miti and Siddhi read
retrieved evidence: `resume_passages_for_skill` (Vaada),
`project_evidence_for_candidate` (Vaada), `transcript_passages_for_skill`
(Miti, the skill's own answers excluded) and `support_passages_for_statement`
(Siddhi). Every one is a `tools.execute` call carrying the agent's runtime id,
the workflow stage and the APPLICATION as a tenant-owned object, so the grant,
the stage and the tenant are checked before any row is read, and the result is
validated, bounded and (when cached) keyed on the tenant.

- **`tests/test_evidence_retrieval_through_tools.py` fails the build when a
  Vaada, Miti or Siddhi module imports `services.rag` directly**, at any depth,
  including a function-level import. The module set is the named directories
  (`miti/`, `siddhi/`, `assessment_pipeline/`, `assessment_questions/`), the
  interviewer, PPI, functional-assessment and gap modules, AND whatever
  `agents/identity` lists for those three agents, so a new module is covered
  without anybody remembering to add it.
- **The same test forbids a direct `projects.context` read** in those modules,
  with ONE pending entry (`assessment_questions/generate.py`, Phase 3) on a list
  that must shrink: an entry that no longer needs the exemption fails the
  build until it is removed.
- **An execution failure DEGRADES; a policy refusal RAISES.** Timeout, handler
  error, bad shape or refused input comes back `degraded=True` with the
  exception CLASS and a WARNING `evidence_retrieval.degraded` (identifiers
  only, never the query). A `ToolPolicyError` or `ToolNotFound` is a wiring
  defect that is identical on every retry, and recording it as "degraded"
  would ship a caller that never retrieved anything. Keyword-only results are
  returned and marked `semantic_unavailable`.
- **Nothing numeric crosses.** `PassageRef` is chunk id, source, section and
  verbatim text; its field set is pinned. Retrieval orders what an agent
  reads, never what an answer is worth.
- **`evidence_retrieval` is NOT_LIVE in `test_ai_reachability.py`, on
  purpose.** It was built ahead of its callers. The wiring is named in the
  entry and in the module docstring: Phase 3 calls the two Vaada reads in the
  DISPATCHED question-generation step (never per interactive turn); Phase 5
  WP5-B/C calls the Miti read in `items.evaluate_skill` before the model call
  and the Siddhi read in the support check. The phase that wires it moves the
  entry to LIVE and moves `app.services.tools` out of
  `IMPORTED_BUT_NOT_EXERCISED` in the same change, because these are the first
  live `tools.execute` callers.

### `extract_project_evidence` IS A TOOL, AND PROJECT EVIDENCE STAYS OUT OF THE INDEX

Project evidence is candidate-owned, tenant-free, and model output one join
away from being cited as verbatim evidence, so it is still NOT a chunk source
(`rag/sources.py`). It crosses the tool boundary instead: a READ tool granted
to the interviewer ONLY, available in the assessment stage ONLY (a scorer that
could read projects would grade the projects rather than the answers), cached
for five minutes under a tenant-keyed entry. Compensation-shaped KEYS are
stripped at any depth before the block is built, through
`projects.context.candidate_project_context(redact=...)`, because the stack's
`languages` map renders its KEYS as the "Observed stack" line and an
`expected_ctc` key would otherwise reach a prompt as a technology. Keys,
never values: "payments gateway" is a stack, not a salary.

### `retrieve_context` CAN LEAVE A SKILL'S OWN ANSWERS OUT

`RetrievalRequest.exclude_answer_message_ids` (transcript retrieval only; the
model refuses it on any other source type). The chunk holding an answer is
found by `rag.sources.exchange_ordinals`, which asks THE SAME pairing walk that
cut the transcript into chunks (`_assessment_exchanges`, now shared with the
loader), so "the chunk for this answer" has one definition. Retrieval
over-fetches by the number excluded, capped at the request's own `top_k`
ceiling.

### EVERY EMBEDDED CHUNK SAYS WHICH MODEL PRODUCED IT

`rag/index.index_document` writes `embedding_model`,
`embedding_contract_version` and `embedding_generated_at` IN THE SAME
STATEMENT as the vector, and NULL for all three when there is no vector.
**They are also NULL for a development-fallback vector** (no
`VOYAGE_CONTEXT_4`): a `voyage-4` stamp on a pseudo-random vector is the lie
`scripts/reembed.py` refuses by name. `index.provenance_for` is the one
definition the indexer and the sweep share, so they cannot disagree about what
"stamped" means. `EMBEDDING_CONTRACT_VERSION` moved from `reembed.py` into
`config/llm_providers` beside `EMBEDDING_MODEL`.

### THE SEMANTIC REPAIR SWEEP ASKS THE PROVENANCE COLUMNS, NEVER A TIMESTAMP

`pickready.repair_semantic_index` (`workers/tasks_retrieval.py`, hourly,
`Route.LAMBDA`, in `workers/schedule.py` and all three environments' scheduler
maps) finds what `reconcile_context_index` structurally cannot: chunks that
EXIST with a NULL vector (an outage at index time left them keyword-only for
ever), a retired model or contract stamp, or the wrong width.

- **Bounded and pausable by a SETTING.** `RETRIEVAL_REPAIR_SWEEP_BATCH`
  (default 200 per pass); 0 pauses it. The first passes re-embed every chunk
  written before the stamp existed (20 in pilot on 2026-09-24), which is the
  one expected cost spike.
- **It re-embeds the chunk's own stored text** through
  `contextual.embedding_input(prefix, content)`, the indexer's one join.
- **The UPDATE is guarded on `content_sha256`**, so a chunk the indexer
  rewrote between the sweep's read and its write keeps the indexer's vector
  (`superseded`), rather than receiving one computed from text that is no
  longer there.
- **It refuses to run without an embedding key** (`skipped=
  embedding_not_configured`) and touches nothing.
- **It always logs `rag.repair.swept`**, including a paused or empty pass. A
  pass that could not embed also logs `rag.repair.degraded` and leaves every
  selected chunk exactly as it was; a CloudWatch metric filter on the task
  worker log group alarms after three consecutive degraded hours
  (`infra/modules/observability`, variable `task_worker_log_group_name`).

### THE TWO RETRIEVAL USE CASES SHARE PRIMITIVES, NOT CODE PATHS

Candidate Retrieval (Yukti: which candidates, in what order) and Evidence RAG
(which passages of THIS application bear on THIS skill) stay separate, because
their scopes differ and Evidence RAG must never decide who is scored.
`docs/spec/RETRIEVAL.md` names the one implementation and the owner of each
shared primitive (embedding model and contract version, query vs document
embedding, the OR lexical query, the ontology, fusion, rerank, compensation
stripping, tenant-keyed caching, telemetry) and the copies still to remove:
two OR-tsquery builders (`matching._tsquery`, `rag/retrieval._tsquery`) and two
compensation strippers (`matching._strip_compensation`, the tool layer's), both
Phase 2's to converge because Phase 2 owns the Yukti side.

### Supersessions

- 2026-09-09 "Retrieval is real now": `reconcile_context_index` is no longer
  the only index sweep. Its docstring's "deliberately NOT a staleness check"
  still holds for DOCUMENTS; chunk-level vector staleness is now the repair
  sweep's, asked of the provenance columns rather than of a Python fingerprint.
- 2026-08-18 "Tools RAISE, loops DEGRADE": unchanged, and
  `evidence_retrieval` is where Evidence RAG's degradation is decided, with the
  one refinement that a POLICY refusal is not a degradation.
- 2026-09-01 Project Evidence "Consumption points": the AI context block now
  also reaches question writing through `extract_project_evidence`; the direct
  `projects.context` import in `assessment_questions/generate.py` is pending
  removal by Phase 3.
