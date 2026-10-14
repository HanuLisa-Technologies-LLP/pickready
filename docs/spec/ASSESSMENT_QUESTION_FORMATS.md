# Assessment question formats

**Status:** current. **Reviewed:** 2026-10-14 against `services/assessment_formats/` and `services/assessment_questions/` module names. Implemented 2026-09-02 from the owner's assessment specification v1.0 (owner: Manju, Hanulisa Technologies LLP) and reshaped by the 2026-09-25 release. How many questions an assessment has and of which kind is [ASSESSMENT_FLOW.md](ASSESSMENT_FLOW.md) section 2; this page is the formats themselves. Coding questions are **decided, pending a redesign** ([CODING_ASSESSMENT.md](CODING_ASSESSMENT.md)).

## The formats

Six formats, each with its own payload and answer shape: evidence-based, short answer, multiple choice (single answer and multi-answer), fill-in-the-blank, coding. Evidence-based and short-answer are open-ended prose and carry most of the assessment; multiple choice and fill-in-the-blank are the objective formats; coding is a supporting format for coding roles only.

- The budget and the mix are pure and deterministic (`services/assessment_questions/budget.py`): one question per skill in the bound contract, never below the grade's floor; prose 70 percent, coding 20, objective 10, counted in questions by largest remainder. `services/assessment_formats/composition.py` turns the mix into slots; the writers fill each slot's content. A slot a writer cannot fill becomes prose with a recorded reason, so the served mix differs from the plan only by a named degradation.
- Every bound is a setting (`assessment_*` in `backend/app/core/config.py`), read once by `services/assessment_formats/config.py`.

## Data model

`candidate_questions` is the per-candidate question row. It holds the format (`question_type`), the type-specific payload with its answer key (`payload_json`), the resume anchor, the time allocation and a weight; rows from before the formats read as `short_answer`. Each row carries the digest of the contract it was written against. `assessment_answers` is the structured answer record: the answer in its type's shape, the deterministic auto-score for an objective type, the AI evaluation with its reasoning for a subjective one, and the revision count. The transcript (`assessment_messages`) remains the conversational record every scorer reads. Time is the server's: turn stamps and pause rows, never a client duration ([ASSESSMENT_FLOW.md](ASSESSMENT_FLOW.md) section 4).

## The candidate boundary

`services/assessment_formats/types.candidate_view` is the only function that turns a stored payload into what a candidate sees, an allowlist per type: a multiple-choice question's options in that candidate's own deterministic order with no correct id, a fill-in-the-blank template with blank sizes and no accepted answers, a coding question's language and starter code with no expected approach. A field added to a payload later is absent from the candidate's view until it is named there.

## Delivery and scoring

Text formats go through the conversational machinery: the question is written for this candidate at this point, answers are classified, non-answers are re-asked, thin answers are followed up under the same key. Structured formats are delivered verbatim, because their answer key was written with them, and scored on submission, deterministically, server-side:

- single-answer multiple choice is binary;
- multi-answer multiple choice uses partial credit `(correct_selected - incorrect_selected) / total_correct` floored at zero, so selecting everything scores zero;
- fill-in-the-blank matches case-insensitively and whitespace-trimmed (case-sensitive per blank where the key says so) and escalates an exact-match failure to an AI equivalence check before marking it wrong.

Subjective formats are evaluated after submission against the stored rubric, with reasoning that cites specific parts of the answer: specificity, ownership clarity, technical depth for the claimed seniority, coherence with the resume claim and honesty markers. Coding evaluation follows [CODING_ASSESSMENT.md](CODING_ASSESSMENT.md); the recruiter is always told whether code was run. Nothing from proctoring enters any of this.

## The recruiter's Q&A view

`GET /api/v2/assessments/transcripts/links/{link_id}` carries, per exchange, the format, the resume anchor for an evidence question (what was being probed), and a detail block with the candidate's structured answer beside the answer key, correctness as a word, per-blank results, the evaluation reasoning and time spent as a phrase. No number crosses the boundary.

## The frontend

One dispatcher (`components/assessment/question-renderer.tsx`), one props contract (`lib/assessment/contracts.ts`), six components. All share debounced autosave that survives a refresh, an autosave indicator, keyboard accessibility, a responsive layout and the proctoring field hooks. The code editor is Monaco, self-hosted, with every suggestion surface off and copy, cut, paste and drop refused (each attempt reported once with its kind). Navigation stays linear, one question at a time.

## Explicitly not built

Recruiter configuration of the format mix, a question bank or reusable templates, video or audio answer formats (spoken answers are transcribed to text, [ASSESSMENT_FLOW.md](ASSESSMENT_FLOW.md) section 5), collaborative formats.
