"""The three-layer hiring intelligence framework (spec-doc5 Part A).

    layers               the precedence model: tune within bounds, never suspend
    department_models    LAYER 1, the platform's own competency baselines
    company_dna          LAYER 2, the client's philosophy, compiled once
    situations           the six role situation types and their weight effects
    swot_quality         LAYER 3's quality control: the probes and the refusals
    transformation       Sutra's seven stages, SWOT phrase -> matrix item
    ontology             vocabulary equivalence, so a synonym is not a gap
    evidence_graph       what evidences what, per department
    gates                G1-G4

DELIBERATELY IMPORT-LIGHT. Nothing here imports `app.models`, a session, or the
router, so the whole framework can be reasoned about and tested without standing
up a database or a provider. `services/ppi.py` and `services/miti/` are the
layers that bind it to rows and to models -- the same separation
`config/llm_providers.py` keeps from `services/llm_router.py`, and for the same
reason: policy that can only be exercised through I/O is policy nobody reviews.
"""
