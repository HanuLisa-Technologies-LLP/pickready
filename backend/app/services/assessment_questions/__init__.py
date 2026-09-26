"""The per-candidate assessment questions: how many (`budget`) and which (`generate`).

Carved out of `services/ppi.py` on 2026-09-24 (PLAN-p3 WP0). Deliberately
empty of imports: `services/ppi` imports `budget`, and `generate` imports
`services/ppi`, so a package that eagerly imported both would close a cycle.
"""
