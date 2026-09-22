"""Drishti: the compiled artifact, the bounded emphasis, the absent no-op.

Vivekium feature 1 under C3. The assertion that matters most is the
ENHANCEMENT-LAYER contract: with no profile, derive_weight is bit-identical
to before this layer had a live supplier.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from app.models.enums import Role
from app.services import capabilities as caps
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
    """The acceptance criterion the old Layer 2 always carried: a
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


# ── The functional-head binding (vivekium feature 1, migration 0115) ─────────
#
# The brief: once per function PER FUNCTIONAL HEAD, updated by that head at
# any time, and "a functional-head change is the CLIENT's trigger, never
# auto-detected". The failure mode has no symptom -- the profile saves, the
# head silently becomes whoever opened the form, and the function's strategic
# direction is attributed to somebody who never set it -- so the rule is a
# pure function and this is where it is pinned.


def test_a_new_function_is_claimed_by_whoever_writes_it_first() -> None:
    caller = uuid.uuid4()
    assert (
        drishti.resolve_head_binding(
            current_head_id=None, caller_id=caller, exists=False,
            change_confirmed=False,
        )
        == drishti.BIND
    )


def test_the_head_of_record_writes_without_rebinding() -> None:
    """An update by the head is the ordinary case and must not restamp the
    binding: a leadership change that never happened would otherwise appear
    in the row and in the audit trail every time they edited a sentence."""
    caller = uuid.uuid4()
    assert (
        drishti.resolve_head_binding(
            current_head_id=caller, caller_id=caller, exists=True,
            change_confirmed=False,
        )
        == drishti.KEEP
    )


def test_somebody_elses_function_is_refused_until_the_change_is_confirmed() -> None:
    """THE RULE. Without confirmation this is a refusal, not a silent
    takeover, and with it the platform is recording a decision the client
    made rather than detecting one it inferred."""
    incumbent, caller = uuid.uuid4(), uuid.uuid4()
    assert (
        drishti.resolve_head_binding(
            current_head_id=incumbent, caller_id=caller, exists=True,
            change_confirmed=False,
        )
        == drishti.REFUSE
    )
    assert (
        drishti.resolve_head_binding(
            current_head_id=incumbent, caller_id=caller, exists=True,
            change_confirmed=True,
        )
        == drishti.BIND
    )


def test_an_unheld_profile_is_claimed_without_asking() -> None:
    """A row from before the binding existed, whose author has since been
    deleted (the FK is ON DELETE SET NULL). There is nobody to take it from,
    and a confirmation dialog naming nobody is a dead end."""
    assert (
        drishti.resolve_head_binding(
            current_head_id=None, caller_id=uuid.uuid4(), exists=True,
            change_confirmed=False,
        )
        == drishti.BIND
    )


def test_the_brief_excludes_the_hiring_manager_as_DATA() -> None:
    """"NOT the Hiring Manager", verbatim from the brief, as a permission row
    rather than as a role branch (rule 2).

    Asserted against the code matrix here and against the MIGRATED DATABASE
    by tests/test_capability_seed_parity.py; a capability constant is only
    half a change. `edit_company_profile` could not have expressed this at
    all: every client-side staff role holds it, the Hiring Manager included.
    """
    matrix = caps.DEFAULT_PERMISSION_MATRIX
    assert matrix[Role.hiring_manager][caps.AUTHOR_DRISHTI_PROFILE] is False
    assert matrix[Role.recruiter][caps.AUTHOR_DRISHTI_PROFILE] is False
    assert matrix[Role.interview_manager][caps.AUTHOR_DRISHTI_PROFILE] is False
    for role in (Role.client, Role.hr_manager, Role.recruitment_manager):
        assert matrix[role][caps.AUTHOR_DRISHTI_PROFILE] is True
    # And it reaches /auth/me, which is what lets the nav entry be gated on
    # it: a capability missing from this list is a page a holder is told does
    # not exist.
    assert caps.AUTHOR_DRISHTI_PROFILE in caps.ALL_CAPABILITIES
    # Every holder of the old capability is NOT automatically a holder of this
    # one, which is the whole reason it is a separate name.
    assert matrix[Role.hiring_manager][caps.EDIT_COMPANY_PROFILE] is True


def test_the_seeding_migration_restates_the_code_matrix_row_for_row() -> None:
    """A capability constant is only HALF a change (rule 2).

    `test_capability_seed_parity` makes the same comparison against a
    MIGRATED DATABASE and is the authority, but it skips when no database is
    reachable -- which is every developer machine without docker up, and was
    every run during the phase the spec-doc6 batch shipped without its
    migration. This one reads the migration file and needs nothing, so the
    half-done change fails somewhere.

    The migration's rows are string literals on purpose (the convention every
    seed migration follows: a historical migration's effect must not shift if
    a constant is renamed), which is exactly why they need comparing.
    """
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0115_drishti_functional_head.py"
    )
    spec = importlib.util.spec_from_file_location("_drishti_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    seeded = {
        (role, capability): allowed for role, capability, allowed in module.SEED_ROWS
    }
    expected = {
        (role.value, caps.AUTHOR_DRISHTI_PROFILE): grants[
            caps.AUTHOR_DRISHTI_PROFILE
        ]
        for role, grants in caps.DEFAULT_PERMISSION_MATRIX.items()
        if caps.AUTHOR_DRISHTI_PROFILE in grants
    }
    assert seeded == expected
