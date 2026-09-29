"""The Leadership Intelligence compiler: deterministic, bounded, and safe (spec 19).

Pure: no database, no model. What it pins:

* an observable, job-related sentence is kept, with provenance naming the
  role, the profile, the version and the department;
* a protected attribute, culture fit, a trait with no event and an over-long
  sentence are each refused with their reason word and never reach a list;
* a field and a profile are capped, and what the cap drops is REPORTED
  (`over_limit`), never silently cut;
* the same words compile to the same artifact;
* `kept_lines` re-judges a stored artifact against TODAY's bar, including a
  line carried across from Drishti by migration 0135.
"""
from __future__ import annotations

import uuid

from app.services.leadership import compiler
from tests import leadership_fixtures as lf

PROFILE = uuid.UUID(int=7)
DEPARTMENT = uuid.UUID(int=9)


def _compile(**fields) -> dict:
    return compiler.compile_profile(
        role="functional_head",
        profile_id=PROFILE,
        version=3,
        department_id=DEPARTMENT,
        **fields,
    )


def _kept(artifact: dict) -> list[str]:
    return [entry["text"] for entry in artifact["provenance"]]


def test_an_observable_line_is_kept_with_its_provenance() -> None:
    artifact = _compile(department_requirements=lf.FH_LINE)
    assert _kept(artifact) == [lf.FH_LINE]
    (entry,) = artifact["provenance"]
    assert entry == {
        "text": lf.FH_LINE,
        "list": entry["list"],
        "field": "department_requirements",
        "role": "functional_head",
        "profile_id": str(PROFILE),
        "version": 3,
        "department_id": str(DEPARTMENT),
    }
    assert entry["list"] in compiler.LISTS
    assert artifact["source"] == {
        "role": "functional_head", "profile_id": str(PROFILE), "department_id": str(DEPARTMENT)
    }


def test_unusable_lines_are_refused_with_their_reason() -> None:
    culture = "Every hire must show strong culture fit with the way we already work here."
    trait = "We want hungry, passionate and driven people on this team always."
    too_long = "We need people who have shipped " + "and maintained services " * 20 + "."
    artifact = _compile(
        department_requirements=" ".join([lf.UNSAFE_LINE, culture, trait, lf.VAGUE_LINE, too_long])
    )
    reasons = [item["reason"] for item in artifact["excluded_or_unsafe_claims"]]
    assert reasons == [
        compiler.REASON_PROTECTED,
        compiler.REASON_CULTURE_FIT,
        compiler.REASON_NOT_OBSERVABLE,
        compiler.REASON_NOT_OBSERVABLE,
        compiler.REASON_TOO_LONG,
    ]
    assert _kept(artifact) == []
    assert all(not artifact[name] for name in compiler.LISTS)


def test_a_protected_criterion_is_refused_however_observable_it_is() -> None:
    line = "We hired women who shipped and led three migrations in production last year."
    artifact = _compile(department_requirements=line)
    assert artifact["excluded_or_unsafe_claims"][0]["reason"] == compiler.REASON_PROTECTED


def test_the_caps_report_what_they_drop() -> None:
    many = " ".join(
        f"Engineers here have shipped service number {word} to production and fixed it."
        for word in ("one two three four five six seven eight nine ten".split())
    )
    artifact = _compile(department_requirements=many)
    assert len(_kept(artifact)) == compiler.MAX_FIELD_LINES
    over = [item for item in artifact["excluded_or_unsafe_claims"] if item["reason"] == "over_limit"]
    assert len(over) == 10 - compiler.MAX_FIELD_LINES


def test_compilation_is_deterministic() -> None:
    fields = {"department_requirements": lf.FH_LINE, "ideal_employee_expectations": lf.IDEAL_LINE}
    assert _compile(**fields) == _compile(**fields)


def test_a_line_lands_in_one_list_by_its_wording_or_its_field() -> None:
    artifact = _compile(
        department_requirements=(
            "Every engineer must write the tests for the code they shipped last quarter. "
            "Nobody on the team has run a payment settlement migration end to end yet."
        ),
        ideal_employee_expectations=lf.FH_LINE,
    )
    assert artifact[compiler.NON_NEGOTIABLES] and artifact[compiler.STRATEGIC_GAPS]
    assert lf.FH_LINE in artifact[compiler.OBSERVABLE_COMPETENCIES]
    placed = [text for name in compiler.LISTS for text in artifact[name]]
    assert len(placed) == len(set(placed)) == 3


def test_kept_lines_rejudge_a_stored_artifact_against_todays_bar() -> None:
    """A stored artifact outlives the compiler: an unsafe line written into it
    by any path never reaches the pipeline, and a Drishti line is judged on the
    words after its section label."""
    stored = {
        "provenance": [
            {"list": "department_priorities", "text": lf.FH_LINE},
            {"list": "department_priorities", "text": lf.UNSAFE_LINE},
            {"list": "not_a_list", "text": lf.CEO_LINE},
        ]
    }
    assert compiler.kept_lines(stored) == [("department_priorities", lf.FH_LINE)]
    drishti = {
        "schema_version": 1,
        "migrated_from": "drishti_profiles",
        "department_priorities": [f"Strategic purpose of the function: {lf.FH_LINE}", "Culture: we value hunger."],
        "provenance": [],
    }
    assert compiler.kept_lines(drishti) == [
        ("department_priorities", f"Strategic purpose of the function: {lf.FH_LINE}")
    ]
    assert compiler.kept_lines(None) == []
