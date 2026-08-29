"""Miti's scoring pipeline (spec-doc5 §A.3, Runbook §56 stages 2-6).

    claims          NORMALISE & EXTRACT   claim extraction, materiality
    tiering         EVIDENCE TIERING      tier, provenance, independence, decay
    dimensions      DIMENSION EVALUATORS  five, isolated, rubric-anchored
    triangulation   TRIANGULATION         contradictions, benign explanations
    aggregation     AGGREGATION           deterministic arithmetic, no model
    pipeline        the orchestration and the gates

THE FIVE INTERNAL DIMENSIONS RUN UNDERNEATH THE THREE PRODUCT-FACING ONES.
Tatva Assessment still shows Must-have / Nice-to-have / Behavioural, and the
Must-have hard cap still applies exactly as before. Verified Competence, Track
Record & Impact, Role & Context Fit, Authenticity & Consistency and Trajectory &
Potential are how a grade is ARRIVED AT; they are never rendered, never named in
a report, and never returned by an API.
"""
