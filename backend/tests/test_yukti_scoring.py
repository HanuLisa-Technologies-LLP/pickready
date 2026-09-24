"""Yukti's arithmetic: grounded verdicts to one outcome per link. Pure.

Mutation check recorded in the Phase 2 report: storing the part scores in the
provenance (`provenance["components"] = present`) fails
`test_provenance_carries_presence_and_never_a_part_score`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from app.services.assessment_contract import ContractSkill
from app.services.yukti import config, scoring
from app.services.yukti.grounding import (
    GroundedItem,
    GroundedJudgement,
    GroundedNeed,
    GroundedSkill,
)
from app.services.yukti.inputs import CandidateInput, JobContext, NamedNeed
from app.services.yukti.validation_fit import ValidationFit

NO_VALIDATION = ValidationFit(None, MappingProxyType({}))


def _skill(name: str, bucket: str, priority: int) -> ContractSkill:
    return ContractSkill(uuid.uuid4(), name, bucket, priority, "")


MH1 = _skill("Kafka stream processing", "must_have", 1)
MH2 = _skill("SQL tuning", "must_have", 2)
NH1 = _skill("Airflow orchestration", "nice_to_have", 1)


def _ctx(*, needs: tuple[NamedNeed, ...] = (), skills=(MH1, MH2, NH1)) -> JobContext:
    return JobContext(
        job_id=uuid.uuid4(),
        title="Data Engineer",
        department=None,
        grade_label="Managerial",
        experience_band="5 to 9 years",
        jd_text="Owns streaming pipelines.",
        role_summary="Owns the pipelines.",
        skills=tuple(skills),
        all_skill_names=tuple(s.name for s in skills),
        needs=needs,
        contract_digest="d" * 64,
        contract_version=0,
        jd_redacted=False,
    )


def _candidate() -> CandidateInput:
    return CandidateInput(
        link_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        resume_text="Owned the Kafka pipeline",
        redacted=True,
        truncated=False,
        neutralised=False,
    )


def _judgement(
    verdicts: dict[ContractSkill, str],
    *,
    experience: GroundedItem | None = GroundedItem("strong", "Led a team of four", "Led a data team"),
    role_fit: GroundedItem | None = GroundedItem("some", "Owned the Kafka pipeline", "Streaming work"),
    needs: tuple[GroundedNeed, ...] = (),
    negative: tuple[ContractSkill, ...] = (),
) -> GroundedJudgement:
    return GroundedJudgement(
        skills=tuple(
            GroundedSkill(skill, verdict, None if verdict == "none" else "quoted line")
            for skill, verdict in verdicts.items()
        ),
        experience=experience,
        role_fit=role_fit,
        needs=needs,
        negative_skills=negative,
    )


def _outcome(judgement, validation=NO_VALIDATION, ctx=None):
    ctx = ctx or _ctx()
    return scoring.outcome_from_judgement(
        ctx, _candidate(), judgement, validation, model_id="gpt-5.6-terra", prompt_version="1+abc"
    )


# ── Parts one and two ────────────────────────────────────────────────────────


def test_priority_weighting_is_harmonic_within_a_bucket() -> None:
    present, _ = scoring.component_scores(
        _judgement({MH1: "strong", MH2: "none", NH1: "none"}), NO_VALIDATION, needs_listed=False
    )
    # weights 1 and 1/2: (1*1.0 + 0.5*0.0) / 1.5
    assert present[config.COMPONENT_MUST_HAVE] == pytest.approx(100 * 1.0 / 1.5)
    present, _ = scoring.component_scores(
        _judgement({MH1: "none", MH2: "strong", NH1: "none"}), NO_VALIDATION, needs_listed=False
    )
    assert present[config.COMPONENT_MUST_HAVE] == pytest.approx(100 * 0.5 / 1.5)


def test_an_empty_nice_to_have_bucket_is_excluded_not_zero() -> None:
    ctx = _ctx(skills=(MH1,))
    present, excluded = scoring.component_scores(
        _judgement({MH1: "strong"}), NO_VALIDATION, needs_listed=False
    )
    assert config.COMPONENT_NICE_TO_HAVE not in present
    assert excluded[config.COMPONENT_NICE_TO_HAVE] == config.EXCLUDED_NO_SKILLS
    assert _outcome(_judgement({MH1: "strong"}), ctx=ctx).status == config.STATUS_SCORED


# ── Parts three to six ───────────────────────────────────────────────────────


def test_an_ungrounded_experience_is_excluded_and_none_counts_as_zero() -> None:
    present, excluded = scoring.component_scores(
        _judgement({MH1: "strong"}, experience=None, role_fit=GroundedItem("none", None, None)),
        NO_VALIDATION,
        needs_listed=False,
    )
    assert excluded[config.COMPONENT_EXPERIENCE] == config.EXCLUDED_UNGROUNDED
    assert present[config.COMPONENT_ROLE_FIT] == 0.0


def test_company_need_fit_is_the_mean_over_surviving_needs() -> None:
    needs = (
        GroundedNeed("n1", "weakness", "strong", "q", "Ran streaming"),
        GroundedNeed("n2", "threat", "none", None, None),
    )
    present, _ = scoring.component_scores(
        _judgement({MH1: "strong"}, needs=needs), NO_VALIDATION, needs_listed=True
    )
    assert present[config.COMPONENT_COMPANY_NEED] == pytest.approx(50.0)


def test_no_listed_needs_and_no_surviving_needs_are_excluded_with_different_reasons() -> None:
    _, excluded = scoring.component_scores(
        _judgement({MH1: "strong"}), NO_VALIDATION, needs_listed=False
    )
    assert excluded[config.COMPONENT_COMPANY_NEED] == config.EXCLUDED_NO_NEEDS
    _, excluded = scoring.component_scores(
        _judgement({MH1: "strong"}), NO_VALIDATION, needs_listed=True
    )
    assert excluded[config.COMPONENT_COMPANY_NEED] == config.EXCLUDED_UNGROUNDED


def test_unanswered_validation_is_excluded() -> None:
    _, excluded = scoring.component_scores(
        _judgement({MH1: "strong"}), NO_VALIDATION, needs_listed=False
    )
    assert excluded[config.COMPONENT_VALIDATION] == config.EXCLUDED_NOT_ANSWERED


# ── The pre score ────────────────────────────────────────────────────────────


def test_the_pre_score_renormalises_over_present_parts() -> None:
    present = {
        config.COMPONENT_MUST_HAVE: 100.0,
        config.COMPONENT_EXPERIENCE: 50.0,
    }
    weights = config.COMPONENT_WEIGHTS
    expected = (weights[config.COMPONENT_MUST_HAVE] * 100 + weights[config.COMPONENT_EXPERIENCE] * 50) / (
        weights[config.COMPONENT_MUST_HAVE] + weights[config.COMPONENT_EXPERIENCE]
    )
    assert scoring.pre_score(present) == pytest.approx(round(expected, 1))


def test_validation_alone_is_never_a_match() -> None:
    assert scoring.pre_score({config.COMPONENT_VALIDATION: 100.0}) is None


def test_the_pre_score_is_clamped_and_rounded() -> None:
    assert scoring.pre_score({config.COMPONENT_MUST_HAVE: 250.0}) == 100.0
    assert scoring.pre_score({config.COMPONENT_MUST_HAVE: -5.0}) == 0.0
    assert scoring.pre_score({config.COMPONENT_MUST_HAVE: 100 / 3}) == 33.3


def test_nothing_grounded_is_not_assessed_with_the_reason() -> None:
    ctx = _ctx(skills=())
    outcome = _outcome(
        _judgement({}, experience=None, role_fit=None),
        ValidationFit(80.0, MappingProxyType({"notice": "Immediate"})),
        ctx=ctx,
    )
    assert outcome.status == config.STATUS_NOT_ASSESSED
    assert outcome.failure_reason == config.FAILURE_NO_GROUNDED_EVIDENCE
    assert outcome.pre_score is None
    assert outcome.tags == ()


# ── Tags ─────────────────────────────────────────────────────────────────────


def test_tags_positives_first_and_skills_by_id_never_by_name() -> None:
    outcome = _outcome(
        _judgement(
            {MH1: "strong", MH2: "none", NH1: "some"},
            needs=(GroundedNeed("n1", "weakness", "some", "q", "Ran streaming"),),
            negative=(MH2,),
        ),
        ctx=_ctx(needs=(NamedNeed("n1", "weakness", "Nobody runs streaming."),)),
    )
    kinds = [(t["kind"], t["polarity"]) for t in outcome.tags]
    assert kinds == [
        ("skill", "positive"),       # MH1
        ("skill", "positive"),       # NH1
        ("experience", "positive"),
        ("role_fit", "positive"),
        ("company_need", "positive"),
        ("skill", "negative"),       # MH2
    ]
    skill_tags = [t for t in outcome.tags if t["kind"] == "skill"]
    assert [t["skill_id"] for t in skill_tags] == [str(MH1.id), str(NH1.id), str(MH2.id)]
    for tag in skill_tags:
        assert "text" not in tag
        assert MH1.name not in str(dict(tag))


def test_a_none_verdict_item_carries_no_tag() -> None:
    outcome = _outcome(
        _judgement({MH1: "strong"}, experience=GroundedItem("none", None, None))
    )
    assert "experience" not in [t["kind"] for t in outcome.tags]


# ── Provenance ───────────────────────────────────────────────────────────────


def _numeric_leaves(value, path=""):
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _numeric_leaves(v, f"{path}.{k}")]
    if isinstance(value, (list, tuple)):
        return [p for i, v in enumerate(value) for p in _numeric_leaves(v, f"{path}[{i}]")]
    return []


def test_provenance_carries_presence_and_never_a_part_score() -> None:
    outcome = _outcome(
        _judgement({MH1: "strong", MH2: "some", NH1: "none"}),
        ValidationFit(90.0, MappingProxyType({"ctc": "Within range"})),
    )
    provenance = dict(outcome.provenance)
    assert _numeric_leaves(provenance) == [".contract_version"]
    assert provenance["components_present"] == [
        c for c in config.COMPONENTS if c != config.COMPONENT_COMPANY_NEED
    ]
    assert provenance["components_excluded"] == {
        config.COMPONENT_COMPANY_NEED: config.EXCLUDED_NO_NEEDS
    }
    assert provenance["validation_parts"] == {"ctc": "Within range"}
    assert provenance["contract_digest"] == "d" * 64
    assert provenance["model_id"] == "gpt-5.6-terra"
    assert provenance["resume_compensation_redacted"] is True
    assert provenance["degraded"] is False


def test_tags_carry_no_number() -> None:
    outcome = _outcome(_judgement({MH1: "strong", MH2: "some", NH1: "some"}))
    assert _numeric_leaves([dict(t) for t in outcome.tags]) == []


# ── Failures ─────────────────────────────────────────────────────────────────


def test_a_failure_is_not_assessed_with_no_substitute_score() -> None:
    outcome = scoring.failed_outcome(
        _ctx(), uuid.uuid4(), uuid.uuid4(), config.FAILURE_MODEL_UNAVAILABLE,
        model_id="m", prompt_version="v",
    )
    assert outcome.status == config.STATUS_NOT_ASSESSED
    assert outcome.pre_score is None
    assert outcome.tags == ()
    assert outcome.provenance["degraded"] is True
    assert outcome.is_transient_failure


def test_a_missing_resume_is_not_a_degraded_run() -> None:
    outcome = scoring.failed_outcome(
        _ctx(), uuid.uuid4(), None, config.FAILURE_NO_RESUME, model_id="m", prompt_version="v"
    )
    assert outcome.provenance["degraded"] is False
    assert not outcome.is_transient_failure


def test_an_unknown_failure_reason_raises() -> None:
    with pytest.raises(ValueError):
        scoring.failed_outcome(_ctx(), uuid.uuid4(), None, "something", model_id="m", prompt_version="v")


def test_a_transient_failure_keeps_a_prior_result_for_the_same_resume_and_contract() -> None:
    ctx = _ctx()
    profile = uuid.uuid4()
    failure = scoring.failed_outcome(
        ctx, uuid.uuid4(), profile, config.FAILURE_MODEL_OUTPUT_INVALID, model_id="m", prompt_version="v"
    )
    same = scoring.PriorResult(config.STATUS_SCORED, profile, ctx.contract_digest)
    assert scoring.keep_prior(same, failure)
    assert not scoring.keep_prior(None, failure)
    assert not scoring.keep_prior(
        scoring.PriorResult(config.STATUS_SCORED, uuid.uuid4(), ctx.contract_digest), failure
    ), "a different resume makes the old result stale"
    assert not scoring.keep_prior(
        scoring.PriorResult(config.STATUS_SCORED, profile, "e" * 64), failure
    ), "a different contract makes the old result stale"
    assert not scoring.keep_prior(
        scoring.PriorResult(config.STATUS_LEGACY, profile, None), failure
    )
    no_resume = scoring.failed_outcome(
        ctx, uuid.uuid4(), profile, config.FAILURE_NO_RESUME_TEXT, model_id="m", prompt_version="v"
    )
    assert not scoring.keep_prior(same, no_resume), "only a transient failure keeps a prior"
    merged = scoring.merge_failed_attempt({"contract_digest": "x"}, failure)
    assert merged == {"contract_digest": "x", "last_attempt_failed": "model_output_invalid"}


def test_columns_is_the_one_mapping_and_touches_no_history_column() -> None:
    outcome = _outcome(_judgement({MH1: "strong"}))
    now = datetime.now(timezone.utc)
    columns = outcome.columns(scored_at=now)
    assert set(columns) == {
        "yukti_pre_score",
        "yukti_status",
        "yukti_failure_reason",
        "evidence_tags_json",
        "yukti_provenance_json",
        "yukti_scored_at",
        "yukti_profile_id",
    }
    for history in ("match_score", "match_rationale", "match_breakdown_json", "tier", "prescreen_grade"):
        assert history not in columns
    assert columns["yukti_scored_at"] is now
    assert isinstance(columns["evidence_tags_json"], list)
    assert isinstance(columns["yukti_provenance_json"], dict)


# ── The one writer of the Yukti columns ──────────────────────────────────────


def _link(link_id, **columns):
    from types import SimpleNamespace

    return SimpleNamespace(id=link_id, **columns)


def test_apply_outcome_writes_every_yukti_column_and_nothing_else() -> None:
    outcome = _outcome(_judgement({MH1: "strong"}))
    link = _link(outcome.link_id, match_score=81.0, tier="matching")
    now = datetime.now(timezone.utc)
    assert scoring.apply_outcome(link, outcome, now=now) is True
    assert link.yukti_status == config.STATUS_SCORED
    assert link.yukti_pre_score == outcome.pre_score
    assert link.yukti_profile_id == outcome.profile_id
    assert link.yukti_scored_at is now
    assert link.evidence_tags_json and link.yukti_provenance_json["contract_digest"]
    assert link.match_score == 81.0 and link.tier == "matching", "history columns are never written"


def test_apply_outcome_keeps_a_scored_result_through_a_transient_failure() -> None:
    ctx = _ctx()
    profile = uuid.uuid4()
    link_id = uuid.uuid4()
    earlier = datetime(2026, 9, 1, tzinfo=timezone.utc)
    link = _link(
        link_id,
        yukti_status=config.STATUS_SCORED,
        yukti_pre_score=77.5,
        yukti_failure_reason=None,
        evidence_tags_json=[{"kind": "skill"}],
        yukti_provenance_json={"contract_digest": ctx.contract_digest},
        yukti_scored_at=earlier,
        yukti_profile_id=profile,
    )
    failure = scoring.failed_outcome(
        ctx, link_id, profile, config.FAILURE_MODEL_UNAVAILABLE, model_id="m", prompt_version="v"
    )
    assert scoring.apply_outcome(link, failure, now=datetime.now(timezone.utc)) is False
    assert link.yukti_status == config.STATUS_SCORED
    assert link.yukti_pre_score == 77.5
    assert link.yukti_scored_at is earlier
    assert link.evidence_tags_json == [{"kind": "skill"}]
    assert link.yukti_provenance_json == {
        "contract_digest": ctx.contract_digest,
        "last_attempt_failed": config.FAILURE_MODEL_UNAVAILABLE,
    }


def test_apply_outcome_replaces_a_result_the_failure_made_stale() -> None:
    ctx = _ctx()
    link_id = uuid.uuid4()
    link = _link(
        link_id,
        yukti_status=config.STATUS_SCORED,
        yukti_pre_score=77.5,
        yukti_provenance_json={"contract_digest": "e" * 64},
        yukti_profile_id=uuid.uuid4(),
    )
    failure = scoring.failed_outcome(
        ctx, link_id, link.yukti_profile_id, config.FAILURE_MODEL_UNAVAILABLE,
        model_id="m", prompt_version="v",
    )
    assert scoring.apply_outcome(link, failure, now=datetime.now(timezone.utc)) is True
    assert link.yukti_status == config.STATUS_NOT_ASSESSED
    assert link.yukti_pre_score is None
    assert link.yukti_failure_reason == config.FAILURE_MODEL_UNAVAILABLE


def test_prior_of_a_link_never_read_is_none() -> None:
    assert scoring.prior_of(_link(uuid.uuid4())) is None
    assert scoring.prior_of(_link(uuid.uuid4(), yukti_status=config.STATUS_PENDING)) is None
    prior = scoring.prior_of(
        _link(uuid.uuid4(), yukti_status="scored", yukti_profile_id=None, yukti_provenance_json=None)
    )
    assert prior == scoring.PriorResult("scored", None, None)


def test_apply_outcome_refuses_another_links_outcome() -> None:
    outcome = _outcome(_judgement({MH1: "strong"}))
    with pytest.raises(ValueError):
        scoring.apply_outcome(_link(uuid.uuid4()), outcome, now=datetime.now(timezone.utc))
