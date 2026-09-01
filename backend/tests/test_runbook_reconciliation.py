"""CODE against RPN-PHIL-001, read from the document itself.

WHAT THIS FILE IS FOR, AND HOW IT DIFFERS FROM THE PARITY TEST
---------------------------------------------------------------
`test_runbook_parity.py` asserts that `runbook_data/` agrees with the Runbook.
This file asserts that the CODE agrees with the Runbook, by parsing
`Readypick Hiring Philosophy.md` at the cited section and comparing what it says
against what the modules do. The two are deliberately separate: a value can be
extracted correctly into YAML and still be applied wrongly by the module that
reads it, and a module can be right while the extraction drifts.

EVERY TEST HERE WOULD HAVE FAILED BEFORE PHASE 0b. That is the bar spec-doc6
§2.3 sets for a `CORRECTED` verdict -- "removing the marker without adding the
test is not reconciliation" -- and it is why these read the document rather than
a constant. A test that compared the code against a second copy of the same
assumption would have passed happily throughout.

The nine sites this covers, and the section each was checked against:

    situations.py:37          §18.4   six situation types' weight consequences
    company_dna.py:48         §16     the twelve-section intake instrument
    swot_quality.py:69        §18.3   the seven high-value probes, §18.5 refusals
    layers.py:47              §3.5    precedence and conflict resolution
    layers.py:177             §11.4   normalisation and clamping
    department_models.py:41   Part VI department models, §11.1 baselines
    evidence_graph.py:42      Part VI observable evidence
    ontology.py:48            §58     the retrieval ontology requirement
    triangulation.py:137      §13.2   the benign-explanation search, §57.4
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.hiring import (
    company_dna,
    department_models,
    layers,
    ontology,
    situations,
    swot_quality,
)
from app.services.miti import triangulation

#: The Runbook moved from the repository root into `docs/product/` on
#: 2026-09-01 with the rest of the documentation.
RUNBOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "product" / "Readypick Hiring Philosophy.md"
)


@pytest.fixture(scope="module")
def runbook() -> str:
    if not RUNBOOK_PATH.exists():
        raise AssertionError(
            f"RPN-PHIL-001 is not at {RUNBOOK_PATH}. This suite compares the "
            f"code against the document and must not pass without it."
        )
    return RUNBOOK_PATH.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """The body of one section, up to the next heading at the same level."""
    lines = text.split("\n")
    start = None
    level = 0
    for index, line in enumerate(lines):
        if line.strip().startswith("#") and heading in line:
            start = index + 1
            level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        raise AssertionError(f"RPN-PHIL-001 has no heading containing {heading!r}")
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("#"):
            if len(stripped) - len(stripped.lstrip("#")) <= level:
                return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


#: The Runbook is markdown, and the emphasis markers are typography rather than
#: content: §18.4 prints "Evidence of operating at the *next* scale" and §21.3
#: prints "Engineering leadership *(EM roles)*". Comparing raw would make every
#: assertion here depend on where an author put an asterisk.
#:
#: The separator character is built from its code point rather than typed, for
#: the standing reason a character class that MATCHES a dash is data and not
#: prose: a repo-wide sweep for that character must not rewrite the code that
#: looks for it.
_EM_DASH = chr(8212)


def plain(text: str) -> str:
    """Markdown emphasis removed, whitespace collapsed."""
    return " ".join(text.replace("*", "").replace("`", "").split())


# ── situations.py, §18.4 ─────────────────────────────────────────────────────

#: §18.4's arrow glyphs, as the module's three ordinal levels.
_ARROWS = {
    "↑↑": situations.STRONG_UP,
    "↑": situations.UP,
    "↓": situations.DOWN,
}


def _parse_weight_consequences(body: str) -> dict[str, dict[str, str]]:
    """§18.4's table, as {situation label: {D-id: arrow level}}."""
    parsed: dict[str, dict[str, str]] = {}
    for line in body.split("\n"):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        label = cells[0].strip("* ")
        consequence = cells[2]
        if not consequence or "Weight consequence" in consequence:
            continue
        effects: dict[str, str] = {}
        for token in consequence.split(","):
            match = re.search(r"(D[1-5])\s*(↑↑|↑|↓)", token)
            if match:
                effects[match.group(1)] = _ARROWS[match.group(2)]
        if effects:
            parsed[label] = effects
    return parsed


def test_every_situation_matches_section_18_4_exactly(runbook: str) -> None:
    """THE test for the highest-risk site.

    Four of the six rows disagreed with §18.4 before reconciliation, two of them
    by inversion: Gap-fill led on Verified Competence where the Runbook leads on
    Role and Context Fit, and Succession left Track Record neutral where the
    Runbook lifts it. Every invented modifier was individually plausible, which
    is exactly why nothing caught them -- a coherently mis-weighted matrix has
    nothing inconsistent in it, which is what §18.4 says about misclassification
    and is equally true of mis-weighting.
    """
    table = _parse_weight_consequences(section(runbook, "18.4"))
    assert len(table) == 6, f"§18.4 should carry six rows, parsed {sorted(table)}"

    by_label = {s.label: s for s in situations.SITUATIONS.values()}
    assert set(table) == set(by_label), (
        f"§18.4 names {sorted(table)}; the code has {sorted(by_label)}"
    )

    for label, expected in table.items():
        coded = by_label[label].effects
        translated = {
            situations.RUNBOOK_ID_BY_DIMENSION[dimension]: arrow
            for dimension, arrow in coded.items()
        }
        assert translated == expected, (
            f"{label}: §18.4 states {expected}, the code has {translated}"
        )


def test_every_situation_carries_section_18_4_s_evidence_emphasis(runbook: str) -> None:
    """§18.4's fourth column, absent from the code entirely before this phase.

    A situation type that re-weighted the matrix without changing what evidence
    was sought would re-rank candidates on evidence nobody went looking for.
    """
    body = section(runbook, "18.4")
    for situationship in situations.SITUATIONS.values():
        emphasis = situationship.evidence_emphasis
        assert emphasis.strip(), situationship.key
        assert plain(emphasis) in plain(body), (
            f"{situationship.key}'s evidence emphasis {emphasis!r} is not "
            f"§18.4's wording"
        )


def test_no_multiplier_is_invented_for_an_arrow_the_runbook_left_ordinal() -> None:
    """No arrow multiplier is a literal in the module; every one is Runbook data.

    HISTORY, because it explains the shape of this test. §18.4 attaches no
    magnitude to its arrows, and §11.3 originally bounded four of the six
    situation types and said nothing about Scale-up or Succession. So this test
    asserted that `dimension_modifiers` RAISED rather than supplying a number: a
    default would have been a magic number wearing a section citation it did not
    have, which is worse than no number because the citation makes it look
    settled.

    The Runbook now states all six bounds and the three arrow multipliers
    (§11.3), so the raise no longer fires and the original assertion has nothing
    left to catch. Rather than skip, the test now guards what actually protects
    the same value: that the multipliers live in the data package under a
    citation, and that the module holds none of its own. A future contributor
    who hardcodes 1.25 into `situations.py` to avoid a data lookup reintroduces
    exactly the defect the original test existed to prevent, and this catches
    that.
    """
    data = _situation_data()
    magnitudes = data.get("arrow_magnitudes")
    assert isinstance(magnitudes, dict) and magnitudes, (
        "situation_types.yaml must declare arrow_magnitudes; the module reads "
        "them rather than restating them, per spec-doc6 section 10.1 rule 5"
    )
    from app.services.hiring import runbook_data

    assert runbook_data.SOURCE_KEY in magnitudes, "the magnitudes need a citation"

    # Every arrow the six situation types actually use has a magnitude, so the
    # loud raise that remains in the module is unreachable for real input.
    declared = {k for k in magnitudes if k != runbook_data.SOURCE_KEY}
    used = {
        arrow
        for situation in data["situation_types"].values()
        for arrow in situation["dimension_effects"].values()
    }
    assert used <= declared, f"arrows with no magnitude: {sorted(used - declared)}"

    # And the module itself carries no multiplier. Read as source rather than
    # by calling, because a literal used as a fallback would not show up in a
    # successful call.
    source = Path(situations.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    for value in sorted(float(magnitudes[k]) for k in declared):
        assert f"= {value}" not in code, (
            f"{value} is assigned in situations.py; arrow magnitudes are "
            "Runbook data and must not be restated in the module"
        )


def _situation_data() -> dict:
    from app.services.hiring import runbook_data

    return runbook_data.situation_types()


def test_an_absent_situation_is_neutral_and_is_not_a_failed_lookup() -> None:
    """The one place degrading is right, and it is a different thing from a
    silent fallback: this is the honest reading of an ABSENT input, not a
    substitute for a FAILED one."""
    modifiers = situations.dimension_modifiers(None)
    assert set(modifiers.values()) == {1.0}


# ── company_dna.py, §16 and Appendix A ───────────────────────────────────────


def test_the_instrument_has_section_16_s_twelve_section_titles(runbook: str) -> None:
    """Five of §16's twelve were missing from the code entirely.

    The two that matter most: §16 Section 12, which the Runbook calls "the
    highest-value input in the entire intake" and without which the calibration
    loop of Part X can never close, and §16 Section 7, which carries the
    client's explicit confirmation of the prohibited-filter list.
    """
    body = section(runbook, "16. The Company DNA Intake Instrument")
    pattern = re.compile(
        r"^#+\s+(?:16\.\d+\s+)?Section\s+(\d+)\s*[" + _EM_DASH + r"\-:]\s*(.+)$",
        re.M,
    )
    headings = pattern.findall(body)
    assert len(headings) == 12, f"§16 should carry twelve sections, found {headings}"

    titles = [title.strip() for _number, title in headings]
    coded = [s.title for s in company_dna.SECTIONS]
    assert len(coded) == 12

    normalised_runbook = [_normalise_title(t) for t in titles]
    normalised_code = [_normalise_title(t) for t in coded]
    assert normalised_code == normalised_runbook, (
        f"§16 orders its sections {normalised_runbook}; the code has "
        f"{normalised_code}"
    )


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z ]", "", title.lower()).strip()


def test_section_two_carries_section_16_s_six_forced_scales(runbook: str) -> None:
    """§16 Section 2 is a six-row table, "each answered on a forced scale, not
    free text". The code had five, of which two were the Runbook's."""
    body = section(runbook, "16. The Company DNA Intake Instrument")
    # The separator characters are built from their code points, never
    # typed: a repo-wide sweep for a dash must not rewrite the code that
    # looks for one.
    _EN_DASH = chr(8211)
    rows = [
        line
        for line in body.split("\n")
        if line.strip().startswith("|")
        and (_EM_DASH in line or f"1{_EN_DASH}5" in line)
    ]
    assert rows, "§16 Section 2's scale table was not found"

    philosophy = next(
        s for s in company_dna.SECTIONS if s.key == "evaluation_philosophy"
    )
    assert len(philosophy.questions) == 6
    assert all(q.kind == company_dna.SCALE_QUESTION for q in philosophy.questions)


def test_the_scale_range_is_the_one_appendix_a2_prints(runbook: str) -> None:
    """Appendix A2 prints every scale 1 to 5. The code ran -2..+2, which is the
    same five positions relabelled and is not what a client is handed -- and it
    made a stored 0 mean "no preference" on one scale and out of range on the
    other, so two intakes could not be told apart in a column of integers."""
    appendix = section(runbook, "A2. Evaluation philosophy")
    assert "1" in appendix and "5" in appendix
    assert (company_dna.SCALE_MIN, company_dna.SCALE_MAX) == (1, 5)


def test_both_section_16_example_pairs_are_used_verbatim(runbook: str) -> None:
    """§16 Section 3 prints TWO accepted/rejected pairs; the code carried one.

    The second is the harder teaching example, because its rejected form is a
    compliment rather than an abstraction and its accepted form names a
    structural condition rather than a nicer adjective.
    """
    body = section(runbook, "16. The Company DNA Intake Instrument")
    rejected = re.findall(r'>\s*Rejected:\s*"([^"]+)"', body)
    assert len(rejected) == 2, f"§16 S3 should print two rejected examples: {rejected}"

    evidence_section = next(
        s for s in company_dna.SECTIONS if s.key == "observable_evidence"
    )
    coded = {e.rejected.strip().rstrip(".").lower() for e in evidence_section.examples}
    assert coded == {r.strip().rstrip(".").lower() for r in rejected}


def test_section_three_asks_for_the_number_of_items_the_runbook_asks_for(
    runbook: str,
) -> None:
    """§16 Section 3: "The client names five to eight behaviours"."""
    body = section(runbook, "16. The Company DNA Intake Instrument")
    assert "five to eight" in body
    evidence_section = next(
        s for s in company_dna.SECTIONS if s.key == "observable_evidence"
    )
    assert (evidence_section.min_items, evidence_section.max_items) == (5, 8)


def test_the_corroboration_floor_is_section_7_4_s_and_not_the_client_s(
    runbook: str,
) -> None:
    """§7.4 indexes minimum independent groups by SENIORITY, as a Layer 1 table.

    A LAYERING INVERSION, not merely a wrong value: the intake asked the client
    how much corroboration they wanted and let "a convincing account is enough"
    set it to one source, which is a Layer 2 answer lowering a Layer 1 floor
    through a question. C2 states it as a commitment: "no candidate is placed in
    a delivered shortlist without corroboration across the minimum number of
    independent sources defined for that department and seniority".
    """
    body = section(runbook, "7.4 Minimum source standards by seniority")
    minimums = [int(m) for m in re.findall(r"\|\s*([234])\s*\|", body)]
    assert minimums, "§7.4's minimum-groups column was not found"
    assert min(minimums) == 2 and max(minimums) == 4

    coded = {
        company_dna.minimum_independent_groups(s)
        for s in ("non_managerial", "managerial", "leadership", "cxo")
    }
    assert min(coded) == 2 and max(coded) == 4
    # No intake answer can move it.
    for answer in (1, 3, 5):
        artifact = company_dna.compile_artifact({"credentials_vs_practice": answer})
        assert artifact.independence_required == 2


# ── swot_quality.py, §18.3 and §18.5 ─────────────────────────────────────────


def test_the_seven_probes_are_section_18_3_s_seven(runbook: str) -> None:
    """Five of the seven differed, and three were absent outright.

    The rejection probe is the loss that mattered: it is the session's only
    instrument for surfacing an UNDECLARED criterion, and an undeclared
    criterion is precisely what becomes an invisible filter later.
    """
    body = section(runbook, "18.3 The seven probes")
    names = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", body, re.M)
    assert len(names) == 7, f"§18.3 should name seven probes, found {names}"

    coded = {p.name.lower().replace("the ", "") for p in swot_quality.HIGH_VALUE_PROBES}
    expected = {n.lower().replace("the ", "") for n in names}
    assert coded == expected, f"§18.3 names {sorted(expected)}; code has {sorted(coded)}"


def test_every_probe_question_is_the_runbook_s_question(runbook: str) -> None:
    """The wording, not merely the name. A probe renamed to §18.3's label while
    asking a different question would pass the test above and change what the
    session collects."""
    body = section(runbook, "18.3 The seven probes")
    for probe in swot_quality.HIGH_VALUE_PROBES:
        question = probe.question
        if "{" in question:
            # The trade-off probe is written "deep X or deep Y" and Appendix B6
            # asks for it to be repeated until the ranking is stable, so the
            # code parameterises it. Compare its fixed frame.
            assert "If you could only have deep" in body
            continue
        needle = question.rstrip("?").strip().lower()
        haystack = body.lower().replace("you'd", "you would").replace(
            "system/team/budget", "system, team or budget"
        )
        assert needle in haystack, f"{probe.key}: {question!r} is not §18.3's wording"


def test_section_18_5_has_six_triggers_and_all_six_are_implemented(
    runbook: str,
) -> None:
    """The code had five. The missing one is the best-performer test, which the
    Runbook singles out: "a devastating and highly effective test -- run it".

    It is the only §18.5 trigger that catches a requirement set which is
    internally coherent and still wrong. The other five catch a malformed
    intake.
    """
    body = section(runbook, "18.5 SWOT quality control")
    triggers = [line for line in body.split("\n") if line.strip().startswith("- ")]
    assert len(triggers) == 6, f"§18.5 should list six triggers, found {triggers}"
    assert any("best performer" in t.lower() for t in triggers)

    rules = {rule for rule, _description in swot_quality.REJECTION_RULES}
    assert "excludes_best_performer" in rules
    report = swot_quality.review(
        {
            "strengths": ["They shipped the reporting rewrite and owned it end to end"],
            "weaknesses": ["The last person could not get product to commit to a scope"],
            "opportunities": [],
            "threats": [],
        },
        situation_key="turnaround",
        best_performer_excluded=True,
    )
    assert "excludes_best_performer" in {r.rule for r in report.rejections}


# ── layers.py, §3.5 and §11.4 ────────────────────────────────────────────────


def test_the_precedence_table_is_section_3_5_row_for_row(runbook: str) -> None:
    """§3.5 is a seven-row table and the code implemented three of them.

    The one that changed behaviour is row 1, "L3 asks for something L2
    prohibits -- L2 wins". `resolve` had Layer 2 and Layer 3 only ever COMPOSE,
    which is §11.4's rule for MODIFIERS and is a different relationship from
    prohibition. There was no way to express a company closing a quantity to a
    role.
    """
    body = section(runbook, "3.5 Precedence and conflict resolution")
    rows = [
        line for line in body.split("\n")
        if line.strip().startswith("|")
        and "---" not in line
        and "Resolution" not in line
    ]
    assert len(rows) == 7, f"§3.5 should carry seven rows, found {len(rows)}"
    assert len(layers.PRECEDENCE_RULES) == 7

    conflicts = [c.strip() for c in (r.strip().strip("|").split("|")[0] for r in rows)]
    coded = [rule.conflict for rule in layers.PRECEDENCE_RULES]
    assert coded == conflicts, f"§3.5 lists {conflicts}; code has {coded}"


def test_a_company_prohibition_beats_a_role_request() -> None:
    """§3.5 row 1: L2 wins, escalate to HR Manager."""
    resolution = layers.resolve(
        "competency_weight",
        company={"delivery": 1.2},
        role={"delivery": 1.8},
        company_prohibits=["delivery"],
    )
    assert resolution.multiplier_for("delivery") == pytest.approx(1.2)
    refusals = [r for r in resolution.refusals if r.layer == layers.LAYER_ROLE]
    assert refusals
    assert refusals[0].rule == "role_asks_what_company_prohibits"
    assert refusals[0].escalate_to == layers.HR_MANAGER


def test_a_clamped_role_request_notifies_the_hiring_manager() -> None:
    """§3.5 row 3 is three obligations, not one: "Clamp to bound; notify hiring
    manager; record the request." The code did the first and the third, and a
    clamp nobody was told about is a preference the hiring manager believes is
    in force and is not."""
    resolution = layers.resolve("competency_weight", role={"delivery": 50.0})
    assert resolution.multiplier_for("delivery") == layers.BOUNDS["competency_weight"].high
    assert resolution.notifications
    assert resolution.notifications[0]["notify"] == layers.HIRING_MANAGER
    assert resolution.notifications[0]["requested"] == 50.0


def test_the_confidence_label_cannot_be_switched_off(runbook: str) -> None:
    """§3.5 row 7, refused under C4, and absent from `INVARIANTS` before this
    phase.

    Its absence was not a missing refusal but a WRONG KIND of refusal: an
    unlisted quantity has no declared bound, and `resolve` raises on one, so
    the request surfaced as an internal error rather than as the reasoned
    refusal with an alternative that §3.5 requires.
    """
    body = section(runbook, "3.5 Precedence and conflict resolution")
    assert "confidence label" in body.lower()

    resolution = layers.resolve("remove_confidence_label", company={"all": 1.0})
    assert resolution.refusals
    refusal = resolution.refusals[0]
    assert refusal.rule == "removal_of_the_confidence_label"
    assert refusal.alternative, "§3.5 refusals carry the alternative on offer"


def test_an_auto_rejection_request_is_refused_with_c5_s_alternative() -> None:
    """§3.5 row 6: "Refused under C5. Offer instead: auto-routing to a priority
    human review queue." A refusal with no alternative reads as an outage
    rather than as a position."""
    resolution = layers.resolve("auto_reject_on_flag", company={"all": 1.0})
    assert resolution.refusals
    assert "priority human review" in (resolution.refusals[0].alternative or "").lower()


def test_the_weight_vector_clamp_is_section_11_4(runbook: str) -> None:
    """§11.4 steps 3, 4 and 5, which the code did not implement at all.

    Applied to a FIXED POINT rather than once through, and that correction is
    forced by the document rather than a departure from it: taken as a strict
    sequence, step 5 undoes step 3, because scaling a clamped vector to sum to
    1.0 lifts every weight and one sitting on the ceiling ends up above it.
    Measured before the fix: D1 clamped to 0.40 came back out at 0.4598.
    """
    body = section(runbook, "11.4 Normalisation and clamping rules")
    assert "0.05" in body and "0.40" in body and "0.12" in body

    vector, notes = layers.clamp_weight_vector(
        {
            "verified_competence": 0.90,
            "track_record_impact": 0.02,
            "role_context_fit": 0.20,
            "authenticity_consistency": 0.01,
            "trajectory_potential": 0.10,
        }
    )
    assert sum(vector.values()) == pytest.approx(1.0)
    for name, value in vector.items():
        assert 0.05 - 1e-9 <= value <= 0.40 + 1e-9, f"{name} breaches §11.4"
    assert vector["authenticity_consistency"] >= 0.12 - 1e-9, "D4 floor is Layer 1"
    assert notes, "§11.4 step 6 requires the derivation to be recorded"


def test_no_dimension_is_ever_weighted_to_zero() -> None:
    """§11.4: "a dimension weighted zero is a dimension nobody is accountable
    for"."""
    vector, _notes = layers.clamp_weight_vector(
        {
            "verified_competence": 1.0,
            "track_record_impact": 0.0,
            "role_context_fit": 0.0,
            "authenticity_consistency": 0.0,
            "trajectory_potential": 0.0,
        }
    )
    assert all(value > 0 for value in vector.values())
    assert vector["authenticity_consistency"] >= 0.12 - 1e-9


# ── department_models.py, Part VI and §11.1 ──────────────────────────────────


def test_part_six_carries_fifteen_departments_and_all_are_reachable(
    runbook: str,
) -> None:
    """The code had five, all invented, so ten of the Runbook's departments had
    no model at all: a civil engineer, a designer, an architect, an HR
    generalist and a tradesperson were every one of them named against a
    generic model, which is the vocabulary collapse the department models exist
    to prevent."""
    headings = re.findall(r"^##\s+(2[1-9]|3[0-5])\.\s+(.+)$", runbook, re.M)
    assert len(headings) == 15, f"Part VI should carry fifteen departments: {headings}"

    coded = department_models.runbook_departments()
    assert len(coded) == 15
    assert {d.number for d in coded.values()} == {int(n) for n, _t in headings}


def test_every_department_menu_is_the_runbook_s_menu(runbook: str) -> None:
    """Competency ids and names come from Part VI's tables, not from this
    codebase. `SW-02` has to be the Runbook's SW-02."""
    for department in department_models.runbook_departments().values():
        assert department.menu, department.key
        for competency in department.menu:
            assert re.match(r"^[A-Z]{2,4}-\d{2}$", competency.id), competency.id
            assert plain(competency.name) in plain(runbook), (
                f"{competency.id} {competency.name!r} is not in RPN-PHIL-001"
            )


def test_the_baseline_weight_matrix_is_section_11_1(runbook: str) -> None:
    """§11.1's vectors, read through the loader rather than restated."""
    body = section(runbook, "11.1 Baseline matrix")
    weights = department_models.baseline_dimension_weights("it_software", "Fresher")
    assert set(weights) == {"D1", "D2", "D3", "D4", "D5"}
    assert weights["D1"] == pytest.approx(0.40)
    row = "| Fresher | 0.40 | 0.05 | 0.15 | 0.20 | 0.20 |"
    assert row in body, "§11.1's IT & Software fresher row moved"


def test_a_seniority_band_from_the_wrong_family_is_refused() -> None:
    """§11.1's bands differ between families and are not interchangeable: IT
    runs "Eng leadership", Leadership runs "Director / VP", trades run
    "Supervisory". Silently accepting one family's band against another's table
    would weight a job against a row nobody chose."""
    with pytest.raises(KeyError):
        department_models.baseline_dimension_weights("it_software", "CXO")


def test_an_unknown_department_raises_rather_than_falling_back() -> None:
    """No generic fallback. Naming a civil engineer's competencies from IT &
    Software's menu would look like a successful lookup at every call site."""
    with pytest.raises(KeyError):
        department_models.runbook_competency_menu("underwater_basket_weaving")


# ── ontology.py, §58 ─────────────────────────────────────────────────────────


def test_all_three_of_section_58_s_named_pairings_resolve(runbook: str) -> None:
    """§58 names three and the code carried two. "FP&A" and "business finance"
    did not resolve, which is the pairing most likely to matter in this
    product's primary market."""
    body = section(runbook, "58. Retrieval design")
    pairs = [
        ("graph database", "semantic technologies"),
        ("GD&T", "geometric tolerancing"),
        ("FP&A", "business finance"),
    ]
    for left, right in pairs:
        assert left.lower() in body.lower(), left
        assert right.lower() in body.lower(), right
        assert ontology.overlap([left], [right]), (
            f"§58 requires {left!r} and {right!r} to resolve to the same node"
        )


def test_the_ontology_is_additive_and_never_substitutive() -> None:
    """§58's fairness argument runs both ways: replacing "GD&T" with "geometric
    tolerancing" would stop matching the candidates who wrote "GD&T"."""
    expanded = ontology.expand(["GD&T"])
    assert expanded[0] == "gd&t"
    assert "geometric tolerancing" in expanded


# ── triangulation.py, §13.2 and §57.4 ────────────────────────────────────────


def test_section_13_2_s_seven_benign_explanations_are_all_available(
    runbook: str,
) -> None:
    """§13.2 STEP 3 names seven and NOT ONE was in the code.

    The pre-Runbook set reached for imprecision, recall and form wording. The
    Runbook's seven are all EMPLOYMENT-RECORD explanations, which is where the
    expensive contradictions live: a resume saying "Acme" where a reference
    says "Acme Systems India" is a company rename, and a benign-explanation
    list that could not produce that reading would work the contradiction
    through the whole protocol and reach the wrong disposition.
    """
    body = section(runbook, "13.2 The resolution protocol")
    for marker in (
        "Company renamed",
        "Team restructured",
        "Title differs from function",
        "Contract-to-permanent conversion",
        "Confidentiality restriction",
        "NDA on the artefact",
        "Regional title conventions",
    ):
        assert marker in body, f"§13.2 STEP 3 no longer names {marker!r}"

    assert len(triangulation.RUNBOOK_BENIGN_EXPLANATIONS) == 7
    available = " ".join(triangulation.RUNBOOK_BENIGN_EXPLANATIONS).lower()
    for concept in (
        "renamed",
        "restructured",
        "title differs",
        "contract",
        "confidentiality",
        "nda",
        "regional title",
    ):
        assert concept in available, concept


def test_step_two_checks_our_own_data_before_the_candidate_s(runbook: str) -> None:
    """§13.2 STEP 2, which the code did not have: "Parsing errors, date-format
    errors, name collisions, and translation artefacts cause a large share of
    apparent contradictions. Rule out our error before attributing to the
    candidate."

    A protocol that only searched for innocent explanations on the candidate's
    side would attribute our own parsing failure to them.
    """
    body = section(runbook, "13.2 The resolution protocol")
    assert "Rule out our error before attributing to the candidate" in body

    explanations = triangulation.standard_explanations("resume_vs_answers")
    texts = [e.text.lower() for e in explanations]
    joined = " ".join(texts)
    for concept in ("parser", "date-format", "name collision", "translation"):
        assert concept in joined, concept
    # And they come first, per the Runbook's own step ordering.
    assert "parser" in texts[0]


def test_every_axis_reaches_the_two_explanation_floor(runbook: str) -> None:
    """§57.4: "generate at least two benign explanations per contradiction
    before assigning severity above Minor". An axis nobody wrote a list for
    must not become an axis where escalation is free."""
    body = section(runbook, "57.4 Triangulation agent")
    assert "at least two benign explanations" in body
    for axis in (
        "resume_vs_validation",
        "resume_vs_answers",
        "answers_across_turns",
        "conclusions_vs_evidence",
        "jd_vs_swot",
        "draft_vs_state",
        "an_axis_nobody_has_written_yet",
    ):
        assert len(triangulation.standard_explanations(axis)) >= 2, axis


def test_rubric_anchors_are_per_dimension_and_not_per_department(
    runbook: str,
) -> None:
    """§9.1 to §9.5, and this corrects a miss in the first reconciliation pass.

    Site 6 marked `baseline_weight` and `primary_dimension` as unsourced and did
    NOT mark `DepartmentModel.anchors`, which is unsourced in the same way and
    for a more specific reason: the Runbook does carry rubric anchors, and it
    carries them somewhere else entirely. §9.1 to §9.5 each hold ONE six-band
    scoring table over 0 to 100, universal, stated once per DIMENSION and never
    restated per department or per seniority.

    §57.3's phrase "retrieved rubric anchors from the department model" is what
    sent the pre-Runbook implementation looking in the wrong place. The anchors
    that exist are the dimension ones; the department model supplies the
    COMPETENCY SET they are applied to.
    """
    for runbook_id in ("D1", "D2", "D3", "D4", "D5"):
        bands = department_models.dimension_rubric_anchors(runbook_id)
        assert len(bands) == 6, f"{runbook_id} should carry six §9.x bands"
        # The bands tile 0..100 downward without a gap or an overlap.
        assert bands[0].high == 100 and bands[-1].low == 0
        for upper, lower in zip(bands, bands[1:]):
            assert lower.high == upper.low - 1, f"{runbook_id}: {upper.band}/{lower.band}"
        for band in bands:
            assert band.meaning in runbook, band.meaning


def test_only_one_department_carries_per_seniority_material(runbook: str) -> None:
    """§21.11 is the only "Seniority notes" table in Part VI.

    Fourteen departments have none, so a per-seniority anchor for all fifteen
    was fifteen inventions rather than one. `seniority_emphasis` returns an
    empty mapping for those fourteen, which is the true answer and is a caller's
    cue to fall back to the universal dimension anchors.
    """
    assert runbook.count("Seniority notes") == 1

    with_notes = [
        key
        for key, department in department_models.runbook_departments().items()
        if department.seniority_notes
    ]
    assert with_notes == ["it_software_engineering"]
    assert department_models.seniority_emphasis("finance_accounting") == {}


def test_contract_c5_points_at_the_legitimate_disqualifier_list(runbook: str) -> None:
    """C5 cited §12.4, the PROHIBITED list, where it means §12.3.

    Read literally, C5 authorised automatic filtering on exactly the attributes
    §12.4 forbids: age, caste, gender, employment gaps. Repaired in the v1.1
    editorial pass. This test exists so a future edit cannot reintroduce it, and
    because this codebase's disqualifier compilation depends on the distinction:
    `company_dna.compile_artifact` admits a §12.3 disqualifier and refuses a
    §12.4 one, and the two lists swapped would invert that.
    """
    # The heading is matched on a substring that survived the v1.1 product
    # naming normalisation ("Ready Pick" became "Ready Pick Now" in prose).
    contract = section(runbook, "Decision Contract")
    c5 = next(line for line in contract.split("\n") if "C5" in line)
    assert "§12.3" in c5, "C5 must cite the LEGITIMATE disqualifier list"
    assert "§12.4" not in c5, (
        "C5 citing §12.4 authorises automatic filtering on the prohibited list"
    )

    # And the code follows the repaired reading in both directions.
    artifact = company_dna.compile_artifact(
        {"hard_disqualifiers": "Must hold a valid CA licence\nNo candidates over 45"}
    )
    assert "Must hold a valid CA licence" in artifact.disqualifiers
    assert "No candidates over 45" in artifact.refused_disqualifiers
