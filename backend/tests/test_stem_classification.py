"""Classification Engine acceptance checklist (Master Directive Part 3 §10).

Every named case in §10's "Classification Engine" block is here verbatim, plus
the §8 edge-case table rows the engine itself owns (title says Engineer but JD
is civil engineering → STEM; Data Analyst with no technical content →
Non-STEM; error → Non-STEM fallback) and the §4.3 counterweights.

The 200ms budget is asserted directly. The engine is pure regex over a few KB
of text, so the real number is microseconds; the assertion exists to fail the
build if someone adds an LLM call to what Part 3 §4 defines as a rule-based
function.
"""
import time

from app.services.stem_classification import (
    NON_STEM,
    STEM,
    ClassificationResult,
    classify,
    classify_safe,
    credit_cost,
)


SALES_JD = """
We are hiring a Business Development Manager. You will own sales targets,
relationship management, client acquisition and account growth. Strong
communication, negotiation and presentation skills required. You will report
to the VP of Sales and manage a portfolio of enterprise clients.
"""

PYTHON_ML_JD = """
Senior role in our AI group. You will build models in Python using TensorFlow,
apply machine learning to production problems, and deploy services.
"""

MECHANICAL_JD = """
As a Mechanical Design Engineer you will produce detailed 3D models in
SolidWorks, run FEA simulations to validate designs, and hand drawings to the
manufacturing team.
"""

GENERIC_ANALYSIS_JD = """
The role involves analysis of market trends, data-driven decisions and monthly
reporting to leadership. You will prepare dashboards and presentations and
coordinate with regional teams.
"""

CIVIL_JD = """
Civil Engineer for our infrastructure practice: structural engineering review,
AutoCAD drawings, site supervision and quantity estimation.
"""

DATA_ANALYST_SOFT_JD = """
Data Analyst to support the marketing team: prepare weekly reports, maintain
spreadsheets, coordinate campaign calendars and summarise performance for
stakeholders.
"""

FIN_ENG_JD = """
Analyst role focused on financial engineering of structured products, client
reporting and portfolio commentary. Strong Excel and communication skills.
"""


# ── §10 acceptance: the four named JDs ──────────────────────────────────────

def test_python_ml_tensorflow_is_stem_high_confidence():
    r = classify(PYTHON_ML_JD, "Machine Learning Engineer")
    assert r.classification == STEM
    assert r.confidence >= 0.80
    assert r.stem_score >= 0.80
    assert not r.tentative
    assert any("python" in s for s in r.signals)
    assert any("tensorflow" in s for s in r.signals)


def test_sales_jd_is_non_stem_high_confidence():
    r = classify(SALES_JD, "Business Development Manager")
    assert r.classification == NON_STEM
    assert r.confidence >= 0.80
    assert r.signals == []


def test_generic_analysis_without_category_c_is_non_stem():
    r = classify(GENERIC_ANALYSIS_JD, "Strategy Associate")
    assert r.classification == NON_STEM


def test_mechanical_design_engineer_with_solidworks_fea_is_stem():
    r = classify(MECHANICAL_JD, "Mechanical Design Engineer")
    assert r.classification == STEM
    assert r.stem_score >= 0.80


def test_classification_completes_within_200ms():
    big_jd = MECHANICAL_JD * 20  # ~7KB, larger than any real JD
    classify(big_jd, "Mechanical Design Engineer")  # warm the regex cache
    started = time.perf_counter()
    classify(big_jd, "Mechanical Design Engineer")
    assert (time.perf_counter() - started) < 0.2


# ── §8 edge cases the engine owns ───────────────────────────────────────────

def test_civil_engineer_is_stem_not_just_software():
    r = classify(CIVIL_JD, "Civil Engineer")
    assert r.classification == STEM


def test_data_analyst_title_without_technical_content_is_non_stem():
    r = classify(DATA_ANALYST_SOFT_JD, "Data Analyst")
    assert r.classification == NON_STEM


def test_engine_error_falls_back_to_non_stem():
    r = classify_safe(None, None)  # type: ignore[arg-type] — the point
    assert isinstance(r, ClassificationResult)
    # None title/text may legitimately classify rather than raise; force a
    # crash through a type the engine cannot lower-case.
    r = classify_safe(object(), object())  # type: ignore[arg-type]
    assert r.classification == NON_STEM
    assert r.engine_error is True
    assert r.confidence == 0.0
    assert r.tentative is True


# ── §4.3 counterweights ─────────────────────────────────────────────────────

def test_financial_engineering_is_not_stem():
    r = classify(FIN_ENG_JD, "Financial Analyst")
    assert r.classification == NON_STEM


def test_power_bi_counts_only_with_dax():
    without = classify("Reporting role using Power BI dashboards.", "MIS Executive")
    with_dax = classify(
        "Analytics role: Power BI with DAX measures, statistical analysis, "
        "SQL and data warehouse modelling.",
        "BI Developer",
    )
    assert not any(s.startswith("bi:") for s in without.signals)
    assert any(s.startswith("bi:") for s in with_dax.signals)
    assert with_dax.classification == STEM


def test_whole_word_matching_no_substring_false_positives():
    # 'scala' must not fire inside 'scalable'; 'r'/'go' are not signals at all.
    r = classify(
        "Build a scalable go-to-market plan for our regional sales org.",
        "Sales Manager",
    )
    assert r.signals == []
    assert r.classification == NON_STEM


# ── ambiguity band → review queue (§4.4, §9) ────────────────────────────────

def test_technical_sales_engineer_band_is_logged_tentative():
    r = classify(
        "Technical Sales Engineer: demo our SQL-backed product, explain "
        "system design trade-offs to customers, and close enterprise deals.",
        "Technical Sales Engineer",
    )
    # sql (strong) + system design (medium) = 0.50 → STEM by §8's tie rule,
    # inside the review band either way.
    assert r.tentative is True
    assert r.classification == (STEM if r.stem_score >= 0.50 else NON_STEM)


# ── credit cost mapping (Part 5 §2.2, Rule 9) ───────────────────────────────

def test_credit_cost_mapping():
    from decimal import Decimal

    assert credit_cost(STEM) == Decimal("1.5")
    assert credit_cost(NON_STEM) == Decimal("1.0")
    assert credit_cost(None) == Decimal("1.0")       # NULL → Non-STEM, logged
    assert credit_cost("garbage") == Decimal("1.0")  # unknown → Non-STEM
