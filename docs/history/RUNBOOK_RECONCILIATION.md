# RUNBOOK RECONCILIATION

**Phase:** spec-doc6 PHASE 0b, §2.3
**Authority:** `Readypick Hiring Philosophy.md` (RPN-PHIL-001 v1.1)
**Re-verified against v1.1** after the section 2.1 editorial pass renumbered
`§16`'s twelve subsections to `§16.1`-`§16.12`, renumbered Appendices D and E,
and normalised product naming. All 33 tests in
`backend/tests/test_runbook_reconciliation.py` pass against v1.1; the parser
matches headings on stable substrings rather than on exact numbering, so the
renumbering did not break it.
**Date:** 2026-08-29
**Scope:** the 9 sites the previous phase marked `ASSUMPTION (RUNBOOK-GAP`

The previous phase built Part A without the Runbook, which was not in the
repository, and marked every place it guessed. The Runbook is now present. Each
of the 9 sites below was read against the section it cited, and reconciled.

**Result: 0 CONFIRMED outright, 8 CORRECTED, 1 CORRECTED-in-part with the
remainder STILL_UNSPECIFIED. Zero `ASSUMPTION (RUNBOOK-GAP` markers remain.**

Every `CORRECTED` row carries a test that reads RPN-PHIL-001 itself and would
have failed before this phase. Those tests live in
`backend/tests/test_runbook_reconciliation.py`, which parses the document at the
cited section rather than comparing the code against a second copy of the same
assumption. That distinction is the whole point: a test written against the
guess would have passed happily throughout.

---

## The table

| Site | File:line | Runbook § | What was assumed | What the Runbook says | Verdict | Change made | Test that now pins it |
|---|---|---|---|---|---|---|---|
| 1 | `backend/app/services/hiring/situations.py:37` | §18.4, corroborated by §11.3 | The six type names and two worked consequences from spec-doc5; the other four types' modifiers and all six magnitudes were "this implementation's judgment". | A six-row table with an explicit weight-consequence column in D1..D5 arrows, plus a fourth "evidence emphasis" column. Gap-fill `D3 ↑↑, D1 ↑`; Turnaround `D2 ↑↑, D3 ↑`; Scale-up `D2 ↑, D5 ↑`; Greenfield `D5 ↑↑, D3 ↑`; Steady-state `D1 ↑↑, D5 ↓`; Succession `D5 ↑↑, D2 ↑`. §11.3 restates four of the six in its own vocabulary and agrees. | **CORRECTED** | `Situation.modifiers` replaced by `Situation.effects`, keyed on §18.4's three arrow levels. All four wrong rows fixed, including two inversions. Every unstated lift and cut removed. §18.4's evidence-emphasis column added as `Situation.evidence_emphasis`. Magnitudes now read from `runbook_data/situation_types.yaml`; nothing is restated in code. | `test_every_situation_matches_section_18_4_exactly`, `test_every_situation_carries_section_18_4_s_evidence_emphasis`, `test_no_multiplier_is_invented_for_an_arrow_the_runbook_left_ordinal`, plus `test_no_situation_lifts_a_dimension_the_runbook_does_not_name` and `test_no_situation_moves_a_dimension_its_runbook_row_does_not_name` |
| 2 | `backend/app/services/hiring/company_dna.py:48` | §16, Appendix A | The twelve section titles and intents were built from spec-doc5's description; the exact wording and example pairs were unavailable. | Twelve named sections with Appendix A's field list. Section 2 is six forced scales printed 1 to 5. Section 3 asks for five to eight items in the form "Has [done X] and can [describe or demonstrate Y]" and prints TWO accepted/rejected pairs. §7.4 sets minimum independent sources by seniority. §15 requires anything outside the six output kinds to be labelled recruiter context. | **CORRECTED** | Five of §16's twelve sections were absent and are added (diversity commitments, data and consent, offer reality, historical calibration, and four of organisational context's six fields). Four invented sections removed. Section 2 rebuilt as §16's six scales on the 1..5 range. Both example pairs carried. §11.2's paired modifiers now move both dimensions. §11.2's regulated-industry row added. Corroboration floor moved from a client answer to §7.4. Risk probes separated from observable signals. `recruiter_context` added per §15. | `test_the_instrument_has_section_16_s_twelve_section_titles`, `test_section_two_carries_section_16_s_six_forced_scales`, `test_the_scale_range_is_the_one_appendix_a2_prints`, `test_both_section_16_example_pairs_are_used_verbatim`, `test_section_three_asks_for_the_number_of_items_the_runbook_asks_for`, `test_the_corroboration_floor_is_section_7_4_s_and_not_the_client_s`, plus 9 in `test_bodha_intake.py` |
| 3 | `backend/app/services/hiring/swot_quality.py:69` | §18.3, §18.5 | Seven probes "written to spec-doc5's stated purpose"; five rejection rules. | §18.3 NAMES all seven probes and gives each one's question and purpose. §18.5 lists SIX triggers, not five. | **CORRECTED** | Five of seven probes replaced with §18.3's own, each carrying the Runbook's name and stated purpose. The three that were absent outright: empty-seat, scale-reality and rejection. The trade-off probe is now parameterised per Appendix B6. §18.5's sixth trigger added: the best-performer test, three-valued so an unasked check is `outstanding` and never a pass. §20.2's six-competency ceiling added. | `test_the_seven_probes_are_section_18_3_s_seven`, `test_every_probe_question_is_the_runbook_s_question`, `test_section_18_5_has_six_triggers_and_all_six_are_implemented`, plus 5 in `test_bodha_intake.py` |
| 4 | `backend/app/services/hiring/layers.py:47` | §3.5 | "The resolution implemented here is built from what spec-doc5 itself states: tuning within declared bounds is permitted, suspension is not." | §3.5 is a seven-row table. Row 1: L3 asks for something L2 prohibits, L2 wins, escalate to HR Manager. Row 3: clamp to bound, **notify hiring manager**, record. Row 6: refused under C5, **offer priority human review instead**. Row 7: removal of the confidence label, refused under C4. | **CORRECTED** | Three of seven rows were implemented. `PRECEDENCE_RULES` is now the table row for row. `resolve` gained `company_prohibits`, the channel row 1 needs and composition cannot express. `Resolution.notifications` added for row 3. `Refusal` carries `rule`, `escalate_to` and `alternative`. Three confidence-label invariants added; without them the request raised an internal error instead of the reasoned refusal §3.5 requires. | `test_the_precedence_table_is_section_3_5_row_for_row`, `test_a_company_prohibition_beats_a_role_request`, `test_a_clamped_role_request_notifies_the_hiring_manager`, `test_the_confidence_label_cannot_be_switched_off`, `test_an_auto_rejection_request_is_refused_with_c5_s_alternative` |
| 5 | `backend/app/services/hiring/layers.py:177` | §3.5 multipliers, resolved against §11.2/§11.3/§11.4 | "The specific multipliers below are this implementation's judgment." | §3.5 gives no multiplier. §11.2/§11.3 bound Layer 2 and Layer 3 modifiers ADDITIVELY on the five dimension weights. §11.4 clamps each weight to `[0.05, 0.40]`, floors D4 at 0.12, and renormalises to 1.0. | **CORRECTED** (the §11.4 half) / **RUNBOOK-AMBIGUITY** (the multiplier half) | §11.4 was absent entirely and is now `clamp_weight_vector`, reading its floors and ceiling from `runbook_data`. The per-competency `BOUNDS` multipliers stay this implementation's, marked `RUNBOOK-AMBIGUITY` and escalated: they bound a different object at a different granularity from §11.2/§11.3, and additive and multiplicative bounds do not convert without fixing a baseline. | `test_the_weight_vector_clamp_is_section_11_4`, `test_no_dimension_is_ever_weighted_to_zero` |
| 6 | `backend/app/services/hiring/department_models.py:41` | Part VI (§21 to §35), §11.1, §9.1-§9.5, §21.11 | Five departments, their competencies, baseline weights and rubric anchors were "this implementation's work, built to the SHAPE spec-doc5 describes". | Part VI carries FIFTEEN department models, each with a competency menu of 8 to 12 entries carrying a stable id, name and observable-evidence sentence. §11.1 carries the Layer 1 baseline weight matrix, ten department families by seniority band, over the five dimensions. Neither supplies a per-competency weight, and §20.3 puts competency importance in the hiring manager's force-ranking. | **CORRECTED** (menus and baselines) / **RUNBOOK-AMBIGUITY** (per-competency weight, competency-to-dimension map) | `runbook_departments()`, `runbook_competency_menu()` and `baseline_dimension_weights()` added, reading all fifteen models and §11.1's matrix from the extracted data. Ten of fifteen departments previously had no model at all and were being named against a generic one. `BaselineCompetency.baseline_weight` and `.primary_dimension` marked `RUNBOOK-AMBIGUITY`: the Runbook supplies neither and changing them moves every weight in the product. **Amended after the extraction agent reported:** `DepartmentModel.anchors` was a third unsourced field and was missed in the first pass. The Runbook does carry rubric anchors, and carries them somewhere else: §9.1-§9.5 hold one six-band 0-100 table per DIMENSION, universal, never restated per department or seniority; only §21.11 carries per-seniority material, for one department of fifteen, and it is an emphasis shift rather than an anchor. `dimension_rubric_anchors()` and `seniority_emphasis()` added for the real thing; `anchors` marked. | `test_part_six_carries_fifteen_departments_and_all_are_reachable`, `test_every_department_menu_is_the_runbook_s_menu`, `test_the_baseline_weight_matrix_is_section_11_1`, `test_a_seniority_band_from_the_wrong_family_is_refused`, `test_an_unknown_department_raises_rather_than_falling_back`, `test_rubric_anchors_are_per_dimension_and_not_per_department`, `test_only_one_department_carries_per_seniority_material` |
| 7 | `backend/app/services/hiring/evidence_graph.py:42` | Part VI | "The graphs below are written to the shape spec-doc5 describes ... the CONTENT is what the real Part VI would replace." | Part VI's observable-evidence column IS "what a good answer would establish". It also supplies per-department evidence tiers, gaming vectors and red flags, and §21.8 states that red flags "route to review, never auto-reject". It does NOT supply edges between competencies. | **CORRECTED** (node content) / **RUNBOOK-AMBIGUITY** (the edges) | `runbook_node_for()` added, building a node whose `establishes` is Part VI's own observable-evidence sentence verbatim and whose `corroborated_by` applies §5.4's originator rule. The `unlocks` edges stay this implementation's and are marked; §67.8 concedes department coverage is uneven, so the absence is not an oversight to read around. | `test_every_department_menu_is_the_runbook_s_menu` (the source the nodes read), `test_part_six_carries_fifteen_departments_and_all_are_reachable` |
| 8 | `backend/app/services/hiring/ontology.py:48` | §58 | "The two examples in the table below are spec-doc5's own. The rest are written to the same test." | §58 states the requirement and the fairness argument, and names THREE pairings: graph database / semantic technologies, GD&T / geometric tolerancing, and **FP&A / business finance**. It does not enumerate a table. | **CORRECTED** (the third example) / **STILL_UNSPECIFIED** (the table) | "business finance" was in no group, so the one equivalence §58 names for finance did not resolve. Added. The requirement, the fairness argument and the additive-not-substitutive rule are all CONFIRMED against §58. The remaining 40 groups are marked `RUNBOOK-AMBIGUITY (§58)` and escalated, because a curated equivalence table is a fairness-relevant artefact. | `test_all_three_of_section_58_s_named_pairings_resolve`, `test_the_ontology_is_additive_and_never_substitutive` |
| 9 | `backend/app/services/miti/triangulation.py:137` | §57.4, resolved against §13.2 | "The Runbook does not supply these. They are written from the ordinary, non-damning ways each axis actually diverges." | §57.4 confirms the two-explanation rule. §13.2 STEP 3 NAMES SEVEN benign explanations: company renamed, team restructured, title differs from function, contract-to-permanent conversion, confidentiality restriction, NDA on the artefact, regional title conventions. STEP 2 additionally requires our own data to be ruled out first: parsing errors, date-format errors, name collisions, translation artefacts. | **CORRECTED** | Not one of §13.2's seven was present. `RUNBOOK_BENIGN_EXPLANATIONS` added and applied to every axis, because all seven are properties of employment records rather than of a source pair. `OUR_OWN_DATA_EXPLANATIONS` added for STEP 2 and ordered first, per the Runbook's own step order. The `_FALLBACK_BENIGN` two-item list is gone; every axis now inherits eleven. | `test_section_13_2_s_seven_benign_explanations_are_all_available`, `test_step_two_checks_our_own_data_before_the_candidate_s`, `test_every_axis_reaches_the_two_explanation_floor` |

---

## What the corrections actually changed about behaviour

Four findings changed how a candidate would be graded or how an intake would be
handled, rather than only how the code reads.

**1. Four of six situation types re-weighted every matrix in the wrong
direction (site 1).** Two were inversions. Gap-fill led on Verified Competence
and said nothing about Role and Context Fit, where §18.4 leads on Role and
Context Fit and its evidence emphasis is "direct prior experience of that exact
problem". Succession left Track Record neutral, where §18.4 lifts it. The
general defect is worth naming because it is the one to look for anywhere else
a table was reconstructed from a summary: **every invented modifier was
plausible.** "A gap-fill needs someone who can do the work now, so weight
demonstrated competence" is a good argument. It is not the Runbook's. And
nothing downstream could have detected it, because a coherently mis-weighted
matrix has nothing inconsistent in it, which is exactly what §18.4 says about
misclassification and is equally true of mis-weighting.

**2. A client could lower a Layer 1 evidence floor through an intake question
(site 2).** The instrument asked "a candidate describes a project convincingly
and nothing else confirms it, is that enough?" and let "Yes" set the
independence requirement to one source. §7.4 sets minimum independent groups by
seniority as a Layer 1 table and C2 states it as a commitment. This was a
layering inversion, not a wrong value: `layers.INVARIANTS` exists to make
exactly this impossible, and this route walked around it. The floor is now
§7.4's and a client may only ask for more.

**3. §18.5's most effective rejection rule was not implemented (site 3).** The
best-performer test is the only §18.5 trigger that catches a requirement set
which is internally coherent and still wrong; the other five catch a malformed
intake. The Runbook singles it out: "a devastating and highly effective test,
run it". It is now asked rather than inferred, and it is three-valued, so an
unasked test is recorded as `outstanding` rather than as a pass. Collapsing
those two would let the rule be satisfied by never running it.

**4. §13.2's benign explanations were entirely absent (site 9).** The
pre-Runbook set reached for imprecision, recall and form wording. The Runbook's
seven are all employment-record explanations, and that is where the expensive
contradictions live. A resume saying "Acme" where a reference says "Acme Systems
India" is a company rename. A benign-explanation list that could not produce
that reading would work the contradiction through the whole six-step protocol
and reach the wrong disposition with the protocol formally satisfied.

## Two defects found and fixed during the reconciliation itself

**A negation false positive in the regulated-industry detector.** §11.2's
"regulated and audit-sensitive" row is matched from Section 1's free text. The
first implementation matched substrings, so "no regulatory exposure" contains
"regulatory" and read as regulated. This is the same class as the documented
"must hold a valid CA licence" / "hold contains old" defect. The cost here runs
the other way and is easier to miss: a false positive raises the authenticity
floor for a client who never asked, which asks candidates for corroboration the
role does not need, and nobody complains on behalf of the candidate who quietly
graded lower. Negated spans are now stripped before matching. Pinned by
`test_a_regulated_industry_raises_authenticity_without_being_asked`.

**§11.4's own step order breaches its own clamp.** Read as a strict sequence,
step 5 (renormalise to 1.0) undoes step 3 (clamp to `[0.05, 0.40]`): scaling a
clamped vector to sum to 1.0 lifts every weight, and one sitting on the ceiling
ends up above it. Measured on a real vector: D1 clamped to 0.40 came back out at
0.4598 with the sum correct and the ceiling breached. `clamp_weight_vector`
therefore iterates clamp and renormalisation to a fixed point, which is the only
reading under which all six of §11.4's steps are true of the vector that leaves
the function. Same argument `resolve` already makes about the composed product:
a bound that holds at each step and not on the result is not a bound. Recorded
as an open question, since the reading is a correction to a literal step order.

---

## Verification

```
cd backend

# Zero markers remain.
grep -rn "ASSUMPTION (RUNBOOK-GAP" .            # returns nothing, exit 1

# The reconciliation tests, which read RPN-PHIL-001 itself.
python -m pytest tests/test_runbook_reconciliation.py -q     # 30 passed

# The named suites.
python -m pytest tests/test_hiring_layers.py tests/test_bodha_intake.py \
  tests/test_miti_pipeline.py tests/test_hiring_retrieval.py \
  tests/test_scoring.py tests/test_runbook_reconciliation.py -q   # 260 passed

# Regression across the area.
python -m pytest tests/ -q -k "hiring or miti or situation or swot or bodha or evidence or triangul or runbook"
                                                              # 558 passed, 4 skipped
```

`tests/test_import_graph.py::test_no_cycle_prone_module_reads_another_service_at_import_time`
fails on `app/services/agents/identity.py:92` reading `permissions.AGENT_*` at
import time. That is a concurrent agent's in-progress RBAC §34 addition to
`app/services/tools/permissions.py` and is outside this phase's file ownership;
none of the nine reconciled modules is involved.

---

## Addendum: findings from the Runbook extraction agent, folded in

Three items reported after the reconciliation table above was written. Two
changed code; one confirmed a finding and one corrected a miss of mine.

### The C5 citation defect never reached this code, and now cannot

The v1.1 editorial pass repaired Decision Contract C5, which cited **§12.4, the
PROHIBITED disqualifier list, where it means §12.3, the legitimate one**. Read
literally, C5 authorised automatic filtering on exactly the attributes §12.4
forbids: age, caste, gender, employment gaps.

Checked against every §12.x citation in the nine reconciled modules. **All were
already correct**, and `company_dna.compile_artifact` was already built on the
repaired reading: an entry that survives `prohibited_in` is admitted as a §12.3
disqualifier, and one that does not is refused and escalated under §12.4. The
defect was in the document and did not propagate.

That is luck rather than diligence, so it is now pinned:
`test_contract_c5_points_at_the_legitimate_disqualifier_list` asserts that C5
cites §12.3 and does not cite §12.4, and separately that the compiler admits the
CA-licence example and refuses the age bar. A future edit cannot reintroduce it
silently.

### Site 1's magnitude gap is confirmed as a Runbook gap, not an extraction miss

The extraction agent reports that §18.4 states its weight consequences as arrows
with no magnitude, that numbers exist only in §11.3, and that **Scale-up and
Succession have no numeric weight consequence anywhere in the document**. That
is the same conclusion this reconciliation reached from the code side, arrived
at independently.

spec-doc6 §2.3 named the six situation types as one of the four highest-risk
guesses. **The answer is that the Runbook does not specify it either.** The
verdict on the arrow directions stays `CORRECTED` and is now doubly sourced; the
verdict on the magnitudes is `STILL_UNSPECIFIED`, and
`situations._arrow_magnitudes` raises rather than inventing one. This is the one
blocking join in the phase and it needs an owner decision, not more searching.

### A miss of mine, corrected: rubric anchors are per dimension

The extraction agent reports that spec-doc6 §2.2's "per-seniority rubric
anchors" exist for **one department out of fifteen** (§21.11), and that rubric
anchors are universal, stated once per dimension in §9.1 to §9.5.

The first pass of site 6 marked `baseline_weight` and `primary_dimension` as
unsourced and **did not mark `DepartmentModel.anchors`**, which is unsourced in
the same way and for a sharper reason: the Runbook does carry rubric anchors and
carries them somewhere else entirely. §57.3's phrase "retrieved rubric anchors
from the department model" is what sent the pre-Runbook implementation looking
per department; the anchors that exist are the dimension ones, and the
department model supplies the competency set they are applied to.

Fixed: `dimension_rubric_anchors()` reads §9.x's six-band tables from the data
package, `seniority_emphasis()` returns §21.11's notes and an empty mapping for
the fourteen departments that have none, and `DepartmentModel.anchors` is marked
`RUNBOOK-AMBIGUITY`. Pinned by
`test_rubric_anchors_are_per_dimension_and_not_per_department`, which checks the
six bands tile 0 to 100 with no gap or overlap and that every band's wording is
in the document, and by
`test_only_one_department_carries_per_seniority_material`.

### Items noted and deliberately not acted on

Four items in that report belong to modules outside this phase's ownership and
to `CONTRADICTIONS.md`, which is another agent's file:

- **The Must-have hard cap has no Runbook source.** Three separate band-capping
  mechanisms exist (§12.1, §12.2, §14.1) and the phrase does not. This is a
  contradiction between spec-doc6 §2.2, CLAUDE.md and the Runbook, and it lands
  in `miti/aggregation.py`, which is not a reconciled site. Not touched.
- **"Weakly independent" is a third value inside an integer count** (§5.4).
  `triangulation.count_independence` is in a reconciled file but the counting
  logic was not a marked site and the fix requires a value decision. Recorded as
  the extraction agent's Q14. Not touched.
- **Three §11.1 baselines breach §11.4's clamp** (Mechanical Fresher D1 0.42,
  Trades Entry D1 0.44, Data Fresher D2 0.04). `clamp_weight_vector` handles
  them correctly by construction, since it clamps whatever it is given, and it
  records the clamp in its notes. The underlying contradiction is the extraction
  agent's Q2.
- **"Insufficient evidence" has two definitions** (§6.7 and §10.7). Lands in
  Miti's aggregation and confidence path, not a reconciled site. Not touched.

## Open questions raised

Recorded in `RUNBOOK_OPEN_QUESTIONS_PHASE0B.md`, because
`RUNBOOK_OPEN_QUESTIONS.md` is owned by another agent and did not exist when
this phase ran. Six entries, each with the quote, the ambiguity, the options,
the consequence of each and the option implemented.
