"""Assessment media: recording, storage, processing and deletion.

Provenance: dual-mode spec 4-7, and the 2026-09-22 owner ruling that
assessment media is compressed, stored in S3 and served back to an authorized
hiring-team member. This package is THE media pipeline for both recording
kinds; there is not a second one anywhere in this product.

The package boundary matters as much as the contents:

  - NOTHING here is imported by a scorer, by Miti, by Siddhi, by ranking or
    by the dashboard. The interview half of the pipeline WRITES the same
    transcript and answer records the conversational mode writes, and the
    existing scorers read those records without knowing which mode produced
    them (spec 21). `tests/test_dual_mode_assessment.py` asserts the import
    graph.
  - NOTHING here imports `services/proctoring/`, and nothing under
    `services/proctoring/` imports this package. A proctored-session
    recording is attached to the same assessment session the proctoring
    record is attached to, and that is the whole of the relationship: the
    media pipeline reads no warning, no event and no session outcome, and
    proctoring decides nothing about the bytes. That is what keeps a stored
    recording from becoming an input to a grade, and it is pinned from both
    sides (`tests/test_dual_mode_assessment.py`,
    `tests/test_proctoring_scoring_isolation.py`).

SUPERSEDED HERE, 2026-09-22: this docstring used to read "Proctoring stores no
media; an assessment video is a separately consented artifact of the
video-interview mode". The first clause is reversed by the owner ruling. The
second still holds for the interview recording and now holds for the proctored
recording too: both are consented, both are retained under the candidate's own
retention consent, and neither is retrievable without `view_review_screen`.
"""
