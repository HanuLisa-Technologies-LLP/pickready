"""Coding questions that are executed: their shape, their key, Run, Submit, grade.

`payload`      the v2 coding payload stored in `candidate_questions.payload_json`.
               Candidate-safe BY CONSTRUCTION: its model forbids extra keys, so a
               hidden test or a reference solution cannot be stored in it.
`keys`         the ONLY module that reads or writes `coding_question_keys`, the
               hidden tests and the reference solution.
`runs`         the Run button: the candidate's code against the VISIBLE tests.
`submissions`  the final answer: accepted in the candidate's transaction,
               executed against the hidden tests by a task dispatched after
               commit, reviewed, filed in the evidence ledger, handed to scoring.
`execution`    sandbox results turned into outcome words; hidden output dropped.
`review`       the code-quality review (the thirty parts of the score).
`scoring`      the 70/30 score. Pure; None unless both halves exist.
`evidence`     `CodingEvidence`, what the grader reads per coding answer.
`phrasing`     the result in spelled-out words; no digit leaves it.
`sweeps`       the reconcile sweep, the sandbox probe, the operator verification.

Generation lives beside the other question writers, in
`services/assessment_formats/coding_generation.py`. Execution goes through
`services/code_execution`, the port; nothing in this package knows which
sandbox runs a program, and nothing in it can start a process.
"""
