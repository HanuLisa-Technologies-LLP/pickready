"""How a claim is read before anything scores it.

Miti stage 2 is deliberately narrow: it EXTRACTS and MUST NOT EVALUATE. An
opinion formed here would enter the pipeline ahead of the dimension evaluators,
without a rubric, without their isolation and without a citation, and
downstream it would be indistinguishable from a finding. So the three readings
below are mechanical, and each has a rule worth pinning:

  MATERIALITY COMES FROM THE MATRIX, never from the claim's own wording. A
  claim that calls itself critical is a candidate's adjective; the cost of
  being wrong is set by the most consequential matrix item it touches.

  A CLAIM MAPPED TO NOTHING IS LOW, NOT DROPPED. Dropping it would lose a piece
  of the candidate's account for the crime of not matching a matrix item, and
  triangulation legitimately reads ungraded claims: an inconsistency in one is
  still an inconsistency.

  AMBIGUOUS ATTRIBUTION IS RECORDED, NOT RESOLVED. "We migrated and I owned the
  rollout" carries both, and inventing a single subject would manufacture
  either ownership the candidate did not claim or distance they did not take.

Pure functions. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.miti import claims


MUST_HAVE = "must_have"
NICE_TO_HAVE = "nice_to_have"
BEHAVIOURAL = "behavioural"


# ── Materiality ──────────────────────────────────────────────────────────────


def test_a_claim_touching_a_must_have_is_the_most_material() -> None:
    """Getting a Must-have wrong caps the report, so it sets the cost."""
    assert (
        claims.materiality_for(competencies=["Kafka"], matrix={"Kafka": MUST_HAVE})
        == claims.MATERIALITY_CRITICAL
    )


def test_a_claim_across_several_competencies_takes_the_highest() -> None:
    """The cost of being wrong is set by the most consequential thing it
    touches, not by the average or by whichever was listed first."""
    highest = claims.materiality_for(
        competencies=["Ownership", "Kafka"],
        matrix={"Ownership": BEHAVIOURAL, "Kafka": MUST_HAVE},
    )
    assert highest == claims.MATERIALITY_CRITICAL


def test_the_order_of_the_competencies_does_not_change_the_answer() -> None:
    matrix = {"Ownership": BEHAVIOURAL, "Kafka": MUST_HAVE}
    forwards = claims.materiality_for(competencies=["Ownership", "Kafka"], matrix=matrix)
    backwards = claims.materiality_for(competencies=["Kafka", "Ownership"], matrix=matrix)
    assert forwards == backwards


def test_a_claim_the_matrix_does_not_grade_is_low_rather_than_dropped() -> None:
    """Triangulation reads ungraded claims on purpose: an inconsistency in one
    is still an inconsistency."""
    assert (
        claims.materiality_for(competencies=["Origami"], matrix={"Kafka": MUST_HAVE})
        == claims.MATERIALITY_LOW
    )


def test_a_claim_bearing_on_nothing_at_all_is_low() -> None:
    assert claims.materiality_for(competencies=[], matrix={}) == claims.MATERIALITY_LOW


def test_every_materiality_returned_is_one_the_module_declares() -> None:
    for category in (MUST_HAVE, NICE_TO_HAVE, BEHAVIOURAL, "invented_category"):
        value = claims.materiality_for(
            competencies=["Kafka"], matrix={"Kafka": category}
        )
        assert value in claims.MATERIALITIES, category


# ── Attribution ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "I built the ingestion pipeline.",
        "I decided to batch the writes.",
    ],
)
def test_a_first_person_claim_reads_as_self(text: str) -> None:
    assert claims.infer_subject(text) == claims.SUBJECT_SELF


@pytest.mark.parametrize(
    "text",
    [
        "We shipped the migration.",
        "The team delivered it in one quarter.",
    ],
)
def test_a_collective_claim_reads_as_team(text: str) -> None:
    assert claims.infer_subject(text) == claims.SUBJECT_TEAM


def test_a_claim_carrying_both_is_recorded_as_ambiguous() -> None:
    """Not resolved to the self-claim. The ambiguity is real, and picking a
    side would manufacture either ownership the candidate did not claim or
    distance they did not take."""
    assert (
        claims.infer_subject("We migrated the cluster and I owned the rollout.")
        == claims.SUBJECT_AMBIGUOUS
    )


def test_a_claim_with_no_subject_at_all_is_ambiguous() -> None:
    """"The migration was completed" names nobody. Reading it as self would
    credit the candidate for a sentence that credits no one."""
    assert claims.infer_subject("The migration was completed.") == claims.SUBJECT_AMBIGUOUS
    assert claims.infer_subject("") == claims.SUBJECT_AMBIGUOUS


# ── Parsing the extraction response ──────────────────────────────────────────


def test_a_payload_with_no_claims_list_yields_nothing() -> None:
    """A malformed response is an outage, not a candidate with no claims, and
    the caller distinguishes them. Returning an empty list here rather than
    raising is what lets it."""
    kwargs = dict(source_kind="resume", source_ref="profile:1", matrix={})
    assert claims.parse_claims({}, **kwargs) == []
    assert claims.parse_claims({"claims": None}, **kwargs) == []
    assert claims.parse_claims({"claims": "not-a-list"}, **kwargs) == []


def test_a_row_that_is_not_a_mapping_is_skipped_not_fatal() -> None:
    """One malformed row must not discard the rows around it: the others are
    still the candidate's account."""
    parsed = claims.parse_claims(
        {
            "claims": [
                "a bare string",
                {"text": "I rebalanced the Kafka partitions.", "competencies": ["Kafka"]},
            ]
        },
        source_kind="resume",
        source_ref="profile:1",
        matrix={"Kafka": MUST_HAVE},
    )
    assert len(parsed) == 1
    assert "Kafka" in parsed[0].text


def test_the_extraction_prompt_lists_the_competencies_it_may_map_to() -> None:
    """The prompt is the only place the model learns the allowed set; an empty
    list has to read as "none" rather than as an open invitation."""
    messages = claims.extraction_prompt(
        text="I rebalanced the partitions.", source_kind="resume", competencies=["Kafka"]
    )
    assert messages[0]["role"] == "system"
    body = " ".join(message["content"] for message in messages)
    assert "Kafka" in body

    empty = claims.extraction_prompt(text="x", source_kind="resume", competencies=[])
    assert "(none)" in " ".join(message["content"] for message in empty)
