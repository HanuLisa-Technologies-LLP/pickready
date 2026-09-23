"""The post-conversation assessment pipeline, one stage per module.

    types       frozen value types shared by the stages (no ORM, no session)
    evidence    stage 1: where each answer lives, and THE per-answer ledger
                writer every caller goes through

The direction is one way: a stage may import `types` and the stages before
it, never a later one, and nothing here imports `functional_assessment`.
Miti (grading), Siddhi (the report) and persistence join this package in the
Vivekium release's grading phase; until then `functional_assessment` is the
orchestrator and calls `evidence` for the ledger.
"""
