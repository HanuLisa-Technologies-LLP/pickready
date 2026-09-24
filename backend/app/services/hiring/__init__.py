"""The three-layer hiring intelligence framework (spec-doc5 Part A).

    layers               the precedence model: tune within bounds, never suspend
    department_models    LAYER 1, the platform's own competency baselines
    situations           the six role situation types and their weight effects
    sutra                the Skills draft and the hidden assessment context
    ontology             vocabulary equivalence, so a synonym is not a gap
    evidence_graph       what evidences what, per department
    gates                G1-G4

The SWOT probes module and Sutra's seven-stage transformation were DELETED in the Vivekium release with the Tatva matrix they served.
`layers`, `department_models` and `situations` remain because Miti still reads
them; nothing on the job-setup path does.

DELIBERATELY IMPORT-LIGHT. Nothing here imports `app.models`, a session, or the
router, so the whole framework can be reasoned about and tested without standing
up a database or a provider. `services/ppi.py` and `services/miti/` are the
layers that bind it to rows and to models -- the same separation
`config/llm_providers.py` keeps from `services/llm_router.py`, and for the same
reason: policy that can only be exercised through I/O is policy nobody reviews.
"""
