# RUNBOOK_OPEN_QUESTIONS.md

Substantive questions raised against `Readypick Hiring Philosophy.md`
(RPN-PHIL-001 v1.1) during the spec-doc6 section 2.1 editorial pass, and
deliberately **not resolved inside the document**.

Every item here falls inside spec-doc6 section 2.1's not-permitted list:
changing a weight, threshold, multiplier, band boundary, tier definition, cap or
floor; changing intake question wording or an accepted/rejected example pair;
adding a rule, gate, disqualifier or precedence clause; or resolving a
substantive internal contradiction by picking a side. **The document is
unchanged at every site below.** The corresponding data entries in
`backend/app/services/hiring/runbook_data/` carry a `runbook_ambiguity` field
naming the section and pointing here, so an implementer meets the question
rather than a number somebody guessed.

Each entry gives the exact quote, the ambiguity, the options with their
consequences, and a recommendation. The recommendation is a recommendation. It
is for the Hiring Standards Board (section 4.6) to settle, not for an
implementer to settle by writing code.

Ordered by consequence, worst first.

---

## Q1. Two of the six situation types have no numeric weight consequence

**Sections:** 18.4, 11.3

**Quote, section 18.4:**

> | **Scale-up** | Working, must grow | D2 up, D5 up | Evidence of operating at
> the *next* scale |
> | **Succession** | Prepare to take over a larger role | D5 up up, D2 up |
> Trajectory, readiness indicators |

(The arrows above are rendered in the document as up-arrow glyphs.)

**Quote, section 11.3**, which is the only place a SWOT-derived weight change is
given a magnitude:

> | Hire must close a specific named capability gap | D3 up, D1 up | +0.08
> combined |
> | Hire is a turnaround / crisis mandate | D2 up, D3 up | +0.08 combined |
> | Hire is greenfield / zero-to-one | D5 up, D3 up | +0.07 combined |
> | Role will change substantially within a year | D5 up | +0.06 |
> | Role is a defined, stable execution seat | D1 up, D5 down | +/-0.06 |
> | High-trust / high-blast-radius role | D4 up | +0.06 |

**The ambiguity.** Section 18.4 says the situation type "materially changes the
weight vector" and expresses the change as arrows. Section 11.3 attaches
numbers, but its rows are keyed on SWOT-derived *conditions*, not on situation
types, and only four of them map cleanly onto a type: Gap-fill, Turnaround,
Greenfield and Steady-state. **Scale-up and Succession have no numeric bound
anywhere in the document.** Section 18.4 also disagrees with section 11.3 on
arrow strength for the four that do map: 18.4 gives Gap-fill "D3 up up" while
11.3 gives the corresponding condition "D3 up".

**Why it matters most.** Section 18.4 itself says misclassifying the situation
is "the most expensive error available at intake, because it corrupts the entire
weight vector." A type whose consequence is unspecified is worse than a
misclassification: it corrupts the vector by *omission*, silently, for every
scale-up and every succession hire, and nothing downstream can detect it.

**Options.**

| Option | Consequence |
|---|---|
| A. Apply no numeric modifier for Scale-up and Succession | Two of six situation types become decorative. A hiring manager who correctly classifies a scale-up gets the department baseline, identical to a manager who classified nothing. The classification confirmation ritual in 18.4 then confirms something with no effect. |
| B. Infer bounds by analogy from the 11.3 rows | Invents a number. Section 11.1's own note says the baselines are "calibration hypotheses"; an inferred bound would be a hypothesis nobody wrote down, and it would look identical in the audit trail to one the Standards Board approved. |
| C. Refuse to freeze a configuration whose situation type is Scale-up or Succession until the Board supplies a bound | Blocks real work today on two common situation types, but blocks it loudly. |
| D. Treat the arrows as ordinal and apply the smallest bound in the 11.3 table (+/-0.06) to each named dimension, recorded as provisional | Produces a movable weight, keeps the magnitude at the conservative end of the range the document already sanctions, and marks itself as unvalidated. Still invents a number. |

**Recommendation: A now, C at the configuration gate, and escalate for a real
bound.** Apply no modifier, record `layer_3_modifier: null` with the ambiguity
attached (this is what `situation_types.yaml` does today), and surface the
missing bound on the configuration screen so a human sees that their
classification changed nothing. Do not infer. This is a Layer 1 weight
question, and section 4.6 reserves Layer 1 amendments to the Standards Board
with calibration evidence.

---

## Q2. Three Layer 1 baselines sit outside the clamp section 11.4 declares absolute

**Sections:** 11.1, 11.4

**Quote, section 11.4:**

> 3. Clamp each `W_i` to its floor and ceiling: **no dimension may fall below
>    0.05 or rise above 0.40.**

**Quote, section 11.1**, three rows out of thirty-nine:

> Mechanical / Electrical / Civil / Manufacturing, Fresher: D1 **0.42**
> Skilled trades / Blue-collar / Non-technical support, Entry: D1 **0.44**
> Data & Analytics / R&D, Fresher: D2 **0.04**

**The ambiguity.** Section 11.4 states the clamp as step 3 of a five-step
sequence that begins with the Layer 1 baseline and applies Layer 2 and Layer 3
modifiers first. Read as an ordered procedure, the clamp is applied to the
*modified* vector, and it will silently rewrite these three Layer 1 baselines
even when no client and no hiring manager asked for anything. Read as an
invariant on the delivered vector, the same thing happens. Read as a bound on
*modifiers only*, the baselines survive, but then the sentence "no dimension may
rise above 0.40" is false of the document's own table.

All thirty-nine rows sum to exactly 1.00, so this is not an arithmetic slip in
the rows. Section 11.1's note on trades explains deliberately why D1 and D4 run
high there, which reads as intent rather than error.

**Options.**

| Option | Consequence |
|---|---|
| A. Clamp the composed vector, as written | Two of the fifteen department models can never deliver the D1 emphasis their own text argues for. A trades entry hire is evaluated at D1 0.40 no matter what section 11.1 says. The behaviour is at least uniform and matches the literal rule. |
| B. Exempt the Layer 1 baseline from the ceiling, clamp only the modifier-induced movement | Preserves the three baselines. Requires a rule the document does not contain, which is exactly what section 2.1 forbids adding. |
| C. Treat the three rows as typographical and read 0.40 / 0.40 / 0.05 | Changes three weights. Also breaks the row sums, which currently come to exactly 1.00, so it would require changing a fourth number in each row. |

**Recommendation: A, and flag it.** Apply the clamp exactly as section 11.4
states, renormalise, and record the clamp in the configuration's derivation so
the trace shows the Layer 1 value that was clamped and by how much. Section
11.4 step 6 already requires that record. This makes the collision visible on
every affected job rather than settling it silently, and it never lets a
delivered weight sit outside a bound the document calls absolute. Escalate the
three rows to the Standards Board.

---

## Q3. The authenticity multiplier can exceed the cap the same section states

**Section:** 10.5

**Quote:**

> ```
> authenticity_multiplier =
>      1.00                    if D4 >= 75
>      0.90 + (D4-60)x0.0067   if 60 <= D4 < 75      (0.90 -> 1.00)
> ```
> **Note the asymmetry, and it is deliberate:** high authenticity does not
> inflate a score above what the evidence supports (multiplier caps at 1.00).

**The ambiguity.** The exact slope for a 0.90-to-1.00 rise across fifteen points
is 1/150 = 0.006666..., and the document rounds it to 0.0067. At D4 = 74.9 the
branch returns 0.99993; at the branch's stated top of D4 = 75 it would return
1.0005. The multiplier therefore exceeds 1.00 inside a section that states it
caps at 1.00. The 45-to-60 branch has the mirror-image rounding error in the
safe direction (0.8995 at D4 = 60 rather than 0.90).

In practice the D4 >= 75 branch catches exactly 75, so the overshoot is
unreachable at integer scores. It is reachable at any non-integer D4 in
[74.93, 75), and dimension scores are computed as weighted means, so
non-integers are the normal case, not the edge case.

**Options.**

| Option | Consequence |
|---|---|
| A. Implement the literal 0.0067 and let it exceed 1.00 | A candidate at D4 = 74.95 gets a small upward multiplier, contradicting the stated asymmetry. The effect is under 0.05% of the score, and it is a bug that will be re-found by every future reader. |
| B. Implement `min(1.00, ...)` on every branch | Honours the stated cap. Adds a clause the document does not contain. |
| C. Implement the exact slope 1/150 | Honours both the cap and the stated endpoints, and reads as arithmetic rather than as a new rule. Changes a printed number. |

**Recommendation: B.** Apply `min(1.00, ...)` as an outer guard rather than
editing the slope, because the cap is stated in prose in the same section and a
guard implements a sentence the document already contains, where changing 0.0067
to 1/150 changes a number the document prints. Mark the site
`RUNBOOK-AMBIGUITY (section 10.5)`.

---

## Q4. Part VI claims a structure it does not deliver, and section 67.8 says so

**Sections:** Part VI preamble, 67.8

**Quote, Part VI preamble:**

> **How to read a department model.** Every model follows the same eleven-section
> structure so that they are comparable and so that adding a new department is a
> filling-in exercise rather than an invention.

**Quote, section 67.8:**

> ### 67.8 Department coverage is uneven

**The ambiguity.** The two statements contradict each other, and the tables
settle which is true. Subsection counts, measured: section 21 has twelve;
sections 22 and 23 have ten; section 34 has ten; section 27 has nine; section 29
and section 30 have eight; sections 24, 25 and 26 have seven; sections 28, 31
and 32 have six; **sections 33 and 35 have four.** Only section 21 carries
seniority notes at all. Nine of the fifteen have no fresher variant, no
credential logic, or neither.

**Why it matters to this phase.** spec-doc6 section 2.2 asks
`department_models.yaml` to carry "department competency models, per-seniority
rubric anchors, baseline weights". Two of those three exist for every
department. **Per-seniority rubric anchors exist for one department out of
fifteen.** Rubric anchors in this document are universal, stated once per
dimension in sections 9.1 to 9.5, and are not restated per department or per
seniority anywhere. A reader of the data file needs to know that the gap is in
the source, not in the extraction.

**Options.**

| Option | Consequence |
|---|---|
| A. Soften the Part VI sentence to describe a target structure | One-sentence edit. Removes a false claim. But it is a change to a normative statement about how models are built, which is a rule, not a typo. |
| B. Complete the eleven sections for all fifteen departments | Correct, and it is Standards Board work with domain input, not an editorial pass. |
| C. Leave both statements and record the measured counts | Costs nothing and hides nothing. |

**Recommendation: C now, B eventually.** `department_models.yaml` records
`subsections_present` per department and a `coverage_notes` block citing section
67.8, so the unevenness is data rather than a surprise. Do not soften the Part
VI sentence: it states the intended standard, and section 67.8 already states
the reality.

---

## Q5. `band_width` is named as a width and behaves as a half-width

**Section:** 10.6

**Quote:**

> ```
> band_width = 20 x (1 - confidence_score)      (confidence_score in [0,1])
> Reported as:  RPS 78  [range 71-85, Moderate confidence]
> ```

**The ambiguity.** A quantity called a width is normally the full span. Under
that reading the example's span of 14 requires `band_width = 14`, hence
confidence 0.30, which section 10.7 labels **Insufficient** and section 10.7
says is not delivered. The example labels it Moderate. Under the half-width
reading, `band_width = 7`, confidence 0.65, which is Moderate. The half-width
reading is the only one that makes the example internally consistent, and the
two worked examples in section 10.11 agree with it: confidence 0.66 gives
"+/- 7" and confidence 0.86 gives "+/- 3", both of which are `20 x (1 - c)`
used as a half-width.

**Why it matters.** Section 10.9 step 4 and section 14.1 both refuse to order
candidates whose bands overlap. Reading the width as a full span halves every
band and roughly halves the size of every tied cluster, which changes which
candidates the system claims to have separated. That is a ranking behaviour, not
a display detail.

**Options.**

| Option | Consequence |
|---|---|
| A. Half-width: report `RPS +/- band_width` | Matches all three worked examples. Contradicts the variable's name. |
| B. Full width: report `RPS +/- band_width/2` | Matches the name. Contradicts every worked example in the document, including the one in the same section. |

**Recommendation: A.** Three worked examples outweigh one variable name, and the
conservative direction here is the wider band, which refuses more orderings.
Mark the site `RUNBOOK-AMBIGUITY (section 10.6)` and propose renaming the
variable to `band_half_width` in a future version.

---

## Q6. An unlisted evidence source has no independence classification

**Sections:** 5.4, 38.1

**Quote, section 5.4:**

> **Independence groups** are declared in the schema. The independence count for
> a claim is the number of *distinct groups* supporting it, not the number of
> evidence items.

Section 5.4 then names nine specific pairs, and section 38.1 names six groups:
`self_written`, `self_structured`, `assessment`, `artefact`, `live`,
`third_party`.

**The ambiguity.** The document never says what to do with an evidence source
that maps to none of the six. Real examples that arise immediately: a platform's
own memory of a previous assessment of the same candidate; a recruiter's notes
from a screening call; a background-verification vendor's report; a
psychometric instrument the client ran themselves (section 16.6 explicitly asks
whether the client runs its own assessments).

The consequence is asymmetric and severe. Classifying an unknown source into a
group nobody has used yet *manufactures corroboration*: it raises the
independence count, which raises the corroboration multiplier (section 6.5), the
confidence score (section 10.7 weights independence at 0.20) and the sufficiency
level (section 6.7). A claim can be promoted from Uncorroborated to Corroborated
by an evidence node that adds nothing new.

**Options.**

| Option | Consequence |
|---|---|
| A. Default an unknown source type to DEPENDENT on the nearest existing group | Never manufactures corroboration. Occasionally under-counts a genuinely independent source, which costs confidence rather than fabricating it. |
| B. Default to a new independent group | Inflates independence counts by construction. |
| C. Refuse to admit an evidence node whose source type is not one of the six | Loud, and blocks legitimate evidence collection. |

**Recommendation: A.** It is the direction in which an error costs confidence
rather than manufacturing it, and it matches the existing platform rule
(CLAUDE.md, the ten-system agent framework: "An unknown source type is assumed
DEPENDENT, because assuming independence manufactures corroboration"). Ask the
Board to extend the section 38.1 list rather than to change the default.

---

## Q7. The Runbook never states a "Must-have hard cap"

**Sections:** 12.1, 12.2, 14.1

spec-doc6 section 2.2 asks `bands.yaml` to carry "grade band boundaries, the
authenticity multiplier, confidence thresholds, and **the Must-have hard-cap
rule**". The phrase does not occur in the Runbook, and no section states a
numeric cap on the score for a failed must-have. What the document has instead
is three separate band-capping mechanisms.

**Quote, section 12.1:**

> | **Competency threshold** | Minimum score on a named competency; failure caps
> the band | Hiring Manager proposes, HR Manager approves | Yes |

**Quote, section 12.2:**

> | D1 | 45 | Cannot exceed "Consider with reservations" |
> | D4 | 45 | Cannot exceed "Consider with reservations" |
> | D3 | 40 | Cannot be delivered as Ready to Pick for this role |

**Quote, section 14.1:**

> | A must-have competency has no evidence above E1 | Competency reported as
> Unassessed; candidate cannot be Ready to Pick |

**The ambiguity.** Three different triggers, three different ceilings, and no
statement of how they compose or which one spec-doc6 means. Note also that only
the section 14.1 row is about *must-haves specifically*, and its trigger is an
evidence-tier condition, not a score condition. Section 12.1's competency
threshold is the closest thing to a score-based must-have cap, and its ceiling
is not stated at all: "caps the band" says nothing about which band.

The existing product carries a fourth, related rule that is not in the Runbook:
"Any Not Matching Must-have caps Overall at Moderately Matching" (CLAUDE.md),
implemented as a `min` on the score after the authenticity multiplier.
"Moderately Matching" corresponds to "Consider with reservations", the 60 to 71
band in section 10.8.

**Options.**

| Option | Consequence |
|---|---|
| A. Read section 12.1's "caps the band" as the section 12.2 ceiling, "Consider with reservations", i.e. `score = min(score, 71)` | Consistent with sections 12.2 and 10.8, and with the product's existing behaviour, so no report regrades. Reads one section's ceiling into another section's rule. |
| B. Read section 14.1 as the must-have cap, ceiling "cannot be Ready to Pick", i.e. `min(score, 71)` again since Ready to Pick starts at 72 | Same numeric answer by a different route, but the trigger is evidence tier rather than score, so it fires on a different set of candidates. |
| C. Apply all three independently and take the lowest resulting ceiling | Never returns a band higher than any single rule permits. Most conservative. Requires stating a composition rule the document does not contain. |

**Recommendation: C, implemented as the minimum of whichever ceilings fire.**
Each of the three rules is separately stated as binding, and taking the minimum
is the only reading under which no rule is quietly ignored. Every ceiling is
applied as a `min` on the score, after the authenticity multiplier, so that a
candidate already below the ceiling is not promoted to it. Mark the site
`RUNBOOK-AMBIGUITY (section 12.1)` and ask the Board for the missing ceiling in
12.1 and the missing composition rule.

### Q7 addendum, 2026-08-29: does CLAUDE.md's Must-have hard cap follow from the Runbook?

**The question put to this pass.** CLAUDE.md carries a standing rule with a
stated rationale and tests behind it:

> **The Must-have hard cap is applied LAST, on the SCORE, and it is a `min`.**
> After the authenticity multiplier, because a cap a later multiplication can
> undo is not a cap. A `min` rather than an assignment, because a candidate who
> already grades Not Matching must stay there [...]

and, from the 2026-07-30 section: "Any Not Matching Must-have caps Overall at
Moderately Matching." The implementation is `rating.cap_to_moderately`, whose
docstring cites "spec section 5.5", and `miti/aggregation.py` lines 412 to 433.
The named source of the rule, RPN-PHIL-001, does not contain the phrase. Is the
rule a correct synthesis of the three mechanisms the Runbook does state, or a
genuine addition that entered from spec-doc4/5?

**Answer: correct on behaviour, incomplete on coverage.** The rule as written is
a faithful synthesis of section 12.1 (trigger and `min`), section 12.2 (the
ceiling) and sections 10.1, 10.5 and 10.8 (the position). **Only the name and
the citation are invented.** But the Runbook states three caps and the product
implements one, so the rule is a correct *subset* of what the Runbook requires,
not the whole of it. The gap is in coverage, not in the rule.

#### The three mechanisms, quoted

**Section 12.1**, the four control types:

> | **Competency threshold** | Minimum score on a named competency; failure
> caps the band | Hiring Manager proposes, HR Manager approves | Yes |

**Section 12.2**, Layer 1 default dimension floors:

> | D1 | 45 | Cannot exceed "Consider with reservations" |
> | D4 | 45 | Cannot exceed "Consider with reservations" |
> | D4 | 25 | HOLD - mandatory human review before any delivery |
> | D3 | 40 | Cannot be delivered as Ready to Pick for this role; may be flagged
> for a different role |

**Section 14.1**, when the system must abstain:

> | A must-have competency has no evidence above E1 | Competency reported as
> Unassessed; candidate cannot be Ready to Pick |

#### Comparison, axis by axis

| Axis | CLAUDE.md | Runbook | Same? |
|---|---|---|---|
| **Trigger** | A Must-have **item** grades Not Matching. A score condition on a named criterion. | Section 12.1: "Minimum score on a named competency; failure caps the band". A score condition on a named competency. | **Yes.** Section 12.1 matches exactly, including that the subject is a named competency and not a dimension. |
| **Mechanism** | `min`, never an assignment. | "caps the band"; "**Cannot exceed** [band]"; "**cannot be** Ready to Pick". | **Yes.** "Cannot exceed X" is the definition of `min(score, X)`. A candidate at 40 who cannot exceed 71 is still at 40. CLAUDE.md's rationale for preferring `min` reads the Runbook correctly rather than adding to it. |
| **Ceiling** | "Moderately Matching", which `rating.MODERATELY_CEILING` fixes at **74** (cut-points 90 / 75 / 60). | Section 12.1 states **no ceiling at all**, which is the gap Q7 opens. Section 12.2 states "Cannot exceed 'Consider with reservations'"; section 14.1 states "cannot be Ready to Pick", and Ready to Pick begins at 72 (section 10.8), so both land on the top of "Consider with reservations", **71**. | **Same band, different number.** "Consider with reservations" (60 to 71) and "Moderately Matching" (60 to 74) are the same position on two different scales: section 10.8 has five bands plus HOLD, `rating.py` has four. Both mean "genuine candidate, material gap stated". |
| **Position** | Last, on the score, after the authenticity multiplier. | Section 10.1 places "Gates, thresholds, disqualifiers" after "Weighted composite". Section 10.5 makes `RPS = RPS_raw x authenticity_multiplier`. Section 10.8's band table is keyed on **RPS**, not on RPS_raw. Chaining the three, a control whose effect is stated in bands necessarily acts on the post-multiplier RPS. | **Yes**, by composition of three sections rather than by a single sentence. See the note below: cap-last is provably the only safe order. |

#### Two things the comparison turns up

**(a) CLAUDE.md's ordering is right, and its stated reason is not the operative
one.** The rationale given is that "a cap a later multiplication can undo is not
a cap". Under section 10.5 the multiplier is bounded above by 1.00, so a later
multiplication can only push a score *further below* a cap, never undo it. The
two orderings are:

```
cap last  (CLAUDE.md)   delivered = min(x * m, C)
cap first               delivered = min(x, C) * m
```

For any m <= 1 these both satisfy `delivered <= C`, and cap-first is the harsher
of the two whenever x > C, because it penalises twice. The ordering only becomes
load-bearing at m > 1, and there is exactly one regime in this document where
that happens: **Q3**, where the rounded slope 0.0067 lets the multiplier reach
1.0005 at D4 = 75. At m > 1, cap-first yields `C * m > C` and **breaches the
ceiling**, while cap-last yields `min(x * m, C) <= C` for every m without
exception. So cap-last is the only ordering that holds the ceiling absolutely
across the full range of multipliers the Runbook permits. The conclusion in
CLAUDE.md is correct; the reason it gives is imprecise, and the real reason is
stronger. Phase 4 should property-test the invariant directly: **for all
composite scores x >= 0 and all multipliers m in [0, 1.0005], delivered <= C.**

**(b) Section 14.1 catches the case a score-based cap structurally cannot.**
This is the substantive finding, and it is why section 14.1 is not redundant.
Section 10.2 defines the competency score as a weighted mean in which the
evidence strength appears in both the numerator and the denominator:

> Comp(k) = sum over claims c mapped to k of [rubric_level(c) x S_final(c)] /
> sum over claims c of S_final(c)

For a competency supported by a **single** claim, the S_final terms cancel and
`Comp(k) = rubric_level(c)` exactly, whatever the evidence tier. The Runbook
says so in its own words in the same section: "A dazzling claim with weak
evidence and a modest claim with strong evidence can land in the same place -
and that is the intended behaviour." A Must-have resting on one E0 resume bullet
with a high rubric level therefore scores **high**, grades Matching or better,
and **never trips a score-based cap**. Section 14.1 is the control that catches
it, and its trigger is an evidence-tier condition rather than a score condition
for exactly that reason. Nothing in CLAUDE.md's Must-have hard cap implements
it. This is the "beautifully written achievement bullet" case section 6.2 is
written against, and today it passes the cap.

#### What the product implements, and what it does not

`miti/aggregation.py` lines 426 to 433 apply one cap:

```
if any(grade == rating.GRADE_NOT for grade in must_have_grades):
    capped_score = float(rating.cap_to_moderately(final_score))
```

and the comment above it records a deliberate decision to key the trigger on the
item rather than on the dimension: "it is missing THAT one that caps, not being
weak on Verified Competence in aggregate." That decision is defensible on the
merits and it was made without the Runbook in hand. **Section 12.2 states the
dimension-floor caps as binding**, and section 10.3 averages competencies within
a dimension, so the two triggers select genuinely different candidates: a
candidate can fail D1 at 30 in aggregate while every individual Must-have item
scrapes a Matching grade, and today that candidate is delivered above "Consider
with reservations" in breach of section 12.2.

| Runbook control | Trigger | Ceiling | Implemented today |
|---|---|---|---|
| Section 12.1 competency threshold | A named Must-have competency fails its minimum score | Not stated in 12.1; 71 by analogy with 12.2 | **Yes**, as the Must-have hard cap, ceiling 74 |
| Section 12.2 dimension floors | D1 < 45, D4 < 45, D3 < 40 | "Consider with reservations"; D3 caps below Ready to Pick | **No** |
| Section 12.2 HOLD | D4 < 25 | Not ranked, mandatory human disposition | Separately, via the HOLD path |
| Section 14.1 unassessed Must-have | A Must-have has no evidence above E1 | Cannot be Ready to Pick | **No** |

**Which is stricter: the Runbook.** Every candidate capped by CLAUDE.md's rule
is also capped by section 12.1. The converse fails twice over, on section 12.2
and on section 14.1, and the Runbook's ceiling of 71 is three points below the
product's 74.

#### Recommendation

1. **Treat the naming and citation as a correction, not a behaviour change.**
   The Must-have hard cap is section 12.1's competency threshold, with section
   12.2's ceiling and the section 10.1 / 10.5 / 10.8 ordering. Cite it that way
   in CLAUDE.md, in `rating.cap_to_moderately` (which currently cites
   "spec section 5.5") and in `miti/aggregation.py`. No grade moves.
2. **Reconcile the ceiling deliberately, not by accident.** 71 and 74 are the
   same band on two scales. Phase 4's property-based tests should assert the
   resulting **band label**, with the number read from `bands.yaml`, so a test
   cannot pass while capping to the wrong scale's number.
3. **Treat the two missing caps as a coverage gap, and decide it explicitly.**
   Section 12.2's dimension floors and section 14.1's unassessed-Must-have rule
   are stated as binding and are not implemented. Either implement them as
   further `min` ceilings composed by taking the lowest, which is this
   question's standing recommendation, or record a decision not to and cite it.
   Do not leave the gap undeclared: section 14.1 in particular closes the one
   hole a score-based cap cannot close.
4. **Property-test the ordering invariant, not the arithmetic.** `delivered <= C`
   for all x >= 0 and all m in [0, 1.0005], and `delivered <= x * m` so that a
   candidate already below the ceiling is never promoted. Those two properties
   together are the whole of what the cap must guarantee, and they hold
   independently of which ceiling number is chosen.

**Verdict on the original either/or: the first reading.** The rule is a correct
synthesis of mechanisms the Runbook does state, and only the name is invented.
It is also narrower than the Runbook, and the narrowing was a deliberate design
decision recorded in a code comment rather than an oversight, so it needs a
decision rather than a fix.

---

## Q8. Force-ranking supplies default weights for four, five and six competencies only

**Sections:** 20.2, 20.3

**Quote, section 20.3:**

> | Count | Weights (rank order) |
> | 4 | 0.36 / 0.28 / 0.22 / 0.14 |
> | 5 | 0.32 / 0.25 / 0.20 / 0.14 / 0.09 |
> | 6 | 0.30 / 0.23 / 0.18 / 0.13 / 0.10 / 0.06 |

**Quote, section 20.2:**

> **Maximum six. No exceptions.**

**The ambiguity.** Section 20.2 states a maximum and no minimum. Section 20.3
states weights for three counts. A scorecard with one, two or three competencies
is permitted by 20.2 and unweightable by 20.3. Three is not a hypothetical: a
narrow, well-defined specialist seat plausibly has three must-haves, and section
20.2's own diagnosis of the over-long scorecard ("the manager has not decided
what matters") gives no reason to think a short one is wrong.

**Options.**

| Option | Consequence |
|---|---|
| A. Refuse to freeze a scorecard with fewer than four competencies | Adds a minimum the document does not state, and blocks legitimate configurations. |
| B. Derive missing rows by the same shape as the three given (a decreasing sequence summing to 1.00) | Invents three numbers, but they are the only numbers the stated pattern admits for an unweighted rank order, and section 20.3's point is the ordering, not the exact values. |
| C. Equal weights below four | Contradicts section 20.3's whole argument, which is that free or flat assignment "is the same as no ranking at all". |

**Recommendation: B, marked provisional.** Derive and record the derivation,
because refusing a three-competency scorecard is a product restriction that no
sentence in the document supports. Do not use equal weights. Ask the Board to
publish rows for 1 to 3 or to state a minimum.

---

## Q9. Candidate A's worked example reports a band centred on neither of its own scores

**Section:** 10.11

**Quote:**

> RPS_raw = 0.34(61)+0.19(48)+0.22(55)+0.20(52)+0.05(60) = 20.7+9.1+12.1+10.4+3.0
> = **55.3**
> Authenticity multiplier at D4=52 -> 0.70+(52-45)(0.0133) = 0.793
> **RPS = 43.9** - Not recommended. Confidence: Moderate (0.66). Band 50 +/- 7.

**The ambiguity.** Every other number in the example reconciles: the weighted sum
is 55.36, the multiplier at D4 = 52 is 0.7931, their product is 43.9, and the
half-width at confidence 0.66 is 20 x 0.34 = 6.8, printed as 7. **The band centre
of 50 is neither 43.9 nor 55.3.** Candidate B's example on the same page is
internally consistent (RPS 84.1, "Band 84 +/- 3").

**Why it matters.** This is a worked example, not a rule, so it binds nothing.
It matters because an implementer checking their arithmetic against the document
will find a mismatch and will not know whether the centre or their code is
wrong, and because a reader who trusts the example may conclude the band is
centred on something other than the RPS.

**Options.**

| Option | Consequence |
|---|---|
| A. Read it as a typographical slip and centre the band on the RPS | The only reading consistent with Candidate B and with section 10.6. |
| B. Read it as evidence of a rule that the band is centred on something else | No such rule exists anywhere in the document. |

**Recommendation: A**, with the number left in the document. Centre the band on
the RPS. This is the lowest-consequence item in this file, and it is listed
because an unexplained mismatch in a worked example costs a future reader an
hour.

---

## Q10. The fresher variant suspends a dimension the clamp rule forbids zeroing

**Sections:** 21.10, 11.4, 9.2

**Quote, section 21.10:**

> Suspend D2. Redistribute to D1 (+0.10) and D5 (+0.05).

**Quote, section 11.4:**

> **no dimension may fall below 0.05 or rise above 0.40.** No dimension is ever
> zero - a dimension weighted zero is a dimension nobody is accountable for.

**Quote, section 9.2:**

> **Special handling:** for freshers, D2 is suspended or heavily reduced (section
> 8.5, Part VIII).

**The ambiguity.** "Suspend" reads as zero. Section 11.4 forbids zero explicitly
and gives the reason. Section 9.2 offers "suspended **or** heavily reduced"
without saying which. Applied to the IT fresher baseline (D1 0.40, D2 0.05, D3
0.15, D4 0.20, D5 0.20), the section 21.10 instruction also drives D1 to 0.50,
above the 0.40 ceiling, and takes the vector to a sum of 1.10 before
renormalisation.

**Options.**

| Option | Consequence |
|---|---|
| A. "Suspend" means clamp to the 0.05 floor | Obeys section 11.4 literally. A fresher still carries a small D2 weight, which is arguably correct: a fresher with an internship outcome should not have it discarded. |
| B. "Suspend" means exclude D2 from the vector and renormalise the other four | Honours section 21.10 and violates section 11.4's "no dimension is ever zero". Note this is exactly the mechanism section 6.6 uses for an UNKNOWN competency, so the document does contain a precedent for exclude-and-renormalise. |
| C. "Suspend" means exclude only when the candidate has no attributable outcome at all, and clamp to the floor otherwise | Most defensible on the merits, and stated nowhere. |

**Recommendation: A.** Clamp rather than zero. Section 11.4 gives an explicit
reason for the no-zero rule and section 21.10 gives none for the suspension, and
option A is the one under which a fresher who *does* have a corroborated outcome
still gets credit for it. Apply the 0.40 ceiling to the redistributed D1 and
renormalise, per section 11.4. Mark the site
`RUNBOOK-AMBIGUITY (section 21.10)`.

---

## Q11. The intake form has room for five of the eight items its own heading asks for

**Sections:** 16.3, Appendix A3

**Quote, section 16.3:**

> The client names **five to eight behaviours** their strongest performers
> demonstrably show.

**Quote, Appendix A3:**

> ### A3. What "good" looks like - observable evidence only (5-8 items)
> > Format: "Has [done X] and can [describe/demonstrate Y]"
> 13. ______________________________________________
> 14. ______________________________________________
> 15. ______________________________________________
> 16. ______________________________________________
> 17. ______________________________________________

Item 18 is the first question of A4.

**The ambiguity.** Five blanks for a range that goes to eight. A paper form
would be annotated in the margin; a screen implementing the form has to decide
whether the field list is fixed at five or grows to eight, and the two produce
different intake artefacts.

**Why it matters more than it looks.** Section 16.3 calls this "the single
highest-leverage part of the whole intake, because unobservable criteria are
exactly where bias enters", and section 17.1 compiles these statements into
`behavioural_competencies`, each with a default weight inside D3. A client
capped at five when they had eight loses three declared criteria that would
otherwise have moved weights.

**Options.**

| Option | Consequence |
|---|---|
| A. Five fixed fields | Matches the form's item numbering exactly. Loses items six to eight, contradicting both headings. |
| B. Five required fields, three optional, matching "5-8" | Matches both prose statements. The form's numbering then runs 13 to 20, which collides with A4's item 18. |
| C. A repeating field with a minimum of five and a maximum of eight, dropping the fixed numbering | Matches the intent and abandons the paper form's numbering, which the field form uses as its item identifiers. |

**Recommendation: C for the implementation, B for the paper form.** The
numbering exists to identify items on a printed sheet; a screen does not need
it. `company_dna_instrument.yaml` records the range as
`item_count_minimum: 5` / `item_count_maximum: 8` from section 16.3, and the
five Appendix A blanks separately, so both facts survive. Ask the Board to add
blanks 6 to 8 and renumber A4.

---

## Q12. An adjusted tier strength can exceed 1.00

**Section:** 6.1

**Quote:**

> | **E5** | Third-party verified | ... | Very high | 0.95 |
>
> These are **default** strengths. Department models may adjust within +/-0.10
> with justification, recorded in the department model, not applied ad hoc.

**The ambiguity.** E5 adjusted upward reaches 1.05. Section 6.5 caps the
*combined* strength `S*(c)` at 1.0 but places no cap on `tier_strength(e)`
itself, and the per-evidence product `s(e)` is then multiplied by up to 1.2
(specificity), 1.1 (attribution) and 1.15 (scale relevance) before the group
aggregation, which is `1 - product of (1 - max s(e))`. A single `s(e)` above 1.0
makes that term negative, and the aggregate exceeds 1.0 before the section 6.5
`min` catches it. The `min` does catch it, so nothing overflows; but the
intermediate is meaningless and the ordering between two strong claims can
invert.

No department model in the document actually adjusts E5 upward, so this is
latent rather than live.

**Options.**

| Option | Consequence |
|---|---|
| A. Clamp `tier_strength` to [0, 1] after any departmental adjustment | One line, no behaviour change today, removes a latent inversion. Adds a clause the document does not contain. |
| B. Read the +/-0.10 allowance as bounded by 1.0 implicitly | Same effect, expressed as interpretation rather than as a new rule. |
| C. Do nothing; the section 6.5 `min` is sufficient | True for the final value, false for the ordering of intermediates. |

**Recommendation: B**, implemented as A. Mark the site
`RUNBOOK-AMBIGUITY (section 6.1)` and ask the Board to state the cap.

---

## Q13. A citation that may point at the wrong section, and was therefore not touched

**Section:** 6.4

**Quote:**

> Our validation instruments are designed specifically to force this distinction
> (section 18.5, section 21.3).

**The ambiguity.** The distinction in question is "participated in" versus
"owned". Section 18.5 is SWOT quality control, whose six rejection rules are
about the hiring manager's intake, not about probing a candidate's attribution.
Section 21.3 is the IT competency menu, which names ownership competencies but
contains no instrument. The sections that actually force the attribution
distinction are 21.6 (the validation probe table, whose Ownership row is exactly
this) and Appendix D.2, titled "Attribution probes".

**Why it was not fixed.** Both cited sections exist and both are topically
adjacent, so this is not a demonstrably broken pointer of the kind section 2.1
permits repairing. Two other pointers in this document were demonstrably broken
and were repaired; see `RUNBOOK_EDITS.md` rows 20 and 21. This one is a
judgement call about authorial intent, and guessing at intent is how a
correction becomes a change.

**Options.** Repair to (21.6, Appendix D.2); repair to (21.6, 18.5, Appendix
D.2); leave as is.

**Recommendation: leave as is** and ask the author. The cost of the current
state is one reader following a pointer to a section that half answers their
question.

---

## Q14. "Weakly independent" is a third value in a count that admits two

**Section:** 5.4

**Quote:**

> | Resume + validation questionnaire (async, written) | **Weakly** | Same
> authorship, but probes are unseen and specific |

Every other row in the table reads **Yes** or **No**.

**The ambiguity.** Section 5.4's counting rule is "the number of distinct groups
supporting it", which is an integer. A weakly independent pair contributes an
amount the document does not state: 0, 1, or something between. The number feeds
the corroboration multiplier (section 6.5: 1 -> 1.00, 2 -> 1.15, 3 -> 1.28,
4+ -> 1.40), the independence term of the confidence score (section 10.7,
weighted 0.20, normalised by 3), and both the section 6.7 sufficiency floor and
the section 7.4 minimum source standards, which are stated as integer counts.

This is not an edge case. Resume plus validation questionnaire is the most
common evidence pair in the product: it is what exists for every candidate
before any live stage.

**Options.**

| Option | Consequence |
|---|---|
| A. Count "Weakly" as 0 groups | The most common pair in the product corroborates nothing, and a candidate who has answered a validation questionnaire is no better evidenced than one who has not. Under-counts systematically. |
| B. Count "Weakly" as 1 group | Treats it identically to a live probe plus a reference. Section 5.4 says explicitly that it is not that. Over-counts. |
| C. Count it as 1 group but never toward the section 7.4 minimum or the section 6.7 "beyond self-report" requirement | Gives the questionnaire credit where the document says it earns some ("probes are unseen and specific") and withholds it where the document requires genuine independence. Requires stating a rule the document does not contain. |
| D. Represent independence as a three-valued enum on the pair and let each consumer decide | Pushes the question to four call sites instead of settling it once. |

**Recommendation: C.** Section 6.7's Moderate row requires "at least 1
independent group **beyond self-report**", and section 38.1 puts the validation
questionnaire in `self_structured`, which is self-report. C is the only option
that respects both that sentence and section 5.4's "Weakly". Mark the site
`RUNBOOK-AMBIGUITY (section 5.4)`.

---

## Q15. "Insufficient" has two independent definitions

**Sections:** 6.7, 10.7

**Quote, section 6.7:**

> | **Insufficient** | Fewer than half of must-haves have any evidence above E1
> -> candidate is NOT delivered; either collect more or exclude with reason |

**Quote, section 10.7:**

> | < 0.40 | **Insufficient** | Not delivered. Collect more evidence or exclude
> with reason. |

**The ambiguity.** Two rules, one label, one consequence, and no statement of
their relationship. They can disagree in both directions. A candidate with
excellent coverage but an unresolved severe contradiction can fall below 0.40 on
the arithmetic while more than half their must-haves have evidence above E1. A
candidate with two of five must-haves evidenced, all at E5, with three
independent groups each and no contradictions, can score above 0.40 while
failing the section 6.7 test.

The consequence in both cases is the same and is severe: not delivered.

**Options.**

| Option | Consequence |
|---|---|
| A. Disjunctive: either condition makes the candidate Insufficient | Excludes more candidates. Never delivers a candidate that either rule says should not be delivered. |
| B. Conjunctive: both must hold | Delivers candidates each rule independently says are not deliverable. |
| C. Section 10.7 governs, section 6.7 is descriptive | Section 6.7's table is headed "the universal floor", which does not read as descriptive. |

**Recommendation: A.** Both sections state a floor and a floor is a floor. The
error direction under A is that a deliverable candidate is held for more
evidence collection, which section 6.6 and section 14.1 both treat as the
correct response to a thin evidence base. Mark the site
`RUNBOOK-AMBIGUITY (section 6.7)`.

---

## Summary

| ID | Sections | One line | Recommendation |
|---|---|---|---|
| Q1 | 18.4, 11.3 | Scale-up and Succession have no numeric weight consequence | Apply none, block at the gate, escalate |
| Q2 | 11.1, 11.4 | Three Layer 1 baselines breach the clamp | Clamp as written, record every clamp |
| Q3 | 10.5 | Authenticity multiplier can exceed its own 1.00 cap | Outer `min(1.00, ...)` |
| Q4 | Part VI, 67.8 | The eleven-section claim is not what the document delivers | Record the real counts, do not soften the claim |
| Q5 | 10.6 | `band_width` is named a width and behaves as a half-width | Half-width |
| Q6 | 5.4, 38.1 | An unlisted source type has no independence classification | Default to DEPENDENT |
| Q7 | 12.1, 12.2, 14.1 | The Must-have hard cap is not a rule the Runbook states | Minimum of whichever ceilings fire. See the Q7 addendum: the product rule is a correct synthesis of 12.1, 12.2 and 10.1/10.5/10.8, and only the name is invented, but it implements one of the three caps the Runbook states |
| Q8 | 20.3 | No default weights below four competencies | Derive, mark provisional |
| Q9 | 10.11 | A worked example's band centre matches neither of its scores | Centre on the RPS |
| Q10 | 21.10, 11.4 | "Suspend D2" against "no dimension is ever zero" | Clamp to the 0.05 floor |
| Q11 | 16.3, Appendix A3 | Five blanks for a five-to-eight range | Repeating field, 5 to 8 |
| Q12 | 6.1 | An adjusted tier strength can exceed 1.00 | Clamp to [0, 1] |
| Q13 | 6.4 | A citation that may name the wrong sections | Leave; ask the author |
| Q14 | 5.4 | "Weakly independent" is a third value in an integer count | Count as 1, never toward the minimums |
| Q15 | 6.7, 10.7 | "Insufficient" has two independent definitions | Disjunctive |

Fifteen questions, none of them applied to the document. Q1, Q2 and Q7 change
delivered grades and should be settled before Part A goes live on real
candidates. Q9 and Q13 cost a reader time and nothing else.

Q7 carries an addendum answering whether CLAUDE.md's Must-have hard cap
follows from the Runbook. It does, on behaviour; the name and the citation
are invented, and two of the three caps the Runbook states are not
implemented. Read it before writing the Phase 4 property-based tests.

---

# ADDENDUM: questions raised by the PHASE 0b code reconciliation

Appended by the spec-doc6 section 2.3 reconciliation of the nine
`ASSUMPTION (RUNBOOK-GAP` sites, not by the section 2.1 editorial pass. Same
rules: the document is unchanged at every site, the safer option was
implemented, and the code site carries a `RUNBOOK-AMBIGUITY (section N)` marker
pointing here.

Q1 above already covers the missing multiplier for section 18.4's arrows, and
the reconciliation reached it independently from the code side. Q4 above covers
Part VI's claimed structure. The seven below are additional.

---

## Q16. The Runbook bounds a layer modifier additively; the engine bounds it multiplicatively

**Sections:** 11.2, 11.3, 11.4, 3.5

**Quote, section 11.3:**

> | Hire is a turnaround / crisis mandate | D2 up, D3 up | +0.08 combined |

**Quote, section 3.5:**

> | L3 weight request exceeds declared bounds | Clamp to bound; notify hiring manager; record the request. |

**The ambiguity.** Sections 11.2 and 11.3 state every Layer 2 and Layer 3
modifier as a signed DELTA on a dimension weight with an absolute cap, and
section 11.4 then clamps and renormalises the vector. `layers.BOUNDS` bounds a
MULTIPLIER around 1.0 on a per-COMPETENCY quantity: a competency's weight, its
evidence threshold, its dimension threshold, its question emphasis. These are
different objects at different granularities, and the Runbook gives a figure for
one of them and not the other. There is no conversion between an additive
dimension delta and a multiplicative competency bound without fixing a baseline.

**Options.**

1. **Keep both, as the reconciliation did.** Section 11.4 governs the five
   dimension weights (`layers.clamp_weight_vector`), and `layers.BOUNDS` governs
   the per-competency quantities the Runbook does not price. Consequence: two
   bounding mechanisms, each correct for its own object, and the per-competency
   figures remain unsourced.
2. **Make the engine additive throughout.** Consequence: `transformation`'s
   four-term weight (`baseline x company x situation x role`) stops being a
   product, and CLAUDE.md records that shape as a deliberate decision with its
   provenance argument. This is a Part A architecture change, not a value change.
3. **Have the Runbook state per-competency bounds.** Consequence: the cleanest,
   and it is a Standards Board addition rather than an implementer's.

**Implemented:** option 1, and the per-competency bounds are marked. They are
asymmetric in the restrictive direction already: a competency can never be
deleted (floor 0.5, not 0.0), and a client may raise an evidence bar freely
(ceiling 3.0) and lower it only marginally (floor 0.8).

**Recommendation:** option 3. Until then the figures in `layers.BOUNDS` are
professional judgement of exactly the kind section 11.1 is honest about being,
and they should be labelled as such rather than inheriting the Runbook's
authority by proximity.

---

## Q17. Section 11.4's own step order breaches section 11.4's own clamp

**Section:** 11.4

**Quote:**

> 3. Clamp each `W_i` to its floor and ceiling: **no dimension may fall below
>    0.05 or rise above 0.40.**
> 4. **D4 floor is 0.12 and cannot be lowered by any client.**
> 5. Renormalise so `Sigma W_i = 1.0`.

**The ambiguity.** Read as a strict sequence, step 5 undoes step 3. Scaling a
clamped vector so it sums to 1.0 multiplies every weight by the same factor, so
a weight sitting exactly on the 0.40 ceiling ends up above it whenever the
clamped vector summed to less than 1.0. Measured on a real vector during the
reconciliation: D1 clamped to 0.40 left the function at 0.4598, with the sum
correct and the ceiling breached. The same happens in the other direction to the
0.05 and 0.12 floors when the clamped vector sums to more than 1.0.

This is distinct from Q2. Q2 observes that three section 11.1 BASELINES already
sit outside the clamp before any modifier is applied. Q17 observes that the
clamp cannot survive its own normalisation step even for a baseline that starts
inside it.

**Options.**

1. **Clamp and renormalise to a fixed point.** Consequence: every one of section
   11.4's six steps is true of the vector that leaves the function. The cost is
   that the operation is no longer the literal six-step sequence printed, and a
   reader comparing code to document finds a loop where the document has a list.
2. **Clamp last, and accept a vector that does not sum to 1.0.** Consequence:
   the bounds hold and step 5 fails. Every downstream consumer that assumes a
   normalised vector is then wrong, silently.
3. **Renormalise last and accept a bound breach.** Consequence: the D4 floor,
   which section 11.4 says "cannot be lowered by any client", can be lowered by
   arithmetic. This is the worst of the three: it makes a Layer 1 integrity
   property depend on the shape of the rest of the vector.

**Implemented:** option 1. It is the reading under which all six steps hold, and
it is the same argument `layers.resolve` already makes about a composed product:
a bound that holds at each step and not on the result is not a bound.

**Recommendation:** option 1, stated explicitly in section 11.4 so that the loop
is the document's and not the implementer's.

---

## Q18. Part VI supplies no per-competency weight, and the engine needs one

**Sections:** Part VI preamble, 11.1, 20.2, 20.3

**Quote, Part VI preamble:**

> **Universal rule across all departments:** the competency list in each model is
> the *menu*. The scorecard for a given role selects at most six from it,
> weighted by SWOT force-ranking. No role uses the whole menu.

**The ambiguity.** Section 11.1 weights the five DIMENSIONS. Part VI lists
competencies. Section 20.3 puts competency importance in the hiring manager's
force-ranking at Layer 3. So no layer supplies a Layer 1 baseline weight for a
competency, and `department_models.BaselineCompetency.baseline_weight` has no
source. It is nevertheless what orders a matrix before any SWOT exists, and
`match_competency` has nothing else.

**Options.**

1. **Keep a Layer 1 per-competency baseline, unsourced and marked.**
   Consequence: what the reconciliation did. The number is honest about being
   unsourced, and it still moves every weight in the product.
2. **Delete it and require a force-ranking before any weight exists.**
   Consequence: faithful to section 20.3 and to C1 ("we will not score a single
   candidate until a completed, force-ranked scorecard exists"). It also means a
   job with no SWOT cannot produce a matrix at all, which is correct under C1
   and is a behaviour change for every existing job.
3. **Derive it from section 11.1 through the competency's dimension.**
   Consequence: requires the competency-to-dimension map that Q19 says does not
   exist, so it moves the problem rather than solving it.

**Implemented:** option 1, marked, because option 2 is a Part A behaviour change
that belongs in a phase with a migration and not in a reconciliation.

**Recommendation:** option 2, sequenced deliberately. It is what C1 and section
20.3 jointly describe, and the current baseline is a number that looks like
Layer 1 authority and is not.

---

## Q19. Nothing maps a competency to a dimension

**Sections:** 9, 21.3 (and every Part VI competency menu)

**The ambiguity.** Section 9 defines the five dimensions. Section 21.3 and its
fourteen siblings list competencies with observable evidence. Nothing between
them says which competency evidences which dimension, and Miti's five isolated
evaluators each need to know which competencies are theirs. Section 57.3 states
the evaluator's input as "competency set for this dimension" and takes the
mapping as given.

**Options.**

1. **Keep the engine's `primary_dimension` hint, marked.** Consequence: what the
   reconciliation did. Routing works and its provenance is the implementer's.
2. **Add the column to Part VI's menus.** Consequence: fifteen tables gain a
   column, and the mapping becomes reviewable by the Standards Board, which is
   where a decision about what evidences what belongs.
3. **Route every competency to every evaluator.** Consequence: destroys the
   isolation section 57.3 requires and the halo-effect protection it exists for.

**Implemented:** option 1.

**Recommendation:** option 2. The mapping is a substantive claim about
evaluation, and it currently lives in a Python constant nobody outside
engineering has read.

---

## Q20. Section 11.1's seniority bands and the product's grade vocabulary do not correspond

**Section:** 11.1

**The ambiguity.** Section 11.1 bands seniority per department family, and the
bands differ between families: IT and Software runs Fresher / 2-5 yrs / 5-10 yrs
/ 10+ or Principal / Eng leadership; Finance adds a CFO row; Leadership runs
First-line manager / Senior manager / Director or VP / CXO; trades run Entry /
Experienced / Supervisory. This codebase has one four-grade vocabulary,
`non_managerial | managerial | leadership | cxo`, used by the whole product, and
CLAUDE.md records that a fifth vocabulary for "how senior is this role" is how
the product previously ended up with two parallel five-label rating scales.

There is no total mapping. "2-5 yrs" and "5-10 yrs" both fall under
`non_managerial`, and choosing between them changes D1 by 0.04 and D2 by 0.06.

**Options.**

1. **Keep both and map at the call site, visibly.** Consequence: what the
   reconciliation did. `baseline_dimension_weights` takes the Runbook's own band
   label, so a reader can see which section 11.1 row a job was weighted against.
   The mapping itself is still somebody's, but it is not hidden.
2. **Adopt the Runbook's bands product-wide.** Consequence: a fifth seniority
   vocabulary in a product that has one, against an explicit standing rule, and
   it reaches every report, email and route that names a grade.
3. **Collapse section 11.1 to four bands.** Consequence: changes weights, which
   section 2.1 forbids applying to the document, and loses the per-family
   distinctions that are the table's point.

**Implemented:** option 1.

**Recommendation:** option 1, with the specific mapping written into the Runbook
as an implementation note so it is reviewed once rather than chosen per call
site. This is a genuine conflict between the Runbook and a standing project
rule, and it is surfaced rather than silently decided.

---

## Q21. Section 58 requires an ontology and does not supply one

**Section:** 58

**Quote:**

> a skills/competency ontology is required so that vocabulary mismatch does not
> cause missed evidence: "graph database" and "semantic technologies," "GD&T"
> and "geometric tolerancing," "FP&A" and "business finance" must resolve to the
> same competency node.

**The ambiguity.** Three pairings are named. The requirement is unambiguous and
its fairness argument is stated plainly. The TABLE is not supplied, and the
Runbook says nowhere else what belongs in it. `ontology.EQUIVALENCE_GROUPS`
currently holds forty-odd groups of which three entries are section 58's.

This is not a cosmetic gap. Section 58 itself says pure vector similarity "will
systematically undervalue candidates who describe their work in non-standard
vocabulary, which correlates with non-standard backgrounds", which makes the
table a fairness-relevant artefact. A wrong entry credits a candidate with
something they did not do; a missing entry costs a candidate ranking for a word
they did not use.

**Options.**

1. **Keep the curated table, marked, and review it.** Consequence: what the
   reconciliation did. The three Runbook pairings resolve, and the rest are
   unreviewed.
2. **Reduce it to section 58's three.** Consequence: strictly sourced, and it
   reintroduces the exact fairness problem section 58 raises for every other
   term.
3. **Add an ontology appendix to the Runbook.** Consequence: the table becomes
   reviewable and versioned like every other Layer 1 artefact.

**Implemented:** option 1.

**Recommendation:** option 3, and the appendix should record who reviewed each
group. Section 52.4's proxy audit is the natural owner.

---

## Q22. Section 18.5 rejects "every competency is marked must-have" without stating a share

**Sections:** 18.5, 20.3

**Quote, section 18.5:**

> An intake is rejected back to the hiring manager if any of:
> - Every competency is marked must-have

**The ambiguity.** "Every" is literal, and a scorecard with nineteen must-haves
and one nice-to-have is not "every" while being the same failure. Section 20.3
requires a force-ranking with no ties, which implies a distribution but states
no threshold. `swot_quality.MAX_MUST_HAVE_SHARE` is 0.67, which is an
implementer's number.

**Options.**

1. **Refuse only at literally 100 percent.** Consequence: faithful to the
   wording, and the rule catches almost nothing. A hiring manager who marks
   nineteen of twenty essential produces a matrix where every imperfect
   candidate grades the same, which is what the rule exists to prevent.
2. **Refuse above a stated share.** Consequence: the rule works, and the share
   is unsourced. Two thirds was chosen because a genuinely demanding role
   legitimately has more essentials than nice-to-haves, and refusing that would
   be the platform telling a hiring manager they are wrong about their own job.
3. **Derive it from section 20.2's six-competency ceiling.** Consequence: at
   most six competencies with a forced ranking and no ties, so "how many may be
   must-have" becomes a rank cut-off rather than a share. This is probably what
   sections 20.2 and 20.3 jointly intend and the Runbook does not say it.

**Implemented:** option 2, which refuses more than option 1.

**Recommendation:** option 3, stated in section 18.5.

---

---

## Q23. Rubric anchors are per dimension; spec-doc6 and the engine both looked per department

**Sections:** 9.1 to 9.5, 21.11, 57.3

**Quote, section 57.3 (dimension evaluator):**

> **Input:** competency set for this dimension, retrieved rubric anchors from
> the department model, evidence mapped to those competencies, seniority
> context.

**The ambiguity.** Section 57.3 says the rubric anchors are retrieved "from the
department model". No department model contains any. Sections 9.1 to 9.5 each
carry one six-band scoring-anchor table over 0 to 100, and those are universal:
stated once per DIMENSION and never restated per department or per seniority.
Exactly one department carries anything per seniority, section 21.11's
"Seniority notes" for IT and Software, and those are an emphasis shift rather
than an anchor. Fourteen departments have neither.

spec-doc6 section 2.2 inherits the same reading when it asks for "department
competency models, per-seniority rubric anchors", and the pre-Runbook engine
inherited it too: `department_models.DepartmentModel.anchors` holds anchor
wording per department per seniority, which is fifteen inventions where the
document has one universal table per dimension.

**Options.**

1. **Read section 57.3 as loose phrasing.** The evaluator retrieves the
   COMPETENCY SET from the department model and the ANCHORS from section 9.x.
   Consequence: everything in the document is consistent, and section 57.3's
   sentence is imprecise. Anchors become universal, which is what makes two
   candidates in different departments comparable on the same dimension.
2. **Read section 57.3 as a requirement Part VI does not yet meet.**
   Consequence: fifteen department models each need a per-seniority anchor
   table, which is a large Standards Board authoring job, and section 67.8
   already concedes department coverage is uneven.
3. **Keep the engine's per-department anchors.** Consequence: fifteen sets of
   unsourced wording decide what "strong" means, and two candidates on the same
   dimension in different departments are graded against different sentences
   that nobody reviewed.

**Implemented:** option 1. `dimension_rubric_anchors` reads sections 9.1 to 9.5
from the data package and is the source an evaluator should use;
`seniority_emphasis` exposes section 21.11 and returns an empty mapping for the
fourteen departments with none, which is the true answer rather than an invented
one. The engine's `anchors` field stays, marked, because Sutra and Vaada read it
today and there is no per-seniority replacement for fourteen departments.

**Recommendation:** option 1, with section 57.3's wording corrected to say the
competency set comes from the department model and the anchors from section 9.x.
That is an editorial repair of a cross-reference, of the same kind as the C5 and
section 6.3 repairs already made in v1.1, and it should be logged the same way.

## Addendum summary

| # | Sections | The question | Implemented |
|---|---|---|---|
| Q16 | 11.2, 11.3, 11.4, 3.5 | Additive Runbook bounds against multiplicative engine bounds | Keep both, mark the per-competency figures |
| Q17 | 11.4 | Renormalisation undoes the clamp it follows | Iterate to a fixed point |
| Q18 | Part VI, 11.1, 20.3 | No per-competency Layer 1 weight exists | Keep the engine's, marked |
| Q19 | 9, 21.3 | Nothing maps a competency to a dimension | Keep the engine's hint, marked |
| Q20 | 11.1 | Seniority bands do not correspond to the product's four grades | Map at the call site, visibly |
| Q21 | 58 | An ontology is required and not supplied | Keep the curated table, marked |
| Q22 | 18.5, 20.3 | No share is stated for "every competency is must-have" | Refuse above two thirds |
| Q23 | 9.1-9.5, 21.11, 57.3 | Anchors are per dimension; 57.3 says per department model | Read 57.3 as loose phrasing |

Eight questions, none applied to the document. Q17 and Q20 are the two that
should be settled before Part A goes live: Q17 can lower the D4 floor that
section 11.4 says no client may lower, and Q20 decides which section 11.1 row a
real job is weighted against.
