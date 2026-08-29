# Ready Pick Now — Architecture Direction (design review, 28 August 2026)

**Status:** ADVISORY, not authoritative. This file records the architectural direction the
product owner settled on in a design-review session on 2026-08-28, the day before
spec-doc6 was issued.

**Precedence:** this file sits BELOW everything in spec-doc6 §0.2's table. It does not
override the RBAC Specification, the Runbook (`Readypick Hiring Philosophy.md`),
spec-doc6, the Candidate Dashboard Specification, or specdoc4/spec-doc5. Where it agrees
with them it is a useful restatement of intent; where it goes beyond them it is a
direction of travel, not a requirement. Treat any item here that is not also stated in a
higher-ranked document as a proposal, and record it in `RUNBOOK_OPEN_QUESTIONS.md` rather
than implementing it silently.

Filed into the repository 2026-08-29 so that the reasoning behind several spec-doc6
decisions is recoverable.

---

## The load-bearing principle

> Deterministic software owns TRUTH and CONTROL. AI owns UNDERSTANDING and REASONING.
> Expensive reasoning is activated by uncertainty, contradiction, risk or exceptional
> potential, never by default.

This is not "50% AI / 50% software". It is responsibility-based allocation.

**Software owns:** policy versions, weights, thresholds, gates, state, provenance,
evidence relationships, scoring mathematics, auditability, configuration, orchestration,
cost control.

**AI owns:** semantic interpretation, claim interpretation, ambiguous evidence,
cross-source reasoning, contradiction interpretation, question generation, candidate
investigation, synthesis.

Two corollaries the codebase already encodes and must keep encoding:

- The aggregator makes zero model calls.
- The LLM is never the policy, never the database, and never the final authority.

---

## 1. Progressive investigation, never an elimination funnel

The single most important correction from this session.

A `3000 -> 800 -> 300 -> 30` cascade of cheap elimination passes is the WRONG
architecture for this product. A candidate with a weak resume may have exceptional actual
capability, and an early cheap signal must not silently remove them.

Every candidate keeps a persistent representation and a baseline evaluation state. What
varies is the DEPTH of investigation the system spends on them, not whether they remain
in the pool.

> Surface evidence may determine investigation priority, but must not by itself determine
> candidate exclusion unless an explicit hard gate or validated criterion applies.

This is consistent with the standing rule already in CLAUDE.md: a candidate linked to a
job is always scored, and retrieval is a ranking prior only.

---

## 2. Signal taxonomy beneath the five dimensions

D1 to D5 are the top-level skeleton, not the intelligence ceiling. Underneath them sits a
large taxonomy of atomic signals:

```
raw evidence -> atomic signals -> dimensions -> decision
```

Examples: evidence coverage, evidence quality, evidence independence, evidence recency,
claim corroboration, requirement coverage, skill depth, skill breadth, scope, scale,
ownership, impact, role relevance, career progression, contradiction count, missing
critical evidence, assessment performance, AI-assistance indicators.

Most of these are DETERMINISTIC and cost nothing per candidate. Only the semantic ones
need a model. The point of the taxonomy is many signals, not many model calls.

---

## 3. Evidence Sufficiency is distinct from Confidence

A system can be confident while lacking evidence on a critical competency. These must not
collapse into one number:

- **Evidence Sufficiency** = f(coverage, quality, independence, consistency, criticality)
- **Evidence Confidence** = how trustworthy the underlying evidence is
- **Interpretation Confidence** = how confident the interpretation is
- **Decision Uncertainty** = how uncertain the resulting ranking is (two adjacent
  candidates can both be high-confidence and still be a statistical tie)

A candidate with a high score and low evidence sufficiency triggers investigation rather
than a verdict.

---

## 4. The Investigation Engine

The system continuously asks "what is the most important unresolved uncertainty about this
candidate?", not "should I run another LLM?".

Escalate on: low evidence sufficiency, a critical evidence gap, a contradiction, an
unusual candidate profile, exceptional potential despite weak surface evidence, a
high-risk role, an unsupported material claim, a novel case, high decision uncertainty.

Otherwise stop. This is the mechanism that controls cost.

---

## 5. Constitutional escalation

The Runbook stays immutable and is NOT sent to a model on every call. The compiled
`runbook_data/` is the runtime representation; the Runbook itself remains the canonical
authority.

```
normal:      candidate -> evidence -> compiled policy -> evaluation
exceptional: candidate -> evidence -> compiled policy -> uncertainty
             -> retrieve relevant original Runbook sections -> deeper reasoning
```

Rare and measurable. The compiled policy must never be described as equivalent to the
whole Runbook.

**And the compiler must not be an autonomous LLM policy writer.** The path is: Runbook ->
controlled extraction/specification -> schema validation -> human approval -> versioned
data package. A model may assist extraction; software validation and human approval
publish the executable policy.

---

## 6. Adaptive assessment

Assessment depth is determined by evidence sufficiency and unresolved uncertainty, not by
an arbitrary question count.

- Default target around 25 questions.
- Hard maximum around 30 to 32, configurable per role.
- At the maximum with evidence still insufficient: stop and report insufficient evidence.
  **Never convert unresolved uncertainty into artificial confidence, and never treat
  insufficient evidence as rejection.**

Four classes of question:

1. **Confirmation** — verify strong existing claims.
2. **Gap** — obtain missing evidence.
3. **Contradiction** — resolve conflicting evidence.
4. **Discovery** — find exceptional capability not visible in the original profile.

The fourth class is what protects the unconventional candidate. Without it the system
reproduces ATS bias.

Note: these numbers are NOT the numbers currently in the codebase (20/17/15/12 technical
and 25/20/15/10 framework questions by grade, per CLAUDE.md). Reconcile before changing
anything; a question-count change moves billing and comparability.

---

## 7. AI-assistance is an integrity signal, never a penalty

Explicitly rejected in this session: em-dash detection, stylistic fingerprints, and any
`AI detected -> subtract N points` rule. A human writes with em dashes; a model writes
without them; a candidate may use a grammar tool or a translator.

Instead, AI-assistance likelihood joins response specificity, cross-source consistency,
the candidate's ability to defend the answer, assessment behaviour and claim verification
to decide whether further INVESTIGATION is warranted. It never directly reduces a
competence score, and it never auto-rejects.

Note the deliberate distinction between three things that are not the same: AI assistance,
capability authenticity, and assessment integrity. Polishing grammar, generating an entire
answer, and having a model solve the technical problem are different behaviours.

---

## 8. Gaps in the Runbook this session identified

Recorded here so they reach `RUNBOOK_OPEN_QUESTIONS.md` rather than being invented in
code. The Runbook is strong on how a good hiring decision should be MADE and thinner on
how to PROVE the system makes good ones.

| Gap | What is missing |
|---|---|
| Predictive validity | No framework proving the ranking predicts actual job success. Needs outcome capture through 90-day / 6-month / 12-month performance and retention, compared against a resume-similarity baseline and the previous engine version. |
| Evidence availability bias | Lack of evidence must not become a proxy for lack of capability. A candidate with little digital footprint needs a different assessment pathway, not a lower score. |
| Candidate x environment fit | The five dimensions are candidate-centric. Manager compatibility, team interaction, working conditions, expectations and compensation alignment are not modelled as an interaction term. |
| Proxy risk analysis | Every criterion should be tested for job-relatedness and proxy risk BEFORE it enters a scorecard, not audited afterwards. "Executive presence" can proxy for background, accent or schooling. |
| Per-stage fairness | Bias can enter at any selection stage. Needs a selection-stage fairness ledger, not one eventual audit. |
| Accessibility and accommodation | Underdeveloped, and material for a product with questionnaires, a conversational agent and timed interaction. An accommodation must never become an authenticity penalty. |
| Adversarial candidate model | No formal "if I wanted to fool this system, how would I?" model. Should be a recurring red-team exercise, not a one-time feature. |
| Novel-case detection | Needs a state beyond pass/fail/confidence: this candidate-job situation lies outside the distribution the evaluation model was designed on. Constrain confidence rather than forcing an unknown into a known rubric. |
| Model and prompt drift | Same candidate, same evidence, different model version can produce a different result. Needs a model/prompt/policy/embedding/ontology version stamp on every evaluation. |
| Regression testing | Needs a golden candidate set (strong, weak, unconventional, sparse-evidence, contradictory, fabricated, AI-assisted, high-potential) run before any model, prompt, policy, weight, ontology or retrieval change ships. |
| Selection quality vs explanation quality | A beautiful dossier does not mean a correct ranking. The two must be evaluated independently, or the product becomes extremely explainable nonsense. |
| Guarantee methodology | The commercial "return your money" promise needs a defined outcome contract: what "good candidate" means, and what happens when a client rejects for their own reasons. |

---

## 9. What was explicitly decided NOT to build

- No Neo4j. The evidence graph lives in PostgreSQL until a real workload proves multi-hop
  traversal is the bottleneck.
- No separate vector database. pgvector until benchmarked.
- No Kubernetes / EKS.
- No multi-agent swarm. More agents is not more intelligence.
- No autonomous policy compilation by a model.
- No LLM-computed final score.
- No prompt containing the entire Runbook.
- No universal resume-similarity ranking.
- No AI detector as a rejection mechanism.
- No candidate elimination funnel.

Each may be reconsidered later if evidence justifies it. Complexity has to earn its place.

---

## 10. The four questions the architecture must answer yes to

1. If two candidates look completely different on paper, can the system still find the
   genuinely better one?
2. If a candidate tries to manipulate the system, does it detect uncertainty and
   investigate rather than trust the presentation?
3. If the system is uncertain, does it know it is uncertain?
4. Six months after the hire, can we determine whether the reasoning was actually
   predictive?

Adding another model, another vector database or another agent does not move any of these.
