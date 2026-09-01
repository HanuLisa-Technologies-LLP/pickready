"""Three places the pipeline decides something on a candidate's behalf.

`test_miti_pipeline` runs the stages. This file takes the arms it does not: an
outcome with no aggregate, an evaluator input with no role-level anchor, and
the three shapes a model response can arrive in that are not a result.

WHAT THE CLIENT SEES WHEN THE PIPELINE PRODUCED NOTHING IS NOT AN EMPTY GRADE,
it is nothing at all. An outcome blocked at G1 has no aggregate, and returning
a shape with empty strings in it would render as a report about a candidate
nobody evaluated.

AN OUTAGE IS NOT A FINDING, AND THERE ARE THREE OF THEM. The call raising, the
response not being JSON, and the response being JSON that is not an object all
land on `insufficient_evidence`, never on a low band. The third is the one that
looks harmless: `json.loads("[]")` succeeds perfectly and then fails on the
first subscript, which is the same hazard the JSON-mode contract check exists
for one layer down.

Pure functions and one injected coroutine. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.miti import dimensions, pipeline


def _payload(dimension: str = "verified_competence") -> pipeline.EvaluatorInput:
    return pipeline.EvaluatorInput(
        dimension=dimension,
        competencies=("Stream processing",),
        rubric_anchor="anchor",
        evidence=(),
        role_context="",
    )


# ── The outcome with nothing in it ───────────────────────────────────────────


def test_an_outcome_with_no_aggregate_shows_a_client_nothing() -> None:
    """A blocked run has no grade. A shape with empty fields in it would render
    as a report about a candidate nobody evaluated."""
    assert pipeline.EvaluationOutcome().client_projection() == {}


def test_an_outcome_with_no_gates_run_is_not_yet_undeliverable() -> None:
    """`deliverable` asks whether any BLOCKING gate failed. Nothing having run
    is not a failure, and reading it as one would refuse every report the
    moment a caller checked before the pipeline started."""
    outcome = pipeline.EvaluationOutcome()
    assert outcome.deliverable is True
    assert outcome.blocking_reasons == []


def test_the_serialised_outcome_carries_the_absences_explicitly() -> None:
    """None rather than a missing key: a consumer must be able to tell "this
    stage did not run" from "this key was not serialised"."""
    payload = pipeline.EvaluationOutcome().as_dict()
    assert payload["triangulation"] is None
    assert payload["aggregate"] is None
    assert payload["dimensions"] == []
    assert payload["degraded_dimensions"] == []


# ── The role-level anchor is added, never substituted ────────────────────────


def test_each_evaluator_gets_its_own_dimension_anchor() -> None:
    """Sections 9.1 to 9.5 state a different six-band table per dimension.
    Giving all five the same one anchors four of them against a rubric written
    for a question they were not asked."""
    payloads = pipeline.build_evaluator_inputs(pipeline.EvaluationInputs())
    anchors = {p.dimension: p.rubric_anchor for p in payloads}
    assert len(anchors) == len(pipeline.DIMENSIONS)
    assert len(set(anchors.values())) == len(anchors), "two dimensions share an anchor"


def test_a_role_anchor_is_appended_to_the_dimension_anchor() -> None:
    """Appended. Replacing the section 9.x table with Sutra's sentence would
    lose the band definitions the evaluator grades against."""
    role = "This role runs the migration without a team."
    payloads = pipeline.build_evaluator_inputs(
        pipeline.EvaluationInputs(rubric_anchor=role)
    )
    for payload in payloads:
        assert role in payload.rubric_anchor
        assert dimensions.rubric_anchor_text(payload.dimension) in payload.rubric_anchor


def test_no_role_anchor_leaves_the_dimension_anchor_exactly_as_it_was() -> None:
    payloads = pipeline.build_evaluator_inputs(pipeline.EvaluationInputs())
    for payload in payloads:
        assert payload.rubric_anchor == dimensions.rubric_anchor_text(payload.dimension)


def test_an_evaluator_only_carries_the_competencies_on_its_own_dimension() -> None:
    """The isolation boundary, at the routing half."""
    inputs = pipeline.EvaluationInputs(
        competency_dimensions={
            "Stream processing": "verified_competence",
            "Ownership": "trajectory_potential",
        }
    )
    by_dimension = {p.dimension: p.competencies for p in pipeline.build_evaluator_inputs(inputs)}
    assert by_dimension["verified_competence"] == ("Stream processing",)
    assert by_dimension["trajectory_potential"] == ("Ownership",)


# ── The three ways an evaluator fails to answer ──────────────────────────────


@pytest.mark.asyncio
async def test_a_provider_outage_is_insufficient_evidence_not_a_low_band() -> None:
    """Converting an outage into a finding about a candidate is the same class
    of error as a hash deciding whether gibberish failed."""
    async def _raise(*args, **kwargs):
        raise TimeoutError("the provider did not answer")

    result = await pipeline._run_evaluator(_payload(), _raise)
    assert result.insufficient_evidence is True
    assert result.evidence_refs == ()


@pytest.mark.asyncio
async def test_a_response_that_is_not_json_is_insufficient_evidence() -> None:
    async def _garbage(*args, **kwargs):
        return "I think this candidate is quite good, actually."

    result = await pipeline._run_evaluator(_payload(), _garbage)
    assert result.insufficient_evidence is True


@pytest.mark.asyncio
async def test_valid_json_that_is_not_an_object_is_insufficient_evidence() -> None:
    """The one that looks harmless. `json.loads("[]")` succeeds perfectly and
    then fails on the first subscript, which is the identical hazard the
    JSON-mode contract check exists for one layer down."""
    for raw in ("[]", '["strong"]', "null", "42", '"strong"'):
        async def _not_an_object(*args, _raw=raw, **kwargs):
            return _raw

        result = await pipeline._run_evaluator(_payload(), _not_an_object)
        assert result.insufficient_evidence is True, raw


@pytest.mark.asyncio
async def test_a_well_formed_answer_is_read() -> None:
    """So the three failures above are failures rather than the only path."""
    async def _answer(*args, **kwargs):
        return (
            '{"band": "solid", "rationale": "ok", "evidence_refs": ["ref:1"], '
            '"insufficient_evidence": false}'
        )

    result = await pipeline._run_evaluator(_payload(), _answer)
    assert result.insufficient_evidence is False
    assert result.band == "solid"
    assert result.evidence_refs == ("ref:1",)


# ── Parsing one response ─────────────────────────────────────────────────────


def test_a_band_the_model_invented_is_refused_rather_than_defaulted() -> None:
    """A silent default would convert a malformed response into a real grade
    for a real person."""
    result = pipeline.parse_result(
        {"band": "excellent", "evidence_refs": ["ref:1"]}, "verified_competence"
    )
    assert result.insufficient_evidence is True


def test_a_band_with_no_citation_is_not_usable() -> None:
    """Siddhi cannot write a sentence about it, and an uncitable score is one
    that would be reported without a citation or not reported at all."""
    result = pipeline.parse_result({"band": "solid"}, "verified_competence")
    assert result.insufficient_evidence is True


def test_an_explicit_insufficient_answer_needs_no_citation() -> None:
    """"I could not tell" is a complete answer and has nothing to cite."""
    result = pipeline.parse_result(
        {"band": "partial", "insufficient_evidence": True}, "verified_competence"
    )
    assert result.insufficient_evidence is True
    assert result.evidence_refs == ()


def test_blank_citations_are_dropped_and_do_not_count_as_citations() -> None:
    """`None` is the one that mattered.

    `str(None)` is "None", which survives a truthiness check and becomes a
    citation reading "None" -- so the band passed the uncitable check above and
    went on to be rendered against a ref that resolves to nothing. A FABRICATED
    citation is a worse failure than a missing one, because it reads as
    provenance. Found by this test and fixed in `parse_result`.
    """
    result = pipeline.parse_result(
        {"band": "solid", "evidence_refs": ["", "   ", None]}, "verified_competence"
    )
    assert result.evidence_refs == ()
    assert result.insufficient_evidence is True


def test_a_per_competency_band_outside_the_scale_is_dropped_not_guessed() -> None:
    """The rest of the answer survives: one unusable sub-band must not discard
    the dimension the evaluator did answer."""
    result = pipeline.parse_result(
        {
            "band": "solid",
            "evidence_refs": ["ref:1"],
            "per_competency": {"Stream processing": "solid", "Ownership": "excellent"},
        },
        "verified_competence",
    )
    assert result.insufficient_evidence is False
    assert result.per_competency == {"Stream processing": "solid"}
