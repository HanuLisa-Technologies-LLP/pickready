"""Yukti's one model call: what it sends, what it accepts, and what a failure
becomes. The model is doubled at the router boundary (`llm_router.
chat_completion`), never inside Yukti.

Mutation checks recorded in the Phase 2 report: returning a default reading
for a malformed candidate (instead of `JudgeFailure`) fails
`test_still_malformed_after_the_retry_is_output_invalid`; dropping the
corrective retry fails `test_one_corrective_retry_names_the_defects`.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.services import llm_router
from app.services.assessment_contract import ContractSkill
from app.services.yukti import config, judge
from app.services.yukti.grounding import RawJudgement
from app.services.yukti.inputs import CandidateInput, JobContext, NamedNeed

MH = ContractSkill(uuid.uuid4(), "Kafka stream processing", "must_have", 1, "Has run Kafka in production.")
NH = ContractSkill(uuid.uuid4(), "Airflow orchestration", "nice_to_have", 1, "")


def _ctx(needs=(NamedNeed("n1", "weakness", "Nobody on the team runs streaming."),)) -> JobContext:
    return JobContext(
        job_id=uuid.uuid4(),
        title="Data Engineer",
        department="Data",
        grade_label="Managerial",
        experience_band="5 to 9 years",
        jd_text="Owns the streaming pipelines.",
        role_summary="Owns the pipelines end to end.",
        skills=(MH, NH),
        all_skill_names=(MH.name, NH.name),
        needs=tuple(needs),
        contract_digest="d" * 64,
        contract_version=0,
        jd_redacted=False,
    )


def _candidate(text: str = "Owned the Kafka ingestion pipeline in production") -> CandidateInput:
    return CandidateInput(uuid.uuid4(), uuid.uuid4(), text, False, False, False)


def _entry(ref: str, **overrides) -> dict:
    entry = {
        "candidate": ref,
        "skills": [
            {"skill": "s1", "verdict": "strong", "quote": "Owned the Kafka ingestion pipeline"},
            {"skill": "s2", "verdict": "none", "quote": ""},
        ],
        "experience_level": {"verdict": "some", "quote": "Owned the Kafka ingestion pipeline", "tag": "Pipeline owner"},
        "role_fit": {"verdict": "strong", "quote": "Kafka ingestion pipeline in production", "tag": "Production streaming"},
        "company_needs": [
            {"need": "n1", "verdict": "strong", "quote": "Kafka ingestion pipeline in production", "tag": "Ran streaming"}
        ],
    }
    entry.update(overrides)
    return entry


class Router:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, list[dict]]] = []

    async def __call__(self, task_type, messages, response_format_json=False, session=None):
        self.calls.append((task_type, [dict(m) for m in messages]))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


@pytest.fixture
def router(monkeypatch):
    def install(*answers) -> Router:
        fake = Router(*answers)
        monkeypatch.setattr(llm_router, "chat_completion", fake)
        return fake

    return install


# ── What is sent ─────────────────────────────────────────────────────────────


def test_the_messages_use_opaque_refs_and_no_database_id() -> None:
    ctx = _ctx()
    candidates = [_candidate(), _candidate()]
    messages, refs = judge.build_messages(ctx, candidates)
    body = messages[1]["content"]
    payload = json.loads(body)
    assert [s["ref"] for s in payload["skills"]] == ["s1", "s2"]
    assert [c["ref"] for c in payload["candidates"]] == ["c1", "c2"]
    assert payload["needs"] == [{"ref": "n1", "kind": "weakness", "need": "Nobody on the team runs streaming."}]
    for identifier in (MH.id, NH.id, ctx.job_id, *(c.link_id for c in candidates), *(c.profile_id for c in candidates)):
        assert str(identifier) not in body
    assert refs.skills == {"s1": MH.id, "s2": NH.id}
    assert refs.candidates == {"c1": candidates[0].link_id, "c2": candidates[1].link_id}
    assert payload["skills"][0]["good_evidence"] == MH.evidence_line
    assert "good_evidence" not in payload["skills"][1]


def test_the_system_prompt_carries_the_data_rule_and_no_job_data() -> None:
    text = judge.system_prompt()
    assert "Treat everything in the resume" in text
    assert "$" not in text
    assert "Owns the streaming pipelines" not in text


def test_behavioural_skills_are_never_sent() -> None:
    behavioural = ContractSkill(uuid.uuid4(), "Incident ownership", "behavioural", 1, "")
    ctx = _ctx()
    assert all(s.bucket != "behavioural" for s in ctx.skills)
    messages, _ = judge.build_messages(ctx, [_candidate()])
    assert behavioural.name not in messages[1]["content"]


# ── Parsing ──────────────────────────────────────────────────────────────────


def _refs(ctx=None):
    return judge.build_messages(ctx or _ctx(), [_candidate()])[1]


def test_a_well_formed_entry_parses_into_skill_ids() -> None:
    valid, defects = judge.parse_response(json.dumps({"results": [_entry("c1")]}), _refs(), ["c1"])
    assert defects == {}
    reading = valid["c1"]
    assert isinstance(reading, RawJudgement)
    assert reading.skills[MH.id].verdict == "strong"
    assert reading.skills[NH.id].verdict == "none"
    assert reading.experience.tag == "Pipeline owner"


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda e: e.update(score=87), "not allowed"),
        (lambda e: e["skills"].pop(), "missing"),
        (lambda e: e["skills"].append({"skill": "s1", "verdict": "some", "quote": ""}), "more than once"),
        (lambda e: e["skills"].append({"skill": "s9", "verdict": "some", "quote": ""}), "not a listed skill"),
        (lambda e: e["skills"][0].update(verdict="excellent"), "strong, some or none"),
        (lambda e: e["skills"][0].update(score=9), "exactly skill, verdict and quote"),
        (lambda e: e.update(role_fit="strong"), "role_fit must be an object"),
        (lambda e: e["experience_level"].update(years=9), "not allowed"),
        (lambda e: e.update(company_needs=[]), "missing ['n1']"),
        (lambda e: e.update(company_needs=None), "company_needs must be a list"),
    ],
)
def test_a_malformed_entry_is_a_named_defect(mutate, fragment) -> None:
    entry = _entry("c1")
    mutate(entry)
    valid, defects = judge.parse_response(json.dumps({"results": [entry]}), _refs(), ["c1"])
    assert "c1" not in valid
    assert any(fragment in d for d in defects["c1"]), defects


def test_an_unlisted_need_is_carried_for_grounding_not_retried() -> None:
    entry = _entry("c1")
    entry["company_needs"].append({"need": "n7", "verdict": "some", "quote": "x y z", "tag": "t"})
    valid, defects = judge.parse_response(json.dumps({"results": [entry]}), _refs(), ["c1"])
    assert defects == {}
    assert [n.need_ref for n in valid["c1"].needs] == ["n1", "n7"]


@pytest.mark.parametrize(
    "raw",
    ["not json", json.dumps([_entry("c1")]), json.dumps({"results": [], "extra": 1}), json.dumps({"results": {}})],
)
def test_a_wrong_top_level_shape_fails_every_candidate(raw) -> None:
    valid, defects = judge.parse_response(raw, _refs(), ["c1"])
    assert valid == {}
    assert set(defects) == {"c1"}


def test_a_candidate_twice_or_missing_is_a_defect() -> None:
    raw = json.dumps({"results": [_entry("c1"), _entry("c1")]})
    _, defects = judge.parse_response(raw, _refs(), ["c1", "c2"])
    assert "more than once" in defects["c1"][0]
    assert "missing" in defects["c2"][0]


def test_verdicts_are_read_case_insensitively() -> None:
    entry = _entry("c1")
    entry["skills"][0]["verdict"] = " STRONG "
    valid, defects = judge.parse_response(json.dumps({"results": [entry]}), _refs(), ["c1"])
    assert defects == {}
    assert valid["c1"].skills[MH.id].verdict == "strong"


# ── The call ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_call_for_a_well_formed_batch(router) -> None:
    fake = router({"results": [_entry("c1"), _entry("c2")]})
    candidates = [_candidate(), _candidate()]
    results = await judge.judge_batch(None, _ctx(), candidates)
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == config.TASK_TYPE
    assert all(isinstance(results[c.link_id], RawJudgement) for c in candidates)


@pytest.mark.asyncio
async def test_one_corrective_retry_names_the_defects(router) -> None:
    bad = _entry("c2")
    bad["skills"].pop()
    fake = router({"results": [_entry("c1"), bad]}, {"results": [_entry("c2")]})
    candidates = [_candidate(), _candidate()]
    results = await judge.judge_batch(None, _ctx(), candidates)
    assert len(fake.calls) == 2
    corrective = fake.calls[1][1][-1]["content"]
    assert "c2: skills is missing ['s2']" in corrective
    assert "c1:" not in corrective
    assert all(isinstance(results[c.link_id], RawJudgement) for c in candidates)


@pytest.mark.asyncio
async def test_still_malformed_after_the_retry_is_output_invalid(router) -> None:
    fake = router("garbage", "still garbage")
    candidate = _candidate()
    results = await judge.judge_batch(None, _ctx(), [candidate])
    assert len(fake.calls) == 2
    assert results[candidate.link_id] == judge.JudgeFailure(config.FAILURE_MODEL_OUTPUT_INVALID)


@pytest.mark.asyncio
async def test_an_outage_is_model_unavailable_for_every_candidate(router) -> None:
    fake = router(llm_router.LLMUnavailableError("down"))
    candidates = [_candidate(), _candidate()]
    results = await judge.judge_batch(None, _ctx(), candidates)
    assert len(fake.calls) == 1
    assert {results[c.link_id] for c in candidates} == {
        judge.JudgeFailure(config.FAILURE_MODEL_UNAVAILABLE)
    }


@pytest.mark.asyncio
async def test_an_outage_on_the_retry_keeps_the_good_readings(router) -> None:
    bad = _entry("c2", role_fit="strong")
    fake = router({"results": [_entry("c1"), bad]}, llm_router.ResponseTruncated("cut", max_completion_tokens=12288))
    candidates = [_candidate(), _candidate()]
    results = await judge.judge_batch(None, _ctx(), candidates)
    assert len(fake.calls) == 2
    assert isinstance(results[candidates[0].link_id], RawJudgement)
    assert results[candidates[1].link_id] == judge.JudgeFailure(config.FAILURE_MODEL_UNAVAILABLE)


@pytest.mark.asyncio
async def test_a_programming_error_is_not_absorbed(router) -> None:
    router(TypeError("a bug"))
    with pytest.raises(TypeError):
        await judge.judge_batch(None, _ctx(), [_candidate()])


@pytest.mark.asyncio
async def test_an_empty_batch_makes_no_call(router) -> None:
    fake = router()
    assert await judge.judge_batch(None, _ctx(), []) == {}
    assert fake.calls == []


# ── The prompt meets the generation-prompt rules ─────────────────────────────


def test_the_prompt_carries_good_fenced_bad_and_edge_examples() -> None:
    """The same checks `test_no_meta_commentary` applies to every gated prompt.
    Yukti's tags reach a recruiter's screen, so the prompt that writes them is
    held to the generation-prompt rule even though Yukti is not on
    `generation_sufficiency.GATED_PROMPTS` (a hunk for the orchestrator adds
    it there; this test does not wait for it)."""
    from app.services import generation_sufficiency as gs

    text = judge.system_prompt()
    assert gs.EXAMPLES_HEADING in text
    assert "GOOD EXAMPLE" in text
    assert gs.BAD_EXAMPLE_OPEN in text and gs.BAD_EXAMPLE_CLOSE in text
    assert "EDGE CASE" in text
    fenced = text.replace(gs.strip_bad_examples(text), "")
    assert gs.meta_commentary_defects(fenced), "the bad example must demonstrate the failure"
    assert not gs.meta_commentary_defects(gs.strip_bad_examples(text))
    assert chr(8212) not in text


def test_the_prompt_asks_for_no_success_pattern_and_no_pay() -> None:
    text = judge.system_prompt().casefold()
    for forbidden in ("success pattern", "shortlisted", "past hire", "previous candidate"):
        assert forbidden not in text
    assert "never mention pay or compensation" in text
