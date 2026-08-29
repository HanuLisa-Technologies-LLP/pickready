"""Yukti publishes an `ai_score` artifact, and neither half may cost the other.

There are exactly two ways this change could have been wrong, and both of them
are the kind that ship green.

THE FIRST IS THAT A NUMBER ESCAPES.
The artifact is the richest thing matching has ever produced about a candidate:
retrieval ranks, a routing policy, a category list, a document digest. Siddhi
declares it consumes `ai_score`, and Siddhi is the agent that writes the
document a client keeps. So the assertion is not "the code looks careful" but
that the three surfaces a client can actually reach -- `client_breakdown`,
`ranking_payload` and the report schema -- carry no number and no field the
artifact introduced. Note what `client_breakdown` really does: it strips
NUMBERS, not FIELDS, so a `{"fusion_rank": 3}` block added to
`match_breakdown_json` would have walked straight through it. That is why the
artifact is built beside the stored row rather than out of it, and why the tests
below check the stored row too.

THE SECOND IS THAT MATCHING BREAKS.
Matching worked before artifacts existed, a recruiter watches it run, and by the
time the hand-off happens the rows are committed. A gate that raises, a contract
that refuses, an agents package that will not import -- each of those must cost
the hand-off and nothing else. Injecting the exception is the only way to
demonstrate that, because the guard is invisible on the happy path.

The run tests drive `run_matching` end to end against fakes rather than a
database, for the same reason `test_matching.py` does: what is being asserted is
which values the pipeline computes and hands on, and a real Postgres would add
an environment dependency without adding an assertion.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

import pytest
from sqlalchemy.sql.elements import TextClause

from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.services import llm_router, matching, matching_categories
from app.services.agents import artifacts, gates, identity
from app.services.matching import client_breakdown, ranking_payload
from app.services.verification import base as verification

TENANT = uuid.uuid4()
JOB_ID = uuid.uuid4()

# 28 words, inside the 25-30 word contract, so `enforce_word_range` returns it
# untouched and the artifact's explanation is provably the string the recruiter
# read rather than a repaired variant of it.
GOOD_COMMENT = (
    "Candidate demonstrates strong practical command of the core technologies this "
    "role requires, with directly comparable delivery experience, though a few "
    "secondary tools remain unevidenced in the submitted resume."
)

#: Five, because `gates.MIN_MATCHING_CATEGORIES` is five. A job on the four
#: legacy keys is covered separately and deliberately.
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("skills_match", "Skills present", "Whether the resume indicates the required skills."),
    ("behavioural_signal", "Behavioural signal", "What the resume's own language suggests."),
    ("experience_relevance", "Experience level", "Same function, at a comparable level."),
    ("role_alignment", "Role and designation alignment", "Duties over titles."),
    ("education_fit", "Education", "Degree level and specialisation."),
)

#: A distinctive internal score, so a test asserting it did not cross a boundary
#: is asserting something a stray 5 or 8 could not accidentally satisfy.
SENTINEL_SCORE = 7


def _breakdown(keys: tuple[str, ...] = tuple(k for k, _, _ in CATEGORIES)) -> dict:
    out: dict[str, Any] = {
        key: {"score": SENTINEL_SCORE, "comment": GOOD_COMMENT} for key in keys
    }
    out["overall"] = {
        "score": matching.compute_overall_score(
            {key: SENTINEL_SCORE for key in keys}, keys
        ),
        "comment": GOOD_COMMENT,
    }
    out["scoring_mode"] = "llm"
    return out


def _job() -> Job:
    job = Job()
    job.id = JOB_ID
    job.tenant_id = TENANT
    job.title = "Staff Platform Engineer"
    job.department = "Platform"
    job.jd_markdown = "Runs the streaming platform."
    job.jd_json = {"skills": ["kafka", "terraform"]}
    job.assessment_grade = "non_managerial"
    return job


def _profile(candidate_id: uuid.UUID | None = None) -> Profile:
    profile = Profile()
    profile.id = uuid.uuid4()
    profile.candidate_id = candidate_id or uuid.uuid4()
    profile.resume_text = "Ten years of Kafka and Terraform."
    profile.parsed_fields_json = {
        "skills": ["kafka", "terraform"],
        "employment_history": [{"title": "SRE"}],
        "education": ["BE"],
    }
    profile.resume_sha256 = "a" * 64
    profile.resume_public_id = "resumes/abc123"
    return profile


def _link(profile: Profile) -> JobCandidateLink:
    link = JobCandidateLink()
    link.id = uuid.uuid4()
    link.tenant_id = TENANT
    link.job_id = JOB_ID
    link.candidate_id = profile.candidate_id
    link.profile_id = profile.id
    link.source_type = "applied"
    return link


def _stages(profile_ids: list[uuid.UUID]) -> dict[str, Any]:
    return {
        "semantic_ids": profile_ids,
        "keyword_ids": profile_ids[:1],
        "linked_ids": profile_ids,
        "fusion_order": profile_ids,
        "top_n": 50,
        "semantic_ran": True,
    }


def _publish_one(
    *,
    categories: tuple[tuple[str, str, str], ...] = CATEGORIES,
    breakdown: dict | None = None,
) -> Any:
    job, profile = _job(), _profile()
    link = _link(profile)
    published = matching.publish_ai_scores(
        job,
        [(profile, link, breakdown if breakdown is not None else _breakdown(
            tuple(k for k, _, _ in categories)
        ))],
        categories=categories,
        stages=_stages([profile.id]),
    )
    assert published, "the fixture published nothing at all"
    return published[0]


# ── generic walkers ──────────────────────────────────────────────────────────


def _numbers(value: Any, path: str = "") -> list[str]:
    """Every int/float anywhere in `value`, by the path that reaches it.

    Booleans are excluded: `True` is an int in Python and a flag is not a score.
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [path or "<root>"]
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.extend(_numbers(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_numbers(item, f"{path}[{index}]"))
    return found


def _keys(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            out.add(str(key))
            out |= _keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            out |= _keys(item)
    return out


# ── 1. NO NUMBER, AND NO ENGINEERING METADATA, REACHES A CLIENT ──────────────


def test_the_client_projections_carry_no_number_at_all() -> None:
    """The product rule, stated against the two functions that enforce it.

    Both are called on the raw stored breakdown, which is where the internal
    1-10 score lives. If either leaked one, every candidate row in the review
    screen would be carrying it.
    """
    stored = _breakdown()
    for name, view in (
        ("client_breakdown", client_breakdown(stored)),
        ("ranking_payload", ranking_payload(stored)),
    ):
        assert _numbers(view) == [], f"{name} returned a number"


#: The one engineering key a client surface is ALLOWED to carry, and it predates
#: this work: `client_breakdown` keeps `scoring_mode` on purpose, so the review
#: screen can tell an LLM score from a degraded retrieval one. Listed rather
#: than silently excluded, because an exception nobody wrote down is one the
#: next person widens.
_SHARED_WITH_THE_REVIEW_SCREEN = {"scoring_mode"}


def _engineering_keys(payload: Mapping[str, Any]) -> set[str]:
    """Every field name the artifact introduces that is engineering state.

    The two metadata subtrees plus the run-identity fields. Deliberately NOT
    "every key not already in the breakdown", for two reasons: the artifact
    restates `name` and `key` per category, which are the recruiter-facing
    headings a client is supposed to see, and it restates the scope identifiers
    (`candidate_id`, `job_id`, `profile_id`, `job_candidate_link_id`), which a
    report already carries because an id identifies a row and authorises
    nothing.
    """
    return (
        _keys({"retrieval": payload["retrieval"], "model": payload["model"]})
        | _keys(payload["provenance"])
        | {
            "evidence_refs",
            "resume_sections",
            "resume_parsed",
            "inferred_fields",
            "jd_version",
            "artifact_version",
            "correlation_id",
        }
    ) - _SHARED_WITH_THE_REVIEW_SCREEN


def test_no_engineering_field_the_artifact_introduces_reaches_a_client() -> None:
    """The failure `client_breakdown` cannot catch.

    It strips values that are numbers; it does not strip fields. Had the
    retrieval and routing metadata been added to `match_breakdown_json` instead
    of to a separate artifact, `{"retrieval": {"fusion_rank": 3}}` would have
    survived it intact and been served to a client through
    `MatchResultOut.breakdown`.
    """
    engineering = _engineering_keys(_publish_one().payload)
    assert {"retrieval", "model", "fusion_rank", "provider_order"} <= engineering, (
        "the fixture is not exercising the metadata this test exists for"
    )
    stored = _breakdown()
    for name, view in (
        ("client_breakdown", client_breakdown(stored)),
        ("ranking_payload", ranking_payload(stored)),
    ):
        leaked = engineering & _keys(view)
        assert not leaked, f"{name} exposes artifact-only fields: {sorted(leaked)}"


def test_no_engineering_VALUE_from_the_artifact_appears_on_a_client_surface() -> None:
    """Key names are the cheap half; the values are what a reader would see."""
    payload = _publish_one().payload
    served = json.dumps(
        {
            "breakdown": client_breakdown(_breakdown()),
            "ranking": ranking_payload(_breakdown()),
        },
        default=str,
    )
    for token in (
        payload["jd_version"],
        payload["correlation_id"],
        payload["model"]["task_type"],
        *payload["model"]["provider_order"],
        # One vendor, so the trace now records the MODEL rather than a
        # per-provider candidate table with one row in it. The rule under test
        # is unchanged: no engineering value from the artifact may appear on a
        # client-facing projection.
        payload["model"]["model"],
        "fusion_rank",
        "exact_match",
        "resume_sha256",
    ):
        assert token not in served, f"{token!r} reached a client-facing projection"


def test_the_internal_category_score_does_not_cross_the_agent_boundary() -> None:
    """A grade crosses as a WORD.

    Same argument `ppi._requirement_word` makes: the point at which an integer
    stops being convertible is the point at which somebody renders it, and the
    declared consumer of this artifact is the agent that writes the report.
    """
    payload = _publish_one().payload
    assert "score" not in _keys(payload)
    # The assessment half of the payload holds no number of any kind. Asserted
    # structurally rather than by searching the serialised text for the sentinel
    # digit, because a uuid contains every digit and a test that cannot fail
    # cleanly is one somebody eventually deletes.
    assert _numbers(payload["categories"]) == []
    assert _numbers(payload["overall"]) == []
    # Every number in the artifact lives in one of three places, none of which
    # says anything about the candidate: a position in a retrieval list, the
    # sampling temperature the call was made at, and the contract version.
    assert {path.split(".")[0] for path in _numbers(payload)} <= {
        "retrieval",
        "model",
        "artifact_version",
    }
    grades = {item["grade"] for item in payload["categories"]}
    assert grades and grades <= set(matching.MATCHING_LABELS)
    assert payload["overall"]["grade"] in matching.MATCHING_LABELS


def test_the_report_schema_has_nowhere_for_this_metadata_to_land() -> None:
    """The third client surface, and the one furthest from this module.

    A PRISM report renders an AI Score item as a grade and a remark. Asserted
    against the schema rather than a rendered report because a field is what
    makes a leak possible: prose can be reviewed, a field is served.
    """
    from app.schemas.assessments import DimensionOut, FunctionalReportOut

    assert set(DimensionOut.model_fields) == {
        "name",
        "description",
        "grade",
        "required_level",
        "remark",
    }
    for name, field in DimensionOut.model_fields.items():
        assert field.annotation in (str, str | None), (
            f"DimensionOut.{name} is not a word-or-nothing field"
        )
    engineering = _engineering_keys(_publish_one().payload)
    for model in (DimensionOut, FunctionalReportOut):
        collision = engineering & set(model.model_fields)
        assert not collision, f"{model.__name__} could carry {sorted(collision)}"


# ── 2. PUBLISHING IS ADDITIVE AND NON-FATAL ──────────────────────────────────


@pytest.mark.parametrize(
    "target,attribute",
    [
        (gates, "run_gate"),
        (artifacts, "publish"),
        (matching, "_ai_score_payload"),
        (matching, "_retrieval_evidence"),
    ],
)
@pytest.mark.asyncio
async def test_a_failure_anywhere_in_the_handoff_leaves_the_run_intact(
    monkeypatch: pytest.MonkeyPatch, target: Any, attribute: str
) -> None:
    """Every step of the hand-off, made to raise in turn.

    The run is already committed when this executes, so the failure being
    prevented is a finished run reported as a failure and redone from the top by
    the Celery retry policy -- re-embedding the JD and re-spending the model
    calls to produce identical rows.
    """
    def _boom(*_args, **_kwargs):
        raise RuntimeError("the hand-off is broken")

    monkeypatch.setattr(target, attribute, _boom)
    harness = _Harness(count=3)
    scored = await harness.run(monkeypatch)
    assert scored == 3
    assert harness.session.commits == 1
    assert len(harness.session.written) == 3


@pytest.mark.asyncio
async def test_the_agents_package_failing_to_import_leaves_the_run_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The import lives inside the guard, unlike Bodha's and Sutra's.

    Those two publish from a request handler that has already flushed its own
    work; this one runs at the tail of a committed background run, so an
    ImportError has to cost the hand-off and not the run.
    """
    import builtins

    real_import = builtins.__import__

    def _refuse(name, *args, **kwargs):
        if name == "app.services.agents":
            raise ImportError("no agents here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _refuse)
    harness = _Harness(count=2)
    scored = await harness.run(monkeypatch)
    assert scored == 2


def test_one_failing_candidate_does_not_discard_the_others() -> None:
    """Per candidate, for the same reason a databank bulk upload allows partial
    success: one unreadable profile must not cost the other forty-nine their
    hand-off."""
    job = _job()
    rows = []
    for _ in range(3):
        profile = _profile()
        rows.append((profile, _link(profile), _breakdown()))
    doomed = rows[1][0].id

    real_publish = artifacts.publish

    def _selective(**kwargs):
        if kwargs["payload"]["profile_id"] == str(doomed):
            raise RuntimeError("this one is broken")
        return real_publish(**kwargs)

    try:
        artifacts.publish = _selective  # type: ignore[assignment]
        published = matching.publish_ai_scores(
            job,
            rows,
            categories=CATEGORIES,
            stages=_stages([row[0].id for row in rows]),
        )
    finally:
        artifacts.publish = real_publish  # type: ignore[assignment]

    assert len(published) == 2
    assert doomed not in {uuid.UUID(a.payload["profile_id"]) for a in published}


def test_publish_ai_scores_itself_contains_nothing_that_can_raise() -> None:
    """The guarantee the call site depends on, asserted on the source.

    `run_matching` calls this after its commit and does NOT wrap it, on the
    grounds that every raising statement is inside `_publish_one_ai_score`'s
    try. A future edit that adds a bare call here would silently remove that.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(matching.publish_ai_scores)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called <= {"_publish_one_ai_score"}, (
        f"publish_ai_scores calls unguarded helpers: {sorted(called)}"
    )


# ── 3. PRODUCT OUTPUT IS UNCHANGED ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_run_produces_identical_output_with_publishing_on_and_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Everything a recruiter, a report or a sort order can see, compared.

    Two runs over identical fixtures, one with the hand-off in place and one
    with it removed entirely. The stored breakdown, the 0-100 match score, the
    tier and the rationale must agree exactly, because a hand-off that moved any
    of them would have changed a grade to add a trace.
    """
    with_publish = await _Harness(count=3, seed=1).run_capturing(monkeypatch)

    monkeypatch.setattr(matching, "publish_ai_scores", lambda *a, **k: [])
    without_publish = await _Harness(count=3, seed=1).run_capturing(monkeypatch)

    assert with_publish == without_publish


@pytest.mark.asyncio
async def test_the_handoff_never_mutates_the_breakdown_that_was_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The breakdown is a live dict the storage loop already wrote from.

    `enforce_breakdown_comments` mutates in place, so a payload builder that
    reused that habit would edit a row after it had been committed -- and the
    edit would appear on the next read, not this one.
    """
    stored = _breakdown()
    before = json.dumps(stored, sort_keys=True, default=str)
    _publish_one(breakdown=stored)
    assert json.dumps(stored, sort_keys=True, default=str) == before


# ── 4. TENANT SCOPE TRAVELS, AND IS COMPARED ARITHMETICALLY ──────────────────


def test_siddhi_can_verify_and_read_a_published_ai_score() -> None:
    artifact = _publish_one()
    verdict = artifacts.verify_for_consumer(
        artifact, identity.SIDDHI, tenant_id=str(TENANT), job_id=str(JOB_ID)
    )
    assert verdict.passed, verdict.as_dict()


def test_a_consumer_in_another_tenant_is_refused() -> None:
    """Two strings, compared. Never inferred, never asked of a model."""
    artifact = _publish_one()
    assert artifact.tenant_id == str(TENANT)
    verdict = artifacts.verify_for_consumer(
        artifact, identity.SIDDHI, tenant_id=str(uuid.uuid4()), job_id=str(JOB_ID)
    )
    assert not verdict.passed
    issues = {f.issue for f in verdict.by_severity(verification.SEVERITY_HIGH)}
    assert "tenant_scope_mismatch" in issues


def test_an_artifact_from_another_job_is_refused() -> None:
    """A candidate is matched against ONE job's categories, so reading another
    job's `ai_score` would state a fit against criteria nobody applied."""
    verdict = artifacts.verify_for_consumer(
        _publish_one(), identity.SIDDHI, tenant_id=str(TENANT), job_id=str(uuid.uuid4())
    )
    assert not verdict.passed


def test_an_agent_that_does_not_declare_it_consumes_ai_score_is_refused() -> None:
    """Reach a future prompt does not have is reach it cannot start using."""
    verdict = artifacts.verify_for_consumer(
        _publish_one(), identity.VAADA, tenant_id=str(TENANT)
    )
    assert not verdict.passed
    issues = {f.issue for f in verdict.by_severity(verification.SEVERITY_HIGH)}
    assert "consumer_not_permitted" in issues


# ── 5. NO LLM CALL DECIDES A GATE ────────────────────────────────────────────


def _code_lines(function: Any) -> list[str]:
    """The function's executable lines, with comments and docstrings removed.

    Same posture as `test_no_canned_acknowledgments_in_the_conversation_path`:
    checked against CODE only, so the comment explaining why the provider that
    answered is not recorded may still name `llm_router.chat_completion`.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.unparse(tree).splitlines()


def test_nothing_in_the_publish_path_calls_a_model() -> None:
    """The moment the guard matters most is the moment the provider is down.

    Asserted on the source of every function in the path, because an awaited
    call added later would be invisible from the outside: the gate would simply
    start failing during an outage, which is exactly when it is needed.
    """
    for function in (
        matching.publish_ai_scores,
        matching._publish_one_ai_score,
        matching._ai_score_payload,
        matching._retrieval_evidence,
        matching._model_metadata,
        gates.yukti_gate,
    ):
        code = "\n".join(_code_lines(function))
        for banned in ("chat_completion", "invoke_llm", "await ", "async def"):
            assert banned not in code, (
                f"{function.__name__} reaches for a model via {banned!r}"
            )


@pytest.mark.asyncio
async def test_the_handoff_still_publishes_with_the_whole_llm_chain_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The degraded run is the one a consumer most needs told about.

    A `retrieval_fallback` breakdown was ordered by document similarity and
    never read by a model. It still publishes -- refusing would leave Siddhi
    unable to tell a degraded score from a candidate who was never matched --
    and `model.scoring_mode` is the field that keeps that honest.
    """
    async def _down(*_args, **_kwargs):
        raise llm_router.LLMUnavailableError("every provider is dark")

    monkeypatch.setattr(llm_router, "chat_completion", _down)
    harness = _Harness(count=2, real_scoring=True)
    scored = await harness.run(monkeypatch)

    assert scored == 2
    assert len(harness.published) == 2
    for artifact in harness.published:
        assert artifact.payload["model"]["scoring_mode"] == "retrieval_fallback"
        assert artifact.payload["model"]["provider"] is None


# ── 6. THE CONTRACT ITSELF ───────────────────────────────────────────────────


def test_the_artifact_is_the_declared_type_from_the_declared_producer() -> None:
    artifact = _publish_one()
    assert artifact.producer == identity.YUKTI
    assert artifact.artifact_type == "ai_score"
    assert "ai_score" in identity.get(identity.YUKTI).produces
    assert artifact.version == matching.AI_SCORE_ARTIFACT_VERSION >= 1
    assert artifact.provenance_complete
    assert artifact.validated


def test_the_payload_carries_every_field_the_hand_off_promises() -> None:
    """One assertion per item the brief names, so a field quietly dropped in a
    later edit fails here rather than at the consumer."""
    artifact = _publish_one()
    payload = artifact.payload

    assert payload["candidate_id"] == artifact.candidate_id
    assert payload["job_id"] == str(JOB_ID)
    assert payload["job_candidate_link_id"]

    category = payload["categories"][0]
    assert category["name"] == CATEGORIES[0][1]
    assert category["requirement"] == CATEGORIES[0][2]
    assert category["explanation"] == GOOD_COMMENT
    assert any(ref.startswith("profiles:") for ref in category["evidence"])
    assert any(ref.startswith("resume_sha256:") for ref in category["evidence"])
    assert set(category["evidence_basis"]) == {
        matching.RETRIEVAL_SEMANTIC,
        matching.RETRIEVAL_EXACT_MATCH,
        matching.RETRIEVAL_LINKED,
    }

    retrieval = payload["retrieval"]
    assert retrieval[matching.RETRIEVAL_SEMANTIC]["hit"] is True
    assert retrieval[matching.RETRIEVAL_SEMANTIC]["stage_ran"] is True
    assert retrieval[matching.RETRIEVAL_EXACT_MATCH]["hit"] is True
    assert retrieval["fusion_rank"] == 0

    assert payload["model"]["task_type"] == matching._SCORING_TASK
    assert payload["model"]["provider_order"]
    assert payload["provenance"]["producer"] == identity.YUKTI
    assert payload["provenance"]["pass"] == "resume_only"
    assert payload["provenance"]["scored_at"]
    assert payload["jd_version"]
    assert payload["artifact_version"] == matching.AI_SCORE_ARTIFACT_VERSION
    assert payload["correlation_id"]


def test_a_skipped_semantic_stage_is_told_apart_from_a_resume_that_did_not_match() -> None:
    """`hit: false` with no stage flag would be recorded as evidence the resume
    failed to match on meaning, when in fact nothing looked."""
    job, profile = _job(), _profile()
    stages = _stages([profile.id])
    stages["semantic_ids"] = []
    stages["semantic_ran"] = False
    published = matching.publish_ai_scores(
        job,
        [(profile, _link(profile), _breakdown())],
        categories=CATEGORIES,
        stages=stages,
    )
    semantic = published[0].payload["retrieval"][matching.RETRIEVAL_SEMANTIC]
    assert semantic["hit"] is False
    assert semantic["stage_ran"] is False


def test_a_legacy_four_category_job_publishes_unvalidated_rather_than_not_at_all() -> None:
    """The gate is not a publish veto, and this is the case that proves it.

    A job created before the per-job lists existed is scored on four keys and
    fails `MIN_MATCHING_CATEGORIES` every single time. Refusing to publish would
    leave Siddhi unable to tell that job from a candidate who was never matched,
    which are opposite situations that must not read the same.
    """
    legacy = tuple(
        (key, name, description)
        for key, name, description in matching_categories.DEFAULT_CATEGORIES
        if key in matching_categories.LEGACY_KEYS
    )
    artifact = _publish_one(categories=legacy)
    assert len(artifact.payload["categories"]) == 4
    assert artifact.validated is False
    verdict = gates.run_gate(identity.YUKTI, artifact.payload)
    issues = {f.issue for f in verdict.findings}
    assert "matching_incomplete" in issues


def test_every_graded_category_cites_the_resume_it_was_graded_from() -> None:
    """`yukti_gate` raises `conclusion_without_evidence` otherwise, and it is
    right to: a graded category citing nothing is an opinion."""
    verdict = gates.run_gate(identity.YUKTI, _publish_one().payload)
    assert "conclusion_without_evidence" not in {f.issue for f in verdict.findings}
    assert verdict.passed, verdict.as_dict()


def test_an_unparsed_resume_is_reported_as_unparsed() -> None:
    """The gate refuses to let a grade written from an unparsed file pass as
    evidenced, and it can only do that if this pass tells it the truth."""
    job = _job()
    empty = Profile()
    empty.id = uuid.uuid4()
    empty.candidate_id = uuid.uuid4()
    published = matching.publish_ai_scores(
        job,
        [(empty, _link(empty), _breakdown())],
        categories=CATEGORIES,
        stages=_stages([empty.id]),
    )
    payload = published[0].payload
    assert payload["resume_parsed"] is False
    assert published[0].validated is False
    assert "resume_not_parsed" in {
        f.issue for f in gates.run_gate(identity.YUKTI, payload).findings
    }


def test_the_payload_carries_no_agent_deliberation() -> None:
    """Enforced by `artifacts.publish`, asserted here because the guarantee that
    matters is that nothing PUTS it there."""
    assert not (
        _keys(_publish_one().payload)
        & {"reasoning", "chain_of_thought", "scratchpad", "thinking", "deliberation"}
    )


def test_a_category_with_no_breakdown_block_is_named_rather_than_padded() -> None:
    """A padded list would let a run that scored three of five categories report
    a complete one, because `yukti_gate` counts `categories`."""
    partial = _breakdown(("skills_match", "experience_relevance"))
    artifact = _publish_one(breakdown=partial)
    assert artifact is not None
    payload = artifact.payload
    assert len(payload["categories"]) == 2
    assert set(payload["provenance"]["categories_unscored"]) == {
        "behavioural_signal",
        "role_alignment",
        "education_fit",
    }
    assert payload["provenance"]["categories_expected"] == [
        key for key, _, _ in CATEGORIES
    ]


def test_one_run_publishes_under_one_correlation_id() -> None:
    """Otherwise "what did this matching run publish" is N unrelated queries."""
    job = _job()
    rows = []
    for _ in range(3):
        profile = _profile()
        rows.append((profile, _link(profile), _breakdown()))
    published = matching.publish_ai_scores(
        job, rows, categories=CATEGORIES, stages=_stages([row[0].id for row in rows])
    )
    assert len({a.payload["correlation_id"] for a in published}) == 1


# ── the run harness ──────────────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """Enough of an AsyncSession for `run_matching`, and no more.

    Dispatches on the statement rather than on call order, so a reordering of
    the pipeline changes what this returns rather than silently handing the next
    stage the previous stage's rows.
    """

    def __init__(self, job: Job, profiles: list[Profile], links: list[JobCandidateLink]):
        self.job = job
        self.profiles = profiles
        self.links = links
        self.written: dict[str, str] = {}
        self.commits = 0
        self.added: list[Any] = []

    async def get(self, model: Any, _ident: Any = None) -> Any:
        return self.job if model is Job else None

    async def execute(self, query: Any, params: dict | None = None) -> _Rows:
        if isinstance(query, TextClause):
            sql = str(query)
            if "UPDATE job_candidate_links" in sql and params:
                self.written[params["id"]] = params["breakdown"]
            return _Rows([])
        entity = query.column_descriptions[0]["entity"]
        if entity is Profile:
            return _Rows(self.profiles)
        if entity is JobCandidateLink:
            return _Rows(self.links)
        return _Rows([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: Any) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class _Harness:
    """One `run_matching` run over fakes, with the real hand-off in place."""

    def __init__(self, *, count: int, seed: int = 0, real_scoring: bool = False):
        self.job = _job()
        self.profiles = [_profile() for _ in range(count)]
        self.links = [_link(profile) for profile in self.profiles]
        if seed:
            # Deterministic ids, so two runs of the same shape are comparable.
            for index, (profile, link) in enumerate(zip(self.profiles, self.links)):
                profile.id = uuid.UUID(int=seed * 1000 + index)
                profile.candidate_id = uuid.UUID(int=seed * 2000 + index)
                link.id = uuid.UUID(int=seed * 3000 + index)
                link.candidate_id = profile.candidate_id
                link.profile_id = profile.id
        self.session = _FakeSession(self.job, self.profiles, self.links)
        self.real_scoring = real_scoring
        self.published: list[Any] = []

    def _install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ids = [profile.id for profile in self.profiles]

        async def _embed(_texts):
            return [[0.1] * 1024]

        async def _ids(*_args, **_kwargs):
            return list(ids)

        async def _nothing(*_args, **_kwargs):
            return None

        async def _no_patterns(*_args, **_kwargs):
            return []

        async def _categories(*_args, **_kwargs):
            return list(CATEGORIES)

        monkeypatch.setattr(matching, "embed", _embed)
        monkeypatch.setattr(matching, "_semantic_stage", _ids)
        monkeypatch.setattr(matching, "_keyword_stage", _ids)
        monkeypatch.setattr(matching, "_linked_stage", _ids)
        monkeypatch.setattr(matching, "_backfill_missing_embeddings", _nothing)
        monkeypatch.setattr(matching, "_customer_success_patterns", _no_patterns)
        monkeypatch.setattr(matching_categories, "resolved_categories", _categories)

        if not self.real_scoring:
            async def _score(*_args, **_kwargs):
                return {profile.id: _breakdown() for profile in self.profiles}

            monkeypatch.setattr(matching, "_llm_score", _score)

        real = matching.publish_ai_scores

        def _spy(*args, **kwargs):
            published = real(*args, **kwargs)
            self.published.extend(published)
            return published

        monkeypatch.setattr(matching, "publish_ai_scores", _spy)

    async def run(self, monkeypatch: pytest.MonkeyPatch) -> int:
        self._install(monkeypatch)
        return await matching.run_matching(self.session, self.job.id)

    async def run_capturing(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """Everything a recruiter, a report or a sort order can see."""
        scored = await self.run(monkeypatch)
        return {
            "scored": scored,
            "breakdowns": dict(sorted(self.session.written.items())),
            "links": sorted(
                (
                    str(link.id),
                    link.match_score,
                    link.match_rationale,
                    getattr(link.tier, "value", link.tier),
                )
                for link in self.links
            ),
        }
