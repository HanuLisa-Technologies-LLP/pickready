"""The occupational layer: classify the FUNCTION, not the keyword.

The spec these cover (2026-09-13, sections 16 to 22) names the defect directly:
"A serious defect exists if roles such as Software Engineer, Software
Developer, Data Scientist, Electronics Engineer are displayed as Non-STEM."
Every one of those did, with a thin job description, because the engine read
only the JD BODY and a title alone could reach at most 0.30 of the 0.50 it
needed.

These tests pin the layer that fixes it, and pin the two places it is
deliberately NOT decisive: section 19's "not every role containing Analyst is
automatically STEM", and its instruction that an HR/Finance/Sales Manager is
Non-STEM whatever technology sits around them.

A TITLE WITH NO JD AT ALL IS THE POINT. Every case here passes an EMPTY body
on purpose. A test that supplied a technical paragraph would pass against the
old engine too and would therefore pin nothing.
"""
import pytest

from app.services.stem_classification import (
    NON_STEM,
    STEM,
    TITLE_NON_STEM_CEILING,
    TITLE_STEM_FLOOR,
    classify,
    classify_occupation,
)

# ── Section 17: representative STEM occupations, title only ────────────────

STEM_TITLES = [
    # Technology
    "Software Engineer", "Software Developer", "Full Stack Developer",
    "Backend Engineer", "Frontend Engineer", "DevOps Engineer",
    "Cloud Engineer", "Site Reliability Engineer", "Data Engineer",
    "AI Engineer", "Machine Learning Engineer", "Data Scientist",
    "Cybersecurity Engineer", "Security Engineer", "Database Engineer",
    "Embedded Software Engineer", "Systems Engineer", "Network Engineer",
    # Engineering
    "Electronics Engineer", "Electrical Engineer", "Mechanical Engineer",
    "Civil Engineer", "Chemical Engineer", "Biomedical Engineer",
    "Aerospace Engineer", "Automotive Engineer", "VLSI Engineer",
    "RF Engineer", "Telecommunications Engineer", "Robotics Engineer",
    "Control Systems Engineer",
    # Science
    "Research Scientist", "Biologist", "Chemist", "Physicist",
    "Materials Scientist", "Computational Scientist", "Laboratory Scientist",
    "Environmental Scientist",
    # Mathematics / quantitative
    "Mathematician", "Statistician", "Actuary", "Quantitative Analyst",
    "Operations Research Scientist",
]

NON_STEM_TITLES = [
    # Human resources
    "HR Manager", "HR Business Partner", "Talent Acquisition Manager",
    "Recruiter", "HR Executive", "People Operations Manager",
    # Management / leadership, where the function is managerial
    "General Manager", "Business Manager", "Administrative Management",
    # Finance
    "Finance Manager", "Accountant", "Financial Controller",
    "Payroll Specialist", "Accounts Executive",
    # Business / commercial
    "Sales Manager", "Business Development Executive", "Account Manager",
    "Customer Success Manager", "Marketing Manager", "Brand Manager",
    "Public Relations Manager",
    # Administration / operations
    "Administrative Assistant", "Office Manager", "Executive Assistant",
    "Administrative Coordinator",
]


@pytest.mark.parametrize("title", STEM_TITLES)
def test_stem_occupation_classifies_stem_from_the_title_alone(title):
    result = classify("", title)
    assert result.classification == STEM, f"{title} regressed to Non-STEM"
    assert result.stem_score >= TITLE_STEM_FLOOR
    # Above the review band: the occupation is not tentative evidence, and a
    # queue full of plainly-STEM engineers is a queue nobody reads.
    assert result.tentative is False
    assert result.occupation == "stem"


@pytest.mark.parametrize("title", NON_STEM_TITLES)
def test_non_stem_occupation_classifies_non_stem_from_the_title_alone(title):
    result = classify("", title)
    assert result.classification == NON_STEM, f"{title} wrongly read as STEM"


# ── Section 19: the distinctions the layer must NOT collapse ───────────────

def test_a_managerial_title_over_engineering_work_is_stem():
    """Section 19 names this one: a Software Engineering Manager is STEM
    because the work being managed is software engineering."""
    assert classify("", "Software Engineering Manager").classification == STEM
    assert classify("", "Engineering Manager").classification == STEM


def test_the_same_manager_noun_over_non_engineering_work_is_not():
    for title in ("HR Manager", "Finance Manager", "Sales Manager"):
        assert classify("", title).classification == NON_STEM


def test_analyst_alone_is_never_decided_by_the_title():
    """Section 19: not every role containing Analyst is automatically STEM.

    `Data Analyst` carries a STEM FIELD and is still handed to the body
    signals, which is what keeps the marketing-reporting Data Analyst
    Non-STEM. `Quantitative Analyst` carries a mathematical PRACTICE and is
    decided (section 17 admits it explicitly)."""
    assert classify_occupation("Data Analyst").verdict is None
    assert classify_occupation("Reporting Analyst").verdict is None
    assert classify_occupation("Quantitative Analyst").verdict == "stem"


def test_a_data_analyst_with_a_technical_jd_still_reaches_stem_on_the_body():
    """No verdict is not a refusal. The body pass is untouched and still
    decides, which is the behaviour the engine had before this layer."""
    result = classify(
        "Build ETL data pipelines in Python, model in the data warehouse, "
        "and run statistical analysis over experiment results.",
        "Data Analyst",
    )
    assert result.classification == STEM
    assert result.occupation is None


def test_a_non_stem_occupation_with_a_technical_jd_is_capped_and_reviewed():
    """A Sales Engineer demoing a technical product is a sales job. The
    ceiling keeps the LABEL Non-STEM and lands the row inside the review band,
    so a human sees it rather than the engine resolving it silently."""
    result = classify(
        "Sales Engineer: demo our Kubernetes-based platform, explain system "
        "design trade-offs to customers in Python terms, and close deals.",
        "Sales Engineer",
    )
    assert result.classification == NON_STEM
    assert result.stem_score == TITLE_NON_STEM_CEILING
    assert result.tentative is True


def test_a_non_stem_occupation_with_a_non_technical_jd_is_not_reviewed():
    result = classify("Own the regional sales quota and the team.", "Sales Manager")
    assert result.classification == NON_STEM
    assert result.tentative is False


# ── Section 21: it generalises, it is not a list of titles ─────────────────

def test_unseen_titles_resolve_from_the_vocabularies_that_compose_them():
    for title in (
        "Principal Embedded Firmware Engineer",
        "Staff Photonics Research Scientist",
        "Senior Geospatial Data Engineer",
        "Lead Mechatronics Validation Engineer",
    ):
        assert classify("", title).classification == STEM, title


def test_a_scientific_suffix_resolves_without_being_listed():
    for title in ("Hydrologist", "Glaciologist", "Volcanologist"):
        assert classify("", title).classification == STEM, title


def test_a_multi_word_stem_phrase_is_never_split_into_a_misleading_half():
    """`machine learning` must not be re-read as the training-and-development
    `learning`, and `business intelligence` must not be re-read as the
    commercial `business`. Both were live bugs in the first cut of the layer."""
    assert classify_occupation("Machine Learning Engineer").verdict == "stem"
    assert classify_occupation("Business Intelligence Engineer").verdict == "stem"
    assert classify_occupation("Learning and Development Manager").verdict == "non_stem"


def test_seniority_words_say_nothing_about_the_occupation():
    plain = classify_occupation("Software Engineer")
    for prefix in ("Senior", "Junior", "Principal", "Staff", "Lead", "Associate"):
        assert classify_occupation(f"{prefix} Software Engineer") == plain


def test_an_unrecognised_occupation_leaves_the_body_in_charge():
    verdict = classify_occupation("Project Coordinator")
    assert verdict.verdict is None
    assert classify("", "Project Coordinator").stem_score == 0.0


def test_an_empty_title_is_not_an_occupation():
    assert classify_occupation("").verdict is None
    assert classify_occupation(None).verdict is None  # type: ignore[arg-type]


# ── Explainability (section 21: correctness, consistency, EXPLAINABILITY) ──

def test_the_persisted_explanation_names_the_occupational_basis():
    result = classify("", "Mechanical Engineer")
    assert "occupation:stem:mechanical_engineer" in result.explanation
    # `signals` stays the body-signal contract other modules read.
    assert not any(s.startswith("occupation:") for s in result.signals)


def test_the_body_score_is_kept_beside_the_final_score():
    result = classify("", "Software Engineer")
    assert result.stem_score == TITLE_STEM_FLOOR
    assert result.body_score < TITLE_STEM_FLOOR


# ── The historical backfill (section 20) ───────────────────────────────────
#
# Section 20 is explicit that fixing the engine is only half the job: "Do not
# leave existing historical data inconsistent with newly created data." The
# migration is the other half, and what matters about it is which rows it
# refuses to touch, so that is what is asserted. It is read out of the file
# that actually runs, because a test that restated the rules would pass while
# the migration did something else.

import importlib.util
import pathlib

MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0086_reclassify_historical_jobs.py"
)


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_the_backfill_migration_is_loadable_and_chained() -> None:
    spec = importlib.util.spec_from_file_location("_stem_backfill", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0086_reclassify_historical_jobs"
    assert module.down_revision == "0085_bgv_inquiries"


def test_a_job_whose_rate_has_already_been_billed_is_left_alone() -> None:
    """`classification_locked` is stamped by the completion charge. Rewriting
    such a row would leave the credit ledger stating a rate the job no longer
    claims, which Part 3 section 8 answers with a credit adjustment instead."""
    assert "classification_locked = FALSE" in _migration_source()


def test_a_job_a_human_already_ruled_on_is_left_alone() -> None:
    """An engine improvement does not outrank a Provider admin who looked at
    this specific job and decided."""
    assert "classification_overridden = FALSE" in _migration_source()


def test_the_backfill_keeps_the_stored_credit_rate_in_step_with_the_label() -> None:
    """A row whose label moved and whose rate did not is the inconsistency
    section 22 forbids, in its most expensive form."""
    source = _migration_source()
    assert "credit_cost_per_report = :cost" in source
    assert "credit_cost(result.classification)" in source


def test_the_backfill_writes_nothing_when_the_label_has_not_moved() -> None:
    assert "if result.classification == row[\"role_classification\"]:" in _migration_source()
