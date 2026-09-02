"""Proctoring: the candidate's browser detects, the server decides, the report
describes (proctoring-spec-doc.md).

Package map, in pipeline order:

    config.py      every threshold, read once from Settings; the client subset
    catalog.py     the event vocabulary with its consequence path (A, B, C)
    gate.py        the two questions the assessment API asks: may this
                   conversation proceed, and has it been terminated
    state.py       the shared Redis state: warning counter, cooldowns,
                   consecutive-evidence counters, heartbeats
    ingestion.py   batch ingestion, server-side classification, warning and
                   termination decisions
    behaviour.py   keystroke and mouse aggregates against the candidate's own
                   baseline, evaluated at submission
    audio.py       the in-memory hand-off of an audio chunk to the analysis
                   service and the second-voice rule
    ai_text.py     the flagged, informational AI-text signal
    phrasing.py    the recruiter-facing sentence library and the candidate
                   warning messages
    report.py      the report generator and the PRISM Report join

Nothing in this package is imported by any scorer, by the Tatva matrix, by
Miti, by the dashboard or by any ranking query (principle P3), and
`tests/test_proctoring_scoring_isolation.py` asserts the import graph says so.
"""
