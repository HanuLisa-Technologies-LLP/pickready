"""Coding questions that are executed: their public shape and their answer key.

`payload`  the v2 coding payload stored in `candidate_questions.payload_json`.
           Candidate-safe BY CONSTRUCTION: its model forbids extra keys, so a
           hidden test or a reference solution cannot be stored in it.
`keys`     the ONLY module that reads or writes `coding_question_keys`, the
           hidden tests and the reference solution.

Generation lives beside the other question writers, in
`services/assessment_formats/coding_generation.py`. Execution goes through
`services/code_execution`, the port; nothing in this package knows which
sandbox runs a program, and nothing in it can start a process.
"""
