"""WHY a Vaada session ended, recorded where somebody can read it later.

The early exit itself is old. `question_budget.conversation_may_close` has decided it since
2026-08-23 and `tests/test_question_count_range.py` and
`tests/test_vaada_miti_loop.py` between them pin the rule, the floor and the
fact that nothing else may end a conversation. None of that is re-asserted
here.

What is new, and what this file is about, is that the decision is now DURABLE.
Before change request 28B the reason lived in one `assessments.conversation_state`
log line, so "did this candidate stop early or run out of questions" was a
CloudWatch lookup per conversation id and was not answerable for a cohort at
all. The two endings are different events and `completed_at` cannot tell them
apart: stopping at fourteen of twenty is the feature working and stopping at
fourteen of fourteen is an interview with nothing left to ask.

Three properties, and the third is the one that would be easy to lose:

  1. both endings really do write their own word, through the real handler
     rather than through a patched stopping rule -- the early-close case below
     answers twelve questions out of fourteen against a floor of twelve, so it
     is `conversation_may_close` that stops it and not this test;
  2. the vocabulary is one vocabulary. `interviewer.STOP_CONDITIONS`, the
     model's CHECK and migration 0116 all restate the same six words, because
     a model and a migration are not importable from each other here;
  3. the reason NEVER reaches a client. How much of the matrix somebody was
     asked is exactly the kind of fact a candidate reads as a verdict on their
     answers, and `interview_telemetry`'s own header is the rule it falls
     under.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from app.models.assessment import END_REASON_VALUES
from app.services import interviewer
from tests.test_conversation_flow import (
    _Fx,
    _cleanup,
    _factory_or_skip,
    _patch_link,
    _respond,
    _seed,
)
from app.services.assessment_questions import budget as question_budget

BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _stub_the_model_calls(monkeypatch):
    """The same two stubs `test_conversation_flow` declares, and for its reasons.

    Re-declared rather than imported: an autouse fixture applies to the module
    that DEFINES it, so importing the helpers above brings the plumbing and not
    the fixture. Both stubs keep this file about flow. The classifier reaches
    the router through the very session these tests drive by hand, and the
    question writer is a live model call whose key bookkeeping rolls that
    transaction back underneath the assertions.
    """
    from app.services import agent_loop, answer_classification as ac, ppi_interview

    async def _substantive(**kwargs):
        return ac.Classification(
            label="substantive",
            confidence="high",
            reason="stubbed for a flow test",
            needs_rechallenge=False,
            scorable=True,
        )

    async def _written(*, row, **kwargs):
        return agent_loop.LoopResult(
            value={"question": row.prompt, "rubric": dict(row.rubric_json or {})},
            degraded=False,
            attempts=1,
        )

    monkeypatch.setattr(ac, "classify", _substantive)
    monkeypatch.setattr(ppi_interview, "write_question", _written)


def _no_probes(monkeypatch) -> None:
    """No follow-up anywhere in these two conversations.

    A probe outstanding holds completion open by design, so leaving the real
    writer in place would make which turn completes depend on what a model
    decided to ask, and a test that cannot say which turn it is asserting about
    is not asserting anything.
    """
    from app.api import assessment_conversation as mod

    async def _none(**kwargs):
        return None

    monkeypatch.setattr(mod.interviewer, "next_follow_up", _none)


# ── The two endings, through the real handler ────────────────────────────────


@pytest.mark.asyncio
async def test_running_out_of_questions_is_recorded_as_exhaustion(
    monkeypatch,
) -> None:
    """One question, answered once. Nothing stopped early; there was no more."""
    from app.api import assessment_conversation as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation

    monkeypatch.setattr(mod, "dispatch", lambda name, *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=1)
        _patch_link(monkeypatch, fx)
        _no_probes(monkeypatch)

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    out = await _respond(mod, fx, session)

        # Read from a SECOND session after the commit, the rule
        # `test_audit_single_insert_api` records: the object the handler was
        # holding cannot show a write that answered and then vanished.
        async with factory() as session:
            async with superadmin_scope(session):
                conv = await session.get(AssessmentConversation, fx.conv_id)
                assert conv.status == "completed"
                assert conv.end_reason == interviewer.STOP_PROMPTS_EXHAUSTED
                assert conv.next_question_index == 1

        assert not hasattr(out, "end_reason"), (
            "the candidate's own response carries the end reason"
        )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_stopping_on_coverage_is_recorded_as_the_reason_it_stopped(
    monkeypatch,
) -> None:
    """Fourteen questions written, twelve answered, and the stop is real.

    The counts are chosen against `question_budget.GRADE_QUESTION_RANGES`, not picked: a
    non-managerial floor is twelve, so the twelfth substantive answer is the
    first turn at which `conversation_may_close` may say yes, and two prompts
    are deliberately left unasked so the early exit is observable rather than
    indistinguishable from exhaustion. The fixture seeds three competencies and
    cycles the questions across them, so every matrix item carries a
    substantive answer long before the floor is reached.

    `every_dimension_covered` rather than `floor_reached` is the assertion that
    matters. The floor is PERMISSION to stop; recording it would read as "we
    stopped at the minimum", which is the fail-style early exit this product
    deliberately does not do.
    """
    from app.api import assessment_conversation as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation

    monkeypatch.setattr(mod, "dispatch", lambda name, *a, **k: None)

    floor = question_budget.min_questions("non_managerial", None)
    written = floor + 2

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, question_count=written)
        _patch_link(monkeypatch, fx)
        _no_probes(monkeypatch)

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for _ in range(floor):
                        await _respond(mod, fx, session)

        async with factory() as session:
            async with superadmin_scope(session):
                conv = await session.get(AssessmentConversation, fx.conv_id)

        assert conv.status == "completed", (
            "the conversation did not stop on coverage at the floor; the "
            "recorded reason below would be meaningless"
        )
        assert conv.next_question_index == floor < written, (
            "every written question was asked, so this is exhaustion and not "
            "an early exit"
        )
        assert conv.end_reason == interviewer.STOP_EVERY_DIMENSION_COVERED
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── One vocabulary, restated in three places ─────────────────────────────────


def _migration_reasons() -> tuple[str, ...]:
    """Read migration 0116's list WITHOUT importing it.

    Parsed rather than imported for the reason every migration is a historical
    record: importing one runs its module body against whatever `alembic`
    global state happens to exist, and the thing under test here is the literal
    text of the revision as it was written, which is exactly what a later
    rename of a code constant must not silently change.
    """
    path = BACKEND / "alembic" / "versions" / "0116_assessment_end_reason.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target = first.id if isinstance(first, ast.Name) else None
        if target == "END_REASONS" and node.value is not None:
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("migration 0116 no longer declares END_REASONS")


def test_the_vocabulary_is_one_vocabulary() -> None:
    """Three restatements of six words, compared rather than trusted.

    `app/models` may not import `app/services/interviewer` at module scope:
    it sits on the service import graph and `tests/test_import_graph` refuses
    exactly that read. A migration may not import a code constant either, for
    its own reason. So the words are typed out three times, which makes this
    comparison the only thing standing between a rename and a table that
    refuses a value the handler now writes.
    """
    assert END_REASON_VALUES == interviewer.STOP_CONDITIONS
    assert _migration_reasons() == interviewer.STOP_CONDITIONS


def test_both_recorded_reasons_are_in_the_vocabulary() -> None:
    """The two words the handler can actually write.

    Named here rather than inferred, so widening what `respond` records is a
    change somebody has to make deliberately in two places.
    """
    assert interviewer.STOP_PROMPTS_EXHAUSTED in END_REASON_VALUES
    assert interviewer.STOP_EVERY_DIMENSION_COVERED in END_REASON_VALUES


def test_the_database_refuses_a_reason_outside_the_vocabulary() -> None:
    """The CHECK, against the real constraint rather than the model's copy.

    A constraint that exists only in `__table_args__` binds nothing: this
    schema is built by migrations, and a model-only CHECK would let any string
    a future caller passes land in the column.
    """
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    async def _probe() -> str:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sa.text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = "
                            "'ck_assessment_conversations_end_reason'"
                        )
                    )
                ).first()
                return "" if row is None else str(row[0])
        finally:
            await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        definition = loop.run_until_complete(_probe())
    except Exception:  # noqa: BLE001 -- no database, like every integration test here
        pytest.skip("no migrated database reachable")
    finally:
        loop.close()

    assert definition, (
        "the end_reason CHECK is absent from the database; migration 0116 has "
        "not been applied"
    )
    for reason in END_REASON_VALUES:
        assert f"'{reason}'" in definition, reason


# ── It is internal, and it stays internal ────────────────────────────────────


def test_no_response_schema_carries_the_end_reason() -> None:
    """The sweep, over the tree rather than at one call site.

    Nothing crosses this API boundary as a bare dict (section 4 of claude.md),
    so every client-visible field is a field on a model under `app/schemas`.
    A name appearing there is the whole failure: the reason says how much of
    the matrix somebody was asked, and a candidate reading "every dimension
    covered" beside their own finished assessment reads a verdict.
    """
    offenders = [
        str(path.relative_to(BACKEND))
        for path in sorted((BACKEND / "app" / "schemas").rglob("*.py"))
        if "end_reason" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_the_conversation_response_model_has_no_end_reason_field() -> None:
    """The one schema a candidate mid-assessment actually receives."""
    from app.schemas.assessments import ConversationOut

    assert "end_reason" not in ConversationOut.model_fields


def test_the_reason_is_carried_by_a_log_line_that_holds_no_text() -> None:
    """`interview_telemetry`'s standing rule, applied to the new event.

    The end event carries three numbers, which is what makes a reason legible
    ("fourteen of twenty above a floor of twelve"), and numbers here are
    operator data that must never be forwarded. What it must not carry is
    anything a candidate or a recruiter wrote, so the field set is asserted
    exactly rather than by the absence of names somebody thought of -- a future
    field called `notes` would pass the narrower check and reopen the hole.
    """
    import dataclasses

    from app.services import interview_telemetry

    assert {
        field.name for field in dataclasses.fields(interview_telemetry.EndEvent)
    } == {
        "conversation_id",
        "end_reason",
        "questions_asked",
        "questions_written",
        "floor",
    }


def test_the_end_line_never_raises_on_a_malformed_event() -> None:
    """It fires on the request that also settles completion, the credit charge
    and the scoring dispatch. An observer that can interrupt that sequence is
    worse than no observer, which is why every function in that module
    swallows its own failure and this one has to as well."""
    from app.services import interview_telemetry

    class _Hostile:
        @property
        def end_reason(self) -> str:
            raise RuntimeError("a field that throws when read")

    interview_telemetry.record_end(_Hostile())  # type: ignore[arg-type]
    interview_telemetry.record_end(None)  # type: ignore[arg-type]


def test_a_conversation_that_has_not_ended_carries_no_reason() -> None:
    """NULL means NOT RECORDED, and the column default has to say so.

    A word written at construction would assert a stopping decision for a
    session that has not stopped, and every row in the table predates the
    column.
    """
    from app.models.assessment import AssessmentConversation

    row = AssessmentConversation(
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_candidate_link_id=uuid.uuid4(),
        grade="non_managerial",
    )
    assert row.end_reason is None
