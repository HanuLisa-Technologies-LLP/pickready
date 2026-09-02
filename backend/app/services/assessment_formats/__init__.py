"""Question formats for the assessment conversation (assessment-spec-doc.md).

Evidence-based questions are the primary instrument; MCQ, fill-in-the-blank
and coding are supporting formats that establish baseline competence around
the core and can never carry the assessment. That ratio is enforced in code by
`composition.py`, not suggested in a prompt.

Package map:

    types.py        the six formats, the payload and answer shapes, the
                    candidate-safe projection that strips every answer key
    config.py       time allocations, weights, shares and durations, from
                    Settings
    composition.py  the format mix per role, its validation and the
                    regenerate-then-fall-back loop
    generation.py   writing a structured question's payload with a model
    scoring.py      deterministic scoring for the objective types and the
                    fuzzy fallback for fill-in-the-blank
    evaluation.py   AI evaluation with reasoning for the subjective types
    rendering.py    the transcript line for a structured answer
"""
