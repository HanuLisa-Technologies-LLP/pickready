# Retrieval: Candidate Retrieval and Evidence RAG

Status: normative for ownership and boundaries, 2026-09-24 (Vivekium release,
PLAN-p5 WP5-E, CONTRACT v4 items 2 and 3). It records what is true in the
checked-out code and names the owner of every shared primitive. It does not
describe Yukti's internals, which Phase 2 owns.

## Two use cases, kept separate on purpose

| | Candidate Retrieval (Yukti) | Evidence RAG (Vaada, Miti, Siddhi) |
|---|---|---|
| Question | Which candidates should this job look at, in what order? | Which parts of THIS application's documents bear on THIS skill or statement? |
| Unit | A whole profile (`profiles.embedding`, `profiles.resume_tsv`) against a whole JD (`jobs.embedding`) | A chunk (`context_chunks`): a resume section, a JD section, one question-and-answer pair |
| Scope | Every consenting candidate the job may consider (tenant pool plus the databank) | Exactly one application: one profile id or one link id in `source_ids` |
| Entry point | `services/matching.run_matching`, becoming `services/yukti` (Phase 2) | `services/evidence_retrieval`, the ONE entry point, every read a `tools.execute` call |
| Output | An ORDER over candidates, and a pre-assessment grade computed afterwards | Verbatim passages with their chunk ids (`context_chunks:<id>` locators). No score |

They must never merge. Evidence RAG deciding who gets scored would let "we
found less about this person" become "this person was not considered", and a
candidate linked to a job is ALWAYS scored (2026-07-26 rule). Candidate
Retrieval reading chunk passages would let one well-indexed resume section
outrank a candidate whose resume the chunker cut differently. The scopes are
different, so the code paths are different.

## The shared primitives, one implementation each

A shared primitive has ONE implementation and ONE owner. A use case CALLS it;
it never keeps a private copy. Where a copy exists today it is listed, with who
removes it.

| Primitive | The one implementation | Owner | Used by | Known copies to remove |
|---|---|---|---|---|
| Embedding model and width | `config/llm_providers.EMBEDDING_MODEL` (`voyage-4`), `services/embeddings.EMBEDDING_DIM` (1024) | Platform | Both | None |
| Embedding contract version (how the embedded text is built) | `config/llm_providers.EMBEDDING_CONTRACT_VERSION` | Platform | Both; `rag/index` and `rag/repair` stamp and compare it, `scripts/reembed.py` imports it | Was script-local in `reembed.py` until 2026-09-24 |
| Document vs query embedding | `embeddings.embed` (document) and `embeddings.embed_query` (query) | Platform | Both | None |
| Lexical OR query | an OR `to_tsquery` over alphanumeric words (precision is fusion's job) | Platform | Both | TWO copies today: `matching._tsquery` (terms list, ontology-expanded) and `rag/retrieval._tsquery` (free text). Phase 2 converges Yukti's onto one shared builder; Evidence RAG adopts it in the same change |
| Vocabulary ontology | `services/hiring/ontology` (`expand`, `canonical`, `matches`, `overlap`) | Phase 2 (Yukti) | Candidate Retrieval, job relevance. Evidence RAG does NOT expand queries today | None |
| Fusion | Reciprocal Rank Fusion, `rag/retrieval.fuse` (ORDER only, never a weighted sum) | Platform (`services/rag`) | Evidence RAG. Candidate Retrieval unions its stages and lets scoring order them | None; if Yukti ever fuses, it calls `fuse` |
| Rerank | `rag/reranker.rerank_chunks` (Voyage `rerank-2.5` or the recorded lexical pass) | Platform (`services/rag`) | Evidence RAG only | None. Yukti's `rerank` LLM TASK TYPE is a scoring call, not a reranker, and Phase 2 deletes the name |
| Compensation stripping | the tool layer: `tools/implementations._is_compensation_key` and `_strip_compensation` (keys, any depth) | Platform (`services/tools`) | Evidence RAG (every tool output) | `matching._strip_compensation` with its own marker list. Phase 2 re-points Yukti's prompt builder at the tool layer's function |
| Tenant-keyed caching | `tools/executor._cache_key` (refuses a key without a tenant) | Platform | Every cached tool read | None |
| Telemetry | Evidence RAG: `tools/telemetry` (name, agent, status, timing; never payloads) plus the `rag.*` log lines. Candidate Retrieval: `matching_progress` stages and `services/activity` | Each use case | | Different surfaces on purpose: one is an interactive progress display, the other a counter |
| Index freshness | `rag/index.index_document` (writes vector AND provenance in one statement), `pickready.reconcile_context_index` (documents with no chunks), `pickready.repair_semantic_index` (chunks with a missing, stale or wrong-width vector) | Platform (`services/rag`) | Evidence RAG | `profiles.embedding` has no repair sweep (Phase 2 risk R7, owner question Q6) |

## Evidence RAG goes through the tool layer, and a test enforces it

`services/evidence_retrieval` is the only module Vaada, Miti and Siddhi read
retrieved evidence through:

| Function | Agent (runtime id) | Tool | Stage | Scope |
|---|---|---|---|---|
| `resume_passages_for_skill` | Vaada (`interviewer`) | `retrieve_context` | assessment | the application's profile id |
| `project_evidence_for_candidate` | Vaada (`interviewer`) | `extract_project_evidence` | assessment | the candidate's derived project evidence |
| `transcript_passages_for_skill` | Miti (`scoring`) | `retrieve_context` | assessment | the link's transcript, the skill's own answers excluded |
| `support_passages_for_statement` | Siddhi (`ppi_report`) | `retrieve_context` | reporting | the link's transcript |

Every call carries `ToolContext(tenant_id, stage, objects=(application,))`, so
the policy engine checks the agent's grant, the stage and the application's
tenant BEFORE any row is read. An execution failure (timeout, handler error,
bad shape) returns `degraded=True` with the exception class and a WARNING
`evidence_retrieval.degraded`; a POLICY refusal raises, because it is a wiring
defect that is identical on every retry. A result whose pieces were all found
by keyword is returned and marked `semantic_unavailable`.

`tests/test_evidence_retrieval_through_tools.py` fails the build when any
module under `miti/`, `siddhi/`, `assessment_pipeline/`, `assessment_questions/`,
the interviewer, PPI and report modules, or anything `agents/identity` lists for
Vaada, Miti or Siddhi imports `services.rag` directly. It also forbids a direct
`services.projects.context` import there, with one pending entry the assessment
phase removes.

Project evidence is deliberately NOT a chunk source (`rag/sources.py` gives the
reason: candidate-owned, tenant-free, and model output one join from being
cited). It crosses the tool boundary as its own READ tool instead.

## Index provenance and repair

Every chunk `rag/index` embeds now carries `embedding_model`,
`embedding_contract_version` and `embedding_generated_at`, written with the
vector. A chunk with no vector, or with a vector from the development fallback
(no `VOYAGE_CONTEXT_4`), carries NULL for all three.

`pickready.repair_semantic_index` (hourly, `Route.LAMBDA`, in
`workers/schedule.py` and all three environments' scheduler maps) selects
chunks whose vector is NULL, whose model or contract stamp is not current, or
whose width is wrong, oldest write first, capped by
`RETRIEVAL_REPAIR_SWEEP_BATCH` (default 200; 0 pauses it). It re-embeds the
chunk's own stored text through `rag/contextual.embedding_input`, and the
UPDATE is guarded on `content_sha256` so a chunk the indexer rewrote mid-pass
keeps the indexer's vector. It refuses to run without an embedding key. Every
pass logs `rag.repair.swept`; a pass that could not embed also logs
`rag.repair.degraded`, which a CloudWatch metric filter on the task worker log
group alarms on after three consecutive hours.

The first passes after this ships re-embed every chunk written before the
stamp existed (pilot held 20 on 2026-09-24). That is the one expected cost
spike, and the batch setting is the lever.

## What is not built, and who builds it

- The CALLERS of `evidence_retrieval`: Phase 3 (Vaada question generation) and
  Phase 5 WP5-B/C (Miti items, Siddhi support). `tests/test_ai_reachability.py`
  records the module as NOT_LIVE until then.
- One lexical query builder shared by both use cases: Phase 2.
- A repair sweep for `profiles.embedding`: an open owner question (Phase 2 Q6).
- Query expansion through the ontology for Evidence RAG: not planned; the
  fairness argument in `hiring/ontology.py` applies to it too, and adding it is
  a call to `ontology.expand` inside `evidence_retrieval.skill_query`, never a
  second term map.
