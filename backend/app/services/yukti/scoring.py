"""From grounded verdicts to one stored outcome per link. Arithmetic only.

THE ARITHMETIC (all of it, and all of it here)
----------------------------------------------
* Parts 1 and 2, Must-have and Nice-to-have evidenced: a priority-weighted
  mean of the verdict values over the bucket's skills, weight `1 / priority`
  (ASSUMPTION, PLAN-p2 Q3: the hiring team's order is the only statement of
  relative importance there is, and a harmonic weight lets the first skill
  lead without silencing the fifth). An empty bucket EXCLUDES the part.
* Parts 3 and 4, experience level and role fit: the verdict value, or excluded
  when grounding excluded the item.
* Part 5, company-need fit: the mean over the needs whose items survived
  grounding; excluded when the job lists no need or none survived.
* Part 6, validation fit: `validation_fit.score`, excluded when nothing was
  answered.
* The PRE-ASSESSMENT score is the weighted mean over the PRESENT parts with the
  weights renormalised, rounded to one decimal and clamped to 0..100. When none
  of parts one to five survived, the link is `not_assessed` with the reason
  `no_grounded_evidence`: part six alone is application paperwork, and ranking
  a candidate on their notice period with nothing read from their resume would
  present a form as a match.

WHAT IS STORED, AND WHAT IS NEVER STORED
----------------------------------------
`YuktiOutcome.columns` is the one mapping onto the link's Yukti columns
(migration phase2_yukti, Phase 2 WP-B): the pre score, the status, the failure
reason, the evidence tags, the provenance, the time and the profile that was
read. It NEVER writes `match_score`, `match_rationale`, `match_breakdown_json`,
`tier` or `prescreen_grade`: those are history, readable and frozen.

The provenance records which parts were PRESENT and why the others were
EXCLUDED, in words, and never a part's number. A JSON document holding six
sub-scores is one careless projection away from a client, so the only numeric
leaf it may carry is the contract version (`tests/test_yukti_scoring.py`
walks it). The blended ranking score is not stored at all: it is derived in
SQL at read time from this pre score, the Miti report and the tenant's ratio
(CONTRACT v2), so it can never go stale.

A FAILURE NEVER OVERWRITES A GOOD RESULT
----------------------------------------
A model outage or twice-malformed output says nothing about the candidate. When
the link already holds a `scored` result for the SAME resume and the SAME
contract, `keep_prior` says so and the caller keeps it, recording only that
the latest attempt failed. Otherwise the link reads `not_assessed`, which the
ranked table shows in words ("Not assessed"), never as a silently unscored row.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import locks
from app.services.assessment_contract import BUCKET_MUST_HAVE, BUCKET_NICE_TO_HAVE
from app.services.yukti import config, grounding, inputs, validation_fit
from app.services.yukti.grounding import GroundedJudgement

logger = logging.getLogger(__name__)

__all__ = [
    "PriorResult",
    "ScoreSummary",
    "YuktiOutcome",
    "component_scores",
    "failed_outcome",
    "keep_prior",
    "merge_failed_attempt",
    "outcome_from_judgement",
    "pre_score",
    "score_links",
]


# ── Pure arithmetic ──────────────────────────────────────────────────────────


def _bucket_score(judgement: GroundedJudgement, bucket: str) -> float | None:
    entries = [g for g in judgement.skills if g.skill.bucket == bucket]
    if not entries:
        return None
    weighted = 0.0
    total = 0.0
    for entry in entries:
        weight = 1.0 / max(1, int(entry.skill.priority))
        weighted += weight * config.VERDICT_VALUES[entry.verdict]
        total += weight
    return weighted / total * 100.0


def component_scores(
    judgement: GroundedJudgement,
    validation: validation_fit.ValidationFit,
    *,
    needs_listed: bool,
) -> tuple[dict[str, float], dict[str, str]]:
    """(present parts -> 0..100, excluded parts -> reason word). Pure."""
    present: dict[str, float] = {}
    excluded: dict[str, str] = {}

    for component, bucket in (
        (config.COMPONENT_MUST_HAVE, BUCKET_MUST_HAVE),
        (config.COMPONENT_NICE_TO_HAVE, BUCKET_NICE_TO_HAVE),
    ):
        value = _bucket_score(judgement, bucket)
        if value is None:
            excluded[component] = config.EXCLUDED_NO_SKILLS
        else:
            present[component] = value

    for component, item in (
        (config.COMPONENT_EXPERIENCE, judgement.experience),
        (config.COMPONENT_ROLE_FIT, judgement.role_fit),
    ):
        if item is None:
            excluded[component] = config.EXCLUDED_UNGROUNDED
        else:
            present[component] = config.VERDICT_VALUES[item.verdict] * 100.0

    if not needs_listed:
        excluded[config.COMPONENT_COMPANY_NEED] = config.EXCLUDED_NO_NEEDS
    elif not judgement.needs:
        excluded[config.COMPONENT_COMPANY_NEED] = config.EXCLUDED_UNGROUNDED
    else:
        present[config.COMPONENT_COMPANY_NEED] = (
            sum(config.VERDICT_VALUES[n.verdict] for n in judgement.needs)
            / len(judgement.needs)
            * 100.0
        )

    if validation.score is None:
        excluded[config.COMPONENT_VALIDATION] = config.EXCLUDED_NOT_ANSWERED
    else:
        present[config.COMPONENT_VALIDATION] = validation.score

    return present, excluded


def pre_score(present: Mapping[str, float]) -> float | None:
    """The renormalised weighted mean over the present parts, 0..100, or None
    when none of the model-judged parts is present."""
    if not any(component in present for component in config.MODEL_COMPONENTS):
        return None
    total_weight = sum(config.COMPONENT_WEIGHTS[c] for c in present)
    weighted = sum(config.COMPONENT_WEIGHTS[c] * v for c, v in present.items())
    return round(min(100.0, max(0.0, weighted / total_weight)), 1)


# ── Tags ─────────────────────────────────────────────────────────────────────


def _tags(judgement: GroundedJudgement) -> list[dict[str, Any]]:
    """Positives first (Must-have by priority, Nice-to-have, experience, role
    fit, company needs), negatives after. A skill tag carries the skill ID,
    never its name: the name is resolved from the live row when it is read."""
    tags: list[dict[str, Any]] = []
    for bucket in (BUCKET_MUST_HAVE, BUCKET_NICE_TO_HAVE):
        for entry in judgement.skills:
            if entry.skill.bucket != bucket or entry.verdict == config.VERDICT_NONE:
                continue
            tags.append(
                {
                    "kind": config.TAG_KIND_SKILL,
                    "skill_id": str(entry.skill.id),
                    "polarity": config.POLARITY_POSITIVE,
                    "strength": entry.verdict,
                    "quote": entry.quote,
                }
            )
    for kind, item in (
        (config.TAG_KIND_EXPERIENCE, judgement.experience),
        (config.TAG_KIND_ROLE_FIT, judgement.role_fit),
    ):
        if item is not None and item.tag and item.verdict != config.VERDICT_NONE:
            tags.append(
                {
                    "kind": kind,
                    "text": item.tag,
                    "polarity": config.POLARITY_POSITIVE,
                    "strength": item.verdict,
                    "quote": item.quote,
                }
            )
    for need in judgement.needs:
        if need.tag and need.verdict != config.VERDICT_NONE:
            tags.append(
                {
                    "kind": config.TAG_KIND_COMPANY_NEED,
                    "text": need.tag,
                    "need_source": need.source,
                    "polarity": config.POLARITY_POSITIVE,
                    "strength": need.verdict,
                    "quote": need.quote,
                }
            )
    for skill in judgement.negative_skills:
        tags.append(
            {
                "kind": config.TAG_KIND_SKILL,
                "skill_id": str(skill.id),
                "polarity": config.POLARITY_NEGATIVE,
            }
        )
    return tags


# ── The outcome ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class YuktiOutcome:
    """One link's result, ready to be written onto its Yukti columns."""

    link_id: uuid.UUID
    profile_id: uuid.UUID | None
    status: str
    pre_score: float | None
    failure_reason: str | None
    tags: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any]

    @property
    def is_transient_failure(self) -> bool:
        return (
            self.status == config.STATUS_NOT_ASSESSED
            and self.failure_reason in config.TRANSIENT_FAILURES
        )

    def columns(self, *, scored_at: datetime) -> dict[str, Any]:
        """The link's Yukti columns, and nothing else. The ONE mapping."""
        return {
            "yukti_pre_score": self.pre_score,
            "yukti_status": self.status,
            "yukti_failure_reason": self.failure_reason,
            "evidence_tags_json": [dict(tag) for tag in self.tags],
            "yukti_provenance_json": dict(self.provenance),
            "yukti_scored_at": scored_at,
            "yukti_profile_id": self.profile_id,
        }


def _base_provenance(ctx: inputs.JobContext, *, model_id: str, prompt_version: str) -> dict[str, Any]:
    return {
        "contract_digest": ctx.contract_digest,
        "contract_version": ctx.contract_version,
        "model_id": model_id,
        "prompt_version": prompt_version,
        "task_type": config.TASK_TYPE,
    }


def outcome_from_judgement(
    ctx: inputs.JobContext,
    candidate: inputs.CandidateInput,
    judgement: GroundedJudgement,
    validation: validation_fit.ValidationFit,
    *,
    model_id: str,
    prompt_version: str,
) -> YuktiOutcome:
    """A grounded reading and the application answers, as a stored outcome."""
    present, excluded = component_scores(
        judgement, validation, needs_listed=bool(ctx.needs)
    )
    score = pre_score(present)
    provenance = _base_provenance(ctx, model_id=model_id, prompt_version=prompt_version)
    provenance.update(
        {
            "components_present": [c for c in config.COMPONENTS if c in present],
            "components_excluded": {
                c: excluded[c] for c in config.COMPONENTS if c in excluded
            },
            "validation_parts": dict(validation.parts),
            "ungrounded": [dict(item) for item in judgement.ungrounded],
            "contradicted_negative": list(judgement.contradicted_negative),
            "tags_refused": [dict(item) for item in judgement.tags_refused],
            "unknown_needs": list(judgement.unknown_needs),
            "resume_compensation_redacted": candidate.redacted,
            "resume_truncated": candidate.truncated,
            "resume_instruction_neutralised": candidate.neutralised,
            "degraded": False,
        }
    )
    if score is None:
        return YuktiOutcome(
            link_id=candidate.link_id,
            profile_id=candidate.profile_id,
            status=config.STATUS_NOT_ASSESSED,
            pre_score=None,
            failure_reason=config.FAILURE_NO_GROUNDED_EVIDENCE,
            tags=(),
            provenance=MappingProxyType(provenance),
        )
    return YuktiOutcome(
        link_id=candidate.link_id,
        profile_id=candidate.profile_id,
        status=config.STATUS_SCORED,
        pre_score=score,
        failure_reason=None,
        tags=tuple(MappingProxyType(tag) for tag in _tags(judgement)),
        provenance=MappingProxyType(provenance),
    )


def failed_outcome(
    ctx: inputs.JobContext,
    link_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    reason: str,
    *,
    model_id: str,
    prompt_version: str,
) -> YuktiOutcome:
    """`not_assessed` for a link, with its reason. NEVER a substitute score:
    a model failure is "Not assessed" (acceptance criterion 6)."""
    if reason not in config.FAILURE_REASONS:
        raise ValueError(f"unknown Yukti failure reason {reason!r}")
    provenance = _base_provenance(ctx, model_id=model_id, prompt_version=prompt_version)
    provenance["degraded"] = reason in config.TRANSIENT_FAILURES
    return YuktiOutcome(
        link_id=link_id,
        profile_id=profile_id,
        status=config.STATUS_NOT_ASSESSED,
        pre_score=None,
        failure_reason=reason,
        tags=(),
        provenance=MappingProxyType(provenance),
    )


@dataclass(frozen=True)
class PriorResult:
    """What a link already holds, read by the caller from its columns."""

    status: str
    profile_id: uuid.UUID | None
    contract_digest: str | None


def keep_prior(prior: PriorResult | None, outcome: YuktiOutcome) -> bool:
    """Should `outcome` leave the link's existing result in place?

    Only a TRANSIENT failure over a `scored` result for the same resume and the
    same contract. A changed resume or a changed contract makes the old result
    stale, and then the honest state is `not_assessed` until a run succeeds.
    """
    if prior is None or not outcome.is_transient_failure:
        return False
    return (
        prior.status == config.STATUS_SCORED
        and prior.profile_id == outcome.profile_id
        and prior.contract_digest == outcome.provenance.get("contract_digest")
    )


def merge_failed_attempt(
    prior_provenance: Mapping[str, Any] | None, outcome: YuktiOutcome
) -> dict[str, Any]:
    """The kept result's provenance, stamped with the attempt that failed."""
    merged = dict(prior_provenance or {})
    merged["last_attempt_failed"] = outcome.failure_reason
    return merged


# ── The run over many links ──────────────────────────────────────────────────


@dataclass
class ScoreSummary:
    """What one `score_links` call produced, for the caller to persist."""

    outcomes: dict[uuid.UUID, YuktiOutcome] = field(default_factory=dict)
    skipped_locked: list[uuid.UUID] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return any(o.is_transient_failure for o in self.outcomes.values())

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes.values() if o.status == status)


async def score_links(
    db: AsyncSession,
    job: Any,
    pairs: Sequence[tuple[Any, Any | None]],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> ScoreSummary:
    """Judge every (link, profile) pair and return one outcome per link.

    WRITES NOTHING. The caller (the matching run, the per-profile rescore)
    persists `outcome.columns(...)` in the SAME transaction, because the
    per-link advisory lock taken here is transaction scoped: it is held until
    that transaction commits, which is exactly the window in which a second
    scorer of the same link must stand aside (`locks.YUKTI_LINK`). A link whose
    lock is held is skipped and listed, never waited on: the holder is doing
    this link's work already.

    The profile paired with a link MUST be the one the link was made with;
    `inputs.candidate_input` refuses anything else.
    """
    from app.config import llm_providers  # noqa: PLC0415
    from app.prompts import registry  # noqa: PLC0415
    from app.services.yukti import judge  # noqa: PLC0415

    summary = ScoreSummary()
    model_id = llm_providers.model_for(config.TASK_TYPE)
    prompt_version = registry.version(config.PROMPT_NAME)
    ctx = await inputs.job_context(db, job)
    needs_by_ref = {need.ref: need for need in ctx.needs}
    compensation = getattr(job, "compensation_json", None)

    unique: dict[uuid.UUID, tuple[Any, Any | None]] = {}
    for link, profile in pairs:
        unique.setdefault(link.id, (link, profile))

    ready: list[tuple[Any, inputs.CandidateInput]] = []
    for link, profile in unique.values():
        if not await locks.try_advisory_lock(db, locks.YUKTI_LINK, link.id):
            summary.skipped_locked.append(link.id)
            continue
        prepared = await inputs.candidate_input(db, ctx, link, profile)
        if isinstance(prepared, inputs.NotEvaluable):
            summary.outcomes[link.id] = failed_outcome(
                ctx,
                link.id,
                prepared.profile_id,
                prepared.reason,
                model_id=model_id,
                prompt_version=prompt_version,
            )
            continue
        ready.append((link, prepared))

    total = len(ready)
    done = 0
    for start in range(0, total, config.BATCH_SIZE):
        batch = ready[start : start + config.BATCH_SIZE]
        results = await judge.judge_batch(db, ctx, [c for _, c in batch])
        for link, candidate in batch:
            result = results[candidate.link_id]
            if isinstance(result, judge.JudgeFailure):
                summary.outcomes[link.id] = failed_outcome(
                    ctx,
                    link.id,
                    candidate.profile_id,
                    result.reason,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )
                continue
            grounded = grounding.ground(
                ctx.skills, needs_by_ref, result, candidate.resume_text
            )
            summary.outcomes[link.id] = outcome_from_judgement(
                ctx,
                candidate,
                grounded,
                validation_fit.score(getattr(link, "validation_json", None), compensation),
                model_id=model_id,
                prompt_version=prompt_version,
            )
        done += len(batch)
        if on_progress is not None:
            on_progress(done, total)

    logger.info(
        "yukti.score_links job_id=%s scored=%d not_assessed=%d skipped_locked=%d degraded=%s",
        getattr(job, "id", None),
        summary.count(config.STATUS_SCORED),
        summary.count(config.STATUS_NOT_ASSESSED),
        len(summary.skipped_locked),
        summary.degraded,
    )
    return summary
