"""Miti writes to the evidence ledger from the live scoring path.

The ledger was built, migrated (0056), tested and deployed, and until this
change NOTHING wrote to it. A grep for its two write functions outside its own
package returned one file: the model definitions. So the question a recruiter
actually asks when they disagree with a grade -- "what did you read?" -- still
had no answer, and every review of the ledger read like an enforced property.

Since 2026-09-24 the writer is `assessment_pipeline.evidence.
record_answer_evidence`, the ONLY ledger writer in the product, called per
answer during the conversation and again as the scoring backfill
(`tests/test_answer_evidence_idempotent.py` proves the two calls write once).
This file keeps asserting the scoring side of it, which since WP5-B lives in
Miti's item stage (`miti/items.py`), the only code that grades a skill.

What is asserted here is not "evidence is recorded". It is the five rules that
decide whether recording it is safe to do at all:

  1. a ledger failure never costs the grade, because the report is the work a
     candidate has already done and a customer has already been charged for;
  2. the ledger stores a LOCATOR and never the sentence, because it is readable
     by anyone with database access while the transcript needs a capability;
  3. only a substantive answer becomes evidence, decided by the SAME classifier
     the scorer uses, never a second one;
  4. a MATERIAL contradiction flags the report for a person and moves no grade;
  5. no grading rule changed -- the same answers produce the same grades with
     the ledger working and with every ledger call raising.
"""
from __future__ import annotations

import inspect
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.services import functional_assessment as fa
from app.services.assessment_contract import ContractSkill
from app.services.assessment_pipeline import evidence as answer_evidence
from app.services.assessment_pipeline.types import AnswerRecord
from app.services.evidence import contradictions, ledger
from app.services.miti import items

_ANSWER = "I rebuilt the ingest pipeline and cut the nightly batch to minutes."
_GIBBERISH = "ewidjverip"


# ── Harness ──────────────────────────────────────────────────────────────────


class _Nested:
    """The savepoint. Counted so a test can prove the writes ran inside one."""

    def __init__(self, session: "_Session") -> None:
        self.session = session

    async def __aenter__(self) -> "_Nested":
        self.session.savepoints += 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Session:
    def __init__(self) -> None:
        self.savepoints = 0

    def begin_nested(self) -> _Nested:
        return _Nested(self)


def _outage() -> OperationalError:
    """A DATABASE failure, which is the only kind the writer absorbs. A
    programming error propagates (the 2026-09-22 rule), so a RuntimeError here
    would be testing a behaviour the writer deliberately does not have."""
    return OperationalError("INSERT INTO evidence_items", {}, Exception("unavailable"))


class _Recorder:
    """Stands in for the three ledger writes and keeps every argument."""

    def __init__(self, *, raising: bool = False) -> None:
        self.raising = raising
        self.evidence: list[dict] = []
        self.claims: list[dict] = []
        self.attachments: list[dict] = []

    async def record_evidence(self, session, **kwargs):
        if self.raising:
            raise _outage()
        self.evidence.append(kwargs)
        return uuid.uuid4()

    async def record_claim(self, session, **kwargs):
        if self.raising:
            raise _outage()
        self.claims.append(kwargs)
        return uuid.uuid4()

    async def attach_evidence(self, session, **kwargs):
        if self.raising:
            raise _outage()
        self.attachments.append(kwargs)

    def install(self, monkeypatch) -> "_Recorder":
        monkeypatch.setattr(ledger, "record_evidence", self.record_evidence)
        monkeypatch.setattr(ledger, "record_claim", self.record_claim)
        monkeypatch.setattr(ledger, "attach_evidence", self.attach_evidence)
        return self


def _fixture(*, answer: str = _ANSWER, category: str = "must_have"):
    """One matrix item, one question, one answer, and the locator for it."""
    tenant_id = uuid.uuid4()
    job = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant_id, assessment_grade="non_managerial"
    )
    link = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant_id, candidate_id=uuid.uuid4()
    )
    competency = SimpleNamespace(
        id=uuid.uuid4(),
        category=category,
        name="Stream Processing",
        description="Runs partition rebalances without data loss.",
        required_level=82,
    )
    question = SimpleNamespace(
        id=uuid.uuid4(),
        prompt="How did you cut the nightly batch?",
        rubric_json={},
        competency_id=competency.id,
    )
    message_id = uuid.uuid4()
    state = {
        "session": _Session(),
        "job": job,
        "link": link,
        "competencies": [competency],
        "candidate_questions": [question],
        "answers": {str(question.id): [answer]},
        "answer_refs": {
            str(question.id): [
                AnswerRecord(
                    message_id=message_id,
                    question_key=str(question.id),
                    turn=2,
                    text=answer,
                    answered_at=None,
                )
            ]
        },
        "transcript": [],
    }
    return state, competency, question, message_id


async def _score_stub(task, messages, **kwargs):
    """The injected model: the item stage's judgement, always 80. Scoring is not
    what this file is about, and an unstubbed call would make every assertion
    depend on whether a provider happened to answer."""
    return json.dumps({"score": 80, "band": "75_89"})


def _context(state) -> items.ItemContext:
    return items.ItemContext(
        tenant_id=state["job"].tenant_id,
        job_id=state["job"].id,
        link_id=state["link"].id,
        candidate_id=state["link"].candidate_id,
    )


def _skill(competency) -> ContractSkill:
    return ContractSkill(
        id=competency.id,
        name=competency.name,
        bucket=competency.category,
        priority=1,
        evidence_line="",
    )


async def _grade(state, competency, question):
    """Miti's item stage for the one skill in the fixture."""
    return await items.evaluate_skill(
        state["session"],
        context=_context(state),
        skill=_skill(competency),
        questions=[question],
        answers=state["answers"],
        locators=state["answer_refs"],
        structured={},
        invoke=_score_stub,
    )


@pytest.fixture(autouse=True)
def _no_claims(monkeypatch):
    async def _none(session, **kwargs):
        return []

    monkeypatch.setattr(ledger, "load_claims", _none)


# ── 1. a ledger failure never costs the report ───────────────────────────────


@pytest.mark.asyncio
async def test_a_ledger_failure_never_fails_scoring(monkeypatch) -> None:
    """The rule with the most at stake. This runs while a report is being
    written for work the candidate has already done and the customer has
    already been charged for, so an audit trail that could destroy the artifact
    it exists to explain would be worse than no audit trail at all."""
    recorder = _Recorder(raising=True).install(monkeypatch)
    state, competency, question, _message_id = _fixture()

    grade = await _grade(state, competency, question)

    assert not recorder.evidence, "the injected failure did not fire"
    assert grade.name == competency.name
    assert grade.score == 80
    assert grade.status == "graded"


@pytest.mark.asyncio
async def test_a_ledger_read_failure_raises_rather_than_passing_as_no_contradiction(
    monkeypatch,
) -> None:
    """REVERSED IN WP5-B. The contradiction read-back used to swallow any
    exception and answer "no contradiction", a silent PASS on the one signal
    that sends a disagreeing record to a person, and it swallowed a TypeError
    as readily as an outage. Miti has already read this ledger twice in the
    same transaction by the time it runs, so a failure here is a defect: it
    raises, the scoring task fails and is retried, and no report is written
    that claims its evidence agreed with itself."""
    _Recorder().install(monkeypatch)

    async def _explode(session, **kwargs):
        raise RuntimeError("the ledger is unreadable")

    monkeypatch.setattr(ledger, "load_claims", _explode)
    state, competency, question, _message_id = _fixture()

    grade = await _grade(state, competency, question)
    assert grade.score == 80, "the item grade itself never read the ledger back"
    with pytest.raises(RuntimeError, match="the ledger is unreadable"):
        await fa._uncertainty_from_evidence(state)


@pytest.mark.asyncio
async def test_a_locator_read_failure_raises_rather_than_scoring_unanswered(
    monkeypatch,
) -> None:
    """REVERSED IN WP5-B. An unreadable locator table used to be absorbed into
    an empty map, and an unreadable answer table into "everything unanswered",
    which is a synthetic Not Matching written from an outage. The inputs are
    read before Miti runs, and a failed read raises: the scoring task fails and
    is retried rather than writing a grade from nothing."""
    recorder = _Recorder().install(monkeypatch)

    async def _explode(session, link_id):
        raise RuntimeError("no locators")

    monkeypatch.setattr(answer_evidence, "answer_records", _explode)
    state, _competency, _question, _message_id = _fixture()
    state.pop("answer_refs")
    state["conversation"] = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(RuntimeError, match="no locators"):
        await fa.ppi_scoring_node(state)
    assert not recorder.evidence


def test_the_writes_run_inside_a_savepoint() -> None:
    """A plain try/except is not enough on its own. An INSERT that reaches
    Postgres and fails aborts the surrounding transaction, so every later
    statement in it -- the report row, the dimension rows, the credit
    reconciliation the caller commits afterwards -- fails too. Swallowing the
    exception would then turn "the ledger write failed" into "the report was
    lost"."""
    source = inspect.getsource(answer_evidence.record_answer_evidence)
    assert "_savepoint(session)" in source
    # A DATABASE failure only: a TypeError is a bug, not an outage.
    assert "except SQLAlchemyError" in source
    assert "except Exception" not in source


@pytest.mark.asyncio
async def test_the_savepoint_is_actually_entered(monkeypatch) -> None:
    _Recorder().install(monkeypatch)
    state, competency, question, _message_id = _fixture()

    await _grade(state, competency, question)

    assert state["session"].savepoints == 1


# ── 2. a locator, never the sentence ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_ledger_is_given_a_locator_and_never_the_answer(
    monkeypatch,
) -> None:
    """`text_ref` is a locator. A ledger row is readable by anyone with database
    access while the transcript it points at needs `view_review_screen`, so a
    ledger carrying excerpts would be a quiet route around that capability."""
    recorder = _Recorder().install(monkeypatch)
    state, competency, question, message_id = _fixture()

    await _grade(state, competency, question)

    assert len(recorder.evidence) == 1
    written = recorder.evidence[0]
    assert written["ref"] == ledger.text_ref(
        table="assessment_messages", row_id=message_id
    )
    assert written["source_type"] == ledger.SOURCE_ANSWER
    assert written["source_id"] == message_id
    assert written["trust"] == ledger.TRUST_OBSERVED

    # Nothing anywhere in any of the three calls may carry the answer, and the
    # check is over the whole serialised payload rather than over the fields
    # somebody remembered to look at.
    payload = json.dumps(
        [recorder.evidence, recorder.claims, recorder.attachments], default=str
    )
    assert _ANSWER not in payload
    for fragment in ("ingest pipeline", "nightly batch"):
        assert fragment not in payload
    assert question.prompt not in payload
    assert competency.name in payload, "the dimension has to be identifiable"


@pytest.mark.asyncio
async def test_the_evidence_carries_the_turn_the_answer_was_given_on(
    monkeypatch,
) -> None:
    """Identifiers, counts and a position. The same shape a trace carries, and
    for the same reason: a turn number locates an exchange, a quotation of it
    reproduces a real candidate's words."""
    recorder = _Recorder().install(monkeypatch)
    state, competency, question, _message_id = _fixture()

    await _grade(state, competency, question)

    provenance = recorder.evidence[0]["provenance"]
    assert provenance["agent"] == "miti"
    assert provenance["candidate_id"] == str(state["link"].candidate_id)
    assert provenance["competency_id"] == str(competency.id)
    assert provenance["question_id"] == str(question.id)
    assert provenance["conversation_turn"] == 2
    assert provenance["recorded_at"]


@pytest.mark.asyncio
async def test_the_evidence_is_attached_to_a_claim_for_that_matrix_item(
    monkeypatch,
) -> None:
    recorder = _Recorder().install(monkeypatch)
    state, competency, question, _message_id = _fixture()

    await _grade(state, competency, question)

    assert len(recorder.claims) == 1
    claim = recorder.claims[0]
    assert claim["dimension"] == competency.name
    assert claim["link_id"] == state["link"].id
    assert claim["job_id"] == state["job"].id
    assert len(recorder.attachments) == 1
    assert recorder.attachments[0]["stance"] == ledger.STANCE_SUPPORTS


def test_the_evidence_is_written_before_the_claim() -> None:
    """A claim with no live evidence under it is CRITICAL to
    `contradictions.detect` -- a conclusion nothing stands behind. Creating the
    claim first and failing on the evidence would manufacture the most serious
    finding the system has out of a transient write error."""
    source = inspect.getsource(answer_evidence.record_answer_evidence)
    assert source.index("record_evidence(") < source.index("record_claim(")


# ── 3. only a substantive answer becomes evidence ────────────────────────────


@pytest.mark.asyncio
async def test_a_non_answer_never_becomes_evidence(monkeypatch) -> None:
    """`ewidjverip` is not evidence of anything. It is already routed to the
    unanswered scoring path; recording it would put a row in the ledger
    asserting that something stood behind a grade of Not Matching."""
    recorder = _Recorder().install(monkeypatch)
    state, competency, question, _message_id = _fixture(answer=_GIBBERISH)

    grade = await _grade(state, competency, question)

    assert not recorder.evidence
    assert not recorder.claims
    assert grade.score == items.UNANSWERED_SCORE
    assert grade.status == "unanswered"


@pytest.mark.asyncio
async def test_a_locator_pointing_at_a_non_answer_is_dropped(monkeypatch) -> None:
    """The filter is on the recorder itself, not only on the scorer that calls
    it. A caller assembling locators by hand must not be able to file a
    keyboard mash as evidence."""
    recorder = _Recorder().install(monkeypatch)
    state, competency, question, message_id = _fixture()

    await items._record_evidence(
        state["session"],
        _context(state),
        _skill(competency),
        question,
        [
            AnswerRecord(
                message_id=message_id,
                question_key=str(question.id),
                turn=1,
                text=_GIBBERISH,
                answered_at=None,
            )
        ],
    )

    assert not recorder.evidence


def test_substance_is_decided_by_the_scorers_own_classifier() -> None:
    """One classifier, not two. A second set of thresholds here would be a
    second thing to keep in step, and the day they drifted the ledger would
    claim evidence for a grade the scorer had already treated as unanswered."""
    source = inspect.getsource(answer_evidence.record_answer_evidence)
    assert "answer_quality.is_substantive" in source
    # No private substance rules of its own.
    for smell in ("MIN_CHARS", "MIN_WORDS", "re.compile", "len(text)"):
        assert smell not in source, f"a second substance rule appeared: {smell}"


# ── 4. a contradiction is preserved, never averaged ──────────────────────────


def _contradicted_claim(dimension: str) -> ledger.Claim:
    live = ledger.EvidenceItem(
        evidence_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
        source_type=ledger.SOURCE_ANSWER,
        source_id=uuid.uuid4(),
        text_ref=ledger.text_ref(table="assessment_messages", row_id=uuid.uuid4()),
        trust=ledger.TRUST_OBSERVED,
    )
    against = ledger.EvidenceItem(
        evidence_id=uuid.uuid4(),
        tenant_id=live.tenant_id,
        job_id=live.job_id,
        link_id=live.link_id,
        source_type=ledger.SOURCE_RESUME,
        source_id=uuid.uuid4(),
        text_ref=ledger.text_ref(table="context_chunks", row_id=uuid.uuid4()),
        trust=ledger.TRUST_OBSERVED,
    )
    return ledger.Claim(
        claim_id=uuid.uuid4(),
        tenant_id=live.tenant_id,
        job_id=live.job_id,
        link_id=live.link_id,
        subject="candidate",
        dimension=dimension,
        claim="the candidate demonstrated it",
        supporting_evidence=(live,),
        contradicting_evidence=(against,),
    )


@pytest.mark.asyncio
async def test_a_material_contradiction_flags_the_report_and_moves_no_grade(
    monkeypatch,
) -> None:
    """Spec 14's rule with the teeth. Live evidence on both sides is the most
    interesting row in the ledger and the easiest one to lose: any rule that let
    support outweigh contradiction would be the silent averaging the whole
    mechanism exists to forbid. The honest outcome once the conversation is over
    is to preserve the uncertainty and hand the report to a person."""
    _Recorder().install(monkeypatch)
    state, competency, _question, _message_id = _fixture()

    async def _claims(session, **kwargs):
        return [_contradicted_claim(competency.name)]

    monkeypatch.setattr(ledger, "load_claims", _claims)

    grade = await _grade(state, competency, _question)
    review, findings = await fa._uncertainty_from_evidence(state)

    assert review is True
    assert findings, "a flagged report must say why"
    assert all(
        finding["issue"] == contradictions.AXIS_CONCLUSIONS_VS_EVIDENCE
        for finding in findings
    )
    # The grade is untouched. Not lowered, not raised, not blended.
    assert grade.score == 80


@pytest.mark.asyncio
async def test_a_flagged_finding_carries_no_report_prose(monkeypatch) -> None:
    """`review_findings_json` is read from far more places than the report, and
    a contradiction's `detail` reads like a quotation whether or not it is
    one."""
    _Recorder().install(monkeypatch)
    state, competency, _question, _message_id = _fixture()

    async def _claims(session, **kwargs):
        return [_contradicted_claim(competency.name)]

    monkeypatch.setattr(ledger, "load_claims", _claims)

    _review, findings = await fa._uncertainty_from_evidence(state)

    for finding in findings:
        assert set(finding) == {"severity", "issue", "location", "recommendation"}
        # One severity vocabulary in the column, the verifier's, because
        # `review_findings_json` already holds gate findings on that scale.
        assert finding["severity"] in {"high", "medium", "low"}


@pytest.mark.asyncio
async def test_a_minor_disagreement_does_not_flag_a_report(monkeypatch) -> None:
    """MINOR exists precisely so a detector that demanded work for every
    rounding difference is not switched off inside a week."""
    _Recorder().install(monkeypatch)
    state, competency, _question, _message_id = _fixture()

    supported = ledger.Claim(
        claim_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
        subject="candidate",
        dimension=competency.name,
        claim="the candidate demonstrated it",
        supporting_evidence=(
            ledger.EvidenceItem(
                evidence_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                job_id=uuid.uuid4(),
                link_id=uuid.uuid4(),
                source_type=ledger.SOURCE_ANSWER,
                source_id=uuid.uuid4(),
                text_ref="assessment_messages:1",
                trust=ledger.TRUST_OBSERVED,
            ),
        ),
    )

    async def _claims(session, **kwargs):
        return [supported]

    monkeypatch.setattr(ledger, "load_claims", _claims)

    review, findings = await fa._uncertainty_from_evidence(state)
    assert review is False
    assert findings == []


@pytest.mark.asyncio
async def test_an_empty_ledger_is_not_a_contradiction(monkeypatch) -> None:
    """A ledger outage that flagged every report in the product for human review
    would be a louder failure than the one it was guarding against."""
    _Recorder(raising=True).install(monkeypatch)
    state, _competency, _question, _message_id = _fixture()

    review, _findings = await fa._uncertainty_from_evidence(state)

    assert review is False


def test_the_report_row_carries_the_flag_beside_the_gates() -> None:
    """Four independent questions -- is the DRAFT sound, does the EVIDENCE under
    it disagree with itself, did Miti's aggregation flag anything, and was any
    recorded evidence unreadable -- OR'd rather than blended, so a clean draft
    cannot cancel out a contradiction nobody has resolved.

    Miti's own verdict joined the OR on 2026-08-29, when the five-dimension
    engine went live. It is OR'd for the same reason as the other three: an
    average of independent review signals lets one clean answer hide another.
    """
    source = inspect.getsource(fa.synthesis_node)
    for term in (
        "not gate_verdict.passed",
        "uncertainty_review",
        "aggregate.needs_human_review",
        "evaluation.unresolved_evidence",
    ):
        assert term in source, term
    assert "+ evidence_findings" in source
    assert "+ miti_findings" in source


# ── 5. no grading rule changed ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grades_are_identical_with_the_ledger_enabled_and_disabled(
    monkeypatch,
) -> None:
    """The whole point of recording evidence as a SIDE EFFECT of scoring. If a
    grade could move because the ledger was reachable, every report in the
    product would depend on the health of a table nobody scores from."""
    with_ledger = _Recorder()
    with_ledger.install(monkeypatch)
    state, competency, question, _message_id = _fixture()
    enabled = await _grade(state, competency, question)

    _Recorder(raising=True).install(monkeypatch)
    state, competency, question, _message_id = _fixture()
    disabled = await _grade(state, competency, question)

    assert with_ledger.evidence, "the enabled run recorded nothing"
    assert (enabled.status, enabled.score, enabled.grade) == (
        disabled.status, disabled.score, disabled.grade
    )


@pytest.mark.asyncio
async def test_a_behavioural_item_grades_the_same_and_is_still_recorded(
    monkeypatch,
) -> None:
    """The other scoring method. One judgement is made across everything said
    about a behavioural competency, so every one of those answers is evidence
    for the same claim -- filed individually, so a recruiter can be shown the
    specific turn a behavioural grade rests on."""
    recorder = _Recorder().install(monkeypatch)
    state, competency, question, _message_id = _fixture(category="behavioural")

    grade = await _grade(state, competency, question)

    assert grade.score == 80
    assert len(recorder.evidence) == 1
    assert recorder.claims[0]["dimension"] == competency.name


def test_the_scoring_path_reads_the_locators_for_nothing_else() -> None:
    """Recording evidence is a side effect of scoring and never an input to it.
    A locator map that could reach the judge or a rubric would make a grade
    depend on how much of the transcript happened to be readable. Read in
    Miti's item stage, where every per-skill grade is written since WP5-B."""
    for function in (items._rubric_scored, items._behavioural):
        source = inspect.getsource(function)
        for line in source.splitlines():
            stripped = line.strip()
            if "locators" not in stripped or stripped.startswith("#"):
                continue
            assert (
                "_record_evidence(" in stripped
                or stripped.startswith("locators: Mapping")
                or stripped == "locators=locators,"
            ), f"the locator map leaked into scoring: {stripped}"


def test_the_evidence_path_can_reach_no_grading_rule() -> None:
    """The cheapest possible proof that no grade moved: neither function can
    name the four-grade scale, the unanswered score or the Must-have hard cap,
    so neither is in a position to change any of them."""
    for name, source in (
        ("items._record_evidence", inspect.getsource(items._record_evidence)),
        ("_uncertainty_from_evidence", inspect.getsource(fa._uncertainty_from_evidence)),
        ("record_answer_evidence", inspect.getsource(answer_evidence)),
    ):
        for rule in (
            "UNANSWERED_SCORE",
            "cap_to_moderately",
            "grade_for_percent",
            "must_have_cap_applies",
            "_stable_score",
            "GRADES",
        ):
            assert rule not in source, f"{name} reaches a grading rule: {rule}"


# ── The import rule this change is most able to break ────────────────────────


def test_the_evidence_package_is_never_imported_at_module_scope() -> None:
    """`app.services.evidence` sits on an import cycle. A module-level import
    here closed one before: the full suite went green while a single test file
    went red, because pytest happened to initialise the other side first.
    `tests/test_import_graph.py` pins the general rule; this pins the specific
    line most likely to break it again."""
    source = inspect.getsource(fa)
    for line in source.splitlines():
        if line.startswith(("from app.services.evidence", "import app.services.evidence")):
            raise AssertionError(f"module-scope evidence import: {line}")
    assert "from app.services.evidence import ledger" in source, (
        "the function-scoped import went missing"
    )
