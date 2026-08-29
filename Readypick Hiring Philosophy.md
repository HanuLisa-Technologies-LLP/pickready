# READY PICK NOW
# HIRING PHILOSOPHY & INTELLIGENCE RUNBOOK

**Version 1.1 — The Standard Runbook**
**Owner:** Hanulisa Technologies LLP · Ready Pick Now
**Classification:** Proprietary & Confidential — Internal + Controlled Client Disclosure
**Document type:** Operating doctrine, decision engine specification, and delivery standard

---

## DOCUMENT CONTROL

| Field | Value |
|---|---|
| Document ID | RPN-PHIL-001 |
| Version | 1.1 |
| Date | 2026-08-29 (v1.1 editorial revision). The v1.0 issue date was not recorded in the source document and has not been invented here. |
| Owner | Hanulisa Technologies LLP (legal entity) · Ready Pick Now (product) |
| Status | Standard (binding on all delivery pods and all engine releases) |
| Supersedes | All prior evaluation notes, ranking heuristics, and ad-hoc scoring sheets |
| Review cadence | Quarterly, plus mandatory review after every calibration cycle |
| Change authority | Ready Pick Now Hiring Standards Board (see §4.6) |
| Companion artefacts | Company DNA Intake Instrument · Role SWOT Instrument · Department Evidence Graphs · Validation Question Banks · Recruiter Dossier Template · Calibration Ledger |

### Amendment log

| Ver | Date | Section | Change | Authority |
|---|---|---|---|---|
| 1.0 | Not recorded | All | Initial standard issued | Hiring Standards Board |
| 1.1 | 2026-08-29 | Front matter; §2; §6.3; §16; Appendices D, E, F; new table of contents and changelog | Editorial revision under spec-doc6 section 2.1. Front matter completed; table of contents added; §16's twelve subsections numbered §16.1-§16.12, which resolves the §16.3 cross-reference in §51; Appendix D and Appendix E subsections renumbered D.1-D.4 and E.1-E.7 so they stop colliding with the dimension names D1-D5 and the tier names E0-E5; two cross-references repaired (C5 cited §12.4, the PROHIBITED disqualifier list, where it means §12.3, the legitimate one; §6.3 cited §11.4 for adverse-impact monitoring, which is §52.5); product naming normalised; glossary extended with the canonical spellings. Every edit is itemised in RUNBOOK_EDITS.md. No weight, threshold, multiplier, band boundary, tier definition, cap, floor, intake question or example pair was changed. | Editorial (no Standards Board decision required) |

---

## HOW TO USE THIS RUNBOOK

This document is **not** a marketing narrative and **not** an architecture whitepaper. It is the operating doctrine that every Ready Pick Now evaluation must obey. It exists so that two different recruiters, two different pods, in two different cities, evaluating the same 400 candidates against the same job, arrive at substantially the same shortlist — and can each explain why.

Read it by role:

| If you are | Read first | Read fully | You are accountable for |
|---|---|---|---|
| Ready Pick Now founder / Standards Board | Parts 0, I, X, XI | All | The philosophy itself; weight governance; calibration |
| Delivery pod recruiter | Parts 0, I, II, VII, IX, XIII | II, VI (your departments), VIII | Evidence collection quality; override discipline |
| Delivery pod coordinator | Parts II, VII, XIII | VII, XIII | Chain of custody; validation completion; flags raised |
| Engineering / AI team | Parts II, III, XII | II, III, VI, XII | Faithful implementation of the scoring contract |
| Client HR Manager / CHRO | Parts 0, I, IV, V, IX, XI | IV, V | Company DNA accuracy; scorecard approval; overrides |
| Client Hiring Manager | Part V | V | Truthful SWOT; force-ranking; calibration review |

**The three-layer rule.** Nothing in this runbook works in isolation. Every evaluation is the product of three layers:

```
LAYER 1 — READY PICK NOW HIRING PHILOSOPHY  (this document; owner-set; universal; rarely changes)
                    ↓ constrains
LAYER 2 — COMPANY HIRING PHILOSOPHY         (client-set; per client; stable across roles)
                    ↓ constrains
LAYER 3 — ROLE SWOT INTELLIGENCE            (hiring-manager-set; per job; changes every role)
                    ↓ produces
              THE EVALUATION CONFIGURATION FOR THIS JOB
```

A lower layer may **tune** a higher layer within declared bounds. A lower layer may **never** suspend a higher layer's integrity rules. Precedence conflicts are resolved in §3.5.

---

## TABLE OF CONTENTS

- [**PART 0 — EXECUTIVE SUMMARY**](#part-0-executive-summary)
  - [0.1 The single sentence](#01-the-single-sentence)
  - [0.2 The problem we are actually solving](#02-the-problem-we-are-actually-solving)
  - [0.3 The Ready Pick Now answer, in five moves](#03-the-ready-pick-now-answer-in-five-moves)
  - [0.4 What we deliver to a client](#04-what-we-deliver-to-a-client)
  - [0.5 What we explicitly refuse to do](#05-what-we-explicitly-refuse-to-do)
  - [0.6 The moat, stated plainly](#06-the-moat-stated-plainly)
  - [0.7 The commercial promise this enables](#07-the-commercial-promise-this-enables)
  - [0.8 Standing caveats on external evidence](#08-standing-caveats-on-external-evidence)
- [**PART I — THE READY PICK NOW HIRING PHILOSOPHY (LAYER 1)**](#part-i-the-ready-pick-now-hiring-philosophy-layer-1)
  - [1. First principles](#1-first-principles)
  - [2. The Ready Pick Now Decision Contract](#2-the-ready-pick-now-decision-contract)
  - [3. The three-layer intelligence model](#3-the-three-layer-intelligence-model)
  - [4. Roles, permissions and governance](#4-roles-permissions-and-governance)
- [**PART II — THE EVIDENCE MODEL**](#part-ii-the-evidence-model)
  - [5. The Candidate Evidence Graph](#5-the-candidate-evidence-graph)
  - [6. The evidence strength hierarchy](#6-the-evidence-strength-hierarchy)
  - [7. The claim–evidence–verification ledger](#7-the-claimevidenceverification-ledger)
  - [8. Reading claims correctly](#8-reading-claims-correctly)
- [**PART III — THE FIVE DIMENSIONS AND THE SCORING ENGINE**](#part-iii-the-five-dimensions-and-the-scoring-engine)
  - [9. The five dimensions](#9-the-five-dimensions)
  - [10. The scoring mathematics](#10-the-scoring-mathematics)
  - [11. Weight vectors](#11-weight-vectors)
  - [12. Thresholds, gates and disqualifiers](#12-thresholds-gates-and-disqualifiers)
  - [13. Contradiction handling](#13-contradiction-handling)
  - [14. Confidence, abstention and escalation](#14-confidence-abstention-and-escalation)
- [**PART IV — LAYER 2: THE COMPANY HIRING PHILOSOPHY**](#part-iv-layer-2-the-company-hiring-philosophy)
  - [15. Purpose and principle](#15-purpose-and-principle)
  - [16. The Company DNA Intake Instrument](#16-the-company-dna-intake-instrument)
  - [17. Compiling Company DNA into engine configuration](#17-compiling-company-dna-into-engine-configuration)
- [**PART V — LAYER 3: ROLE SWOT INTELLIGENCE**](#part-v-layer-3-role-swot-intelligence)
  - [18. Why SWOT, and why it must not stay a form](#18-why-swot-and-why-it-must-not-stay-a-form)
  - [19. The transformation pipeline](#19-the-transformation-pipeline)
  - [20. The scorecard](#20-the-scorecard)
- [**PART VI — DEPARTMENT EVIDENCE GRAPHS**](#part-vi-department-evidence-graphs)
  - [21. IT & SOFTWARE ENGINEERING](#21-it-software-engineering)
  - [22. DATA, ANALYTICS, DATA SCIENCE & AI/ML](#22-data-analytics-data-science-aiml)
  - [23. MECHANICAL ENGINEERING & MANUFACTURING](#23-mechanical-engineering-manufacturing)
  - [24. ELECTRICAL & ELECTRONICS ENGINEERING](#24-electrical-electronics-engineering)
  - [25. CIVIL, STRUCTURAL & CONSTRUCTION](#25-civil-structural-construction)
  - [26. R&D AND PRODUCT DEVELOPMENT](#26-rd-and-product-development)
  - [27. DESIGN, UX & CREATIVE](#27-design-ux-creative)
  - [28. ARCHITECTURE (BUILT ENVIRONMENT)](#28-architecture-built-environment)
  - [29. FINANCE & ACCOUNTING](#29-finance-accounting)
  - [30. LEADERSHIP, GENERAL MANAGEMENT & EXECUTIVE](#30-leadership-general-management-executive)
  - [31. HUMAN RESOURCES](#31-human-resources)
  - [32. SALES, MARKETING & BUSINESS DEVELOPMENT](#32-sales-marketing-business-development)
  - [33. OPERATIONS, SUPPLY CHAIN & LOGISTICS](#33-operations-supply-chain-logistics)
  - [34. SKILLED TRADES, BLUE-COLLAR & FRONTLINE WORKFORCE](#34-skilled-trades-blue-collar-frontline-workforce)
  - [35. NON-TECHNICAL SUPPORT & ADMINISTRATIVE](#35-non-technical-support-administrative)
  - [36. ADDING A NEW DEPARTMENT](#36-adding-a-new-department)
- [**PART VII — THE AUTHENTICITY DOCTRINE**](#part-vii-the-authenticity-doctrine)
  - [37. The strategic position](#37-the-strategic-position)
  - [38. The triangulation protocol](#38-the-triangulation-protocol)
- [**PART VIII — CANDIDATE-STATE LOGIC**](#part-viii-candidate-state-logic)
  - [39. The state model](#39-the-state-model)
  - [40. State-specific protocols](#40-state-specific-protocols)
- [**PART IX — HUMAN–AI INTERACTION, OVERRIDE AND ACCOUNTABILITY**](#part-ix-humanai-interaction-override-and-accountability)
  - [41. The division of labour](#41-the-division-of-labour)
  - [42. Override discipline](#42-override-discipline)
  - [43. The recruiter dossier (the delivered artefact)](#43-the-recruiter-dossier-the-delivered-artefact)
- [**PART X — CALIBRATION AND LEARNING**](#part-x-calibration-and-learning)
  - [44. Why calibration is the whole game](#44-why-calibration-is-the-whole-game)
  - [45. The outcome data model](#45-the-outcome-data-model)
  - [46. Calibration metrics](#46-calibration-metrics)
  - [47. The calibration cycle](#47-the-calibration-cycle)
  - [48. The per-client calibration review](#48-the-per-client-calibration-review)
  - [49. Guarding against pathological learning](#49-guarding-against-pathological-learning)
- [**PART XI — FAIRNESS, COMPLIANCE AND ETHICS**](#part-xi-fairness-compliance-and-ethics)
  - [50. The position](#50-the-position)
  - [51. Where bias actually enters](#51-where-bias-actually-enters)
  - [52. Structural fairness controls](#52-structural-fairness-controls)
  - [53. Compliance framework](#53-compliance-framework)
  - [54. Ethical commitments beyond compliance](#54-ethical-commitments-beyond-compliance)
- [**PART XII — IMPLEMENTATION ARCHITECTURE**](#part-xii-implementation-architecture)
  - [55. Architecture principles](#55-architecture-principles)
  - [56. The pipeline](#56-the-pipeline)
  - [57. Component contracts](#57-component-contracts)
  - [58. Retrieval design](#58-retrieval-design)
  - [59. Data schema (core objects)](#59-data-schema-core-objects)
  - [60. Build sequencing](#60-build-sequencing)
- [**PART XIII — OPERATING PROCEDURES**](#part-xiii-operating-procedures)
  - [61. SOP-01 · Client onboarding (Layer 2 capture)](#61-sop-01-client-onboarding-layer-2-capture)
  - [62. SOP-02 · Role intake and configuration](#62-sop-02-role-intake-and-configuration)
  - [63. SOP-03 · Evidence collection](#63-sop-03-evidence-collection)
  - [64. SOP-04 · Evaluation and review](#64-sop-04-evaluation-and-review)
  - [65. SOP-05 · Post-delivery](#65-sop-05-post-delivery)
  - [66. Checklists](#66-checklists)
- [**PART XIV — SELF-CRITIQUE AND KNOWN LIMITATIONS**](#part-xiv-self-critique-and-known-limitations)
  - [67. Where this runbook is weakest](#67-where-this-runbook-is-weakest)
  - [68. Deliberate choices that will be questioned](#68-deliberate-choices-that-will-be-questioned)
  - [69. What would falsify this approach](#69-what-would-falsify-this-approach)
- [**APPENDICES**](#appendices)
  - [Appendix A — The Company DNA Intake Instrument (field form)](#appendix-a-the-company-dna-intake-instrument-field-form)
  - [Appendix B — The Role SWOT Instrument (field form)](#appendix-b-the-role-swot-instrument-field-form)
  - [Appendix C — The Scorecard Template](#appendix-c-the-scorecard-template)
  - [Appendix D — Validation probe bank (starter set, all departments)](#appendix-d-validation-probe-bank-starter-set-all-departments)
  - [Appendix E — End-to-end worked example](#appendix-e-end-to-end-worked-example)
  - [Appendix F — Glossary](#appendix-f-glossary)
  - [Appendix G — One-page summary (for internal display)](#appendix-g-one-page-summary-for-internal-display)

---

# PART 0 — EXECUTIVE SUMMARY

*(This part replaces what was previously scoped as a "Prism Report." It is written to be read standalone by an executive audience in twelve minutes, and to be handed to a prospective client without further editing.)*

## 0.1 The single sentence

> **Ready Pick Now is not a resume-ranking product. It is an evidence-driven hiring decision system that first establishes what "good" means for a specific company, department, role, seniority and hiring situation — and then gathers independent, cross-verified evidence to determine which candidates genuinely demonstrate it.**

## 0.2 The problem we are actually solving

A client does not want 500 candidates sorted by similarity. A client wants an answer to a much harder question: *of these 500, who are the ten I should seriously spend my leadership's time on, and why should I believe you?*

Three forces have made the old answer worthless:

1. **The visible layer of hiring is now trivially optimised.** Any candidate with a JD and a general-purpose AI model can produce an ATS-perfect, keyword-complete, achievement-quantified resume in ninety seconds. Resume-to-JD similarity has therefore become a measure of *tool access*, not *capability*. It is close to noise, and in some populations it is actively inverted — the most polished document often belongs to the least verified candidate.
2. **Assessments are equally gameable.** Generic AI-generated questions, answered by a generic AI, tell us nothing. Industry survey data (see caveat in §0.8) indicates a large majority of candidates would use AI on an assessment if they believed detection was impossible, while only a small minority disclose it.
3. **Different jobs are not the same problem in different clothes.** The evidence that proves a competent backend engineer is structurally different from the evidence that proves a competent structural engineer, a competent FP&A manager, a competent product designer, or a competent maintenance technician. A single universal ranking formula must, by construction, be wrong for almost every role it touches.

## 0.3 The Ready Pick Now answer, in five moves

**Move 1 — One skeleton, different muscles.** Every candidate, in every department, is evaluated on the same five universal dimensions: *Verified Competence, Track Record & Impact, Role & Context Fit, Authenticity & Consistency, Trajectory & Potential*. What changes by department and seniority is (a) which sub-signals feed each dimension, (b) what counts as acceptable evidence, and (c) how much each dimension weighs. The skeleton makes results comparable and auditable. The muscles make results correct.

**Move 2 — The resume is demoted to a hypothesis.** A resume states claims. It does not settle them. Every claim material to the decision is promoted into a *Claim Record* that must be corroborated by at least one independent source before it can contribute full score. The candidate becomes a **Candidate Evidence Graph** — claims, evidence nodes, verification links, contradictions, and gaps — not a document with a score attached.

**Move 3 — Authenticity becomes a first-class score, not a detector.** We do not chase a magic "was this written by AI" oracle; those are unstable and adversarial. We instead triangulate: resume ↔ validation questionnaire ↔ assessment ↔ work artefacts ↔ interview ↔ references. Consistency across independent sources raises confidence; contradiction lowers it and raises a flag. **No flag ever auto-rejects.** Flags route to human review with the underlying evidence attached.

**Move 4 — Client knowledge is compiled, not filed.** The Company Hiring Philosophy (Layer 2) and the Hiring Manager SWOT (Layer 3) are not documents shown to a model as context and hoped upon. They are transformed through a defined pipeline — *SWOT → competency → observable evidence statement → assessment method → weight → threshold → disqualifier* — into the actual numeric configuration the engine runs. A hiring manager's statement that "our team has no cloud depth and we migrate in six months" must visibly move a weight, not merely appear in a summary.

**Move 5 — Every score is traceable, and the recruiter stays accountable.** No number is delivered without the evidence that produced it. The output is a decision dossier — ranking, evidence, fit rationale, authenticity, risks, missing evidence, and a stated confidence level — not an opaque 87.

## 0.4 What we deliver to a client

For each role, per shortlisted candidate:

- **Ready Pick Score** with per-dimension breakdown and the weight vector used
- **Confidence level** (High / Moderate / Low / Insufficient), with what would raise it
- **Evidence ledger**: every material claim, its corroboration status, and its source
- **Authenticity assessment**: consistency findings, verification results, open flags
- **Fit rationale** written against *this* role's SWOT-derived priorities, not the JD text
- **Risk register**: missing evidence, contradictions, retention/notice/counter-offer risks
- **Comparison logic**: why this candidate ranks above the next one

## 0.5 What we explicitly refuse to do

- Auto-reject on any AI-detection signal
- Present a score without its evidence
- Use one weight vector across departments or seniorities
- Treat institutional pedigree as a proxy for competence (see §8.9 — the pedigree cap)
- Score a dimension on which we have no evidence (we mark it *Unknown*, we do not impute)
- Claim predictive validity we have not measured

## 0.6 The moat, stated plainly

Retrieval-augmented generation is a commodity. Agent orchestration is a commodity. Embeddings, parsers and vector stores are commodities. Our defensible asset is the **hiring intelligence encoded around the AI**: the signal taxonomy, the evidence hierarchy, the department competency models, the weight-setting discipline, the contradiction logic, the confidence and abstention rules, and the calibration loop that ties all of it back to real post-hire outcomes.

Competitors sell **speed** and **volume**. We sell **defensibility**. A client can buy fast shortlists anywhere. A client cannot easily buy a shortlist whose every position is explained by evidence and whose method survives an audit.

## 0.7 The commercial promise this enables

Because every position in a Ready Pick Now shortlist is evidence-linked, we can make a stronger commitment than "our AI ranked them." We can commit to *standards of process*: every shortlisted candidate has been triangulated across a minimum number of independent sources; every material claim has a corroboration status; every score carries a stated confidence; and we will show the working. That is the basis on which a satisfaction-backed guarantee becomes a responsible commercial promise rather than a marketing risk. (Guarantee mechanics are commercial policy and sit outside this runbook; the runbook's job is to make the promise operationally true.)

## 0.8 Standing caveats on external evidence

This runbook draws on public research and industry survey data. Three cautions are binding on how we use it:

- **Vendor statistics are marketing claims until independently audited.** Detection-accuracy figures, profile-count claims, and time-saving percentages published by assessment and sourcing vendors are directional only and must never be cited to clients as fact.
- **Detection is an arms race.** No proctoring or plagiarism method is durable. Authenticity must be a composite of many weak signals; any single detector will be defeated.
- **Credential and legal frameworks are jurisdiction-specific.** Professional licensure regimes, bias-audit statutes and background-check norms differ by country. The engine encodes them as conditionals, never as universals. Ready Pick Now is India-first with international reach; §11 defines how jurisdiction is resolved.

---

# PART I — THE READY PICK NOW HIRING PHILOSOPHY (LAYER 1)

*This part is authored by Ready Pick Now as owner. It is the constitution. A client may not amend it; a client may only configure within it.*

## 1. First principles

These fourteen axioms are the philosophical core. Every rule later in this runbook is derivable from them. When a novel situation arises that the runbook does not cover, resolve it by reasoning from these axioms and log the reasoning for the Standards Board.

### Axiom 1 — Hiring is a decision under uncertainty, not a matching exercise.
The task is never "find the closest document." It is "reduce uncertainty about future job performance to the point where a human can responsibly commit." Every feature we build must be judged on whether it reduces that uncertainty.

### Axiom 2 — Claims are cheap; evidence is expensive; verified evidence is the product.
Anything a candidate can assert costlessly carries near-zero decision weight on its own. Our value is created precisely at the point where an assertion is converted into corroborated evidence.

### Axiom 3 — "Good" is contextual and must be defined before candidates are examined.
We do not discover the criteria while reading resumes. The scorecard is frozen before scoring begins. Criteria discovered mid-stream are the mechanism by which bias enters hiring, because they are almost always reverse-engineered from a candidate someone already likes.

### Axiom 4 — Different work requires different proof.
There is no universal evidence hierarchy. A commit history proves something about a software engineer and nothing about a site engineer. A stamped drawing proves something about a structural engineer and nothing about a designer. Department models are not a nicety; they are the correctness condition.

### Axiom 5 — Process evidence outranks output evidence when outputs are cheap to fabricate.
Where an artefact can be generated by a machine in minutes (code that compiles, a polished deck, a written case study, an essay answer), the artefact itself carries reduced weight and the candidate's ability to *reason about* it carries increased weight. We score how someone got there, not only where they got.

### Axiom 6 — Contradiction is information, not noise.
When two sources disagree, the correct response is not to average them. It is to (a) record the contradiction, (b) attempt resolution through targeted probing, and (c) if unresolved, reduce confidence and flag. Averaging destroys the single most valuable signal we collect.

### Axiom 7 — Absence of evidence is not evidence of absence.
A missing GitHub is not a weak engineer. A missing reference is not a dishonest candidate. Missing evidence produces *Unknown*, which reduces confidence, which may reduce rank — but it must never be silently scored as a low value. §6.6 governs when Unknown becomes a penalty.

### Axiom 8 — The system must be able to say "I don't know."
Abstention is a feature. An engine that always produces a confident ranking is an engine that lies when the evidence is thin. Ready Pick Now abstains, escalates, or requests more evidence under the conditions in §14.

### Axiom 9 — Explainability is not a compliance chore; it is the product.
If we cannot explain a ranking in terms a hiring manager can dispute, we have not delivered a decision — we have delivered an opinion with a number attached. Every score must be traceable to specific evidence.

### Axiom 10 — The human is accountable; the AI is instrumental.
Ready Pick Now never makes a hiring decision. It structures evidence, applies declared rules, and surfaces reasoning. Recruiters and client decision-makers remain accountable. This is both an ethical position and a legal one.

### Axiom 11 — Adversarial pressure is permanent and must be designed for, not patched.
Candidates optimise against whatever the evaluator rewards. Any signal we publicly reward will be gamed. Therefore: keep some signals non-obvious, vary probe generation, prefer signals that are expensive to fake even when known, and never rely on a single detector.

### Axiom 12 — Fairness is a design constraint, not a filter applied afterwards.
Bias enters through criteria selection, evidence availability, and proxy variables — long before any model runs. We control it at the scorecard stage (observable evidence only), the evidence stage (proxy caps), and the audit stage (adverse-impact monitoring).

### Axiom 13 — A rule that is never measured will drift into superstition.
Every weight, threshold and evidence tier in this runbook is a hypothesis about predictive validity. Each carries a calibration obligation (§10). Rules that cannot be tested are downgraded to guidance and marked as such.

### Axiom 14 — Simplicity is a safety property.
Complexity that does not demonstrably improve prediction is a liability: it hides errors, resists audit, and inflates cost. If an added signal, agent or dimension does not out-predict the simpler configuration, it is removed. Complexity must earn its place.

### 1.1 Anti-axioms (things we deliberately reject)

| Rejected belief | Why we reject it |
|---|---|
| "Better similarity matching wins." | Similarity measures document optimisation, not capability. |
| "A good enough AI detector solves cheating." | Detectors are defeated within one release cycle; composites are not. |
| "More agents = more intelligence." | Agent count is architecture, not intelligence. Intelligence is in the rules the agents apply. |
| "Speed is the differentiator." | Speed is a commodity and a race to the bottom. Defensibility is not. |
| "The client's JD is the specification." | The JD is a public advertisement. The SWOT is the specification. |
| "Structured data beats human judgement." | Structured data *organises* human judgement. It does not replace it. |
| "One model can hold the whole hiring context." | Context that is not compiled into weights and rules is context that silently fails to apply. |

---

## 2. The Ready Pick Now Decision Contract

Every engagement operates under a contract of six commitments. These are the operational form of the philosophy and are stated to the client verbatim.

**C1 — Criteria before candidates.** We will not score a single candidate until a completed, force-ranked scorecard exists and has been approved by the client's HR Manager. Roles without an approved scorecard are blocked in the system, not worked around.

**C2 — Minimum evidence standard.** No candidate is placed in a delivered shortlist without corroboration across the minimum number of independent sources defined for that department and seniority (§7.4). A candidate we could not verify is either not shortlisted or is shortlisted with an explicit Low-Confidence label and the reason.

**C3 — Full traceability.** Every dimension score in a delivered dossier links to the evidence that produced it. If a client asks "why," an answer exists that does not include the phrase "the model determined."

**C4 — Declared confidence.** Every candidate carries a confidence level. We would rather deliver eight High-Confidence candidates and say so than pad to ten with unverifiable profiles.

**C5 — No silent automation of rejection.** Authenticity flags, credential mismatches and threshold failures produce human-reviewed outcomes. Only explicitly client-declared hard disqualifiers (§12.3) may filter automatically, and every automatic filter is logged and reviewable.

**C6 — Calibration honesty.** We will report to the client how our rankings performed against their actual interview and hire outcomes, including when we were wrong, and we will show what we changed as a result.

---

## 3. The three-layer intelligence model

### 3.1 Layer 1 — Ready Pick Now Hiring Philosophy (owner layer)

**Owned by:** Ready Pick Now Standards Board.
**Changes:** Rarely, and only through versioned amendment.
**Contains:** The axioms; the five dimensions and their definitions; the evidence hierarchy and tier rules; the department competency models and their baseline weight vectors; the authenticity doctrine; contradiction and confidence mathematics; abstention rules; fairness constraints; the calibration protocol; the delivery standard.

This layer answers: *What does Ready Pick Now believe about hiring, and what will it never do regardless of what a client asks?*

### 3.2 Layer 2 — Company Hiring Philosophy (client layer)

**Owned by:** Client HR Manager / CHRO.
**Captured:** Once at onboarding, reviewed every six months or on major organisational change.
**Changes:** Slowly.
**Contains:** The client's hiring DNA — what they consistently value, how they evaluate, what they will not tolerate, their structural constraints, and their standing defaults.

This layer answers: *What does "good" mean at this company, across all its roles?*

Full instrument in Part IV. In summary it captures: evaluation philosophy (practical vs credentialed; potential vs proven); tenure and stability norms; the client's own bar-raising practices; interview capacity and process shape; compensation banding discipline; location/relocation/notice realities; diversity commitments; compliance constraints (background checks, licensure requirements, data-handling restrictions); non-negotiable cultural behaviours expressed as observable evidence; and their history of what has worked and failed.

### 3.3 Layer 3 — Role SWOT Intelligence (hiring manager layer)

**Owned by:** Client Hiring Manager, approved by HR Manager.
**Captured:** Per job, in a structured working session.
**Changes:** Per role; recalibrated after first shortlist review.
**Contains:** The actual hiring problem behind the JD — what the team already has, what it lacks, what the hire is expected to unlock, and what would cause the hire to fail.

This layer answers: *Given that we are hiring a Senior Backend Engineer, what specific hiring problem is this particular hire solving?*

Full instrument and transformation rules in Part V.

### 3.4 How the layers combine

```
L1 baseline weight vector  (department × seniority)
        │
        ├── L2 company modifiers   (bounded ±, declared, reusable)
        │
        ├── L3 SWOT modifiers      (bounded ±, per-role, force-ranked)
        │
        ▼
   Normalised active weight vector for this job
        +
   L1 integrity rules (non-negotiable)
        +
   L2 constraints (compliance, disqualifiers)
        +
   L3 thresholds & disqualifiers (role-specific)
        ▼
   THE EVALUATION CONFIGURATION  →  frozen, versioned, auditable
```

The configuration is **frozen at the moment scoring begins** and versioned. If a hiring manager changes their mind mid-search — which is common and legitimate — a new configuration version is created and the affected candidates are rescored under it, with both versions retained. Silent mid-stream criteria changes are the single most common source of unfair and unexplainable shortlists, and the system makes them structurally impossible.

### 3.5 Precedence and conflict resolution

| Conflict | Resolution |
|---|---|
| L3 asks for something L2 prohibits | L2 wins. Escalate to HR Manager. |
| L2 asks for something L1 prohibits | L1 wins. Ready Pick Now declines the configuration and explains why. |
| L3 weight request exceeds declared bounds | Clamp to bound; notify hiring manager; record the request. |
| L3 disqualifier is a protected-characteristic proxy | Refuse; escalate to HR Manager; log under fairness audit. |
| Two L3 competencies both demanded as top priority | Force-ranking session; "everything is a must-have" configurations are rejected (§20.3). |
| Client requests auto-rejection on an authenticity flag | Refused under C5. Offer instead: auto-routing to a priority human review queue. |
| Client requests removal of the confidence label | Refused under C4. |

### 3.6 What each layer must never do

- **L1 must never** encode client-specific preference, geography-specific credential logic as universal, or any rule it cannot calibrate.
- **L2 must never** introduce criteria that are not observable evidence, or constraints that are protected-characteristic proxies.
- **L3 must never** introduce a criterion that contradicts the posted JD in a way that would be unfair to applicants who applied in good faith. If the real requirement differs materially from the JD, the JD is amended and reposted — the SWOT does not become a hidden filter.

---

## 4. Roles, permissions and governance

### 4.1 The job-creation flow (canonical)

```
1. Recruiter drafts JD from role request
        ↓
2. System generates the Role SWOT instrument, pre-populated from
   Company DNA (L2) + department model (L1) + JD draft
        ↓
3. Hiring Manager completes SWOT in a structured working session
   (async permitted; live session strongly preferred for senior roles)
        ↓
4. System transforms SWOT → draft scorecard
   (4–6 competencies, observable evidence, proposed weights, disqualifiers)
        ↓
5. Recruiter reviews draft scorecard, resolves ambiguity with HM
        ↓
6. HR Manager approves scorecard  ← MANDATORY GATE
        ↓
7. JD posted; configuration frozen as v1; sourcing begins
        ↓
8. First shortlist → calibration review with HM → configuration v2 if needed
```

### 4.2 Permission matrix

| Action | Recruiter | Hiring Manager | HR Manager / CHRO | RPN Pod | RPN Standards Board |
|---|---|---|---|---|---|
| Draft JD | ✔ | ✔ | ✔ | ✔ | — |
| Complete Role SWOT | assist | ✔ own | ✔ | assist | — |
| Propose weights | ✔ | ✔ | ✔ | ✔ | — |
| **Approve scorecard** | — | — | ✔ **only** | — | — |
| Edit Company DNA (L2) | — | — | ✔ | assist | — |
| Add a hard disqualifier | propose | propose | ✔ approve | propose | audit |
| Override a candidate ranking | ✔ (logged) | ✔ (logged) | ✔ | ✔ (logged) | audit |
| Clear an authenticity flag | — | — | ✔ | ✔ (with note) | audit |
| Amend Layer 1 (this runbook) | — | — | — | propose | ✔ **only** |
| Access raw candidate evidence | ✔ | scoped | ✔ | ✔ | audit |
| Export dossier to client | ✔ | — | ✔ | ✔ | — |

**HR Manager is the chief of the recruitment function** and holds supervisory permission over everything within the client's tenancy: scorecard approval, disqualifier approval, flag clearance, override audit, and fairness review. This is deliberate — the governance layer is not optional, and it must sit with a named human who has organisational authority.

### 4.3 The four mandatory gates

| Gate | Condition | Blocked if not met |
|---|---|---|
| **G1 — Configuration gate** | Approved scorecard exists, ≤6 competencies, force-ranked, disqualifiers declared | Scoring cannot start |
| **G2 — Evidence gate** | Candidate meets minimum-source standard for department/seniority | Cannot enter delivered shortlist at High/Moderate confidence |
| **G3 — Integrity gate** | All open authenticity flags reviewed by a human and dispositioned | Cannot be delivered |
| **G4 — Delivery gate** | Dossier complete: evidence links, confidence, risks, comparison logic | Cannot be exported to client |

### 4.4 The Standards Board

A standing body of three to five people (founder, senior recruitment leader, engineering lead, and where possible an external HR/psychometrics advisor). Responsibilities:

- Approve amendments to Layer 1
- Review quarterly calibration results and authorise weight revisions
- Review the fairness audit and adverse-impact monitoring
- Adjudicate any refusal of a client configuration
- Own the "unmeasurable rules" register (Axiom 13) and retire rules that never earn their place

### 4.5 Delivery pod responsibilities (two-person pod)

| Recruiter | Coordinator |
|---|---|
| Runs the SWOT working session | Owns evidence chain of custody |
| Owns scorecard quality and force-ranking discipline | Drives validation questionnaire completion rates |
| Reviews all authenticity flags | Schedules and administers assessments |
| Writes the fit rationale in each dossier | Runs reference collection and logs responses |
| Holds the override log and justifies each override | Maintains the calibration ledger entries |
| Runs the calibration review with the client | Flags data-quality and consent issues |

### 4.6 Change control on this runbook

Any pod member may propose an amendment via the Standards Register with: the rule in question, the observed failure, the proposed change, and the calibration evidence supporting it. The Board reviews monthly. Amendments to scoring mathematics or weight baselines require calibration evidence, not argument alone.

---

# PART II — THE EVIDENCE MODEL

*This is the foundation everything else stands on. If the evidence model is wrong, no amount of scoring sophistication rescues the result.*

## 5. The Candidate Evidence Graph

### 5.1 Why a graph and not a profile

A profile is a flat set of attributes: skills, years, titles. It cannot represent the two things we most need — **where a fact came from** and **whether two facts agree**. A graph can.

The Candidate Evidence Graph (CEG) has four node types and three edge types.

**Node types**

| Node | Definition | Example |
|---|---|---|
| **Claim** | An assertion about the candidate's capability, experience, credential or outcome, made by the candidate or on their behalf | "Led migration of a monolith to microservices for a 40-person engineering org" |
| **Evidence** | An observable artefact or response that bears on a claim | A commit history; an assessment transcript; a reference response; a stamped drawing; a P&L summary |
| **Verification** | The result of applying a defined check to evidence | "Credential verified with issuing body — active, expires 2028" |
| **Gap** | A material claim or required competency for which no evidence exists | "No evidence of production ownership" |

**Edge types**

| Edge | Meaning |
|---|---|
| **Supports** | Evidence increases belief in a claim; carries a strength value |
| **Contradicts** | Evidence decreases belief in a claim; carries a severity value |
| **Corroborates** | Two *independent* evidence nodes support the same claim (the highest-value edge in the system) |

### 5.2 The graph schema

```json
{
  "candidate_id": "RPN-C-000000",
  "role_id": "RPN-J-000000",
  "config_version": "v1",
  "claims": [
    {
      "claim_id": "CL-01",
      "text": "Owned production migration monolith → microservices",
      "type": "experience_ownership",
      "maps_to_competency": ["CMP-CLOUD-ARCH", "CMP-PROD-OWNERSHIP"],
      "materiality": "high",
      "source": "resume",
      "status": "corroborated | partially_corroborated | uncorroborated | contradicted",
      "supporting_evidence": ["EV-04", "EV-07", "EV-11"],
      "contradicting_evidence": [],
      "independence_count": 3
    }
  ],
  "evidence": [
    {
      "evidence_id": "EV-04",
      "type": "assessment_response",
      "tier": "E4",
      "collected_at": "2026-08-14",
      "recency_of_underlying_event": "2025-Q3",
      "provenance": "rpn_administered",
      "independence_group": "assessment",
      "content_ref": "…",
      "verification": {"method": "live_probe", "result": "consistent"}
    }
  ],
  "gaps": [
    {"gap_id": "GP-02", "competency": "CMP-COST-OPT",
     "reason": "no_evidence_requested", "impact": "confidence_only"}
  ],
  "contradictions": [
    {"id": "CX-01", "claim": "CL-03", "severity": "moderate",
     "description": "…", "resolution_attempted": true,
     "resolution_outcome": "unresolved", "disposition": "flagged_human_review"}
  ]
}
```

### 5.3 Materiality

Not every claim matters. A claim is **material** if it maps to a competency on the approved scorecard, or if it would change a threshold or disqualifier decision. Immaterial claims are recorded but not verified — verification effort is finite and must be spent where it changes the decision.

Materiality is assigned as:

- **High** — maps to a must-have competency, or to a disqualifier condition
- **Medium** — maps to a nice-to-have competency, or supports the Impact dimension
- **Low** — contextual colour; no verification obligation

**Rule:** every High-materiality claim must reach at least *partially corroborated* status before the candidate can be delivered at Moderate or High confidence.

### 5.4 Independence

Corroboration only counts when the sources are genuinely independent. Two evidence nodes are **independent** if they could not have been produced from the same act of preparation by the candidate.

| Pair | Independent? | Reason |
|---|---|---|
| Resume + cover letter | **No** | Same authorship, same preparation session |
| Resume + LinkedIn profile | **No** | Same self-report; trivially synchronised |
| Resume + validation questionnaire (async, written) | **Weakly** | Same authorship, but probes are unseen and specific |
| Validation questionnaire + live probe on the same claim | **Yes** | Live probing removes preparation time |
| Assessment + interview reasoning | **Yes** | Different modality, different pressure |
| Portfolio artefact + reference response | **Yes** | Different authorship |
| GitHub commit history + assessment debugging process | **Yes** | Different production context |
| Two references from the same manager | **No** | Single source |
| Reference + credential verification with issuing body | **Yes** | One is human, one is institutional |

**Independence groups** are declared in the schema. The independence count for a claim is the number of *distinct groups* supporting it, not the number of evidence items.

### 5.5 Chain of custody

Every evidence node records: who collected it, when, under what conditions, and whether the candidate consented. Evidence collected outside the declared process (a recruiter's informal impression from an unlogged phone call; a social-media inference) is **inadmissible** — it may not appear in the graph and may not be used in a dossier. This is not bureaucratic; unlogged evidence is where bias hides.

---

## 6. The evidence strength hierarchy

### 6.1 The six tiers

| Tier | Name | Definition | Cost to fabricate | Default strength |
|---|---|---|---|---|
| **E0** | Asserted | Unverifiable self-claim | Zero | 0.10 |
| **E1** | Self-described with specificity | Self-report containing checkable specifics (numbers, systems, names, mechanisms) | Low | 0.25 |
| **E2** | Artefact provided by candidate | Portfolio, sample, document, repository link supplied by the candidate | Low–moderate | 0.40 |
| **E3** | Structured response under controlled conditions | Validation questionnaire, take-home, unproctored assessment | Moderate | 0.55 |
| **E4** | Demonstrated under observation | Live/proctored assessment, live technical probe, work simulation, structured interview reasoning | High | 0.80 |
| **E5** | Third-party verified | Credential confirmed with issuing body; reference corroboration on a specific claim; verified commit history with sustained authorship; documented client/employer confirmation | Very high | 0.95 |

These are **default** strengths. Department models may adjust within ±0.10 with justification, recorded in the department model, not applied ad hoc.

### 6.2 The fabrication-cost principle

Strength is assigned by **how expensive the evidence is to fake**, not how impressive it looks. This principle is what makes the hierarchy robust in an AI-saturated environment:

- A beautifully written achievement bullet is E0/E1 — it is free to produce.
- A polished portfolio case study is E2 — it is now cheap to generate.
- The same candidate explaining, live and unprepared, *why* they rejected the alternative approach in that case study is E4 — it is expensive to fake.

**This is the axis on which our entire anti-gaming posture rests.** We do not try to detect fabrication. We systematically shift decision weight onto evidence classes where fabrication is costly.

### 6.3 Recency and decay

Evidence about capability ages. Two different clocks matter:

- **Collection recency** — when we gathered it (rarely decays; a reference given last month is still a reference)
- **Event recency** — when the underlying work happened (decays, at rates that differ by domain)

Decay multiplier applied to the *claim's* contribution:

| Age of underlying event | Fast-moving domains (software, data, digital marketing, AI) | Stable domains (civil, mechanical, finance, HR, trades) |
|---|---|---|
| 0–2 years | 1.00 | 1.00 |
| 2–4 years | 0.90 | 0.97 |
| 4–7 years | 0.75 | 0.92 |
| 7–10 years | 0.60 | 0.85 |
| 10+ years | 0.45 | 0.75 |

**Exceptions that do not decay:** demonstrated reasoning ability, licensure currently active, leadership scope (for executive roles, historical scope is still informative), and domain fundamentals in stable fields.

**Anti-pattern guarded against:** decay must not become an age proxy. Decay applies to *specific technical claims*, never to the candidate's overall profile, and never to the Trajectory dimension. Adverse-impact monitoring (§52.5) specifically tests decay for age-correlated effects.

### 6.4 Evidence quality modifiers

Beyond tier, four modifiers adjust an evidence node's contribution:

| Modifier | Range | Applies when |
|---|---|---|
| **Specificity** | ×0.7 – ×1.2 | Generic language reduces; checkable mechanism/number/system names increase |
| **Attribution clarity** | ×0.6 – ×1.1 | "We built" vs "I owned"; ambiguous ownership reduces |
| **Scale relevance** | ×0.8 – ×1.15 | Evidence at scale similar to the role's context increases |
| **Corroboration** | ×1.0 – ×1.4 | Number of independent groups supporting the claim |

**The attribution modifier deserves emphasis.** The distinction between *participated in* and *owned* is one of the highest-value discriminations in resume evaluation and is almost never made by similarity-based systems. Our validation instruments are designed specifically to force this distinction (§18.5, §21.3).

### 6.5 Effective evidence strength

For a claim `c` supported by evidence set `E`:

```
For each evidence e in E:
   s(e) = tier_strength(e)
        × specificity(e)
        × attribution(e)
        × scale_relevance(e)
        × decay(event_recency(e), domain_clock)

Combine using diminishing-returns aggregation across INDEPENDENT groups g:

   S(c) = 1 − Π over groups g of ( 1 − max_{e∈g} s(e) )

Then apply the corroboration bonus:
   S*(c) = min( 1.0 , S(c) × corroboration_multiplier(independence_count) )

   independence_count : 1 → 1.00
                        2 → 1.15
                        3 → 1.28
                        4+ → 1.40   (capped)
```

Why this shape: within a group, adding more of the same kind of evidence adds little (three resume bullets about the same project are not three pieces of evidence). Across groups, evidence compounds — which is exactly the incentive structure we want.

**Contradiction adjustment:**

```
   S_final(c) = S*(c) × (1 − contradiction_penalty)

   contradiction severity → penalty
     minor      → 0.10
     moderate   → 0.30
     severe     → 0.60
     disqualifying → claim voided, integrity flag raised
```

### 6.6 Handling missing evidence (the Unknown discipline)

This is where most systems quietly cheat. A missing signal gets scored as zero, which is mathematically identical to negative evidence, which is wrong and unfair.

Ready Pick Now's rule:

```
IF evidence for competency X was NEVER REQUESTED
   → status = UNKNOWN
   → competency is EXCLUDED from the weighted average
   → weights renormalise across remaining competencies
   → confidence is reduced
   → gap recorded as "not_assessed"

IF evidence for competency X WAS REQUESTED and candidate DID NOT PROVIDE
   → status = NOT_PROVIDED
   → confidence reduced more sharply
   → flag: "requested and not supplied"
   → recruiter reviews reason (may be legitimate: NDA, confidentiality, no access)
   → only after review may it be scored as a deficiency

IF evidence WAS PROVIDED and DOES NOT SUPPORT the claim
   → status = WEAK or CONTRADICTED
   → this is genuine negative evidence and IS scored
```

Three distinct states, three distinct handlings. Conflating them is the most common source of unjust rankings, and it is banned.

**The confidentiality carve-out.** Candidates in finance, defence, healthcare, legal and enterprise consulting frequently cannot share artefacts. NOT_PROVIDED for confidentiality reasons must trigger an alternative evidence route (structured verbal walkthrough under NDA-safe abstraction; reference corroboration; redacted samples) before it counts against anyone. Failure to offer an alternative route is a delivery defect.

### 6.7 Evidence sufficiency

How much is enough? Sufficiency is defined per dimension per department (Part VI), but the universal floor is:

| Confidence level | Requirement |
|---|---|
| **High** | All must-have competencies at ≥2 independent groups, ≥1 at E4+, zero unresolved contradictions of moderate+ severity |
| **Moderate** | All must-have competencies at ≥1 independent group beyond self-report, ≥1 competency at E4+, contradictions resolved or minor |
| **Low** | Some must-haves rest on E0–E2 only, or unresolved moderate contradictions exist |
| **Insufficient** | Fewer than half of must-haves have any evidence above E1 → candidate is NOT delivered; either collect more or exclude with reason |

---

## 7. The claim–evidence–verification ledger

### 7.1 Purpose

The ledger is the human-readable form of the graph and the backbone of every dossier. It is what makes "show me why" answerable in ten seconds.

### 7.2 Ledger format

| # | Claim | Materiality | Sources | Independence | Tier (best) | Status | Note |
|---|---|---|---|---|---|---|---|
| 1 | Owned monolith→microservices migration, 40-eng org | High | Resume; Validation Q7; Live probe; Reference (ex-EM) | 3 groups | E5 | **Corroborated** | Reference confirmed scope and ownership; live probe surfaced specific rollback design |
| 2 | "Reduced infra cost 34%" | High | Resume; Validation Q9 | 1 group | E1 | **Uncorroborated** | Candidate could not reconstruct baseline; number treated as directional only |
| 3 | AWS Solutions Architect – Professional | Medium | Resume; Issuer verification | 2 groups | E5 | **Verified** | Active, expires 2027 |
| 4 | "Expert in Kubernetes" | High | Resume; Assessment | 2 groups | E4 | **Contradicted (moderate)** | Assessment showed operational gaps in networking/debugging; flagged |

### 7.3 The four claim statuses

- **Corroborated** — ≥2 independent groups, best tier E3+, no contradictions
- **Partially corroborated** — 1 independent group beyond self-report, or 2 groups at low tiers
- **Uncorroborated** — self-report only; contributes at heavily reduced strength; may not carry a must-have competency alone
- **Contradicted** — evidence disagrees; severity assigned; resolution attempted; flagged

### 7.4 Minimum source standards by seniority

| Seniority | Minimum independent groups on must-have competencies | Mandatory E4+ evidence |
|---|---|---|
| Fresher / entry | 2 | 1 (assessment or structured live probe) |
| 2–5 years | 2 | 1 |
| 5–10 years | 3 | 1, plus 1 reference or verified artefact |
| 10+ / leadership | 3 | 1 live structured probe + 2 references |
| Executive / CXO | 4 | Structured executive interview + ≥2 referenced corroborations + verifiable scope evidence |

### 7.5 Verification methods register

| Verification method | Produces tier | Applicable to |
|---|---|---|
| Issuing-body credential check | E5 | Licences, certifications, degrees |
| Employment verification (dates, title, scope) | E5 | Employment claims |
| Structured reference on a specific claim | E5 | Ownership, impact, behaviour |
| Signed-commit / sustained-authorship analysis | E5 | Code contribution |
| Unsigned repository analysis | E2–E3 | Code contribution (author fields are trivially spoofable — treat as unverified) |
| Live proctored assessment | E4 | Applied skill |
| Live unprepared probe on a claimed artefact | E4 | Depth of ownership |
| Work simulation / job sample | E4 | Applied skill under realistic conditions |
| Take-home / async assessment | E3 | Applied skill (assume AI assistance; score process not output) |
| Validation questionnaire | E3 | Claim specificity |
| Candidate-supplied artefact | E2 | Output quality |
| Public profile scrape | E1 | Context only |

---

## 8. Reading claims correctly

### 8.1 The impact test

For any achievement claim, four questions determine whether it is impact or activity:

1. **Attribution** — What exactly did *this person* do, as distinct from the team?
2. **Counterfactual** — What would have happened without them?
3. **Baseline** — What was the starting state, and how is it known?
4. **Durability** — Did the result persist after they left?

A claim that survives all four is strong Impact evidence. A claim that survives none is a sentence.

### 8.2 The quantification trap

"Increased revenue 40%" is not automatically stronger than "ran the pricing revision that moved gross margin, though I can't share the numbers." AI-written resumes are saturated with plausible, unverifiable numbers. Our rule:

> **A number with no reconstructable baseline is weaker evidence than a mechanism the candidate can explain.**

Validation instruments therefore probe *mechanism*, not magnitude: how was it measured, what was the baseline, what else was changing at the time, what would have happened anyway.

### 8.3 Scope vs. title

Titles inflate and deflate by company. A "Manager" at a 40-person startup and a "Manager" at a 40,000-person enterprise are not comparable. We normalise on **scope evidence**: people managed (direct/indirect), budget or P&L owned, systems owned, decision rights held, blast radius of failure. Title is context, not a score input.

### 8.4 Tenure reading

Short tenures are neither good nor bad in isolation. The signal is in the **pattern and its explanation**:

- Multiple short tenures with coherent, verifiable reasons (funding collapse, acquisition, contract role, relocation) → neutral
- Multiple short tenures with vague or shifting explanations → mild risk signal, probe further
- Long tenure with no scope growth → probe for stagnation vs. deep expertise; do not assume either
- Long tenure with scope growth → strong Trajectory evidence

Tenure rules that a client wishes to apply are Layer 2 configuration and must be declared explicitly, not applied by recruiter instinct.

### 8.5 Freshers and the evidence problem

Freshers have no employment track record, so an evidence model built for experienced hires will systematically undervalue them. Part VIII defines the fresher variant in full. The core adjustment: for freshers, **Track Record & Impact is reduced or suspended and its weight redistributed** to Verified Competence and Trajectory, and academic/project/internship evidence is elevated in tier when it can be probed live.

### 8.6 Career breaks

A break is a gap in *employment*, not in *capability*. Handling:

- Record the break; do not score it
- Ask one neutral, respectful question about it (client-configurable whether asked at all)
- If the break involved relevant activity (study, caregiving with transferable coordination work, freelance, health recovery), accept it as context
- Probe **currency** of technical skill only where the domain clock is fast-moving, and probe it the same way you would for a continuously employed candidate

Breaks may never function as a disqualifier. Any client request to filter on gaps is refused under §3.5.

### 8.7 Transferable skills and adjacency

An adjacency claim ("I haven't used Kafka but I've run RabbitMQ at scale") is legitimate evidence, at reduced strength, when three conditions hold: the underlying concepts genuinely transfer; the candidate can articulate what would differ; and there is prior evidence of the candidate successfully making a similar transition before. The third condition is the discriminator — demonstrated learning transitions are the best predictor of future ones.

### 8.8 The "no GitHub" problem

Many excellent engineers have no public code: enterprise employers prohibit it, consultancies own the IP, and open-source participation correlates with free time, which correlates with life circumstances and therefore with demographics. **Absence of a public repository is never negative evidence.** It creates a gap that must be filled by another route: live coding, architecture probe, work simulation, or a walkthrough of a system they cannot show but can explain. Departments where this applies list the substitution routes explicitly (§22).

### 8.9 The pedigree cap

Institutional prestige (college tier, employer brand) is *weak, indirect* evidence of capability and *strong* evidence of prior access. It correlates heavily with socioeconomic background and therefore imports bias directly into the score.

**Rule:** institutional pedigree may contribute to at most **5% of the total Ready Pick Score**, may only contribute to the Trajectory dimension, and may never appear in a threshold or disqualifier. A client may raise this ceiling only by explicit written Layer 2 configuration, which is recorded and surfaced in the fairness audit. A client may lower it to zero freely.

**Corollary:** where a client's Layer 2 declares a target-school or target-company list, that list is applied as a *sourcing* preference (where we look), never as an *evaluation* criterion (how we score). This distinction is enforced in the engine.

---

# PART III — THE FIVE DIMENSIONS AND THE SCORING ENGINE

## 9. The five dimensions

### 9.1 D1 — Verified Competence

**Question:** *Can this person actually do the work this role requires?*

**What it measures:** demonstrated, corroborated ability against the must-have competencies on the approved scorecard — not claimed ability, not adjacent ability, not potential ability.

**Sub-signals (universal):**
- Depth on the core competency set (as defined by the department model)
- Applied performance under observation
- Reasoning quality: can they explain *why*, handle ambiguity, and identify what they don't know
- Tool and method fluency at the level the role requires
- Relevant credential or licence where the role genuinely gates on it

**Evidence types that count:** live assessment, work simulation, structured technical probe, verified artefact with sustained authorship, credential verification, reference corroboration on specific technical claims.

**Evidence types that do not count on their own:** skill lists, self-rated proficiency, keyword presence, certification claims that have not been verified, portfolio artefacts without process explanation.

**Scoring anchors (0–100):**

| Band | Meaning |
|---|---|
| 90–100 | Demonstrated mastery across all must-haves under observation; can teach it; reasoning is fluent under pressure and ambiguity |
| 75–89 | Solid demonstrated capability on all must-haves; minor gaps in one non-critical area; reasoning sound |
| 60–74 | Demonstrated on most must-haves; one must-have is only partially evidenced; would need support |
| 45–59 | Mixed: claimed broadly, demonstrated narrowly; material gaps in must-haves |
| 25–44 | Little demonstrated capability beyond self-report; assessment performance weak or shallow |
| 0–24 | Contradicted or absent capability on core requirements |

### 9.2 D2 — Track Record & Impact

**Question:** *What has changed in the world because this person did their job?*

**What it measures:** the outcomes attributable to the candidate, at a scope and in a context relevant to this role.

**Sub-signals:**
- Attributable outcomes (passes the four-question impact test, §8.1)
- Ownership depth (owned vs. contributed vs. participated)
- Scope: people, budget, systems, decision rights
- Difficulty and constraint of the environment in which the outcome was achieved
- Durability of the outcome
- Consistency of impact across roles, not a single career highlight

**Special handling:** for freshers, D2 is suspended or heavily reduced (§8.5, Part VIII). For roles where individual attribution is genuinely impossible (large-team infrastructure, regulated environments), D2 shifts toward *demonstrated contribution to a documented outcome* rather than personal attribution.

**Scoring anchors (0–100):**

| Band | Meaning |
|---|---|
| 90–100 | Repeated, corroborated, attributable outcomes at or above role scope, in comparable difficulty; durable |
| 75–89 | Clear corroborated ownership of significant outcomes; scope broadly matches role |
| 60–74 | Real contributions with partial attribution clarity; scope somewhat below role |
| 45–59 | Activity is documented; outcomes are asserted but not corroborated |
| 25–44 | Participation only; no evidence of ownership or outcome |
| 0–24 | Claims contradicted, or a track record inconsistent with claimed seniority |

### 9.3 D3 — Role & Context Fit

**Question:** *Is this person right for THIS hiring problem, at THIS company, right now?*

This is where the SWOT does its work. It is the dimension most systems omit entirely, and it is the reason two identically capable candidates can be correctly ranked far apart.

**Sub-signals:**
- Alignment to the SWOT-derived priority competencies (the gap this hire must close)
- Situation match: turnaround vs. scale-up vs. steady-state vs. greenfield vs. maintenance
- Operating-environment match: structure, ambiguity tolerance, resourcing level, process maturity
- Stakeholder and collaboration model match
- Practical constraints: location, notice period, compensation band, work model, travel, shift
- Company-DNA behaviours (Layer 2), expressed as observable evidence

**Critical discipline:** "culture fit" is banned as a criterion. It is unobservable, unfalsifiable, and a well-documented vector for homophily bias. It is replaced by **observable working-context evidence**: "has operated effectively with no dedicated QA function," "has delivered under a regulated approval cycle," "has built a function from zero rather than inheriting one."

**Scoring anchors (0–100):**

| Band | Meaning |
|---|---|
| 90–100 | Directly addresses the top SWOT-derived priority with corroborated evidence; environment and constraints align cleanly |
| 75–89 | Addresses top priority well; one secondary mismatch that is manageable |
| 60–74 | Generally suitable; the specific gap this hire must close is only partially covered |
| 45–59 | Capable person, but not aimed at this hiring problem; or a material practical constraint |
| 25–44 | Substantial mismatch on situation or environment |
| 0–24 | Fundamental mismatch; or a declared constraint is unmet |

### 9.4 D4 — Authenticity & Consistency

**Question:** *Does the candidate's story hold together across independent sources?*

This dimension is Ready Pick Now's defining addition. It is not a cheating detector. It is a **coherence measure**.

**Sub-signals:**
- Cross-source consistency on material claims
- Specificity under unprepared probing
- Artefact authenticity (authorship, sustained contribution, provenance)
- Reference corroboration alignment
- Assessment-process integrity signals (routed to human review, never auto-scored to rejection)
- Self-representation accuracy: does claimed seniority match demonstrated depth
- Disclosure behaviour: candidates who accurately describe their own limits score *higher*, not lower

**Explicitly not measured:** whether the candidate used AI. Using AI to draft a resume is normal professional behaviour in 2026. What we measure is whether the underlying capability exists. A candidate who used AI to write a beautiful resume *and* can demonstrate everything on it scores highly on D4.

**Scoring anchors (0–100):**

| Band | Meaning |
|---|---|
| 90–100 | Every material claim corroborated across independent groups; specificity high under live probing; artefacts authentic; references align precisely |
| 75–89 | Strong overall consistency; one minor unexplained variance |
| 60–74 | Mostly consistent; some claims uncorroborated; specificity thin in places |
| 45–59 | Material claims rest on self-report; probing produced generic answers; or one moderate unresolved contradiction |
| 25–44 | Multiple contradictions; artefacts of doubtful authorship; references do not match claims |
| 0–24 | Severe contradiction or verified misrepresentation → integrity flag, mandatory human review, candidate not delivered without HR Manager decision |

### 9.5 D5 — Trajectory & Potential

**Question:** *What does this person's growth pattern suggest they will be able to do?*

**Sub-signals:**
- Rate and direction of scope expansion over time
- Demonstrated successful transitions into adjacent domains
- Learning behaviour: how they acquired the last major skill, and how fast
- Self-direction and initiative evidence
- Ceiling indicators: complexity of problems they now handle unaided
- Curiosity and depth-seeking under probing

**Weighting logic:** highest for freshers (little else to go on) and for senior/executive hires (where the role will change under them). Lowest for mid-level specialist roles hired to do a defined job now.

**Scoring anchors (0–100):**

| Band | Meaning |
|---|---|
| 90–100 | Consistent scope expansion; multiple successful domain transitions; evidence of self-directed mastery; handles problems above current title |
| 75–89 | Clear upward trajectory; one demonstrated successful transition |
| 60–74 | Steady growth within a lane; adequate learning evidence |
| 45–59 | Flat trajectory; capability acquired early and not extended |
| 25–44 | Declining scope, or repeated lateral moves without depth accumulation |
| 0–24 | No growth evidence; or pattern inconsistent with claimed seniority |

### 9.6 Why exactly five

Fewer than five collapses distinctions that matter (competence and impact are genuinely different; authenticity cannot be folded into competence without destroying it). More than five produces dimensions that correlate so highly they add noise, not signal, and that recruiters cannot hold in their heads. Five is the point where the model is complete enough to be correct and small enough to be used. Per Axiom 14, any proposal to add a sixth must demonstrate incremental predictive validity in calibration.

---

## 10. The scoring mathematics

### 10.1 The pipeline

```
Evidence  →  Competency scores  →  Dimension scores  →  Weighted composite
                                          ↓
                            Gates, thresholds, disqualifiers
                                          ↓
                              Confidence computation
                                          ↓
                          READY PICK SCORE + CONFIDENCE + BAND
```

### 10.2 Competency score

For each competency `k` on the approved scorecard:

```
Comp(k) = Σ over claims c mapped to k of [ rubric_level(c) × S_final(c) ]
          ────────────────────────────────────────────────────────────
                        Σ over claims c of S_final(c)

where rubric_level(c) ∈ [0,100] is the department rubric's rating of
the demonstrated level, and S_final(c) is the evidence strength from §6.5.
```

In words: **the rubric says how good it looks; the evidence strength says how much we should believe it.** A dazzling claim with weak evidence and a modest claim with strong evidence can land in the same place — and that is the intended behaviour.

If no claim maps to `k`: `Comp(k) = UNKNOWN` (see §6.6), and `k` is dropped from the dimension average with renormalisation.

### 10.3 Dimension score

```
D_i = Σ over competencies k assigned to dimension i of [ w_k × Comp(k) ]
      ──────────────────────────────────────────────────────────────
                   Σ over k with Comp(k) ≠ UNKNOWN of w_k
```

Where `w_k` is the intra-dimension competency weight from the scorecard force-ranking.

### 10.4 The composite

```
RPS_raw = Σ over i=1..5 of ( W_i × D_i )     where Σ W_i = 1.0
```

`W_i` is the **active weight vector**: the department × seniority baseline, modified by Layer 2 and Layer 3 within bounds, then normalised (§11).

### 10.5 The authenticity multiplier — why D4 is not merely additive

Treating authenticity as one weighted dimension among five is insufficient. A candidate with severe unresolved contradictions should not be rescued by strong scores elsewhere; in fact, strong scores elsewhere are exactly what is in doubt when authenticity is compromised.

D4 therefore acts **twice**: once as a weighted dimension, and once as a multiplier on the composite.

```
authenticity_multiplier =
     1.00                    if D4 ≥ 75
     0.90 + (D4−60)×0.0067   if 60 ≤ D4 < 75      (0.90 → 1.00)
     0.70 + (D4−45)×0.0133   if 45 ≤ D4 < 60      (0.70 → 0.90)
     0.50 + (D4−25)×0.0100   if 25 ≤ D4 < 45      (0.50 → 0.70)
     HOLD — not scored, mandatory human review    if D4 < 25

RPS = RPS_raw × authenticity_multiplier
```

**Note the asymmetry, and it is deliberate:** high authenticity does not inflate a score above what the evidence supports (multiplier caps at 1.00). Low authenticity suppresses it. Authenticity is a *licence to believe the other four dimensions*, not a fifth way to win.

### 10.6 The uncertainty band

A single number implies a precision we do not have. Every RPS is reported with a band derived from confidence:

```
band_width = 20 × (1 − confidence_score)      (confidence_score ∈ [0,1])
Reported as:  RPS 78  [range 71–85, Moderate confidence]
```

Two candidates whose bands overlap are reported as **not meaningfully separated**, and the dossier says so explicitly. This single practice eliminates the most common false precision in AI hiring products: ranking #4 above #5 on a two-point difference that the evidence cannot support.

### 10.7 Confidence computation

```
confidence_score = 0.35 × evidence_coverage
                 + 0.30 × evidence_depth
                 + 0.20 × independence
                 + 0.15 × consistency

evidence_coverage = (must-have competencies with any evidence > E1) / (total must-haves)
evidence_depth    = mean over must-haves of ( best tier strength achieved )
independence      = min(1, mean independence group count on must-haves / 3)
consistency       = 1 − (weighted unresolved contradiction severity)
```

Mapped to labels:

| confidence_score | Label | Delivery implication |
|---|---|---|
| ≥ 0.80 | **High** | Deliverable; recommend for interview |
| 0.60–0.79 | **Moderate** | Deliverable; dossier states what would raise confidence |
| 0.40–0.59 | **Low** | Deliverable only with explicit label + recruiter note; not counted toward guaranteed shortlist size |
| < 0.40 | **Insufficient** | Not delivered. Collect more evidence or exclude with reason. |

### 10.8 Score bands

| RPS | Band | Meaning to the client |
|---|---|---|
| 85–100 | **Ready to Pick — Strong** | Strong corroborated evidence against this specific hiring problem |
| 72–84 | **Ready to Pick** | Solid evidence; specific known gaps stated |
| 60–71 | **Consider with reservations** | Genuine candidate; material gap or unresolved question stated |
| 45–59 | **Not recommended for this role** | May be strong for a different problem; explain which |
| < 45 | **Not recommended** | — |
| HOLD | **Integrity review** | Not ranked pending human disposition |

### 10.9 The Top-10 rule

When a client asks for "the best ten," we do **not** simply take rows 1–10.

```
1. Exclude HOLD and Insufficient-confidence candidates.
2. Take all candidates in "Ready to Pick — Strong".
3. Fill from "Ready to Pick", ordered by RPS.
4. STOP at the point where band overlap makes ordering arbitrary,
   and report the remainder as a tied cluster rather than a false ranking.
5. If fewer than 10 candidates qualify, DELIVER FEWER AND SAY WHY.
   Padding a shortlist with candidates we do not believe in is the single
   fastest way to destroy the promise this product is built on.
6. Deliberately include, where they exist, up to 2 "high-variance" candidates:
   strong D1/D5, non-standard background, lower surface fit — labelled as such.
   Rationale in §10.10.
```

### 10.10 The exploration slot

A purely exploitative ranking converges on the same profile shape forever and never learns whether the weights are wrong. Two labelled exploration slots per shortlist serve three purposes: they surface genuinely strong non-traditional candidates who would otherwise be filtered by proxy; they generate the outcome data needed to calibrate weights (§46–§47); and they protect against the model quietly encoding a single template of "good." The client is told explicitly that these are exploration candidates and why they were included.

### 10.11 Worked example

Role: Senior Backend Engineer. SWOT reveals the team's real gap is cloud/infrastructure ownership ahead of a six-month migration.

Active weights: D1 0.34 · D2 0.19 · D3 0.22 · D4 0.20 · D5 0.05

**Candidate A** — immaculate resume, keyword-perfect, four polished projects.

| Dim | Score | Basis |
|---|---|---|
| D1 | 61 | Assessment: correct outputs, but architecture probe shallow; could not explain trade-offs of chosen approach |
| D2 | 48 | Outcomes asserted, attribution unclear ("we"), no corroboration |
| D3 | 55 | Keyword alignment high; no evidence of production infrastructure ownership — the actual gap |
| D4 | 52 | Repos created recently, no sustained history; validation answers generic; one moderate contradiction unresolved |
| D5 | 60 | Some growth; no demonstrated domain transition |

RPS_raw = 0.34(61)+0.19(48)+0.22(55)+0.20(52)+0.05(60) = 20.7+9.1+12.1+10.4+3.0 = **55.3**
Authenticity multiplier at D4=52 → 0.70+(52−45)(0.0133) = 0.793
**RPS = 43.9** — Not recommended. Confidence: Moderate (0.66). Band 50 ± 7.

**Candidate B** — plainer resume, fewer keywords.

| Dim | Score | Basis |
|---|---|---|
| D1 | 82 | Live probe: explained rollback strategy, blast-radius reasoning, and what they got wrong |
| D2 | 79 | Reference from former EM corroborated ownership and scope of a real migration |
| D3 | 88 | Directly closes the SWOT gap: has owned exactly this migration shape at comparable scale |
| D4 | 91 | Resume ↔ validation ↔ assessment ↔ reference all consistent; sustained verified commit history |
| D5 | 71 | Steady scope growth; one successful transition (ops → platform) |

RPS_raw = 0.34(82)+0.19(79)+0.22(88)+0.20(91)+0.05(71) = 27.9+15.0+19.4+18.2+3.6 = **84.1**
Authenticity multiplier = 1.00 → **RPS = 84.1** — Ready to Pick. Confidence: High (0.86). Band 84 ± 3.

A keyword-similarity system ranks A above B. Ready Pick Now ranks B forty points above A, and can show a client exactly which six pieces of evidence produced the difference.

---

## 11. Weight vectors

### 11.1 Baseline matrix (Layer 1)

Baselines are Ready Pick Now's professional judgement, expressed as calibration hypotheses (Axiom 13). They are revised only on calibration evidence. Values are D1/D2/D3/D4/D5.

**IT & Software**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Fresher | 0.40 | 0.05 | 0.15 | 0.20 | 0.20 |
| 2–5 yrs | 0.36 | 0.16 | 0.16 | 0.22 | 0.10 |
| 5–10 yrs | 0.32 | 0.22 | 0.18 | 0.20 | 0.08 |
| 10+ / Principal | 0.26 | 0.26 | 0.22 | 0.18 | 0.08 |
| Eng leadership | 0.18 | 0.30 | 0.26 | 0.16 | 0.10 |

**Mechanical / Electrical / Civil / Manufacturing**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Fresher | 0.42 | 0.05 | 0.16 | 0.17 | 0.20 |
| 2–5 yrs | 0.38 | 0.17 | 0.18 | 0.17 | 0.10 |
| 5–10 yrs | 0.34 | 0.23 | 0.20 | 0.15 | 0.08 |
| 10+ | 0.28 | 0.28 | 0.22 | 0.14 | 0.08 |
| Engineering leadership | 0.20 | 0.30 | 0.26 | 0.14 | 0.10 |

**Data & Analytics / R&D**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Fresher | 0.40 | 0.04 | 0.14 | 0.20 | 0.22 |
| 2–5 yrs | 0.36 | 0.15 | 0.16 | 0.22 | 0.11 |
| 5–10 yrs | 0.32 | 0.22 | 0.18 | 0.20 | 0.08 |
| 10+ | 0.26 | 0.26 | 0.22 | 0.18 | 0.08 |

**Design / UX / Architecture**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Fresher | 0.38 | 0.06 | 0.16 | 0.20 | 0.20 |
| 2–5 yrs | 0.34 | 0.16 | 0.18 | 0.22 | 0.10 |
| 5–10 yrs | 0.30 | 0.22 | 0.20 | 0.20 | 0.08 |
| 10+ / Principal | 0.26 | 0.24 | 0.24 | 0.18 | 0.08 |

**Finance & Accounting**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Fresher | 0.40 | 0.05 | 0.15 | 0.20 | 0.20 |
| 2–5 yrs | 0.36 | 0.16 | 0.16 | 0.22 | 0.10 |
| 5–10 yrs | 0.30 | 0.24 | 0.18 | 0.20 | 0.08 |
| 10+ / Controller | 0.24 | 0.28 | 0.22 | 0.18 | 0.08 |
| CFO | 0.16 | 0.32 | 0.26 | 0.16 | 0.10 |

**Leadership / Executive / General Management**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| First-line manager | 0.26 | 0.24 | 0.22 | 0.18 | 0.10 |
| Senior manager | 0.22 | 0.28 | 0.22 | 0.16 | 0.12 |
| Director / VP | 0.16 | 0.30 | 0.26 | 0.14 | 0.14 |
| CXO | 0.12 | 0.32 | 0.28 | 0.12 | 0.16 |

**HR**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Executive / Generalist | 0.36 | 0.14 | 0.20 | 0.20 | 0.10 |
| Manager / BP | 0.28 | 0.24 | 0.22 | 0.16 | 0.10 |
| Head of HR / CHRO | 0.16 | 0.30 | 0.28 | 0.14 | 0.12 |

**Sales / Marketing / BD**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Entry | 0.32 | 0.08 | 0.20 | 0.22 | 0.18 |
| Mid | 0.24 | 0.30 | 0.20 | 0.18 | 0.08 |
| Leadership | 0.18 | 0.34 | 0.24 | 0.14 | 0.10 |

**Operations / Supply Chain**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Entry–Mid | 0.36 | 0.18 | 0.18 | 0.18 | 0.10 |
| Senior | 0.28 | 0.26 | 0.22 | 0.16 | 0.08 |
| Leadership | 0.20 | 0.30 | 0.26 | 0.14 | 0.10 |

**Skilled trades / Blue-collar / Non-technical support**

| Seniority | D1 | D2 | D3 | D4 | D5 |
|---|---|---|---|---|---|
| Entry | 0.44 | 0.06 | 0.20 | 0.22 | 0.08 |
| Experienced | 0.40 | 0.14 | 0.22 | 0.18 | 0.06 |
| Supervisory | 0.32 | 0.22 | 0.24 | 0.16 | 0.06 |

*Note on trades:* D4 remains high not because these candidates are more likely to misrepresent — they are not — but because reliability and credential corroboration are the highest-value verifiable signals in these roles, and they live in D4 and D3.

### 11.2 Layer 2 modifiers (company)

The client's Company DNA may shift weights within declared bounds:

| Company declaration | Effect | Bound |
|---|---|---|
| "We value proven delivery over potential" | D2 ↑, D5 ↓ | ±0.06 |
| "We hire for potential and train" | D5 ↑, D2 ↓ | ±0.08 |
| "We are highly practical; credentials matter little" | Shifts within D1 sub-signals | n/a to W |
| "We have limited onboarding capacity" | D1 ↑, D5 ↓ | ±0.05 |
| "Our environment is highly ambiguous / early-stage" | D3 ↑, D5 ↑ | ±0.05 each |
| "We are regulated and audit-sensitive" | D4 ↑ | +0.06 max |

### 11.3 Layer 3 modifiers (role SWOT)

| SWOT-derived condition | Effect | Bound |
|---|---|---|
| Hire must close a specific named capability gap | D3 ↑, D1 ↑ | +0.08 combined |
| Hire is a turnaround / crisis mandate | D2 ↑, D3 ↑ | +0.08 combined |
| Hire is greenfield / zero-to-one | D5 ↑, D3 ↑ | +0.07 combined |
| Role will change substantially within a year | D5 ↑ | +0.06 |
| Role is a defined, stable execution seat | D1 ↑, D5 ↓ | ±0.06 |
| High-trust / high-blast-radius role | D4 ↑ | +0.06 |

### 11.4 Normalisation and clamping rules

1. Apply L2 modifiers to the L1 baseline.
2. Apply L3 modifiers.
3. Clamp each `W_i` to its floor and ceiling: **no dimension may fall below 0.05 or rise above 0.40.** No dimension is ever zero — a dimension weighted zero is a dimension nobody is accountable for.
4. **D4 floor is 0.12 and cannot be lowered by any client.** Authenticity is a Layer 1 integrity property.
5. Renormalise so `Σ W_i = 1.0`.
6. Record the final vector, its derivation, and who requested each modifier, in the frozen configuration.

### 11.5 Weight transparency

The active weight vector is shown to the client in every dossier. A client who cannot see the weights cannot dispute them, and a weight nobody disputes is a weight nobody has validated.

---

## 12. Thresholds, gates and disqualifiers

### 12.1 Four control types

| Control | Behaviour | Who sets it | Automatic? |
|---|---|---|---|
| **Hard disqualifier** | Candidate excluded from consideration | HR Manager approves only | Yes, logged and reviewable |
| **Competency threshold** | Minimum score on a named competency; failure caps the band | Hiring Manager proposes, HR Manager approves | Yes |
| **Dimension floor** | Minimum score on a dimension; failure caps the band | Layer 1 defaults + Layer 3 | Yes |
| **Review trigger** | Routes to a human queue | Layer 1 and Layer 3 | Routes only, never rejects |

### 12.2 Layer 1 default dimension floors

| Dimension | Floor | Effect if breached |
|---|---|---|
| D1 | 45 | Cannot exceed "Consider with reservations" |
| D4 | 45 | Cannot exceed "Consider with reservations" |
| D4 | 25 | HOLD — mandatory human review before any delivery |
| D3 | 40 | Cannot be delivered as Ready to Pick for this role; may be flagged for a different role |

### 12.3 Legitimate hard disqualifiers

A hard disqualifier is only legitimate when it is: (a) objective and binary, (b) genuinely non-negotiable for the role, (c) not a proxy for a protected characteristic, and (d) declared before sourcing.

Acceptable examples:
- Absence of a licence legally required to perform the work (e.g. a licence required to sign off statutory drawings, a valid commercial driving licence for a driving role, a required trade certification)
- Absence of legal work authorisation for the location
- Verified misrepresentation of a credential or employment record
- Failure to meet a statutory minimum qualification for a regulated role
- Unwillingness to accept a declared, published, non-negotiable condition of the role (shift pattern, travel requirement, on-site requirement) after being informed of it

### 12.4 Prohibited disqualifiers

The following are refused regardless of client request, under §3.5:

- Employment gaps of any length
- Age, or proxies for it (graduation year filters, "digital native," "high-energy," maximum experience caps)
- Marital or parental status; caregiving history
- Gender, and proxies (gendered role language, "culture fit" with a homogeneous team)
- Caste, religion, region, mother tongue, or any proxy for them (including native-language requirements not genuinely required for the work, or locality filters that map to community concentration)
- Disability, or proxies (blanket physical requirements not tied to essential job functions)
- Institutional pedigree as a filter (§8.9)
- Current or previous salary as a filter or as a ranking input
- Health status, other than where a statutory occupational health requirement applies and is applied uniformly
- Any characteristic protected under applicable law in the hiring jurisdiction

Requests for prohibited disqualifiers are logged, refused, escalated to the client's HR Manager, and reported in the quarterly fairness audit. Repeated requests are a commercial escalation.

### 12.5 The soft-constraint approach

Most things clients want as disqualifiers are better modelled as **weighted constraints**. "Must be in Hyderabad" becomes a D3 constraint that a willing-to-relocate candidate can partially satisfy, with the relocation risk stated. This preserves the client's intent while preventing the loss of strong candidates to a blunt filter — and it makes the trade-off visible rather than invisible.

---

## 13. Contradiction handling

### 13.1 The taxonomy

| Type | Description | Default severity |
|---|---|---|
| **T1 — Depth contradiction** | Claimed expertise not supported by demonstrated depth | Moderate |
| **T2 — Ownership contradiction** | Claimed ownership contradicted by reference or artefact | Moderate–Severe |
| **T3 — Timeline contradiction** | Dates, tenures or sequences that do not reconcile | Minor–Severe (severity rises if unexplained after probe) |
| **T4 — Artefact contradiction** | Work product provenance inconsistent with claim (e.g. repository created after the claimed project period; portfolio work attributable to another author) | Severe |
| **T5 — Credential contradiction** | Credential not verifiable, lapsed, or not as claimed | Severe → Disqualifying if misrepresentation confirmed |
| **T6 — Scale contradiction** | Claimed scope inconsistent with verifiable organisational reality | Moderate |
| **T7 — Consistency-of-self contradiction** | Candidate's account changes materially across interactions | Moderate |
| **T8 — Assessment-integrity signal** | Process anomalies during assessment | Review trigger only — never scored directly |

### 13.2 The resolution protocol

Contradictions must be **worked**, not merely recorded. The protocol is mandatory before any severity above Minor is finalised.

```
STEP 1 — RESTATE
  Express the contradiction precisely. "Resume says X; reference says Y."
  Reject vague discomfort ("something feels off") — it is not admissible.

STEP 2 — CHECK OUR OWN DATA
  Parsing errors, date-format errors, name collisions, and translation
  artefacts cause a large share of apparent contradictions.
  Rule out our error before attributing to the candidate.

STEP 3 — BENIGN-EXPLANATION SEARCH
  Enumerate at least two innocent explanations before any adverse reading.
  Company renamed. Team restructured. Title differs from function.
  Contract-to-permanent conversion. Confidentiality restriction.
  NDA on the artefact. Regional title conventions.

STEP 4 — TARGETED PROBE
  Ask the candidate directly, neutrally, and specifically.
  Not: "There's an inconsistency in your background."
  But: "Your resume lists the migration as 2024; your reference placed it
  in 2023 — can you help me get the timeline right?"
  Candidates resolve most contradictions in one sentence.
  DENYING THE CANDIDATE THE CHANCE TO EXPLAIN IS A PROCESS DEFECT.

STEP 5 — DISPOSITION
  Resolved-benign        → contradiction closed, no penalty, logged
  Resolved-adverse       → severity assigned, penalty applied, logged
  Unresolved             → severity assigned at one level below the
                            adverse reading, flagged, human review
  Candidate unreachable  → severity capped at Minor; recorded as
                            "unresolved — no opportunity to respond"

STEP 6 — RECORD
  Every step above is written to the evidence graph.
```

### 13.3 Never average contradicting evidence

If the assessment says 40 and the resume says 90, the answer is not 65. The answer is: *there is a depth contradiction; the assessment is E4 and the resume is E0; the assessment governs the score; the contradiction is recorded, probed, and reported.* Averaging is prohibited by Axiom 6 and is a scoring defect.

### 13.4 Evidence precedence when sources conflict

When two sources genuinely conflict and both survive Step 3:

1. Higher tier wins (E5 > E4 > E3 > E2 > E1 > E0)
2. At equal tier, more recent underlying event wins
3. At equal tier and recency, the source with no incentive to misrepresent wins
4. At equal everything, both are reported and confidence is reduced — we do not manufacture a resolution

### 13.5 The false-positive obligation

Every flag we raise carries a cost to a real person's employment prospects. We therefore track flag precision (§46) and hold ourselves to it. **If confirmed-issue precision on any flag type falls below 60%, that flag type's threshold is raised or the flag is retired.** A detector that is wrong more often than it is right is not a safety feature; it is a harm generator.

---

## 14. Confidence, abstention and escalation

### 14.1 When the system must abstain

Ready Pick Now declines to rank — and says so — under any of:

| Condition | Action |
|---|---|
| Confidence < 0.40 | Not delivered; evidence collection continues or candidate excluded with stated reason |
| A must-have competency has no evidence above E1 | Competency reported as Unassessed; candidate cannot be Ready to Pick |
| D4 < 25 | HOLD; mandatory human disposition |
| Score bands of adjacent candidates overlap | Report as tied cluster; refuse to order them |
| Fewer than the minimum independent groups for the seniority tier | Downgrade confidence; report the shortfall |
| The scorecard was not approved | Scoring blocked entirely (Gate G1) |
| Configuration changed mid-search without re-scoring | Scoring blocked pending re-run |
| A competency in the scorecard has no defined assessment route | Configuration rejected back to the recruiter |

### 14.2 Escalation ladder

```
Automated evaluation
      ↓ (flag / low confidence / contradiction / override request)
Delivery pod recruiter review
      ↓ (integrity flag / disqualifier dispute / fairness concern)
Client HR Manager
      ↓ (refused configuration / prohibited disqualifier / systemic pattern)
Ready Pick Now Standards Board
```

### 14.3 What "confidence" is communicated as

Never as a probability of success — we have not earned that claim. Always as *strength of the evidence base*:

> "High confidence — every must-have competency is corroborated across at least two independent sources, including a live demonstration."

> "Low confidence — strong assessment performance, but we could not verify the claimed team-leadership scope; two reference requests went unanswered. Ask about this directly in your first interview."

The second statement is more valuable to a client than a confident number would be, and it is honest.

---

# PART IV — LAYER 2: THE COMPANY HIRING PHILOSOPHY

*Captured once per client. Reusable across every role. This is the client's hiring DNA, compiled into engine configuration.*

## 15. Purpose and principle

Most hiring platforms treat client preference as free text shown to a model. That fails silently: the model may or may not apply it, differently each run, with no audit trail. Ready Pick Now compiles Company DNA into **declared, bounded, versioned configuration** — every statement the client makes must map to a concrete engine effect, or it is not captured.

**The compilation rule:** *If a client statement cannot be expressed as (a) a weight modifier, (b) an evidence requirement, (c) a threshold, (d) a disqualifier, (e) a sourcing instruction, or (f) a dossier presentation preference — it is context for the recruiter, not configuration for the engine, and it is labelled as such.*

## 16. The Company DNA Intake Instrument

Administered to the HR Manager / CHRO in a 90-minute structured session. Twelve sections.

### 16.1 Section 1 — Organisational context

| Field | Why it matters |
|---|---|
| Headcount, growth rate, funding/ownership stage | Sets the "environment match" reference for D3 |
| Industry and regulatory exposure | Drives D4 floor, background-check requirements, credential logic |
| Locations and work model per location | D3 practical constraints |
| Attrition rate and where it concentrates | Informs risk register and calibration expectations |
| Time-to-hire reality (not aspiration) | Sets evidence-collection depth achievable |
| Interview capacity per role per week | Determines shortlist size that is actually usable |

### 16.2 Section 2 — Evaluation philosophy

Each answered on a forced scale, not free text.

| Question | Scale | Maps to |
|---|---|---|
| Proven delivery vs. potential | 1 (all proven) — 5 (heavy potential) | D2/D5 modifier |
| Specialist depth vs. generalist range | 1–5 | Competency weighting within D1 |
| Credentials vs. demonstrated practice | 1–5 | Evidence tier preferences within D1 |
| Stability vs. velocity of prior moves | 1–5 | Tenure reading rules (§8.4) |
| Internal training capacity | 1–5 | D1/D5 modifier |
| Tolerance for non-traditional backgrounds | 1–5 | Exploration slot count; pedigree cap |

### 16.3 Section 3 — What "good" looks like here, as observable evidence

The client names **five to eight behaviours** their strongest performers demonstrably show. Each must be written as observable evidence, not as a trait.

> Rejected: "Ownership mindset."
> Accepted: "Has taken a project from unclear brief to shipped outcome without a defined process being handed to them, and can describe the decisions they made when nobody told them what to do."

> Rejected: "Team player."
> Accepted: "Has worked in a matrixed structure where they had responsibility without authority, and can describe how they secured commitment from people who did not report to them."

The recruiter is responsible for enforcing this conversion. It is the single highest-leverage part of the whole intake, because unobservable criteria are exactly where bias enters.

### 16.4 Section 4 — What fails here

The mirror question, and often more informative: *describe two or three people who looked strong on paper and did not work out. What was the actual failure mode?* Failure modes convert into **risk probes** in the validation instrument and into risk-register items in the dossier.

### 16.5 Section 5 — Non-negotiables and constraints

- Statutory and policy requirements (background verification, drug screening where lawful and applied uniformly, licensure)
- Notice-period tolerance
- Compensation bands by level, and flexibility
- Location / relocation / work-model rules
- Any genuinely binary requirement, each tested against §12.3

### 16.6 Section 6 — Process shape

- Number and type of interview stages the client will actually run
- Who makes the final decision
- Whether the client will run their own assessments (avoid duplication)
- Turnaround commitments — because a slow client process changes which candidates remain available, and the dossier should say so

### 16.7 Section 7 — Diversity and inclusion commitments

Captured as **process commitments**, never as quotas applied to scoring:
- Slate composition goals at the *sourcing* stage
- Structured-evaluation commitments
- Any adverse-impact reporting the client wants
- Explicit confirmation of the prohibited-disqualifier list (§12.4)

### 16.8 Section 8 — Data, consent and privacy

- What candidate data the client may receive and retain
- Consent language the client requires
- Retention period and deletion obligations
- Cross-border transfer constraints
- Whether references may be contacted before or only after an offer stage

### 16.9 Section 9 — Compensation and offer reality

Not used in scoring. Used in the **risk register**: a candidate whose market value exceeds the band is a retention/counter-offer risk that the client should know about before investing interview time. Salary history is never collected or used as a ranking input (§12.4).

### 16.10 Section 10 — Sourcing preferences

Where to look. Explicitly separated from how to score (§8.9 corollary). Target companies, target industries, and talent-pool preferences live here and are applied at sourcing only.

### 16.11 Section 11 — Dossier presentation preferences

Depth of dossier, format, whether the client wants raw evidence attached, named vs. anonymised first-pass review, and language.

### 16.12 Section 12 — Historical calibration data

If the client can supply it: past hires, who succeeded, who did not, and on what dimension the failure occurred. This is the highest-value input in the entire intake and is worth pursuing hard. It converts our weight baselines from professional judgement into client-specific evidence.

## 17. Compiling Company DNA into engine configuration

### 17.1 The compilation table

Every Company DNA session produces this artefact, which is what the engine actually reads:

```yaml
company_id: CL-0000
dna_version: v1
effective_from: 2026-09-01

weight_modifiers:
  D2: +0.04        # "proven over potential" = 2/5
  D5: -0.04
  D4: +0.03        # regulated industry

evidence_requirements:
  - competency_class: leadership
    minimum_tier: E4
    rationale: "Client's two prior failures were leadership-scope misreads"
  - all_roles:
    reference_minimum: 2
    reference_timing: pre_offer_allowed

behavioural_competencies:            # from Section 3, as observable evidence
  - id: CDNA-01
    statement: "Has taken a project from unclear brief to shipped outcome…"
    default_weight_in_D3: 0.20
    assessment_route: structured_behavioural_probe

risk_probes:                          # from Section 4
  - "Probe for reliance on defined process; client's failures were
     strong performers who stalled without structure."

hard_constraints:
  - work_authorisation: required
  - background_verification: required_pre_offer

prohibited_filters_confirmed: true
pedigree_cap: 0.05                    # default retained
exploration_slots: 2

sourcing_preferences:                 # NOT scoring inputs
  target_industries: [...]

presentation:
  dossier_depth: full
  first_pass: anonymised
```

### 17.2 Review cadence

Company DNA is reviewed every six months, and immediately on: leadership change, funding or ownership change, entry into a new regulatory regime, a significant attrition event, or two consecutive calibration cycles showing systematic mismatch.

### 17.3 The DNA drift check

Each quarter, compare declared DNA against **revealed preference**: what the client actually shortlisted, interviewed, rejected and hired. Where the two diverge materially, the DNA is wrong and the client is told. This conversation — "you told us you hire for potential, but you have rejected every candidate without direct domain experience" — is one of the most valuable things Ready Pick Now delivers, and no similarity-ranking product can have it.

---

# PART V — LAYER 3: ROLE SWOT INTELLIGENCE

## 18. Why SWOT, and why it must not stay a form

A job description is a public advertisement. It is written to attract, to satisfy internal approval, and to comply. It is almost never an accurate specification of the hiring problem.

The SWOT is where the hiring manager tells us the truth:

> **JD says:** "Senior Backend Engineer, 6+ years, AWS, microservices."
> **SWOT says:** "My backend team is excellent but nobody here has ever owned infrastructure. We break the monolith in six months and if that migration goes badly it takes the product down. I need someone who has personally survived this, not someone who has read about it."

Those are different requirements, and only the second one can produce a correct ranking.

### 18.1 The four quadrants, defined operationally

| Quadrant | The question asked | What it produces |
|---|---|---|
| **Strengths** | What does this team already do well, that a new hire will inherit rather than provide? | Competencies to *deprioritise*; the bar the hire must meet to be credible; differentiators to weight up |
| **Weaknesses** | What is missing, and what breaks because it is missing? | The gap competencies — the highest-weighted items on the scorecard |
| **Opportunities** | What becomes possible if this hire is excellent? What will the role grow into? | Trajectory and adjacency signals to reward |
| **Threats** | What would make this hire fail? What has failed before? | Risk probes, thresholds, disqualifiers, retention risks |

### 18.2 The intake session protocol

Sixty to ninety minutes, live wherever possible. Async is permitted for junior volume roles.

```
0–10   Context. What is the business situation this role sits inside?
       Why now? What happens if the seat stays empty for six months?
10–25  Strengths. Not the company's strengths — THE TEAM'S.
       Probe: "If I joined tomorrow, what would I not have to do because
       someone else already does it well?"
25–45  Weaknesses. The core of the session.
       Probe: "What goes wrong today because of a missing capability?"
       Probe: "What does the team keep having to outsource or defer?"
       Probe: "What did the last person in this seat struggle with?"
45–60  Opportunities. Where does this role go in 18 months?
       Probe: "If this person is excellent, what do you hand them next?"
60–75  Threats. Two directions.
       Failure modes: "Describe the version of this hire that goes badly."
       External: notice periods, counter-offers, market scarcity, comp reality.
75–90  Force-ranking and disqualifier confirmation.
```

### 18.3 The seven probes that produce the most usable intelligence

1. **The empty-seat probe** — "What is not getting done right now?" Converts abstract requirements into concrete work.
2. **The first-90-days probe** — "What would this person deliver in their first quarter?" Converts to must-have competencies immediately.
3. **The last-person probe** — "Why did the previous person leave or fail?" Produces the most honest threat data available.
4. **The rejection probe** — "Describe a candidate who looked perfect on paper that you'd still say no to. Why?" Surfaces unstated criteria before they become invisible filters.
5. **The trade-off probe** — "If you could only have deep X or deep Y, which?" This is the force-ranking, extracted conversationally.
6. **The scale-reality probe** — "What size and messiness of system/team/budget will they actually face?" Prevents scope mismatches.
7. **The autonomy probe** — "How much direction will they get?" Determines how heavily to weight self-direction evidence.

### 18.4 Extracting the hiring situation type

Every role is classified into one of six situation types, which materially changes the weight vector and the evidence sought:

| Situation | Description | Weight consequence | Evidence emphasis |
|---|---|---|---|
| **Gap-fill** | A specific missing capability | D3 ↑↑, D1 ↑ | Direct prior experience of that exact problem |
| **Turnaround** | Something is broken and must be fixed | D2 ↑↑, D3 ↑ | Evidence of fixing, not just running |
| **Scale-up** | Working, must grow | D2 ↑, D5 ↑ | Evidence of operating at the *next* scale |
| **Greenfield** | Building from zero | D5 ↑↑, D3 ↑ | Evidence of building without inherited structure |
| **Steady-state** | Maintain and execute | D1 ↑↑, D5 ↓ | Reliability, depth, consistency |
| **Succession** | Prepare to take over a larger role | D5 ↑↑, D2 ↑ | Trajectory, readiness indicators |

Misclassifying the situation is the most expensive error available at intake, because it corrupts the entire weight vector. The recruiter states the classification back to the hiring manager for explicit confirmation before the session closes.

### 18.5 SWOT quality control

An intake is rejected back to the hiring manager if any of:

- Weaknesses are absent or purely external ("the market is competitive")
- Every competency is marked must-have
- Requirements are traits, not observable evidence
- A prohibited disqualifier appears
- The stated requirements would exclude the hiring manager's own current best performer (a devastating and highly effective test — run it)
- The situation type cannot be determined

---

## 19. The transformation pipeline

This is the mechanical heart of Layer 3. Each SWOT input traverses seven stages. Nothing enters the engine without completing all seven.

```
    SWOT INPUT
        ↓
1.  COMPETENCY          — name the capability, from the department model
        ↓
2.  OBSERVABLE EVIDENCE — what would we SEE if this were true?
        ↓
3.  EVIDENCE SOURCES    — where can this be observed?
        ↓
4.  ASSESSMENT METHOD   — what specific instrument tests it?
        ↓
5.  WEIGHT              — from the force-ranking
        ↓
6.  THRESHOLD           — minimum acceptable level, if any
        ↓
7.  DISQUALIFIER        — binary exclusion, if genuinely warranted
        ↓
    ENGINE CONFIGURATION
```

### 19.1 Worked transformation — Weakness

**SWOT input (Weakness):** *"Team has strong backend engineers but nobody has owned cloud infrastructure. We migrate monolith → microservices in six months."*

| Stage | Output |
|---|---|
| 1. Competency | CMP-CLOUD-ARCH — Production cloud infrastructure ownership |
| 2. Observable evidence | "Has personally designed and operated production cloud infrastructure supporting a live service, including making and defending capacity, failure-isolation and rollback decisions" |
| 3. Sources | Resume claims; validation Q; live architecture probe; commit/IaC artefacts; reference from an engineering manager |
| 4. Assessment | Live architecture probe: present the candidate with this team's actual migration constraints and require a design, a failure analysis, and a rollback plan. Score reasoning, not the design's aesthetics. |
| 5. Weight | Rank 1 of 5 → intra-D1 weight 0.35; contributes to D3 at 0.30 |
| 6. Threshold | Competency score ≥ 60 required; below this, band caps at "Consider with reservations" |
| 7. Disqualifier | None (this is a capability, not a legal gate) |

### 19.2 Worked transformation — Strength

**SWOT input (Strength):** *"Our code review culture is exceptional and our testing discipline is strong."*

| Stage | Output |
|---|---|
| 1. Competency | CMP-TEST-DISCIPLINE (deprioritised) |
| 2. Observable evidence | Evidence of writing tests and participating in review — a *floor*, not a differentiator |
| 3. Sources | Assessment artefacts; commit history |
| 4. Assessment | Observed during the standard technical exercise; no separate instrument |
| 5. Weight | Rank 5 of 5 → intra-D1 weight 0.08 |
| 6. Threshold | Minimum 45 (must not be actively bad; the team will teach the rest) |
| 7. Disqualifier | None |

**Note the counterintuitive but correct move:** a team strength *reduces* the weight of that competency, because the hire does not need to supply it. Systems that weight everything the JD mentions get this exactly backwards and consistently select candidates who duplicate existing strengths while leaving the real gap unfilled.

### 19.3 Worked transformation — Opportunity

**SWOT input (Opportunity):** *"Within 18 months this person could lead the platform team — we have no platform lead."*

| Stage | Output |
|---|---|
| 1. Competency | CMP-TECH-LEADERSHIP-POTENTIAL |
| 2. Observable evidence | "Has informally led technical direction for others without formal authority; has mentored; has driven a decision across teams" |
| 3. Sources | Validation Q; behavioural probe; reference |
| 4. Assessment | Structured behavioural probe on a cross-team technical decision they drove |
| 5. Weight | D5 weight raised +0.06; intra-D5 weight 0.40 |
| 6. Threshold | None (this is upside, not a requirement) |
| 7. Disqualifier | None |

### 19.4 Worked transformation — Threat

**SWOT input (Threat):** *"Last two hires at this level left within a year — both said the on-call load wasn't what they expected."*

| Stage | Output |
|---|---|
| 1. Competency | CMP-OPS-REALITY-TOLERANCE (context fit) |
| 2. Observable evidence | "Has carried production on-call responsibility in a comparable environment and chose to continue in such a role" |
| 3. Sources | Validation Q (direct and explicit); reference |
| 4. Assessment | Direct disclosure question with the real on-call rota described honestly to the candidate |
| 5. Weight | D3 sub-weight 0.20 |
| 6. Threshold | Candidate must be *informed* — this is a transparency obligation, not merely a scoring input |
| 7. Disqualifier | None; but a candidate who declines the on-call model is withdrawn by mutual agreement, not scored down |

**Important principle demonstrated here:** some threats convert into **obligations on us and the client** (tell the candidate the truth about the job) rather than into filters on the candidate. Retention failures caused by misrepresentation of a role are not candidate-quality problems, and the runbook will not let them be scored as such.

### 19.5 The transformation completeness check

Before a configuration can be frozen:

- [ ] Every SWOT element has either produced a configuration item or been explicitly marked "context only, no engine effect"
- [ ] Every competency has a named assessment method (Axiom: a competency we cannot test is a competency we cannot score — §14.1)
- [ ] Every threshold has a rationale
- [ ] Every disqualifier passes the §12.3 test
- [ ] Weights are force-ranked and sum correctly
- [ ] The evidence sources named are actually collectable within the engagement timeline

---

## 20. The scorecard

### 20.1 Format

| # | Competency | Must / Nice | Observable evidence statement | Assessment method | Weight | Threshold |
|---|---|---|---|---|---|---|
| 1 | Production cloud infrastructure ownership | Must | Has designed and operated production cloud infra, and can defend capacity, failure-isolation and rollback decisions | Live architecture probe + IaC artefact + EM reference | 0.35 | 60 |
| 2 | Distributed systems debugging | Must | Has diagnosed a production incident in a distributed system and can reconstruct the reasoning path | Live debugging exercise | 0.25 | 55 |
| 3 | Migration execution under deadline | Must | Has executed a phased migration on a live service without extended downtime | Behavioural probe + reference | 0.20 | 50 |
| 4 | Mentoring / technical influence | Nice | Has driven a technical decision across teams without authority | Behavioural probe | 0.12 | — |
| 5 | Testing discipline | Nice | Writes tests as a matter of course | Observed in exercise | 0.08 | 45 |

### 20.2 The six-competency ceiling

**Maximum six. No exceptions.** Beyond six, force-ranking becomes meaningless, weights become noise, evaluation time per competency drops below the level at which evidence is real, and every candidate looks average because the weighted mean regresses. Six is not a stylistic preference; it is a mathematical property of weighted averages over noisy estimates.

If a hiring manager insists on more, the correct diagnosis is almost always one of three things, and the recruiter names it: the role is actually two roles; the manager has not decided what matters; or the manager is describing an ideal person rather than a needed hire.

### 20.3 The force-ranking requirement

Competencies are ranked 1..n and weights derive from the ranking, not from free assignment. Weight assignment by free choice reliably produces five items at "high importance," which is the same as no ranking at all.

Default derived weights:

| Count | Weights (rank order) |
|---|---|
| 4 | 0.36 / 0.28 / 0.22 / 0.14 |
| 5 | 0.32 / 0.25 / 0.20 / 0.14 / 0.09 |
| 6 | 0.30 / 0.23 / 0.18 / 0.13 / 0.10 / 0.06 |

The hiring manager may adjust within ±0.05 with a stated reason. The ordering may not be flattened.

### 20.4 The calibration commitment

Approval of a scorecard includes the hiring manager's commitment to a **calibration review**: a scheduled session, after the first shortlist, in which they react to real candidates and we adjust. This is not optional politeness — it is the mechanism by which stated criteria are corrected against revealed criteria, and it is the single highest-value hour in the entire engagement. A hiring manager who will not commit to it gets a note in the engagement record, because unreviewed configurations drift and the drift will later be attributed to us.

### 20.5 Scorecard freeze and versioning

On HR Manager approval the scorecard is frozen as `v1`. Any change creates `v2`, triggers re-scoring of all evaluated candidates, and both versions are retained with the rationale for the change. Clients see which version each candidate was scored under. This makes criteria drift visible instead of invisible — and visible drift is legitimate; invisible drift is how unfair shortlists get built.

---

# PART VI — DEPARTMENT EVIDENCE GRAPHS

*The department models are the operational core of the runbook. Each defines what "good" means in that function, what evidence proves it, how that evidence is tiered, what the department-specific gaming vectors are, and how to evaluate a fresher in that field.*

**How to read a department model.** Every model follows the same eleven-section structure so that they are comparable and so that adding a new department is a filling-in exercise rather than an invention. The structure is deliberate: *what good means → competencies → evidence tiers → assessment design → validation probes → authenticity vectors → red flags → credential logic → fresher variant → seniority notes → worked example.*

**Universal rule across all departments:** the competency list in each model is the *menu*. The scorecard for a given role selects at most six from it, weighted by SWOT force-ranking. No role uses the whole menu.

---

## 21. IT & SOFTWARE ENGINEERING

### 21.1 Role families
Backend · Frontend · Full-stack · Mobile · Platform/Infrastructure/SRE/DevOps · Data engineering · QA/SDET · Security · Embedded · Engineering management

### 21.2 What "good" actually means here

The defining shift in this department is that **working output is no longer evidence of capability**. Code that compiles, passes tests and looks idiomatic can be produced by anyone with a model and a prompt. Therefore:

> In software, we score **reasoning, judgement and ownership**, and we treat produced artefacts as prompts for probing rather than as proof.

The specific things that remain expensive to fake:
- Explaining *why* a design was chosen over the alternatives that were actually considered
- Reconstructing a debugging path through a problem the candidate genuinely lived
- Describing what went wrong, what it cost, and what changed afterwards
- Reasoning about failure modes, blast radius, and rollback
- Knowing the boundary of one's own knowledge and saying so
- Sustained authorship over time in a real codebase

### 21.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| SW-01 | Core language & runtime depth | Can reason about memory, concurrency, error semantics of their primary stack; explains behaviour, not just syntax |
| SW-02 | System design & architecture | Designs to constraints; states trade-offs; identifies failure modes and their blast radius |
| SW-03 | Debugging & problem diagnosis | Reconstructs a real diagnosis path; forms and tests hypotheses rather than guessing |
| SW-04 | Production ownership | Has been accountable for a live service: on-call, incidents, rollbacks, postmortems |
| SW-05 | Data modelling & storage | Chooses stores to fit access patterns; explains consistency and migration implications |
| SW-06 | Code quality & maintainability | Reviews meaningfully; refactors deliberately; writes tests as a matter of course |
| SW-07 | Delivery under constraint | Ships within real deadlines with explicit, defended scope trade-offs |
| SW-08 | Collaboration & technical influence | Drives decisions across teams; changes minds with reasoning; documents |
| SW-09 | AI-tool fluency | Uses AI tooling to increase leverage while retaining verification, review and understanding — *and can say where they don't trust it* |
| SW-10 | Security & correctness awareness | Anticipates misuse, injection, authz failure, data exposure |
| SW-11 | Domain/product judgement | Understands who uses the system and makes engineering choices accordingly |
| SW-12 | Engineering leadership *(EM roles)* | Grows engineers; sets technical direction; owns delivery of a group |

### 21.4 Evidence tiers — software-specific

| Tier | What qualifies in software |
|---|---|
| **E5** | GPG/SSH-signed commit history with sustained authorship in a substantive repository; employer/EM reference corroborating specific ownership; verified production access history; verified conference/publication authorship |
| **E4** | Live coding under observation with reasoning required; live architecture probe on the team's real constraints; live debugging of an unfamiliar failing system; unprepared deep-dive into a system the candidate claims to have built |
| **E3** | Take-home exercise **with a mandatory walkthrough**; structured written validation on architecture decisions; recorded async technical explanation |
| **E2** | Public repository without signed commits; deployed live link; technical blog; portfolio project |
| **E1** | Specific self-description with system names, scale numbers, mechanisms |
| **E0** | Skill lists, self-rated proficiency, keyword-dense resume bullets |

**Standing note on repositories:** git author fields are trivially spoofable. An unsigned repository is E2 evidence of *output*, never E5 evidence of *authorship*. Sustained history across time, with commit-message coherence, issue participation and review activity, raises confidence; a repository created shortly before the application does the opposite and is a T4 contradiction candidate if it purports to represent older work.

### 21.5 Assessment design

**Prohibited:** unproctored algorithmic puzzle tests as a primary signal. They measure interview preparation and AI access, correlate weakly with job performance, and are the most heavily gamed instrument in the industry.

**Preferred instruments, in order of value:**

1. **The reasoning walkthrough (E4, always required).** The candidate presents any system they claim to have built. The evaluator probes: why this and not that; what broke; what you'd change; what you didn't know at the time; where the bodies are buried. Twenty minutes. This single instrument does more discriminating work than any other in the department, because it cannot be prepared for in general — only by actually having done the work.
2. **The live debugging exercise (E4).** A small, unfamiliar codebase with a genuine bug. Score the *process*: hypothesis formation, instrumentation, narrowing, verification. Whether they find it in time is secondary.
3. **The constrained design probe (E4).** Present the team's real constraints from the SWOT. Require a design, a failure analysis, and a rollback plan. Score trade-off articulation.
4. **The take-home with walkthrough (E3→E4).** If a take-home is used, the walkthrough is mandatory and the score comes from the walkthrough, not the submission. **Explicitly permit AI use on the take-home** and tell candidates so — then probe understanding. This converts a gamed instrument into an honest one at zero cost.
5. **AI-collaboration observation (E4).** Give a task, permit AI, and observe: do they verify output, catch the model's errors, know when to stop trusting it? This is now a genuine job skill and one of the better discriminators available.

### 21.6 Validation questionnaire probes (E3)

Design rule: **probe for specifics that only a participant would know, not for knowledge that is publicly retrievable.**

| Claim type | Weak probe (retrievable) | Strong probe (participatory) |
|---|---|---|
| Scale | "What is horizontal scaling?" | "At what point did your system first fall over, what was the symptom, and what did you change first?" |
| Ownership | "Did you lead the migration?" | "Who else could have done your part, and what would have been different if they had?" |
| Architecture | "Explain microservices." | "What did you split first, and what did you deliberately leave in the monolith — and why that boundary?" |
| Debugging | "How do you debug?" | "Describe a bug that took more than two days. What was your first wrong hypothesis?" |
| Impact | "What was the improvement?" | "How was the baseline measured, and what else changed during that period?" |
| AI use | "Do you use AI tools?" | "Describe a case where the model's suggestion was confidently wrong and how you caught it." |

### 21.7 Gaming vectors and countermeasures

| Vector | Countermeasure |
|---|---|
| AI-written resume with perfect keyword alignment | Demote resume to E0/E1; weight walkthrough |
| AI-generated portfolio projects | Probe design decisions and failure modes; check repository history depth |
| AI-solved take-home | Permit AI explicitly; score the walkthrough |
| Live coding with off-screen assistance | Require reasoning aloud; ask unpredictable follow-ups on their own code; interrupt with a constraint change |
| Fabricated repository ownership | Signed-commit check; contribution graph; ask about a specific commit they made |
| Deepfake / proxy interviewing | Live synchronous stage mandatory at senior level; identity continuity checks across stages; unpredictable topic shifts |
| Inflated title/scope | Reference corroboration on scope; blast-radius questions |
| Memorised system-design answers | Use the *client's actual* constraints from the SWOT; canned answers fail immediately |

### 21.8 Red flags (route to review, never auto-reject)

- Cannot explain a decision in a system they claim to have architected
- All repository activity created within weeks of applying, presented as multi-year work
- Fluent vocabulary with no operational specifics ("we used Kafka for event-driven architecture" with no partitioning, ordering or failure discussion)
- Claims production ownership but cannot describe a single incident
- Assessment performance dramatically inconsistent with claimed seniority in either direction
- Reference cannot confirm the scope the candidate claims

### 21.9 Credential logic

Software is the department where formal credentials matter **least**. Degrees and certifications are context, never gates. Cloud certifications are E1 unless verified with the issuer, and even then evidence only of study, not of production capability. The pedigree cap (§8.9) applies with full force — this field has abundant evidence of capable engineers from non-traditional paths and equally abundant evidence that prestige filtering imports bias without improving prediction.

### 21.10 Fresher variant

Suspend D2. Redistribute to D1 (+0.10) and D5 (+0.05).

Evidence sources that carry real weight for a fresher:
- A project they can explain in depth, including what they got wrong — **one deep project outranks six shallow ones, and the model must not reward project count**
- Sustained personal repository activity over time (a habit, not a portfolio)
- Live debugging performance on unfamiliar code
- Learning-path evidence: how they taught themselves something, and how fast
- Internship work with a reference
- Competitive programming / open-source / hackathon participation — as *context*, weighted modestly; these correlate with free time and access

Fresher-specific probe: *"Show me something you built that didn't work, and tell me why."* Candidates who have genuinely built things always have this answer. Candidates who have assembled a portfolio do not.

### 21.11 Seniority notes

| Level | Emphasis shift |
|---|---|
| Fresher | Reasoning, learning velocity, depth on one thing |
| 2–5 | Independent delivery; debugging; code quality |
| 5–10 | System design; production ownership; influence |
| 10+ | Architecture at organisational scale; trade-off judgement; mentoring |
| EM | Delivery of a group; growing engineers; technical direction; D1 shifts from personal coding to technical judgement |

### 21.12 Worked mini-example

Role: SRE, 5 years, situation type **Turnaround** (client's reliability is poor).
Scorecard top three: SW-04 Production ownership (0.32) · SW-03 Debugging (0.25) · SW-02 System design (0.20).

Candidate claims: "Reduced P1 incidents by 60%."
- Resume (E0) → claim recorded, materiality High
- Validation Q (E3): asked for the incident taxonomy before and after, and which class of incident fell most → answered with specifics: three classes, largest reduction in deploy-related, mechanism was staged rollout plus automated rollback
- Live probe (E4): reconstructed a specific incident, including a wrong initial hypothesis
- Reference from former manager (E5): confirmed ownership of the rollout system and the incident-reduction programme
→ 3 independent groups; best tier E5; corroboration multiplier 1.28; **Corroborated**.
D2 contribution is strong *because the mechanism was reconstructable*, not because the number was impressive.

---

## 22. DATA, ANALYTICS, DATA SCIENCE & AI/ML

### 22.1 Role families
Data analyst · BI · Analytics engineer · Data engineer · Data scientist · ML engineer · Applied research

### 22.2 What "good" means here

This function has the widest gap in the market between **claimed** and **actual** capability, because the vocabulary is easy to acquire and the underlying statistical judgement is not. The discriminating question is almost never "do you know the algorithm" — it is "**do you know when the analysis is lying to you.**"

Good looks like: framing an ambiguous business question into a measurable one; interrogating data quality before modelling; choosing the simplest method that answers the question; recognising leakage, confounding and selection effects; quantifying uncertainty; and communicating a result to someone who will act on it.

### 22.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| DA-01 | Problem framing | Converts a vague business question into a measurable one with a defined decision it will inform |
| DA-02 | Data quality interrogation | Checks provenance, completeness, bias, collection mechanism before analysing |
| DA-03 | Statistical judgement | Knows when a method is invalid; recognises leakage, confounding, multiple comparisons, survivorship |
| DA-04 | SQL & data manipulation | Writes correct, efficient queries against messy real schemas |
| DA-05 | Modelling craft | Chooses methods appropriate to data and decision; validates honestly; avoids over-engineering |
| DA-06 | Pipeline & production engineering | Builds reliable, monitored, reproducible pipelines |
| DA-07 | Communication & influence | Explains findings to non-technical decision-makers; states uncertainty without hedging into uselessness |
| DA-08 | Business impact | Analysis changed a decision, and the decision changed an outcome |
| DA-09 | Experimentation | Designs valid experiments; understands power, novelty effects, interference |
| DA-10 | ML systems operation | Monitors drift, retrains, handles feedback loops and degradation |

### 22.4 Evidence tiers — data-specific

| Tier | Qualifies |
|---|---|
| E5 | Verified production model/dashboard in use with a corroborating reference; published peer-reviewed work; verified competition standing |
| E4 | Live analysis of an unfamiliar messy dataset with reasoning required; live critique of a flawed analysis; unprepared walkthrough of their own project's assumptions |
| E3 | Take-home analysis **with walkthrough**; written validation on methodology choices |
| E2 | Notebooks, dashboards, portfolio projects, Kaggle work |
| E1 | Specific self-description of methods and results |
| E0 | Tool and library lists |

### 22.5 The signature assessment: the flawed-analysis critique

Give the candidate a short analysis containing three planted defects — one leakage, one selection-bias, one overclaimed causal conclusion. Ask what they would ask the author.

This instrument is exceptionally discriminating and unusually resistant to gaming, because it requires *judgement* rather than *production*, and because a candidate who has only learned vocabulary will critique the presentation while missing the leakage. It should be used at every level from junior analyst upward, with difficulty scaled.

### 22.6 Validation probes

- "What did you check about the data before you trusted it?"
- "What would have made you abandon this approach?"
- "Who acted on this analysis, and what did they do differently?"
- "What's the simplest thing that would have worked, and why didn't you do that?"
- "Where could this result be wrong, and how badly?"
- For ML: "How did it degrade in production, and how did you find out?"

### 22.7 Gaming vectors

| Vector | Countermeasure |
|---|---|
| Kaggle/portfolio notebooks generated or copied | Probe assumptions and alternatives; require live critique |
| Vocabulary fluency without judgement | Flawed-analysis critique; ask what they *checked*, not what they *used* |
| Claiming model ownership on a team project | Attribution probe; reference; ask what they specifically decided |
| Overstated impact numbers | Ask for the decision the analysis changed and who made it |
| AI-generated take-home | Permit AI, require walkthrough, probe the assumptions the model made silently |

### 22.8 Red flags
Cannot name a single thing that could invalidate their own result · presents accuracy without a baseline · no awareness of how data was collected · describes production ML with no mention of monitoring or drift · impact claims with no decision attached.

### 22.9 Credential logic
Degrees matter more here than in software (statistical training is genuinely harder to acquire informally) but remain non-gating. Verify advanced-degree claims where D1 weight rests on them. Certifications are E1.

### 22.10 Fresher variant
D2 suspended; D1 +0.08, D5 +0.06. Emphasise: one project with genuine data-quality problems (not a clean benchmark dataset); ability to critique their own work; SQL competence under observation; and the flawed-analysis exercise at reduced difficulty. Explicitly discount clean-dataset portfolio projects — they demonstrate tool use, not judgement.

---

## 23. MECHANICAL ENGINEERING & MANUFACTURING

### 23.1 Role families
Design engineer · Manufacturing/production engineer · Quality engineer · Maintenance · Tooling · NPD · HVAC · Automotive · Plant engineering

### 23.2 What "good" means here

This department is where a software-derived evaluation model fails most badly. GitHub is irrelevant. Algorithmic puzzles are irrelevant. What matters is **physical judgement**: has this person made things that had to work in the real world, under tolerance, cost and manufacturability constraints, and do they understand why things fail?

Good looks like: fundamentals that are actually applied (not recited); tool fluency at production depth; understanding of manufacturing processes and their constraints; hands-on exposure to real equipment and real failures; and disciplined process knowledge.

### 23.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| ME-01 | Engineering fundamentals applied | Uses mechanics/thermo/materials to justify a real design decision, not to recite theory |
| ME-02 | CAD/CAE tool depth | Production-level modelling; assembly management; drawing standards |
| ME-03 | Design for manufacturability | Designs to real process capability and cost; has changed a design because of manufacturing feedback |
| ME-04 | GD&T and tolerancing | Applies and defends tolerance decisions; understands stack-up |
| ME-05 | Simulation & analysis | FEA/CFD with validated assumptions; knows when results are untrustworthy |
| ME-06 | Process & quality methods | FMEA, root-cause, SPC, lean, Six Sigma applied to a real problem |
| ME-07 | Hands-on/shop-floor exposure | Has worked with machines, operators and physical prototypes |
| ME-08 | Testing & validation | Designs and runs tests; interprets failure |
| ME-09 | Standards & compliance | Applies relevant codes and standards to the work |
| ME-10 | Project execution | Delivered a component/product/line change on schedule with defined trade-offs |
| ME-11 | Vendor & supply-chain interface | Has specified, sourced and qualified parts |

### 23.4 Evidence tiers — mechanical-specific

| Tier | Qualifies |
|---|---|
| E5 | Verified professional licence (where discipline requires); employer-verified project ownership; patents with verified inventorship; supervisor reference on specific technical scope |
| E4 | Live design/tolerancing exercise; live CAD task under observation; unprepared walkthrough of a drawing or assembly the candidate produced; failure-analysis reasoning probe |
| E3 | Structured written validation on design decisions; take-home design task with walkthrough |
| E2 | Portfolio drawings/models; project reports; academic project documentation |
| E1 | Specific self-description of designs, tolerances, materials, processes |
| E0 | Tool lists, "proficient in SolidWorks" |

### 23.5 The signature assessment: the drawing walkthrough

Ask the candidate to walk through a drawing or model **they produced**. Probe: why this material; why this tolerance; what would you relax first if cost had to come down; what does this part cost to make and where does the cost sit; how would this fail; what did manufacturing tell you to change.

This is close to unfakeable. A candidate who has designed real parts answers fluently. A candidate who has only used CAD in coursework cannot produce the manufacturing-feedback layer at all.

**Secondary instrument:** the failure probe. "Describe something you designed or maintained that failed. What was the root cause, and how did you find it?" Every genuinely experienced mechanical engineer has this story. Its absence is informative.

### 23.6 Validation probes
- "What tolerance did you specify and why that value?"
- "What did the shop floor tell you to change?"
- "What was the cost driver in that assembly?"
- "Which of your FEA assumptions were you least comfortable with?"
- "Describe a root-cause investigation you ran end to end."
- "What machines have you personally stood next to while they ran?"

### 23.7 Credential logic (conditional — critical)

Professional licensure requirements are **discipline- and jurisdiction-dependent**, and treating them as universal is a serious modelling error.

```
IF role involves statutory sign-off, public safety certification,
   or independent consulting practice
   AND jurisdiction requires licensure for that activity
     → licence is a HARD DISQUALIFIER if absent
ELSE
     → licence is POSITIVE EVIDENCE at modest weight, never a gate
```

In most private-industry mechanical and manufacturing roles — the majority of hiring volume, and the overwhelming majority in India — licensure is **not** required and must not be weighted as if it were. Certifications worth weighting where relevant: Six Sigma (Green/Black Belt) for process roles; PMP for project roles; CAD professional certifications; industry-specific quality certifications (IATF/AS9100 exposure for automotive/aerospace).

### 23.8 Gaming vectors
Coursework projects presented as industrial experience (probe for manufacturing feedback and cost — coursework has neither) · tool lists without depth (require a live task) · borrowed team-project drawings (ask about a specific dimension) · certification claims (verify with issuer) · inflated plant/line scale (probe throughput, shift structure, headcount).

### 23.9 Red flags
Cannot justify a single tolerance · no failure story at all after five years · describes designs with no cost or manufacturability dimension · claims FEA competence but cannot state boundary conditions or mesh sensitivity concerns · "hands-on" claimed with no specific equipment named.

### 23.10 Fresher variant
D2 suspended; D1 +0.10, D5 +0.05. Weight heavily: lab and workshop exposure; a capstone project the candidate can defend dimensionally; internship in a real plant with a reference; competition teams (Baja/Formula/Robotics) — which are unusually good evidence here because they involve real fabrication, real failure and real constraints; and demonstrated tool depth under live observation rather than claimed.

---

## 24. ELECTRICAL & ELECTRONICS ENGINEERING

### 24.1 Role families
Power systems · Electrical design · Controls & automation · PLC/SCADA · Embedded hardware · PCB design · Testing & commissioning · Instrumentation · Maintenance

### 24.2 What "good" means

Split the field carefully — **power/electrical infrastructure** and **electronics/embedded** are close to different professions and must not share a scorecard.

Power side: safety discipline, standards fluency, load and protection calculations, field commissioning experience.
Electronics side: circuit-level reasoning, debugging with instruments, signal integrity, firmware–hardware boundary judgement, design-for-test.

### 24.3 Competency menu

| ID | Competency | Side | Observable evidence |
|---|---|---|---|
| EE-01 | Circuit analysis & design | Electronics | Designs and defends a circuit; reasons about tolerances and margins |
| EE-02 | PCB design & signal integrity | Electronics | Layout decisions defended; understands EMI, grounding, routing constraints |
| EE-03 | Embedded firmware/hardware interface | Electronics | Has debugged across the boundary; understands timing and peripherals |
| EE-04 | Instrumentation & measurement | Both | Uses scope/analyser/meter to diagnose; interprets what the instrument is telling them |
| EE-05 | Power system design & calculation | Power | Load calcs, cable sizing, protection coordination, short-circuit study |
| EE-06 | Standards & safety compliance | Power | Applies applicable electrical codes; LOTO and arc-flash discipline |
| EE-07 | Controls, PLC & automation | Both | Has programmed, commissioned and debugged live control systems |
| EE-08 | Commissioning & field experience | Power | Has energised and tested real installations |
| EE-09 | Failure diagnosis | Both | Systematic fault-finding on real equipment |
| EE-10 | Documentation & schematics | Both | Produces and reads accurate schematics, SLDs, panel drawings |

### 24.4 Signature assessments
- **Fault-finding probe (E4):** present a described failure symptom and require a diagnostic sequence. Score the ordering and reasoning, not the answer.
- **Schematic walkthrough (E4):** their own schematic or SLD, probed for protection, margins and standards choices.
- **Safety-discipline probe (E4, power roles, mandatory):** describe a situation requiring isolation. A candidate whose safety answers are vague is disqualified on safety grounds regardless of technical strength — this is one of the few places where a single dimension can veto, and it is justified because the failure mode is fatal.

### 24.5 Credential logic
Same conditional structure as §23.7. Additionally: for power and installation work, statutory electrical licences/permits where the jurisdiction requires them are hard disqualifiers. Safety certifications (e.g. LOTO, arc-flash, height/confined-space where relevant) are verified and weighted for field roles.

### 24.6 Gaming vectors and red flags
Simulation-only experience presented as field experience (probe commissioning specifics: what tripped, what you measured, who signed) · PLC "knowledge" without commissioning (ask about a live debug with production stopped) · safety language recited without behavioural specifics · claimed instrument use with no diagnostic story.

### 24.7 Fresher variant
Weight lab work, hands-on projects that were physically built (not simulated), internship with real equipment exposure, and live instrument-reasoning. A fresher who has built and debugged real hardware is materially stronger evidence than one with higher marks and simulation-only work — and the model should say so explicitly.

---

## 25. CIVIL, STRUCTURAL & CONSTRUCTION

### 25.1 Role families
Structural design · Site/execution engineer · Project management · Quantity surveying/estimation · Geotechnical · Transportation · Water/environmental · MEP coordination · QA/QC

### 25.2 What "good" means

Civil is the department where **credentials and regulatory gating genuinely matter most**, and where field experience is least substitutable. It is also where the split between *design* and *execution* roles is sharpest — a brilliant design engineer may be useless on site and vice versa, and the scorecard must choose.

Good looks like: codes and standards applied fluently to real decisions; a design engineer's ability to defend a load path; a site engineer's ability to sequence work, manage subcontractors and solve problems with the materials actually present; and — universally — safety discipline.

### 25.3 Competency menu

| ID | Competency | Track | Observable evidence |
|---|---|---|---|
| CE-01 | Structural analysis & design | Design | Defends a load path, member sizing, and code compliance |
| CE-02 | Codes & standards fluency | Both | Applies applicable national/local codes to a specific decision |
| CE-03 | Design software depth | Design | STAAD/ETABS/SAFE/Revit at production level; validates outputs rather than trusting them |
| CE-04 | Site execution & sequencing | Execution | Has run a real sequence, managed interfaces, resolved on-site conflicts |
| CE-05 | Quantity, cost & estimation | Both | Produces and defends BOQ/estimates; understands wastage and rate analysis |
| CE-06 | Quality control & materials | Execution | Test regimes, mix design, acceptance criteria, non-conformance handling |
| CE-07 | Safety management | Execution | Demonstrable safety discipline and incident handling |
| CE-08 | Contract & documentation | Both | Understands contract types, variations, claims, RA bills |
| CE-09 | Geotechnical judgement | Both | Reads soil data and its design consequences |
| CE-10 | Stakeholder & authority interface | Both | Has dealt with approving authorities, clients, consultants |
| CE-11 | Project controls | PM | Schedule, resource, cost tracking on a real project |

### 25.4 Signature assessments
- **The project walkthrough (E4).** Their most significant project: scale, their specific scope, three problems encountered and how each was resolved, what they would do differently. Probe for site-reality detail that cannot be invented (weather, labour, material availability, authority delays).
- **The code-application probe (E4).** A specific design or execution decision and which clause governs it. Score judgement, not memorisation — an engineer who says "I would check clause X and here's what I'd expect it to constrain" is stronger than one who recites a number.
- **The drawing/BOQ probe (E4).** Read a drawing or BOQ extract and identify what is missing or wrong.

### 25.5 Credential logic (strongest in this department)

```
IF role requires signing/certifying structural or statutory documents
   → the relevant statutory registration/licence is a HARD DISQUALIFIER if absent
IF role is public-sector, consulting, or independently certifying
   → registration is near-mandatory; weight heavily; verify with issuing body
IF role is contractor-side execution or an in-house design support role
   → registration is positive evidence at moderate weight, not a gate
```

Verify: degree accreditation status, statutory registration validity, and safety certifications. Jurisdiction is resolved at configuration time (§53.1) — the runbook holds the *rule shape*, and the jurisdiction table supplies the specifics.

### 25.6 Gaming vectors and red flags
Project scale inflation (probe headcount, duration, contract value, their reporting line) · design-software familiarity without validation judgement (ask what they check when the software output looks wrong) · "site experience" that was supervisory visits (probe daily routine, who reported to them, what decisions they personally made) · safety claims without incident specifics · claimed authority interface without naming the approval process.

### 25.7 Fresher variant
Site internship with a reference is the single strongest fresher signal in this department and should be weighted accordingly. Also: a defended capstone design; software depth under live observation; and demonstrable code familiarity. A fresher with genuine site exposure outranks one with only design coursework for execution roles, and the reverse for design roles — the scorecard must not blur this.

---

## 26. R&D AND PRODUCT DEVELOPMENT

### 26.1 Role families
Applied research · Product R&D · Materials · Process development · Formulation (pharma/FMCG/chemicals) · Advanced engineering · Innovation

### 26.2 What "good" means

R&D is evaluated on **the quality of the search process**, not on outcomes, because outcomes in research are substantially stochastic. A researcher who ran three well-designed programmes that failed informatively is stronger than one who got lucky once.

Good looks like: rigorous experimental design; honest interpretation of negative results; knowing when to kill a line of work; translating research into something manufacturable or shippable; and documentation quality.

### 26.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| RD-01 | Experimental design | Designs experiments with controls, adequate power, and a defined kill criterion |
| RD-02 | Domain scientific depth | Reasons from first principles in the domain, not from procedure |
| RD-03 | Negative-result discipline | Has killed their own project and can explain the decision |
| RD-04 | Literature & prior-art fluency | Knows what has been tried; does not rediscover |
| RD-05 | Lab / instrumentation technique | Hands-on method competence and awareness of measurement limits |
| RD-06 | Research-to-product translation | Has moved something from lab to pilot/production and knows what broke in transfer |
| RD-07 | Regulatory & documentation rigour | GLP/GMP/design-history-file discipline where applicable |
| RD-08 | IP awareness | Understands patentability and freedom-to-operate |
| RD-09 | Collaboration across functions | Has worked with manufacturing, quality, commercial |

### 26.4 Signature assessment
**The negative-result probe (E4).** "Tell me about a research direction you abandoned. How did you decide, and how long did it take you to decide?" This is the highest-signal question in R&D hiring. It tests intellectual honesty, judgement, and whether the candidate has actually run programmes rather than executed instructions.

Secondary: **the design critique** — give a flawed experimental protocol and ask what they would change.

### 26.5 Evidence specifics
E5: verified publications with confirmed authorship position; granted patents with verified inventorship; verified regulatory submissions the candidate contributed to; supervisor reference on specific scope.
E4: live protocol design; negative-result probe; technique walkthrough.
Note: publication *count* is weak evidence and heavily influenced by field and institution. Authorship *position* and the candidate's ability to explain their specific contribution are the real signals.

### 26.6 Red flags
No abandoned projects · cannot state the limitations of their own method · claims results with no discussion of reproducibility · describes only positive outcomes across a whole career · inability to explain their specific contribution to a multi-author paper.

### 26.7 Fresher variant
Thesis depth is the primary evidence: probe the methodology, what they would change, and the limitations. Weight the candidate's ability to critique their own work far above the result they obtained.

---

## 27. DESIGN, UX & CREATIVE

### 27.1 Role families
Product/UX design · UI/visual · UX research · Graphic/brand · Industrial design · Motion/content design

### 27.2 What "good" means

Design is now the department **second-most disrupted by generative AI** after software. Beautiful visual output is cheap. Case studies are cheap to write. Therefore, exactly as in software:

> **The portfolio is a conversation starter, not a qualification. The decision is made on process reasoning.**

Good looks like: framing the actual problem; showing the research that informed the decision; explaining what was rejected and why; showing the messy middle (sketches, wrong turns, constraints); stating what happened after launch, including when it didn't work.

### 27.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| DS-01 | Problem framing | Reframes a brief; identifies the real user problem behind the request |
| DS-02 | Research & evidence use | Has run or used research; changed a design because of what they learned |
| DS-03 | Process reasoning | Can reconstruct the decision path: what was tried, rejected, and why |
| DS-04 | Craft & visual quality | Execution quality appropriate to the role's level |
| DS-05 | Interaction & systems thinking | Designs for states, edge cases, and system consistency |
| DS-06 | Constraint navigation | Has designed within real technical, business and timeline constraints |
| DS-07 | Collaboration with engineering/product | Has shipped; understands feasibility conversations |
| DS-08 | Outcome awareness | Knows what happened after launch and what it means |
| DS-09 | Critique & iteration | Takes and gives critique substantively |
| DS-10 | Tooling & design systems | Works within/builds design systems; tool fluency |

### 27.4 The two-stage model

**Stage 1 — screening filter:** tool fluency, craft level, relevant domain exposure. Cheap, fast, low-weight.
**Stage 2 — decision driver:** three to five case studies structured as *problem → my role → constraints → what I tried → what I rejected → what shipped → what happened*, probed live.

**Rule:** a portfolio without process is E2 evidence at most. A portfolio *with a live walkthrough* becomes E4. Never rank designers on portfolio artefacts alone.

### 27.5 Signature assessment
**The live critique + redesign probe (E4).** Show the candidate an existing interface with genuine problems. Ask: what's wrong, what would you research first, what would you change, and what would you need to know before committing. Forty minutes. This tests everything the portfolio cannot: reasoning under uncertainty, research instinct, and the ability to say "I don't have enough information."

**Explicitly avoid unpaid spec work.** It is exploitative, it selects for candidates with free time (importing socioeconomic bias), and it produces worse signal than the critique probe. Ready Pick Now will not administer it.

### 27.6 Validation probes
- "What did you try that you rejected, and why?"
- "What did research tell you that surprised you?"
- "What was the constraint you fought hardest against?"
- "What shipped that you weren't happy with, and why did it ship that way?"
- "What did the data say after launch?"
- "Which part of this was someone else's work?" *(attribution — critical in agency and team portfolios)*

### 27.7 Gaming vectors
AI-generated visual work presented as original (probe process and iterations — AI output has no messy middle) · team work presented as individual (direct attribution probe, reference) · case studies written to a template with invented metrics (probe how the metric was measured) · trend-following portfolios with no problem framing.

### 27.8 Red flags
Only polished final screens, no process artefacts · cannot name anything they rejected · no failed or compromised project in an entire career · metrics with no measurement mechanism · cannot articulate a constraint they designed around.

### 27.9 Fresher variant
Weight one deeply explained project over a broad portfolio. Self-initiated redesign projects are acceptable evidence **if** the problem framing and research are real. The live critique probe works well at fresher level and is the primary instrument.

---

## 28. ARCHITECTURE (BUILT ENVIRONMENT)

### 28.1 Role families
Architectural design · Project architect · Interior architecture · Urban design · BIM/documentation · Landscape

### 28.2 What "good" means
A hybrid of the civil model (credentials, codes, statutory reality, documentation rigour) and the design model (portfolio process reasoning). Both halves are required; a scorecard that takes only one produces a beautiful designer who cannot deliver a set of drawings, or a competent documenter with no design judgement.

### 28.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| AR-01 | Design conceptualisation & reasoning | Can explain the generative logic of a scheme and what it responded to |
| AR-02 | Technical documentation | Produces coordinated, buildable drawing sets |
| AR-03 | Building codes & regulatory compliance | Applies applicable building bylaws, fire, accessibility, zoning |
| AR-04 | BIM / software depth | Revit/Rhino/AutoCAD/ArchiCAD at production level; model discipline |
| AR-05 | Materials & construction knowledge | Details buildable junctions; understands cost and constructability |
| AR-06 | Site & construction administration | Has been on site during construction; handled RFIs and changes |
| AR-07 | Consultant coordination | Has coordinated structure/MEP; resolved clashes |
| AR-08 | Client & authority interface | Has presented to clients and navigated approvals |
| AR-09 | Sustainability & performance | Applies environmental performance thinking substantively |

### 28.4 Credential logic
Registration/licensure with the applicable statutory council is a **hard gate for the title and for statutory sign-off** in most jurisdictions. Unregistered candidates may hold every other competency and must be evaluated on merit for non-signing roles — but the title and the sign-off authority are legally constrained, and the runbook does not let a client blur this.

### 28.5 Signature assessment
**Portfolio walkthrough plus detail probe (E4).** Two parts: (1) explain the scheme's generative logic and what changed under constraint; (2) show a construction detail and explain how it is built, what it costs, and what could go wrong on site. Candidates strong on only one part are correctly and usefully differentiated by this — and which half matters more is a SWOT question, not a universal one.

### 28.6 Red flags
Competition/conceptual work only, no built or documented work, presented as professional experience · cannot explain a detail's construction sequence · no code awareness · claimed project role that reference or drawing-set authorship contradicts.

---

## 29. FINANCE & ACCOUNTING

### 29.1 Role families
FP&A · Controllership & accounting · Audit · Treasury · Tax · Investment/equity research · Corporate finance/M&A · Finance leadership (Finance Manager → CFO)

### 29.2 What "good" means

Finance is the department where **credential inflation is highest and credential predictiveness is most conditional**. A qualification proves training; it does not prove judgement. The discriminating capabilities are: building a model whose assumptions are defensible; explaining a variance in business terms rather than accounting terms; and knowing which number is wrong before anyone else notices.

Sub-function matters more here than in almost any other department — an excellent auditor and an excellent FP&A manager share almost no competencies beyond the fundamentals.

### 29.3 Competency menu

| ID | Competency | Sub-function | Observable evidence |
|---|---|---|---|
| FI-01 | Financial modelling | FP&A, CF | Builds a model from scratch; assumptions explicit and defended; handles sensitivity |
| FI-02 | Accounting technical depth | Control, Audit | Applies the applicable standard to a non-obvious transaction |
| FI-03 | Variance & performance analysis | FP&A | Explains *why* the number moved and what the business should do |
| FI-04 | Business partnering | FP&A, Leadership | Has changed an operating decision through financial insight |
| FI-05 | Controls & compliance | Control, Audit | Has designed or tested controls; handled an audit finding |
| FI-06 | Cash & working capital | Treasury, Leadership | Has managed liquidity, collections, or funding |
| FI-07 | Systems & data fluency | All | Advanced Excel; ERP; BI/EPM tools; can trace a number to source |
| FI-08 | Reporting & close discipline | Control | Has owned a close cycle and improved it |
| FI-09 | Valuation & transaction work | CF, IR | DCF/comps; has worked a live transaction |
| FI-10 | P&L ownership & scope | Leadership | Has owned a P&L of stated size and made trade-off decisions |
| FI-11 | Stakeholder communication | Leadership | Has presented to a board, investors or an audit committee |

### 29.4 Signature assessment
**The live modelling exercise (E4).** A short, realistic modelling task in Excel under observation. Score: structure, assumption transparency, error-checking behaviour, and — most revealing — what they do when given an inconsistent input. Strong candidates flag it. Weak candidates build on it.

**The variance narration probe (E4).** Give a variance table and ask what they would say to the CEO. This separates people who *report* from people who *analyse*, and it is the single best FP&A discriminator available.

**The judgement probe (E4, accounting/audit).** A non-obvious recognition or classification question with no clean answer. Score reasoning and awareness of the alternative treatment, not the conclusion.

### 29.5 Credential logic

```
IF role requires statutory sign-off (statutory audit, statutory reporting)
   → the applicable professional qualification is a HARD requirement
IF role is controllership, tax, or technical accounting
   → the qualification is strong positive evidence; weight high
IF role is FP&A, business finance, or corporate finance
   → qualification is moderate positive evidence; MODELLING CAPABILITY
     OUTWEIGHS IT and the weight vector must reflect that
IF role is investment analysis
   → investment-specific credentials carry weight, but demonstrated
     analytical judgement outweighs them
```

All credential claims are verified with the issuing body — this is a department where verification is cheap and misrepresentation is materially consequential.

### 29.6 Validation probes
- "Walk me through the three assumptions in your model that mattered most and how you set them."
- "Tell me about a number you were asked to produce that you pushed back on."
- "What did you find in a close that nobody else had noticed?"
- "Describe a forecast you got badly wrong. What did you miss?"
- "What was the size of the P&L you owned, and what were you actually allowed to decide?"

### 29.7 Gaming vectors and red flags
Template models presented as built (require a live build) · P&L "ownership" that was reporting (probe decision rights explicitly) · credential claims (verify) · impact numbers with no baseline (probe the measurement) · confidentiality used to avoid all specificity (offer the abstraction route per §6.6, but a candidate who cannot discuss *any* mechanism is a genuine gap).

### 29.8 Fresher variant
Weight: modelling under observation; accounting fundamentals reasoning; internship with a reference; qualification progress. A fresher who can build a clean, well-structured model live outranks one with better exam results and no demonstrated build — and this should be stated to clients who over-index on ranks.

---

## 30. LEADERSHIP, GENERAL MANAGEMENT & EXECUTIVE

### 30.1 Role families
First-line manager · Senior manager · Director/VP · Business head/GM · Functional head · CXO

### 30.2 What "good" means

At leadership level, **D1 falls and D2/D3 rise sharply**, and the single most common evaluation failure is mistaking *presence* for *capability*. Senior candidates are almost universally polished interviewers. Polish is not evidence.

The evidence that matters: attributable business outcomes at relevant scope; the situation match (a turnaround leader and a scale-up leader are different people); demonstrated ability to build capability in others; and decision quality under incomplete information — including decisions that went wrong.

### 30.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| LD-01 | Business outcome ownership | Owned P&L/function/business unit; outcomes attributable and corroborated |
| LD-02 | Situation-appropriate leadership | Has led in the *specific* situation type this role requires |
| LD-03 | Team building & talent development | Has hired, grown and lost people; can name people they developed and what happened to them |
| LD-04 | Strategic judgement | Has made a consequential strategic choice and can defend the reasoning, including the rejected option |
| LD-05 | Organisational execution | Has moved a large organisation to do something different |
| LD-06 | Stakeholder & board management | Has managed upward, sideways and externally under pressure |
| LD-07 | Decision quality under uncertainty | Can describe a decision made on incomplete information and its consequences |
| LD-08 | Change and resistance handling | Has driven change against genuine internal resistance |
| LD-09 | Financial and commercial literacy | Understands the economics of the business they run |
| LD-10 | Self-awareness & failure ownership | Can describe a failure they caused, without deflection |
| LD-11 | Values and integrity under pressure | Has made a costly decision on principle |

### 30.4 The situation-match discipline

The single highest-value discrimination in executive hiring. Classify the mandate (§18.4) and require **evidence of the same situation type**:

| Mandate | Required evidence | Common mis-hire |
|---|---|---|
| Turnaround | Has fixed something broken; has cut; has removed people | A scale-up leader who only knows how to add |
| Scale-up | Has operated at the *next* scale, not just the current one | A big-company operator who has never built |
| Greenfield | Has built a function from zero without inherited structure | A leader who has only inherited working machines |
| Steady-state / optimise | Has sustained and incrementally improved | A change-junkie who breaks working systems |
| Succession | Trajectory and readiness evidence | Over-indexing on current performance |

### 30.5 Signature assessments
- **The decision archaeology probe (E4).** Take one consequential decision and go three layers down: what did you know, what did you not know, who disagreed, what did you decide, what happened, what do you think now. Polished candidates handle layer one. Layer three is where real experience shows.
- **The failure probe (E4, mandatory at director+).** "Describe a decision of yours that cost the business materially." A senior leader with no such answer is either inexperienced or not being straight — both are findings.
- **The scope-verification probe (E4).** Exactly what decisions were yours to make? What did you need approval for? Who else could veto you? This catches title inflation better than any other question.
- **Multi-reference triangulation (E5, mandatory).** Minimum three references at executive level, ideally including someone who reported *to* them.

### 30.6 Reference discipline at leadership level

References are candidate-chosen and often coached. Handling:
- Ask for specific, closed questions, not general impressions
- Ask each reference the *same* structured questions to enable comparison
- Ask about scope and decision rights, which are factual and hard to shade
- Ask "what would they need support with in a role that requires X" — a reference who says "nothing" is a low-value reference
- Weight downward-facing references (former direct reports) heavily, where obtainable
- Treat reference data as corroboration, never as proof

### 30.7 Gaming vectors and red flags
Title/scope inflation (scope probe + verification) · claiming credit for organisational outcomes they were present for (attribution probe, multi-reference) · rehearsed narratives (decision archaeology defeats these) · no failure story · cannot name people they developed · strategic vocabulary with no operational specifics · every prior situation described as a success.

### 30.8 Executive-specific weight note
D1 remains at 0.12–0.20 and never goes to zero: functional credibility still matters, and a leader who cannot engage with the substance of their function loses their team. But the bulk of the weight sits on D2 (attributable outcomes) and D3 (situation match).

---

## 31. HUMAN RESOURCES

### 31.1 Role families
HR generalist/HRBP · Talent acquisition · Compensation & benefits · L&D · HR operations/HRIS · Employee relations · HR leadership/CHRO

### 31.2 What "good" means

HR is evaluated on **business outcomes achieved through people mechanisms**, not on process administration. The discriminating capability is diagnostic: can this person identify what is actually causing an organisational problem, rather than deploying a standard intervention?

### 31.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| HR-01 | Organisational diagnosis | Identified a root cause behind a people symptom and can show the evidence they used |
| HR-02 | Business partnering | Changed a business decision through people insight; is trusted by line leaders |
| HR-03 | Talent acquisition capability | Has built or run hiring that measurably improved quality, not just speed |
| HR-04 | Compensation & benefits design | Has designed or restructured pay structures with defensible logic |
| HR-05 | Employee relations & risk | Has handled serious ER cases, including ones with legal exposure |
| HR-06 | Employment law & compliance | Applies applicable labour law to real situations |
| HR-07 | HR systems & data | Has implemented or run HR technology; uses data to argue |
| HR-08 | Learning & capability building | Has built capability that changed performance, with evidence |
| HR-09 | Change & culture work | Has driven organisational change with measurable behavioural outcomes |
| HR-10 | Performance & talent management | Has run calibration, succession, or performance systems that were actually used |
| HR-11 | HR leadership | Has owned the function; sat at the leadership table; influenced strategy |

### 31.4 Signature assessments
- **The diagnostic case (E4).** Present a real organisational symptom (attrition spike in one function; a failing performance cycle) and ask what they would look at, in what order, before doing anything. Strong HR people ask questions; weak ones propose programmes.
- **The ER judgement probe (E4).** A messy employee-relations scenario with competing obligations. Score the balance of legal exposure, fairness and business reality.
- **The metric probe (E4).** "What HR metrics did you actually change, and how did you know it was you?"

### 31.5 Red flags
Describes programmes rather than outcomes · attributes all attrition to compensation · no ER experience at a level claimed to have owned it · cannot cite applicable labour law relevant to their region · "culture" work with no behavioural measurement · HR technology "implementation" that was participation in someone else's project.

### 31.6 Note on hiring HR for Ready Pick Now clients
Where the role being hired is itself a hiring role, weight HR-03 with a specific probe: *"How did you know your hires were good? What did you measure, and how long did you follow them?"* Very few talent-acquisition professionals can answer this. Those who can are exceptional and should be identified as such.

---

## 32. SALES, MARKETING & BUSINESS DEVELOPMENT

### 32.1 Role families
Inside sales/SDR · Field sales/AE · Key account management · Channel/partnerships · Solution/pre-sales · Marketing (performance, product, brand, content) · Growth · Sales leadership

### 32.2 What "good" means

Sales is the department with the **highest claim inflation and the most verifiable underlying reality**. Quota attainment, territory, deal size, cycle length and product complexity are all checkable, and the gap between claimed and verified numbers is where the evaluation lives.

The critical distinction: **did they sell, or were they present while the product sold itself?** Selling a category leader in a growing market is a different capability from selling an unknown product against an incumbent.

### 32.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| SL-01 | Attainment track record | Quota, attainment %, ranking, consistency across periods and companies |
| SL-02 | Deal ownership & complexity | Deal size, cycle length, stakeholder count, competitive context |
| SL-03 | Prospecting & pipeline generation | Has self-generated pipeline, not just worked inbound |
| SL-04 | Discovery & qualification | Can demonstrate a real discovery conversation; qualifies out |
| SL-05 | Domain & product fluency | Understands what they sell deeply enough to be credible |
| SL-06 | Negotiation & commercial judgement | Has held price; has walked away; understands margin |
| SL-07 | Account growth | Has expanded existing accounts with evidence |
| SL-08 | Sales-process discipline | CRM hygiene, forecast accuracy — *forecast accuracy is a strong and underused signal* |
| SL-09 | Marketing: measurable outcome ownership | Owned a channel/campaign with attributable results |
| SL-10 | Marketing: analytical rigour | Understands attribution limits; does not overclaim |
| SL-11 | Sales leadership | Built and coached a team; improved team-level performance, not just their own |

### 32.4 Signature assessments
- **The deal walkthrough (E4).** One deal, end to end: how it started, who the stakeholders were, what nearly killed it, what they specifically did, what the competitor offered, why they won. Fabricated deals collapse under stakeholder-level questioning within three minutes.
- **The live discovery role-play (E4).** The single best sales assessment available. Score question quality, listening, and willingness to qualify out — not smoothness.
- **The lost-deal probe (E4).** "Tell me about a deal you should have won and lost." Absence of a real answer is a strong negative signal in a salesperson.
- **Forecast-accuracy probe.** "How accurate was your forecast, and how do you know?" Very few candidates can answer; those who can are usually genuinely disciplined.

### 32.5 The market-context adjustment
Attainment figures are near-meaningless without context. Always capture: was the territory new or inherited; what was the product's market position; what was the total quota; what was team-average attainment; was the market growing or contracting. A 90% attainment against a hard quota in a contracting market is stronger evidence than 140% in a boom, and the scoring must reflect that.

### 32.6 Gaming vectors and red flags
Unverifiable attainment (probe context, seek reference) · team results claimed individually · "managed a ₹X crore portfolio" that was account servicing (probe new-business split) · no lost deals · discovery role-play that is a pitch · marketing attribution claims with no acknowledgement of attribution limits.

---

## 33. OPERATIONS, SUPPLY CHAIN & LOGISTICS

### 33.1 Role families
Operations management · Supply chain planning · Procurement · Warehousing · Logistics · Production planning · Service delivery · Process excellence

### 33.2 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| OP-01 | Process design & improvement | Has redesigned a process with measured before/after |
| OP-02 | Planning & forecasting | Has owned a planning cycle; knows their forecast error |
| OP-03 | Vendor & procurement management | Has negotiated, qualified and managed suppliers; handled a failure |
| OP-04 | Inventory & working-capital discipline | Understands the cost of inventory decisions |
| OP-05 | Operational problem-solving | Has handled a real disruption under time pressure |
| OP-06 | Systems & data | ERP/WMS/TMS fluency; uses operational data |
| OP-07 | People & shift management | Has run frontline teams; understands attrition and absenteeism reality |
| OP-08 | Compliance & safety | Applies applicable regulatory and safety requirements |
| OP-09 | Cost management | Has reduced cost without breaking service |
| OP-10 | Scale and complexity handled | Volume, SKUs, sites, headcount, geography |

### 33.3 Signature assessments
- **The disruption probe (E4).** "A key supplier fails on a Friday and you ship Monday. Walk me through the next four hours." Score sequencing, prioritisation and stakeholder handling.
- **The metric-honesty probe (E4).** "What was your fill rate / OTIF / forecast error, and what was the definition you used?" Definitions vary enormously; candidates who know their own definition have genuinely owned the metric.
- **The trade-off probe (E4).** Cost vs service vs inventory — force them to choose and defend.

### 33.4 Red flags
Improvement claims with no baseline definition · never handled a real failure · "managed operations" with no volume or headcount numbers · cost reduction with no service consequence discussed (there is always one) · no awareness of frontline attrition reality in roles that require it.

---

## 34. SKILLED TRADES, BLUE-COLLAR & FRONTLINE WORKFORCE

### 34.1 Role families
Technicians (electrical, mechanical, HVAC, automotive) · Machine operators · Welders/fabricators · Drivers · Warehouse and material handling · Housekeeping and facilities · Security · Field service · Construction labour and supervision

### 34.2 What "good" means — and why this model is genuinely different

Almost everything written for white-collar hiring fails here, and applying it produces both unfair and inaccurate results. Three structural differences:

1. **The resume carries almost no information.** Many strong candidates have no written resume, or one produced by an agent. Scoring resume quality here is scoring literacy and access, not capability.
2. **The dominant risk is not competence — it is reliability.** Employers in these roles lose far more value to no-shows, early attrition, absenteeism and safety incidents than to skill gaps. The evidence model must reflect the actual failure mode.
3. **Practical demonstration is cheap, fast and decisive.** A thirty-minute practical test tells you more than any amount of interviewing, and it is the fairest instrument available because it does not depend on language fluency or self-presentation.

**Therefore the model inverts:** practical demonstration and reliability corroboration carry the weight; documents carry almost none.

### 34.3 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| TR-01 | Practical task competence | Performs the actual task to standard, under observation, within time |
| TR-02 | Tool and equipment handling | Uses the right tool correctly and safely without prompting |
| TR-03 | Safety judgement | Recognises hazards; follows and can explain safe procedure; stops work when unsafe |
| TR-04 | Quality and standard adherence | Work meets specification; self-checks; reports defects |
| TR-05 | Fault-finding | Diagnoses a simple failure systematically |
| TR-06 | Reliability and attendance | Verified attendance record from prior supervisor |
| TR-07 | Instruction-following and communication | Understands and confirms instruction; escalates appropriately |
| TR-08 | Situational judgement | Makes sound decisions in ambiguous frontline situations |
| TR-09 | Physical/role-specific requirements | Meets genuine, essential, uniformly-applied job requirements |
| TR-10 | Trade certification / licence | Holds required trade credential where legally or contractually mandated |
| TR-11 | Team and supervisor working | Works within a crew; takes direction; supports others |

### 34.4 The competency-to-stage map

Every competency must map to a specific assessment stage. **Any competency mapping to nothing is removed from the scorecard, and any stage assessing nothing is removed from the process.** This discipline eliminates the ritual interviewing that plagues frontline hiring.

| Competency | Stage that tests it |
|---|---|
| TR-01, TR-02, TR-04 | Practical test (≤30 minutes) |
| TR-03 | Practical observation + safety SJT + direct scenario question |
| TR-05 | Practical fault-finding task |
| TR-06 | Supervisor reference (the single highest-value signal in this department) |
| TR-07, TR-11 | Structured interview + practical observation |
| TR-08 | Situational judgement items, free-text or verbal response |
| TR-09 | Documented, uniformly applied requirement check |
| TR-10 | Credential verification with issuer |

### 34.5 Practical test design rules

- **Under 30 minutes.** Longer tests reduce completion and select for candidates who can afford the time, not the best candidates.
- **The actual task**, not an abstraction of it.
- **Scored on a written rubric** by a trained assessor, with the rubric shown to the candidate afterwards.
- **Language-independent wherever possible.** Instructions available in the candidate's language; do not let language fluency contaminate a skill measurement.
- **Paid or reimbursed** where it requires travel or significant time. This is both an ethics position and a completion-rate optimisation.
- **Safety-observed throughout.** Unsafe behaviour during a practical test is a legitimate and immediate finding.

### 34.6 Situational judgement design
Free-text or verbal responses rather than multiple choice — constructed responses are markedly harder to game and produce richer signal. Items must be drawn from the client's actual operating environment, not generic. Score on the reasoning, and always ask a follow-up: "what would you do if that didn't work?"

### 34.7 Reliability evidence — handled carefully

Supervisor references on attendance, punctuality and conduct are the strongest available predictor of the dominant failure mode. But:
- Ask factual, closed questions (attendance record, notice given, eligibility for rehire), not character judgements
- Never allow a reference to introduce a protected characteristic
- Recognise that reference availability is unequal — informal-sector work histories may have no contactable supervisor. **Absence of a reference is a gap, never a negative** (§6.6), and an alternative route (extended practical, probation-structured start, character reference) must be offered.

### 34.8 Fairness obligations specific to this department

This is the highest-risk department for discriminatory practice, and the runbook is deliberately strict:

- Physical requirements must be **essential to the job, documented, and uniformly applied**. Blanket physical criteria are prohibited.
- Language requirements must match actual job need. Requiring English fluency for a role performed entirely in a regional language is a proxy filter and is refused.
- Address, locality and community-correlated filters are prohibited (§12.4).
- Background-check requirements must be lawful, job-relevant, uniformly applied, and disclosed to the candidate.
- Age requirements are prohibited except where statutorily mandated for the specific work.

### 34.9 Red flags
Cannot demonstrate the core task despite claimed years of experience · unsafe behaviour during practical assessment · credential claim not verifiable with the issuer · verified attendance record materially inconsistent with claims · unwillingness to accept the disclosed shift or safety requirements *after honest disclosure*.

### 34.10 What is explicitly NOT a red flag
No written resume · gaps in informal work history · limited English · no reference from an informal employer · low formal education where the job does not require it · having changed employers frequently in industries where that is the norm.

---

## 35. NON-TECHNICAL SUPPORT & ADMINISTRATIVE

### 35.1 Role families
Administration · Executive assistance · Customer support · Back office/data processing · Reception/front office · Coordination roles

### 35.2 Competency menu

| ID | Competency | Observable evidence |
|---|---|---|
| NT-01 | Task execution accuracy | Completes a realistic work sample to standard |
| NT-02 | Written and verbal communication | Produces clear, appropriate written output; handles a call scenario |
| NT-03 | Prioritisation under competing demands | Orders a realistic conflicting workload and defends the order |
| NT-04 | Tool fluency | Demonstrated competence in the actual tools used |
| NT-05 | Discretion and confidentiality | Handles a scenario involving sensitive information appropriately |
| NT-06 | Service judgement | Handles a difficult customer/stakeholder scenario |
| NT-07 | Reliability | Attendance and follow-through corroborated |
| NT-08 | Process adherence and improvement | Follows process; has suggested or made an improvement |

### 35.3 Signature assessment
**The realistic work sample (E4).** Twenty to forty minutes of the actual work: process the emails, draft the response, resolve the scheduling conflict, handle the call. This is a far better predictor than interviewing, is fast, and is fair.

**The prioritisation exercise (E4).** Five competing demands, limited time, forced ordering with reasoning. Excellent discrimination for coordination and EA roles.

### 35.4 Notes
These roles are frequently under-assessed and over-interviewed. The runbook's position: replace the second interview with a work sample. Also note that AI tools have materially changed the written-output component — permit AI use in the sample and probe the candidate's editorial judgement over the output, which is now the actual skill.

---

## 36. ADDING A NEW DEPARTMENT

Ready Pick Now will encounter departments not covered above (legal, healthcare clinical, education, hospitality, media, agriculture, public sector). A new department model is added only through the following procedure, and never improvised mid-engagement:

```
1. IDENTIFY THE FAILURE MODE
   What actually goes wrong when these hires fail? Competence?
   Reliability? Judgement? Fit? Credential? This determines the
   weight vector shape more than anything else.

2. IDENTIFY WHAT IS EXPENSIVE TO FAKE
   List the evidence classes that a capable person can produce
   easily and an incapable person cannot. These become E4/E5.

3. IDENTIFY THE STATUTORY GATES
   What is legally required to perform this work in this
   jurisdiction? These become conditional hard disqualifiers.

4. WRITE THE COMPETENCY MENU (8–12 items)
   Each as an observable evidence statement.

5. DESIGN THE SIGNATURE ASSESSMENT
   One instrument that does most of the discriminating work.
   If you cannot name it, the model is not ready.

6. WRITE THE VALIDATION PROBES
   Participatory, not retrievable (§21.6).

7. IDENTIFY THE GAMING VECTORS
   How would a motivated candidate fake this, and what defeats it?

8. WRITE THE FRESHER VARIANT
   What evidence exists when there is no track record?

9. SET PROVISIONAL WEIGHTS
   Mark them explicitly as unvalidated hypotheses.

10. CALIBRATE
   The model is PROVISIONAL until it has 20+ outcome data points.
   Provisional models are labelled as such in client dossiers, and
   recruiter override rates on them are monitored closely.
```

Steps 1, 2 and 5 are where the intellectual work lives. Steps 4 and 6 are largely mechanical once those three are done.

---

# PART VII — THE AUTHENTICITY DOCTRINE

## 37. The strategic position

### 37.1 What we are actually fighting

We are not fighting "AI use." Using AI to write a resume, prepare for an interview, or draft a take-home solution is ordinary professional behaviour and will be universal within a year. Treating it as misconduct is both futile and unfair.

We are fighting **the decoupling of presentation from capability**. The problem is not that a resume was AI-assisted; it is that a resume no longer carries information about the person. The solution is not to restore the resume's credibility — that is impossible — but to **move the decision onto evidence that AI cannot supply on the candidate's behalf.**

### 37.2 The three-part doctrine

```
1. SHIFT THE WEIGHT
   Move decision weight to evidence classes that are expensive to
   fabricate (E4/E5). This is structural and permanent.

2. TRIANGULATE
   Require corroboration across independent sources. Consistency is
   the signal; contradiction is the finding.

3. VERIFY LIVE
   At least one synchronous, unprepared interaction per candidate at
   every level above entry — because preparation time is the resource
   that gaming depends on, and live interaction removes it.
```

Detection tools are a distant fourth, and they are used only as **review triggers**, never as scores and never as rejections.

### 37.3 Why we do not build or buy a "cheating detector" as a primary control

- Detectors are adversarial systems in an arms race they structurally lose
- False positives cause real harm to real people's employment
- Detector outputs are not explainable to a candidate or a client
- Detection accuracy claims by vendors are self-reported and unaudited
- Most importantly: **detection answers the wrong question.** We do not need to know whether AI was used. We need to know whether the capability exists.

## 38. The triangulation protocol

### 38.1 The six independent sources

| # | Source | What it establishes | Independence group |
|---|---|---|---|
| 1 | Resume / profile | Claims to be tested | `self_written` |
| 2 | Validation questionnaire | Specificity of claims | `self_structured` |
| 3 | Assessment | Demonstrated capability | `assessment` |
| 4 | Work artefacts | Production evidence | `artefact` |
| 5 | Live interaction | Unprepared reasoning | `live` |
| 6 | References | External corroboration | `third_party` |

### 38.2 The triangulation matrix

For each High-materiality claim, the system builds:

| Claim | Resume | Validation | Assessment | Artefact | Live | Reference | Verdict |
|---|---|---|---|---|---|---|---|
| "Led production migration" | Claimed | Specific mechanism given | Design probe consistent | IaC history consistent | Reconstructed decisions | EM confirmed ownership | **Corroborated (5 groups)** |
| "Reduced cost 34%" | Claimed | Vague | n/a | n/a | Could not reconstruct baseline | Not asked | **Uncorroborated** |
| "Expert Kubernetes" | Claimed | Confident | Operational gaps | n/a | Could not debug networking | n/a | **Contradicted (moderate)** |

Three claims, three different verdicts, from the same candidate. The dossier reports all three. The client sees a nuanced, truthful picture rather than a single number — and importantly, the second row is *not* treated as dishonesty. It is treated as an unverified claim, which is a different and much more common thing.

### 38.3 The specificity gradient

The core mechanism of the validation instrument. A claim is probed at increasing specificity until either the candidate demonstrates participatory knowledge or the probe exhausts.

```
LEVEL 1  What did you do?              → anyone can answer
LEVEL 2  How did you do it?            → a well-read person can answer
LEVEL 3  Why that way and not the      → someone who considered
         obvious alternative?             alternatives can answer
LEVEL 4  What went wrong, and what     → only someone who lived it
         did it cost?                     can answer
LEVEL 5  What would you do differently → only someone who reflected
         and what did you learn that      on having lived it
         you didn't expect?
```

**Levels 4 and 5 are the discriminators.** Generative models produce plausible Level 1–3 content effortlessly. They produce generic, unfalsifiable Level 4–5 content — "we faced some scaling challenges but overcame them through collaboration." Real experience produces specific, slightly unflattering, and often surprising Level 4–5 content.

**Design rule for all validation instruments across all departments: at least 40% of probe items must sit at Level 4 or 5.**

### 38.4 Live verification requirements

| Level | Minimum live requirement |
|---|---|
| Entry / trades | Practical demonstration (may be in person or supervised remote) |
| 2–5 years | One live technical/functional probe |
| 5–10 years | One live probe + one behavioural deep-dive |
| 10+ / leadership | Live decision-archaeology session + references |
| Executive | Extended structured session + multi-reference triangulation |

**Identity continuity** is checked across stages: is the person in the live session the same person who did the assessment and the same person the references describe? This is done through continuity of substance (they can discuss their own assessment work), not through invasive biometric measures.

### 38.5 Assessment integrity signals

Where an assessment platform provides integrity signals, they are treated as follows:

| Signal | Handling |
|---|---|
| Similarity/plagiarism flag | Review trigger only. Route to human with the comparison shown. |
| Off-screen attention patterns | Review trigger only, at low weight — highly prone to false positives (disability, environment, note-taking) |
| Unusual typing/editing patterns | Review trigger only, very low weight |
| Copy-paste events | Contextual note only; copy-paste is normal engineering behaviour |
| Multiple identity mismatches across stages | Escalate to HR Manager |

**No integrity signal ever contributes a number to a score.** Every one of them routes to a human, who decides. This is Contract C5 and is non-negotiable.

### 38.6 Artefact authenticity

| Artefact | Verification approach |
|---|---|
| Code repository | Sustained-authorship analysis; signed commits raise to E5; recency-of-creation vs claimed-period check (T4 contradiction); ask about a specific commit |
| Design portfolio | Process artefacts (sketches, iterations, research); attribution probe; live critique of their own work |
| Documents/reports | Ask about a specific decision inside it; ask what was cut and why |
| Models/spreadsheets | Live rebuild of a section; ask about an assumption |
| Drawings/CAD | Dimension and tolerance probe; manufacturing feedback probe |
| Publications | Authorship position; specific contribution probe; verification with the publication record |

### 38.7 The honest-candidate protection

A doctrine focused on catching misrepresentation will, if not deliberately balanced, harm honest candidates who are poor self-presenters. Explicit protections:

- **Nervousness is not inconsistency.** Assessors are trained to distinguish poor communication from absent knowledge, and to probe again in a different form.
- **Language fluency is not competence.** For non-native-language candidates, allow response in their preferred language wherever possible, and never let fluency contaminate a technical score.
- **Modest self-description is rewarded, not punished.** A candidate whose claims are *understated* relative to demonstrated capability receives a D4 score at the top of the range.
- **"I don't know" is a positive signal.** Explicitly scored as such under D1 reasoning quality and D4 self-representation accuracy.
- **Every contradiction gets a chance to be explained** (§13.2, Step 4). Denying this is a process defect, not a shortcut.
- **Neurodivergent and disabled candidates** may need adjusted formats. Adjustments are offered proactively, not on request only, and never noted in the score.

### 38.8 What we tell candidates

Transparency is both ethical and strategically correct — candidates who know the evaluation is evidence-based behave differently, and better. Every candidate is told, before assessment:

- That evaluation is based on demonstrated evidence, not resume polish
- That AI use on take-home work is permitted and that they will be asked to explain their work
- That claims will be verified across sources, and that unverifiable claims are marked as such rather than treated as false
- What will be asked of references, and when
- That they may request format adjustments
- That a human makes every consequential decision

This disclosure does not weaken the system. It strengthens it: it removes the incentive to fabricate, since fabrication now has a known cost and no benefit, and it improves completion rates because the process feels fair.

---

# PART VIII — CANDIDATE-STATE LOGIC

*Different candidate situations require different evidence models. Applying the standard model to a non-standard situation is a primary source of both inaccuracy and unfairness.*

## 39. The state model

| State | Trigger | Core adjustment |
|---|---|---|
| **Fresher** | <1 year professional experience | D2 suspended; D1/D5 raised; academic and project evidence elevated |
| **Early career** | 1–3 years | D2 reduced; D5 elevated |
| **Standard experienced** | 3–15 years | Baseline |
| **Senior/executive** | Leadership scope | D2/D3 dominant; D1 reframed as judgement |
| **Career break returner** | Gap ≥6 months | Currency probed, not penalised; break never scored |
| **Career changer** | Switching function/industry | Transferable-skill route; adjacency evidence; D5 elevated |
| **Domain switcher (same function)** | Same craft, new industry | Domain-specific knowledge separated from craft; D1 split |
| **Freelancer / consultant** | Non-linear employment | Portfolio and client references replace employer references |
| **Informal-sector** | No documented employment history | Practical demonstration dominant; documents near-zero weight |
| **Internal / referral candidate** | Known to client | Same evidence standard applies — see §40.9 |
| **Overqualified** | Materially above role scope | Not a disqualifier; flight risk stated in risk register |
| **Returning to a former employer** | Boomerang | Prior performance data (client-held) becomes E5 evidence |

## 40. State-specific protocols

### 40.1 Freshers

**Weight adjustment:** D2 → 0.04–0.06 (not zero; internships and academic projects can produce genuine outcomes). Redistribute to D1 (+0.08) and D5 (+0.08).

**Evidence hierarchy inversion:** for freshers, live demonstration is *even more* dominant, because there is no track record to corroborate against.

**What counts:**
- One project explained in depth, including failures (the single best fresher signal in every department)
- Live performance on a role-relevant task
- Learning-velocity evidence: how they acquired something recently and how fast
- Internship work with a supervisor reference
- Academic work only where it can be probed live

**What does not count:**
- Certificate counts, course completions, badge collections
- Portfolio breadth (explicitly anti-weighted: six shallow projects rank below one deep one)
- Institutional pedigree beyond the §8.9 cap
- Marks/GPA beyond a modest signal — weak predictor and correlated with access

**The fresher probe set:** "Show me something you built that didn't work." · "What did you have to teach yourself, and how?" · "Which part of this did someone help you with?" *(honesty probe — rewarded)* · "What would you do differently now?"

### 40.2 Career break returners

- The break is recorded, not scored. Ever.
- One neutral question, and only if the client's Layer 2 permits it: "Anything about the gap you'd like to add? It's not a factor in our evaluation."
- Currency is probed only where the domain clock is fast (§6.3), and identically to how it would be probed for a continuously employed candidate — the test is the same, not harder.
- Where a candidate has used the break productively (study, freelance, caregiving with transferable coordination responsibilities), that evidence is admissible on the same terms as any other.
- A returner-specific consideration for the dossier: some clients offer structured returnship support. Where Layer 2 records this, note it — it changes the client's own risk calculus.

### 40.3 Career changers

Three-part evaluation:
1. **Transferable competence** — which competencies genuinely carry across? Score at full weight.
2. **Domain gap** — what must be learned? Score as a stated gap with a time estimate, not as a deficiency.
3. **Transition evidence** — has this person successfully made a comparable transition before? This is the single best predictor and should be probed specifically.

Do not require the changer to demonstrate domain knowledge they could not have acquired. Require them to demonstrate the *capability to acquire it*, evidenced by having done so previously.

### 40.4 Freelancers and consultants

- Client references replace employer references and are equally valid at E5
- Attribution probing is more important, not less — consulting work is often advisory, and the distinction between "recommended" and "implemented" is material
- Portfolio breadth is genuine evidence here (unlike for freshers), because varied client work is the job
- Employment-gap logic does not apply at all
- Probe: "Which of your recommendations were actually implemented, and what happened?"

### 40.5 Informal-sector and undocumented work histories

Common and important in India and across emerging markets. Protocol:
- Practical demonstration carries dominant weight
- Verbal work-history reconstruction is accepted as E1 evidence and probed for specificity
- Where no formal reference exists, accept a supervisor or contractor reference regardless of documentation status, or a character reference at reduced tier
- Never treat documentation absence as a negative
- Never require documents the candidate could not reasonably possess

### 40.6 Overqualified candidates

Not a disqualifier and never scored down. The genuine concerns — retention risk, compensation mismatch, potential frustration with scope — are **risk-register items disclosed to the client**, not score deductions. The candidate is asked directly and respectfully why the role interests them, and their answer is reported verbatim in substance. Many such candidates have entirely sound reasons (relocation, life stage, sector interest, wanting less scope deliberately), and blanket filtering on this basis loses excellent hires.

### 40.7 Internal and referred candidates

Subject to the **same** evidence standard, with one addition and one caution:
- **Addition:** the client holds performance data on internal candidates, which is E5 evidence and should be used where lawful and permitted.
- **Caution:** referral candidates receive systematically inflated evaluations through familiarity effects. The runbook requires that referred candidates be assessed with the same instruments, and that the referral relationship be recorded in the dossier so the client can see it.

### 40.8 Candidates who decline parts of the process

A candidate may legitimately decline: references before offer stage, an unpaid long take-home, sharing confidential artefacts, or a specific assessment format. Protocol:
- Record the decline and the stated reason
- Offer an alternative evidence route
- If no route exists, the competency becomes NOT_PROVIDED with confidence reduced, and the dossier says exactly that
- **A decline is never recorded as a negative behavioural signal.** Candidates who protect confidentiality obligations are demonstrating integrity, which is positive evidence.

### 40.9 Seniority mis-labelling

A candidate applying above or below their demonstrated level is common and is not misrepresentation. Where demonstrated capability sits materially below the claimed level, the correct output is: *"Not a fit for this role at this level; strong evidence of fit for [the level below]"* — with a recommendation that they be considered for a different opening if one exists. This turns a rejection into a placement opportunity and is commercially valuable as well as fair.

---

# PART IX — HUMAN–AI INTERACTION, OVERRIDE AND ACCOUNTABILITY

## 41. The division of labour

| The system does | The human does |
|---|---|
| Structure evidence into the graph | Judge whether the evidence is adequate |
| Apply declared rules consistently | Decide whether the rules fit this situation |
| Surface contradictions and gaps | Resolve contradictions with the candidate |
| Compute scores and confidence from declared weights | Override with reason where judgement differs |
| Explain every score in terms of evidence | Decide who to interview and who to hire |
| Flag anomalies | Disposition every flag |
| Track calibration | Change the rules |

**The system never decides. The system makes deciding better.**

## 42. Override discipline

### 42.1 Overrides are expected, healthy and informative

An override rate of zero means either the humans have stopped thinking or the system has stopped being challenged. Both are failures. Overrides are the primary channel through which the model learns it is wrong.

### 42.2 Override protocol

Every override requires four fields, and the system will not accept it otherwise:

```
1. DIRECTION        Promote / Demote / Exclude / Reinstate
2. REASON CATEGORY  Evidence the system lacked
                    Evidence the system misread
                    Weight is wrong for this role
                    Rubric level misapplied
                    Contradiction resolved offline
                    Client instruction
                    Recruiter judgement (uncategorised)
3. SPECIFIC BASIS   Free text — what specifically supports this?
4. EVIDENCE LINK    Which evidence node, or what new evidence added?
```

Overrides in the category "recruiter judgement (uncategorised)" are permitted but **tracked separately and reviewed monthly** — a rising rate of uncategorised overrides indicates either a model problem or a discipline problem, and it matters which.

### 42.3 The override diagnostic thresholds

| Observation | Diagnosis | Action |
|---|---|---|
| Override rate >20% in a department | Weights or rubric miscalibrated for that function | Standards Board reviews the department model |
| Override rate >30% for one client | Company DNA is inaccurate or incomplete | Re-run Layer 2 intake |
| Override rate >30% for one recruiter across clients | Individual calibration issue or a genuine insight the model lacks | Shadow review; determine which |
| Overrides cluster on one demographic direction | **Fairness incident** | Immediate fairness review; escalate to Standards Board |
| Override rate <3% sustained | Rubber-stamping — humans have disengaged | Introduce blind spot-checks |

The fourth row is treated with the highest seriousness of any monitoring signal in the runbook. Human override is a common vector by which bias re-enters a structured process, and it must be watched precisely because we encourage overriding.

### 42.4 Who may override what

| Override | Recruiter | Hiring Manager | HR Manager |
|---|---|---|---|
| Rank position | ✔ | ✔ | ✔ |
| Exclude a candidate | ✔ with reason | ✔ with reason | ✔ |
| Reinstate an auto-filtered candidate | ✔ | ✔ | ✔ |
| Clear an integrity flag | — | — | ✔ |
| Override a hard disqualifier | — | — | ✔ (logged, reviewed) |
| Change a frozen configuration | — | propose | ✔ (creates new version) |

### 42.5 The blind spot-check

Monthly, a random sample of 5% of evaluated candidates is re-evaluated independently by a second recruiter without seeing the first evaluation or the system score. Divergence between the two humans is measured alongside divergence from the system. This is the only way to distinguish "the system is wrong" from "the recruiter is idiosyncratic," and both findings are valuable.

## 43. The recruiter dossier (the delivered artefact)

### 43.1 Structure

```
1. HEADLINE
   Name (or anonymised ID), current position, Ready Pick Score,
   band, confidence level, one-sentence summary of the fit case.

2. WHY THIS CANDIDATE (the fit rationale)
   Written against the SWOT-derived priorities, not the JD.
   3–5 sentences. Names the top-weighted competency and the
   evidence that satisfies it.

3. DIMENSION BREAKDOWN
   Five scores, the active weights, and for each dimension:
   the two or three pieces of evidence that drove it.

4. EVIDENCE LEDGER
   Every material claim, its status, its sources, its independence
   count, its best tier. (§7.2 format.)

5. AUTHENTICITY SUMMARY
   Consistency findings. Verifications completed. Open questions.
   Written neutrally: "we could not verify X" not "candidate claims X."

6. RISK REGISTER
   Missing evidence. Unresolved contradictions. Practical risks
   (notice, comp band, relocation, counter-offer). Retention signals.

7. WHAT TO ASK IN INTERVIEW
   Three to five specific questions derived from the gaps and
   unresolved items. THIS IS THE MOST USED SECTION OF THE DOSSIER
   and should be written with care.

8. COMPARISON NOTE
   Why this candidate sits above/below adjacent candidates, and
   whether the separation is meaningful or within band overlap.

9. CONFIGURATION FOOTER
   Scorecard version, weight vector, evaluation date, evaluator.
```

### 43.2 Writing standards

- **Never** write "the AI determined" or "the model scored." Write what the evidence showed.
- **Never** state a claim as fact if it is uncorroborated. Write "states that" or "we could not verify."
- **Never** hide a weakness. A dossier with no risks listed is an incomplete dossier and will be rejected at Gate G4.
- **Never** use unfalsifiable praise ("great communicator," "strong culture fit"). Every positive statement must attach to evidence.
- Write for a hiring manager with fifteen minutes, not for an HR audit. The audit trail sits underneath; the top of the dossier is for deciding.

### 43.3 The candidate-facing obligation

Where a candidate requests feedback, they receive a version containing: the competencies assessed, how they performed, what evidence would have strengthened their case, and — where applicable — the fact that a claim could not be verified and how they could verify it in future. This is disclosed as available at the start of the process.

We do **not** disclose weights, other candidates' information, or client-confidential SWOT content.

---

# PART X — CALIBRATION AND LEARNING

## 44. Why calibration is the whole game

Every weight, tier and threshold in this runbook is a hypothesis (Axiom 13). A hiring system that never checks its predictions against outcomes is not intelligent — it is merely consistent, and consistency without accuracy is just efficient error.

Calibration is also the only honest basis for the commercial promise. We can say "our shortlists outperform" only when we have measured it.

## 45. The outcome data model

### 45.1 What we collect, and when

| Stage | Outcome captured | Available |
|---|---|---|
| Shortlist delivery | Which candidates the client advanced | Days |
| Interview | Client's stage-by-stage decisions and stated reasons | 1–3 weeks |
| Offer | Offered / declined / accepted, and reason | 3–6 weeks |
| Join | Joined / no-show | 4–10 weeks |
| 90 days | Still employed; manager's early assessment | 3 months |
| 6 months | Performance rating or structured manager assessment | 6 months |
| 12 months | Performance; retention; promotion trajectory | 12 months |

### 45.2 The three prediction targets

| Target | Signal | Available |
|---|---|---|
| **Advancement** | Did the client interview them? | Fast, noisy, biased by client preference |
| **Selection** | Did they get the offer? | Medium speed, better signal |
| **Performance** | Did they do the job well? | Slow, best signal, hardest to obtain |

Optimising only for the fast signal (advancement) trains the system to predict *client taste*, including the client's biases. Performance is the true target and must anchor calibration even though it arrives twelve months late. This tension is permanent and must be managed consciously, not resolved by convenience.

## 46. Calibration metrics

| Metric | Definition | Target | Action if missed |
|---|---|---|---|
| **Shortlist precision** | Shortlisted candidates who advanced to a later interview stage | ≥60% | Review D3 weights and SWOT quality |
| **Top-3 hit rate** | Hires drawn from our top 3 | ≥40% | Review ordering and band logic |
| **Rank–outcome correlation** | Correlation between RPS and 6-month performance | Positive and rising | Weight recalibration |
| **Incremental validity** | RPS predictive power vs. resume-similarity baseline | RPS must beat baseline materially | If not, the added signals are not earning their complexity (Axiom 14) — simplify |
| **Confidence calibration** | High-confidence candidates should outperform Low-confidence ones | Monotonic | Confidence formula revision |
| **Flag precision** | Authenticity flags confirmed on review | ≥60% | Raise thresholds or retire the flag (§13.5) |
| **Override rate** | By department, client, recruiter | 5–20% | See §42.3 |
| **Dimension utility** | Does each dimension add incremental prediction? | All five positive | A dimension that never adds is a candidate for removal |
| **Adverse impact ratio** | Selection rate by group vs. highest group | ≥0.80 as a monitoring trigger | Investigate the mechanism |
| **Evidence yield** | % of requested evidence actually obtained | ≥70% | Process or candidate-experience problem |
| **Candidate completion** | % who complete the validation and assessment | ≥65% | Instrument is too long, unclear, or feels unfair |

## 47. The calibration cycle

```
QUARTERLY — Standards Board

1. ASSEMBLE
   All closed roles from the quarter with outcome data.

2. MEASURE
   Every metric in §46, by department, seniority and client.

3. DIAGNOSE
   For each miss, identify the layer at fault:
     - Evidence layer? (we collected the wrong things)
     - Rubric layer? (we rated it wrongly)
     - Weight layer? (we valued the wrong things)
     - Intake layer? (we were told the wrong problem)
   Intake failures are the most common and the most correctable.

4. HYPOTHESISE
   Propose specific changes with the expected effect stated
   in advance. "We think D5 is over-weighted for mid-level
   engineering; reducing it 0.04 should improve rank–performance
   correlation." Stating the expected effect BEFORE the change
   is what makes the next cycle a test rather than a rationalisation.

5. CHANGE
   Version the department model. Record the change and the
   hypothesis in the calibration ledger.

6. VERIFY
   Next cycle, check whether the predicted effect occurred.
   If it did not, revert and record the failed hypothesis —
   failed hypotheses are retained permanently, because a model
   that only remembers its successes will repeat its errors.
```

## 48. The per-client calibration review

Held after the first shortlist of every role, and quarterly at relationship level.

**After the first shortlist (30 minutes, mandatory):**
- Hiring manager reacts to five real candidates
- Recruiter probes every reaction: "what specifically made that a no?"
- Reactions that reveal unstated criteria are converted into scorecard amendments (creating configuration v2)
- Reactions that reveal *stated* criteria being ignored are reflected back — this is the DNA drift check at role level

**Quarterly (60 minutes):**
- Our predictions vs. their outcomes, honestly presented including misses
- Company DNA drift check (§17.3)
- Weight adjustments proposed and agreed
- Process friction: completion rates, candidate feedback, turnaround

## 49. Guarding against pathological learning

Three failure modes the calibration loop can itself create, and their controls:

| Failure mode | Mechanism | Control |
|---|---|---|
| **Bias amplification** | Learning from client decisions teaches the model the client's biases | Anchor on performance not selection; adverse-impact monitoring on every cycle; exploration slots |
| **Homogenisation** | Optimising for past success narrows the profile until only one template scores well | Exploration slots (§10.10); monitor profile diversity of shortlists over time; track whether high-variance candidates who were hired performed well |
| **Survivorship blindness** | We only see outcomes for candidates who were hired, so we never learn about the strong candidates we rejected | Track rejected candidates who were hired elsewhere at higher levels where visible; treat client-rejected-but-we-rated-highly cases as priority review items |

The third is the hardest and is only partially solvable. It is recorded here so that nobody mistakes an unmeasurable error for an absent one.

---

# PART XI — FAIRNESS, COMPLIANCE AND ETHICS

## 50. The position

Ready Pick Now handles decisions that materially affect people's livelihoods. The runbook treats fairness as a design constraint with the same standing as accuracy — and in several places as a constraint that overrides client preference (§3.5, §12.4).

Two practical reasons reinforce the ethical one. First, unfair systems are inaccurate systems: proxy filters remove capable candidates and therefore degrade the shortlist we are paid to produce. Second, regulatory exposure on automated employment decision tools is expanding globally, and a system designed for auditability from the start absorbs that expansion without redesign.

## 51. Where bias actually enters

| Entry point | Mechanism | Control in this runbook |
|---|---|---|
| **Criteria selection** | Unobservable traits, "culture fit," criteria reverse-engineered from a favoured candidate | §16.3 observable-evidence requirement; §20.5 scorecard freeze; §3.5 refusal rules |
| **Sourcing** | Narrow channels reproduce existing demographics | Sourcing/scoring separation (§8.9); slate composition monitoring |
| **Evidence availability** | Public code, portfolios, references and unpaid tests correlate with free time and access | §8.8 no-GitHub rule; §6.6 Unknown discipline; paid/short practical tests; alternative evidence routes |
| **Proxy variables** | Pedigree, locality, tenure patterns, language fluency, gap history | §8.9 pedigree cap; §12.4 prohibited filters; language-fluency separation |
| **Rubric application** | Same evidence rated differently by candidate group | Anchored rubrics; blind spot-checks (§42.5); rater consistency monitoring |
| **Human override** | Familiarity and homophily re-enter through the override channel | §42.3 override direction monitoring — treated as the highest-priority signal |
| **Calibration** | Learning from biased decisions amplifies bias | §49 anchoring on performance; exploration slots |

Note that four of the seven entry points are **human**, not algorithmic. A runbook that only audits the model audits the wrong half of the system.

## 52. Structural fairness controls

### 52.1 Observable evidence only
Every criterion, at every layer, must be written as something that could be observed. This one rule does more fairness work than any downstream audit, because unobservable criteria cannot be applied consistently and therefore will be applied according to the evaluator's priors.

### 52.2 Anonymised first pass
Where the client's Layer 2 permits, the first evaluation pass excludes name, photograph, address, age indicators, institution names and employer brands. Evidence, claims and demonstrated performance remain. Identity is revealed only after competency scoring is complete. This is offered by default and is one of the most effective single interventions available.

### 52.3 Uniform process
Every candidate for a role receives the same instruments, the same probes, and the same evidence requests. Deviations require a recorded reason. "We didn't bother assessing that one because it was obvious" is how inconsistency and bias enter, and it is a process defect.

### 52.4 The proxy audit
Before a configuration is frozen, every criterion is tested against the proxy question: *could this criterion systematically exclude a group for a reason unrelated to job performance?* Criteria that fail are rewritten or removed. Common failures caught by this test: unnecessary language-fluency requirements, "immediate joiner" preferences (correlated with employment status and caregiving), blanket physical criteria, unnecessary travel requirements, "cultural alignment" language, and locality preferences.

### 52.5 Adverse impact monitoring
Where the client permits demographic data collection and it is lawful to do so, selection rates are monitored across groups at each stage. A ratio below 0.80 between the lowest and highest group rate triggers investigation — **of the mechanism, not automatically of the outcome.** Sometimes the mechanism is legitimate and explicable; sometimes it is a proxy nobody noticed. The point of the trigger is to find out which.

Where demographic data cannot lawfully or practically be collected, we monitor proxies of process fairness instead: completion rates by candidate segment, evidence-yield rates, adjustment requests granted, and override directions.

## 53. Compliance framework

### 53.1 Jurisdiction resolution

The runbook holds **rule shapes**; a jurisdiction table supplies the specifics. At configuration time the engine resolves:

```
work_location → jurisdiction
              → applicable employment/anti-discrimination law
              → applicable data protection regime
              → applicable licensure requirements for the role
              → applicable background-check permissions and limits
              → applicable AEDT/automated-decision obligations
```

Where a role spans jurisdictions, the **most protective** applicable standard governs.

### 53.2 India-first obligations (primary operating jurisdiction)

- **Data protection:** candidate personal data is processed on a lawful basis with clear notice and consent, collected only for the stated purpose, retained only as long as necessary, and deleted on request subject to legitimate retention needs. Consent language is explicit, specific, and not bundled.
- **Anti-discrimination:** constitutional protections and applicable statutes covering caste, religion, sex, place of birth, disability and other characteristics. Caste, religion, region and community are never collected, never inferred, and never used — directly or by proxy (§12.4). Proxies of particular concern in the Indian context: surname-based inference, locality filters, mother-tongue requirements not genuinely job-related, and institutional filters that track community concentration.
- **Disability:** reasonable accommodation offered proactively in every assessment; format adjustments never recorded in scoring.
- **Labour law:** verification of statutory eligibility where required; no collection of salary history as a ranking input.

### 53.3 International operation
Where Ready Pick Now serves clients hiring outside India, the configuration must resolve the local regime before sourcing. Notable shapes to encode: automated-decision-tool bias-audit and notice requirements in some jurisdictions; consent and data-transfer requirements under regimes with extraterritorial reach; sector-specific licensure gates; and restrictions on background-check content and timing.

### 53.4 Data handling standards

| Requirement | Standard |
|---|---|
| Collection | Only what a defined competency requires |
| Consent | Specific, informed, unbundled, withdrawable |
| Storage | Encrypted; access controlled by role (§4.2) |
| Retention | Per client agreement; default 12 months post-decision |
| Deletion | On request, subject to legitimate retention; confirmed to the candidate |
| Sharing | Only with the client, only what the dossier standard defines |
| Cross-border | Per the applicable regime; recorded in Layer 2 |
| Vendor data | **Candidate evidence must reside in Ready Pick Now's own data layer**, not solely in third-party assessment or sourcing vendor systems — this is both a compliance and a strategic requirement |

### 53.5 The transparency obligation

Candidates are told, before evaluation: that AI-assisted evaluation is used; what it does and does not decide; that a human makes every consequential decision; what data is collected; how long it is kept; and how to request feedback, adjustment or deletion. This is disclosed plainly, not buried.

## 54. Ethical commitments beyond compliance

1. **We do not waste candidate time.** Assessment length is proportionate to the role. Long unpaid exercises are avoided; where substantial time is required, it is compensated.
2. **We tell candidates the truth about the job**, including the difficult parts identified in the SWOT threats (§19.4). Retention failures caused by misrepresentation are our failure, not the candidate's.
3. **We give feedback where requested.** A candidate who spent hours on our process is owed something.
4. **We do not use dark patterns** in candidate communication — no false urgency, no fabricated competition, no misleading role framing.
5. **We do not sell or repurpose candidate data.**
6. **We refuse configurations we believe are discriminatory**, even at commercial cost, and we say why.
7. **We report our own errors.** When calibration shows we were wrong about a candidate we rejected, that goes in the quarterly review with the client, not just in our internal ledger.

---

# PART XII — IMPLEMENTATION ARCHITECTURE

## 55. Architecture principles

1. **The rules live in configuration, not in prompts.** Weights, thresholds, tiers and disqualifiers are structured data the engine reads. Anything expressed only in natural-language instruction to a model will apply inconsistently and cannot be audited.
2. **Every model call has a narrow, verifiable job.** Broad "evaluate this candidate" calls produce unauditable results. Narrow calls ("extract claims," "rate this evidence against this rubric anchor," "identify inconsistencies between these two texts") produce checkable ones.
3. **Retrieval supplies rules, not judgement.** RAG injects the applicable department model, Company DNA and role scorecard. It does not decide.
4. **Every output carries its provenance.** No score exists without a link to the evidence and the rule that produced it.
5. **Determinism where possible.** Weighted aggregation, threshold application and gating are arithmetic, not inference. Only rubric rating and text analysis require a model.
6. **The pipeline must be replayable.** Given the same evidence and the same configuration version, the same result must be reproducible.

## 56. The pipeline

```
┌──────────────────────────────────────────────────────────────┐
│ 0. CONFIGURATION RESOLUTION                                  │
│    Company DNA (L2) + role scorecard (L3) + department model  │
│    (L1) + seniority + jurisdiction → frozen config vN         │
│    GATE G1: scorecard approved?                               │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 1. INGEST                                                    │
│    resume · validation responses · assessment output ·       │
│    artefacts · interview notes · reference responses ·        │
│    credential verification results                            │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 2. NORMALISE & EXTRACT   (narrow model calls)                │
│    → structured candidate object                              │
│    → CLAIM EXTRACTION with materiality assignment             │
│    → each claim mapped to scorecard competencies              │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 3. EVIDENCE TIERING      (deterministic + narrow model)      │
│    each evidence node → tier, provenance, independence group, │
│    specificity/attribution/scale/decay modifiers               │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 4. DIMENSION EVALUATORS  (five parallel, rubric-anchored)    │
│    each receives ONLY: the competencies in its scope, the     │
│    retrieved rubric anchors, and the evidence mapped to them  │
│    each returns: rubric level + citation to evidence node     │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 5. TRIANGULATION AGENT                                       │
│    cross-source consistency · contradiction detection with    │
│    type and severity · independence counting · benign-        │
│    explanation generation (§13.2 step 3) · authenticity score │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 6. AGGREGATION           (deterministic arithmetic)          │
│    competency → dimension → weighted composite →              │
│    authenticity multiplier → confidence → band → gates        │
│    GATE G2 evidence · GATE G3 integrity                       │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 7. EXPLANATION GENERATION                                    │
│    dossier assembly from the graph — every statement must     │
│    carry an evidence citation or it is not emitted            │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 8. HUMAN REVIEW → override log → GATE G4 → DELIVERY          │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ 9. OUTCOME CAPTURE → CALIBRATION LEDGER                      │
└──────────────────────────────────────────────────────────────┘
```

## 57. Component contracts

### 57.1 Claim extractor
**Input:** raw candidate documents and responses.
**Output:** claim list with type, materiality, competency mapping, source, and a verbatim span reference.
**Must not:** evaluate, rate, or infer beyond what the text states.
**Failure mode to guard:** inventing claims not present in the source. Every claim must carry a span reference into the source text, and claims without one are dropped.

### 57.2 Evidence tierer
**Input:** evidence node with provenance metadata.
**Output:** tier, modifiers, independence group.
**Mostly deterministic** — tier follows from evidence type and collection method, which are known. Only the specificity modifier requires model judgement.

### 57.3 Dimension evaluator (×5)
**Input:** competency set for this dimension, retrieved rubric anchors from the department model, evidence mapped to those competencies, seniority context.
**Output:** per-competency rubric level (0–100), each with mandatory citation to the evidence nodes that justified it.
**Must not:** see the other dimensions' scores, the candidate's name, or the composite. Isolation prevents halo effects, which are as real in models as in humans.

### 57.4 Triangulation agent
**Input:** the full claim/evidence graph.
**Output:** contradiction list with type and severity; independence counts; benign explanations generated; authenticity score with itemised basis.
**Must:** generate at least two benign explanations per contradiction before assigning severity above Minor (§13.2).

### 57.5 Aggregator
**Fully deterministic.** No model involvement. Implements §10 exactly. This is essential: the arithmetic must be reproducible, testable, and identical across runs.

### 57.6 Explanation generator
**Input:** the completed graph plus scores.
**Output:** the dossier per §43.1.
**Hard constraint:** every substantive statement must carry an evidence citation. The generator is architecturally prevented from emitting an uncited claim — this is enforced in code, not by instruction.

## 58. Retrieval design

**What is retrieved, per evaluation:**
- The department competency model and rubric anchors for the relevant seniority
- The Company DNA compilation artefact (§17.1)
- The frozen role scorecard and weight vector
- Jurisdiction rules for licensure, data and disqualifier legality
- The validation probe bank for the relevant competencies

**What is never retrieved into an evaluation:**
- Other candidates' evaluations (contamination and relative-ranking drift)
- Prior evaluations of the same candidate for other roles under other configurations
- The client's stated preference in free-text form (it must be compiled first, per §15)

**Retrieval quality requirement:** a skills/competency ontology is required so that vocabulary mismatch does not cause missed evidence — "graph database" and "semantic technologies," "GD&T" and "geometric tolerancing," "FP&A" and "business finance" must resolve to the same competency node. Pure vector similarity fails on this and will systematically undervalue candidates who describe their work in non-standard vocabulary, which correlates with non-standard backgrounds.

## 59. Data schema (core objects)

```yaml
Role:
  role_id, client_id, department, sub_function, seniority,
  jurisdiction, situation_type, jd_ref, swot_ref,
  scorecard_version, config_frozen_at, status

Scorecard:
  scorecard_id, role_id, version, approved_by, approved_at
  competencies:
    - id, name, must_or_nice, observable_evidence_statement,
      assessment_method, rank, weight, threshold, dimension
  disqualifiers: [ {condition, rationale, approved_by} ]
  weight_vector: {D1..D5}
  weight_derivation: {baseline, l2_modifiers, l3_modifiers, clamps}

Candidate:
  candidate_id, role_id, state (fresher/returner/changer/...),
  consent_record, evidence_graph_ref, config_version_scored_under

Evaluation:
  evaluation_id, candidate_id, config_version,
  competency_scores: [ {competency_id, score, evidence_refs[]} ]
  dimension_scores: {D1..D5 with evidence_refs}
  authenticity: {score, multiplier, findings[]}
  confidence: {score, label, components}
  rps: {raw, final, band, uncertainty_range}
  gates: {G1..G4 status}
  flags: [ {type, severity, disposition, dispositioned_by} ]
  overrides: [ {direction, category, basis, evidence_ref, by, at} ]

CalibrationRecord:
  role_id, candidate_id, our_rank, our_score, our_confidence,
  advanced, offered, joined, perf_90d, perf_6m, perf_12m,
  retained_12m, notes
```

## 60. Build sequencing

| Stage | Window | Deliverable | Exit benchmark |
|---|---|---|---|
| **1. Encode the taxonomy** | 0–60 days | Five dimensions; evidence tiers; six to eight department models with rubric anchors; baseline weight matrix | Two recruiters independently scoring the same 20 candidates agree within 8 RPS points on 80% of cases |
| **2. Intake compilation** | 30–90 days | Company DNA instrument + compiler; SWOT instrument + transformation pipeline; scorecard with force-ranking and approval gate | 100% of active roles have an approved ≤6-competency force-ranked scorecard; zero "everything is a must-have" configs |
| **3. Authenticity layer** | 60–120 days | Triangulation agent; contradiction taxonomy; independence counting; authenticity score; flag review queue | Flag precision ≥60%; zero auto-rejections; every flag human-dispositioned |
| **4. Explainability** | 90–150 days | Evidence graph with full citation enforcement; dossier generator; candidate feedback path | 100% of delivered dossiers pass the uncited-statement check |
| **5. Calibration loop** | 120–210 days | Outcome capture; calibration ledger; quarterly cycle; per-client review | First full cycle completed; RPS demonstrably out-predicts a similarity baseline on advancement, with 6-month data collection underway |
| **6. Fairness infrastructure** | Parallel, continuous | Proxy audit at config freeze; anonymised first pass; adverse-impact monitoring; override-direction monitoring | Monitoring live on 100% of roles; first fairness review completed |

**Sequencing rationale:** stages 1 and 2 must precede 3, because triangulation without a scorecard has nothing to triangulate *against*. Stage 4 must precede 5, because calibration requires knowing what drove each score. Stage 6 runs parallel throughout because retrofitting fairness controls is far more expensive than building them in, and because a fairness failure in month three is not repaired by a control shipped in month nine.

---

# PART XIII — OPERATING PROCEDURES

## 61. SOP-01 · Client onboarding (Layer 2 capture)

```
D+0   Kickoff. Explain the three-layer model and the Decision Contract.
D+2   Company DNA session (90 min) with HR Manager/CHRO.
D+3   Recruiter drafts the DNA compilation artefact (§17.1).
      EVERY statement must map to a concrete engine effect or be
      labelled "recruiter context only."
D+5   HR Manager reviews and approves the compilation.
D+5   Prohibited-filter confirmation signed.
D+5   Data, consent and retention terms confirmed.
D+7   Historical calibration data requested (chase persistently — it is
      the highest-value input available).
```

**Quality bar:** if the DNA compilation contains more than two "recruiter context only" items in the evaluation-philosophy section, the session was not structured enough. Re-run it.

## 62. SOP-02 · Role intake and configuration

```
1.  Receive role request; classify department, sub-function, seniority.
2.  Draft JD.
3.  Generate the SWOT instrument, pre-populated from L1 + L2.
4.  Run the SWOT session (§18.2). Confirm the situation type explicitly.
5.  Run the transformation pipeline (§19) on every SWOT element.
6.  Produce the draft scorecard: ≤6 competencies, force-ranked,
    observable evidence statements, assessment method per competency,
    thresholds, disqualifiers.
7.  Run the QC checks:
      [ ] Every competency has an assessment method
      [ ] Every disqualifier passes §12.3
      [ ] Proxy audit passed (§52.4)
      [ ] The "would this exclude your best current performer?" test passed
      [ ] Situation type confirmed by the hiring manager
      [ ] Weight vector within clamps; D4 ≥ 0.12
8.  HR Manager approval → GATE G1.
9.  Freeze configuration as v1. Post JD. Book the calibration review.
```

**Time budget:** 3–5 hours of recruiter time per role. This is the highest-leverage time in the entire engagement and must not be compressed. A role configured in twenty minutes will produce a shortlist nobody can defend.

## 63. SOP-03 · Evidence collection

```
STAGE 1 — Application evidence
  Resume/profile ingested. Claims extracted. Materiality assigned.

STAGE 2 — Validation questionnaire
  Generated per role from the competency set and the probe bank.
  40% minimum of items at specificity Level 4–5 (§38.3).
  Length: 20–35 minutes. Longer instruments destroy completion rates,
  and a 40% completion rate on a perfect instrument is worse than a
  75% completion rate on a good one.
  Candidate told: AI use permitted; you will be asked to explain.

STAGE 3 — Assessment
  Department signature instrument (Part VI).
  Format adjustments offered proactively.
  Integrity signals collected but NOT scored.

STAGE 4 — Artefact collection
  Requested where relevant. Confidentiality alternatives offered
  (§6.6) BEFORE recording anything as NOT_PROVIDED.

STAGE 5 — Live verification
  Per §38.4 minimum by seniority. Unprepared probes on the
  candidate's own claims.

STAGE 6 — References
  Structured, identical questions across references.
  Timing per Layer 2 (pre-offer permitted or not).
  Factual/closed questions; scope and decision-rights focus.

STAGE 7 — Credential verification
  With issuing bodies, for every credential carrying material weight.
```

**Evidence-yield checkpoint:** if a candidate reaches Stage 6 with fewer independent groups than §7.4 requires, the coordinator escalates *before* evaluation rather than after — remediation is cheap at this point and impossible later.

## 64. SOP-04 · Evaluation and review

```
1.  Run the pipeline (§56).
2.  Recruiter reviews EVERY candidate in the top 20, not just the top 10.
3.  For each: does the fit rationale hold? Is any evidence misread?
4.  Disposition every flag (GATE G3). Attempt resolution (§13.2)
    before assigning any severity above Minor.
5.  Log any override with all four fields (§42.2).
6.  Check band overlaps — do not present a false ordering.
7.  Confirm the exploration slots are populated and labelled.
8.  Write the fit rationale and the "what to ask in interview" section
    yourself. Do not ship generated text in these two sections without
    reading and editing them — they are what the client actually reads.
9.  GATE G4 dossier completeness check.
10. Deliver.
```

## 65. SOP-05 · Post-delivery

```
D+2    Confirm client received and can navigate the dossiers.
D+7    Calibration review after the first shortlist (mandatory).
       Probe every rejection: "what specifically made that a no?"
       Convert unstated criteria into a v2 configuration.
D+30   Capture interview and offer outcomes.
D+90   Capture join and early-performance signals.
D+180  Capture 6-month performance.
D+365  Capture 12-month performance and retention.
```

## 66. Checklists

### 66.1 Configuration freeze checklist
- [ ] ≤6 competencies, force-ranked, weights derived from ranking
- [ ] Every competency written as observable evidence, not a trait
- [ ] Every competency has a named, feasible assessment method
- [ ] Situation type classified and confirmed
- [ ] Every disqualifier passes §12.3 and is HR Manager approved
- [ ] Proxy audit completed
- [ ] Weight vector within clamps; D4 ≥ 0.12; no dimension < 0.05
- [ ] Jurisdiction resolved; licensure conditionals set correctly
- [ ] Best-current-performer test passed
- [ ] Calibration review booked

### 66.2 Pre-delivery checklist (per candidate)
- [ ] Minimum independent groups met for seniority (§7.4)
- [ ] All High-materiality claims have a status
- [ ] All flags dispositioned by a human
- [ ] All contradictions worked through §13.2, including the candidate's chance to explain
- [ ] Confidence computed and labelled
- [ ] Band overlap checked against adjacent candidates
- [ ] Risk register populated (an empty one is a defect)
- [ ] Every dossier statement carries an evidence citation
- [ ] Fit rationale and interview questions written and edited by the recruiter
- [ ] No unfalsifiable praise anywhere in the document

### 66.3 Weekly pod review
- [ ] Override rate by role; any uncategorised overrides reviewed
- [ ] Flag precision on flags closed this week
- [ ] Evidence-yield and completion rates
- [ ] Any configuration blocked at a gate, and why
- [ ] Any candidate delivered at Low confidence, and whether that was avoidable
- [ ] Any refused client request, escalated appropriately

---

# PART XIV — SELF-CRITIQUE AND KNOWN LIMITATIONS

*A runbook that presents itself as complete is a runbook that will not be improved. This part exists to record, in advance, where this document is most likely to be wrong — so that the calibration loop knows where to look first.*

## 67. Where this runbook is weakest

### 67.1 The weight baselines are professional judgement, not measurement
The department × seniority matrices in §11.1 are the most consequential numbers in the document and the least evidenced. They are informed reasoning about what predicts performance in each function; they are not derived from outcome data, because we do not yet have any. **They must be treated as version-1 hypotheses and revised aggressively once calibration data exists.** Anyone using these numbers as though they were validated is misusing the document.

### 67.2 The evidence-tier strengths are approximations
The values in §6.1 (E0 = 0.10 through E5 = 0.95) are ordinally correct — the ordering is defensible — but the specific magnitudes and the gaps between them are estimates. The aggregation formula in §6.5 is sensitive to these values, and a different but equally reasonable set would produce different rankings at the margins. This is a real limitation. Mitigation: the uncertainty band (§10.6) and the refusal to order overlapping candidates limit the damage.

### 67.3 The system is expensive to run properly
Three to five hours of intake per role, live verification per candidate, structured references, credential verification, and human review of every top-20 candidate is a substantially more expensive process than similarity ranking. This is the correct trade-off for the positioning — but it constrains the volume the business can serve and it will be under commercial pressure to be shortcut. **The shortcuts that will be attempted first are: compressing intake, skipping live verification for mid-level roles, and reducing reference collection.** These are precisely the components that produce the differentiation. If they are cut, the product becomes an ordinary ranking tool with an expensive document attached.

### 67.4 The calibration loop takes a year to close
Performance data at twelve months is the true target, but a young company cannot wait a year for its first learning signal. The interim reliance on advancement and selection data risks training the system on client taste, including client bias (§45.2, §49). We have named the control but not solved the problem, and it will bite before it is fixed.

### 67.5 Reference data is weaker than we would like
References are candidate-selected and frequently coached. We have downgraded them to corroboration rather than proof, restricted questions to factual and closed forms, and required multiple references at senior levels. None of this fully solves it. Reference response rate is an availability signal, not a quality signal, and must not be treated as one.

### 67.6 Anti-gaming is a moving target
Everything in Part VII will require revision as candidate practice evolves. In particular, the specificity gradient (§38.3) depends on generative models producing generic Level 4–5 content — an assumption that is already weakening and may not hold in two years. The structural defences (shift the weight to expensive-to-fake evidence; triangulate; verify live) are durable. The specific probes are not, and the probe banks require continuous refresh.

### 67.7 Fairness controls address the mechanisms we can see
Section 51 lists seven bias entry points. There are certainly others. The adverse-impact monitoring in §52.5 depends on demographic data that is often unavailable in our primary market, and the proxy monitoring that replaces it is weaker. The override-direction monitoring in §42.3 is our best control and it depends on volume we do not yet have.

### 67.8 Department coverage is uneven
The IT, data, mechanical, civil, finance, leadership and trades models are relatively developed. Electrical, R&D, architecture and non-technical are thinner. Legal, healthcare clinical, education, hospitality, media, agriculture and public-sector roles are absent entirely and must be built through §36 before being served.

### 67.9 The six-competency ceiling may be too tight for some roles
Genuinely complex hybrid roles — a plant head, a founding engineer, a general manager — may legitimately require more than six discriminating competencies. The ceiling is defended on mathematical grounds (§20.2) and the current guidance is to split the role or accept the loss. This may prove wrong for a class of roles and should be watched.

### 67.10 We assume the hiring manager tells the truth in the SWOT
The entire Layer 3 apparatus rests on honest intake. Hiring managers sometimes do not know what they want, sometimes describe an ideal rather than a need, and occasionally have unstated criteria they will not voice. The rejection probe (§18.3) and the calibration review (§48) are our defences. They are partial.

## 68. Deliberate choices that will be questioned

| Choice | The objection | Why we made it anyway |
|---|---|---|
| Never auto-reject on authenticity flags | "Slower; costs money" | Flag precision is imperfect; auto-rejection converts our error into someone's lost job |
| Delivering fewer than ten candidates when warranted | "Client expects ten" | Padding destroys the only promise that differentiates us |
| Refusing to order overlapping candidates | "Clients want a clean ranking" | False precision is a lie told with a number |
| Pedigree capped at 5% | "Some clients want pedigree filtering" | It imports bias and does not improve prediction; available as a *sourcing* preference instead |
| Exploration slots | "Why include lower-ranked candidates?" | Without them we never learn our weights are wrong, and we converge on one profile shape |
| D4 floor of 0.12, non-negotiable | "Client wants speed over verification" | Authenticity is the product; a client who does not want it is not our client |
| Permitting AI use on take-homes | "Isn't that letting them cheat?" | It removes the incentive to hide it and moves the score to the walkthrough, where it belongs |
| Six-competency ceiling | "Too restrictive" | Weighted averages over noisy estimates degrade past six; see §20.2 |

## 69. What would falsify this approach

Stated in advance, per Axiom 13:

- If, after two full calibration cycles, the Ready Pick Score does not materially out-predict a resume-similarity baseline against 6-month performance, the added complexity is not earning its place and the model must be simplified (Axiom 14).
- If authenticity flag precision cannot be sustained above 60%, the flag apparatus is causing more harm than good and must be substantially reduced.
- If override rates remain above 20% in a department after two rounds of weight revision, that department model is wrong at a level that weight adjustment cannot fix and must be rebuilt.
- If confidence labels do not correlate monotonically with outcomes, the confidence formula is decorative and must be rebuilt or removed.
- If the process cannot be delivered within the engagement's commercial constraints without cutting live verification, the business model and the runbook are in conflict and one must change — explicitly, at Board level, not silently in the field.

---

# APPENDICES

## Appendix A — The Company DNA Intake Instrument (field form)

**Client:** ________________  **Respondent:** ________________  **Date:** ______

### A1. Context
1. Headcount, growth rate, ownership/funding stage
2. Industry and regulatory exposure
3. Locations; work model per location
4. Annual attrition, and where it concentrates
5. Actual (not target) time-to-hire, by level
6. Interview capacity per role per week

### A2. Evaluation philosophy (forced scales, 1–5)
7. Proven delivery ←1 … 5→ Potential
8. Specialist depth ←1 … 5→ Generalist range
9. Credentials ←1 … 5→ Demonstrated practice
10. Stability of prior moves ←1 … 5→ Velocity/change
11. Internal training capacity: none ←1 … 5→ substantial
12. Tolerance for non-traditional backgrounds: low ←1 … 5→ high

### A3. What "good" looks like — observable evidence only (5–8 items)
> Format: "Has [done X] and can [describe/demonstrate Y]"
13. ______________________________________________
14. ______________________________________________
15. ______________________________________________
16. ______________________________________________
17. ______________________________________________

### A4. Failure modes
18. Describe two or three hires who looked strong and did not work out. What was the actual failure? *(This produces risk probes — press for specifics.)*

### A5. Non-negotiables
19. Statutory/policy requirements
20. Notice-period tolerance
21. Compensation bands by level, and flexibility
22. Location/relocation/work-model rules
23. Any genuinely binary requirement *(each tested against §12.3)*

### A6. Process
24. Interview stages actually run
25. Final decision-maker
26. Client-run assessments (to avoid duplication)
27. Turnaround commitments

### A7. Diversity commitments (process, not quotas)
28. Slate composition goals at sourcing
29. Adverse-impact reporting wanted?
30. Prohibited-filter list confirmed and signed? ☐

### A8. Data and consent
31. Data the client may receive/retain
32. Required consent language
33. Retention period
34. Cross-border constraints
35. Reference timing: pre-offer permitted? ☐ Yes ☐ No

### A9. Offer reality
36. Band vs. market position by level
37. Known counter-offer patterns

### A10. Sourcing preferences *(sourcing only — never scoring)*
38. Target industries/companies
39. Talent pools to prioritise

### A11. Presentation
40. Dossier depth; anonymised first pass? ☐; language

### A12. Historical calibration data
41. Past hires with outcomes — available? ☐ *(Pursue hard.)*

---

## Appendix B — The Role SWOT Instrument (field form)

**Role:** ________________ **Department:** ________ **Seniority:** ________
**Hiring Manager:** ________________ **Situation type:** ________________

### B1. Context
1. Why this role, why now?
2. What happens if the seat stays empty for six months?
3. Who does this person work with most closely?
4. How much direction will they receive? *(autonomy probe)*

### B2. Strengths — what the team already has
5. If I joined tomorrow, what would I *not* have to do because someone else already does it well?
6. What is this team genuinely good at?
7. Which of those must the new hire at least not undermine? *(→ floors, not differentiators)*

### B3. Weaknesses — the gap this hire must close
8. What goes wrong today because a capability is missing?
9. What does the team keep outsourcing or deferring?
10. What did the last person in this seat struggle with?
11. What must this person deliver in their first 90 days?
   *(→ these become the highest-weighted competencies)*

### B4. Opportunities — the upside
12. If this person is excellent, what do you hand them next?
13. Where does this role go in 18 months?
14. What could this hire unlock that isn't in the JD?

### B5. Threats — how this fails
15. Describe the version of this hire that goes badly.
16. Why did the last two people at this level leave?
17. What will make offers hard to close here? *(honest comp/market reality)*
18. Describe a candidate who would look perfect on paper that you'd still reject. Why?
   *(→ this surfaces unstated criteria before they become invisible filters)*

### B6. Force-ranking
19. Rank the required competencies 1..n (max 6). No ties.
20. If you could only have deep ___ or deep ___, which? *(repeat until ranking is stable)*

### B7. Confirmation
21. Situation type confirmed: Gap-fill / Turnaround / Scale-up / Greenfield / Steady-state / Succession
22. Would these requirements exclude your current best performer? ☐ No ☐ Yes *(if yes, revise)*
23. Disqualifiers declared and justified
24. Calibration review booked for: ______

---

## Appendix C — The Scorecard Template

| # | Competency | Must/Nice | Observable evidence statement | Assessment method | Dimension | Rank | Weight | Threshold |
|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | 1 | | |
| 2 | | | | | | 2 | | |
| 3 | | | | | | 3 | | |
| 4 | | | | | | 4 | | |
| 5 | | | | | | 5 | | |
| 6 | | | | | | 6 | | |

**Disqualifiers** (each must pass §12.3):
| Condition | Rationale | Approved by |
|---|---|---|

**Weight vector:** D1 ___ D2 ___ D3 ___ D4 ___ D5 ___  (Σ = 1.00; D4 ≥ 0.12)
**Derivation:** baseline ____ · L2 modifiers ____ · L3 modifiers ____ · clamps applied ____
**Approved by (HR Manager):** ______________ **Date:** ______ **Version:** ____

---

## Appendix D — Validation probe bank (starter set, all departments)

### D.1 Universal probes (usable in any department)
| Level | Probe |
|---|---|
| 3 | "Why that approach rather than the more obvious one?" |
| 4 | "What went wrong, and what did it cost?" |
| 4 | "Which part of this was someone else's work?" |
| 4 | "What did you get wrong at first?" |
| 5 | "What do you think differently about now, having done it?" |
| 5 | "What would you refuse to do the same way again?" |
| — | "What part of this were you least confident about?" |
| — | "Who could argue you're wrong about that, and what would they say?" |

### D.2 Attribution probes
- "What would have happened if you hadn't been on that project?"
- "Who else could have done your part?"
- "What decisions were yours to make without asking?"
- "What did you need approval for?"

### D.3 Impact probes
- "How was the baseline measured?"
- "What else changed during that period?"
- "Did it hold after you left?"
- "Who acted on it, and what did they do differently?"

### D.4 Honesty-rewarding probes *(scored positively for candour)*
- "What part of this job would you need the most help with?"
- "What's something on your resume you'd describe more modestly in person?"
- "What did you have to learn on the job that you'd claimed to know?"

---

## Appendix E — End-to-end worked example

**Client:** mid-size product company, 600 people, regulated fintech.
**Role:** Engineering Manager, Payments Platform.
**Candidates:** 340 applicants.

### E.1 Layer 2 (extract)
Evaluation philosophy: proven over potential (2/5) → D2 +0.04, D5 −0.04. Regulated → D4 +0.03. Prior failures: "two strong ICs promoted into EM roles who could not handle stakeholder conflict" → risk probe on conflict handling. Anonymised first pass: yes.

### E.2 Layer 3 SWOT (extract)
- **Strength:** "Payments domain knowledge in the team is deep." → domain competency deprioritised to rank 5.
- **Weakness:** "Nobody manages the relationship with compliance and the team keeps getting blocked late." → **CMP-CROSS-FUNCTIONAL-INFLUENCE, rank 1.**
- **Weakness:** "Delivery predictability is poor." → CMP-DELIVERY-OWNERSHIP, rank 2.
- **Opportunity:** "This could become a two-team org next year." → D5 +0.05.
- **Threat:** "Last EM left because they were expected to code and manage." → transparency obligation: tell candidates the real split; probe their expectation.
- **Situation type:** Turnaround (delivery predictability broken) → D2 +0.04, D3 +0.04.

### E.3 Scorecard (approved)

| # | Competency | M/N | Observable evidence | Assessment | Rank | Weight | Threshold |
|---|---|---|---|---|---|---|---|
| 1 | Cross-functional influence under constraint | Must | Has moved a blocking function (compliance/legal/risk) to a decision, without authority | Decision-archaeology probe + reference | 1 | 0.30 | 60 |
| 2 | Delivery ownership & predictability | Must | Has taken an unpredictable team to a predictable cadence, with a mechanism they can describe | Behavioural probe + reference | 2 | 0.23 | 55 |
| 3 | Engineer development | Must | Can name people they grew and what happened to them | Behavioural probe + downward reference | 3 | 0.18 | 50 |
| 4 | Technical judgement (non-coding) | Must | Can evaluate an architecture decision and state the trade-offs | Live architecture discussion | 4 | 0.13 | 50 |
| 5 | Payments domain | Nice | Familiar with payments constraints | Discussion | 5 | 0.10 | — |
| 6 | Regulated-environment operation | Nice | Has delivered under audit/compliance constraints | Discussion + reference | 6 | 0.06 | — |

**Weight vector:** baseline EM (0.18/0.30/0.26/0.16/0.10) + L2 (D2 +0.04, D5 −0.04, D4 +0.03) + L3 (D2 +0.04, D3 +0.04, D5 +0.05) → clamp and normalise → **D1 0.15 · D2 0.32 · D3 0.28 · D4 0.15 · D5 0.10**

### E.4 Funnel

| Stage | Count | Note |
|---|---|---|
| Applied | 340 | |
| Claim extraction + hard disqualifier check | 312 | 28 excluded on work authorisation (declared, logged) |
| Validation questionnaire sent | 312 | 24-minute instrument, 11 items, 5 at Level 4–5 |
| Completed | 214 | 69% completion |
| Assessment stage | 74 | Screened on validation specificity, not resume score |
| Live probe | 31 | |
| References + verification | 18 | |
| Evaluated and gated | 18 | 2 HOLD (integrity), 3 Insufficient confidence |
| **Delivered** | **9** | Not 10 — see below |

### E.5 Why nine, not ten
The tenth-ranked candidate's band overlapped the eleventh and twelfth, and none had corroboration on CMP-CROSS-FUNCTIONAL-INFLUENCE — the highest-weighted competency. Delivering them would have meant presenting a candidate we could not defend on the thing that mattered most. The dossier pack opens with a note saying exactly this. **The client accepted nine and interviewed six.**

### E.6 The two HOLD candidates
- One: repository presented as five years of work, created eleven weeks before applying (T4, severe). Probed; candidate explained it was a re-upload after leaving a previous employer. Plausible and common. Reference confirmed the underlying work existed. **Flag cleared by HR Manager; candidate delivered at Moderate confidence with the note included.**
- Two: reference contradicted claimed team size (claimed 14 direct reports, reference said 4 direct and 10 dotted-line). Probed; candidate acknowledged the distinction immediately and had not intended to mislead. Severity reduced to Minor; scope corrected in the ledger; **delivered with the corrected scope stated.**

Both cases illustrate the doctrine: neither candidate was rejected by a machine, both got to explain, and both explanations were reasonable. An auto-rejecting system would have removed two viable candidates, one of whom was ultimately hired.

### E.7 Outcome (recorded in the calibration ledger)
Client interviewed six, offered our ranked #2, who joined. At six months the manager's assessment noted specifically that the hire had "unblocked the compliance relationship within the first quarter" — the exact SWOT-derived weakness that drove the rank-1 competency weighting. Recorded as a **positive calibration signal for the SWOT→weight transformation**, with the caveat that a single case is not evidence.

Our ranked #1 declined at offer stage due to a counter-offer — a risk the dossier had flagged in the risk register. **Recorded as a correct risk identification and an incorrect commercial mitigation:** we identified the risk and did not act on it early enough. Process change made: counter-offer risk flagged at *shortlist delivery*, not at offer stage.

---

## Appendix F — Glossary

| Term | Definition |
|---|---|
| **Active weight vector** | The final, normalised D1–D5 weights for a specific role after all layer modifiers |
| **Authenticity multiplier** | The D4-derived factor applied to the composite score (§10.5) |
| **Candidate Evidence Graph (CEG)** | The claim/evidence/verification/gap structure representing a candidate |
| **Claim** | An assertion about a candidate's capability, experience, credential or outcome |
| **Configuration** | The frozen, versioned set of rules governing evaluation for one role |
| **Corroboration** | Support for a claim from independent evidence groups |
| **Decision Contract** | The six commitments Ready Pick Now makes to every client (§2) |
| **Disqualifier** | A binary, objective exclusion condition; HR Manager approval required |
| **Evidence tier (E0–E5)** | The strength classification of an evidence node, set by fabrication cost |
| **Exploration slot** | A deliberately included high-variance candidate, labelled as such |
| **Gate (G1–G4)** | Mandatory checkpoints: configuration, evidence, integrity, delivery |
| **Independence group** | A class of evidence that could not have been produced by one act of candidate preparation |
| **Layer 1 / 2 / 3** | Ready Pick Now Hiring Philosophy / Company DNA / Role SWOT |
| **Materiality** | Whether a claim matters to the decision; determines verification obligation |
| **NOT_PROVIDED** | Evidence was requested and not supplied — distinct from UNKNOWN |
| **Ready Pick Score (RPS)** | The final composite, reported with a band and a confidence label |
| **Situation type** | The hiring problem's shape: gap-fill, turnaround, scale-up, greenfield, steady-state, succession |
| **Specificity gradient** | The five-level probing ladder used to test participatory knowledge (§38.3) |
| **Triangulation** | Testing a claim across independent sources for consistency |
| **UNKNOWN** | No evidence was requested or collected — excluded from scoring, reduces confidence |

### Canonical spellings (added v1.1)

One spelling per concept. Code, tests, documents, UI strings and log messages use these forms and no others.

| Concept | Canonical form | Do not use |
|---|---|---|
| Product name, in prose | Ready Pick Now | Ready Pick, ReadyPick Now, Readypick Now |
| Wordmark and domain-facing brand | ReadyPick (readypick.ai) | Readypick, readypick, Ready-Pick |
| Legal entity | Hanulisa Technologies LLP | Hanulisa Technologies, Hanulisa LLP |
| Layer 1 | Ready Pick Now Hiring Philosophy | Ready Pick Hiring Philosophy, Ready Pick philosophy |
| Layer 2 | Company DNA | company DNA, Company Hiring DNA |
| Layer 3 | Role SWOT | role SWOT, SWOT intelligence |
| Composite score | Ready Pick Score (RPS) | Ready Pick Now Score, RP Score |
| Dimension 1 | D1 — Verified Competence | Competence, D1 Competency |
| Dimension 2 | D2 — Track Record & Impact | Track Record and Impact, Impact |
| Dimension 3 | D3 — Role & Context Fit | Context Fit, Role Fit |
| Dimension 4 | D4 — Authenticity & Consistency | Authenticity, Consistency |
| Dimension 5 | D5 — Trajectory & Potential | Potential, Trajectory |
| Evidence tiers | E0 Asserted; E1 Self-described with specificity; E2 Artefact provided by candidate; E3 Structured response under controlled conditions; E4 Demonstrated under observation; E5 Third-party verified | Tier 0-5, Level 0-5 |
| Gates | G1 Configuration gate; G2 Evidence gate; G3 Integrity gate; G4 Delivery gate | Gate 1-4, Checkpoint 1-4 |
| Situation types | Gap-fill; Turnaround; Scale-up; Greenfield; Steady-state; Succession | Gap fill, Scale up, Steady state, Green-field |
| Layers | Layer 1 / Layer 2 / Layer 3, abbreviated L1 / L2 / L3 | Tier 1, Level 1 |
| Instruments | Company DNA Intake Instrument; Role SWOT Instrument; Scorecard; Validation questionnaire; Recruiter dossier | DNA form, SWOT form, score sheet |
| Independence groups | self_written; self_structured; assessment; artefact; live; third_party | self, docs, interview |
| Claim statuses | Corroborated; Partially corroborated; Uncorroborated; Contradicted | verified, unverified |
| Evidence states | UNKNOWN; NOT_PROVIDED; WEAK; CONTRADICTED | missing, n/a, absent |
| Confidence labels | High; Moderate; Low; Insufficient | Very high, Medium |
| Score bands | Ready to Pick — Strong; Ready to Pick; Consider with reservations; Not recommended for this role; Not recommended; HOLD | Strong, Good, Weak, Reject |

---

## Appendix G — One-page summary (for internal display)

```
                      READY PICK NOW HIRING PHILOSOPHY

  WE DO NOT RANK RESUMES. WE BUILD EVIDENCE FOR DECISIONS.

  THREE LAYERS            FIVE DIMENSIONS           SIX EVIDENCE TIERS
  ─────────────           ───────────────           ──────────────────
  1  RPN philosophy       D1 Verified competence    E5 Third-party verified
  2  Company DNA          D2 Track record & impact  E4 Demonstrated live
  3  Role SWOT            D3 Role & context fit     E3 Structured response
                          D4 Authenticity           E2 Candidate artefact
                          D5 Trajectory             E1 Specific self-report
                                                    E0 Asserted

  THE FOUR RULES THAT MATTER MOST

  1. CRITERIA BEFORE CANDIDATES.   Scorecard frozen before scoring.
  2. WEIGHT WHAT IS EXPENSIVE TO FAKE.  Move the decision to E4/E5.
  3. CONTRADICTION IS INFORMATION.  Never average. Always probe.
  4. NO SCORE WITHOUT ITS EVIDENCE. And no auto-rejection, ever.

  THE PROMISE
  Not "our AI ranked them."
  But "here is what we verified, how we verified it, how confident we
  are, and what we still don't know."

  THE MOAT
  Not the model. Not RAG. Not the agents.
  The hiring intelligence encoded around them.
```

---

## CHANGELOG

Editorial revisions are recorded here and itemised, edit by edit, in `RUNBOOK_EDITS.md` at the repository root. Substantive questions raised against this document and deliberately NOT resolved inside it are recorded in `RUNBOOK_OPEN_QUESTIONS.md`.

| Version | Date | Category | Change | Authority |
|---|---|---|---|---|
| 1.0 | Not recorded | Issue | Initial standard issued. | Hiring Standards Board |
| 1.1 | 2026-08-29 | Editorial | Front matter completed (Date and Owner rows; version raised to 1.1). Table of contents added. §16's twelve subsections numbered §16.1-§16.12, repairing the §16.3 cross-reference in §51. Two cross-references repaired: C5 in §2 cited §12.4, the PROHIBITED disqualifier list, where the sentence means §12.3, the legitimate one; and §6.3 cited §11.4 for adverse-impact monitoring, which is §52.5. Appendix D and Appendix E subsections renumbered D.1-D.4 and E.1-E.7 so they no longer collide with the dimension names D1-D5 and the tier names E0-E5. Product naming normalised to 'Ready Pick Now'. Layer 1's name normalised to one spelling. 'Company DNA' capitalised consistently. Glossary extended with a canonical-spellings table. NO weight, threshold, multiplier, band boundary, tier definition, cap, floor, intake question, probe or accepted/rejected example pair was changed. | Editorial |

**Mechanical content of this document is mirrored in `backend/app/services/hiring/runbook_data/` as YAML, every entry citing `RPN-PHIL-001 §N`.** `backend/tests/test_runbook_parity.py` fails if the two drift in either direction. Change this document and the data files in the same commit.

---

*End of document. RPN-PHIL-001 v1.1.*
*Amendments to Layer 1 require Standards Board approval and calibration evidence.*
