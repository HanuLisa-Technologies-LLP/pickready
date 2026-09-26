"""The sentences a frozen matrix item explains itself with, and no number.

WHAT MAKES THE RESTATEMENT WORTH ANYTHING IS THAT IT IS CHECKABLE BY THE PERSON
CHECKING IT. A hiring manager confirming "1.4850" is confirming that some
arithmetic looks plausible, which is not a review. A hiring manager confirming
"this counts for more because you told me the last person never owned anything
in production" is confirming the thing they actually said, and can say no.

So `plain_provenance` carries NO NUMBERS. That is the standing product rule and
here it is also the mechanism: a multiplier in this text would replace a
reviewable sentence with an unreviewable one.

The silences are as deliberate as the sentences. A layer that came back at the
identity expressed no opinion, and saying "this was unchanged" about four
layers in a row is noise a reader learns to skip past -- at which point they
skip the line that did move too.

The menu draw that used to be tested here (`_candidates_from_menu`) was
DELETED with the matrix compiler in the Vivekium release. `plain_provenance`
survives with the scorecard's read half, for a frozen matrix written before it.

Pure functions. No database, no network, no model.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.hiring import scorecard, situations


def _item(**overrides) -> scorecard.MatrixItem:
    fields = dict(
        competency_id=uuid.uuid4(),
        competency="Stream processing",
        category="must_have",
        dimension="verified_competence",
        observable_evidence="has rebalanced partitions under load",
        evidence_sources=("assessment",),
        assessment_method="structured_probe",
        weight=0.2,
        threshold={},
        disqualifier=None,
        provenance={},
        swot_origin=None,
        anchor_key="stream_processing",
        force_rank=1,
        required_level="Advanced",
        ordinal=1,
    )
    fields.update(overrides)
    return scorecard.MatrixItem(**fields)


def _provenance(**terms) -> dict:
    return {"terms": terms}


# ── The no-numbers rule ──────────────────────────────────────────────────────


def test_no_sentence_ever_carries_a_number() -> None:
    """Across every combination of layers, because the one that leaks is always
    the branch nobody built a screen for."""
    key = next(iter(situations.SITUATIONS))
    for anchor in ("stream_processing", None):
        for origin in ("the last person never owned anything in production", None):
            for situation in (1.35, 0.8, 1.0, None, "nonsense"):
                item = _item(
                    anchor_key=anchor,
                    swot_origin=origin,
                    provenance={
                        "terms": {
                            "situation_layer3": situation,
                            "role_layer3": 1.1,
                        },
                        "situation_key": key,
                        "unreachable_sources": ["reference"],
                    },
                )
                for line in scorecard.plain_provenance(item):
                    assert not any(c.isdigit() for c in line), line


# ── Layer 1: anchored or not ─────────────────────────────────────────────────


def test_an_anchored_item_says_it_starts_from_the_platform_baseline() -> None:
    lines = scorecard.plain_provenance(_item(anchor_key="stream_processing"))
    assert any("department model already recognises" in line for line in lines)


def test_an_unanchored_item_says_it_starts_neutral() -> None:
    """`match_competency` returning None is honest provenance, and this is where
    a hiring manager reads it: the criterion is specific to their role rather
    than something the menu already knew about."""
    lines = scorecard.plain_provenance(_item(anchor_key=None))
    assert any("specific to your role" in line for line in lines)
    assert not any("already recognises" in line for line in lines)


# ── Layer 3: only when it moved something ────────────────────────────────────


def _situation_provenance(value):
    """A provenance carrying one readable situation key and one term."""
    return {
        "terms": {"situation_layer3": value},
        "situation_key": next(iter(situations.SITUATIONS)),
    }


def test_a_layer_that_lifted_the_weight_is_named() -> None:
    lines = scorecard.plain_provenance(_item(provenance=_situation_provenance(1.35)))
    assert any("more heavily" in line for line in lines)


def test_a_layer_that_lowered_it_is_named_the_other_way() -> None:
    lines = scorecard.plain_provenance(_item(provenance=_situation_provenance(0.8)))
    assert any("less heavily" in line for line in lines)


def test_a_layer_that_expressed_no_opinion_says_nothing() -> None:
    """The identity means the layer did not speak. "This was unchanged" about
    every layer running is noise, and a reader who skips it skips the line that
    did move."""
    lines = scorecard.plain_provenance(_item(provenance=_situation_provenance(1.0)))
    assert not any("heavily" in line for line in lines)


@pytest.mark.parametrize("junk", [None, "", "a bit more", {}, []])
def test_an_unreadable_term_says_nothing_rather_than_guessing(junk) -> None:
    """A provenance dict written by an older pipeline must not produce a
    sentence claiming a layer did something.

    A row written before 2026-09-09 still carries a `company_layer2` term. It
    is not read any more, and nothing reads it back into a sentence.
    """
    assert scorecard._direction(junk) is None
    lines = scorecard.plain_provenance(_item(provenance=_situation_provenance(junk)))
    assert not any("heavily" in line for line in lines)


def test_a_retired_company_term_on_an_old_row_produces_no_sentence() -> None:
    """The removal, asserted from the reader's side.

    Every matrix row frozen before Layer 2 was withdrawn carries a
    `company_layer2` multiplier in its stored provenance. Those rows are still
    read on the review screen, and a hiring manager must not be told a layer
    weighed something when there is no longer any such input.
    """
    lines = scorecard.plain_provenance(
        _item(provenance=_provenance(company_layer2=1.4))
    )
    assert not any("heavily" in line for line in lines)
    assert not any("philosophy" in line for line in lines)


def test_the_situation_is_named_by_its_label_when_it_moved_the_weight() -> None:
    key, situation = next(iter(situations.SITUATIONS.items()))
    lines = scorecard.plain_provenance(
        _item(
            provenance={
                "terms": {"situation_layer3": 1.35},
                "situation_key": key,
            }
        )
    )
    assert any(situation.label in line for line in lines)


def test_a_situation_key_nothing_recognises_is_not_named() -> None:
    """A stored key from a retired taxonomy must not put an unrecognisable word
    in front of a hiring manager as though they had confirmed it."""
    lines = scorecard.plain_provenance(
        _item(
            provenance={
                "terms": {"situation_layer3": 1.35},
                "situation_key": "not_a_situation",
            }
        )
    )
    assert not any("You confirmed this role is" in line for line in lines)


def test_a_situation_that_moved_nothing_is_not_named() -> None:
    key = next(iter(situations.SITUATIONS))
    lines = scorecard.plain_provenance(
        _item(provenance={"terms": {"situation_layer3": 1.0}, "situation_key": key})
    )
    assert not any("You confirmed this role is" in line for line in lines)


# ── The three ways a SWOT origin is reported ─────────────────────────────────


def test_a_swot_sentence_that_moved_the_weight_is_quoted_with_its_effect() -> None:
    """Quoted verbatim. A paraphrase would ask the manager to confirm our
    wording of what they said rather than what they said."""
    said = "the last person never owned anything in production"
    lines = scorecard.plain_provenance(
        _item(swot_origin=said, provenance=_provenance(role_layer3=1.1))
    )
    assert any(said in line and "moved this criterion" in line for line in lines)


def test_a_swot_sentence_that_moved_nothing_is_still_quoted() -> None:
    """It is where the criterion came from, and dropping it would make an item
    the manager themselves raised read as one the platform invented."""
    said = "we need someone who can run the migration alone"
    lines = scorecard.plain_provenance(
        _item(swot_origin=said, provenance=_provenance(role_layer3=1.0))
    )
    assert any(said in line for line in lines)
    assert not any("moved this criterion" in line for line in lines)


def test_an_item_the_session_never_touched_says_exactly_that() -> None:
    """Silence about it would leave a hiring manager assuming they had asked
    for a criterion they never mentioned."""
    lines = scorecard.plain_provenance(
        _item(swot_origin=None, provenance=_provenance(role_layer3=1.1))
    )
    assert any("Nothing in your SWOT session spoke to this one" in line for line in lines)


# ── Evidence the assessment cannot reach ─────────────────────────────────────


def test_evidence_outside_the_assessment_is_declared_up_front() -> None:
    """Said before finalisation rather than discovered in the report: the
    manager is agreeing to a criterion the assessment can probe and cannot
    confirm."""
    lines = scorecard.plain_provenance(
        _item(provenance={"unreachable_sources": ["reference", "work_artefact"]})
    )
    assert any("sits outside the assessment" in line for line in lines)


def test_nothing_is_said_when_every_source_is_reachable() -> None:
    lines = scorecard.plain_provenance(_item(provenance={"unreachable_sources": []}))
    assert not any("outside the assessment" in line for line in lines)


def test_an_item_with_no_provenance_at_all_still_explains_itself() -> None:
    """A row written before provenance was recorded must still produce a
    reviewable sentence, or the review screen is blank for exactly the oldest
    and least trustworthy items."""
    lines = scorecard.plain_provenance(_item(provenance=None))
    assert lines
    assert all(line.strip() for line in lines)
