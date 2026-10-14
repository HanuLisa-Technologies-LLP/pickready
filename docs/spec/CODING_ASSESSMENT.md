# Coding assessment

**Status:** decided 2026-10-14, confirmed in the second ruling (register S-20), **not built**. **Reviewed:** 2026-10-14 against `services/assessment_questions/budget.py`, `services/code_execution/`, `services/coding_assessment/`.
Replaces the sandbox design recorded in [../history/SUPERSEDED.md](../history/SUPERSEDED.md). Where the code and this page disagree, this page states the intent and the code is owed a change ([../decisions/REGISTER.md](../decisions/REGISTER.md), Pending change 2).

## 1. The decision

- Candidate code is **never executed** by the product. No sandbox (Judge0) is required, and the sandbox stays switched off.
- The candidate writes code in the editor. A **static checker tool** analyses it without running it (type and syntax errors) and **AI reviews and evaluates it**.
- Coding questions go **only to coding roles**: a STEM job whose title is a computing occupation (`stem_classification.is_computing_occupation`). The coding share of a question mix is 0.2 by count (largest remainder); when the role is not eligible the share joins prose and the reason is recorded.

## 2. What is not decided

Tracked as T-02 in [../decisions/OPEN.md](../decisions/OPEN.md). The hidden-test scoring policy is not retained. Until the owner rules, do not build:

- which languages are offered and which checker serves each;
- whether the candidate may trigger the checker while writing (the old Run button) or only at submit;
- how checker findings and the AI review combine into the coding score (the old split of 70 percent hidden tests and 30 percent review cannot stand without tests);
- the exact recruiter wording (it must say the code was reviewed, not run).

## 3. Rules that carry over from the existing design

These hold regardless of the open points, because they are properties of grading, not of the sandbox.

- A coding question and its rubric are written together and both are persisted before the candidate sees either. Questions are written from the bound contract snapshot.
- The review never invents: it quotes the candidate's code verbatim; code addressed to the reviewer (an injection attempt) is recorded as a limitation, not obeyed and not a refusal.
- Coding is graded by Miti like every other skill, from stored evidence. An empty answer is `unanswered`. A checker or model failure is `not_assessed`, never a substitute score. Scoring may wait for an owed coding answer for a bounded time, then reports "Not assessed" with a person in the loop.
- The candidate is told only a state ("Submitted", "Being checked", "Checked"). A report states results in words, never a number.
- The editor is Monaco, self-hosted. Copy, cut, paste and drop are refused in the answer field and each attempt is reported once with its kind. Every suggestion surface is off.
- Nothing the checker or the review stores or logs contains the candidate's source beyond what the report's evidence needs; no source in logs.

## 4. What exists in the repository today

| Component | State | Fate under S-20 |
|---|---|---|
| `services/code_execution/` (port, Judge0 adapter, fake, limits, languages) | Built, disabled (`CODE_EXECUTION_BACKEND=disabled`) | Remove |
| `services/coding_assessment/` run, submit, keys, execution, final answer, scoring | Built, disabled | Remove the execution and key parts; keep evidence, review and phrasing as the basis for the new path |
| `coding_question_keys`, `coding_submissions`, `coding_runs` tables | Exist | Retire through a guarded migration; owner approval first (register R-02) |
| `infra/modules/code_sandbox`, `judge0_*` settings, `scripts/mirror-judge0-images.sh` | Terraform and scripts exist; stage A resources destroyed 2026-09-28 | Remove |
| Run button and hidden-test UI in the frontend | Built | Do not extend; remove with the backend |
| `tests/test_candidate_code_never_executes.py` | Pins the exact set of execution capabilities in the image | Must be updated when a static checker is added (it analyses, it does not execute) |

Nothing in this table was changed by the documentation task that recorded the decision.
