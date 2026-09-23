"""W6.6: an explicit disclaimer is written as counter-evidence, and only that.

THE DISTINCTION UNDER TEST
----------------------------
Negative evidence is a substantive answer DENYING the claim. Absent evidence
is a non-answer. The first now lands in the ledger as `contradicts`; the
second never reaches the recording loop at all and keeps costing confidence
rather than score, which is the Miti rule this workstream must not weaken.

WHAT THE CONSEQUENCE IS, AND IS NOT
-------------------------------------
A contradicted claim grades MATERIAL and routes the report to a person. It
must never reject, and the structural half of that is asserted here the way
the proctoring and judge isolation tests do it: by the import graph, because
a detector that could reach a scorer is a detector one refactor away from
moving a grade.
"""
from __future__ import annotations

import ast
import pathlib

from app.services.evidence import negative


# ── The detector, both directions ────────────────────────────────────────────

def test_a_plain_disclaimer_is_detected() -> None:
    assert negative.disclaims("I have not used Kafka.", "Kafka Stream Processing")
    assert negative.disclaims(
        "Honestly, I've never worked with Terraform in a real project.",
        "Terraform Infrastructure",
    )
    assert negative.disclaims(
        "I do not have any experience with Snowflake.", "Snowflake Warehousing"
    )


def test_ordinary_negation_is_not_a_disclaimer() -> None:
    """The miss direction is chosen: these are all filed as support, which is
    where every answer was filed before the module existed."""
    cases = [
        # Negation about something other than the competency.
        ("I have not used the staging cluster much, though Kafka is where I "
         "spend most days.", "Kafka Stream Processing"),
        # Hedges are not denials.
        ("Not only did I use Kafka, I ran the migration.", "Kafka Stream Processing"),
        # A denial in one sentence must not reach a term in the next.
        ("I have not used it in anger. Kafka came later in my career.",
         "Kafka Stream Processing"),
        # Third-person negation is not a self-disclaimer.
        ("The previous team had not used Kafka before I joined.",
         "Kafka Stream Processing"),
        # A real answer about the thing.
        ("I led the Kafka migration and tuned the consumer groups.",
         "Kafka Stream Processing"),
    ]
    for answer, competency in cases:
        assert not negative.disclaims(answer, competency), answer


def test_generic_competency_words_never_anchor_a_match() -> None:
    """"I don't know" beside a generic word must not contradict a behavioural
    competency: one hedged clause would otherwise contradict half a
    framework."""
    assert not negative.disclaims(
        "I don't know that our management approach was perfect.",
        "Stakeholder Management",
    )
    assert negative._terms("Team Leadership Skills") == []


def test_the_disclaimed_term_is_reported_for_provenance() -> None:
    assert negative.disclaimed_terms(
        "I have never used Kubernetes.", "Kubernetes Operations"
    ) == ["kubernetes"]


# ── The wiring, asserted over the source ────────────────────────────────────

def _write_site_source() -> str:
    """The ONE ledger writer for answers (since 2026-09-24), which the
    conversation calls per answer and the scoring pass calls as a backfill."""
    import inspect

    from app.services.assessment_pipeline import evidence

    return inspect.getsource(evidence.record_answer_evidence)


def test_the_stance_is_decided_per_answer_at_the_one_write_site() -> None:
    """The stance is a choice between the two ledger constants, keyed on the
    detector, in the same loop that records the evidence. Not a second loop
    and not a second write site: one answer gets exactly one stance."""
    source = _write_site_source()
    assert "negative_evidence.disclaimed_terms(" in source
    assert "ledger.STANCE_CONTRADICTS" in source
    # The old unconditional support write is gone.
    assert "stance=ledger.STANCE_SUPPORTS,\n" not in source


def test_non_answers_still_never_reach_the_ledger() -> None:
    """Absent evidence stays absent. The writer returns before any write for
    a non-substantive answer, so a non-answer cannot acquire a stance of either
    kind, and the insufficient-evidence path keeps costing confidence rather
    than score."""
    source = _write_site_source()
    guard = "if not answer_quality.is_substantive(text):\n        return"
    assert guard in source
    assert source.index(guard) < source.index("record_evidence(")


def test_the_detector_reaches_no_scorer_and_no_score_reaches_it() -> None:
    """By the import graph, both halves. `negative` imports nothing beyond
    the standard library, and nothing under miti imports it: the only
    consumer is the ledger write site, whose downstream is
    `needs_human_review`. No flag ever auto-rejects."""
    repo = pathlib.Path(__file__).resolve().parents[1]

    tree = ast.parse(
        (repo / "app/services/evidence/negative.py").read_text(encoding="utf-8")
    )
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported <= {"re", "__future__"}, imported

    for path in (repo / "app" / "services" / "miti").rglob("*.py"):
        module_tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module_tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "evidence.negative" not in node.module, path
