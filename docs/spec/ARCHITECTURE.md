# Architecture decisions

Normative. Each entry is a decision the code already has to obey, the reason
for it, what enforces it, and what it does NOT mean. An entry is changed by
writing a new one that supersedes it in place, never by editing the old one to
match new behaviour.

Precedence: below the RBAC Specification and the Runbook, beside
[HIRING_WORKFLOW.md](HIRING_WORKFLOW.md). Where an older document (including
[ARCHITECTURE_DIRECTION_2026-08-28.md](ARCHITECTURE_DIRECTION_2026-08-28.md),
which is advisory) describes agent artifacts as a store, a bus or a source of
record, this document wins.

---

## AD-1. The database is authoritative. A2A artifacts are provenance and hand-off records only.

Recorded 2026-09-24, Vivekium simplification release (CONTRACT v4 item 4).

### The decision

Every fact the product acts on, shows or bills is read from a Postgres table
through the RLS-aware session: the skills contract and its frozen version
(`job_competencies`, `job_scorecard_bindings`, the skill snapshot), the
application (`job_candidate_links`), the conversation and its answers, the
evidence ledger (`evidence_items` and its claims), the evaluation working
(`evaluations`), the delivered PRISM Report (`functional_skills_reports` and
its dimension rows), the proctoring record and the credit ledger.

An A2A artifact (`services/agents/artifacts.Artifact`: `tatva_matrix`,
`ai_score`, `swot_evidence`, `answer_event`, `scoring_state`, `evidence_gap`,
`prism_report`) is a TYPED, VERSIONED, TENANT-SCOPED DESCRIPTION of rows that
already exist. It exists for two jobs and no third:

1. **Hand-off.** It is the only shape in which one agent's output may cross to
   another agent, so the boundary is typed, versioned and checked
   (`verify_for_consumer`) rather than prose pasted into a prompt.
2. **Provenance.** Its identifiers (`artifact_id`, `version`, `source_refs`,
   `correlation_id`, the principal) are what a trace, a log line or a
   `provenance.StageRecord` cites to say which work produced what.

### What that means in code

- **An artifact is built FROM committed rows, never the other way round.**
  `ppi.publish_tatva_matrix` is called after the matrix rows and the freeze
  binding are flushed; `matching.publish_ai_scores` runs after the ranking
  commit, and its own comment states the rule: "an artifact describes rows that
  exist". `ppi.published_matrix` rebuilds the matrix artifact from
  `load_framework` and the binding's version on demand.
- **No table stores an artifact payload.** There is no `artifacts` table and
  there must not be one. `agent_execution_traces` carries identifiers, counts
  and timings, never content (2026-08-18 rule). The artifact module, the
  provenance ledger and the run envelope import nothing from SQLAlchemy, the
  ORM models or the session factory, so they cannot become a second store by
  accident.
- **No consumer treats an artifact as the answer.** A grade, a rank, a report
  section, a gate verdict or a charge is computed from the tables. Where a
  consumer is handed an artifact, it verifies it (tenant, job, version, status,
  contract fields) and the verification can only REFUSE: it never supplies a
  value the table does not hold.
- **A failed publish costs the hand-off, never the work.** Both publishers
  return None and log on failure, AFTER the rows are durable. Deleting either
  publish call changes no stored value, grade, tier or order, which is the
  practical test of "provenance only".
- **A timestamp is not evidence, and neither is an artifact.** A stage that
  claims to have run is checked against the TABLE (`hiring.gates.scorecard_gate`
  asks for matrix rows before the approval stamp; `provenance.Ledger.problems`
  reports an artifact-bearing stage with no artifact as "a timestamp"). An
  artifact's existence is not proof either: it proves a publish happened, and
  the rows it cites are what a reader checks.

### Why

- **One source of truth per fact (rule 5).** An artifact store beside the
  tables would be a second answer to "what are this job's criteria", and the
  two would disagree exactly when it matters: after a human edit, a revive, a
  rename or a re-freeze. The 2026-09-23 Tatva authority rules (rename clears
  `swot_origin`, revive keeps it, Save Matrix freezes the reviewed version) are
  written against rows; a stored payload would carry the pre-edit version.
- **Tenant isolation lives in RLS (rule 3).** A table read goes through the
  policy. An artifact payload read from anywhere other than a freshly built
  object would be a read path RLS does not see.
- **Erasure has to reach everything (2026-09-09, embeddings are PII).**
  `cascade_erasure` and `job_closure_erasure` delete rows. A stored artifact
  payload quoting a resume line or an answer would be one more copy those two
  functions do not know about, and it would outlive the Delete My Profile the
  candidate was promised.
- **Immutability of the delivered report is a property of its row.** The
  PRISM Report is written once to `functional_skills_reports` and the report
  route answers PATCH, PUT and DELETE with 403; an artifact copy kept anywhere
  else would be an unguarded second copy of an immutable document.

### What it does NOT mean

- It does not remove artifacts. The typed boundary and the provenance ids are
  worth keeping; what is refused is using them as a store or a source.
- It does not mean every stage publishes one. Only the publishers above exist.
  A stage without an artifact is recorded by its rows, and by a
  `StageRecord` carrying a gate verdict where a gate fired.
- It does not make `ppi.published_matrix` load bearing. It has no production
  caller today (see `models/job_scorecard_binding.py`); consumers read the
  current rows. Wiring it in later changes nothing about this decision,
  because it too rebuilds from the tables.

### What enforces it

- `backend/tests/test_architecture_database_authoritative.py`: the artifact,
  provenance and envelope modules import no SQLAlchemy, no `app.models` and no
  `app.core.db`; no ORM table is named for artifacts; no table carries an
  artifact payload column.
- `backend/tests/test_no_silent_degradation.py`: the live refusal points (G1
  asks the table first, the ledger reports an artifact-less stage as a
  timestamp, the envelope refuses a run with no principal or correlation id,
  an artifact without its contract fields is refused at publish).
- `backend/tests/test_ai_reachability.py` and the sweeps it names for the
  import graph as a whole.

### Owners and related records

- Code: `backend/app/services/agents/artifacts.py`,
  `backend/app/services/agents/provenance.py`,
  `backend/app/services/agents/envelope.py`; publishers in
  `backend/app/services/ppi.py` and `backend/app/services/matching.py`.
- CLAUDE.md: the Vivekium simplification release section (2026-09-2x) carries
  the one-line rule and points here.
