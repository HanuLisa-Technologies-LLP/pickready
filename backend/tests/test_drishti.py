"""Drishti: the compiled artifact, the bounded emphasis, the absent no-op.

Vivekium feature 1 under C3. The assertion that matters most is the
ENHANCEMENT-LAYER contract: with no profile, derive_weight is bit-identical
to before this layer had a live supplier.
"""
from __future__ import annotations

from app.services.hiring import drishti, transformation


def test_the_sections_are_the_briefs_five():
    assert drishti.SECTION_KEYS == (
        "strategic_purpose",
        "people_philosophy",
        "non_negotiables",
        "culture_expectations",
        "strategic_gap",
    )


def test_compile_is_deterministic_and_observable_gated():
    sections = {
        "strategic_purpose": (
            "Has taken a project from an unclear brief to a shipped outcome. "
            "We believe in excellence."
        ),
        "non_negotiables": "Must be strong in Kafka.",
    }
    a = drishti.compile_profile(function_name="Engineering", sections=sections)
    b = drishti.compile_profile(function_name="Engineering", sections=sections)
    assert a == b, "compilation must be reproducible"
    # The observable sentence made it; the values-page sentence did not.
    joined = " ".join(a["context_lines"])
    assert "unclear brief" in joined
    assert "excellence" not in joined
    assert a["version"] == drishti.COMPILED_VERSION


def test_emphasis_resolves_at_freeze_on_word_boundaries():
    compiled = drishti.compile_profile(
        function_name="Engineering",
        sections={"non_negotiables": "Java and delivery ownership are required."},
    )
    out = drishti.emphasis_map(compiled, ["Java", "JavaScript", "Delivery Ownership"])
    assert out == {
        "Java": drishti.EMPHASIS_MULTIPLIER,
        "Delivery Ownership": drishti.EMPHASIS_MULTIPLIER,
    }
    assert drishti.emphasis_map(None, ["Java"]) == {}
    assert drishti.emphasis_map(compiled, []) == {}


def test_the_critique_holds_the_observable_bar():
    probes = drishti.critique(
        "We value hunger and ownership mindset. "
        "Has taken a project from an unclear brief to a shipped outcome."
    )
    assert len(probes) == 1, "one probe per non-observable claim, none for real ones"
    assert drishti.critique("") == []


def test_absent_profile_changes_no_weight():
    """The enhancement-layer contract, at the arithmetic."""
    without = transformation.derive_weight(
        anchor=None, dimension="track_record", situation_key=None,
        role_emphasis=None, company_emphasis=None, subject="Kafka",
    )
    empty = transformation.derive_weight(
        anchor=None, dimension="track_record", situation_key=None,
        role_emphasis=None, company_emphasis={}, subject="Kafka",
    )
    assert without.value == empty.value
    assert without.company == 1.0 == empty.company


def test_emphasis_moves_the_weight_through_the_bounds_and_is_named():
    """The acceptance criterion the Company DNA layer always carried: a
    Layer 2 change must demonstrably MOVE a weight, within bounds, with
    provenance naming the layer."""
    plain = transformation.derive_weight(
        anchor=None, dimension="track_record", situation_key=None,
        role_emphasis=None, company_emphasis=None, subject="Kafka",
    )
    leaned = transformation.derive_weight(
        anchor=None, dimension="track_record", situation_key=None,
        role_emphasis=None,
        company_emphasis={"Kafka": drishti.EMPHASIS_MULTIPLIER},
        subject="Kafka",
    )
    assert leaned.value > plain.value
    assert leaned.company == drishti.EMPHASIS_MULTIPLIER
    assert any(
        adj.get("layer") == "company" for adj in leaned.provenance
    ), f"the company layer must be named in provenance: {leaned.provenance}"
    # The terms serialize, so a stored weight explains itself.
    assert leaned.as_dict()["terms"]["company_layer2"] == drishti.EMPHASIS_MULTIPLIER


def test_a_suspension_shaped_emphasis_is_clamped_not_obeyed():
    """TUNE, never SUSPEND: an absurd multiplier comes out clamped to the
    bounds table, with the clamp recorded."""
    wild = transformation.derive_weight(
        anchor=None, dimension="track_record", situation_key=None,
        role_emphasis=None, company_emphasis={"Kafka": 40.0}, subject="Kafka",
    )
    assert wild.company < 40.0, "layers.resolve must clamp the company term"
