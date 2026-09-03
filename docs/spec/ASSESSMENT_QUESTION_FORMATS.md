# Assessment question formats

Status: implemented 2026-09-02, per `assessment-spec-doc.md` v1.0 (owner:
Manju, Hanulisa Technologies LLP). This file records how the specification
landed in this codebase and where each rule is enforced.

## The core principle, made structural

Evidence-based questions are the primary instrument. MCQ, fill-in-the-blank
and coding are supporting formats: they establish baseline competence around
the core and cannot close an evidence gap. That ratio is enforced in code, not
suggested in a prompt:

- `services/assessment_formats/composition.py` decides the format mix per job
  and validates every generated assessment against the six composition rules
  before it is served. Evidence questions must carry the majority of total
  weight AND total time; the supporting formats are bounded to a minority of
  the question count (a smaller minority for leadership and CXO); coding
  appears only on STEM roles; every evidence question is anchored to a
  specific, quotable resume item; no two questions share an anchor and no two
  questions probe one competency with the same structured format; the total
  time allocation fits the role's duration.
- A mix that fails validation is regenerated up to a bounded number of times
  and then falls back deterministically by turning the supporting slots back
  into evidence questions, so what reaches a candidate is always valid.
- `CandidateQuestion.weight` makes the dominance structural inside scoring: a
  matrix item's score is the weighted mean of its questions' scores, and a
  supporting-format question carries less of the item than an evidence
  question does.

Every bound is a setting (`assessment_*` in `backend/app/core/config.py`),
read once by `services/assessment_formats/config.py`.

### One ambiguity, resolved and measured

The specification splits the assessment two ways: evidence-based questions
carry the majority of time and weight, and the supporting formats (MCQ,
fill-in-the-blank, coding) are the minority. It never says which side
`SHORT_ANSWER` sits on. It is read here as an open-ended probe on the evidence
side, because that is the format the product already asked every behavioural
competency in before this change, and because the contrast the specification
actually draws is open-ended probing against recall-style formats.

That reading has a consequence worth stating plainly, measured on 2026-09-03
across all four grades on both role classifications:

| Share | Range across the eight role configurations |
|---|---|
| Evidence-based alone, of the whole assessment | 35.9% to 63.7% |
| Open-ended (evidence plus short answer), which rule 1 enforces | 87.2% to 94.7% |
| Evidence-based, of Must-have plus Nice-to-have, which rule 1b enforces | 73.7% to 90.9% |

So on a managerial STEM role, evidence-based questions alone are a minority of
the whole assessment. That is not supporting formats taking over: it is the
behavioural third of the Tatva matrix, which is judgement-scored prose by
product decision and cannot be anchored to a resume claim, because there is
nothing on a resume to quote for "judgement under pressure". Requiring
evidence-based to exceed half of everything would be a requirement on the
behavioural dimension's size rather than on the format mix.

Rule 1b exists so the reading cannot be abused. On the Must-have and
Nice-to-have slots every question is resume-anchorable, so the specification's
sentence has one reading, and that is where an assessment quietly filling up
with MCQs would show. The floor is the same `evidence_min_share`, and the
composer clears it by a wide margin everywhere. It is scoped to an assessment
that actually uses a supporting format, so the deterministic all-text fallback
(the product's behaviour from before formats existed) is not refused.

**This is the one judgement call in the feature that an owner might make
differently.** Tightening it to evidence-based alone means either shrinking the
behavioural dimension or accepting fewer behavioural probes.

## Data model

`candidate_questions` IS the specification's `assessment_questions` table.
It already held one row per question per candidate, so migration 0076 added
the format columns rather than a second table: `question_type`,
`payload_json` (the type-specific structure, including the answer key),
`resume_anchor`, `time_allocation_seconds` and `weight`. Rows written before
the migration read as `short_answer`, which is the honest classification for a
text question with no stored anchor.

`assessment_answers` is the structured answer record: the answer as
submitted in its type's shape, when the question was opened and answered
(measured by the server from `assessment_conversations.prompt_shown_at`, less
the time a blocking proctoring warning held the screen), the deterministic
auto-score for an objective type, the AI evaluation with its reasoning for a
subjective one, and the revision count. The transcript (`assessment_messages`)
is unchanged and remains the conversational record every scorer reads.

## The candidate boundary

`services/assessment_formats/types.candidate_view` is the only function that
turns a stored payload into what a candidate sees, and it is an allowlist per
type: an MCQ's options in that candidate's own deterministic order with no
correct id, a fill-blank's template with blank sizes and no accepted answers,
a coding question's language and starter code with no expected approach. A
field added to a payload later is absent from the candidate's view until it
is named there.

## Delivery and scoring

Text formats (evidence-based, short answer) go through the existing
conversational machinery unchanged: the question is written for this
candidate at this point, answers are classified, non-answers are re-asked,
thin answers are followed up under the same key. Structured formats are
delivered verbatim, because their answer key was written with them, and are
scored on submission, deterministically, server-side: single-answer MCQ is
binary; multi-answer MCQ uses partial credit
`(correct_selected - incorrect_selected) / total_correct` floored at zero,
so selecting everything scores zero; fill-in-the-blank matches
case-insensitively and whitespace-trimmed (case-sensitive per blank where the
key says so) and escalates an exact-match failure to an AI equivalence check
before marking it wrong.

Subjective formats are evaluated after submission against the stored rubric,
with reasoning that cites specific parts of the answer. Evidence questions are
judged on specificity, ownership clarity, technical depth for the claimed
seniority, coherence with the resume claim and honesty markers. Coding is
judged by reading, never by execution: the prompt says so, the stored
evaluation carries the note, and the recruiter's view shows it.

Nothing from proctoring enters any of this.

## The recruiter's Q&A view

`GET /api/v2/assessments/transcripts/links/{link_id}` carries, per exchange,
the format, the resume anchor for an evidence question (the most valuable
thing on the screen: what was being probed), and a detail block with the
candidate's structured answer beside the answer key, correctness as a word,
per-blank results, the evaluation reasoning, the not-executed note for code
and time spent as a phrase. No number crosses the boundary.

## The frontend

One dispatcher (`components/assessment/question-renderer.tsx`), one props
contract (`lib/assessment/contracts.ts`), six components. All share debounced
autosave that survives a refresh, an autosave indicator, keyboard
accessibility, a responsive layout and the proctoring field hooks. The coding
editor is CodeMirror 6 with no autocompletion and paste blocked at the editor
as well as by the proctoring lockdown. Navigation stays linear, one question
at a time.

## Explicitly not built (spec section 10)

A code execution engine, recruiter configuration of the format mix, a
question bank or reusable templates, video or audio answer formats,
collaborative formats.
