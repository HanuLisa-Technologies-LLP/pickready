"""Turning a resume into claims, and refusing when the Runbook data is absent.

TWO SEPARATE PROPERTIES, both of which only show on the awkward input.

CLAIMS ARE DEDUPLICATED AND ANONYMISED BEFORE THEY ARE SPLIT. A resume that
lists the same skill in a summary and again in a role would otherwise file it
twice, and two entries for one statement read downstream as corroboration --
the same defect the evidence ledger's independence rule exists to prevent, one
layer earlier. The name is removed before the split, so no claim carries it.

THE RUNBOOK DATA IS REFUSED LOUDLY WHEN IT CANNOT LOAD. Nothing here has a
default: a missing tier strength or decay table would otherwise silently score
every candidate on a value nobody wrote down, and the grade would look
ordinary.

Pure functions over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.hiring import prescreen


# ── Claim extraction ─────────────────────────────────────────────────────────


def test_a_skill_and_a_role_line_both_become_claims() -> None:
    claims = prescreen.claims_from_resume(
        "Led the payments rewrite across three teams over eighteen months.",
        skills=["Kafka"],
        role_lines=[
            ("Led the payments rewrite across three teams over eighteen months.", 2.0)
        ],
    )
    assert claims
    joined = " ".join(claim.text.casefold() for claim in claims)
    assert "kafka" in joined or any("kafka" in " ".join(c.terms) for c in claims)


def test_the_same_skill_listed_twice_is_one_claim() -> None:
    """Two entries for one statement read downstream as corroboration."""
    once = prescreen.claims_from_resume("", skills=["Kafka"])
    twice = prescreen.claims_from_resume("", skills=["Kafka", "kafka", " Kafka "])
    assert len(twice) == len(once)


def test_the_same_role_line_repeated_is_one_claim() -> None:
    line = "Rebuilt the ingestion pipeline and cut nightly runtime substantially."
    claims = prescreen.claims_from_resume(
        "", role_lines=[(line, 1.0), (line, 1.0), (line.upper(), 2.0)]
    )
    assert len(claims) == 1


def test_a_role_line_too_short_to_be_a_claim_is_dropped() -> None:
    """A fragment carries no checkable content, and filing it would pad the
    ledger with entries that can never be corroborated or contradicted."""
    claims = prescreen.claims_from_resume("", role_lines=[("Did stuff", 1.0)])
    assert claims == ()


def test_blank_skills_and_lines_are_ignored() -> None:
    claims = prescreen.claims_from_resume(
        "", skills=["", "   ", None], role_lines=[("", 1.0), ("   ", None)]
    )
    assert claims == ()


def test_an_empty_resume_yields_no_claims_rather_than_raising() -> None:
    """A scanned image resume extracts to nothing. That is a graded outcome
    (Hold) rather than a crash in the grader."""
    assert prescreen.claims_from_resume(None) == ()
    assert prescreen.claims_from_resume("") == ()


def test_the_candidate_name_never_reaches_a_claim() -> None:
    """Anonymisation happens before the split, so no claim can carry it."""
    claims = prescreen.claims_from_resume(
        "Ada Lovelace rebuilt the ingestion pipeline and cut nightly runtime.",
        role_lines=[
            ("Ada Lovelace rebuilt the ingestion pipeline and cut nightly runtime.", 1.0)
        ],
        identities=["Ada Lovelace"],
    )
    for claim in claims:
        assert "ada" not in claim.text.casefold()
        assert "lovelace" not in claim.text.casefold()
        assert not any("lovelace" in term for term in claim.terms)


def test_every_claim_carries_a_tier_and_comparable_terms() -> None:
    claims = prescreen.claims_from_resume(
        "", role_lines=[("Rebuilt the ingestion pipeline end to end.", 1.0)]
    )
    assert claims
    for claim in claims:
        assert claim.tier
        assert isinstance(claim.terms, frozenset)


# ── The Runbook data refusals ────────────────────────────────────────────────


def test_an_unloadable_tier_table_is_refused_and_says_what_is_lost(monkeypatch) -> None:
    """Re-raised, never swallowed: without it there is no tier strength, no
    quality modifier and no decay multiplier, and a default for any of them
    would score every candidate on a number nobody wrote down."""
    class _Broken:
        @staticmethod
        def evidence_tiers():
            raise FileNotFoundError("evidence_tiers.yaml")

    monkeypatch.setattr(prescreen, "runbook_data", _Broken)
    with pytest.raises(prescreen.PreScreenUnavailable) as excinfo:
        prescreen.tier_strength("E3")
    assert "evidence_tiers.yaml" in str(excinfo.value)


def test_an_unloadable_band_table_is_refused(monkeypatch) -> None:
    class _Broken:
        @staticmethod
        def bands():
            raise FileNotFoundError("bands.yaml")

    monkeypatch.setattr(prescreen, "runbook_data", _Broken)
    with pytest.raises(prescreen.PreScreenUnavailable) as excinfo:
        prescreen._band_data()
    assert "bands.yaml" in str(excinfo.value)


def test_an_unknown_quality_modifier_is_refused_by_name(monkeypatch) -> None:
    monkeypatch.setattr(
        prescreen, "_tiers", lambda: {"quality_modifiers": {"specificity": {"low": 0.9}}}
    )
    with pytest.raises(prescreen.PreScreenUnavailable) as excinfo:
        prescreen._modifier("not_a_modifier", "low")
    assert "not_a_modifier" in str(excinfo.value)


def test_a_decay_table_with_no_usable_row_falls_back_to_neutral(monkeypatch) -> None:
    """Neutral, not harsh. A table this module cannot read must not silently
    become an age penalty, which is the exact failure section 6.3 warns of."""
    monkeypatch.setattr(
        prescreen,
        "_tiers",
        lambda: {"decay": {"multipliers": [{"age_of_underlying_event": "recent"}]}},
    )
    assert prescreen.decay_multiplier(10.0, prescreen.CLOCK_FAST) == 1.0
