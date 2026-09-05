"""Video-interview recording, storage and processing (dual-mode spec 4-7).

The package boundary matters as much as the contents:

  - NOTHING here is imported by a scorer, by Miti, by Siddhi, by ranking or
    by the dashboard. The video pipeline WRITES the same transcript and
    answer records the conversational mode writes, and the existing scorers
    read those records without knowing which mode produced them (spec 21).
    `tests/test_dual_mode_assessment.py` asserts the import graph.
  - NOTHING here touches `services/proctoring/`. Proctoring stores no media;
    an assessment video is a separately consented artifact of the
    video-interview mode, and the two must never share a write path.
"""
