"""Domain critics that decide whether a generated output is fit to ship.

HOW THIS RELATES TO THE LOOP
----------------------------
`agent_loop` owns the mechanism: bounded attempts, a deterministic critique fed
back verbatim, an honest degradation record. This package owns the JUDGEMENT
for one output type each, and hands it over as `Verdict.to_critique()`. Nothing
here retries anything, and nothing here calls a model.

That split is why adding a critic is cheap. A generator wires one in as its
`verify=` callback and inherits the retry behaviour, the tracing and the
degradation accounting it already had.

WHAT EACH CRITIC IS FOR
-----------------------
  ppi_report    graded items, 45-50 word remarks, the Must-have cap, and the
                rule that no number reaches a client
  email         the transition it claims, its link, its placeholders, and the
                fact that it cannot be recalled once sent
  probes        gap probes: written for a real gap, grounded in a real answer,
                and not a question the assessment already asked
  contradiction resume against form against transcript. Surfaced for a
                recruiter, never resolved by an agent

THE SPEC THIS IMPLEMENTS WAS WRITTEN AGAINST AN OLDER PRODUCT
--------------------------------------------------------------
Two of its checks are deliberately absent: the ranking weight-sum check (there
are no weights) and the five-label scale (collapsed into the four grades of
`services.rating` on 2026-07-30). The `ranking` critic that stood in for the
first, judging the retired AI Score's four parameters and their 25-30 word
comments, is DELETED with that AI Score (Vivekium release, Phase 2 WP-F): Yukti
writes no comment to judge, and its evidence is grounded deterministically in
`services/yukti/grounding.py` before anything is stored.
"""
from __future__ import annotations

from app.services.verification import (
    contradiction,
    email,
    generic_language,
    ppi_report,
    probes,
)
from app.services.verification.base import (
    CONFIDENCE_FLOOR,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    Finding,
    Verdict,
    combine,
    high,
    low,
    medium,
    verdict,
    words_in,
)

__all__ = [
    "CONFIDENCE_FLOOR",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "Finding",
    "Verdict",
    "combine",
    "contradiction",
    "email",
    "generic_language",
    "high",
    "low",
    "medium",
    "ppi_report",
    "probes",
    "verdict",
    "words_in",
]
